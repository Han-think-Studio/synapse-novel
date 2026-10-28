"""Read-only binding of project-owned artifact and gate evidence.

This checks identity and freshness, not the meaning of a domain gate. Caller
labels are not authentication, and the supplied policy is not approved here.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from synapse.core.idea_session import canonical_hash

PROJECT_EVIDENCE_BINDING_SCHEMA = "project.evidence-binding.v1"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GATE_FIELDS = (
    "project_id", "run_id", "scope_id", "input_bundle_hash",
    "output_artifact_id", "output_content_hash", "gate_kind",
    "checker_version", "evidence_id", "verdict",
)


class ProjectEvidenceBindingError(ValueError):
    """The declared project artifact/policy identity is malformed."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectEvidenceBindingError(f"{label} must be a non-empty string")
    return value.strip()


def _hash(value: Any, label: str) -> str:
    result = _text(value, label)
    if _SHA256.fullmatch(result) is None:
        raise ProjectEvidenceBindingError(f"{label} must be a canonical sha256 digest")
    return result


def _artifact_ref(value: Any, label: str, *, role_required: bool) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ProjectEvidenceBindingError(f"{label} must be an object")
    fields = {"artifact_id", "content_hash"}
    if role_required:
        fields.add("role")
    if set(value) != fields:
        raise ProjectEvidenceBindingError(f"{label} fields must be {', '.join(sorted(fields))}")
    result = {
        "artifact_id": _text(value["artifact_id"], f"{label}.artifact_id"),
        "content_hash": _hash(value["content_hash"], f"{label}.content_hash"),
    }
    if role_required:
        result["role"] = _text(value["role"], f"{label}.role")
    return result


def _ordered_refs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ProjectEvidenceBindingError("input_artifacts must be a non-empty ordered array")
    refs = [
        _artifact_ref(item, f"input_artifacts[{index}]", role_required=True)
        for index, item in enumerate(value)
    ]
    identities = [(item["role"], item["artifact_id"]) for item in refs]
    if len(set(identities)) != len(identities):
        raise ProjectEvidenceBindingError("input_artifacts contains a duplicate role/artifact_id")
    return refs


def _required_kinds(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ProjectEvidenceBindingError("required_gate_kinds must be a non-empty array")
    kinds = [_text(item, "required_gate_kinds item") for item in value]
    if len(set(kinds)) != len(kinds):
        raise ProjectEvidenceBindingError("required_gate_kinds contains duplicates")
    return kinds


def review_project_evidence_binding(
    *,
    project_id: str,
    run_id: str,
    scope_id: str,
    input_artifacts: Sequence[Mapping[str, Any]],
    policy_ref: Mapping[str, Any],
    required_gate_kinds: Sequence[str],
    output_artifact: Mapping[str, Any],
    gate_receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Review whether gate receipts describe exactly one declared artifact basis.

    The caller supplies artifact digests, project policy, and checker results.
    This pure function never reads their contents or verifies their provenance.
    READY_FOR_HUMAN_REVIEW therefore means only that the declared evidence is
    internally bound; it is neither domain quality nor execution approval.
    """

    identity = {
        "project_id": _text(project_id, "project_id"),
        "run_id": _text(run_id, "run_id"),
        "scope_id": _text(scope_id, "scope_id"),
    }
    inputs = _ordered_refs(input_artifacts)
    policy = _artifact_ref(policy_ref, "policy_ref", role_required=False)
    output = _artifact_ref(output_artifact, "output_artifact", role_required=False)
    required = _required_kinds(required_gate_kinds)
    if not isinstance(gate_receipts, (list, tuple)):
        raise ProjectEvidenceBindingError("gate_receipts must be an array")

    input_bundle_hash = canonical_hash({
        **identity,
        "input_artifacts": inputs,
        "policy_ref": policy,
        "required_gate_kinds": required,
    })
    expected = {
        **identity,
        "input_bundle_hash": input_bundle_hash,
        "output_artifact_id": output["artifact_id"],
        "output_content_hash": output["content_hash"],
    }
    present: Counter[str] = Counter()
    verdicts: dict[str, set[str]] = {kind: set() for kind in required}
    stale: set[str] = set()
    mismatches: list[dict[str, Any]] = []
    unsupported: set[str] = set()
    unexpected: set[str] = set()
    invalid_indices: list[int] = []
    projected: list[dict[str, Any]] = []

    for index, raw in enumerate(gate_receipts):
        if not isinstance(raw, Mapping):
            invalid_indices.append(index)
            continue
        kind = raw.get("gate_kind")
        if not isinstance(kind, str) or not kind.strip():
            invalid_indices.append(index)
            continue
        kind = kind.strip()
        if kind not in verdicts:
            unexpected.add(kind)
            continue
        present[kind] += 1
        if any(not isinstance(raw.get(field), str) or not raw[field].strip() for field in _GATE_FIELDS):
            invalid_indices.append(index)
            continue
        mismatched_fields = sorted(
            field for field, value in expected.items() if raw[field] != value
        )
        if mismatched_fields:
            stale.add(kind)
            mismatches.append({
                "index": index,
                "gate_kind": kind,
                "fields": mismatched_fields,
            })
            continue
        if raw["verdict"] not in {"PASS", "FAIL"}:
            unsupported.add(kind)
            continue
        verdicts[kind].add(raw["verdict"])
        projected.append({
            "gate_kind": kind,
            "checker_version": raw["checker_version"],
            "evidence_id": raw["evidence_id"],
            "verdict": raw["verdict"],
        })

    missing = sorted(set(required) - set(present))
    duplicate = sorted(kind for kind, count in present.items() if count > 1)
    conflicting = sorted(kind for kind, values in verdicts.items() if len(values) > 1)
    failed = sorted(kind for kind, values in verdicts.items() if "FAIL" in values)
    if failed:
        status = "BLOCKED"
    elif invalid_indices or duplicate or conflicting or unsupported or unexpected:
        status = "UNRESOLVED"
    elif stale:
        status = "STALE"
    elif missing:
        status = "WAITING"
    else:
        status = "READY_FOR_HUMAN_REVIEW"

    receipt = {
        "schema_version": PROJECT_EVIDENCE_BINDING_SCHEMA,
        **identity,
        "input_artifacts": inputs,
        "policy_ref": policy,
        "required_gate_kinds": required,
        "input_bundle_hash": input_bundle_hash,
        "output_artifact": output,
        "gate_evidence": sorted(projected, key=lambda item: (item["gate_kind"], item["evidence_id"])),
        "missing_gate_kinds": missing,
        "stale_gate_kinds": sorted(stale),
        "mismatched_gate_receipts": mismatches,
        "duplicate_gate_kinds": duplicate,
        "conflicting_gate_kinds": conflicting,
        "failed_gate_kinds": failed,
        "unsupported_verdict_gate_kinds": sorted(unsupported),
        "unexpected_gate_kinds": sorted(unexpected),
        "invalid_receipt_indices": invalid_indices,
        "status": status,
        "source_content_verified": False,
        "policy_authority_verified": False,
        "approval_recorded": False,
        "execution_allowed": False,
        "model_invoked": False,
        "filesystem_mutation": False,
        "canonical_mutation": False,
    }
    receipt["receipt_hash"] = canonical_hash(receipt)
    return receipt


__all__ = [
    "PROJECT_EVIDENCE_BINDING_SCHEMA",
    "ProjectEvidenceBindingError",
    "review_project_evidence_binding",
]
