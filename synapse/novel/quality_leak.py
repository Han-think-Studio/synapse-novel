"""Detect model-internal channel text leaked into a JSON string field.

A grammar-constrained JSON request cannot stop a model from writing chat-template control
tokens or its English reasoning *inside* a string value: the grammar accepts any string
character. The 2026-10-13 sampling lab saw one gpt-oss response whose content ended with
``}<|end|><|start|>assistant<|channel|>analysis<|message|>The user is writing a novel...``
while the JSON stayed valid, ``finish_reason`` was ``stop`` and the length was in range.

A draft that is entirely in another language is a different case: the canon review already
turns it into ``unchecked``. Only text that mixes Korean with control tokens, think tags or an
English run is treated as a leak.
"""

from __future__ import annotations

import re

_CONTROL_TOKEN = re.compile(r"<\|[A-Za-z_]+\|>|<\|[A-Za-z_]*\Z|\A[A-Za-z_]*\|>")
_THINK_TAG = re.compile(r"</?think>", re.IGNORECASE)
# Six or more consecutive English words never belong inside Korean prose.
_ENGLISH_RUN = re.compile(r"[A-Za-z]{2,}(?:[ ,.'’-]+[A-Za-z]{2,}){5,}")
_SOME_KOREAN = 0.05
META_LEAK_THRESHOLD = 3
_QUOTED = re.compile(r'“[^”]*”|"[^"\n]*"|‘[^’]*’', re.DOTALL)
_META = re.compile(
    r"자연스럽게\s*서술(?:되었|됐|했|하였)습니다|요청하신|글자\s*수"
    r"|(?:조건|요청|지침|분량|문체|시제|정본|설정)[^.!?\n]{0,40}확인합니다"
    r"|(?m:^\s*확인합니다)"
)
_FIRST_PERSON = re.compile(r"(?<![가-힣])(?:나는|내가)(?![가-힣])|(?<![가-힣])내\s+")


def korean_meta_suspicions(text: str) -> dict[str, object]:
    """Weak markers outside quoted dialogue; three independent matches reject a leak."""
    narrative = _QUOTED.sub("", text)
    matches = list(_META.finditer(narrative))
    return {
        "count": len(matches),
        "threshold": META_LEAK_THRESHOLD,
        "markers": [m.group(0)[:120] for m in matches],
    }


def first_person_suspicions(text: str, source_text: str) -> dict[str, object]:
    """Advisory only: consecutive first-person narrative absent in source narrative."""
    source = _QUOTED.sub("", source_text)
    applicable = bool(source.strip()) and not _FIRST_PERSON.search(source)
    narrative = _QUOTED.sub("", text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", narrative) if s.strip()]
    consecutive = 0
    flagged = []
    for index, sentence in enumerate(sentences):
        consecutive = consecutive + 1 if _FIRST_PERSON.search(sentence) else 0
        if applicable and consecutive >= 2:
            flagged.append(index)
    return {
        "applicable": bool(applicable),
        "count": len(flagged),
        "sentence_indexes": flagged,
        "blocking": False,
    }


def _hangul_ratio(text: str) -> float:
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return 0.0
    hangul = sum("가" <= character <= "힣" for character in letters)
    return hangul / len(letters)


def channel_leak(text: str) -> dict[str, object]:
    """Return whether ``text`` contains control tokens, think tags or English inside Korean."""

    reasons: list[str] = []
    if _CONTROL_TOKEN.search(text):
        reasons.append("control_token")
    if _THINK_TAG.search(text):
        reasons.append("think_tag")
    ratio = _hangul_ratio(text)
    english_runs = len(_ENGLISH_RUN.findall(text))
    if english_runs and ratio > _SOME_KOREAN:
        reasons.append("english_run_in_korean_text")
    meta = korean_meta_suspicions(text)
    if meta["count"] >= META_LEAK_THRESHOLD:
        reasons.append("korean_meta_text")
    return {
        "korean_meta": meta,
        "leak": bool(reasons),
        "reasons": reasons,
        "english_runs": english_runs,
        "hangul_ratio": round(ratio, 3),
    }
