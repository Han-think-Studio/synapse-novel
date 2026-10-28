"""Explicit local model calls for proposal-only Canon claim labels."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from synapse.core.canon_claims import Claim
from synapse.core.canon_labels import LABELS
from synapse.runtime.contracts import RuntimeRequest, RuntimeResponse, response_was_truncated
from synapse.runtime.lmstudio import LMStudioAdapter

MAX_BATCH_SIZE = 20
MAX_MISSING_RETRIES = 2
BATCH_TIMEOUT_SECONDS = 600.0
MAX_OUTPUT_TOKENS = 8192

_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string", "enum": list(LABELS)},
                },
                "required": ["id", "label"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class LabelRuntimeResult:
    """Model proposals and per-batch failures; no field is a human confirmation."""

    labels: Mapping[str, str]
    errors: tuple[Mapping[str, Any], ...]
    attempted_requests: int

    @property
    def failed_claim_ids(self) -> tuple[str, ...]:
        return tuple(claim_id for error in self.errors for claim_id in error["claim_ids"])


Complete = Callable[[RuntimeRequest], RuntimeResponse | str]


def _strict_json_loads(text: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"nonstandard JSON number: {value}")

    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (TypeError, ValueError) as exc:
        raise ValueError("response is not strict JSON") from exc


def _parse_labels(text: str, requested_ids: frozenset[str]) -> dict[str, str]:
    payload = _strict_json_loads(text)
    if not isinstance(payload, dict) or set(payload) != {"labels"}:
        raise ValueError("response must contain only the labels property")
    if not isinstance(payload["labels"], list):
        raise TypeError("labels must be an array")
    found: dict[str, str] = {}
    for index, item in enumerate(payload["labels"]):
        if not isinstance(item, dict) or set(item) != {"id", "label"}:
            raise ValueError(f"labels[{index}] must contain only id and label")
        claim_id, label = item.get("id"), item.get("label")
        if not isinstance(claim_id, str) or claim_id not in requested_ids:
            raise ValueError(f"labels[{index}].id is not one of the requested IDs")
        if claim_id in found:
            raise ValueError(f"labels[{index}].id is duplicated")
        if not isinstance(label, str) or label not in LABELS:
            raise ValueError(f"labels[{index}].label is outside the closed label list")
        found[claim_id] = label
    return found


def _request(claims: Sequence[Claim], *, model: str, request_id: str) -> RuntimeRequest:
    listing = json.dumps(
        [{"id": claim.id, "section": claim.section, "text": claim.text[:500]} for claim in claims],
        ensure_ascii=False,
    )
    schema = deepcopy(_SCHEMA)
    schema["properties"]["labels"]["items"]["properties"]["id"]["enum"] = [
        claim.id for claim in claims
    ]
    prompt = (
        "Label each supplied claim with one closed-list label. These are proposals only. "
        "prohibition=must not; requirement=must; character_fact=character fact; "
        "state_value=value tied to a time axis; style_guidance=style; work_info=work metadata; "
        "sequence_step=ordered step; space=place; review_item=checklist item; "
        "reference_entry=reference or dictionary entry; other=none of these. "
        "Return every supplied id exactly once as JSON. Copy the complete id string unchanged, "
        "including its prefix and colon; never shorten or reinterpret an id.\n\n" + listing
    )
    return RuntimeRequest(
        request_id=request_id,
        model=model,
        messages=({"role": "user", "content": prompt},),
        parameters={
            "temperature": 0.1,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "reasoning_effort": "none",
        },
        response_schema=schema,
        timeout_seconds=BATCH_TIMEOUT_SECONDS,
    )


def label_claims(
    claims: Sequence[Claim],
    *,
    model: str,
    adapter: LMStudioAdapter | None = None,
    complete: Complete | None = None,
) -> LabelRuntimeResult:
    """Label claims in bounded batches, retrying only IDs omitted by a response.

    Calls are made only when this function is explicitly invoked. A transport error
    fails that batch without replay; a received but incomplete response retries only
    its missing IDs, at most twice.
    """

    if not isinstance(model, str) or not model.strip():
        raise ValueError("model is required")
    if complete is None:
        selected = adapter or LMStudioAdapter()
        complete = selected.complete

    labels: dict[str, str] = {}
    errors: list[Mapping[str, Any]] = []
    attempted = 0
    for batch_index, start in enumerate(range(0, len(claims), MAX_BATCH_SIZE)):
        batch = tuple(claims[start : start + MAX_BATCH_SIZE])
        pending = {claim.id: claim for claim in batch}
        for retry in range(MAX_MISSING_RETRIES + 1):
            if not pending:
                break
            request = _request(
                tuple(pending.values()),
                model=model.strip(),
                request_id=f"canon-label:{batch_index}:{retry}",
            )
            attempted += 1
            try:
                response = complete(request)
            except Exception as exc:  # noqa: BLE001 -- a failed batch remains unlabeled and is recorded
                errors.append(
                    {
                        "batch": batch_index,
                        "claim_ids": list(pending),
                        "error": f"{type(exc).__name__}: {exc}",
                        "retryable": False,
                    }
                )
                break
            if isinstance(response, RuntimeResponse):
                if response.request_id != request.request_id:
                    errors.append(
                        {
                            "batch": batch_index,
                            "claim_ids": list(pending),
                            "error": "response request_id does not match the active request",
                            "expected_request_id": request.request_id,
                            "received_request_id": response.request_id,
                            "retryable": False,
                        }
                    )
                    break
                if response_was_truncated(response):
                    errors.append(
                        {
                            "batch": batch_index,
                            "claim_ids": list(pending),
                            "error": "response was truncated at the output limit",
                            "finish_reason": response.finish_reason,
                            "retryable": False,
                        }
                    )
                    break
                finish_reason = str(response.finish_reason or "").strip().lower()
                if finish_reason not in {"stop", "completed"}:
                    errors.append(
                        {
                            "batch": batch_index,
                            "claim_ids": list(pending),
                            "error": "response did not finish normally",
                            "finish_reason": response.finish_reason,
                            "retryable": False,
                        }
                    )
                    break
                text = response.text
            elif isinstance(response, str):
                text = response
            else:
                errors.append(
                    {
                        "batch": batch_index,
                        "claim_ids": list(pending),
                        "error": "response must be RuntimeResponse or text",
                        "retryable": False,
                    }
                )
                break
            try:
                received = _parse_labels(text, frozenset(pending))
            except (TypeError, ValueError) as exc:
                errors.append(
                    {
                        "batch": batch_index,
                        "claim_ids": list(pending),
                        "error": str(exc),
                        "retryable": False,
                    }
                )
                break
            labels.update(received)
            for claim_id in received:
                pending.pop(claim_id, None)
            if pending and retry == MAX_MISSING_RETRIES:
                errors.append(
                    {
                        "batch": batch_index,
                        "claim_ids": list(pending),
                        "error": "model response omitted claim IDs after two retries",
                        "retryable": False,
                    }
                )
    return LabelRuntimeResult(labels=labels, errors=tuple(errors), attempted_requests=attempted)


__all__ = ["LabelRuntimeResult", "label_claims"]
