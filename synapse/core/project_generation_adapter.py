"""Caller-owned, contract-bound producer adapter for the existing durable entry.

The caller supplies the artifact reader, producer, project checker, route, and
Supervisor approval. This module has no provider, project vocabulary, output
store, or automatic retry. A producer exception is left to ``invoke_entry`` to
record as an unknown outcome; a returned output is preserved in its result.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

from synapse.core.action import ActionRoute
from synapse.core.idea_session import canonical_hash
from synapse.core.orchestration import Approval
from synapse.core.orchestration_entry import invoke_entry
from synapse.core.packets import ResultPacket, TaskPacket
from synapse.core.project_generation_contract import (
    ProjectGenerationContractError,
    project_generation_contract_hash,
    review_project_generation_contract,
)
from synapse.core.run import DurableRun

MAX_PROJECT_OUTPUT_BYTES = 256 * 1024
MAX_PROJECT_CONTRACT_BYTES = 64 * 1024

ArtifactReader = Callable[[str], bytes]
ProjectProducer = Callable[[Mapping[str, Any], Mapping[str, bytes]], bytes]
ProjectChecker = Callable[
    [Mapping[str, Any], Mapping[str, bytes], bytes, Mapping[str, Any]],
    Sequence[Mapping[str, Any]],
]
ProjectInputValidator = Callable[[Mapping[str, Any], Mapping[str, bytes]], Mapping[str, Any]]


def _content_hash(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def project_generation_task_packet(contract: Mapping[str, Any]) -> TaskPacket:
    """Build the sole packet identity for one declared output and attempt."""
    digest = project_generation_contract_hash(contract)
    frozen_contract = json.loads(json.dumps(contract, ensure_ascii=False, sort_keys=True))
    encoded = json.dumps(frozen_contract, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PROJECT_CONTRACT_BYTES:
        raise ProjectGenerationContractError("generation contract exceeds the bounded packet size")
    return TaskPacket.from_record({
        "task_id": "project-generation:" + digest.removeprefix("sha256:"),
        "objective": "Produce one project artifact under its declared generation contract.",
        "scope": {
            "project_id": contract["project_id"], "run_id": contract["run_id"],
            "scope_id": contract["scope_id"], "generation_contract_hash": digest,
            "output_artifact_id": contract["output_artifact"]["artifact_id"],
            "generation_contract": frozen_contract,
        },
        "relevant_paths": [],
        "known_state": {"adapter_canonical_mutation": False,
                        "producer_side_effects": "CALLER_OWNED"},
        "constraints": ["caller_started", "one_attempt", "no_automatic_retry", "no_canonical_mutation"],
        "acceptance_criteria": ["contract_bound_output", "project_gate_receipts", "human_review_pending"],
        "tests": [], "stop_conditions": ["source_stale", "producer_outcome_unknown"],
        "preferred_capability": "project_generation", "context_budget_hint": 0,
    })


def _failed_result(task_id: str, *, reason: str, details: Mapping[str, Any] | None = None) -> ResultPacket:
    return ResultPacket.from_record({
        "task_id": task_id, "status": "FAILED", "changed_files": [],
        "commands_executed": [], "test_result": {"project_generation": reason},
        "relevant_errors": [reason], "unresolved_issues": [reason],
        "risk": "high", "escalation_required": False,
        "short_summary": "project generation stopped before a reviewable output",
        "project_generation": {"status": reason, **dict(details or {}),
                               "adapter_canonical_mutation": False,
                               "adapter_filesystem_mutation": False,
                               "producer_side_effects_verified": False},
    })


def _read_snapshot(contract: Mapping[str, Any], reader: ArtifactReader) -> tuple[dict[str, bytes], list[str]]:
    refs = [*contract["input_artifacts"], contract["policy_ref"]]
    snapshots: dict[str, bytes] = {}
    errors: list[str] = []
    for ref in refs:
        artifact_id = ref["artifact_id"]
        expected = ref["content_hash"]
        if artifact_id in snapshots:
            if _content_hash(snapshots[artifact_id]) != expected:
                errors.append(artifact_id)
            continue
        try:
            content = reader(artifact_id)
        except (OSError, KeyError, ValueError):
            errors.append(artifact_id)
            continue
        if not isinstance(content, bytes) or _content_hash(content) != expected:
            errors.append(artifact_id)
            continue
        snapshots[artifact_id] = content
    return snapshots, sorted(set(errors))


def _input_validation_receipt(contract: Mapping[str, Any], digest: str,
                              raw: Mapping[str, Any]) -> dict[str, Any]:
    fields = {"checker_version", "checked_ref_ids", "checked_link_ids", "errors"}
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise ValueError("project input validator must return a complete receipt")
    version = raw["checker_version"]
    if not isinstance(version, str) or not version.strip():
        raise ValueError("project input checker_version is missing")
    refs = raw["checked_ref_ids"]
    links = raw["checked_link_ids"]
    errors = raw["errors"]
    if (not isinstance(refs, (list, tuple)) or
        list(refs) != [item["ref_id"] for item in contract["declared_refs"]]):
        raise ValueError("project input validator did not cover declared references")
    if (not isinstance(links, (list, tuple)) or
        list(links) != [item["link_id"] for item in contract["links"]]):
        raise ValueError("project input validator did not cover declared links")
    if not isinstance(errors, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in errors
    ):
        raise ValueError("project input validation errors must be an array of non-empty strings")
    receipt = {
        "schema_version": "project.input-validation.v1",
        "generation_contract_hash": digest,
        "checker_version": version.strip(),
        "checked_ref_ids": list(refs),
        "checked_link_ids": list(links),
        "errors": [item.strip() for item in errors],
        "verdict": "FAIL" if errors else "PASS",
    }
    receipt["receipt_hash"] = canonical_hash(receipt)
    return receipt


def invoke_project_generation(
    *, contract: Mapping[str, Any], read_artifact: ArtifactReader,
    produce: ProjectProducer, validate_inputs: ProjectInputValidator | None,
    check: ProjectChecker | None,
    run: DurableRun, route: ActionRoute, approval: Approval, adapter_id: str,
    state_path: str | Path, receipt_path: str | Path,
    max_output_bytes: int = MAX_PROJECT_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Call one producer through the existing approved, durable invocation.

    Source bytes and project policy are checked against the declaration before
    the producer receives them. ``validate_inputs`` returns checker_version,
    ordered checked_ref_ids/checked_link_ids, and errors; core verifies claimed
    coverage, while the project owns semantic truth. Reads around validation,
    production and checking prevent a changed source from yielding a current
    review candidate. The bounded output is journaled for human review.
    """
    digest = project_generation_contract_hash(contract)
    frozen_contract = json.loads(json.dumps(contract, ensure_ascii=False, sort_keys=True))
    if project_generation_contract_hash(frozen_contract) != digest:
        raise ProjectGenerationContractError("contract changed while preparing invocation")
    packet = project_generation_task_packet(frozen_contract)
    if approval.attempt != 1:
        raise ProjectGenerationContractError("project generation allows only one explicit attempt per contract")
    if run.budget != 1:
        raise ProjectGenerationContractError("project generation durable run must have a one-attempt budget")
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or max_output_bytes < 1:
        raise ProjectGenerationContractError("max_output_bytes must be a positive integer")
    def adapter(received: TaskPacket) -> ResultPacket:
        if received.to_record() != packet.to_record():
            return _failed_result(packet.task_id, reason="CONTRACT_PACKET_MISMATCH")
        snapshots, stale = _read_snapshot(frozen_contract, read_artifact)
        if stale:
            return _failed_result(packet.task_id, reason="SOURCE_OR_POLICY_STALE",
                                  details={"stale_artifact_ids": stale, "producer_called": False})
        if frozen_contract["declared_refs"] and validate_inputs is None:
            return _failed_result(packet.task_id, reason="PROJECT_INPUT_VALIDATOR_MISSING",
                                  details={"producer_called": False})
        input_validation_status = "NOT_REQUIRED"
        input_validation_receipt = None
        if validate_inputs is not None:
            try:
                raw_receipt = validate_inputs(json.loads(json.dumps(frozen_contract)),
                                              MappingProxyType(snapshots))
                input_validation_receipt = _input_validation_receipt(frozen_contract, digest, raw_receipt)
            except Exception as exc:  # noqa: BLE001 - a project validator failure must stop before dispatch
                return _failed_result(packet.task_id, reason="PROJECT_INPUT_VALIDATION_ERROR",
                                      details={"error": f"{exc.__class__.__name__}: {str(exc)[:160]}",
                                               "producer_called": False})
            if input_validation_receipt["verdict"] == "FAIL":
                return _failed_result(packet.task_id, reason="PROJECT_INPUT_REFS_INVALID",
                                      details={"input_validation_receipt": input_validation_receipt,
                                               "producer_called": False})
            input_validation_status = "PASS"
        _, changed_after_validation = _read_snapshot(frozen_contract, read_artifact)
        if changed_after_validation:
            return _failed_result(packet.task_id, reason="SOURCE_OR_POLICY_STALE",
                                  details={"stale_artifact_ids": changed_after_validation,
                                           "input_validation_receipt": input_validation_receipt,
                                           "producer_called": False})
        # A producer may perform an external operation. Let exceptions reach
        # invoke_entry so it holds the outcome without replaying this callback.
        content = produce(json.loads(json.dumps(frozen_contract)), MappingProxyType(snapshots))
        if not isinstance(content, bytes):
            return _failed_result(packet.task_id, reason="OUTPUT_NOT_BYTES",
                                  details={"producer_called": True})
        output_hash = _content_hash(content)
        if len(content) > max_output_bytes:
            return _failed_result(packet.task_id, reason="OUTPUT_EXCEEDS_BOUND",
                                  details={"producer_called": True, "output_content_hash": output_hash,
                                           "output_bytes": len(content)})

        producer_receipt = {
            "contract_hash": digest,
            "output_artifact_id": frozen_contract["output_artifact"]["artifact_id"],
            "output_content_hash": output_hash,
        }
        basis = review_project_generation_contract(
            contract=frozen_contract, output_content_hash=output_hash,
            producer_receipt=producer_receipt, gate_receipts=[],
        )
        _, changed_before_check = _read_snapshot(frozen_contract, read_artifact)
        checker_error = None
        receipts: Sequence[Mapping[str, Any]] = []
        if not changed_before_check and check is not None:
            try:
                receipts = check(json.loads(json.dumps(frozen_contract)),
                                 MappingProxyType(snapshots), content, basis)
            except Exception as exc:  # noqa: BLE001 - preserve known output after a project checker fails
                checker_error = f"{exc.__class__.__name__}: {str(exc)[:160]}"
        _, changed_after_check = _read_snapshot(frozen_contract, read_artifact)
        changed = sorted(set(changed_before_check) | set(changed_after_check))
        if changed:
            receipts = []
        try:
            binding = review_project_generation_contract(
                contract=frozen_contract, output_content_hash=output_hash,
                producer_receipt=producer_receipt, gate_receipts=receipts,
            )
        except Exception as exc:  # noqa: BLE001 - malformed checker evidence must not lose known output
            checker_error = f"{exc.__class__.__name__}: {str(exc)[:160]}"
            binding = basis
        review_status = "STALE" if changed else binding["status"]
        result = {
            "schema_version": "project.generation-result.v1",
            "generation_contract_hash": digest,
            "output_artifact_id": frozen_contract["output_artifact"]["artifact_id"],
            "output_content_hash": output_hash,
            "output_bytes": len(content),
            "output_content_base64": base64.b64encode(content).decode("ascii"),
            "source_reader_hashes_matched": True,
            "source_current_after_production": not changed,
            "stale_source_artifact_ids": changed,
            "project_input_validation": input_validation_status,
            "input_validation_receipt": input_validation_receipt,
            "project_input_validation_authority_verified": False,
            "producer_callback_returned_output": True,
            "producer_model_consumption_verified": False,
            "producer_side_effects_verified": False,
            "project_policy_authority_verified": False,
            "checker_error": checker_error,
            "binding": binding,
            "review_status": review_status,
            "human_verdict": "PENDING",
            "adapter_canonical_mutation": False,
            "adapter_filesystem_mutation": False,
        }
        unresolved = [] if review_status == "READY_FOR_HUMAN_REVIEW" else [review_status]
        if checker_error:
            unresolved.append("PROJECT_CHECKER_ERROR")
        return ResultPacket.from_record({
            "task_id": packet.task_id, "status": "COMPLETED", "changed_files": [],
            "commands_executed": [], "test_result": {"project_generation": review_status},
            "relevant_errors": [checker_error] if checker_error else [],
            "unresolved_issues": [*unresolved, "HUMAN_VERDICT_PENDING"],
            "risk": "medium", "escalation_required": bool(checker_error),
            "short_summary": "contract-bound project output returned for review",
            "project_generation": result,
        })

    return invoke_entry(
        packet, run, route=route, approval=approval, adapter_id=adapter_id,
        adapters={adapter_id: adapter}, state_path=state_path, receipt_path=receipt_path,
    )


__all__ = ["MAX_PROJECT_CONTRACT_BYTES", "MAX_PROJECT_OUTPUT_BYTES", "ProjectChecker", "ProjectInputValidator", "ProjectProducer",
           "invoke_project_generation", "project_generation_task_packet"]
