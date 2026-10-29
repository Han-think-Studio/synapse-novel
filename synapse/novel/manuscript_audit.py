"""Audit an imported manuscript snapshot against confirmed canon, without a model."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from synapse.core import canon_values, canon_values_read
from synapse.core.canon_check_entry import check as check_canon
from synapse.novel.manuscript_import import MANIFEST_NAME
from synapse.novel.manuscript_import import SNAPSHOT_SCHEMA as MANIFEST_SCHEMA

AUDIT_SCHEMA = "synapse-novel-manuscript-audit.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _flow():
    # Reuse the novel flow's project paths, JSON validation and safe atomic writer.
    from synapse.novel import edition_flow

    return edition_flow


def _not_ready(project_id: str | None, next_step: str) -> tuple[dict[str, Any], int]:
    from synapse.core import canon_values_cli

    return (
        {
            "schema_version": AUDIT_SCHEMA,
            "project_id": project_id,
            "chapters": [],
            "conflicts": [],
            "possible": [],
            "unchecked": [],
            "not_examined": [],
            "next_step": next_step,
        },
        canon_values_cli.NOT_DONE,
    )


def _safe_snapshot_path(manifest_path: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative.strip() or "\x00" in relative or ":" in relative:
        raise ValueError("원고 manifest의 snapshot_file 이름이 올바르지 않습니다.")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("원고 snapshot_file은 manuscript 폴더 안의 상대 경로여야 합니다.")
    base = manifest_path.parent.resolve()
    candidate = base.joinpath(*pure.parts)
    cursor = base
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("원고 snapshot_file은 심볼릭 링크일 수 없습니다.")
    resolved = candidate.resolve()
    if resolved == base or base not in resolved.parents:
        raise ValueError("원고 snapshot_file이 manuscript 폴더 밖을 가리킵니다.")
    return resolved


def _read_snapshot(path: Path, maximum: int, expected_sha256: str) -> str:
    try:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"원고 snapshot 파일이 없습니다: {path.name}")
        if path.stat().st_size > maximum:
            raise ValueError(f"원고 snapshot 파일이 크기 제한을 넘었습니다: {path.name}")
        raw = path.read_bytes()
        if len(raw) > maximum:
            raise ValueError(f"원고 snapshot 파일이 크기 제한을 넘었습니다: {path.name}")
    except OSError as exc:
        raise ValueError(f"원고 snapshot 파일을 읽을 수 없습니다: {path.name}") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError(f"원고 snapshot 지문이 manifest와 다릅니다: {path.name}")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError(f"원고 snapshot 파일이 UTF-8이 아닙니다: {path.name}") from exc


def _load_manifest(manifest_path: Path, maximum_json_bytes: int) -> list[dict[str, Any]]:
    flow = _flow()
    if manifest_path.is_symlink():
        raise flow.FlowError(flow.canon_values_cli.CANNOT_RUN, "원고 manifest는 심볼릭 링크일 수 없습니다.")
    document = flow._read_json(manifest_path, maximum=maximum_json_bytes)
    if document.get("schema") != MANIFEST_SCHEMA or not isinstance(document.get("chapters"), list):
        raise flow.FlowError(
            flow.canon_values_cli.CANNOT_RUN,
            "원고 manifest의 형식이 맞지 않습니다.",
        )

    chapters: list[dict[str, Any]] = []
    seen_chapters: set[int] = set()
    seen_scenes: set[str] = set()
    for item in document["chapters"]:
        if not isinstance(item, dict):
            raise flow.FlowError(flow.canon_values_cli.CANNOT_RUN, "원고 manifest의 장 항목이 맞지 않습니다.")
        chapter = item.get("chapter")
        file_name = item.get("file")
        digest = item.get("sha256")
        snapshot_digest = item.get("snapshot_sha256", digest)
        line_unit = item.get("line_unit", "line")
        paragraph_offset = item.get("paragraph_offset", 0)
        title = item.get("title")
        scenes = item.get("scenes")
        if (
            type(chapter) is not int
            or chapter < 1
            or chapter in seen_chapters
            or not isinstance(file_name, str)
            or not file_name
            or Path(file_name).name != file_name
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or not isinstance(snapshot_digest, str)
            or _SHA256.fullmatch(snapshot_digest) is None
            or line_unit not in {"line", "paragraph"}
            or type(paragraph_offset) is not int
            or paragraph_offset < 0
            or (title is not None and not isinstance(title, str))
            or not isinstance(scenes, list)
        ):
            raise flow.FlowError(flow.canon_values_cli.CANNOT_RUN, "원고 manifest의 장 정보가 맞지 않습니다.")
        seen_chapters.add(chapter)
        snapshot_path = _safe_snapshot_path(manifest_path, item.get("snapshot_file"))
        text = _read_snapshot(snapshot_path, flow.MAX_TEXT_BYTES, snapshot_digest)
        source_lines = text.splitlines(keepends=True)
        scene_rows: list[dict[str, Any]] = []
        for scene in scenes:
            if not isinstance(scene, dict):
                raise flow.FlowError(flow.canon_values_cli.CANNOT_RUN, "원고 manifest의 장면 항목이 맞지 않습니다.")
            scene_id = scene.get("id")
            first = scene.get("first_line")
            last = scene.get("last_line")
            if (
                not isinstance(scene_id, str)
                or not scene_id
                or scene_id in seen_scenes
                or type(first) is not int
                or type(last) is not int
                or first < 1
                or last < first
                or last > len(source_lines)
            ):
                raise flow.FlowError(flow.canon_values_cli.CANNOT_RUN, "원고 manifest의 장면 줄 정보가 맞지 않습니다.")
            scene_text = "".join(source_lines[first - 1 : last])
            if not scene_text.strip():
                raise flow.FlowError(flow.canon_values_cli.CANNOT_RUN, f"원고 장면이 비어 있습니다: {scene_id}")
            seen_scenes.add(scene_id)
            scene_rows.append(
                {
                    "id": scene_id,
                    "first_line": first,
                    "last_line": last,
                    "text": scene_text,
                    "line_unit": line_unit,
                    "paragraph_offset": paragraph_offset,
                }
            )
        chapters.append(
            {
                "chapter": chapter,
                "file": file_name,
                "snapshot_file": item["snapshot_file"],
                "scenes": scene_rows,
                "line_unit": line_unit,
                "paragraph_offset": paragraph_offset,
                **({"title": title} if title is not None else {}),
            }
        )
    chapters.sort(key=lambda entry: entry["chapter"])
    if [entry["chapter"] for entry in chapters] != list(range(1, len(chapters) + 1)):
        raise flow.FlowError(flow.canon_values_cli.CANNOT_RUN, "원고 manifest의 장 번호 순서가 맞지 않습니다.")
    return chapters


def _conflict_location(
    conflict: dict[str, Any], scene_text: str, first_line: int
) -> int | None:
    evidence = conflict.get("evidence")
    if not isinstance(evidence, str) or not evidence:
        return None
    offset = scene_text.find(evidence)
    if offset < 0:
        return None
    line_breaks = re.findall(r"\r\n|\r|\n", scene_text[:offset])
    return first_line + len(line_breaks)


def _located_conflicts(
    values: object,
    *,
    chapter: int,
    scene: str,
    file_name: str,
    text: str,
    first_line: int,
    line_unit: str,
    paragraph_offset: int,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    results: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        line = _conflict_location(value, text, first_line)
        results.append(
            {
                **value,
                "chapter": chapter,
                "scene": scene,
                "file": file_name,
                "line": line + paragraph_offset if line is not None else None,
                "line_unit": line_unit,
            }
        )
    return results


def _unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[tuple[object, ...], dict[str, Any]] = {}
    for record in records:
        key = tuple(record.get(name) for name in ("key", "file"))
        found.setdefault(key, record)
    return [found[key] for key in sorted(found, key=lambda pair: (str(pair[0]), str(pair[1])))]


def run_audit(project: str | Path) -> tuple[dict[str, Any], int]:
    """Check every imported scene against stored confirmed canon and persist one audit record."""

    from synapse.core import canon_values_cli

    flow = _flow()
    root = flow._root(project)
    novel = root / "novel"
    manifest_path = novel / "manuscript" / MANIFEST_NAME
    if novel.is_symlink() or (novel / "manuscript").is_symlink():
        raise flow.FlowError(canon_values_cli.CANNOT_RUN, "프로젝트 원고 폴더는 심볼릭 링크일 수 없습니다.")
    if not manifest_path.is_file():
        return _not_ready(
            None,
            "먼저 `novel import --project P --manuscript DIR`로 원고 snapshot을 만드세요.",
        )

    identity_path = novel / "project.json"
    if identity_path.is_symlink():
        raise flow.FlowError(canon_values_cli.CANNOT_RUN, "프로젝트 정체 기록은 심볼릭 링크일 수 없습니다.")
    identity = flow._read_json(identity_path, maximum=flow.MAX_JSON_BYTES)
    project_id = identity.get("project_id")
    if (
        identity.get("schema") != flow.PROJECT_SCHEMA
        or not isinstance(project_id, str)
        or not project_id.strip()
    ):
        raise flow.FlowError(canon_values_cli.CANNOT_RUN, "프로젝트 정체 기록의 형식이 맞지 않습니다.")

    chapters = _load_manifest(manifest_path, flow.MAX_JSON_BYTES)
    if not chapters or not any(chapter["scenes"] for chapter in chapters):
        return _not_ready(
            project_id,
            "감사할 장면이 없습니다. 하나 이상의 비어 있지 않은 장면이 있는 원고를 다시 가져오세요.",
        )
    values_path = novel / "canon-values.json"
    if values_path.is_symlink():
        raise flow.FlowError(canon_values_cli.CANNOT_RUN, "정본 값 파일은 심볼릭 링크일 수 없습니다.")
    if not values_path.exists():
        return _not_ready(
            project_id,
            "정본 값을 제안한 뒤 `canon values confirm`으로 터미널에서 하나 이상 확정하세요.",
        )
    try:
        # As the writing flow does: values whose source file is in the project are revalidated against it and
        # count as examined; a value whose source is elsewhere is checked from what is stored and said to be so.
        store, _claims, examined = canon_values_read.load_against(values_path, novel)
    except (OSError, TypeError, ValueError) as exc:
        raise flow.FlowError(canon_values_cli.CANNOT_RUN, f"정본 값 파일을 읽을 수 없습니다: {exc}") from exc
    if store.project_id != project_id:
        raise flow.FlowError(canon_values_cli.CANNOT_RUN, "정본 값 파일의 project_id가 프로젝트와 다릅니다.")
    if not canon_values.checkable(store):
        return _not_ready(
            project_id,
            "정본 값을 제안한 뒤 `canon values confirm`으로 터미널에서 하나 이상 확정하세요.",
        )

    run_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    chapter_results: list[dict[str, Any]] = []
    check_records: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    possible: list[dict[str, Any]] = []
    unchecked: list[dict[str, Any]] = []
    not_examined: list[dict[str, Any]] = []
    left_out: list[dict[str, Any]] = []

    for chapter in chapters:
        scene_results: list[dict[str, Any]] = []
        for scene in chapter["scenes"]:
            try:
                made = check_canon(
                    scene["text"],
                    store,
                    run_id=run_id,
                    scope_id=scene["id"],
                    examined_files=examined,
                )
            except ValueError as exc:
                raise flow.FlowError(canon_values_cli.CANNOT_RUN, f"장면 정본 검사를 할 수 없습니다: {scene['id']}: {exc}") from exc
            scene_conflicts = _located_conflicts(
                made["conflicts"],
                chapter=chapter["chapter"],
                scene=scene["id"],
                file_name=chapter["file"],
                text=scene["text"],
                first_line=scene["first_line"],
                line_unit=chapter["line_unit"],
                paragraph_offset=chapter["paragraph_offset"],
            )
            scene_possible = _located_conflicts(
                made["possible"],
                chapter=chapter["chapter"],
                scene=scene["id"],
                file_name=chapter["file"],
                text=scene["text"],
                first_line=scene["first_line"],
                line_unit=chapter["line_unit"],
                paragraph_offset=chapter["paragraph_offset"],
            )
            conflicts.extend(scene_conflicts)
            possible.extend(scene_possible)
            unchecked.extend(
                {
                    **item,
                    "chapter": chapter["chapter"],
                    "scene": scene["id"],
                }
                for item in made["unchecked"]
                if isinstance(item, dict)
            )
            not_examined.extend(item for item in made["not_examined"] if isinstance(item, dict))
            left_out.extend(item for item in made["left_out"] if isinstance(item, dict))
            scene_results.append(
                {
                    "scene": scene["id"],
                    "verdict": "FAIL" if scene_conflicts else "PASS",
                    "conflicts": len(scene_conflicts),
                    "possible": len(scene_possible),
                }
            )
            check_records.append(
                {
                    "chapter": chapter["chapter"],
                    "scene": scene["id"],
                    "receipt": made["receipt"],
                }
            )
        chapter_results.append(
            {
                "chapter": chapter["chapter"],
                "file": chapter["file"],
                "line_unit": chapter["line_unit"],
                **({"title": chapter["title"]} if "title" in chapter else {}),
                "scenes": scene_results,
            }
        )

    not_examined = _unique_records(not_examined)
    left_out = _unique_records(left_out)
    audit_folder = novel / "audits"
    if audit_folder.is_symlink():
        raise flow.FlowError(canon_values_cli.CANNOT_RUN, "감사 기록 폴더는 심볼릭 링크일 수 없습니다.")
    timestamp = created_at.replace("-", "").replace(":", "")
    record_path = audit_folder / f"audit-{timestamp}-{run_id}.json"
    record = {
        "schema_version": AUDIT_SCHEMA,
        "project_id": project_id,
        "run_id": run_id,
        "created_at": created_at,
        "chapters": chapter_results,
        "conflicts": conflicts,
        "possible": possible,
        "unchecked": unchecked,
        "not_examined": not_examined,
        "left_out": left_out,
        "checks": check_records,
        "model_invoked": False,
        "canonical_mutation": False,
    }
    try:
        flow._write_json(record_path, record, overwrite=False)
    except flow.FlowError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise flow.FlowError(canon_values_cli.CANNOT_RUN, f"감사 기록을 저장할 수 없습니다: {exc}") from exc

    result = {
        **record,
        "record": str(record_path),
        "note": "저장된 정본 값과 원고 snapshot을 비교했습니다. 이 감사는 정본을 확정하거나 승인하지 않습니다.",
    }
    return result, canon_values_cli.NOT_DONE if conflicts else canon_values_cli.DONE


__all__ = ["AUDIT_SCHEMA", "run_audit"]
