"""Read-only inspection of portable Synapse ZIP bundles.

This module is intentionally an intake/observation boundary, not an import
commit path. It reads a ZIP without extracting or executing anything, checks
for traversal/symlink/size hazards, fingerprints each entry, and reports the
project shape so a later phase can turn reviewed observations into proposals.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import posixpath
import re
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple
from uuid import uuid4

DEFAULT_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 20_000
DEFAULT_MAX_ENTRY_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024
MANIFEST_NAME = "synapse.bundle.json"
# One file mentioning more distinct files than this is an index, not a reference;
# what is cut is named in ``content_summary`` (``references_truncated``, and
# ``decisions_truncated`` for a document that wrote down more decisions than are kept)
# so nobody builds on a short list.
MAX_REFERENCES_PER_ENTRY = 256
MAX_DECISIONS_PER_ENTRY = 32
# Entry fields that were added after the report format was first written.
_ADDED_ENTRY_FIELDS = ("references", "superseded_by", "missing_references", "start_points", "decisions")


class BundleError(ValueError):
    """Raised when a ZIP cannot be safely inspected as a portable bundle."""


@dataclass(frozen=True, slots=True)
class BundleReference:
    """Another file of the archive that a text file mentions, and how it wrote it.

    Only files that are in the archive are recorded; what a file mentions that is
    not there is not. ``path_mentions`` counts the target's path written out, with
    any folder in front of it allowed; ``relative_mentions`` the path written
    relative to the folder of the file that mentions it; ``name_mentions`` the
    bare file name, which is taken only when no other file shares that name.
    """

    target: str
    path_mentions: int = 0
    relative_mentions: int = 0
    name_mentions: int = 0


@dataclass(frozen=True, slots=True)
class BundleMissingReference:
    """A document names a document that is not in the archive.

    ``target`` is how it was written. If a file whose path ends with it does exist
    (``server/README.md`` for ``10_execution/server/README.md``) the name is right
    and the folder is not, and those files are in ``moved_to``; otherwise nothing in
    the archive carries that name. Only documents naming documents are recorded: a
    script that names an output it has not written yet is not a broken reference.
    """

    target: str
    mentions: int
    moved_to: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BundleDecision:
    """A line of a document that says something was decided, as the document wrote it.

    The text is the line itself (shortened if long), so whoever is about to ask a
    question can show what the project already says before asking.
    """

    line: int
    text: str


@dataclass(frozen=True, slots=True)
class BundleEntry:
    path: str
    size: int
    sha256: str
    kind: str
    title: str | None = None
    headings: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    references: tuple[BundleReference, ...] = ()
    # Files the document's own opening names as replacing it ("최신본은 X", "replaced by X").
    superseded_by: tuple[str, ...] = ()
    missing_references: tuple[BundleMissingReference, ...] = ()
    # Documents named on a line that says to start there ("Start with `docs/a.md`").
    start_points: tuple[str, ...] = ()
    # Lines of the document that say something was decided.
    decisions: tuple[BundleDecision, ...] = ()


@dataclass(frozen=True, slots=True)
class BundleReport:
    archive: str
    archive_sha256: str
    entry_count: int
    total_uncompressed_size: int
    roots: tuple[str, ...]
    key_files: tuple[str, ...]
    entries: tuple[BundleEntry, ...]
    manifest: Mapping[str, Any] | None
    content_summary: Mapping[str, Any]
    warnings: tuple[str, ...] = ()

    @property
    def safe(self) -> bool:
        return not self.warnings

    @property
    def source_id(self) -> str:
        """Stable provenance root for this exact archive content."""
        return f"source:bundle:{self.archive_sha256}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_entry_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise BundleError("ZIP entry 이름이 비어 있거나 NUL을 포함합니다.")
    normalised = name.replace("\\", "/")
    path = PurePosixPath(normalised)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BundleError(f"ZIP path traversal 또는 절대 경로가 감지되었습니다: {name!r}")
    result = str(path)
    return result.rstrip("/") if result != "." else ""


def _normalise_zip_member_name(info: zipfile.ZipInfo) -> tuple[str, tuple[str, str] | None]:
    """Interpret a ZIP member name once, including legacy UTF-8 and slash rules."""

    raw_name = info.filename
    name = raw_name
    if not (info.flag_bits & 0x800) and any(ord(char) > 127 for char in name):
        try:
            decoded = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        else:
            name = decoded
    decoded_from = (raw_name, name) if name != raw_name else None
    return _normalise_entry_name(name.replace("\\", "/")), decoded_from


def _entry_kind(path: str, wrapper: str = "") -> str:
    # What a file is depends on where it sits in the project, not in the zip: the
    # folder the zip tool added around everything is not part of the project.
    lower = path.removeprefix(wrapper).lower()
    if lower == MANIFEST_NAME:
        return "manifest"
    if lower == "agents.md" or lower.startswith("spec/"):
        return "authority"
    if lower.startswith(("docs/", "doc/")):
        return "documentation"
    if lower.startswith("tests/"):
        return "tests"
    if lower.startswith(("synapse/", "domain_packs/")):
        return "source"
    if lower.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".md", ".toml")):
        return "text"
    return "asset"


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # Unix file type bits are stored in the high word when an archive was
    # created on Unix. A symlink is never extracted or followed by this tool.
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


_MARKERS = (
    ("internal_only", re.compile(r"\binternal-only\b", re.IGNORECASE)),
    ("partial_public", re.compile(r"\bpartial-public\b", re.IGNORECASE)),
    ("api_key", re.compile(r"\bapi\s*key\b", re.IGNORECASE)),
    ("checkpoint", re.compile(r"\bcheckpoint\b", re.IGNORECASE)),
    ("project_connection", re.compile(r"\bproject\s+connection\b", re.IGNORECASE)),
    ("provenance", re.compile(r"\bprovenance\b", re.IGNORECASE)),
    ("canonical_state", re.compile(r"\bcanonical\s+state\b", re.IGNORECASE)),
)


# The words a marker cannot match without. Looking for a word in the text is far cheaper than
# running its pattern over all of it, and most texts hold none of them, so the pattern is run
# only for the few that hold every word. That is a pre-check, not a second definition: whatever
# the pattern matches the words are in the text, so the answers are the pattern's own.
_MARKER_WORDS = {
    "internal_only": ("internal-only",),
    "partial_public": ("partial-public",),
    "api_key": ("api", "key"),
    "checkpoint": ("checkpoint",),
    "project_connection": ("project", "connection"),
    "provenance": ("provenance",),
    "canonical_state": ("canonical", "state"),
}
# What a case-blind pattern counts as the same letter as an ASCII one beyond what lower() already
# makes of it: the dotted capital I (lower() gives an i with a mark after it), the dotless i and
# the long s. The Kelvin sign needs no entry; lower() makes it a k. Checked against every code point.
# Replaced one by one, not with ``str.translate``, which is slow over the non-ASCII text a
# Korean project is made of; a replace of a letter the text does not hold costs next to nothing.
_FOLD_TO_ASCII = (("\u0130", "i"), ("\u0131", "i"), ("\u017f", "s"))


def _signals_in(text: str) -> tuple[str, ...]:
    probe = text
    for odd, plain in _FOLD_TO_ASCII:
        probe = probe.replace(odd, plain)
    probe = probe.lower()
    return tuple(
        name
        for name, marker in _MARKERS
        if all(word in probe for word in _MARKER_WORDS[name]) and marker.search(text)
    )


def _text_analysis(path: str, data: bytes) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    if not path.lower().endswith((".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml")):
        return None, (), ()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, (), ()
    headings = tuple(
        match.group(1).strip()
        for match in re.finditer(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", text, re.MULTILINE)
        if match.group(1).strip()
    )
    title = headings[0] if headings else None
    return title, headings[:32], _signals_in(text)


_REFERENCE_SUFFIXES = (
    ".md", ".markdown", ".rst", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".sh", ".ps1", ".bat", ".cmd",
)  # fmt: skip
# A run of characters a file path is made of -- `[\w.\-/\\~@+]+` -- that has a dot in it, which
# is what a file name needs. A path with a space in it is not followed; that is a limit of
# reading prose, not a decision. Only the runs with a dot are handed over: most of a text is
# words, and walking all of them one by one was the larger part of reading a big project.
# The lookbehind pins a match to the start of a run and the possessive repeats never give a
# character back, so a long run with no dot is passed over in one go, not tried from every letter.
_DOTTED_PATH_TOKEN = re.compile(r"(?<![\w.\-/\\~@+])[\w\-/\\~@+]*+\.[\w.\-/\\~@+]*+")
# A path as source code writes it (every backslash doubled) and `docs//a.md` are each one path.
_REPEATED_SEPARATOR = re.compile(r"/{2,}")


# What a document says about itself when it has been replaced. Only its opening is
# read: a word like "deprecated" halfway down a page is usually about something else.
_SUPERSEDED = re.compile(
    r"갱신됨|최신본|대체됨|대체되었|폐기됨|\bsuperseded\b|\breplaced\s+by\b|\bobsolete\b"
    r"|\bdeprecated\b|\boutdated\b|\bno\s+longer\s+(?:maintained|used|valid|current)\b",
    re.IGNORECASE,
)
_OPENING_LINES = 20
# A line that says something was decided -- by its shape, not by the word. "decision"
# is also what a pipeline calls one of its fields, and "a matter for the user to
# decide" is the opposite of a decision made. What counts is a label ("결정:",
# "Decision:"), a mark ("(사용자 결정)", "(decided 2026-07-08)"), or the past tense
# ("확정했다", "decided today"). Lines inside a code fence are examples, not decisions.
_DECIDED = re.compile(
    r"^\s*(?:[-*>]+\s*)*(?:\*\*)?(?:final\s+|최종\s*)?(?:decision|decided|확정|결정)(?:\*\*)?\s*[:：]"
    r"|[(\[（]\s*(?:사용자\s*|소유자\s*)?(?:확정|결정)(?:됨)?\s*[)\]）]"
    r"|[(\[]\s*(?:decided\b|(?:user|owner|supervisor)\s+(?:confirmed|approved|decided)\b)[^)\]]{0,40}[)\]]"
    r"|(?:확정|결정)(?:했|됐|되었|함)|\bdecided\s+(?:today|on|in)\b|\bwe\s+decided\b|\bit\s+was\s+decided\b",
    re.IGNORECASE,
)
_EXCERPT_CHARS = 200
# A line that tells the reader where to begin. The documents named on it are start
# points. The word has to stand alone: `START_HERE_NEXT_STEPS.md` is a file name.
_START_HERE = re.compile(
    r"\bstart\s+(?:here|with)\b|\bbegin\s+with\b|\bread\s+(?:this\s+)?first\b"
    r"|여기서\s*시작|시작(?:은|점|하세요|하려면)|먼저\s*(?:읽|보|확인)|처음에?\s*(?:읽|보)",
    re.IGNORECASE,
)
# A document is replaced by another document: a settings file that says "deprecated"
# near its top, or a notice that also names the scripts it touches, is not that.
_DOCUMENT_SUFFIXES = (".md", ".markdown", ".rst", ".txt")


def archive_wrapper(names: Iterable[str]) -> str:
    """The one folder every file of an archive sits in, with its slash, or ``""``.

    A project zipped as one folder is mentioned by its own paths (`docs/a.md`), not
    by the folder the zip happens to add (`project/docs/a.md`).
    """

    listed = frozenset(names)
    tops = {name.partition("/")[0] for name in listed}
    return f"{next(iter(tops))}/" if len(tops) == 1 and all("/" in name for name in listed) else ""


TEXT_SUFFIXES = (".md", ".markdown", ".txt")
MAX_TEXT_FILES = 500
MAX_TEXT_BYTES = 1024 * 1024


def archive_texts(report: BundleReport) -> dict[str, str]:
    """The text files of an inspected archive, by their path in it; the archive is read, never extracted.

    Each member is checked against what the inspection saw (size and fingerprint), so a ZIP that changed since is
    refused, not half read. Files that are not UTF-8 text, are over the size limit or come after the first
    ``MAX_TEXT_FILES`` are left out.
    """

    wanted = {
        entry.path: entry
        for entry in report.entries
        if entry.path.lower().endswith(TEXT_SUFFIXES) and entry.size <= MAX_TEXT_BYTES
    }
    texts: dict[str, str] = {}
    members = verified_members(report)
    for name, data in members.items():
        entry = wanted.get(name)
        if entry is None:
            continue
        if len(texts) >= MAX_TEXT_FILES:
            break
        try:
            texts[name] = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
    return texts


def verified_members(report: BundleReport) -> dict[str, bytes]:
    """Return every member from the exact ZIP bytes named by an inspection report."""

    try:
        archive_path = Path(report.archive)
        if archive_path.stat().st_size > DEFAULT_MAX_ARCHIVE_BYTES:
            raise BundleError("검증할 ZIP이 허용 크기를 초과합니다.")
        with archive_path.open("rb") as handle:
            archive_bytes = handle.read(DEFAULT_MAX_ARCHIVE_BYTES + 1)
    except OSError as exc:
        raise BundleError(f"검사한 ZIP을 다시 읽을 수 없습니다: {exc}") from exc
    if len(archive_bytes) > DEFAULT_MAX_ARCHIVE_BYTES:
        raise BundleError("검증할 ZIP이 허용 크기를 초과합니다.")
    if hashlib.sha256(archive_bytes).hexdigest() != report.archive_sha256:
        raise BundleError("검사한 ZIP과 현재 ZIP의 전체 내용이 다릅니다.")

    entries = {entry.path: entry for entry in report.entries}
    if (
        len(entries) != len(report.entries)
        or report.entry_count != len(entries)
    ):
        raise BundleError("검사 결과의 파일 목록이 일치하지 않습니다.")

    members: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name, _decoded_from = _normalise_zip_member_name(info)
                if name in members:
                    raise BundleError(f"정규화 후 중복되는 ZIP entry입니다: {name}")
                entry = entries.get(name)
                if entry is None:
                    raise BundleError("검사한 ZIP과 현재 ZIP의 파일 목록이 다릅니다.")
                if info.file_size != entry.size:
                    raise BundleError(f"검사한 ZIP과 현재 파일 크기가 다릅니다: {name}")
                data = archive.read(info)
                if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
                    raise BundleError(f"검사한 ZIP과 현재 파일 내용이 다릅니다: {name}")
                members[name] = data
    except BundleError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, ValueError) as exc:
        raise BundleError(f"검사한 ZIP의 멤버를 읽을 수 없습니다: {exc}") from exc
    if members.keys() != entries.keys():
        raise BundleError("검사한 ZIP과 현재 ZIP의 파일 목록이 다릅니다.")
    return members


class _ArchiveIndex(NamedTuple):
    """What reading a file needs to know about the archive as a whole."""

    listed: frozenset[str]
    holders: Mapping[str, tuple[str, ...]]  # file name -> every path that carries it
    wrapper: str


def _reference_index(names: Iterable[str]) -> _ArchiveIndex:
    listed = frozenset(names)
    holders: dict[str, list[str]] = {}
    for name in sorted(listed):
        holders.setdefault(name.rpartition("/")[2], []).append(name)
    return _ArchiveIndex(
        listed=listed,
        holders={leaf: tuple(paths) for leaf, paths in holders.items()},
        wrapper=archive_wrapper(listed),
    )


# Around a path in prose these mean it is a pattern, not a file: `docs/*_SPEC.md`,
# `<CATEGORY>_SPEC.md`, `${HOME}/a.md`. What is left of the pattern is not a name.
_PATTERN_EDGE = frozenset("*<>{}$%")
_PLACEHOLDER = re.compile(r"YYYY|MM-DD|\bDD\b|NNN|XXX")


def _note_missing(
    missing: dict[str, list[Any]],
    text: str,
    match: re.Match[str],
    candidate: str,
    index: _ArchiveIndex,
) -> None:
    """Remember a document named but not there -- unless it is not really a name."""

    raw = match.group()
    leaf = candidate.rpartition("/")[2]
    stem = leaf.rpartition(".")[0]
    if not leaf.lower().endswith(_DOCUMENT_SUFFIXES) or not re.search(r"[A-Za-z가-힣]", stem):
        return
    if raw.startswith(("/", "\\", "~")) or ".." in candidate.split("/"):
        return  # outside the project: an absolute path, a home folder, somewhere above
    before = text[match.start() - 1] if match.start() else ""
    after = text[match.end()] if match.end() < len(text) else ""
    if before in _PATTERN_EDGE or after in _PATTERN_EDGE or _PLACEHOLDER.search(candidate):
        return
    holders = index.holders.get(leaf, ())
    moved: tuple[str, ...] = ()
    if holders:
        # The name exists somewhere. It is a broken reference only if what was written
        # is a path and names exactly where the file used to be -- the end of a path
        # that is there: `server/README.md` for `10_execution/server/README.md`.
        if "/" not in candidate:
            return
        moved = tuple(path for path in holders if path.endswith("/" + candidate))
        if not 1 <= len(moved) <= 3:
            return
    entry = missing.setdefault(candidate, [0, moved])
    entry[0] += 1


def _mentions(
    path: str,
    text: str,
    index: _ArchiveIndex,
    missing: dict[str, list[Any]] | None = None,
) -> dict[str, list[int]]:
    """target -> [written out, relative to this file, bare name] for the files this text mentions.

    ``missing``, when given, also collects the documents it names that are not there.
    """

    listed, wrapper = index.listed, index.wrapper
    here = path.rpartition("/")[0]
    found: dict[str, list[int]] = {}
    for match in _DOTTED_PATH_TOKEN.finditer(text):
        token = match.group()
        if token.startswith("//"):
            continue  # `//` is what follows `https:`, somebody else's file
        # `@AGENTS.md` is how one agent file imports another: it names that file.
        candidate = _REPEATED_SEPARATOR.sub("/", token.replace("\\", "/")).rstrip(".").removeprefix("@")
        while candidate.startswith("./"):
            candidate = candidate[2:]
        leaf = candidate.rpartition("/")[2]
        if "." not in leaf or len(candidate) > 260:
            continue
        if leaf not in index.holders and (missing is None or not leaf.lower().endswith(_DOCUMENT_SUFFIXES)):
            # Every way of finding a target below ends at a file with this very name, and the
            # only other thing done with a name nobody carries is to note a missing document.
            continue
        target: str | None = None
        slot = 0
        if candidate in listed:
            target = candidate
        elif wrapper and wrapper + candidate in listed:
            target = wrapper + candidate
        else:
            parts = candidate.split("/")
            # The path with a folder in front of it (`/srv/project/docs/a.md`). The bare
            # file name is not a path and is handled below.
            for start in range(1, len(parts) - 1):
                tail = "/".join(parts[start:])
                if tail in listed:
                    target = tail
                    break
                if wrapper and wrapper + tail in listed:
                    target = wrapper + tail
                    break
            if target is None:
                relative = posixpath.normpath(posixpath.join(here, candidate))
                holders = index.holders.get(leaf, ())
                if relative in listed:
                    target, slot = relative, 1
                elif len(holders) == 1:
                    target, slot = holders[0], 2
                    if missing is not None:
                        # Found by its name alone; if what was written is the end of
                        # where it is now, the folder it names is the one it left.
                        _note_missing(missing, text, match, candidate, index)
        if target is None:
            if missing is not None:
                _note_missing(missing, text, match, candidate, index)
            continue
        if target == path:
            continue
        found.setdefault(target, [0, 0, 0])[slot] += 1
    return found


class _Read(NamedTuple):
    """What reading one file for the files it names came to."""

    references: tuple[BundleReference, ...] = ()
    cut: bool = False
    replaced_by: tuple[str, ...] = ()
    missing: tuple[BundleMissingReference, ...] = ()
    start_points: tuple[str, ...] = ()
    decisions: tuple[BundleDecision, ...] = ()
    decisions_cut: bool = False


def _decisions_in(lines: list[str]) -> list[BundleDecision]:
    found = []
    fenced = False
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and _DECIDED.search(line):
            said = " ".join(line.split())
            found.append(
                BundleDecision(number, said if len(said) <= _EXCERPT_CHARS else said[: _EXCERPT_CHARS - 1] + "…")
            )
    return found


def _read_references(path: str, data: bytes, index: _ArchiveIndex) -> _Read:
    """What this file mentions, whether a list was cut, what replaced it, what is missing,
    and where it tells the reader to start."""

    if not path.lower().endswith(_REFERENCE_SUFFIXES):
        return _Read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _Read()
    is_document = path.lower().endswith(_DOCUMENT_SUFFIXES)
    missing: dict[str, list[Any]] | None = {} if is_document else None
    ranked = sorted(
        _mentions(path, text, index, missing).items(),
        key=lambda item: (-sum(item[1]), item[0]),
    )
    kept = sorted(ranked[:MAX_REFERENCES_PER_ENTRY])
    gone = sorted((missing or {}).items(), key=lambda item: (-item[1][0], item[0]))
    kept_gone = sorted(gone[:MAX_REFERENCES_PER_ENTRY])
    lines = text.splitlines() if is_document else []  # only documents are read by line, and only once
    opening = "\n".join(lines[:_OPENING_LINES])
    replaced_by = (
        tuple(
            sorted(
                target
                for target in _mentions(path, opening, index)
                if target.lower().endswith(_DOCUMENT_SUFFIXES)
            )
        )
        if is_document and _SUPERSEDED.search(opening)
        else ()
    )
    start_points: tuple[str, ...] = ()
    if is_document and _START_HERE.search(text):
        # Only the documents named on the very line that says to start there.
        start_points = tuple(
            sorted(
                {
                    target
                    for line in lines
                    if _START_HERE.search(line)
                    for target in _mentions(path, line, index)
                    if target.lower().endswith(_DOCUMENT_SUFFIXES)
                }
            )
        )
    decided = _decisions_in(lines)
    return _Read(
        references=tuple(BundleReference(target, *counts) for target, counts in kept),
        cut=len(ranked) > MAX_REFERENCES_PER_ENTRY or len(gone) > MAX_REFERENCES_PER_ENTRY,
        decisions=tuple(decided[:MAX_DECISIONS_PER_ENTRY]),
        decisions_cut=len(decided) > MAX_DECISIONS_PER_ENTRY,
        replaced_by=replaced_by,
        missing=tuple(
            BundleMissingReference(target, count, moved) for target, (count, moved) in kept_gone
        ),
        start_points=start_points,
    )


def _report_dict(report: BundleReport) -> dict[str, Any]:
    return asdict(report)


def save_bundle_report(report: BundleReport, path: str | Path) -> Path:
    """Atomically persist an intake report without importing its contents."""
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "synapse-bundle-report",
        "version": 1,
        "source_id": report.source_id,
        "report": _report_dict(report),
    }
    document = {"payload": payload, "sha256": hashlib.sha256(_canonical_json(payload)).hexdigest()}
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BundleError(f"bundle report를 저장할 수 없습니다: {destination}") from exc
    return destination


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_bundle_report(path: str | Path, *, archive: str | Path | None = None) -> BundleReport:
    """Reload a report and optionally verify it against the current ZIP."""
    source = Path(path).expanduser().resolve()
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"bundle report를 읽을 수 없습니다: {source}") from exc
    if not isinstance(document, Mapping):
        raise BundleError("bundle report 최상위 문서는 객체여야 합니다.")
    payload = document.get("payload")
    digest = document.get("sha256")
    if not isinstance(payload, Mapping) or not isinstance(digest, str):
        raise BundleError("bundle report integrity envelope가 없습니다.")
    if digest != hashlib.sha256(_canonical_json(payload)).hexdigest():
        raise BundleError("bundle report checksum이 일치하지 않습니다.")
    if payload.get("format") != "synapse-bundle-report" or payload.get("version") != 1:
        raise BundleError("지원하지 않는 bundle report format/version입니다.")
    raw = payload.get("report")
    if not isinstance(raw, Mapping) or payload.get("source_id") != f"source:bundle:{raw.get('archive_sha256', '')}":
        raise BundleError("bundle report source identity가 일치하지 않습니다.")
    try:
        # A report saved before these were recorded lacks their keys; it is compared
        # with the current ZIP without exactly what it lacks, as it always was.
        lacks = {
            name
            for name in _ADDED_ENTRY_FIELDS
            if any(name not in entry for entry in raw["entries"])
        }
        entries = tuple(
            BundleEntry(
                path=str(entry["path"]),
                size=int(entry["size"]),
                sha256=str(entry["sha256"]),
                kind=str(entry["kind"]),
                title=entry.get("title"),
                headings=tuple(entry.get("headings", ())),
                signals=tuple(entry.get("signals", ())),
                references=tuple(
                    BundleReference(
                        target=str(item["target"]),
                        path_mentions=int(item.get("path_mentions", 0)),
                        relative_mentions=int(item.get("relative_mentions", 0)),
                        name_mentions=int(item.get("name_mentions", 0)),
                    )
                    for item in entry.get("references", ())
                ),
                superseded_by=tuple(str(item) for item in entry.get("superseded_by", ())),
                start_points=tuple(str(item) for item in entry.get("start_points", ())),
                decisions=tuple(
                    BundleDecision(line=int(item["line"]), text=str(item["text"]))
                    for item in entry.get("decisions", ())
                ),
                missing_references=tuple(
                    BundleMissingReference(
                        target=str(item["target"]),
                        mentions=int(item.get("mentions", 0)),
                        moved_to=tuple(str(path) for path in item.get("moved_to", ())),
                    )
                    for item in entry.get("missing_references", ())
                ),
            )
            for entry in raw["entries"]
        )
        report = BundleReport(
            archive=str(raw["archive"]),
            archive_sha256=str(raw["archive_sha256"]),
            entry_count=int(raw["entry_count"]),
            total_uncompressed_size=int(raw["total_uncompressed_size"]),
            roots=tuple(raw.get("roots", ())),
            key_files=tuple(raw.get("key_files", ())),
            entries=entries,
            manifest=raw.get("manifest"),
            content_summary=raw.get("content_summary", {}),
            warnings=tuple(raw.get("warnings", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BundleError("bundle report 구조가 잘못되었습니다.") from exc
    if report.source_id != payload["source_id"]:
        raise BundleError("bundle report source_id가 archive hash와 다릅니다.")
    if archive is not None:
        current = inspect_bundle(archive)
        # ``archive`` is a locator, not part of the observed ZIP content. A
        # caller may move or copy an unchanged archive before reusing its
        # report. Compare every observed field while rebinding that locator to
        # the current archive, then return the current observation so later
        # proposal checks stay bound to the path the caller supplied.
        expected = replace(report, archive=current.archive)
        observed = current
        if lacks:
            observed = replace(
                current,
                entries=tuple(
                    replace(entry, **{name: () for name in lacks}) for entry in current.entries
                ),
                content_summary={
                    key: value
                    for key, value in current.content_summary.items()
                    if not (
                        (key == "references_truncated" and lacks & {"references", "missing_references"})
                        or (key == "decisions_truncated" and "decisions" in lacks)
                    )
                },
            )
        if observed != expected:
            raise BundleError(
                "저장된 bundle report가 현재 ZIP의 관찰 결과와 다릅니다."
            )
        report = current
    return report


def inspect_bundle(
    archive: str | Path,
    *,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> BundleReport:
    """Inspect a ZIP file without extracting or executing its contents."""
    path = Path(archive).expanduser().resolve()
    if not path.is_file():
        raise BundleError(f"ZIP 파일을 찾을 수 없습니다: {path}")
    archive_size = path.stat().st_size
    if archive_size > max_archive_bytes:
        raise BundleError(f"ZIP 파일이 허용 크기를 초과합니다: {archive_size}")

    entries: list[BundleEntry] = []
    names_seen: set[str] = set()
    total_size = 0
    manifest: Mapping[str, Any] | None = None
    warnings: list[str] = []
    marker_counts: dict[str, int] = {}
    references_cut: list[str] = []
    decisions_cut: list[str] = []
    try:
        with zipfile.ZipFile(path) as source:
            infos = source.infolist()
            if len(infos) > max_entries:
                raise BundleError(f"ZIP entry 수가 허용 한도를 초과합니다: {len(infos)}")

            def interpreted_name(info: zipfile.ZipInfo) -> str:
                name, decoded_from = _normalise_zip_member_name(info)
                if decoded_from is not None:
                    warning = f"이름을 UTF-8로 다시 해석함: {decoded_from[0]} -> {decoded_from[1]}"
                    if warning not in warnings:
                        warnings.append(warning)
                return name

            listed_names: list[str] = []
            for info in infos:
                if info.is_dir():
                    continue
                try:
                    listed = interpreted_name(info)
                except BundleError:
                    continue  # refused with its own message when the loop below reaches it
                if listed:
                    listed_names.append(listed)
            archive_index = _reference_index(listed_names)
            for info in infos:
                name = interpreted_name(info)
                if not name:
                    continue
                collision_key = name.casefold()
                if collision_key in names_seen:
                    raise BundleError(f"대소문자 무시 시 중복되는 ZIP entry입니다: {name}")
                names_seen.add(collision_key)
                if _is_symlink(info):
                    raise BundleError(f"symlink ZIP entry는 허용하지 않습니다: {name}")
                if info.file_size > max_entry_bytes:
                    raise BundleError(f"ZIP entry가 허용 크기를 초과합니다: {name}")
                total_size += info.file_size
                if total_size > max_total_bytes:
                    raise BundleError("ZIP의 압축 해제 총량이 허용 한도를 초과합니다.")
                if info.is_dir():
                    continue
                with source.open(info, "r") as handle:
                    data = handle.read(max_entry_bytes + 1)
                if len(data) != info.file_size:
                    raise BundleError(f"ZIP entry 크기를 검증할 수 없습니다: {name}")
                if name.removeprefix(archive_index.wrapper) == MANIFEST_NAME:
                    try:
                        loaded = json.loads(data.decode("utf-8"))
                    except (UnicodeError, json.JSONDecodeError) as exc:
                        raise BundleError("synapse.bundle.json을 읽을 수 없습니다.") from exc
                    if not isinstance(loaded, Mapping):
                        raise BundleError("synapse.bundle.json은 JSON 객체여야 합니다.")
                    manifest = dict(loaded)
                title, headings, signals = _text_analysis(name, data)
                for signal in signals:
                    marker_counts[signal] = marker_counts.get(signal, 0) + 1
                read = _read_references(name, data, archive_index)
                if read.cut:
                    references_cut.append(name)
                if read.decisions_cut:
                    decisions_cut.append(name)
                entries.append(
                    BundleEntry(
                        path=name,
                        size=info.file_size,
                        sha256=hashlib.sha256(data).hexdigest(),
                        kind=_entry_kind(name, archive_index.wrapper),
                        title=title,
                        headings=headings,
                        signals=signals,
                        references=read.references,
                        superseded_by=read.replaced_by,
                        missing_references=read.missing,
                        start_points=read.start_points,
                        decisions=read.decisions,
                    )
                )
    except BundleError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise BundleError(f"ZIP 파일을 안전하게 읽을 수 없습니다: {path}") from exc

    paths = tuple(entry.path for entry in entries)
    roots = tuple(sorted({entry.path.split("/", 1)[0] for entry in entries}))
    key_files = tuple(
        path_name
        for path_name in paths
        if (logical := path_name.removeprefix(archive_index.wrapper).casefold())
        in {"agents.md", "readme.md", "pyproject.toml", "package.json", ".openai/hosting.json"}
        or logical.startswith("spec/")
    )
    return BundleReport(
        archive=str(path),
        archive_sha256=_sha256_file(path),
        entry_count=len(entries),
        total_uncompressed_size=total_size,
        roots=roots,
        key_files=key_files,
        entries=tuple(sorted(entries, key=lambda entry: entry.path)),
        manifest=manifest,
        content_summary={
            "text_files": sum(1 for entry in entries if entry.title or entry.headings or entry.signals),
            "markdown_files": sum(1 for entry in entries if entry.path.lower().endswith(".md")),
            "marker_file_counts": dict(sorted(marker_counts.items())),
            "titles": {
                entry.path: entry.title
                for entry in sorted(entries, key=lambda entry: entry.path)
                if entry.title
            },
            **({"references_truncated": sorted(references_cut)} if references_cut else {}),
            **({"decisions_truncated": sorted(decisions_cut)} if decisions_cut else {}),
        },
        warnings=tuple(warnings),
    )


def manifest_entry_path(report: BundleReport) -> str | None:
    """Where the project's manifest is in the archive -- inside the wrapping folder if
    the zip added one -- or ``None`` if there is none."""

    for entry in report.entries:
        if entry.kind == "manifest":
            return entry.path
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a Synapse ZIP bundle safely")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--report", type=Path, help="분석 report를 JSON으로 저장할 경로")
    args = parser.parse_args(argv)
    try:
        report = inspect_bundle(args.archive)
    except BundleError as exc:
        parser.error(str(exc))
    if args.report:
        save_bundle_report(report, args.report)
    print(json.dumps(_report_dict(report), ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "MAX_REFERENCES_PER_ENTRY",
    "MAX_TEXT_BYTES",
    "MAX_TEXT_FILES",
    "TEXT_SUFFIXES",
    "BundleDecision",
    "BundleEntry",
    "BundleError",
    "BundleMissingReference",
    "BundleReference",
    "BundleReport",
    "archive_wrapper",
    "inspect_bundle",
    "load_bundle_report",
    "manifest_entry_path",
    "save_bundle_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
