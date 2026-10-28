"""Propose which documents of an existing project are old, and what to do with each.

Once a project's canon is established the documents that no longer say what is
true still sit beside it, and the same requirement read in an old one looks as
good as in the new. Telling them apart is a judgement about what the project is
now, and only the person who owns it can make that. What can be known from the
files alone is narrower, and this keeps to it:

* a document that code mentions cannot be moved without breaking that code;
* a document the project's own front door mentions is current by the owner's own
  words;
* a document may say, in its opening, which document replaced it;
* moving a document leaves every path written out to it pointing nowhere, and how
  many those are can be counted exactly.

So it names the documents worth asking about, shows the evidence for each, asks,
and works out the rest from the answers: which can be moved, which can only be
marked where they are, and which paths have to be rewritten. The only thing it
proposes on its own is a document that says itself that another replaced it and
that nothing reads. Everything is a proposal. It reads nothing itself, writes
nothing, and lists no directory; applying a plan is a separate, confirmed step.

It follows the rules the canon questions follow: an answer must name a document
that was actually observed, a question is answered once, and an answer produces a
new immutable revision.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from synapse.core.bundle import BundleReport, archive_wrapper, inspect_bundle
from synapse.core.contracts import LifecycleStatus
from synapse.core.idea_session import OpenQuestion, UserAnswer

CLEANUP_SCHEMA = "canon-cleanup.v1"

# A document is a candidate when the date in its name is more than this many days
# older than the newest dated document of the project.
STALE_AFTER_DAYS = 30

# Where a moved document goes: a folder of that name beside where it was.
OLD_FOLDER = "_old"

ANSWER_OLD = "OLD"
ANSWER_CURRENT = "CURRENT"
_ANSWERS = (ANSWER_OLD, ANSWER_CURRENT)

_DOCUMENT_SUFFIXES = (".md", ".markdown", ".rst", ".txt")
_NAME_DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
_NAME_SPLIT = re.compile(r"[_\-\s]+")

# The candidates are read in these sections, in this order. A section says what the
# files show about a document; it is not an answer. Documents in one section are
# often old and current alike (what code reads is both), so nothing is answered for
# a whole section -- only for the groups below, which are made of documents that
# are the same kind of thing.
SECTION_REPLACED = "REPLACED"
SECTION_READ_BY_CODE = "READ_BY_CODE"
SECTION_MENTIONED_OUTSIDE = "MENTIONED_OUTSIDE"
SECTION_MENTIONED_ONLY_BY_CANDIDATES = "MENTIONED_ONLY_BY_CANDIDATES"
SECTION_UNMENTIONED = "UNMENTIONED"
_SECTIONS = (
    (SECTION_REPLACED, "스스로 다른 문서로 대체됐다고 적은 문서"),
    (SECTION_READ_BY_CODE, "코드가 읽는 문서 (옛 문서라도 옮길 수 없고 제자리에 표시만 가능)"),
    (SECTION_MENTIONED_OUTSIDE, "이 후보들 밖의 문서가 가리키는 문서"),
    (SECTION_MENTIONED_ONLY_BY_CANDIDATES, "다른 후보 문서만 가리키는 문서"),
    (SECTION_UNMENTIONED, "아무 파일도 가리키지 않는 문서"),
)

GROUP_SIBLINGS = "SIBLINGS"
GROUP_SAME_MENTIONERS = "SAME_MENTIONERS"
_MIN_SIBLINGS = 3
_MIN_SAME_MENTIONERS = 2


class CanonCleanupError(ValueError):
    """Raised when a cleanup cannot be proposed or an answer cannot be taken as given."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanupMention:
    """One file that mentions a candidate, and in which of the three ways."""

    file: str
    path: int
    relative: int
    name: int
    by_code: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "path": self.path,
            "relative": self.relative,
            "name": self.name,
            "by_code": self.by_code,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanupCandidate:
    """A document worth asking about, with what the files say about it."""

    path: str
    dated: str | None
    age_days: int | None
    mentions: tuple[CleanupMention, ...]
    front_door: tuple[str, ...]
    replaced_by: tuple[str, ...]
    named_by_person: bool = False

    @property
    def read_by_code(self) -> tuple[str, ...]:
        return tuple(item.file for item in self.mentions if item.by_code)

    @property
    def proposed(self) -> bool:
        """The one case it proposes unasked: it says it was replaced and nothing reads it."""

        return bool(self.replaced_by) and not self.read_by_code and not self.front_door

    @property
    def if_old(self) -> str:
        """What can be done with it if it is old: code reads it, so only mark it in place."""

        return "in_place" if self.read_by_code else "move"

    def to_record(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "dated": self.dated,
            "age_days": self.age_days,
            "read_by_code": list(self.read_by_code),
            "mentioned_by": [item.to_record() for item in self.mentions],
            "front_door": list(self.front_door),
            "replaced_by": list(self.replaced_by),
            "named_by_person": self.named_by_person,
            "proposed": self.proposed,
            "if_old": self.if_old,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class PathRewrite:
    """A path written out in one file that must change when its document moves."""

    file: str
    old: str
    new: str
    count: int

    def to_record(self) -> dict[str, Any]:
        return {"file": self.file, "old": self.old, "new": self.new, "count": self.count}


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanupPlan:
    """What the answers so far come to. A proposal to look at, not an instruction."""

    move: tuple[str, ...]
    in_place: tuple[str, ...]
    human_check: tuple[str, ...]
    rewrites: tuple[PathRewrite, ...]
    reasons: Mapping[str, str]

    def to_record(self) -> dict[str, Any]:
        return {
            "move": list(self.move),
            "in_place": list(self.in_place),
            "human_check": list(self.human_check),
            "rewrites": [item.to_record() for item in self.rewrites],
            "reasons": dict(sorted(self.reasons.items())),
            "proposed_status": LifecycleStatus.DEPRECATED.value,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanupGroup:
    """Documents of the same kind, so the person can answer for them in one go."""

    kind: str
    label: str
    why: str
    members: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "why": self.why, "members": list(self.members)}


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanupSection:
    """One section of the candidates: some in groups, the rest on their own."""

    id: str
    title: str
    groups: tuple[CleanupGroup, ...]
    singles: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "groups": [item.to_record() for item in self.groups],
            "singles": list(self.singles),
        }


def _name_tokens(path: str) -> tuple[str, ...]:
    stem = _NAME_DATE.sub("", posixpath.splitext(path.rpartition("/")[2])[0])
    return tuple(token for token in _NAME_SPLIT.split(stem) if token)


def _normalise(value: str) -> str:
    path = value.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _is_document(path: str) -> bool:
    return path.lower().endswith(_DOCUMENT_SUFFIXES)


def _folder(path: str) -> str:
    return path.rpartition("/")[0]


def _destination(path: str) -> str:
    return posixpath.join(_folder(path), OLD_FOLDER, path.rpartition("/")[2])


def destination_of(path: str) -> str:
    """Where a document goes when it is set aside: beside itself, in the old-documents folder."""

    return _destination(path)


def _name_date(path: str) -> date | None:
    match = _NAME_DATE.search(path.rpartition("/")[2])
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def _why_needed(candidate: CleanupCandidate) -> str:
    facts: list[str] = []
    if candidate.dated is not None and candidate.age_days:
        facts.append(f"이름의 날짜({candidate.dated})가 가장 새 문서보다 {candidate.age_days}일 오래됐습니다.")
    if candidate.named_by_person:
        facts.append("직접 지목하신 문서입니다.")
    if candidate.replaced_by:
        facts.append(f"문서가 스스로 {', '.join(candidate.replaced_by)}(으)로 대체됐다고 적고 있습니다.")
    if candidate.read_by_code:
        facts.append(
            f"코드가 이 문서를 가리킵니다({', '.join(candidate.read_by_code)}). 옛 문서라도 옮기면 "
            "그 코드가 깨지므로 제자리에 표시만 할 수 있습니다."
        )
    elif candidate.mentions:
        facts.append(f"다른 문서 {len(candidate.mentions)}개가 가리킵니다.")
    else:
        facts.append("이 문서를 가리키는 파일이 없습니다.")
    if candidate.front_door:
        facts.append(f"프로젝트 첫 화면 문서가 가리킵니다({', '.join(candidate.front_door)}).")
    return " ".join(facts) + " 옛 문서인지는 이 프로젝트의 지금 방향을 아는 분만 정할 수 있습니다."


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanupInquiry:
    """The documents asked about, and what has been answered."""

    project_id: str
    archive_sha256: str
    wrapper: str
    observed_paths: tuple[str, ...]
    candidates: tuple[CleanupCandidate, ...]
    questions: tuple[OpenQuestion, ...]
    answers: tuple[UserAnswer, ...] = ()
    revision: int = 0
    schema_version: str = CLEANUP_SCHEMA

    @property
    def open_question_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.questions if item.status != "ANSWERED")

    def question_id(self, path: str) -> str:
        return f"cleanup-question:{self.archive_sha256}:{path}"

    def section_of(self, candidate: CleanupCandidate) -> str:
        if candidate.replaced_by:
            return SECTION_REPLACED
        if candidate.read_by_code:
            return SECTION_READ_BY_CODE
        if not candidate.mentions:
            return SECTION_UNMENTIONED
        asked = {item.path for item in self.candidates}
        if any(item.file not in asked for item in candidate.mentions):
            return SECTION_MENTIONED_OUTSIDE
        return SECTION_MENTIONED_ONLY_BY_CANDIDATES

    @property
    def sections(self) -> tuple[CleanupSection, ...]:
        """The candidates in reading order, with the groups that can be answered at once.

        Two kinds of group, both inside one section so the files show the same
        thing about every member: names that differ in a single word (the nine
        `*_SPEC` documents, one per kind of product), and documents that exactly
        the same files point to. A shared first word is not enough -- the project's
        own name starts half the file names.
        """

        by_section: dict[str, list[CleanupCandidate]] = {}
        for candidate in self.candidates:
            by_section.setdefault(self.section_of(candidate), []).append(candidate)

        result = []
        for section_id, title in _SECTIONS:
            members = by_section.get(section_id, [])
            if not members:
                continue
            groups: list[CleanupGroup] = []
            taken: set[str] = set()

            siblings: dict[tuple[Any, ...], list[str]] = {}
            for candidate in members:
                words = _name_tokens(candidate.path)
                if len(words) < 2:
                    continue  # one word differing is all of it: nothing is shared
                for slot in range(len(words)):
                    shape = (_folder(candidate.path), len(words), slot, words[:slot] + words[slot + 1 :])
                    siblings.setdefault(shape, []).append(candidate.path)
            for shape, named in sorted(siblings.items(), key=lambda item: (-len(item[1]), str(item[0]))):
                paths = [path for path in named if path not in taken]
                if len(paths) < _MIN_SIBLINGS:
                    continue
                folder, _, slot, rest = shape
                pattern = "_".join((*rest[:slot], "*", *rest[slot:]))
                groups.append(
                    CleanupGroup(
                        kind=GROUP_SIBLINGS,
                        label=posixpath.join(folder, pattern) if folder else pattern,
                        why=f"이름이 한 단어만 다른 같은 꼴의 문서 {len(paths)}개입니다.",
                        members=tuple(sorted(paths)),
                    )
                )
                taken.update(paths)

            sharing: dict[frozenset[str], list[str]] = {}
            for candidate in members:
                if candidate.path not in taken and candidate.mentions:
                    sharing.setdefault(frozenset(item.file for item in candidate.mentions), []).append(
                        candidate.path
                    )
            for files, paths in sorted(sharing.items(), key=lambda item: sorted(item[0])):
                if len(paths) < _MIN_SAME_MENTIONERS:
                    continue
                groups.append(
                    CleanupGroup(
                        kind=GROUP_SAME_MENTIONERS,
                        label="가리키는 파일: " + ", ".join(sorted(files)),
                        why=f"똑같은 파일들만 가리키는 문서 {len(paths)}개입니다.",
                        members=tuple(sorted(paths)),
                    )
                )
                taken.update(paths)

            result.append(
                CleanupSection(
                    id=section_id,
                    title=title,
                    groups=tuple(sorted(groups, key=lambda item: (item.kind, item.label))),
                    singles=tuple(sorted(item.path for item in members if item.path not in taken)),
                )
            )
        return tuple(result)

    def answer_for(self, paths: Iterable[str], value: str) -> CleanupInquiry:
        """Answer the same way for several documents, e.g. a whole group.

        It is one answer per document, recorded as such; nothing is answered for a
        document that was not named, and if any one cannot be answered none is.
        """

        wanted = [_normalise(path) for path in paths]
        known = {item.path for item in self.candidates}
        for path in wanted:
            if path not in known:
                raise CanonCleanupError(f"질문 대상이 아닌 문서입니다: {path}")
        return self.answer({self.question_id(path): value for path in wanted})

    def answer(self, answers: Mapping[str, str]) -> CleanupInquiry:
        """Take the person's answers and return a new revision. Changes nothing else."""

        if not isinstance(answers, Mapping):
            raise CanonCleanupError("answers는 mapping이어야 합니다.")
        by_id = {item.id: item for item in self.questions}
        given = list(self.answers)
        seen = {item.question_id for item in given}
        updated = dict(by_id)

        for question_id, raw in answers.items():
            if not isinstance(question_id, str) or question_id not in by_id:
                raise CanonCleanupError(f"알 수 없는 정리 질문입니다: {question_id}")
            if not isinstance(raw, str) or not raw.strip():
                raise CanonCleanupError(f"질문 답이 비어 있습니다: {question_id}")
            if question_id in seen:
                raise CanonCleanupError(f"이미 답변된 정리 질문입니다: {question_id}")
            value = raw.strip().upper()
            if value not in _ANSWERS:
                raise CanonCleanupError(f"답은 {' 또는 '.join(_ANSWERS)} 이어야 합니다: {raw.strip()}")
            updated[question_id] = replace(by_id[question_id], status="ANSWERED", answer=value)
            given.append(
                UserAnswer(
                    question_id=question_id,
                    answer=value,
                    source="user",
                    created_at=f"canon-cleanup:{self.project_id}:revision:{self.revision + 1}",
                )
            )
            seen.add(question_id)

        return replace(
            self,
            questions=tuple(updated[item.id] for item in self.questions),
            answers=tuple(given),
            revision=self.revision + (1 if answers else 0),
        )

    @property
    def plan(self) -> CleanupPlan:
        """What the answers so far come to.

        Answered OLD: moved if nothing reads it, otherwise marked where it is.
        Answered CURRENT: left alone. Unanswered: moved only if it was proposed,
        otherwise still waiting for the person.
        """

        answered = {item.question_id: item.answer for item in self.answers}
        old: dict[str, CleanupCandidate] = {}
        waiting: list[str] = []
        for candidate in self.candidates:
            answer = answered.get(self.question_id(candidate.path))
            if answer == ANSWER_OLD or (answer is None and candidate.proposed):
                old[candidate.path] = candidate
            elif answer is None:
                waiting.append(candidate.path)

        # A path written relative to the file that wrote it is not rewritten, so it
        # survives a move only if that file moves into the same new folder with it.
        # Anything else is left where it is, and marked, rather than broken. A bare
        # name in a sentence names the file wherever it is, so it blocks nothing.
        # The reasons are written into the project's own files, so the paths in them are the
        # project's (without the folder a zip added): the same answers make the same words.
        def inside(path: str) -> str:
            return path.removeprefix(self.wrapper)

        reasons: dict[str, str] = {}
        taken = set(self.observed_paths)
        while True:
            moving = {path for path, item in old.items() if not item.read_by_code and path not in reasons}
            stranded: dict[str, str] = {}
            for path in sorted(moving):
                if _destination(path) in taken:
                    stranded[path] = f"옮길 자리({inside(_destination(path))})에 이미 파일이 있습니다."
                    continue
                for mention in old[path].mentions:
                    if mention.relative and not (
                        mention.file in moving and _folder(mention.file) == _folder(path)
                    ):
                        stranded[path] = (
                            f"{inside(mention.file)}이(가) 상대 경로로 가리켜서 옮기면 끊어집니다. "
                            "그 파일을 고치기 전에는 제자리에 표시만 합니다."
                        )
                        break
            if not stranded:
                break
            reasons.update(stranded)

        move = tuple(sorted(moving))
        in_place = tuple(sorted(path for path, item in old.items() if path not in moving))
        rewrites = []
        for path in move:
            written_old = path.removeprefix(self.wrapper)
            written_new = _destination(written_old)
            for mention in old[path].mentions:
                if mention.path and mention.file != path:
                    rewrites.append(
                        PathRewrite(file=mention.file, old=written_old, new=written_new, count=mention.path)
                    )
        rewrites.sort(key=lambda item: (item.file, item.old))
        return CleanupPlan(
            move=move,
            in_place=in_place,
            human_check=tuple(sorted(waiting)),
            rewrites=tuple(rewrites),
            reasons=reasons,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "archive_sha256": self.archive_sha256,
            "revision": self.revision,
            "candidates": [item.to_record() for item in self.candidates],
            "sections": [item.to_record() for item in self.sections],
            "questions": [item.to_record() for item in self.questions],
            "answers": [item.to_record() for item in self.answers],
            "open_question_ids": list(self.open_question_ids),
            "progress": {
                "answered": len(self.questions) - len(self.open_question_ids),
                "total": len(self.questions),
            },
            "plan": self.plan.to_record(),
            "canonical_mutation": False,
            "filesystem_scan": False,
            "workspace_write": False,
        }


def propose_cleanup(
    report: BundleReport,
    *,
    also: Iterable[str] = (),
    stale_after_days: int = STALE_AFTER_DAYS,
    project_id: str | None = None,
) -> CleanupInquiry:
    """The documents of one inspected archive worth asking the person about.

    ``also`` names documents the person has already pointed at; they are asked
    about whether or not their names carry a date.
    """

    if not isinstance(report, BundleReport):
        raise CanonCleanupError("정리 입력은 BundleReport이어야 합니다.")
    if not report.safe:
        raise CanonCleanupError("안전 검사를 통과하지 못한 bundle은 정리할 수 없습니다.")
    if isinstance(stale_after_days, bool) or not isinstance(stale_after_days, int) or stale_after_days < 0:
        raise CanonCleanupError("stale_after_days는 0 이상의 정수여야 합니다.")

    observed = tuple(sorted(entry.path for entry in report.entries))
    wrapper = archive_wrapper(observed)

    def inside(path: str) -> str:
        return path.removeprefix(wrapper)

    named = []
    for raw in also:
        if not isinstance(raw, str) or not raw.strip():
            raise CanonCleanupError("지목한 문서 경로가 비어 있습니다.")
        path = _normalise(raw)
        if path not in observed or not _is_document(path):
            raise CanonCleanupError(f"지목한 문서는 관찰된 프로젝트 문서여야 합니다: {raw.strip()}")
        named.append(path)

    entries = {entry.path: entry for entry in report.entries}
    front = tuple(path for path in observed if "/" not in inside(path) and _is_document(path))
    front_mentions = {ref.target: [] for file in front for ref in entries[file].references}
    for file in front:
        for ref in entries[file].references:
            front_mentions[ref.target].append(file)
    mentioned_by: dict[str, list[CleanupMention]] = {}
    for entry in report.entries:
        for ref in entry.references:
            mentioned_by.setdefault(ref.target, []).append(
                CleanupMention(
                    file=entry.path,
                    path=ref.path_mentions,
                    relative=ref.relative_mentions,
                    name=ref.name_mentions,
                    by_code=not _is_document(entry.path),
                )
            )

    dates = {path: _name_date(path) for path in observed if _is_document(path)}
    newest = max((value for value in dates.values() if value is not None), default=None)
    pool = {
        path
        for path, value in dates.items()
        if value is not None
        and newest is not None
        and "/" in inside(path)
        and OLD_FOLDER not in inside(path).split("/")[:-1]  # already set aside
        and (newest - value).days >= stale_after_days
        and path not in front_mentions
    }
    chosen = sorted(pool | set(named))

    candidates = []
    for path in chosen:
        value = dates.get(path)
        candidates.append(
            CleanupCandidate(
                path=path,
                dated=value.isoformat() if value is not None else None,
                age_days=(newest - value).days if value is not None and newest is not None else None,
                mentions=tuple(sorted(mentioned_by.get(path, ()), key=lambda item: item.file)),
                front_door=tuple(sorted(front_mentions.get(path, ()))),
                replaced_by=entries[path].superseded_by,
                named_by_person=path in named,
            )
        )

    project = project_id or f"project:{report.archive_sha256}"
    questions = tuple(
        OpenQuestion(
            id=f"cleanup-question:{report.archive_sha256}:{candidate.path}",
            question=(
                f"'{candidate.path}'은(는) 옛 문서입니까? "
                f"(OLD = 옛 문서, CURRENT = 지금도 쓰는 문서)"
            ),
            why_needed=_why_needed(candidate),
            affected_nodes=(f"bundle:{report.archive_sha256}",),
            suggestions=_ANSWERS,
            blocking=False,
        )
        for candidate in candidates
    )
    return CleanupInquiry(
        project_id=project,
        archive_sha256=report.archive_sha256,
        wrapper=wrapper,
        observed_paths=observed,
        candidates=tuple(candidates),
        questions=questions,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask which documents of a ZIP project are old")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--answers", type=Path, help="question_id -> OLD | CURRENT JSON object")
    parser.add_argument("--also", action="append", default=[], help="a document you already know is old")
    args = parser.parse_args(argv)
    try:
        # The answers file is read first: a missing or broken one is refused before the ZIP is opened.
        answers = None if args.answers is None else json.loads(args.answers.read_text(encoding="utf-8-sig"))
        inquiry = propose_cleanup(inspect_bundle(args.archive), also=args.also)
        if args.answers is not None:
            inquiry = inquiry.answer(answers)
    except (OSError, ValueError, RecursionError) as exc:  # a file nested past the parser's depth is a bad file, not a crash
        print(f"canon-cleanup: {exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(inquiry.to_record(), ensure_ascii=False, indent=2), flush=True)
    return 0


__all__ = [
    "ANSWER_CURRENT",
    "ANSWER_OLD",
    "CLEANUP_SCHEMA",
    "GROUP_SAME_MENTIONERS",
    "GROUP_SIBLINGS",
    "OLD_FOLDER",
    "SECTION_MENTIONED_ONLY_BY_CANDIDATES",
    "SECTION_MENTIONED_OUTSIDE",
    "SECTION_READ_BY_CODE",
    "SECTION_REPLACED",
    "SECTION_UNMENTIONED",
    "STALE_AFTER_DAYS",
    "CanonCleanupError",
    "CleanupCandidate",
    "CleanupGroup",
    "CleanupInquiry",
    "CleanupMention",
    "CleanupPlan",
    "CleanupSection",
    "PathRewrite",
    "destination_of",
    "main",
    "propose_cleanup",
]

if __name__ == "__main__":
    raise SystemExit(main())
