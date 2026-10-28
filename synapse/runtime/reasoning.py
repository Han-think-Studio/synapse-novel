"""Provider/model-family controls for bounded local reasoning requests."""

from __future__ import annotations

from typing import Any


def reasoning_suppression(
    provider: str, model: str
) -> tuple[tuple[dict[str, Any], ...], str, dict[str, Any]]:
    """Return verified family controls that keep structured output available."""

    if provider != "lmstudio":
        return (), "", {}
    lowered = model.lower()
    params: dict[str, Any] = {"reasoning_effort": "none"}
    if "qwen" in lowered:
        return (), "/no_think\n", {
            **params,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    if "nemotron" in lowered:
        return ({"role": "system", "content": "detailed thinking off"},), "", params
    if "glm" in lowered:
        return (), "/nothink\n", {
            **params,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    return (), "", params


__all__ = ["reasoning_suppression"]
