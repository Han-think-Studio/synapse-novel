"""The one way to ask what a change reaches, for every door that offers it.

The command line, the MCP tool and the console each take a request in their own shape and each has its own rules
about where a file may be read from. What they do after that is the same, and it is here, once: build the plan or
inspect the archive, bring the texts, ask ``impact_review``, and say in an ``evidence`` envelope how the answer came to
know what it says. Reads only; nothing is written, stored or approved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from synapse.core import canon_evidence, impact_review
from synapse.core.bundle import archive_texts, inspect_bundle
from synapse.core.canon_values import load as load_values
from synapse.core.scaffold import build_scaffold_plan

READ_VALUES = tuple[str, str, str]


def stored_values(values_file: str | Path) -> list[READ_VALUES]:
    """(key, file, status) of the values in a values file, as stored: nothing is re-read or re-judged here."""

    loaded = load_values(Path(values_file), (), only_files=())
    return [(entry.key, entry.file, entry.status) for entry in loaded.entries]


def for_archive(
    archive: str | Path,
    changed: Sequence[str],
    *,
    values: Sequence[READ_VALUES] = (),
) -> dict[str, Any]:
    """What a change reaches in an existing project, a ZIP that is opened here and never extracted."""

    path = Path(archive)
    report = inspect_bundle(path)
    result = impact_review.review_archive_impact(
        report, changed, values=values, files=archive_texts(report)
    )
    result["evidence"] = canon_evidence.read({path.name: f"sha256:{report.archive_sha256}"})
    return result


def for_idea(
    idea: str,
    preset_id: str,
    changed: Sequence[str],
    *,
    project_name: str = "",
    files: Mapping[str, str] | None = None,
    files_read: Mapping[str, str] | None = None,
    values: Sequence[READ_VALUES] = (),
) -> dict[str, Any]:
    """What a change reaches in a new project made from ``idea`` and one preset.

    ``files`` are the texts of the project's files, if the caller has them. If the caller read them from disk it also
    passes ``files_read`` (the sha256 of each file's bytes) and the answer says it read them; otherwise the texts were
    only handed over, and it says that.
    """

    for supplied_files in (files, files_read):
        if supplied_files is not None and "given:idea" in supplied_files:
            raise ValueError("'given:idea' is reserved for the handed idea")
    plan = build_scaffold_plan(idea, preset_id=preset_id, project_name=project_name)
    result = impact_review.review_plan_impact(plan, changed, values=values, files=files)
    if files_read is not None:
        result["evidence"] = canon_evidence.read(
            files_read, limits=["the idea was given, not read from a file here"]
        )
    else:
        result["evidence"] = canon_evidence.derived({"given:idea": idea, **(files or {})})
    return result


__all__ = ["READ_VALUES", "for_archive", "for_idea", "stored_values"]
