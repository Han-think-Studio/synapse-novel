"""Assemble confirmed canon and deterministic review instructions without a model.

The block of canon a producer is shown, and the check of what it wrote, must rest on the same facts. The block has a
hash (``context_record``) so a later check can say whether the facts it checked against are the ones the producer was
given. A retry instruction says which canon value was contradicted, by which rule and where in the draft -- it does
not repeat the rejected wording, because a producer shown its own sentence tends to polish that sentence instead of
writing the scene differently.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from synapse.core import canon_check
from synapse.core.canon_check import (
    CanonValue,
    Conflict,
    automatically_checkable_values,
    unobservable_contrast_attributes,
)
from synapse.core.idea_session import sha256_text


@dataclass(frozen=True, slots=True)
class CanonContext:
    entries: tuple[CanonValue, ...]
    truncated: bool
    dropped_keys: tuple[str, ...]


MIN_CANON_KOREAN_RATIO = 0.35
CANON_KOREAN_RATIO = 0.50
_CHARACTER_KEY = re.compile(r"^character\.(.+)\.[^.]+$")


@dataclass(frozen=True, slots=True)
class ReviewCoverage:
    total_values: int
    checkable_values: int
    unchecked_values: int
    person_names_found: int
    person_names: tuple[str, ...]
    canon_language: str
    draft_korean_ratio: float
    unchecked_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Review:
    status: str
    passed: bool
    conflicts: tuple[Conflict, ...]
    coverage: ReviewCoverage
    possible_conflicts: tuple[dict[str, str], ...] = ()


def conflict_review(conflicts: tuple[Conflict, ...]) -> Review:
    """A review that is only a list of conflicts, for a caller that checked the text itself."""

    empty = ReviewCoverage(0, 0, 0, 0, (), "other", 0.0, ())
    return Review(
        status="conflict" if conflicts else "passed",
        passed=not conflicts,
        conflicts=conflicts,
        coverage=empty,
    )


def to_prompt_block(context: CanonContext) -> str:
    lines = []
    if context.entries:
        lines.append("CANON (confirmed by the user; the draft must not contradict any line):")
        for entry in context.entries:
            source = f" ({entry.file}:{entry.line})" if entry.file else ""
            lines.append(f"- [{entry.key}] {entry.text}{source}")
    if context.truncated:
        lines.append(
            f"NOTE: {len(context.dropped_keys)} canon values were left out because of the size limit: "
            + ", ".join(context.dropped_keys)
        )
    return "\n".join(lines)


def context_record(context: CanonContext) -> dict[str, Any]:
    """What a producer was shown, as a record a check can be bound to: the hash of the block, its keys, what was cut."""

    return {
        "block_hash": sha256_text(to_prompt_block(context)),
        "keys": [entry.key for entry in context.entries],
        "dropped": list(context.dropped_keys),
        "truncated": context.truncated,
    }


MAX_NAMES = 200
DEFAULT_MAX_CHARS = 4000
MAX_MAX_CHARS = 20_000
_REQUEST_KEYS = frozenset(
    {"block_hash", "subjects", "rows", "max_chars", "keys", "dropped", "truncated"}
)


class CanonContextError(ValueError):
    """A context request that is not one."""


def _names(raw: object, what: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) > MAX_NAMES
        or not all(isinstance(name, str) and name.strip() and len(name) <= 200 for name in raw)
    ):
        raise CanonContextError(f"`{what}` is a list of at most {MAX_NAMES} names.")
    return tuple(dict.fromkeys(name.strip() for name in raw))


def request_from(raw: object) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """The subjects, rows and size limit a block is built with, from a request or a ``context_record`` with them."""

    source = {} if raw is None else raw
    if not isinstance(source, dict):
        raise CanonContextError("`context` is the `context` object a context request returned.")
    limit = source.get("max_chars", DEFAULT_MAX_CHARS)
    if type(limit) is not int or not 0 <= limit <= MAX_MAX_CHARS:
        raise CanonContextError(f"`max_chars` is a whole number from 0 to {MAX_MAX_CHARS}.")
    return _names(source.get("subjects"), "subjects"), _names(source.get("rows"), "rows"), limit


def produced_with(values: Iterable[CanonValue], given: object) -> dict[str, Any] | None:
    """What a caller says it wrote from, set against the block these values give now, ready for a receipt.

    ``given`` is the ``context`` object that came back with the block (its hash and the subjects, rows and limit it was
    built with); ``None`` means the caller did not say, and there is nothing to bind.
    """

    if given is None:
        return None
    if (
        not isinstance(given, dict)
        or set(given) - _REQUEST_KEYS
        or not isinstance(given.get("block_hash"), str)
    ):
        raise CanonContextError("`context` is the `context` object a context request returned.")
    subjects, rows, limit = request_from(given)
    current = build_canon_context(values, subjects=subjects, rows=rows, max_chars=limit)
    current_record = context_record(current)
    if "keys" in given:
        keys = given["keys"]
        if not isinstance(keys, (list, tuple)) or not all(
            isinstance(key, str) and key for key in keys
        ):
            raise CanonContextError("context.keys must be a list of canon keys.")
        current_record["given_keys"] = list(dict.fromkeys(keys))
    return {"given_block_hash": given["block_hash"], "current": current_record}


def state_key_belongs_to_row(key: str, row: str) -> bool:
    """Whether a state key has this whole row part and one undotted header part."""

    if not isinstance(key, str) or not isinstance(row, str):
        return False
    prefix = f"state.{row}."
    if not key.startswith(prefix):
        return False
    header = key[len(prefix) :]
    return bool(header) and "." not in header


def build_canon_context(
    values: Iterable[CanonValue],
    *,
    subjects: Iterable[str] = (),
    rows: Iterable[str] = (),
    max_chars: int = 4000,
) -> CanonContext:
    """Keep input order and budget the complete rendered block, including any note."""

    if type(max_chars) is not int:
        raise TypeError("max_chars는 정수여야 합니다.")
    if max_chars < 0:
        raise ValueError("max_chars는 0 이상이어야 합니다.")
    character_prefixes = tuple(f"character.{name}." for name in subjects)
    row_names = tuple(rows)
    selected = tuple(
        value
        for value in values
        if value.status == "CONFIRMED"
        and (
            value.kind in {"prohibition", "requirement"}
            or value.key.startswith(character_prefixes)
            or any(state_key_belongs_to_row(value.key, row) for row in row_names)
        )
    )
    removal_order = (
        [
            index
            for index in reversed(range(len(selected)))
            if selected[index].kind not in {"prohibition", "requirement"}
        ]
        + [
            index
            for index in reversed(range(len(selected)))
            if selected[index].kind == "requirement"
        ]
        + [
            index
            for index in reversed(range(len(selected)))
            if selected[index].kind == "prohibition"
        ]
    )
    removed: set[int] = set()
    dropped: list[str] = []
    context = CanonContext(selected, False, ())
    for index in removal_order:
        if len(to_prompt_block(context)) <= max_chars:
            break
        removed.add(index)
        dropped.append(selected[index].key)
        context = CanonContext(
            tuple(entry for position, entry in enumerate(selected) if position not in removed),
            True,
            tuple(dropped),
        )
    return context


def _hangul_ratio(text: str) -> float:
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return 0.0
    hangul = sum(
        "\uac00" <= character <= "\ud7a3"
        or "\u1100" <= character <= "\u11ff"
        or "\u3130" <= character <= "\u318f"
        or "\ua960" <= character <= "\ua97f"
        or "\ud7b0" <= character <= "\ud7ff"
        for character in letters
    )
    return hangul / len(letters)


def _canon_person_names(values: tuple[CanonValue, ...]) -> tuple[str, ...]:
    names = []
    for value in values:
        match = _CHARACTER_KEY.fullmatch(value.key)
        if match:
            names.append(match.group(1))
    return tuple(dict.fromkeys(names))


def review_candidate(
    text: str,
    values: Iterable[CanonValue],
    *,
    coverage_text: str | None = None,
    required_names: Iterable[str] = (),
) -> Review:
    """Check all supplied text; measure coverage on the body when supplied separately.

    ``required_names`` are the people the plan says act here: one the body never names keeps the review unchecked.
    """
    observed_text = text if coverage_text is None else coverage_text
    confirmed = tuple(value for value in values if value.status == "CONFIRMED")
    conflicts, possible = canon_check.check_text_with_possible(text, confirmed)
    checkable = automatically_checkable_values(observed_text, confirmed)
    canon_names = _canon_person_names(confirmed)
    found_names = tuple(name for name in canon_names if name in observed_text)
    canon_ratio = _hangul_ratio(" ".join(value.text for value in confirmed))
    draft_ratio = _hangul_ratio(observed_text)
    canon_language = "ko" if canon_ratio >= CANON_KOREAN_RATIO else "other"
    reasons = []
    if canon_names and not found_names:
        reasons.append("초안에서 정본 인물 이름을 하나도 찾지 못했습니다.")
    absent = [name for name in dict.fromkeys(required_names) if name and name not in observed_text]
    if absent:
        reasons.append("계획에서 이 부분에 정한 인물이 본문에 나오지 않습니다: " + ", ".join(absent))
    if canon_language == "ko" and draft_ratio < MIN_CANON_KOREAN_RATIO:
        reasons.append("초안의 한글 비율이 기준 35%보다 낮습니다.")
    if not checkable:
        reasons.append("자동 대조할 수 있는 정본 값이 없습니다.")
    unseen_attributes = unobservable_contrast_attributes(observed_text, confirmed)
    if unseen_attributes:
        reasons.append("비교할 속성어를 초안에서 찾지 못했습니다: " + ", ".join(unseen_attributes))
    coverage = ReviewCoverage(
        total_values=len(confirmed),
        checkable_values=len(checkable),
        unchecked_values=len(confirmed) - len(checkable),
        person_names_found=len(found_names),
        person_names=found_names,
        canon_language=canon_language,
        draft_korean_ratio=round(draft_ratio, 4),
        unchecked_reasons=tuple(reasons),
    )
    status = "conflict" if conflicts else "unchecked" if reasons or possible else "passed"
    by_identity = {(value.key, value.claim_id): value for value in confirmed}
    possible_records = tuple(
        {
            "canon_key": item.value_key,
            "canon_quote": by_identity[(item.value_key, item.claim_id)].text,
            "draft_quote": item.evidence,
            "reason": item.message,
        }
        for item in possible
    )
    return Review(
        status=status, passed=status == "passed", conflicts=conflicts,
        coverage=coverage, possible_conflicts=possible_records,
    )


_FIXES = {
    "R1": "this character's age is written differently in the draft. Fix: use the confirmed age.",
    "R2": "the draft calls two characters alike where the canon says they differ. Fix: show the confirmed difference.",
    "R3": "the draft contains a confirmed forbidden phrase. Fix: say it another way.",
    "R4": "the draft matches a pattern the person confirmed as not to be written. Fix: say it another way.",
    "R6": "the draft describes this character the opposite way from the canon. Fix: describe it as the canon says.",
}
_SENTENCE_END = re.compile(r"[.!?\u3002\uff01\uff1f\n]")


def _where(text: str | None, evidence: str) -> str:
    """Paragraph and sentence of the first place ``evidence`` stands in ``text``, or nothing when it is not found."""

    if not text or not evidence:
        return ""
    position = text.find(evidence)
    if position < 0:
        return ""
    before = text[:position]
    paragraph = len(re.split(r"\n\s*\n", before))
    sentence = len(_SENTENCE_END.findall(re.split(r"\n\s*\n", before)[-1])) + 1
    return f" (paragraph {paragraph}, sentence {sentence})"


def to_retry_instruction(
    review: Review, values: Iterable[CanonValue], text: str | None = None
) -> str:
    """What to change, without quoting the wording that was rejected.

    ``text`` is the draft that was reviewed; with it the instruction says where in the draft each conflict stands.
    """

    if review.passed and not review.possible_conflicts:
        return ""
    confirmed = {
        (value.key, value.claim_id): value for value in values if value.status == "CONFIRMED"
    }
    has_conflicts = bool(review.conflicts)
    lines = (
        ["Rewrite the draft. It contradicts these confirmed canon values:"]
        if has_conflicts
        else ["Rewrite the draft. The canon review is unchecked."]
    )
    for conflict in review.conflicts:
        value = confirmed.get((conflict.value_key, conflict.claim_id))
        if value is None:
            raise ValueError(f"충돌의 확정 값을 찾을 수 없습니다: {conflict.value_key}")
        fix = _FIXES.get(conflict.rule, "the draft conflicts with this value.")
        lines.append(
            f"- [{value.key}] canon says: {value.text}. Rule {conflict.rule}: {fix}"
            f"{_where(text, conflict.evidence)}"
        )
    if review.status == "unchecked" or review.coverage.unchecked_reasons:
        language = "Korean" if review.coverage.canon_language == "ko" else "the canon's language"
        names = ", ".join(
            review.coverage.person_names or _canon_person_names(tuple(confirmed.values()))
        )
        lines.append(
            f"Write in {language} and use the exact canon character names as shown"
            + (f": {names}." if names else ".")
        )
        lines.extend(f"- {reason}" for reason in review.coverage.unchecked_reasons)
    lines.append(
        "Change only what conflicts; keep everything else. Do not repeat the wording that conflicts."
    )
    return "\n".join(lines)


__all__ = [
    "MIN_CANON_KOREAN_RATIO",
    "CanonContext",
    "CanonContextError",
    "Review",
    "ReviewCoverage",
    "build_canon_context",
    "conflict_review",
    "context_record",
    "produced_with",
    "request_from",
    "review_candidate",
    "state_key_belongs_to_row",
    "to_prompt_block",
    "to_retry_instruction",
]
