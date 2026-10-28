"""Deterministic Korean OOV ranking from project text without new dependencies."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

_HANGUL_WORD = re.compile(r"[가-힣]{2,}")
_EOJEOL = re.compile(r"\S+")


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


def build_lexicon(corpus_sources: Iterable[str], *, min_frequency: int = 2) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for text in corpus_sources:
        counts.update(_HANGUL_WORD.findall(text))
    bigrams: Counter[str] = Counter()
    for word, frequency in counts.items():
        for i in range(len(word) - 1):
            bigrams[word[i : i + 2]] += frequency
    return {
        "counts": dict(counts),
        "frequent_words": sorted(word for word, count in counts.items() if count >= min_frequency),
        "bigrams": dict(bigrams),
        "min_frequency": min_frequency,
    }


def rank_oov_words(text: str, lexicon: dict[str, Any], *, limit: int = 100) -> list[dict[str, Any]]:
    counts = Counter(_HANGUL_WORD.findall(text))
    known = set(lexicon.get("frequent_words", []))
    bigrams = lexicon.get("bigrams", {})
    vocabulary = set(lexicon.get("counts", {}))
    ranked = []
    for word, count in counts.items():
        if word in known:
            continue
        pairs = [word[i : i + 2] for i in range(len(word) - 1)]
        rarity = sum(1 for pair in pairs if bigrams.get(pair, 0) == 0) / max(1, len(pairs))
        near = sorted(
            (
                (candidate, _distance(word, candidate))
                for candidate in vocabulary
                if abs(len(word) - len(candidate)) <= 2
                and (candidate[0] == word[0] or len(set(candidate) & set(word)) >= len(word) - 2)
            ),
            key=lambda item: (item[1], item[0]),
        )
        suggestions = [candidate for candidate, distance in near if 0 < distance <= 2][:3]
        ranked.append(
            {
                "word": word,
                "count": count,
                "unseen_bigram_ratio": rarity,
                "near_corpus_words": suggestions,
                "suspicion_score": round(0.7 * rarity + (0.3 if suggestions else 0.0), 3),
            }
        )
    return sorted(
        ranked, key=lambda item: (-item["suspicion_score"], -item["count"], item["word"])
    )[:limit]


def measure_suspicion_rate(text: str, lexicon: dict[str, Any]) -> dict[str, Any]:
    words = _HANGUL_WORD.findall(text)
    suspects = rank_oov_words(text, lexicon, limit=max(1, len(set(words))))
    eojeol_count = len(_EOJEOL.findall(text))
    suspect_count = sum(item["count"] for item in suspects)
    return {
        "eojeol_count": eojeol_count,
        "suspect_count": suspect_count,
        "suspect_unique": len(suspects),
        "per_1000_eojeol": round(1000 * suspect_count / eojeol_count, 3) if eojeol_count else 0.0,
        "suspects": suspects,
    }
