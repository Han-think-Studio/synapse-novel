"""Say what kind of statement each claim is, without letting a model decide it alone.

The second step of the canon value gate. A claim (``canon_claims``) is a statement cut out of a person's file.
What *kind* of statement it is -- a prohibition, a requirement, a fact about a character, a stage, a style hint --
decides how it is checked later, so it must not be a guess that nobody looked at. Two independent readers label
every claim, and the person is asked only where they do not agree:

* **the section rule** (plain Python): the headings above a claim usually say it. Everything under "피할 것" is
  a prohibition; every table row is a state. A rule never invents: it either matches a heading or says nothing.
* **a model** (supplied by the caller as a function; this module never calls one): it reads the claim and picks
  one label from the closed list below. It cannot add a label, and a label outside the list is discarded.

Agreement settles nothing as canon -- only the person's confirmation does -- but it lets the system propose with
confidence. Disagreement, or nobody being able to say, becomes a question for the person. Nothing is silently
resolved in favour of either reader.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from synapse.core.canon_claims import CHECKBOX, TABLE_ROW, Claim

LABELS_SCHEMA = "canon-labels.v1"

PROHIBITION = "prohibition"
REQUIREMENT = "requirement"
CHARACTER_FACT = "character_fact"
STATE_VALUE = "state_value"
STYLE_GUIDANCE = "style_guidance"
WORK_INFO = "work_info"
SEQUENCE_STEP = "sequence_step"
SPACE = "space"
OTHER = "other"
REVIEW_ITEM = "review_item"
REFERENCE_ENTRY = "reference_entry"

LABELS: tuple[str, ...] = (
    PROHIBITION,
    REQUIREMENT,
    CHARACTER_FACT,
    STATE_VALUE,
    STYLE_GUIDANCE,
    WORK_INFO,
    SEQUENCE_STEP,
    SPACE,
    OTHER,
    REVIEW_ITEM,
    REFERENCE_ENTRY,
)

AGREED = "AGREED"  # both readers gave the same label
RULE_ONLY = "RULE_ONLY"  # the section says it; the model gave nothing usable
MODEL_ONLY = "MODEL_ONLY"  # no section rule applies; only the model spoke -> still asked
DISAGREE = "DISAGREE"  # both spoke and differ -> asked
UNLABELED = "UNLABELED"  # nobody could say -> asked

# Ordered: the first rule whose pattern matches the claim's *innermost* heading wins. Only that heading counts: a
# claim under an unnamed sub-section is not given the label of the file's title, it is asked about. Patterns are
# deliberately plain words a person writes as headings.
SECTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"목차|차례|table of contents|contents|(?:문서|파일) 구성", re.IGNORECASE),
        REFERENCE_ENTRY,
    ),
    (re.compile(r"^\d+장\b|^에필로그\b", re.IGNORECASE), SEQUENCE_STEP),
    (
        re.compile(
            r"피할 것|금지|하지 않을|하지 말|avoid|forbidden|prohibit|must not|never", re.IGNORECASE
        ),
        PROHIBITION,
    ),
    (re.compile(r"규칙|원칙|지킬|rules?\b|invariants?", re.IGNORECASE), REQUIREMENT),
    (re.compile(r"기본 정보|개요|작품 정보|metadata|overview", re.IGNORECASE), WORK_INFO),
    (
        re.compile(r"—\s*(남성|여성|남|여)\b|인물|캐릭터|핵심 대비|character", re.IGNORECASE),
        CHARACTER_FACT,
    ),
    (re.compile(r"관계의 운동|단계|순서|흐름|stages?|steps?", re.IGNORECASE), SEQUENCE_STEP),
    (re.compile(r"공간|장소|배경|places?|locations?|spaces?", re.IGNORECASE), SPACE),
    (
        re.compile(
            r"곡선|밀도|문체|감각|톤|tone|style|sensory|초반|중반|후반|반복 음향|반복 소리",
            re.IGNORECASE,
        ),
        STYLE_GUIDANCE,
    ),
    (
        re.compile(r"체크리스트|점검|확인 항목|review checklist|checklist", re.IGNORECASE),
        REVIEW_ITEM,
    ),
)

_TIME_AXIS = re.compile(r"^(?:주차|주|장|week|chapter)$", re.IGNORECASE)
_BATCH_RULE_LABELS = frozenset(
    {PROHIBITION, REQUIREMENT, REVIEW_ITEM, STATE_VALUE, REFERENCE_ENTRY}
)

Labeler = Callable[[Sequence[Claim]], Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class LabelProposal:
    claim_id: str
    rule: str | None
    model: str | None
    label: str | None
    status: str
    rule_confident: bool = False

    @property
    def needs_person(self) -> bool:
        return self.status in {MODEL_ONLY, DISAGREE, UNLABELED} and not self.rule_confident

    def to_record(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "rule": self.rule,
            "model": self.model,
            "label": self.label,
            "status": self.status,
            "needs_person": self.needs_person,
            "rule_confident": self.rule_confident,
        }


def is_time_axis_header(header: str) -> bool:
    """Return whether a header matches the existing time-axis rule."""

    return _TIME_AXIS.fullmatch(header.strip()) is not None


def section_rule(claim: Claim) -> str | None:
    """The label the headings above the claim give it, or ``None`` when they say nothing."""

    if claim.kind == CHECKBOX:
        return REVIEW_ITEM
    if claim.kind == TABLE_ROW:
        first_header = claim.fields[0][0].strip() if claim.fields else ""
        return STATE_VALUE if is_time_axis_header(first_header) else REFERENCE_ENTRY
    filename = claim.file.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if filename.startswith("readme"):
        return REFERENCE_ENTRY
    headings = [part for part in claim.section.split(" / ") if part]
    if not headings:
        return None
    for pattern, label in SECTION_RULES:
        if pattern.search(headings[-1]):
            return label
    return None


def character_subject(claim: Claim) -> tuple[str, str] | None:
    """The name and heading of the person a claim is about, when the section rule says it is a character fact.

    The name is whatever the innermost heading has before its first ``—``; a heading without one names nobody.
    """

    if section_rule(claim) != CHARACTER_FACT:
        return None
    heading = claim.section.rsplit(" / ", 1)[-1].strip()
    name, separator, _description = heading.partition("—")
    if separator and name.strip():
        return name.strip(), heading
    return None


def propose_labels(
    claims: Sequence[Claim], labeler: Labeler | None = None
) -> tuple[LabelProposal, ...]:
    """Label every claim with the section rule and, when given, a model's closed-list answer."""

    spoken: Mapping[str, str] = labeler(claims) if labeler else {}
    proposals = []
    for claim in claims:
        rule = section_rule(claim)
        raw = spoken.get(claim.id)
        model = (
            raw if raw in LABELS else None
        )  # a label outside the closed list is discarded, never kept
        confident = rule in _BATCH_RULE_LABELS
        if rule and model:
            status, label = (
                (AGREED, rule) if rule == model else (DISAGREE, rule if confident else None)
            )
        elif rule:
            status, label = RULE_ONLY, rule
        elif model:
            status, label = MODEL_ONLY, None
        else:
            status, label = UNLABELED, None
        proposals.append(LabelProposal(claim.id, rule, model, label, status, confident))
    return tuple(proposals)


def questions_for(
    claims: Sequence[Claim], proposals: Sequence[LabelProposal]
) -> tuple[dict[str, object], ...]:
    """One question per claim the two readers could not settle -- asked in the person's own file's words."""

    by_id = {claim.id: claim for claim in claims}
    questions = []
    batch_items: list[dict[str, object]] = []
    for proposal in proposals:
        claim = by_id[proposal.claim_id]
        question = {
            "claim_id": claim.id,
            "file": claim.file,
            "line": claim.line,
            "section": claim.section,
            "text": claim.text,
            "status": proposal.status,
            "section_says": proposal.rule,
            "model_says": proposal.model,
            "choices": list(LABELS),
        }
        if (
            proposal.status == DISAGREE
            and proposal.rule_confident
            and proposal.rule != REFERENCE_ENTRY
        ):
            batch_items.append(question)
        elif proposal.needs_person:
            questions.append(question)
    if batch_items:
        questions.append(
            {
                "kind": "batch_rule_confirmation",
                "status": DISAGREE,
                "claim_ids": [item["claim_id"] for item in batch_items],
                "claims": batch_items,
                "choices": list(LABELS),
            }
        )
    return tuple(questions)


def summary(proposals: Sequence[LabelProposal]) -> dict[str, int]:
    counts = dict.fromkeys((AGREED, RULE_ONLY, MODEL_ONLY, DISAGREE, UNLABELED), 0)
    for proposal in proposals:
        counts[proposal.status] += 1
    counts["total"] = len(proposals)
    counts["asked"] = sum(1 for proposal in proposals if proposal.needs_person)
    return counts


__all__ = [
    "AGREED",
    "DISAGREE",
    "LABELS",
    "LABELS_SCHEMA",
    "MODEL_ONLY",
    "REFERENCE_ENTRY",
    "REVIEW_ITEM",
    "RULE_ONLY",
    "UNLABELED",
    "LabelProposal",
    "character_subject",
    "is_time_axis_header",
    "propose_labels",
    "questions_for",
    "section_rule",
    "summary",
]
