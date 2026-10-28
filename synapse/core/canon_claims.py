"""Cut a person's file into claims, in plain Python, so nothing about the cutting can be invented or left out.

The first step of the canon value gate. A file a person wrote -- a bible, a timeline, a list of rules -- is
many small statements: a bullet, a numbered step, a checkbox, one row of a table, a paragraph. Each of them is
a *claim*, and a claim keeps exactly where it came from: the file, the line, the headings above it, and its
words as they stand in the file. No model is involved here. That is the point: whatever later reads, labels or
checks a claim starts from something that is, word for word, in the file, and every line of the file that says
something is in exactly one claim. A model that is asked to *find* the facts can skip one or take the cell
next door; a model that is only asked to *label* claims that are already cut cannot.

What this does not do: decide what a claim means, or whether it is true. A label is a later, separate,
proposal-only step, and only the person's confirmation makes anything canon.

Cutting rules, in the order a line is read:

* a heading (``#``..``######``) is not a claim; it names the section of everything under it
* a fenced block is one claim of kind ``code`` (its body, exactly)
* a table is a header line, a separator line, and rows. The header and separator are structure; every row is
  a ``table_row`` claim whose ``fields`` pair each header with its cell. A row with a different number of
  cells than the header is ``table_row_ragged`` and carries no fields: it is kept, not guessed at
* ``-``/``*``/``+`` items are ``bullet`` (``checkbox`` when they start with ``[ ]``/``[x]``); ``1.`` items are
  ``numbered`` and keep their number in ``marker``; indentation gives ``depth``
* consecutive other lines are one ``paragraph``
* blank lines and horizontal rules say nothing and are skipped

Line endings are normalised to ``\\n`` first; nothing else about the text is changed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CLAIMS_SCHEMA = "canon-claims.v1"

BULLET = "bullet"
NUMBERED = "numbered"
CHECKBOX = "checkbox"
TABLE_ROW = "table_row"
TABLE_ROW_RAGGED = "table_row_ragged"
PARAGRAPH = "paragraph"
CODE = "code"

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(?:\[( |x|X)\]\s+)?(.*)$")
_NUMBERED = re.compile(r"^(\s*)(\d+[.)])\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


class CanonClaimsError(ValueError):
    """The input is not something that can be cut."""


@dataclass(frozen=True, slots=True)
class Claim:
    """One statement of a file, with where it is and its words exactly as written."""

    id: str
    file: str
    line: int
    section: str
    kind: str
    text: str
    marker: str = ""
    depth: int = 0
    checked: bool | None = None
    fields: tuple[tuple[str, str], ...] = ()

    def field(self, header: str) -> str | None:
        for name, cell in self.fields:
            if name == header:
                return cell
        return None

    def to_record(self) -> dict[str, object]:
        return {
            "id": self.id,
            "file": self.file,
            "line": self.line,
            "section": self.section,
            "kind": self.kind,
            "text": self.text,
            "marker": self.marker,
            "depth": self.depth,
            "checked": self.checked,
            "fields": [list(pair) for pair in self.fields],
        }


def _claim_id(file: str, line: int, text: str) -> str:
    return "claim:" + hashlib.sha256(f"{file}\0{line}\0{text}".encode()).hexdigest()[:16]


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body.removeprefix("|")
    body = body.removesuffix("|")
    return [cell.strip() for cell in body.split("|")]


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().count("|") >= 2


def cut_text(file: str, text: str) -> tuple[Claim, ...]:
    """Cut one file's text into claims, in file order."""

    if not isinstance(file, str) or not file.strip():
        raise CanonClaimsError("file name is required")
    if not isinstance(text, str):
        raise CanonClaimsError("text must be a string")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    claims: list[Claim] = []
    stack: list[tuple[int, str]] = []
    paragraph: list[tuple[int, str]] = []

    def section() -> str:
        return " / ".join(title for _level, title in stack)

    def add(line: int, kind: str, body: str, **extra: object) -> None:
        claims.append(
            Claim(
                id=_claim_id(file, line, body),
                file=file,
                line=line,
                section=section(),
                kind=kind,
                text=body,
                **extra,
            )
        )  # type: ignore[arg-type]

    def flush() -> None:
        if paragraph:
            add(paragraph[0][0], PARAGRAPH, "\n".join(item for _n, item in paragraph))
            paragraph.clear()

    index = 0
    while index < len(lines):
        number, line = index + 1, lines[index]
        stripped = line.strip()
        if not stripped or _RULE.match(line):
            flush()
            index += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2)))
            index += 1
            continue
        if _FENCE.match(line):
            flush()
            fence, body, end = _FENCE.match(line).group(1), [], index + 1  # type: ignore[union-attr]
            while end < len(lines) and not lines[end].strip().startswith(fence):
                body.append(lines[end])
                end += 1
            add(number + 1, CODE, "\n".join(body))
            index = end + 1
            continue
        if (
            _is_table_line(line)
            and index + 1 < len(lines)
            and _SEPARATOR.match(lines[index + 1])
            and "|" in lines[index + 1]
        ):
            flush()
            headers = _cells(line)
            end = index + 2
            while end < len(lines) and _is_table_line(lines[end]):
                cells = _cells(lines[end])
                row = lines[end].strip()
                if len(cells) == len(headers):
                    add(end + 1, TABLE_ROW, row, fields=tuple(zip(headers, cells, strict=True)))
                else:
                    add(end + 1, TABLE_ROW_RAGGED, row)
                end += 1
            index = end
            continue
        bullet = _BULLET.match(line)
        if bullet:
            flush()
            indent, mark, body = bullet.groups()
            if mark is None:
                add(number, BULLET, body, depth=len(indent) // 2)
            else:
                add(number, CHECKBOX, body, depth=len(indent) // 2, checked=mark.lower() == "x")
            index += 1
            continue
        numbered = _NUMBERED.match(line)
        if numbered:
            flush()
            indent, mark, body = numbered.groups()
            add(number, NUMBERED, body, marker=mark, depth=len(indent) // 2)
            index += 1
            continue
        paragraph.append((number, line.rstrip()))
        index += 1
    flush()
    return tuple(claims)


def cut_files(files: Mapping[str, str]) -> tuple[Claim, ...]:
    """Cut several files; claims come back grouped by file name, in file order."""

    claims: list[Claim] = []
    for name in sorted(files):
        claims.extend(cut_text(name, files[name]))
    return tuple(claims)


def is_verbatim(claim: Claim, source: str) -> bool:
    """True when the claim's words are, character for character, in the source text."""

    normalised = source.replace("\r\n", "\n").replace("\r", "\n")
    return claim.text in normalised


def table_rows(claims: Sequence[Claim], *, file: str | None = None) -> dict[str, dict[str, str]]:
    """Key each table row by its first cell: ``{"9주": {"주차": "9주", "학기 사건": ..., ...}}``.

    Only whole rows count (``table_row``). A first cell that appears twice is an error, not a silent overwrite.
    """

    rows: dict[str, dict[str, str]] = {}
    for claim in claims:
        if claim.kind != TABLE_ROW or (file is not None and claim.file != file):
            continue
        key = claim.fields[0][1]
        if key in rows:
            raise CanonClaimsError(
                f"table row key appears twice: {key!r} ({claim.file}:{claim.line})"
            )
        rows[key] = dict(claim.fields)
    return rows


__all__ = [
    "BULLET",
    "CHECKBOX",
    "CLAIMS_SCHEMA",
    "CODE",
    "NUMBERED",
    "PARAGRAPH",
    "TABLE_ROW",
    "TABLE_ROW_RAGGED",
    "CanonClaimsError",
    "Claim",
    "cut_files",
    "cut_text",
    "is_verbatim",
    "table_rows",
]
