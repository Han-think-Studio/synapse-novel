"""Say which files a change reaches, one step away or several, and what to ask about each. Decide nothing.

A project's files are not independent: a task is written from the inputs, a later task from an earlier one, the
review criteria from the tasks' checks, a summary from the file it summarizes. When one of them changes, the ones
connected to it have to be looked at again -- and the person should see them together, so that they are changed
together. This follows the connections that are already written down, outward from what changed, and lists what it
reaches with the reason and the question to ask:

* for a new idea, the preset's own task order and the plan's canon map (who owns a fact, what only repeats it);
* for an existing project, the files of the archive that name each other (what the inspection already found).

It stores nothing and adds no graph of its own: the connections are read from the preset, the canon map and the
inspection each time, so they cannot drift from them. It does not edit, regenerate or approve anything.

What it cannot see it says. A file that is not listed is not shown to be unaffected: what the words of one file say
about another is not read here, so the answer is a list to look at, never "nothing else is affected".
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from synapse.core.bundle import BundleReport
from synapse.core.canon import PLAN_OWNER, CopyKind
from synapse.core.canon_claims import TABLE_ROW, cut_files
from synapse.core.preset_pipeline import load_preset
from synapse.core.scaffold import ScaffoldPlan

IMPACT_SCHEMA = "project-impact.v1"

PROPOSED = "PROPOSED"
UNRESOLVED = "UNRESOLVED"

# How the file that is reached is connected to the one it is reached from.
FEEDS = "feeds"  # the source is an input the target is written from
BUILDS_ON = "builds_on"  # the target takes the result of an earlier task
JUDGED_BY = (
    "judged_by"  # the target sets what the project is checked against, from the source's checks
)
SUMMARIZES = "summarizes"  # the target restates the source in short
GENERATED_FROM = "generated_from"  # the target is computed from the source
CHECKED_AGAINST = "checked_against"  # the target is written apart and a named check says it agrees
PENDING = "pending"  # related, and nobody has established that they agree
NAMES = "names"  # the target is a file of an existing project that names the source
DEFINES = "defines"  # the source defines a word (a heading, a table row) that the target repeats
ASKS_OF = "asks_of"  # the source asks something of the target (a checklist item names a chapter)
ASKED_OF = (
    "asked_of"  # the target asks something of the source (the same item, when the chapter changed)
)

_ASK = {
    FEEDS: "{target} is written from {source}: check that it still says what {source} now says.",
    BUILDS_ON: "{target} takes the result of {source}: check that it still follows from it.",
    JUDGED_BY: "{target} says what the project is checked against, taken from the checks of {source}: check that it still asks the right things.",
    SUMMARIZES: "{target} restates {source} in short: check that the short version still agrees; it adds no fact of its own.",
    GENERATED_FROM: "{target} is computed from {source}: make it again rather than editing it by hand.",
    CHECKED_AGAINST: "{target} is written apart from {source} and a named check says they agree: run that check again.",
    PENDING: "{target} is related to {source} and nobody has established that they agree: this is the time to decide.",
    NAMES: "{target} names {source}: read the place where it does and see whether it still holds.",
    DEFINES: '{target} speaks of "{about}", which {source} defines: check that what it says still agrees with the definition.',
    ASKS_OF: "{source} asks something of {target}: check that {target} still does what it asks.",
    ASKED_OF: "{target} asks something of {source}: check that {source} still does what it asks.",
}
_COPY_RELATION = {
    CopyKind.GENERATED: GENERATED_FROM,
    CopyKind.CHECKED: CHECKED_AGAINST,
    CopyKind.SUMMARY: SUMMARIZES,
    CopyKind.PENDING: PENDING,
}

# The layout every preset's plan follows (scaffold.py, canon.derive_scaffold_canon).
_IDEA_FILE = "00_intake/idea.md"
_TASKS_DIR = "01_tasks/"
_REVIEW_DIR = "02_review/"

LIMIT_PLAN = (
    "A file that is not listed is not shown to be unaffected: only the preset's task order and the canon map are "
    "followed."
)
LIMIT_ARCHIVE = (
    "A file that is not listed is not shown to be unaffected: only files that name each other by path or by a "
    "unique file name are followed."
)


LIMIT_WORDS = (
    "Connections by what files say follow only a word that a heading or the first cell of a table row defines in one "
    "or two files and another file repeats; a word nobody defines, a different spelling of it, and what a sentence "
    "means are not followed."
)
LIMIT_NO_WORDS = "No file text was given, so what the files say about each other was not read."
# How much of the words of a project is followed, so that the answer stays quick on a large one.
MAX_SUBJECTS = 300
MAX_DEFINING_FILES = 2
MIN_SUBJECT_CHARS = 2
MAX_SUBJECT_CHARS = 40


class ImpactError(ValueError):
    """A request that cannot be answered as given."""


@dataclass(frozen=True, slots=True)
class Edge:
    """A change in ``source`` reaches ``target``."""

    source: str
    target: str
    relation: str
    about: str = ""

    def ask(self) -> str:
        return _ASK[self.relation].format(source=self.source, target=self.target, about=self.about)


def plan_edges(plan: ScaffoldPlan) -> tuple[Edge, ...]:
    """The connections of a new project's files, from its preset and its canon map."""

    _, preset, _ = load_preset(plan.preset.id)
    canon = plan.canon
    owner_of = {fact.id: fact.owner for fact in canon.facts}
    task_file = {task["id"]: owner_of[f"{_TASKS_DIR}{task['id']}"] for task in preset["tasks"]}
    review = sorted(owner for owner in owner_of.values() if owner.startswith(_REVIEW_DIR))
    edges: list[Edge] = []
    for source in canon.sources:
        edges.extend(Edge(source, target, FEEDS) for target in task_file.values())
    for task in preset["tasks"]:
        edges.extend(
            Edge(task_file[earlier], task_file[task["id"]], BUILDS_ON)
            for earlier in task["depends_on"]
        )
    for source in task_file.values():
        edges.extend(Edge(source, target, JUDGED_BY) for target in review)
    for copy in canon.copies:
        owner = owner_of[copy.fact_id]
        # What the plan computes, it computes from the idea.
        source = _IDEA_FILE if owner == PLAN_OWNER else owner
        edges.append(Edge(source, copy.path, _COPY_RELATION[copy.kind]))
    return tuple(dict.fromkeys(edges))


def archive_edges(report: BundleReport) -> tuple[Edge, ...]:
    """The connections of an existing project's files: a file that names another is reached from it."""

    return tuple(
        Edge(reference.target, entry.path, NAMES)
        for entry in report.entries
        for reference in entry.references
    )


def _subject_words(heading: str) -> str:
    """What a heading names: the part before a dash or a colon."""

    return re.split(r"\s*[\u2014:\uff1a]\s*|\s+-\s+", heading.strip(), maxsplit=1)[0].strip()


def _defined_words(files: Mapping[str, str]) -> dict[str, set[str]]:
    """Each word that a heading or the first cell of a table row defines, with the files that define it."""

    defined: dict[str, set[str]] = {}
    for claim in cut_files(files):
        words = [_subject_words(part) for part in claim.section.split(" / ") if part.strip()]
        if claim.kind == TABLE_ROW and claim.fields and claim.fields[0][1].strip():
            words.append(claim.fields[0][1].strip())
        for word in words:
            if MIN_SUBJECT_CHARS <= len(word) <= MAX_SUBJECT_CHARS:
                defined.setdefault(word, set()).add(claim.file)
    return defined


def _says(text: str, word: str) -> bool:
    """Whether ``text`` repeats ``word`` as a word of its own."""

    # A word that starts or ends with a Latin letter or a digit is not found inside a longer one ("art" is not in
    # "partial", "9주" is not in "19주"). Hangul is left alone: particles join the word ("강민재가").
    before = r"(?<![0-9A-Za-z])" if word[0].isascii() and word[0].isalnum() else ""
    after = r"(?![0-9A-Za-z])" if word[-1].isascii() and word[-1].isalnum() else ""
    if not before and not after:
        return word in text
    return re.search(before + re.escape(word) + after, text) is not None


def word_edges(
    files: Mapping[str, str], known: Collection[str]
) -> tuple[tuple[Edge, ...], list[str]]:
    """Connections from what the files say: a file that defines a word reaches the files that repeat it.

    A word is defined by a heading (the part before a dash or colon) or by the first cell of a table row. A word that
    more than ``MAX_DEFINING_FILES`` files define is a kind of section (an overview, a goal), not a subject, and is
    left out. Only files in ``known`` take part. Returns the edges and the notices about anything that was cut short.
    """

    texts = {name: text.casefold() for name, text in files.items() if name in known}
    defined = {
        word: owners & texts.keys()
        for word, owners in _defined_words({name: files[name] for name in texts}).items()
    }
    subjects = sorted(
        word for word, owners in defined.items() if owners and len(owners) <= MAX_DEFINING_FILES
    )
    notices = []
    if len(subjects) > MAX_SUBJECTS:
        notices.append(
            f"Only the first {MAX_SUBJECTS} of {len(subjects)} defined words were followed (in alphabetical order)."
        )
        subjects = subjects[:MAX_SUBJECTS]
    edges = []
    for word in subjects:
        folded = word.casefold()
        for name, text in sorted(texts.items()):
            if name in defined[word] or not _says(text, folded):
                continue
            edges.extend(Edge(owner, name, DEFINES, word) for owner in sorted(defined[word]))
    return tuple(dict.fromkeys(edges)), notices


@dataclass(frozen=True, slots=True)
class Reach:
    """How far a change gets to one file, and by which connections."""

    path: str
    distance: int
    route: tuple[Edge, ...]


def reach(edges: Iterable[Edge], changed: Sequence[str]) -> dict[str, Reach]:
    """Every file the changed ones reach, nearest first, each with one shortest route (the changed ones excluded).

    Breadth first with a visited set, so a cycle ends; among routes of equal length the first by name is kept.
    """

    outgoing: dict[str, list[Edge]] = {}
    for edge in sorted(set(edges), key=lambda e: (e.source, e.target, e.relation, e.about)):
        outgoing.setdefault(edge.source, []).append(edge)
    found: dict[str, Reach] = {}
    seen = set(changed)
    queue: deque[tuple[str, tuple[Edge, ...]]] = deque((path, ()) for path in sorted(set(changed)))
    while queue:
        path, route = queue.popleft()
        for edge in outgoing.get(path, ()):
            if edge.target in seen:
                continue
            seen.add(edge.target)
            ahead = (*route, edge)
            found[edge.target] = Reach(edge.target, len(ahead), ahead)
            queue.append((edge.target, ahead))
    return found


def _because(edge: Edge) -> dict[str, str]:
    reason = {"from": edge.source, "relation": edge.relation}
    if edge.about:
        reason["about"] = edge.about
    return reason


def _reasons_in(edges: Iterable[Edge], changed: Collection[str], path: str) -> list[Edge]:
    """Every connection from a changed file straight into ``path``: a file reached from two of them says both."""

    return sorted(
        (e for e in set(edges) if e.target == path and e.source in changed),
        key=lambda e: (e.source, e.relation, e.about),
    )


def _changed(raw: Sequence[str], known: Collection[str]) -> tuple[list[str], list[str]]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ImpactError("`changed` is a list of relative paths.")
    if not all(isinstance(path, str) for path in raw):
        raise ImpactError("`changed` is a list of relative paths.")
    paths = sorted(path.replace("\\", "/") for path in raw)
    errors: list[str] = []
    if not paths:
        errors.append("changed_paths_missing")
    if len(paths) != len(set(paths)):
        errors.append("changed_paths_duplicate")
    errors.extend(
        f"unknown_changed_path:{path}" for path in dict.fromkeys(paths) if path not in known
    )
    return paths, errors


def _review(
    edges: tuple[Edge, ...],
    known: Collection[str],
    changed: Sequence[str],
    *,
    source: str,
    limits: Sequence[str],
    values: Iterable[tuple[str, str, str]],
    extra_limits: Sequence[str] = (),
    files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    paths, errors = _changed(changed, known)
    structure_count = len(set(edges))
    notices: list[str] = []
    if files is None:
        limits = [*limits, LIMIT_NO_WORDS]
    else:
        by_words, notices = word_edges(files, known)
        edges = (*edges, *by_words)
        limits = [*limits, LIMIT_WORDS]
    record: dict[str, Any] = {
        "schema_version": IMPACT_SCHEMA,
        "source": source,
        "changed": paths,
        "errors": errors,
        "upstream": [],
        "direct": [],
        "indirect": [],
        "values_from_changed_files": [],
        "followed": {"structure": structure_count, "words": len(set(edges)) - structure_count},
        "not_shown": [*limits, *notices, *extra_limits],
        "canonical_mutation": False,
        "filesystem_mutation": False,
        "provider_called": False,
        "execution_allowed": False,
    }
    if errors:
        record["status"] = UNRESOLVED
        return record
    # A changed file that itself rests on another (a summary, a generated copy) is asked about too.
    for path in paths:
        behind = sorted(
            (e for e in set(edges) if e.target == path and e.source not in paths),
            key=lambda e: (e.source, e.relation, e.about),
        )
        if behind:
            record["upstream"].append(
                {
                    "path": path,
                    "because": [_because(e) for e in behind],
                    "ask": [e.ask() for e in behind],
                }
            )
    reached = reach(edges, paths)
    for path in sorted(reached, key=lambda p: (reached[p].distance, p)):
        step = reached[path]
        if step.distance == 1:
            into = _reasons_in(edges, paths, path)
            record["direct"].append(
                {
                    "path": path,
                    "because": [_because(e) for e in into],
                    "ask": [e.ask() for e in into],
                }
            )
        else:
            hops = [step.route[0].source, *(e.target for e in step.route)]
            record["indirect"].append(
                {
                    "path": path,
                    "distance": step.distance,
                    "route": hops,
                    "ask": [step.route[-1].ask()],
                }
            )
    record["values_from_changed_files"] = [
        {"key": key, "file": file, "status": status}
        for key, file, status in sorted(values)
        if file in set(paths)
    ]
    record["status"] = PROPOSED
    return record


def review_plan_impact(
    plan: ScaffoldPlan,
    changed: Sequence[str],
    *,
    values: Iterable[tuple[str, str, str]] = (),
    files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """What a change to the named files of a new project's plan reaches.

    With ``files`` (path -> text), the words the files say about each other are followed too.
    """

    return _review(
        plan_edges(plan),
        [item.path for item in plan.files],
        changed,
        source="plan",
        limits=[LIMIT_PLAN],
        values=values,
        files=files,
    )


def review_texts_impact(
    texts: Mapping[str, str],
    changed: Sequence[str],
    *,
    edges: Iterable[Edge] = (),
    source: str = "texts",
    limits: Sequence[str] = (),
    values: Iterable[tuple[str, str, str]] = (),
) -> dict[str, Any]:
    """What a change to one of a set of named texts reaches.

    For a caller that has its own units (the chapters of a manuscript, say) and the connections it knows between
    them. The words the texts repeat from each other are followed too.
    """

    known = set(texts)
    return _review(
        tuple(edge for edge in edges if edge.source in known and edge.target in known),
        list(texts),
        changed,
        source=source,
        limits=limits,
        values=values,
        files=texts,
    )


def review_archive_impact(
    report: BundleReport,
    changed: Sequence[str],
    *,
    values: Iterable[tuple[str, str, str]] = (),
    files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """What a change to the named files of an existing project (an inspected archive) reaches.

    With ``files`` (path -> text), the words the files say about each other are followed too.
    """

    unseen = {
        entry.path: [m.target for m in entry.missing_references]
        for entry in report.entries
        if entry.missing_references
    }
    paths = {path.replace("\\", "/") for path in changed if isinstance(path, str)}
    extra = [
        f"{path} names documents that are not in the archive: {', '.join(unseen[path])}"
        for path in sorted(paths & set(unseen))
    ]
    return _review(
        archive_edges(report),
        [entry.path for entry in report.entries],
        changed,
        source="archive",
        limits=[LIMIT_ARCHIVE],
        values=values,
        extra_limits=extra,
        files=files,
    )


def as_reasons(edges: Iterable[Edge], changed: str, reached: Mapping[str, Reach]) -> dict[str, str]:
    """One sentence per reached file, for a caller that wants a reason beside each path."""

    return {
        path: " ".join(e.ask() for e in _reasons_in(edges, {changed}, path))
        if step.distance == 1
        else step.route[-1].ask()
        for path, step in reached.items()
    }


__all__ = [
    "ASKED_OF",
    "ASKS_OF",
    "BUILDS_ON",
    "CHECKED_AGAINST",
    "DEFINES",
    "FEEDS",
    "GENERATED_FROM",
    "IMPACT_SCHEMA",
    "JUDGED_BY",
    "LIMIT_ARCHIVE",
    "LIMIT_NO_WORDS",
    "LIMIT_PLAN",
    "LIMIT_WORDS",
    "NAMES",
    "PENDING",
    "PROPOSED",
    "SUMMARIZES",
    "UNRESOLVED",
    "Edge",
    "ImpactError",
    "Reach",
    "archive_edges",
    "as_reasons",
    "plan_edges",
    "reach",
    "review_archive_impact",
    "review_plan_impact",
    "word_edges",
]
