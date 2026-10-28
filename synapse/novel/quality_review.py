"""Quality review orchestration; deterministic checks and model proposals stay advisory."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .quality_format import inspect_format, review_format
from .quality_intent import review_intent, validate_intent_model_response
from .quality_leak import first_person_suspicions, korean_meta_suspicions
from .quality_repeat import review_repetition
from .quality_tense import review_tense
from .quality_typo import build_lexicon, rank_oov_words

MAX_PROOFREAD_PROPOSALS = 12
_WORD = re.compile(r"^[가-힣]{2,12}$")


def _distance(left: str, right: str, cutoff: int = 2) -> int:
    if abs(len(left) - len(right)) > cutoff:
        return cutoff + 1
    row = list(range(len(right) + 1))
    for i, one in enumerate(left, 1):
        current = [i]
        for j, two in enumerate(right, 1):
            current.append(min(current[-1] + 1, row[j] + 1, row[j - 1] + (one != two)))
        if min(current) > cutoff:
            return cutoff + 1
        row = current
    return row[-1]


def validate_proofreading_response(
    content: str, response: Any, *, maximum: int = MAX_PROOFREAD_PROPOSALS
) -> list[dict[str, str]]:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except (TypeError, ValueError):
            return []
    if not isinstance(response, list):
        return []
    valid = []
    for item in response:
        if not isinstance(item, Mapping):
            continue
        wrong, right, reason = item.get("wrong"), item.get("right"), item.get("reason")
        if not all(isinstance(value, str) for value in (wrong, right, reason)):
            continue
        if (
            wrong == right
            or wrong not in content
            or not _WORD.fullmatch(wrong)
            or not _WORD.fullmatch(right)
        ):
            continue
        if _distance(wrong, right) > 2 or not reason.strip():
            continue
        valid.append({"wrong": wrong, "right": right, "reason": reason.strip()[:240]})
        if len(valid) >= maximum:
            break
    return valid


def proofreading_request(content: str) -> dict[str, Any]:
    return {
        "kind": "proofreading_proposals",
        "content": content,
        "response": "JSON array of {wrong,right,reason}; proposals only",
    }


def intent_request(content: str, directive: str) -> dict[str, Any]:
    return {
        "kind": "intent_review",
        "directive": directive,
        "content": content,
        "response": "JSON object {satisfied:boolean,quote:string}; exact quote only",
    }


def evaluate_chunk(
    content: str,
    *,
    source_paragraphs: Sequence[str] = (),
    source_texts: Sequence[str] = (),
    source_pov_text: str | None = None,
    corpus_sources: Sequence[str] = (),
    directive: str = "",
    subjects: Sequence[str] = (),
    start_state: str = "",
    end_state: str = "",
    min_chars: int = 0,
    max_chars: int = 10**9,
    contrast_card: Mapping[str, Any] | None = None,
    model_runtime: Callable[[dict[str, Any]], Any] | None = None,
    include_model_reviews: bool = False,
) -> dict[str, Any]:
    formatted = review_format(content)
    format_findings = inspect_format(content)
    normalized = formatted["content"]
    tense = review_tense(source_paragraphs, [normalized]) if source_paragraphs else None
    repetition = review_repetition(normalized, source_texts=source_texts, level="chunk")
    lexicon = build_lexicon([*corpus_sources, *source_texts])
    typo_words = set(re.findall(r"[가-힣]{2,}", normalized))
    typo = rank_oov_words(normalized, lexicon, limit=max(1, len(typo_words)))
    intent = review_intent(
        normalized,
        directive=directive,
        subjects=subjects,
        start_state=start_state,
        end_state=end_state,
        min_chars=min_chars,
        max_chars=max_chars,
        contrast_card=contrast_card,
    )
    retry_reasons = []
    if format_findings["issue_count"]:
        retry_reasons.append("format_threshold_exceeded")
    if tense and tense["flagged"]:
        retry_reasons.append("tense_profile_mismatch")
    if repetition["issue_count"]:
        retry_reasons.append("repetition_threshold_exceeded")
    result = {
        "korean_meta_suspicions": korean_meta_suspicions(normalized),
        "first_person_suspicions": first_person_suspicions(
            normalized,
            source_pov_text
            if source_pov_text is not None
            else "\n\n".join(source_texts or source_paragraphs),
        ),
        "format": {
            "issues": format_findings["issues"],
            "normalization": formatted["normalization"],
        },
        "tense": tense,
        "repetition": repetition,
        "typo_suspicions": typo,
        "intent_review": intent,
        "quality_retry": {
            "eligible": bool(retry_reasons),
            "reasons": retry_reasons,
            "automatic": False,
            "max_quality_retries": 1,
        },
        "normalized_content": normalized,
        "proofreading_proposals": [],
    }
    if include_model_reviews and model_runtime is not None:
        result["proofreading_proposals"] = validate_proofreading_response(
            normalized, model_runtime(proofreading_request(normalized))
        )
        intent["model_review"] = validate_intent_model_response(
            normalized, model_runtime(intent_request(normalized, directive))
        )
    result.pop("normalized_content")
    return result


def authorize_quality_retry(
    *, approved: bool, source: str, reason: str, attempt_number: int, prior_quality_retries: int
) -> dict[str, Any]:
    if approved is not True or source != "operator":
        raise ValueError("품질 재요청은 운영자가 명시 승인해야 합니다.")
    if not reason.strip():
        raise ValueError("재요청 사유가 필요합니다.")
    if attempt_number >= 3 or prior_quality_retries >= 1:
        raise ValueError("품질 사유 재요청은 3회 한도 안에서 한 번만 허용됩니다.")
    return {
        "source": source,
        "reason": reason.strip(),
        "attempt": attempt_number + 1,
        "quality_retry_number": prior_quality_retries + 1,
        "automatic": False,
    }


def apply_approved_proofreading(
    content: str,
    proposals: Sequence[Mapping[str, str]],
    selected: Sequence[Mapping[str, str]],
    *,
    approved: bool,
    source: str,
    expected_sha256: str,
) -> dict[str, Any]:
    actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if approved is not True or source != "operator":
        raise ValueError("교정 제안은 운영자가 명시 승인해야 합니다.")
    if actual != expected_sha256:
        raise ValueError("교정 대상 원고 해시가 바뀌었습니다.")
    known = {(item.get("wrong"), item.get("right")) for item in proposals}
    output = content
    changes = []
    for item in selected:
        wrong, right = item.get("wrong"), item.get("right")
        if (
            (wrong, right) not in known
            or not isinstance(wrong, str)
            or not isinstance(right, str)
            or wrong == right
        ):
            raise ValueError("승인 목록에 없는 교정 제안입니다.")
        if wrong not in output:
            raise ValueError("교정할 단어가 원고에 없습니다.")
        output = output.replace(wrong, right, 1)
        changes.append({"wrong": wrong, "right": right})
    return {
        "content": output,
        "source": source,
        "before_sha256": actual,
        "after_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "changes": changes,
    }
