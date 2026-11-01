"""A light novel tab over the novel edition's flow, for a shell without the full writer (DEC-0250).

It answers the same screen shape as the full writer (``get``/``post`` returning ``(status, value)``) but drives only
``synapse.novel.edition_flow``: seed, plan, beat request, check and assemble go straight through; confirming a canon
value, confirming the plan and locking a beat are a person's confirmations and run only with the code the shell
prints in its own terminal (``novel.flow``). Each such confirmation is logged in the project's
``novel/screen-confirmations.jsonl``. No model is called: the person's own assistant writes each beat from the request
shown here.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from synapse.core import canon_values_cli, canon_values_read
from synapse.core.run import append_artifact
from synapse.core.screen_confirm import ConfirmAction, ScreenConfirmations, ScreenConfirmError
from synapse.novel import edition_flow
from synapse.novel.edition_flow import FlowError
from synapse.novel.flow_steps import STEPS, current_edition_step

CONFIRMING_ROUTES = {
    "/api/novel/value-confirm": "정본 값 확정",
    "/api/novel/plan-confirm": "계획 확정",
    "/api/novel/lock": "비트 잠금",
}
_NO_ROUTE = object()


@dataclass(frozen=True)
class EditionApp:
    """One opened edition project's screen API, the same shape the shell expects of the full writer."""

    project: Any
    confirmations: ScreenConfirmations
    get: Callable[[str], Any]
    post: Callable[[str, dict[str, Any]], tuple[int, Any]]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _status_of(error: Exception) -> int:
    code = getattr(error, "code", canon_values_cli.CANNOT_RUN)
    return 409 if code == canon_values_cli.NOT_DONE or isinstance(error, canon_values_cli.NotDone) else 422


def _words(words: Any) -> str:
    text = words.strip() if isinstance(words, str) else ""
    if len(text) < canon_values_cli.MIN_WORDS:
        raise ScreenConfirmError(f"본인 말이 {canon_values_cli.MIN_WORDS}자 미만이라 확정하지 않았습니다.")
    return text


def _scene_beat(document: Mapping[str, Any]) -> tuple[str, int]:
    scene, beat = document.get("scene"), document.get("beat")
    if not isinstance(scene, str) or not scene.strip():
        raise ScreenConfirmError("장면 ID를 적어 주세요.")
    try:
        number = int(beat)
    except (TypeError, ValueError) as exc:
        raise ScreenConfirmError("박자 번호를 적어 주세요.") from exc
    return scene.strip(), number


def _through_file(value: Any, run: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    """The edition flow reads seeds and plans from files; hand it this page's JSON through a temporary one."""

    if not isinstance(value, Mapping):
        raise ScreenConfirmError("JSON 객체를 붙여 넣어 주세요.")
    with tempfile.TemporaryDirectory(prefix="synapse-novel-screen-") as folder:
        path = Path(folder) / "input.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return run(path)


def build_edition_app(project_path: str | Path, *, announce: Callable[[str], bool] | None = None) -> EditionApp:
    root = Path(project_path).expanduser().resolve()
    novel = root / "novel"

    def _values() -> list[dict[str, Any]]:
        if not (novel / "seed.json").is_file():
            return []
        store, _claims, _checked = canon_values_read.load_against(novel / "canon-values.json", novel)
        return [
            {"key": entry.key, "kind": entry.kind, "text": entry.text, "status": entry.status, "words": entry.words}
            for entry in store.entries
        ]

    def _status() -> dict[str, Any]:
        if not root.is_dir():
            return {"project": str(root), "exists": False, "seed": False, "values": [], "flow": None,
                    "steps": [{"id": step, "name": name} for step, name in STEPS], "current": "seed"}
        values = _values()
        flow = edition_flow.status(root) if (novel / "seed.json").is_file() else {"plan": None, "beats": [], "stale": []}
        seed_present = (novel / "seed.json").is_file()
        confirmed = sum(1 for value in values if value["status"] == "CONFIRMED")
        return {
            "project": str(root),
            "exists": True,
            "seed": seed_present,
            "values": values,
            "flow": flow,
            "steps": [{"id": step, "name": name} for step, name in STEPS],
            "current": current_edition_step(flow, seed_present=seed_present, confirmed_values=confirmed),
        }

    def _get(path: str) -> Any:
        route = urlsplit(path).path
        try:
            if route == "/api/novel/status":
                return (200, _status())
            if route == "/api/novel/plan":
                payload, plan_hash, current = edition_flow._plan_payload(root)
                return (200, {"plan": payload, "sha256": plan_hash, "current": current})
        except (FlowError, OSError, ValueError) as exc:
            return (_status_of(exc), {"error": getattr(exc, "message", str(exc))})
        return _NO_ROUTE

    def _dispatch(route: str, document: dict[str, Any], *, words: str = "") -> Any:
        if route == "/api/novel/seed":
            return _through_file(document.get("seed"), lambda path: edition_flow.seed_project(root, path))
        if route == "/api/novel/plan":
            return _through_file(document.get("plan"), lambda path: edition_flow.plan_project(root, path))
        if route == "/api/novel/beat":
            scene, beat = _scene_beat(document)
            return edition_flow.request_beat(root, scene, beat, now=_now())
        if route == "/api/novel/check":
            scene, beat = _scene_beat(document)
            text = document.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ScreenConfirmError("검사할 박자 본문을 붙여 넣어 주세요.")
            return edition_flow.check_beat(root, scene, beat, text, now=_now())
        if route == "/api/novel/assemble":
            scene = document.get("scene")
            if not isinstance(scene, str) or not scene.strip():
                raise ScreenConfirmError("장면 ID를 적어 주세요.")
            folder = root / "assembled"
            folder.mkdir(exist_ok=True)
            out = folder / f"{scene.strip()}.md"
            number = 2
            while out.exists():
                out = folder / f"{scene.strip()}-{number}.md"
                number += 1
            return edition_flow.assemble_scene(root, scene.strip(), out)
        if route == "/api/novel/value-confirm":
            key = document.get("key")
            if not isinstance(key, str) or not key.strip():
                raise ScreenConfirmError("확정할 값의 키를 고르세요.")
            spoken = _words(words)
            return canon_values_cli.run_confirm(
                novel / "canon-values.json", novel, key.strip(), terms=None,
                ask=lambda: spoken, out=lambda _message: None, now=_now,
            )
        if route == "/api/novel/plan-confirm":
            return edition_flow._confirm_plan(root, words=_words(words), now=_now())
        if route == "/api/novel/lock":
            scene, beat = _scene_beat(document)
            return edition_flow._lock_beat(root, scene, beat, words=_words(words), now=_now())
        return _NO_ROUTE

    def _post(route: str, document: dict[str, Any]) -> tuple[int, Any]:
        if route in CONFIRMING_ROUTES:
            return (428, {"error": "이 확정은 셸의 확인 코드로만 합니다.", "needs_code": True, "action": "novel.flow"})
        try:
            result = _dispatch(route, document)
        except (FlowError, canon_values_cli.CliError, canon_values_cli.NotDone, ScreenConfirmError, OSError, ValueError) as exc:
            return (_status_of(exc), {"error": getattr(exc, "message", str(exc))})
        if result is _NO_ROUTE:
            return (404, {"error": "요청한 경로가 없습니다."})
        return (200, result)

    def _describe(args: Mapping[str, Any]) -> str:
        route, document = args.get("route"), args.get("payload")
        if route not in CONFIRMING_ROUTES or not isinstance(document, Mapping):
            raise ScreenConfirmError("셸의 코드로 확정하는 경로가 아닙니다.")
        detail = ""
        if route == "/api/novel/value-confirm":
            detail = f" {document.get('key')}"
        elif route == "/api/novel/lock":
            detail = f" {document.get('scene')} · {document.get('beat')}번째 박자"
        return f"소설 {root.name}: {CONFIRMING_ROUTES[route]}{detail}"

    def _run(args: Mapping[str, Any], *, words: str, channel: str) -> dict[str, Any]:
        route, document = args["route"], dict(args["payload"])
        try:
            result = _dispatch(route, document, words=words)
        except (FlowError, canon_values_cli.CliError, canon_values_cli.NotDone) as exc:
            raise ScreenConfirmError(getattr(exc, "message", str(exc))) from exc
        append_artifact(
            novel / "screen-confirmations.jsonl",
            run_id=f"novel:{root.name}",
            kind="screen_confirmation",
            payload={
                "route": route,
                "payload_sha256": hashlib.sha256(
                    json.dumps(args["payload"], ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "words": words if isinstance(words, str) else "",
                "channel": channel,
                "at": _now(),
            },
        )
        return result if isinstance(result, dict) else {"result": result}

    confirmations = ScreenConfirmations(
        {"novel.flow": ConfirmAction(label="소설 흐름 확정", describe=_describe, run=_run, command="python editions/novel/synapse_novel.py novel --help")},
        **({"announce": announce} if announce is not None else {}),
    )
    return EditionApp(SimpleNamespace(root=root), confirmations, _get, _post)


__all__ = ["CONFIRMING_ROUTES", "EditionApp", "build_edition_app"]
