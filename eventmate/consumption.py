"""Per-workspace limits on model use (OWASP "unbounded consumption", Phase 4.7).

One visitor must not be able to spend everyone's model allowance. Every model call passes ``WorkspaceBudgets.slot`` for the workspace bound to the
current request (a context variable the web layer sets), which enforces an hourly call budget, an hourly estimated-token budget and a small concurrency
cap. The process-wide ``llm.METER`` stays as the owner's ceiling above all workspaces. A call that fails still counts: the provider may have billed it.
"""

from __future__ import annotations

import contextvars
import os
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from collections.abc import Callable, Iterator

LOCAL = "local"
WINDOW_S = 3600
_current: contextvars.ContextVar[str] = contextvars.ContextVar("eventmate_workspace", default=LOCAL)
_stage_now: contextvars.ContextVar[str] = contextvars.ContextVar("eventmate_stage", default="other")
# Calls per workspace per hour for each stage of the product (architecture L10: a cap per stage, not only per workspace). These sit under the workspace's own caps, never above them.
# A stage that calls the model once per step (the browser loop, when it exists) gets the tightest. Tune with EVENTMATE_STAGE_<NAME>_CALLS_PER_HOUR; an unlabelled call has no stage cap.
STAGE_CALLS = {"intake": 60, "delta": 40, "assistant": 120, "ideas": 30, "explain": 60, "explore": 40, "browser": 30}
_STAGE = re.compile(r"[a-z][a-z_]{0,19}")


class ConsumptionError(Exception):
    """A limit was reached. ``code`` is machine readable; the web layer answers 429 with it."""

    status = 429

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def bind(workspace_id: str) -> contextvars.Token:
    return _current.set(workspace_id)


def unbind(token: contextvars.Token) -> None:
    _current.reset(token)


def current_workspace() -> str:
    return _current.get()


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Label the model calls made inside the block with the product stage they belong to, so each stage has its own cap."""
    if not _STAGE.fullmatch(name):
        raise ValueError("a stage name is lowercase letters and '_'")
    token = _stage_now.set(name)
    try:
        yield
    finally:
        _stage_now.reset(token)


def current_stage() -> str:
    return _stage_now.get()


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


class WorkspaceBudgets:
    """Hourly calls and estimated tokens, and in-flight calls, per workspace. Limits are read from the environment on each use so they can be tuned."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: dict[str, deque[tuple[float, int]]] = defaultdict(deque)  # workspace -> (time, estimated tokens)
        self._inflight: dict[str, int] = defaultdict(int)
        self._stages: dict[tuple[str, str], deque[float]] = defaultdict(deque)  # (workspace, stage) -> call times

    def stage_limit(self, stage_name: str) -> int:
        default = STAGE_CALLS.get(stage_name, 0)
        return _int_env(f"EVENTMATE_STAGE_{stage_name.upper()}_CALLS_PER_HOUR", default, 1, 100000) if default or os.environ.get(f"EVENTMATE_STAGE_{stage_name.upper()}_CALLS_PER_HOUR") else 0

    def limits(self) -> tuple[int, int, int]:
        return (_int_env("EVENTMATE_WS_LLM_CALLS_PER_HOUR", 200, 1, 100000), _int_env("EVENTMATE_WS_LLM_TOKENS_PER_HOUR", 300000, 100, 100000000),
                _int_env("EVENTMATE_WS_LLM_CONCURRENCY", 2, 1, 32))

    def _trim(self, q: deque[tuple[float, int]], now: float) -> None:
        while q and now - q[0][0] > WINDOW_S:
            q.popleft()

    @contextmanager
    def slot(self, workspace_id: str, est_tokens: int) -> Iterator[None]:
        calls, tokens, conc = self.limits()
        with self._lock:
            now, q = self._clock(), self._calls[workspace_id]
            self._trim(q, now)
            if self._inflight[workspace_id] >= conc:
                raise ConsumptionError("too many AI requests are running for this workspace; wait for one to finish", "workspace_busy")
            if len(q) >= calls or sum(t for _, t in q) + est_tokens > tokens:
                raise ConsumptionError("this workspace has used its hourly AI allowance; try again later", "workspace_budget")
            name = _stage_now.get()
            sq, cap = self._stages[(workspace_id, name)], self.stage_limit(name)
            while sq and now - sq[0] > WINDOW_S:
                sq.popleft()
            if cap and len(sq) >= cap:  # checked before anything is counted, so a refused call costs the workspace nothing
                raise ConsumptionError(f"this workspace has used its hourly allowance for {name.replace('_', ' ')}; try again later", "stage_budget")
            sq.append(now)
            q.append((now, est_tokens))  # counted before the call: a failed call may still have been billed
            self._inflight[workspace_id] += 1
        try:
            yield
        finally:
            with self._lock:
                self._inflight[workspace_id] -= 1

    def add_tokens(self, workspace_id: str, extra: int) -> None:
        """Add the estimated size of a reply to the latest call of this workspace."""
        with self._lock:
            q = self._calls[workspace_id]
            if q and extra > 0:
                t, used = q[-1]
                q[-1] = (t, used + extra)

    def status(self) -> dict[str, int]:
        with self._lock:
            now = self._clock()
            for q in self._calls.values():
                self._trim(q, now)
            return {"workspaces": sum(1 for q in self._calls.values() if q), "calls_last_hour": sum(len(q) for q in self._calls.values()),
                    "in_flight": sum(self._inflight.values())}

    def stage_status(self) -> dict[str, int]:
        """Calls in the last hour per stage, over every workspace (the owner's view; no workspace id)."""
        with self._lock:
            now, out = self._clock(), defaultdict(int)
            for (_, name), sq in self._stages.items():
                out[name] += sum(1 for t in sq if now - t <= WINDOW_S)
            return dict(out)

    def reset(self) -> None:
        with self._lock:
            self._stages.clear()
            self._calls.clear()
            self._inflight.clear()


BUDGETS = WorkspaceBudgets()
