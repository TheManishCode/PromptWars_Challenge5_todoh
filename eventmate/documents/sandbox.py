"""Convert an untrusted file to markdown in a separate process that can hurt nothing: a private temporary directory, a scrubbed environment (no keys), a time limit, a memory limit,
a cap on what it may write, and its own process group so a timeout takes every child with it. The file's bytes are checked first: size, extension, and that the content is the
kind the extension says."""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from eventmate.documents.worker import BUILTIN, HEAVY

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_MARKDOWN_CHARS = 500_000
TIMEOUT_S = 30.0
MEMORY_BYTES = 2 * 1024**3  # Docling loads models; the built-in engines use a few MB
MAGIC = {".pdf": (b"%PDF-",), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",), ".pptx": (b"PK\x03\x04",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",)}
ROOT = str(Path(__file__).resolve().parents[2])  # so the worker imports this checkout, whatever else is installed


class DocumentError(ValueError):
    """A plain sentence for the person who uploaded the file."""


@dataclass(frozen=True)
class Converted:
    markdown: str
    engine: str
    truncated: bool


def check(data: bytes, filename: str, max_bytes: int = MAX_FILE_BYTES) -> str:
    """Returns the lower-case extension, or raises ``DocumentError`` for a file that should not even be opened."""
    ext = Path(filename or "").suffix.lower()
    if ext not in BUILTIN | HEAVY:
        raise DocumentError("send the quote as a PDF, Word, Excel, image, CSV, HTML or text file")
    if not data:
        raise DocumentError("that file is empty")
    if len(data) > max_bytes:
        raise DocumentError(f"that file is larger than {max_bytes // (1024 * 1024)} MB")
    if ext in MAGIC and not data.startswith(MAGIC[ext]):
        raise DocumentError(f"that does not look like a real {ext[1:]} file")
    return ext


def _limits(max_out: int, memory: int, cpu_s: int):
    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_out, max_out))  # what it may write, to its output file or anywhere else
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        except (ValueError, OSError):
            pass
    return apply


def convert(data: bytes, filename: str, *, timeout: float = TIMEOUT_S, memory: int = MEMORY_BYTES, command: list[str] | None = None) -> Converted:
    """``command`` replaces the worker, for tests; it gets the file path and extension as its last two arguments."""
    ext = check(data, filename)
    work = Path(tempfile.mkdtemp(prefix="eventmate-doc-"))
    try:
        os.chmod(work, 0o700)
        src, out = work / f"input{ext}", work / "out.json"
        src.write_bytes(data)
        max_out = MAX_MARKDOWN_CHARS * 4 + 4096  # JSON escapes and multi-byte characters can quadruple a character
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": ROOT, "LANG": "C.UTF-8", "HOME": str(work), "TMPDIR": str(work), "PYTHONDONTWRITEBYTECODE": "1"}
        argv = (command or [sys.executable, "-m", "eventmate.documents.worker"]) + [str(src), ext]
        with open(out, "wb") as sink:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.DEVNULL, cwd=work, env=env,
                                    preexec_fn=_limits(max_out, memory, int(timeout) + 5), close_fds=True)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise DocumentError("reading that file took too long, so it was stopped") from None
        raw = out.read_bytes()[: max_out]
        if code != 0 and not raw:
            raise DocumentError("that file could not be read")
        try:
            answer = json.loads(raw.decode("utf-8", errors="replace"))
            ok, error = answer.get("ok") is True, answer.get("error")
            markdown, engine = answer.get("markdown", ""), str(answer.get("engine", ""))[:20]
            if not isinstance(markdown, str):
                raise ValueError
        except (ValueError, AttributeError):
            raise DocumentError("that file could not be read") from None
        if not ok:
            raise DocumentError(str(error or "that file could not be read")[:300])
        return Converted(markdown[:MAX_MARKDOWN_CHARS], engine, len(markdown) > MAX_MARKDOWN_CHARS)
    finally:
        shutil.rmtree(work, ignore_errors=True)
