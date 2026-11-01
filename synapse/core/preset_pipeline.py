"""Fixed-preset task pipeline: explicit preset -> one bounded packet per task -> fixed JSON -> review.

The 18 presets under ``domain_packs/_presets/independent`` are the only selectable categories.
Each preset has at most five tasks. A task is exported as one ``TaskPacket``, answered by a
model with a JSON object whose shape the server enforces (``response_schema``), re-checked
strictly here, and accepted or rejected by a person before a dependent task may start.

Nothing in this module chooses a preset, calls a model or writes a file on its own. A model is
called only through ``synapse.core.preset_dispatch``, one attempt per call through the core's
one-task entry (DEC-0245); here an answer already in hand is recorded (``record_answer_attempt``),
an attempt whose outcome is unknown is held (``record_unknown_attempt``) until a person says how
it ended (``resolve_unknown``), and a review holds the attempt and the hash of what it accepted.
Model output is a review candidate, never canon.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from synapse.core.packets import TaskPacket
from synapse.runtime.contracts import RuntimeRequest, copy_json_data

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "domain_packs" / "_presets" / "independent"
PIPELINE_VERSION = 1
RUN_KIND = "preset_run"
RUN_SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3
MAX_RAW_TEXT = 24_000
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_RUN_ID = re.compile(r"^run-[0-9a-f]{12}$")
_SOURCE_STATUSES = frozenset({"CONFIRMED", "ASSUMED", "PROPOSED", "UNRESOLVED"})
_REVIEWERS = frozenset({"operator", "user"})
_RESPONSE_KEYS = frozenset({"status", "fields", "evidence_ids", "unresolved"})


class PresetPipelineError(ValueError):
    """A preset, context, response or run record was rejected."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PresetPipelineError(message)


def read_json(path: Path, limit: int = 200_000) -> dict:
    raw = path.read_bytes()
    require(len(raw) <= limit, f"input exceeds byte limit: {path.name}")
    value = json.loads(raw.decode("utf-8"))
    require(isinstance(value, dict), "expected JSON object")
    return value


def encoded(value: object) -> bytes:
    # A request carries its schema frozen; ``copy_json_data`` is the one way to turn that back into plain JSON.
    return json.dumps(
        copy_json_data(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def digest(value: object) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def texts(value: object, *, nonempty: bool = True) -> bool:
    return isinstance(value, list) and (bool(value) or not nonempty) and all(text(v) for v in value)


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _refuse_constant(name: str) -> Any:
    raise PresetPipelineError(f"non-standard JSON number: {name}")


def strict_json_loads(raw: str) -> Any:
    """Parse JSON without duplicate keys, NaN/Infinity, or trailing text."""

    try:
        return json.loads(
            raw, object_pairs_hook=_refuse_duplicates, parse_constant=_refuse_constant
        )
    except json.JSONDecodeError as exc:
        raise PresetPipelineError(f"response is not one JSON value: {exc.msg}") from exc


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------------------------


def preset_ids() -> tuple[str, ...]:
    ids = read_json(PACK_ROOT / "catalog.json")["presets"]
    require(texts(ids) and len(set(ids)) == len(ids), "invalid preset catalog")
    return tuple(ids)


def load_preset(preset_id: str) -> tuple[dict, dict, str]:
    """Return ``(common, preset, digest)`` for one explicitly named preset."""

    require(isinstance(preset_id, str) and bool(_ID.fullmatch(preset_id)), "invalid preset id")
    require(preset_id in preset_ids(), "unknown preset")
    common = read_json(PACK_ROOT / "common.json")
    preset = read_json(PACK_ROOT / "presets" / f"{preset_id}.json")
    require(preset["id"] == preset_id and preset["schema_version"] == 1, "preset identity mismatch")
    require(text(preset["version"]) and texts(preset["required_inputs"]), "invalid preset metadata")
    require(
        isinstance(preset["origin"], dict) and preset["origin"].get("id") == preset_id,
        "origin identity mismatch",
    )
    tasks = preset["tasks"]
    require(isinstance(tasks, list) and 1 <= len(tasks) <= 5, "invalid tasks")
    seen: set[str] = set()
    for task in tasks:
        require(
            bool(_ID.fullmatch(task["id"])) and task["id"] not in seen, "duplicate/invalid task"
        )
        require(texts(task["depends_on"], nonempty=False), "invalid dependencies")
        require(len(set(task["depends_on"])) == len(task["depends_on"]), "duplicate dependency")
        require(set(task["depends_on"]) <= seen, "dependency must precede task")
        fields = task["fields"]
        require(isinstance(fields, dict) and 1 <= len(fields) <= 5, "invalid fields")
        require(all(text(k) and text(v) for k, v in fields.items()), "empty output field")
        for key in ("questions", "do", "do_not", "checks"):
            require(texts(task[key]), f"task {task['id']} missing {key}")
        require(text(task["title"]), "task title required")
        seen.add(task["id"])
    basis = {"pipeline": PIPELINE_VERSION, "common": common, "preset": preset}
    return common, preset, digest(basis)


def list_presets(edition_id: str | None = None) -> tuple[dict, ...]:
    """List all presets, or only those assigned to one registered edition."""

    if edition_id is None:
        selected_ids = preset_ids()
    else:
        from synapse.core.editions import edition

        selected_ids = edition(edition_id).presets
    result = []
    for preset_id in selected_ids:
        _, preset, preset_digest = load_preset(preset_id)
        result.append(
            {
                "id": preset_id,
                "label": preset["label"],
                "purpose": preset["purpose"],
                "version": preset["version"],
                "digest": preset_digest,
                "required_inputs": list(preset["required_inputs"]),
                "tasks": [
                    {"id": t["id"], "title": t["title"], "depends_on": list(t["depends_on"])}
                    for t in preset["tasks"]
                ],
            }
        )
    return tuple(result)


def task_for(preset: dict, task_id: str) -> dict:
    for task in preset["tasks"]:
        if task["id"] == task_id:
            return task
    raise PresetPipelineError("unknown task")


# --------------------------------------------------------------------------------------------
# Context, binding, validation
# --------------------------------------------------------------------------------------------


def validate_context(context: dict, common: dict) -> str:
    require(isinstance(context, dict), "context must be an object")
    require(
        set(context) == {"project_id", "objective", "project_rules", "inputs"},
        "context keys mismatch",
    )
    require(text(context["project_id"]) and text(context["objective"]), "missing project/objective")
    require(
        texts(context["project_rules"], nonempty=False), "project_rules must be an explicit list"
    )
    require(isinstance(context["inputs"], list), "inputs must be a list")
    seen: set[str] = set()
    for item in context["inputs"]:
        require(isinstance(item, dict), "invalid input record")
        require(set(item) == {"id", "source", "status", "content"}, "input keys mismatch")
        require(all(text(v) for v in item.values()), "input fields must be nonempty strings")
        require(item["id"] not in seen, "duplicate evidence id")
        require(item["status"] in _SOURCE_STATUSES, "invalid source status")
        seen.add(item["id"])
    require(
        len(encoded(context)) <= common["limits"]["max_context_utf8_bytes"],
        "context exceeds budget; select shorter explicit excerpts",
    )
    return digest(context)


def binding(
    preset: dict,
    task: dict,
    preset_digest: str,
    context: dict,
    context_digest: str,
    previous: list[dict],
) -> dict:
    return {
        "project_id": context["project_id"],
        "preset_id": preset["id"],
        "preset_digest": preset_digest,
        "context_digest": context_digest,
        "task_id": task["id"],
        "previous_digest": digest(previous),
    }


def validated_previous(preset: dict, task: dict, context: dict, previous: list[dict]) -> list[dict]:
    require(
        all(isinstance(p, dict) and text(p.get("task_id")) for p in previous),
        "invalid previous result",
    )
    prior_ids = [p["task_id"] for p in previous]
    require(len(prior_ids) == len(set(prior_ids)), "duplicate previous result")
    require(
        set(prior_ids) == set(task["depends_on"]), "supply exactly the required previous results"
    )
    prior_map = {p["task_id"]: p for p in previous}
    ordered = [prior_map[i] for i in task["depends_on"]]
    for result in ordered:
        ancestors = task_for(preset, result["task_id"])["depends_on"]
        require(set(ancestors) <= set(prior_map), "missing prerequisite ancestry")
        validate_response(
            result,
            preset_id=preset["id"],
            task_id=result["task_id"],
            context=context,
            previous=[prior_map[i] for i in ancestors],
        )
        require(result["status"] == "PROPOSED", "unresolved prerequisite blocks next task")
    return ordered


def validate_response(
    response: dict,
    *,
    preset_id: str,
    task_id: str,
    context: dict,
    previous: list[dict] | None = None,
) -> dict:
    common, preset, preset_digest = load_preset(preset_id)
    context_digest = validate_context(context, common)
    task = task_for(preset, task_id)
    ordered = validated_previous(preset, task, context, previous or [])
    expected = binding(preset, task, preset_digest, context, context_digest, ordered)
    require(isinstance(response, dict), "response must be an object")
    require(set(response) == set(expected) | _RESPONSE_KEYS, "response keys mismatch")
    require(all(response[k] == v for k, v in expected.items()), "stale or cross-project response")
    require(response["status"] in common["output_contract"]["status"], "invalid response status")
    fields = response["fields"]
    require(
        isinstance(fields, dict) and set(fields) == set(task["fields"]), "response field mismatch"
    )
    require(
        all(text(v) and len(v) <= common["limits"]["max_field_chars"] for v in fields.values()),
        "empty/oversize field",
    )
    refs = response["evidence_ids"]
    require(texts(refs, nonempty=False), "invalid evidence refs")
    require(
        len(refs) == len(set(refs)) and len(refs) <= common["limits"]["max_evidence_refs"],
        "duplicate/oversize evidence refs",
    )
    require(set(refs) <= {i["id"] for i in context["inputs"]}, "invented evidence id")
    require(texts(response["unresolved"], nonempty=False), "invalid unresolved list")
    if response["status"] == "UNRESOLVED":
        require(bool(response["unresolved"]), "UNRESOLVED requires questions")
    else:
        require(
            not response["unresolved"] and bool(refs),
            "PROPOSED requires evidence and no unresolved questions",
        )
    require(
        len(encoded(response)) <= common["limits"]["max_response_utf8_bytes"],
        "response exceeds budget",
    )
    return {
        "structure": "PASS",
        "domain_quality": "UNASSESSED",
        "canonical_approval": False,
        "candidate_status": response["status"],
    }


# --------------------------------------------------------------------------------------------
# Packet, fixed JSON shape and model request
# --------------------------------------------------------------------------------------------


def build_packet(
    preset_id: str, task_id: str, context: dict, previous: list[dict] | None = None
) -> TaskPacket:
    common, preset, preset_digest = load_preset(preset_id)
    context_digest = validate_context(context, common)
    task = task_for(preset, task_id)
    ordered = validated_previous(preset, task, context, previous or [])
    expected = binding(preset, task, preset_digest, context, context_digest, ordered)
    response_shape = dict(
        expected,
        status="UNRESOLVED",
        fields={k: "" for k in task["fields"]},
        evidence_ids=[],
        unresolved=[],
    )
    record = {
        "task_id": f"{preset_id}.{task_id}.{digest(expected)[:16]}",
        "objective": f"{context['objective']} — {task['title']}",
        "scope": {"preset_id": preset_id, "task_id": task_id, "mode": "proposal_only"},
        "relevant_paths": [preset["origin"]["path"]],
        "known_state": {
            "context": context,
            "previous_results": ordered,
            "input_status_note": "Caller-declared status; not independently verified.",
        },
        "constraints": {
            "common": common["rules"],
            "do": task["do"],
            "do_not": task["do_not"],
            "required_input_guide": preset["required_inputs"],
            "questions": task["questions"],
            "output_fields": task["fields"],
            "response_shape": response_shape,
            "output_contract": common["output_contract"],
            "limits": common["limits"],
        },
        "acceptance_criteria": task["checks"],
        "tests": {"structural": "validate_response", "domain_checks": "human review required"},
        "stop_conditions": [
            "missing or conflicting evidence",
            "unresolved prerequisites",
            "budget exceeded",
        ],
        "preferred_capability": "bounded_structured_text",
        "context_budget_hint": 4096,
        "preset_binding": expected,
        "budget_note": "4096 is a planning hint, not a tokenizer measurement. Enforced limits are UTF-8 bytes.",
    }
    # TaskPacket freezes the envelope, not arbitrary nested caller dictionaries.
    packet = TaskPacket.from_record(json.loads(encoded(record)))
    require(
        len(encoded(packet.to_record())) <= common["limits"]["max_packet_utf8_bytes"],
        "packet exceeds budget; reduce explicit context",
    )
    return packet


def response_schema(preset: dict, task: dict, context: dict, common: dict) -> dict:
    """The only JSON object a model may return; binding hashes are added by Python, not the model.

    The two statuses are separate shapes so an impossible pair (``PROPOSED`` without evidence, or
    ``UNRESOLVED`` without a question) cannot be produced: PROPOSED needs one or more supplied
    evidence ids and no questions; UNRESOLVED needs one or more questions.
    """

    ids = [item["id"] for item in context["inputs"]]
    limits = common["limits"]
    field_schema = {"type": "string", "minLength": 1, "maxLength": limits["max_field_chars"]}
    fields = {
        "type": "object",
        "additionalProperties": False,
        "required": list(task["fields"]),
        "properties": {name: dict(field_schema) for name in task["fields"]},
    }
    evidence_items: dict[str, Any] = {"type": "string", "enum": ids} if ids else {"type": "string"}
    max_refs = min(limits["max_evidence_refs"], len(ids))
    question = {"type": "string", "minLength": 1, "maxLength": limits["max_field_chars"]}

    def shape(status: str, evidence: dict, unresolved: dict) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "fields", "evidence_ids", "unresolved"],
            "properties": {
                "status": {"type": "string", "enum": [status]},
                "fields": fields,
                "evidence_ids": evidence,
                "unresolved": unresolved,
            },
        }

    unresolved_shape = shape(
        "UNRESOLVED",
        {"type": "array", "maxItems": max_refs, "items": evidence_items},
        {"type": "array", "minItems": 1, "items": question},
    )
    if not ids:
        return unresolved_shape
    proposed_shape = shape(
        "PROPOSED",
        {"type": "array", "minItems": 1, "maxItems": max_refs, "items": evidence_items},
        {"type": "array", "maxItems": 0, "items": question},
    )
    return {"type": "object", "anyOf": [proposed_shape, unresolved_shape]}


def build_request(
    preset_id: str,
    task_id: str,
    context: dict,
    previous: list[dict] | None,
    *,
    model: str,
    request_id: str,
    timeout_seconds: float = 900.0,
) -> RuntimeRequest:
    """One provider-neutral request for one task. The prompt names only the four model keys."""

    common, preset, _ = load_preset(preset_id)
    validate_context(context, common)
    task = task_for(preset, task_id)
    ordered = validated_previous(preset, task, context, previous or [])
    system = "\n".join(["작업 하나만 수행하고 JSON 객체 하나만 반환한다.", *common["rules"]])
    body = {
        "작업": task["title"],
        "목표": context["objective"],
        "질문": task["questions"],
        "해야 할 일": task["do"],
        "하지 말 것": task["do_not"],
        "검토 기준": task["checks"],
        "프로젝트 규칙": context["project_rules"],
        "입력": context["inputs"],
        "이전 결과": [{"task_id": p["task_id"], "fields": p["fields"]} for p in ordered],
        "반환 형식": {
            "status": "PROPOSED(근거가 충분함) 또는 UNRESOLVED(근거 부족·충돌)",
            "fields": dict(task["fields"]),
            "evidence_ids": "위 입력의 id만. PROPOSED면 하나 이상.",
            "unresolved": "UNRESOLVED일 때만 구체적 질문. PROPOSED면 빈 목록.",
        },
    }
    return RuntimeRequest(
        request_id=request_id,
        model=model,
        messages=(
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(body, ensure_ascii=False, indent=1)},
        ),
        parameters={"temperature": 0.2, "max_tokens": 4096, "reasoning_effort": "none"},
        response_schema=response_schema(preset, task, context, common),
        timeout_seconds=timeout_seconds,
    )


def assemble_response(model_text: str, expected: Mapping[str, Any]) -> dict:
    """Parse the model's four-key object strictly and add the Python-owned binding fields."""

    value = strict_json_loads(model_text)
    require(isinstance(value, dict) and set(value) == _RESPONSE_KEYS, "response keys mismatch")
    return {**expected, **value}


# --------------------------------------------------------------------------------------------
# Run record: explicit steps, bounded attempts, human review
# --------------------------------------------------------------------------------------------


def new_run(
    preset_id: str, context: dict, *, run_id: str | None = None, created_at: str | None = None
) -> dict:
    """Start a run. ``preset_id`` is mandatory; there is no default preset."""

    common, preset, preset_digest = load_preset(preset_id)
    context_digest = validate_context(context, common)
    chosen = run_id or f"run-{secrets.token_hex(6)}"
    require(bool(_RUN_ID.fullmatch(chosen)), "invalid run id")
    return {
        "kind": RUN_KIND,
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": chosen,
        "created_at": created_at or _now(),
        "preset_id": preset_id,
        "preset_digest": preset_digest,
        "context": copy.deepcopy(context),
        "context_digest": context_digest,
        "tasks": [{"task_id": t["id"], "attempts": [], "review": None} for t in preset["tasks"]],
    }


def _task_record(run: Mapping[str, Any], task_id: str) -> dict:
    for record in run["tasks"]:
        if record["task_id"] == task_id:
            return record
    raise PresetPipelineError("unknown task")


def _latest(record: Mapping[str, Any]) -> dict | None:
    return record["attempts"][-1] if record["attempts"] else None


KNOWN = "KNOWN"
RESULT_UNKNOWN = "RESULT_UNKNOWN"


def _review_holds(record: Mapping[str, Any]) -> bool:
    """A review counts only when a person made it for the latest attempt and that response is unchanged (DEC-0245)."""

    review, latest = record.get("review"), _latest(record)
    return bool(
        isinstance(review, Mapping)
        and latest
        and review.get("actor") in _REVIEWERS
        and review.get("attempt") == latest.get("attempt")
        and latest.get("response") is not None
        and review.get("response_sha256") == digest(latest["response"])
    )


def _unknown(record: Mapping[str, Any]) -> bool:
    latest = _latest(record)
    return bool(latest and latest.get("outcome") == RESULT_UNKNOWN and not latest.get("resolved"))


def accepted_result(run: Mapping[str, Any], task_id: str) -> dict | None:
    """The reviewed response of one task, or ``None`` until a person accepted a passing attempt.

    The review must hold the latest attempt and the sha256 of the response it accepted; a response changed
    afterwards, a review for another attempt or one made by anyone but the operator or user is not accepted.
    """

    record = _task_record(run, task_id)
    review, latest = record["review"], _latest(record)
    if (
        review
        and review.get("decision") == "ACCEPTED"
        and latest
        and latest["validation"]["structure"] == "PASS"
        and _review_holds(record)
    ):
        return latest["response"]
    return None


def _previous_for(run: Mapping[str, Any], task: dict) -> list[dict]:
    previous = []
    for dep in task["depends_on"]:
        result = accepted_result(run, dep)
        require(result is not None, f"prerequisite not accepted: {dep}")
        previous.append(result)
    return previous


def _ensure_current(run: Mapping[str, Any]) -> tuple[dict, dict, dict]:
    require(run.get("kind") == RUN_KIND, "not a preset run")
    common, preset, preset_digest = load_preset(run["preset_id"])
    require(run["preset_digest"] == preset_digest, "preset definition changed; start a new run")
    require(run["context_digest"] == validate_context(run["context"], common), "context changed")
    return common, preset, run


def task_states(run: Mapping[str, Any]) -> list[dict]:
    """Per-task state for a screen: ``BLOCKED``, ``READY``, ``NEEDS_REVIEW``, ``ACCEPTED``,
    ``ACCEPTED_UNRESOLVED`` (accepted but it blocks dependents), ``EXHAUSTED``, ``UNKNOWN`` (the last call's
    outcome is unknown until a person says how it ended) or ``REVIEW_INVALID`` (an acceptance that no longer
    holds what it accepted: a person reviews again)."""

    _, preset, _ = _ensure_current(run)
    states = []
    for task in preset["tasks"]:
        record = _task_record(run, task["id"])
        latest = _latest(record)
        review = record["review"]
        accepted = accepted_result(run, task["id"])
        if _unknown(record):
            state = "UNKNOWN"
        elif accepted is not None:
            state = "ACCEPTED" if accepted["status"] == "PROPOSED" else "ACCEPTED_UNRESOLVED"
        elif isinstance(review, Mapping) and review.get("decision") == "ACCEPTED":
            state = "REVIEW_INVALID"
        elif latest and latest["validation"]["structure"] == "PASS" and review is None:
            state = "NEEDS_REVIEW"
        elif len(record["attempts"]) >= MAX_ATTEMPTS:
            state = "EXHAUSTED"
        else:
            deps_ok = all(
                (a := accepted_result(run, dep)) is not None and a["status"] == "PROPOSED"
                for dep in task["depends_on"]
            )
            state = "READY" if deps_ok else "BLOCKED"
        states.append(
            {
                "task_id": task["id"],
                "title": task["title"],
                "state": state,
                "attempts": len(record["attempts"]),
                "max_attempts": MAX_ATTEMPTS,
                "review": copy.deepcopy(review),
            }
        )
    return states


def is_complete(run: Mapping[str, Any]) -> bool:
    return all(item["state"] == "ACCEPTED" for item in task_states(run))


def prepare_task(run: Mapping[str, Any], task_id: str) -> tuple[TaskPacket, RuntimeRequest | None]:
    """Export the packet for one ready task (no model call)."""

    _, preset, current = _ensure_current(run)
    task = task_for(preset, task_id)
    state = next(s for s in task_states(current) if s["task_id"] == task_id)["state"]
    require(state == "READY", f"task is not ready: {state}")
    return build_packet(run["preset_id"], task_id, run["context"], _previous_for(run, task)), None


@dataclass(frozen=True, slots=True)
class PreparedCall:
    """Everything one attempt needs before anything is sent: the packet, the request and its identity."""

    task_id: str
    attempt: int
    packet: TaskPacket
    request: RuntimeRequest
    previous: tuple[dict, ...]

    @property
    def request_sha256(self) -> str:
        return digest(
            {
                "messages": [dict(m) for m in self.request.messages],
                "schema": dict(self.request.response_schema or {}),
            }
        )


def prepare_call(run: Mapping[str, Any], task_id: str, *, model: str) -> PreparedCall:
    """The next attempt of one READY task; nothing is called or recorded."""

    require(text(model), "model is required")
    _common, preset, current = _ensure_current(run)
    task = task_for(preset, task_id)
    state = next(s for s in task_states(current) if s["task_id"] == task_id)["state"]
    require(state == "READY", f"task is not ready: {state}")
    record = _task_record(run, task_id)
    attempt_number = len(record["attempts"]) + 1
    previous = _previous_for(run, task)
    packet = build_packet(run["preset_id"], task_id, run["context"], previous)
    request = build_request(
        run["preset_id"],
        task_id,
        run["context"],
        previous,
        model=model.strip(),
        request_id=f"preset-run:{run['run_id']}:{task_id}:{attempt_number}",
    )
    return PreparedCall(task_id, attempt_number, packet, request, tuple(previous))


def _with_attempt(run: Mapping[str, Any], call: PreparedCall, entry: dict) -> dict:
    updated = copy.deepcopy(dict(run))
    record = _task_record(updated, call.task_id)
    require(len(record["attempts"]) + 1 == call.attempt, "the run changed since this attempt was prepared")
    record["attempts"].append(entry)
    record["review"] = None
    return updated


def record_answer_attempt(
    run: Mapping[str, Any],
    task_id: str,
    *,
    model: str,
    answer_text: str,
    finish_reason: str | None = None,
    call: PreparedCall | None = None,
) -> dict:
    """Record one attempt from an answer already in hand and return the updated run; nothing is called.

    A truncated or malformed answer is recorded as a failed attempt. ``call`` is the attempt the answer came back
    for (``preset_dispatch``); without it the next attempt is prepared here (an answer a host AI wrote).
    """

    call = call or prepare_call(run, task_id, model=model)
    require(call.task_id == task_id, "attempt belongs to another task")
    expected = dict(call.packet.to_record()["preset_binding"])
    response, validation = None, {"structure": "FAIL"}
    raw_text = answer_text if isinstance(answer_text, str) else str(answer_text)
    if finish_reason not in (None, "stop"):
        validation["error"] = f"incomplete answer: finish_reason={finish_reason}"
    else:
        try:
            candidate = assemble_response(raw_text, expected)
            validation = validate_response(
                candidate,
                preset_id=run["preset_id"],
                task_id=task_id,
                context=run["context"],
                previous=list(call.previous),
            )
            response = candidate
        except PresetPipelineError as exc:
            validation["error"] = str(exc)
    return _with_attempt(
        run,
        call,
        {
            "attempt": call.attempt,
            "model": model.strip(),
            "request_id": call.request.request_id,
            "request_sha256": call.request_sha256,
            "finish_reason": finish_reason,
            "raw_text": raw_text[:MAX_RAW_TEXT],
            "response": response,
            "validation": validation,
            "outcome": KNOWN,
            "at": _now(),
        },
    )


def record_unknown_attempt(run: Mapping[str, Any], call: PreparedCall, *, model: str, reason: str) -> dict:
    """Hold an attempt whose call may or may not have happened: no next attempt until a person resolves it."""

    return _with_attempt(
        run,
        call,
        {
            "attempt": call.attempt,
            "model": model.strip(),
            "request_id": call.request.request_id,
            "request_sha256": call.request_sha256,
            "finish_reason": None,
            "raw_text": "",
            "response": None,
            "validation": {"structure": "FAIL", "error": f"result unknown: {reason}"},
            "outcome": RESULT_UNKNOWN,
            "at": _now(),
        },
    )


def resolve_unknown(
    run: Mapping[str, Any], task_id: str, *, actor: str, note: str = "", channel: str | None = None
) -> dict:
    """A person says the unknown attempt ended without an answer to use; it counts as a failed attempt."""

    require(actor in _REVIEWERS, "only the operator or user resolves an unknown result")
    require(isinstance(note, str) and len(note) <= 2000, "note must be text up to 2000 characters")
    _ensure_current(run)
    require(_unknown(_task_record(run, task_id)), "no unknown result to resolve")
    updated = copy.deepcopy(dict(run))
    _latest(_task_record(updated, task_id))["resolved"] = {
        "as": "no_answer",
        "actor": actor,
        "note": note.strip(),
        "at": _now(),
        **({"channel": channel} if channel else {}),
    }
    return updated


def review_task(
    run: Mapping[str, Any],
    task_id: str,
    *,
    decision: str,
    actor: str,
    note: str = "",
    words: str = "",
    channel: str | None = None,
) -> dict:
    """A person accepts or rejects the latest attempt. Acceptance needs a structurally passing one."""

    require(decision in {"ACCEPTED", "REJECTED"}, "decision must be ACCEPTED or REJECTED")
    require(actor in _REVIEWERS, "review must come from the operator or user")
    require(isinstance(note, str) and len(note) <= 2000, "note must be text up to 2000 characters")
    _ensure_current(run)
    record = _task_record(run, task_id)
    latest = _latest(record)
    require(latest is not None, "no attempt to review")
    require(not _unknown(record), "the last call's outcome is unknown; resolve it first")
    reviewable = record["review"] is None or (
        record["review"].get("decision") == "ACCEPTED" and not _review_holds(record)
    )
    require(reviewable, "this attempt was already reviewed")
    if decision == "ACCEPTED":
        require(
            latest["validation"]["structure"] == "PASS", "only a passing attempt can be accepted"
        )
    updated = copy.deepcopy(dict(run))
    _task_record(updated, task_id)["review"] = {
        "decision": decision,
        "actor": actor,
        "attempt": latest["attempt"],
        # DEC-0245: the review holds what it accepted; a changed response no longer counts as accepted.
        "response_sha256": digest(latest["response"]) if latest.get("response") is not None else None,
        "note": note.strip(),
        "at": _now(),
        **({"words": words.strip()} if isinstance(words, str) and words.strip() else {}),
        # DEC-0246: how the person reviewed (``screen+code`` from the console); left out by in-process callers.
        **({"channel": channel} if channel else {}),
    }
    return updated


# --------------------------------------------------------------------------------------------
# Persistence: one explicit JSON file per run in a caller-given directory
# --------------------------------------------------------------------------------------------


def _run_path(directory: str | Path, run_id: str) -> Path:
    require(bool(_RUN_ID.fullmatch(run_id)), "invalid run id")
    base = Path(directory)
    require(str(directory).strip() != "" and base.is_dir(), "directory must be an existing folder")
    return base / f"preset-{run_id}.json"


def save_run(directory: str | Path, run: Mapping[str, Any]) -> Path:
    require(run.get("kind") == RUN_KIND, "not a preset run")
    path = _run_path(directory, run["run_id"])
    document = {"checksum": digest(run), "run": dict(run)}
    data = (json.dumps(document, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".preset-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return path


def load_run(directory: str | Path, run_id: str) -> dict:
    path = _run_path(directory, run_id)
    document = read_json(path, limit=2_000_000)
    run = document.get("run")
    require(isinstance(run, dict) and run.get("run_id") == run_id, "run identity mismatch")
    require(document.get("checksum") == digest(run), "run file was modified outside the program")
    require(
        run.get("kind") == RUN_KIND and run.get("schema_version") == RUN_SCHEMA_VERSION,
        "unsupported run file",
    )
    return run


def list_runs(directory: str | Path) -> list[dict]:
    base = Path(directory)
    require(base.is_dir(), "directory must be an existing folder")
    entries = []
    for path in sorted(base.glob("preset-run-*.json")):
        try:
            run = load_run(base, path.stem.removeprefix("preset-"))
            states = task_states(run)
            entries.append(
                {
                    "run_id": run["run_id"],
                    "preset_id": run["preset_id"],
                    "project_id": run["context"]["project_id"],
                    "created_at": run["created_at"],
                    "complete": all(s["state"] == "ACCEPTED" for s in states),
                    "states": [s["state"] for s in states],
                    "stale": False,
                }
            )
        except (PresetPipelineError, OSError, KeyError, ValueError) as exc:
            entries.append(
                {"run_id": path.stem.removeprefix("preset-"), "error": str(exc), "stale": True}
            )
    return entries


def export_report(run: Mapping[str, Any]) -> str:
    """Plain-text report of accepted results only; unreviewed output is never exported."""

    _, preset, _ = _ensure_current(run)
    lines = [f"# {preset['label']} — {run['context']['objective']}", ""]
    for task in preset["tasks"]:
        result = accepted_result(run, task["id"])
        lines.append(f"## {task['title']}")
        if result is None:
            lines += ["(검토 승인된 결과 없음)", ""]
            continue
        lines.append(f"상태: {result['status']}")
        for name, value in result["fields"].items():
            lines.append(f"- {name}: {value}")
        if result["evidence_ids"]:
            lines.append("근거: " + ", ".join(result["evidence_ids"]))
        for question in result["unresolved"]:
            lines.append(f"미해결: {question}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
