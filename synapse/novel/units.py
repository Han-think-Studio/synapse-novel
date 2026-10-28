"""Ordered novel units and the common single-invocation boundary."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .beat_types import SCENE_MAX_TOKENS, NovelServiceError

SOURCES = {"python", "model_proposed", "user", "operator"}
LEVELS = {"chapter", "scene", "beat", "insertion"}
PROSE_MIN_CHARS = 200
PROSE_MAX_CHARS = 8000

PLAN_OPERATIONS = {
    "chapter_slot": "chapter",
    "scene_list": "chapter",
    "beat_list": "scene",
    "anchors": "chapter",
}


@dataclass(frozen=True, slots=True)
class Unit:
    unit_id: str
    level: str
    parent: str
    order: int
    source: str
    start_state: str
    end_state: str
    status: str
    inputs_hash: str

    def __post_init__(self) -> None:
        if not self.unit_id or self.level not in LEVELS or self.source not in SOURCES:
            raise NovelServiceError("작업 단위의 번호·종류·출처를 확인해 주세요.")
        if type(self.order) is not int or self.order < 1 or not self.parent:
            raise NovelServiceError("작업 단위의 부모와 순서가 필요합니다.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.inputs_hash):
            raise NovelServiceError("작업 단위의 입력 해시가 올바르지 않습니다.")
        if not all(
            isinstance(v, str) and v.strip()
            for v in (self.start_state, self.end_state, self.status)
        ):
            raise NovelServiceError("작업 단위에 시작·끝 상태와 현재 상태가 필요합니다.")


def assert_single_unit(
    units: Sequence[Unit],
    *,
    operation: str,
    max_tokens: int,
    expected_max_chars: int = PROSE_MAX_CHARS,
    attempt: int = 1,
    attempt_limit: int = 3,
    anchor_count: int = 0,
) -> dict[str, Any]:
    """A plan may describe one parent's children; it cannot target two parents."""
    if len(units) != 1:
        raise NovelServiceError("한 요청에는 장·장면·삽입·박자·칸을 하나만 넣을 수 있습니다.")
    unit = units[0]
    if (
        type(attempt_limit) is not int
        or attempt_limit < 3
        or (attempt_limit > 3 and operation != "prose")
        or type(attempt) is not int
        or not 1 <= attempt <= attempt_limit
    ):
        raise NovelServiceError(
            "허용된 시도 범위를 벗어났습니다. 추가 승인과 종료 기록을 확인해 주세요.", status=409
        )
    if type(max_tokens) is not int or max_tokens < 1:
        raise NovelServiceError("요청의 출력 토큰 한도가 올바르지 않습니다.")
    if operation == "prose":
        if unit.level not in {"beat", "insertion"} or max_tokens > SCENE_MAX_TOKENS:
            raise NovelServiceError("산문은 박자나 삽입 하나씩, 2,500토큰 이내로 요청해 주세요.")
        if (
            type(expected_max_chars) is not int
            or not PROSE_MIN_CHARS <= expected_max_chars <= PROSE_MAX_CHARS
        ):
            raise NovelServiceError(
                f"산문 조각 목표는 {PROSE_MIN_CHARS}~{PROSE_MAX_CHARS}자 범위여야 합니다."
            )
    elif operation in PLAN_OPERATIONS:
        if unit.level != PLAN_OPERATIONS[operation] or max_tokens > 600:
            raise NovelServiceError("계획은 해당 부모의 칸 하나씩, 600토큰 이내로 제안해 주세요.")
        if type(expected_max_chars) is not int or expected_max_chars < 1:
            raise NovelServiceError("계획 응답의 글자 한도를 지정해 주세요.")
    else:
        raise NovelServiceError("전체 계획·전체 장을 한 번에 모델에 요청할 수 없습니다.")
    if type(anchor_count) is not int or not 0 <= anchor_count <= 3:
        raise NovelServiceError("앵커는 한 장 안에서 최대 세 개까지 제안할 수 있습니다.")
    return {
        **asdict(unit),
        "operation": operation,
        "expected_max_chars": expected_max_chars,
        "max_tokens": max_tokens,
        "attempt": attempt,
    }


def require_previous_complete(
    unit: Unit, siblings: Sequence[Unit], *, planning: bool = False
) -> None:
    same = [s for s in siblings if s.parent == unit.parent]
    if (
        len({s.order for s in same}) != len(same)
        or sum(s.unit_id == unit.unit_id for s in same) != 1
    ):
        raise NovelServiceError(
            "작업 단위 순서가 겹치거나 현재 단위를 찾을 수 없습니다.", status=409
        )
    accepted = {"CONFIRMED"} if planning else {"LOCKED", "APPLIED"}
    if any(s.status not in accepted for s in same if s.order < unit.order):
        raise NovelServiceError("앞 단위를 확정하고 끝낸 뒤 다음 단위를 시작해 주세요.", status=409)


def confirmation_source(value: object = "user") -> str:
    if not isinstance(value, str) or value not in {"user", "operator"}:
        raise NovelServiceError("확정한 사람이 사용자 또는 operator인지 기록해 주세요.")
    return str(value)
