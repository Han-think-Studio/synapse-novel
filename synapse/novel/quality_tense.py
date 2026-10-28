"""Deterministic Korean narrative-tense comparison with a frozen threshold."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Flag at >=60% past source and >=60% present insertion, >=3 endings each,
# and past-ratio gap >=0.25. Quoted speech is excluded.
MIN_ENDINGS = 3
DOMINANT_RATIO = 0.60
MIN_RATIO_GAP = 0.25
_QUOTED = re.compile(r"[“‘\"「『]([^”’\"」』]*)[”’\"」』]")
_SENTENCES = re.compile(r"[^.!?。！？\n]+(?:[.!?。！？]+|$)")
_ENDING = re.compile(r"[가-힣]+(?:(?:았|었|였|했)다|ㄴ다|는다|한다)")
_PAST = re.compile(r"(?:았|었|였|했)다$")
_CLOSERS = "\"'”’」』"
_TERMINATORS = ".!?。！？"


def _narration(text: str) -> str:
    return _QUOTED.sub(" ", text)


def tense_profile(paragraphs: Iterable[str]) -> dict[str, Any]:
    counts = {"past": 0, "present": 0}
    kinds = []
    for paragraph in paragraphs:
        for match in _SENTENCES.finditer(_narration(paragraph)):
            sentence = match.group(0).strip().rstrip(_CLOSERS + _TERMINATORS).rstrip(_CLOSERS)
            ending = _ENDING.fullmatch(sentence.split()[-1]) if sentence.split() else None
            if ending is None:
                continue
            kind = "past" if _PAST.search(ending.group(0)) else "present"
            counts[kind] += 1
            kinds.append(kind)
    total = sum(counts.values())
    return {
        "past": counts["past"],
        "present": counts["present"],
        "total": total,
        "past_ratio": counts["past"] / total if total else 0.0,
        "present_ratio": counts["present"] / total if total else 0.0,
        "dominant": max(counts, key=counts.get) if total else None,
        "ending_kinds": kinds,
    }


def review_tense(
    source_paragraphs: Iterable[str], inserted_paragraphs: Iterable[str]
) -> dict[str, Any]:
    source = tense_profile(source_paragraphs)
    inserted = tense_profile(inserted_paragraphs)
    gap = source["past_ratio"] - inserted["past_ratio"]
    flagged = (
        source["total"] >= MIN_ENDINGS
        and inserted["total"] >= MIN_ENDINGS
        and source["dominant"] == "past"
        and inserted["dominant"] == "present"
        and gap >= MIN_RATIO_GAP
        and source["past_ratio"] >= DOMINANT_RATIO
        and inserted["present_ratio"] >= DOMINANT_RATIO
    )
    return {
        "source": source,
        "inserted": inserted,
        "ratio_gap": gap,
        "thresholds": {
            "min_endings_each": MIN_ENDINGS,
            "dominant_ratio": DOMINANT_RATIO,
            "min_ratio_gap": MIN_RATIO_GAP,
        },
        "flagged": flagged,
    }
