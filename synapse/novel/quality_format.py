"""Deterministic prose formatting checks; only forced paragraph line breaks normalize."""

from __future__ import annotations

import hashlib
import re
from typing import Any


def normalize_paragraph_linebreaks(text: str) -> tuple[str, dict[str, Any]]:
    canonical = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n[ \t]*\n+", canonical)
    joined = 0
    normalized_parts = []
    for paragraph in paragraphs:
        lines = paragraph.split("\n")
        joined += max(0, len(lines) - 1)
        normalized_parts.append(" ".join(lines))
    normalized = "\n\n".join(normalized_parts)
    return normalized, {
        "changed": normalized != text,
        "forced_linebreaks_joined": joined,
        "original_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "normalized_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def inspect_format(text: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line != line.strip():
            issues.append({"kind": "leading_trailing_whitespace", "line": number})
    for left, right in (("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』")):
        if text.count(left) != text.count(right):
            issues.append({"kind": "mismatched_quotes", "pair": left + right})
    if text.count('"') % 2:
        issues.append({"kind": "mismatched_quotes", "pair": '""'})
    for match in re.finditer(r"([.!?,;:。！？、])\1+", text):
        issues.append({"kind": "duplicate_punctuation", "evidence": match.group(0)[:8]})
    return {"issues": issues, "issue_count": len(issues)}


def review_format(text: str) -> dict[str, Any]:
    normalized, normalization = normalize_paragraph_linebreaks(text)
    return {"content": normalized, "normalization": normalization, **inspect_format(normalized)}
