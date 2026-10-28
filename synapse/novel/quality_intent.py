"""Report-only deterministic intent evidence and exact-quote validation."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

SUGGESTED_CONTRAST_WORDS = ("질투", "서운", "신경", "확인", "스토리", "좋아요", "답장", "연락")
_QUOTED = re.compile(r"[“‘\"「『]([^”’\"」』]+)[”’\"」』]")
_SENTENCE = re.compile(r"[^.!?。！？\n]+(?:[.!?。！？]+|$)")
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")


def _similarity(left: str, right: str) -> float:
    a, b = set(_TOKEN.findall(left.casefold())), set(_TOKEN.findall(right.casefold()))
    return len(a & b) / len(a | b) if a or b else 0.0


def review_intent(
    content: str,
    *,
    directive: str,
    subjects: Sequence[str],
    start_state: str = "",
    end_state: str = "",
    min_chars: int = 0,
    max_chars: int = 10**9,
    contrast_card: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    quoted = [match.group(1) for match in _QUOTED.finditer(content)]
    narration = _QUOTED.sub(" ", content).strip()
    # Every named person is looked for; only the contrast below compares exactly two.
    names = list(subjects[:2])
    name_presence = {name: bool(name and name in content) for name in dict.fromkeys(subjects)}
    state_scores = [
        _similarity(state, sentence)
        for state in (start_state, end_state)
        if state
        for sentence in _SENTENCE.findall(content)
    ]
    overlap = max(state_scores, default=0.0)
    evidence: dict[str, Any] = {
        "size": {
            "chars": len(content),
            "min": min_chars,
            "max": max_chars,
            "ok": min_chars <= len(content) <= max_chars,
        },
        "names": name_presence,
        "dialogue_and_narration": {
            "dialogue_count": len(quoted),
            "narration_chars": len(narration),
            "ok": bool(quoted) and bool(narration),
        },
        "state_restatement": {"max_sentence_similarity": round(overlap, 3), "ok": overlap < 0.60},
    }
    if directive.upper() == "CONTRAST":
        if (
            not isinstance(contrast_card, Mapping)
            or contrast_card.get("operator_confirmed") is not True
        ):
            evidence["contrast_asymmetry"] = {
                "status": "needs_user_card",
                "suggested_words": list(SUGGESTED_CONTRAST_WORDS),
            }
        else:
            words = [str(word) for word in contrast_card.get("clue_words", []) if str(word)]
            counts = Counter()
            for sentence in _SENTENCE.findall(content):
                clues = sum(1 for word in words if word in sentence)
                for name in names:
                    if name in sentence:
                        counts[name] += clues
            asymmetric = (
                len(names) == 2
                and counts[names[0]] > 0
                and counts[names[1]] > 0
                and counts[names[0]] != counts[names[1]]
            )
            evidence["contrast_asymmetry"] = {
                "status": "reviewed",
                "counts": dict(counts),
                "ok": asymmetric,
            }
    return {
        "directive": directive,
        "deterministic_evidence": evidence,
        "human_verdict": "",
        "model_review": None,
        "report_only": True,
        "can_promote_canon_status": False,
    }


def validate_intent_model_response(content: str, response: Any) -> dict[str, Any]:
    if isinstance(response, str):
        try:
            import json

            response = json.loads(response)
        except (TypeError, ValueError):
            return {"valid": False, "reason": "invalid_json", "satisfied": None, "quote": ""}
    if not isinstance(response, Mapping) or type(response.get("satisfied")) is not bool:
        return {"valid": False, "reason": "invalid_shape", "satisfied": None, "quote": ""}
    quote = response.get("quote")
    if not isinstance(quote, str) or len(quote) > 120 or (quote and quote not in content):
        return {
            "valid": False,
            "reason": "quote_not_exact_or_too_long",
            "satisfied": None,
            "quote": "",
        }
    if response["satisfied"] and not quote:
        return {"valid": False, "reason": "missing_quote", "satisfied": None, "quote": ""}
    return {
        "valid": True,
        "reason": "exact_quote",
        "satisfied": response["satisfied"],
        "quote": quote,
    }
