"""Explicit, one-shot runtime operation receipts.

The provider adapters own request preparation and response normalization.  This
module adds only the shared execution boundary around an already selected
completer, so callers can see which step was reached without turning a runtime
response into Canonical State.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any

from synapse.runtime.contracts import (
    RuntimeAdapterError,
    RuntimeErrorKind,
    RuntimeObservation,
    RuntimeProgressEvent,
    RuntimeProgressEvidence,
    RuntimeRequest,
    RuntimeResponse,
    utc_now,
)
from synapse.runtime.redaction import redact_diagnostic_detail

RUNTIME_OPERATION_SCHEMA = "runtime.operation.v1"
RUNTIME_OPERATION_STAGES = (
    "REQUEST_VALIDATED",
    "DISPATCH_APPROVED",
    "PROVIDER_INVOKED",
    "RESPONSE_NORMALIZED",
)
RUNTIME_OPERATION_STATUSES = ("SUCCEEDED", "FAILED")
MAX_RUNTIME_ERROR_DETAIL = 2000
RuntimeCompleter = Callable[[RuntimeRequest], RuntimeResponse]
_PROGRESS_TRANSITIONS = {
    "PREPARING": {"REQUEST_SENT"},
    "REQUEST_SENT": {"WAITING_RESPONSE"},
    "WAITING_RESPONSE": {"RESPONSE_RECEIVED", "FAILED", "CANCELLED"},
    "RESPONSE_RECEIVED": {"VALIDATING"},
    "VALIDATING": {"COMPLETED", "FAILED", "CANCELLED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}


class RuntimeOperationError(ValueError):
    """Raised when an explicit runtime operation cannot be started or consumed."""


class RuntimeOperationCancelled(RuntimeError):
    """Raised by an explicit completer when the caller observed cancellation."""


def _json_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _text_digest(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _response_metadata(response: RuntimeResponse) -> dict[str, Any]:
    return {
        "request_id": response.request_id,
        "provider": response.provider,
        "model": response.model,
        "text_sha256": _text_digest(response.text),
        "text_length": len(response.text),
        "usage": dict(response.usage),
        "finish_reason": response.finish_reason,
    }


def _bounded_detail(detail: object) -> str:
    return redact_diagnostic_detail(detail, limit=MAX_RUNTIME_ERROR_DETAIL)


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeOperationResult:
    """Immutable, non-authoritative receipt for one explicit runtime call."""

    request: RuntimeRequest
    provider: str
    response: RuntimeResponse | None
    observation: RuntimeObservation
    stages: tuple[str, ...]
    status: str
    endpoint: str
    response_hash: str | None = None
    schema_version: str = RUNTIME_OPERATION_SCHEMA
    id: str = ""
    automatic: bool = False
    retry_count: int = 0
    fallback_used: bool = False
    filesystem_mutation: bool = False
    canonical_mutation: bool = False
    progress: tuple[RuntimeProgressEvent, ...] = ()
    file_id: str | None = None
    slot_id: str | None = None
    attempt: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_OPERATION_SCHEMA:
            raise RuntimeOperationError("지원하지 않는 runtime operation schema입니다.")
        if not isinstance(self.request, RuntimeRequest):
            raise RuntimeOperationError("request는 RuntimeRequest이어야 합니다.")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise RuntimeOperationError("provider가 필요합니다.")
        provider = self.provider.strip().lower()
        object.__setattr__(self, "provider", provider)
        if not isinstance(self.response, RuntimeResponse | type(None)):
            raise RuntimeOperationError("response 타입이 잘못되었습니다.")
        if not isinstance(self.observation, RuntimeObservation):
            raise RuntimeOperationError("observation은 RuntimeObservation이어야 합니다.")
        if not isinstance(self.endpoint, str) or not self.endpoint.strip():
            raise RuntimeOperationError("endpoint가 필요합니다.")
        if self.status not in RUNTIME_OPERATION_STATUSES:
            raise RuntimeOperationError("지원하지 않는 runtime operation 상태입니다.")
        stages = tuple(self.stages)
        if self.status == "SUCCEEDED":
            if stages != RUNTIME_OPERATION_STAGES:
                raise RuntimeOperationError("성공 operation 단계 순서가 계약과 다릅니다.")
            if self.response is None:
                raise RuntimeOperationError("성공 operation에는 response가 필요합니다.")
        elif stages != RUNTIME_OPERATION_STAGES[:-1]:
            raise RuntimeOperationError("실패 operation 단계 순서가 계약과 다릅니다.")
        object.__setattr__(self, "stages", stages)

        if self.observation.request_id != self.request.request_id:
            raise RuntimeOperationError("runtime observation request identity가 다릅니다.")
        if self.observation.provider.strip().lower() != provider:
            raise RuntimeOperationError("runtime observation provider identity가 다릅니다.")
        if self.observation.endpoint != self.endpoint:
            raise RuntimeOperationError("runtime observation endpoint가 다릅니다.")
        if self.observation.status != self.status:
            raise RuntimeOperationError("runtime observation status가 operation과 다릅니다.")
        if self.status == "SUCCEEDED":
            assert self.response is not None
            if self.response.request_id != self.request.request_id:
                raise RuntimeOperationError("normalized response request identity가 다릅니다.")
            if self.response.provider.strip().lower() != provider:
                raise RuntimeOperationError("normalized response provider identity가 다릅니다.")
            expected_response_hash = _json_digest(_response_metadata(self.response))
            if self.response_hash is not None and self.response_hash != expected_response_hash:
                raise RuntimeOperationError("response metadata hash가 다릅니다.")
            object.__setattr__(self, "response_hash", expected_response_hash)
            if self.observation.error_kind is not None:
                raise RuntimeOperationError("성공 operation에는 error_kind가 없어야 합니다.")
        elif self.response is not None:
            raise RuntimeOperationError("실패 operation에는 normalized response가 없어야 합니다.")
        elif self.observation.error_kind is None:
            raise RuntimeOperationError("실패 operation에는 error_kind가 필요합니다.")
        for value, label in (
            (self.automatic, "operation.automatic"),
            (self.fallback_used, "operation.fallback_used"),
            (self.filesystem_mutation, "operation.filesystem_mutation"),
            (self.canonical_mutation, "operation.canonical_mutation"),
        ):
            if not isinstance(value, bool) or value:
                raise RuntimeOperationError(f"{label}은(는) false여야 합니다.")
        if not isinstance(self.retry_count, int) or self.retry_count != 0:
            raise RuntimeOperationError("retry_count는 0이어야 합니다.")
        for value, label in ((self.file_id, "file_id"), (self.slot_id, "slot_id")):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise RuntimeOperationError(f"{label}은(는) 비어 있지 않은 문자열이어야 합니다.")
        if not isinstance(self.attempt, int) or isinstance(self.attempt, bool) or self.attempt < 1:
            raise RuntimeOperationError("attempt는 1 이상의 정수여야 합니다.")

        progress = tuple(self.progress)
        if any(not isinstance(event, RuntimeProgressEvent) for event in progress):
            raise RuntimeOperationError("progress에는 RuntimeProgressEvent만 허용됩니다.")
        if progress:
            if progress[0].state != "PREPARING":
                raise RuntimeOperationError("progress는 PREPARING에서 시작해야 합니다.")
            for previous, current in pairwise(progress):
                if current.request_id != self.request.request_id:
                    raise RuntimeOperationError("progress request identity가 요청과 다릅니다.")
                if current.state not in _PROGRESS_TRANSITIONS[previous.state]:
                    raise RuntimeOperationError("progress 단계 순서가 계약과 다릅니다.")
            if progress[0].request_id != self.request.request_id:
                raise RuntimeOperationError("progress request identity가 요청과 다릅니다.")
            terminal = progress[-1].state
            if self.status == "SUCCEEDED" and terminal != "COMPLETED":
                raise RuntimeOperationError("성공 operation progress는 COMPLETED로 끝나야 합니다.")
            if self.status == "FAILED" and terminal not in {"FAILED", "CANCELLED"}:
                raise RuntimeOperationError("실패 operation progress는 FAILED/CANCELLED로 끝나야 합니다.")
        object.__setattr__(self, "progress", progress)

        operation_payload = {
            "schema_version": self.schema_version,
            "request_id": self.request.request_id,
            "provider": provider,
            "model": self.request.model,
            "status": self.status,
            "endpoint": self.endpoint,
            "stages": list(stages),
            "response_hash": self.response_hash,
            "file_id": self.file_id,
            "slot_id": self.slot_id,
            "attempt": self.attempt,
        }
        expected_id = f"runtime-operation:{_json_digest(operation_payload).removeprefix('sha256:')}"
        if self.id and self.id != expected_id:
            raise RuntimeOperationError("runtime operation id가 payload와 일치하지 않습니다.")
        object.__setattr__(self, "id", expected_id)

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCEEDED"

    @property
    def failed(self) -> bool:
        return self.status == "FAILED"

    @property
    def last_progress(self) -> RuntimeProgressEvent | None:
        return self.progress[-1] if self.progress else None

    @property
    def elapsed_seconds(self) -> float | None:
        try:
            started = datetime.fromisoformat(self.observation.started_at)
            finished = datetime.fromisoformat(self.observation.finished_at)
        except (TypeError, ValueError):
            return None
        return max(0.0, (finished - started).total_seconds())

    @property
    def provider_progress(self) -> RuntimeProgressEvidence:
        if self.response is None:
            return RuntimeProgressEvidence(
                status="UNOBSERVED",
                source="non_streaming_wait",
                observed_at=self.observation.finished_at,
                detail="비스트리밍 대기 중 provider 내부 진행은 관측되지 않았습니다.",
            )
        raw_tokens = self.response.usage.get("completion_tokens")
        completion_tokens = raw_tokens if isinstance(raw_tokens, int) and not isinstance(raw_tokens, bool) and raw_tokens >= 0 else None
        if completion_tokens is None:
            return RuntimeProgressEvidence(
                status="UNOBSERVED",
                source="non_streaming_response",
                observed_at=self.observation.finished_at,
                detail="응답은 받았지만 provider usage에 completion token 진행 근거가 없습니다.",
            )
        return RuntimeProgressEvidence(
            status="OBSERVED_AFTER_RESPONSE",
            source="response.usage",
            observed_at=self.observation.finished_at,
            completion_tokens=completion_tokens,
            detail="completion token usage는 응답 수신 후 관측된 값이며 in-flight 진행률이 아닙니다.",
        )

    @property
    def response_received_at(self) -> str | None:
        event = next((item for item in self.progress if item.state == "RESPONSE_RECEIVED"), None)
        return event.observed_at if event else None

    @property
    def exit_reason(self) -> str:
        return "completed" if self.succeeded else (self.observation.error_kind.value if self.observation.error_kind else "failed")

    @property
    def server_stop_observed(self) -> bool | None:
        """Return only what this caller observed about provider shutdown."""

        return False if self.failed else None

    @property
    def validation_result(self) -> dict[str, str]:
        if self.succeeded:
            return {"status": "RESPONSE_NORMALIZED", "source": "runtime.operation"}
        if any(item.state == "VALIDATING" for item in self.progress):
            return {"status": "VALIDATION_FAILED", "source": "runtime.operation"}
        return {"status": "NOT_RUN", "source": "runtime.operation"}

    def require_response(self) -> RuntimeResponse:
        if self.response is None or not self.succeeded:
            detail = self.observation.detail or "runtime operation이 실패했습니다."
            raise RuntimeOperationError(detail)
        return self.response

    def to_record(self) -> dict[str, Any]:
        response_record: dict[str, Any] | None = None
        if self.response is not None:
            metadata = _response_metadata(self.response)
            response_record = {
                "request_id": metadata["request_id"],
                "provider": metadata["provider"],
                "model": metadata["model"],
                "text_sha256": metadata["text_sha256"],
                "text_length": metadata["text_length"],
                "usage": metadata["usage"],
                "finish_reason": metadata["finish_reason"],
            }
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "request_id": self.request.request_id,
            "provider": self.provider,
            "requested_model": self.request.model,
            "endpoint": self.endpoint,
            "status": self.status,
            "stages": list(self.stages),
            "observation": self.observation.to_dict(),
            "response_hash": self.response_hash,
            "response_metadata": response_record,
            "automatic": False,
            "retry_count": 0,
            "fallback_used": False,
            "filesystem_mutation": False,
            "canonical_mutation": False,
            "progress": [event.to_dict() for event in self.progress],
            "last_observation": self.last_progress.to_dict() if self.last_progress else None,
            "elapsed_seconds": self.elapsed_seconds,
            "provider_progress": self.provider_progress.to_dict(),
            "termination": {
                "exit_reason": self.exit_reason,
                "server_stop_observed": self.server_stop_observed,
                "source": "runtime.operation.caller_observation",
            },
            "trace_context": {
                "request_id": self.request.request_id,
                "file_id": self.file_id,
                "slot_id": self.slot_id,
                "attempt": self.attempt,
                "model_id": self.request.model,
                "requested_at": self.observation.started_at,
                "response_at": self.response_received_at,
                "exit_reason": self.exit_reason,
                "validation": self.validation_result,
            },
        }


def run_explicit_runtime_operation(
    request: RuntimeRequest,
    *,
    provider: str,
    complete: RuntimeCompleter,
    dispatch_approved: bool,
    endpoint: str | None = None,
    clock: Callable[[], str] = utc_now,
    file_id: str | None = None,
    slot_id: str | None = None,
    attempt: int = 1,
) -> RuntimeOperationResult:
    """Perform exactly one explicitly approved completion and return its receipt."""

    if not isinstance(request, RuntimeRequest):
        raise RuntimeOperationError("request는 RuntimeRequest이어야 합니다.")
    if not isinstance(provider, str) or not provider.strip():
        raise RuntimeOperationError("provider가 필요합니다.")
    normalized_provider = provider.strip().lower()
    if dispatch_approved is not True:
        raise RuntimeOperationError("runtime operation에는 dispatch_approved=true가 필요합니다.")
    if not callable(complete):
        raise RuntimeOperationError("complete는 호출 가능한 provider completer이어야 합니다.")
    normalized_endpoint = str(endpoint or f"provider://{normalized_provider}").strip()
    if not normalized_endpoint:
        raise RuntimeOperationError("endpoint가 필요합니다.")
    if not callable(clock):
        raise RuntimeOperationError("clock은 호출 가능해야 합니다.")
    for value, label in ((file_id, "file_id"), (slot_id, "slot_id")):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise RuntimeOperationError(f"{label}은(는) 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise RuntimeOperationError("attempt는 1 이상의 정수여야 합니다.")

    started_at = clock()
    progress: list[RuntimeProgressEvent] = []

    def mark(state: str, observed_at: str, detail: str) -> None:
        progress.append(
            RuntimeProgressEvent(
                request_id=request.request_id,
                state=state,
                observed_at=observed_at,
                source="runtime.operation",
                detail=detail,
            )
        )

    mark("PREPARING", started_at, "explicit runtime request is prepared")
    completed_stages = RUNTIME_OPERATION_STAGES[:-1]
    mark("REQUEST_SENT", clock(), "explicit completer boundary entered")
    mark("WAITING_RESPONSE", clock(), "waiting for provider completer to return")
    terminal_state = "FAILED"
    try:
        response = complete(request)
        mark("RESPONSE_RECEIVED", clock(), "provider completer returned")
        mark("VALIDATING", clock(), "validating normalized response identity")
        if not isinstance(response, RuntimeResponse):
            raise RuntimeOperationError("provider가 RuntimeResponse를 반환하지 않았습니다.")
        if response.request_id != request.request_id:
            raise RuntimeOperationError("provider response request identity가 요청과 다릅니다.")
        if response.provider.strip().lower() != normalized_provider:
            raise RuntimeOperationError("provider response identity가 선택 provider와 다릅니다.")
        finished_at = clock()
        mark("COMPLETED", finished_at, "normalized response accepted")
        observation = RuntimeObservation(
            request_id=request.request_id,
            provider=normalized_provider,
            endpoint=normalized_endpoint,
            model=response.model,
            status="SUCCEEDED",
            started_at=started_at,
            finished_at=finished_at,
        )
        return RuntimeOperationResult(
            request=request,
            provider=normalized_provider,
            response=response,
            observation=observation,
            stages=RUNTIME_OPERATION_STAGES,
            status="SUCCEEDED",
            endpoint=normalized_endpoint,
            progress=tuple(progress),
            file_id=file_id,
            slot_id=slot_id,
            attempt=attempt,
        )
    except RuntimeOperationCancelled as exc:
        error_kind = RuntimeErrorKind.CANCELLED
        detail = str(exc) or "caller cancellation observed; server stop not observed"
        terminal_state = "CANCELLED"
    except KeyboardInterrupt as exc:
        error_kind = RuntimeErrorKind.CANCELLED
        detail = str(exc) or "caller cancellation observed; server stop not observed"
        terminal_state = "CANCELLED"
    except RuntimeOperationError as exc:
        error_kind = RuntimeErrorKind.MALFORMED_RESPONSE
        detail = str(exc)
    except RuntimeAdapterError as exc:
        error_kind = exc.kind
        detail = str(exc)
    except ValueError as exc:
        error_kind = RuntimeErrorKind.PROVIDER
        detail = str(exc)
    except (OSError, RuntimeError) as exc:  # pragma: no cover - defensive provider boundary
        error_kind = RuntimeErrorKind.UNKNOWN
        detail = str(exc)
    except (AttributeError, IndexError, KeyError, TypeError) as exc:  # pragma: no cover - defensive provider boundary
        error_kind = RuntimeErrorKind.UNKNOWN
        detail = str(exc)

    finished_at = clock()
    mark(
        terminal_state,
        finished_at,
        "caller cancellation observed; server stop not observed"
        if terminal_state == "CANCELLED"
        else "runtime operation failed",
    )
    observation = RuntimeObservation(
        request_id=request.request_id,
        provider=normalized_provider,
        endpoint=normalized_endpoint,
        model=request.model,
        status="FAILED",
        started_at=started_at,
        finished_at=finished_at,
        error_kind=error_kind,
        detail=_bounded_detail(detail),
    )
    return RuntimeOperationResult(
        request=request,
        provider=normalized_provider,
        response=None,
        observation=observation,
        stages=completed_stages,
        status="FAILED",
        endpoint=normalized_endpoint,
        progress=tuple(progress),
        file_id=file_id,
        slot_id=slot_id,
        attempt=attempt,
    )


__all__ = [
    "MAX_RUNTIME_ERROR_DETAIL",
    "RUNTIME_OPERATION_SCHEMA",
    "RUNTIME_OPERATION_STAGES",
    "RUNTIME_OPERATION_STATUSES",
    "RuntimeCompleter",
    "RuntimeOperationCancelled",
    "RuntimeOperationError",
    "RuntimeOperationResult",
    "run_explicit_runtime_operation",
]
