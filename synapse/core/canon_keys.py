"""Derive reviewable semantic canon keys from verbatim claims without a model."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace

from synapse.core.canon_check import CanonValue
from synapse.core.canon_claims import BULLET, TABLE_ROW, Claim
from synapse.core.canon_labels import (
    CHARACTER_FACT,
    LABELS,
    PROHIBITION,
    REQUIREMENT,
    is_time_axis_header,
    section_rule,
)
from synapse.core.canon_proposal import (
    ATTRIBUTE_LABELS,
    VOCABULARY_KEY,
    BadKeyError,
    attribute_token,
    character_heading,
    character_key,
    entry_for,
    rule_key,
    state_cells,
    state_key,
)

_PARTICLE = re.compile(r"^(.+?)(이|가|은|는|을|를|도|의)$")
_AMBIGUOUS_PARTICLE_WORDS = frozenset({"종이", "아이", "사이", "많이", "같이", "높이", "없이"})
_CONTEXT_SUFFIXES = ("에서", "에게", "으로", "부터", "까지", "에", "와", "과")


def _character(claim: Claim) -> tuple[str, str, str] | None:
    if section_rule(claim) != CHARACTER_FACT:
        return None
    heading = character_heading(claim)
    if heading is None or heading[1] is None or heading[2] is None:
        return None
    return heading[0], heading[1], heading[2]


def _particle_attribute(text: str, names: frozenset[str]) -> str | None:
    words = text.strip().split()
    for index, raw_word in enumerate(words):
        if index > 1:
            break
        word = raw_word.strip("\"'‘’“”「」『』()[]{}.,:;!?…。！？")
        if index == 1:
            previous = words[0].strip("\"'‘’“”「」『』()[]{}.,:;!?…。！？")
            if previous.endswith(_CONTEXT_SUFFIXES):
                return None
        if word in _AMBIGUOUS_PARTICLE_WORDS:
            return None
        particle = _PARTICLE.fullmatch(word)
        if particle is None:
            continue
        candidate = particle.group(1)
        if candidate.endswith(_CONTEXT_SUFFIXES):
            return None
        if not candidate or candidate in names:
            continue
        return attribute_token(candidate)
    return None


def _value(
    claim: Claim,
    *,
    key: str,
    kind: str,
    text: str,
    terms: tuple[str, ...] = (),
    needs_person: bool = False,
) -> CanonValue | None:
    try:
        entry = entry_for(
            claim,
            key=key,
            kind=kind,
            text=text,
            label=kind,
            terms=terms,
        )
    except BadKeyError:
        return None
    return CanonValue(
        key=entry.key,
        kind=entry.kind,
        text=entry.text,
        claim_id=entry.claim_id,
        status=entry.status,
        terms=entry.terms,
        file=entry.file,
        line=entry.line,
        needs_person=needs_person,
    )


def _append_value(result: list[CanonValue], value: CanonValue | None) -> None:
    if value is not None:
        result.append(value)


def _character_value(
    claim: Claim,
    *,
    name: str,
    attribute: str,
    text: str,
    terms: tuple[str, ...] = (),
    needs_person: bool = False,
) -> CanonValue | None:
    try:
        key = character_key(name, attribute)
    except BadKeyError:
        return None
    return _value(
        claim,
        key=key,
        kind="character_fact",
        text=text,
        terms=terms,
        needs_person=needs_person,
    )


def propose_values(
    claims: Iterable[Claim],
    *,
    confirmed_attributes: Iterable[str] | None = None,
    label_overrides: Mapping[str, str] | None = None,
) -> tuple[CanonValue, ...]:
    """Return deterministic value proposals; nothing produced here is confirmed."""
    claim_items = tuple(claims)
    if label_overrides is not None and (
        not isinstance(label_overrides, Mapping)
        or any(
            not isinstance(claim_id, str) or not isinstance(label, str) or label not in LABELS
            for claim_id, label in label_overrides.items()
        )
    ):
        raise ValueError("분류 선택은 정본 주장 ID와 허용된 이름표여야 합니다.")
    confirmed = None if confirmed_attributes is None else tuple(confirmed_attributes)
    normalized_confirmed = tuple(
        dict.fromkeys(
            token
            for attribute in confirmed or ()
            if isinstance(attribute, str) and (token := attribute_token(attribute)) is not None
        )
    )
    names = frozenset(
        character[0] for claim in claim_items if (character := _character(claim)) is not None
    )
    character_bullets: list[tuple[Claim, str, str | None]] = []
    attribute_claims: dict[str, set[str]] = {}
    attribute_people: dict[str, set[str]] = {}
    for claim in claim_items:
        character = _character(claim)
        if character is None or claim.kind != BULLET:
            continue
        name = character[0]
        candidate = _particle_attribute(claim.text, names)
        character_bullets.append((claim, name, candidate))
        if candidate is not None:
            attribute_claims.setdefault(candidate, set()).add(claim.id)
            attribute_people.setdefault(candidate, set()).add(name)

    repeated_attributes = {
        attribute
        for attribute, claim_ids in attribute_claims.items()
        if len(claim_ids) >= 2 or len(attribute_people[attribute]) >= 2
    }
    accepted_attributes = (
        set(repeated_attributes) if confirmed is None else set(normalized_confirmed)
    )
    accepted_attributes.update({"age", "sex"})
    candidate_by_claim = {claim.id: candidate for claim, _name, candidate in character_bullets}

    result: list[CanonValue] = []
    seen_characters: set[tuple[str, str, str]] = set()
    note_numbers: dict[str, int] = {}
    first_character_claim: Claim | None = None

    for claim in claim_items:
        if claim.kind == TABLE_ROW and claim.fields:
            first_header = claim.fields[0][0]
            if is_time_axis_header(first_header.strip()):
                row = claim.fields[0][1]
                for _key, header, cell in state_cells(claim):
                    # state_cells also lists a cell whose key cannot be built, for a request to be refused;
                    # here the key is built again so such a cell (an empty row name) is not proposed.
                    try:
                        key = state_key(row, header)
                    except BadKeyError:
                        continue
                    _append_value(result, _value(claim, key=key, kind="state_value", text=cell))
            continue

        selected_label = (label_overrides or {}).get(claim.id)
        if claim.kind == BULLET and selected_label is not None:
            _append_value(
                result,
                _value(
                    claim,
                    key=rule_key(claim),
                    kind=selected_label,
                    text=claim.text,
                ),
            )
            continue

        label = section_rule(claim)
        if label in {PROHIBITION, REQUIREMENT} and claim.kind == BULLET:
            kind = "prohibition" if label == PROHIBITION else "requirement"
            _append_value(
                result,
                _value(
                    claim,
                    key=rule_key(claim),
                    kind=kind,
                    text=claim.text,
                ),
            )
            continue

        character = _character(claim)
        if character is None:
            continue
        name, sex, age = character
        first_character_claim = first_character_claim or claim
        identity = (claim.file, name, claim.section)
        if identity not in seen_characters:
            for value in (
                _character_value(
                    claim,
                    name=name,
                    attribute="age",
                    text=age,
                    terms=(ATTRIBUTE_LABELS["age"],),
                ),
                _character_value(
                    claim,
                    name=name,
                    attribute="sex",
                    text=sex,
                    terms=(ATTRIBUTE_LABELS["sex"],),
                ),
            ):
                _append_value(result, value)
            seen_characters.add(identity)

        if claim.kind != BULLET:
            continue
        candidate = candidate_by_claim.get(claim.id)
        if candidate is None or candidate not in accepted_attributes:
            note_numbers[name] = note_numbers.get(name, 0) + 1
            _append_value(
                result,
                _character_value(
                    claim,
                    name=name,
                    attribute=f"note_{note_numbers[name]}",
                    text=claim.text,
                    needs_person=True,
                ),
            )
        else:
            _append_value(
                result,
                _character_value(
                    claim,
                    name=name,
                    attribute=candidate,
                    text=claim.text,
                    terms=(ATTRIBUTE_LABELS.get(candidate, candidate),),
                ),
            )

    if first_character_claim is not None:
        suggested = tuple(
            dict.fromkeys(
                (
                    "age",
                    "sex",
                    *(
                        attribute
                        for attribute in sorted(repeated_attributes)
                        if attribute not in {"age", "sex"}
                    ),
                )
            )
        )
        vocabulary = normalized_confirmed if confirmed is not None else suggested
        _append_value(
            result,
            _value(
                first_character_claim,
                key=VOCABULARY_KEY,
                kind="attribute_vocabulary",
                text=", ".join(ATTRIBUTE_LABELS.get(item, item) for item in vocabulary),
                terms=vocabulary,
            ),
        )

    unique: list[CanonValue] = []
    positions: dict[str, int] = {}
    sources_by_key: dict[str, list[tuple[str, str]]] = {}
    for value in result:
        sources_by_key.setdefault(value.key, []).append((value.claim_id, value.text))
        position = positions.get(value.key)
        if position is None:
            positions[value.key] = len(unique)
            unique.append(value)
            continue
        previous = unique[position]
        needs_person = previous.needs_person or value.needs_person or previous.text != value.text
        unique[position] = replace(previous, needs_person=needs_person)

    return tuple(
        replace(value, source_claims=tuple(dict.fromkeys(sources_by_key[value.key])))
        if len({text for _claim_id, text in sources_by_key[value.key]}) > 1
        else value
        for value in unique
    )


__all__ = ["propose_values"]
