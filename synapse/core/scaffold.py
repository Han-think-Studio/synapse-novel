"""Idea-to-layout planning derived from one fixed preset (DEC-0199).

The layout of a project is derived from the preset it is started with; there is no shared base
layer and no default preset. The planner is intentionally a proposal boundary: it turns a small
idea seed and one selected preset into a deterministic folder/file checklist, and it never creates
files, calls a model, or mutates Canonical State.

Layout convention (the one ``canon.derive_scaffold_canon`` reads): ``00_intake/*`` are sources,
``NN_<folder>/<file>`` is the owner of one fact, ``README.md`` and ``synapse.project.yaml`` are copies.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from synapse.core import preset_pipeline
from synapse.core.canon import CanonMap, derive_scaffold_canon


class ScaffoldError(ValueError):
    """Raised when an idea-to-scaffold request is not safe or complete."""


SCAFFOLD_PLANNER_VERSION = 3


@dataclass(frozen=True, slots=True)
class ScaffoldFile:
    path: str
    title: str
    purpose: str
    required: bool = True
    initial_status: str = "EMPTY"

    def __post_init__(self) -> None:
        if not self.path.strip() or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ScaffoldError(f"안전하지 않은 scaffold 경로입니다: {self.path}")
        if not self.title.strip() or not self.purpose.strip():
            raise ScaffoldError("ScaffoldFile title과 purpose가 필요합니다.")
        if self.initial_status not in {"EMPTY", "GUIDE", "PROPOSED"}:
            raise ScaffoldError(f"지원하지 않는 초기 파일 상태입니다: {self.initial_status}")

    def to_record(self) -> dict[str, object]:
        return {
            "path": self.path,
            "title": self.title,
            "purpose": self.purpose,
            "required": self.required,
            "status": self.initial_status,
        }


@dataclass(frozen=True, slots=True)
class ScaffoldStep:
    id: str
    title: str
    instruction: str
    file_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.title.strip() or not self.instruction.strip():
            raise ScaffoldError("ScaffoldStep 식별자와 안내가 필요합니다.")
        object.__setattr__(self, "file_paths", tuple(dict.fromkeys(self.file_paths)))

    def to_record(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "instruction": self.instruction,
            "file_paths": list(self.file_paths),
        }


@dataclass(frozen=True, slots=True)
class ScaffoldPreset:
    id: str
    label: str
    description: str
    extra_files: tuple[ScaffoldFile, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.label.strip() or not self.description.strip():
            raise ScaffoldError("ScaffoldPreset 기본 설명이 비어 있습니다.")
        object.__setattr__(self, "extra_files", tuple(self.extra_files))
        object.__setattr__(self, "tags", tuple(dict.fromkeys(self.tags)))

    def to_record(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class ScaffoldPlan:
    id: str
    project_name: str
    project_slug: str
    preset: ScaffoldPreset
    idea: str
    files: tuple[ScaffoldFile, ...]
    steps: tuple[ScaffoldStep, ...]
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.project_name.strip() or not self.project_slug.strip():
            raise ScaffoldError("ScaffoldPlan 식별자가 비어 있습니다.")
        if not self.idea.strip() or not self.files or not self.steps:
            raise ScaffoldError("ScaffoldPlan에는 idea, files, steps가 필요합니다.")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ScaffoldError("ScaffoldPlan 파일 경로가 중복됩니다.")
        known = set(paths)
        missing = sorted({path for step in self.steps for path in step.file_paths if path not in known})
        if missing:
            raise ScaffoldError(f"ScaffoldStep가 알 수 없는 파일을 참조합니다: {missing}")
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def required_count(self) -> int:
        return sum(item.required for item in self.files)

    @property
    def canon(self) -> CanonMap:
        """Who owns each fact of this project, decided before any file has content.

        Derived from the file layout alone, so it is as deterministic as the plan
        and never enters the plan id: the id is a hash of what the plan asks for,
        and a canon that could move it would move every id already published.
        """

        return derive_scaffold_canon(self.files)

    def to_record(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_name": self.project_name,
            "project_slug": self.project_slug,
            "preset": self.preset.to_record(),
            "idea": self.idea,
            "files": [item.to_record() for item in self.files],
            "steps": [step.to_record() for step in self.steps],
            "canon": self.canon.to_record(),
            "progress": {"completed": 0, "total": len(self.files), "percent": 0},
            "metadata": dict(self.metadata),
            "canonical_mutation": False,
        }


def _preset_view(preset: Mapping[str, object]) -> ScaffoldPreset:
    return ScaffoldPreset(
        str(preset["id"]),
        str(preset["label"]),
        str(preset["purpose"]),
        tags=("fixed-preset",),
    )


def _layout(preset: Mapping[str, object]) -> tuple[tuple[ScaffoldFile, ...], tuple[ScaffoldStep, ...]]:
    """The files and steps of one preset, from its own inputs, tasks and acceptance rule."""

    _, data, _ = preset_pipeline.load_preset(str(preset["id"]))
    intake = [
        ScaffoldFile("00_intake/idea.md", "아이디어 원문", "사용자가 처음 적은 아이디어를 원문 그대로 보존합니다.", initial_status="EMPTY")
    ]
    for number, guide in enumerate(data["required_inputs"], start=1):
        intake.append(
            ScaffoldFile(f"00_intake/input-{number:02d}.md", f"입력 {number}", str(guide)[:300])
        )
    tasks = [
        ScaffoldFile(
            f"01_tasks/{task['id']}.md",
            str(task["title"]),
            f"{task['title']}의 결과: " + ", ".join(task["fields"]),
        )
        for task in data["tasks"]
    ]
    checks: list[str] = []
    for task in data["tasks"]:
        checks.extend(str(check) for check in task.get("checks", ()) if str(check) not in checks)
    review = ScaffoldFile(
        "02_review/acceptance.md",
        "검토 기준",
        ("사람이 이 프로젝트의 결과를 승인할 때 확인할 기준을 프로젝트 말로 적습니다. 확인 항목: " + " / ".join(checks))[:300],
    )
    files = (
        ScaffoldFile("synapse.project.yaml", "Project manifest", "프로젝트 이름·프리셋·초안 버전을 기록합니다.", initial_status="GUIDE"),
        ScaffoldFile("README.md", "사용 안내", "프리셋의 첫 작업 결과를 짧게 다시 적습니다.", initial_status="GUIDE"),
        *intake,
        *tasks,
        review,
    )
    steps = [
        ScaffoldStep("intake", "근거 모으기", "프리셋이 요구하는 입력을 00_intake에 적습니다.", tuple(item.path for item in intake)),
        *(
            ScaffoldStep(str(task["id"]), str(task["title"]), "; ".join(task["questions"])[:300], (f"01_tasks/{task['id']}.md",))
            for task in data["tasks"]
        ),
        ScaffoldStep("review", "검토하기", str(data["acceptance"])[:300], ("02_review/acceptance.md",)),
    ]
    return files, tuple(steps)


def list_scaffold_presets() -> tuple[ScaffoldPreset, ...]:
    """The fixed menu, in catalog order."""

    return tuple(_preset_view(item) for item in preset_pipeline.list_presets())


def _slug(value: str) -> str:
    normalized = re.sub(r"[^0-9a-z가-힣]+", "-", value.strip().lower()).strip("-")
    return normalized or "synapse-project"


# A run of these is how people draw a line, not how they name a thing. A line
# made only of them, or one fenced by them at both ends, is a separator carrying
# a label -- "--- README.md ---" -- and the label is the file it came from.
_SEPARATOR_RUN = r"[-=*_~]{3,}"
_SEPARATOR_LINE = re.compile(rf"^\s*(?:{_SEPARATOR_RUN}\s*.*?\s*{_SEPARATOR_RUN}|{_SEPARATOR_RUN})\s*$")
_HAS_WORD = re.compile(r"[^\W_]")
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.+?)\s*$")
# A line that closes the way a sentence closes. Used only to decide whether the
# author had already started writing before the first heading appeared.
_PROSE_LINE = re.compile(r"(?:[.!?。！？]|[다요죠까](?:\.)?)\s*$")


def _name_from_idea(idea: str) -> str:
    """The line that claims to be a title, not the line that happens to be first.

    An idea written for this tool opens with its own name, so the first line is
    the title and nothing here changes that. Material that was already underway
    somewhere else opens with whatever the person pasted first -- a separator, a
    file banner, a date, a greeting, a bullet, an opening code fence -- and its
    real title is a heading a line or two below. Taking the first line
    regardless made "--- README.md ---" the project name for a folder that said
    "# 중고 시세 추적기" immediately after it.

    So a heading wins, but only while nothing has been *written* yet: once a
    line closes like a sentence, the author has started, and a heading after
    that is a section of their text rather than the name of it.
    """
    scrap = ""
    for line in idea.splitlines():
        if not line.strip() or _SEPARATOR_LINE.match(line):
            continue
        heading = _HEADING_LINE.match(line)
        if heading:
            return heading.group("text")[:48].strip()
        candidate = line.strip(" #\t").strip()[:48].strip()
        if not candidate or not _HAS_WORD.search(candidate):
            continue
        if _PROSE_LINE.search(line.strip()):
            return candidate
        scrap = scrap or candidate
    if scrap:
        return scrap
    # Every line was a separator, so the label inside one is all there is.
    for line in idea.splitlines():
        candidate = line.strip().strip("-=*_~ \t#").strip()[:48].strip()
        if candidate and _HAS_WORD.search(candidate):
            return candidate
    return "새 Synapse 프로젝트"


def build_scaffold_plan(
    idea: str,
    *,
    preset_id: str,
    project_name: str = "",
) -> ScaffoldPlan:
    """Build a deterministic layout proposal for one named preset without writing to disk."""

    seed = str(idea).strip()
    if len(seed) < 3:
        raise ScaffoldError("아이디어를 세 글자 이상 적어 주세요.")
    try:
        _, data, _ = preset_pipeline.load_preset(str(preset_id).strip())
    except preset_pipeline.PresetPipelineError as exc:
        raise ScaffoldError(f"지원하지 않는 프리셋입니다: {preset_id}") from exc
    preset = _preset_view(data)
    name = str(project_name).strip() or _name_from_idea(seed)
    files, steps = _layout(data)
    canonical = json.dumps(
        {"name": name, "slug": _slug(name), "preset": preset.id, "idea": seed, "files": [item.to_record() for item in files]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ScaffoldPlan(
        id=f"scaffold:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        project_name=name,
        project_slug=_slug(name),
        preset=preset,
        idea=seed,
        files=files,
        steps=steps,
        metadata={
            "version": SCAFFOLD_PLANNER_VERSION,
            "preset_count": len(preset_pipeline.preset_ids()),
            "llm_invoked": False,
        },
    )


__all__ = [
    "SCAFFOLD_PLANNER_VERSION",
    "ScaffoldError",
    "ScaffoldFile",
    "ScaffoldPlan",
    "ScaffoldPreset",
    "ScaffoldStep",
    "build_scaffold_plan",
    "list_scaffold_presets",
]
