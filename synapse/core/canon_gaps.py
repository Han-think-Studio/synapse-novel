"""Find missing canon values, time-axis entries and conflicting confirmations."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from synapse.core.canon_check import CanonValue
from synapse.core.canon_claims import TABLE_ROW, Claim
from synapse.core.canon_labels import character_subject, is_time_axis_header
from synapse.core.canon_proposal import BadKeyError, character_key


@dataclass(frozen=True, slots=True)
class Gap:
    """One Korean question with an immutable tuple of supporting evidence."""

    kind: str
    key: str
    question: str
    evidence: tuple[str, ...]


_CHARACTER_ATTRIBUTE = re.compile(r"^character\.(.+)\.([^.]+)$")
_FIRST_INTEGER = re.compile(r"\d+")


def _peer_gaps(claims: tuple[Claim, ...], values: tuple[CanonValue, ...]) -> list[Gap]:
    vocabulary = next(
        (
            value
            for value in values
            if value.kind == "attribute_vocabulary" and value.status == "CONFIRMED"
        ),
        None,
    )
    if vocabulary is None:
        return []
    required = set(vocabulary.terms)
    if not required:
        return []

    subjects: dict[str, tuple[str, str]] = {}
    for claim in claims:
        subject = character_subject(claim)
        if subject is not None:
            name, heading = subject
            try:
                key = character_key(name)
            except BadKeyError:
                continue
            subjects.setdefault(name, (heading, key))

    attributes: dict[str, set[str]] = {}
    owners: dict[str, set[str]] = {}
    for value in values:
        if value.status != "CONFIRMED":
            continue
        match = _CHARACTER_ATTRIBUTE.fullmatch(value.key)
        if match is None:
            continue
        name, attribute = match.groups()
        if attribute not in required:
            continue
        try:
            character_key(name, attribute)
        except BadKeyError:
            continue
        attributes.setdefault(name, set()).add(attribute)
        owners.setdefault(attribute, set()).add(name)

    gaps: list[Gap] = []
    for name in sorted(subjects):
        present = attributes.get(name, set())
        if not present:
            gaps.append(
                Gap(
                    kind="subject_without_values",
                    key=subjects[name][1],
                    question=f"{name}의 속성 값들을 정해 주시겠어요?",
                    evidence=(subjects[name][0],),
                )
            )
            continue
        for attribute in sorted(owners.keys() & required - present):
            gaps.append(
                Gap(
                    kind="peer_missing",
                    key=character_key(name, attribute),
                    question=f"{name}의 {attribute} 값은 무엇인가요?",
                    evidence=tuple(sorted(owners[attribute])),
                )
            )
    return gaps


def _time_axis_gaps(claims: tuple[Claim, ...]) -> list[Gap]:
    tables: list[list[Claim]] = []
    for claim in sorted(claims, key=lambda item: (item.file, item.line)):
        if (
            claim.kind != TABLE_ROW
            or not claim.fields
            or not is_time_axis_header(claim.fields[0][0])
        ):
            continue
        if tables:
            previous = tables[-1][-1]
            same_table = (
                claim.file == previous.file
                and claim.section == previous.section
                and claim.line == previous.line + 1
                and tuple(header for header, _cell in claim.fields)
                == tuple(header for header, _cell in previous.fields)
            )
        else:
            same_table = False
        if same_table:
            tables[-1].append(claim)
        else:
            tables.append([claim])

    gaps: list[Gap] = []
    for rows in tables:
        numbers: set[int] = set()
        for row in rows:
            match = _FIRST_INTEGER.search(row.fields[0][1])
            if match is not None:
                numbers.add(int(match.group(0)))
        if numbers:
            for number in range(min(numbers), max(numbers) + 1):
                if number not in numbers:
                    gaps.append(
                        Gap(
                            kind="row_missing",
                            key=f"row.{number}",
                            question=f"시간축 표의 {number}번 항목을 채워 주시겠어요?",
                            evidence=tuple(row.fields[0][1] for row in rows),
                        )
                    )
        for row in rows:
            first_cell = row.fields[0][1]
            for header, cell in row.fields:
                if cell == "":
                    gaps.append(
                        Gap(
                            kind="cell_empty",
                            key=f"row.{first_cell}.{header}",
                            question=(
                                f"시간축 표의 {first_cell or '이'} 행에서 "
                                f"{header} 칸에는 무엇을 넣을까요?"
                            ),
                            evidence=(f"{row.file}:{row.line}",),
                        )
                    )
    return gaps


def _value_conflicts(values: tuple[CanonValue, ...]) -> list[Gap]:
    confirmed: dict[str, list[str]] = {}
    for value in values:
        if value.status != "CONFIRMED":
            continue
        texts = confirmed.setdefault(value.key, [])
        if value.text not in texts:
            texts.append(value.text)
    return [
        Gap(
            kind="value_conflict",
            key=key,
            question=f"{key} 값이 서로 다르게 확정되어 있는데 어느 값을 사용할까요?",
            evidence=tuple(texts[:2]),
        )
        for key, texts in sorted(confirmed.items())
        if len(texts) > 1
    ]


def find_gaps(claims: Iterable[Claim], values: Iterable[CanonValue]) -> tuple[Gap, ...]:
    """Ask about missing entries and conflicting confirmed values without a model."""

    claim_items = tuple(claims)
    value_items = tuple(values)
    return tuple(
        _peer_gaps(claim_items, value_items)
        + _time_axis_gaps(claim_items)
        + _value_conflicts(value_items)
    )


__all__ = ["Gap", "find_gaps"]
