"""Say how a canon result came to know what it says: it read it, derived it from text it was given, or it is a proposal.

A canon tool answers from files. Whether it *read* them, or was *handed* their text by a caller, or is only *proposing*
what it would write, changes how far the answer can be trusted -- and a hash alone does not say which. So each result
carries one small envelope with three kinds and nothing else:

* ``read``      -- this process opened the files and read their bytes; the hashes are of those bytes.
* ``derived``   -- computed from text a caller passed in; the process never saw the files, so the hashes are of the
                   text it was given and nothing proves that text is what is on disk.
* ``proposal``  -- what a tool would write for a person to confirm; nothing here makes it canon.

A kind is never raised: a ``derived`` result does not become ``read`` because it was read back later, and a proposal is
not a reading. Each envelope names its inputs with the hash of each and says what it could not look at, because an
input that is not listed is unknown, not absent. Proposal inputs may be text handed by the caller or hashes of files
opened by this process. It stores nothing and checks nothing; it is a label with evidence.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from synapse.core.idea_session import sha256_text

EVIDENCE_SCHEMA = "canon-evidence.v1"

READ = "read"
DERIVED = "derived"
PROPOSAL = "proposal"
KINDS = (READ, DERIVED, PROPOSAL)

HANDED_TEXT = "the files were not opened here: the text is what the caller passed, and nothing proves it is what is on disk"


class EvidenceError(ValueError):
    """An envelope that does not say what it must."""


def _limits(limits: Sequence[str]) -> list[str]:
    if isinstance(limits, (str, bytes)) or not all(
        isinstance(item, str) and item for item in limits
    ):
        raise EvidenceError("limits는 비어 있지 않은 문자열의 목록이어야 합니다.")
    return list(dict.fromkeys(limits))


def _hashed(hashes: Mapping[str, str]) -> list[dict[str, str]]:
    if not all(
        isinstance(name, str) and name and isinstance(digest, str) and digest
        for name, digest in hashes.items()
    ):
        raise EvidenceError("입력의 이름과 해시는 비어 있지 않은 문자열이어야 합니다.")
    return [{"name": name, "sha256": hashes[name]} for name in sorted(hashes)]


def _envelope(kind: str, inputs: list[dict[str, str]], limits: Sequence[str]) -> dict[str, Any]:
    if kind not in KINDS:
        raise EvidenceError(f"증거의 종류는 {KINDS} 중 하나여야 합니다: {kind!r}")
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "kind": kind,
        "inputs": inputs,
        "limits": _limits(limits),
    }


def read(
    hashes: Mapping[str, str], *, not_read: Collection[str] = (), limits: Sequence[str] = ()
) -> dict[str, Any]:
    """Bytes this process read: ``hashes`` maps a name to the sha256 of the bytes it read; ``not_read`` names what it could not.

    ``limits`` are anything else the reader wants said, such as an input that was given rather than read.
    """

    said = [f"not read: {name}" for name in sorted(not_read)]
    return _envelope(READ, _hashed(hashes), [*said, *_limits(limits)])


def derived(texts: Mapping[str, str], *, limits: Sequence[str] = ()) -> dict[str, Any]:
    """Text a caller passed in: the hashes are of that text, and the files themselves were not looked at."""

    return _envelope(
        DERIVED,
        _hashed({name: sha256_text(text) for name, text in texts.items()}),
        [HANDED_TEXT, *_limits(limits)],
    )


def proposal(
    texts: Mapping[str, str] | None = None,
    *,
    read_hashes: Mapping[str, str] | None = None,
    limits: Sequence[str] = (),
) -> dict[str, Any]:
    """What would be written for a person to confirm, from handed text or inputs this process read."""

    if (texts is None) == (read_hashes is None):
        raise EvidenceError("제안에는 texts 또는 read_hashes 중 하나만 지정해야 합니다.")
    extra_limits = _limits(limits)
    if texts is not None:
        if not isinstance(texts, Mapping) or any(not isinstance(text, str) for text in texts.values()):
            raise EvidenceError("texts는 문자열 이름과 본문의 매핑이어야 합니다.")
        inputs = _hashed({name: sha256_text(text) for name, text in texts.items()})
        proposal_limits = [HANDED_TEXT, "a proposal is not canon until a person confirms it"]
    else:
        if not isinstance(read_hashes, Mapping):
            raise EvidenceError("read_hashes는 문자열 이름과 해시의 매핑이어야 합니다.")
        if HANDED_TEXT in extra_limits:
            raise EvidenceError("read_hashes 제안에는 HANDED_TEXT를 사용할 수 없습니다.")
        inputs = _hashed(read_hashes)
        proposal_limits = [
            "made from inputs read here",
            "a proposal is not canon until a person confirms it",
        ]
    return _envelope(PROPOSAL, inputs, [*proposal_limits, *extra_limits])


def check(envelope: object) -> str | None:
    """Why ``envelope`` is not a well-formed evidence envelope, or ``None``."""

    if not isinstance(envelope, Mapping) or set(envelope) != {
        "schema_version",
        "kind",
        "inputs",
        "limits",
    }:
        return "not an envelope"
    if envelope["schema_version"] != EVIDENCE_SCHEMA or envelope["kind"] not in KINDS:
        return "unknown schema or kind"
    inputs, limits = envelope["inputs"], envelope["limits"]
    if not isinstance(inputs, list) or not all(
        isinstance(item, Mapping)
        and set(item) == {"name", "sha256"}
        and all(isinstance(v, str) and v for v in item.values())
        for item in inputs
    ):
        return "inputs are not (name, sha256) pairs"
    if not isinstance(limits, list) or not all(isinstance(item, str) for item in limits):
        return "limits are not text"
    return None


__all__ = [
    "DERIVED",
    "EVIDENCE_SCHEMA",
    "HANDED_TEXT",
    "KINDS",
    "PROPOSAL",
    "READ",
    "EvidenceError",
    "check",
    "derived",
    "proposal",
    "read",
]
