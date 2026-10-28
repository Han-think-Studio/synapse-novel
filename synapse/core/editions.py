"""Validated registry of the editions built on the shared Synapse core."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
_REGISTRY_PATH = Path("editions") / "editions.yaml"
_CATALOG_PATH = Path("domain_packs") / "_presets" / "independent" / "catalog.json"
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_STATUSES = frozenset({"published", "planned", "later"})


@dataclass(frozen=True, slots=True)
class Edition:
    """One edition's identity and files that ship beyond the shared core."""

    id: str
    name: str
    status: str
    source: str
    repository: str | None
    presets: tuple[str, ...]
    ships: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "source": self.source,
            **({"repository": self.repository} if self.repository is not None else {}),
            "presets": list(self.presets),
            "ships": list(self.ships),
        }


def _relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"edition {field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"edition {field} must stay inside the repository")
    return path.as_posix()


def _strings(value: object, field: str, *, ids: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"edition {field} must be a list of non-empty strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise ValueError(f"edition {field} contains duplicates")
    if ids and any(not _ID.fullmatch(item) for item in result):
        raise ValueError(f"edition {field} contains an invalid id")
    return result


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"edition registry is unreadable: {exc}") from exc


def load_editions(root: str | Path | None = None) -> tuple[Edition, ...]:
    """Load and validate the ordered registry against the canonical preset catalog."""

    repo = Path(root).resolve() if root is not None else ROOT
    raw = _read_yaml(repo / _REGISTRY_PATH)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("edition registry must be a version 1 object")
    entries = raw.get("editions")
    if not isinstance(entries, list) or not entries:
        raise ValueError("edition registry must contain an ordered edition list")

    try:
        catalog = json.loads((repo / _CATALOG_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"preset catalog is unreadable: {exc}") from exc
    catalog_ids = catalog.get("presets") if isinstance(catalog, dict) else None
    if not isinstance(catalog_ids, list) or any(not isinstance(item, str) for item in catalog_ids):
        raise ValueError("preset catalog must contain a list of preset ids")
    known_presets = set(catalog_ids)

    editions: list[Edition] = []
    seen_ids: set[str] = set()
    seen_modules: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("edition entry must be an object")
        edition_id = entry.get("id")
        if not isinstance(edition_id, str) or not _ID.fullmatch(edition_id):
            raise ValueError("edition id is invalid")
        if edition_id in seen_ids:
            raise ValueError(f"duplicate edition id: {edition_id}")
        seen_ids.add(edition_id)

        name = entry.get("name")
        status = entry.get("status")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"edition {edition_id} requires a name")
        if status not in _STATUSES:
            raise ValueError(f"edition {edition_id} has an unsupported status")
        source = _relative_path(entry.get("source"), "source")
        repository = entry.get("repository")
        if repository is not None and (not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)):
            raise ValueError(f"edition {edition_id} has an invalid repository")
        if edition_id == "novel" and repository is None:
            raise ValueError("published novel edition requires its repository")
        presets = _strings(entry.get("presets"), "presets", ids=True)
        unknown = set(presets) - known_presets
        if unknown:
            raise ValueError(f"edition {edition_id} references unknown presets: {sorted(unknown)}")
        ships = tuple(_relative_path(item, "ships") for item in _strings(entry.get("ships"), "ships"))
        overlap = seen_modules.intersection(ships)
        if overlap:
            raise ValueError(f"edition module is listed more than once: {sorted(overlap)}")
        seen_modules.update(ships)
        editions.append(Edition(edition_id, name.strip(), status, source, repository, presets, ships))
    return tuple(editions)


def edition(edition_id: str, root: str | Path | None = None) -> Edition:
    """Return one edition by id, raising ``KeyError`` when it is not registered."""

    for item in load_editions(root):
        if item.id == edition_id:
            return item
    raise KeyError(edition_id)


__all__ = ["Edition", "edition", "load_editions"]
