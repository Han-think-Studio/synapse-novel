"""Compose a canon text check and its caller-owned receipt in one shared entry."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from synapse.core import canon_context, canon_receipt
from synapse.core.canon_values import ValueStore, checkable


def check(
    text: str,
    store: ValueStore,
    *,
    run_id: str,
    scope_id: str,
    examined_files: Collection[str],
    produced_with: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Check text against confirmed canon and return the shared caller-owned record.

    This entry deliberately contains no evidence or explanatory note; each adapter supplies those
    fields according to whether it read files or was handed text. Receipt errors pass through unchanged.
    """

    made = canon_receipt.check_output(
        text,
        store,
        run_id=run_id,
        scope_id=scope_id,
        examined_files=examined_files,
        produced_with=produced_with,
    )
    retry = ""
    if made.conflicts:
        review = canon_context.conflict_review(made.conflicts)
        retry = canon_context.to_retry_instruction(review, checkable(store), text)

    return {
        "passed": made.passed,
        "complete": made.complete,
        "facts_match": made.facts_match,
        "produced_with": made.record["produced_with"],
        "retry": retry,
        "not_examined": made.record["scope"]["unexamined"],
        "unchecked": made.record["scope"]["unchecked"],
        "conflicts": made.record["conflicts"],
        "possible": made.record["possible"],
        "receipt": made.record,
        "left_out": [
            {"key": entry.key, "status": entry.status}
            for entry in store.entries
            if entry.status != "CONFIRMED"
        ],
    }
