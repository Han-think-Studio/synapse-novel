"""Seed values, deterministic question cards, and data-driven structure templates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synapse.core.canon_cards import DecisionCard
    from synapse.core.canon_claims import Claim
    from synapse.core.canon_values import ValueStore

_TEMPLATE_FILE = Path(__file__).with_name("structure_templates.json")


@dataclass(frozen=True, slots=True)
class SeedCharacter:
    name: str
    description: str
    age: str | None = None
    sex: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or "\n" in self.name:
            raise ValueError("인물 이름은 한 줄로 적어 주세요.")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or "\n" in self.description
            or "\r" in self.description
        ):
            raise ValueError("인물 설명은 한 줄로 적어 주세요.")
        for field, label in (("age", "나이"), ("sex", "성별")):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or "\n" in value
                or "\r" in value
            ):
                raise ValueError(f"인물 {label}은 한 줄 문자열이어야 합니다.")


@dataclass(frozen=True, slots=True)
class SeedRule:
    kind: str
    text: str

    def __post_init__(self) -> None:
        if (
            self.kind not in {"prohibition", "requirement"}
            or not isinstance(self.text, str)
            or not self.text.strip()
            or "\n" in self.text
        ):
            raise ValueError("규칙은 한 줄 prohibition/requirement로 적어 주세요.")


@dataclass(frozen=True, slots=True)
class ScratchSeed:
    premise: str
    characters: tuple[SeedCharacter, ...]
    title: str = ""
    genre: str = ""
    tone: str = ""
    goal: str = ""
    opposition: str = ""
    timeframe: str = ""
    ending_direction: str = ""
    rules: tuple[SeedRule, ...] = ()
    chapter_count: int = 4
    structure: str = "four_part"

    def __post_init__(self) -> None:
        for name in (
            "premise",
            "title",
            "genre",
            "tone",
            "goal",
            "opposition",
            "timeframe",
            "ending_direction",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or "\n" in value or "\r" in value:
                raise ValueError(f"씨앗의 {name}은 한 줄 문자열이어야 합니다.")
        if not self.premise.strip():
            raise ValueError("한 줄 줄거리는 꼭 필요합니다.")
        if not isinstance(self.characters, tuple) or not 1 <= len(self.characters) <= 30:
            raise ValueError("인물은 1~30명이어야 합니다.")
        if any(not isinstance(person, SeedCharacter) for person in self.characters):
            raise ValueError("인물 입력 형식이 맞지 않습니다.")
        names = [person.name.strip() for person in self.characters]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("인물 이름은 비어 있지 않고 서로 달라야 합니다.")
        if any(
            "\n" in person.description or "\r" in person.description for person in self.characters
        ):
            raise ValueError("인물 설명은 한 줄로 적어 주세요.")
        if not isinstance(self.rules, tuple) or any(
            not isinstance(rule, SeedRule) for rule in self.rules
        ):
            raise ValueError("규칙 입력 형식이 맞지 않습니다.")
        if any(
            rule.kind not in {"prohibition", "requirement"} or not rule.text.strip()
            for rule in self.rules
        ):
            raise ValueError("규칙은 prohibition/requirement와 내용이 필요합니다.")
        if type(self.chapter_count) is not int or not 3 <= self.chapter_count <= 30:
            raise ValueError("장 수는 3~30이어야 합니다.")
        if self.structure not in {template["key"] for template in load_structure_templates()}:
            raise ValueError("목록에서 구조 틀을 골라 주세요.")


def scratch_seed_from_payload(payload: Mapping[str, Any]) -> ScratchSeed:
    characters = payload.get("characters")
    rules = payload.get("rules", [])
    if not isinstance(characters, list) or not isinstance(rules, list):
        raise TypeError("인물과 규칙은 목록으로 적어 주세요.")
    try:
        return ScratchSeed(
            title=payload.get("title", ""),
            premise=payload.get("premise", ""),
            genre=payload.get("genre", ""),
            tone=payload.get("tone", ""),
            goal=payload.get("goal", ""),
            opposition=payload.get("opposition", ""),
            timeframe=payload.get("timeframe", ""),
            ending_direction=payload.get("ending_direction", ""),
            characters=tuple(SeedCharacter(**item) for item in characters),
            rules=tuple(SeedRule(**item) for item in rules),
            chapter_count=payload.get("chapter_count", 4),
            structure=payload.get("structure", "four_part"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def load_structure_templates() -> tuple[dict[str, Any], ...]:
    try:
        document = json.loads(_TEMPLATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("구조 틀 자료를 읽을 수 없습니다.") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != "synapse-novel-structure-templates.v1"
    ):
        raise ValueError("구조 틀 자료의 형식이 맞지 않습니다.")
    items = document.get("templates")
    if not isinstance(items, list) or len(items) < 2:
        raise ValueError("구조 틀이 두 종류 이상 필요합니다.")
    keys = [item.get("key") for item in items if isinstance(item, dict)]
    if len(keys) != len(items) or len(set(keys)) != len(keys):
        raise ValueError("구조 틀의 key가 비었거나 겹칩니다.")
    for item in items:
        parts = item.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("구조 틀에는 부별 칸 정의가 필요합니다.")
        if sum(part.get("weight", 0) for part in parts) != 100:
            raise ValueError("구조 틀의 부별 비율 합은 100이어야 합니다.")
    return tuple(items)


def structure_template(key: str) -> dict[str, Any]:
    for template in load_structure_templates():
        if template["key"] == key:
            return template
    raise ValueError("목록에서 구조 틀을 골라 주세요.")


def allocate_chapters(chapter_count: int, template_key: str) -> tuple[dict[str, Any], ...]:
    """Allocate all chapters by largest remainder with template order as tie-break."""
    if type(chapter_count) is not int or not 3 <= chapter_count <= 30:
        raise ValueError("장 수는 3~30이어야 합니다.")
    template = structure_template(template_key)
    parts = template["parts"]
    exact = [chapter_count * part["weight"] / 100 for part in parts]
    counts = [int(value) for value in exact]
    remaining = chapter_count - sum(counts)
    ranking = sorted(range(len(parts)), key=lambda i: (-(exact[i] - counts[i]), i))
    for index in ranking[:remaining]:
        counts[index] += 1
    result = []
    order = 0
    for part, count in zip(parts, counts, strict=True):
        for _ in range(count):
            order += 1
            result.append(
                {
                    "chapter_id": f"chapter-{order:02d}",
                    "order": order,
                    "part": part["key"],
                    "part_label": part["label"],
                    "definition": part["definition"],
                }
            )
    if order != chapter_count:
        raise ValueError("장 배분 합계가 맞지 않습니다.")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class _SeedValueSpec:
    line: int
    key: str | None = None
    kind: str | None = None
    attribute: str | None = None
    words: str | None = None


def _seed_lines(seed: ScratchSeed) -> tuple[str, tuple[_SeedValueSpec, ...]]:
    from synapse.core.canon_proposal import character_key

    lines = ["# 씨앗", "", "## 작품"]
    specs: list[_SeedValueSpec] = []

    def add(key: str, kind: str, text: str) -> None:
        lines.append(f"- {text}")
        specs.append(_SeedValueSpec(line=len(lines), key=key, kind=kind))

    for key, label, value in (
        ("work.title", "제목", seed.title),
        ("work.premise", "한 줄 줄거리", seed.premise),
        ("work.genre", "장르", seed.genre),
        ("work.tone", "톤", seed.tone),
        ("work.goal", "주인공 목표", seed.goal),
        ("work.opposition", "대립 요소", seed.opposition),
        ("work.timeframe", "시간 구조", seed.timeframe),
        ("work.ending_direction", "끝의 변화", seed.ending_direction),
    ):
        if value.strip():
            add(key, "work_fact", f"{label}: {value}")

    lines.extend(("", "## 인물"))
    for person in seed.characters:
        has_attributes = person.age is not None or person.sex is not None
        heading = f"### {person.name} — 인물" if has_attributes else f"### {person.name}"
        lines.extend(("", heading))
        if person.description.strip():
            add(character_key(person.name, "description"), "character_fact", person.description)
        for attribute, label, value in (
            ("age", "나이", person.age),
            ("sex", "성별", person.sex),
        ):
            if value is None:
                continue
            lines.append(f"- {label}: {value}")
            specs.append(_SeedValueSpec(line=len(lines), attribute=attribute, words=value))

    for rule_kind, heading in (("prohibition", "금지 규칙"), ("requirement", "지킬 규칙")):
        matching = [rule for rule in seed.rules if rule.kind == rule_kind]
        if not matching:
            continue
        lines.extend(("", f"## {heading}"))
        for index, rule in enumerate(matching, start=1):
            add(f"rule.{rule_kind}.{index:04d}", rule_kind, rule.text)
    return "\n".join(lines) + "\n", tuple(specs)


def seed_values(
    seed: ScratchSeed, project_id: str, *, archive_sha256: str | None = None
) -> tuple[ValueStore, tuple[Claim, ...], str]:
    """Turn exact seed source lines into PROPOSED entries for the existing card flow."""
    from synapse.core.canon_claims import cut_text
    from synapse.core.canon_proposal import Request, add_to_store, draft_values, entry_for
    from synapse.core.canon_values import new_store, propose

    if (
        not isinstance(seed, ScratchSeed)
        or not isinstance(project_id, str)
        or not project_id.strip()
    ):
        raise ValueError("씨앗과 project_id가 필요합니다.")
    source, specs = _seed_lines(seed)
    claims = cut_text("scratch-seed.md", source)
    by_line = {claim.line: claim for claim in claims}
    archive_hash = archive_sha256 or hashlib.sha256(source.encode("utf-8")).hexdigest()
    if len(archive_hash) != 64 or any(char not in "0123456789abcdef" for char in archive_hash):
        raise ValueError("씨앗 자료의 SHA256 형식이 맞지 않습니다.")
    store = new_store(project_id, archive_hash)
    for spec in specs:
        claim = by_line.get(spec.line)
        if claim is None:
            raise ValueError("씨앗의 값 출처 줄을 찾을 수 없습니다.")
        if spec.attribute is not None:
            if spec.words is None:
                raise ValueError("씨앗의 인물 속성 값이 비어 있습니다.")
            draft = draft_values(
                claims,
                (Request(claim.id, attribute=spec.attribute, words=spec.words),),
            )
            if draft.refused or len(draft.entries) != 1:
                raise ValueError("씨앗의 인물 속성을 정본 제안으로 만들 수 없습니다.")
            store = add_to_store(store, draft)
            continue
        if spec.key is None or spec.kind is None:
            raise ValueError("씨앗의 값 제안 정보가 없습니다.")
        entry = entry_for(
            claim,
            key=spec.key,
            kind=spec.kind,
            text=claim.text,
            label=spec.kind,
        )
        store = propose(store, entry)
    return store, claims, source


def seed_value_cards(store: ValueStore, claims: tuple[Claim, ...]) -> tuple[DecisionCard, ...]:
    from synapse.core.canon_cards import build_cards

    return build_cards(entries=store.entries, claims=claims)


def missing_seed_questions(seed: ScratchSeed) -> tuple[DecisionCard, ...]:
    """Ask only deterministic questions for required template values absent from the seed."""
    from synapse.core.canon_cards import Choice, DecisionCard, Evidence

    template = structure_template(seed.structure)
    present = {entry.key for entry in seed_values(seed, "question-check")[0].entries}
    questions = {
        "work.goal": "주인공은 무엇을 이루고 싶나요?",
        "work.opposition": "주인공의 목표를 가로막는 대상이나 조건은 무엇인가요?",
        "work.timeframe": "이야기는 어떤 시간 범위로 진행되나요?",
        "work.ending_direction": "마지막에 무엇이 달라지길 바라나요?",
        "rule.prohibition": "이야기에서 반드시 피해야 할 것은 무엇인가요?",
        "work.genre": "어떤 장르로 쓸까요?",
        "work.tone": "문장 분위기를 어떻게 정할까요?",
    }
    cards = []
    for key in template["required_values"]:
        present_for_key = key in present or (
            key == "rule.prohibition"
            and any(value_key.startswith("rule.prohibition.") for value_key in present)
        )
        if present_for_key:
            continue
        question = questions[key]
        cards.append(
            DecisionCard(
                "missing_key",
                question,
                "이 칸이 비어 있어 계획 제안에 필요한 기준을 정하지 못했습니다.",
                None,
                (
                    Choice("fill", "값을 적습니다. 적은 내용은 제안 값으로 검토합니다."),
                    Choice("keep_unknown", "미정으로 두고 해당 계획을 보류합니다."),
                ),
                (Evidence("scratch-seed.md", 0, "필수 정본 칸이 비어 있음"),),
                True,
                key=key,
            )
        )
    return tuple(cards)


def confirm_seed_card(
    store: ValueStore,
    card: DecisionCard,
    *,
    choice: str,
    actor: str,
    words: str,
    now: str,
) -> tuple[ValueStore, dict[str, object]]:
    if actor not in {"user", "operator"}:
        raise ValueError("확정 출처는 user 또는 operator여야 합니다.")
    from synapse.core.canon_cards import confirm_values

    updated, answer = confirm_values(
        store,
        card,
        choice=choice,
        words=f"{actor}: {words}",
        now=now,
    )
    return updated, {**answer, "source": actor}


__all__ = [
    "ScratchSeed",
    "SeedCharacter",
    "SeedRule",
    "allocate_chapters",
    "confirm_seed_card",
    "load_structure_templates",
    "missing_seed_questions",
    "scratch_seed_from_payload",
    "seed_value_cards",
    "seed_values",
    "structure_template",
]
