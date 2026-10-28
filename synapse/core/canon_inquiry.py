"""Establish a project's canon by asking the person who owns it.

A new idea gets a canon proposed from its layout, because the layout already
says which file is for what. A project that was already underway has no such
layout, and nothing observed about it can say which file decides a fact: the
same requirement can sit in a README, a notes file and a chat log, and the
wording repeated most often starts to look like the truth. Only the person who
made the project knows which one they meant.

So this asks. For each kind of fact a project keeps -- its goals, its
constraints, its next work -- it asks which file decides it, offers the files
most likely to be the answer, and takes the person's word. Their answers, and
nothing else, become the canon.

It follows the rules the existing reconstruction questions follow:

* an answer must be a path that was actually observed, so a caller cannot name
  a file that is not in the project;
* a question is answered once;
* an answer produces a new immutable revision and is never Canonical State --
  the map it yields is a proposal like every other, confirmed through the normal
  Proposal boundary or not at all.

It reads nothing itself. It is given the report of an archive that has already
been inspected, and it lists no directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from synapse.core import preset_pipeline
from synapse.core.bundle import BundleReport, inspect_bundle
from synapse.core.canon import PLAN_OWNER, CanonCopy, CanonFact, CanonMap, CopyKind
from synapse.core.canon_earlier import EarlierDecision, earlier_decisions
from synapse.core.idea_session import OpenQuestion, UserAnswer
from synapse.core.scaffold import build_scaffold_plan

CANON_INQUIRY_SCHEMA = "canon-inquiry.v1"

# The one answer that is not a path: this project has no such thing yet. It is
# an answer, so the question is closed; it does not invent an owner.
NONE_ANSWER = "NONE"

MAX_SUGGESTIONS = 8

LANGUAGES = ("ko", "en", "zh")
DEFAULT_LANGUAGE = "ko"

_TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml")

# Only the order of the files offered to the person depends on these. The answer
# does not: any observed file may be named, whether or not a hint matched it.
_REVIEW_HINTS = ("accept", "review", "검토", "기준", "승인", "checklist")


@lru_cache(maxsize=64)
def _hints(preset_id: str, fact_id: str) -> tuple[str, ...]:
    """Words that suggest a file decides this fact, derived from the preset's own task."""

    _, preset, _ = preset_pipeline.load_preset(preset_id)
    if fact_id == "02_review/acceptance":
        return _REVIEW_HINTS
    task_id = fact_id.removeprefix("01_tasks/")
    task = next((t for t in preset["tasks"] if t["id"] == task_id), None)
    if task is None:
        return ()
    words = {task_id.lower(), *(word.lower() for word in re.split(r"[\s/·,]+", task["title"]) if len(word) >= 2)}
    words.update(name.lower() for name in task["fields"] if len(name) >= 2)
    return tuple(sorted(words))


class CanonInquiryError(ValueError):
    """Raised when an answer cannot be taken as given."""


_SENTENCES: dict[str, dict[str, str]] = {
    "ko": {
        "owner": "이 프로젝트에서 '{title}'을(를) 정하는 파일은 어느 것입니까? (이 항목: {purpose})",
        "owner_why": (
            "같은 내용을 여러 파일이 적고 있을 때 기준을 정해 두지 않으면, 가장 자주 나오는 표현이 "
            "정본처럼 굳습니다. 답한 파일이 이 내용의 유일한 주인이 됩니다. "
            "해당 내용이 아직 없으면 {none}라고 답하세요."
        ),
        "copy": "'{title}'은(는) {owner}이(가) 정합니다. 같은 내용을 따로 적고 있는 다른 파일이 있다면 어느 것입니까?",
        "copy_why": (
            "다른 파일이 같은 내용을 따로 적고 있으면 시간이 지나며 서로 어긋납니다. 알려 주시면 그 파일을 "
            "'아직 맞추지 못한 사본'으로 기록해 두고 하나씩 확인할 수 있게 합니다. 없으면 {none}라고 답하세요."
        ),
    },
    "en": {
        "owner": "Which file in this project decides '{title}'? (This item: {purpose})",
        "owner_why": (
            "When several files say the same thing and no file is named as the reference, the most "
            "repeated wording hardens into the truth. The file you name becomes the only owner of this "
            "item. If there is none yet, answer {none}."
        ),
        "copy": "'{title}' is decided by {owner}. Is there another file that restates the same thing, and which?",
        "copy_why": (
            "A file that restates the same thing drifts away from it over time. Name it and it is "
            "recorded as a copy not yet matched, so each can be checked one by one. If there is none, "
            "answer {none}."
        ),
    },
    "zh": {
        "owner": "在这个项目中，决定“{title}”的是哪个文件？（该项内容：{purpose}）",
        "owner_why": (
            "当多个文件写着相同的内容而没有指定依据时，出现最多的说法会被当成事实。您指定的文件将成为"
            "这一项的唯一依据。如果还没有这样的内容，请回答 {none}。"
        ),
        "copy": "“{title}”由 {owner} 决定。是否还有其他文件另外写着相同的内容？是哪一个？",
        "copy_why": (
            "另外写着相同内容的文件会随时间与它产生分歧。请告诉我，它会被记为“尚未对齐的副本”，"
            "之后可以逐一核对。如果没有，请回答 {none}。"
        ),
    },
}


@lru_cache(maxsize=64)
def _categories(preset_id: str) -> tuple[tuple[str, str, str], ...]:
    """(fact id, title, purpose) for the kinds of fact one preset's projects keep.

    Read from the layout the preset derives for a new project, so an existing project and a new
    one of the same preset name their facts identically and their canons can be compared.
    """

    plan = build_scaffold_plan("canon inquiry", preset_id=preset_id)
    titles = {item.path: item.title for item in plan.files}
    return tuple(
        (fact.id, titles[fact.owner], fact.statement)
        for fact in plan.canon.facts
        if fact.owner != PLAN_OWNER
    )


def _facts_in(language: str, preset_id: str) -> tuple[tuple[str, str, str], ...]:
    """(fact id, title, purpose) for one preset. The wording is the preset's own (Korean) in every
    language; only the question sentences are translated."""

    if language not in LANGUAGES:
        raise CanonInquiryError(f"지원하지 않는 언어입니다: {language} (가능: {', '.join(LANGUAGES)})")
    return _categories(preset_id)


def _normalise(value: str) -> str:
    path = value.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _ranked(preset_id: str, fact_id: str, observed: tuple[str, ...]) -> tuple[str, ...]:
    """Every file that looks like it could decide this fact, most likely first."""

    hints = _hints(preset_id, fact_id)
    scored: list[tuple[int, int, str]] = []
    for path in observed:
        if not path.lower().endswith(_TEXT_SUFFIXES):
            continue
        name = path.rsplit("/", 1)[-1].lower()
        score = sum(2 for hint in hints if hint in name) + sum(1 for hint in hints if hint in path.lower())
        scored.append((-score, path.count("/"), path))
    # When no file name says what it decides, the person is still offered the project's own documents.
    chosen = [item for item in scored if item[0]] or [(0, depth, path) for _, depth, path in scored]
    # Equally likely files are told apart by how close they sit to the root, not
    # by the alphabet: a project's own README is at its top, and cutting a tie by
    # path let every `10_execution/...` README crowd it out. Depth is relative, so
    # a wrapping folder moves every path down alike and the order does not change.
    return tuple(path for _, _, path in sorted(chosen))


def _suggest(preset_id: str, fact_id: str, observed: tuple[str, ...]) -> tuple[str, ...]:
    return _ranked(preset_id, fact_id, observed)[:MAX_SUGGESTIONS]


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonInquiry:
    """The questions asked about one project, and what has been answered."""

    project_id: str
    preset_id: str
    archive_sha256: str
    observed_paths: tuple[str, ...]
    questions: tuple[OpenQuestion, ...]
    answers: tuple[UserAnswer, ...] = ()
    copy_answers: tuple[UserAnswer, ...] = ()
    language: str = DEFAULT_LANGUAGE
    # (question id, a decision already written in one of the files offered for it).
    # Shown with the question, not in its wording: the person should see what the
    # candidates already say before choosing which of them decides.
    earlier: tuple[tuple[str, EarlierDecision], ...] = ()
    revision: int = 0
    schema_version: str = CANON_INQUIRY_SCHEMA

    @property
    def open_question_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.questions if item.status != "ANSWERED")

    def earlier_for(self, question_id: str) -> tuple[EarlierDecision, ...]:
        """What the files offered for this question already say was decided."""

        return tuple(decision for owner, decision in self.earlier if owner == question_id)

    @property
    def canon(self) -> CanonMap:
        """The canon these answers yield: one fact per file the person named, and the
        files they said restate it, each a copy not yet matched.

        The wording of a fact is the preset's, whatever language the questions were
        asked in, so the same answers make the same canon (and the same hash).
        """

        detail = {fact_id: purpose for fact_id, _, purpose in _categories(self.preset_id)}
        answered = {item.question_id: item.answer for item in self.answers}
        facts = []
        for question in self.questions:
            answer = answered.get(question.id)
            if answer is None or answer == NONE_ANSWER:
                continue
            fact_id = question.id.rsplit(":", 1)[-1]
            facts.append(CanonFact(fact_id, answer, detail[fact_id], f"answered:{question.id}"))
        copies = [
            CanonCopy(path, item.question_id.rsplit(":", 1)[-1], CopyKind.PENDING, f"answered:{item.question_id}")
            for item in self.copy_answers
            if item.answer != NONE_ANSWER
            for path in item.answer.split("\n")
        ]
        return CanonMap(facts=tuple(facts), copies=tuple(copies))

    def answer(self, answers: Mapping[str, str]) -> CanonInquiry:
        """Take the person's answers and return a new revision. Changes nothing else."""

        if not isinstance(answers, Mapping):
            raise CanonInquiryError("answers는 mapping이어야 합니다.")
        by_id = {item.id: item for item in self.questions}
        given = list(self.answers)
        seen = {item.question_id for item in given}
        observed = set(self.observed_paths)
        updated = dict(by_id)

        for question_id, raw in answers.items():
            if not isinstance(question_id, str) or question_id not in by_id:
                raise CanonInquiryError(f"알 수 없는 정본 질문입니다: {question_id}")
            if not isinstance(raw, str) or not raw.strip():
                raise CanonInquiryError(f"질문 답이 비어 있습니다: {question_id}")
            if question_id in seen:
                raise CanonInquiryError(f"이미 답변된 정본 질문입니다: {question_id}")
            value = raw.strip() if raw.strip() == NONE_ANSWER else _normalise(raw)
            if value != NONE_ANSWER and value not in observed:
                raise CanonInquiryError(
                    f"정본의 주인은 관찰된 프로젝트 경로여야 합니다: {raw.strip()}"
                )
            updated[question_id] = replace(by_id[question_id], status="ANSWERED", answer=value)
            given.append(
                UserAnswer(
                    question_id=question_id,
                    answer=value,
                    source="user",
                    created_at=f"canon-inquiry:{self.project_id}:revision:{self.revision + 1}",
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
    def copy_questions(self) -> tuple[OpenQuestion, ...]:
        """For each fact whose owner is named: which other file says the same thing?

        A file that restates a fact is how a canon drifts, and it is the files
        nobody has mentioned that do it. Asking only becomes possible once there
        is an owner to be a copy of.
        """

        owners = {fact.id: fact.owner for fact in self.canon.facts}
        given = {item.question_id: item.answer for item in self.copy_answers}
        words = {fact_id: title for fact_id, title, _ in _facts_in(self.language, self.preset_id)}
        sentences = _SENTENCES[self.language]
        result = []
        for question in self.questions:
            fact_id = question.id.rsplit(":", 1)[-1]
            owner = owners.get(fact_id)
            if owner is None:
                continue
            question_id = f"canon-copy-question:{self.archive_sha256}:{fact_id}"
            answer = given.get(question_id)
            result.append(
                OpenQuestion(
                    id=question_id,
                    question=sentences["copy"].format(title=words[fact_id], owner=owner),
                    why_needed=sentences["copy_why"].format(none=NONE_ANSWER),
                    affected_nodes=(f"bundle:{self.archive_sha256}",),
                    suggestions=tuple(p for p in _ranked(self.preset_id, fact_id, self.observed_paths) if p != owner)[
                        :MAX_SUGGESTIONS
                    ],
                    blocking=False,
                    answer=answer,
                    status="ANSWERED" if answer else "OPEN",
                )
            )
        return tuple(result)

    @property
    def open_copy_question_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.copy_questions if item.status != "ANSWERED")

    def answer_copies(self, answers: Mapping[str, str | Sequence[str]]) -> CanonInquiry:
        """Take the person's answers about copies and return a new revision.

        An answer is the files that restate the fact, or NONE. Each becomes a copy
        that is not yet matched -- recorded, not judged.
        """

        if not isinstance(answers, Mapping):
            raise CanonInquiryError("answers는 mapping이어야 합니다.")
        askable = {item.id: item for item in self.copy_questions}
        owners = {fact.id: fact.owner for fact in self.canon.facts}
        given = list(self.copy_answers)
        seen = {item.question_id for item in given}
        observed = set(self.observed_paths)

        for question_id, raw in answers.items():
            if not isinstance(question_id, str) or question_id not in askable:
                raise CanonInquiryError(
                    f"사본을 물을 수 없는 질문입니다(주인이 아직 정해지지 않았거나 없는 질문): {question_id}"
                )
            if question_id in seen:
                raise CanonInquiryError(f"이미 답변된 사본 질문입니다: {question_id}")
            values = [raw] if isinstance(raw, str) else list(raw)
            if not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise CanonInquiryError(f"질문 답이 비어 있습니다: {question_id}")
            owner = owners[question_id.rsplit(":", 1)[-1]]
            if any(v.strip() == NONE_ANSWER for v in values):
                if len(values) != 1:
                    raise CanonInquiryError(f"{NONE_ANSWER}는 다른 답과 함께 쓸 수 없습니다: {question_id}")
                value = NONE_ANSWER
            else:
                paths = sorted({_normalise(v) for v in values})
                for path in paths:
                    if path not in observed:
                        raise CanonInquiryError(f"사본은 관찰된 프로젝트 경로여야 합니다: {path}")
                    if path == owner:
                        raise CanonInquiryError(f"정본의 주인은 자기 사본일 수 없습니다: {path}")
                    if "\n" in path:
                        raise CanonInquiryError(f"줄바꿈이 든 경로는 사본으로 기록할 수 없습니다: {path!r}")
                value = "\n".join(paths)
            given.append(
                UserAnswer(
                    question_id=question_id,
                    answer=value,
                    source="user",
                    created_at=f"canon-inquiry:{self.project_id}:revision:{self.revision + 1}",
                )
            )
            seen.add(question_id)

        return replace(self, copy_answers=tuple(given), revision=self.revision + (1 if answers else 0))

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "preset_id": self.preset_id,
            "archive_sha256": self.archive_sha256,
            "revision": self.revision,
            "language": self.language,
            "earlier_decisions": {
                question.id: [decision.to_record() for decision in self.earlier_for(question.id)]
                for question in self.questions
                if self.earlier_for(question.id)
            },
            "questions": [item.to_record() for item in self.questions],
            "answers": [item.to_record() for item in self.answers],
            "open_question_ids": list(self.open_question_ids),
            "progress": {
                "answered": len(self.questions) - len(self.open_question_ids),
                "total": len(self.questions),
            },
            "copy_questions": [item.to_record() for item in self.copy_questions],
            "copy_answers": [item.to_record() for item in self.copy_answers],
            "copy_progress": {
                "answered": len(self.copy_questions) - len(self.open_copy_question_ids),
                "total": len(self.copy_questions),
            },
            "canon": self.canon.to_record(),
            "canonical_mutation": False,
            "filesystem_scan": False,
        }


def canon_inquiry_for_bundle(
    report: BundleReport, *, preset_id: str, project_id: str | None = None, language: str = DEFAULT_LANGUAGE
) -> CanonInquiry:
    """The questions that establish the canon of one inspected archive, for one named preset."""

    facts = _facts_in(language, preset_id)  # refuses a language or preset it does not have
    sentences = _SENTENCES[language]
    observed = tuple(sorted(entry.path for entry in report.entries))
    project = project_id or f"project:{report.archive_sha256}"
    questions = tuple(
        OpenQuestion(
            id=f"canon-question:{report.archive_sha256}:{fact_id}",
            question=sentences["owner"].format(title=title, purpose=purpose),
            why_needed=sentences["owner_why"].format(none=NONE_ANSWER),
            affected_nodes=(f"bundle:{report.archive_sha256}",),
            suggestions=_suggest(preset_id, fact_id, observed),
            blocking=False,
        )
        for fact_id, title, purpose in facts
    )
    return CanonInquiry(
        project_id=project,
        preset_id=preset_id,
        archive_sha256=report.archive_sha256,
        observed_paths=observed,
        questions=questions,
        language=language,
        earlier=tuple(
            (question.id, decision)
            for question in questions
            for decision in earlier_decisions(report, in_files=question.suggestions)
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask which file decides each kind of fact in a ZIP project")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--preset", required=True, choices=preset_pipeline.preset_ids(), help="the fixed preset this project is read as")
    parser.add_argument("--answers", type=Path, help="question_id -> answer JSON object")
    parser.add_argument(
        "--copy-answers", type=Path, help="copy question_id -> a path, a list of paths, or NONE (JSON object)"
    )
    parser.add_argument("--language", choices=LANGUAGES, default=DEFAULT_LANGUAGE, help="language of the questions")
    args = parser.parse_args(argv)
    try:
        # The answer files are read first: a missing or broken one is refused before the ZIP is opened.
        answers = None if args.answers is None else json.loads(args.answers.read_text(encoding="utf-8-sig"))
        copy_answers = None if args.copy_answers is None else json.loads(args.copy_answers.read_text(encoding="utf-8-sig"))
        inquiry = canon_inquiry_for_bundle(inspect_bundle(args.archive), preset_id=args.preset, language=args.language)
        if args.answers is not None:
            inquiry = inquiry.answer(answers)
        if args.copy_answers is not None:
            inquiry = inquiry.answer_copies(copy_answers)
    except (OSError, ValueError, RecursionError) as exc:  # a file nested past the parser's depth is a bad file, not a crash
        print(f"canon-inquiry: {exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(inquiry.to_record(), ensure_ascii=False, indent=2), flush=True)
    return 0


__all__ = [
    "CANON_INQUIRY_SCHEMA",
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "NONE_ANSWER",
    "CanonInquiry",
    "CanonInquiryError",
    "canon_inquiry_for_bundle",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
