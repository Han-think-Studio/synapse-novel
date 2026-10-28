"""Keep what the person has answered, so another process -- or another AI -- can carry on.

Everything about a project's canon that can be worked out from its ZIP is worked out
again each time. What cannot is what the person said: which file decides what, what they
answered about each disagreement and in which words, which open points they knowingly
passed over. Those words are the decisions, and they are the only thing that is lost if
the process ends. This writes them down, in a file the caller names, and reads them back.

It is a record of answers, not of the project: no file body is written, nothing is
cached, nothing is written anywhere the caller did not name, and it is never Canonical State.
It follows the shape the guided session store already has -- an explicit save, a checksum,
and a load that revalidates everything before it is believed.

Three rules make it safe to hand over:

* **Loading never trusts.** An answer is carried into the present only if the question it
  answered is, word for word, the question being asked now. A project that has changed since
  is not the project that was answered about; what was said then is kept, shown as an
  earlier answer, and asked again. Nothing quietly becomes valid because it was typed once.
* **A record only grows.** Saving over an existing record is refused unless it carries
  everything the old one did, in the same order. Two AIs that both continued from the same
  record cannot overwrite each other; the second finds a record that is no longer its ancestor.
* **A damaged record is refused, not repaired.** A changed byte, a wrong shape, a size no
  record has: it is an error in words, and nothing is guessed.

Replaying goes through the same methods the answers were first given with, so every rule
that applied then applies again.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from synapse.core.bundle import BundleReport
from synapse.core.canon_cleanup import CanonCleanupError, CleanupInquiry, propose_cleanup
from synapse.core.canon_gate import Acknowledgement, StructureReview, review_structure
from synapse.core.canon_inquiry import (
    LANGUAGES,
    CanonInquiry,
    CanonInquiryError,
    canon_inquiry_for_bundle,
)
from synapse.core.canon_mismatch import find_mismatches
from synapse.core.canon_table import CanonTableError, ConfirmationItem, ConfirmationTable
from synapse.core.idea_session import canonical_hash
from synapse.core.preset_pipeline import preset_ids

HANDOFF_SCHEMA = "canon-handoff.v2"

# A record of answers is small. A file much larger than any plausible one is not one.
MAX_BYTES = 8 * 1024 * 1024

OWNER_ANSWER = "owner_answer"
COPY_ANSWER = "copy_answer"
TABLE_ROW = "table_row"
CLEANUP_ANSWER = "cleanup_answer"
CLEANUP_POINTER = "cleanup_pointer"

_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "project_id",
        "preset_id",
        "archive_sha256",
        "language",
        "owner_answers",
        "copy_answers",
        "table",
        "cleanup",
        "acknowledgements",
        "lapsed",
        "canonical_mutation",
    }
)


class HandoffError(ValueError):
    """Raised when a record cannot be written, or cannot be believed."""


@dataclass(frozen=True, slots=True, kw_only=True)
class LapsedAnswer:
    """Something the person said once that does not apply to the project as it is now.

    It is kept with their words exactly as given: it was a decision, and it is what they
    should see before they answer again.
    """

    kind: str
    # The point it was about, in a form that survives a change of project: the kind of
    # fact for an owner answer, the row id for a table row, the document for a cleanup answer.
    point: str
    was: str
    answer: str
    words: str
    version: int
    from_archive: str

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "point": self.point,
            "was": self.was,
            "answer": self.answer,
            "words": self.words,
            "version": self.version,
            "from_archive": self.from_archive,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class HandoffReceipt:
    """What was written, and a number that only goes up."""

    path: str
    file_sha256: str
    archive_sha256: str
    # How many answers and acknowledgements the record holds. Never less than before.
    sequence: int

    def to_record(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "file_sha256": self.file_sha256,
            "archive_sha256": self.archive_sha256,
            "sequence": self.sequence,
            "canonical_mutation": False,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Resumed:
    """The state a record comes to for a project as it is today."""

    project_id: str
    archive_sha256: str
    recorded_archive_sha256: str
    inquiry: CanonInquiry
    table: ConfirmationTable
    cleanup: CleanupInquiry | None
    acknowledged: tuple[Acknowledgement, ...]
    lapsed: tuple[LapsedAnswer, ...]

    @property
    def same_archive(self) -> bool:
        return self.archive_sha256 == self.recorded_archive_sha256

    def lapsed_ids(self) -> frozenset[str]:
        """The points of today's project that have an earlier answer that no longer applies."""

        ids = set()
        for item in self.lapsed:
            if item.kind == OWNER_ANSWER:
                ids.add(f"canon-question:{self.archive_sha256}:{item.point}")
            elif item.kind == TABLE_ROW:
                ids.add(item.point)
        return frozenset(ids)

    def review(self, report: BundleReport) -> StructureReview:
        """Where the structure stands, with what lapsed shown as STALE rather than forgotten."""

        return review_structure(
            report,
            inquiry=self.inquiry,
            table=self.table,
            acknowledged=self.acknowledged,
            lapsed=self.lapsed_ids(),
        )


# --- writing ---------------------------------------------------------------


def _revision_of(stamp: str) -> int:
    try:
        return int(stamp.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise HandoffError(f"답의 개정 표지를 읽을 수 없습니다: {stamp}") from exc


def _answer_rows(answers: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {"question_id": item.question_id, "answer": item.answer, "revision": _revision_of(item.created_at)}
        for item in answers
    ]


def _payload(
    report: BundleReport,
    inquiry: CanonInquiry,
    table: ConfirmationTable | None,
    cleanup: CleanupInquiry | None,
    acknowledged: Sequence[Acknowledgement],
    lapsed: Sequence[LapsedAnswer],
) -> dict[str, Any]:
    return {
        "schema_version": HANDOFF_SCHEMA,
        "project_id": inquiry.project_id,
        "preset_id": inquiry.preset_id,
        "archive_sha256": report.archive_sha256,
        "language": inquiry.language,
        "owner_answers": _answer_rows(inquiry.answers),
        "copy_answers": _answer_rows(inquiry.copy_answers),
        "table": None
        if table is None
        else {
            "project_id": table.project_id,
            "owner": table.owner,
            "items": [item.to_record() for item in table.items],
            "answers": [
                {
                    "item_id": answer.item_id,
                    "choice": answer.choice.value,
                    "words": answer.words,
                    "version": answer.version,
                    "revision": answer.revision,
                }
                for answer in table.answers
            ],
        },
        "cleanup": None
        if cleanup is None
        else {
            "project_id": cleanup.project_id,
            "also": [item.path for item in cleanup.candidates if item.named_by_person],
            "answers": _answer_rows(cleanup.answers),
        },
        "acknowledgements": [
            {"item_id": ack.item_id, "basis": ack.basis, "words": ack.words, "revision": ack.revision}
            for ack in acknowledged
        ],
        "lapsed": [item.to_record() for item in lapsed],
        "canonical_mutation": False,
    }


def _sequence(payload: Mapping[str, Any]) -> int:
    table = payload["table"]
    cleanup = payload["cleanup"]
    return (
        len(payload["owner_answers"])
        + len(payload["copy_answers"])
        + (0 if table is None else len(table["answers"]))
        + (0 if cleanup is None else len(cleanup["answers"]))
        + len(payload["acknowledgements"])
    )


def _is_prefix(old: Sequence[Any], new: Sequence[Any]) -> bool:
    return len(old) <= len(new) and list(new[: len(old)]) == list(old)


def _extends(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """Whether ``new`` carries everything ``old`` did, in the same order."""

    if any(old[key] != new[key] for key in ("project_id", "preset_id", "archive_sha256", "language")):
        return False
    if not all(_is_prefix(old[key], new[key]) for key in ("owner_answers", "copy_answers", "acknowledgements", "lapsed")):
        return False
    if old["table"] is not None:
        if new["table"] is None or any(old["table"][key] != new["table"][key] for key in ("project_id", "owner")):
            return False
        if not all(row in new["table"]["items"] for row in old["table"]["items"]):
            return False
        if not _is_prefix(old["table"]["answers"], new["table"]["answers"]):
            return False
    if old["cleanup"] is not None:
        if new["cleanup"] is None or old["cleanup"]["project_id"] != new["cleanup"]["project_id"]:
            return False
        if not set(old["cleanup"]["also"]) <= set(new["cleanup"]["also"]):
            return False
        if not _is_prefix(old["cleanup"]["answers"], new["cleanup"]["answers"]):
            return False
    return True


def _document_bytes(payload: Mapping[str, Any]) -> bytes:
    document = {"payload": payload, "sha256": canonical_hash(payload)}
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def _built(
    report: BundleReport,
    inquiry: CanonInquiry,
    table: ConfirmationTable | None,
    cleanup: CleanupInquiry | None,
    acknowledged: Sequence[Acknowledgement],
    lapsed: Sequence[LapsedAnswer],
) -> tuple[dict[str, Any], bytes]:
    """The record of the answers so far, checked, and the bytes it would be written as."""

    if not isinstance(report, BundleReport):
        raise HandoffError("기록은 검사한 ZIP(BundleReport)을 기준으로만 쓸 수 있습니다.")
    if not isinstance(inquiry, CanonInquiry):
        raise HandoffError("inquiry는 CanonInquiry이어야 합니다.")
    if inquiry.archive_sha256 != report.archive_sha256:
        raise HandoffError("정본 질의가 이 ZIP의 것이 아닙니다. 지금 ZIP에 대한 질의만 저장할 수 있습니다.")
    if table is not None and not isinstance(table, ConfirmationTable):
        raise HandoffError("table은 ConfirmationTable이어야 합니다.")
    if cleanup is not None:
        if not isinstance(cleanup, CleanupInquiry):
            raise HandoffError("cleanup은 CleanupInquiry이어야 합니다.")
        if cleanup.archive_sha256 != report.archive_sha256:
            raise HandoffError("정리 질의가 이 ZIP의 것이 아닙니다.")
    acknowledged, lapsed = tuple(acknowledged), tuple(lapsed)
    if any(not isinstance(item, Acknowledgement) for item in acknowledged):
        raise HandoffError("acknowledged는 Acknowledgement의 묶음이어야 합니다.")
    if any(not isinstance(item, LapsedAnswer) for item in lapsed):
        raise HandoffError("lapsed는 LapsedAnswer의 묶음이어야 합니다.")

    payload = _payload(report, inquiry, table, cleanup, acknowledged, lapsed)
    data = _document_bytes(payload)
    if len(data) > MAX_BYTES:
        raise HandoffError("기록이 너무 큽니다.")
    return payload, data


def handoff_document(
    *,
    report: BundleReport,
    inquiry: CanonInquiry,
    table: ConfirmationTable | None = None,
    cleanup: CleanupInquiry | None = None,
    acknowledged: Sequence[Acknowledgement] = (),
    lapsed: Sequence[LapsedAnswer] = (),
) -> dict[str, Any]:
    """The answers so far as a record in memory, for a caller that keeps it itself.

    The very record ``save_handoff`` would write -- same checks, same checksum -- handed back
    instead of written. Nothing is written, anywhere, and nothing is kept here.
    """

    _, data = _built(report, inquiry, table, cleanup, acknowledged, lapsed)
    return json.loads(data)


def save_handoff(
    path: str | Path,
    *,
    report: BundleReport,
    inquiry: CanonInquiry,
    table: ConfirmationTable | None = None,
    cleanup: CleanupInquiry | None = None,
    acknowledged: Sequence[Acknowledgement] = (),
    lapsed: Sequence[LapsedAnswer] = (),
    update: bool = False,
) -> HandoffReceipt:
    """Write the answers so far to ``path``. Nothing else is written, anywhere.

    A file that is already there is left alone unless ``update`` is true, and even then
    only if the new record carries everything the old one did.
    """

    payload, data = _built(report, inquiry, table, cleanup, acknowledged, lapsed)
    destination = Path(path).expanduser().resolve(strict=False)
    if not destination.parent.is_dir():
        raise HandoffError(f"기록을 둘 폴더가 없습니다: {destination.parent}")
    if destination.exists():
        if not update:
            raise HandoffError(f"이미 있는 파일입니다. 이어서 저장하려면 update를 켜 주세요: {destination}")
        existing = _read_document(destination)["payload"]
        if not _extends(existing, payload):
            raise HandoffError(
                "저장된 기록과 이어지지 않습니다(다른 갈래이거나 뒤로 간 기록). 덮어쓰지 않습니다."
            )

    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise HandoffError(f"기록을 저장할 수 없습니다: {destination}") from exc
    return HandoffReceipt(
        path=str(destination),
        file_sha256=hashlib.sha256(data).hexdigest(),
        archive_sha256=report.archive_sha256,
        sequence=_sequence(payload),
    )


# --- reading ---------------------------------------------------------------


def _need(value: Any, kind: type | tuple[type, ...], label: str) -> Any:
    if isinstance(value, bool) and kind is not bool:
        raise HandoffError(f"기록의 {label} 형식이 맞지 않습니다.")
    if not isinstance(value, kind):
        raise HandoffError(f"기록의 {label} 형식이 맞지 않습니다.")
    return value


def _rows(value: Any, keys: Sequence[str], label: str) -> list[dict[str, Any]]:
    rows = _need(value, list, label)
    for row in rows:
        _need(row, dict, label)
        if set(row) != set(keys):
            raise HandoffError(f"기록의 {label} 항목 모양이 맞지 않습니다.")
    return rows


def read_handoff_bytes(path: Path) -> bytes:
    """The bytes of a record file, refused before reading when it is too large; a caller may hash what it read."""

    try:
        if path.stat().st_size > MAX_BYTES:
            raise HandoffError("기록 파일이 너무 큽니다.")
        data = path.read_bytes()
    except OSError as exc:
        raise HandoffError(f"기록을 읽을 수 없습니다: {path}") from exc
    if len(data) > MAX_BYTES:
        raise HandoffError("기록 파일이 너무 큽니다.")
    return data


def handoff_from_bytes(data: bytes, path: Path) -> dict[str, Any]:
    """A record from the bytes ``read_handoff_bytes`` read, checked as ``save_handoff`` wrote it."""

    try:
        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as exc:  # too deeply nested to parse is damage too
        raise HandoffError(f"기록을 읽을 수 없습니다: {path}") from exc
    return _checked(document)


def _read_document(path: Path) -> dict[str, Any]:
    return handoff_from_bytes(read_handoff_bytes(path), path)


def _checked(document: Any) -> dict[str, Any]:
    """A record as read, shown to be what ``save_handoff`` writes and nothing a hand has changed."""

    if not isinstance(document, dict) or set(document) != {"payload", "sha256"}:
        raise HandoffError("기록의 최상위 모양이 맞지 않습니다.")
    payload = document["payload"]
    if not isinstance(payload, dict) or document["sha256"] != canonical_hash(payload):
        raise HandoffError("기록이 손상되었거나 손으로 고쳐졌습니다(체크섬이 맞지 않습니다).")
    if payload.get("schema_version") != HANDOFF_SCHEMA:
        raise HandoffError(f"알 수 없는 기록 형식입니다: {payload.get('schema_version')!r}")
    if set(payload) != _PAYLOAD_KEYS:
        raise HandoffError("기록의 항목이 맞지 않습니다.")
    if payload["canonical_mutation"] is not False:
        raise HandoffError("기록은 정본을 바꿀 수 없습니다.")
    _need(payload["project_id"], str, "project_id")
    _need(payload["archive_sha256"], str, "archive_sha256")
    if payload["preset_id"] not in preset_ids():
        raise HandoffError(f"기록의 프리셋이 고정 목록에 없습니다: {payload['preset_id']!r}")
    if payload["language"] not in LANGUAGES:
        raise HandoffError(f"기록의 언어를 지원하지 않습니다: {payload['language']!r}")
    answer_keys = ("question_id", "answer", "revision")
    for key in ("owner_answers", "copy_answers"):
        for row in _rows(payload[key], answer_keys, key):
            _need(row["question_id"], str, key)
            _need(row["answer"], str, key)
            _need(row["revision"], int, key)
    table = payload["table"]
    if table is not None:
        _need(table, dict, "table")
        if set(table) != {"project_id", "owner", "items", "answers"}:
            raise HandoffError("기록의 table 모양이 맞지 않습니다.")
        _need(table["project_id"], str, "table")
        _need(table["owner"], str, "table")
        for row in _rows(table["items"], ("id", "question", "proposal", "candidates"), "table.items"):
            for key in ("id", "question", "proposal"):
                _need(row[key], str, "table.items")
            for candidate in _need(row["candidates"], list, "table.items"):
                _need(candidate, str, "table.items")
        for row in _rows(table["answers"], ("item_id", "choice", "words", "version", "revision"), "table.answers"):
            for key in ("item_id", "choice", "words"):
                _need(row[key], str, "table.answers")
            for key in ("version", "revision"):
                _need(row[key], int, "table.answers")
    cleanup = payload["cleanup"]
    if cleanup is not None:
        _need(cleanup, dict, "cleanup")
        if set(cleanup) != {"project_id", "also", "answers"}:
            raise HandoffError("기록의 cleanup 모양이 맞지 않습니다.")
        _need(cleanup["project_id"], str, "cleanup")
        for path_text in _need(cleanup["also"], list, "cleanup.also"):
            _need(path_text, str, "cleanup.also")
        for row in _rows(cleanup["answers"], answer_keys, "cleanup.answers"):
            _need(row["question_id"], str, "cleanup.answers")
            _need(row["answer"], str, "cleanup.answers")
            _need(row["revision"], int, "cleanup.answers")
    for row in _rows(payload["acknowledgements"], ("item_id", "basis", "words", "revision"), "acknowledgements"):
        for key in ("item_id", "basis", "words"):
            _need(row[key], str, "acknowledgements")
        _need(row["revision"], int, "acknowledgements")
    for row in _rows(payload["lapsed"], ("kind", "point", "was", "answer", "words", "version", "from_archive"), "lapsed"):
        for key in ("kind", "point", "was", "answer", "words", "from_archive"):
            _need(row[key], str, "lapsed")
        _need(row["version"], int, "lapsed")
    return document


def _groups(rows: Sequence[Mapping[str, Any]]) -> dict[int, list[Mapping[str, Any]]]:
    groups: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["revision"], []).append(row)
    return groups


def _replay_inquiry(report: BundleReport, payload: Mapping[str, Any]) -> CanonInquiry:
    inquiry = canon_inquiry_for_bundle(
        report, preset_id=payload["preset_id"], project_id=payload["project_id"], language=payload["language"]
    )
    steps: dict[int, tuple[str, dict[str, str]]] = {}
    for kind, rows in (("owner", payload["owner_answers"]), ("copy", payload["copy_answers"])):
        for row in rows:
            step = steps.setdefault(row["revision"], (kind, {}))
            if step[0] != kind:
                raise HandoffError("기록의 한 개정에 질문과 사본 답이 섞여 있습니다.")
            step[1][row["question_id"]] = row["answer"]
    for revision in sorted(steps):
        kind, answers = steps[revision]
        try:
            if kind == "owner":
                inquiry = inquiry.answer(answers)
            else:
                inquiry = inquiry.answer_copies({key: value.split("\n") for key, value in answers.items()})
        except CanonInquiryError as exc:
            raise HandoffError(f"기록의 정본 답이 이 ZIP과 맞지 않습니다: {exc}") from exc
        if inquiry.revision != revision:
            raise HandoffError("기록의 정본 답 개정 번호가 이어지지 않습니다.")
    again = _answer_rows(inquiry.answers), _answer_rows(inquiry.copy_answers)
    if again != (payload["owner_answers"], payload["copy_answers"]):
        raise HandoffError("기록의 정본 답이 다시 쌓은 결과와 다릅니다.")
    return inquiry


def _replay_table(
    project_id: str, owner: str, items: Sequence[ConfirmationItem], answers: Sequence[Mapping[str, Any]]
) -> ConfirmationTable:
    try:
        table = ConfirmationTable(project_id=project_id, owner=owner, items=tuple(items))
        for revision, group in sorted(_groups(answers).items()):
            revising = {row["version"] > 1 for row in group}
            if len(revising) != 1:
                raise HandoffError("기록의 한 개정에 첫 답과 고친 답이 섞여 있습니다.")
            batch = {row["item_id"]: {"choice": row["choice"], "words": row["words"]} for row in group}
            if len(batch) != len(group):
                raise HandoffError("기록의 한 개정에 같은 항목의 답이 둘 있습니다.")
            table = table.revise(batch) if revising == {True} else table.answer(batch)
    except CanonTableError as exc:
        raise HandoffError(f"기록의 확인표 답이 맞지 않습니다: {exc}") from exc
    return table


def _carry_table(
    report: BundleReport, payload: Mapping[str, Any]
) -> tuple[ConfirmationTable, list[LapsedAnswer]]:
    """Today's rows, with the answers that still apply to them."""

    record = payload["table"]
    project_id = payload["project_id"]
    shown = find_mismatches(report).to_table(project_id).items
    if record is None:
        return ConfirmationTable(project_id=project_id, items=shown), []
    held = {
        row["id"]: ConfirmationItem(
            id=row["id"], question=row["question"], proposal=row["proposal"], candidates=tuple(row["candidates"])
        )
        for row in record["items"]
    }
    current = {item.id: item for item in shown}
    answered = {row["item_id"] for row in record["answers"]}
    lapsed_rows = {
        item_id
        for item_id in answered
        if item_id in current and item_id in held and held[item_id] != current[item_id]
    }
    items = [*shown, *(item for item_id, item in held.items() if item_id not in current)]
    carried = [row for row in record["answers"] if row["item_id"] not in lapsed_rows]
    table = _replay_table(record["project_id"], record["owner"], items, carried)
    if not lapsed_rows and _table_history(table) != record["answers"]:
        raise HandoffError("기록의 확인표 답이 다시 쌓은 결과와 다릅니다.")
    lapsed = [
        LapsedAnswer(
            kind=TABLE_ROW,
            point=row["item_id"],
            was=held[row["item_id"]].question,
            answer=row["choice"],
            words=row["words"],
            version=row["version"],
            from_archive=payload["archive_sha256"],
        )
        for row in record["answers"]
        if row["item_id"] in lapsed_rows
    ]
    return table, lapsed


def _table_history(table: ConfirmationTable) -> list[dict[str, Any]]:
    return [
        {
            "item_id": answer.item_id,
            "choice": answer.choice.value,
            "words": answer.words,
            "version": answer.version,
            "revision": answer.revision,
        }
        for answer in table.answers
    ]


def _carry_cleanup(
    report: BundleReport, payload: Mapping[str, Any], same_archive: bool
) -> tuple[CleanupInquiry | None, list[LapsedAnswer]]:
    record = payload["cleanup"]
    if record is None:
        return None, []
    observed = {entry.path for entry in report.entries}
    also = [path for path in record["also"] if path in observed]
    lapsed = [
        LapsedAnswer(
            kind=CLEANUP_POINTER, point=path, was="지목한 문서", answer="", words="", version=1,
            from_archive=payload["archive_sha256"],
        )
        for path in record["also"]
        if path not in observed
    ]
    try:
        cleanup = propose_cleanup(report, also=also, project_id=record["project_id"])
        if same_archive:
            for revision, group in sorted(_groups(record["answers"]).items()):
                cleanup = cleanup.answer({row["question_id"]: row["answer"] for row in group})
                if cleanup.revision != revision:
                    raise HandoffError("기록의 정리 답 개정 번호가 이어지지 않습니다.")
            if _answer_rows(cleanup.answers) != record["answers"]:
                raise HandoffError("기록의 정리 답이 다시 쌓은 결과와 다릅니다.")
    except CanonCleanupError as exc:
        raise HandoffError(f"기록의 정리 답이 이 ZIP과 맞지 않습니다: {exc}") from exc
    if not same_archive:
        lapsed.extend(
            LapsedAnswer(
                kind=CLEANUP_ANSWER,
                point=row["question_id"].split(":", 2)[-1],
                was="옛 문서입니까?",
                answer=row["answer"],
                words="",
                version=1,
                from_archive=payload["archive_sha256"],
            )
            for row in record["answers"]
        )
    return cleanup, lapsed


def load_handoff(path: str | Path, report: BundleReport) -> Resumed:
    """Read a record and bring it to the project as it is now.

    What still applies is carried over. What no longer does -- answered for another version
    of the project, or for a question that has since been worded differently -- comes back in
    ``lapsed``, with the person's words exactly as they were.
    """

    if not isinstance(report, BundleReport):
        raise HandoffError("기록은 검사한 ZIP(BundleReport)에 대해서만 불러올 수 있습니다.")
    source = Path(path).expanduser().resolve(strict=False)
    return _resumed(_read_document(source)["payload"], report)


def resume_handoff(document: Mapping[str, Any], report: BundleReport) -> Resumed:
    """The same as ``load_handoff``, for a record the caller holds in memory.

    Trusted no more than a file is: it must be the record ``save_handoff`` writes, with its
    checksum intact, or it is refused. Nothing is read from disk.
    """

    if not isinstance(report, BundleReport):
        raise HandoffError("기록은 검사한 ZIP(BundleReport)에 대해서만 불러올 수 있습니다.")
    try:
        data = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(data) > MAX_BYTES:
            raise HandoffError("기록이 너무 큽니다.")
        copied = json.loads(data)
    except HandoffError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:  # not plain JSON, or nested past what can be held
        raise HandoffError("기록이 JSON 객체가 아닙니다.") from exc
    return _resumed(_checked(copied)["payload"], report)


def _resumed(payload: Mapping[str, Any], report: BundleReport) -> Resumed:
    same = payload["archive_sha256"] == report.archive_sha256

    lapsed = [
        LapsedAnswer(**{key: row[key] for key in ("kind", "point", "was", "answer", "words", "version", "from_archive")})
        for row in payload["lapsed"]
    ]
    if same:
        inquiry = _replay_inquiry(report, payload)
    else:
        inquiry = canon_inquiry_for_bundle(
        report, preset_id=payload["preset_id"], project_id=payload["project_id"], language=payload["language"]
    )
        for kind, rows in ((OWNER_ANSWER, payload["owner_answers"]), (COPY_ANSWER, payload["copy_answers"])):
            for row in rows:
                lapsed.append(
                    LapsedAnswer(
                        kind=kind,
                        point=row["question_id"].rsplit(":", 1)[-1],
                        was="",
                        answer=row["answer"],
                        words="",
                        version=1,
                        from_archive=payload["archive_sha256"],
                    )
                )
    table, table_lapsed = _carry_table(report, payload)
    cleanup, cleanup_lapsed = _carry_cleanup(report, payload, same)
    lapsed.extend(table_lapsed)
    lapsed.extend(cleanup_lapsed)
    acknowledged = tuple(
        Acknowledgement(item_id=row["item_id"], basis=row["basis"], words=row["words"], revision=row["revision"])
        for row in payload["acknowledgements"]
    )
    return Resumed(
        project_id=inquiry.project_id,
        archive_sha256=report.archive_sha256,
        recorded_archive_sha256=payload["archive_sha256"],
        inquiry=inquiry,
        table=table,
        cleanup=cleanup,
        acknowledged=acknowledged,
        lapsed=tuple(lapsed),
    )


__all__ = [
    "CLEANUP_ANSWER",
    "CLEANUP_POINTER",
    "COPY_ANSWER",
    "HANDOFF_SCHEMA",
    "MAX_BYTES",
    "OWNER_ANSWER",
    "TABLE_ROW",
    "HandoffError",
    "HandoffReceipt",
    "LapsedAnswer",
    "Resumed",
    "handoff_document",
    "handoff_from_bytes",
    "load_handoff",
    "read_handoff_bytes",
    "resume_handoff",
    "save_handoff",
]
