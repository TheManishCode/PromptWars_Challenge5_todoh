"""Convert an untrusted file to markdown in a separate process that can hurt nothing: a private temporary directory, a scrubbed environment (no keys), a time limit, a memory limit,
a cap on what it may write, and its own process group so a timeout takes every child with it. The file's bytes are checked first: size, extension, and that the content is the
kind the extension says."""

from __future__ import annotations

import contextlib
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from eventmate.documents import isolation
from eventmate.documents.worker import BUILTIN, HEAVY

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_MARKDOWN_CHARS = 500_000
TIMEOUT_S = 30.0
HEAVY_TIMEOUT_S = 120.0  # Docling loads its models first
MEMORY_BYTES = 2 * 1024**3  # built-in engines use a few MB
HEAVY_MEMORY_BYTES = 3 * 1024**3  # resident memory Docling may use; its libraries map far more address space than they touch, so it is measured, not capped
MAGIC = {".pdf": (b"%PDF-",), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",), ".pptx": (b"PK\x03\x04",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",)}
ROOT = str(Path(__file__).resolve().parents[2])  # so the worker imports this checkout, whatever else is installed


def model_home() -> str:
    """Where the downloaded models are kept: ``HF_HOME`` if set, else the Hugging Face default under the real home directory."""
    return os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")


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


def _limits(max_out: int, memory: int | None, cpu_s: int):
    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_out, max_out))  # what it may write, to its output file or anywhere else
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if memory is not None:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            except (ValueError, OSError):
                pass
    return apply


def _group_rss(pgid: int) -> int:
    """Resident bytes of every process in the group (the worker and anything it started), read from /proc; 0 where /proc is not there."""
    total, page = 0, os.sysconf("SC_PAGE_SIZE")
    try:
        names = os.listdir("/proc")
    except OSError:
        return 0
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                after = fh.read().rsplit(b")", 1)[1].split()  # state, ppid, pgrp, session, tty, tpgid, flags, minflt, ... rss is the 22nd field of the line
            if int(after[2]) == pgid:
                total += int(after[21]) * page
        except (OSError, IndexError, ValueError):
            continue
    return total


def required_levels(environ=None) -> frozenset[str] | None:
    """The isolation levels a reader must reach before it may run, or None where a weaker one is allowed (locally, unless EVENTMATE_REQUIRE_NETLESS=1 asks for a network cut).
    Shared mode (strangers' files) needs the network cut and the server's other files hidden; the server will not start without it (``web/hosting.py``), and no file is read without it."""
    environ = os.environ if environ is None else environ
    if environ.get("EVENTMATE_MODE", "").strip().lower() == "shared":
        return isolation.SHARED_MODE_NEEDS
    return isolation.NETWORK_CUT if environ.get("EVENTMATE_REQUIRE_NETLESS") == "1" else None


def isolation_level(interpreter: str | None = None) -> str:
    """How far the reader is shut in on this machine: contained, network, no-sockets or none (see ``documents/isolation.py``). Probed once."""
    return isolation.available(interpreter or sys.executable, ROOT)


def convert(data: bytes, filename: str, *, timeout: float | None = None, memory: int | None = None, command: list[str] | None = None, cap_address_space: bool | None = None) -> Converted:
    """``command`` replaces the worker, for tests; it gets the file path and extension as its last two arguments. A built-in engine is held to ``memory`` of address space; a heavy
    one (Docling) is held to ``memory`` of resident memory, measured while it runs, and gets a longer time limit."""
    ext = check(data, filename)
    heavy = ext in HEAVY
    timeout = timeout if timeout is not None else (HEAVY_TIMEOUT_S if heavy else TIMEOUT_S)
    memory = memory if memory is not None else (HEAVY_MEMORY_BYTES if heavy else MEMORY_BYTES)
    cap_address_space = (not heavy) if cap_address_space is None else cap_address_space
    work = Path(tempfile.mkdtemp(prefix="eventmate-doc-"))
    try:
        os.chmod(work, 0o700)
        src, out = work / f"input{ext}", work / "out.json"
        src.write_bytes(data)
        max_out = MAX_MARKDOWN_CHARS * 4 + 4096  # JSON escapes and multi-byte characters can quadruple a character
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": ROOT, "LANG": "C.UTF-8", "HOME": str(work), "TMPDIR": str(work), "PYTHONDONTWRITEBYTECODE": "1"}
        if heavy:  # the models were fetched ahead of time (python -m eventmate.documents.warm); a conversion never reaches the network for them
            env.update({"HF_HOME": model_home(), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        base = command or [sys.executable, "-m", "eventmate.documents.worker"]
        shut = isolation.plan(base[0], ROOT, str(work), model_home() if heavy else None)
        need = required_levels()
        if need is not None and shut.level not in need:
            raise DocumentError("this machine cannot shut the file reader in as far as this setup requires (no network, and the server's other files out of reach), so reading files is switched off")
        argv = [*shut.prefix, *base, str(src), ext]
        limits = _limits(max_out, memory if cap_address_space else None, int(timeout) + 5)

        def shut_in():
            limits()
            if shut.preexec:
                shut.preexec()
        with open(out, "wb") as sink:
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.DEVNULL, cwd=work, env=env,
                                        preexec_fn=shut_in, close_fds=True)
            except (OSError, subprocess.SubprocessError):
                raise DocumentError("the file reader could not be started with its restrictions in place, so the file was not read") from None  # the restrictions failed to apply: nothing ran
            deadline, code, why = time.monotonic() + timeout, None, ""
            while code is None:
                try:
                    code = proc.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    if time.monotonic() > deadline:
                        why = "reading that file took too long, so it was stopped"
                    elif _group_rss(proc.pid) > memory:
                        why = "reading that file needed too much memory, so it was stopped"
                    if why:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                        raise DocumentError(why) from None
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
