"""Read-only review of the two existing ways to enter a project.

This is a display projection over a deterministic new-project ScaffoldPlan or
an existing-project reconstruction proposal. It is not a Registry Proposal,
approval, source freshness check, or permission to run a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synapse.core.canon_inquiry import CanonInquiry
from synapse.core.contracts import LifecycleStatus
from synapse.core.idea_session import canonical_hash, canonical_json
from synapse.core.project_reconstruction import ObservationStatus, ProjectReconstructionProposal
from synapse.core.scaffold import ScaffoldPlan, build_scaffold_plan

PROJECT_ENTRY_REVIEW_SCHEMA = "project-entry-review.v1"


class ProjectEntryReviewError(ValueError):
    """Raised when a source cannot be projected without misstating its identity."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectEntryReview:
    """Common, non-authoritative fields for a human project-entry review."""

    source_kind: str
    source_id: str
    source_fingerprint: str
    review_revision: int
    source_basis: str
    project_id: str
    project_name: str | None
    project_name_status: str
    project_name_provenance: str
    proposed_entity_status: LifecycleStatus
    blocking_question_ids: tuple[str, ...]
    ready_for_review: bool
    observation_report_bound: bool
    # Whether the project already has a canon: a decision about which file owns
    # each fact. A new idea is handed one to review, derived from its layout. An
    # existing project usually has none, and saying so plainly is the point --
    # it is the state in which the most-repeated wording passes for the truth.
    canon_status: str = "NOT_ESTABLISHED"
    canon_hash: str | None = None
    canon_pending: int | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_ENTRY_REVIEW_SCHEMA,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_fingerprint": self.source_fingerprint,
            "review_revision": self.review_revision,
            "source_basis": self.source_basis,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_name_status": self.project_name_status,
            "project_name_provenance": self.project_name_provenance,
            "proposed_entity_status": self.proposed_entity_status.value,
            "blocking_question_ids": list(self.blocking_question_ids),
            "ready_for_review": self.ready_for_review,
            "observation_report_bound": self.observation_report_bound,
            "canon_status": self.canon_status,
            "canon_hash": self.canon_hash,
            "canon_pending": self.canon_pending,
            "canonical_mutation": False,
            "dispatch_performed": False,
        }


def _existing_canon_status(source: ProjectReconstructionProposal) -> str:
    """What an observed project can honestly say about having a canon.

    Reconstruction sees which files look like authorities; it does not see which
    facts each one decides. So the most it can report is candidates, and when it
    found none the answer is that no canon has been established.
    """

    authority = source.finding("authority_candidates")
    if authority.status is ObservationStatus.UNKNOWN or not authority.value:
        return "NOT_ESTABLISHED"
    return "CANDIDATES_OBSERVED"


def _existing_canon_state(
    source: ProjectReconstructionProposal, canon: CanonInquiry | None
) -> tuple[str, str | None, int | None]:
    """(status, canon hash, copies not yet matched) of an existing project.

    With no inquiry this is only what was observed. Once the person has started
    answering which file decides what, the canon is being established: some answered,
    some still open. When every question is answered it is the same PROPOSED canon a
    new idea is handed -- answers are a proposal, never Canonical State.
    """

    observed = _existing_canon_status(source)
    if canon is None:
        return observed, None, None
    if canon.archive_sha256 != source.archive_sha256:
        raise ProjectEntryReviewError("정본 질의가 이 프로젝트의 ZIP과 다릅니다. 다시 질의해 주세요.")
    if len(canon.open_question_ids) == len(canon.questions):
        return observed, None, None
    status = "ESTABLISHING" if canon.open_question_ids else canon.canon.status.value
    return status, canon.canon.canon_hash, canon.canon.pending_count


def build_project_entry_review(
    source: ScaffoldPlan | ProjectReconstructionProposal,
    *,
    canon: CanonInquiry | None = None,
) -> ProjectEntryReview:
    """Project one existing source into shared review fields without I/O.

    ``canon`` is the inquiry that is establishing an existing project's canon, if
    one has been started; a new idea already carries its canon in the plan.
    """

    if canon is not None and isinstance(source, ScaffoldPlan):
        raise ProjectEntryReviewError("새 아이디어의 정본은 계획이 이미 갖고 있어 정본 질의를 받지 않습니다.")
    if isinstance(source, ScaffoldPlan):
        # A hand-constructed or modified plan must not carry a stale plan ID
        # into a view that claims to bind the current idea and preset.
        expected = build_scaffold_plan(
            source.idea,
            preset_id=source.preset.id,
            project_name=source.project_name,
        )
        if source != expected:
            raise ProjectEntryReviewError("ScaffoldPlan이 현재 결정적 계획과 다릅니다. 다시 계획해 주세요.")
        return ProjectEntryReview(
            source_kind="new_idea",
            source_id=f"input:idea:{source.id}",
            source_fingerprint=source.id,
            review_revision=0,
            source_basis="INTENT",
            project_id=f"project:{source.id}",
            project_name=source.project_name,
            project_name_status="INTENDED",
            project_name_provenance="scaffold_plan",
            proposed_entity_status=LifecycleStatus.ASSUMED,
            blocking_question_ids=(),
            ready_for_review=True,
            observation_report_bound=False,
            canon_status=source.canon.status.value,
            canon_hash=source.canon.canon_hash,
            canon_pending=source.canon.pending_count,
        )

    if isinstance(source, ProjectReconstructionProposal):
        canon_status, canon_hash, canon_pending = _existing_canon_state(source, canon)
        name = source.finding("project_name")
        provenance = name.provenance[-1].method if name.provenance else "unknown"
        return ProjectEntryReview(
            source_kind="existing_zip",
            source_id=source.source_id,
            source_fingerprint=source.archive_sha256,
            review_revision=source.revision,
            source_basis="OBSERVATION",
            project_id=source.project_id,
            project_name=name.value if isinstance(name.value, str) else None,
            project_name_status=name.status.value,
            project_name_provenance=provenance,
            proposed_entity_status=LifecycleStatus.CONFIRMED,
            blocking_question_ids=source.blocking_question_ids,
            ready_for_review=source.ready_for_review,
            observation_report_bound=source.source_report is not None,
            canon_status=canon_status,
            canon_hash=canon_hash,
            canon_pending=canon_pending,
        )

    raise ProjectEntryReviewError("ScaffoldPlan 또는 ProjectReconstructionProposal이 필요합니다.")


def build_project_entry_generation_input(
    source: ScaffoldPlan | ProjectReconstructionProposal,
) -> tuple[dict[str, str], bytes]:
    """Make a reviewable entry projection available as a generation input.

    This binds the current proposal revision, not its approval or the current
    bytes of an external ZIP. Existing-project callers must still re-inspect
    that ZIP at their own freshness boundary before approved execution.
    """
    review = build_project_entry_review(source)
    if not review.ready_for_review:
        raise ProjectEntryReviewError("프로젝트 입구의 필수 질문이 해결되지 않았습니다.")
    if review.source_kind == "existing_zip" and not review.observation_report_bound:
        raise ProjectEntryReviewError("기존 프로젝트의 검사 보고서가 연결되지 않았습니다.")
    record = review.to_record()
    content = canonical_json(record).encode("utf-8")
    identity = {
        "source_kind": review.source_kind,
        "source_id": review.source_id,
        "source_fingerprint": review.source_fingerprint,
        "review_revision": review.review_revision,
    }
    artifact = {
        "role": "project_entry_provenance",
        "artifact_id": "entry:" + canonical_hash(identity),
        "content_hash": canonical_hash(record),
    }
    return artifact, content


__all__ = [
    "PROJECT_ENTRY_REVIEW_SCHEMA",
    "ProjectEntryReview",
    "ProjectEntryReviewError",
    "build_project_entry_generation_input",
    "build_project_entry_review",
]
