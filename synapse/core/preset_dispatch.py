"""One preset model call, through the core's one-task entry (DEC-0245).

A preset run lives in its folder. ``dispatch_task`` reads it there by run id and first brings in whatever the journal
knows that the run file does not (a crash between the two writes). It refuses while the task's last call is unknown.
Then one attempt goes through ``orchestration_entry.invoke_entry``: a route on a fresh observation of the chosen
local runtime (DEC-0223), the Supervisor's approval for exactly that attempt, a lock against other threads and
processes, the intent written before the call and the outcome after. A timeout or any transport failure is held as
RESULT_UNKNOWN; an answer is validated and recorded; the run file is written. A reply the page never received loses
nothing: the page reads the run again.

Before the entry is called, a ``preset-attempt`` line names the attempt, its model and the core run that carries it,
so the journal alone can say what happened to an attempt the run file never recorded. Not exactly-once: an attempt
held as unknown may have reached the model.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from synapse.core import preset_pipeline as pp
from synapse.core.action import RoutingObservation
from synapse.core.orchestration import Approval, OrchestrationCoordinator
from synapse.core.orchestration_entry import EntryError, invoke_entry
from synapse.core.run import DurableRun, RunError, append_artifact, create_run, read_artifacts
from synapse.runtime.contracts import RuntimeRequest, RuntimeResponse

ATTEMPT_KIND = "preset-attempt"
CAPABILITY = "llm"
PROVIDERS = frozenset({"lmstudio", "ollama"})


def journal_path(directory: str | Path, run_id: str) -> Path:
    return Path(directory) / f"preset-{run_id}.journal.jsonl"


def _state_path(directory: str | Path, run_id: str, task_id: str, attempt: int, model: str, adapter_id: str) -> Path:
    tag = hashlib.sha256(f"{adapter_id}\n{model}".encode()).hexdigest()[:12]
    return Path(directory) / f".preset-{run_id}.{task_id}.{attempt}.{tag}.state.json"


def _durable(run_id: str, call: pp.PreparedCall, model: str, adapter_id: str) -> DurableRun:
    goal = f"preset {run_id} {call.task_id} attempt {call.attempt} via {adapter_id}:{model}"
    return create_run(goal, owner="Supervisor", steps=[call.packet.task_id], budget=1)


def _journal(directory: str | Path, run_id: str) -> list[dict[str, Any]]:
    path = journal_path(directory, run_id)
    if not path.is_file():
        return []
    try:
        return read_artifacts(path)
    except (OSError, RunError) as exc:
        raise pp.PresetPipelineError(f"the run's journal cannot be trusted: {exc}") from exc


def _outcome(events: list[dict[str, Any]], durable_id: str) -> tuple[bool, dict[str, Any] | None]:
    """Whether the entry wrote an intent for this core run, and its outcome payload if it wrote one."""

    intent = any(e.get("run_id") == durable_id and e.get("kind") == "orchestration-intent" for e in events)
    outcomes = [
        e["payload"] for e in events if e.get("run_id") == durable_id and e.get("kind") == "orchestration-outcome"
    ]
    return intent, (outcomes[-1] if outcomes else None)


def reconcile(directory: str | Path, run: Mapping[str, Any]) -> tuple[dict, bool]:
    """Bring into the run any attempt the journal settled but the run file never recorded; nothing is called."""

    events = _journal(directory, run["run_id"])
    current = dict(run)
    changed = False
    for task in run["tasks"]:
        task_id = task["task_id"]
        while True:
            record = next(t for t in current["tasks"] if t["task_id"] == task_id)
            attempt = len(record["attempts"]) + 1
            markers = [
                e["payload"]
                for e in events
                if e.get("kind") == ATTEMPT_KIND
                and e["payload"].get("task_id") == task_id
                and e["payload"].get("attempt") == attempt
            ]
            settled = None
            for marker in markers:
                intent, outcome = _outcome(events, marker["durable_run_id"])
                if intent:
                    settled = (marker, outcome)
            if settled is None:
                break
            marker, outcome = settled
            call = pp.prepare_call(current, task_id, model=marker["model"])
            if call.request.request_id != marker["request_id"]:
                raise pp.PresetPipelineError("the journal's attempt does not match the run; inspect the folder")
            if outcome is not None and outcome.get("outcome") == "KNOWN":
                result = outcome.get("result") or {}
                current = pp.record_answer_attempt(
                    current,
                    task_id,
                    model=marker["model"],
                    answer_text=str(result.get("answer_text", "")),
                    finish_reason=result.get("finish_reason"),
                    call=call,
                )
            else:
                reason = (outcome or {}).get("reason", "intent_without_outcome")
                current = pp.record_unknown_attempt(current, call, model=marker["model"], reason=str(reason))
            changed = True
    return current, changed


def load_current(directory: str | Path, run_id: str) -> dict:
    """The run as its folder holds it, with the journal's settled attempts brought in and saved."""

    run = pp.load_run(directory, run_id)
    run, changed = reconcile(directory, run)
    if changed:
        pp.save_run(directory, run)
    return run


def dispatch_task(
    directory: str | Path,
    run_id: str,
    task_id: str,
    *,
    model: str,
    adapter_id: str,
    complete: Callable[[RuntimeRequest], RuntimeResponse | str],
    observation: RoutingObservation,
    dispatch_approved: bool,
) -> dict:
    """Send one attempt of one READY task through the core's one-task entry and return the saved run."""

    pp.require(dispatch_approved is True, "model dispatch needs explicit approval")
    pp.require(adapter_id in PROVIDERS, "choose lmstudio or ollama")
    pp.require(pp.text(model), "model is required")
    model = model.strip()
    run = load_current(directory, run_id)
    call = pp.prepare_call(run, task_id, model=model)
    route = OrchestrationCoordinator().plan(
        call.packet, executor_id=adapter_id, capability=CAPABILITY, observation=observation
    )
    if route.selected is None:
        raise pp.PresetPipelineError(f"the runtime cannot take this call now: {route.reason}")
    durable = _durable(run_id, call, model, adapter_id)
    receipt_path = journal_path(directory, run_id)
    try:
        append_artifact(
            receipt_path,
            run_id=f"preset:{run_id}",
            kind=ATTEMPT_KIND,
            payload={
                "task_id": task_id,
                "attempt": call.attempt,
                "model": model,
                "adapter_id": adapter_id,
                "request_id": call.request.request_id,
                "durable_run_id": durable.id,
            },
            deduplicate=True,
        )
    except RunError as exc:
        raise pp.PresetPipelineError(f"the attempt could not be written before the call: {exc}") from exc

    def adapter(packet) -> dict[str, Any]:
        reply = complete(call.request)
        text, finish = (reply.text, getattr(reply, "finish_reason", None)) if isinstance(reply, RuntimeResponse) else (str(reply), None)
        return {
            "task_id": packet.task_id,
            "status": "COMPLETED",
            "changed_files": [],
            "commands_executed": [],
            "test_result": "not_run",
            "relevant_errors": [],
            "unresolved_issues": [],
            "risk": "low",
            "escalation_required": False,
            "short_summary": "preset answer received",
            "answer_text": text[: pp.MAX_RAW_TEXT],
            "finish_reason": finish,
        }

    approval = Approval(
        actor="Supervisor", status="APPROVED", task_id=call.packet.task_id, executor_id=adapter_id, attempt=1
    )
    try:
        receipt = invoke_entry(
            call.packet,
            durable,
            route=route,
            approval=approval,
            adapter_id=adapter_id,
            adapters={adapter_id: adapter},
            state_path=_state_path(directory, run_id, task_id, call.attempt, model, adapter_id),
            receipt_path=receipt_path,
        )
    except EntryError as exc:
        raise pp.PresetPipelineError(str(exc)) from exc
    status = receipt.get("status")
    if status == "REJECTED":
        raise pp.PresetPipelineError(f"the call was not made: {receipt.get('reason')}")
    if status == "COMPLETED" and isinstance(receipt.get("result"), Mapping):
        result = receipt["result"]
        run = pp.record_answer_attempt(
            run,
            task_id,
            model=model,
            answer_text=str(result.get("answer_text", "")),
            finish_reason=result.get("finish_reason"),
            call=call,
        )
    else:
        run = pp.record_unknown_attempt(run, call, model=model, reason=str(receipt.get("reason") or status))
    pp.save_run(directory, run)
    return run


__all__ = ["ATTEMPT_KIND", "dispatch_task", "journal_path", "load_current", "reconcile"]
