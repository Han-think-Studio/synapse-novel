"""Shared construction of the original novel writer's beat request."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from .beat_types import SceneBeat
from .quality_repeat import last_sentences
from .units import PROSE_MAX_CHARS, PROSE_MIN_CHARS

if TYPE_CHECKING:
    from synapse.core.canon_check import CanonValue


def last_paragraph(text: str) -> str:
    paragraphs = [part.strip() for part in re.split(r"\r?\n\s*\r?\n", text) if part.strip()]
    return paragraphs[-1] if paragraphs else ""


def _state_field(text: str, labels: str) -> str:
    match = re.search(rf"^\s*(?:{labels})\s*[:：=]\s*(.+?)\s*$", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def story_state(
    beats: Sequence[SceneBeat],
    part_number: int,
    *,
    values: Iterable[CanonValue],
    rows: Sequence[str],
    subjects: Sequence[str],
) -> dict[str, Any]:
    from synapse.core.canon_context import state_key_belongs_to_row

    values = tuple(values)
    beat = beats[part_number - 1]
    state_text = beat.start_state + "\n" + beat.end_state
    canon_state_values = {
        value.key: value.text
        for value in values
        if any(state_key_belongs_to_row(value.key, row) for row in rows)
    }
    pencil_value = next(
        (
            value.text
            for value in values
            if re.search(
                r"연필|볼펜|필기구|pencil|pen", value.key + " " + value.text, re.IGNORECASE
            )
        ),
        "",
    )
    return {
        "place": _state_field(state_text, r"장소|위치|place"),
        "time": _state_field(state_text, r"시간|시점|time"),
        "characters": list(beat.actors)
        or [name for name in subjects if name in state_text]
        or list(subjects),
        "past_events": [item.end_state for item in beats[: part_number - 1]],
        "pencil_state": _state_field(state_text, r"연필(?: 상태)?|필기구|pencil state")
        or pencil_value,
        "canon_state_values": canon_state_values,
    }


def beat_messages(
    *,
    model: str,
    request: str,
    beats: Sequence[SceneBeat],
    part_number: int,
    subjects: Sequence[str],
    rows: Sequence[str],
    values: Iterable[CanonValue],
    canon_block: str,
    plan_index: int = 1,
    plan_total: int = 1,
    plan_mode: str = "",
    sequence_context: str = "",
    previous_content: str = "",
    length_instruction: str | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    if length_instruction is None:
        length_instruction = (
            f"본문은 공백과 문장부호를 포함해 {PROSE_MIN_CHARS}~{PROSE_MAX_CHARS}자여야 하며, "
            "목표는 2000~3000자입니다."
        )
    beat = beats[part_number - 1]
    previous = last_sentences(last_paragraph(previous_content)) if previous_content else ""
    prior_context = (
        "\n\n직전 조각의 마지막 문단만(연속성 참고용): 아래 문장은 이미 쓰였습니다. "
        f"한 문장도 다시 쓰지 말고 바로 다음 순간부터 이어 쓰세요.\n{previous}"
        if previous
        else ""
    )
    sequence_context = (
        f"\n\n전체 계획에서 이어지는 정보:\n{sequence_context}" if sequence_context else ""
    )
    mode_context = (
        "사용자가 선택한 방향: 기존 원고 이어쓰기."
        if plan_mode == "continue_existing_draft"
        else "사용자가 선택한 방향: 새 구성."
        if plan_mode == "new_structure"
        else ""
    )
    state = json.dumps(
        story_state(beats, part_number, values=values, rows=rows, subjects=subjects),
        ensure_ascii=False,
        indent=2,
    )
    system = "Write concise, natural Korean prose. Return only the requested JSON."
    if "gpt-oss" in model.lower():
        system = "Reasoning: low\n" + system
    user = (
        "사용자가 확정한 전체 장면·박자 계획 중 한 박자만 자연스러운 한국어 소설로 작성합니다. "
        f"이번 장면은 계획 {plan_index}/{plan_total}입니다. "
        f"이번 요청은 {part_number}/{len(beats)}번째 박자입니다. {mode_context} "
        f"{length_instruction} "
        "이 박자의 핵심 행동이나 관계 변화 하나에 집중하세요. "
        "다른 박자의 사건은 쓰지 마세요. "
        "시작 상태에서 끝 상태로 변화가 보이게 쓰세요. "
        "정본 인물 이름은 원문 그대로 쓰고 CANON 블록과 모순되지 않게 작성하세요. "
        "행동과 대사를 자연스러운 문장과 문단으로 마무리하고, 같은 행동이나 끝 상태를 반복하지 마세요. "
        "계획의 시작·끝 상태를 재서술하지 말고 실제 작은 행동이나 대사로 변화가 일어나게 쓰세요. "
        "필기 도구도 CANON에 확인된 명칭 그대로 쓰고, 반환 전에 한글 인물 이름과 오타를 검토하세요. "
        f"이번 장면의 정확한 인물 이름: {', '.join(subjects)}. "
        + (
            f"이 박자에서 행동하는 인물(계획에서 확정): {', '.join(beat.actors)}. "
            "이 인물들이 모두 이 박자에 나와 행동하게 쓰세요. "
            if beat.actors
            else ""
        )
        + "서술문에 이 전체 이름을 한 번 이상 그대로 쓰고(이후에는 성을 뗀 이름도 자연스럽게 "
        "쓸 수 있습니다), 대사 속 호칭은 자연스럽게 쓰세요. "
        "summary와 content를 JSON으로 반환하세요.\n\n"
        f"장면 요청:\n{request}"
        f"\n\n확정한 박자 시작 상태:\n{beat.start_state}"
        f"\n\n확정한 박자 끝 상태:\n{beat.end_state}"
        f"\n\n파이썬이 유지하는 장면 상태:\n{state}"
        f"{sequence_context}{prior_context}" + (f"\n\n{canon_block}" if canon_block else "")
    )
    return ({"role": "system", "content": system}, {"role": "user", "content": user})
