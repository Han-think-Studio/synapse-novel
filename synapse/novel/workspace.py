"""Synapse Novel's workspace in the shell (DEC-0242, DEC-0248, DEC-0250).

The shell opens one novel project at a time and serves its screen at ``PAGE`` and its API under ``API_PREFIX``. Where
the full writer exists (the original repository), that is the screen (``synapse.novel.server.build_app``); a release
that ships only the novel edition's flow gets the light tab instead (``synapse.novel.edition_app``). Either way the
project's confirmations join the shell's, so every code prints in the shell's one terminal.
"""

from __future__ import annotations

import importlib.util
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any

TITLE = "소설"
PAGE = "/novel/"
API_PREFIX = "/api/novel/"
CONFIRM_ACTIONS: dict = {}  # a project's confirmation comes with ``open_project``

# The full writer is not part of the public edition (DEC-0222); where it is missing, the light tab stands in.
FULL_WRITER = importlib.util.find_spec("synapse.novel.server") is not None

_HERE = Path(__file__).parent
_FILES = {"": "index.html", "index.html": "index.html", "app.js": "app.js", "style.css": "style.css"}


def open_project(project_path: str | Path, *, announce: Callable[[str], bool]) -> Any:
    if FULL_WRITER:
        from synapse.novel.server import build_app

        return build_app(project_path, announce=announce)
    from synapse.novel.edition_app import build_edition_app

    return build_edition_app(project_path, announce=announce)


def page_file(name: str) -> tuple[bytes, str] | None:
    """One file of the screen as the shell serves it under ``PAGE``; ``None`` when there is no such file."""

    filename = _FILES.get(name)
    if filename is None:
        return None
    body = (_HERE / ("static" if FULL_WRITER else "edition_static") / filename).read_bytes()
    if FULL_WRITER and filename == "index.html":
        # In the shell the canon cards are the shell's own panel.
        body = body.replace(b"http://127.0.0.1:8766/#canon-gate", b"/#canon-gate")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    charset = "; charset=utf-8" if media_type in {"text/html", "text/css", "application/javascript", "text/javascript"} else ""
    return body, media_type + charset


__all__ = ["API_PREFIX", "CONFIRM_ACTIONS", "FULL_WRITER", "PAGE", "TITLE", "open_project", "page_file"]
