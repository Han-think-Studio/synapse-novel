"""Import a pre-existing manuscript as a bounded, source-linked snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from synapse.core import canon_values

SNAPSHOT_SCHEMA = "synapse-novel-manuscript-snapshot.v1"
MANIFEST_NAME = "manifest.json"
_SCENE_BREAK = re.compile(r"^[ \t]*[*\-=~#◇◆](?:[ \t]*[*\-=~#◇◆]){2,}[ \t]*$")
_NATURAL_PARTS = re.compile(r"([0-9]+)")


class ManuscriptImportError(ValueError):
    """A caller-facing manuscript import or status problem."""


def _natural_key(name: str) -> tuple[object, ...]:
    parts: list[object] = []
    for part in _NATURAL_PARTS.split(name):
        if part.isascii() and part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part.casefold()))
    return (*parts, (2, name.casefold()), (3, name))


def _hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    try:
        flags = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(flags & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2))


def _scene_ranges(text: str, chapter: int) -> list[dict[str, Any]]:
    lines = text.splitlines()
    boundaries = [index for index, line in enumerate(lines, start=1) if _SCENE_BREAK.fullmatch(line)]
    ranges: list[tuple[int, int]] = []
    first = 1
    for boundary in boundaries:
        ranges.append((first, boundary - 1))
        first = boundary + 1
    ranges.append((first, len(lines)))

    scenes: list[dict[str, Any]] = []
    for start, end in ranges:
        populated = [line_no for line_no in range(start, end + 1) if lines[line_no - 1].strip()]
        if not populated:
            continue
        scene_number = len(scenes) + 1
        scenes.append(
            {
                "id": f"ch{chapter}s{scene_number}",
                "first_line": populated[0],
                "last_line": populated[-1],
            }
        )
    return scenes


def _chapter_files(folder: Path) -> tuple[list[Path], list[str]]:
    """The chapter files directly in ``folder`` in natural order, and the names of the other files."""

    try:
        entries = list(folder.iterdir())
    except OSError as exc:
        raise ManuscriptImportError(f"원고 폴더를 읽을 수 없습니다: {folder}") from exc

    files: list[Path] = []
    skipped: list[str] = []
    for path in entries:
        if _hidden(path):
            continue
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False
        if not is_file:
            continue
        if path.suffix.lower() in {".txt", ".md"}:
            files.append(path)
        else:
            skipped.append(path.name)

    files.sort(key=lambda path: _natural_key(path.name))
    skipped.sort(key=_natural_key)
    return files, skipped


def _read_source_folder(manuscript: str | Path, *, max_text_bytes: int, max_scenes: int) -> tuple[Path, list[dict[str, Any]], list[str]]:
    folder = Path(manuscript).expanduser().resolve()
    if not folder.is_dir():
        raise ManuscriptImportError(f"원고 폴더가 없습니다: {folder}")
    files, skipped = _chapter_files(folder)
    if not files:
        raise ManuscriptImportError(f"가져올 .txt 또는 .md 장 파일이 없습니다: {folder}")

    chapters: list[dict[str, Any]] = []
    scene_count = 0
    for chapter_number, path in enumerate(files, start=1):
        try:
            with path.open("rb") as stream:
                data = stream.read(max_text_bytes + 1)
        except OSError as exc:
            raise ManuscriptImportError(f"원고 파일을 읽을 수 없습니다: {path.name}") from exc
        if len(data) > max_text_bytes:
            raise ManuscriptImportError(
                f"원고 파일이 흐름의 크기 제한({max_text_bytes} bytes)을 넘었습니다: {path.name}"
            )
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ManuscriptImportError(f"원고 파일이 UTF-8이 아닙니다: {path.name}") from exc

        scenes = _scene_ranges(text, chapter_number)
        if not scenes:
            raise ManuscriptImportError(f"원고 파일에 확인할 장면이 없습니다: {path.name}")
        scene_count += len(scenes)
        if scene_count > max_scenes:
            raise ManuscriptImportError(
                f"원고의 장면 수가 흐름의 제한({max_scenes})을 넘었습니다: {path.name}"
            )
        chapters.append(
            {
                "file": path.name,
                "chapter": chapter_number,
                "sha256": hashlib.sha256(data).hexdigest(),
                "snapshot_file": f"chapters/{chapter_number:04d}.txt",
                "scenes": scenes,
                "_bytes": data,
            }
        )
    return folder, chapters, skipped


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ManuscriptImportError(f"파일을 읽을 수 없습니다: {path}") from exc
    if len(data) > 4 * 1024 * 1024:
        raise ManuscriptImportError(f"파일이 크기 제한을 넘었습니다: {path}")
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManuscriptImportError(f"JSON 형식이 맞지 않습니다: {path}") from exc
    if not isinstance(value, dict):
        raise ManuscriptImportError(f"JSON 객체가 필요합니다: {path}")
    return value


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ManuscriptImportError(f"파일이 이미 있습니다: {path}") from exc
    except OSError as exc:
        if created:
            path.unlink(missing_ok=True)
        raise ManuscriptImportError(f"파일을 저장할 수 없습니다: {path}") from exc


def _project_state(project: Path) -> tuple[str | None, canon_values.ValueStore | None]:
    from synapse.novel.edition_flow import PROJECT_SCHEMA

    identity_path = project / "novel" / "project.json"
    values_path = project / "novel" / "canon-values.json"
    project_id: str | None = None
    store: canon_values.ValueStore | None = None
    if identity_path.exists():
        identity = _read_json(identity_path)
        value = identity.get("project_id")
        if identity.get("schema") != PROJECT_SCHEMA or not isinstance(value, str) or not value.strip():
            raise ManuscriptImportError("프로젝트 정체 기록의 형식이 맞지 않습니다.")
        project_id = value
    if values_path.exists():
        try:
            store = canon_values.load(values_path, (), only_files=())
        except (OSError, TypeError, ValueError) as exc:
            raise ManuscriptImportError(f"기존 정본 값 파일을 읽을 수 없습니다: {exc}") from exc
        if project_id is not None and store.project_id != project_id:
            raise ManuscriptImportError("프로젝트 정체 기록과 정본 값의 project_id가 다릅니다.")
        project_id = project_id or store.project_id
    return project_id, store


def _manifest(project: Path) -> dict[str, Any] | None:
    snapshot = project / "novel" / "manuscript"
    manifest_path = snapshot / MANIFEST_NAME
    if not snapshot.exists():
        return None
    if not manifest_path.is_file():
        raise ManuscriptImportError(f"원고 snapshot manifest를 찾을 수 없습니다: {manifest_path}")
    manifest = _read_json(manifest_path)
    if manifest.get("schema") != SNAPSHOT_SCHEMA:
        raise ManuscriptImportError(f"원고 manifest의 스키마가 맞지 않습니다: {manifest_path}")
    source_dir = manifest.get("source_dir")
    chapters = manifest.get("chapters")
    if not isinstance(source_dir, str) or not isinstance(chapters, list):
        raise ManuscriptImportError(f"원고 manifest의 형식이 맞지 않습니다: {manifest_path}")
    return manifest


def _prepare_snapshot(stage: Path, source_dir: Path, chapters: list[dict[str, Any]]) -> dict[str, Any]:
    manifest_chapters: list[dict[str, Any]] = []
    for item in chapters:
        snapshot_file = item["snapshot_file"]
        _write_new(stage / snapshot_file, item["_bytes"])
        manifest_chapters.append(
            {
                "file": item["file"],
                "chapter": item["chapter"],
                "sha256": item["sha256"],
                "snapshot_file": snapshot_file,
                "scenes": item["scenes"],
            }
        )
    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "source_dir": str(source_dir),
        "chapters": manifest_chapters,
    }
    _write_new(stage / MANIFEST_NAME, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return manifest


def import_manuscript(
    project: str | Path,
    manuscript: str | Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Copy text chapters into a project snapshot without changing their source folder."""

    from synapse.novel.edition_flow import MAX_SCENES, MAX_TEXT_BYTES, PROJECT_SCHEMA

    root = Path(project).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ManuscriptImportError(f"프로젝트 경로가 폴더가 아닙니다: {root}")
    if not root.exists() and not root.parent.is_dir():
        raise ManuscriptImportError(f"프로젝트 상위 폴더가 없습니다: {root.parent}")

    source_dir, chapters, skipped = _read_source_folder(
        manuscript, max_text_bytes=MAX_TEXT_BYTES, max_scenes=MAX_SCENES
    )
    snapshot = root / "novel" / "manuscript"
    had_snapshot = snapshot.exists()
    if had_snapshot and not replace:
        raise ManuscriptImportError("원고 snapshot이 이미 있습니다. 교체하려면 --replace를 사용하세요.")
    if had_snapshot and not snapshot.is_dir():
        raise ManuscriptImportError(f"원고 snapshot 경로가 폴더가 아닙니다: {snapshot}")

    project_id, existing_store = _project_state(root)
    identity_path = root / "novel" / "project.json"
    values_path = root / "novel" / "canon-values.json"
    if had_snapshot and (not identity_path.is_file() or not values_path.is_file()):
        raise ManuscriptImportError("기존 원고 snapshot을 교체하려면 프로젝트 정체와 정본 값 파일이 있어야 합니다.")
    if project_id is None:
        project_id = "novel-" + uuid.uuid4().hex
    if existing_store is None:
        existing_store = canon_values.new_store(project_id, hashlib.sha256(b"").hexdigest())

    parent_was_present = root.exists()
    novel_folder = root / "novel"
    created_paths: list[Path] = []
    stage = novel_folder / f".manuscript-stage-{uuid.uuid4().hex}"
    backup = novel_folder / f".manuscript-backup-{uuid.uuid4().hex}"
    published = False
    try:
        root.mkdir(exist_ok=True)
        novel_folder.mkdir(exist_ok=True)
        stage.mkdir()
        _prepare_snapshot(stage, source_dir, chapters)

        if not identity_path.exists():
            identity = {"schema": PROJECT_SCHEMA, "project_id": project_id}
            _write_new(identity_path, (json.dumps(identity, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            created_paths.append(identity_path)
        if not values_path.exists():
            canon_values.save(existing_store, values_path)
            created_paths.append(values_path)

        if had_snapshot:
            snapshot.rename(backup)
            try:
                stage.rename(snapshot)
                published = True
            except OSError:
                backup.rename(snapshot)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            stage.rename(snapshot)
            published = True
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        if not published:
            for path in reversed(created_paths):
                path.unlink(missing_ok=True)
            if not parent_was_present:
                try:
                    novel_folder.rmdir()
                    root.rmdir()
                except OSError:
                    pass
        raise

    return {
        "project": str(root),
        "project_id": project_id,
        "snapshot": str(snapshot),
        "manifest": str(snapshot / MANIFEST_NAME),
        "chapters": [
            {
                "file": item["file"],
                "chapter": item["chapter"],
                "sha256": item["sha256"],
                "scenes": item["scenes"],
            }
            for item in chapters
        ],
        "skipped": skipped,
        "message": "원고 snapshot을 가져왔습니다. 원본 폴더는 변경하지 않았습니다.",
    }


def _last_audit(project: Path) -> dict[str, Any] | None:
    audits = project / "novel" / "audits"
    if not audits.is_dir():
        return None
    try:
        records = sorted(
            (path for path in audits.iterdir() if path.is_file() and path.suffix.lower() == ".json"),
            key=lambda path: path.name,
        )
    except OSError:
        return None
    if not records:
        return None
    path = records[-1]
    result: dict[str, Any] = {"path": str(path)}
    try:
        record = _read_json(path)
    except ManuscriptImportError:
        return result
    if isinstance(record.get("run_id"), str):
        result["run_id"] = record["run_id"]
    if isinstance(record.get("created_at"), str):
        result["timestamp"] = record["created_at"]
    return result


def manuscript_status(project: str | Path) -> dict[str, Any] | None:
    """Return imported chapter metadata, changed or missing source files, and the latest audit."""

    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise ManuscriptImportError(f"프로젝트 폴더가 없습니다: {root}")
    from synapse.novel.edition_flow import MAX_TEXT_BYTES

    manifest = _manifest(root)
    if manifest is None:
        return None
    source_dir = Path(manifest["source_dir"])
    stale: list[str] = []
    chapters: list[dict[str, Any]] = []
    for item in manifest["chapters"]:
        if not isinstance(item, dict):
            raise ManuscriptImportError("원고 manifest의 장 항목 형식이 맞지 않습니다.")
        name = item.get("file")
        chapter = item.get("chapter")
        sha256 = item.get("sha256")
        scenes = item.get("scenes")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or type(chapter) is not int
            or chapter < 1
            or not isinstance(sha256, str)
            or not isinstance(scenes, list)
        ):
            raise ManuscriptImportError("원고 manifest의 장 항목 형식이 맞지 않습니다.")
        source_file = source_dir / name
        try:
            with source_file.open("rb") as stream:
                current = stream.read(MAX_TEXT_BYTES + 1)
            current_sha = hashlib.sha256(current).hexdigest() if len(current) <= MAX_TEXT_BYTES else None
        except OSError:
            current_sha = None
        if current_sha != sha256:
            stale.append(name)
        chapters.append({"file": name, "chapter": chapter, "scenes": scenes})
    imported = {item["file"] for item in chapters}
    added: list[str] = []
    if source_dir.is_dir():
        try:
            added = [path.name for path in _chapter_files(source_dir)[0] if path.name not in imported]
        except ManuscriptImportError:
            added = []
    result = {"chapters": chapters, "stale": stale, "added": added, "last_audit": _last_audit(root)}
    if stale or added:
        result["next_step"] = (
            "원고 원본이 바뀌었거나, 없어졌거나, 새 장이 생겼습니다. novel import --replace로 snapshot을 갱신하세요."
        )
    return result


__all__ = [
    "MANIFEST_NAME",
    "SNAPSHOT_SCHEMA",
    "ManuscriptImportError",
    "import_manuscript",
    "manuscript_status",
]
