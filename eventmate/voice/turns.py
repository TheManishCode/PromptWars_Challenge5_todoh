"""Voice sessions and turns: the server side of a spoken conversation (docs/VOICE.md, ADR-056).

A turn is one transcript sent to the same assistant pipeline as typed chat. It differs in three ways, all about not committing too early: proposals the turn
makes are staged and filed only if the turn still stands, the answer cache is read but never written, and the turn is checked for cancellation after every
blocking step. The assistant's model client cannot abort a call in flight, so cancelling means "discard the result and commit nothing".

Access: a session belongs to the principal that opened it and to one plan. A ``session_id`` or ``turn_id`` alone grants nothing; unknown, expired, other
people's and other plans' ids all give the same answer.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Any, Callable, Iterator

from eventmate.assistant.approvals import ApprovalQueue
from eventmate.assistant.identity import Principal
from eventmate.voice.speech import MORE, spoken_answer, split_sentences, strip_markup, verify_spoken

GENERIC = "that voice session or turn is not available"
ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z]{2,4})?")
INPUT_MODES = ("voice_note", "live")
MAX_TURN_CHARS = 600
LOW_CONFIDENCE = 0.5
CLARIFY = "Sorry, I didn't catch that. Could you say it again?"
INTERRUPTED = "(interrupted)"


class VoiceError(Exception):
    """A voice request is refused. ``status`` is the HTTP status; the message is safe to show and never says which of several things was wrong."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class _NullLog:
    def write(self, **fields) -> None:
        return None


def _staging_queue() -> ApprovalQueue:
    """A queue that remembers nothing on disk: proposals made during a turn wait here until the turn is known to stand."""
    return ApprovalQueue(_NullLog())


@dataclass
class Turn:
    turn_id: str
    generation: int
    text: str
    input_mode: str
    memory_user: str
    confidence: float | None = None
    staging: ApprovalQueue = field(default_factory=_staging_queue)
    state: str = "thinking"  # thinking (before the barrier) | answered (committed) | cancelled | interrupted | done
    result: Any = None
    spoken: Any = None
    played_chars: int = 0
    _cancel: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def commit(self, write: Callable[[], Any]) -> Any:
        """The barrier. Under the turn's lock: if the turn is still running, run ``write`` (trace, memory, proposals) and mark it committed; if a cancel
        got here first, run nothing. Returns what ``write`` returned, or None when the cancel won. Exactly one of commit and cancel wins."""
        with self._lock:
            if self.state != "thinking" or self._cancel.is_set():
                return None
            result = write()
            self.state = "answered"
            return result

    def request_cancel(self) -> bool:
        """Cancel if the barrier has not been passed. Returns whether this call cancelled it."""
        with self._lock:
            if self.state != "thinking":
                return False
            self._cancel.set()
            self.state = "cancelled"
            return True


@dataclass
class Session:
    session_id: str
    plan_id: str
    user_id: str
    created: datetime
    last_activity: datetime
    turns: dict[str, Turn] = field(default_factory=dict)
    generation: int = 0
    turn_times: deque[float] = field(default_factory=deque)
    cancel_times: deque[float] = field(default_factory=deque)
    ended: bool = False

    @property
    def memory_user(self) -> str:
        return f"{self.user_id}|voice|{self.session_id}"


def _bounded(q: deque[float], now: float, limit: int, window: float = 60.0) -> bool:
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


class VoiceService:
    def __init__(self, assistant, em, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC), mono: Callable[[], float] = time.monotonic,
                 idle_s: int = 90, cap_s: int = 900, max_chars: int = MAX_TURN_CHARS, turns_per_min: int = 20, cancels_per_min: int = 60, max_sessions: int = 3,
                 max_turns_kept: int = 50, run_timeout_s: float = 90.0):
        self.assistant, self.em, self.clock, self.mono = assistant, em, clock, mono
        self.idle_s, self.cap_s, self.max_chars = idle_s, cap_s, max_chars
        self.turns_per_min, self.cancels_per_min, self.max_sessions = turns_per_min, cancels_per_min, max_sessions
        self.max_turns_kept, self.run_timeout_s = max_turns_kept, run_timeout_s
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="voice-turn")

    # ------------------------------------------------------------ sessions

    def limits(self) -> dict:
        return {"idle_timeout_s": self.idle_s, "session_cap_s": self.cap_s, "max_turn_chars": self.max_chars, "turns_per_minute": self.turns_per_min}

    def _expires(self, s: Session) -> datetime:
        return min(s.created + timedelta(seconds=self.cap_s), s.last_activity + timedelta(seconds=self.idle_s))

    def _alive(self, s: Session) -> bool:
        return not s.ended and self.clock() < self._expires(s)

    def _sweep(self) -> None:
        for sid in [sid for sid, s in self._sessions.items() if not self._alive(s)]:
            self._drop(self._sessions[sid])

    def _drop(self, s: Session) -> None:
        s.ended = True
        for t in s.turns.values():
            t._cancel.set()
        self.assistant.sessions.clear(s.plan_id, s.memory_user)
        self._sessions.pop(s.session_id, None)

    def create_session(self, principal: Principal, plan_id: str) -> dict:
        self.em.store.latest(plan_id)  # an unknown plan fails here
        with self._lock:
            self._sweep()
            if sum(1 for s in self._sessions.values() if s.user_id == principal.user_id) >= self.max_sessions:
                raise VoiceError(429, "too many voice sessions are open; end one first")
            now = self.clock()
            s = Session(session_id=secrets.token_urlsafe(18), plan_id=plan_id, user_id=principal.user_id, created=now, last_activity=now)
            self._sessions[s.session_id] = s
            return {"session_id": s.session_id, "expires_at": self._expires(s).isoformat(), "limits": self.limits()}

    def _get(self, principal: Principal, session_id: Any) -> Session:
        with self._lock:
            s = self._sessions.get(session_id) if isinstance(session_id, str) else None
            if s is None or s.user_id != principal.user_id:
                raise VoiceError(404, GENERIC)
            if not self._alive(s):
                self._drop(s)
                raise VoiceError(404, GENERIC)
            return s

    def end_session(self, principal: Principal, session_id: str) -> None:
        with self._lock:
            self._drop(self._get(principal, session_id))

    # --------------------------------------------------------------- turns

    def start_turn(self, principal: Principal, session_id: str, turn_id: Any, generation: Any, text: Any, input_mode: Any, confidence: float | None = None, language: str | None = None) -> Turn:
        if not isinstance(turn_id, str) or not ID.fullmatch(turn_id) or isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise VoiceError(400, "turn_id and generation are required")
        if input_mode not in INPUT_MODES:
            raise VoiceError(400, "input_mode must be voice_note or live")
        if not isinstance(text, str) or not text.strip():
            raise VoiceError(400, "there is no transcript to send")
        if len(text) > self.max_chars:
            raise VoiceError(400, f"turns are limited to {self.max_chars} characters; say it in parts")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
            raise VoiceError(400, "confidence must be between 0 and 1")
        if language is not None and not (isinstance(language, str) and LANGUAGE.fullmatch(language)):
            raise VoiceError(400, "language must be a language code such as en or hi-IN")
        with self._lock:
            s = self._get(principal, session_id)
            if generation <= s.generation:
                raise VoiceError(409, "that turn is out of date: a newer one has started")
            if any(turn_id in x.turns for x in self._sessions.values()):
                raise VoiceError(409, "that turn id was already used")
            if not _bounded(s.turn_times, self.mono(), self.turns_per_min):
                raise VoiceError(429, "too many turns too quickly; wait a moment")
            for old in s.turns.values():
                old.request_cancel()  # a newer generation supersedes a turn still running; one already past the barrier is left alone
            s.generation = generation
            s.last_activity = self.clock()
            turn = Turn(turn_id=turn_id, generation=generation, text=text.strip(), input_mode=input_mode, memory_user=s.memory_user, confidence=confidence)
            s.turns[turn_id] = turn
            while len(s.turns) > self.max_turns_kept:
                s.turns.pop(next(iter(s.turns)))
            return turn

    @staticmethod
    def _clarify(turn: Turn) -> bool:
        return len(turn.text.split()) < 2 or (turn.confidence is not None and turn.confidence < LOW_CONFIDENCE)

    def run_turn(self, principal: Principal, session_id: str, turn: Turn) -> Iterator[dict]:
        """Yield the events of one turn: status, then sentences, then final (or error). Closing the generator (a dropped connection) cancels the turn."""
        s = self._get(principal, session_id)
        try:
            yield {"event": "status", "data": {"turn_id": turn.turn_id, "state": "thinking"}}
            if self._clarify(turn):
                yield from self._finish_clarify(turn)
                return
            future = self._pool.submit(self.assistant.ask, turn.text, s.plan_id, principal, turn)
            deadline = self.mono() + self.run_timeout_s
            while not future.done():
                if turn.cancelled() or self.mono() > deadline:
                    break
                wait([future], timeout=0.02)  # wakes the moment the answer is ready; otherwise looks again for a cancel every 20 ms
            if not future.done():
                turn.request_cancel()  # a no-op if it was already cancelled, or if the barrier was passed in this same instant
                if turn.state == "cancelled":
                    yield {"event": "error", "data": {"turn_id": turn.turn_id, "code": "cancelled", "message": "this turn was cancelled; nothing was saved"}}
                    return
            try:
                result = future.result()
            except Exception:
                turn.request_cancel()
                yield {"event": "error", "data": {"turn_id": turn.turn_id, "code": "failed", "message": "the assistant could not answer; try again or type your question"}}
                return
            if result.outcome == "cancelled":
                yield {"event": "error", "data": {"turn_id": turn.turn_id, "code": "cancelled", "message": "this turn was cancelled; nothing was saved"}}
                return
            yield from self._deliver(s, turn, result)
        except GeneratorExit:
            self._abandon(s, turn)
            raise

    def _finish_clarify(self, turn: Turn) -> Iterator[dict]:
        spoken = spoken_answer(CLARIFY)
        turn.state, turn.spoken = "answered", spoken
        yield {"event": "sentence", "data": {"seq": 0, "text_spoken": CLARIFY, "text_display": CLARIFY}}
        yield {"event": "final", "data": {"turn_id": turn.turn_id, "answer": CLARIFY, "spoken_text": CLARIFY, "proposals": [], "outcome": "clarify"}}

    def _deliver(self, s: Session, turn: Turn, result) -> Iterator[dict]:
        readback = "; ".join(p.summary for p in result.proposals)
        spoken = spoken_answer(result.answer, readback=readback)
        if not verify_spoken(result.answer, spoken.text, readback):  # the rewrite must add nothing; if it ever did, say the plain words instead
            plain = " ".join(strip_markup(x) for x in split_sentences(result.answer)[:3])
            spoken = spoken_answer(plain, readback=readback)
            if not verify_spoken(result.answer, spoken.text, readback):
                spoken = spoken_answer("The answer is on screen.", readback="")
        turn.result, turn.spoken = result, spoken  # the turn is already committed (state "answered" was set at the barrier)
        s.last_activity = self.clock()
        for seq, (say, show) in enumerate(spoken.sentences):
            if turn.cancelled():
                return
            yield {"event": "sentence", "data": {"seq": seq, "text_spoken": say, "text_display": show}}
        yield {"event": "final", "data": {
            "turn_id": turn.turn_id, "answer": result.answer, "spoken_text": spoken.text, "outcome": result.outcome,
            "proposals": [{"id": p.id, "tool": p.tool, "summary": p.summary, "status": p.status} for p in result.proposals],
        }}

    def _abandon(self, s: Session, turn: Turn) -> None:
        """The connection closed. Before the barrier that is a cancel; after it the answer and its proposals are committed and stay (the user can still decide them)."""
        turn.request_cancel()

    # -------------------------------------------------------------- cancel

    def cancel_turn(self, principal: Principal, session_id: Any, turn_id: Any, generation: Any, playback_chars: Any) -> dict:
        """Cancel one turn of one session. Every miss (unknown, ended, someone else's, wrong plan) is the same generic 404; a wrong generation on the
        caller's own session is a 409 that changes nothing.

        Before the commit barrier: ``cancelled``, nothing was written. After it the turn is final: ``already_final`` (nothing is withdrawn or dropped, and
        nothing says "nothing was saved"), or ``interrupted`` when playback stopped part way, which only annotates the stored answer with what was played."""
        if isinstance(playback_chars, bool) or not isinstance(playback_chars, int) or not 0 <= playback_chars <= 100_000:
            raise VoiceError(400, "playback_chars must be a whole number of characters played so far")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise VoiceError(400, "generation must be the turn's generation")
        with self._lock:
            s = self._sessions.get(session_id) if isinstance(session_id, str) else None
            turn = s.turns.get(turn_id) if s is not None and isinstance(turn_id, str) else None
            if s is None or turn is None or s.user_id != principal.user_id or not self._alive(s):
                raise VoiceError(404, GENERIC)
            if turn.generation != generation:
                raise VoiceError(409, "that turn is not the one with this generation")
            if not _bounded(s.cancel_times, self.mono(), self.cancels_per_min):
                raise VoiceError(429, "too many cancels too quickly; wait a moment")
            s.last_activity = self.clock()
        if turn.request_cancel():
            return {"turn_id": turn_id, "state": "cancelled"}
        with turn._lock:
            if turn.state == "cancelled":
                return {"turn_id": turn_id, "state": "cancelled"}  # a second cancel changes nothing
            if turn.state == "interrupted":
                return {"turn_id": turn_id, "state": "interrupted", "played_chars": turn.played_chars}
            total = len(turn.spoken.text) if turn.spoken else 0
            if playback_chars == 0 or playback_chars >= total:
                turn.state = "done" if playback_chars >= total and total else turn.state
                return {"turn_id": turn_id, "state": "already_final"}
            self.assistant.sessions.amend_last(s.plan_id, s.memory_user, self._played_prefix(turn, playback_chars) + " " + INTERRUPTED)
            turn.state, turn.played_chars = "interrupted", playback_chars
            return {"turn_id": turn_id, "state": "interrupted", "played_chars": playback_chars}

    @staticmethod
    def _played_prefix(turn: Turn, chars: int) -> str:
        """The part of the displayed answer that corresponds to ``chars`` characters of speech: whole sentences played, and a proportional part of the one in progress.
        An estimate from the speech engine's boundary events, not proof of what the user heard."""
        out: list[str] = []
        offset = 0
        for say, show in turn.spoken.sentences:
            if say == MORE:
                break
            end = offset + len(say)
            if chars >= end:
                out.append(show)
                offset = end + 1  # the space between sentences
                continue
            if chars > offset:  # part of this sentence was played: the same share of its words
                words = show.split()
                out.append(" ".join(words[: max(1, int(len(words) * (chars - offset) / len(say)))]) + "...")
            break
        return " ".join(out)
