"""Turn a cleanup plan into an exact, checkable list of changes -- and change nothing.

Setting old documents aside means moving files, marking them, and rewriting the paths
other files wrote down for them. Doing it is a job for whoever has hands on the project's
folder: a person, a script, another AI. What can be done here, without touching a single
file, is everything that makes that job safe:

* the changes are written out exactly -- which file, which bytes, how many times -- so
  the one who carries them out has nothing to decide;
* each change is proved on an in-memory copy before it is proposed: nothing outside the
  plan changes, no document is lost, a moved document is its old bytes under a marking,
  line endings and a byte-order mark are kept, and the plan can be undone to the exact
  byte (a plan that cannot be undone is not made);
* it names what must be true first: the folder is a working copy and not the original,
  every file is the file the plan was made from, the new places are free, and the plan
  has not been carried out already;
* afterwards, the result can be checked from the outside: the project is inspected again
  and compared with the plan, so a change the plan did not ask for -- or an executor that
  was careless -- shows up as a difference, whoever the executor was.

It reads no file and writes no file. The bytes of the few files a plan touches are handed
to it by the caller, who took them from the archive by name; the folder is never listed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from synapse.core.bundle import BundleReport, archive_wrapper
from synapse.core.canon_cleanup import ANSWER_OLD, CleanupInquiry, PathRewrite, destination_of
from synapse.core.idea_session import canonical_hash

APPLY_PLAN_SCHEMA = "canon-apply-plan.v1"

# A folder is a working copy only if it carries this file. The original never does, so a plan
# refuses to be carried out there.
WORK_COPY_SENTINEL = ".synapse_work_copy"

# The documents a plan touches are text; a file far larger than one is not read into a plan.
MAX_FILE_BYTES = 5 * 1024 * 1024

MARK_OLD = "OLD"
MARK_STAY = "STAY"
APPLY = "apply"
REVERT = "revert"

_BOM = b"\xef\xbb\xbf"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ApplyPlanError(ValueError):
    """Raised when a plan cannot be made, or cannot safely be carried out."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class Mark:
    """A document that gets a marking on top: where it is, whether it moves, and the marking."""

    path: str
    kind: str
    # The marking, one line per "\n". It is written with the file's own line ending.
    text: str
    newline: str
    # Where the document goes. Empty: it stays where it is.
    to: str = ""

    def to_record(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "to": self.to, "newline": self.newline, "text": self.text}


def _banner(mark: Mark) -> bytes:
    return (mark.newline.join(mark.text.split("\n")) + mark.newline + mark.newline).encode("utf-8")


def _with_banner(data: bytes, mark: Mark) -> bytes:
    bom = _BOM if data.startswith(_BOM) else b""
    return bom + _banner(mark) + data[len(bom) :]


def _without_banner(data: bytes, mark: Mark, where: str) -> bytes:
    bom = _BOM if data.startswith(_BOM) else b""
    rest = data[len(bom) :]
    banner = _banner(mark)
    if not rest.startswith(banner):
        raise ApplyPlanError(f"{where}에 이 계획의 표시가 없습니다. 되돌릴 수 없습니다.")
    return bom + rest[len(banner) :]


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyPlan:
    """Every change, written out; and what must be true before and after. Not carried out."""

    archive_sha256: str
    wrapper: str
    marked_on: str
    marks: tuple[Mark, ...]
    rewrites: tuple[PathRewrite, ...]
    # sha256 of every file the plan reads, before; and of every file it leaves, after.
    # Paths are the project's own (the archive's single wrapping folder, if any, is left off).
    before: Mapping[str, str]
    after: Mapping[str, str]
    direction: str = APPLY
    requires_sentinel: str = WORK_COPY_SENTINEL
    schema_version: str = APPLY_PLAN_SCHEMA

    # --- what it names ------------------------------------------------------

    def archive_path(self, path: str) -> str:
        """The name of a project path inside the archive it was made from."""

        return f"{self.wrapper}{path}"

    def _moved(self, path: str) -> str:
        return next((mark.to for mark in self.marks if mark.path == path and mark.to), path)

    @property
    def _destinations(self) -> tuple[str, ...]:
        return tuple(mark.to for mark in self.marks if mark.to)

    @property
    def _origins_of_moves(self) -> tuple[str, ...]:
        return tuple(mark.path for mark in self.marks if mark.to)

    @property
    def _must_be_free(self) -> tuple[str, ...]:
        """Where the documents are about to go (or, on the way back, came from)."""

        return self._destinations if self.direction == APPLY else self._origins_of_moves

    @property
    def paths_to_read(self) -> tuple[str, ...]:
        """The only paths whose state the one carrying it out has to report -- and nothing else."""

        return tuple(sorted({*self.before, *self._must_be_free, self.requires_sentinel}))

    # --- carrying out, in memory ----------------------------------------------

    def _forward(self, files: Mapping[str, bytes]) -> dict[str, bytes]:
        work = dict(files)
        for rewrite in self.rewrites:
            data = work[rewrite.file]
            old = rewrite.old.encode("utf-8")
            if data.count(old) != rewrite.count:
                raise ApplyPlanError(
                    f"{rewrite.file}에 '{rewrite.old}'이(가) {data.count(old)}번 있습니다(계획은 {rewrite.count}번)."
                )
            work[rewrite.file] = data.replace(old, rewrite.new.encode("utf-8"))
        for mark in self.marks:
            marked = _with_banner(work[mark.path], mark)
            if mark.to:
                del work[mark.path]
                work[mark.to] = marked
            else:
                work[mark.path] = marked
        return work

    def _backward(self, files: Mapping[str, bytes]) -> dict[str, bytes]:
        work = dict(files)
        for mark in self.marks:  # each mark is a different file, so their order does not matter
            here = mark.to or mark.path
            original = _without_banner(work[here], mark, here)
            if mark.to:
                del work[here]
            work[mark.path] = original
        for rewrite in reversed(self.rewrites):
            data = work[rewrite.file]
            new = rewrite.new.encode("utf-8")
            if data.count(new) != rewrite.count:
                raise ApplyPlanError(
                    f"{rewrite.file}에 '{rewrite.new}'이(가) {data.count(new)}번 있습니다(계획은 {rewrite.count}번)."
                )
            work[rewrite.file] = data.replace(new, rewrite.old.encode("utf-8"))
        return work

    def apply_to(self, files: Mapping[str, bytes]) -> dict[str, bytes]:
        """What the working copy comes to if this plan is carried out. ``files`` is not changed.

        ``files`` holds the files that exist among ``paths_to_read`` -- a path that is not in it
        does not exist. It is refused unless the folder is a working copy, every file is the one
        the plan was made from, the places the documents go are free, and the plan has not been
        carried out already.
        """

        present = dict(files)
        again = "이미 적용된 계획입니다" if self.direction == APPLY else "이미 되돌려진 계획입니다"
        if self.requires_sentinel not in present:
            raise ApplyPlanError(
                f"작업 사본이 아닙니다: '{self.requires_sentinel}' 표지가 없습니다. 원본에는 적용하지 않습니다."
            )
        stale = [path for path, digest in self.before.items() if path not in present or _sha(present[path]) != digest]
        if stale:
            if all(path in present and _sha(present[path]) == digest for path, digest in self.after.items()):
                raise ApplyPlanError(f"{again}. 같은 계획을 두 번 적용하지 않습니다.")
            raise ApplyPlanError("계획을 만든 뒤에 바뀌었거나 없는 파일이 있습니다: " + ", ".join(sorted(stale)[:5]))
        taken = [path for path in self._must_be_free if path in present]
        if taken:
            raise ApplyPlanError("옮길 자리에 이미 파일이 있습니다: " + ", ".join(sorted(taken)[:5]))

        result = self._forward(present) if self.direction == APPLY else self._backward(present)

        wrong = [path for path, digest in self.after.items() if path not in result or _sha(result[path]) != digest]
        if wrong:
            raise ApplyPlanError("계산한 결과가 계획의 기대와 다릅니다: " + ", ".join(sorted(wrong)[:5]))
        named = set(self.before) | set(self.after)
        outside = [
            path
            for path in {*present, *result}
            if path not in named and present.get(path) != result.get(path)
        ]
        if outside:
            raise ApplyPlanError("계획 밖의 파일이 바뀌게 됩니다: " + ", ".join(sorted(outside)[:5]))
        return result

    def revert_plan(self) -> ApplyPlan:
        """The plan that puts every file back to the exact bytes it had. Applying it twice is refused."""

        return replace(
            self,
            direction=REVERT if self.direction == APPLY else APPLY,
            before=dict(self.after),
            after=dict(self.before),
        )

    # --- reading it ---------------------------------------------------------

    @property
    def operations(self) -> tuple[dict[str, Any], ...]:
        """What the one carrying it out does, in this order, whichever way the plan goes.

        ``mark``: put the marking on top of ``path`` (after a byte-order mark, if there is one),
        then, if ``to`` is not empty, move the file there. ``unmark``: take the marking off
        ``from`` and, if ``to`` differs, move the file there. ``rewrite``: replace ``old`` with
        ``new`` in ``file``, exactly ``count`` times. Nothing has to be worked out from the
        direction: an executor that does these in order does the plan, or undoes it.
        """

        if self.direction == APPLY:
            return (
                *({"op": "rewrite", "file": r.file, "old": r.old, "new": r.new, "count": r.count} for r in self.rewrites),
                *(
                    {"op": "mark", "path": m.path, "to": m.to, "newline": m.newline, "text": m.text}
                    for m in self.marks
                ),
            )
        return (
            *(
                {"op": "unmark", "from": m.to or m.path, "to": m.path, "newline": m.newline, "text": m.text}
                for m in self.marks
            ),
            *(
                {"op": "rewrite", "file": r.file, "old": r.new, "new": r.old, "count": r.count}
                for r in reversed(self.rewrites)
            ),
        )

    def preview(self) -> str:
        """The plan in plain words, for a person to read before anyone carries it out."""

        moves = [mark for mark in self.marks if mark.to]
        stays = [mark for mark in self.marks if not mark.to]
        total = sum(item.count for item in self.rewrites)
        forward = self.direction == APPLY
        lines = [
            f"[{'적용 계획' if forward else '되돌리기 계획'}] 이 계획은 아무 파일도 바꾸지 않았습니다. 미리 보는 것입니다."
        ]
        if forward:
            lines.append(f"  옛 문서로 옮김 {len(moves)}개 (표시를 위에 붙여서)")
            lines += [f"    {mark.path} -> {mark.to}" for mark in moves]
            lines.append(f"  제자리에 표시 {len(stays)}개")
        else:
            lines.append(f"  옛 문서를 제자리로 되돌림 {len(moves)}개 (맨 위의 표시를 떼고)")
            lines += [f"    {mark.to} -> {mark.path}" for mark in moves]
            lines.append(f"  제자리의 표시 떼기 {len(stays)}개")
        lines += [f"    {mark.path} | {mark.text.split(chr(10))[0][:90]}" for mark in stays]
        lines.append(
            f"  경로 {'고쳐' if forward else '되돌려'} 쓰기 {len(self.rewrites)}건, 총 {total}번 (정확히 이 횟수만 바꿈)"
        )
        lines += [
            f"    {item.file}: {item.old if forward else item.new} -> {item.new if forward else item.old} x{item.count}"
            for item in self.rewrites
        ]
        lines.append(f"  손대는 기존 파일 {len(self.before)}개 (실행 직전에 해시를 다시 확인해야 함)")
        lines.append(f"  조건: 폴더에 '{self.requires_sentinel}' 표지가 있어야 하고, 원본에는 적용하지 않습니다.")
        lines.append("  실행 뒤에는 프로젝트를 다시 검사해 계획과 같은지 확인합니다(verify_result).")
        return "\n".join(lines)

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": self.schema_version,
            "direction": self.direction,
            "archive_sha256": self.archive_sha256,
            "wrapper": self.wrapper,
            "marked_on": self.marked_on,
            "requires_sentinel": self.requires_sentinel,
            "operations": list(self.operations),
            "marks": [mark.to_record() for mark in self.marks],
            "rewrites": [item.to_record() for item in self.rewrites],
            "before_sha256": dict(sorted(self.before.items())),
            "after_sha256": dict(sorted(self.after.items())),
            "paths_to_read": list(self.paths_to_read),
            "executor": "external",
            "filesystem_write": False,
            "model_invoked": False,
            "canonical_mutation": False,
        }
        record["plan_hash"] = canonical_hash(record)
        return record


# --- making a plan ----------------------------------------------------------


def needed_paths(cleanup: CleanupInquiry) -> tuple[str, ...]:
    """The files of the archive a plan for the answers so far touches, as the archive names them."""

    plan = cleanup.plan
    return tuple(sorted({*plan.move, *plan.in_place, *(item.file for item in plan.rewrites)}))


def _newline_of(data: bytes) -> str:
    return "\r\n" if b"\r\n" in data[:4096] else "\n"


def _is_utf8(data: bytes) -> bool:
    """Text the way a marking is written: UTF-8 (a byte-order mark is fine), and no NUL, which
    is what UTF-16 without a byte-order mark looks like when it happens to decode."""

    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _pointer(owners: Sequence[str]) -> str:
    if owners:
        return " 지금의 기준은 " + ", ".join(f"`{owner}`" for owner in owners) + "입니다."
    return " 지금의 기준은 이 프로젝트의 정본 문서입니다."


def check_marked_on(marked_on: Any) -> None:
    """Refuse anything but a real YYYY-MM-DD date. Cheap, so the command line asks it before opening a ZIP."""

    if not isinstance(marked_on, str) or _DATE.fullmatch(marked_on) is None:
        raise ApplyPlanError("marked_on은 YYYY-MM-DD 날짜여야 합니다.")
    try:
        date.fromisoformat(marked_on)
    except ValueError as exc:
        raise ApplyPlanError(f"marked_on이 날짜가 아닙니다: {marked_on}") from exc


def build_apply_plan(
    report: BundleReport,
    cleanup: CleanupInquiry,
    files: Mapping[str, bytes],
    *,
    marked_on: str,
    canon_owners: Sequence[str] = (),
) -> ApplyPlan:
    """The exact changes the answers so far come to, proved safe on an in-memory copy.

    ``files`` maps each of ``needed_paths(cleanup)`` to its bytes in the archive. ``marked_on`` is
    the date written in the markings (given, so the same inputs make the same plan), and
    ``canon_owners`` the files that are the standard now, named in them.
    """

    if not isinstance(report, BundleReport):
        raise ApplyPlanError("적용 계획은 검사한 ZIP(BundleReport)을 기준으로만 만듭니다.")
    if not isinstance(cleanup, CleanupInquiry):
        raise ApplyPlanError("cleanup은 CleanupInquiry이어야 합니다.")
    if cleanup.archive_sha256 != report.archive_sha256:
        raise ApplyPlanError("정리 질의가 이 ZIP의 것이 아닙니다.")
    check_marked_on(marked_on)
    owners = tuple(sorted({owner for owner in canon_owners if isinstance(owner, str) and owner.strip()}))

    plan = cleanup.plan
    if not (plan.move or plan.in_place or plan.rewrites):
        raise ApplyPlanError("적용할 것이 없습니다: 옮기거나 표시하거나 고칠 문서가 아직 없습니다.")
    wrapper = cleanup.wrapper
    hashes = {entry.path: entry.sha256 for entry in report.entries}
    work: dict[str, bytes] = {}
    for name in needed_paths(cleanup):
        data = files.get(name)
        if not isinstance(data, bytes):
            raise ApplyPlanError(f"계획이 손대는 파일의 내용이 없습니다: {name}")
        if len(data) > MAX_FILE_BYTES:
            raise ApplyPlanError(f"파일이 너무 커서 계획에 넣을 수 없습니다: {name}")
        if _sha(data) != hashes.get(name):
            raise ApplyPlanError(f"받은 내용이 이 ZIP의 파일과 다릅니다: {name}")
        work[name.removeprefix(wrapper)] = data
    # A marking is UTF-8 text laid on top of a file, and a path is rewritten as UTF-8 bytes. A
    # document saved in another encoding (UTF-16, a Korean code page) would come out half one
    # and half the other, so a plan that touches one is not made.
    foreign = sorted(name for name, data in work.items() if not _is_utf8(data))
    if foreign:
        raise ApplyPlanError(
            "UTF-8 텍스트가 아닌 문서는 표시를 붙이거나 고칠 수 없습니다. 인코딩을 UTF-8로 바꾸거나 "
            "그 문서를 CURRENT로 답해 주세요: " + ", ".join(foreign[:5])
        )

    def rel(path: str) -> str:
        return path.removeprefix(wrapper)

    answered = {item.question_id: item.answer for item in cleanup.answers}
    by_path = {item.path: item for item in cleanup.candidates}
    pointer = _pointer(owners)
    marks = []
    for path in plan.move:
        reason = (
            "사람이 옛 문서라고 정했고 코드가 읽거나 쓰지 않습니다."
            if answered.get(cleanup.question_id(path)) == ANSWER_OLD
            else "스스로 다른 문서가 대체했다고 적었고 아무것도 읽지 않아 옮기도록 제안되었습니다."
        )
        marks.append(
            Mark(
                path=rel(path),
                kind=MARK_OLD,
                to=rel(destination_of(path)),
                newline=_newline_of(work[rel(path)]),
                text=(
                    f"> **[옛 문서 — {marked_on} 표시]** 이 문서는 과거 시점의 기록입니다.{pointer}\n"
                    f"> 원래 위치: `{rel(path)}` · 사유: {reason}"
                ),
            )
        )
    for path in plan.in_place:
        candidate = by_path[path]
        if candidate.read_by_code:
            readers = ", ".join(sorted(rel(name) for name in candidate.read_by_code)[:3])
            why = f"코드가 이 문서를 읽거나 갱신합니다({readers}). 그래서 옮기지 않고 제자리에 둡니다."
        else:
            why = plan.reasons.get(path) or "옮기지 않고 제자리에 표시만 합니다."
        marks.append(
            Mark(
                path=rel(path),
                kind=MARK_STAY,
                newline=_newline_of(work[rel(path)]),
                text=(
                    f"> **[정본 아님 — {marked_on} 표시]** {why}\n"
                    f"> 이 문서는 옛 기록이거나 도구가 만드는 문서라 지금의 기준이 아닙니다.{pointer}"
                ),
            )
        )
    rewrites = tuple(
        PathRewrite(file=rel(item.file), old=item.old, new=item.new, count=item.count) for item in plan.rewrites
    )
    sources = {mark.path for mark in marks} | {item.file for item in rewrites}
    draft = ApplyPlan(
        archive_sha256=report.archive_sha256,
        wrapper=wrapper,
        marked_on=marked_on,
        marks=tuple(marks),
        rewrites=rewrites,
        before={path: _sha(work[path]) for path in sorted(sources)},
        after={},
    )
    with_sentinel = {**work, WORK_COPY_SENTINEL: b""}
    forward = draft._forward(with_sentinel)
    finals = {draft._moved(path) for path in sources}
    plan_ = replace(draft, after={path: _sha(forward[path]) for path in sorted(finals)})

    # A plan that cannot be proved safe is not made: carrying it out must be accepted by every
    # check of ``apply_to``, and so must undoing it -- which, by those same checks, gives back
    # every file at the hash it had, and changes nothing the plan does not name.
    try:
        plan_.revert_plan().apply_to(plan_.apply_to(with_sentinel))
    except ApplyPlanError as exc:
        raise ApplyPlanError(f"안전하다고 증명되지 않아 계획을 만들지 않습니다: {exc}") from exc
    return plan_


# --- checking the result from the outside -----------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultCheck:
    """How a project compares with a plan after someone carried it out."""

    ok: bool
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    changed: tuple[str, ...]
    still_there: tuple[str, ...]
    untouched_changed: tuple[str, ...]
    unexpected_gone: tuple[str, ...]
    unexpected_new: tuple[str, ...]
    allowed_extra: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "matched": list(self.matched),
            "missing": list(self.missing),
            "changed": list(self.changed),
            "still_there": list(self.still_there),
            "untouched_changed": list(self.untouched_changed),
            "unexpected_gone": list(self.unexpected_gone),
            "unexpected_new": list(self.unexpected_new),
            "allowed_extra": list(self.allowed_extra),
            "canonical_mutation": False,
            "filesystem_scan": False,
        }


def _hashes(report: BundleReport) -> dict[str, str]:
    wrapper = archive_wrapper(entry.path for entry in report.entries)
    return {entry.path.removeprefix(wrapper): entry.sha256 for entry in report.entries}


def verify_result(plan: ApplyPlan, before: BundleReport, after: BundleReport) -> ResultCheck:
    """Compare the project after the plan was carried out with the plan, file by file.

    ``before`` is the project the plan was made from (for a plan to revert: the project as it was
    when the revert was made) and ``after`` the project inspected again. Nothing is read from a
    folder here: both are reports of archives, so every file of the project is compared, including
    the ones the plan says nothing about.
    """

    if not isinstance(plan, ApplyPlan) or not isinstance(before, BundleReport) or not isinstance(after, BundleReport):
        raise ApplyPlanError("verify_result에는 ApplyPlan과 검사한 ZIP 둘이 필요합니다.")
    if plan.direction == APPLY and before.archive_sha256 != plan.archive_sha256:
        raise ApplyPlanError("계획은 다른 ZIP의 것입니다.")
    b, a = _hashes(before), _hashes(after)
    off = [path for path, digest in plan.before.items() if b.get(path) != digest]
    if off:
        raise ApplyPlanError("이 보고서는 계획의 '전' 상태가 아닙니다: " + ", ".join(sorted(off)[:5]))

    removed = set(plan._origins_of_moves if plan.direction == APPLY else plan._destinations)
    expected = {path: digest for path, digest in b.items() if path not in removed}
    expected.update(plan.after)
    extra = {plan.requires_sentinel} if plan.requires_sentinel in a and plan.requires_sentinel not in expected else set()

    matched, missing, changed = [], [], []
    untouched_changed, unexpected_gone = [], []
    for path, digest in sorted(expected.items()):
        planned = path in plan.after
        if path not in a:
            (missing if planned else unexpected_gone).append(path)
        elif a[path] != digest:
            (changed if planned else untouched_changed).append(path)
        elif planned:
            matched.append(path)
    still_there = sorted(path for path in removed if path in a)
    unexpected_new = sorted(path for path in a if path not in expected and path not in removed and path not in extra)
    ok = not (missing or changed or still_there or untouched_changed or unexpected_gone or unexpected_new)
    return ResultCheck(
        ok=ok,
        matched=tuple(matched),
        missing=tuple(missing),
        changed=tuple(changed),
        still_there=tuple(still_there),
        untouched_changed=tuple(untouched_changed),
        unexpected_gone=tuple(unexpected_gone),
        unexpected_new=tuple(unexpected_new),
        allowed_extra=tuple(sorted(extra)),
    )


# --- command line -------------------------------------------------------------


def read_members(archive: Any, names: Sequence[str]) -> dict[str, bytes]:
    """Read the named members of a ZIP -- those, and no others. The one place a file is read."""

    import zipfile

    found: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(archive) as bundle:
            known = set(bundle.namelist())
            for name in names:
                if name not in known:
                    raise ApplyPlanError(f"ZIP에 없는 파일입니다: {name}")
                if bundle.getinfo(name).file_size > MAX_FILE_BYTES:
                    raise ApplyPlanError(f"파일이 너무 커서 읽지 않습니다: {name}")
                found[name] = bundle.read(name)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ApplyPlanError(f"ZIP을 읽을 수 없습니다: {archive}") from exc
    return found


def _plan_from(archive: Any, record: Any, marked_on: str) -> tuple[BundleReport, ApplyPlan]:
    from synapse.core.bundle import inspect_bundle
    from synapse.core.canon_handoff import load_handoff

    report = inspect_bundle(archive)
    resumed = load_handoff(record, report)
    if not resumed.same_archive:
        raise ApplyPlanError("기록이 이 ZIP의 것이 아닙니다(ZIP이 바뀌었습니다). 다시 답한 기록으로 계획을 만들어 주세요.")
    if resumed.cleanup is None:
        raise ApplyPlanError("기록에 정리 질문 답이 없습니다.")
    owners = sorted({fact.owner for fact in resumed.inquiry.canon.facts if fact.owner and fact.owner != "plan"})
    owners = [owner.removeprefix(resumed.cleanup.wrapper) for owner in owners]
    files = read_members(archive, needed_paths(resumed.cleanup))
    return report, build_apply_plan(report, resumed.cleanup, files, marked_on=marked_on, canon_owners=owners)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    from synapse.core.bundle import inspect_bundle

    parser = argparse.ArgumentParser(
        prog="python -m synapse.core.canon_apply",
        description="Plan, and check, the setting aside of old documents. Writes nothing.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan", help="print the exact changes the answers come to")
    verify_parser = commands.add_parser("verify", help="compare a project, inspected after the plan was carried out, with the plan")
    for sub in (plan_parser, verify_parser):
        sub.add_argument("archive", type=Path, help="the project as it was (a ZIP)")
        sub.add_argument("--resume", type=Path, required=True, help="a handoff record holding the cleanup answers")
        sub.add_argument("--marked-on", required=True, help="the date written in the markings, YYYY-MM-DD")
    plan_parser.add_argument("--revert", action="store_true", help="print the plan that undoes it instead")
    verify_parser.add_argument("after", type=Path, help="the project afterwards (a ZIP of the working copy)")
    args = parser.parse_args(argv)
    try:
        check_marked_on(args.marked_on)  # before the archive is opened: that takes about half a minute
        report, plan = _plan_from(args.archive, args.resume, args.marked_on)
        if args.command == "plan":
            shown = plan.revert_plan() if args.revert else plan
            output: dict[str, Any] = {"plan": shown.to_record(), "preview": shown.preview().split("\n")}
            code = 0
        else:
            check = verify_result(plan, report, inspect_bundle(args.after))
            output, code = check.to_record(), (0 if check.ok else 1)
    except (OSError, ValueError) as exc:
        print(f"canon-apply: {exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)
    return code


__all__ = [
    "APPLY",
    "APPLY_PLAN_SCHEMA",
    "MARK_OLD",
    "MARK_STAY",
    "MAX_FILE_BYTES",
    "REVERT",
    "WORK_COPY_SENTINEL",
    "ApplyPlan",
    "ApplyPlanError",
    "Mark",
    "ResultCheck",
    "build_apply_plan",
    "check_marked_on",
    "main",
    "needed_paths",
    "read_members",
    "verify_result",
]

if __name__ == "__main__":
    raise SystemExit(main())
