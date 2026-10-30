"""Deterministic, model-free novel edition flow over the existing canon and artifact contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from synapse.core import canon_values, canon_values_cli, canon_values_read
from synapse.core.canon_check_entry import check as check_canon
from synapse.core.canon_context import (
    build_canon_context,
    context_record,
    produced_with,
    to_prompt_block,
)
from synapse.core.run import RunError, append_artifact, read_artifacts
from synapse.novel.beat_prompt import beat_messages
from synapse.novel.beats import parse_beats
from synapse.novel.manuscript_audit import run_audit
from synapse.novel.manuscript_import import import_manuscript, manuscript_status
from synapse.novel.quality_review import evaluate_chunk
from synapse.novel.scratch_seed import scratch_seed_from_payload, seed_values

PLAN_SCHEMA = "synapse-novel-edition-plan.v1"
SEED_SCHEMA = "synapse-novel-edition-seed.v1"
PROJECT_SCHEMA = "synapse-novel-edition-project.v1"
REQUEST_SCHEMA = "synapse-novel-edition-request.v1"
CHECK_SCHEMA = "synapse-novel-edition-check.v1"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_SCENES = 120
_CONTEXT_MAX_CHARS = 4000
_SCENE_ID = re.compile(r"^ch[1-9][0-9]*s[1-9][0-9]*$")


class FlowError(ValueError):
    """A caller-facing flow problem with its stable exit code."""

    def __init__(self, code: int, message: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.result = result

    def __str__(self) -> str:
        return self.message


class _FlowArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise FlowError(canon_values_cli.CANNOT_RUN, f"명령 인자가 올바르지 않습니다: {message}")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _hash_bytes(body)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"중복 JSON 항목: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, f"파일을 읽을 수 없습니다: {path}") from exc
    if len(data) > maximum:
        raise FlowError(canon_values_cli.CANNOT_RUN, f"파일이 크기 제한을 넘었습니다: {path}")
    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, f"JSON 형식이 맞지 않습니다: {path}") from exc
    if not isinstance(value, dict):
        raise FlowError(canon_values_cli.CANNOT_RUN, f"JSON 객체가 필요합니다: {path}")
    return value


def _write_bytes(path: Path, data: bytes, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        if not overwrite:
            with path.open("xb") as stream:
                created = True
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            return
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except FileExistsError as exc:
        temporary.unlink(missing_ok=True)
        raise FlowError(canon_values_cli.CANNOT_RUN, f"파일이 이미 있습니다: {path}") from exc
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        if created:
            path.unlink(missing_ok=True)
        raise FlowError(canon_values_cli.CANNOT_RUN, f"파일을 저장할 수 없습니다: {path}") from exc


def _write_json(path: Path, value: Any, *, overwrite: bool = True) -> None:
    _write_bytes(path, _json_bytes(value), overwrite=overwrite)


def _root(project: str | Path) -> Path:
    path = Path(project).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise FlowError(canon_values_cli.CANNOT_RUN, f"프로젝트 폴더가 없습니다: {path}")
    return path


def _novel(project: Path) -> Path:
    return _root(project) / "novel"


def _project_id(project: Path) -> str:
    identity_path = _novel(project) / "project.json"
    if identity_path.is_file():
        identity = _read_json(identity_path)
        project_id = identity.get("project_id")
        if identity.get("schema") != PROJECT_SCHEMA or not isinstance(project_id, str) or not project_id.strip():
            raise FlowError(canon_values_cli.CANNOT_RUN, "프로젝트 정체 기록의 형식이 맞지 않습니다.")
        return project_id
    return "novel-" + _hash_bytes(str(project.resolve()).encode("utf-8"))[:24]


def _seed_path(project: Path) -> Path:
    return _novel(project) / "seed.json"


def _seed_text_path(project: Path) -> Path:
    return _novel(project) / "scratch-seed.md"


def _values_path(project: Path) -> Path:
    return _novel(project) / "canon-values.json"


def _events_path(project: Path) -> Path:
    return _novel(project) / "locks.jsonl"


def _seed_state(project: Path):
    seed_doc = _read_json(_seed_path(project))
    if seed_doc.get("schema") != SEED_SCHEMA or not isinstance(seed_doc.get("seed"), Mapping):
        raise FlowError(canon_values_cli.CANNOT_RUN, "씨앗 기록의 형식이 맞지 않습니다.")
    try:
        seed = scratch_seed_from_payload(seed_doc["seed"])
        store, _claims, checked = canon_values_read.load_against(
            _values_path(project), _novel(project)
        )
    except (OSError, TypeError, ValueError) as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, f"씨앗이나 정본 값을 읽을 수 없습니다: {exc}") from exc
    return seed_doc, seed, store, checked


def _input_fingerprint(project: Path, store: canon_values.ValueStore) -> str:
    try:
        values_bytes = _values_path(project).read_bytes()
        seed_bytes = _seed_path(project).read_bytes()
    except OSError as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, "씨앗 또는 정본 값 파일을 읽을 수 없습니다.") from exc
    return _hash_json(
        {
            "seed_sha256": _hash_bytes(seed_bytes),
            "values_sha256": _hash_bytes(values_bytes),
            "entries": [asdict(entry) for entry in store.entries],
        }
    )


def _plan_path(project: Path) -> Path:
    return _novel(project) / "plan.json"


def _request_path(project: Path, scene: str, beat: int) -> Path:
    return _novel(project) / "requests" / f"{scene}-{beat}.json"


def _beat_path(project: Path, scene: str, beat: int) -> Path:
    return _novel(project) / "beats" / f"{scene}-{beat}.txt"


def _receipt_path(project: Path, scene: str, beat: int) -> Path:
    return _novel(project) / "receipts" / f"{scene}-{beat}.json"


def _events(project: Path) -> list[dict[str, Any]]:
    try:
        return read_artifacts(_events_path(project))
    except (OSError, RunError, ValueError) as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, f"계획·잠금 기록을 확인할 수 없습니다: {exc}") from exc


def _append_event(project: Path, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return append_artifact(
            _events_path(project),
            run_id=_project_id(project),
            kind=kind,
            payload=payload,
            deduplicate=True,
        )
    except (OSError, RunError, ValueError) as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, f"계획·잠금 기록을 저장할 수 없습니다: {exc}") from exc


def _plan_payload(project: Path) -> tuple[dict[str, Any], str, bool]:
    path = _plan_path(project)
    if not path.is_file():
        raise FlowError(canon_values_cli.NOT_DONE, "장면 계획이 없습니다.")
    doc = _read_json(path)
    payload = doc.get("payload")
    if doc.get("schema") != PLAN_SCHEMA or not isinstance(payload, dict):
        raise FlowError(canon_values_cli.CANNOT_RUN, "장면 계획의 형식이 맞지 않습니다.")
    plan_hash = _hash_json(payload)
    if doc.get("sha256") != plan_hash:
        raise FlowError(canon_values_cli.CANNOT_RUN, "장면 계획 지문이 맞지 않습니다.")
    _seed_doc, _seed, store, _checked = _seed_state(project)
    return payload, plan_hash, payload.get("input_sha256") == _input_fingerprint(project, store)


def _scene(payload: Mapping[str, Any], scene_id: str) -> tuple[int, dict[str, Any]]:
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        raise FlowError(canon_values_cli.CANNOT_RUN, "장면 계획 목록이 손상되었습니다.")
    for index, item in enumerate(scenes, start=1):
        if isinstance(item, dict) and item.get("id") == scene_id:
            return index, item
    raise FlowError(canon_values_cli.CANNOT_RUN, f"계획에 장면이 없습니다: {scene_id}")


def _beat(beat: int, scene: Mapping[str, Any]) -> Any:
    beats = scene.get("beats")
    if type(beat) is not int or not isinstance(beats, list) or not 1 <= beat <= len(beats):
        raise FlowError(canon_values_cli.CANNOT_RUN, "박자 번호가 계획 범위에 없습니다.")
    parsed = parse_beats({"beats": beats}, scene["subjects"])
    return parsed[beat - 1]


def _current_confirmation(project: Path, plan_hash: str) -> dict[str, Any] | None:
    confirmed = [
        item
        for item in _events(project)
        if item.get("kind") == "plan_confirmed"
        and isinstance(item.get("payload"), Mapping)
        and item["payload"].get("plan_sha256") == plan_hash
    ]
    return confirmed[-1] if confirmed else None


def _require_confirmed(project: Path, plan_hash: str, current: bool) -> None:
    if not current:
        raise FlowError(canon_values_cli.NOT_DONE, "씨앗 또는 확정 정본 값이 바뀌어 계획을 다시 확인해야 합니다.")
    if _current_confirmation(project, plan_hash) is None:
        raise FlowError(canon_values_cli.NOT_DONE, "사람이 터미널에서 이 계획을 확인해야 합니다.")


def _load_request(project: Path, scene: str, beat: int) -> dict[str, Any]:
    path = _request_path(project, scene, beat)
    if not path.is_file():
        raise FlowError(canon_values_cli.NOT_DONE, "먼저 이 박자의 작성 요청을 만들어 주세요.")
    doc = _read_json(path)
    if doc.get("schema") != REQUEST_SCHEMA or not isinstance(doc.get("request"), dict):
        raise FlowError(canon_values_cli.CANNOT_RUN, "박자 요청 기록의 형식이 맞지 않습니다.")
    return doc["request"]


def _latest_lock(project: Path, scene: str, beat: int) -> dict[str, Any] | None:
    found = [
        item
        for item in _events(project)
        if item.get("kind") == "beat_locked"
        and isinstance(item.get("payload"), Mapping)
        and item["payload"].get("scene") == scene
        and item["payload"].get("beat") == beat
    ]
    return found[-1] if found else None


def _load_live_state(project: Path):
    payload, plan_hash, current = _plan_payload(project)
    _seed_doc, seed, store, checked = _seed_state(project)
    return payload, plan_hash, current, seed, store, checked


def _context_for(scene: Mapping[str, Any], values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    context = build_canon_context(
        values, subjects=scene["subjects"], rows=scene.get("rows", []), max_chars=_CONTEXT_MAX_CHARS
    )
    record = {
        **context_record(context),
        "subjects": list(scene["subjects"]),
        "rows": list(scene.get("rows", [])),
        "max_chars": _CONTEXT_MAX_CHARS,
    }
    return to_prompt_block(context), record


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _quality_advice(text: str, scene: Mapping[str, Any], beat: Any) -> dict[str, Any]:
    quality = evaluate_chunk(
        text,
        directive=scene["request"],
        subjects=scene["subjects"],
        start_state=beat.start_state,
        end_state=beat.end_state,
    )
    quality.pop("quality_retry", None)
    quality["advisory"] = True
    quality["typo_suspicions"] = []
    quality["typo_review"] = {
        "status": "not_assessed",
        "corpus_available": False,
        "note": "말뭉치가 없어 오타 의심을 평가하지 않았습니다.",
        "advisory": True,
    }
    return quality


def _current_previous_content(
    project: Path,
    scene_id: str,
    beat_number: int,
    plan_payload: Mapping[str, Any],
    plan_hash: str,
    plan_current: bool,
) -> str:
    if beat_number == 1:
        return ""
    previous_number = beat_number - 1
    previous = _latest_lock(project, scene_id, previous_number)
    if previous is None or _lock_stale_reason(
        project, scene_id, previous_number, previous, plan_payload, plan_hash, plan_current
    ):
        raise FlowError(
            canon_values_cli.NOT_DONE,
            f"{previous_number}번째 박자를 먼저 확인하고 잠가야 합니다.",
            {"ready": False, "scene": scene_id, "beat": beat_number},
        )
    try:
        text = _beat_path(project, scene_id, previous_number).read_bytes().decode("utf-8")
    except OSError as exc:
        raise FlowError(canon_values_cli.NOT_DONE, "잠근 직전 박자 본문을 찾을 수 없습니다.") from exc
    except UnicodeError as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, "잠근 직전 박자 본문이 UTF-8이 아닙니다.") from exc
    return _normalize_text(text)


def seed_project(project: str | Path, seed_file: str | Path) -> dict[str, Any]:
    """Seed a project; a missing project folder is made, as `import` makes one, and taken back if seeding fails."""

    path = Path(project).expanduser().resolve()
    made = not path.exists() and path.parent.is_dir()
    if made:
        path.mkdir()
    try:
        return _seed_project(path, seed_file)
    except BaseException:
        if made:
            for leftover in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                leftover.unlink() if leftover.is_file() else leftover.rmdir()
            path.rmdir()
        raise


def _seed_project(project: Path, seed_file: str | Path) -> dict[str, Any]:
    root = _root(project)
    folder = _novel(root)
    identity_path = _novel(root) / "project.json"
    values_path = _values_path(root)
    seed_targets = (_seed_path(root), _seed_text_path(root))
    if any(path.exists() for path in seed_targets):
        raise FlowError(canon_values_cli.CANNOT_RUN, "씨앗 자료가 이미 있어 덮어쓰지 않았습니다.")

    existing_store = None
    identity_exists = identity_path.exists()
    values_exist = values_path.exists()
    if identity_exists or values_exist:
        manifest_path = folder / "manuscript" / "manifest.json"
        if not (identity_exists and values_exist and manifest_path.is_file()):
            raise FlowError(
                canon_values_cli.CANNOT_RUN,
                "기존 프로젝트에 씨앗을 추가하려면 원고 가져오기가 끝났고 정본 값이 비어 있어야 합니다.",
            )
        identity = _read_json(identity_path)
        project_id = identity.get("project_id")
        if (
            identity.get("schema") != PROJECT_SCHEMA
            or not isinstance(project_id, str)
            or not project_id.strip()
        ):
            raise FlowError(canon_values_cli.CANNOT_RUN, "프로젝트 정체 기록의 형식이 맞지 않습니다.")
        try:
            existing_store = canon_values.load(values_path, (), only_files=())
        except (OSError, TypeError, ValueError) as exc:
            raise FlowError(canon_values_cli.CANNOT_RUN, f"정본 값 파일을 읽을 수 없습니다: {exc}") from exc
        if existing_store.project_id != project_id:
            raise FlowError(canon_values_cli.CANNOT_RUN, "프로젝트 정체 기록과 정본 값의 project_id가 다릅니다.")
        if existing_store.entries or existing_store.history:
            raise FlowError(canon_values_cli.CANNOT_RUN, "정본 값이 이미 있어 씨앗을 추가하지 않았습니다.")
    else:
        project_id = "novel-" + uuid.uuid4().hex

    payload = _read_json(Path(seed_file).expanduser().resolve())
    try:
        seed = scratch_seed_from_payload(payload)
        store, _claims, source = seed_values(
            seed,
            project_id,
            archive_sha256=existing_store.archive_sha256 if existing_store is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, f"씨앗 내용을 확인할 수 없습니다: {exc}") from exc
    folder.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        records = (
            (_seed_path(root), _json_bytes({"schema": SEED_SCHEMA, "seed": payload})),
            (_seed_text_path(root), source.encode("utf-8")),
        )
        if existing_store is None:
            records = (
                (identity_path, _json_bytes({"schema": PROJECT_SCHEMA, "project_id": project_id})),
                *records,
            )
        for path, data in records:
            _write_bytes(path, data, overwrite=False)
            created.append(path)
        canon_values.save(store, values_path)
        if existing_store is None:
            created.append(values_path)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    proposed = [entry.key for entry in store.entries]
    confirm_commands = []
    for key in proposed:
        command = [
            "python",
            "editions/novel/synapse_novel.py",
            "canon",
            "values",
            "confirm",
            str(values_path),
            key,
            "--files",
            str(folder),
        ]
        confirm_commands.append(
            subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
        )
    missing_character_values = []
    for person in seed.characters:
        missing = [
            name
            for name, value in (("age", getattr(person, "age", None)), ("sex", getattr(person, "sex", None)))
            if value is None
        ]
        if missing:
            missing_character_values.append({"name": person.name, "missing": missing})
    return {
        "project": str(root),
        "seed_file": str(_seed_path(root)),
        "scratch_seed_file": str(_seed_text_path(root)),
        "values_file": str(_values_path(root)),
        "proposed": proposed,
        "confirm_commands": confirm_commands,
        "missing_character_values": missing_character_values,
        "message": "정본 값은 제안 상태입니다. confirm_commands에 표시된 전체 터미널 명령으로 하나씩 확인해 주세요.",
    }


def plan_project(project: str | Path, plan_file: str | Path) -> dict[str, Any]:
    root = _root(project)
    _seed_doc, _seed, store, _checked = _seed_state(root)
    source = _read_json(Path(plan_file).expanduser().resolve())
    raw_scenes = source.get("scenes")
    problems: list[str] = []
    if not isinstance(raw_scenes, list) or not 1 <= len(raw_scenes) <= MAX_SCENES:
        problems.append(f"장면은 1~{MAX_SCENES}개여야 합니다.")
        raw_scenes = []
    scenes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_scenes, start=1):
        before = len(problems)
        if not isinstance(item, Mapping):
            problems.append(f"{index}번째 장면 형식이 맞지 않습니다.")
            continue
        scene_id = item.get("id")
        chapter = item.get("chapter")
        request = item.get("request")
        subjects = item.get("subjects")
        rows = item.get("rows", [])
        beats = item.get("beats")
        if not isinstance(scene_id, str) or _SCENE_ID.fullmatch(scene_id) is None or scene_id in seen:
            problems.append(f"{index}번째 장면 ID가 비었거나 중복되었습니다.")
            continue
        seen.add(scene_id)
        if type(chapter) is not int or chapter < 1:
            problems.append(f"{scene_id}: 장 번호는 양의 정수여야 합니다.")
        if not isinstance(request, str) or not request.strip():
            problems.append(f"{scene_id}: 장면 요청을 적어 주세요.")
        if (
            not isinstance(subjects, list)
            or not subjects
            or any(not isinstance(name, str) or not name.strip() for name in subjects)
        ):
            problems.append(f"{scene_id}: 장면 인물은 이름 목록이어야 합니다.")
            subjects = []
        else:
            subjects = list(dict.fromkeys(name.strip() for name in subjects))
        if not isinstance(rows, list) or any(not isinstance(row, str) or not row.strip() for row in rows):
            problems.append(f"{scene_id}: 시간축 행은 문자열 목록이어야 합니다.")
            rows = []
        else:
            rows = list(dict.fromkeys(row.strip() for row in rows))
        try:
            parsed = parse_beats({"beats": beats}, subjects)
        except (TypeError, ValueError) as exc:
            problems.append(f"{scene_id}: {exc}")
            parsed = ()
        if len(problems) == before:
            scenes.append(
                {
                    "id": scene_id,
                    "chapter": chapter,
                    "request": request.strip(),
                    "subjects": subjects,
                    "rows": rows,
                    "beats": [
                        {
                            "start_state": beat.start_state,
                            "end_state": beat.end_state,
                            "confirmed": True,
                            **({"actors": list(beat.actors)} if beat.actors else {}),
                        }
                        for beat in parsed
                    ],
                }
            )
    if problems or len(scenes) != len(raw_scenes):
        return {"problems": problems or ["장면 계획을 확인해 주세요."], "plan_sha256": None}
    payload = {
        "project_id": _project_id(root),
        "input_sha256": _input_fingerprint(root, store),
        "scenes": scenes,
    }
    plan_hash = _hash_json(payload)
    _write_json(
        _plan_path(root),
        {"schema": PLAN_SCHEMA, "payload": payload, "sha256": plan_hash},
        overwrite=True,
    )
    return {"problems": [], "plan_sha256": plan_hash, "scenes": scenes, "plan_file": str(_plan_path(root))}


def _confirm_plan(project: str | Path, *, words: str, now: str) -> dict[str, Any]:
    root = _root(project)
    _payload, plan_hash, current = _plan_payload(root)
    if not current:
        raise FlowError(
            canon_values_cli.NOT_DONE,
            "씨앗 또는 정본 값이 바뀌어 계획을 다시 만들어야 합니다.",
            {"plan_sha256": plan_hash, "current": False},
        )
    _append_event(root, "plan_confirmed", {"plan_sha256": plan_hash, "words": words, "confirmed_at": now})
    return {"confirmed": plan_hash, "words": words, "at": now, "plan_file": str(_plan_path(root))}


def request_beat(
    project: str | Path, scene_id: str, beat_number: int, *, model: str = "", now: str
) -> dict[str, Any]:
    root = _root(project)
    payload, plan_hash, current, _seed, store, _checked = _load_live_state(root)
    _require_confirmed(root, plan_hash, current)
    if not isinstance(scene_id, str) or _SCENE_ID.fullmatch(scene_id) is None:
        raise FlowError(canon_values_cli.CANNOT_RUN, "장면 ID 형식이 맞지 않습니다.")
    scene_index, scene = _scene(payload, scene_id)
    _beat_value = _beat(beat_number, scene)
    previous_content = _current_previous_content(root, scene_id, beat_number, payload, plan_hash, current)
    previous_content_sha256 = _hash_bytes(previous_content.encode("utf-8"))
    values = canon_values.checkable(store)
    if not values:
        raise FlowError(canon_values_cli.NOT_DONE, "확정된 정본 값이 없어 박자 요청을 만들 수 없습니다.")
    canon_block, context = _context_for(scene, values)
    request_file = _request_path(root, scene_id, beat_number)
    if request_file.exists():
        existing = _read_json(request_file)
        request = existing.get("request")
        if (
            existing.get("schema") == REQUEST_SCHEMA
            and isinstance(request, dict)
            and request.get("plan_sha256") == plan_hash
            and request.get("context", {}).get("block_hash") == context["block_hash"]
            and request.get("previous_content_sha256") == previous_content_sha256
            and request.get("model", "") == model
        ):
            return request
    messages = beat_messages(
        model=model,
        request=scene["request"],
        beats=parse_beats({"beats": scene["beats"]}, scene["subjects"]),
        part_number=beat_number,
        subjects=scene["subjects"],
        rows=scene.get("rows", []),
        values=values,
        canon_block=canon_block,
        plan_index=scene_index,
        plan_total=len(payload["scenes"]),
        plan_mode="new_structure",
        previous_content=previous_content,
    )
    request_id = _hash_json(
        {
            "plan_sha256": plan_hash,
            "scene": scene_id,
            "beat": beat_number,
            "block_hash": context["block_hash"],
            "previous_content_sha256": previous_content_sha256,
            "model": model,
            "created_at": now,
        }
    )
    record = {
        "request_id": request_id,
        "scene": scene_id,
        "beat": beat_number,
        "plan_sha256": plan_hash,
        "model": model,
        "created_at": now,
        "canon_block": canon_block,
        "canon_block_sha256": context["block_hash"],
        "previous_content_sha256": previous_content_sha256,
        "context": context,
        "messages": [dict(item) for item in messages],
        "request_file": str(request_file),
    }
    _write_json(request_file, {"schema": REQUEST_SCHEMA, "request": record}, overwrite=True)
    return record


def _evaluate_text(root: Path, scene_id: str, beat_number: int, text: str, *, now: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise FlowError(canon_values_cli.CANNOT_RUN, "검사할 본문은 비어 있지 않은 UTF-8 텍스트여야 합니다.")
    payload, plan_hash, current, _seed, store, checked_files = _load_live_state(root)
    if not current:
        raise FlowError(canon_values_cli.NOT_DONE, "씨앗 또는 정본 값이 바뀌어 계획을 다시 만들어야 합니다.")
    _require_confirmed(root, plan_hash, current)
    _scene_index, scene = _scene(payload, scene_id)
    beat = _beat(beat_number, scene)
    request = _load_request(root, scene_id, beat_number)
    if request.get("plan_sha256") != plan_hash:
        raise FlowError(canon_values_cli.NOT_DONE, "이 박자 요청이 현재 계획에 속하지 않습니다.")
    previous_content = _current_previous_content(
        root, scene_id, beat_number, payload, plan_hash, current
    )
    previous_content_sha256 = _hash_bytes(previous_content.encode("utf-8"))
    if request.get("previous_content_sha256") != previous_content_sha256:
        raise FlowError(
            canon_values_cli.NOT_DONE,
            "직전 잠금 박자가 바뀌었습니다. 이 박자 요청을 다시 만들어 주세요.",
        )
    values = canon_values.checkable(store)
    facts = produced_with(values, request.get("context"))
    checked = check_canon(
        text,
        store,
        run_id=_project_id(root),
        scope_id=f"{scene_id}-beat-{beat_number}",
        examined_files=checked_files,
        produced_with=facts,
    )
    missing_actors = [name for name in beat.actors if name not in text]
    not_examined = checked["not_examined"]
    possible = checked["possible"]
    conflicts = checked["conflicts"]
    unchecked: list[dict[str, Any]] = list(checked["unchecked"])
    known = {item.get("key") for item in unchecked if isinstance(item, Mapping)}
    for entry in store.entries:
        if entry.status != "CONFIRMED" and entry.key not in known:
            unchecked.append({"key": entry.key, "status": entry.status})
    quality = _quality_advice(text, scene, beat)
    lockable = bool(
        not conflicts
        and not not_examined
        and checked["facts_match"] is True
        and not missing_actors
        and _current_confirmation(root, plan_hash) is not None
    )
    return {
        "scene": scene_id,
        "beat": beat_number,
        "lockable": lockable,
        "conflicts": conflicts,
        "possible": possible,
        "not_examined": not_examined,
        "unchecked": unchecked,
        "missing_actors": missing_actors,
        "facts_match": checked["facts_match"],
        "produced_with": checked["produced_with"],
        "retry": checked["retry"],
        "quality": quality,
        "plan_sha256": plan_hash,
        "canon_block_sha256": request["canon_block_sha256"],
        "request_id": request["request_id"],
        "text_sha256": _hash_bytes(text.encode("utf-8")),
        "beat_file": str(_beat_path(root, scene_id, beat_number)),
        "receipt_file": str(_receipt_path(root, scene_id, beat_number)),
        "checked_at": now,
        "receipt": checked["receipt"],
    }


def check_beat(project: str | Path, scene: str, beat: int, text: str, *, now: str) -> dict[str, Any]:
    if isinstance(text, str):
        # Treat platform line endings as the same prose so a Windows-written
        # text file does not change its fingerprint merely by being checked.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    root = _root(project)
    result = _evaluate_text(root, scene, beat, text, now=now)
    _write_bytes(_beat_path(root, scene, beat), text.encode("utf-8"), overwrite=True)
    _write_json(_receipt_path(root, scene, beat), {"schema": CHECK_SCHEMA, "check": result}, overwrite=True)
    return result


def _current_block_hash(project: Path, scene: Mapping[str, Any]) -> str:
    _seed_doc, _seed, store, _checked = _seed_state(project)
    values = canon_values.checkable(store)
    return context_record(
        build_canon_context(
            values, subjects=scene["subjects"], rows=scene.get("rows", []), max_chars=_CONTEXT_MAX_CHARS
        )
    )["block_hash"]


def _lock_stale_reason(
    project: Path,
    scene_id: str,
    beat_number: int,
    event: Mapping[str, Any],
    plan_payload: Mapping[str, Any],
    plan_hash: str,
    plan_current: bool,
) -> str | None:
    record = event.get("payload")
    if not isinstance(record, Mapping):
        return "lock_record_invalid"
    if not plan_current or record.get("plan_sha256") != plan_hash:
        return "plan_changed"
    try:
        text = _beat_path(project, scene_id, beat_number).read_bytes()
    except OSError:
        return "beat_missing"
    if _hash_bytes(text) != record.get("text_sha256"):
        return "beat_text_changed"
    try:
        _index, scene = _scene(plan_payload, scene_id)
        if _current_block_hash(project, scene) != record.get("canon_block_sha256"):
            return "canon_block_changed"
    except FlowError:
        return "canon_block_unavailable"
    if beat_number > 1:
        previous_number = beat_number - 1
        previous = _latest_lock(project, scene_id, previous_number)
        if previous is None or _lock_stale_reason(
            project,
            scene_id,
            previous_number,
            previous,
            plan_payload,
            plan_hash,
            plan_current,
        ):
            return "previous_beat_unlocked_or_stale"
        try:
            previous_text = _normalize_text(
                _beat_path(project, scene_id, previous_number).read_bytes().decode("utf-8")
            )
        except (OSError, UnicodeError):
            return "previous_beat_unavailable"
        previous_hash = _hash_bytes(previous_text.encode("utf-8"))
        if record.get("previous_content_sha256", _hash_bytes(b"")) != previous_hash:
            return "previous_beat_changed"
    return None


def _stale_next_step(reason: str) -> str:
    if reason == "plan_changed":
        return "현재 계획을 터미널에서 다시 확인한 뒤 이 박자의 요청을 새로 만들고 검사·잠그세요."
    if reason.startswith("previous_beat_"):
        return "앞 박자를 다시 확인하고 잠근 다음 이 박자의 요청을 새로 만들어 검사·잠그세요."
    return "본문과 정본 값을 확인하고 이 박자를 다시 검사한 뒤 잠그세요."


def _status(project: Path) -> dict[str, Any]:
    try:
        payload, plan_hash, current = _plan_payload(project)
    except FlowError as exc:
        if exc.code == canon_values_cli.NOT_DONE:
            return {"plan": None, "beats": [], "stale": []}
        raise
    beats: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for scene in payload["scenes"]:
        scene_id = scene["id"]
        for number, _beat_value in enumerate(scene["beats"], start=1):
            requested = _request_path(project, scene_id, number).is_file()
            checked = _receipt_path(project, scene_id, number).is_file()
            latest = _latest_lock(project, scene_id, number)
            reason = (
                _lock_stale_reason(project, scene_id, number, latest, payload, plan_hash, current)
                if latest is not None
                else None
            )
            is_stale = latest is not None and reason is not None
            if is_stale:
                stale.append({"scene": scene_id, "beat": number})
            beats.append(
                {
                    "scene": scene_id,
                    "beat": number,
                    "requested": requested,
                    "checked": checked,
                    "locked": latest is not None and not is_stale,
                    "stale": is_stale,
                    **({"stale_reason": reason} if reason else {}),
                    **({"next_step": _stale_next_step(reason)} if is_stale and reason else {}),
                }
            )
    result = {
        "plan": {
            "sha256": plan_hash,
            "confirmed": _current_confirmation(project, plan_hash) is not None,
            "current": current,
        },
        "beats": beats,
        "stale": stale,
    }
    if not current:
        result["next_step"] = "씨앗이나 정본 값이 바뀌었습니다. plan을 다시 실행하고 터미널에서 새 계획을 확인하세요."
    return result


def status(project: str | Path) -> dict[str, Any]:
    root = _root(project)
    result = _status(root)
    manuscript = manuscript_status(root)
    result["manuscript"] = manuscript
    if isinstance(manuscript, Mapping) and (manuscript.get("stale") or manuscript.get("added") or manuscript.get("unsupported")):
        result["next_step"] = manuscript.get(
            "next_step", "원본 원고 파일이 snapshot과 다릅니다. novel import --replace로 다시 가져오세요."
        )
    return result


def _lock_beat(project: str | Path, scene: str, beat: int, *, words: str, now: str) -> dict[str, Any]:
    root = _root(project)
    payload, plan_hash, current, _seed, _store, _checked = _load_live_state(root)
    _require_confirmed(root, plan_hash, current)
    _scene_index, scene_data = _scene(payload, scene)
    _beat(beat, scene_data)
    request = _load_request(root, scene, beat)
    beat_file = _beat_path(root, scene, beat)
    try:
        # Keep the exact checked bytes so Windows CRLF text does not get
        # normalized here and immediately make the receipt stale.
        text = beat_file.read_bytes().decode("utf-8")
    except OSError as exc:
        raise FlowError(canon_values_cli.NOT_DONE, "검사한 박자 본문을 찾을 수 없습니다.") from exc
    except UnicodeError as exc:
        raise FlowError(canon_values_cli.CANNOT_RUN, "검사한 박자 본문이 UTF-8이 아닙니다.") from exc
    checked = _evaluate_text(root, scene, beat, text, now=now)
    if not checked["lockable"]:
        raise FlowError(
            canon_values_cli.NOT_DONE,
            "확정된 정본 충돌·읽지 못한 원문·바뀐 계획·빠진 인물을 고친 뒤 다시 검사해 주세요.",
            {"scene": scene, "beat": beat, "lockable": False, "unchecked": checked["unchecked"]},
        )
    _write_json(_receipt_path(root, scene, beat), {"schema": CHECK_SCHEMA, "check": checked}, overwrite=True)
    payload_record = {
        "scene": scene,
        "beat": beat,
        "beat_file": str(beat_file),
        "receipt_file": str(_receipt_path(root, scene, beat)),
        "text_sha256": checked["text_sha256"],
        "plan_sha256": plan_hash,
        "canon_block_sha256": checked["canon_block_sha256"],
        "previous_content_sha256": request["previous_content_sha256"],
        "request_id": request["request_id"],
        "quality": checked["quality"],
        "possible": list(checked["possible"]),
        "unchecked": [
            item["key"]
            for item in checked["unchecked"]
            if isinstance(item, Mapping) and isinstance(item.get("key"), str)
        ],
        "words": words,
        "locked_at": now,
    }
    event = _append_event(root, "beat_locked", payload_record)
    return {"locked": {"scene": scene, "beat": beat, **payload_record}, "artifact_id": event["artifact_id"]}


def _fresh_locked(project: Path, scene_id: str, beat: int, payload: Mapping[str, Any], plan_hash: str, current: bool) -> dict[str, Any] | None:
    latest = _latest_lock(project, scene_id, beat)
    if latest is None:
        return None
    if _lock_stale_reason(project, scene_id, beat, latest, payload, plan_hash, current) is not None:
        return None
    return latest["payload"]


def assemble_scene(project: str | Path, scene_id: str, out: str | Path) -> dict[str, Any]:
    root = _root(project)
    payload, plan_hash, current, _seed, _store, _checked = _load_live_state(root)
    _require_confirmed(root, plan_hash, current)
    if not isinstance(scene_id, str) or _SCENE_ID.fullmatch(scene_id) is None:
        raise FlowError(canon_values_cli.CANNOT_RUN, "장면 ID 형식이 맞지 않습니다.")
    _index, scene = _scene(payload, scene_id)
    output = Path(out).expanduser().resolve()
    if output.exists():
        raise FlowError(canon_values_cli.CANNOT_RUN, f"조립 결과 파일이 이미 있습니다: {output}")
    contents = []
    for number in range(1, len(scene["beats"]) + 1):
        if _fresh_locked(root, scene_id, number, payload, plan_hash, current) is None:
            raise FlowError(canon_values_cli.NOT_DONE, f"{number}번째 박자가 없거나 오래되었습니다.")
        try:
            contents.append(_beat_path(root, scene_id, number).read_text(encoding="utf-8"))
        except OSError as exc:
            raise FlowError(canon_values_cli.NOT_DONE, f"{number}번째 박자 본문을 찾을 수 없습니다.") from exc
    text = "\n\n".join(item.strip() for item in contents) + "\n"
    _write_bytes(output, text.encode("utf-8"), overwrite=False)
    return {"assembled": str(output), "scene": scene_id, "beats": len(contents), "plan_sha256": plan_hash}


def _dispatch(args: argparse.Namespace, *, ask: Callable[[], str], interactive: Callable[[], bool], now: Callable[[], str]) -> tuple[int, dict[str, Any]]:
    if args.command == "seed":
        return canon_values_cli.DONE, seed_project(args.project, args.seed_file)
    if args.command == "import":
        return canon_values_cli.DONE, import_manuscript(
            args.project, args.manuscript, replace=args.replace
        )
    if args.command == "audit":
        result, code = run_audit(args.project)
        return code, result
    if args.command == "plan":
        result = plan_project(args.project, args.plan_file)
        return (canon_values_cli.DONE if not result["problems"] else canon_values_cli.NOT_DONE), result
    if args.command == "plan-confirm":
        if not interactive():
            raise FlowError(canon_values_cli.CANNOT_RUN, "계획 확인은 사람의 터미널에서 해야 합니다.")
        payload, plan_hash, current = _plan_payload(_root(args.project))
        if not current:
            raise FlowError(
                canon_values_cli.NOT_DONE,
                "씨앗 또는 정본 값이 바뀌어 계획을 다시 만들어야 합니다.",
                {"plan_sha256": plan_hash, "current": False},
            )
        out = lambda message: print(message, file=sys.stderr, flush=True)
        out("확인할 장면 계획:")
        for scene in payload["scenes"]:
            out(f"  - {scene['id']}: {len(scene['beats'])}개 박자")
        out(f"  plan_sha256: {plan_hash}")
        words = canon_values_cli._words(ask, out, "계획이 맞으면 본인 말로 한 줄 확인해 주세요:")
        return canon_values_cli.DONE, _confirm_plan(args.project, words=words, now=now())
    if args.command == "beat":
        return canon_values_cli.DONE, request_beat(
            args.project, args.scene, args.beat, model=args.model or "", now=now()
        )
    if args.command == "check":
        try:
            data = args.text_file.expanduser().read_bytes()
        except OSError as exc:
            raise FlowError(canon_values_cli.CANNOT_RUN, f"검사할 본문을 읽을 수 없습니다: {args.text_file}") from exc
        if len(data) > MAX_TEXT_BYTES:
            raise FlowError(canon_values_cli.CANNOT_RUN, "검사할 본문이 크기 제한을 넘었습니다.")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise FlowError(canon_values_cli.CANNOT_RUN, "검사할 본문이 UTF-8이 아닙니다.") from exc
        result = check_beat(args.project, args.scene, args.beat, text, now=now())
        display = {key: value for key, value in result.items() if key != "receipt"}
        return (canon_values_cli.DONE if result["lockable"] else canon_values_cli.NOT_DONE), display
    if args.command == "lock":
        if not interactive():
            raise FlowError(canon_values_cli.CANNOT_RUN, "박자 잠금은 사람의 터미널에서 해야 합니다.")
        out = lambda message: print(message, file=sys.stderr, flush=True)
        project = _root(args.project)
        beat_file = _beat_path(project, args.scene, args.beat)
        try:
            text = beat_file.read_bytes().decode("utf-8")
        except OSError as exc:
            raise FlowError(canon_values_cli.NOT_DONE, "먼저 본문을 검사해 주세요.") from exc
        except UnicodeError as exc:
            raise FlowError(canon_values_cli.CANNOT_RUN, "검사한 박자 본문이 UTF-8이 아닙니다.") from exc
        checked = _evaluate_text(project, args.scene, args.beat, text, now=now())
        if not checked["lockable"]:
            raise FlowError(
                canon_values_cli.NOT_DONE,
                "확정된 정본 충돌·읽지 못한 원문·바뀐 계획·빠진 인물을 고친 뒤 다시 검사해 주세요.",
                {
                    "scene": args.scene,
                    "beat": args.beat,
                    "lockable": False,
                    "conflicts": checked["conflicts"],
                    "unchecked": checked["unchecked"],
                },
            )
        out("확인하지 못한 값과 가능한 충돌을 함께 확인하세요.")
        for item in checked["unchecked"]:
            out(f"  - {item.get('key', '?')}: 확인되지 않음")
        for item in checked["possible"]:
            out(f"  - 가능한 충돌: {json.dumps(item, ensure_ascii=False, sort_keys=True)}")
        words = canon_values_cli._words(ask, out, "이 박자를 잠그려면 본인 말로 한 줄 확인해 주세요:")
        return canon_values_cli.DONE, _lock_beat(args.project, args.scene, args.beat, words=words, now=now())
    if args.command == "status":
        result = status(args.project)
        stale_plan = result.get("plan") is not None and result["plan"].get("current") is False
        manuscript = result.get("manuscript")
        stale_manuscript = isinstance(manuscript, Mapping) and bool(manuscript.get("stale") or manuscript.get("added") or manuscript.get("unsupported"))
        return (
            canon_values_cli.NOT_DONE if result["stale"] or stale_plan or stale_manuscript else canon_values_cli.DONE,
            result,
        )
    if args.command == "assemble":
        return canon_values_cli.DONE, assemble_scene(args.project, args.scene, args.out)
    raise FlowError(canon_values_cli.CANNOT_RUN, "알 수 없는 novel 명령입니다.")


def _refusal_message(result: Mapping[str, Any]) -> str:
    next_step = result.get("next_step")
    if isinstance(next_step, str) and next_step.strip():
        return next_step
    problems = result.get("problems")
    if isinstance(problems, list):
        first = next((item for item in problems if isinstance(item, str) and item.strip()), None)
        if first:
            return first
    if isinstance(result.get("conflicts"), list) and result["conflicts"]:
        return "원고에서 정본 충돌을 찾았습니다. 위치를 확인하고 원고 또는 정본 값을 고친 뒤 audit을 다시 실행하세요."
    if result.get("lockable") is False:
        return "검사 결과를 확인하고 필요한 내용을 고친 뒤 이 박자를 다시 검사하세요."
    if isinstance(result.get("stale"), list) and result["stale"]:
        return "오래된 잠금 박자를 확인하고 다시 잠가야 합니다."
    return "요청을 완료하지 못했습니다."


def _has_actionable_exit_one(command: str, result: Mapping[str, Any]) -> bool:
    """Whether an exit-1 result reports a finding or stale state with a next step."""

    if command == "audit":
        return bool(result.get("conflicts")) or bool(result.get("next_step"))
    if command == "status":
        return bool(result.get("next_step")) or bool(result.get("stale"))
    if command == "plan":
        problems = result.get("problems")
        return isinstance(problems, list) and bool(problems)
    if command == "check":
        return result.get("lockable") is False
    return False


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main(
    argv: list[str] | None = None,
    *,
    ask: Callable[[], str] | None = None,
    interactive: Callable[[], bool] = canon_values_cli.at_a_terminal,
    now: Callable[[], str] = _now,
) -> int:
    parser = _FlowArgumentParser(prog="python editions/novel/synapse_novel.py novel")
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_FlowArgumentParser
    )
    seed = commands.add_parser("seed", help="create a project seed and propose canon values")
    seed.add_argument("--project", type=Path, required=True)
    seed.add_argument("--seed-file", type=Path, required=True)
    manuscript_import = commands.add_parser("import", help="import a written manuscript as a canon-check snapshot")
    manuscript_import.add_argument("--project", type=Path, required=True)
    manuscript_import.add_argument("--manuscript", type=Path, required=True)
    manuscript_import.add_argument("--replace", action="store_true")
    audit = commands.add_parser("audit", help="check an imported manuscript against confirmed canon values")
    audit.add_argument("--project", type=Path, required=True)
    plan = commands.add_parser("plan", help="validate and save a scene plan")
    plan.add_argument("--project", type=Path, required=True)
    plan.add_argument("--plan-file", type=Path, required=True)
    confirm = commands.add_parser("plan-confirm", help="confirm the current plan hash at a terminal")
    confirm.add_argument("--project", type=Path, required=True)
    beat = commands.add_parser("beat", help="record the writing request for the next eligible beat; calls no model")
    beat.add_argument("--project", type=Path, required=True)
    beat.add_argument("--scene", required=True)
    beat.add_argument("--beat", type=int, required=True)
    beat.add_argument("--model", default="")
    check = commands.add_parser("check", help="check one beat of prose against canon and planned actors")
    check.add_argument("--project", type=Path, required=True)
    check.add_argument("--scene", required=True)
    check.add_argument("--beat", type=int, required=True)
    check.add_argument("--text-file", type=Path, required=True)
    lock = commands.add_parser("lock", help="recheck and lock one beat at a terminal")
    lock.add_argument("--project", type=Path, required=True)
    lock.add_argument("--scene", required=True)
    lock.add_argument("--beat", type=int, required=True)
    state = commands.add_parser("status", help="read plan and per-beat requested, checked, locked, and stale state")
    state.add_argument("--project", type=Path, required=True)
    assemble = commands.add_parser("assemble", help="assemble a scene after every beat is locked and current")
    assemble.add_argument("--project", type=Path, required=True)
    assemble.add_argument("--scene", required=True)
    assemble.add_argument("--out", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        code, result = _dispatch(args, ask=ask or input, interactive=interactive, now=now)
    except SystemExit as exc:
        return int(exc.code) if type(exc.code) is int else canon_values_cli.CANNOT_RUN
    except FlowError as exc:
        code = exc.code
        result = dict(exc.result or {})
        result.setdefault("error", str(exc))
    except (OSError, RunError, ValueError, TypeError) as exc:
        code, result = canon_values_cli.CANNOT_RUN, {"error": str(exc)}
    if code == canon_values_cli.NOT_DONE:
        if _has_actionable_exit_one(args.command, result):
            error_message = result.pop("error", None)
            result.setdefault("next_step", error_message or _refusal_message(result))
        else:
            result.setdefault("error", _refusal_message(result))
    elif code == canon_values_cli.CANNOT_RUN:
        result.setdefault("error", _refusal_message(result))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return code


__all__ = [
    "CHECK_SCHEMA",
    "PLAN_SCHEMA",
    "REQUEST_SCHEMA",
    "SEED_SCHEMA",
    "FlowError",
    "assemble_scene",
    "check_beat",
    "main",
    "plan_project",
    "request_beat",
    "seed_project",
    "status",
]

if __name__ == "__main__":
    raise SystemExit(main())
