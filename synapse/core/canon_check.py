"""Compare draft text with confirmed canon values using deterministic checks.

A user may attach ``pattern`` when confirming a value.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True, slots=True)
class CanonValue:
    """사용자가 값을 확정할 때 글에서 속성을 가리키는 말도 붙인다."""

    key: str
    kind: str
    text: str
    claim_id: str
    status: str
    pattern: str | None = None
    terms: tuple[str, ...] = ()
    file: str = ""
    line: int = 0
    needs_person: bool = False
    opposite_terms: tuple[str, ...] = ()
    source_claims: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Conflict:
    rule: str
    value_key: str
    claim_id: str
    evidence: str
    message: str


_AGE_KEY = re.compile(r"^character\.(.+)\.age$")
_ONES = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9}
_TENS = {"열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40, "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}
_ELAPSED_YEAR_DIGITS = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_ELAPSED_YEAR_SMALL_UNITS = {"십": 10, "백": 100, "천": 1_000}
_ELAPSED_YEAR_LARGE_UNITS = {"만": 10_000, "억": 100_000_000}
_AGE_NUMBER = r"(?:\d+|(?:열|스물|스무|서른|마흔|쉰|예순|일흔|여든|아흔)(?:하나|다섯|여섯|일곱|여덟|아홉|한|두|둘|세|셋|네|넷)?|하나|다섯|여섯|일곱|여덟|아홉|한|두|둘|세|셋|네|넷)"
_AGE_TENS_NUMBER = r"(?:열|스물|스무|서른|마흔|쉰|예순|일흔|여든|아흔)(?:하나|다섯|여섯|일곱|여덟|아홉|한|두|둘|세|셋|네|넷)?"
_AGE_VALUE = re.compile(rf"\s*(?:만\s*)?({_AGE_NUMBER})\s*(?:세|살)\s*")
_LABELED_AGE_VALUE = re.compile(
    rf"\s*나이(?:는|가|\s*[:：=]|\s+)\s*(?:만\s*)?({_AGE_NUMBER})\s*(?:세|살)"
    r"(?:입니다|이다|다)?\s*\.?\s*"
)
_AGE_MENTION = re.compile(rf"(?<![\d가-힣])(?:만\s*)?({_AGE_NUMBER})\s*(?:세(?!기|대)|살)")
_BARE_AGE_MENTION = re.compile(
    rf"(?<![\d가-힣])({_AGE_TENS_NUMBER})"
    # Narrative present (`서른둘이다`) only: in dialogue (`내가 여든둘이야`) the speaker is seldom a named character.
    r"(?=(?:이었|였|인|의|이(?:다|고|며|지만)|이\s*되(?:면|기\s*전에)))"
)
# What follows `N살의` / `N인` when it is the predicate of the clause (`30세의 청년이었다`): the age then describes the
# clause's subject, not a name after it.
_AGE_PREDICATE_PHRASE = re.compile(
    r"\s*(?:[가-힣]+\s+){0,2}[가-힣]+?"
    r"(?:이었|였)(?:다|고|으며|는데|지만|던)|"
    r"\s*(?:[가-힣]+\s+){0,2}[가-힣]+?(?:이다|이고|이며|이지만)"
)
_AGE_SUBJECT_TAIL = re.compile(
    r"\s*(?:씨|님|선생|박사)?\s*(?:은|는|이|가)\s*"
    r"(?:(?:올해(?:로)?|현재|지금|오늘|요즘|그\s*무렵|그때|그\s*시절|당시)\s*)?$"
)
_AGE_SUBJECT_WORD = re.compile(
    r"(?<![가-힣])(?P<name>[가-힣]{2,4})(?:\s*(?:씨|님|선생|박사))?"
    r"(?P<particle>은|는|이|가)\s*"
    r"(?:(?:올해(?:로)?|현재|지금|오늘|요즘|그\s*무렵|그때|그\s*시절|당시)\s*)?$"
)
_AGE_NONPERSON_SUBJECTS = {"나이", "연령"}
_AGE_BIRTHDAY_AFTER = re.compile(r"\s*생일")
_AGE_MILESTONE_CUE = re.compile(
    r"(?<![가-힣])(?:환갑|회갑|고희|[일이삼사오육칠팔구]순)"
    r"(?=(?:은|는|이|가|을|를|의|에|[\s,.;!?]|$))"
)
_AGE_OWNER_AMBIGUITY = re.compile(
    r"(?:중|가운데)\s*(?:한\s*(?:명|사람)|하나|1\s*명)"
)
_AGE_PLAN_AFTER = re.compile(r"\s*(?:(?:이|가)\s*)?되(?:면|기\s*전에)")
_AGE_PAST_STAGE_AFTER = re.compile(r"\s*(?:때(?:부터)?|무렵|시절)")
_AGE_PAST_EVENT_AFTER = re.compile(r"\s*에(?:\s|$)")
_AGE_PAST_VERB = re.compile(
    r"(?:았|었|였|했|됐|뒀|왔|갔|났|냈|섰|졌|썼)(?:다|던|고|으며|는데|지만|다가|어|아|지)"
)
_AGE_DURATION_NUMBER = rf"(?:{_AGE_NUMBER}|[영공일이삼사오육칠팔구십백천만억]+)"
_AGE_PAST_ANCHOR = re.compile(
    rf"{_AGE_DURATION_NUMBER}\s*년\s*전"
    r"|당시|그때|그\s*시절|처음|그\s*뒤로.{0,80}(?:흘렀|지났|지나갔)"
)
_AGE_PAST_EVENT_BEFORE = re.compile(r"[가-힣]{1,10}(?:한|했던|하던)\s*(?:건|것|일|때)\s*$")
_AGE_YEARS_AGO = re.compile(rf"({_AGE_DURATION_NUMBER})\s*년\s*전")
_AGE_CURRENT_ANCHOR = re.compile(r"(?:올해(?:로)?|현재|지금|오늘|요즘)\s*$")
_CHARACTER_ATTRIBUTE = re.compile(r"^character\.(.+)\.([^.]+)$")
_SIMILARITY = re.compile(r"같지|같은|같았|같다|똑같|비슷|동일|닮았|동갑")
_NEGATION = re.compile(
    r"\uC54A\uB2E4|\uC54A\uB294|\uC54A\uC558|\uC544\uB2C8|\uC5C6\uB2E4|\uC5C6\uB294|\uC5C6\uC744|\uBABB\uD55C|\uBABB\uD588|\uB9AC\s*\uC5C6"
)
_QUOTED_PROHIBITION = re.compile(r"\u2018([^\u2019]+)\u2019|\u201C([^\u201D]+)\u201D|\x22([^\x22]+)\x22|\x27([^\x27]+)\x27")
_CLAUSE_BOUNDARY = re.compile(
    r"[.!?\u3002\uFF01\uFF1F;\uFF1B:\n]+|(?:\uC9C0\uB9CC|\uC73C\uB098|\uBA74\uC11C|\uB294\s+\uBC18\uBA74|\uBC18\uBA74|\uD558\uC9C0\uB9CC|\uADF8\uB7EC\uB098|\uB294\uB370|\uACE0)(?=[\s,\uFF0C])"
)
_DIALOGUE = re.compile(r"\u2018[^\u2019]*\u2019|\u201C[^\u201D]*\u201D|\x22[^\x22]*\x22|\x27[^\x27]*\x27")
_ATTRIBUTE_TERMS = {"pressure": ("\ud544\uc555",)}
_SENTENCE_END = frozenset(".!?。！？")
# How far from a character's name a number is still taken to be said about that character.
_NAME_REACH = 40
# Words that speak of the pair as a pair, without naming them.
_PAIR_WORDS = ("두 사람", "두 인물", "둘 다", "둘이", "둘은", "둘의", "서로", "양쪽")
_DIFFERENCE = re.compile(r"\uB2E4\uB974|\uCC28\uC774")
_PROHIBITION_SIMILARITY_THRESHOLD = 0.88
_PARTICLES = tuple(
    sorted(
        (
            "\uc73c\ub85c\ubd80\ud130",
            "\uc5d0\uac8c\uc11c\ub294",
            "\uc5d0\uac8c\uc11c",
            "\uc5d0\uac8c\ub294",
            "\uc5d0\uc11c\ub294",
            "\uc73c\ub85c\ub294",
            "\uc73c\ub85c",
            "\uc5d0\uac8c",
            "\uc5d0\uc11c",
            "\uaed8\uc11c",
            "\uc740",
            "\ub294",
            "\uc774",
            "\uac00",
            "\uc744",
            "\ub97c",
            "\uc758",
            "\ub3c4",
            "\ub9cc",
            "\uc640",
            "\uacfc",
            "\ub791",
        ),
        key=len,
        reverse=True,
    )
)
_STRONG_PREDICATE = re.compile(
    r"\uAC15\uD558|\uAC15\uD55C|강했|\uC138\uB2E4|\uC138\uAC8C|\uC138\uACE0|\uC138\uBA70|"
    r"\uC13C(?=[\s,.;!?\u3002\uFF01\uFF1F]|$)|\uBB35\uC9C1"
)
_WEAK_PREDICATE = re.compile(r"약하|약한|약했|가볍|가벼|옅|얇|얕(?!보)")
_OPPOSITE_DISPLAY = {
    "strong": (
        "\uc57d\ud55c",
        "\uac00\ubcbc\uc6b4",
        "\uc605\uc740",
        "\uc587\uc740",
        "\uc595\uc740",
    ),
    "weak": ("\uac15\ud55c", "\uc13c", "\ubb35\uc9c1\ud55c"),
}
_FEMALE_SEX = re.compile(r"\uC5EC\uC131|\uC5EC\uC790|\b(?:female|woman|women)\b", re.IGNORECASE)
_MALE_SEX = re.compile(r"\uB0A8\uC131|\uB0A8\uC790|\b(?:male|man|men)\b", re.IGNORECASE)
_PRONOUN = re.compile(
    r"(?<![\uAC00-\uD7A3A-Za-z])(?P<pronoun>\uADF8\uB140|\uADF8)(?=(?:\uB294|\uAC00|\uB97C|\uC758|\uB3C4|\uC640|\uC5D0\uAC8C|\uB9CC|[,.;!?\u3002\uFF01\uFF1F]|$))"
)


def _clauses(text: str) -> Iterable[tuple[int, int, str]]:
    start = 0
    for boundary in _CLAUSE_BOUNDARY.finditer(text):
        if boundary.start() > start:
            yield start, boundary.start(), text[start : boundary.start()]
        start = boundary.end()
    if start < len(text):
        yield start, len(text), text[start:]


def _mask_dialogue(text: str) -> str:
    characters = list(text)
    for quoted in _DIALOGUE.finditer(text):
        for index in range(quoted.start(), quoted.end()):
            if characters[index] != "\n":
                characters[index] = " "
    return "".join(characters)


def _normalize_with_spans(text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    removed: set[int] = set()
    for token in re.finditer(r"[\w]+", text, re.UNICODE):
        word = token.group(0)
        for particle in _PARTICLES:
            if not word.endswith(particle) or len(word) - len(particle) < 2:
                continue
            prefix = word[: -len(particle)]
            if any("\uac00" <= char <= "\ud7a3" for char in prefix):
                removed.update(range(token.end() - len(particle), token.end()))
                break
    normalized: list[str] = []
    spans: list[tuple[int, int]] = []
    for index, character in enumerate(text):
        if index in removed or not character.isalnum():
            continue
        normalized.append(character.casefold())
        spans.append((index, index + 1))
    return "".join(normalized), tuple(spans)


def _cuts_into_a_word(text: str, start: int, end: int, phrase: str) -> bool:
    """A match that crosses a space the phrase lacks must begin a word: `사랑 해` holds `사랑해`, `회사 과장` not `사과`."""

    if any(c.isspace() for c in phrase) or not any(c.isspace() for c in text[start:end]):
        return False
    return start > 0 and text[start - 1].isalnum()


def _find_approximate_prohibition(text: str, phrase: str) -> tuple[str, ...]:
    target, _ = _normalize_with_spans(phrase)
    haystack, spans = _normalize_with_spans(text)
    if not target or not haystack:
        return ()
    exact: list[str] = []
    for match in re.finditer(re.escape(target), haystack):
        start = spans[match.start()][0]
        end = spans[match.end() - 1][1]
        if _cuts_into_a_word(text, start, end, phrase):
            continue
        exact.append(text[start:end])
    if exact or len(target) < 7:
        return tuple(dict.fromkeys(exact))

    minimum = max(1, int(len(target) * _PROHIBITION_SIMILARITY_THRESHOLD))
    maximum = min(len(haystack), int(len(target) / _PROHIBITION_SIMILARITY_THRESHOLD) + 1)
    evidence: list[str] = []
    last_end = -1
    for start in range(len(haystack)):
        if start < last_end:
            continue
        best: tuple[float, int] | None = None
        for size in range(minimum, maximum + 1):
            end = start + size
            if end > len(haystack):
                break
            ratio = SequenceMatcher(None, target, haystack[start:end], autojunk=False).ratio()
            if ratio >= _PROHIBITION_SIMILARITY_THRESHOLD and (best is None or ratio > best[0]):
                best = ratio, end
        if best is None:
            continue
        end = best[1]
        source_start = spans[start][0]
        source_end = spans[end - 1][1]
        if _cuts_into_a_word(text, source_start, source_end, phrase):
            continue
        evidence.append(text[source_start:source_end])
        last_end = end
    return tuple(dict.fromkeys(evidence))


def _find_linking_siot_prohibition(text: str, phrase: str) -> tuple[str, ...]:
    """Find a prohibited noun whose final open syllable takes a linking ㅅ."""

    if not phrase:
        return ()
    last = phrase[-1]
    syllable_index = ord(last) - 0xAC00
    if not (0 <= syllable_index < 11_172) or syllable_index % 28:
        return ()

    variant = phrase[:-1] + chr(ord(last) + 19)
    target, _ = _normalize_with_spans(variant)
    haystack, spans = _normalize_with_spans(text)
    if not target or not haystack:
        return ()

    evidence: list[str] = []
    for match in re.finditer(re.escape(target), haystack):
        start = spans[match.start()][0]
        end = spans[match.end() - 1][1]
        if end >= len(text) or not ("가" <= text[end] <= "힣"):
            continue
        if start and (text[start - 1].isalnum() or text[start - 1] == "_"):
            continue
        evidence.append(text[start : end + 1])
    return tuple(dict.fromkeys(evidence))


def _canonical_polarity(text: str) -> str | None:
    if _NEGATION.search(text):
        return None
    strong = bool(_STRONG_PREDICATE.search(text))
    weak = bool(_WEAK_PREDICATE.search(text))
    if strong == weak:
        return None
    return "strong" if strong else "weak"


def _opposite_vocabulary(text: str) -> tuple[str, ...]:
    """Return the closed, human-readable opposite terms for a recognized value."""
    polarity = _canonical_polarity(text)
    if polarity is None:
        return ()
    return _OPPOSITE_DISPLAY[polarity]


def _opposite_predicates(value: CanonValue, polarity: str) -> tuple[re.Pattern[str], ...]:
    default = _WEAK_PREDICATE if polarity == "strong" else _STRONG_PREDICATE
    additions = tuple(re.compile(re.escape(term)) for term in value.opposite_terms if term)
    return (default, *additions)


def _character_sexes(values: tuple[CanonValue, ...]) -> dict[str, str]:
    sexes: dict[str, str] = {}
    for value in values:
        match = _CHARACTER_ATTRIBUTE.fullmatch(value.key)
        if not match or match.group(2) != "sex":
            continue
        name = match.group(1)
        female = bool(_FEMALE_SEX.search(value.text))
        male = bool(_MALE_SEX.search(value.text))
        if female != male:
            sexes[name] = "female" if female else "male"
    return sexes


def _paragraph_start(text: str, position: int) -> int:
    return max(
        (match.end() for match in re.finditer(r"\n[ \t]*\n+", text[:position])),
        default=0,
    )


def _resolve_pronoun(
    text: str,
    pronoun: str,
    position: int,
    names: tuple[str, ...],
    sexes: dict[str, str],
) -> str | None:
    expected = "female" if pronoun == "그녀" else "male"
    paragraph_start = _paragraph_start(text, position)
    mentions = _mentions_of(text[paragraph_start:position], names)
    compatible = {name for _, _, name in mentions if sexes.get(name) == expected}
    if len(compatible) != 1:
        return None
    owner = next(iter(compatible))
    latest = max(end for _, end, name in mentions if name == owner)
    return owner if position - (paragraph_start + latest) <= _NAME_REACH else None


def _person_for_attribute(
    clause: str,
    term_match: re.Match[str],
    clause_start: int,
    full_text: str,
    names: tuple[str, ...],
    sexes: dict[str, str],
) -> tuple[str | None, bool]:
    candidates: list[tuple[int, str | None, bool]] = []
    exact = set(_mentions_of(clause, names))
    mentions = _mentions_of(clause, names, include_embedded=True)
    direct_owners: list[tuple[int, str, bool]] = []
    blocked_by_later_genitive = False
    for start, end, name in mentions:
        if end > term_match.start():
            continue
        between = clause[end:term_match.start()]
        possessive = re.match(r"\s*의(?=\s|$)", between)
        if possessive is None:
            continue
        if any(
            other_name != name
            and other_start >= end
            and other_end <= term_match.start()
            for other_start, other_end, other_name in mentions
        ):
            continue
        if re.search(r"\S+의(?=\s|$)", between[possessive.end():]):
            blocked_by_later_genitive = True
            continue
        direct_owners.append((start, name, (start, end, name) not in exact))
    if direct_owners:
        owners = {name for _, name, _ in direct_owners}
        if len(owners) != 1:
            return None, True
        return next(iter(owners)), all(ambiguous for _, _, ambiguous in direct_owners)
    if blocked_by_later_genitive:
        return None, True

    for start, end, name in mentions:
        distance = max(0, term_match.start() - end, start - term_match.end())
        if distance <= 36:
            candidates.append((distance, name, (start, end, name) not in exact))
    for match in _PRONOUN.finditer(clause):
        distance = max(0, term_match.start() - match.end(), match.start() - term_match.end())
        if distance <= 24:
            resolved = _resolve_pronoun(
                full_text, match.group("pronoun"), clause_start + match.start(), names, sexes
            )
            candidates.append((distance, resolved, resolved is None))
    if not candidates:
        return None, False
    nearest = min(distance for distance, _, _ in candidates)
    choices = [(name, ambiguous) for distance, name, ambiguous in candidates if distance == nearest]
    owners = {name for name, _ in choices}
    if len(owners) != 1:
        return None, True
    return next(iter(owners)), all(ambiguous for _, ambiguous in choices)


def _add_conflict(conflicts: list[Conflict], conflict: Conflict) -> None:
    if conflict not in conflicts:
        conflicts.append(conflict)


def _mentions_of(
    text: str, names: Iterable[str], *, include_embedded: bool = False
) -> list[tuple[int, int, str]]:
    aliases: dict[str, set[str]] = {}
    for name in set(names):
        for form in _name_forms(name):
            aliases.setdefault(form, set()).add(name)
    found = [
        (m.start(), m.end(), next(iter(owners)))
        for form, owners in aliases.items() if len(owners) == 1
        for m in re.finditer(re.escape(form), text)
    ]
    # A recognized longer full name owns its substring.
    found = [
        (a, b, n) for a, b, n in found
        if not any(c <= a and b <= d and (c, d) != (a, b) for c, d, _ in found)
    ]
    if include_embedded:
        return sorted(found)
    particles = "|".join(re.escape(particle) for particle in _PARTICLES)
    return sorted(
        (start, end, name) for start, end, name in found
        if (start == 0 or not (text[start - 1].isalnum() or text[start - 1] == "_"))
        and re.match(rf"(?:{particles})*(?!\w)", text[end:])
    )


def _age_number(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    if word in _ONES:
        return _ONES[word]
    for prefix, tens in _TENS.items():
        if word == prefix:
            return tens
        if word.startswith(prefix) and word[len(prefix):] in _ONES:
            return tens + _ONES[word[len(prefix):]]
    return None


def _elapsed_year_number(word: str) -> int | None:
    age_number = _age_number(word)
    if age_number is not None:
        return age_number

    total = section = 0
    current: int | None = None
    for character in word:
        if character in _ELAPSED_YEAR_DIGITS:
            current = _ELAPSED_YEAR_DIGITS[character]
        elif character in _ELAPSED_YEAR_SMALL_UNITS:
            multiplier = current if current is not None else 1
            section += multiplier * _ELAPSED_YEAR_SMALL_UNITS[character]
            current = None
        elif character in _ELAPSED_YEAR_LARGE_UNITS:
            group = section + (current or 0)
            total += (group or 1) * _ELAPSED_YEAR_LARGE_UNITS[character]
            section = 0
            current = None
        else:
            return None
    return total + section + (current or 0)


def _canonical_age(text: str) -> int | None:
    """Read a bare age or an explicit, unqualified age-label statement."""
    match = _AGE_VALUE.fullmatch(text) or _LABELED_AGE_VALUE.fullmatch(text)
    return _age_number(match.group(1)) if match else None


def _age_mentions(text: str) -> list[re.Match[str]]:
    return sorted(
        (*_AGE_MENTION.finditer(text), *_BARE_AGE_MENTION.finditer(text)),
        key=lambda mention: (mention.start(), mention.end()),
    )


def _nearest_mention(
    mention: re.Match[str], named: list[tuple[int, int, str]]
) -> tuple[int, int, str] | None:
    nearby = [
        (start, end, name) for start, end, name in named
        if max(0, start - mention.end(), mention.start() - end) <= _NAME_REACH
    ]
    return min(
        nearby,
        key=lambda item: (
            max(0, item[0] - mention.end(), mention.start() - item[1]),
            0 if item[1] <= mention.start() else 1,
            item[0],
        ),
        default=None,
    )


def _age_owner_mentions(text: str, names: Iterable[str]) -> list[tuple[int, int, str]]:
    """Keep embedded aliases as review candidates; exact ownership is resolved separately."""

    return _mentions_of(text, names, include_embedded=True)


def _postposed_age_owner(
    mention: re.Match[str],
    sentence: str,
    sentence_start: int,
    sentence_names: list[tuple[int, int, str]],
) -> tuple[str | None, bool, bool]:
    """Resolve ``N살의 이름`` and ``N인 이름`` before nearby earlier mentions."""

    local_end = mention.end() - sentence_start
    suffix = re.match(r"\s*(?P<marker>의|인)\s*", sentence[local_end:])
    if suffix is None:
        return None, False, False

    name_start = mention.end() + suffix.end()
    candidates: list[tuple[int, int, str]] = []
    for start, end, name in sentence_names:
        if start < name_start:
            continue
        gap = sentence[name_start - sentence_start : start - sentence_start]
        if not gap or re.fullmatch(r"[가-힣]{1,5}\s+", gap):
            candidates.append((start, end, name))
    if not candidates:
        clause = re.split(r"[,.!?;\n]", sentence[name_start - sentence_start :], maxsplit=1)[0]
        if _AGE_PREDICATE_PHRASE.fullmatch(clause.rstrip()):
            return None, False, False  # `30세의 청년이었다`: the clause's subject owns the age
        return None, False, True

    _start, _end, owner = min(candidates, key=lambda item: item[0])
    return owner, False, True


def _postposed_subject_age_owner(
    mention: re.Match[str],
    sentence: str,
    sentence_start: int,
    sentence_names: list[tuple[int, int, str]],
    known_names: Iterable[str],
) -> tuple[str | None, bool, bool]:
    """Resolve a name used as the subject immediately after an age phrase."""

    local_end = mention.end() - sentence_start
    after = sentence[local_end:]
    for start, end, owner in sentence_names:
        if start < mention.end():
            continue
        gap = sentence[local_end : start - sentence_start]
        if not re.fullmatch(r"\s*(?:에\s*)?", gap):
            continue
        particle = re.match(r"(?:은|는|이|가)(?=\s|[,.!?]|$)", sentence[end - sentence_start :])
        if particle:
            return owner, False, True

    unregistered_subject = re.match(
        r"\s*(?P<name>[가-힣]{2,4})(?:\s*(?:씨|님|선생|박사))?(?:은|는|이|가)(?=\s|[,.!?]|$)",
        after,
    )
    if unregistered_subject and unregistered_subject.group("name") not in _AGE_NONPERSON_SUBJECTS:
        surface = unregistered_subject.group("name")
        if any(form in surface for name in known_names for form in _name_forms(name)):
            return None, False, False
        return None, False, True
    return None, False, False


def _subject_age_owner(
    mention: re.Match[str],
    sentence: str,
    sentence_start: int,
    sentence_names: list[tuple[int, int, str]],
    known_names: Iterable[str],
) -> tuple[str | None, bool, bool]:
    """Prefer the character who is the age clause's subject or topic."""

    local_start = mention.start() - sentence_start
    before = sentence[:local_start]
    subjects = [
        (start, end, name)
        for start, end, name in sentence_names
        if end <= mention.start()
        and _AGE_SUBJECT_TAIL.fullmatch(sentence[end - sentence_start : local_start])
    ]
    if subjects:
        _start, _end, owner = max(subjects, key=lambda item: item[0])
        return owner, False, True

    unregistered_subject = _AGE_SUBJECT_WORD.search(before)
    if unregistered_subject and unregistered_subject.group("name") not in _AGE_NONPERSON_SUBJECTS:
        surface = unregistered_subject.group("name")
        if any(form in surface for name in known_names for form in _name_forms(name)):
            return None, False, False
        prior_genitive = any(
            end <= sentence_start + unregistered_subject.start("name")
            and re.search(r"의(?:\s+[^\n,.;!?]*)?\s*$", sentence[end - sentence_start : unregistered_subject.start("name")])
            for _start, end, _name in sentence_names
        )
        if unregistered_subject.group("particle") in {"은", "는"} or prior_genitive:
            return None, False, True
    return None, False, False


def _age_mention_owner(
    mention: re.Match[str],
    sentence: str,
    sentence_start: int,
    ages: dict[str, tuple[int, CanonValue]],
    named: set[tuple[int, int, str]],
    all_named: list[tuple[int, int, str]],
    sexes: dict[str, str],
) -> tuple[str | None, bool]:
    mention_start = mention.start()
    sentence_end = sentence_start + len(sentence)
    sentence_names = [
        (start, end, name)
        for start, end, name in named
        if sentence_start <= start and end <= sentence_end
    ]
    for marker in _AGE_OWNER_AMBIGUITY.finditer(sentence):
        marker_start = sentence_start + marker.start()
        marker_end = sentence_start + marker.end()
        if marker_end > mention_start or mention_start - marker_end > _NAME_REACH:
            continue
        local_names = {
            name
            for start, end, name in sentence_names
            if max(0, start - marker_end, marker_start - end) <= _NAME_REACH
        }
        if len(local_names) < 2:
            continue
        explicitly_named_after_marker = {
            name
            for start, end, name in sentence_names
            if marker_end <= start and end <= mention_start
        }
        if len(explicitly_named_after_marker) != 1:
            return None, True

    owner, uncertain, explicit = _postposed_age_owner(
        mention, sentence, sentence_start, sentence_names
    )
    if explicit:
        return owner, uncertain

    owner, uncertain, explicit = _subject_age_owner(
        mention, sentence, sentence_start, sentence_names, ages
    )
    if explicit:
        return owner, uncertain

    owner, uncertain, explicit = _postposed_subject_age_owner(
        mention, sentence, sentence_start, sentence_names, ages
    )
    if explicit:
        return owner, uncertain

    closest = _nearest_mention(mention, all_named)
    owner = closest[2] if closest else None
    uncertain = closest is not None and closest not in named
    if closest is not None and owner is not None:
        uncertain |= closest[1] - closest[0] != len(owner)
    if owner is None:
        candidates = []
        for pronoun in _PRONOUN.finditer(sentence):
            expected = "female" if pronoun.group("pronoun") == "그녀" else "male"
            candidates.extend(name for name in ages if sexes.get(name) == expected)
        if len(set(candidates)) == 1:
            owner = candidates[0]
            uncertain = True
    return owner, uncertain


def _parsed_age_owners(text: str, values: tuple[CanonValue, ...]) -> set[str]:
    ages: dict[str, tuple[int, CanonValue]] = {}
    for value in values:
        key_match = _AGE_KEY.fullmatch(value.key)
        number = _canonical_age(value.text)
        if key_match and number is not None:
            ages.setdefault(key_match.group(1), (number, value))
    if not ages:
        return set()

    everyone = {
        name for value in values if (name := _character_name(value.key)) is not None
    } | set(ages)
    named = set(_mentions_of(text, everyone))
    all_named = _age_owner_mentions(text, everyone)
    sexes = _character_sexes(values)
    spans = _sentence_spans(text)
    starts = [start for start, _end in spans]
    owners: set[str] = set()
    for mention in _age_mentions(text):
        if _age_number(mention.group(1)) is None:
            continue
        sentence_index = bisect_right(starts, mention.start()) - 1
        sentence_start, sentence_end = spans[sentence_index]
        sentence = text[sentence_start:sentence_end]
        if _AGE_BIRTHDAY_AFTER.match(text[mention.end() : sentence_end]):
            continue
        owner, _uncertain = _age_mention_owner(
            mention, sentence, sentence_start, ages, named, all_named, sexes
        )
        if owner in ages:
            owners.add(owner)
    return owners


def _birthday_age_owners(text: str, values: tuple[CanonValue, ...]) -> set[str]:
    ages: dict[str, tuple[int, CanonValue]] = {}
    for value in values:
        key_match = _AGE_KEY.fullmatch(value.key)
        number = _canonical_age(value.text)
        if key_match and number is not None:
            ages.setdefault(key_match.group(1), (number, value))
    if not ages:
        return set()

    everyone = {
        name for value in values if (name := _character_name(value.key)) is not None
    } | set(ages)
    named = set(_mentions_of(text, everyone))
    all_named = _age_owner_mentions(text, everyone)
    sexes = _character_sexes(values)
    spans = _sentence_spans(text)
    starts = [start for start, _end in spans]
    owners: set[str] = set()
    for mention in _age_mentions(text):
        sentence_index = bisect_right(starts, mention.start()) - 1
        sentence_start, sentence_end = spans[sentence_index]
        if not _AGE_BIRTHDAY_AFTER.match(text[mention.end() : sentence_end]):
            continue
        owner, _uncertain = _age_mention_owner(
            mention, text[sentence_start:sentence_end], sentence_start, ages, named, all_named, sexes
        )
        if owner in ages:
            owners.add(owner)
    return owners


def _unread_age_cue_names(text: str, names: Iterable[str]) -> set[str]:
    """Names locally associated with milestone ages that R1 cannot parse."""

    known_names = set(names)
    unread: set[str] = set()
    for _start, _end, clause in _clauses(text):
        named = _mentions_of(clause, known_names)
        if not named:
            continue
        for cue in _AGE_MILESTONE_CUE.finditer(clause):
            nearby = {
                name
                for start, end, name in named
                if max(0, start - cue.end(), cue.start() - end) <= _NAME_REACH
            }
            # A single local name is the owner. With multiple candidates, keep
            # every candidate unchecked instead of letting proximity break a tie.
            unread.update(nearby)
    return unread


def _paragraph_at(text: str, position: int) -> str:
    start = 0
    end = len(text)
    for boundary in re.finditer(r"(?:\r?\n[ \t]*){2,}", text):
        if boundary.end() <= position:
            start = boundary.end()
        elif boundary.start() >= position:
            end = boundary.start()
            break
    return text[start:end]


def _is_past_age(
    text: str, mention: re.Match[str], sentence_start: int, sentence_end: int
) -> bool:
    after = text[mention.end():sentence_end]
    preceding = text[max(sentence_start, mention.start() - 40):mention.start()]
    current_anchor = _AGE_CURRENT_ANCHOR.search(preceding) is not None
    if _AGE_PAST_STAGE_AFTER.match(after):
        return True
    if _AGE_PAST_EVENT_AFTER.match(after) and _AGE_PAST_VERB.search(after[:160]):
        return not current_anchor

    sentence = text[sentence_start:sentence_end]
    anchored = bool(_AGE_PAST_ANCHOR.search(sentence))
    anchored |= _AGE_PAST_EVENT_BEFORE.search(preceding) is not None
    if not anchored and re.match(r"\s*의", after):
        anchored = bool(_AGE_PAST_ANCHOR.search(_paragraph_at(text, mention.start())))
    if not anchored:
        return False

    return not current_anchor


def _check_age(text: str, values: tuple[CanonValue, ...], conflicts: list[Conflict],
               possible: list[Conflict]) -> None:
    ages: dict[str, tuple[int, CanonValue]] = {}
    for value in values:
        key_match = _AGE_KEY.fullmatch(value.key)
        number = _canonical_age(value.text)
        if key_match and number is not None:
            ages.setdefault(key_match.group(1), (number, value))
    if not ages:
        return
    everyone = {name for value in values if (name := _character_name(value.key)) is not None} | set(ages)
    named = set(_mentions_of(text, everyone))
    all_named = _age_owner_mentions(text, everyone)
    sexes = _character_sexes(values)
    spans = _sentence_spans(text)
    starts = [a for a, _ in spans]
    quoted = tuple(_DIALOGUE.finditer(text))
    for mention in _age_mentions(text):
        number = _age_number(mention.group(1))
        if number is None:
            continue
        sentence_index = bisect_right(starts, mention.start()) - 1
        sentence_start, sentence_end = spans[sentence_index]
        sentence = text[sentence_start:sentence_end]
        if _AGE_BIRTHDAY_AFTER.match(text[mention.end() : sentence_end]):
            continue
        owner, uncertain = _age_mention_owner(
            mention, sentence, sentence_start, ages, named, all_named, sexes
        )
        if owner not in ages:
            continue
        canonical_age, value = ages[owner]
        if number == canonical_age:
            continue
        earlier = text[max(sentence_start, mention.start() - 30):mention.start()]
        after = text[mention.end():min(sentence_end, mention.end() + 24)]
        after_sentence = text[mention.end():sentence_end]
        if _AGE_PLAN_AFTER.match(after_sentence):
            continue
        if _is_past_age(text, mention, sentence_start, sentence_end):
            years_ago = _AGE_YEARS_AGO.search(sentence)
            arithmetic_mismatch = number > canonical_age
            if years_ago:
                elapsed = _elapsed_year_number(years_ago.group(1))
                age_delta = canonical_age - number
                if elapsed is None:
                    arithmetic_mismatch = True
                else:
                    arithmetic_mismatch |= not elapsed <= age_delta <= elapsed + 1
            if not arithmetic_mismatch:
                continue
            uncertain = True
        nearby_subject = re.search(r"(?<![가-힣])([가-힣]{2,4}?)(?:은|는|이|가)\s*$", earlier)
        other_subject = bool(
            nearby_subject
            and nearby_subject.group(1) not in _name_forms(owner)
        )
        uncertain |= bool(
            other_subject
            or re.search(r"그\s*무렵", earlier)
            or any(q.start() <= mention.start() < q.end() for q in quoted)
            or re.search(r"아니|않|믿었|생각했|여겼|알았|물었|\?", after)
            or re.search(r"믿|생각|여겼|알았", earlier)
            or re.match(r"\s*(?:아이|어린이|소년|소녀|동생|형|누나|아들|딸)", after)
            or re.search(r"(?:동생|형|누나|아들|딸)\s+[가-힣]{2,4}(?:은|는|이|가)\s*$", earlier)
        )
        item = Conflict("R1", value.key, value.claim_id, mention.group(0),
                        f"{owner}의 확정 나이는 {canonical_age}세지만 다른 나이가 언급됩니다.")
        _add_conflict(possible if uncertain else conflicts, item)

def _character_name(key: str) -> str | None:
    found = _CHARACTER_ATTRIBUTE.fullmatch(key)
    return None if found is None else found.group(1)


def _attribute_terms(attribute: str) -> tuple[str, ...]:
    return _ATTRIBUTE_TERMS.get(attribute, ())


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Where each sentence of ``text`` starts and ends: a sentence mark ends one, and so does a line break."""

    spans = []
    start = 0
    for index, character in enumerate(text):
        if character in _SENTENCE_END or character == "\n":
            end = index + 1 if character in _SENTENCE_END else index
            if end > start:
                spans.append((start, end))
            start = index + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _sentence_at(text: str, spans: list[tuple[int, int]], starts: list[int], position: int) -> str:
    """The sentence a position of ``text`` falls in (the last one before it, if it falls between two)."""

    index = bisect_right(starts, position) - 1
    if index < 0:
        return ""
    start, end = spans[index]
    return text[start:end]


def _name_forms(name: str) -> tuple[str, ...]:
    """The name as written and, for a name of three or more letters, the given name without its first (family) one."""

    return (name, name[1:]) if len(name) >= 3 else (name,)


def _about_the_pair(
    sentence: str, left: str, right: str, names: tuple[str, ...] | None = None
) -> bool:
    """A sentence speaks of these two when it says "the two of them" or names both, whole or by given name."""

    if any(word in sentence for word in _PAIR_WORDS):
        return True
    mentioned = {name for _, _, name in _mentions_of(sentence, names or (left, right))}
    return left in mentioned and right in mentioned


def _similarity_directly_describes(
    clause: str, similarity: re.Match[str], attribute: re.Match[str]
) -> bool:
    phrase = similarity.group(0)
    if similarity.end() <= attribute.start():
        gap = clause[similarity.end() : attribute.start()]
        if not gap.strip() and len(gap) <= 3:
            return True
        return phrase in {"똑같", "비슷", "동일"} and gap.strip() in {"은", "한"}
    if attribute.end() > similarity.start():
        return False
    gap = clause[attribute.end() : similarity.start()]
    if len(gap) > 18:
        return False
    suffix = clause[similarity.end() :]
    if phrase == "같은" or (phrase == "똑같" and suffix.startswith("은")):
        return False
    if phrase == "비슷" and suffix.startswith("한"):
        return False
    return not (phrase == "동일" and suffix.startswith("한"))


def _similarity_is_negated(
    clause: str,
    similarity: re.Match[str],
    attribute: re.Match[str] | None = None,
) -> bool:
    suffix = clause[similarity.end() :]
    if re.match(
        r"\s*(?:(?:지|하지|해질|이\s*지)\s*(?:않|못|리\s*없)|"
        r"(?:않|못)(?!을\s*(?:리\s*없|수\s*없|수밖에\s*없))|"
        r"(?:이|은|는|도)\s*(?:아니|아닙|아니었|아니라)|"
        r"일\s*리\s*없)",
        suffix,
    ):
        return True

    if attribute is not None and similarity.end() < attribute.start():
        after_attribute = clause[attribute.end() :]
        return bool(
            re.match(
                r"\s*(?:(?:은|는|이|가|도|만)\s*)?"
                r"(?:아니|아닙|아닐|일\s*리\s*없)",
                after_attribute,
            )
        )
    return False


def _similarity_has_explicit_object(clause: str, similarity: re.Match[str]) -> bool:
    following_words = re.findall(r"[\w]+", clause[similarity.end() : similarity.end() + 40])
    for word in following_words[:5]:
        for particle in _PARTICLES:
            if word.endswith(particle) and len(word) - len(particle) >= 2:
                return True
    return False


def _check_contrasts(
    text: str,
    values: tuple[CanonValue, ...],
    conflicts: list[Conflict],
    possible: list[Conflict],
) -> None:
    by_attribute: dict[str, list[tuple[str, CanonValue]]] = {}
    for value in values:
        match = _CHARACTER_ATTRIBUTE.fullmatch(value.key)
        if match is not None:
            character, attribute = match.groups()
            by_attribute.setdefault(attribute, []).append((character, value))

    spans = _sentence_spans(text)
    starts = [start for start, _ in spans]
    names = tuple(dict.fromkeys(name for entries in by_attribute.values() for name, _ in entries))
    age_values = by_attribute.get("age", ())
    ages = {
        name: (number, value)
        for name, value in age_values
        if (number := _canonical_age(value.text)) is not None
    }
    age_named = set(_mentions_of(text, ages))
    all_age_named = _age_owner_mentions(text, ages)
    sexes = _character_sexes(values)
    age_mentions = _age_mentions(text)
    for attribute, entries in by_attribute.items():
        for left_index, (left_character, left_value) in enumerate(entries):
            for right_character, right_value in entries[left_index + 1 :]:
                if (
                    left_character == right_character
                    or left_value.text.strip() == right_value.text.strip()
                ):
                    continue
                provided_terms = tuple(
                    dict.fromkeys(term for term in (*left_value.terms, *right_value.terms) if term)
                )
                attribute_terms = provided_terms or _attribute_terms(attribute)
                if not attribute_terms and attribute != "age":
                    continue
                for clause_start, _clause_end, clause in _clauses(text):
                    if _DIFFERENCE.search(clause):
                        continue
                    sentence_index = bisect_right(starts, clause_start) - 1
                    sentence_start, sentence_end = spans[sentence_index]
                    sentence = text[sentence_start:sentence_end]
                    exact_names = set(_mentions_of(sentence, names))
                    all_names = _mentions_of(sentence, names, include_embedded=True)
                    pair_uncertain = any(
                        item not in exact_names and item[2] in (left_character, right_character)
                        for item in all_names
                    )
                    if not _about_the_pair(sentence, left_character, right_character, names):
                        mentioned = {name for _, _, name in all_names}
                        if left_character not in mentioned or right_character not in mentioned:
                            continue
                        pair_uncertain = True
                    age_equal = re.search(r"동갑", clause)
                    noncurrent_pair_age = False
                    if attribute == "age" and ages:
                        for mention in age_mentions:
                            if not sentence_start <= mention.start() < sentence_end:
                                continue
                            if not (
                                _AGE_BIRTHDAY_AFTER.match(text[mention.end() : sentence_end])
                                or _is_past_age(text, mention, sentence_start, sentence_end)
                            ):
                                continue
                            owner, _uncertain = _age_mention_owner(
                                mention,
                                sentence,
                                sentence_start,
                                ages,
                                age_named,
                                all_age_named,
                                sexes,
                            )
                            if owner in (left_character, right_character):
                                noncurrent_pair_age = True
                                break
                    if (
                        attribute == "age"
                        and age_equal
                        and not noncurrent_pair_age
                        and not _similarity_is_negated(clause, age_equal)
                    ):
                        for value in (left_value, right_value):
                            _add_conflict(possible if pair_uncertain else conflicts, Conflict(
                                "R2", value.key, value.claim_id, "동갑",
                                f"{left_character}와 {right_character}의 확정 나이가 다릅니다.",
                            ))
                    if attribute == "age" and noncurrent_pair_age:
                        continue
                    attribute_matches = [
                        match
                        for term in attribute_terms
                        if term
                        for match in re.finditer(re.escape(term), clause)
                    ]
                    for similarity in _SIMILARITY.finditer(clause):
                        if re.search(r"반대|다르|차이", sentence):
                            continue
                        for attribute_match in attribute_matches:
                            if not _similarity_directly_describes(
                                clause, similarity, attribute_match
                            ):
                                continue
                            if _similarity_is_negated(
                                clause, similarity, attribute_match
                            ):
                                continue
                            uncertain = pair_uncertain or bool(
                                similarity.group(0) == "같은"
                                and similarity.end() < attribute_match.start()
                                and re.match(r"\s+[가-힣]+", clause[attribute_match.end():])
                                and not re.match(r"\s*(?:으로|로|이다|이었다)", clause[attribute_match.end():])
                            )
                            start = min(similarity.start(), attribute_match.start())
                            end = max(similarity.end(), attribute_match.end())
                            evidence = text[clause_start + start : clause_start + end]
                            message = (
                                f"{left_character}와 {right_character}의 확정 {attribute} 값은 "
                                "서로 다르지만 결과에서는 비슷하거나 같은 것으로 표현했습니다."
                            )
                            for value in (left_value, right_value):
                                _add_conflict(
                                    possible if uncertain else conflicts,
                                    Conflict(
                                        rule="R2",
                                        value_key=value.key,
                                        claim_id=value.claim_id,
                                        evidence=evidence,
                                        message=message,
                                    ),
                                )


def _check_quoted_prohibitions(
    text: str,
    values: tuple[CanonValue, ...],
    conflicts: list[Conflict],
    possible: list[Conflict],
) -> None:
    for value in values:
        if value.kind != "prohibition":
            continue
        for quoted in _QUOTED_PROHIBITION.finditer(value.text):
            phrase = next((part for part in quoted.groups() if part), "")
            linked_siot = _find_linking_siot_prohibition(text, phrase)
            matches = tuple(
                evidence
                for evidence in _find_approximate_prohibition(text, phrase)
                if not any(evidence == linked or evidence in linked for linked in linked_siot)
            )
            for evidence in linked_siot:
                _add_conflict(
                    possible,
                    Conflict(
                        "R3",
                        value.key,
                        value.claim_id,
                        evidence,
                        "금지 낱말이 ㅅ을 더한 합성어에 들어 있어 사람의 검토가 필요합니다.",
                    ),
                )
            if not matches and len(phrase) >= 10:
                # A long shared prefix with a different ending is suggestive only.
                normalized_phrase, _ = _normalize_with_spans(phrase)
                for start, end in _sentence_spans(text):
                    sentence = text[start:end]
                    normalized, spans = _normalize_with_spans(sentence)
                    prefix_length = max(7, (len(normalized_phrase) + 1) // 2)
                    prefix = normalized_phrase[:prefix_length]
                    if len(prefix) < 7:
                        continue
                    index = normalized.find(prefix)
                    if index >= 0:
                        first, last = spans[index][0], spans[index + len(prefix) - 1][1]
                        if _cuts_into_a_word(sentence, first, last, phrase):
                            continue
                        evidence = sentence[first:last]
                        _add_conflict(possible, Conflict(
                            "R3", value.key, value.claim_id, evidence,
                            "금지 구절과 일부가 같아 사람의 검토가 필요합니다.",
                        ))
            for evidence in matches:
                # The same words may be spoken by different people at different locations.
                for occurrence in re.finditer(re.escape(evidence), text):
                    location = occurrence.start()
                    spans = _sentence_spans(text)
                    starts = [a for a, _ in spans]
                    sentence_index = bisect_right(starts, location) - 1
                    sentence_start, sentence_end = spans[sentence_index]
                    sentence = text[sentence_start:sentence_end]
                    before = text[sentence_start:location]
                    names = tuple(dict.fromkeys(n for v in values if (n := _character_name(v.key))))
                    rule_names = {n for _, _, n in _mentions_of(value.text.split(phrase)[0], names)}
                    subjects = list(re.finditer(r"(?<![가-힣])([가-힣]{2,4}?)(?:은|는|이|가)(?=\s)", before))
                    speaker = subjects[-1].group(1) if subjects else None
                    subject_matches = bool(speaker and any(
                        speaker in _name_forms(name) for name in rule_names
                    ))
                    uncertain = bool(
                        re.search(r"않|아니|못했|말하지|금지", sentence)
                        or (rule_names and not subject_matches)
                    )
                    _add_conflict(
                        possible if uncertain else conflicts,
                        Conflict(
                            rule="R3",
                            value_key=value.key,
                            claim_id=value.claim_id,
                            evidence=evidence,
                            message="확정된 금지 구절과 같거나 매우 비슷한 표현이 결과에 포함되어 있습니다.",
                        ),
                    )


def _check_semantic_contrasts(
    text: str,
    values: tuple[CanonValue, ...],
    conflicts: list[Conflict],
    possible: list[Conflict],
) -> None:
    narrative = _mask_dialogue(text)
    names = tuple(
        dict.fromkeys(
            match.group(1)
            for value in values
            if (match := _CHARACTER_ATTRIBUTE.fullmatch(value.key)) is not None
        )
    )
    sexes = _character_sexes(values)
    for value in values:
        key_match = _CHARACTER_ATTRIBUTE.fullmatch(value.key)
        if key_match is None:
            continue
        character, attribute = key_match.groups()
        canonical_polarity = _canonical_polarity(value.text)
        if canonical_polarity is None:
            continue
        opposites = _opposite_predicates(value, canonical_polarity)
        terms = tuple(term for term in (value.terms or _attribute_terms(attribute)) if term)
        if not terms:
            continue
        for clause_start, clause_end in _sentence_spans(narrative):
            clause = narrative[clause_start:clause_end]
            predicates = tuple(
                sorted(
                    (match for opposite in opposites for match in opposite.finditer(clause)),
                    key=lambda match: (match.start(), match.end()),
                )
            )
            if not predicates:
                continue
            for term in terms:
                for term_match in re.finditer(re.escape(term), clause):
                    subject, uncertain = _person_for_attribute(
                        clause, term_match, clause_start, narrative, names, sexes
                    )
                    if subject is not None and subject != character:
                        continue
                    if subject is None:
                        expected = sexes.get(character)
                        ambiguous_pronouns = tuple(_PRONOUN.finditer(clause))
                        if not expected or not any(
                            (m.group("pronoun") == "그녀") == (expected == "female")
                            for m in ambiguous_pronouns
                        ):
                            continue
                        uncertain = True
                    for predicate in predicates:
                        if predicate.start() and clause[predicate.start() - 1].isalnum():
                            continue
                        if re.search(r"^(?:지(?:는|도)?\s*)?(?:않|아니|못)", clause[predicate.end():]):
                            continue
                        if predicate.group(0).startswith("세") and re.search(
                            r"(?:을|를)\s*$", clause[:predicate.start()]
                        ):
                            continue
                        between = clause[min(term_match.end(), predicate.end()):
                                         max(term_match.start(), predicate.start())]
                        if any(term in between for term in terms):
                            continue
                        distance = max(
                            0,
                            term_match.start() - predicate.end(),
                            predicate.start() - term_match.end(),
                        )
                        if distance > 60 or (distance > 24 and "," not in clause):
                            continue
                        if any(
                            max(0, other.start() - predicate.end(), predicate.start() - other.end()) < distance
                            for word in terms for other in re.finditer(re.escape(word), clause)
                        ):
                            continue
                        start = min(term_match.start(), predicate.start())
                        end = max(term_match.end(), predicate.end())
                        evidence = text[clause_start + start : clause_start + end]
                        _add_conflict(
                            possible if uncertain else conflicts,
                            Conflict(
                                rule="R6",
                                value_key=value.key,
                                claim_id=value.claim_id,
                                evidence=evidence,
                                message=(
                                    f"{character}의 확정 {attribute} 값과 반대되는 표현이 "
                                    "같은 문장에 있습니다."
                                ),
                            ),
                        )


def _check_user_patterns(
    text: str,
    values: tuple[CanonValue, ...],
    conflicts: list[Conflict],
) -> None:
    for value in values:
        if value.pattern is None:
            continue
        for match in re.finditer(value.pattern, text):
            evidence = match.group(0)
            if not evidence:
                continue
            _add_conflict(
                conflicts,
                Conflict(
                    rule="R4",
                    value_key=value.key,
                    claim_id=value.claim_id,
                    evidence=evidence,
                    message="사용자가 확정한 정규식과 결과가 일치합니다.",
                ),
            )


def check_text_with_possible(
    text: str, values: Iterable[CanonValue]
) -> tuple[tuple[Conflict, ...], tuple[Conflict, ...]]:
    """Check once, separating certain contradictions from cases needing review."""
    confirmed = tuple(value for value in values if value.status == "CONFIRMED")
    if not text or not confirmed:
        return (), ()
    conflicts: list[Conflict] = []
    possible: list[Conflict] = []
    _check_age(text, confirmed, conflicts, possible)
    _check_contrasts(text, confirmed, conflicts, possible)
    _check_quoted_prohibitions(text, confirmed, conflicts, possible)
    _check_semantic_contrasts(text, confirmed, conflicts, possible)
    _check_user_patterns(text, confirmed, conflicts)
    return tuple(conflicts), tuple(possible)


def check_text(text: str, values: Iterable[CanonValue]) -> tuple[Conflict, ...]:
    """Return only certain conflicts, keeping the original interface."""
    return check_text_with_possible(text, values)[0]

def unobservable_contrast_attributes(text: str, values: Iterable[CanonValue]) -> tuple[str, ...]:
    """Find explicit two-person comparisons that omit a known comparison property."""

    confirmed = tuple(value for value in values if value.status == "CONFIRMED")
    by_attribute: dict[str, list[tuple[str, CanonValue]]] = {}
    for value in confirmed:
        match = _CHARACTER_ATTRIBUTE.fullmatch(value.key)
        if match:
            character, attribute = match.groups()
            by_attribute.setdefault(attribute, []).append((character, value))

    unobservable: list[str] = []
    generic_pair = re.compile(r"두\s*사람|둘|서로|양쪽")
    for _start, _end, clause in _clauses(text):
        similarities = tuple(_SIMILARITY.finditer(clause))
        if not similarities or _NEGATION.search(clause) or _DIFFERENCE.search(clause):
            continue
        if all(_similarity_has_explicit_object(clause, item) for item in similarities):
            continue
        for attribute, entries in by_attribute.items():
            terms = tuple(
                dict.fromkeys(term for _character, value in entries for term in value.terms if term)
            ) or _attribute_terms(attribute)
            if not terms or any(term in clause for term in terms):
                continue
            for index, (left_character, left_value) in enumerate(entries):
                for right_character, right_value in entries[index + 1 :]:
                    if (
                        left_character == right_character
                        or left_value.text.strip() == right_value.text.strip()
                    ):
                        continue
                    named_pair = left_character in clause and right_character in clause
                    if named_pair or generic_pair.search(clause):
                        unobservable.extend(terms)
                        break
                else:
                    continue
                break
    return tuple(dict.fromkeys(unobservable))


def automatically_checkable_values(
    text: str, values: Iterable[CanonValue]
) -> tuple[CanonValue, ...]:
    """Return confirmed values whose supported rule can inspect this draft."""

    confirmed = tuple(value for value in values if value.status == "CONFIRMED")
    checkable: set[tuple[str, str]] = set()

    age_names = {
        key_match.group(1)
        for value in confirmed
        if (key_match := _AGE_KEY.fullmatch(value.key)) is not None
        and _canonical_age(value.text) is not None
    }
    unread_age_names = _unread_age_cue_names(text, age_names)
    parsed_age_names = _parsed_age_owners(text, confirmed)
    birthday_age_names = _birthday_age_owners(text, confirmed)

    for value in confirmed:
        key_match = _AGE_KEY.fullmatch(value.key)
        number = _canonical_age(value.text)
        if (
            key_match
            and number is not None
            and (
                key_match.group(1) not in unread_age_names
                or key_match.group(1) in parsed_age_names
            )
            and (
                key_match.group(1) in parsed_age_names
                or (
                    key_match.group(1) not in birthday_age_names
                    and _mentions_of(text, (key_match.group(1),))
                )
            )
        ):
            checkable.add((value.key, value.claim_id))

    by_attribute: dict[str, list[tuple[str, CanonValue]]] = {}
    for value in confirmed:
        match = _CHARACTER_ATTRIBUTE.fullmatch(value.key)
        if match:
            character, attribute = match.groups()
            by_attribute.setdefault(attribute, []).append((character, value))

    for attribute, entries in by_attribute.items():
        for left_index, (left_character, left_value) in enumerate(entries):
            for right_character, right_value in entries[left_index + 1 :]:
                if (
                    left_character == right_character
                    or left_value.text.strip() == right_value.text.strip()
                ):
                    continue
                provided_terms = tuple(
                    dict.fromkeys(term for term in (*left_value.terms, *right_value.terms) if term)
                )
                terms = provided_terms or _attribute_terms(attribute)
                if terms and any(term in text for term in terms):
                    checkable.add((left_value.key, left_value.claim_id))
                    checkable.add((right_value.key, right_value.claim_id))

    for value in confirmed:
        if value.kind == "prohibition" and _QUOTED_PROHIBITION.search(value.text):
            checkable.add((value.key, value.claim_id))
        if value.pattern:
            try:
                re.compile(value.pattern)
            except re.error:
                continue
            checkable.add((value.key, value.claim_id))

    for value in confirmed:
        match = _CHARACTER_ATTRIBUTE.fullmatch(value.key)
        if match is None or _canonical_polarity(value.text) is None:
            continue
        character, attribute = match.groups()
        terms = tuple(term for term in (value.terms or _attribute_terms(attribute)) if term)
        if _mentions_of(text, (character,)) and terms and any(term in text for term in terms):
            checkable.add((value.key, value.claim_id))

    return tuple(value for value in confirmed if (value.key, value.claim_id) in checkable)


__all__ = [
    "CanonValue",
    "Conflict",
    "automatically_checkable_values",
    "check_text",
    "check_text_with_possible",
    "unobservable_contrast_attributes",
]
