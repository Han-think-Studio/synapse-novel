"""Register the Synapse Novel with Claude Code and Codex, once a person has seen the plan and said yes (DEC-0206).

``setup`` writes one MCP entry named ``synapse-novel`` into a client's own config and copies the skill folder beside
it. ``doctor`` reads and reports; ``uninstall`` takes back exactly what ``setup`` put there. Standard library only.

How it stays safe:

* **Named, never found.** Clients are named with ``--client``; nothing is detected, listed or searched. Each client
  has a few fixed paths, read and written directly. No other program is run and no ``PATH`` is consulted.
* **The plan comes first and is the default.** ``--dry-run`` writes nothing. Applying needs the person to type
  ``yes`` at a terminal, or to pass ``--yes`` together with an explicit ``--client``. A config's *contents* are never
  printed (they can hold tokens); only paths, actions and the one entry that would be written.
* **Only our entry.** An entry named ``synapse-novel`` is ours only when its arguments name this edition's
  ``editions/novel/synapse_novel_mcp.py``; anything else under that name is left alone and the client is refused. Everything
  else in the file is kept: other servers, other keys, comments in TOML.
* **Check before writing, check after.** The new file is built in memory and parsed; if anything but our entry
  would change, nothing is written. The config's hash is compared with the plan's right before the swap; if the
  client saved in between, nothing is written.
* **No rollback over a newer save.** The client should be closed while this runs. If the file changes after the
  write, it is *reported* with the paths of the recovery copies, not undone, because undoing would erase the other
  program's save.
* **Recovery copies are records, not state.** Before the first change the original is kept beside the config as
  ``<name>.synapse-novel.backup`` (never overwritten); if the config differs from it later, a ``.recovery`` copy
  named by its hash is added. They hold the whole config, so they stay in the same folder with the same mode.
* **Skill copy.** Written only into an empty or absent folder, or one whose files are exactly what an earlier
  ``setup`` installed. A file the person added or edited stops it; nothing is deleted.

The registered command is this interpreter (``sys.executable``), so run ``setup`` with the Python the server should
use, normally the checkout's ``.venv``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SERVER_NAME = "synapse-novel"
SERVER_FILE = "synapse_novel_mcp.py"
ROOT_ENV = "SYNAPSE_NOVEL_ROOT"
CLIENTS = ("claude_code", "codex")
RESTART = {"claude_code": "Claude Code", "codex": "Codex"}
MANIFEST = ".synapse-novel-install.json"
BACKUP_SUFFIX = ".synapse-novel.backup"
MAX_CONFIG_BYTES = 64 * 1024 * 1024

DONE, NOT_DONE, CANNOT_RUN = 0, 1, 2

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_SKILL_NAME = re.compile(r"^---\s*\n(?:.*\n)*?name:\s*synapse-novel\s*$", re.MULTILINE)


class Refused(ValueError):
    """A target that cannot be changed safely; the message says why, and nothing was written."""


class Changed(Refused):
    """The folder is not what it was when the plan was made; nothing was written or removed."""


@dataclass(frozen=True, slots=True)
class Target:
    client: str
    config: Path
    skill: Path
    kind: str  # "json" or "toml"


@dataclass(frozen=True, slots=True)
class Hooks:
    """Points where a test (or a very careful caller) can act between the steps; never set in normal use."""

    before_write: Callable[[str, Path], None] | None = None
    after_write: Callable[[str, Path], None] | None = None
    before_skill: Callable[[str, Path], None] | None = None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- where each client keeps things ----------------------------------------------------------------


def resolve_target(client: str, home: Path, env: Mapping[str, str]) -> Target:
    if client == "claude_code":
        if env.get("CLAUDE_CONFIG_DIR", "").strip():
            raise Refused(
                "CLAUDE_CONFIG_DIR is set, so where Claude Code keeps .claude.json is not known here; "
                "nothing is guessed. Register by hand (see CONNECT.md)."
            )
        return Target(
            client, home / ".claude.json", home / ".claude" / "skills" / SERVER_NAME, "json"
        )
    if client == "codex":
        configured = env.get("CODEX_HOME", "").strip()
        base = Path(configured) if configured else home / ".codex"
        if not base.is_absolute():
            raise Refused("CODEX_HOME must be an absolute path.")
        return Target(
            client, base / "config.toml", home / ".agents" / "skills" / SERVER_NAME, "toml"
        )
    raise Refused(f"unknown client: {client}")


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def _refuse_links(path: Path, home: Path) -> None:
    """Refuse a path that is, or lies under, a link: a write would land somewhere else.

    Under the home folder every folder down from it is looked at; elsewhere (a ``CODEX_HOME`` the person chose) the
    file and the folder it is in.
    """

    candidates = [path, path.parent]
    if home in path.parents:
        candidates = [path, *[p for p in path.parents if home in p.parents]]
    for probe in candidates:
        if _is_link(probe):
            raise Refused(f"{probe} is a link; setup does not write through links.")


def _read_config(target: Target, home: Path) -> bytes | None:
    _refuse_links(target.config, home)
    if not target.config.exists():
        return None
    if not target.config.is_file():
        raise Refused(f"{target.config} is not a file.")
    if target.config.stat().st_size > MAX_CONFIG_BYTES:
        raise Refused(f"{target.config} is larger than {MAX_CONFIG_BYTES} bytes; not touched.")
    return target.config.read_bytes()


def _decode(data: bytes | None, path: Path) -> str | None:
    if data is None:
        return None
    if data.startswith(b"\xef\xbb\xbf"):
        raise Refused(f"{path} starts with a byte-order mark; not touched.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refused(f"{path} is not UTF-8; not touched.") from exc


# --- the entry --------------------------------------------------------------------------------------


def _our_script(argument: Any) -> bool:
    if not isinstance(argument, str):
        return False
    parts = re.split(r"[\\/]", argument)
    return len(parts) >= 2 and parts[-1] == SERVER_FILE and parts[-2] == "novel"


def is_ours(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("args"), list)
        and any(_our_script(argument) for argument in entry["args"])
    )


def desired_entry(
    existing: Any, *, kind: str, python: str, server: str, root: str
) -> dict[str, Any]:
    """Our entry, keeping any keys and any other environment variables the person already put in it."""

    entry: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise Refused(
            "the existing synapse-novel entry has an `env` that is not a table of strings."
        )
    wanted: dict[str, Any] = {"command": python, "args": [server], "env": {**env, ROOT_ENV: root}}
    if kind == "json":
        wanted = {"type": "stdio", **wanted}
    entry.update(wanted)
    return entry


# --- JSON (Claude Code) -----------------------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def _parse_json(text: str | None, path: Path) -> dict[str, Any]:
    if text is None or not text.strip():
        return {}
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates)
    except ValueError as exc:
        raise Refused(f"{path} is not valid JSON ({exc}); not touched.") from exc
    if not isinstance(data, dict):
        raise Refused(f"{path} is not a JSON object; not touched.")
    return data


def _dump_json(data: dict[str, Any], original: str | None) -> str:
    indent: str | int = 2
    eol = "\n"
    trailing = True
    if original:
        found = re.search(r"\n([ \t]+)\"", original)
        if found:
            indent = found.group(1)
        if "\r\n" in original:
            eol = "\r\n"
        trailing = original.endswith("\n")
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    return text.replace("\n", eol) + (eol if trailing else "")


@dataclass(frozen=True, slots=True)
class Change:
    action: str  # create | update | unchanged | remove | absent
    new_text: str | None = None
    # For a skill folder: every file it held when the plan was made, as (relative path, sha256), sorted.
    files: tuple[tuple[str, str], ...] | None = None


def plan_json(text: str | None, path: Path, *, python: str, server: str, root: str) -> Change:
    data = _parse_json(text, path)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise Refused(f"`mcpServers` in {path} is not an object; not touched.")
    existing = servers.get(SERVER_NAME)
    if existing is not None and not is_ours(existing):
        raise Refused(
            f"{path} already has a server named {SERVER_NAME} that is not this edition's; not touched."
        )
    entry = desired_entry(existing, kind="json", python=python, server=server, root=root)
    if existing == entry:
        return Change("unchanged")
    updated = {**data, "mcpServers": {**servers, SERVER_NAME: entry}}
    new_text = _dump_json(updated, text)
    if _parse_json(new_text, path) != updated:
        raise Refused(
            f"{path} could not be rewritten without changing something else; not touched."
        )
    return Change("create" if existing is None else "update", new_text)


def plan_json_removal(text: str | None, path: Path) -> Change:
    data = _parse_json(text, path)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise Refused(f"`mcpServers` in {path} is not an object; not touched.")
    existing = servers.get(SERVER_NAME)
    if existing is None:
        return Change("absent")
    if not is_ours(existing):
        raise Refused(f"the {SERVER_NAME} entry in {path} is not this edition's; not touched.")
    remaining = {k: v for k, v in servers.items() if k != SERVER_NAME}
    updated = {**data, "mcpServers": remaining}
    new_text = _dump_json(updated, text)
    if _parse_json(new_text, path) != updated:
        raise Refused(
            f"{path} could not be rewritten without changing something else; not touched."
        )
    return Change("remove", new_text)


# --- TOML (Codex) -----------------------------------------------------------------------------------


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    escaped = "".join(
        f"\\u{ord(ch):04x}" if ord(ch) < 32 or ord(ch) == 127 else ch for ch in escaped
    )
    return f'"{escaped}"'


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _toml_string(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items())
        return "{ " + inner + " }" if inner else "{}"
    raise Refused("the existing synapse-novel entry holds a value setup cannot write back safely.")


def _toml_table(entry: dict[str, Any]) -> str:
    lines = [f"[mcp_servers.{SERVER_NAME}]"]
    for key in ("command", "args", "env"):
        lines.append(f"{key} = {_toml_value(entry[key])}")
    lines.extend(
        f"{_toml_key(k)} = {_toml_value(v)}"
        for k, v in entry.items()
        if k not in {"command", "args", "env"}
    )
    return "\n".join(lines) + "\n"


def _parse_toml(text: str | None, path: Path) -> dict[str, Any]:
    if text is None or not text.strip():
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise Refused(f"{path} is not valid TOML ({exc}); not touched.") from exc


_HEADER = re.compile(
    r"^[ \t]*\[[ \t]*mcp_servers[ \t]*\.[ \t]*(?:synapse-novel|\"synapse-novel\"|'synapse-novel')[ \t]*\][ \t]*(?:#[^\n]*)?$",
    re.MULTILINE,
)
_ANY_HEADER = re.compile(r"^[ \t]*\[", re.MULTILINE)


def _table_span(text: str, path: Path) -> tuple[int, int]:
    """Where our table sits in the text, or a refusal when it cannot be told apart from anything else."""

    found = list(_HEADER.finditer(text))
    if len(found) != 1:
        raise Refused(
            f"the {SERVER_NAME} entry in {path} is written in a form setup cannot edit in place; not touched."
        )
    start = found[0].start()
    following = _ANY_HEADER.search(text, found[0].end())
    return start, following.start() if following else len(text)


def _eol_of(text: str | None, path: Path) -> str:
    if not text or "\r\n" not in text:
        return "\n"
    if re.search(r"(?<!\r)\n", text):
        raise Refused(f"{path} mixes line endings; not touched.")
    return "\r\n"


def _toml_servers(data: dict[str, Any], path: Path) -> dict[str, Any]:
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise Refused(f"`mcp_servers` in {path} is not a table; not touched.")
    return servers


def plan_toml(text: str | None, path: Path, *, python: str, server: str, root: str) -> Change:
    data = _parse_toml(text, path)
    servers = _toml_servers(data, path)
    existing = servers.get(SERVER_NAME)
    if existing is not None and not is_ours(existing):
        raise Refused(
            f"{path} already has a server named {SERVER_NAME} that is not this edition's; not touched."
        )
    entry = desired_entry(existing, kind="toml", python=python, server=server, root=root)
    if existing == entry:
        return Change("unchanged")
    eol = _eol_of(text, path)
    body = (text or "").replace("\r\n", "\n")
    table = _toml_table(entry)
    if existing is None:
        new_body = (
            table if not body else body + ("" if body.endswith("\n") else "\n") + "\n" + table
        )
    else:
        start, end = _table_span(body, path)
        new_body = body[:start] + table + ("\n" if end < len(body) else "") + body[end:]
    new_text = new_body.replace("\n", eol)
    expected = {**data, "mcp_servers": {**servers, SERVER_NAME: entry}}
    if _parse_toml(new_text, path) != expected:
        raise Refused(f"{path} could not be edited without changing something else; not touched.")
    return Change("create" if existing is None else "update", new_text)


def plan_toml_removal(text: str | None, path: Path) -> Change:
    data = _parse_toml(text, path)
    servers = _toml_servers(data, path)
    existing = servers.get(SERVER_NAME)
    if existing is None:
        return Change("absent")
    if not is_ours(existing):
        raise Refused(f"the {SERVER_NAME} entry in {path} is not this edition's; not touched.")
    eol = _eol_of(text, path)
    body = (text or "").replace("\r\n", "\n")
    start, end = _table_span(body, path)
    head, rest = body[:start].rstrip("\n"), body[end:].lstrip("\n")
    new_body = (head + "\n" if head else "") + ("\n" if head and rest else "") + rest
    new_text = new_body.replace("\n", eol)
    remaining = {k: v for k, v in servers.items() if k != SERVER_NAME}
    bare = {k: v for k, v in data.items() if k != "mcp_servers"}
    allowed = [{**bare, "mcp_servers": remaining}] + ([bare] if not remaining else [])
    if _parse_toml(new_text, path) not in allowed:
        raise Refused(f"{path} could not be edited without changing something else; not touched.")
    return Change("remove", new_text)


# --- writing ------------------------------------------------------------------------------------------


def _write_atomic(path: Path, data: bytes, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _mode_of(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _keep_copy(config: Path, original: bytes) -> dict[str, str | None]:
    """The first snapshot is never overwritten; a config that differs from it later is kept by hash."""

    first = config.with_name(config.name + BACKUP_SUFFIX)
    mode = _mode_of(config)
    if first.exists() or _is_link(first):
        if not first.is_file() or _is_link(first):
            raise Refused(f"{first} is in the way and is not a plain file; not touched.")
        if first.read_bytes() == original:
            return {"backup": str(first), "recovery": None}
        recovery = config.with_name(f"{config.name}{BACKUP_SUFFIX}.{_sha(original)[:16]}.recovery")
        if not recovery.exists():
            _write_atomic(recovery, original, mode)
        return {"backup": str(first), "recovery": str(recovery)}
    _write_atomic(first, original, mode)
    return {"backup": str(first), "recovery": None}


# --- the skill copy -------------------------------------------------------------------------------------


def bundle_skill() -> dict[str, bytes]:
    folder = HERE.parents[1] / "skills" / SERVER_NAME
    if not folder.is_dir():
        raise Refused(f"the skill folder is missing from this checkout: {folder}")
    files: dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            files[path.relative_to(folder).as_posix()] = path.read_bytes()
    if "SKILL.md" not in files:
        raise Refused("the skill folder in this checkout has no SKILL.md.")
    return files


def _folder_files(folder: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for current, directories, names in os.walk(folder):
        for name in [*directories, *names]:
            if _is_link(Path(current, name)):
                raise Refused(
                    f"{Path(current, name)} is a link; setup does not write through links."
                )
        for name in names:
            path = Path(current, name)
            files[path.relative_to(folder).as_posix()] = path.read_bytes()
    return files


def _manifest(data: Mapping[str, bytes]) -> dict[str, str]:
    return {name: _sha(content) for name, content in data.items() if name != MANIFEST}


def plan_skill(folder: Path, bundle: Mapping[str, bytes], home: Path) -> Change:
    _refuse_links(folder, home)
    if not folder.exists():
        return Change("create", files=())
    if not folder.is_dir():
        raise Refused(f"{folder} is not a folder; not touched.")
    current = _folder_files(folder)
    skill = current.get("SKILL.md", b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
    if not _SKILL_NAME.search(skill):
        raise Refused(f"{folder} is not this edition's skill folder; not touched.")
    installed: dict[str, str] | None = None
    if MANIFEST in current:
        try:
            installed = json.loads(current[MANIFEST].decode("utf-8"))["files"]
            if not isinstance(installed, dict):
                raise TypeError("files")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            raise Refused(f"{folder / MANIFEST} cannot be read; not touched.") from exc
    known = set(bundle) | set(installed or ())
    extra = sorted(set(current) - known - {MANIFEST})
    if extra:
        raise Refused(
            f"{folder} holds files setup did not install ({', '.join(extra)}); not touched."
        )
    pristine = installed if installed is not None else _manifest(bundle)
    for name, content in current.items():
        if name != MANIFEST and pristine.get(name) != _sha(content):
            raise Refused(f"{folder / name} was edited by hand; not touched.")
    listing = _listing(current)
    if _manifest(current) == _manifest(bundle) and MANIFEST in current:
        return Change("unchanged", files=listing)
    return Change("update", files=listing)


def _listing(files: Mapping[str, bytes]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((name, _sha(content)) for name, content in files.items()))


def _still_as_planned(folder: Path, expected: tuple[tuple[str, str], ...] | None) -> None:
    """Refuse to touch a skill folder that gained, lost or changed a file since the plan."""

    if expected is None:
        raise Changed(f"{folder} was not planned; nothing was changed.")
    present = _listing(_folder_files(folder)) if folder.exists() else ()
    if present != expected:
        raise Changed(f"{folder} changed after the plan was made; nothing was changed. Plan again.")


def apply_skill(
    folder: Path, bundle: Mapping[str, bytes], expected: tuple[tuple[str, str], ...] | None
) -> None:
    _still_as_planned(folder, expected)
    for name, content in bundle.items():
        _write_atomic(folder / name, content, None)
    for name, _digest in expected or ():
        if (
            name not in bundle and name != MANIFEST
        ):  # a file an earlier setup installed, checked unchanged above
            (folder / name).unlink()
    record = json.dumps(
        {
            "installed_by": "synapse-novel setup",
            "files": _manifest(bundle),
            "server_sha256": _sha((HERE / SERVER_FILE).read_bytes()),
        },
        indent=2,
        sort_keys=True,
    )
    _write_atomic(folder / MANIFEST, (record + "\n").encode("utf-8"), None)


def plan_skill_removal(folder: Path, bundle: Mapping[str, bytes], home: Path) -> Change:
    try:
        change = plan_skill(folder, bundle, home)
    except Refused:
        if not folder.exists():
            return Change("absent")
        raise
    return Change("absent") if change.action == "create" else Change("remove", files=change.files)


def remove_skill(folder: Path, expected: tuple[tuple[str, str], ...] | None) -> None:
    _still_as_planned(folder, expected)
    for name, _digest in sorted(expected or (), reverse=True):
        (folder / name).unlink()
    _remove_empty(folder)


# --- plans ---------------------------------------------------------------------------------------------


def validate_root(raw: str) -> str:
    root = Path(raw).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"--root must be an existing folder: {root}")
    if root == Path(root.anchor):
        raise ValueError("--root must be a folder, not a whole drive.")
    return str(root)


def validate_python(raw: str) -> str:
    path = Path(raw)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"the interpreter must be an absolute path to a file: {raw}")
    return str(path)


def _entry_view(python: str, server: str, root: str) -> dict[str, Any]:
    return {
        "command": python,
        "args": [server],
        "env": {ROOT_ENV: root},
        "script_sha256": _sha(Path(server).read_bytes()),
    }


def _plan_target(
    client: str,
    mode: str,
    home: Path,
    env: Mapping[str, str],
    settings: Mapping[str, str],
    bundle: Mapping[str, bytes],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "client": client,
        "restart": RESTART[client],
        "config": {},
        "skill": {},
    }
    try:
        target = resolve_target(client, home, env)
        raw = _read_config(target, home)
        text = _decode(raw, target.config)
        planner = {
            ("json", "setup"): lambda: plan_json(text, target.config, **settings),
            ("toml", "setup"): lambda: plan_toml(text, target.config, **settings),
            ("json", "remove"): lambda: plan_json_removal(text, target.config),
            ("toml", "remove"): lambda: plan_toml_removal(text, target.config),
        }[(target.kind, mode)]
        change = planner()
        record["config"] = {
            "path": str(target.config),
            "action": change.action,
            "before_sha256": None if raw is None else _sha(raw),
            "backup": None
            if raw is None or change.action in {"unchanged", "absent"}
            else str(target.config.with_name(target.config.name + BACKUP_SUFFIX)),
        }
        record["_change"], record["_target"], record["_raw"] = change, target, raw
    except Refused as exc:
        record["config"] = {"action": "refused", "reason": str(exc)}
        record["skill"] = {"action": "skipped", "reason": "the config was refused"}
        return record
    try:
        skill = (
            plan_skill(target.skill, bundle, home)
            if mode == "setup"
            else plan_skill_removal(target.skill, bundle, home)
        )
        record["skill"] = {"path": str(target.skill), "action": skill.action}
        record["_skill"] = skill
    except Refused as exc:
        record["skill"] = {"path": str(target.skill), "action": "refused", "reason": str(exc)}
    return record


def build_plan(
    mode: str,
    clients: list[str],
    *,
    home: Path,
    env: Mapping[str, str],
    python: str,
    root: str | None,
) -> dict[str, Any]:
    server = str(HERE / SERVER_FILE)
    if not (HERE / SERVER_FILE).is_file():
        raise ValueError(f"the server is missing from this checkout: {server}")
    settings = {"python": python, "server": server, "root": root or ""}
    bundle = bundle_skill() if mode == "setup" else _safe_bundle()
    targets = [_plan_target(c, mode, home, env, settings, bundle) for c in dict.fromkeys(clients)]
    warnings = []
    venv = HERE.parents[1] / ".venv"
    if mode == "setup" and venv.is_dir() and venv.resolve() not in Path(python).resolve().parents:
        warnings.append("this interpreter is not the checkout's .venv; the server runs with it")
    return {
        "mode": mode,
        "entry": _entry_view(python, server, root or "") if mode == "setup" else None,
        "targets": targets,
        "warnings": warnings,
        "writes": sorted(_writes(targets)),
    }


def _safe_bundle() -> dict[str, bytes]:
    try:
        return bundle_skill()
    except Refused:
        return {}


def _writes(targets: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for target in targets:
        for part in ("config", "skill"):
            info = target[part]
            if info.get("action") in {"create", "update", "remove"} and info.get("path"):
                paths.add(info["path"])
        backup = target["config"].get("backup")
        if backup:
            paths.add(backup)
    return paths


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """The plan without its working parts: paths and actions only, never a config's contents."""

    return {
        **{k: v for k, v in plan.items() if k != "targets"},
        "targets": [
            {k: v for k, v in target.items() if not k.startswith("_")} for target in plan["targets"]
        ],
    }


# --- applying -------------------------------------------------------------------------------------------


def _apply_config(target: dict[str, Any], hooks: Hooks) -> dict[str, Any]:
    info = target["config"]
    change: Change = target["_change"]
    config: Path = target["_target"].config
    if change.action in {"unchanged", "absent"}:
        return {"status": change.action}
    now = config.read_bytes() if config.exists() else None
    if (None if now is None else _sha(now)) != info["before_sha256"]:
        return {
            "status": "aborted",
            "reason": "the config changed after the plan was made; nothing was written. Plan again.",
        }
    try:
        copies = {} if now is None else _keep_copy(config, now)
        if hooks.before_write:
            hooks.before_write(target["client"], config)
        again = config.read_bytes() if config.exists() else None
        if (None if again is None else _sha(again)) != info["before_sha256"]:
            return {
                "status": "aborted",
                "reason": "the config changed just before the swap; nothing was written.",
                **copies,
            }
        data = (change.new_text or "").encode("utf-8")
        _write_atomic(
            config,
            data,
            _mode_of(config) if now is not None else (0o600 if os.name != "nt" else None),
        )
    except Refused as exc:
        return {"status": "refused", "reason": str(exc)}
    except OSError as exc:
        return {
            "status": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "note": "the original is untouched",
        }
    if hooks.after_write:
        hooks.after_write(target["client"], config)
    written = config.read_bytes() if config.exists() else b""
    if written != data:
        return {
            "status": "changed_after_write",
            "reason": "the config differs from what was written, so another program saved it; nothing was undone.",
            **copies,
        }
    return {"status": "done", **copies}


def apply_plan(plan: dict[str, Any], *, hooks: Hooks | None = None) -> list[dict[str, Any]]:
    hooks = hooks or Hooks()
    bundle = bundle_skill() if plan["mode"] == "setup" else _safe_bundle()
    results = []
    for target in plan["targets"]:
        result: dict[str, Any] = {"client": target["client"], "restart": target["restart"]}
        if target["config"]["action"] == "refused":
            result["config"] = {"status": "refused", "reason": target["config"]["reason"]}
            result["skill"] = {"status": "skipped"}
            results.append(result)
            continue
        if target["skill"]["action"] == "refused":
            result["config"] = {
                "status": "skipped",
                "reason": "the skill folder was refused, so this client was left as it was",
            }
            result["skill"] = {"status": "refused", "reason": target["skill"].get("reason", "")}
            results.append(result)
            continue
        result["config"] = _apply_config(target, hooks)
        if result["config"]["status"] not in {"done", "unchanged", "absent"}:
            result["skill"] = {"status": "skipped", "reason": "the config was not changed"}
            results.append(result)
            continue
        skill: Change | None = target.get("_skill")
        folder = target["_target"].skill
        if skill is None:
            result["skill"] = {"status": "refused", "reason": target["skill"].get("reason", "")}
        elif skill.action in {"create", "update", "remove"}:
            try:
                if hooks.before_skill:
                    hooks.before_skill(target["client"], folder)
                if skill.action == "remove":
                    remove_skill(folder, skill.files)
                else:
                    apply_skill(folder, bundle, skill.files)
                result["skill"] = {"status": "done", "path": str(folder)}
            except Changed as exc:
                result["skill"] = {"status": "aborted", "reason": str(exc)}
            except (OSError, Refused) as exc:
                result["skill"] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
        else:
            result["skill"] = {"status": skill.action}
        results.append(result)
    return results


def _remove_empty(folder: Path) -> None:
    for current, _directories, _names in os.walk(folder, topdown=False):
        try:
            os.rmdir(current)
        except OSError:
            return


def succeeded(results: list[dict[str, Any]]) -> bool:
    ok = {"done", "unchanged", "absent"}
    return all(r["config"]["status"] in ok and r["skill"]["status"] in ok for r in results)


# --- doctor ---------------------------------------------------------------------------------------------


def diagnose(client: str, *, home: Path, env: Mapping[str, str], python: str) -> dict[str, Any]:
    server = str(HERE / SERVER_FILE)
    report: dict[str, Any] = {"client": client, "config": {}, "skill": {}}
    try:
        target = resolve_target(client, home, env)
        text = _decode(_read_config(target, home), target.config)
        if target.kind == "json":
            entry = _parse_json(text, target.config).get("mcpServers", {})
            entry = entry.get(SERVER_NAME) if isinstance(entry, dict) else None
        else:
            servers = _toml_servers(_parse_toml(text, target.config), target.config)
            entry = servers.get(SERVER_NAME)
    except Refused as exc:
        report["config"] = {"state": "invalid", "reason": str(exc)}
        report["skill"] = {"state": "unknown"}
        return report
    report["config"]["path"] = str(target.config)
    if entry is None:
        report["config"]["state"] = "missing"
    elif not is_ours(entry):
        report["config"] = {
            **report["config"],
            "state": "invalid",
            "reason": "another server uses this name",
        }
    else:
        problems = []
        if entry.get("command") != python:
            problems.append("the command is not this interpreter")
        if entry.get("args") != [server]:
            problems.append("the script is not this checkout's")
        root = (
            (entry.get("env") or {}).get(ROOT_ENV) if isinstance(entry.get("env"), dict) else None
        )
        if not isinstance(root, str) or not Path(root).is_dir():
            problems.append(f"{ROOT_ENV} is missing or not a folder")
        if not Path(str(entry.get("command", ""))).is_file():
            problems.append("the registered interpreter does not exist")
        if not all(Path(str(a)).is_file() for a in entry.get("args", []) if _our_script(a)):
            problems.append("the registered script does not exist")
        report["config"]["state"] = (
            "disabled" if entry.get("enabled") is False else ("stale" if problems else "aligned")
        )
        if problems:
            report["config"]["problems"] = problems
    try:
        bundle = bundle_skill()
        change = plan_skill(target.skill, bundle, home)
        state = {"create": "missing", "unchanged": "aligned", "update": "stale"}[change.action]
        report["skill"] = {"path": str(target.skill), "state": state}
    except Refused as exc:
        report["skill"] = {"path": str(target.skill), "state": "modified", "reason": str(exc)}
    return report


# --- the command line -------------------------------------------------------------------------------------


def _at_a_terminal() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synapse_novel.py",
        description="Register the Synapse Novel with Claude Code and Codex, or check or remove it.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "uninstall", "doctor"):
        sub = commands.add_parser(name)
        sub.add_argument("--client", action="append", choices=CLIENTS, default=[])
        if name == "setup":
            sub.add_argument(
                "--root", help="the one folder the server may read and write (must exist)"
            )
        if name != "doctor":
            sub.add_argument(
                "--dry-run", action="store_true", help="show the plan and write nothing"
            )
            sub.add_argument(
                "--yes", action="store_true", help="apply without asking (needs --client)"
            )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    python: str | None = None,
    ask: Callable[[], str] | None = None,
    interactive: Callable[[], bool] = _at_a_terminal,
    hooks: Hooks | None = None,
) -> int:
    args = _parser().parse_args(argv)
    home = home or Path.home()
    env = os.environ if env is None else env

    def say(document: Any) -> None:
        print(json.dumps(document, ensure_ascii=False, indent=2), flush=True)

    def err(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        interpreter = validate_python(python or sys.executable)
        if args.command == "doctor":
            clients = args.client or list(CLIENTS)
            reports = [diagnose(c, home=home, env=env, python=interpreter) for c in clients]
            say(
                {
                    "doctor": reports,
                    "note": (
                        "reads the user-level config and the skill copy only: a project or managed entry of the same "
                        "name can take precedence, and whether a client has loaded the server is not known"
                    ),
                }
            )
            aligned = all(
                r["config"].get("state") == "aligned" and r["skill"].get("state") == "aligned"
                for r in reports
            )
            return DONE if aligned else NOT_DONE
        if not args.client:
            say(
                {
                    "clients": list(CLIENTS),
                    "message": "Name the clients with --client (no detection is done). Add --dry-run to see the plan.",
                }
            )
            return DONE
        root = None
        if args.command == "setup":
            if not args.root:
                raise ValueError(
                    "setup needs --root <folder>: the one folder the server may read and write."
                )
            root = validate_root(args.root)
        plan = build_plan(
            "setup" if args.command == "setup" else "remove",
            args.client,
            home=home,
            env=env,
            python=interpreter,
            root=root,
        )
    except (ValueError, OSError, Refused) as exc:
        err(f"setup: {exc}")
        return CANNOT_RUN

    shown = public_plan(plan)
    if args.dry_run:
        say({"status": "dry_run", "plan": shown})
        return DONE
    actionable = [
        t
        for t in plan["targets"]
        if t["config"]["action"] in {"create", "update", "remove"}
        or t["skill"]["action"] in {"create", "update", "remove"}
    ]
    refused = [
        t for t in plan["targets"] if "refused" in {t["config"]["action"], t["skill"]["action"]}
    ]
    if not actionable:
        say({"status": "nothing_to_do" if not refused else "refused", "plan": shown})
        return NOT_DONE if refused else DONE
    if not args.yes:
        if not interactive():
            say(
                {
                    "status": "needs_confirmation",
                    "plan": shown,
                    "how": "run it again at a terminal and type yes, or add --yes",
                }
            )
            return NOT_DONE
        err(json.dumps(shown, ensure_ascii=False, indent=2))
        err("Close the clients named above first. Type yes to apply:")
        try:
            answer = (ask or input)().strip()
        except EOFError:
            answer = ""
        if answer != "yes":
            say({"status": "cancelled", "plan": shown})
            return NOT_DONE
    results = apply_plan(plan, hooks=hooks)
    ok = succeeded(results) and not refused
    say(
        {
            "status": "applied" if ok else "incomplete",
            "results": results,
            "restart": sorted({r["restart"] for r in results if r["config"]["status"] == "done"}),
            "note": "Each client was handled on its own: what finished stays. Nothing was undone over a newer save.",
        }
    )
    return DONE if ok else NOT_DONE


if __name__ == "__main__":
    for _stream in (
        sys.stdout,
        sys.stderr,
    ):  # JSON with Korean words: UTF-8 even when piped on a cp949 Windows
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
