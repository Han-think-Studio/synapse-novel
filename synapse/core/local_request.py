"""A local Synapse screen answers only its own page (DEC-0243).

The console and the novel writer listen on 127.0.0.1. A browser lets any page it has open *send* a request to that
address, even though it will not let the page read the answer, and a page can make its own name point at 127.0.0.1
(DNS rebinding). So a request is answered only when its ``Host`` is this server's own local address and port, and a
POST also needs no foreign ``Origin``, no cross-site ``Sec-Fetch-Site`` and a JSON body; a cross-site page cannot
send that body without a preflight this server never grants.

A program on this computer that calls the server directly, with no browser, sends no ``Origin`` and still passes.
This closes the browser path; it is not authentication.

``LocalRequestGuard`` goes first in a handler's bases, before ``BaseHTTPRequestHandler``: it checks right after the
request line and headers are parsed, so a refused request reaches no route.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

LOCAL_NAMES = ("127.0.0.1", "localhost", "[::1]")
LOCAL_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DRAIN_LIMIT = 1_000_000
SESSION_HEADER = "X-Synapse-Session"

MESSAGES = {
    "host": "이 화면은 자기 주소(127.0.0.1·localhost)로 온 요청에만 답합니다.",
    "origin": "다른 페이지에서 온 요청이라 처리하지 않았습니다.",
    "fetch_site": "다른 사이트에서 온 요청이라 처리하지 않았습니다.",
    "content_type": "요청 본문은 application/json이어야 합니다.",
    "session": "이 화면을 연 실행의 비밀값이 없어 처리하지 않았습니다. 페이지를 새로 고치세요.",
}
STATUS = {
    "host": HTTPStatus.FORBIDDEN,
    "origin": HTTPStatus.FORBIDDEN,
    "fetch_site": HTTPStatus.FORBIDDEN,
    "content_type": HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    "session": HTTPStatus.FORBIDDEN,
}


def _header(headers: Any, name: str) -> str | None:
    value = headers.get(name) if isinstance(headers, Mapping) or hasattr(headers, "get") else None
    return value.strip().lower() if isinstance(value, str) else None


def refusal(method: str, headers: Any, port: int, session_secret: str | None = None) -> str | None:
    """Why this request is not answered (a key of ``MESSAGES``), or ``None`` when it may go on.

    ``session_secret`` is the launch secret the shell put in the page it served (DEC-0244); when the server has one,
    a POST must carry it in ``X-Synapse-Session``.
    """

    hosts = {f"{name}:{port}" for name in LOCAL_NAMES}
    if _header(headers, "Host") not in hosts:
        return "host"
    if method.upper() != "POST":
        return None
    origin = _header(headers, "Origin")
    if origin is not None and origin not in {f"http://{host}" for host in hosts}:
        return "origin"
    site = _header(headers, "Sec-Fetch-Site")
    if site is not None and site not in {"same-origin", "none"}:
        return "fetch_site"
    content_type = (_header(headers, "Content-Type") or "").split(";", 1)[0].strip()
    if content_type != "application/json":
        return "content_type"
    if session_secret is not None:
        sent = headers.get(SESSION_HEADER) if hasattr(headers, "get") else None
        if not isinstance(sent, str) or not hmac.compare_digest(sent.strip(), session_secret):
            return "session"
    return None


class LocalRequestGuard:
    """Refuse, before any route, a request that did not come from this server's own page."""

    def parse_request(self) -> bool:  # type: ignore[override]
        handler: BaseHTTPRequestHandler = self  # type: ignore[assignment]
        if not super().parse_request():  # type: ignore[misc]
            return False
        reason = refusal(
            handler.command,
            handler.headers,
            handler.server.server_address[1],
            getattr(handler.server, "session_secret", None),
        )
        if reason is None:
            return True
        _drain(handler)
        handler.close_connection = True
        body = json.dumps({"error": MESSAGES[reason], "refused": reason}, ensure_ascii=False).encode("utf-8")
        handler.send_response(STATUS[reason])
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
        return False


def _drain(handler: BaseHTTPRequestHandler) -> None:
    """Read what the client sent, so closing the socket does not reset it before it reads the answer."""

    try:
        length = int(handler.headers.get("Content-Length", "0") or 0)
    except ValueError:
        return
    if 0 < length <= DRAIN_LIMIT:
        handler.rfile.read(length)


__all__ = ["LOCAL_BIND_HOSTS", "MESSAGES", "SESSION_HEADER", "LocalRequestGuard", "refusal"]
