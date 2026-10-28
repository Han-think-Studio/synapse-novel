"""Record what a person decided about each point, in their own words.

When the system has a proposal for something -- which file decides a fact, whether
an old document is old, what a list of fields should be -- the person answers
with one of a few choices and, when they have something to say, says it. This
keeps both: the choice, and the words exactly as they were typed. Nothing is
tidied, shortened or translated, because the words are the decision; the
choice only says how to read them.

The choices, and what each one is allowed to become:

* AS_IS      the proposal stands as it is        -> CONFIRMED
* DRAFT      take it as a draft                  -> PROPOSED
* CHANGED    no, and here is what I mean         -> PROPOSED (the words are the new statement)
* TO_CREATE  it does not exist yet, to be made   -> UNRESOLVED (open)
* UNKNOWN    I do not know                       -> UNRESOLVED (open)

The statuses are the ones the project already has. Nothing here closes an open
item: only a later answer does. And nothing here is Canonical State: a row is a
``Decision`` record that the Registry could be asked to take, and the Registry's
own rule decides whether it can -- it refuses PROPOSED, accepts UNRESOLVED and
CONFIRMED -- so a draft or a changed answer stays a proposal until it is settled.
Building the Registry Proposal, and committing it, are left to the explicit
approval boundary that already exists (``to_registry_proposal``); this module
does neither.

An item is answered once. Changing a decision is a separate, explicit revision
that raises the version and keeps the earlier answer in the history; it never
overwrites it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from synapse.core.contracts import Decision, LifecycleStatus, Provenance

TABLE_SCHEMA = "canon-table.v1"

# A decision is a sentence or a paragraph, not a document.
MAX_WORDS = 20_000


class CanonTableError(ValueError):
    """Raised when an item or an answer cannot be taken as given."""


class Choice(str, Enum):
    AS_IS = "AS_IS"
    DRAFT = "DRAFT"
    CHANGED = "CHANGED"
    TO_CREATE = "TO_CREATE"
    UNKNOWN = "UNKNOWN"


CHOICE_LABELS = {
    Choice.AS_IS: "지금 그대로",
    Choice.DRAFT: "초안으로",
    Choice.CHANGED: "다르게 고침",
    Choice.TO_CREATE: "새로 만들 것",
    Choice.UNKNOWN: "모르겠음",
}

_STATUS = {
    Choice.AS_IS: LifecycleStatus.CONFIRMED,
    Choice.DRAFT: LifecycleStatus.PROPOSED,
    Choice.CHANGED: LifecycleStatus.PROPOSED,
    Choice.TO_CREATE: LifecycleStatus.UNRESOLVED,
    Choice.UNKNOWN: LifecycleStatus.UNRESOLVED,
}

# What the Registry's own validator refuses to commit. Kept next to the statuses
# above so a test can hold it against the real validator.
_REFUSED_BY_REGISTRY = frozenset({LifecycleStatus.PROPOSED, LifecycleStatus.BLOCKED})


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CanonTableError(f"{label}은(는) 비어 있지 않은 문자열이어야 합니다.")
    return value


def _choice(value: Any) -> Choice:
    if isinstance(value, Choice):
        return value
    if isinstance(value, str):
        text = value.strip()
        for choice in Choice:
            if text.upper() == choice.value or text == CHOICE_LABELS[choice]:
                return choice
    raise CanonTableError(f"알 수 없는 선택입니다: {value!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationItem:
    """One point to decide, with what the system proposes for it."""

    id: str
    question: str
    proposal: str = ""
    candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, "item.id")
        _text(self.question, "item.question")
        if not isinstance(self.proposal, str):
            raise CanonTableError("item.proposal은 문자열이어야 합니다.")
        object.__setattr__(self, "candidates", tuple(self.candidates))

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "proposal": self.proposal,
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class TableAnswer:
    """A choice and the words that came with it, exactly as given."""

    item_id: str
    choice: Choice
    words: str
    version: int
    revision: int

    def to_record(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "choice": self.choice.value,
            "label": CHOICE_LABELS[self.choice],
            "words": self.words,
            "version": self.version,
            "revision": self.revision,
        }


def _parse(value: Any) -> tuple[Choice, str]:
    """A choice alone, ``(choice, words)``, or ``{"choice": ..., "words": ...}``."""

    words: Any = ""
    if isinstance(value, Mapping):
        if "choice" not in value:
            raise CanonTableError("답에는 choice가 필요합니다.")
        choice, words = _choice(value["choice"]), value.get("words", "")
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        if not 1 <= len(value) <= 2:
            raise CanonTableError("답은 (선택) 또는 (선택, 원문)이어야 합니다.")
        choice, words = _choice(value[0]), (value[1] if len(value) == 2 else "")
    else:
        choice = _choice(value)
    if not isinstance(words, str):
        raise CanonTableError("원문은 문자열이어야 합니다.")
    if len(words) > MAX_WORDS:
        raise CanonTableError(f"원문이 너무 깁니다(최대 {MAX_WORDS}자).")
    if choice is Choice.CHANGED and not words.strip():
        raise CanonTableError("'다르게 고침'에는 바꾼 내용을 적은 원문이 필요합니다.")
    return choice, words


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationTable:
    """The points to decide, and what has been decided so far."""

    project_id: str
    items: tuple[ConfirmationItem, ...]
    owner: str = "human"
    answers: tuple[TableAnswer, ...] = ()
    revision: int = 0
    schema_version: str = TABLE_SCHEMA

    def __post_init__(self) -> None:
        _text(self.project_id, "project_id")
        _text(self.owner, "owner")
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise CanonTableError("확인표 항목 id가 겹칩니다.")
        known = set(ids)
        if any(answer.item_id not in known for answer in self.answers):
            raise CanonTableError("확인표에 없는 항목의 답이 있습니다.")

    def current(self, item_id: str) -> TableAnswer | None:
        """The latest answer to one item, or None if it has not been answered."""

        found = [answer for answer in self.answers if answer.item_id == item_id]
        return found[-1] if found else None

    def _record(self, answers: Mapping[str, Any], *, revising: bool) -> ConfirmationTable:
        if not isinstance(answers, Mapping):
            raise CanonTableError("answers는 mapping이어야 합니다.")
        known = {item.id for item in self.items}
        given = list(self.answers)
        new_revision = self.revision + 1
        for item_id, raw in answers.items():
            if not isinstance(item_id, str) or item_id not in known:
                raise CanonTableError(f"알 수 없는 확인표 항목입니다: {item_id}")
            before = self.current(item_id)
            if revising and before is None:
                raise CanonTableError(f"아직 답하지 않은 항목은 고칠 수 없습니다: {item_id}")
            if not revising and before is not None:
                raise CanonTableError(f"이미 답변된 확인표 항목입니다: {item_id}")
            choice, words = _parse(raw)
            given.append(
                TableAnswer(
                    item_id=item_id,
                    choice=choice,
                    words=words,
                    version=1 if before is None else before.version + 1,
                    revision=new_revision,
                )
            )
        return replace(self, answers=tuple(given), revision=new_revision if answers else self.revision)

    def answer(self, answers: Mapping[str, Any]) -> ConfirmationTable:
        """Take first answers and return a new revision. An item is answered once."""

        return self._record(answers, revising=False)

    def revise(self, answers: Mapping[str, Any]) -> ConfirmationTable:
        """Change earlier answers. The new answer is a later version; the old one stays."""

        return self._record(answers, revising=True)

    @property
    def open_item_ids(self) -> tuple[str, ...]:
        """Items not answered yet, and items answered 'open' (unknown, or still to be made)."""

        result = []
        for item in self.items:
            answer = self.current(item.id)
            if answer is None or _STATUS[answer.choice] is LifecycleStatus.UNRESOLVED:
                result.append(item.id)
        return tuple(result)

    def _decision(self, item: ConfirmationItem, answer: TableAnswer) -> Decision:
        stamp = f"confirmation-table:{self.project_id}:revision:{answer.revision}"
        statement = {
            Choice.AS_IS: item.proposal.strip() or CHOICE_LABELS[Choice.AS_IS],
            Choice.DRAFT: item.proposal.strip() or CHOICE_LABELS[Choice.DRAFT],
            Choice.CHANGED: answer.words,
            Choice.TO_CREATE: answer.words if answer.words.strip() else CHOICE_LABELS[Choice.TO_CREATE],
            Choice.UNKNOWN: CHOICE_LABELS[Choice.UNKNOWN],
        }[answer.choice]
        return Decision(
            id=f"decision:{self.project_id}:{item.id}",
            owner=self.owner,
            status=_STATUS[answer.choice],
            version=answer.version,
            provenance=(
                Provenance(
                    id=f"prov:{self.project_id}:{item.id}:v{answer.version}",
                    source_id=f"answer:{self.project_id}",
                    method="user_answer",
                    locator=f"confirmation-table:{item.id}:v{answer.version}",
                    excerpt=answer.words or None,
                    captured_at=stamp,
                ),
            ),
            created_at=stamp,
            updated_at=stamp,
            question=item.question,
            decision=statement,
            rationale=f"choice={answer.choice.value}",
            alternatives=item.candidates,
        )

    @property
    def decisions(self) -> tuple[Decision, ...]:
        """One Decision record per answered item -- its latest answer. Not Canonical State."""

        result = []
        for item in self.items:
            answer = self.current(item.id)
            if answer is not None:
                result.append(self._decision(item, answer))
        return tuple(result)

    def to_record(self) -> dict[str, Any]:
        rows = []
        for item in self.items:
            answer = self.current(item.id)
            row = item.to_record()
            if answer is None:
                row.update(
                    choice=None, label=None, words=None, status=None, version=None,
                    registry_accepts=None, open=True,
                )  # fmt: skip
            else:
                status = _STATUS[answer.choice]
                row.update(
                    choice=answer.choice.value,
                    label=CHOICE_LABELS[answer.choice],
                    words=answer.words,
                    status=status.value,
                    version=answer.version,
                    registry_accepts=status not in _REFUSED_BY_REGISTRY,
                    open=status is LifecycleStatus.UNRESOLVED,
                )
            rows.append(row)
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "owner": self.owner,
            "revision": self.revision,
            "rows": rows,
            "history": [answer.to_record() for answer in self.answers],
            "open_item_ids": list(self.open_item_ids),
            "progress": {
                "answered": sum(1 for item in self.items if self.current(item.id) is not None),
                "total": len(self.items),
            },
            "canonical_mutation": False,
            "filesystem_scan": False,
        }


__all__ = [
    "CHOICE_LABELS",
    "MAX_WORDS",
    "TABLE_SCHEMA",
    "CanonTableError",
    "Choice",
    "ConfirmationItem",
    "ConfirmationTable",
    "TableAnswer",
]
