"""Explicit opt-in JSON HTTP transport for provider adapters.

Importing this module does not contact a server.  A caller must explicitly
invoke ``UrllibJsonTransport.send``; failures are classified and never become
Canonical State automatically.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from synapse.runtime.contracts import (
    PreparedRequest,
    RuntimeAdapterError,
    RuntimeErrorKind,
)
from synapse.runtime.redaction import redact_diagnostic_detail


def _http_error_detail(exc: urllib.error.HTTPError, *, limit: int = 512) -> str:
    """Return a bounded provider error detail without exposing a full body."""

    try:
        raw = exc.read(4096).decode("utf-8", errors="replace").strip()
    except OSError:
        raw = ""
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        detail = raw
    else:
        detail_value: Any = payload
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping):
                detail_value = error.get("message") or error.get("detail") or error
            elif error:
                detail_value = error
            elif payload.get("message"):
                detail_value = payload["message"]
        detail = str(detail_value)
    return redact_diagnostic_detail(detail, limit=limit)


def _connection_closed(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    if isinstance(
        reason,
        (
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            EOFError,
        ),
    ):
        return True
    lowered = str(reason).casefold()
    return any(
        marker in lowered
        for marker in (
            "remote end closed",
            "connection reset",
            "connection aborted",
            "incomplete read",
            "broken pipe",
            "원격 호스트가 연결을 닫",
            "연결이 재설정",
        )
    )


class JsonTransport(Protocol):
    def send(self, request: PreparedRequest, *, timeout_seconds: float) -> Mapping[str, Any]:
        """Send one prepared request and return a JSON object."""
        ...


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is answered as an error, never followed: a request that was checked against one address
    (loopback only, say) must not be sent with its body to another."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirect)


def _open(request: urllib.request.Request, *, timeout: float):
    """Open ``request`` without following any redirect."""

    return _OPENER.open(request, timeout=timeout)


class UrllibJsonTransport:
    """Small stdlib transport used only when a caller explicitly opts in."""

    def send(self, request: PreparedRequest, *, timeout_seconds: float) -> Mapping[str, Any]:
        body = json.dumps(dict(request.body), ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            request.url,
            data=body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with _open(http_request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise RuntimeAdapterError(
                    RuntimeErrorKind.TRANSPORT,
                    f"{request.provider} HTTP {exc.code}: 리디렉션은 따르지 않습니다(요청을 다른 주소로 보내지 않음)",
                ) from exc
            kind = {
                401: RuntimeErrorKind.AUTHENTICATION,
                403: RuntimeErrorKind.AUTHENTICATION,
                408: RuntimeErrorKind.TIMEOUT,
                429: RuntimeErrorKind.RATE_LIMIT,
                504: RuntimeErrorKind.TIMEOUT,
            }.get(exc.code, RuntimeErrorKind.PROVIDER)
            detail = _http_error_detail(exc)
            suffix = f": {detail}" if detail else ""
            raise RuntimeAdapterError(kind, f"{request.provider} HTTP {exc.code}{suffix}") from exc
        except TimeoutError as exc:
            raise RuntimeAdapterError(RuntimeErrorKind.TIMEOUT, f"{request.provider} 요청 시간 초과") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                kind = RuntimeErrorKind.TIMEOUT
                label = "요청 시간 초과"
            elif _connection_closed(exc):
                kind = RuntimeErrorKind.CONNECTION_CLOSED
                label = "원격 연결 종료; 서버 중단 여부는 관측하지 않음"
            else:
                kind = RuntimeErrorKind.TRANSPORT
                label = f"전송 실패: {exc.reason}"
            raise RuntimeAdapterError(kind, f"{request.provider} {label}") from exc
        except OSError as exc:
            if _connection_closed(exc):
                raise RuntimeAdapterError(
                    RuntimeErrorKind.CONNECTION_CLOSED,
                    f"{request.provider} 원격 연결 종료; 서버 중단 여부는 관측하지 않음",
                ) from exc
            raise RuntimeAdapterError(RuntimeErrorKind.TRANSPORT, f"{request.provider} 전송 실패") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeAdapterError(
                RuntimeErrorKind.MALFORMED_RESPONSE,
                f"{request.provider} 응답이 JSON이 아닙니다.",
            ) from exc
        if not isinstance(payload, Mapping):
            raise RuntimeAdapterError(
                RuntimeErrorKind.MALFORMED_RESPONSE,
                f"{request.provider} 응답이 JSON object가 아닙니다.",
            )
        return payload
