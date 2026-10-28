"""Shared deterministic beat rules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .beat_types import SCENE_BEAT_MAX_COUNT, NovelServiceError, SceneBeat

if TYPE_CHECKING:
    from synapse.core.canon_check import CanonValue
    from synapse.core.canon_context import Review


def beat_actors(
    raw: Any, subjects: Sequence[str], number: int, *, status: int = 422
) -> tuple[str, ...]:
    """The people one beat names as acting: only the scene's own exact names, each once."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(name, str) for name in raw):
        raise NovelServiceError(
            f"{number}번째 박자의 인물은 이름 목록이어야 합니다.", status=status
        )
    names = tuple(dict.fromkeys(name.strip() for name in raw if name.strip()))
    unknown = [name for name in names if name not in subjects]
    if unknown:
        raise NovelServiceError(
            f"{number}번째 박자의 인물은 이 장면의 인물 중에서만 고를 수 있습니다: "
            + ", ".join(unknown),
            status=status,
        )
    return names


def beat_record(beat: SceneBeat) -> dict[str, Any]:
    """A beat as stored: actors only when the plan names them, so older plan files read and hash the same."""
    record: dict[str, Any] = {
        "start_state": beat.start_state,
        "end_state": beat.end_state,
        "confirmed": beat.confirmed,
    }
    if beat.actors:
        record["actors"] = list(beat.actors)
    return record


def part_review(part: Mapping[str, Any], values: Iterable[CanonValue], beat: SceneBeat) -> Review:
    """The canon review of one beat's text; a person the beat names who never appears keeps it unchecked."""
    from synapse.core.canon_context import review_candidate

    return review_candidate(
        part["summary"] + "\n" + part["content"],
        values,
        coverage_text=part["content"],
        required_names=beat.actors,
    )


def parse_beats(payload: Mapping[str, Any], subjects: Sequence[str] = ()) -> tuple[SceneBeat, ...]:
    raw_beats = payload.get("beats")
    if not isinstance(raw_beats, list) or not 1 <= len(raw_beats) <= SCENE_BEAT_MAX_COUNT:
        raise NovelServiceError(f"확정한 장면 박자를 1~{SCENE_BEAT_MAX_COUNT}개 입력해 주세요.")
    beats = []
    for index, item in enumerate(raw_beats, start=1):
        if not isinstance(item, Mapping):
            raise NovelServiceError(f"{index}번째 장면 박자 형식이 맞지 않습니다.")
        start_state = item.get("start_state")
        end_state = item.get("end_state")
        if not isinstance(start_state, str) or not start_state.strip():
            raise NovelServiceError(f"{index}번째 박자의 시작 상태를 적어 주세요.")
        if not isinstance(end_state, str) or not end_state.strip():
            raise NovelServiceError(f"{index}번째 박자의 끝 상태를 적어 주세요.")
        if item.get("confirmed") is not True:
            raise NovelServiceError(f"{index}번째 박자를 확인하고 확정해 주세요.")
        actors = beat_actors(item.get("actors"), subjects, index, status=409)
        beats.append(SceneBeat(start_state.strip(), end_state.strip(), True, actors))
    return tuple(beats)
