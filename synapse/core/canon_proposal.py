"""Turn claims into proposed canon values, where code builds every key and an AI may only choose words already there.

The step that was missing between a labelled claim (``canon_labels``) and a value a person can confirm
(``canon_values``). Nothing here calls a model and nothing here stores: it returns the values it would propose and
the reasons it refused the rest, and the caller decides whether to write them. A proposal is ``PROPOSED``; only a
person confirming it makes it canon.

What decides what:

* **The kind** comes from ``canon_labels``, and only when the section rule and the caller's label agree or the
  section rule is the only reader. A claim the two readers cannot settle is *not* proposed; it stays a question for
  the person (and a person can settle it by naming the section, which is the file's own words).
* **The key** is built by code wherever code can: a time-axis table row gives ``state.<first cell>.<header>`` for each
  filled cell, a prohibition or a requirement gives ``rule.<end of the claim id>``, and a character fact gives
  ``character.<name>.<attribute>``. The name is the heading's, cut off by code.
* **What an AI may choose** is only what code cannot: a character fact's attribute name (``age``) and the words that
  state it (``21세``). The attribute is one bounded English or Korean token, and the words must be in the claim or its
  own age/sex heading, character for character ignoring whitespace. Whatever is stored is the source's own text, not a
  caller's retyping of it.
* **Everything else** -- style, work information, sequence, space, review items, reference entries -- is not a value.

A refusal is a result, not an exception: one bad request does not hide the good ones.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from synapse.core.canon_claims import BULLET, NUMBERED, PARAGRAPH, TABLE_ROW, Claim
from synapse.core.canon_labels import (
    AGREED,
    CHARACTER_FACT,
    PROHIBITION,
    REQUIREMENT,
    RULE_ONLY,
    STATE_VALUE,
    character_subject,
    propose_labels,
)
from synapse.core.canon_values import ValueEntry, ValueStore, claim_source_sha256, propose

PROPOSAL_SCHEMA = "canon-proposal.v1"

MAX_KEY_CHARS = 200
_ATTRIBUTE = re.compile(r"[a-z가-힣][a-z0-9가-힣_]{0,39}")
_ID_TAIL = 12
_FORBIDDEN_IN_KEY = re.compile(r":")
_STATEMENT_SHAPES = frozenset({BULLET, NUMBERED, PARAGRAPH})
ATTRIBUTE_ALIASES = {"나이": "age", "성별": "sex"}
ATTRIBUTE_LABELS = {"age": "나이", "sex": "성별"}
VOCABULARY_KEY = "project.attribute_vocabulary"

_CHARACTER_HEADING = re.compile(
    r"^(?P<name>[^—,]+?)\s*—\s*(?P<sex>[^,，]+?)\s*[,，]\s*(?P<age>\d+\s*세)\s*$"
)


class BadKeyError(ValueError):
    """A canon key cannot be created without changing or losing one of its parts."""

    def __init__(self, message: str, *, key: str | None = None) -> None:
        super().__init__(message)
        self.key = key


def attribute_token(raw: str) -> str | None:
    """Normalize the two established Korean aliases and accept one bounded English/Korean token."""

    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    candidate = ATTRIBUTE_ALIASES.get(candidate, candidate)
    return candidate if _ATTRIBUTE.fullmatch(candidate) is not None else None


def character_key(name: str, attribute: str | None = None) -> str:
    """Build a stable character key, normalizing only the established age/sex aliases."""

    if not isinstance(name, str):
        raise BadKeyError("인물 이름은 문자열이어야 합니다.")
    if attribute is None:
        key = f"character.{name}"
        parts = (name,)
    else:
        normalized = attribute_token(attribute)
        if normalized is None:
            raise BadKeyError("인물 속성 이름을 키로 만들 수 없습니다.")
        key = f"character.{name}.{normalized}"
        parts = (name, normalized)
    problem = _key_problem(key, *parts)
    if problem:
        raise BadKeyError(problem, key=key)
    return key


def state_key(row: str, header: str) -> str:
    """Build a key for a filled time-axis cell."""

    if not isinstance(row, str) or not isinstance(header, str):
        raise BadKeyError("시간축 행과 머리글은 문자열이어야 합니다.")
    if "." in header:
        raise BadKeyError("A state header cannot contain a dot.", key=f"state.{row}.{header}")
    key = f"state.{row}.{header}"
    problem = _key_problem(key, row, header)
    if problem:
        raise BadKeyError(problem, key=key)
    return key


def character_heading(claim: Claim) -> tuple[str, str | None, str | None] | None:
    """Read one claim's character name and, only for the strict title shape, its sex and age."""

    subject = character_subject(claim)
    if subject is None:
        return None
    name, heading = subject
    match = _CHARACTER_HEADING.fullmatch(heading)
    if match is None:
        return name, None, None
    return name, match.group("sex").strip(), match.group("age").strip()


def entry_for(
    claim: Claim,
    *,
    key: str,
    kind: str,
    text: str,
    label: str,
    terms: tuple[str, ...] = (),
    pattern: str | None = None,
) -> ValueEntry:
    """Construct the sole source-backed proposed-value entry used by every proposal path."""

    problem = _key_problem(key, key)
    if problem:
        raise BadKeyError(problem, key=key if isinstance(key, str) else None)
    return ValueEntry(
        key=key,
        kind=kind,
        text=text,
        status="PROPOSED",
        claim_id=claim.id,
        file=claim.file,
        line=claim.line,
        source_sha256=claim_source_sha256(claim, key=key, kind=kind),
        label=label,
        terms=terms,
        pattern=pattern,
    )

# Why a request was refused. Closed on purpose: a caller can act on a code, and a test can name each one.
UNKNOWN_CLAIM = "unknown_claim"
LABEL_NEEDS_PERSON = "label_needs_person"
NOT_A_VALUE = "kind_not_a_value"
WRONG_SHAPE = "wrong_claim_shape"
UNEXPECTED_ARGUMENT = "unexpected_argument"
MISSING_ARGUMENT = "missing_argument"
BAD_ATTRIBUTE = "bad_attribute"
WORDS_NOT_IN_CLAIM = "words_not_in_claim"
NO_NAME = "no_name"
NAME_NOT_IN_CLAIM = "name_not_in_claim"
BAD_KEY = "bad_key"
KEY_EXISTS = "key_exists"
DUPLICATE_KEY = "duplicate_key"
NOTHING_TO_PROPOSE = "nothing_to_propose"

REFUSAL_CODES = frozenset(
    {
        UNKNOWN_CLAIM,
        LABEL_NEEDS_PERSON,
        NOT_A_VALUE,
        WRONG_SHAPE,
        UNEXPECTED_ARGUMENT,
        MISSING_ARGUMENT,
        BAD_ATTRIBUTE,
        WORDS_NOT_IN_CLAIM,
        NO_NAME,
        NAME_NOT_IN_CLAIM,
        BAD_KEY,
        KEY_EXISTS,
        DUPLICATE_KEY,
        NOTHING_TO_PROPOSE,
    }
)


@dataclass(frozen=True, slots=True)
class Request:
    """One claim a caller wants proposed, with the little a caller may add to it."""

    claim_id: str
    label: str | None = None
    attribute: str | None = None
    words: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Refusal:
    claim_id: str
    code: str
    message: str
    key: str | None = None

    def to_record(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "code": self.code,
            "message": self.message,
            "key": self.key,
        }


@dataclass(frozen=True, slots=True)
class Draft:
    """What would be proposed and what was refused; storing it is the caller's step."""

    entries: tuple[ValueEntry, ...]
    refused: tuple[Refusal, ...]

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": PROPOSAL_SCHEMA,
            "proposed": [
                {
                    "key": entry.key,
                    "kind": entry.kind,
                    "text": entry.text,
                    "status": entry.status,
                    "claim_id": entry.claim_id,
                    "file": entry.file,
                    "line": entry.line,
                }
                for entry in self.entries
            ],
            "refused": [refusal.to_record() for refusal in self.refused],
        }


def settled_kind(claim: Claim, model_label: str | None = None) -> str | None:
    """The kind ``canon_labels`` has settled for a claim, or ``None`` when a person still has to say."""

    proposal = propose_labels(
        [claim], (lambda _claims: {claim.id: model_label}) if model_label else None
    )[0]
    return proposal.label if proposal.status in {AGREED, RULE_ONLY} else None


def _key_problem(key: str, *parts: str) -> str | None:
    if not isinstance(key, str) or not all(isinstance(part, str) and part.strip() for part in parts):
        return "a part of the key is empty"
    if _FORBIDDEN_IN_KEY.search(key) or any(unicodedata.category(character) == "Cc" for character in key):
        return "a key cannot hold ':' or a control character"
    if len(key) > MAX_KEY_CHARS:
        return f"the key is longer than {MAX_KEY_CHARS} characters"
    return None


def state_cells(claim: Claim) -> tuple[tuple[str, str, str], ...]:
    """``(key, header, cell)`` for every filled cell of a time-axis row except its first, which names the row."""

    if claim.kind != TABLE_ROW or not claim.fields:
        return ()
    row = claim.fields[0][1]
    cells = []
    for header, cell in claim.fields[1:]:
        if not cell:
            continue
        try:
            key = state_key(row, header)
        except BadKeyError as exc:
            key = exc.key or ""
        cells.append((key, header, cell))
    return tuple(cells)


def _planned_state_keys(claim: Claim) -> list[str]:
    """Keep invalid state keys out of the plan while preserving their cell for request refusal."""

    if not claim.fields:
        return []
    row = claim.fields[0][1]
    keys = []
    for _cell_key, header, _cell in state_cells(claim):
        try:
            keys.append(state_key(row, header))
        except BadKeyError:
            continue
    return keys


def rule_key(claim: Claim) -> str:
    """The key of a rule taken from a claim. It does not name the kind: a rule that is re-sorted (a prohibition
    that turns out to be a style note) is still the same value, with the same key."""

    return f"rule.{claim.id[-_ID_TAIL:]}"


def _span_ignoring_whitespace(text: str, words: str) -> str | None:
    """The stretch of ``text`` that reads as ``words`` once whitespace is ignored, as the file spells it."""

    needle = "".join(character for character in words if not character.isspace())
    if not needle:
        return None
    positions = [index for index, character in enumerate(text) if not character.isspace()]
    haystack = "".join(text[index] for index in positions)
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return None
        end = found + len(needle)
        # "21" is not in "121" or "210": a number is found only as a whole number
        inside_a_number = (found > 0 and needle[0].isdigit() and haystack[found - 1].isdigit()) or (
            end < len(haystack) and needle[-1].isdigit() and haystack[end].isdigit()
        )
        if not inside_a_number:
            return text[positions[found] : positions[end - 1] + 1]
        start = found + 1


def plan_for(claim: Claim, model_label: str | None = None) -> dict[str, object]:
    """What proposing this claim would take, for a caller deciding what to ask for."""

    kind = settled_kind(claim, model_label)
    if kind is None:
        return {"plan": "ask_person", "kind": None}
    if kind == STATE_VALUE:
        return {"plan": "row", "kind": kind, "keys": _planned_state_keys(claim)}
    if kind in {PROHIBITION, REQUIREMENT} and claim.kind in _STATEMENT_SHAPES:
        return {"plan": "rule", "kind": kind, "keys": [rule_key(claim)]}
    if kind == CHARACTER_FACT:
        heading = character_heading(claim)
        needs = ["attribute", "words"] + ([] if heading else ["name"])
        return {
            "plan": "character",
            "kind": kind,
            "name": heading[0] if heading else None,
            "needs": needs,
        }
    return {"plan": None, "kind": kind}


def _refuse(claim_id: str, code: str, message: str, key: str | None = None) -> Refusal:
    return Refusal(claim_id, code, message, key)


def _unexpected(request: Request, allowed: Collection[str]) -> str | None:
    given = {
        name: getattr(request, name)
        for name in ("attribute", "words", "name")
        if getattr(request, name) is not None
    }
    extra = sorted(set(given) - set(allowed))
    return ", ".join(extra) if extra else None


def _draft_state(claim: Claim, request: Request) -> tuple[list[ValueEntry], list[Refusal]]:
    extra = _unexpected(request, ())
    if extra:
        return [], [
            _refuse(
                claim.id,
                UNEXPECTED_ARGUMENT,
                f"표의 행은 코드가 키를 만듭니다. 주지 않을 것: {extra}",
            )
        ]
    cells = state_cells(claim)
    if not cells:
        return [], [_refuse(claim.id, NOTHING_TO_PROPOSE, "이 행에는 채워진 칸이 없습니다.")]
    row = claim.fields[0][1]
    entries, refused = [], []
    for _cell_key, header, cell in cells:
        try:
            key = state_key(row, header)
            entries.append(
                entry_for(
                    claim,
                    key=key,
                    kind=STATE_VALUE,
                    text=cell,
                    label=f"{row} {header}",
                )
            )
        except BadKeyError as exc:
            refused.append(
                _refuse(claim.id, BAD_KEY, str(exc), exc.key or _cell_key)
            )
    return entries, refused


def _draft_rule(
    claim: Claim, kind: str, request: Request
) -> tuple[list[ValueEntry], list[Refusal]]:
    extra = _unexpected(request, ())
    if extra:
        return [], [
            _refuse(
                claim.id,
                UNEXPECTED_ARGUMENT,
                f"금지·요구는 코드가 키와 말을 만듭니다. 주지 않을 것: {extra}",
            )
        ]
    if claim.kind not in _STATEMENT_SHAPES:
        return [], [
            _refuse(
                claim.id, WRONG_SHAPE, "금지·요구는 글머리·번호·문단 주장에서만 만들 수 있습니다."
            )
        ]
    key = rule_key(claim)
    try:
        return [entry_for(claim, key=key, kind=kind, text=claim.text, label=kind)], []
    except BadKeyError as exc:
        return [], [_refuse(claim.id, BAD_KEY, str(exc), exc.key or key)]


def _draft_character(claim: Claim, request: Request) -> tuple[list[ValueEntry], list[Refusal]]:
    if claim.kind not in _STATEMENT_SHAPES:
        return [], [
            _refuse(
                claim.id, WRONG_SHAPE, "인물 값은 글머리·번호·문단 주장에서만 만들 수 있습니다."
            )
        ]
    heading = character_heading(claim)
    if heading is None and request.name is None:
        return [], [
            _refuse(
                claim.id,
                NO_NAME,
                "제목에서 인물 이름을 뗄 수 없습니다. name을 주세요(주장 안에 있는 말이어야 합니다).",
            )
        ]
    if heading is not None and request.name is not None:
        return [], [
            _refuse(
                claim.id, UNEXPECTED_ARGUMENT, "이름은 제목에서 코드가 뗍니다. name을 주지 마세요."
            )
        ]
    missing = [name for name in ("attribute", "words") if not getattr(request, name)]
    if missing:
        return [], [
            _refuse(claim.id, MISSING_ARGUMENT, f"인물 값에는 {', '.join(missing)}가 필요합니다.")
        ]
    raw_attribute, words = request.attribute or "", request.words or ""
    attribute = attribute_token(raw_attribute)
    if attribute is None:
        return [], [
            _refuse(
                claim.id,
                BAD_ATTRIBUTE,
                "속성 이름은 영문 소문자나 한글로 시작하는 소문자·한글·숫자·밑줄(40자까지)이어야 합니다.",
            )
        ]
    if heading is not None:
        name = heading[0]
    else:
        name = (request.name or "").strip()
        if _span_ignoring_whitespace(claim.text, name) is None:
            return [], [_refuse(claim.id, NAME_NOT_IN_CLAIM, "name이 주장 안에 그대로 없습니다.")]
    span = _span_ignoring_whitespace(claim.text, words)
    if span is None and heading is not None:
        heading_words = heading[1] if attribute == "sex" else heading[2] if attribute == "age" else None
        if heading_words is not None:
            span = _span_ignoring_whitespace(heading_words, words)
    if span is None:
        return [], [
            _refuse(
                claim.id,
                WORDS_NOT_IN_CLAIM,
                "words가 주장 안에 그대로 없습니다. 주장의 말을 그대로 고르세요.",
            )
        ]
    key = None
    try:
        key = character_key(name, attribute)
        terms = () if re.fullmatch(r"note_\d+", attribute) else (
            ATTRIBUTE_LABELS.get(attribute, attribute),
        )
        entry = entry_for(
            claim,
            key=key,
            kind=CHARACTER_FACT,
            text=span,
            label=f"{name} {attribute}",
            terms=terms,
        )
        return [entry], []
    except BadKeyError as exc:
        return [], [_refuse(claim.id, BAD_KEY, str(exc), exc.key or key)]


def draft_values(
    claims: Sequence[Claim],
    requests: Sequence[Request],
    *,
    existing_keys: Collection[str] = (),
) -> Draft:
    """What proposing these requests would add. Pure: it stores nothing and reads nothing but its arguments."""

    by_id = {claim.id: claim for claim in claims}
    taken = set(existing_keys)
    entries: list[ValueEntry] = []
    refused: list[Refusal] = []
    for request in requests:
        claim = by_id.get(request.claim_id)
        if claim is None:
            refused.append(
                _refuse(
                    request.claim_id,
                    UNKNOWN_CLAIM,
                    "넘긴 파일에서 자른 주장 중에 이 id가 없습니다.",
                )
            )
            continue
        kind = settled_kind(claim, request.label)
        if kind is None:
            refused.append(
                _refuse(
                    claim.id,
                    LABEL_NEEDS_PERSON,
                    "이름표를 코드와 AI가 함께 정하지 못했습니다. 사람이 정해야 합니다.",
                )
            )
            continue
        if kind == STATE_VALUE:
            made, bad = _draft_state(claim, request)
        elif kind in {PROHIBITION, REQUIREMENT}:
            made, bad = _draft_rule(claim, kind, request)
        elif kind == CHARACTER_FACT:
            made, bad = _draft_character(claim, request)
        else:
            made, bad = [], [_refuse(claim.id, NOT_A_VALUE, f"'{kind}' 종류는 값이 되지 않습니다.")]
        refused.extend(bad)
        for entry in made:
            if entry.key in existing_keys:
                refused.append(
                    _refuse(claim.id, KEY_EXISTS, "이미 값 파일에 있는 키입니다.", entry.key)
                )
            elif entry.key in taken:
                refused.append(
                    _refuse(
                        claim.id,
                        DUPLICATE_KEY,
                        "이번 요청 안에서 같은 키가 두 번 나왔습니다.",
                        entry.key,
                    )
                )
            else:
                taken.add(entry.key)
                entries.append(entry)
    return Draft(tuple(entries), tuple(refused))


def add_to_store(store: ValueStore, draft: Draft) -> ValueStore:
    """Append a draft's values to a store as ``PROPOSED``; the store's own checks still apply."""

    for entry in draft.entries:
        store = propose(store, entry)
    return store


__all__ = [
    "ATTRIBUTE_ALIASES",
    "ATTRIBUTE_LABELS",
    "PROPOSAL_SCHEMA",
    "REFUSAL_CODES",
    "VOCABULARY_KEY",
    "BadKeyError",
    "Draft",
    "Refusal",
    "Request",
    "add_to_store",
    "attribute_token",
    "character_heading",
    "character_key",
    "draft_values",
    "entry_for",
    "plan_for",
    "rule_key",
    "settled_kind",
    "state_cells",
    "state_key",
]
