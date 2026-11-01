"""What every Synapse shell does, in one place (DEC-0242, DEC-0244, DEC-0248, DEC-0250).

A shell loads the editions' workspaces through the registry, holds the one on-screen confirmation (a code it prints
in its own terminal), opens one project per project-opening workspace, and answers the routes those need:

* ``GET /api/workspaces`` and a workspace's page (``PAGE``) and API (``API_PREFIX``);
* ``POST /api/workspace/open``, ``/api/confirm/request``, ``/api/confirm/complete`` and a workspace's API.

The console builds on this and adds its own panels; ``synapse.core.shell_server`` is the small shell a release
carries, with nothing else. Neither imports an edition by name: the registry names the workspaces.

``ShellCore`` answers with ``ShellReply`` values and raises ``ValueError`` (including ``ScreenConfirmError``) for a
request it refuses; each HTTP layer turns those into its own responses.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from synapse.core.screen_confirm import ConfirmAction, ScreenConfirmations
from synapse.core.workspaces import Workspace, confirm_actions, load_workspaces


@dataclass(frozen=True, slots=True)
class ShellReply:
    """One answer: JSON ``body`` when ``content_type`` is ``None``, otherwise bytes of that type."""

    status: int
    body: Any
    content_type: str | None = None


def inject_config(html: str, config: Mapping[str, Any]) -> str:
    """Put ``window.SYNAPSE_UI_CONFIG`` into a page the shell serves; a page without ``</head>`` is left alone."""

    marker = "</head>"
    if marker not in html:
        return html
    script = f"<script>window.SYNAPSE_UI_CONFIG = {json.dumps(dict(config), ensure_ascii=False)};</script>"
    return html.replace(marker, f"{script}\n{marker}", 1)


class ShellCore:
    """The workspaces, the on-screen confirmation and the opened projects of one shell process."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        actions: Mapping[str, ConfirmAction] | None = None,
        announce: Callable[[str], bool] | None = None,
    ) -> None:
        self.workspaces: tuple[Workspace, ...] = load_workspaces(root)
        self.confirmations = ScreenConfirmations(
            {**confirm_actions(self.workspaces), **dict(actions or {})},
            **({"announce": announce} if announce is not None else {}),
        )
        self.opened: dict[str, Any] = {}
        self._lock = threading.Lock()

    def open_project(self, edition: Any, project: Any) -> dict[str, Any]:
        """Open one project in an edition's workspace; its confirmations join the shell's (DEC-0248)."""

        workspace = next((item for item in self.workspaces if item.edition == edition and item.opens_projects), None)
        if workspace is None:
            raise ValueError("이 판에는 프로젝트를 여는 작업 화면이 없습니다.")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("프로젝트 경로를 적어 주세요.")
        app = workspace.module.open_project(project.strip(), announce=lambda line: self.confirmations.announce(line))
        for name, action in app.confirmations.actions.items():
            if not name.startswith(f"{workspace.edition}."):
                raise ValueError(f"작업 화면의 확정 이름은 {workspace.edition}.로 시작해야 합니다: {name}")
            self.confirmations.actions[name] = action
        with self._lock:
            self.opened[workspace.edition] = app
        opened = getattr(app.project, "project_file", None) or getattr(app.project, "root", None) or project.strip()
        return {"edition": workspace.edition, "page": workspace.page, "project": str(opened)}

    def _opened(self, workspace: Workspace) -> Any:
        app = self.opened.get(workspace.edition)
        if app is None:
            raise LookupError(f"{workspace.title} 프로젝트를 먼저 여세요.")
        return app

    def answer_get(self, path: str, *, page_config: Mapping[str, Any] | None = None) -> ShellReply | None:
        """A shell GET route, or ``None`` when ``path`` is not one. Served pages get ``page_config`` injected."""

        route = urlsplit(path).path
        if route.rstrip("/") == "/api/workspaces":
            return ShellReply(200, {"workspaces": [item.to_record() for item in self.workspaces]})
        for workspace in self.workspaces:
            if not workspace.opens_projects:
                continue
            if route.startswith(workspace.api_prefix):
                try:
                    app = self._opened(workspace)
                except LookupError as exc:
                    return ShellReply(409, {"error": str(exc)})
                outcome = app.get(path)
                if not isinstance(outcome, tuple):
                    return ShellReply(404, {"error": "요청한 경로가 없습니다."})
                status, value = outcome
                return ShellReply(int(status), value)
            if route == workspace.page.rstrip("/") or route.startswith(workspace.page):
                name = route[len(workspace.page):] if route.startswith(workspace.page) else ""
                found = workspace.module.page_file(name)
                if found is None:
                    return ShellReply(404, {"error": "파일을 찾을 수 없습니다."})
                body, content_type = found
                if content_type.startswith("text/html") and page_config is not None:
                    body = inject_config(body.decode("utf-8"), page_config).encode("utf-8")
                return ShellReply(200, body, content_type)
        return None

    def answer_post(self, route: str, read_json: Callable[[], dict[str, Any]]) -> ShellReply | None:
        """A shell POST route, or ``None`` when ``route`` is not one; ``read_json`` reads the body only when needed."""

        if route == "/api/workspace/open":
            payload = read_json()
            return ShellReply(200, self.open_project(payload.get("edition"), payload.get("project")))
        if route == "/api/confirm/request":
            payload = read_json()
            return ShellReply(200, self.confirmations.request(payload.get("action"), payload.get("args")))
        if route == "/api/confirm/complete":
            payload = read_json()
            return ShellReply(
                201,
                self.confirmations.complete(payload.get("pending_id"), payload.get("code"), payload.get("words")),
            )
        for workspace in self.workspaces:
            if workspace.opens_projects and route.startswith(workspace.api_prefix):
                try:
                    app = self._opened(workspace)
                except LookupError as exc:
                    return ShellReply(409, {"error": str(exc)})
                status, value = app.post(route, read_json())
                return ShellReply(int(status), value)
        return None


__all__ = ["ShellCore", "ShellReply", "inject_config"]
