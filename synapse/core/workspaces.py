"""The editions' workspaces, read through the registry (DEC-0242, DEC-0244).

The shell never imports an edition by name. It reads ``editions/editions.yaml``, imports only the modules an edition
lists as its ``workspace``, and takes from each its ``TITLE`` and its on-screen confirmations (``CONFIRM_ACTIONS``),
whose names must start with the edition's id. A release whose registry lists one edition loads that one alone.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse.core.editions import load_editions
from synapse.core.screen_confirm import ConfirmAction


@dataclass(frozen=True, slots=True)
class Workspace:
    edition: str
    name: str
    status: str
    title: str
    actions: Mapping[str, ConfirmAction]
    module: Any = None
    page: str | None = None
    api_prefix: str | None = None

    @property
    def opens_projects(self) -> bool:
        """A workspace with its own page and API opens a project (``open_project``) and serves it (DEC-0248)."""

        return bool(self.page and self.api_prefix and callable(getattr(self.module, "open_project", None)))

    def to_record(self) -> dict[str, Any]:
        return {
            "edition": self.edition,
            "name": self.name,
            "status": self.status,
            "title": self.title,
            "page": self.page if self.opens_projects else None,
            "actions": [
                {"name": name, "label": action.label, "fields": [dict(item) for item in action.fields], "command": action.command}
                for name, action in self.actions.items()
            ],
        }


def load_workspaces(root: str | Path | None = None) -> tuple[Workspace, ...]:
    workspaces: list[Workspace] = []
    for edition in load_editions(root):
        if edition.workspace is None:
            continue
        module = importlib.import_module(edition.workspace)
        actions = getattr(module, "CONFIRM_ACTIONS", {})
        if not isinstance(actions, Mapping):
            raise TypeError(f"{edition.workspace}.CONFIRM_ACTIONS must be a mapping")
        for name, action in actions.items():
            if not isinstance(name, str) or not name.startswith(f"{edition.id}."):
                raise ValueError(f"{edition.workspace}: confirmation {name!r} must start with {edition.id!r}.")
            if not isinstance(action, ConfirmAction):
                raise TypeError(f"{edition.workspace}: confirmation {name!r} must be a ConfirmAction")
        title = getattr(module, "TITLE", edition.name)
        page, api_prefix = getattr(module, "PAGE", None), getattr(module, "API_PREFIX", None)
        for value, label in ((page, "PAGE"), (api_prefix, "API_PREFIX")):
            if value is not None and (not isinstance(value, str) or not value.startswith("/") or not value.endswith("/")):
                raise ValueError(f"{edition.workspace}.{label} must be a path that starts and ends with '/'")
        workspaces.append(
            Workspace(edition.id, edition.name, edition.status, str(title), dict(actions), module, page, api_prefix)
        )
    return tuple(workspaces)


def confirm_actions(workspaces: tuple[Workspace, ...]) -> dict[str, ConfirmAction]:
    merged: dict[str, ConfirmAction] = {}
    for workspace in workspaces:
        merged.update(workspace.actions)
    return merged


__all__ = ["Workspace", "confirm_actions", "load_workspaces"]
