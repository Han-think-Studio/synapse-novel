"""Project canon: which file owns each fact, and what every other file is to it.

A project whose facts are written in several files has no canon until someone
says which file wins. Left unsaid, the most repeated wording starts to look
like the truth, a digest starts to read as an authority, and a decision made in
one place never reaches the others. Deciding it late means untangling every
copy at once; deciding it first costs one small map.

So a new project begins with this map, before any of its files has content. It
is a proposal like every other plan output: it grants no authority, writes
nothing and mutates no Canonical State. Confirming it is a separate, explicit
step through the normal Proposal boundary.

The map has three parts.

* facts, each with exactly one owner -- a workspace file, or the plan itself
  for what is computed from the plan;
* copies, each classified by how it stays true to the fact it repeats;
* sources, the human's own words, kept verbatim and never made canon.

Copies are one of four kinds, and the kind says what may be trusted:

* ``GENERATED`` -- computed from the owner and never edited by hand;
* ``CHECKED``   -- written independently, with a named check that confirms it;
* ``SUMMARY``   -- a digest for people; it is not an authority and adds no fact;
* ``PENDING``   -- related to the fact, but agreement is not yet established.

``PENDING`` is the progress measure. A copy leaves it only when a check is
written, so the count falling is the honest sign that a project is settling,
and a count that never falls is the honest sign that it is not.

This module reads no files and lists no directories. It judges a map, and the
paths a caller supplies, and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Protocol

from synapse.core.contracts import LifecycleStatus

CANON_SCHEMA = "project-canon.v1"

# The owner of a fact that is computed from the plan rather than written in a
# file. It is not a path and can never be present or absent in a workspace.
PLAN_OWNER = "plan"


class CanonError(ValueError):
    """Raised when a canon map is malformed or contradicts itself."""


class CopyKind(str, Enum):
    GENERATED = "GENERATED"
    CHECKED = "CHECKED"
    SUMMARY = "SUMMARY"
    PENDING = "PENDING"


class CanonFindingCode(str, Enum):
    # A planned file has no role at all: not an owner, not a copy, not a source.
    # It is the unwired-file defect -- a file the structure does not mention.
    UNCLASSIFIED_PATH = "UNCLASSIFIED_PATH"
    OWNER_NOT_PLANNED = "OWNER_NOT_PLANNED"
    COPY_NOT_PLANNED = "COPY_NOT_PLANNED"
    # A digest or restatement exists in the workspace while the file that owns
    # the fact does not: the copy is standing in for the canon.
    COPY_PRESENT_WITHOUT_OWNER = "COPY_PRESENT_WITHOUT_OWNER"


def _safe_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CanonError(f"{label}이(가) 비어 있습니다.")
    path = value.strip()
    parts = path.split("/")
    if (
        path.startswith("/")
        or "\\" in path
        or ".." in parts
        or "" in parts
        or re.match(r"^[A-Za-z]:", path)
    ):
        raise CanonError(f"안전하지 않은 {label}입니다: {value}")
    return path


def _text(value: str, label: str, *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise CanonError(f"{label}이(가) 비어 있습니다.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CanonFact:
    """One thing the project asserts, with the single place that decides it."""

    id: str
    owner: str
    statement: str
    basis: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "fact.id"))
        owner = _text(self.owner, "fact.owner")
        object.__setattr__(self, "owner", owner if owner == PLAN_OWNER else _safe_path(owner, "fact.owner"))
        object.__setattr__(self, "statement", _text(self.statement, "fact.statement"))
        object.__setattr__(self, "basis", _text(self.basis, "fact.basis", required=False))

    def to_record(self) -> dict[str, str]:
        return {"id": self.id, "owner": self.owner, "statement": self.statement, "basis": self.basis}


@dataclass(frozen=True, slots=True)
class CanonCopy:
    """A file that repeats a fact it does not own, and how it stays true to it."""

    path: str
    fact_id: str
    kind: CopyKind
    basis: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _safe_path(self.path, "copy.path"))
        object.__setattr__(self, "fact_id", _text(self.fact_id, "copy.fact_id"))
        try:
            object.__setattr__(self, "kind", CopyKind(self.kind))
        except ValueError as exc:
            raise CanonError(f"지원하지 않는 사본 종류입니다: {self.kind}") from exc
        object.__setattr__(self, "basis", _text(self.basis, "copy.basis", required=False))

    def to_record(self) -> dict[str, str]:
        return {"path": self.path, "fact_id": self.fact_id, "kind": self.kind.value, "basis": self.basis}


@dataclass(frozen=True, slots=True)
class CanonMap:
    """Who owns what, before anything is written."""

    facts: tuple[CanonFact, ...]
    copies: tuple[CanonCopy, ...] = ()
    sources: tuple[str, ...] = ()
    status: LifecycleStatus = LifecycleStatus.PROPOSED

    def __post_init__(self) -> None:
        if not isinstance(self.status, LifecycleStatus):
            raise CanonError("canon.status는 LifecycleStatus여야 합니다.")
        facts = tuple(sorted(self.facts, key=lambda item: item.id))
        copies = tuple(sorted(self.copies, key=lambda item: (item.path, item.fact_id)))
        sources = tuple(sorted({_safe_path(item, "source") for item in self.sources}))
        ids = [fact.id for fact in facts]
        if len(ids) != len(set(ids)):
            raise CanonError("정본 fact 식별자가 중복됩니다. 사실마다 주인은 하나여야 합니다.")
        owners = {fact.id: fact.owner for fact in facts}
        seen: set[tuple[str, str]] = set()
        for copy in copies:
            if copy.fact_id not in owners:
                raise CanonError(f"사본이 알 수 없는 fact를 가리킵니다: {copy.fact_id}")
            if copy.path == owners[copy.fact_id]:
                raise CanonError(f"파일이 자기 fact의 사본일 수 없습니다: {copy.path}")
            key = (copy.path, copy.fact_id)
            if key in seen:
                raise CanonError(f"같은 사본이 두 번 선언되었습니다: {copy.path} -> {copy.fact_id}")
            seen.add(key)
        # A human's own words are input. Making them canon, or a copy of canon,
        # would let the original be rewritten to agree with a summary of it.
        made_canon = set(sources) & ({o for o in owners.values() if o != PLAN_OWNER} | {c.path for c in copies})
        if made_canon:
            raise CanonError(f"사람이 쓴 원문은 정본이나 사본이 될 수 없습니다: {sorted(made_canon)}")
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "copies", copies)
        object.__setattr__(self, "sources", sources)

    @property
    def owned_paths(self) -> frozenset[str]:
        return frozenset(fact.owner for fact in self.facts if fact.owner != PLAN_OWNER)

    @property
    def role_paths(self) -> frozenset[str]:
        return self.owned_paths | {copy.path for copy in self.copies} | frozenset(self.sources)

    @property
    def pending_count(self) -> int:
        return sum(copy.kind is CopyKind.PENDING for copy in self.copies)

    def copies_by_kind(self) -> dict[str, int]:
        return {kind.value: sum(copy.kind is kind for copy in self.copies) for kind in CopyKind}

    def extended(
        self,
        *,
        facts: Iterable[CanonFact] = (),
        copies: Iterable[CanonCopy] = (),
        sources: Iterable[str] = (),
    ) -> CanonMap:
        """A new, re-validated map with more in it. The original is untouched."""

        return replace(
            self,
            facts=(*self.facts, *facts),
            copies=(*self.copies, *copies),
            sources=(*self.sources, *sources),
        )

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": CANON_SCHEMA,
            "status": self.status.value,
            "facts": [fact.to_record() for fact in self.facts],
            "copies": [copy.to_record() for copy in self.copies],
            "sources": list(self.sources),
        }

    @property
    def canon_hash(self) -> str:
        payload = json.dumps(self._body(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "canon:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_record(self) -> dict[str, Any]:
        record = self._body()
        record["summary"] = {
            "facts": len(self.facts),
            "copies_by_kind": self.copies_by_kind(),
            "sources": len(self.sources),
            "pending": self.pending_count,
        }
        record["canon_hash"] = self.canon_hash
        record["canonical_mutation"] = False
        return record


@dataclass(frozen=True, slots=True)
class CanonFinding:
    code: CanonFindingCode
    path: str
    detail: str

    def to_record(self) -> dict[str, str]:
        return {"code": self.code.value, "path": self.path, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class CanonReport:
    """What a canon map says about the paths it was set against."""

    canon_hash: str
    findings: tuple[CanonFinding, ...]
    facts: int
    copies_by_kind: Mapping[str, int]
    pending: int

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def unclassified(self) -> tuple[str, ...]:
        return tuple(
            item.path for item in self.findings if item.code is CanonFindingCode.UNCLASSIFIED_PATH
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "canon_hash": self.canon_hash,
            "ok": self.ok,
            "facts": self.facts,
            "copies_by_kind": dict(self.copies_by_kind),
            "pending": self.pending,
            "unclassified": list(self.unclassified),
            "findings": [item.to_record() for item in self.findings],
            "canonical_mutation": False,
            "filesystem_scan": False,
        }


def check_canon(
    canon: CanonMap,
    *,
    planned_paths: Iterable[str],
    present_paths: Iterable[str] | None = None,
) -> CanonReport:
    """Set a map against the files a project plans to have, and optionally has.

    Both path collections are supplied by the caller. Nothing here looks at a
    disk, so a caller that lists a directory is the one who decided to.
    """

    planned = tuple(dict.fromkeys(_safe_path(path, "planned path") for path in planned_paths))
    planned_set = set(planned)
    findings: list[CanonFinding] = []

    roles = canon.role_paths
    for path in planned:
        if path not in roles:
            findings.append(
                CanonFinding(
                    CanonFindingCode.UNCLASSIFIED_PATH,
                    path,
                    "no fact is owned here, no fact is copied here, and it is not a source",
                )
            )
    for fact in canon.facts:
        if fact.owner != PLAN_OWNER and fact.owner not in planned_set:
            findings.append(
                CanonFinding(CanonFindingCode.OWNER_NOT_PLANNED, fact.owner, f"owner of {fact.id} is not planned")
            )
    for copy in canon.copies:
        if copy.path not in planned_set:
            findings.append(
                CanonFinding(CanonFindingCode.COPY_NOT_PLANNED, copy.path, f"copy of {copy.fact_id} is not planned")
            )

    if present_paths is not None:
        present = {_safe_path(path, "present path") for path in present_paths}
        owners = {fact.id: fact.owner for fact in canon.facts}
        for copy in canon.copies:
            owner = owners[copy.fact_id]
            # An owner not written yet is ordinary work in progress. A copy that
            # exists without it is not: it is the digest doing the owner's job.
            if copy.path in present and owner != PLAN_OWNER and owner not in present:
                findings.append(
                    CanonFinding(
                        CanonFindingCode.COPY_PRESENT_WITHOUT_OWNER,
                        copy.path,
                        f"{copy.kind.value} copy of {copy.fact_id} exists but its owner {owner} does not",
                    )
                )

    findings.sort(key=lambda item: (item.code.value, item.path, item.detail))
    return CanonReport(
        canon_hash=canon.canon_hash,
        findings=tuple(findings),
        facts=len(canon.facts),
        copies_by_kind=canon.copies_by_kind(),
        pending=canon.pending_count,
    )


class _PlannedFile(Protocol):
    path: str
    purpose: str


_INTAKE_DIR = "00_intake"
_LAYOUT_DIR = re.compile(r"^\d{2}_")
_README = "README.md"
_MANIFEST = "synapse.project.yaml"
_IDENTITY = "project_identity"


def _fact_id(path: str) -> str:
    """A fact is named for the file that owns it, so the name cannot collide."""

    return path.rsplit(".", 1)[0]


def derive_scaffold_canon(files: Iterable[_PlannedFile]) -> CanonMap:
    """The canon a new project starts with, from its layout alone.

    Deterministic and free of any model: the owner of a fact is the file the
    layout already dedicates to it, and the two files that exist only to reflect
    others -- the README and the manifest -- are declared as copies. A file the
    layout does not account for is left out on purpose, so ``check_canon`` names
    it rather than this function quietly deciding what it is.
    """

    planned = {item.path: item.purpose for item in files}
    facts: list[CanonFact] = []
    copies: list[CanonCopy] = []
    sources: list[str] = []

    for path, purpose in planned.items():
        top = path.split("/", 1)[0]
        if top == _INTAKE_DIR and "/" in path:
            sources.append(path)
        elif "/" in path and _LAYOUT_DIR.match(top):
            facts.append(CanonFact(_fact_id(path), path, purpose, "scaffold layout"))

    first_owned = facts[0] if facts else None

    if _MANIFEST in planned:
        facts.append(
            CanonFact(
                _IDENTITY,
                PLAN_OWNER,
                "project name, preset and draft status, computed from the plan",
                "scaffold plan",
            )
        )
        copies.append(CanonCopy(_MANIFEST, _IDENTITY, CopyKind.GENERATED, "rendered from the plan"))

    if _README in planned and first_owned:
        # The README restates where the preset starts; it is never the owner of anything.
        copies.append(CanonCopy(_README, first_owned.id, CopyKind.SUMMARY, "the README restates the first task's result briefly"))

    return CanonMap(facts=tuple(facts), copies=tuple(copies), sources=tuple(sources))


__all__ = [
    "CANON_SCHEMA",
    "PLAN_OWNER",
    "CanonCopy",
    "CanonError",
    "CanonFact",
    "CanonFinding",
    "CanonFindingCode",
    "CanonMap",
    "CanonReport",
    "CopyKind",
    "check_canon",
    "derive_scaffold_canon",
]
