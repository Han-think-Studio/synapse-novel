"""Safe, bounded formatting for runtime diagnostics."""

from __future__ import annotations

import re

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_NAMES = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|secret|token)"
_SECRET_ASSIGNMENT = r"""["']?\b""" + _SECRET_NAMES + r"""\b["']?\s*[:=]\s*"""
_QUOTED_SECRET_RE = re.compile(
    r"(?i)(" + _SECRET_ASSIGNMENT + r""")(["'])((?:\\.|(?!\2).)*)\2"""
)
_SECRET_RE = re.compile(
    r"(?i)(" + _SECRET_ASSIGNMENT + r""")([^\s,;}\]\x22\x27]+)(["']?)"""
)


def redact_diagnostic_detail(value: object, *, limit: int = 400) -> str:
    """Bound detail and redact common credential-shaped values."""

    text = str(value)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _QUOTED_SECRET_RE.sub(r"\1\2<redacted>\2", text)
    text = _SECRET_RE.sub(r"\1<redacted>\3", text)
    return text[:limit]


__all__ = ["redact_diagnostic_detail"]
