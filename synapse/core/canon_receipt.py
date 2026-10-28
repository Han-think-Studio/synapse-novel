"""Bind a check of an output to the exact canon values, rules and words it was made against. Decide nothing.

Checking a draft against the confirmed canon says something only about *that* draft, *those* values and *those*
rules. Change any of the three and an earlier "no conflict" no longer says what it did. This keeps that binding
as a record the caller holds, and later says whether the record still describes the present: the values may
have been revised, one may have been added or gone, the rules of the check may have been edited, or the draft
may have changed.

It builds on what already exists. The check is ``canon_check.check_text``; the binding is
``project_evidence_binding.review_project_evidence_binding``, whose words it passes on unchanged
(READY_FOR_HUMAN_REVIEW, STALE, BLOCKED, UNRESOLVED). The reasons are the same kind of pointer ``canon_closure``
gives -- ``value:<key>``, plus ``rules:canon_check``, ``output:<scope id>`` and ``receipt:<hash>`` -- not a new
code.

Every confirmed value is an input, not only the ones that fired: the contrast rule compares confirmed values with
each other, so a value added later can make an old "no conflict" wrong. A check against no confirmed value, or of
no words, proves nothing and is refused rather than recorded as a pass.

A confirmed value is checked from what is stored; whether its source file still says the same needs that file to
have been looked at. A receipt therefore records which files were examined and which confirmed values were not, and
a receipt with values left unexamined is complete in no sense a reader should rely on: its review waits.

A producer is shown a block of canon (``canon_context``); the check is made against the confirmed values as they are
now. When the caller says what block the producer was given, the receipt records whether it is still the block those
values give (``same``) and which confirmed values the producer was never shown (``not_shown``). A producer that worked
from older facts than the check is not a match: its review waits on the canon it was given.

What a receipt is not: authentication. It says what the check was bound to; it does not say who ran it, and the
caller can write any record. The hashes are computed here from the values and the words it is given
(``derived``); none is a file read. READY_FOR_HUMAN_REVIEW means the same values, rules and words still give the
same answer, not that the draft is good or approved.

It reads nothing and writes nothing, and the record is kept by whoever asked for the check (DEC-0203).
"""

from __future__ import annotations

import inspect
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from synapse.core import canon_check
from synapse.core.canon_check import CanonValue, Conflict
from synapse.core.canon_closure import FILE, VALUE, Reason
from synapse.core.canon_gate import READY_FOR_HUMAN_REVIEW, STALE, UNRESOLVED, WAITING
from synapse.core.canon_values import ValueStore, checkable
from synapse.core.idea_session import canonical_hash, sha256_text
from synapse.core.project_evidence_binding import (
    ProjectEvidenceBindingError,
    review_project_evidence_binding,
)

RECEIPT_SCHEMA = "canon-receipt.v3"
_LEGACY_RECEIPT_SCHEMA = "canon-receipt.v2"
REVIEW_SCHEMA = "canon-output-closure.v1"

GATE_KIND = "canon_check"
POLICY_ID = "canon_check:rules"

PASS = "PASS"
FAIL = "FAIL"
# ``project_evidence_binding``'s word for a check that came back failed.
BLOCKED = "BLOCKED"

# What a reason points at, besides ``canon_closure.VALUE``.
RULES = "rules"
OUTPUT = "output"
RECEIPT = "receipt"
CONTEXT = "context"

_RECORD_KEYS = frozenset(
    {
        "schema_version", "project_id", "run_id", "scope_id", "verdict", "rules_hash", "output_hash",
        "values", "scope", "produced_with", "conflicts", "possible", "gate_receipt", "binding", "approval_recorded", "execution_allowed",
        "model_invoked", "canonical_mutation", "filesystem_scan", "receipt_hash",
    }
)  # fmt: skip


class ReceiptError(ValueError):
    """Raised when there is nothing to bind, or the input cannot be taken as given."""


def rules_hash() -> str:
    """A hash of the rules of the check: the text of ``canon_check``, line endings read as LF.

    Any edit to the file changes it, which is the point: an edited rule must be asked again. If the text cannot be
    read there is no hash, and so no receipt, rather than a guess.
    """

    try:
        source = inspect.getsource(canon_check)
    except (OSError, TypeError) as exc:
        raise ReceiptError(
            "검사 규칙 파일을 읽을 수 없어 영수증을 만들거나 확인할 수 없습니다."
        ) from exc
    return sha256_text(source.replace("\r\n", "\n"))


def value_hash(value: CanonValue) -> str:
    """A hash of the parts of a value that decide a check: its key, kind, words, pattern, terms and opposite terms.

    Where it sits in a file, when it was confirmed and what it was labelled do not decide a check, so they do not
    change this.
    """

    return canonical_hash(
        {
            "key": value.key,
            "kind": value.kind,
            "text": value.text,
            "pattern": value.pattern,
            "terms": list(value.terms),
            "opposite_terms": list(value.opposite_terms),
        }
    )


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptError(f"{label}은(는) 비어 있지 않은 문자열이어야 합니다.")
    return value


def _conflict_record(conflict: Conflict) -> dict[str, str]:
    return {
        "rule": conflict.rule,
        "value_key": conflict.value_key,
        "claim_id": conflict.claim_id,
        "evidence": conflict.evidence,
        "message": conflict.message,
    }


def _arguments(
    project_id: str,
    run_id: str,
    scope_id: str,
    values: tuple[CanonValue, ...],
    rules: str,
    text: str,
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "run_id": run_id,
        "scope_id": scope_id,
        "input_artifacts": [
            {
                "artifact_id": f"canon:{value.key}",
                "content_hash": value_hash(value),
                "role": "canon_value",
            }
            for value in sorted(values, key=lambda item: item.key)
        ],
        "policy_ref": {"artifact_id": POLICY_ID, "content_hash": rules},
        "required_gate_kinds": [GATE_KIND],
        "output_artifact": {"artifact_id": scope_id, "content_hash": sha256_text(text)},
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckReceipt:
    """The record to keep, and what the check found."""

    record: Mapping[str, Any]
    conflicts: tuple[Conflict, ...]

    @property
    def passed(self) -> bool:
        return self.record["verdict"] == PASS

    @property
    def complete(self) -> bool:
        """Every relevant value was examined and a v3 receipt says none were unchecked."""

        scope = self.record["scope"]
        return (
            self.record.get("schema_version") == RECEIPT_SCHEMA
            and not scope["unexamined"]
            and not scope.get("unchecked", ())
            and isinstance(self.record.get("possible"), list)
            and not self.record["possible"]
        )

    @property
    def facts_match(self) -> bool | None:
        """Whether the producer worked from the canon the check used; None when the caller did not say."""

        produced = self.record["produced_with"]
        return None if produced is None else bool(produced["same"])


def _text_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ReceiptError("produced_with의 목록은 문자열의 목록이어야 합니다.")
    return list(value)


def _produced_record(
    produced_with: Mapping[str, Any] | None, values: tuple[CanonValue, ...]
) -> dict[str, Any] | None:
    """What the producer was shown against the confirmed values now: whether it is the same block, what it never saw."""

    if produced_with is None:
        return None
    if not isinstance(produced_with, Mapping) or set(produced_with) != {
        "given_block_hash",
        "current",
    }:
        raise ReceiptError("produced_with는 {given_block_hash, current}이어야 합니다.")
    given, current = produced_with["given_block_hash"], produced_with["current"]
    if not isinstance(given, str) or not given or not isinstance(current, Mapping):
        raise ReceiptError("produced_with의 형식이 맞지 않습니다.")
    block = current.get("block_hash")
    if not isinstance(block, str) or not block:
        raise ReceiptError("produced_with.current에 block_hash가 필요합니다.")
    keys, dropped = _text_list(current.get("keys")), _text_list(current.get("dropped"))
    given_keys = _text_list(current.get("given_keys", keys))
    return {
        "given_block_hash": given,
        "current_block_hash": block,
        "same": given == block,
        "keys": keys,
        "dropped": dropped,
        "not_shown": sorted({value.key for value in values} - set(given_keys)),
    }


def _relevant_for_check(value: CanonValue, text: str, given_keys: set[str]) -> bool:
    if value.kind in {"prohibition", "requirement"} or value.key in given_keys:
        return True
    if not value.key.startswith("character."):
        return False
    name, separator, attribute = value.key[len("character.") :].rpartition(".")
    return bool(separator and name and attribute and name in text)


def _file_names(examined_files: object) -> tuple[str, ...]:
    if isinstance(examined_files, (str, bytes)) or not isinstance(examined_files, Collection):
        raise ReceiptError("examined_files는 파일 이름의 묶음이어야 합니다.")
    if not all(isinstance(name, str) and name for name in examined_files):
        raise ReceiptError("examined_files의 이름은 비어 있지 않은 문자열이어야 합니다.")
    return tuple(sorted(set(examined_files)))


def check_output(
    text: str,
    store: ValueStore,
    *,
    run_id: str,
    scope_id: str,
    examined_files: Collection[str],
    produced_with: Mapping[str, Any] | None = None,
) -> CheckReceipt:
    """Check ``text`` against every confirmed value of ``store`` and bind the answer to what it was made against.

    ``run_id`` and ``scope_id`` are the caller's own names for this check and for the thing checked; they are
    kept as given. ``examined_files`` are the source files the caller actually looked at for this check; a confirmed
    value from any other file is still checked from what is stored, and the record says it was not examined.
    ``produced_with`` says what canon the producer was shown (see ``canon_context.context_record``): the hash it was
    given and the block those values give now. Nothing is stored: the record is returned for the caller to keep.
    """

    if not isinstance(text, str):
        raise ReceiptError("text는 문자열이어야 합니다.")
    if not isinstance(store, ValueStore):
        raise ReceiptError("store는 ValueStore이어야 합니다.")
    run_id, scope_id = _name(run_id, "run_id"), _name(scope_id, "scope_id")
    examined = _file_names(examined_files)
    produced = _produced_record(produced_with, checkable(store))
    if not text.strip():
        raise ReceiptError("빈 글은 검사한 것으로 셀 수 없습니다.")
    values = checkable(store)
    if not values:
        raise ReceiptError(
            "확정된 값이 없어 비교할 것이 없습니다. 비교할 것이 없는 통과는 통과가 아닙니다."
        )

    checked = {
        (value.key, value.claim_id)
        for value in canon_check.automatically_checkable_values(text, values)
    }
    given_keys = set()
    if produced_with is not None:
        current = produced_with["current"]
        given_keys.update(_text_list(current.get("given_keys", current["keys"])))
    unchecked = [
        {"key": value.key, "file": value.file}
        for value in sorted(values, key=lambda item: item.key)
        if _relevant_for_check(value, text, given_keys)
        and (value.key, value.claim_id) not in checked
    ]

    rules = rules_hash()
    conflicts, possible = canon_check.check_text_with_possible(text, values)
    verdict = FAIL if conflicts else PASS
    conflict_records = [_conflict_record(conflict) for conflict in conflicts]
    possible_records = [_conflict_record(conflict) for conflict in possible]
    arguments = _arguments(store.project_id, run_id, scope_id, values, rules, text)
    try:
        bundle = review_project_evidence_binding(**arguments, gate_receipts=[])["input_bundle_hash"]
        gate = {
            "project_id": store.project_id,
            "run_id": run_id,
            "scope_id": scope_id,
            "input_bundle_hash": bundle,
            "output_artifact_id": scope_id,
            "output_content_hash": arguments["output_artifact"]["content_hash"],
            "gate_kind": GATE_KIND,
            "checker_version": rules,
            "evidence_id": canonical_hash(
                {"verdict": verdict, "conflicts": conflict_records, "possible": possible_records, "input_bundle_hash": bundle}
            ),
            "verdict": verdict,
        }
        binding = review_project_evidence_binding(**arguments, gate_receipts=[gate])
    except ProjectEvidenceBindingError as exc:
        raise ReceiptError(f"영수증을 묶을 수 없습니다: {exc}") from exc

    record: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "project_id": store.project_id,
        "run_id": run_id,
        "scope_id": scope_id,
        "verdict": verdict,
        "rules_hash": rules,
        "output_hash": arguments["output_artifact"]["content_hash"],
        "values": [
            {"key": value.key, "hash": value_hash(value)}
            for value in sorted(values, key=lambda item: item.key)
        ],
        "scope": {
            "examined_files": list(examined),
            "unexamined": [
                {"key": value.key, "file": value.file}
                for value in sorted(values, key=lambda item: item.key)
                if value.file not in examined
            ],
            "unchecked": unchecked,
        },
        "produced_with": produced,
        "conflicts": conflict_records,
        "possible": possible_records,
        "gate_receipt": gate,
        "binding": binding,
        "approval_recorded": False,
        "execution_allowed": False,
        "model_invoked": False,
        "canonical_mutation": False,
        "filesystem_scan": False,
    }
    record["receipt_hash"] = canonical_hash(record)
    return CheckReceipt(record=record, conflicts=conflicts)


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputReview:
    """Whether a kept receipt still describes the present, and if not, what moved."""

    state: str
    reasons: tuple[Reason, ...] = ()
    receipt_hash: str = ""
    verdict: str = ""
    conflicts: tuple[Mapping[str, str], ...] = ()
    possible: tuple[Mapping[str, str], ...] = ()
    schema_version: str = REVIEW_SCHEMA

    @property
    def current(self) -> bool:
        """The same values, rules and words still give the same answer, and that answer was PASS."""

        return self.state == READY_FOR_HUMAN_REVIEW and self.verdict == PASS

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": self.schema_version,
            "state": self.state,
            "current": self.current,
            "verdict": self.verdict,
            "receipt_hash": self.receipt_hash,
            "reasons": [reason.to_record() for reason in self.reasons],
            "conflicts": [dict(conflict) for conflict in self.conflicts],
            "possible": [dict(conflict) for conflict in self.possible],
            "approval_recorded": False,
            "execution_allowed": False,
            "model_invoked": False,
            "canonical_mutation": False,
            "filesystem_scan": False,
        }
        record["review_hash"] = canonical_hash(record)
        return record


def _unreadable(why: str) -> OutputReview:
    return OutputReview(
        state=UNRESOLVED, reasons=(Reason(pointer=f"{RECEIPT}:{why}", state=UNRESOLVED),)
    )


def _malformed(receipt: Any) -> str | None:
    """Why a record cannot be taken as a receipt, or None. A forged hash is caught; a forged record is not."""

    if not isinstance(receipt, Mapping) or set(receipt) not in (
        _RECORD_KEYS, _RECORD_KEYS - {"possible"}
    ):
        return "unreadable"
    if receipt["schema_version"] not in {RECEIPT_SCHEMA, _LEGACY_RECEIPT_SCHEMA}:
        return "unreadable"
    strings = ("project_id", "run_id", "scope_id", "rules_hash", "output_hash", "receipt_hash")
    if not all(isinstance(receipt[name], str) and receipt[name] for name in strings):
        return "unreadable"
    values, gate = receipt["values"], receipt["gate_receipt"]
    listed = isinstance(values, list) and all(
        isinstance(item, Mapping)
        and set(item) == {"key", "hash"}
        and isinstance(item["key"], str)
        and isinstance(item["hash"], str)
        for item in values
    )
    conflicts = receipt["conflicts"]
    scope = receipt["scope"]
    expected_scope_keys = {"examined_files", "unexamined"}
    if receipt["schema_version"] == RECEIPT_SCHEMA:
        expected_scope_keys.add("unchecked")
    scoped = (
        isinstance(scope, Mapping)
        and set(scope) == expected_scope_keys
        and isinstance(scope["examined_files"], list)
        and all(isinstance(name, str) for name in scope["examined_files"])
        and isinstance(scope["unexamined"], list)
        and all(
            isinstance(item, Mapping)
            and set(item) == {"key", "file"}
            and isinstance(item["key"], str)
            and isinstance(item["file"], str)
            for item in scope["unexamined"]
        )
        and (
            receipt["schema_version"] == _LEGACY_RECEIPT_SCHEMA
            or (
                isinstance(scope["unchecked"], list)
                and all(
                    isinstance(item, Mapping)
                    and set(item) == {"key", "file"}
                    and isinstance(item["key"], str)
                    and isinstance(item["file"], str)
                    for item in scope["unchecked"]
                )
            )
        )
    )
    produced = receipt["produced_with"]
    produced_ok = produced is None or (
        isinstance(produced, Mapping)
        and set(produced)
        == {"given_block_hash", "current_block_hash", "same", "keys", "dropped", "not_shown"}
        and isinstance(produced["given_block_hash"], str)
        and isinstance(produced["current_block_hash"], str)
        and isinstance(produced["same"], bool)
        and all(
            isinstance(produced[name], list)
            and all(isinstance(item, str) for item in produced[name])
            for name in ("keys", "dropped", "not_shown")
        )
    )
    if not listed or not scoped or not produced_ok or not isinstance(gate, Mapping):
        return "unreadable"
    if not isinstance(conflicts, list) or not all(isinstance(item, Mapping) for item in conflicts):
        return "unreadable"
    possible = receipt.get("possible")
    if "possible" in receipt and (
        not isinstance(possible, list) or not all(isinstance(item, Mapping) for item in possible)
    ):
        return "unreadable"
    if receipt["verdict"] not in (PASS, FAIL) or gate.get("verdict") != receipt["verdict"]:
        return "unreadable"
    body = {name: value for name, value in receipt.items() if name != "receipt_hash"}
    if canonical_hash(body) != receipt["receipt_hash"]:
        return "altered"
    evidence = {
        "verdict": receipt["verdict"],
        "conflicts": conflicts,
        "input_bundle_hash": gate.get("input_bundle_hash"),
    }
    if "possible" in receipt:
        evidence["possible"] = possible
    if gate.get("evidence_id") != canonical_hash(evidence):
        return "altered"
    return None


def review_output_receipt(
    receipt: Mapping[str, Any],
    store: ValueStore,
    text: str,
    *,
    examined_files: Collection[str] | None = None,
    context: Mapping[str, Any] | None = None,
) -> OutputReview:
    """Whether ``receipt`` still describes ``text`` checked against ``store`` as it is now.

    The values, the rules hash and the words are worked out again from what is given; what the receipt says was
    kept is only compared with them, never taken as the present. The reasons say which of them moved.

    A confirmed value whose source file was not examined -- when the check was made, or now, if ``examined_files``
    says what is being looked at now -- makes the review wait on that file: the check never compared the value with
    its source, so it cannot be called current.

    A receipt that records the canon block its producer was given (``produced_with``) is stale when the producer worked
    from older facts than the check, or -- if ``context`` (the current ``context_record``) is given -- when that block
    is no longer the one the values give.
    """

    if not isinstance(store, ValueStore):
        raise ReceiptError("store는 ValueStore이어야 합니다.")
    if not isinstance(text, str):
        raise ReceiptError("text는 문자열이어야 합니다.")
    broken = _malformed(receipt)
    if broken is not None:
        return _unreadable(broken)

    verdict = receipt["verdict"]
    kept = {item["key"]: item["hash"] for item in receipt["values"]}
    values = checkable(store)
    now = {value.key: value_hash(value) for value in values}
    reasons = [
        Reason(pointer=f"{VALUE}:{key}", state=STALE)
        for key in sorted(set(kept) | set(now))
        if kept.get(key) != now.get(key)
    ]
    try:
        rules: str | None = rules_hash()
    except ReceiptError:
        rules = None
        reasons.append(Reason(pointer=f"{RULES}:{GATE_KIND}", state=UNRESOLVED))
    else:
        if rules != receipt["rules_hash"]:
            reasons.append(Reason(pointer=f"{RULES}:{GATE_KIND}", state=STALE))
    if sha256_text(text) != receipt["output_hash"]:
        reasons.append(Reason(pointer=f"{OUTPUT}:{receipt['scope_id']}", state=STALE))
    produced = receipt["produced_with"]
    if produced is not None:
        moved = not produced["same"] or (
            context is not None and context.get("block_hash") != produced["current_block_hash"]
        )
        if moved:
            reasons.append(Reason(pointer=f"{CONTEXT}:canon", state=STALE))
    waiting = {item["file"] for item in receipt["scope"]["unexamined"]}
    if (
        receipt["schema_version"] == _LEGACY_RECEIPT_SCHEMA
        or receipt["scope"].get("unchecked")
    ):
        reasons.append(Reason(pointer=f"{RECEIPT}:unchecked", state=WAITING))
    if "possible" not in receipt or receipt["possible"]:
        reasons.append(Reason(pointer=f"{RECEIPT}:possible", state=WAITING))
    if examined_files is not None:
        now_examined = set(_file_names(examined_files))
        waiting |= {value.file for value in values if value.file not in now_examined}
    reasons.extend(Reason(pointer=f"{FILE}:{name}", state=WAITING) for name in sorted(waiting))

    state: str
    if reasons:
        if any(reason.state == UNRESOLVED for reason in reasons):
            state = UNRESOLVED
        else:
            state = STALE if any(reason.state == STALE for reason in reasons) else WAITING
    else:
        try:
            binding = review_project_evidence_binding(
                **_arguments(
                    store.project_id,
                    receipt["run_id"],
                    receipt["scope_id"],
                    values,
                    rules or "",
                    text,
                ),
                gate_receipts=[receipt["gate_receipt"]],
            )
            state = binding["status"]
        except ProjectEvidenceBindingError:
            state = UNRESOLVED
        if state not in (READY_FOR_HUMAN_REVIEW, BLOCKED):
            reasons.append(Reason(pointer=f"{RECEIPT}:{receipt['receipt_hash']}", state=state))
    return OutputReview(
        state=state,
        reasons=tuple(sorted(reasons, key=lambda reason: reason.pointer)),
        receipt_hash=receipt["receipt_hash"],
        verdict=verdict,
        conflicts=tuple(dict(conflict) for conflict in receipt["conflicts"]),
        possible=tuple(dict(conflict) for conflict in receipt.get("possible", ())),
    )


__all__ = [
    "BLOCKED",
    "CONTEXT",
    "FAIL",
    "GATE_KIND",
    "OUTPUT",
    "PASS",
    "POLICY_ID",
    "RECEIPT",
    "RECEIPT_SCHEMA",
    "REVIEW_SCHEMA",
    "RULES",
    "CheckReceipt",
    "OutputReview",
    "ReceiptError",
    "check_output",
    "review_output_receipt",
    "rules_hash",
    "value_hash",
]
