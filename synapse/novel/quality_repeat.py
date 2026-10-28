"""Repeat scans reusable at chunk, chapter, and manuscript levels."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from itertools import pairwise
from typing import Any

_TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")
_SENTENCE = re.compile(r"[^.!?。！？\n]+(?:[.!?。！？]+|$)")
_ACTIONS = ("고개를 끄덕", "한숨을 내쉬", "한숨을 쉬", "머뭇거리", "망설이", "시선을 돌리")
_PAST = re.compile(r"(?:았|었|였|했)다$")
_PRESENT = re.compile(r"(?:ㄴ다|는다|한다)$")


def _sentences(text: str) -> list[str]:
    return [m.group(0).strip() for m in _SENTENCE.finditer(text) if m.group(0).strip()]


def _ending_pattern(sentence: str) -> str:
    value = sentence.strip().rstrip("\"'”’」』.!?。！？")
    if _PAST.search(value):
        return "past_da"
    if _PRESENT.search(value):
        return "present_da"
    if value.endswith(("요", "죠", "네요", "습니다", "습니까")):
        return "polite"
    return "other"


def _source_overlap(
    content: str, source_texts: Iterable[str], width: int = 40
) -> list[dict[str, Any]]:
    findings = []
    for source_index, source in enumerate(source_texts):
        if len(source) < width:
            continue
        windows = {source[i : i + width] for i in range(len(source) - width + 1)}
        # Every offset of the draft, not every 40th: a copy that starts at offset 1 is still a copy.
        for start in range(len(content) - width + 1):
            chunk = content[start : start + width]
            if chunk in windows:
                findings.append(
                    {
                        "source_index": source_index,
                        "content_offset": start,
                        "evidence": chunk[:120],
                    }
                )
                break
    return findings


def paragraph_repetitions(text: str) -> dict[str, Any]:
    paragraphs = [
        " ".join(unicodedata.normalize("NFKC", item).split())
        for item in re.split(r"\r?\n\s*\r?\n", text)
        if item.strip()
    ]
    counts = Counter(paragraphs)
    repeats = [{"count": n, "evidence": p[:120]} for p, n in counts.items() if n > 1]
    return {
        "paragraph_count": len(paragraphs),
        "unique_paragraph_count": len(counts),
        "repeated_paragraph_count": sum(n - 1 for n in counts.values()),
        "max_occurrences": max(counts.values(), default=0),
        "loop": any(n >= 3 for n in counts.values()),
        "groups": repeats,
    }


def longest_repeated_phrase(text: str) -> dict[str, Any]:
    """Longest repeated contiguous token sequence, measured by suffix array/LCP."""
    tokens = [t.casefold() for t in _TOKEN.findall(text)]
    n = len(tokens)
    if n < 2:
        return {"token_count": 0, "chars": 0, "evidence": ""}
    codes = {token: i for i, token in enumerate(sorted(set(tokens)))}
    ranks = [codes[token] for token in tokens]
    order = list(range(n))
    width = 1
    while width < n:
        order.sort(key=lambda i: (ranks[i], ranks[i + width] if i + width < n else -1))
        next_ranks = [0] * n
        for left, right in pairwise(order):
            next_ranks[right] = next_ranks[left] + (
                (ranks[left], ranks[left + width] if left + width < n else -1)
                != (ranks[right], ranks[right + width] if right + width < n else -1)
            )
        ranks = next_ranks
        if ranks[order[-1]] == n - 1:
            break
        width *= 2
    inverse = [0] * n
    for rank, index in enumerate(order):
        inverse[index] = rank
    matched = 0
    best = 0
    start = 0
    for index in range(n):
        rank = inverse[index]
        if rank == 0:
            matched = 0
            continue
        previous = order[rank - 1]
        while (
            index + matched < n
            and previous + matched < n
            and tokens[index + matched] == tokens[previous + matched]
        ):
            matched += 1
        if matched > best:
            best = matched
            start = index
        matched = max(0, matched - 1)
    phrase = " ".join(tokens[start : start + best])
    return {"token_count": best, "chars": len(phrase), "evidence": phrase[:120]}


def review_repetition(
    text: str,
    *,
    source_texts: Iterable[str] = (),
    level: str = "chunk",
    trigram_limit: int = 3,
    action_limit: int = 3,
) -> dict[str, Any]:
    tokens = [token.casefold() for token in _TOKEN.findall(text)]
    grams = Counter(tuple(tokens[i : i + 3]) for i in range(max(0, len(tokens) - 2)))
    repeated_grams = [
        {"gram": " ".join(gram), "count": count}
        for gram, count in grams.items()
        if count >= trigram_limit
    ]
    sentences = _sentences(text)
    runs = []
    current = []
    prior = None
    for sentence in sentences:
        pattern = _ending_pattern(sentence)
        if pattern == prior:
            current.append(sentence)
        else:
            if len(current) >= 3:
                runs.append({"pattern": prior, "count": len(current), "evidence": current[0][:120]})
            current, prior = [sentence], pattern
    if len(current) >= 3:
        runs.append({"pattern": prior, "count": len(current), "evidence": current[0][:120]})
    actions = [
        {"phrase": phrase, "count": text.count(phrase)}
        for phrase in _ACTIONS
        if text.count(phrase) >= action_limit
    ]
    overlaps = _source_overlap(text, source_texts)
    paragraphs = paragraph_repetitions(text)
    return {
        "level": level,
        "paragraphs": paragraphs,
        "longest_repeated_phrase": longest_repeated_phrase(text),
        "token_count": len(tokens),
        "repeated_3grams": repeated_grams,
        "ending_runs": runs,
        "action_phrases": actions,
        "source_overlap_40": overlaps,
        "issue_count": len(repeated_grams)
        + len(runs)
        + len(actions)
        + len(overlaps)
        + len(paragraphs["groups"]),
    }


def _normalized_sentences(text: str) -> list[str]:
    return [" ".join(unicodedata.normalize("NFKC", item).split()) for item in _sentences(text)]


def last_sentences(text: str, *, count: int = 2, max_chars: int = 160) -> str:
    """Return at most ``count`` closing sentences, trimmed to ``max_chars`` from the end."""

    tail = _normalized_sentences(text)[-count:]
    joined = " ".join(tail)
    while len(joined) > max_chars and len(tail) > 1:
        tail = tail[1:]
        joined = " ".join(tail)
    return joined[-max_chars:] if len(joined) > max_chars else joined


def cross_part_repeated_sentences(
    content: str, prior_texts: Iterable[str], *, min_chars: int = 20
) -> list[str]:
    """Sentences of ``content`` that an earlier part of the same scene already contains.

    A model shown the previous part often restates it before continuing; short stock phrases
    below ``min_chars`` are not counted.
    """

    seen: set[str] = set()
    for prior in prior_texts:
        seen.update(item for item in _normalized_sentences(prior) if len(item) >= min_chars)
    repeated: list[str] = []
    for sentence in _normalized_sentences(content):
        if len(sentence) >= min_chars and sentence in seen and sentence not in repeated:
            repeated.append(sentence)
    return repeated
