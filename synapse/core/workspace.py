"""Safe workspace inspection and file Proposal boundaries.

The workspace layer is deliberately deterministic and domain-neutral.  It can
inspect a scaffold, explain which files are affected by an edit, and create a
Proposal for one file.  Nothing is written until the caller supplies an
explicit approval token to :func:`apply_file_proposal`.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from synapse.core import impact_review
from synapse.core.scaffold import ScaffoldPlan


class WorkspaceError(ValueError):
    """Raised when a workspace request is invalid or cannot be applied safely."""


_MAX_READ_BYTES = 512 * 1024
_STATUSES = {"MISSING", "EMPTY", "READY", "BINARY", "TOO_LARGE", "UNSAFE"}
SUBSTANTIVE_CONTENT_CHECKER_VERSION = 1


def _normalise_relative_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or ":" in raw:
        raise WorkspaceError(f"안전하지 않은 workspace 파일 경로입니다: {value}")
    parts = tuple(part for part in raw.split("/") if part not in {""})
    if not parts or any(part in {".", ".."} for part in parts):
        raise WorkspaceError(f"안전하지 않은 workspace 파일 경로입니다: {value}")
    return "/".join(parts)


def _resolve_workspace(value: str | Path | None) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise WorkspaceError("workspace 폴더를 지정해 주세요.")
    try:
        return Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError(f"workspace 경로를 읽지 못했습니다: {exc}") from exc


def _safe_target(root: Path, relative: str) -> Path:
    path = (root / Path(*_normalise_relative_path(relative).split("/"))).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkspaceError(f"workspace 경계를 벗어난 파일입니다: {relative}") from exc
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_proposal_diff(before: bytes | None, after: str) -> dict[str, object]:
    """Return a deterministic line-range diff without copying file text."""

    before_lines = (
        before.decode("utf-8").splitlines(keepends=True) if before is not None else []
    )
    after_lines = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(
        a=before_lines,
        b=after_lines,
        autojunk=False,
    )
    hunks: list[dict[str, object]] = []
    added = 0
    removed = 0
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        added += after_end - after_start
        removed += before_end - before_start
        hunks.append(
            {
                "kind": {
                    "insert": "ADD",
                    "delete": "REMOVE",
                    "replace": "REPLACE",
                }[tag],
                "before_range": [before_start, before_end],
                "after_range": [after_start, after_end],
            }
        )
    return {
        "schema_version": "workspace.proposal-diff.v1",
        "range_basis": "zero_based_half_open_lines",
        "base_exists": before is not None,
        "before_line_count": len(before_lines),
        "after_line_count": len(after_lines),
        "added_line_count": added,
        "removed_line_count": removed,
        "changed": before is None or bool(hunks),
        "hunks": hunks,
    }


def _starter_content(plan: ScaffoldPlan, path: str, title: str, purpose: str) -> str:
    """Return a small deterministic writing guide, never an inferred answer."""

    if path == "synapse.project.yaml":
        return (
            "# Synapse project manifest\n"
            f"name: {plan.project_name}\n"
            f"slug: {plan.project_slug}\n"
            f"preset: {plan.preset.id}\n"
            f"scaffold_id: {plan.id}\n"
            "status: PROPOSED\n"
        )
    if path == "README.md":
        return f"# {plan.project_name}\n\n{purpose}\n\n## 시작점\n\n{plan.idea}\n"
    return f"# {title}\n\n> {purpose}\n\n## 기록\n\n"


_PLACEHOLDER_LINE = re.compile(
    r"^(?:[-*+]\s*)?(?:\[[ xX]\]\s*)?(?:todo|tbd|fixme|placeholder|"
    r"fill in|write here|to be filled|lorem ipsum|내용 입력|여기에 작성|"
    r"작성 예정|추가 예정|미정|미작성|아직 없음)(?:\s*[:：.!?…-].*)?$",
    re.IGNORECASE,
)


def explain_substantive_workspace_content(
    content: object,
    *,
    scaffold_purpose: str,
) -> dict[str, object]:
    """Explain the canonical authored-content predicate without changing its result."""

    criterion = (
        "canonical substantive-content check: authored payload and non-empty "
        "Markdown leaf sections"
    )
    if not isinstance(content, str):
        return {
            "status": "INVALID_INPUT",
            "substantive": False,
            "criterion": criterion,
            "reason": "The content value is not a UTF-8 text string.",
        }
    headings: list[dict[str, object]] = []
    heading_stack: list[dict[str, object]] = []
    has_payload = False
    ignored_guidance = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"(?:-{3,}|_{3,}|\*{3,}|={3,}|`{3,}|~{3,})", stripped):
            continue
        if re.fullmatch(r"(?:\|\s*:?-{3,}:?\s*)+\|?", stripped):
            continue
        heading = re.match(r"^(#{1,6})(?:\s+|$)(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            while heading_stack and int(heading_stack[-1]["level"]) >= level:
                heading_stack.pop()
            entry: dict[str, object] = {"level": level, "content": False, "child": False}
            if heading_stack:
                heading_stack[-1]["child"] = True
            headings.append(entry)
            heading_stack.append(entry)
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            ignored_guidance = True
            continue
        if _PLACEHOLDER_LINE.fullmatch(stripped):
            ignored_guidance = True
            continue
        if re.fullmatch(r"(?:[-*+]\s*)?(?:\[\s*[xX]?\s*\])?", stripped):
            ignored_guidance = True
            continue
        if re.fullmatch(r"[|:\-\s]+", stripped):
            ignored_guidance = True
            continue
        if re.match(r"^(?:[-*+]\s*)?[\w.-]+\s*:\s*(?:\"\"|''|null|none)?\s*$", stripped, re.IGNORECASE):
            ignored_guidance = True
            continue
        if stripped.startswith(">") and scaffold_purpose.strip():
            quoted_text = stripped[1:].lstrip()
            if quoted_text == scaffold_purpose.strip():
                ignored_guidance = True
                continue
        has_payload = True
        if heading_stack:
            heading_stack[-1]["content"] = True
    if not has_payload:
        if ignored_guidance:
            status = "TEMPLATE"
            reason = "Only scaffold guidance or placeholder content was found."
        elif headings:
            status = "TITLE_ONLY"
            reason = "Headings exist, but no authored body follows them."
        else:
            status = "EMPTY"
            reason = "The file has no authored text."
        return {
            "status": status,
            "substantive": False,
            "criterion": criterion,
            "reason": reason,
        }
    leaf_sections = [heading for heading in headings if not heading["child"]]
    if not all(bool(heading["content"]) for heading in leaf_sections):
        return {
            "status": "EMPTY_SECTION",
            "substantive": False,
            "criterion": criterion,
            "reason": "One or more Markdown leaf sections has a heading but no authored body.",
        }
    return {
        "status": "PASS",
        "substantive": True,
        "criterion": criterion,
        "reason": "Authored text exists and every Markdown leaf section has content.",
    }


def has_substantive_workspace_content(content: object, *, scaffold_purpose: str) -> bool:
    """Detect authored payload and reject empty Markdown leaf sections."""

    return bool(
        explain_substantive_workspace_content(
            content,
            scaffold_purpose=scaffold_purpose,
        )["substantive"]
    )


@dataclass(frozen=True, slots=True)
class WorkspaceFileState:
    path: str
    title: str
    purpose: str
    required: bool
    status: str
    exists: bool
    size: int
    sha256: str | None
    content: str
    suggested_content: str
    detail: str = ""
    substantive: bool = False

    def __post_init__(self) -> None:
        if not self.path.strip() or self.status not in _STATUSES:
            raise WorkspaceError("WorkspaceFileState path/status가 잘못되었습니다.")
        if self.size < 0:
            raise WorkspaceError("WorkspaceFileState size는 0 이상이어야 합니다.")

    def to_record(self) -> dict[str, object]:
        return {
            "path": self.path,
            "title": self.title,
            "purpose": self.purpose,
            "required": self.required,
            "status": self.status,
            "exists": self.exists,
            "size": self.size,
            "sha256": self.sha256,
            "content": self.content,
            "suggested_content": self.suggested_content,
            "detail": self.detail,
            "substantive": self.substantive,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    workspace_path: str
    status: str
    exists: bool
    is_directory: bool
    writable: bool
    plan_id: str
    files: tuple[WorkspaceFileState, ...]
    progress: Mapping[str, int]
    canonical_mutation: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"MISSING", "READY", "INVALID"}:
            raise WorkspaceError(f"지원하지 않는 workspace snapshot 상태입니다: {self.status}")
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "progress", MappingProxyType(dict(self.progress)))
        if self.canonical_mutation:
            raise WorkspaceError("workspace preview는 Canonical State를 변경할 수 없습니다.")

    def to_record(self) -> dict[str, object]:
        return {
            "workspace_path": self.workspace_path,
            "status": self.status,
            "exists": self.exists,
            "is_directory": self.is_directory,
            "writable": self.writable,
            "plan_id": self.plan_id,
            "files": [item.to_record() for item in self.files],
            "progress": dict(self.progress),
            "canonical_mutation": False,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceChange:
    """One externally observed change between two read-only workspace scans."""

    path: str
    kind: str
    previous_sha256: str | None
    current_sha256: str | None

    def __post_init__(self) -> None:
        _normalise_relative_path(self.path)
        if self.kind not in {"ADDED", "MODIFIED", "REMOVED"}:
            raise WorkspaceError(f"지원하지 않는 workspace 변경 종류입니다: {self.kind}")

    def to_record(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "previous_sha256": self.previous_sha256,
            "current_sha256": self.current_sha256,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceObservation:
    """Read-only scan plus the deterministic changes since the caller's hashes."""

    snapshot: WorkspaceSnapshot
    changes: tuple[WorkspaceChange, ...]
    canonical_mutation: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", tuple(sorted(self.changes, key=lambda item: item.path)))
        if self.canonical_mutation:
            raise WorkspaceError("workspace observation은 Canonical State를 변경할 수 없습니다.")

    def to_record(self) -> dict[str, object]:
        return {
            "workspace": self.snapshot.to_record(),
            "changes": [change.to_record() for change in self.changes],
            "canonical_mutation": False,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceImpact:
    plan_id: str
    changed_path: str
    affected_paths: tuple[str, ...]
    reasons: Mapping[str, str]
    card_lenses: tuple[str, ...]
    card_ids: tuple[str, ...]
    event_kind: str = "file_modified"
    status: str = "PROPOSED"
    canonical_mutation: bool = False

    def __post_init__(self) -> None:
        _normalise_relative_path(self.changed_path)
        if self.status != "PROPOSED" or self.canonical_mutation:
            raise WorkspaceError("workspace 영향 분석은 PROPOSED 읽기 전용이어야 합니다.")
        object.__setattr__(self, "affected_paths", tuple(self.affected_paths))
        object.__setattr__(self, "reasons", MappingProxyType(dict(self.reasons)))
        object.__setattr__(self, "card_lenses", tuple(dict.fromkeys(self.card_lenses)))
        object.__setattr__(self, "card_ids", tuple(dict.fromkeys(self.card_ids)))

    def to_record(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "changed_path": self.changed_path,
            "affected_paths": list(self.affected_paths),
            "reasons": dict(self.reasons),
            "card_lenses": list(self.card_lenses),
            "card_ids": list(self.card_ids),
            "event_kind": self.event_kind,
            "status": self.status,
            "canonical_mutation": False,
        }


@dataclass(frozen=True, slots=True)
class FileProposal:
    id: str
    plan_id: str
    workspace_path: str
    path: str
    content: str
    content_sha256: str
    base_sha256: str | None
    owner: str
    source_id: str
    rationale: str
    impact: WorkspaceImpact
    create_workspace: bool = False
    status: str = "PROPOSED"
    canonical_mutation: bool = False
    diff: Mapping[str, object] = field(default_factory=dict)
    protected_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.plan_id.strip() or not self.owner.strip() or not self.source_id.strip():
            raise WorkspaceError("FileProposal id/plan/owner/source가 필요합니다.")
        _normalise_relative_path(self.path)
        if self.impact.plan_id != self.plan_id or self.impact.changed_path != self.path:
            raise WorkspaceError("FileProposal과 영향 분석의 plan/path binding이 일치하지 않습니다.")
        if self.content_sha256 != _sha256(self.content.encode("utf-8")):
            raise WorkspaceError("FileProposal content checksum이 일치하지 않습니다.")
        if self.status not in {"PROPOSED", "APPLIED"} or self.canonical_mutation:
            raise WorkspaceError("FileProposal 상태 또는 Canonical 경계가 잘못되었습니다.")
        if not isinstance(self.diff, Mapping):
            raise WorkspaceError("FileProposal diff가 mapping이 아닙니다.")
        if isinstance(self.protected_paths, (str, bytes)):
            raise WorkspaceError("FileProposal 보호 경로는 경로 목록이어야 합니다.")
        try:
            protected = tuple(
                sorted({_normalise_relative_path(path) for path in self.protected_paths})
            )
        except (TypeError, WorkspaceError) as exc:
            raise WorkspaceError("FileProposal 보호 경로가 잘못되었습니다.") from exc
        if self.path in protected:
            raise WorkspaceError("FileProposal 대상은 보호 경로에 포함될 수 없습니다.")
        object.__setattr__(self, "diff", MappingProxyType(dict(self.diff)))
        object.__setattr__(self, "protected_paths", protected)

    @property
    def approval_target(self) -> dict[str, object]:
        """Return the immutable identity an external approval must bind to."""

        return {
            "schema_version": "workspace.proposal-approval.v1",
            "proposal_id": self.id,
            "plan_id": self.plan_id,
            "path": self.path,
            "base_sha256": self.base_sha256,
            "content_sha256": self.content_sha256,
            "protected_paths": list(self.protected_paths),
        }

    def to_record(self, *, include_content: bool = True) -> dict[str, object]:
        record: dict[str, object] = {
            "id": self.id,
            "plan_id": self.plan_id,
            "workspace_path": self.workspace_path,
            "path": self.path,
            "content_sha256": self.content_sha256,
            "base_sha256": self.base_sha256,
            "owner": self.owner,
            "source_id": self.source_id,
            "rationale": self.rationale,
            "impact": self.impact.to_record(),
            "diff": dict(self.diff),
            "protected_paths": list(self.protected_paths),
            "approval_target": self.approval_target,
            "create_workspace": self.create_workspace,
            "status": self.status,
            "canonical_mutation": False,
        }
        if include_content:
            record["content"] = self.content
        return record


@dataclass(frozen=True, slots=True)
class WorkspaceApplyResult:
    proposal_id: str
    workspace_path: str
    path: str
    sha256: str
    status: str = "APPLIED"
    canonical_mutation: bool = False

    def to_record(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "workspace_path": self.workspace_path,
            "path": self.path,
            "sha256": self.sha256,
            "status": self.status,
            "canonical_mutation": False,
        }


def _file_state(plan: ScaffoldPlan, root: Path, item: Any) -> WorkspaceFileState:
    path = _normalise_relative_path(item.path)
    suggested = _starter_content(plan, path, item.title, item.purpose)
    try:
        target = _safe_target(root, path)
    except WorkspaceError as exc:
        return WorkspaceFileState(
            path=path, title=item.title, purpose=item.purpose, required=item.required,
            status="UNSAFE", exists=False, size=0, sha256=None, content="",
            suggested_content=suggested, detail=str(exc),
        )
    if not target.exists():
        return WorkspaceFileState(
            path=path, title=item.title, purpose=item.purpose, required=item.required,
            status="MISSING", exists=False, size=0, sha256=None, content="",
            suggested_content=suggested, detail="아직 파일이 없습니다.",
        )
    if not target.is_file():
        return WorkspaceFileState(
            path=path, title=item.title, purpose=item.purpose, required=item.required,
            status="UNSAFE", exists=True, size=0, sha256=None, content="",
            suggested_content=suggested, detail="파일이 아닌 경로입니다.",
        )
    try:
        data = target.read_bytes()
    except OSError as exc:
        return WorkspaceFileState(
            path=path, title=item.title, purpose=item.purpose, required=item.required,
            status="UNSAFE", exists=True, size=0, sha256=None, content="",
            suggested_content=suggested, detail=f"읽을 수 없습니다: {exc}",
        )
    digest = _sha256(data)
    if len(data) > _MAX_READ_BYTES:
        return WorkspaceFileState(
            path=path, title=item.title, purpose=item.purpose, required=item.required,
            status="TOO_LARGE", exists=True, size=len(data), sha256=digest, content="",
            suggested_content=suggested, detail="파일이 너무 커서 편집기에 불러오지 않았습니다.",
        )
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return WorkspaceFileState(
            path=path, title=item.title, purpose=item.purpose, required=item.required,
            status="BINARY", exists=True, size=len(data), sha256=digest, content="",
            suggested_content=suggested, detail="UTF-8 텍스트 파일이 아닙니다.",
        )
    return WorkspaceFileState(
        path=path, title=item.title, purpose=item.purpose, required=item.required,
        status="READY" if content.strip() else "EMPTY", exists=True, size=len(data),
        sha256=digest, content=content, suggested_content=suggested,
        detail="읽기 전용 미리보기입니다.",
        substantive=has_substantive_workspace_content(content, scaffold_purpose=item.purpose),
    )


def inspect_workspace(plan: ScaffoldPlan, workspace_path: str | Path) -> WorkspaceSnapshot:
    """Inspect expected scaffold files without creating or changing anything."""

    root = _resolve_workspace(workspace_path)
    exists = root.exists()
    is_directory = root.is_dir() if exists else False
    if exists and not is_directory:
        raise WorkspaceError("workspace 경로가 폴더가 아닙니다.")
    writable = is_directory and os.access(root, os.W_OK)
    files = tuple(_file_state(plan, root, item) for item in plan.files)
    completed = sum(item.status == "READY" for item in files)
    total = len(files)
    required_files = tuple(item for item in files if item.required)
    required_substantive = sum(item.substantive for item in required_files)
    required_total = len(required_files)
    return WorkspaceSnapshot(
        workspace_path=str(root),
        status="READY" if is_directory else "MISSING",
        exists=exists,
        is_directory=is_directory,
        writable=writable,
        plan_id=plan.id,
        files=files,
        progress={
            "completed": completed,
            "total": total,
            "percent": round((completed / total) * 100) if total else 0,
            "required_substantive_completed": required_substantive,
            "required_total": required_total,
            "required_substantive_percent": (
                round((required_substantive / required_total) * 100) if required_total else 0
            ),
        },
    )


def build_file_impact(plan: ScaffoldPlan, changed_path: str) -> WorkspaceImpact:
    """Map one changed scaffold file to the files it reaches, from the plan's own connections."""

    changed = _normalise_relative_path(changed_path)
    known = tuple(item.path for item in plan.files)
    if changed not in known:
        raise WorkspaceError(f"scaffold에 없는 파일입니다: {changed}")
    edges = impact_review.plan_edges(plan)
    reached = impact_review.reach(edges, (changed,))
    affected = tuple(path for path in known if path in reached)
    lenses = ("structure", "causal") if affected else ("structure",)
    cards = ("core/decompose", "core/evidence") if affected else ("core/evidence",)
    return WorkspaceImpact(
        plan_id=plan.id,
        changed_path=changed,
        affected_paths=affected,
        reasons=impact_review.as_reasons(edges, changed, reached),
        card_lenses=lenses,
        card_ids=cards,
    )


def build_recheck_targets(
    plan: ScaffoldPlan, changed_paths: Sequence[str]
) -> dict[str, object]:
    """Return a deterministic, read-only target list for an explicit recheck request."""

    if isinstance(changed_paths, (str, bytes)) or not isinstance(changed_paths, Sequence):
        raise TypeError("changed_paths must be a sequence of relative paths")
    normalized = sorted(_normalise_relative_path(path) for path in changed_paths)
    if not normalized:
        return {
            "schema_version": "workspace.recheck-targets.v1",
            "status": "UNRESOLVED",
            "plan_id": plan.id,
            "changed_paths": [],
            "targets": [],
            "errors": ["changed_paths_missing"],
            "canonical_mutation": False,
            "filesystem_mutation": False,
            "provider_called": False,
            "execution_allowed": False,
        }
    if len(normalized) != len(set(normalized)):
        return {
            "schema_version": "workspace.recheck-targets.v1",
            "status": "UNRESOLVED",
            "plan_id": plan.id,
            "changed_paths": normalized,
            "targets": [],
            "errors": ["changed_paths_duplicate"],
            "canonical_mutation": False,
            "filesystem_mutation": False,
            "provider_called": False,
            "execution_allowed": False,
        }
    known = {item.path for item in plan.files}
    unknown = sorted(set(normalized) - known)
    if unknown:
        return {
            "schema_version": "workspace.recheck-targets.v1",
            "status": "UNRESOLVED",
            "plan_id": plan.id,
            "changed_paths": normalized,
            "targets": [],
            "errors": [f"unknown_changed_path:{path}" for path in unknown],
            "canonical_mutation": False,
            "filesystem_mutation": False,
            "provider_called": False,
            "execution_allowed": False,
        }
    target_reasons: dict[str, set[str]] = {
        path: {"explicitly_changed_path"} for path in normalized
    }
    selected_by: dict[str, set[str]] = {path: {path} for path in normalized}
    for changed in normalized:
        impact = build_file_impact(plan, changed)
        for path in impact.affected_paths:
            target_reasons.setdefault(path, set()).add("declared_downstream_impact")
            selected_by.setdefault(path, set()).add(changed)
    targets = [
        {
            "path": path,
            "reasons": sorted(target_reasons[path]),
            "selected_by_changed_paths": sorted(selected_by[path]),
        }
        for path in sorted(target_reasons)
    ]
    return {
        "schema_version": "workspace.recheck-targets.v1",
        "status": "PROPOSED",
        "plan_id": plan.id,
        "changed_paths": normalized,
        "targets": targets,
        "errors": [],
        "canonical_mutation": False,
        "filesystem_mutation": False,
        "provider_called": False,
        "execution_allowed": False,
    }


def observe_workspace_changes(
    plan: ScaffoldPlan,
    workspace_path: str | Path,
    previous_hashes: Mapping[str, str | None],
) -> WorkspaceObservation:
    """Scan expected files and detect external changes without reading beyond the plan."""

    snapshot = inspect_workspace(plan, workspace_path)
    current = {item.path: item.sha256 for item in snapshot.files}
    previous = {_normalise_relative_path(path): value for path, value in previous_hashes.items()}
    unknown_previous = sorted(set(previous) - set(current))
    if unknown_previous:
        raise WorkspaceError(f"scaffold에 없는 이전 파일 hash입니다: {unknown_previous}")
    changes: list[WorkspaceChange] = []
    for path in sorted(set(current) | set(previous)):
        before = previous.get(path)
        after = current.get(path)
        if before == after:
            continue
        kind = "ADDED" if before is None and after is not None else "REMOVED" if before is not None and after is None else "MODIFIED"
        changes.append(
            WorkspaceChange(
                path=path,
                kind=kind,
                previous_sha256=before,
                current_sha256=after,
            )
        )
    return WorkspaceObservation(snapshot=snapshot, changes=tuple(changes))


def propose_file_update(
    plan: ScaffoldPlan,
    workspace_path: str | Path,
    path: str,
    content: str,
    *,
    owner: str = "human",
    source_id: str = "guided-ui",
    rationale: str = "",
    create_workspace: bool = False,
) -> FileProposal:
    """Build a content Proposal and impact report without writing the file."""

    root = _resolve_workspace(workspace_path)
    relative = _normalise_relative_path(path)
    if relative not in {item.path for item in plan.files}:
        raise WorkspaceError(f"scaffold에 없는 파일입니다: {relative}")
    if not isinstance(content, str):
        raise WorkspaceError("파일 내용은 텍스트여야 합니다.")
    if len(content.encode("utf-8")) > _MAX_READ_BYTES:
        raise WorkspaceError("Proposal 파일 내용이 너무 큽니다.")
    target = _safe_target(root, relative)
    base: str | None = None
    base_data: bytes | None = None
    if target.exists():
        if not target.is_file():
            raise WorkspaceError("Proposal 대상이 일반 파일이 아닙니다.")
        data = target.read_bytes()
        if len(data) > _MAX_READ_BYTES:
            raise WorkspaceError("기존 파일이 너무 커서 안전하게 Proposal할 수 없습니다.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("기존 파일이 UTF-8 텍스트가 아닙니다.") from exc
        base_data = data
        base = _sha256(data)
    impact = build_file_impact(plan, relative)
    owner_value = str(owner).strip() or "human"
    source_value = str(source_id).strip() or "guided-ui"
    content_hash = _sha256(content.encode("utf-8"))
    protected_paths = tuple(item.path for item in plan.files if item.path != relative)
    diff = _build_proposal_diff(base_data, content)
    canonical = "|".join((plan.id, str(root), relative, content_hash, base or "", owner_value, source_value))
    proposal_id = f"workspace-proposal:{_sha256(canonical.encode('utf-8'))}"
    return FileProposal(
        id=proposal_id,
        plan_id=plan.id,
        workspace_path=str(root),
        path=relative,
        content=content,
        content_sha256=content_hash,
        base_sha256=base,
        owner=owner_value,
        source_id=source_value,
        rationale=str(rationale).strip(),
        impact=impact,
        create_workspace=bool(create_workspace),
        diff=diff,
        protected_paths=protected_paths,
    )


def _validate_approval_target(
    proposal: FileProposal, approval_target: Mapping[str, object]
) -> None:
    if not isinstance(approval_target, Mapping):
        raise WorkspaceError("승인 대상은 mapping이어야 합니다.")
    actual = dict(approval_target)
    protected = actual.get("protected_paths")
    if isinstance(protected, (str, bytes)) or not isinstance(protected, Sequence):
        raise WorkspaceError("승인 대상의 보호 경로가 잘못되었습니다.")
    actual["protected_paths"] = list(protected)
    if actual != proposal.approval_target:
        raise WorkspaceError("승인 대상이 현재 Proposal과 일치하지 않습니다.")


def apply_file_proposal(
    proposal: FileProposal,
    *,
    approved: bool = False,
    approval_target: Mapping[str, object] | None = None,
) -> WorkspaceApplyResult:
    """Apply one proposal only after explicit approval and a base-hash check."""

    if proposal.status != "PROPOSED":
        raise WorkspaceError("이미 처리된 Proposal은 다시 적용할 수 없습니다.")
    if not approved:
        raise WorkspaceError("파일 저장은 명시적인 approved=true가 필요합니다.")
    if approval_target is not None:
        _validate_approval_target(proposal, approval_target)
    root = Path(proposal.workspace_path).resolve(strict=False)
    if root.exists() and not root.is_dir():
        raise WorkspaceError("workspace 경로가 폴더가 아닙니다.")
    if not root.exists():
        if not proposal.create_workspace:
            raise WorkspaceError("workspace 폴더가 없습니다. 생성 승인을 먼저 지정하세요.")
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"workspace 폴더를 만들 수 없습니다: {root}") from exc
    if not os.access(root, os.W_OK):
        raise WorkspaceError("workspace 폴더에 쓸 수 없습니다.")
    target = _safe_target(root, proposal.path)
    current = target.read_bytes() if target.exists() and target.is_file() else None
    current_hash = _sha256(current) if current is not None else None
    if current_hash != proposal.base_sha256:
        raise WorkspaceError("파일이 Proposal 이후 바뀌었습니다. 다시 읽고 영향 분석을 해 주세요.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"workspace 파일 폴더를 만들 수 없습니다: {target.parent}") from exc
    data = proposal.content.encode("utf-8")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, dir=str(target.parent), prefix=".synapse-", suffix=".tmp") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = handle.name
        os.replace(temporary, target)
    except OSError as exc:
        raise WorkspaceError(f"workspace 파일을 적용할 수 없습니다: {target}") from exc
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return WorkspaceApplyResult(
        proposal_id=proposal.id,
        workspace_path=str(root),
        path=proposal.path,
        sha256=proposal.content_sha256,
    )


def apply_file_proposal_set(
    proposals: tuple[FileProposal, ...],
    *,
    approved: bool = False,
    approval_targets: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[WorkspaceApplyResult, ...]:
    """Apply a multi-file Proposal after preflighting every base hash.

    The files are prepared and checked as one set.  If an unexpected write
    failure occurs after one file has changed, the exact previous bytes are
    restored for the files touched by this call.
    """

    if not approved:
        raise WorkspaceError("파일 묶음 저장은 명시적인 approved=true가 필요합니다.")
    if not proposals:
        raise WorkspaceError("저장할 파일 Proposal이 없습니다.")
    roots = {proposal.workspace_path for proposal in proposals}
    plan_ids = {proposal.plan_id for proposal in proposals}
    paths = [proposal.path for proposal in proposals]
    if len(roots) != 1 or len(plan_ids) != 1 or len(paths) != len(set(paths)):
        raise WorkspaceError("파일 묶음 Proposal의 workspace, plan 또는 경로가 일치하지 않습니다.")
    if approval_targets is not None:
        if not isinstance(approval_targets, Mapping):
            raise WorkspaceError("파일 묶음 승인 대상은 mapping이어야 합니다.")
        for proposal in proposals:
            target = approval_targets.get(proposal.id)
            if target is None:
                raise WorkspaceError(f"승인 대상이 없습니다: {proposal.id}")
            _validate_approval_target(proposal, target)
    root = Path(next(iter(roots))).resolve(strict=False)
    snapshots: list[tuple[Path, bool, bytes | None]] = []
    for proposal in proposals:
        if proposal.status != "PROPOSED":
            raise WorkspaceError("이미 처리된 Proposal은 파일 묶음에 넣을 수 없습니다.")
        target = _safe_target(root, proposal.path)
        current = target.read_bytes() if target.exists() and target.is_file() else None
        current_hash = _sha256(current) if current is not None else None
        if current_hash != proposal.base_sha256:
            raise WorkspaceError(f"파일이 Proposal 이후 바뀌었습니다: {proposal.path}")
        snapshots.append((target, current is not None, current))
    results: list[WorkspaceApplyResult] = []
    try:
        for proposal in proposals:
            target = approval_targets.get(proposal.id) if approval_targets is not None else None
            results.append(
                apply_file_proposal(
                    proposal,
                    approved=True,
                    approval_target=target,
                )
            )
    except Exception:
        for target, existed, data in reversed(snapshots[: len(results)]):
            try:
                if existed and data is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                elif target.exists():
                    target.unlink()
            except OSError:
                pass
        raise
    return tuple(results)


__all__ = [
    "SUBSTANTIVE_CONTENT_CHECKER_VERSION",
    "FileProposal",
    "WorkspaceApplyResult",
    "WorkspaceChange",
    "WorkspaceError",
    "WorkspaceFileState",
    "WorkspaceImpact",
    "WorkspaceObservation",
    "WorkspaceSnapshot",
    "apply_file_proposal",
    "apply_file_proposal_set",
    "build_file_impact",
    "build_recheck_targets",
    "explain_substantive_workspace_content",
    "has_substantive_workspace_content",
    "inspect_workspace",
    "observe_workspace_changes",
    "propose_file_update",
]
