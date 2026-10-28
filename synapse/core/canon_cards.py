"""Pure adapters for canon decision cards; a default needs explicit acceptance to confirm values."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace

from synapse.core.canon_check import CanonValue, Conflict, _opposite_vocabulary
from synapse.core.canon_claims import BULLET, TABLE_ROW, Claim
from synapse.core.canon_gaps import Gap
from synapse.core.canon_labels import CHARACTER_FACT, LABELS, OTHER, REFERENCE_ENTRY, section_rule
from synapse.core.canon_values import (
    ValueEntry,
    ValueStore,
    claim_source_sha256,
    confirm,
    confirm_source,
)
from synapse.core.idea_session import canonical_hash, canonical_json

_KINDS = frozenset({"missing_key", "conflict", "unlabeled", "review", "approval"})
_BLOCKING_GAP_KINDS = frozenset(
    {"peer_missing", "subject_without_values", "row_missing", "cell_empty"}
)
_LABEL_NAMES = dict(
    zip(
        LABELS,
        (
            "피할 것",
            "지킬 것",
            "인물 정보",
            "시점별 상태",
            "문체 안내",
            "작품 정보",
            "진행 순서",
            "장소",
            "기타 정보",
            "점검 항목",
            "참고 자료",
        ),
        strict=True,
    )
)
_ATTRIBUTE_NAMES = {"age": "나이", "sex": "성별", "pressure": "필압"}


@dataclass(frozen=True, slots=True)
class Choice:
    key: str
    text: str

    def __post_init__(self):
        if (
            not isinstance(self.key, str)
            or not isinstance(self.text, str)
            or not self.key
            or not self.text
            or "\n" in self.text
        ):
            raise ValueError("선택지에는 식별자와 한 줄 설명이 필요합니다.")


@dataclass(frozen=True, slots=True)
class Evidence:
    file: str
    line: int
    text: str
    source: str = "claim"

    def __post_init__(self):
        if not isinstance(self.file, str) or not isinstance(self.text, str):
            raise TypeError("근거는 원문과 위치를 문자열로 보존해야 합니다.")
        if self.source not in {"claim", "value", "draft", "context"}:
            raise ValueError("근거의 출처 종류가 맞지 않습니다.")
        if type(self.line) is not int or self.line < 0:
            raise ValueError("근거의 줄 번호가 맞지 않습니다.")


@dataclass(frozen=True, slots=True)
class DecisionCard:
    kind: str
    title: str
    why: str
    default: str | None
    choices: tuple[Choice, ...]
    evidence: tuple[Evidence, ...]
    blocking: bool
    key: str = ""
    default_choice: str | None = None
    items: tuple[DecisionCard, ...] = ()
    entries: tuple[ValueEntry, ...] = ()
    labels: tuple[tuple[str, str | None, str | None], ...] = ()
    alternatives: tuple[ValueEntry, ...] = ()

    def __post_init__(self):
        for name in ("choices", "evidence", "items", "entries", "labels", "alternatives"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "labels", tuple(tuple(label) for label in self.labels))
        if self.kind not in _KINDS or type(self.blocking) is not bool:
            raise ValueError("카드의 종류나 진행 여부가 맞지 않습니다.")
        if any(not value or "\n" in value for value in (self.title, self.why)):
            raise ValueError("제목과 이유는 비어 있지 않은 한 줄이어야 합니다.")
        keys = tuple(choice.key for choice in self.choices)
        if not 2 <= len(keys) <= 4 or len(set(keys)) != len(keys):
            raise ValueError("카드 선택지는 서로 다른 2~4개여야 합니다.")
        if (self.default is None) != (self.default_choice is None):
            raise ValueError("추천에는 선택지와 이유를 함께 지정해야 합니다.")
        if self.default_choice is not None and (
            self.default_choice not in keys or "\n" in self.default
        ):
            raise ValueError("추천은 카드의 선택지 중 하나여야 합니다.")

    @property
    def id(self) -> str:
        payload = self._record()
        payload["bindings"] = [asdict(entry) for entry in self.entries]
        payload["labels"] = self.labels
        if self.alternatives:
            payload["alternatives"] = [asdict(entry) for entry in self.alternatives]
        return "card:" + canonical_hash(payload).removeprefix("sha256:")

    def _record(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "title": self.title,
            "why": self.why,
            "default": self.default,
            "choices": [asdict(choice) for choice in self.choices],
            "evidence": [asdict(item) for item in self.evidence],
            "blocking": self.blocking,
            "key": self.key,
            "default_choice": self.default_choice,
            "items": [item.to_record() for item in self.items],
            "value_keys": [entry.key for entry in self.entries],
            "values": [
                {"key": entry.key, "kind": entry.kind, "text": entry.text, "status": entry.status}
                for entry in self.entries
            ],
            "claim_ids": [claim_id for claim_id, _rule, _model in self.labels],
        }

    def to_record(self) -> dict[str, object]:
        return {"id": self.id, **self._record()}


def _claim_evidence(claim: Claim) -> Evidence:
    return Evidence(claim.file, claim.line, claim.text)


def _value_evidence(value: CanonValue | ValueEntry, claims: tuple[Claim, ...]) -> Evidence:
    claim = next((item for item in claims if item.id == value.claim_id), None)
    if claim is not None:
        return _claim_evidence(claim)
    return Evidence(value.file, value.line, value.text, "value")


def _human_key(key: str, values: tuple[CanonValue, ...]) -> str:
    if key.startswith("character."):
        parts = key.split(".")
        name = parts[1]
        if len(parts) == 2:
            return f"{name}의 정보"
        attribute = parts[-1]
        term = next(
            (
                term
                for value in values
                if value.key.endswith("." + attribute)
                for term in value.terms
                if term
            ),
            None,
        )
        return f"{name}의 {term or _ATTRIBUTE_NAMES.get(attribute, '값')}"
    if key.startswith("row."):
        return " · ".join(key.split(".")[1:]) + " 항목"
    return "이 항목"


def _gap_card(gap: Gap, values: tuple[CanonValue, ...], claims: tuple[Claim, ...]) -> DecisionCard:
    evidence: list[Evidence] = []
    for value in values:
        peer = gap.kind == "peer_missing" and value.key.endswith("." + gap.key.rsplit(".", 1)[-1])
        if value.key == gap.key or peer:
            evidence.append(_value_evidence(value, claims))
    if gap.kind == "subject_without_values":
        name = gap.key.removeprefix("character.")
        for claim in claims:
            heading = claim.section.rsplit(" / ", 1)[-1]
            if heading.partition("—")[0].strip() == name:
                evidence.append(_claim_evidence(claim))
                break
    for raw in gap.evidence:
        matched = next(
            (
                claim
                for claim in claims
                if raw == f"{claim.file}:{claim.line}"
                or (claim.fields and claim.fields[0][1] == raw)
            ),
            None,
        )
        if matched is not None:
            evidence.append(_claim_evidence(matched))
        else:
            evidence.append(Evidence("", 0, raw, "context"))
    evidence = list(dict.fromkeys(evidence))
    conflict = gap.kind == "value_conflict"
    title = (
        f"서로 다른 {_human_key(gap.key, values)} 값을 확인해 주세요"
        if conflict
        else f"{_human_key(gap.key, values)}: 아직 정해지지 않았습니다"
    )
    return DecisionCard(
        "conflict" if conflict else "missing_key",
        title,
        "어느 값이 맞는지 정해야 초안과 비교할 수 있습니다."
        if conflict
        else "빈 값을 모델이 지어내지 않도록 먼저 정해야 합니다.",
        None,
        (
            Choice("fill", "값을 적습니다. 적은 내용을 제안 값으로 검토합니다."),
            Choice("keep_unknown", "아직 모름으로 둡니다. 이 항목은 질문으로 남습니다."),
        ),
        tuple(evidence),
        gap.kind in _BLOCKING_GAP_KINDS,
        key=gap.key,
    )


def _needs_person_cards(
    gaps: tuple[Gap, ...], values: tuple[CanonValue, ...], claims: tuple[Claim, ...]
) -> tuple[DecisionCard, ...]:
    values_by_key = {value.key: value for value in values}
    claims_by_id = {claim.id: claim for claim in claims}
    groups: dict[tuple[str, str], list[tuple[Gap, CanonValue, Claim]]] = {}
    for gap in gaps:
        if gap.kind != "needs_person":
            continue
        value = values_by_key.get(gap.key)
        claim = claims_by_id.get(value.claim_id) if value is not None else None
        if value is None or claim is None:
            continue
        groups.setdefault((claim.file, claim.section), []).append((gap, value, claim))

    cards = []
    for (file, section), items in groups.items():
        cards.append(
            DecisionCard(
                "review",
                f"참고 문장 {len(items)}개를 참고용으로 둡니다. 확인?",
                "속성 이름이 분명하지 않은 문장은 참고용으로 남기며 확정 값으로 쓰지 않습니다.",
                "참고용으로 두는 것을 추천합니다.",
                (
                    Choice("use_reference", "참고용으로 둡니다."),
                    Choice("keep_unknown", "미정으로 남깁니다."),
                ),
                tuple(_claim_evidence(claim) for _gap, _value, claim in items),
                False,
                key=f"needs_person:{file}:{section}",
                default_choice="use_reference",
                labels=tuple((claim.id, None, None) for _gap, _value, claim in items),
            )
        )
    return tuple(cards)


def _gap_cards(
    gaps: Iterable[Gap], values: tuple[CanonValue, ...], claims: tuple[Claim, ...]
) -> tuple[DecisionCard, ...]:
    gap_items = tuple(gaps)
    cards = [_gap_card(gap, values, claims) for gap in gap_items if gap.kind != "needs_person"]
    cards.extend(_needs_person_cards(gap_items, values, claims))
    return tuple(cards)


def _label_card(question: Mapping[str, object], *, confident: bool = False) -> DecisionCard:
    claim_id = str(question["claim_id"])
    rule, model = question.get("section_says"), question.get("model_says")
    rule = rule if rule in LABELS else None
    model = model if model in LABELS else None
    choices = []
    if rule:
        choices.append(
            Choice(
                "use_rule", f"절 제목을 따릅니다. 이 문장을 '{_LABEL_NAMES[rule]}' 제안으로 둡니다."
            )
        )
    if model and model != rule:
        choices.append(
            Choice(
                "use_model",
                f"모델 제안을 고릅니다. 이 문장을 '{_LABEL_NAMES[model]}' 제안으로 둡니다.",
            )
        )
    choices.extend(
        (
            Choice("choose_kind", "직접 종류를 고릅니다. 고른 종류를 제안으로 기록합니다."),
            Choice("keep_unknown", "아직 모름으로 둡니다. 이 문장은 미정으로 남습니다."),
        )
    )
    return DecisionCard(
        "review" if confident else "unlabeled",
        "절 제목으로 정해진 종류를 확인해 주세요"
        if confident
        else "이 문장이 어떤 정보인지 골라 주세요",
        "제목의 규칙을 따르되 값은 별도로 확정합니다."
        if confident
        else "종류가 정해져야 알맞은 칸에 값을 넣을 수 있습니다.",
        "절 제목을 따르는 것을 추천합니다. 원문 구조를 근거로 하며 값은 아직 제안입니다."
        if confident
        else None,
        tuple(choices),
        (Evidence(str(question["file"]), int(question["line"]), str(question["text"])),),
        False,
        key=claim_id,
        default_choice="use_rule" if confident else None,
        labels=((claim_id, rule, model),),
    )


def _reference_label_card(
    questions: tuple[Mapping[str, object], ...], file: str, section: str
) -> DecisionCard:
    items = tuple(_label_card(question) for question in questions)
    return DecisionCard(
        "review",
        f"참고 문장 {len(items)}개를 참고용으로 둡니다. 확인?",
        "같은 파일과 절의 분류가 불분명한 문장을 한꺼번에 참고용으로 제안합니다.",
        "참고용으로 두는 것을 추천합니다.",
        (
            Choice("use_reference", "참고용으로 둡니다."),
            Choice("review_each", "문장별로 직접 종류를 고릅니다."),
        ),
        tuple(dict.fromkeys(item.evidence[0] for item in items)),
        False,
        key=f"reference:{file}:{section}",
        default_choice="use_reference",
        items=items,
    )


def _label_cards(questions: Iterable[Mapping[str, object]]) -> tuple[DecisionCard, ...]:
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    batches = []
    for question in questions:
        if question.get("kind") == "batch_rule_confirmation":
            items = tuple(_label_card(item, confident=True) for item in question["claims"])
            if items:
                batches.append(
                    DecisionCard(
                        "review",
                        f"규칙으로 정해진 {len(items)}건을 한꺼번에 확인해 주세요",
                        "제목의 규칙으로 종류를 제안했습니다. 확정 값은 별도로 확인해야 합니다.",
                        "절 제목 규칙을 쓰는 것을 추천합니다. 원문 근거를 보존하고 값은 제안으로 둡니다.",
                        (
                            Choice(
                                "use_rule",
                                "절 제목 규칙을 씁니다. 묶인 항목의 종류만 제안으로 기록합니다.",
                            ),
                            Choice(
                                "review_each", "항목별로 확인합니다. 묶음을 펼쳐 각각 고릅니다."
                            ),
                        ),
                        tuple(item.evidence[0] for item in items),
                        False,
                        default_choice="use_rule",
                        items=items,
                    )
                )
            continue
        group_key = (str(question.get("file", "")), str(question.get("section", "")))
        groups.setdefault(group_key, []).append(question)

    cards = [
        _reference_label_card(tuple(items), file, section)
        for (file, section), items in groups.items()
    ]
    cards.extend(batches)
    return tuple(cards)


def _conflict_card(
    conflict: Conflict, values: tuple[CanonValue, ...], claims: tuple[Claim, ...]
) -> DecisionCard:
    titles = {
        "R1": "인물의 나이가 확정 값과 다릅니다",
        "R2": "서로 다른 인물의 속성을 같다고 썼습니다",
        "R3": "피하기로 한 표현이 초안에 들어 있습니다",
        "R4": "사용자가 피하기로 한 표현이 초안에 있습니다",
    }
    evidence = [Evidence("", 0, conflict.evidence, "draft")]
    value = next(
        (
            item
            for item in values
            if item.status == "CONFIRMED"
            and item.key == conflict.value_key
            and item.claim_id == conflict.claim_id
        ),
        None,
    )
    if value is not None:
        evidence.append(_value_evidence(value, claims))
    return DecisionCard(
        "conflict",
        titles.get(conflict.rule, "초안이 확정 값과 어긋납니다"),
        "확정한 내용과 다른 부분을 확인해야 잘못된 초안의 적용을 막을 수 있습니다.",
        None,
        (
            Choice("rewrite", "초안을 고칩니다. 어긋난 부분을 수정하고 다시 검사합니다."),
            Choice("review", "직접 확인합니다. 충돌은 확인할 항목으로 남습니다."),
        ),
        tuple(evidence),
        False,
        key=conflict.value_key,
    )


def _attribute_tokens(words: str) -> tuple[str, ...]:
    aliases = {"나이": "age", "성별": "sex"}
    pieces = re.split(r"[,，/·:\s]+", words.strip())
    return tuple(
        dict.fromkeys(
            aliases.get(piece.strip(), piece.strip()) for piece in pieces if piece.strip()
        )
    )


def _attribute_vocabulary_card(entry: ValueEntry, claims: tuple[Claim, ...]) -> DecisionCard:
    terms = tuple(term for term in entry.terms if term not in {"age", "sex", "나이", "성별"})
    evidence = tuple(
        dict.fromkeys(
            _claim_evidence(claim)
            for claim in claims
            if section_rule(claim) == CHARACTER_FACT
            and claim.kind == BULLET
            and any(term in claim.text for term in terms)
        )
    )
    if not evidence:
        evidence = (_value_evidence(entry, claims),)
    return DecisionCard(
        "review",
        f"인물 정보 항목: {entry.text} 맞나요?",
        "반복된 말과 인물 제목에서 후보를 모았습니다. 확정한 항목만 인물 사이의 빈칸을 비교합니다.",
        "제안한 목록 전체를 확정하는 것을 추천합니다.",
        (
            Choice("use_list", "이 목록으로 확정합니다."),
            Choice("remove_items", "항목을 빼고 확정합니다. 뺄 항목을 쉼표로 적습니다."),
            Choice("add_items", "항목을 추가하고 확정합니다. 더할 항목을 쉼표로 적습니다."),
            Choice("keep_proposed", "아직 확정하지 않고 제안으로 둡니다."),
        ),
        evidence,
        False,
        key=entry.key,
        default_choice="use_list",
        entries=(entry,),
    )


def _is_structured_value(entry: ValueEntry, claims_by_id: Mapping[str, Claim]) -> bool:
    if re.search(r"\.note_\d+$", entry.key):
        return False
    claim = claims_by_id.get(entry.claim_id)
    if claim is None:
        return False
    if entry.kind == "state_value":
        return claim.kind == TABLE_ROW and any(cell == entry.text for _header, cell in claim.fields)
    if entry.kind in {"prohibition", "requirement"}:
        return claim.kind == BULLET and claim.text == entry.text
    if entry.kind != "character_fact":
        return False
    attribute = entry.key.rsplit(".", 1)[-1]
    heading = claim.section.rsplit(" / ", 1)[-1]
    if attribute in {"age", "sex"}:
        return entry.text in heading
    return claim.kind == BULLET and claim.text == entry.text


def _is_pressure_value(entry: ValueEntry) -> bool:
    if entry.kind != "character_fact" or not entry.key.startswith("character."):
        return False
    attribute = entry.key.rsplit(".", 1)[-1]
    return attribute in {"pressure", "\ud544\uc555"} or "\ud544\uc555" in entry.terms


def _opposite_tokens(words: str) -> tuple[str, ...]:
    tokens = tuple(
        dict.fromkeys(part.strip() for part in re.split(r"[,，;；\n]+", words) if part.strip())
    )
    if any(len(token) > 80 for token in tokens):
        raise ValueError("반대 표현은 각각 80자 이하여야 합니다.")
    return tokens


def _value_group(
    entries: tuple[ValueEntry, ...], claims: tuple[Claim, ...], *, structured: bool
) -> DecisionCard:
    choice = "confirm" if structured else "keep_proposed"
    recommendation = (
        "원문에 그대로 적힌 값을 확인하고 확정합니다."
        if structured
        else "속성을 분명히 정할 수 없어 제안으로 둡니다."
    )
    base_choices = (
        Choice("confirm", "원문을 확인하고 확정합니다. 이후 초안은 이 값을 지켜야 합니다."),
        Choice("keep_proposed", "제안으로 둡니다. 아직 정본으로 사용하지 않습니다."),
    )
    items = tuple(
        DecisionCard(
            "review",
            "제안 값을 확정할지 확인해 주세요",
            "확정한 값만 이후 초안의 기준으로 사용됩니다.",
            recommendation,
            base_choices
            + (
                (
                    Choice(
                        "confirm_opposite_terms",
                        "기본 반대 표현을 확인합니다. 더할 말은 쉼표로 적습니다.",
                    ),
                )
                if _is_pressure_value(entry) and _opposite_vocabulary(entry.text)
                else ()
            ),
            (_value_evidence(entry, claims),),
            False,
            key=entry.key,
            default_choice=choice,
            entries=(entry,),
        )
        for entry in entries
    )
    if len(items) == 1:
        item = items[0]
        vocabulary = _opposite_vocabulary(entries[0].text) if _is_pressure_value(entries[0]) else ()
        if vocabulary:
            item = replace(
                item,
                why="기본 반대 표현: "
                + ", ".join(vocabulary)
                + ". 필요하면 선택에서 더할 수 있습니다.",
            )
        return item
    choices = [
        Choice("confirm", "원문을 확인하고 확정합니다."),
        Choice("keep_proposed", "제안으로 둡니다."),
        Choice("review_each", "항목별로 확인합니다."),
    ]
    pressure_entries = tuple(entry for entry in entries if _is_pressure_value(entry))
    if len(pressure_entries) == 1 and _opposite_vocabulary(pressure_entries[0].text):
        choices.append(
            Choice(
                "confirm_opposite_terms", "기본 반대 표현을 확인합니다. 더할 말은 쉼표로 적습니다."
            )
        )
    why = "각 원문 줄과 제안 값을 확인한 뒤 기본 선택을 적용합니다."
    if len(pressure_entries) == 1:
        vocabulary = _opposite_vocabulary(pressure_entries[0].text)
        if vocabulary:
            why += " 필압 기본 반대 표현: " + ", ".join(vocabulary) + "."
    return DecisionCard(
        "review",
        f"원문에서 바로 확인할 수 있는 제안 값 {len(items)}건"
        if structured
        else f"뜻을 더 확인할 제안 값 {len(items)}건",
        why,
        recommendation,
        tuple(choices),
        tuple(dict.fromkeys(item.evidence[0] for item in items)),
        False,
        items=items,
        default_choice=choice,
    )


def _source_choice_key(candidate: ValueEntry, *, disambiguate: bool) -> str:
    key = "source:" + candidate.claim_id
    if disambiguate:
        # One claim can contain more than one proposed text; bind both to the choice.
        key += ":" + canonical_hash([candidate.claim_id, candidate.text])
    return key


def _disagreement_cards(
    entry: ValueEntry, value: CanonValue, claims: tuple[Claim, ...]
) -> tuple[DecisionCard, ...]:
    claims_by_id = {claim.id: claim for claim in claims}
    if any(claim_id not in claims_by_id for claim_id, _text in value.source_claims):
        raise ValueError("서로 다른 값의 원문을 모두 찾을 수 없습니다.")
    candidates = tuple(
        replace(
            entry,
            text=text,
            claim_id=claim_id,
            file=claims_by_id[claim_id].file,
            line=claims_by_id[claim_id].line,
            source_sha256=claim_source_sha256(
                claims_by_id[claim_id], key=entry.key, kind=entry.kind
            ),
        )
        for claim_id, text in dict.fromkeys(value.source_claims)
    )
    duplicate_claim_ids = {
        candidate.claim_id for candidate in candidates
        if sum(other.claim_id == candidate.claim_id for other in candidates) > 1
    }
    evidence = tuple(
        evidence
        for candidate in candidates
        for evidence in (
            Evidence(candidate.file, 0, claims_by_id[candidate.claim_id].section, "context"),
            _claim_evidence(claims_by_id[candidate.claim_id]),
        )
    )
    cards = []
    for offset in range(0, len(candidates), 3):
        group = candidates[offset : offset + 3]
        cards.append(
            DecisionCard(
                "review",
                "서로 다른 원문 중 정본을 골라 주세요",
                "출처마다 값이 다릅니다. 각 원문과 값을 확인하고 선택해 주세요.",
                "아직 확정하지 않고 나중에 결정합니다.",
                tuple(
                    Choice(
                        _source_choice_key(c, disambiguate=c.claim_id in duplicate_claim_ids),
                        f"{c.file}:{c.line}의 {c.text} 선택",
                    )
                    for c in group
                )
                + (Choice("keep_proposed", "나중에 결정합니다."),),
                evidence,
                False,
                key=entry.key,
                default_choice="keep_proposed",
                entries=(entry,),
                alternatives=group,
            )
        )
    return tuple(cards)


def _value_cards(
    entries: Iterable[ValueEntry], claims: tuple[Claim, ...], values: tuple[CanonValue, ...]
) -> tuple[DecisionCard, ...]:
    proposed = tuple(entry for entry in entries if entry.status == "PROPOSED")
    vocabulary = tuple(entry for entry in proposed if entry.kind == "attribute_vocabulary")
    disagreements = {value.key: value for value in values if value.source_claims}
    disputed = tuple(entry for entry in proposed if entry.key in disagreements)
    ordinary = tuple(
        entry
        for entry in proposed
        if entry.kind != "attribute_vocabulary" and entry.key not in disagreements
    )
    claims_by_id = {claim.id: claim for claim in claims}
    structured = tuple(entry for entry in ordinary if _is_structured_value(entry, claims_by_id))
    ambiguous = tuple(entry for entry in ordinary if entry not in structured)
    cards = [
        _attribute_vocabulary_card(entry, claims)
        for entry in vocabulary
        if entry.key not in disagreements
    ]
    for entry in disputed:
        cards.extend(_disagreement_cards(entry, disagreements[entry.key], claims))
    cards.extend(
        _value_group(group, claims, structured=is_structured)
        for group, is_structured in ((structured, True), (ambiguous, False))
        if group
    )
    return tuple(cards)


def build_cards(
    *,
    gaps: Iterable[Gap] = (),
    questions: Iterable[Mapping[str, object]] = (),
    conflicts: Iterable[Conflict] = (),
    values: Iterable[CanonValue] = (),
    entries: Iterable[ValueEntry] = (),
    claims: Iterable[Claim] = (),
    approval: bool = False,
) -> tuple[DecisionCard, ...]:
    """Adapt existing outputs without a model, a clock or filesystem access."""
    value_items, claim_items = tuple(values), tuple(claims)
    conflict_items = tuple(conflicts)
    cards = [_conflict_card(item, value_items, claim_items) for item in conflict_items]
    if approval and conflict_items:
        cards.append(
            DecisionCard(
                "approval",
                "정본과 다른 초안을 적용할까요?",
                "확정 내용과 다른 부분이 있어 적용 전에 명시적인 확인이 필요합니다.",
                "먼저 고치는 것을 추천합니다. 잘못된 내용을 작업 폴더에 저장하는 것을 막습니다.",
                (
                    Choice("rewrite", "먼저 고칩니다. 지금 초안은 적용하지 않습니다."),
                    Choice(
                        "apply",
                        "충돌을 확인하고 적용합니다. 정본과 다른 부분을 알고 적용하는 것으로 기록합니다.",
                    ),
                    Choice("cancel", "취소합니다. 지금 초안은 적용하지 않습니다."),
                ),
                tuple(dict.fromkeys(evidence for card in cards for evidence in card.evidence)),
                True,
                default_choice="rewrite",
            )
        )
    cards.extend(_gap_cards(gaps, value_items, claim_items))
    label_cards = _label_cards(questions)
    cards.extend(card for card in label_cards if not card.items)
    cards.extend(_value_cards(entries, claim_items, value_items))
    cards.extend(card for card in label_cards if card.items)
    return tuple(cards)


def card_pages(
    cards: Iterable[DecisionCard], *, page_size: int = 5
) -> tuple[tuple[DecisionCard, ...], ...]:
    """Keep every card and its order; a page never shows more than five cards."""
    if type(page_size) is not int or not 1 <= page_size <= 5:
        raise ValueError("한 페이지에는 1~5장의 카드만 놓을 수 있습니다.")
    items = tuple(cards)
    return tuple(items[index : index + page_size] for index in range(0, len(items), page_size))


def _bindings(card: DecisionCard) -> tuple[ValueEntry, ...]:
    return card.entries + tuple(entry for item in card.items for entry in _bindings(item))


def _labels(card: DecisionCard) -> tuple[tuple[str, str | None, str | None], ...]:
    return card.labels + tuple(label for item in card.items for label in _labels(item))


def answer_card(
    card: DecisionCard, *, choice: str | None = None, words: str = "", label: str | None = None
) -> dict[str, object]:
    """Record the choice and exact words; an unanswered blocking card never proceeds."""
    if not isinstance(words, str):
        raise TypeError("내 말은 문자열이어야 합니다.")
    default_used = choice is None
    if choice is None:
        if card.blocking or card.default_choice is None:
            raise ValueError("이 카드는 사용자의 선택이 필요합니다.")
        choice = card.default_choice
    if choice not in {item.key for item in card.choices}:
        raise ValueError("카드에 없는 선택지입니다.")
    result: dict[str, object] = {
        "card_id": card.id,
        "choice": choice,
        "words": words,
        "default_used": default_used,
        "note": "기본값 사용" if default_used else "사용자 선택",
    }
    if choice == "confirm_opposite_terms":
        pressure_entries = tuple(entry for entry in _bindings(card) if _is_pressure_value(entry))
        if len(pressure_entries) != 1 or not _opposite_vocabulary(pressure_entries[0].text):
            raise ValueError("필압 반대 표현을 확인할 수 있는 값 한 개가 필요합니다.")
        result["opposite_terms"] = list(_opposite_tokens(words))
    if card.kind == "approval":
        result["acknowledge_canon_conflicts"] = choice == "apply" and not default_used
    if card.key == "project.attribute_vocabulary" and card.entries:
        entry = card.entries[0]
        if choice == "remove_items":
            selected = _attribute_tokens(words)
            if not selected or not set(selected) <= set(entry.terms):
                raise ValueError("목록에 있는 항목 중에서 뺄 항목을 쉼표로 적어 주세요.")
            result["attribute_terms"] = [
                attribute for attribute in entry.terms if attribute not in set(selected)
            ]
        elif choice == "add_items":
            additions = _attribute_tokens(words)
            if not additions:
                raise ValueError("추가할 항목을 쉼표로 적어 주세요.")
            result["attribute_terms"] = list(dict.fromkeys((*entry.terms, *additions)))
        elif choice == "use_list":
            result["attribute_terms"] = list(entry.terms)
    labels = _labels(card)
    if labels and choice in {
        "use_rule",
        "use_model",
        "choose_kind",
        "keep_unknown",
        "use_reference",
    }:
        if choice == "choose_kind" and label not in LABELS:
            raise ValueError("원문에 맞는 종류를 목록에서 골라 주세요.")
        chosen = []
        for claim_id, rule, model in labels:
            selected = (
                rule
                if choice == "use_rule"
                else model
                if choice == "use_model"
                else label
                if choice == "choose_kind"
                else (REFERENCE_ENTRY if rule == REFERENCE_ENTRY else OTHER)
                if choice == "use_reference"
                else None
            )
            if choice in {"use_rule", "use_model", "use_reference"} and selected not in LABELS:
                raise ValueError("근거가 있는 종류 제안만 고를 수 있습니다.")
            chosen.append({"claim_id": claim_id, "label": selected, "status": "PROPOSED"})
        result["labels"] = chosen
    return result


def _current_entry_changed(store: ValueStore, entry: ValueEntry) -> bool:
    return (
        entry.status != "PROPOSED"
        or next((item for item in store.entries if item.key == entry.key), None) != entry
    )


def confirm_values(
    store: ValueStore,
    card: DecisionCard,
    *,
    choice: str | None = None,
    words: str = "",
    accept_default: bool = False,
    now: str,
) -> tuple[ValueStore, dict[str, object]]:
    """Confirm only reviewed proposed entries; a default requires explicit caller acceptance."""
    if choice is None and not accept_default:
        raise ValueError("기본 선택을 사용하려면 명시적인 동의가 필요합니다.")
    bindings = _bindings(card)
    if card.kind != "review" or not bindings:
        raise ValueError("제안 값이 담긴 확인 카드만 값을 확정할 수 있습니다.")
    answer = answer_card(card, choice=choice, words=words)
    selected_choice = str(answer["choice"])
    if selected_choice.startswith("source:"):
        if len(bindings) != 1 or _current_entry_changed(store, bindings[0]):
            raise ValueError("제안 값이 바뀌었습니다. 현재 값으로 카드를 다시 만들어 주세요.")
        candidate = next(
            (
                item for item in card.alternatives
                if selected_choice in {
                    _source_choice_key(item, disambiguate=False),
                    _source_choice_key(item, disambiguate=True),
                }
            ),
            None,
        )
        if candidate is None:
            raise ValueError("카드에 없는 출처입니다.")
        saved_words = canonical_json(
            {
                "choice": selected_choice,
                "words": words,
                "default_used": bool(answer["default_used"]),
            }
        )
        return confirm_source(store, candidate, words=saved_words, now=now), answer
    vocabulary = bindings[0].kind == "attribute_vocabulary"
    if vocabulary:
        if selected_choice == "keep_proposed":
            return store, answer
        if selected_choice not in {"use_list", "remove_items", "add_items"}:
            raise ValueError("속성 목록 카드의 선택이 맞지 않습니다.")
        selected_terms = tuple(answer["attribute_terms"])
        selected_text = ", ".join(
            {"age": "나이", "sex": "성별"}.get(term, term) for term in selected_terms
        )
    elif selected_choice in {"confirm", "confirm_opposite_terms"}:
        selected_terms = ()
        selected_text = None
    else:
        return store, answer
    selected_opposite_terms = tuple(answer.get("opposite_terms", ()))

    current = {entry.key: entry for entry in store.entries}
    if any(entry.status != "PROPOSED" or current.get(entry.key) != entry for entry in bindings):
        raise ValueError("제안 값이 바뀌었습니다. 현재 값으로 카드를 다시 만들어 주세요.")
    if len({entry.key for entry in bindings}) != len(bindings):
        raise ValueError("같은 값이 두 번 묶인 카드는 확정할 수 없습니다.")
    default_used = bool(answer["default_used"])
    saved_words = canonical_json(
        {"choice": selected_choice, "words": words, "default_used": default_used}
    )
    for entry in bindings:
        store = confirm(
            store,
            entry.key,
            words=saved_words,
            now=now,
            pattern=entry.pattern,
            terms=selected_terms if vocabulary else entry.terms,
            text=selected_text,
            opposite_terms=tuple(
                dict.fromkeys(
                    (
                        *entry.opposite_terms,
                        *(selected_opposite_terms if _is_pressure_value(entry) else ()),
                    )
                )
            ),
        )
    return store, answer


__all__ = [
    "Choice",
    "DecisionCard",
    "Evidence",
    "answer_card",
    "build_cards",
    "card_pages",
    "confirm_values",
]
