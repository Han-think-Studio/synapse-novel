"""Read a values file against the files it was cut from, on the command line: list, closure, check, review.

The read-only half of the ``canon values`` command (the other half, ``canon_values_cli``, is where a person
confirms). Everything here is also available over the novel MCP; this is the same code reached from a terminal, so
that a person without an assistant gets the same answers.

* ``list``: every value and its state.
* ``closure``: which confirmed values are settled and what the others wait on (``canon_closure``).
* ``check``: a draft against every confirmed value, with a receipt that binds the answer to the values, the rules
  and the words (``canon_receipt``). The receipt can be written to a file the person names; Synapse keeps nothing.
* ``review``: whether an earlier receipt still describes the present, and if not, what moved.

Nothing here writes a values file, confirms anything or calls a model. The files the values were cut from are read
from a folder the person names, and only inside it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from synapse.core import (
    canon_check_entry,
    canon_closure,
    canon_context,
    canon_evidence,
    canon_receipt,
)
from synapse.core import canon_values as values
from synapse.core.canon_claims import Claim, cut_text

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FOLDER_FILES = 500
TEXT_SUFFIXES = (".md", ".markdown", ".txt")
MAX_DRAFT_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 8 * 1024 * 1024

DONE, NOT_DONE, CANNOT_RUN = 0, 1, 2


class CliError(ValueError):
    """The input cannot be used; the message says what to change."""


class NotDone(ValueError):
    """The request was understood and not carried out: the value's state, or the person, said no."""


def read_sources(folder: Path, names: Collection[str]) -> tuple[dict[str, str], dict[str, str]]:
    """The named files from inside ``folder`` and the sha256 of the bytes read; others are left out."""

    root = folder.resolve()
    found: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for name in sorted(names):
        if (
            not name.strip() or "\x00" in name or ":" in name
        ):  # a ':' is a drive or a stream, never a name here
            continue
        candidate = (root / name).resolve()
        if candidate != root and root not in candidate.parents:
            continue
        try:
            if not candidate.is_file():
                continue
            with candidate.open("rb") as handle:
                data = handle.read(
                    MAX_FILE_BYTES + 1
                )  # a file that grew after it was looked at is still bounded
            if len(data) > MAX_FILE_BYTES:
                continue
            found[name] = data.decode("utf-8-sig")
            hashes[name] = f"sha256:{hashlib.sha256(data).hexdigest()}"
        except (OSError, UnicodeError):
            continue
    return found, hashes


def folder_texts(folder: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Every text file under ``folder`` (by its path from it) and the sha256 of the bytes read.

    A link is not followed, a file over the size limit or not UTF-8 is left out, and at most ``MAX_FOLDER_FILES``
    are taken, in path order.
    """

    root = folder.resolve()
    names: list[str] = []
    for candidate in sorted(root.rglob("*")):
        if len(names) >= MAX_FOLDER_FILES:
            break
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.suffix.lower() not in TEXT_SUFFIXES:
            continue
        names.append(candidate.relative_to(root).as_posix())
    return read_sources(root, names)


def read_files(folder: Path, names: Collection[str]) -> dict[str, str]:
    """The named files from inside ``folder``; a name that points outside it, or at no file, is left out."""

    return read_sources(folder, names)[0]


def load_against(
    values_file: Path, folder: Path | None, hashes: dict[str, str] | None = None
) -> tuple[values.ValueStore, dict[str, Claim], set[str]]:
    """The values, revalidated against the files that could be read, and those files' claims by id.

    ``hashes``, if given, is filled with the sha256 of the bytes of every file read.
    """

    data = _read_bytes(values_file, values.MAX_BYTES, "값", hashes, "values")
    stored = values.load_bytes(values_file, data, (), only_files=())
    if folder is None:
        return stored, {}, set()
    texts, read_hashes = read_sources(folder, {entry.file for entry in stored.entries})
    if hashes is not None:
        hashes.update(read_hashes)
    claims = {claim.id: claim for name, text in texts.items() for claim in cut_text(name, text)}
    return values.load_bytes(values_file, data, claims.values(), only_files=set(texts)), claims, set(texts)


def _read_bytes(
    path: Path,
    limit: int,
    what: str,
    hashes: dict[str, str] | None = None,
    role: str | None = None,
) -> bytes:
    try:
        if not path.is_file():
            raise CliError(f"{what} 파일이 없습니다: {path}")
        if path.stat().st_size > limit:
            raise CliError(f"{what} 파일이 너무 큽니다: {path}")
        data = path.read_bytes()
        if len(data) > limit:
            raise CliError(f"{what} 파일이 너무 큽니다: {path}")
    except OSError as exc:
        raise CliError(f"{what} 파일을 읽을 수 없습니다: {path}") from exc
    if hashes is not None and role is not None:
        hashes[f"{role}:{path.name}"] = f"sha256:{hashlib.sha256(data).hexdigest()}"
    return data


def _read_json(
    path: Path,
    what: str,
    hashes: dict[str, str] | None = None,
    role: str | None = None,
) -> Any:
    try:
        return json.loads(
            _read_bytes(path, MAX_RECORD_BYTES, what, hashes, role).decode("utf-8-sig")
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CliError(f"{what} 파일이 JSON이 아닙니다: {path}") from exc


def _read_draft(path: Path, hashes: dict[str, str] | None = None) -> str:
    try:
        return _read_bytes(path, MAX_DRAFT_BYTES, "검사할 글", hashes, "draft").decode(
            "utf-8-sig"
        )
    except UnicodeError as exc:
        raise CliError(f"검사할 글이 UTF-8이 아닙니다: {path}") from exc


def _context_object(path: Path, hashes: dict[str, str] | None = None) -> dict[str, Any]:
    """The `context` object of a file `context` wrote (the whole output, or just that object)."""

    document = _read_json(path, "맥락", hashes, "context")
    if (
        isinstance(document, dict)
        and "block_hash" not in document
        and isinstance(document.get("context"), dict)
    ):
        document = document["context"]
    if not isinstance(document, dict):
        raise CliError(f"맥락 파일은 JSON 객체여야 합니다: {path}")
    return document


def _produced_with(
    store: values.ValueStore,
    context_file: Path | None,
    hashes: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if context_file is None:
        return None
    try:
        return canon_context.produced_with(
            values.checkable(store), _context_object(context_file, hashes)
        )
    except canon_context.CanonContextError as exc:
        raise CliError(str(exc)) from exc


def run_context(
    values_file: Path,
    folder: Path,
    *,
    subjects: str | None,
    rows: str | None,
    max_chars: int,
) -> tuple[dict[str, Any], int]:
    """The block of confirmed canon to write from, with the hash a later check is bound to."""

    hashes: dict[str, str] = {}
    store, _, _ = load_against(values_file, folder, hashes)
    try:
        names, row_names, limit = canon_context.request_from(
            {
                "subjects": [s for s in (subjects or "").split(",") if s.strip()],
                "rows": [s for s in (rows or "").split(",") if s.strip()],
                "max_chars": max_chars,
            }
        )
    except canon_context.CanonContextError as exc:
        raise CliError(str(exc)) from exc
    context = canon_context.build_canon_context(
        values.checkable(store), subjects=names, rows=row_names, max_chars=limit
    )
    return {
        "block": canon_context.to_prompt_block(context),
        "context": {
            **canon_context.context_record(context),
            "subjects": list(names),
            "rows": list(row_names),
            "max_chars": limit,
        },
        "evidence": _evidence(store, hashes),
        "note": (
            "`block`을 쓰는 글의 프롬프트에 넣으세요. `context` 객체를 파일로 저장해 `check --context`에 주면 영수증이 "
            "그 글이 검사와 같은 정본에서 쓰였는지 기록합니다. 확정된 값만 들어 있고 금지·요구는 항상, 나머지는 "
            "subjects·rows에 이름이 있을 때만 들어갑니다."
        ),
    }, DONE


def _evidence(store: values.ValueStore, hashes: dict[str, str]) -> dict[str, Any]:
    """Bytes this process read, and the source files of the values that it could not."""

    return canon_evidence.read(hashes, not_read={e.file for e in store.entries} - set(hashes))


def record_of(entry: values.ValueEntry) -> dict[str, Any]:
    return {
        "key": entry.key,
        "status": entry.status,
        "kind": entry.kind,
        "text": entry.text,
        "file": entry.file,
        "line": entry.line,
        "confirmed_at": entry.confirmed_at,
        "words": entry.words,
        "terms": list(entry.terms),
        "has_pattern": entry.pattern is not None,
    }


def run_list(values_file: Path, folder: Path | None) -> tuple[dict[str, Any], int]:
    hashes: dict[str, str] = {}
    store, _, checked = load_against(values_file, folder, hashes)
    counts = {"PROPOSED": 0, "CONFIRMED": 0, "STALE": 0}
    for entry in store.entries:
        counts[entry.status] += 1
    return {
        "values_file": str(values_file),
        "project_id": store.project_id,
        "counts": counts,
        "checked_against": sorted(checked) if folder is not None else None,
        "evidence": _evidence(store, hashes),
        "values": [record_of(entry) for entry in store.entries],
    }, DONE


def run_closure(
    values_file: Path, folder: Path, links_file: Path | None
) -> tuple[dict[str, Any], int]:
    hashes: dict[str, str] = {}
    store, claims, checked = load_against(values_file, folder, hashes)
    try:
        links = canon_closure.links_from_records(
            None if links_file is None else _read_json(links_file, "연결", hashes, "links")
        )
        review = canon_closure.review_value_closure(
            store, claims.values(), examined_files=checked, links=links
        )
    except canon_closure.ClosureError as exc:
        raise CliError(str(exc)) from exc
    return {
        "closure": review.to_record(),
        "evidence": _evidence(store, hashes),
        "note": (
            "지금 값 파일과 --files 폴더로 계산한 것이고 아무것도 저장하지 않습니다. 폴더에서 찾지 못한 파일의 "
            "값은 기다립니다. 닫힘은 승인이 아닙니다."
        ),
    }, DONE if review.all_closed else NOT_DONE


def run_check(
    values_file: Path,
    folder: Path,
    draft_file: Path,
    *,
    run_id: str,
    scope_id: str,
    receipt_out: Path | None,
    context_file: Path | None = None,
) -> tuple[dict[str, Any], int]:
    if receipt_out is not None and (receipt_out.exists() or not receipt_out.parent.is_dir()):
        raise CliError(f"영수증을 쓸 곳이 맞지 않습니다(이미 있거나 폴더가 없음): {receipt_out}")
    hashes: dict[str, str] = {}
    store, _, checked = load_against(values_file, folder, hashes)
    text = _read_draft(draft_file, hashes)
    produced = _produced_with(store, context_file, hashes)
    try:
        made = canon_check_entry.check(
            text,
            store,
            run_id=run_id,
            scope_id=scope_id,
            examined_files=checked,
            produced_with=produced,
        )
    except canon_receipt.ReceiptError as exc:
        raise NotDone(str(exc)) from exc
    if receipt_out is not None:
        try:
            with receipt_out.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(made["receipt"], ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            raise CliError(f"영수증을 쓸 수 없습니다: {receipt_out}") from exc
    return {
        "passed": made["passed"],
        "evidence": _evidence(store, hashes),
        "complete": made["complete"],
        "facts_match": made["facts_match"],
        "produced_with": made["produced_with"],
        "retry": made["retry"],
        "not_examined": made["not_examined"],
        "unchecked": made["unchecked"],
        "conflicts": made["conflicts"],
        "possible": made["possible"],
        "receipt": made["receipt"],
        "receipt_file": None if receipt_out is None else str(receipt_out),
        "left_out": made["left_out"],
        "note": (
            "`retry`는 거부된 글을 되풀이하지 않고 무엇을 고칠지 알려 줍니다. `facts_match`는 --context로 알린 글이 "
            "지금 검사와 같은 정본에서 쓰였는지, `produced_with.not_shown`은 쓰는 쪽이 못 본 확정 값입니다. "
            "확정된 값만 검사했고 `left_out`은 검사하지 않았습니다. `not_examined`는 확정된 값이지만 원문 파일을 "
            "이번에 열어 보지 못한 것이라 저장된 말로만 검사했고, 하나라도 있으면 `complete`가 false이며 통과로 "
            "믿지 마세요. 영수증은 시냅스가 보관하지 않으니 직접 "
            "보관하세요. 기록일 뿐 승인도 글의 질에 대한 판단도 아닙니다."
        ),
    }, DONE if made["passed"] and made["complete"] and made["facts_match"] is not False else NOT_DONE


def run_review(
    values_file: Path,
    folder: Path,
    draft_file: Path,
    receipt_file: Path,
    context_file: Path | None = None,
) -> tuple[dict[str, Any], int]:
    hashes: dict[str, str] = {}
    receipt = _read_json(receipt_file, "영수증", hashes, "receipt")
    if not isinstance(receipt, dict):
        raise CliError(f"영수증 파일은 JSON 객체여야 합니다: {receipt_file}")
    store, _, checked = load_against(values_file, folder, hashes)
    produced = _produced_with(store, context_file, hashes)
    review = canon_receipt.review_output_receipt(
        receipt,
        store,
        _read_draft(draft_file, hashes),
        examined_files=checked,
        context=None if produced is None else produced["current"],
    )
    return {
        "review": review.to_record(),
        "evidence": _evidence(store, hashes),
        "note": (
            "READY_FOR_HUMAN_REVIEW와 PASS는 같은 값·규칙·글이 여전히 같은 답을 낸다는 뜻입니다. 승인이 아니고, "
            "영수증은 누가 검사를 돌렸는지 증명하지 않습니다."
        ),
    }, DONE if review.current else NOT_DONE


__all__ = [
    "CANNOT_RUN",
    "DONE",
    "NOT_DONE",
    "CliError",
    "NotDone",
    "folder_texts",
    "load_against",
    "read_files",
    "read_sources",
    "record_of",
    "run_check",
    "run_closure",
    "run_context",
    "run_list",
    "run_review",
]
