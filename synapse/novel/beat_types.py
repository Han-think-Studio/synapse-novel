"""Shared pure types and limits for novel beats."""

from __future__ import annotations

from dataclasses import dataclass

SCENE_MAX_TOKENS = 2500

SCENE_BEAT_MAX_COUNT = 12


class NovelServiceError(ValueError):
    def __init__(self, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class SceneBeat:
    start_state: str
    end_state: str
    confirmed: bool
    # Who acts in this beat, by the scene's exact names; none means the plan did not say.
    actors: tuple[str, ...] = ()
