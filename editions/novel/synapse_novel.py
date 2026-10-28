"""A thin local launcher for the canonical Synapse core.

This folder is a convenience surface, not a second Synapse implementation.
It imports the planner and substantive-content verifier from the canonical
repository and never calls a model or mutates Canonical State.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

SOURCE_ROOT_ENV = "SYNAPSE_SOURCE_ROOT"
CANONICAL_ASSESSMENT_SOURCE = "canonical_substantive_content_checker"
REPRODUCIBILITY_SCHEMA_VERSION = "synapse.mini.reproducibility.v1"
COMPARISON_SCHEMA_VERSION = "synapse.mini.comparison.v1"
COMPARISON_DIMENSIONS = (
    "idea",
    "preset",
    "file_content",
    "checker_rules",
    "reference_relations",
)


def _source_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get(SOURCE_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    script = Path(__file__).resolve()
    # Support the in-repository edition layout and a sibling canonical checkout.
    candidates = (script.parents[2], script.parents[2].parent / "Synapse")
    for candidate in candidates:
        if (candidate / "synapse").is_dir():
            return candidate.resolve()
    return candidates[0].resolve()


def _describe_source(root: Path) -> str:
    """Name the core without disclosing where this copy happens to sit."""

    return "bundled" if (Path(__file__).resolve().parents[2] == root) else str(root)


def _core(source_root: str | None):
    root = _source_root(source_root)
    if not (root / "synapse").is_dir():
        raise FileNotFoundError(
            "canonical Synapse source was not found; pass --source-root or set "
            + SOURCE_ROOT_ENV
        )
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from synapse.core.scaffold import ScaffoldError, build_scaffold_plan
    from synapse.core.workspace import (
        explain_substantive_workspace_content,
        has_substantive_workspace_content,
    )

    return (
        root,
        ScaffoldError,
        build_scaffold_plan,
        has_substantive_workspace_content,
        explain_substantive_workspace_content,
    )


def _idea_path_candidate(value: str) -> Path | None:
    raw = value.strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return candidate
    if (
        candidate.suffix.lower() in {".md", ".markdown", ".rst", ".txt", ".text"}
        or "/" in raw
        or "\\" in raw
        or (len(raw) >= 2 and raw[1] == ":")
    ):
        return candidate
    return None


def _read_idea(value: str) -> str:
    candidate = _idea_path_candidate(value)
    if candidate is None:
        return value
    try:
        # utf-8-sig, not utf-8: a file saved by Notepad begins with a byte order
        # mark, and reading it as plain utf-8 leaves that mark inside the first
        # line, where it becomes the first character of the project name.
        text = candidate.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ValueError(
            f"Idea file was not found: {candidate}. Check the path and re-run."
        ) from exc
    except IsADirectoryError as exc:
        raise ValueError(f"Idea path is not a file: {candidate}.") from exc
    except PermissionError as exc:
        raise ValueError(
            f"Idea file could not be read because permission was denied: {candidate}."
        ) from exc
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Idea file is not UTF-8 text: {candidate}. Save it again as UTF-8 and re-run."
        ) from exc
    except OSError as exc:
        raise ValueError(f"Idea file could not be read: {candidate}.") from exc
    if not text.strip():
        raise ValueError(
            f"Idea file is empty: {candidate}. Add idea text and re-run."
        )
    return text


def _display_path(value: str | Path) -> str:
    path = Path(value).expanduser()
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _print_preflight(args: argparse.Namespace) -> None:
    idea_source = _idea_path_candidate(args.idea)
    if idea_source is None:
        idea_label = "direct text"
    else:
        idea_label = "file: " + _display_path(idea_source)
    output_label = _display_path(args.out) if args.out else "stdout"
    output_policy = (
        "overwrite enabled"
        if args.out and args.overwrite
        else "refuse existing file unless --overwrite"
        if args.out
        else "no file write"
    )
    print("[synapse-novel] preflight", file=sys.stderr)
    print(f"[synapse-novel] input: {idea_label}", file=sys.stderr)
    print(f"[synapse-novel] preset: {args.preset}", file=sys.stderr)
    if args.command == "check":
        print(
            f"[synapse-novel] target workspace: {_display_path(args.workspace)}",
            file=sys.stderr,
        )
    elif args.command == "demo":
        print(
            f"[synapse-novel] target proposal: {_display_path(args.content_json)}",
            file=sys.stderr,
        )
    else:
        print("[synapse-novel] target: plan only; no workspace write", file=sys.stderr)
    print(f"[synapse-novel] output: {output_label}", file=sys.stderr)
    print(f"[synapse-novel] output policy: {output_policy}", file=sys.stderr)
    print(
        f"[synapse-novel] relative paths base: {_display_path(Path.cwd())}",
        file=sys.stderr,
    )


def _build_plan(args: argparse.Namespace):
    root, scaffold_error, build_scaffold_plan, _, _ = _core(args.source_root)
    idea = _read_idea(args.idea)
    plan = build_scaffold_plan(
        idea,
        preset_id=args.preset,
        project_name=args.project_name,
    )
    return root, plan, scaffold_error


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_named_inputs(inputs: dict[str, bytes | None]) -> str:
    """Hash an ordered name/bytes manifest without storing input contents."""

    digest = hashlib.sha256()
    for name in sorted(inputs):
        name_bytes = name.encode("utf-8")
        digest.update(len(name_bytes).to_bytes(4, "big"))
        digest.update(name_bytes)
        value = inputs[name]
        if value is None:
            digest.update(b"MISSING")
            continue
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _json_digest(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(rendered)


def _source_scope(root: Path) -> str:
    bundled_root = Path(__file__).resolve().parents[2]
    try:
        return "bundled" if root.resolve() == bundled_root.resolve() else "external"
    except OSError:
        return "external"


def _reproducibility(
    root: Path,
    plan: Any,
    *,
    assessment_input_sha256: str | None = None,
    assessment_input_kind: str | None = None,
) -> dict[str, Any]:
    from synapse.core.scaffold import SCAFFOLD_PLANNER_VERSION
    from synapse.core.workspace import SUBSTANTIVE_CONTENT_CHECKER_VERSION

    idea_bytes = plan.idea.encode("utf-8")
    record: dict[str, Any] = {
        "schema_version": REPRODUCIBILITY_SCHEMA_VERSION,
        "idea": {
            "sha256": _sha256_bytes(idea_bytes),
            "utf8_bytes": len(idea_bytes),
        },
        "preset_id": plan.preset.id,
        "plan_id": plan.id,
        "planner": {
            "id": "synapse.core.scaffold.build_scaffold_plan",
            "version": int(plan.metadata.get("version", SCAFFOLD_PLANNER_VERSION)),
        },
        "checker": {
            "id": "synapse.core.workspace.explain_substantive_workspace_content",
            "version": SUBSTANTIVE_CONTENT_CHECKER_VERSION,
        },
        "source_scope": _source_scope(root),
    }
    if assessment_input_sha256 is not None or assessment_input_kind is not None:
        if not assessment_input_sha256 or not assessment_input_kind:
            raise ValueError("assessment input digest and kind must be provided together")
        record["assessment_input"] = {
            "kind": assessment_input_kind,
            "sha256": assessment_input_sha256,
        }
    preset_basis = {
        "preset": plan.preset.to_record(),
        "files": [item.to_record() for item in plan.files],
    }
    reference_basis = {
        "files": [
            {
                "path": item.path,
                "required": item.required,
                "purpose": item.purpose,
            }
            for item in plan.files
        ],
        "steps": [step.to_record() for step in plan.steps],
    }
    record["comparison"] = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "dimensions": {
            "idea": {
                "state": "OBSERVED",
                "sha256": record["idea"]["sha256"],
            },
            "preset": {
                "state": "OBSERVED",
                "id": plan.preset.id,
                "sha256": _json_digest(preset_basis),
            },
            "file_content": (
                {
                    "state": "OBSERVED",
                    "kind": record["assessment_input"]["kind"],
                    "sha256": record["assessment_input"]["sha256"],
                }
                if "assessment_input" in record
                else {"state": "UNASSESSED"}
            ),
            "checker_rules": {
                "state": "OBSERVED",
                "id": record["checker"]["id"],
                "version": record["checker"]["version"],
            },
            "reference_relations": {
                "state": "OBSERVED",
                "sha256": _json_digest(reference_basis),
            },
        },
    }
    return record


def compare_comparison_records(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Compare two additive comparison records without deciding their meaning."""

    if not isinstance(before, dict) or not isinstance(after, dict):
        raise TypeError("comparison records must be objects")
    if (
        before.get("schema_version") != COMPARISON_SCHEMA_VERSION
        or after.get("schema_version") != COMPARISON_SCHEMA_VERSION
    ):
        return {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "status": "UNRESOLVED",
            "changed_dimensions": [],
            "unchanged_dimensions": [],
            "unresolved_dimensions": list(COMPARISON_DIMENSIONS),
            "reason": "comparison_schema_unsupported",
        }
    before_dimensions = before.get("dimensions")
    after_dimensions = after.get("dimensions")
    if not isinstance(before_dimensions, dict) or not isinstance(after_dimensions, dict):
        raise TypeError("comparison records must contain dimensions")
    changed: list[str] = []
    unchanged: list[str] = []
    unresolved: list[str] = []
    for dimension in COMPARISON_DIMENSIONS:
        left = before_dimensions.get(dimension)
        right = after_dimensions.get(dimension)
        if (
            not isinstance(left, dict)
            or not isinstance(right, dict)
            or left.get("state") != "OBSERVED"
            or right.get("state") != "OBSERVED"
        ):
            unresolved.append(dimension)
        elif left != right:
            changed.append(dimension)
        else:
            unchanged.append(dimension)
    status = "UNRESOLVED" if unresolved else "CHANGED" if changed else "UNCHANGED"
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "status": status,
        "changed_dimensions": changed,
        "unchanged_dimensions": unchanged,
        "unresolved_dimensions": unresolved,
    }


def _plan_result(root: Path, plan: Any) -> dict[str, Any]:
    return {
        "schema_version": "synapse.mini.plan.v1",
        "status": "PLAN_ONLY",
        "result_kind": "scaffold_plan",
        "operation": {
            "kind": "PLAN_ONLY",
            "model_invoked": False,
            "workspace_write": False,
            "regeneration": False,
            "recheck": False,
        },
        "canonical_source": _describe_source(root),
        "files_total": len(plan.files),
        "workspace_created": False,
        "reproducibility": _reproducibility(root, plan),
        "next_actions": [
            "Review each listed file role before writing project content.",
            "Fill the files in a workspace, then run check against that workspace.",
        ],
        "plan": plan.to_record(),
    }


def _fix_example(item: Any, status: str) -> str:
    if status == "MISSING":
        return f"Create {item.path} and write authored text for: {item.purpose}"
    if status == "EMPTY":
        return f"Add authored text for: {item.purpose}"
    if status == "TITLE_ONLY":
        return f"Keep the headings in {item.path} and add authored body text under them"
    if status == "TEMPLATE":
        return "Replace copied scaffold guidance or placeholders with project-specific text"
    if status == "EMPTY_SECTION":
        return f"Add authored text under every Markdown leaf heading in {item.path}"
    if status == "INVALID_INPUT":
        return "Provide a UTF-8 text string for this file value"
    return "No structural fix; review domain meaning and style before proceeding"


def _evaluate(
    plan: Any,
    contents: dict[str, Any],
    verifier: Any,
    explainer: Any | None = None,
    input_errors: dict[str, str] | None = None,
    reproducibility: dict[str, Any] | None = None,
    operation_kind: str = "ASSESSMENT_ONLY",
) -> dict[str, Any]:
    if explainer is None:
        from synapse.core.workspace import explain_substantive_workspace_content

        explainer = explain_substantive_workspace_content
    input_errors = input_errors or {}
    if reproducibility is None:
        reproducibility = _reproducibility(Path(__file__).resolve().parents[2], plan)
    rows = []
    for item in plan.files:
        value = contents.get(item.path)
        if not isinstance(value, str):
            diagnosis = explainer(value, scaffold_purpose=item.purpose)
            status = (
                "INVALID_INPUT"
                if item.path in input_errors or item.path in contents
                else "MISSING"
            )
            reason = (
                "The required file is not present in the checked workspace."
                if status == "MISSING"
                else input_errors.get(item.path, str(diagnosis["reason"]))
            )
            rows.append(
                {
                    "path": item.path,
                    "title": item.title,
                    "purpose": item.purpose,
                    "required": item.required,
                    "assessment_source": CANONICAL_ASSESSMENT_SOURCE,
                    "check_criterion": diagnosis["criterion"],
                    "status": status,
                    "utf8_bytes": 0,
                    "reason": reason,
                    "fix_example": _fix_example(item, status),
                }
            )
            continue
        diagnosis = explainer(value, scaffold_purpose=item.purpose)
        passed = verifier(value, scaffold_purpose=item.purpose)
        status = str(diagnosis["status"])
        if passed and status != "PASS":
            raise ValueError("canonical verifier and explanation disagree")
        if not passed and status == "PASS":
            raise ValueError("canonical verifier and explanation disagree")
        rows.append(
            {
                "path": item.path,
                "title": item.title,
                "purpose": item.purpose,
                "required": item.required,
                "assessment_source": CANONICAL_ASSESSMENT_SOURCE,
                "check_criterion": diagnosis["criterion"],
                "status": status,
                "utf8_bytes": len(value.encode("utf-8")),
                "reason": diagnosis["reason"],
                "fix_example": _fix_example(item, status),
            }
        )
    required = [row for row in rows if row["required"]]
    passed = [row for row in required if row["status"] == "PASS"]
    status = "READY" if len(passed) == len(required) else "BLOCKED"
    if status == "READY":
        next_actions = [
            "Review each PASS file for domain meaning and style; READY is structural only.",
            "Run the same check again after any substantive edit.",
        ]
    else:
        next_actions = [
            (
                "Fill or repair each required file marked MISSING, EMPTY, TITLE_ONLY, "
                "TEMPLATE, EMPTY_SECTION, or INVALID_INPUT."
            ),
            "Run the same check again after the fixes.",
        ]
    return {
        "schema_version": "synapse.mini.content-check.v1",
        "result_kind": "content_check",
        "operation": {
            "kind": operation_kind,
            "model_invoked": False,
            "workspace_write": False,
            "regeneration": False,
            "recheck": operation_kind == "RECHECK_ONLY",
        },
        "assessment_source": CANONICAL_ASSESSMENT_SOURCE,
        "plan_id": plan.id,
        "project_name": plan.project_name,
        "required_files": len(required),
        "passed_files": len(passed),
        "files_total": len(rows),
        "workspace_created": False,
        "reproducibility": reproducibility,
        "next_actions": next_actions,
        "status": status,
        "files": rows,
    }


def _print_result_summary(record: dict[str, Any]) -> None:
    required_files = int(record["required_files"])
    passed_files = int(record["passed_files"])
    failed_files = sum(
        1
        for item in record["files"]
        if item.get("required") and item.get("status") != "PASS"
    )
    status = record["status"]
    print(f"[synapse-novel] verdict: {status}", file=sys.stderr)
    print(
        "[synapse-novel] scope: canonical structural content check of required files",
        file=sys.stderr,
    )
    print(
        f"[synapse-novel] counts: passed required files {passed_files}/{required_files}; "
        f"failed required files {failed_files}",
        file=sys.stderr,
    )
    if status == "READY":
        print(
            "[synapse-novel] limit: READY means the required files passed this structural "
            "check; it does not establish domain meaning, literary quality, or execution approval.",
            file=sys.stderr,
        )
    else:
        print(
            "[synapse-novel] limit: BLOCKED identifies structural failures; it does not "
            "judge domain meaning or literary quality.",
            file=sys.stderr,
        )


def _emit(
    record: dict[str, Any],
    output: str | None,
    *,
    overwrite: bool = False,
) -> int:
    rendered = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        destination = Path(output).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise ValueError(
                f"Output already exists: {destination}. Choose another path or pass --overwrite."
            )
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Output path is not a file: {destination}.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if record.get("status") in {"READY", "PLAN_ONLY", "PROPOSED"} else 1


def _canon(args: argparse.Namespace) -> int:
    """Hand the arguments to the canon command that was asked for, and keep its exit code.

    The same contract as the other commands: one JSON document on stdout, a problem with the
    input on stderr with exit code 2, and 0 or 1 for how the project stands.
    """

    _core(args.source_root)  # puts the canonical source on the path, or says it cannot
    if args.canon_command == "gate":
        from synapse.core.canon_gate import main as run
    elif args.canon_command == "values":
        from synapse.core.canon_values_cli import main as run
    else:
        from synapse.core.canon_apply import main as run
    return run(args.canon_args)


def _impact(args: argparse.Namespace) -> int:
    """Which files a change reaches. Reads; the same answer the MCP tool gives, with no assistant in between."""

    root, _, _, _, _ = _core(args.source_root)
    from synapse.core import impact_entry

    stored = impact_entry.stored_values(args.values.expanduser()) if args.values else []
    if args.archive:
        if args.idea or args.files:
            raise ValueError("give --archive (an existing project) or --idea with --preset, not both")
        result = impact_entry.for_archive(args.archive.expanduser(), args.changed, values=stored)
    else:
        if not args.idea or not args.preset:
            raise ValueError("give --idea and --preset (a new project), or --archive (an existing one)")
        texts, hashes = (None, None)
        if args.files:
            from synapse.core.canon_values_read import folder_texts

            texts, hashes = folder_texts(args.files.expanduser())
        result = impact_entry.for_idea(
            _read_idea(args.idea),
            args.preset,
            args.changed,
            project_name=args.project_name,
            files=texts,
            files_read=hashes,
            values=stored,
        )
    result["canonical_source"] = _describe_source(root)
    return _emit(result, args.out, overwrite=args.overwrite)


SETUP_COMMANDS = ("setup", "doctor", "uninstall")


def _utf8_streams() -> None:
    """Speak UTF-8 on stdout and stderr whatever the Windows code page is.

    The commands print JSON with the person's own words in it. Piped, a Korean Windows would otherwise write the
    ANSI code page (cp949), which a reader that expects UTF-8 -- an assistant, a script -- cannot read back.
    """

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> int:
    _utf8_streams()
    if len(sys.argv) > 1 and sys.argv[1] in SETUP_COMMANDS:
        # The registration commands have their own options and need none of the canonical source.
        if __package__:
            from . import synapse_novel_setup
        else:
            import synapse_novel_setup

        return synapse_novel_setup.main(sys.argv[1:])
    parser = argparse.ArgumentParser(
        description="Synapse Novel: canonical idea planning and read-only content checks"
    )
    parser.add_argument(
        "--source-root",
        help="path to the canonical Synapse repository (default: sibling Synapse)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="build a deterministic plan without writes")
    plan_parser.add_argument("idea", help="idea text or a UTF-8 text file")
    plan_parser.add_argument("--preset", required=True, help="one of the fixed presets (no default)")
    plan_parser.add_argument("--project-name", default="")
    plan_parser.add_argument("--out", default=None)
    plan_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing --out file",
    )

    check_parser = subparsers.add_parser(
        "check", help="read an existing workspace and check substantive required files"
    )
    check_parser.add_argument("workspace", type=Path)
    check_parser.add_argument("--idea", required=True, help="idea text or a UTF-8 text file")
    check_parser.add_argument("--preset", required=True, help="one of the fixed presets (no default)")
    check_parser.add_argument("--project-name", default="")
    check_parser.add_argument("--out", default=None)
    check_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing --out file",
    )

    demo_parser = subparsers.add_parser(
        "demo", help="check a JSON path-to-content proposal without writing it"
    )
    demo_parser.add_argument("--idea", required=True, help="idea text or a UTF-8 text file")
    demo_parser.add_argument("--content-json", required=True, type=Path)
    demo_parser.add_argument("--preset", required=True, help="one of the fixed presets (no default)")
    demo_parser.add_argument("--project-name", default="")
    demo_parser.add_argument("--out", default=None)
    demo_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing --out file",
    )

    impact_parser = subparsers.add_parser(
        "impact",
        help=(
            "list the files a change reaches (one step, or through others) and what to ask about each, so "
            "they are changed together; reads only"
        ),
    )
    impact_parser.add_argument(
        "--changed", nargs="+", required=True, metavar="PATH", help="the files that changed or will change"
    )
    impact_parser.add_argument("--idea", default=None, help="a new project: idea text or a UTF-8 text file")
    impact_parser.add_argument("--preset", default=None, help="a new project: one of the fixed presets")
    impact_parser.add_argument("--project-name", default="")
    impact_parser.add_argument(
        "--archive", type=Path, default=None, help="an existing project: its ZIP (read, never extracted)"
    )
    impact_parser.add_argument(
        "--files",
        type=Path,
        default=None,
        help="a new project: the folder its files are written in, so that what they say about each other is followed too",
    )
    impact_parser.add_argument(
        "--values", type=Path, default=None, help="a canon values file: list the values taken from the changed files"
    )
    impact_parser.add_argument("--out", default=None)
    impact_parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing --out file"
    )

    canon_parser = subparsers.add_parser(
        "canon",
        help=(
            "ask which file decides each fact of a project (a ZIP, or a new idea) and say whether "
            "its structure is settled, or confirm the canon values an assistant proposed; no model, "
            "writes only a file you name"
        ),
    )
    canon_parser.add_argument(
        "canon_command",
        choices=("gate", "apply", "values"),
        help=(
            "gate: the questions and where the project stands. values: list canon values, or confirm / "
            "revise one at a terminal with your own words (never done by an assistant). apply: "
            "experimental, not part of the public surface (a plan for setting old documents aside, and "
            "a check of the result)"
        ),
    )
    canon_parser.add_argument(
        "canon_args",
        nargs=argparse.REMAINDER,
        help="that command's own arguments; run `python -m synapse.core.canon_gate --help` (or canon_apply, canon_values_cli) to list them",
    )

    novel_parser = subparsers.add_parser(
        "novel",
        help="seed a novel, confirm its plan, request and check beats, then lock and assemble them",
        add_help=False,
    )
    novel_parser.add_argument(
        "novel_args",
        nargs=argparse.REMAINDER,
        help="arguments for the novel edition flow",
    )

    for name, text in (
        ("setup", "register the novel edition with Claude Code and/or Codex (shows a plan first)"),
        ("doctor", "check, read only, that the registration and the skill copy are in place"),
        ("uninstall", "take back exactly what setup put there"),
    ):
        subparsers.add_parser(name, help=text, add_help=False)  # listed here; handled before parsing

    args, unknown_args = parser.parse_known_args()
    if unknown_args and args.command != "novel":
        parser.error("unrecognized arguments: " + " ".join(unknown_args))
    try:
        if args.command == "novel":
            root = _source_root(args.source_root)
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from synapse.novel.edition_cli import main as novel_main

            return novel_main([*args.novel_args, *unknown_args])
        if args.command == "canon":
            return _canon(args)
        if args.command == "impact":
            return _impact(args)
        _print_preflight(args)
        root, plan, _ = _build_plan(args)
        if args.command == "plan":
            return _emit(_plan_result(root, plan), args.out, overwrite=args.overwrite)

        _, _, _, verifier, explainer = _core(args.source_root)
        if args.command == "check":
            workspace = args.workspace.expanduser().resolve()
            contents = {}
            input_errors = {}
            assessment_inputs: dict[str, bytes | None] = {}
            for item in plan.files:
                path = workspace / item.path
                if path.is_file():
                    raw = path.read_bytes()
                    assessment_inputs[item.path] = raw
                    try:
                        contents[item.path] = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        input_errors[item.path] = (
                            f"{item.path} is not UTF-8 text; save it as UTF-8 and re-run"
                        )
                else:
                    assessment_inputs[item.path] = None
            result = _evaluate(
                plan,
                contents,
                verifier,
                explainer,
                input_errors=input_errors,
                reproducibility=_reproducibility(
                    root,
                    plan,
                    assessment_input_sha256=_digest_named_inputs(assessment_inputs),
                    assessment_input_kind="workspace_files",
                ),
                operation_kind="RECHECK_ONLY",
            )
        else:
            raw_content = args.content_json.expanduser().read_bytes()
            payload = json.loads(raw_content.decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("content JSON must be an object keyed by relative path")
            result = _evaluate(
                plan,
                payload,
                verifier,
                explainer,
                reproducibility=_reproducibility(
                    root,
                    plan,
                    assessment_input_sha256=_sha256_bytes(raw_content),
                    assessment_input_kind="proposal_json",
                ),
                operation_kind="PROPOSAL_REVIEW_ONLY",
            )
        result["canonical_source"] = _describe_source(root)
        _print_result_summary(result)
        return _emit(result, args.out, overwrite=args.overwrite)
    except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
        print("synapse-novel: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
