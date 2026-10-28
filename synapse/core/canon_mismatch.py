"""Show where a project's files disagree with each other. Decide nothing.

A project that grew over months says some things in more than one place, and
some of them stop agreeing: an instruction file that names a policy document
nobody ever wrote, a guide that points at a path the folder left long ago. Nobody
notices because each file reads fine on its own.

This lays those out, one finding each, with the files involved and what was
counted. It does not say which side is right -- that is the owner's to say, and
the question is handed to the confirmation table to be asked in their own terms:
"this names that, and that is not there; what do you want?"

Only what the files themselves show is reported, from the report of an archive
that has already been inspected. It reads nothing itself, lists no directory, and
writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from synapse.core.bundle import BundleReport, archive_wrapper
from synapse.core.canon_earlier import EarlierDecision, earlier_decisions
from synapse.core.canon_table import ConfirmationItem, ConfirmationTable

MISMATCH_SCHEMA = "canon-mismatch.v1"

# What every disagreement's id starts with: the id of a row in the confirmation table
# that this module made, as opposed to a row someone else put there.
MISMATCH_ID_PREFIX = "mismatch:"

# How many of the naming files a sentence lists before saying "and N more".
_SHOWN = 5


class MismatchKind(str, Enum):
    """The kinds of disagreement that can be shown from the files alone."""

    DANGLING_REFERENCE = "dangling_reference"
    MULTIPLE_START_POINTS = "multiple_start_points"


@dataclass(frozen=True, slots=True, kw_only=True)
class Mismatch:
    """One place the files disagree, and who is involved."""

    kind: MismatchKind
    subject: str
    paths: tuple[str, ...]
    mentions: int
    moved_to: tuple[str, ...] = ()
    # For a disagreement about where to begin: the documents it is pointed to.
    targets: tuple[str, ...] = ()
    # What the files already say was decided about the very things named here.
    earlier: tuple[EarlierDecision, ...] = ()

    @property
    def id(self) -> str:
        return f"{MISMATCH_ID_PREFIX}{self.kind.value}:{self.subject}"

    @property
    def detail(self) -> str:
        if self.kind is MismatchKind.MULTIPLE_START_POINTS:
            sources = tuple(path for path in self.paths if path not in self.targets) or self.paths
            return (
                f"{', '.join(sources[:_SHOWN])}이(가) '여기서 시작'이라고 서로 다른 문서 "
                f"{len(self.targets)}개를 가리킵니다: {', '.join(self.targets)}."
            )
        shown = ", ".join(self.paths[:_SHOWN])
        if len(self.paths) > _SHOWN:
            shown += f" 외 {len(self.paths) - _SHOWN}개"
        base = f"{shown}이(가) '{self.subject}'을(를) 모두 {self.mentions}번 가리키지만"
        if self.moved_to:
            return f"{base} 그 경로에는 없고, 같은 이름의 문서가 {', '.join(self.moved_to)}에 있습니다."
        return f"{base} 이 프로젝트에는 그런 문서가 없습니다."

    @property
    def _already(self) -> str:
        """What the project has already put in writing about this, asked against."""

        if not self.earlier:
            return ""
        return (
            " 이미 적혀 있는 결정이 있습니다: "
            + " / ".join(item.cite() for item in self.earlier[:3])
            + ". 그 결정을 퍼뜨릴까요, 되돌릴까요?"
        )

    def to_item(self) -> ConfirmationItem:
        """The point to ask the owner about. A moved file gets a proposal; a missing one does not."""

        if self.kind is MismatchKind.MULTIPLE_START_POINTS:
            return ConfirmationItem(
                id=self.id,
                question=f"{self.detail}{self._already} 어디서 시작하면 됩니까?",
                candidates=self.targets,
            )
        fix = (
            f"가리키는 경로를 {self.moved_to[0]}(으)로 고친다" if len(self.moved_to) == 1 else ""
        )
        return ConfirmationItem(
            id=self.id,
            question=f"{self.detail}{self._already} 어떻게 할까요?",
            proposal=fix,
            candidates=self.moved_to,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "subject": self.subject,
            "paths": list(self.paths),
            "mentions": self.mentions,
            "moved_to": list(self.moved_to),
            "targets": list(self.targets),
            "earlier_decisions": [item.to_record() for item in self.earlier],
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class MismatchReport:
    """Everything the files of one archive disagree about, as far as the files show."""

    archive_sha256: str
    mismatches: tuple[Mismatch, ...]
    schema_version: str = MISMATCH_SCHEMA

    def of_kind(self, kind: MismatchKind) -> tuple[Mismatch, ...]:
        return tuple(item for item in self.mismatches if item.kind is kind)

    def findings(self) -> dict[str, Any]:
        """The shape the trial's scorer reads: ``{"findings": [{"kind", "paths"}]}``."""

        return {
            "findings": [
                {"kind": item.kind.value, "paths": list(item.paths)} for item in self.mismatches
            ]
        }

    def to_table(self, project_id: str, *, owner: str = "human") -> ConfirmationTable:
        """One row per disagreement, for the owner to answer. Nothing is answered for them."""

        return ConfirmationTable(
            project_id=project_id,
            owner=owner,
            items=tuple(item.to_item() for item in self.mismatches),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "archive_sha256": self.archive_sha256,
            "mismatches": [item.to_record() for item in self.mismatches],
            "canonical_mutation": False,
            "filesystem_scan": False,
        }


def find_mismatches(report: BundleReport) -> MismatchReport:
    """The disagreements the files of one inspected archive show."""

    if not isinstance(report, BundleReport):
        raise TypeError("어긋남 찾기 입력은 BundleReport이어야 합니다.")

    dangling: dict[str, dict[str, Any]] = {}
    for entry in report.entries:
        for missing in entry.missing_references:
            found = dangling.setdefault(
                missing.target, {"paths": set(), "mentions": 0, "moved_to": set()}
            )
            found["paths"].add(entry.path)
            found["mentions"] += missing.mentions
            found["moved_to"].update(missing.moved_to)

    mismatches = [
        Mismatch(
            kind=MismatchKind.DANGLING_REFERENCE,
            subject=target,
            paths=tuple(sorted(found["paths"])),
            mentions=found["mentions"],
            moved_to=tuple(sorted(found["moved_to"])),
            earlier=earlier_decisions(report, mentioning=(target,)),  # a moved file's new path ends with it
        )
        for target, found in sorted(dangling.items())
    ]

    # The front door is what sits at the top of the project. If it says to start in
    # more than one place -- in one file or across several -- a newcomer is told two things.
    wrapper = archive_wrapper(entry.path for entry in report.entries)
    starts: dict[str, set[str]] = {}
    for entry in report.entries:
        if "/" not in entry.path.removeprefix(wrapper):
            for target in entry.start_points:
                starts.setdefault(target, set()).add(entry.path)
    if len(starts) >= 2:
        involved = set(starts).union(*starts.values())
        mismatches.append(
            Mismatch(
                kind=MismatchKind.MULTIPLE_START_POINTS,
                subject="start_points",
                paths=tuple(sorted(involved)),
                mentions=len(starts),
                targets=tuple(sorted(starts)),
                earlier=earlier_decisions(report, mentioning=[t.rpartition("/")[2] for t in sorted(starts)]),
            )
        )
    return MismatchReport(archive_sha256=report.archive_sha256, mismatches=tuple(mismatches))


__all__ = [
    "MISMATCH_ID_PREFIX",
    "MISMATCH_SCHEMA",
    "Mismatch",
    "MismatchKind",
    "MismatchReport",
    "find_mismatches",
]
