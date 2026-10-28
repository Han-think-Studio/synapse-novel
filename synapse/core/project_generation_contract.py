"""Pure, domain-neutral project generation declarations and review binding.

References describe caller-declared IDs and relationships only; this module
does not infer their meaning, inspect source content, authenticate receipts, or
authorize generation. Review results are evidence candidates for a human.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from synapse.core.idea_session import canonical_hash
from synapse.core.project_evidence_binding import review_project_evidence_binding

PROJECT_GENERATION_CONTRACT_SCHEMA = "project.generation-contract.v1"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PRODUCER_GATE = "generation_producer"


class ProjectGenerationContractError(ValueError):
    """A project generation declaration or producer receipt is malformed."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectGenerationContractError(f"{label} must be a non-empty string")
    return value.strip()


def _hash(value: Any, label: str) -> str:
    result = _text(value, label)
    if _SHA256.fullmatch(result) is None:
        raise ProjectGenerationContractError(f"{label} must be a canonical sha256 digest")
    return result


def _ref_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        raise ProjectGenerationContractError("declared_refs must be an array")
    result = []
    ids = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {"ref_id", "kind", "input_artifact_id"}:
            raise ProjectGenerationContractError(f"declared_refs[{index}] has invalid fields")
        item = {key: _text(raw[key], f"declared_refs[{index}].{key}") for key in ("ref_id", "kind", "input_artifact_id")}
        if item["ref_id"] in ids:
            raise ProjectGenerationContractError("declared_refs contains duplicate ref_id")
        ids.add(item["ref_id"])
        result.append(item)
    return result


def normalize_project_generation_contract(
    *, project_id: str, run_id: str, scope_id: str,
    input_artifacts: Sequence[Mapping[str, Any]], policy_ref: Mapping[str, Any],
    declared_refs: Sequence[Mapping[str, Any]], links: Sequence[Mapping[str, Any]],
    output_artifact: Mapping[str, Any], required_gate_kinds: Sequence[str],
) -> dict[str, Any]:
    """Validate and return a canonical declaration; input and link order is preserved."""
    if not isinstance(input_artifacts, (list, tuple)) or not input_artifacts:
        raise ProjectGenerationContractError("input_artifacts must be a non-empty ordered array")
    inputs = []
    seen_inputs = set()
    for index, raw in enumerate(input_artifacts):
        if not isinstance(raw, Mapping) or set(raw) != {"role", "artifact_id", "content_hash"}:
            raise ProjectGenerationContractError(f"input_artifacts[{index}] has invalid fields")
        item = {"role": _text(raw["role"], f"input_artifacts[{index}].role"),
                "artifact_id": _text(raw["artifact_id"], f"input_artifacts[{index}].artifact_id"),
                "content_hash": _hash(raw["content_hash"], f"input_artifacts[{index}].content_hash")}
        if item["artifact_id"] in seen_inputs:
            raise ProjectGenerationContractError("input_artifacts contains duplicate artifact_id")
        seen_inputs.add(item["artifact_id"])
        inputs.append(item)
    if not isinstance(policy_ref, Mapping) or set(policy_ref) != {"artifact_id", "content_hash"}:
        raise ProjectGenerationContractError("policy_ref has invalid fields")
    policy = {"artifact_id": _text(policy_ref["artifact_id"], "policy_ref.artifact_id"),
              "content_hash": _hash(policy_ref["content_hash"], "policy_ref.content_hash")}
    for item in inputs:
        if item["artifact_id"] == policy["artifact_id"] and item["content_hash"] != policy["content_hash"]:
            raise ProjectGenerationContractError("policy_ref conflicts with input artifact content_hash")
    refs = _ref_list(declared_refs)
    if any(ref["input_artifact_id"] not in seen_inputs for ref in refs):
        raise ProjectGenerationContractError("declared_refs contains a dangling input_artifact_id")
    if not isinstance(links, (list, tuple)):
        raise ProjectGenerationContractError("links must be an array")
    normalized_links = []
    seen_link_ids = set()
    ref_ids = {ref["ref_id"] for ref in refs}
    for index, raw in enumerate(links):
        fields = {"link_id", "kind", "from_ref_id", "to_ref_id"}
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise ProjectGenerationContractError(f"links[{index}] has invalid fields")
        item = {key: _text(raw[key], f"links[{index}].{key}") for key in ("link_id", "kind", "from_ref_id", "to_ref_id")}
        if item["link_id"] in seen_link_ids:
            raise ProjectGenerationContractError("links contains duplicate link_id")
        seen_link_ids.add(item["link_id"])
        if item["from_ref_id"] not in ref_ids or item["to_ref_id"] not in ref_ids:
            raise ProjectGenerationContractError("links contains a dangling declared reference")
        normalized_links.append(item)
    if not isinstance(output_artifact, Mapping) or set(output_artifact) != {"artifact_id", "role"}:
        raise ProjectGenerationContractError("output_artifact must declare artifact_id and role")
    output = {"artifact_id": _text(output_artifact["artifact_id"], "output_artifact.artifact_id"),
              "role": _text(output_artifact["role"], "output_artifact.role")}
    if output["artifact_id"] in seen_inputs:
        raise ProjectGenerationContractError("output artifact_id duplicates an input artifact")
    if not isinstance(required_gate_kinds, (list, tuple)) or not required_gate_kinds:
        raise ProjectGenerationContractError("required_gate_kinds must be a non-empty array")
    gates = [_text(kind, "required_gate_kinds item") for kind in required_gate_kinds]
    if len(set(gates)) != len(gates) or _PRODUCER_GATE in gates:
        raise ProjectGenerationContractError("required_gate_kinds contains duplicates or reserved kind")
    return {"schema_version": PROJECT_GENERATION_CONTRACT_SCHEMA,
            "project_id": _text(project_id, "project_id"), "run_id": _text(run_id, "run_id"),
            "scope_id": _text(scope_id, "scope_id"), "input_artifacts": inputs,
            "policy_ref": policy, "declared_refs": refs, "links": normalized_links,
            "output_artifact": output, "required_gate_kinds": gates}


def project_generation_contract_hash(contract: Mapping[str, Any]) -> str:
    """Return the digest of the normalized contract, rejecting non-normalized inputs."""
    if not isinstance(contract, Mapping):
        raise ProjectGenerationContractError("contract must be an object")
    fields = {"schema_version", "project_id", "run_id", "scope_id", "input_artifacts", "policy_ref",
              "declared_refs", "links", "output_artifact", "required_gate_kinds"}
    if set(contract) != fields or contract.get("schema_version") != PROJECT_GENERATION_CONTRACT_SCHEMA:
        raise ProjectGenerationContractError("contract fields or schema_version are invalid")
    # Re-normalize to ensure hashes are canonical and declarations remain valid.
    normalized = normalize_project_generation_contract(**{key: contract[key] for key in fields if key != "schema_version"})
    if dict(contract) != normalized:
        raise ProjectGenerationContractError("contract is not normalized")
    return canonical_hash(normalized)


def review_project_generation_contract(
    *, contract: Mapping[str, Any], output_content_hash: str,
    producer_receipt: Mapping[str, Any] | None, gate_receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind contract identity and producer evidence into project evidence review.

    A matching producer receipt asserts only that the caller reports this
    contract/output pair as produced. Receipt authenticity and content are not
    verified. Missing evidence waits; malformed producer evidence raises;
    mismatched evidence is stale. No generation, approval, or mutation occurs.
    """
    digest = project_generation_contract_hash(contract)
    output_hash = _hash(output_content_hash, "output_content_hash")
    inputs = [dict(item) for item in contract["input_artifacts"]]
    inputs.append({"role": "generation_contract", "artifact_id": f"contract:{digest}", "content_hash": digest})
    required = [*contract["required_gate_kinds"], _PRODUCER_GATE]
    receipts = list(gate_receipts) if isinstance(gate_receipts, (list, tuple)) else gate_receipts
    if not isinstance(receipts, (list, tuple)):
        raise ProjectGenerationContractError("gate_receipts must be an array")
    if any(isinstance(receipt, Mapping) and receipt.get("gate_kind") == _PRODUCER_GATE for receipt in receipts):
        raise ProjectGenerationContractError("gate_receipts cannot supply the reserved generation_producer gate")
    if producer_receipt is not None:
        fields = {"contract_hash", "output_artifact_id", "output_content_hash"}
        if not isinstance(producer_receipt, Mapping) or set(producer_receipt) != fields:
            raise ProjectGenerationContractError("producer_receipt has invalid fields")
        _hash(producer_receipt["contract_hash"], "producer_receipt.contract_hash")
        _text(producer_receipt["output_artifact_id"], "producer_receipt.output_artifact_id")
        _hash(producer_receipt["output_content_hash"], "producer_receipt.output_content_hash")
        prelim = review_project_evidence_binding(
            project_id=contract["project_id"], run_id=contract["run_id"], scope_id=contract["scope_id"],
            input_artifacts=inputs, policy_ref=contract["policy_ref"], required_gate_kinds=required,
            output_artifact={"artifact_id": contract["output_artifact"]["artifact_id"], "content_hash": output_hash},
            gate_receipts=[],
        )
        receipt_matches = (producer_receipt["contract_hash"] == digest
            and producer_receipt["output_artifact_id"] == contract["output_artifact"]["artifact_id"]
            and producer_receipt["output_content_hash"] == output_hash)
        producer_gate = {
            "project_id": contract["project_id"], "run_id": contract["run_id"], "scope_id": contract["scope_id"],
            "input_bundle_hash": prelim["input_bundle_hash"] if receipt_matches else "sha256:" + "0" * 64,
            "output_artifact_id": producer_receipt["output_artifact_id"],
            "output_content_hash": producer_receipt["output_content_hash"], "gate_kind": _PRODUCER_GATE,
            "checker_version": "producer-receipt:v1", "evidence_id": "producer-receipt:caller-supplied",
            "verdict": "PASS",
        }
        receipts.append(producer_gate)
    binding = review_project_evidence_binding(
        project_id=contract["project_id"], run_id=contract["run_id"], scope_id=contract["scope_id"],
        input_artifacts=inputs, policy_ref=contract["policy_ref"], required_gate_kinds=required,
        output_artifact={"artifact_id": contract["output_artifact"]["artifact_id"], "content_hash": output_hash},
        gate_receipts=receipts,
    )
    binding["generation_contract_hash"] = digest
    binding["producer_receipt_authenticated"] = False
    binding["receipt_hash"] = canonical_hash({key: value for key, value in binding.items() if key != "receipt_hash"})
    return binding


__all__ = ["PROJECT_GENERATION_CONTRACT_SCHEMA", "ProjectGenerationContractError",
           "normalize_project_generation_contract", "project_generation_contract_hash",
           "review_project_generation_contract"]
