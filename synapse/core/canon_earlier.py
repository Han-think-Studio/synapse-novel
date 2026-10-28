"""What the project has already decided, found before anyone is asked again.

The mistake this prevents is a small one that cost a day once: a name was offered
to the owner as if nothing had ever been said about it, when a file of the project
had said, in plain words and not yet overturned, which name it was. The owner then
chose the opposite of what they had decided and had to be told. Whatever was
decided last may well be the file nobody has looked at lately.

So before a question is put, the decisions the files themselves state are looked up
and shown with it: where they are written and what they say. Nothing is concluded
from them. They are not the answer, they are what the question has to be asked
against -- keep it or change it.

A decision here is a line a document wrote to say one was made, found when the
archive was inspected (see ``BundleDecision``). This reads nothing itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from synapse.core.bundle import BundleReport

# A question is not buried under what the project once said about it.
DEFAULT_PER_FILE = 3
DEFAULT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class EarlierDecision:
    """A decision a file states, and where."""

    path: str
    line: int
    text: str

    def cite(self) -> str:
        return f"{self.path}:{self.line} “{self.text}”"

    def to_record(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "text": self.text}


def earlier_decisions(
    report: BundleReport,
    *,
    in_files: Iterable[str] = (),
    mentioning: Iterable[str] = (),
    per_file: int = DEFAULT_PER_FILE,
    limit: int = DEFAULT_LIMIT,
) -> tuple[EarlierDecision, ...]:
    """The decisions already written down about, or in, what a question concerns.

    A decision qualifies if it is written in one of ``in_files``, or its line names
    one of ``mentioning`` (compared without regard to case). Those in the files
    given come first, in the order the files were given; at most ``per_file`` from
    one file and ``limit`` in all.
    """

    wanted = list(dict.fromkeys(in_files))
    needles = [item.casefold() for item in dict.fromkeys(mentioning) if item.strip()]
    by_path = {entry.path: entry for entry in report.entries}

    found: list[EarlierDecision] = []
    taken: set[tuple[str, int]] = set()
    from_file: dict[str, int] = {}  # across both passes: a file asked for both ways still gives per_file at most

    def add(path: str, decisions: Iterable[Any]) -> None:
        for decision in decisions:
            if from_file.get(path, 0) >= per_file or len(found) >= limit:
                return
            if (path, decision.line) not in taken:
                taken.add((path, decision.line))
                found.append(EarlierDecision(path, decision.line, decision.text))
                from_file[path] = from_file.get(path, 0) + 1

    for path in wanted:
        if path in by_path:
            add(path, by_path[path].decisions)
    if needles:
        for path in sorted(by_path):
            add(
                path,
                [d for d in by_path[path].decisions if any(needle in d.text.casefold() for needle in needles)],
            )
    return tuple(found)


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_PER_FILE",
    "EarlierDecision",
    "earlier_decisions",
]
