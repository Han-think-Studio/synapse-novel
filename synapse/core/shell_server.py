"""The small Synapse shell: only what every shell does, for a release that carries no console (DEC-0250).

It serves the editions' workspaces through ``synapse.core.shell`` on this computer only: a request is answered only
from its own page (DEC-0243), every POST carries the launch secret the page was served with, and a confirmation runs
only with the code printed in this window (DEC-0244). The front page lists the workspaces and opens a project.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import traceback
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from synapse.core.local_request import LOCAL_BIND_HOSTS, LocalRequestGuard
from synapse.core.shell import ShellCore, ShellReply, inject_config

DEFAULT_PORT = 8770
MAX_BODY = 1_000_000

FRONT_PAGE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Synapse</title>
  <style>
    body { margin: 0; font-family: system-ui, "Malgun Gothic", sans-serif; background: #f6f7fb; color: #1d2433; }
    main { max-width: 40rem; margin: 2rem auto; padding: 0 1.5rem; }
    section { background: #fff; border: 1px solid #e3e6ee; border-radius: 10px; padding: 1rem 1.2rem; margin: 1rem 0; }
    input { width: 100%; box-sizing: border-box; font: inherit; padding: .45rem .55rem; margin: .4rem 0;
            border: 1px solid #c9cfdb; border-radius: 6px; }
    button { font: inherit; padding: .45rem .9rem; border-radius: 6px; border: 1px solid #1f4b99;
             background: #1f4b99; color: #fff; cursor: pointer; }
    .muted { color: #6b7280; font-size: .92rem; }
    .error { color: #b42318; }
  </style>
</head>
<body>
  <main>
    <h1>Synapse</h1>
    <p class="muted">이 컴퓨터에서만 열리는 화면입니다. 확정할 때 쓰는 확인 코드는 이 셸을 띄운 창에 찍힙니다.</p>
    <p id="error" class="error" role="alert"></p>
    <div id="workspaces"></div>
  </main>
  <script>
    "use strict";
    const secret = window.SYNAPSE_UI_CONFIG && window.SYNAPSE_UI_CONFIG.sessionSecret;
    async function open(edition, project) {
      const response = await fetch("/api/workspace/open", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(secret ? { "X-Synapse-Session": secret } : {}) },
        body: JSON.stringify({ edition, project }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "열 수 없습니다.");
      window.location.href = data.page;
    }
    fetch("/api/workspaces").then((r) => r.json()).then((data) => {
      const box = document.getElementById("workspaces");
      for (const workspace of data.workspaces.filter((item) => item.page)) {
        const section = document.createElement("section");
        const title = document.createElement("h2");
        title.textContent = workspace.title + " · " + workspace.name;
        const input = document.createElement("input");
        input.placeholder = "프로젝트 폴더 경로";
        const button = document.createElement("button");
        button.textContent = "열기";
        button.addEventListener("click", () => open(workspace.edition, input.value.trim())
          .catch((error) => { document.getElementById("error").textContent = error.message; }));
        section.append(title, input, button);
        box.append(section);
      }
    });
  </script>
</body>
</html>
"""


def create_shell(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    *,
    session_secret: str | None = None,
    announce: Callable[[str], bool] | None = None,
    root: str | Path | None = None,
) -> ThreadingHTTPServer:
    shell = ShellCore(root=root, announce=announce)
    page_config: dict[str, Any] = {"sessionSecret": session_secret} if session_secret else {}

    class Handler(LocalRequestGuard, BaseHTTPRequestHandler):
        server_version = "SynapseShell/1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: Any) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _reply(self, reply: ShellReply) -> None:
            if reply.content_type is None:
                self._json(reply.status, reply.body)
            else:
                self._send(reply.status, reply.body, reply.content_type)

        def _failed(self, method: str) -> None:
            # The last door, as in the console: a request that breaks a route still gets a JSON answer.
            print(f"[Synapse shell] unexpected {method} failure: {urlsplit(self.path).path}", file=sys.stderr)
            traceback.print_exc()
            self._json(500, {"error": "셸 내부 오류로 요청을 처리하지 못했습니다."})

        def do_GET(self) -> None:
            try:
                reply = shell.answer_get(self.path, page_config=page_config)
            except Exception:  # noqa: BLE001 - final HTTP boundary must preserve a JSON response
                self._failed("GET")
                return
            if reply is not None:
                self._reply(reply)
                return
            if urlsplit(self.path).path in {"/", "/index.html"}:
                self._send(200, inject_config(FRONT_PAGE, page_config).encode("utf-8"), "text/html; charset=utf-8")
                return
            self._json(404, {"error": "요청한 경로가 없습니다."})

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("요청 본문은 1MB 이하의 JSON이어야 합니다.")
            document = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(document, dict):
                raise ValueError("요청 본문은 JSON 객체여야 합니다.")  # noqa: TRY004 - answered 422 like any bad body
            return document

        def do_POST(self) -> None:
            route = urlsplit(self.path).path.rstrip("/") or "/"
            try:
                reply = shell.answer_post(route, self._read_json)
            except (ValueError, OSError) as exc:
                self._json(422, {"error": str(exc)})
                return
            except Exception:  # noqa: BLE001 - final HTTP boundary must preserve a JSON response
                self._failed("POST")
                return
            if reply is None:
                self._json(404, {"error": "요청한 경로가 없습니다."})
                return
            self._reply(reply)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.shell = shell  # type: ignore[attr-defined]
    server.session_secret = session_secret  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The small Synapse shell (local screen for the editions)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--novel-project", help="open this novel project folder and start on its tab")
    args = parser.parse_args(argv)
    if args.host not in LOCAL_BIND_HOSTS:
        parser.error("셸은 이 컴퓨터(127.0.0.1, localhost, ::1)에서만 엽니다.")
    try:
        server = create_shell(args.host, args.port, session_secret=secrets.token_urlsafe(32))
    except OSError as exc:
        print(f"셸을 열 수 없습니다(포트 {args.port}): {exc}", file=sys.stderr)
        return 2
    url = f"http://{args.host}:{server.server_address[1]}/"
    if args.novel_project:
        try:
            opened = server.shell.open_project("novel", args.novel_project)  # type: ignore[attr-defined]
        except (OSError, ValueError) as exc:
            print(f"소설 프로젝트를 열 수 없습니다: {exc}", file=sys.stderr)
            server.server_close()
            return 2
        url = f"http://{args.host}:{server.server_address[1]}{opened['page']}"
    print("Synapse shell")
    print(f"URL: {url}")
    print("확정할 때 쓰는 확인 코드가 이 창에 찍힙니다. 이 창을 닫지 마세요.")
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
