"""A new idea is asked the same things as a project that is already underway.

A project underway reaches the canon questions as the report of its archive. A new idea has
no archive, only a plan that names the files the project will have. This turns the plan into
the report such a project would have on its first day -- the planned files, none of them
written -- so the owner questions, the confirmation table and the structure gate work on it
exactly as they work on an archive, with no second way of doing any of it.

The layout already proposes which file decides each fact. That proposal is not taken as the
answer: the planned files are only what the person is offered, and the person says which one
decides each kind of fact, or that nothing does yet. Nor is the table filled in for them: one
row is made for each pair of files that will say the same thing with nothing yet checking that
they agree, and the owner answers it.

Nothing is read and nothing is written here. The report is made in memory from a plan that was
itself made in memory, and it says so (``planned_only``): it is not an observation of anything.
"""

from __future__ import annotations

import hashlib
import json

from synapse.core.bundle import BundleEntry, BundleReport
from synapse.core.canon import CopyKind
from synapse.core.canon_table import ConfirmationItem, ConfirmationTable
from synapse.core.scaffold import ScaffoldPlan

# What every row of this module's table starts with, as the mismatch rows start with theirs, so a
# row put there by someone else can be told from these.
PENDING_COPY_ID_PREFIX = "pending-copy:"

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def report_of_idea(plan: ScaffoldPlan) -> BundleReport:
    """The project as it stands on its first day: every planned file, none of them written."""

    if not isinstance(plan, ScaffoldPlan):
        raise TypeError("새 아이디어의 보고서는 ScaffoldPlan에서만 만듭니다.")
    paths = sorted(item.path for item in plan.files)
    titles = {item.path: item.title for item in plan.files}
    # The same plan gives the same hash, so an answer given to this idea reads as given to it
    # on another day; a different idea is a different project and its answers lapse.
    identity = json.dumps({"plan": plan.id, "files": paths}, sort_keys=True, ensure_ascii=False)
    return BundleReport(
        archive=f"idea:{plan.id}",
        archive_sha256=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        entry_count=len(paths),
        total_uncompressed_size=0,
        roots=tuple(sorted({path.split("/", 1)[0] for path in paths})),
        key_files=(),
        entries=tuple(
            BundleEntry(path=path, size=0, sha256=_EMPTY_SHA256, kind="text", title=titles[path], headings=(titles[path],))
            for path in paths
        ),
        manifest=None,
        content_summary={
            "text_files": 0,
            "markdown_files": sum(1 for path in paths if path.endswith(".md")),
            "marker_file_counts": {},
            "titles": dict(sorted(titles.items())),
            "planned_only": True,
        },
    )


def table_of_idea(plan: ScaffoldPlan, project_id: str, *, owner: str = "human") -> ConfirmationTable:
    """One row for each pair of planned files that will say the same thing and are not yet checked.

    Only the relations the layout leaves unchecked become rows (``PENDING``). A README that
    restates the goals by design, or a manifest rendered from the plan, is not a disagreement
    waiting to happen, so it is not asked about.
    """

    if not isinstance(plan, ScaffoldPlan):
        raise TypeError("새 아이디어의 확인표는 ScaffoldPlan에서만 만듭니다.")
    canon = plan.canon
    owners = {fact.id: fact.owner for fact in canon.facts}
    items = []
    for copy in canon.copies:
        if copy.kind is not CopyKind.PENDING or copy.fact_id not in owners:
            continue
        items.append(
            ConfirmationItem(
                id=f"{PENDING_COPY_ID_PREFIX}{copy.path}:{copy.fact_id}",
                question=(
                    f"'{copy.path}'와(과) '{owners[copy.fact_id]}'는 같은 내용을 따로 적게 되는데 "
                    f"({copy.basis}). 어떻게 할까요?"
                ),
            )
        )
    return ConfirmationTable(project_id=project_id, owner=owner, items=tuple(items))


__all__ = [
    "PENDING_COPY_ID_PREFIX",
    "report_of_idea",
    "table_of_idea",
]
