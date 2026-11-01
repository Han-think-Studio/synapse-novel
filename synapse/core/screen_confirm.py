"""The one way a person confirms on screen (DEC-0242, DEC-0244).

The screen asks. The shell prints a one-time code in its own terminal together with what will be confirmed; the
person reads it there and types it on the screen with their own words; only then does the edition's action run, and
its record says ``channel: screen+code``. A code is used once, lives five minutes and allows three tries. With no
terminal to print to there is no code, and the screen is told to use the command line instead.

This is not authentication: a program that can read that terminal passes, as with the command line's terminal check.
An edition offers its confirmations as ``ConfirmAction`` values from its workspace module; the core never imports an
edition by name (``synapse.core.workspaces`` reads them through the registry).

What a code confirms is fixed when it is asked for. The request keeps the action it was printed for, so a code asked
for one project never runs in another the shell opened since; and an action with ``pin`` holds the hash of what it
names (``args["pinned"]``), prints it beside the code, and refuses at the end if the content changed meanwhile.
"""

from __future__ import annotations

import hmac
import secrets
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

SCREEN_CHANNEL = "screen+code"
TERMINAL_CHANNEL = "terminal"
CODE_TTL_SECONDS = 300
CODE_TRIES = 3
PINNED = "pinned"


class ScreenConfirmError(ValueError):
    """A confirmation that cannot go on; nothing ran."""


class ScreenConfirmUnavailable(ScreenConfirmError):
    """The shell has no terminal to print a code to."""


class ScreenCodeRequired(ScreenConfirmError):
    """A confirmation came to the HTTP door without the shell's code (DEC-0246); ``action`` is where it goes instead."""

    def __init__(self, action: str, message: str = "이 확정은 셸의 확인 코드로만 합니다. 화면에서 코드를 받아 주세요.") -> None:
        super().__init__(message)
        self.action = action


@dataclass(frozen=True, slots=True)
class ConfirmAction:
    """One confirmation an edition offers on screen.

    ``describe(args)`` checks the arguments and says in one line what will be confirmed (it is printed beside the
    code); ``run(args, words=..., channel=...)`` makes the edition's record and returns it. ``fields`` tell the screen
    what to ask for: ``{"name", "label", "kind": "path" | "text" | "choice", "choices"?, "optional"?}``.

    ``pin(args)``, when given, reads what the confirmation names as it is at the request (its hashes); the result is
    kept as ``args["pinned"]`` for ``describe`` and ``run``, and ``run`` refuses when the content no longer matches.
    A page cannot send its own ``pinned``.
    """

    label: str
    describe: Callable[[Mapping[str, Any]], str]
    run: Callable[..., Mapping[str, Any]]
    fields: tuple[Mapping[str, Any], ...] = ()
    command: str = ""
    pin: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None


def print_to_terminal(line: str) -> bool:
    """Print ``line`` where a person at this shell's terminal sees it; ``False`` when there is no terminal."""

    try:
        if not sys.stderr.isatty():
            return False
        print(line, file=sys.stderr, flush=True)
        return True
    except (AttributeError, OSError, ValueError):
        return False


@dataclass
class _Pending:
    action: str
    handler: ConfirmAction
    args: dict[str, Any]
    what: str
    code: str
    expires: float
    tries: int = CODE_TRIES
    running: bool = False


@dataclass
class ScreenConfirmations:
    """Pending on-screen confirmations of one shell process."""

    actions: Mapping[str, ConfirmAction]
    announce: Callable[[str], bool] = print_to_terminal
    clock: Callable[[], float] = time.monotonic
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _action(self, name: Any) -> ConfirmAction:
        action = self.actions.get(name) if isinstance(name, str) else None
        if action is None:
            raise ScreenConfirmError(f"이 셸에 없는 확정입니다: {name!r}")
        return action

    def _prune(self) -> None:
        now = self.clock()
        for pending_id in [key for key, item in self._pending.items() if item.expires <= now]:
            del self._pending[pending_id]

    def request(self, action_name: Any, args: Any) -> dict[str, Any]:
        """Print a code for one confirmation and say what it is for; the code itself is never returned."""

        action = self._action(action_name)
        if not isinstance(args, Mapping):
            raise ScreenConfirmError("확정할 내용은 JSON 객체여야 합니다.")
        held = {key: value for key, value in args.items() if key != PINNED}
        if action.pin is not None:
            held[PINNED] = dict(action.pin(held))
        what = action.describe(held)
        code = f"{secrets.randbelow(1_000_000):06d}"
        if not self.announce(f"[Synapse] 확인 코드 {code} — {what} (5분 안에 화면에 입력)"):
            hint = f" 명령줄: {action.command}" if action.command else ""
            raise ScreenConfirmUnavailable(
                "이 셸은 터미널 창 없이 실행되어 확인 코드를 보여 줄 수 없습니다. 터미널에서 셸을 띄우거나 명령줄로 확정하세요."
                + hint
            )
        pending_id = secrets.token_urlsafe(12)
        with self._lock:
            self._prune()
            self._pending[pending_id] = _Pending(
                str(action_name), action, held, what, code, self.clock() + CODE_TTL_SECONDS
            )
        return {"pending_id": pending_id, "what": what, "expires_in": CODE_TTL_SECONDS, "tries": CODE_TRIES}

    def complete(self, pending_id: Any, code: Any, words: Any) -> dict[str, Any]:
        """Run the edition's action once the code from the terminal matches; a code works once.

        If the edition refuses (too few words, a changed file) nothing was recorded and the same code may be tried
        again within its time; a recorded confirmation spends it.
        """

        if not isinstance(pending_id, str) or not isinstance(code, str):
            raise ScreenConfirmError("확인 요청 번호와 코드를 보내 주세요.")
        with self._lock:
            self._prune()
            pending = self._pending.get(pending_id)
            if pending is None:
                raise ScreenConfirmError("확인 요청이 없거나 5분이 지났습니다. 다시 요청하세요.")
            if self.actions.get(pending.action) is not pending.handler:
                # The shell opened a project for this edition since: the printed line named the one open before.
                del self._pending[pending_id]
                raise ScreenConfirmError("코드를 받은 뒤 이 판에 프로젝트를 새로 열어 확인 요청을 지웠습니다. 다시 요청하세요.")
            if pending.running:
                raise ScreenConfirmError("이 확인 요청은 지금 처리 중입니다.")
            if not hmac.compare_digest(code.strip(), pending.code):
                pending.tries -= 1
                if pending.tries <= 0:
                    del self._pending[pending_id]
                    raise ScreenConfirmError("코드가 세 번 틀려 이 확인 요청을 지웠습니다. 다시 요청하세요.")
                raise ScreenConfirmError(f"코드가 맞지 않습니다. 남은 시도 {pending.tries}번.")
            pending.running = True
        try:
            record = pending.handler.run(pending.args, words=words, channel=SCREEN_CHANNEL)
        except BaseException:
            with self._lock:
                pending.running = False
            raise
        with self._lock:
            self._pending.pop(pending_id, None)
        return {"confirmed": pending.action, "what": pending.what, "record": dict(record)}

    def describe_actions(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "label": action.label, "fields": [dict(item) for item in action.fields], "command": action.command}
            for name, action in self.actions.items()
        ]


__all__ = [
    "CODE_TRIES",
    "CODE_TTL_SECONDS",
    "PINNED",
    "SCREEN_CHANNEL",
    "TERMINAL_CHANNEL",
    "ConfirmAction",
    "ScreenCodeRequired",
    "ScreenConfirmError",
    "ScreenConfirmUnavailable",
    "ScreenConfirmations",
    "print_to_terminal",
]
