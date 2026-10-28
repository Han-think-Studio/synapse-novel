"""Say whether a project's structure is settled enough to begin work. Decide nothing.

A project is not ready to be worked on while it is still unclear which file decides
what, or while its files disagree with each other. Each of those is a point somebody
has to answer, and an answer that was never given -- or was given to a project that has
since changed -- is not an answer. This gathers every such point in one place and says,
in one word, how far along they are.

The words are the ones ``project_evidence_binding`` already uses for how far a set of
evidence has come, in the same order of weight. Here they mean:

* ``WAITING``      a point has not been answered yet;
* ``UNRESOLVED``   it was answered "I do not know" or "it is yet to be made";
* ``STALE``        it was answered, but for a project or a question that is not this one;
* ``READY_FOR_HUMAN_REVIEW``  nothing is waiting on the person.

``BLOCKED`` is that module's word for a check that came back failed. Nothing here fails
a check -- it only counts what has not been answered -- so it is never said. And
READY_FOR_HUMAN_REVIEW means no more than its name: it is not approval, and it
permits nothing to run.

A point may be passed over with open eyes. For that one point -- never for a group, never
for "everything" -- the person says "I know this is still open; go on", in their own
words if they like. It does not close the point: it stays listed as open, with who passed
it over and what they said, and it stops holding the others back. It holds only for the
point exactly as it was put to them; if the project or the question changes, it lapses.

What counts: which file decides each kind of fact, which files restate it, and every
place the files disagree with each other. What does not: tidying old documents, which is
hygiene rather than canon; and the copies that are still unmatched, which fall only
when a check is written and are therefore reported, not required.

It reads nothing and writes nothing. It is given the report of an archive that has
already been inspected, and the answers the person has given so far. Whether starting
work is actually held back until this is READY is a separate decision; this only tells.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from synapse.core import canon_evidence
from synapse.core.bundle import BundleReport, inspect_bundle
from synapse.core.canon_idea import report_of_idea, table_of_idea
from synapse.core.canon_inquiry import CanonInquiry, canon_inquiry_for_bundle
from synapse.core.canon_mismatch import MISMATCH_ID_PREFIX, find_mismatches
from synapse.core.canon_table import MAX_WORDS, ConfirmationItem, ConfirmationTable
from synapse.core.idea_session import canonical_hash
from synapse.core.preset_pipeline import preset_ids
from synapse.core.scaffold import build_scaffold_plan

STRUCTURE_GATE_SCHEMA = "canon-gate.v1"

WAITING = "WAITING"
UNRESOLVED = "UNRESOLVED"
STALE = "STALE"
READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"

# Heaviest first: what is said when more than one of them is true.
_WEIGHT = (UNRESOLVED, STALE, WAITING)

# Where a point came from.
OWNER_QUESTION = "canon_question"
COPY_QUESTION = "copy_question"
MISMATCH = "mismatch"
TABLE_ROW = "table_row"

# What an open point leaves unknown, said once per kind of point. It is the same text every time, so it is not
# part of what an acknowledgement is bound to: wording it better never lapses an answer.
SETTLES = {
    OWNER_QUESTION: "which file decides this; until then two files that disagree about it cannot be told apart as the canon and a copy",
    COPY_QUESTION: "which other files restate it; until then those copies are not compared with the file that decides it",
    MISMATCH: "what the project does about a reference that points at no document, or at different ones; until then that reference is not to be trusted",
    TABLE_ROW: "a row the confirmation table already holds; it is settled when the owner's answer to it is on record",
}


class StructureGateError(ValueError):
    """Raised when the points or an acknowledgement cannot be taken as given."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Acknowledgement:
    """A person saying, of one point: I know this is still open; go on."""

    item_id: str
    # The point exactly as it was put to them. A changed point is another point.
    basis: str
    # Their words, exactly as given.
    words: str
    revision: int

    def to_record(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "words": self.words, "revision": self.revision}


@dataclass(frozen=True, slots=True, kw_only=True)
class GateItem:
    """One point to be answered, and where it stands."""

    id: str
    source: str
    question: str
    basis: str
    # None: settled. Otherwise WAITING, UNRESOLVED or STALE.
    state: str | None
    acknowledgement: Acknowledgement | None = None
    # An acknowledgement that was given for this point as it used to be.
    lapsed: Acknowledgement | None = None

    @property
    def settles(self) -> str:
        """What answering this point would settle, and so what stays unknown while it is open."""

        return SETTLES[self.source]

    @property
    def is_open(self) -> bool:
        return self.state is not None

    @property
    def blocks(self) -> bool:
        """Open, and nobody has said to go on regardless."""

        return self.state is not None and self.acknowledgement is None

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "question": self.question,
            "settles": self.settles,
            "state": self.state,
            "acknowledged": self.acknowledgement is not None,
            "words": None if self.acknowledgement is None else self.acknowledgement.words,
            "acknowledgement_lapsed": self.lapsed is not None,
        }


def _basis(item_id: str, content: Any) -> str:
    return canonical_hash({"id": item_id, "content": content})


def _fact(question_id: str) -> str:
    return question_id.rsplit(":", 1)[-1]


def _question_items(
    report: BundleReport, inquiry: CanonInquiry | None, preset_id: str | None
) -> tuple[list[GateItem], int, str]:
    """The owner and copy questions, how many copies are still not matched, and the project's id."""

    if inquiry is None:
        if preset_id is None:
            raise StructureGateError("프리셋(preset)이 필요합니다: 고정 프리셋 중 하나를 고르세요.")
        bound, answered_before = canon_inquiry_for_bundle(report, preset_id=preset_id), frozenset()
    elif inquiry.archive_sha256 == report.archive_sha256:
        bound, answered_before = inquiry, frozenset()
    else:
        # Its answers were given to another version of the project. They are not this
        # project's, but they were answers, and the person should be told so.
        bound = canon_inquiry_for_bundle(report, preset_id=inquiry.preset_id, language=inquiry.language)
        answered_before = frozenset(_fact(item.id) for item in inquiry.questions if item.status == "ANSWERED")

    items = []
    for question in bound.questions:
        if question.status == "ANSWERED":
            state = None
        else:
            state = STALE if _fact(question.id) in answered_before else WAITING
        items.append(
            GateItem(
                id=question.id,
                source=OWNER_QUESTION,
                question=question.question,
                basis=_basis(question.id, ""),
                state=state,
            )
        )
    for question in bound.copy_questions:
        items.append(
            GateItem(
                id=question.id,
                source=COPY_QUESTION,
                question=question.question,
                basis=_basis(question.id, ""),
                state=None if question.status == "ANSWERED" else WAITING,
            )
        )
    return items, bound.canon.pending_count, bound.project_id


def _row_item(
    source: str, current: ConfirmationItem, given: ConfirmationItem | None, table: ConfirmationTable | None
) -> GateItem:
    if table is None or table.current(current.id) is None:  # no row, or a row nobody answered
        state: str | None = WAITING
    elif given != current:
        state = STALE  # it was answered, but not this question
    elif current.id in table.open_item_ids:
        state = UNRESOLVED
    else:
        state = None
    return GateItem(
        id=current.id,
        source=source,
        question=current.question,
        basis=_basis(current.id, current.to_record()),
        state=state,
    )


def _table_items(
    report: BundleReport, project_id: str, table: ConfirmationTable | None
) -> tuple[list[GateItem], tuple[str, ...]]:
    """A row for every disagreement the files show now, and every other row the table holds."""

    shown = find_mismatches(report).to_table(project_id).items
    wanted = {item.id for item in shown}
    held = {} if table is None else {item.id: item for item in table.items}
    items = [_row_item(MISMATCH, item, held.get(item.id), table) for item in shown]
    retired = []
    if table is not None:
        for row in table.items:
            if row.id in wanted:
                continue
            if row.id.startswith(MISMATCH_ID_PREFIX):
                retired.append(row.id)  # about a disagreement the files no longer show
            else:
                items.append(_row_item(TABLE_ROW, row, row, table))
    return items, tuple(retired)


def _attach(items: Sequence[GateItem], acknowledged: Sequence[Acknowledgement]) -> tuple[GateItem, ...]:
    """Match the acknowledgements given so far to the points as they stand now."""

    latest: dict[str, Acknowledgement] = {}
    for ack in acknowledged:
        if ack.item_id not in latest or ack.revision >= latest[ack.item_id].revision:
            latest[ack.item_id] = ack
    result = []
    for item in items:
        ack = latest.get(item.id) if item.is_open else None
        valid = ack if ack is not None and ack.basis == item.basis else None
        result.append(
            replace(item, acknowledgement=valid, lapsed=ack if ack is not None and valid is None else None)
        )
    return tuple(result)


@dataclass(frozen=True, slots=True, kw_only=True)
class StructureReview:
    """Every point that has to be settled before work begins, and where each stands."""

    project_id: str
    archive_sha256: str
    items: tuple[GateItem, ...]
    # Everything ever acknowledged, including what no longer holds.
    acknowledged: tuple[Acknowledgement, ...] = ()
    # Rows of the table about disagreements the files no longer show.
    retired: tuple[str, ...] = ()
    pending_copies: int = 0
    revision: int = 0
    schema_version: str = STRUCTURE_GATE_SCHEMA

    @property
    def blocking(self) -> tuple[GateItem, ...]:
        return tuple(item for item in self.items if item.blocks)

    @property
    def status(self) -> str:
        states = {item.state for item in self.blocking}
        for word in _WEIGHT:
            if word in states:
                return word
        return READY_FOR_HUMAN_REVIEW

    @property
    def ready(self) -> bool:
        return self.status == READY_FOR_HUMAN_REVIEW

    @property
    def counts(self) -> dict[str, int]:
        waiting_on = Counter(item.state for item in self.blocking)
        return {
            "items": len(self.items),
            "settled": sum(not item.is_open for item in self.items),
            "acknowledged": sum(item.is_open and not item.blocks for item in self.items),
            "waiting": waiting_on[WAITING],
            "unresolved": waiting_on[UNRESOLVED],
            "stale": waiting_on[STALE],
        }

    @property
    def unmatched_acknowledgements(self) -> tuple[str, ...]:
        """Acknowledged points that are no longer among the points of this project."""

        known = {item.id for item in self.items}
        return tuple(sorted({ack.item_id for ack in self.acknowledged} - known))

    def acknowledge(self, points: Mapping[str, str]) -> StructureReview:
        """Pass over the named points, each with the person's own words, and return a new revision.

        Only a point that is open can be passed over, only by name, and only once.
        """

        if not isinstance(points, Mapping):
            raise StructureGateError("알고 넘길 항목은 mapping이어야 합니다.")
        by_id = {item.id: item for item in self.items}
        given = list(self.acknowledged)
        revision = self.revision + 1
        for item_id, words in points.items():
            item = by_id.get(item_id) if isinstance(item_id, str) else None
            if item is None:
                raise StructureGateError(f"알 수 없는 항목입니다: {item_id}")
            if not item.is_open:
                raise StructureGateError(f"이미 답이 있는 항목은 알고 넘길 필요가 없습니다: {item_id}")
            if item.acknowledgement is not None:
                raise StructureGateError(f"이미 알고 넘긴 항목입니다: {item_id}")
            if not isinstance(words, str):
                raise StructureGateError("원문은 문자열이어야 합니다.")
            if len(words) > MAX_WORDS:
                raise StructureGateError(f"원문이 너무 깁니다(최대 {MAX_WORDS}자).")
            given.append(Acknowledgement(item_id=item_id, basis=item.basis, words=words, revision=revision))
        if not points:
            return self
        return replace(self, items=_attach(self.items, given), acknowledged=tuple(given), revision=revision)

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "archive_sha256": self.archive_sha256,
            "revision": self.revision,
            "status": self.status,
            "ready_for_human_review": self.ready,
            "counts": self.counts,
            "items": [item.to_record() for item in self.items],
            "blocking_item_ids": [item.id for item in self.blocking],
            "acknowledgements": [ack.to_record() for ack in self.acknowledged],
            "unmatched_acknowledgements": list(self.unmatched_acknowledgements),
            "retired_table_rows": list(self.retired),
            "pending_copies": self.pending_copies,
            "approval_recorded": False,
            "execution_allowed": False,
            "model_invoked": False,
            "canonical_mutation": False,
            "filesystem_scan": False,
        }
        record["gate_hash"] = canonical_hash(record)
        return record


def review_structure(
    report: BundleReport,
    *,
    inquiry: CanonInquiry | None = None,
    table: ConfirmationTable | None = None,
    acknowledged: Sequence[Acknowledgement] = (),
    lapsed: Collection[str] = (),
    preset_id: str | None = None,
) -> StructureReview:
    """Every point of one inspected archive that has to be settled, set against the answers so far.

    ``inquiry`` is the canon inquiry being answered, ``table`` the confirmation table of
    disagreements, ``acknowledged`` what was passed over before. Any of them may belong to
    an earlier version of the project; that is reported as STALE, not hidden. The points
    themselves always come from ``report``, so a point nobody was ever asked about is
    WAITING rather than missing.

    ``lapsed`` names points (by their id today) that were answered once, for the project or
    the question as it used to be. Such a point is STALE rather than WAITING: it has an
    earlier answer that no longer applies, which the person should see before answering again.
    """

    if not isinstance(report, BundleReport):
        raise TypeError("구조 점검 입력은 BundleReport이어야 합니다.")
    if inquiry is not None and not isinstance(inquiry, CanonInquiry):
        raise TypeError("inquiry는 CanonInquiry이어야 합니다.")
    if table is not None and not isinstance(table, ConfirmationTable):
        raise TypeError("table은 ConfirmationTable이어야 합니다.")
    acknowledged = tuple(acknowledged)
    if any(not isinstance(ack, Acknowledgement) for ack in acknowledged):
        raise TypeError("acknowledged는 Acknowledgement의 묶음이어야 합니다.")

    questions, pending, project_id = _question_items(report, inquiry, preset_id)
    rows, retired = _table_items(report, project_id, table)
    items = [*questions, *rows]
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise StructureGateError("확인표의 항목 id가 정본 질문이나 다른 항목과 겹칩니다.")
    if isinstance(lapsed, str):
        raise TypeError("lapsed는 항목 id의 묶음이어야 합니다.")
    earlier = frozenset(lapsed)
    items = [replace(item, state=STALE) if item.id in earlier and item.state == WAITING else item for item in items]
    return StructureReview(
        project_id=project_id,
        archive_sha256=report.archive_sha256,
        items=_attach(items, acknowledged),
        acknowledged=acknowledged,
        retired=retired,
        pending_copies=pending,
    )


_REPLAY_KEYS = frozenset({"answers", "copy_answers", "table_answers", "acknowledge", "also", "cleanup_answers"})


@dataclass(frozen=True, slots=True)
class GateRun:
    """Where a project stands once the answers given were taken."""

    # What the command prints: the review, and beside it what lapsed and the old-document questions.
    record: dict[str, Any]
    ready: bool
    # The answers so far as a record to keep, when it was asked for; the caller holds it.
    handoff: dict[str, Any] | None = None
    # What was asked and what is on the table, with the files offered for each question: for a
    # caller that shows them. The command prints none of this, only ``record``.
    inquiry: CanonInquiry | None = None
    table: ConfirmationTable | None = None


def review_project(
    *,
    archive: str | Path | None = None,
    idea: str | None = None,
    preset: str | None = None,
    replay: Mapping[str, Any] | None = None,
    resume: str | Path | Mapping[str, Any] | None = None,
    save: str | Path | None = None,
    update: bool = False,
    want_handoff: bool = False,
) -> GateRun:
    """Take the answers in ``replay`` for one project and say where it stands.

    The project is a ZIP (``archive``) or a new idea (``idea``, its text), not both. ``replay``
    is a mapping with any of the keys the command line's ``--replay`` takes. ``resume`` is a
    record to carry on from: a path, or the record itself held in memory. ``save`` writes the
    record to a path; ``want_handoff`` hands it back instead, so the caller keeps it. What can
    be refused without the project is refused before it is opened (about half a minute).
    """

    from synapse.core.canon_cleanup import propose_cleanup
    from synapse.core.canon_handoff import (
        handoff_document,
        load_handoff,
        resume_handoff,
        save_handoff,
    )

    replay = {} if replay is None else replay
    if not isinstance(replay, Mapping) or set(replay) - _REPLAY_KEYS:
        raise StructureGateError(f"replay(--replay)는 {sorted(_REPLAY_KEYS)} 중 일부를 가진 JSON 객체여야 합니다.")
    if update and save is None:
        raise StructureGateError("update(--update)는 save(--save)와 함께 써야 합니다.")
    if (archive is None) == (idea is None):
        raise StructureGateError("ZIP 프로젝트(archive) 하나 또는 새 아이디어(idea, --idea-file) 하나를 주세요(둘 다, 또는 둘 다 없이는 안 됩니다).")
    if not isinstance(preset, str) or not preset.strip():
        raise StructureGateError("preset(--preset)이 필요합니다: 고정 프리셋 중 하나를 시작할 때 고르세요(기본값 없음).")
    preset = preset.strip()
    plan = None
    if idea is None:
        report = inspect_bundle(archive)
    else:
        plan = build_scaffold_plan(idea, preset_id=preset)
        report = report_of_idea(plan)
    if resume is None:
        resumed = None
    elif isinstance(resume, Mapping):
        resumed = resume_handoff(resume, report)
    else:
        resumed = load_handoff(resume, report)
    if resumed is not None and resumed.inquiry.preset_id != preset:
        raise StructureGateError(
            f"이어받는 기록의 프리셋({resumed.inquiry.preset_id})이 지금 고른 프리셋({preset})과 다릅니다."
        )
    if resumed is None:
        inquiry = canon_inquiry_for_bundle(report, preset_id=preset)
        table = (
            find_mismatches(report).to_table(inquiry.project_id)
            if plan is None
            else table_of_idea(plan, inquiry.project_id)
        )
        acknowledged: tuple[Acknowledgement, ...] = ()
        lapsed_ids: frozenset[str] = frozenset()
    else:
        inquiry, table, acknowledged, lapsed_ids = (
            resumed.inquiry, resumed.table, resumed.acknowledged, resumed.lapsed_ids()
        )
    if "answers" in replay:
        inquiry = inquiry.answer(replay["answers"])
    if "copy_answers" in replay:
        inquiry = inquiry.answer_copies(replay["copy_answers"])
    if "table_answers" in replay:
        table = table.answer(replay["table_answers"])
    cleanup = None if resumed is None else resumed.cleanup
    if "also" in replay:
        pointed = replay["also"]
        if not isinstance(pointed, list):
            raise StructureGateError("also는 문서 경로의 목록이어야 합니다.")
        if cleanup is not None and cleanup.answers:
            raise StructureGateError("정리 질문에 이미 답이 있어 지목한 문서를 더할 수 없습니다. 새 기록에서 시작해 주세요.")
        earlier = [] if cleanup is None else [item.path for item in cleanup.candidates if item.named_by_person]
        cleanup = propose_cleanup(report, also=[*earlier, *pointed], project_id=inquiry.project_id)
    if "cleanup_answers" in replay:
        if cleanup is None:
            cleanup = propose_cleanup(report, project_id=inquiry.project_id)
        cleanup = cleanup.answer(replay["cleanup_answers"])
    review = review_structure(report, inquiry=inquiry, table=table, acknowledged=acknowledged, lapsed=lapsed_ids)
    if "acknowledge" in replay:
        review = review.acknowledge(replay["acknowledge"])
    kept = {
        "report": report,
        "inquiry": inquiry,
        "table": table,
        "cleanup": cleanup,
        "acknowledged": review.acknowledged,
        "lapsed": () if resumed is None else resumed.lapsed,
    }
    if save is not None:
        save_handoff(save, **kept, update=update)
    handoff = handoff_document(**kept) if want_handoff else None
    record = review.to_record()
    if resumed is not None:
        # Beside the review, not in it: what the person said before and no longer applies.
        record["lapsed_answers"] = [item.to_record() for item in resumed.lapsed]
    if cleanup is not None:
        # Also beside it: the old-document questions and the plan the answers so far come to.
        record["cleanup"] = cleanup.to_record()
    return GateRun(record=record, ready=review.ready, handoff=handoff, inquiry=inquiry, table=table)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m synapse.core.canon_gate",
        description="Say whether a project's structure is settled enough to begin work: a ZIP project, or a new idea",
    )
    parser.add_argument("archive", type=Path, nargs="?", help="a ZIP project (or --idea-file, not both)")
    parser.add_argument(
        "--idea-file",
        type=Path,
        help="a new idea instead of a ZIP: a UTF-8 text file; it is asked about as the project it would start",
    )
    parser.add_argument("--preset", required=True, choices=preset_ids(), help="the fixed preset this project is read as (no default)")
    parser.add_argument(
        "--replay",
        type=Path,
        help=(
            "JSON object with any of: answers, copy_answers, table_answers, acknowledge (each as the module that takes it does); "
            "also (documents you already know are old) and cleanup_answers (question id -> OLD | CURRENT) for the old-document questions"
        ),
    )
    parser.add_argument("--resume", type=Path, help="a handoff record to carry on from (what lapsed is shown as STALE)")
    parser.add_argument("--save", type=Path, help="write the handoff record here, after --replay is applied")
    parser.add_argument(
        "--update", action="store_true", help="let --save extend a record that is already there (never overwrite one it does not extend)"
    )
    args = parser.parse_args(argv)
    read_hashes: dict[str, str] = {}

    def read_input(path: Path, role: str) -> bytes:
        data = path.read_bytes()
        read_hashes[f"{role}:{path.name}"] = f"sha256:{hashlib.sha256(data).hexdigest()}"
        return data

    def decode_input(data: bytes) -> str:
        # Match Path.read_text() universal-newline handling after hashing the original bytes.
        return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")

    try:
        # Hash each input from the same bytes used to interpret it.
        replay = (
            None
            if args.replay is None
            else json.loads(decode_input(read_input(args.replay, "replay")))
        )
        idea = (
            None
            if args.idea_file is None
            else decode_input(read_input(args.idea_file, "idea"))
        )
        resume = None
        if args.resume is not None:
            from synapse.core.canon_handoff import handoff_from_bytes, read_handoff_bytes

            # The handoff module reads its own records; the bytes it read are the ones hashed.
            resume_path = args.resume.expanduser().resolve(strict=False)
            resume_bytes = read_handoff_bytes(resume_path)
            read_hashes[f"resume:{resume_path.name}"] = f"sha256:{hashlib.sha256(resume_bytes).hexdigest()}"
            resume = handoff_from_bytes(resume_bytes, resume_path)
        run = review_project(
            archive=args.archive,
            idea=idea,
            preset=args.preset,
            replay=replay,
            resume=resume,
            save=args.save,
            update=args.update,
        )
        if args.archive is not None:
            assert run.inquiry is not None
            read_hashes[f"archive:{args.archive.name}"] = f"sha256:{run.inquiry.archive_sha256}"
        evidence = canon_evidence.read(read_hashes)
    except (OSError, ValueError, RecursionError) as exc:  # a file nested past the parser's depth is a bad file, not a crash
        print(f"canon-gate: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        json.dumps(
            {**run.record, "evidence": evidence},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0 if run.ready else 1


__all__ = [
    "COPY_QUESTION",
    "MISMATCH",
    "OWNER_QUESTION",
    "READY_FOR_HUMAN_REVIEW",
    "SETTLES",
    "STALE",
    "STRUCTURE_GATE_SCHEMA",
    "TABLE_ROW",
    "UNRESOLVED",
    "WAITING",
    "Acknowledgement",
    "GateItem",
    "GateRun",
    "StructureGateError",
    "StructureReview",
    "main",
    "review_project",
    "review_structure",
]

if __name__ == "__main__":
    raise SystemExit(main())
