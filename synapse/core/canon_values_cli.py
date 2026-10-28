"""The command line for canon values, and the only place a value is confirmed (DEC-0205).

An AI may propose values (``canon_proposal``, over the local MCP) but never confirm one. The confirming half is a
command a person runs in their own terminal. It shows the value and the words it came from, and asks the person to
type, themselves, one line of their own. Only then does a value become ``CONFIRMED``.

* ``confirm``: a ``PROPOSED`` value becomes canon, with the person's words.
* ``revise``: the person gives a value new words; it is canon again at once, and the old words stay in the history.
* ``list``, ``closure``, ``check``, ``review``: read only, from ``canon_values_read``; they need no terminal.

What this does and does not prove. It refuses to run unless standard input and the screen are a terminal, so a
pipe or a captured shell cannot answer for the person, and it asks for words that are typed, not passed as an
argument. That stops the common slip (an assistant quoting a "yes" it never heard) and nothing more. It does **not**
prove a person is there, and not **who**: a program that opens a pseudo-terminal passes the same check, and some
assistant tools hand out exactly that. It is not authentication, and nothing downstream may treat it as one.

The files the values were cut from are read from a folder the person names, and only inside it: the names in a
values file were typed by whoever proposed the values and are not trusted to point anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from synapse.core import canon_values as values
from synapse.core.canon_claims import Claim
from synapse.core.canon_values_read import (
    CANNOT_RUN,
    DONE,
    NOT_DONE,
    CliError,
    NotDone,
    load_against,
    run_check,
    run_closure,
    run_context,
    run_list,
    run_review,
)

MIN_WORDS = 3
MAX_WORDS = 2000
MAX_TERMS = 20
MAX_TERM_CHARS = 40


def at_a_terminal() -> bool:
    """Standard input and the screen messages go to are both a terminal; a pipe is not a person."""

    try:
        return bool(sys.stdin.isatty() and sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _terms(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    terms = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if len(terms) > MAX_TERMS or any(len(term) > MAX_TERM_CHARS for term in terms):
        raise CliError(f"단어 목록은 {MAX_TERMS}개까지, 한 단어는 {MAX_TERM_CHARS}자까지입니다.")
    return terms


def _show(
    entry: values.ValueEntry, claim: Claim, *, heading: str, out: Callable[[str], None]
) -> None:
    out(heading)
    out(f"  키        : {entry.key}")
    out(f"  값의 말   : {entry.text}")
    out(f"  종류      : {entry.kind}")
    out(
        f"  원문 위치 : {entry.file}:{entry.line}"
        + (f"  ({claim.section})" if claim.section else "")
    )
    out(f"  원문 전체 : {claim.text}")
    if entry.status == "CONFIRMED":
        out(f"  지금 확정된 말: {entry.words}  ({entry.confirmed_at})")
    if entry.terms:
        out(f"  단어 목록 : {', '.join(entry.terms)}")
    out("  이 값은 AI가 제안한 것일 수 있습니다. 확정하면 정본이 되고, 이후 글 검사에 쓰입니다.")


def _typed(ask: Callable[[], str], out: Callable[[str], None], prompt: str) -> str:
    out(prompt)
    try:
        line = ask()
    except EOFError:
        return ""
    return line.strip()


def _words(ask: Callable[[], str], out: Callable[[str], None], prompt: str) -> str:
    words = _typed(ask, out, prompt)
    if len(words) < MIN_WORDS:
        raise NotDone(f"본인 말이 {MIN_WORDS}자 미만이라 취소했습니다. 아무것도 바뀌지 않았습니다.")
    if len(words) > MAX_WORDS or any(ord(ch) < 32 for ch in words):
        raise NotDone(
            "본인 말이 너무 길거나 제어 문자가 있어 취소했습니다. 아무것도 바뀌지 않았습니다."
        )
    return words


def _locate(
    action: str,
    values_file: Path,
    folder: Path | None,
    key: str,
) -> tuple[values.ValueStore, values.ValueEntry, Claim]:
    if folder is None:
        raise CliError(
            f"{action}에는 --files <원문 폴더>가 필요합니다: 원문이 아직 같은지 보고, 사람에게 보여 주려고요."
        )
    store, claims, checked = load_against(values_file, folder)
    entry = next((item for item in store.entries if item.key == key), None)
    if entry is None:
        raise NotDone(f"그 키의 값이 없습니다: {key}")
    if entry.file not in checked:
        raise NotDone(f"원문 파일을 --files 폴더에서 찾지 못했습니다: {entry.file}")
    if entry.status == "STALE":
        raise NotDone(
            "원문이 바뀌어 이 값은 낡았습니다. 낡은 값은 확정도 수정도 하지 않고, 새로 제안받으세요."
        )
    return store, entry, claims[entry.claim_id]


def run_confirm(
    values_file: Path,
    folder: Path | None,
    key: str,
    *,
    terms: str | None,
    ask: Callable[[], str],
    out: Callable[[str], None],
    now: Callable[[], str],
) -> dict[str, Any]:
    store, entry, claim = _locate("확정", values_file, folder, key)
    if entry.status != "PROPOSED":
        raise NotDone("이미 확정된 값입니다. 말을 고치려면 revise를 쓰세요.")
    chosen = _terms(terms) or ()
    _show(replace(entry, terms=chosen), claim, heading="값을 정본으로 확정합니다.", out=out)
    words = _words(ask, out, f"맞으면 본인 말로 한 줄 적으세요({MIN_WORDS}자 이상, 비우면 취소):")
    stamp = now()
    values.save(values.confirm(store, key, words=words, now=stamp, terms=chosen), values_file)
    return {"confirmed": key, "at": stamp, "values_file": str(values_file)}


def run_revise(
    values_file: Path,
    folder: Path | None,
    key: str,
    *,
    terms: str | None,
    ask: Callable[[], str],
    out: Callable[[str], None],
    now: Callable[[], str],
) -> dict[str, Any]:
    store, entry, claim = _locate("수정", values_file, folder, key)
    chosen = entry.terms if terms is None else (_terms(terms) or ())
    _show(replace(entry, terms=chosen), claim, heading="값의 말을 고쳐 다시 확정합니다.", out=out)
    text = _typed(ask, out, "새 값의 말을 적으세요(비우면 취소):")
    if not text or len(text) > MAX_WORDS or any(ord(ch) < 32 for ch in text):
        raise NotDone("새 값의 말이 비었거나 맞지 않아 취소했습니다. 아무것도 바뀌지 않았습니다.")
    words = _words(
        ask, out, f"고치는 까닭을 본인 말로 한 줄 적으세요({MIN_WORDS}자 이상, 비우면 취소):"
    )
    stamp = now()
    revised = values.revise(
        store,
        key,
        text=text,
        words=words,
        now=stamp,
        pattern=entry.pattern,
        terms=chosen,
        opposite_terms=entry.opposite_terms,
    )
    values.save(revised, values_file)
    return {"revised": key, "at": stamp, "values_file": str(values_file)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m synapse.core.canon_values_cli",
        description=(
            "Canon values on the command line. list / closure / check / review only read. confirm and "
            "revise change a value and need you at a terminal, with your own words: an assistant cannot."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    folder_help = "folder holding the files the values were cut from"

    list_parser = commands.add_parser("list", help="show every value and its state (read only)")
    list_parser.add_argument("values_file", type=Path)
    list_parser.add_argument("--files", type=Path, help=folder_help)

    closure_parser = commands.add_parser(
        "closure", help="which confirmed values are settled and what the others wait on (read only)"
    )
    closure_parser.add_argument("values_file", type=Path)
    closure_parser.add_argument("--files", type=Path, required=True, help=folder_help)
    closure_parser.add_argument(
        "--links", type=Path, help="JSON list of {relation, from_key, to_key, ref?} you declare"
    )

    context_parser = commands.add_parser(
        "context", help="the block of confirmed canon to write from, with a hash (read only)"
    )
    context_parser.add_argument("values_file", type=Path)
    context_parser.add_argument("--files", type=Path, required=True, help=folder_help)
    context_parser.add_argument(
        "--subjects", help="comma-separated character names the writing is about"
    )
    context_parser.add_argument(
        "--rows", help="comma-separated time-axis rows the writing is about"
    )
    context_parser.add_argument(
        "--max-chars", type=int, default=4000, help="size limit of the block"
    )

    check_parser = commands.add_parser(
        "check", help="check a draft against every confirmed value and get a receipt (read only)"
    )
    check_parser.add_argument("values_file", type=Path)
    check_parser.add_argument("--files", type=Path, required=True, help=folder_help)
    check_parser.add_argument("--text-file", type=Path, required=True, help="the draft to check")
    check_parser.add_argument("--run-id", required=True, help="your name for this check")
    check_parser.add_argument("--scope-id", required=True, help="your name for the thing checked")
    check_parser.add_argument(
        "--receipt-out", type=Path, help="write the receipt to this new file; Synapse keeps none"
    )
    check_parser.add_argument(
        "--context",
        type=Path,
        help="the file `context` wrote for the block the draft was written from",
    )

    review_parser = commands.add_parser(
        "review", help="whether an earlier receipt still describes the present (read only)"
    )
    review_parser.add_argument("values_file", type=Path)
    review_parser.add_argument("--files", type=Path, required=True, help=folder_help)
    review_parser.add_argument(
        "--text-file", type=Path, required=True, help="the draft as it is now"
    )
    review_parser.add_argument(
        "--receipt", type=Path, required=True, help="the receipt file `check` wrote"
    )
    review_parser.add_argument(
        "--context", type=Path, help="the file `context` wrote, to see if the block moved"
    )

    for name, text in (
        ("confirm", "make a proposed value canon"),
        ("revise", "give a value new words"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("values_file", type=Path)
        sub.add_argument("key")
        sub.add_argument("--files", type=Path, required=True, help=folder_help)
        sub.add_argument(
            "--terms", help="comma-separated words that point at this value in a draft"
        )
    return parser


def _read_only(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command == "list":
        return run_list(args.values_file, args.files)
    if args.command == "closure":
        return run_closure(args.values_file, args.files, args.links)
    if args.command == "context":
        return run_context(
            args.values_file,
            args.files,
            subjects=args.subjects,
            rows=args.rows,
            max_chars=args.max_chars,
        )
    if args.command == "check":
        return run_check(
            args.values_file,
            args.files,
            args.text_file,
            run_id=args.run_id,
            scope_id=args.scope_id,
            receipt_out=args.receipt_out,
            context_file=args.context,
        )
    return run_review(args.values_file, args.files, args.text_file, args.receipt, args.context)


def main(
    argv: list[str] | None = None,
    *,
    ask: Callable[[], str] | None = None,
    interactive: Callable[[], bool] = at_a_terminal,
    now: Callable[[], str] = _now,
) -> int:
    args = _parser().parse_args(argv)

    def out(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    code = DONE
    try:
        if args.command in {"confirm", "revise"}:
            if not interactive():
                raise CliError(
                    "확정과 수정은 사람이 터미널에서 직접 해야 합니다. 이 실행은 터미널이 아니어서 거절했습니다"
                    "(파이프나 캡처된 셸은 사람이 아닙니다)."
                )
            runner = run_confirm if args.command == "confirm" else run_revise
            output = runner(
                args.values_file,
                args.files,
                args.key,
                terms=args.terms,
                ask=ask or input,
                out=out,
                now=now,
            )
        else:
            output, code = _read_only(args)
    except NotDone as exc:
        print(f"canon-values: {exc}", file=sys.stderr, flush=True)
        return NOT_DONE
    except (OSError, ValueError) as exc:
        print(f"canon-values: {exc}", file=sys.stderr, flush=True)
        return CANNOT_RUN
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)
    return code


__all__ = [
    "CANNOT_RUN",
    "DONE",
    "NOT_DONE",
    "CliError",
    "NotDone",
    "at_a_terminal",
    "main",
    "run_confirm",
    "run_revise",
]

if __name__ == "__main__":
    for _stream in (
        sys.stdout,
        sys.stderr,
    ):  # JSON with Korean words: UTF-8 even when piped on a cp949 Windows
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
