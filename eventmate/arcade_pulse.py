"""In-memory stage pulse for the arcade UI (``/arcade``).

Records which pipeline stage is running *right now* so the arcade page can
light the matching module while real work happens. This is observability only:

- it changes no decisions and writes nothing to disk;
- hooks are cheap no-ops while no run is active (CLI, tests, other UIs);
- a stage is only ever lit by a hook in the real pipeline, never by the page.
  If a hook is missing or a stage is skipped, the module stays dark - the page
  cannot invent progress.
"""

from __future__ import annotations

import contextvars
import threading
import time

# The stages the arcade shows, in pipeline order. Names are stable API.
STAGES = (
    "parse",      # deterministic brief parsing (Manager)
    "decompose",  # task graph + AOP checks (Manager)
    "budget",     # exact integer allocation + contingency (Budget Agent)
    "guests",     # headcount + dietary roll-up (Guest Agent)
    "vendors",    # per-category shortlists, every hard constraint checked (Vendor Agent)
    "select",     # exhaustive vendor-combination search (Planner)
    "schedule",   # CP-SAT schedule solve (Scheduling Agent)
    "symbolic",   # PDDL feasibility: Fast Downward + VAL (SymbolicChecker)
    "validate",   # independent re-derivation of every fact (Validator)
    "store",      # immutable version write + explanation (PlanStore / Explainer)
)


class ArcadePulse:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run_id = 0
        self._label = ""
        self._state = "idle"  # idle | running | done | failed
        self._error = ""
        self._stages: dict[str, dict[str, float | None]] = {}
        self._t0 = 0.0
        self._seq = 0
        self._trail: list[tuple[int, dict]] = []  # every state the current run went through, in order: a stream sends each one even when the run is quicker than its poll

    def _note(self) -> None:
        """Called with the lock held after each change: remember the picture of the run at this moment."""
        self._seq += 1
        self._trail.append((self._seq, self._picture()))

    def watch(self, since: int | None) -> tuple[list[dict], int]:
        """(the pictures recorded after ``since``, the new position). With ``None``: the picture now and the position to continue from."""
        with self._lock:
            if since is None:
                return [self._picture()], self._seq
            return [pic for n, pic in self._trail if n > since], self._seq

    def begin_run(self, label: str) -> int:
        with self._lock:
            self._run_id += 1
            self._label = label[:120]
            self._state = "running"
            self._error = ""
            self._t0 = time.monotonic()
            self._stages = {name: {"state": "waiting", "t0": None, "t1": None} for name in STAGES}
            self._trail = []
            self._note()
            return self._run_id

    def stage_started(self, name: str) -> None:
        with self._lock:
            if self._state != "running":
                return
            st = self._stages.get(name)
            if not st or st["state"] == "done":
                return
            st["state"] = "active"
            st["t0"] = time.monotonic()
            self._note()

    def stage_done(self, name: str) -> None:
        with self._lock:
            if self._state != "running":
                return
            st = self._stages.get(name)
            if not st or st["state"] == "done":
                return
            if st["t0"] is None:
                st["t0"] = time.monotonic()
            st["t1"] = time.monotonic()
            st["state"] = "done"
            self._note()

    def finish(self, ok: bool, error: str = "") -> None:
        with self._lock:
            if self._state != "running":
                return
            now = time.monotonic()
            for st in self._stages.values():
                if st["state"] == "active":
                    st["state"] = "done" if ok else "failed"
                    st["t1"] = now
            self._state = "done" if ok else "failed"
            self._error = error[:300]
            self._note()

    def snapshot(self) -> dict:
        with self._lock:
            return self._picture()

    def _picture(self) -> dict:
        now = time.monotonic()
        stages: list[dict] = []
        for name in STAGES:
            st = self._stages.get(name)
            if not st:
                stages.append({"name": name, "state": "waiting", "ms": None})
                continue
            ms: float | None = None
            if st["t0"] is not None:
                ms = round(((st["t1"] or now) - st["t0"]) * 1000, 1)
            stages.append({"name": name, "state": st["state"], "ms": ms})
        return {
            "run_id": self._run_id,
            "label": self._label,
            "state": self._state,
            "error": self._error,
            "elapsed_ms": round((now - self._t0) * 1000, 1) if self._state != "idle" else None,
            "stages": stages,
        }


class _CurrentPulse:
    """The pulse hooks call. Each request binds its own workspace's pulse (``bind``), so a visitor sees only the runs their own workspace made: one pulse for the whole
    process would show every visitor the last request text and error of every other (F-014). With nothing bound (the CLI, tests, a background worker) it is a private
    default that no web route reads."""

    def __init__(self) -> None:
        self._default = ArcadePulse()
        self._var: contextvars.ContextVar[ArcadePulse | None] = contextvars.ContextVar("eventmate_pulse", default=None)

    def current(self) -> ArcadePulse:
        """The pulse this request is bound to: a stream holds it while it runs, after the request context has moved on."""
        return self._target()

    def bind(self, pulse_: ArcadePulse):
        return self._var.set(pulse_)

    def unbind(self, token) -> None:
        self._var.reset(token)

    def _target(self) -> ArcadePulse:
        return self._var.get() or self._default

    def begin_run(self, label: str) -> int:
        return self._target().begin_run(label)

    def stage_started(self, name: str) -> None:
        self._target().stage_started(name)

    def stage_done(self, name: str) -> None:
        self._target().stage_done(name)

    def finish(self, ok: bool, error: str = "") -> None:
        self._target().finish(ok, error)

    def snapshot(self) -> dict:
        return self._target().snapshot()

    def watch(self, since: int | None) -> tuple[list[dict], int]:
        return self._target().watch(since)


pulse = _CurrentPulse()
