"""Say whether each confirmed canon value is settled, and if not, what it is still waiting on. Decide nothing.

A value the person confirmed is not necessarily settled. The words it was taken from may have changed, the
file it lives in may not have been looked at this time, another source may confirm something different under
the same key, or a point about that file may still be open. This gathers those in one place, per value.

It adds no state word and no code of its own. The words are the ones ``canon_gate`` already uses, in the same
order of weight (UNRESOLVED, then STALE, then WAITING). A reason is a *pointer* to something that already
exists -- ``value:<key>``, ``claim:<id>``, ``gap:<key>``, ``file:<path>``, ``gate:<item id>`` -- not a new
vocabulary. A value is closed when it has no reason at all.

What closing does not mean: "I know this is still open; go on" (an acknowledgement) never closes anything; it
is shown beside the reason it was given for. A record with nothing to close is not closed, and neither is a
record while values the project still lacks are known (``missing``). READY is not approval.

Values may be connected: ``depends_on`` (this one rests on that one) or ``conflicts_with`` (the two are in
tension). A value is then also open while a value it is connected to is open, or has changed (new words, or a
source gone stale) since the person last settled it. That reaction is not worked out here: the values are projected, read only, onto the items the
Resolver already understands and its own ``propagate`` is asked, in its conservative mode, who is affected. The
Registry is never written to, so nothing is promoted (``canonical_promotion``) and no repair rule is applied.
The order of the store's history says which came later; the clock strings it carries are not trusted.

It reads nothing and writes nothing. It is given the values, the claims as they are now, the files that were
actually looked at, optionally the structure review with the files each of its points concerns, and the links
the person has declared. The same input gives the same answer, and the closure is computed, never stored
(DEC-0203).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from synapse.core.canon_check import CanonValue
from synapse.core.canon_claims import Claim
from synapse.core.canon_gaps import find_gaps
from synapse.core.canon_gate import STALE, UNRESOLVED, WAITING, StructureReview
from synapse.core.canon_values import ValueStore, checkable, source_current
from synapse.core.contracts import Fact, LifecycleStatus, Provenance
from synapse.core.idea_session import canonical_hash
from synapse.core.resolver import ResolverBudgetExceeded, propagate

CLOSURE_SCHEMA = "canon-closure.v1"

# Heaviest first: what is said when more than one reason holds. The same order as ``canon_gate``.
_WEIGHT = (UNRESOLVED, STALE, WAITING)

# What a reason points at. Each is something that already exists; none is a new status.
VALUE = "value"
CLAIM = "claim"
GAP = "gap"
FILE = "file"
GATE = "gate"
LINK = "link"

# The two relations a person can declare between values. ``supersedes`` is the Registry's, not ours.
DEPENDS_ON = "depends_on"
CONFLICTS_WITH = "conflicts_with"
_RELATIONS = frozenset({DEPENDS_ON, CONFLICTS_WITH})

# One propagation may re-mark at most this many values; more is refused rather than cut short.
MAX_REMARKS = 10_000

# ``canon_gaps`` says a key was confirmed with different words this way; every other kind is a missing value.
_CONFLICT_KIND = "value_conflict"

_NODE = "value:"  # how a value is named as an item the Resolver can read


class ClosureError(ValueError):
    """Raised when the input cannot be taken as given."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Link:
    """A connection the person declared between two values.

    ``depends_on``: ``from_key`` rests on ``to_key``, so it must be looked at again when ``to_key`` moves.
    ``conflicts_with``: the two are in tension, and each must be looked at again when the other moves.
    ``ref`` says where it was declared (the confirmation-table row, once there is one); it names the link when
    one end is not a value this store holds.
    """

    relation: str
    from_key: str
    to_key: str
    ref: str = ""

    def __post_init__(self) -> None:
        if self.relation not in _RELATIONS:
            raise ClosureError(
                f"연결의 종류는 {sorted(_RELATIONS)} 중 하나여야 합니다: {self.relation!r}"
            )
        for name in ("from_key", "to_key", "ref"):
            if not isinstance(getattr(self, name), str):
                raise ClosureError(f"연결의 {name}은(는) 문자열이어야 합니다.")
        if not self.from_key or not self.to_key:
            raise ClosureError("연결의 양 끝에는 값의 키가 필요합니다.")
        if self.from_key == self.to_key:
            raise ClosureError("값은 자기 자신과 연결할 수 없습니다.")

    @property
    def pointer(self) -> str:
        return f"{LINK}:{self.ref or f'{self.relation}:{self.from_key}->{self.to_key}'}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Reason:
    """One thing a value is still waiting on, and whether the person has said to go on regardless."""

    pointer: str
    state: str
    acknowledged: bool = False

    def to_record(self) -> dict[str, Any]:
        return {"pointer": self.pointer, "state": self.state, "acknowledged": self.acknowledged}


@dataclass(frozen=True, slots=True, kw_only=True)
class ValueClosure:
    """One value, and why it is not settled yet -- or that it is."""

    key: str
    reasons: tuple[Reason, ...] = ()

    @property
    def state(self) -> str | None:
        """The heaviest word among the reasons, or None when there are none."""

        held = {reason.state for reason in self.reasons}
        for word in _WEIGHT:
            if word in held:
                return word
        return None

    @property
    def closed(self) -> bool:
        return not self.reasons

    @property
    def acknowledged(self) -> bool:
        """Every reason has been passed over by the person. The value is still open."""

        return bool(self.reasons) and all(reason.acknowledged for reason in self.reasons)

    def to_record(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "state": self.state,
            "closed": self.closed,
            "acknowledged": self.acknowledged,
            "reasons": [reason.to_record() for reason in self.reasons],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ClosureReview:
    """Every confirmed or proposed value, and the values the project still lacks."""

    project_id: str
    archive_sha256: str
    values: tuple[ValueClosure, ...]
    # Pointers (``gap:<key>``) to values the project is known to lack. Not about any one value.
    missing: tuple[str, ...] = ()
    # Pointers (``link:<ref>``) to declared links with an end that is not a value of this store.
    broken: tuple[str, ...] = ()
    schema_version: str = CLOSURE_SCHEMA

    @property
    def all_closed(self) -> bool:
        """At least one value, every one closed, nothing known to be missing, no link cut."""

        return (
            bool(self.values)
            and not self.missing
            and not self.broken
            and all(value.closed for value in self.values)
        )

    @property
    def counts(self) -> dict[str, int]:
        states = Counter(value.state for value in self.values)
        return {
            "values": len(self.values),
            "closed": sum(value.closed for value in self.values),
            "waiting": states[WAITING],
            "unresolved": states[UNRESOLVED],
            "stale": states[STALE],
            "acknowledged_open": sum(value.acknowledged for value in self.values),
            "missing": len(self.missing),
            "broken": len(self.broken),
        }

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "archive_sha256": self.archive_sha256,
            "all_closed": self.all_closed,
            "empty": not self.values,
            "counts": self.counts,
            "values": [value.to_record() for value in self.values],
            "missing": list(self.missing),
            "broken": list(self.broken),
            "approval_recorded": False,
            "execution_allowed": False,
            "model_invoked": False,
            "canonical_mutation": False,
            "filesystem_scan": False,
        }
        record["closure_hash"] = canonical_hash(record)
        return record


def _names(value: Any, label: str) -> frozenset[str]:
    if isinstance(value, str) or not isinstance(value, Collection):
        raise ClosureError(f"{label}은(는) 문자열의 묶음이어야 합니다.")
    if not all(isinstance(item, str) for item in value):
        raise ClosureError(f"{label}의 항목은 문자열이어야 합니다.")
    return frozenset(value)


def _gate_points(
    gate: StructureReview | None, gate_files: Mapping[str, Collection[str]] | None
) -> dict[str, list[Any]]:
    """The open points of the structure review, by the file each one concerns.

    A point that is not named in ``gate_files`` cannot be tied to a file, so it is left out rather than guessed at.
    """

    if gate is None:
        return {}
    if not isinstance(gate, StructureReview):
        raise ClosureError("gate는 StructureReview이어야 합니다.")
    named = {} if gate_files is None else gate_files
    if not isinstance(named, Mapping):
        raise ClosureError("gate_files는 항목 id에서 파일 묶음으로 가는 mapping이어야 합니다.")
    by_file: dict[str, list[Any]] = {}
    for item in gate.items:
        if not item.is_open or item.id not in named:
            continue
        for path in sorted(_names(named[item.id], f"gate_files[{item.id!r}]")):
            by_file.setdefault(path, []).append(item)
    return by_file


_CONFIRMS = frozenset({"confirm", "revise"})  # history actions by which the person settles a value

_STATUS = {
    "CONFIRMED": LifecycleStatus.CONFIRMED,
    "PROPOSED": LifecycleStatus.PROPOSED,
    "STALE": LifecycleStatus.UNRESOLVED,  # it has to be looked at again
}


def _weight(state: str) -> int:
    return _WEIGHT.index(state)


def _merged(found: Iterable[Reason]) -> tuple[Reason, ...]:
    """Each pointer once, with the heaviest word it was given, in pointer order."""

    kept: dict[str, Reason] = {}
    for reason in found:
        held = kept.get(reason.pointer)
        if held is None or _weight(reason.state) < _weight(held.state):
            kept[reason.pointer] = reason
    return tuple(kept[pointer] for pointer in sorted(kept))


def _positions(store: ValueStore) -> tuple[dict[str, int], dict[str, int]]:
    """Where in the store's history each value last changed, and where the person last settled it.

    A value changes when its words are revised to something different, or when its source goes stale. Confirming
    it, or confirming it again with the same words, is settling, not changing: it is the Registry's rule too
    that creating an item reacts nothing while updating one does. Positions, not times: the store keeps its
    history in order, while the clock strings in it are whatever the caller wrote.
    """

    changed: dict[str, int] = {}
    settled: dict[str, int] = {}
    for position, record in enumerate(store.history):
        entry, previous, action = record["entry"], record["previous"], record["action"]
        key = entry["key"]
        if action in _CONFIRMS:
            settled[key] = position
        if action == "stale" or (
            action == "revise" and previous is not None and previous["text"] != entry["text"]
        ):
            changed[key] = position
    return changed, settled


def _project(
    store: ValueStore, depends: Mapping[str, set[str]], conflicts: Mapping[str, set[str]]
) -> dict[str, Fact]:
    """The values as items the Resolver reads. Read only: nothing here is a Registry item or a promotion."""

    items: dict[str, Fact] = {}
    for entry in store.entries:
        stamp = entry.confirmed_at or ""
        subject, _, predicate = entry.key.rpartition(".")
        provenance = ()
        if entry.status == "CONFIRMED":
            provenance = (
                Provenance(
                    source_id=entry.claim_id or entry.key,
                    method="person_confirmed",
                    locator=f"{entry.file}:{entry.line}",
                    captured_at=stamp,
                ),
            )
        items[_NODE + entry.key] = Fact(
            id=_NODE + entry.key,
            owner="canon_values",
            subject_id=subject or entry.key,
            predicate=predicate or "value",
            value=entry.text,
            status=_STATUS[entry.status],
            provenance=provenance,
            depends_on=tuple(_NODE + key for key in sorted(depends.get(entry.key, ()))),
            conflicts_with=tuple(_NODE + key for key in sorted(conflicts.get(entry.key, ()))),
            created_at=stamp,
            updated_at=stamp,
        )
    return items


def _connect(
    store: ValueStore, links: tuple[Link, ...], found_by_key: dict[str, list[Reason]]
) -> tuple[str, ...]:
    """Add the reasons that come from connected values; return the links with an end this store lacks.

    A value that is confirmed and rests on, or is in tension with, another is looked at again when that other is
    open now, or changed (new words, or a source gone stale) after the person last settled this one. Which values that reaches, once or through a
    chain, is the Resolver's own ``propagate`` in its conservative mode: no repair rule, so nothing stays
    confirmed on the strength of a guess.
    """

    held = {entry.key: entry for entry in store.entries}
    depends: dict[str, set[str]] = {}
    conflicts: dict[str, set[str]] = {}
    flows: set[tuple[str, str]] = (
        set()
    )  # (source, dependent): when the source moves, the dependent is re-read
    broken: set[str] = set()
    for link in links:
        if link.from_key not in held or link.to_key not in held:
            broken.add(link.pointer)
        elif link.relation == DEPENDS_ON:
            depends.setdefault(link.from_key, set()).add(link.to_key)
            flows.add((link.to_key, link.from_key))
        else:
            conflicts.setdefault(link.from_key, set()).add(link.to_key)
            conflicts.setdefault(link.to_key, set()).add(link.from_key)
            flows.update({(link.from_key, link.to_key), (link.to_key, link.from_key)})
    if flows:
        items = _project(store, depends, conflicts)
        changed, settled = _positions(store)
        open_keys = {key for key, reasons in found_by_key.items() if reasons}
        triggers = sorted(
            {
                source
                for source, dependent in flows
                if held[dependent].status == "CONFIRMED"
                and (source in open_keys or changed.get(source, -1) > settled.get(dependent, -1))
            }
        )
        for trigger in triggers:
            try:
                result = propagate(
                    items.values(), _NODE + trigger, mode="conservative", max_remarks=MAX_REMARKS
                )
            except ResolverBudgetExceeded as exc:
                raise ClosureError("연결을 따라 다시 볼 값이 한도를 넘었습니다.") from exc
            for change in result.changes:
                found_by_key[change.id.removeprefix(_NODE)].append(
                    Reason(
                        pointer=f"{VALUE}:{change.trigger_id.removeprefix(_NODE)}", state=UNRESOLVED
                    )
                )
    return tuple(sorted(broken))


MAX_LINKS = 1000
_LINK_KEYS = frozenset({"relation", "from_key", "to_key", "ref"})


def links_from_records(raw: object) -> tuple[Link, ...]:
    """Links as a person or a caller wrote them down: a list of ``{relation, from_key, to_key, ref?}``."""

    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_LINKS:
        raise ClosureError(
            f"`links` is a list of {{relation, from_key, to_key, ref?}}, at most {MAX_LINKS}."
        )
    for item in raw:
        if (
            not isinstance(item, dict)
            or not {"relation", "from_key", "to_key"} <= set(item)
            or set(item) - _LINK_KEYS
        ):
            raise ClosureError("each link is {relation, from_key, to_key, ref?}.")
    return tuple(Link(**item) for item in raw)


def review_value_closure(
    store: ValueStore,
    claims: Iterable[Claim],
    *,
    examined_files: Collection[str],
    more_values: Iterable[CanonValue] = (),
    gate: StructureReview | None = None,
    gate_files: Mapping[str, Collection[str]] | None = None,
    links: Iterable[Link] = (),
) -> ClosureReview:
    """Every value of one store, set against the claims as they are now and the files actually looked at.

    ``examined_files`` is required: a file nobody says was looked at is not known to be settled, so a value
    from it waits. ``more_values`` are confirmed values from another source, so that two sources confirming
    different words under one key show up. ``gate_files`` names, per point of ``gate``, the files it concerns.
    ``links`` are the connections the person declared between values; with none, nothing reacts.
    """

    if not isinstance(store, ValueStore):
        raise ClosureError("store는 ValueStore이어야 합니다.")
    claim_items = tuple(claims)
    if not all(isinstance(claim, Claim) for claim in claim_items):
        raise ClosureError("claims는 Claim의 묶음이어야 합니다.")
    examined = _names(examined_files, "examined_files")
    others = tuple(more_values)
    if not all(isinstance(value, CanonValue) for value in others):
        raise ClosureError("more_values는 CanonValue의 묶음이어야 합니다.")
    open_by_file = _gate_points(gate, gate_files)
    link_items = tuple(links)
    if not all(isinstance(link, Link) for link in link_items):
        raise ClosureError("links는 Link의 묶음이어야 합니다.")

    by_claim = {claim.id: claim for claim in claim_items}
    gaps = find_gaps(claim_items, (*checkable(store), *others))
    conflicting = {gap.key for gap in gaps if gap.kind == _CONFLICT_KIND}
    missing = tuple(sorted({f"{GAP}:{gap.key}" for gap in gaps if gap.kind != _CONFLICT_KIND}))

    found_by_key: dict[str, list[Reason]] = {}
    for entry in sorted(store.entries, key=lambda item: item.key):
        found: list[Reason] = []
        if entry.status == "PROPOSED":
            found.append(Reason(pointer=f"{VALUE}:{entry.key}", state=WAITING))
        # The words are only compared for a file that was looked at; for one that was not, nothing is known.
        if entry.status == "STALE" or (
            entry.file in examined and not source_current(entry, by_claim.get(entry.claim_id))
        ):
            found.append(Reason(pointer=f"{CLAIM}:{entry.claim_id}", state=STALE))
        if entry.key in conflicting:
            found.append(Reason(pointer=f"{GAP}:{entry.key}", state=UNRESOLVED))
        if entry.file not in examined:
            found.append(Reason(pointer=f"{FILE}:{entry.file}", state=WAITING))
        found.extend(
            Reason(
                pointer=f"{GATE}:{item.id}",
                state=item.state,
                acknowledged=item.acknowledgement is not None,
            )
            for item in open_by_file.get(entry.file, ())
        )
        found_by_key[entry.key] = found

    broken = _connect(store, link_items, found_by_key)
    closures = tuple(
        ValueClosure(key=key, reasons=_merged(found_by_key[key])) for key in sorted(found_by_key)
    )
    return ClosureReview(
        project_id=store.project_id,
        archive_sha256=store.archive_sha256,
        values=closures,
        missing=missing,
        broken=broken,
    )


__all__ = [
    "CLAIM",
    "CLOSURE_SCHEMA",
    "CONFLICTS_WITH",
    "DEPENDS_ON",
    "FILE",
    "GAP",
    "GATE",
    "LINK",
    "MAX_LINKS",
    "MAX_REMARKS",
    "VALUE",
    "ClosureError",
    "ClosureReview",
    "Link",
    "Reason",
    "ValueClosure",
    "links_from_records",
    "review_value_closure",
]
