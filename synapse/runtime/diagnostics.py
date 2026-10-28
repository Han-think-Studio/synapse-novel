"""Bounded, read-only classification for one explicit runtime observation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from synapse.runtime.contracts import RuntimeErrorKind


class RuntimeFailureCode(str, Enum):
    """Failure labels supported by the M4-03 diagnostic boundary."""

    SERVER_NOT_RUNNING = "server_not_running"
    ADDRESS_ERROR = "address_error"
    MODEL_NOT_FOUND = "model_not_found"
    AUTHENTICATION_FAILED = "authentication_failed"
    RESPONSE_FORMAT_UNSUPPORTED = "response_format_unsupported"
    TIMEOUT = "timeout"
    CONNECTION_CLOSED = "connection_closed"
    CANCELLED = "cancelled"
    TRANSPORT_ERROR = "transport_error"
    MALFORMED_RESPONSE = "malformed_response"
    RATE_LIMIT = "rate_limit"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN = "unknown"


_STAGES = frozenset({"connection", "model_selection", "structured_response"})
_SERVER_NOT_RUNNING_MARKERS = (
    "10061",
    "connection refused",
    "actively refused",
    "연결을 거부",
    "연결이 거부",
)
_ADDRESS_ERROR_MARKERS = (
    "getaddrinfo",
    "name or service not known",
    "no such host",
    "nodename nor servname",
    "invalid url",
    "unsupported url",
    "호스트를 찾을 수",
    "주소가 잘못",
)
_RESPONSE_FORMAT_MARKERS = (
    "response_format",
    "json_schema",
    "structured output",
    "structured response",
    "응답 형식",
    "json schema",
    "지원하지 않",
)


@dataclass(frozen=True, slots=True)
class RuntimeFailure:
    """One bounded diagnosis; it contains no provider response body."""

    stage: str
    code: RuntimeFailureCode
    detail: str
    scope: str = "specified_target_only"

    def __post_init__(self) -> None:
        if self.stage not in _STAGES:
            raise ValueError(f"지원하지 않는 runtime 진단 단계입니다: {self.stage}")
        if not self.detail.strip():
            raise ValueError("runtime 진단 detail이 필요합니다.")
        object.__setattr__(self, "detail", self.detail.strip()[:400])

    def to_record(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "code": self.code.value,
            "detail": self.detail,
            "scope": self.scope,
        }


def _lower(detail: str) -> str:
    return detail.casefold()


def classify_runtime_failure(
    *,
    stage: str,
    error_kind: RuntimeErrorKind | None = None,
    detail: str = "",
    selected_model: str | None = None,
    available_models: Sequence[str] = (),
    model_list_observed: bool = False,
) -> RuntimeFailure | None:
    """Classify one explicit observation without probing anything else.

    ``model_list_observed`` is deliberately explicit: an empty list that was
    never successfully observed cannot prove that a model is missing.
    """

    if stage not in _STAGES:
        raise ValueError(f"지원하지 않는 runtime 진단 단계입니다: {stage}")
    normalized_detail = detail.strip() or "runtime failure was observed"
    lowered = _lower(normalized_detail)

    if stage == "model_selection" and model_list_observed:
        requested = (selected_model or "").strip()
        known = {item.strip() for item in available_models if isinstance(item, str) and item.strip()}
        if requested and requested not in known:
            return RuntimeFailure(stage=stage, code=RuntimeFailureCode.MODEL_NOT_FOUND, detail=normalized_detail)

    if error_kind is RuntimeErrorKind.AUTHENTICATION:
        code = RuntimeFailureCode.AUTHENTICATION_FAILED
    elif error_kind is RuntimeErrorKind.RATE_LIMIT:
        code = RuntimeFailureCode.RATE_LIMIT
    elif error_kind is RuntimeErrorKind.TIMEOUT:
        code = RuntimeFailureCode.TIMEOUT
    elif error_kind is RuntimeErrorKind.CONNECTION_CLOSED:
        code = RuntimeFailureCode.CONNECTION_CLOSED
    elif error_kind is RuntimeErrorKind.CANCELLED:
        code = RuntimeFailureCode.CANCELLED
    elif error_kind is RuntimeErrorKind.CONFIGURATION:
        code = RuntimeFailureCode.ADDRESS_ERROR
    elif error_kind is RuntimeErrorKind.TRANSPORT:
        if any(marker in lowered for marker in _SERVER_NOT_RUNNING_MARKERS):
            code = RuntimeFailureCode.SERVER_NOT_RUNNING
        elif any(marker in lowered for marker in _ADDRESS_ERROR_MARKERS):
            code = RuntimeFailureCode.ADDRESS_ERROR
        else:
            code = RuntimeFailureCode.TRANSPORT_ERROR
    elif stage == "structured_response" and any(marker in lowered for marker in _RESPONSE_FORMAT_MARKERS):
        code = RuntimeFailureCode.RESPONSE_FORMAT_UNSUPPORTED
    elif error_kind is RuntimeErrorKind.MALFORMED_RESPONSE:
        code = RuntimeFailureCode.MALFORMED_RESPONSE
    elif error_kind is RuntimeErrorKind.PROVIDER:
        code = RuntimeFailureCode.PROVIDER_ERROR
    elif error_kind is RuntimeErrorKind.UNKNOWN:
        code = RuntimeFailureCode.UNKNOWN
    else:
        return None
    return RuntimeFailure(stage=stage, code=code, detail=normalized_detail)


__all__ = ["RuntimeFailure", "RuntimeFailureCode", "classify_runtime_failure"]
