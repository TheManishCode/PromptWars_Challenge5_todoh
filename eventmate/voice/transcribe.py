"""Turning a recorded voice note into text on this machine (docs/VOICE.md).

No engine is enabled by default: until the owner installs one and sets ``EVENTMATE_VOICE_ENGINE`` the capability says ``none`` and transcription
answers with a clear error. A transcript is never invented, and the audio never leaves this machine or outlives the call. The transcript is untrusted
text: it goes through the same input checks as typed text before anything acts on it.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Mapping, NamedTuple, Optional, Protocol

from eventmate.voice import audio

TIMEOUT_MARGIN_S = 30
MAX_TRANSCRIPT_CHARS = 5000


class TranscribeError(Exception):
    """Transcription cannot be done. ``status`` is the HTTP status; the message is safe to show and names what to do instead."""

    def __init__(self, status: int, message: str, code: str = "transcribe_failed"):
        super().__init__(message)
        self.status = status
        self.code = code  # machine readable, for the {"error", "code"} body


class Transcript(NamedTuple):
    text: str
    language: str
    duration_s: float


class Engine(Protocol):
    name: str
    model: str
    languages: tuple

    def transcribe(self, path: Path, language: Optional[str]) -> Transcript: ...


class FasterWhisperEngine:
    """faster-whisper (MIT) on this machine, loaded from a local model folder and never downloading anything. Optional: it is not a dependency of this
    project and this class has not been run by the author of this change (see docs/VOICE.md for how to measure it)."""

    name = "faster-whisper"
    languages = ("en", "hi", "mr")

    def __init__(self, model_path: str, device: str = "cpu", compute_type: str = "int8"):
        from faster_whisper import WhisperModel  # optional; imported only when the owner has enabled this engine

        self.model = Path(model_path).name
        self._model = WhisperModel(model_path, device=device, compute_type=compute_type, local_files_only=True)

    def transcribe(self, path: Path, language: Optional[str]) -> Transcript:
        segments, info = self._model.transcribe(str(path), language=language if language in self.languages else None, vad_filter=True)
        return Transcript(" ".join(s.text.strip() for s in segments).strip(), info.language, float(info.duration))


def engine_from_env(environ: Mapping[str, str]) -> Optional[Engine]:
    """The engine the owner enabled, or None. Off unless ``EVENTMATE_VOICE_ENGINE=faster-whisper`` and ``EVENTMATE_VOICE_MODEL`` names an existing folder."""
    if environ.get("EVENTMATE_VOICE_ENGINE", "").strip().lower() != "faster-whisper":
        return None
    model = environ.get("EVENTMATE_VOICE_MODEL", "").strip()
    if not model or not Path(model).is_dir():
        return None
    try:
        return FasterWhisperEngine(model, environ.get("EVENTMATE_VOICE_DEVICE", "cpu"), environ.get("EVENTMATE_VOICE_COMPUTE", "int8"))
    except Exception:  # not installed, or the folder is not a model: voice stays off rather than failing the app
        return None


class Transcriber:
    def __init__(self, engine: Optional[Engine], tmp_dir: Path, max_seconds: int = audio.MAX_SECONDS, max_bytes: int = audio.MAX_BYTES, queue_limit: int = 3,
                 timeout_margin_s: float = TIMEOUT_MARGIN_S):
        self.engine, self.max_seconds, self.max_bytes = engine, max_seconds, max_bytes
        self.tmp_dir, self.queue_limit, self.margin = Path(tmp_dir), queue_limit, timeout_margin_s
        self._running = threading.Lock()  # one transcription at a time: the engine is heavy
        self._count = threading.Lock()
        self._waiting = 0
        self._stuck = None  # the future of a transcription that outlived its timeout: it cannot be killed, so nothing new starts until it ends (F-006)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcribe")

    def capabilities(self) -> dict:
        if self.engine is None:
            return {"route": "none", "engine": None, "model": None, "languages": [], "max_seconds": self.max_seconds, "max_bytes": self.max_bytes}
        return {"route": "server", "engine": self.engine.name, "model": self.engine.model, "languages": list(self.engine.languages), "max_seconds": self.max_seconds, "max_bytes": self.max_bytes}

    def transcribe(self, data: bytes, declared_type: str, language: Optional[str] = None) -> Transcript:
        """Check the upload, run the engine, delete the audio. Raises TranscribeError with the HTTP status to report."""
        if self.engine is None:
            raise TranscribeError(503, "voice transcription is not set up here; type your message instead", "transcriber_unavailable")
        try:
            kind = audio.check(data, declared_type, self.max_bytes)
        except audio.AudioRejected as exc:
            raise TranscribeError(exc.status, str(exc), {413: "audio_too_large", 415: "bad_audio_type"}.get(exc.status, "bad_audio")) from None
        length = audio.probe_seconds(data, kind)
        if length is not None and length > self.max_seconds:
            raise TranscribeError(413, f"the recording is longer than {self.max_seconds} seconds", "audio_too_long")
        with self._count:
            if self._stuck is not None and not self._stuck.done():
                raise TranscribeError(503, "voice transcription is still finishing an earlier recording; try again later or type your message", "transcriber_unavailable")
            self._stuck = None
            if self._waiting >= self.queue_limit:
                raise TranscribeError(429, "voice transcription is busy; try again in a moment", "busy")
            self._waiting += 1
        try:
            with self._running:
                return self._run(data, kind, length, language)
        finally:
            with self._count:
                self._waiting -= 1

    def _run(self, data: bytes, kind: str, length: Optional[float], language: Optional[str]) -> Transcript:
        self.tmp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.tmp_dir, suffix=audio.EXTENSION[kind])  # a random name: nothing the client sent is used as a path
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            limit = 2 * (length if length is not None else self.max_seconds) + self.margin
            future = self._pool.submit(self.engine.transcribe, path, language)
            try:
                result = future.result(timeout=limit)
            except FutureTimeout:
                self._stuck = future  # a thread cannot be stopped: do not pile more work behind it
                raise TranscribeError(504, "transcription took too long; try a shorter note or type your message", "transcribe_timeout") from None
            except TranscribeError:
                raise
            except Exception:
                raise TranscribeError(500, "the recording could not be transcribed; try again or type your message") from None
        finally:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()  # gone as soon as the engine is done with it, on every path
        if result.duration_s > self.max_seconds:
            raise TranscribeError(413, f"the recording is longer than {self.max_seconds} seconds", "audio_too_long")
        text = " ".join(result.text.split())[:MAX_TRANSCRIPT_CHARS]
        if not text:
            raise TranscribeError(422, "no speech was recognised; record again or type your message", "no_speech")
        return Transcript(text, result.language, round(result.duration_s, 2))
