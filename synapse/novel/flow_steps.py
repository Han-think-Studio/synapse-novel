"""The seven steps of writing a novel, named once (DEC-0249).

The novel edition's commands (``edition_flow``: ``seed``, ``plan``, ``plan-confirm``, ``beat``, ``check``, ``lock``,
``assemble``) and the writer's screen use the same ids and names, so a person sees one flow whichever way they work.
The writer's own tools (seed questions and cards, chapter slots, draft insertion, version pin) sit inside these steps.

``current_step`` reads saved records to say where a project stands. It is a reading, not a gate: every route keeps
its own checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STEPS: tuple[tuple[str, str], ...] = (
    ("seed", "씨앗"),
    ("plan", "계획"),
    ("plan-confirm", "계획 확정"),
    ("beat", "비트 쓰기"),
    ("check", "검사"),
    ("lock", "잠금"),
    ("assemble", "조립"),
)
STEP_IDS = tuple(step for step, _name in STEPS)


def current_step(status: Mapping[str, Any], *, seed_present: bool | None = None) -> str:
    """The step a project is at, read from the writer's status (saved plans and the scenes in progress).

    ``seed_present`` is whether a new-structure seed is saved (``None`` when the project continues a manuscript).
    """

    plans = [
        plan
        for plan in status.get("scene_plans") or []
        if isinstance(plan, Mapping) and plan.get("plan_status") not in {None, "INCOMPLETE"}
    ]
    if not plans:
        if seed_present is False and not status.get("confirmed_values"):
            return "seed"
        return "plan"
    latest = plans[-1]
    if latest.get("plan_status") == "STALE":
        return "plan"
    if latest.get("plan_status") != "CONFIRMED":
        return "plan-confirm"
    scene_states = [scene.get("status") for scene in latest.get("scenes") or [] if isinstance(scene, Mapping)]
    if scene_states and all(state in {"LOCKED", "APPLIED"} for state in scene_states):
        return "assemble"
    if status.get("resumable_scenes"):
        return "check"
    return "beat"


def current_edition_step(flow: Mapping[str, Any] | None, *, seed_present: bool, confirmed_values: int) -> str:
    """The step a novel edition project is at, read from ``edition_flow.status`` (DEC-0250's light tab)."""

    if not seed_present or not confirmed_values:
        return "seed"
    plan = (flow or {}).get("plan")
    if not isinstance(plan, Mapping) or not plan.get("current"):
        return "plan"
    if not plan.get("confirmed"):
        return "plan-confirm"
    for beat in (flow or {}).get("beats") or []:
        if not isinstance(beat, Mapping) or beat.get("locked"):
            continue
        if not beat.get("requested"):
            return "beat"
        if not beat.get("checked") or beat.get("lockable") is False or beat.get("stale"):
            return "check"
        return "lock"
    return "assemble"


def steps_view(status: Mapping[str, Any], *, seed_present: bool | None = None) -> dict[str, Any]:
    current = current_step(status, seed_present=seed_present)
    return {
        "steps": [{"id": step, "name": name, "number": index + 1} for index, (step, name) in enumerate(STEPS)],
        "current": current,
        "reading_not_gate": True,
    }


__all__ = ["STEPS", "STEP_IDS", "current_edition_step", "current_step", "steps_view"]
