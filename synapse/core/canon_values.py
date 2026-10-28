"""Persist canon values and their append-only history with source revalidation."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from types import MappingProxyType

from synapse.core.canon_check import CanonValue
from synapse.core.canon_claims import TABLE_ROW, Claim
from synapse.core.idea_session import canonical_hash, canonical_json, sha256_text

SCHEMA = "canon-values.v1"
MAX_BYTES = 8 * 1024 * 1024
_STATUSES = frozenset({"PROPOSED", "CONFIRMED", "STALE"})
_SOURCE_HASH = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ValueEntry:
    key: str
    kind: str
    text: str
    status: str
    claim_id: str
    file: str
    line: int
    source_sha256: str
    label: str
    words: str = ""
    pattern: str | None = None
    terms: tuple[str, ...] = ()
    confirmed_at: str | None = None
    opposite_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "key",
            "kind",
            "text",
            "status",
            "claim_id",
            "file",
            "source_sha256",
            "label",
            "words",
        ):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"값의 {name} 형식이 맞지 않습니다.")
        if not self.key or self.status not in _STATUSES:
            raise ValueError("값의 key 또는 status가 맞지 않습니다.")
        if type(self.line) is not int or self.line < 1:
            raise ValueError("값의 줄 번호가 맞지 않습니다.")
        if _SOURCE_HASH.fullmatch(self.source_sha256) is None:
            raise ValueError("주장 원문의 SHA256 형식이 맞지 않습니다.")
        if self.pattern is not None and not isinstance(self.pattern, str):
            raise ValueError("값의 pattern 형식이 맞지 않습니다.")
        if self.confirmed_at is not None and not isinstance(self.confirmed_at, str):
            raise ValueError("값의 confirmed_at 형식이 맞지 않습니다.")
        if not isinstance(self.terms, (tuple, list)) or not all(
            isinstance(term, str) for term in self.terms
        ):
            raise ValueError("값의 terms 형식이 맞지 않습니다.")
        object.__setattr__(self, "terms", tuple(self.terms))
        if not isinstance(self.opposite_terms, (tuple, list)) or not all(
            isinstance(term, str) and term and "\n" not in term for term in self.opposite_terms
        ):
            raise ValueError("값의 opposite_terms 형식이 맞지 않습니다.")
        object.__setattr__(self, "opposite_terms", tuple(self.opposite_terms))


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("이력의 항목 이름은 문자열이어야 합니다.")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError("이력의 항목 형식이 맞지 않습니다.")


@dataclass(frozen=True, slots=True)
class ValueStore:
    """Entries and nested history records are immutable snapshots."""

    project_id: str
    archive_sha256: str
    entries: tuple[ValueEntry, ...]
    history: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not isinstance(self.archive_sha256, str):
            raise TypeError("저장소의 프로젝트 또는 압축 파일 정보가 맞지 않습니다.")
        entries = tuple(self.entries)
        if not all(isinstance(entry, ValueEntry) for entry in entries):
            raise ValueError("저장소의 값 항목 형식이 맞지 않습니다.")
        if len({entry.key for entry in entries}) != len(entries):
            raise ValueError("저장소에 같은 key가 두 번 있습니다.")
        history = tuple(self.history)
        if not all(isinstance(record, Mapping) for record in history):
            raise ValueError("저장소의 이력 형식이 맞지 않습니다.")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "history", tuple(_freeze(record) for record in history))


_ENTRY_KEYS = frozenset(field.name for field in fields(ValueEntry))
_LEGACY_ENTRY_KEYS = _ENTRY_KEYS - {"opposite_terms"}
_PAYLOAD_KEYS = frozenset({"schema_version", "project_id", "archive_sha256", "entries", "history"})


def new_store(project_id: str, archive_sha256: str) -> ValueStore:
    return ValueStore(project_id, archive_sha256, (), ())


def claim_source_sha256(claim: Claim, *, key: str, kind: str) -> str:
    """Fingerprint only the claim material that the derived value depends on."""
    if not isinstance(claim, Claim) or not isinstance(key, str) or not isinstance(kind, str):
        raise TypeError("값 출처 지문에는 주장, key, kind가 필요합니다.")
    dependencies: dict[str, object] = {"text": claim.text}
    if kind == "character_fact" or key.startswith("character."):
        dependencies["section"] = claim.section
    if claim.kind == TABLE_ROW or key.startswith("state."):
        dependencies["section"] = claim.section
        dependencies["fields"] = claim.fields
    if len(dependencies) == 1:
        return sha256_text(claim.text).removeprefix("sha256:")
    return canonical_hash(dependencies).removeprefix("sha256:")


def _find(store: ValueStore, key: str) -> ValueEntry:
    for entry in store.entries:
        if entry.key == key:
            return entry
    raise ValueError(f"아직 제안된 값이 없습니다: {key}")


def _transition(
    store: ValueStore, action: str, previous: ValueEntry | None, entry: ValueEntry
) -> ValueStore:
    entries = (
        (*store.entries, entry)
        if previous is None
        else tuple(entry if item.key == entry.key else item for item in store.entries)
    )
    record = {
        "action": action,
        "previous": None if previous is None else asdict(previous),
        "entry": asdict(entry),
    }
    return replace(store, entries=entries, history=(*store.history, record))


def propose(store: ValueStore, entry: ValueEntry) -> ValueStore:
    if entry.status != "PROPOSED":
        raise ValueError("PROPOSED 값만 새로 제안할 수 있습니다.")
    if any(item.key == entry.key for item in store.entries):
        raise ValueError(f"이미 있는 값입니다: {entry.key}")
    return _transition(store, "propose", None, entry)


def confirm(
    store: ValueStore,
    key: str,
    *,
    words: str,
    now: str,
    pattern: str | None = None,
    terms: tuple[str, ...] = (),
    text: str | None = None,
    opposite_terms: tuple[str, ...] = (),
) -> ValueStore:
    previous = _find(store, key)
    if previous.status != "PROPOSED":
        raise ValueError("PROPOSED 값만 확정할 수 있습니다.")
    if not isinstance(now, str):
        raise TypeError("확정 시각은 호출자가 문자열로 넣어야 합니다.")
    if text is not None and previous.kind != "attribute_vocabulary":
        raise ValueError("확정할 때 글을 바꿀 수 있는 값은 속성 목록뿐입니다.")
    entry = replace(
        previous,
        text=previous.text if text is None else text,
        status="CONFIRMED",
        words=words,
        confirmed_at=now,
        pattern=pattern,
        terms=terms,
        opposite_terms=opposite_terms,
    )
    return _transition(store, "confirm", previous, entry)


def confirm_source(store: ValueStore, candidate: ValueEntry, *, words: str, now: str) -> ValueStore:
    """Confirm a selected source while retaining its own claim and source fingerprint."""
    previous = _find(store, candidate.key)
    if previous.status != "PROPOSED" or candidate.status != "PROPOSED":
        raise ValueError("현재 제안 값의 출처만 선택할 수 있습니다.")
    if candidate != replace(
        previous,
        text=candidate.text,
        claim_id=candidate.claim_id,
        file=candidate.file,
        line=candidate.line,
        source_sha256=candidate.source_sha256,
    ):
        raise ValueError("선택한 출처가 제안 값과 일치하지 않습니다.")
    if not isinstance(now, str):
        raise TypeError("확정 시각은 문자열이어야 합니다.")
    entry = replace(candidate, status="CONFIRMED", words=words, confirmed_at=now)
    return _transition(store, "confirm_source", previous, entry)


def revise(
    store: ValueStore,
    key: str,
    *,
    text: str,
    words: str,
    now: str,
    pattern: str | None = None,
    terms: tuple[str, ...] = (),
    opposite_terms: tuple[str, ...] = (),
) -> ValueStore:
    previous = _find(store, key)
    if not isinstance(now, str):
        raise TypeError("확정 시각은 호출자가 문자열로 넣어야 합니다.")
    entry = replace(
        previous,
        text=text,
        status="CONFIRMED",
        words=words,
        confirmed_at=now,
        pattern=pattern,
        terms=terms,
        opposite_terms=opposite_terms,
    )
    return _transition(store, "revise", previous, entry)


def reclassify(
    store: ValueStore,
    key: str,
    *,
    kind: str,
    words: str | None = None,
    now: str | None = None,
) -> ValueStore:
    """Persist an explicit classification choice without discarding its prior state."""
    previous = _find(store, key)
    if previous.status == "STALE":
        raise ValueError("오래된 값은 다시 분류할 수 없습니다.")
    if not isinstance(kind, str) or not kind:
        raise ValueError("분류 종류가 비어 있습니다.")
    if previous.status == "CONFIRMED":
        if not isinstance(words, str) or not words.strip() or not isinstance(now, str) or not now:
            raise ValueError("확정된 값의 분류를 바꾸려면 재확인 말과 시각이 필요합니다.")
        saved_words, confirmed_at = words, now
    else:
        if words is not None or now is not None:
            raise ValueError("제안 값의 분류 선택에는 확정 말이나 시각을 넣을 수 없습니다.")
        saved_words, confirmed_at = previous.words, previous.confirmed_at
    kind_changed = previous.kind != kind
    entry = replace(
        previous,
        kind=kind,
        status=previous.status,
        words=saved_words,
        confirmed_at=confirmed_at,
        pattern=None if kind_changed else previous.pattern,
        terms=() if kind_changed else previous.terms,
        opposite_terms=() if kind_changed else previous.opposite_terms,
    )
    return _transition(store, "reclassify", previous, entry)


def _entry_from_record(record: object) -> ValueEntry:
    if not isinstance(record, Mapping):
        raise TypeError("저장된 값의 항목 형식이 맞지 않습니다.")
    keys = frozenset(record)
    if keys not in {_ENTRY_KEYS, _LEGACY_ENTRY_KEYS}:
        raise ValueError("저장된 값의 항목이 맞지 않습니다.")
    entry = dict(record)
    entry.setdefault("opposite_terms", ())
    return ValueEntry(**entry)


def _validate_store(store: ValueStore) -> None:
    current: dict[str, ValueEntry] = {}
    for record in store.history:
        if set(record) != {"action", "previous", "entry"}:
            raise ValueError("저장된 이력의 항목이 맞지 않습니다.")
        entry = _entry_from_record(record["entry"])
        previous_record = record["previous"]
        previous = None if previous_record is None else _entry_from_record(previous_record)
        if current.get(entry.key) != previous:
            raise ValueError("이전 값과 이력의 순서가 맞지 않습니다.")
        action = record["action"]
        if not isinstance(action, str):
            raise TypeError("저장된 이력의 작업 이름이 맞지 않습니다.")
        if action == "propose":
            valid = previous is None and entry.status == "PROPOSED"
        elif previous is None:
            valid = False
        elif action in {"confirm", "revise"}:
            expected = replace(
                previous,
                text=entry.text
                if action == "revise"
                or (action == "confirm" and previous.kind == "attribute_vocabulary")
                else previous.text,
                status="CONFIRMED",
                words=entry.words,
                confirmed_at=entry.confirmed_at,
                pattern=entry.pattern,
                terms=entry.terms,
                opposite_terms=entry.opposite_terms,
            )
            valid = (
                entry == expected
                and isinstance(entry.confirmed_at, str)
                and (action == "revise" or previous.status == "PROPOSED")
            )
        elif action == "confirm_source":
            expected = replace(
                previous,
                text=entry.text,
                claim_id=entry.claim_id,
                file=entry.file,
                line=entry.line,
                source_sha256=entry.source_sha256,
                status="CONFIRMED",
                words=entry.words,
                confirmed_at=entry.confirmed_at,
            )
            valid = (
                previous.status == "PROPOSED"
                and entry == expected
                and isinstance(entry.confirmed_at, str)
            )
        elif action == "stale":
            valid = previous.status != "STALE" and entry == replace(previous, status="STALE")
        elif action == "source_rebind":
            valid = (
                previous.status != "STALE"
                and entry == replace(previous, source_sha256=entry.source_sha256)
                and entry.source_sha256 != previous.source_sha256
            )
        elif action == "reclassify":
            kind_changed = previous.kind != entry.kind
            expected = replace(
                previous,
                kind=entry.kind,
                status=previous.status,
                words=entry.words if previous.status == "CONFIRMED" else previous.words,
                confirmed_at=entry.confirmed_at
                if previous.status == "CONFIRMED"
                else previous.confirmed_at,
                pattern=None if kind_changed else previous.pattern,
                terms=() if kind_changed else previous.terms,
                opposite_terms=() if kind_changed else previous.opposite_terms,
            )
            valid = (
                previous.status != "STALE"
                and entry == expected
                and (
                    previous.status != "CONFIRMED"
                    or (bool(entry.words.strip()) and isinstance(entry.confirmed_at, str))
                )
            )
        else:
            valid = False
        if not valid:
            raise ValueError("저장된 이력의 상태 전환이 맞지 않습니다.")
        current[entry.key] = entry
    if tuple(current.values()) != store.entries:
        raise ValueError("현재 값이 저장된 이력과 맞지 않습니다.")


def _payload(store: ValueStore) -> dict[str, object]:
    _validate_store(store)
    return {
        "schema_version": SCHEMA,
        "project_id": store.project_id,
        "archive_sha256": store.archive_sha256,
        "entries": [asdict(entry) for entry in store.entries],
        "history": list(store.history),
    }


def _store_from_bytes(data: bytes, path: Path) -> ValueStore:
    if not isinstance(data, bytes):
        raise TypeError("저장된 값 파일은 bytes여야 합니다.")
    if len(data) > MAX_BYTES:
        raise ValueError("저장된 값 파일이 8MB를 넘습니다.")
    try:
        document = json.loads(data.decode("utf-8-sig"))
        if not isinstance(document, dict) or set(document) != {"payload", "sha256"}:
            raise ValueError("저장된 값 파일의 모양이 맞지 않습니다.")
        payload = document["payload"]
        if not isinstance(payload, dict) or document["sha256"] != canonical_hash(payload):
            raise ValueError("저장된 값 파일의 체크섬이 맞지 않습니다.")
        if payload.get("schema_version") != SCHEMA or set(payload) != _PAYLOAD_KEYS:
            raise ValueError("저장된 값 파일의 스키마가 맞지 않습니다.")
        if not isinstance(payload["entries"], list) or not isinstance(payload["history"], list):
            raise TypeError("저장된 값 또는 이력의 형식이 맞지 않습니다.")
        store = ValueStore(
            payload["project_id"],
            payload["archive_sha256"],
            tuple(_entry_from_record(record) for record in payload["entries"]),
            tuple(payload["history"]),
        )
        _validate_store(store)
        return store
    except (UnicodeError, RecursionError, TypeError) as exc:
        raise ValueError(f"저장된 값 파일을 읽을 수 없습니다: {path}") from exc


def _read_store(path: Path) -> ValueStore:
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("저장된 값 파일이 8MB를 넘습니다.")
        with path.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"저장된 값 파일을 읽을 수 없습니다: {path}") from exc
    return _store_from_bytes(data, path)


def _require_ancestor(old: ValueStore, new: ValueStore) -> None:
    if (
        old.project_id != new.project_id
        or old.archive_sha256 != new.archive_sha256
        or len(old.history) > len(new.history)
        or old.history != new.history[: len(old.history)]
    ):
        raise ValueError("기존 기록이 새 기록의 조상이 아니어서 저장할 수 없습니다.")


def save(store: ValueStore, path: str | Path) -> None:
    try:
        payload = _payload(store)
        data = (
            canonical_json({"payload": payload, "sha256": canonical_hash(payload)}) + "\n"
        ).encode("utf-8")
    except (UnicodeError, RecursionError, TypeError) as exc:
        raise ValueError("값 기록을 저장할 수 있는 형식이 아닙니다.") from exc
    if len(data) > MAX_BYTES:
        raise ValueError("값 파일이 8MB를 넘어서 저장할 수 없습니다.")
    destination = Path(path).resolve()
    lock = destination.with_name(f".{destination.name}.lock")
    temporary: Path | None = None
    lock_owned = False
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        lock_owned = True
        os.close(descriptor)
        if destination.exists():
            _require_ancestor(_read_store(destination), store)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        raise ValueError(f"값 파일을 저장할 수 없습니다: {destination}") from exc
    finally:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise ValueError("저장용 임시 파일을 정리할 수 없습니다.") from exc
        finally:
            if lock_owned:
                try:
                    lock.unlink(missing_ok=True)
                except OSError as exc:
                    raise ValueError("저장용 잠금을 정리할 수 없습니다.") from exc


def source_current(entry: ValueEntry, claim: Claim | None) -> bool:
    """Whether the claim a value was taken from is still there, as far as the value depends on it."""

    return (
        claim is not None
        and claim_source_sha256(claim, key=entry.key, kind=entry.kind) == entry.source_sha256
    )


def load(
    path: str | Path,
    claims: Iterable[Claim],
    *,
    only_files: Collection[str] | None = None,
    archive_sha256: str | None = None,
) -> ValueStore:
    """Read a values file and mark stale every value whose source words are no longer in ``claims``.

    With ``only_files``, only values taken from those files are checked: the claims of a file that was not
    looked at say nothing about it, so a value from such a file is returned as it was stored, not marked stale.

    A value stored with the older fingerprint (the claim's text alone) is re-bound to the current one when
    ``archive_sha256`` is the archive the store was made for; otherwise it is marked stale.
    """

    return _revalidate_store(
        _read_store(Path(path)),
        claims,
        only_files=only_files,
        archive_sha256=archive_sha256,
    )


def load_bytes(
    path: str | Path,
    data: bytes,
    claims: Iterable[Claim],
    *,
    only_files: Collection[str] | None = None,
    archive_sha256: str | None = None,
) -> ValueStore:
    """Read and revalidate a values file from bytes already opened by the caller."""

    return _revalidate_store(
        _store_from_bytes(data, Path(path)),
        claims,
        only_files=only_files,
        archive_sha256=archive_sha256,
    )


def _revalidate_store(
    store: ValueStore,
    claims: Iterable[Claim],
    *,
    only_files: Collection[str] | None,
    archive_sha256: str | None,
) -> ValueStore:
    by_id = {claim.id: claim for claim in claims}
    for entry in tuple(store.entries):
        if entry.status == "STALE":
            continue
        if only_files is not None and entry.file not in only_files:
            continue
        claim = by_id.get(entry.claim_id)
        if claim is None:
            store = _transition(store, "stale", entry, replace(entry, status="STALE"))
            continue
        expected_hash = claim_source_sha256(claim, key=entry.key, kind=entry.kind)
        if expected_hash == entry.source_sha256:
            continue
        legacy_hash = sha256_text(claim.text).removeprefix("sha256:")
        if (
            archive_sha256 is not None
            and archive_sha256 == store.archive_sha256
            and entry.source_sha256 == legacy_hash
        ):
            store = _transition(
                store,
                "source_rebind",
                entry,
                replace(entry, source_sha256=expected_hash),
            )
        else:
            store = _transition(store, "stale", entry, replace(entry, status="STALE"))
    return store


def checkable(store: ValueStore) -> tuple[CanonValue, ...]:
    return tuple(
        CanonValue(
            key=entry.key,
            kind=entry.kind,
            text=entry.text,
            claim_id=entry.claim_id,
            status="CONFIRMED",
            pattern=entry.pattern,
            terms=entry.terms,
            file=entry.file,
            line=entry.line,
            opposite_terms=entry.opposite_terms,
        )
        for entry in store.entries
        if entry.status == "CONFIRMED"
    )


__all__ = [
    "ValueEntry",
    "ValueStore",
    "checkable",
    "claim_source_sha256",
    "confirm",
    "load",
    "load_bytes",
    "new_store",
    "propose",
    "reclassify",
    "revise",
    "save",
    "source_current",
]
