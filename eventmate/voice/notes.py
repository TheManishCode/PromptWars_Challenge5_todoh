"""Keeping the original voice note with a plan: opt-in, off by default (docs/VOICE.md).

By default a recording is deleted the moment it has been transcribed. If the owner turns this on and the user chooses to keep a note, it is stored
under a random name in a private folder for that plan, never put in a log, a trace or a prompt, never sent to a model, and removed when the note, the
plan or its retention period (default 30 days) ends.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from eventmate.retention import PLAN_ID, wipe
from eventmate.voice import audio

NOTE_ID = re.compile(r"[0-9a-f]{32}")
CONTENT_TYPE = {".webm": "audio/webm", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".wav": "audio/wav"}


class NotesDisabled(RuntimeError):
    """Keeping originals is not turned on (``EVENTMATE_VOICE_KEEP_ORIGINAL=1``)."""


class VoiceNoteStore:
    def __init__(self, root: Path, enabled: Optional[bool] = None, retention_days: Optional[int] = None, clock: Callable[[], float] = time.time):
        self.dir = Path(root) / "voicenotes"
        self._enabled, self._days, self._clock = enabled, retention_days, clock

    @property
    def enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        return os.environ.get("EVENTMATE_VOICE_KEEP_ORIGINAL", "").strip().lower() in ("1", "true", "yes")

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def retention_days(self) -> int:
        return self._days if self._days is not None else max(1, int(os.environ.get("EVENTMATE_VOICE_NOTE_DAYS", "30")))

    def _plan_dir(self, plan_id: str) -> Path:
        if not PLAN_ID.fullmatch(plan_id):
            raise ValueError("invalid plan id")
        return self.dir / plan_id

    def _expired(self, path: Path) -> bool:
        return self._clock() - path.stat().st_mtime >= self.retention_days * 86400

    def save(self, plan_id: str, data: bytes, declared_type: str) -> dict:
        """Check the recording like any upload, then store it. Raises NotesDisabled, or audio.AudioRejected for a bad file."""
        if not self.enabled:
            raise NotesDisabled("keeping voice notes is not turned on")
        kind = audio.check(data, declared_type)
        d = self._plan_dir(plan_id)
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        note_id = secrets.token_hex(16)
        path = d / f"{note_id}{audio.EXTENSION[kind]}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        seconds = audio.probe_seconds(data, kind)
        return {"note_id": note_id, "duration_s": None if seconds is None else round(seconds, 2), "bytes": len(data)}

    def _find(self, plan_id: str, note_id: str) -> Optional[Path]:
        if not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id):
            return None
        d = self._plan_dir(plan_id)
        for ext in CONTENT_TYPE:
            p = d / f"{note_id}{ext}"
            if p.is_file() and not p.is_symlink():
                return None if self._expired(p) else p
        return None

    def read(self, plan_id: str, note_id: str) -> Optional[Tuple[bytes, str]]:
        p = self._find(plan_id, note_id)
        return None if p is None else (p.read_bytes(), CONTENT_TYPE[p.suffix])

    def delete(self, plan_id: str, note_id: str) -> bool:
        p = self._find(plan_id, note_id)
        return p is not None and wipe(p)

    def delete_plan(self, plan_id: str) -> int:
        """Remove every kept note of a plan. Returns how many files there were."""
        d = self._plan_dir(plan_id)
        if not d.is_dir():
            return 0
        gone = sum(wipe(p) for p in d.iterdir() if p.is_file() or p.is_symlink())
        try:
            d.rmdir()
        except OSError:
            pass
        return gone

    def purge_expired(self) -> List[str]:
        """Remove notes older than the retention period. Returns the note ids removed."""
        removed: List[str] = []
        if not self.dir.is_dir():
            return removed
        for plan in self.dir.iterdir():
            if not plan.is_dir() or not PLAN_ID.fullmatch(plan.name):
                continue
            for p in plan.iterdir():
                if p.is_file() and not p.is_symlink() and NOTE_ID.fullmatch(p.stem) and self._expired(p) and wipe(p):
                    removed.append(p.stem)
            try:
                plan.rmdir()
            except OSError:
                pass
        return removed
