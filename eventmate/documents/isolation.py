"""How far the file reader is shut in. It parses a file somebody else wrote, so it should reach neither the network nor the server's other files.

Three ways, tried in this order, each probed once on this machine (a probe that cannot run falls through to the next):

* ``contained``: bubblewrap. A new mount, network, process and user namespace; only the system libraries, the interpreter, this package, the model directory (read only) and the
  reader's own temporary directory are visible. No other file of the server exists for the reader: not ``.env``, not any workspace.
* ``confined``: Landlock (a Linux security module that needs no privilege) plus the socket filter below. The reader can open only the same short list of paths, read only, and its own
  directory read-write; everything else, including the server's data and ``.env``, is refused with EPERM. Works in the locked-down container, where bubblewrap cannot run.
* ``network``: ``unshare -rn``. No network, not even loopback. The reader still sees the files the server's user can read.
* ``no-sockets``: a seccomp filter that makes ``socket()`` fail (and io_uring, which can open sockets), and that refuses the whole x32 system-call range on x86-64, where an exact match on the call number is bypassed by setting the x32 bit. Needs no namespace and no privilege, so it works in the locked-down container
  the Dockerfile builds (all capabilities dropped, no-new-privileges), where the first two cannot. Same limit as ``network``: files are not hidden.

``none`` means nothing could be set up. Shared mode refuses to start then (``web/hosting.py``); locally the reader still runs unless ``EVENTMATE_REQUIRE_NETLESS=1``.
"""

from __future__ import annotations

import ctypes
import functools
import os
import platform
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LEVELS = ("contained", "confined", "network", "no-sockets", "none")
NETWORK_CUT = frozenset({"contained", "confined", "network", "no-sockets"})
FILES_HIDDEN = frozenset({"contained", "confined"})
SHARED_MODE_NEEDS = FILES_HIDDEN  # both properties: a level that hides the files also cuts the network
_BIN = "/usr/bin:/bin"

# x86-64 and arm64: the architecture numbers and the system call numbers the filter names
_ARCH = {"x86_64": (0xC000003E, 41, 425), "aarch64": (0xC00000B7, 198, 425)}  # audit arch, socket, io_uring_setup


@dataclass(frozen=True)
class Plan:
    level: str
    prefix: tuple[str, ...] = ()
    preexec: Callable[[], None] | None = None


def _seccomp_no_sockets() -> None:
    """Called in the child before exec. Every ``socket()`` and ``io_uring_setup()`` then fails with EPERM; ``socketpair()`` (connected local pairs only) still works."""
    audit, nr_socket, nr_uring = _ARCH[platform.machine()]
    EPERM = 1
    # struct sock_filter { u16 code; u8 jt; u8 jf; u32 k }
    def ins(code, jt, jf, k):
        return struct.pack("HBBI", code, jt, jf, k)

    LD_W_ABS, JEQ_K, JGE_K, RET_K = 0x20, 0x15, 0x35, 0x06
    ALLOW, KILL, DENY = 0x7FFF0000, 0x00000000, 0x00050000 | EPERM
    X32_BIT = 0x40000000  # on x86-64 the x32 ABI reports the same architecture and sets this bit in the call number: socket() is then 0x40000029 and an exact match on 41 misses it
    prog = b"".join([
        ins(LD_W_ABS, 0, 0, 4), ins(JEQ_K, 1, 0, audit), ins(RET_K, 0, 0, KILL),  # a different architecture: refuse to guess
        ins(LD_W_ABS, 0, 0, 0),  # the system call number
        *([ins(JGE_K, 0, 1, X32_BIT), ins(RET_K, 0, 0, DENY)] if platform.machine() == "x86_64" else []),  # the whole x32 range, not only the two calls: nothing here needs it
        ins(JEQ_K, 0, 1, nr_socket), ins(RET_K, 0, 0, DENY),
        ins(JEQ_K, 0, 1, nr_uring), ins(RET_K, 0, 0, DENY),
        ins(RET_K, 0, 0, ALLOW),
    ])
    n = len(prog) // 8
    buf = ctypes.create_string_buffer(prog, len(prog))

    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    fprog = SockFprog(n, ctypes.cast(buf, ctypes.c_void_p))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(fprog), 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS, PR_SET_SECCOMP / SECCOMP_MODE_FILTER
        raise OSError(ctypes.get_errno(), "could not install the socket filter")


def _landlock_enter(read_dirs: list[str], read_files: list[str], work: str, list_only: tuple[str, ...] = ()) -> Callable[[], None]:
    """A function to call in the child before exec: from then on it can open only ``read_dirs`` and ``read_files`` (read only) and ``work`` (read and write); and it has no sockets."""
    EXEC, WRITE, READ, READ_DIR, REMOVE_DIR, REMOVE_FILE = 1, 2, 4, 8, 16, 32
    MAKE = 64 | 128 | 256 | 512 | 1024 | 2048 | 4096  # char, dir, reg, sock, fifo, block, sym
    REFER, TRUNCATE = 1 << 13, 1 << 14

    def enter() -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        abi = libc.syscall(444, None, 0, 1)  # landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)
        if abi < 1:
            raise OSError(ctypes.get_errno(), "no Landlock")
        handled = EXEC | WRITE | READ | READ_DIR | REMOVE_DIR | REMOVE_FILE | MAKE | (REFER if abi >= 2 else 0) | (TRUNCATE if abi >= 3 else 0)
        attr = ctypes.c_uint64(handled)
        fd = libc.syscall(444, ctypes.byref(attr), 8, 0)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "landlock_create_ruleset")

        class Beneath(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

        def allow(path: str, access: int) -> None:
            try:
                parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                return  # a path this machine does not have (no /lib64, no model directory yet)
            try:
                rule = Beneath(access, parent)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) != 0:
                    raise OSError(ctypes.get_errno(), f"landlock_add_rule {path}")
            finally:
                os.close(parent)

        for d in read_dirs:
            allow(d, EXEC | READ | READ_DIR)
        for d in list_only:
            allow(d, READ_DIR)  # names may be listed (the import system lists the directory that holds the package); no file in it can be opened
        for f in read_files:
            allow(f, READ)
        allow("/dev/null", READ | WRITE)
        allow(work, handled)  # its own directory: everything the ruleset can grant
        _seccomp_no_sockets()  # also sets no_new_privs, which restrict_self requires
        if libc.syscall(446, fd, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
        os.close(fd)

    return enter


def _interpreter_prefixes(interpreter: str) -> list[Path]:
    """Every prefix (the directory above ``bin``) the interpreter is reached through, as written: the virtual environment, each hop of the symlinks from its ``bin/python``, and where they end. A
    uv or setup-python interpreter is reached through an alias directory (``cpython-3.12-linux-x86_64-gnu`` pointing at ``cpython-3.12.13-...``); the sandbox has to mount the interpreter's files at
    the path the symlink names, or exec finds nothing there (F10). Only the interpreter's own prefixes: nothing else of the host."""
    out: list[Path] = []
    venv = Path(interpreter).parent.parent
    if (venv / "pyvenv.cfg").exists():
        out.append(venv)
    path, hops = interpreter, 0
    while os.path.islink(path) and hops < 16:
        target = os.path.normpath(os.path.join(os.path.dirname(path), os.readlink(path)))
        out.append(Path(target).parent.parent)
        path, hops = target, hops + 1
    out.append(Path(os.path.realpath(interpreter)).parent.parent)
    return [p for p in dict.fromkeys(out) if str(p) != "/"]


def _confinement_paths(interpreter: str, package_root: str, model_home: str | None) -> tuple[list[str], list[str]]:
    # /proc is not granted whole: another process of the same user (the server) has its environment, command line and memory there. Only the reader's own entry, and the two files that describe the machine.
    dirs = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/proc/self", "/sys/devices/system/cpu", os.path.join(package_root, "eventmate")]
    for p in (*_interpreter_prefixes(interpreter), Path(sys.base_prefix)):
        if str(p) != "/":
            dirs.append(str(p))
    if model_home:
        dirs.append(model_home)
    return list(dict.fromkeys(dirs)), ["/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/mime.types", "/etc/localtime", "/proc/cpuinfo", "/proc/meminfo", "/dev/urandom", "/dev/random", "/dev/zero"]


def _binds(interpreter: str, package_root: str, work: str, model_home: str | None) -> list[str]:
    """The mounts of the ``contained`` level. Nothing else of the host is visible."""
    out = ["--tmpfs", "/tmp", "--ro-bind", "/usr", "/usr"]  # nosec B108 - a tmpfs inside the sandbox, not a host path; /tmp first: anything bound below it (a virtual environment, the work directory) must be mounted after it
    for name in ("lib", "lib64", "bin", "sbin"):
        link = Path("/") / name
        if link.is_symlink():
            out += ["--symlink", os.readlink(link), str(link)]
        elif link.is_dir():
            out += ["--ro-bind", str(link), str(link)]
    seen: set[str] = {"/usr"}
    for p in _interpreter_prefixes(interpreter):
        if str(p) not in seen and not str(p).startswith("/usr/") and str(p) != "/":
            seen.add(str(p))
            out += ["--ro-bind", os.path.realpath(p), str(p)]  # the real files, at the path the symlinks name
    out += ["--ro-bind", os.path.join(package_root, "eventmate"), os.path.join(package_root, "eventmate")]
    if model_home and os.path.isdir(model_home):
        out += ["--ro-bind", model_home, model_home]
    out += ["--bind", work, work, "--proc", "/proc", "--dev", "/dev"]
    return out


@functools.cache
def _bwrap_works(interpreter: str, package_root: str) -> bool:
    exe = shutil.which("bwrap", path=_BIN)
    if not exe:
        return False
    probe = Path(os.path.realpath(interpreter)).parent  # any existing directory works as the scratch mount for the probe
    argv = [exe, "--unshare-all", "--die-with-parent", *_binds(interpreter, package_root, str(probe), None), "--chdir", "/", interpreter, "-c", "import eventmate"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=30, env={"PATH": _BIN, "PYTHONPATH": package_root}, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@functools.cache
def _landlock_works(interpreter: str, package_root: str) -> bool:
    """Landlock can be entered, the interpreter still starts and imports this package, and a file outside the allowed paths cannot be read."""
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as work, tempfile.NamedTemporaryFile(prefix="landlock-probe-") as outside:  # the second file is in the system temp directory: outside every allowed path
            dirs, files = _confinement_paths(interpreter, package_root, None)
            code = "import eventmate, sys\ntry:\n    open(sys.argv[1]).read()\nexcept OSError:\n    sys.exit(0)\nsys.exit(3)"
            r = subprocess.run([interpreter, "-c", code, outside.name], capture_output=True, timeout=30, env={"PATH": _BIN, "PYTHONPATH": package_root, "HOME": work}, cwd=work,
                               preexec_fn=_landlock_enter(dirs, files, work, (package_root,)), check=False)
            return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@functools.cache
def _unshare_works() -> str | None:
    exe = shutil.which("unshare", path=_BIN)
    if exe:
        try:
            if subprocess.run([exe, "-rn", "true"], capture_output=True, timeout=10, check=False).returncode == 0:
                return exe
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


@functools.cache
def _seccomp_works() -> bool:
    if platform.machine() not in _ARCH:
        return False
    try:
        probe = "import socket,sys\ntry:\n    socket.socket()\nexcept OSError:\n    sys.exit(0)\nsys.exit(1)"
        return subprocess.run([sys.executable, "-c", probe], capture_output=True, timeout=10, check=False, preexec_fn=_seccomp_no_sockets).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def available(interpreter: str | None = None, package_root: str | None = None) -> str:
    """The strongest level this machine can give the reader (probed, cached)."""
    interpreter = interpreter or sys.executable
    package_root = package_root or str(Path(__file__).resolve().parents[2])
    if _bwrap_works(interpreter, package_root):
        return "contained"
    if _landlock_works(interpreter, package_root) and _seccomp_works():
        return "confined"
    if _unshare_works():
        return "network"
    return "no-sockets" if _seccomp_works() else "none"


def plan(interpreter: str, package_root: str, work: str, model_home: str | None) -> Plan:
    level = available(interpreter, package_root)
    if level == "contained":
        exe = shutil.which("bwrap", path=_BIN)
        return Plan(level, (exe, "--unshare-all", "--die-with-parent", *_binds(interpreter, package_root, work, model_home), "--chdir", work))
    if level == "confined":
        dirs, files = _confinement_paths(interpreter, package_root, model_home)
        return Plan(level, (), _landlock_enter(dirs, files, work, (package_root,)))
    if level == "network":
        return Plan(level, (_unshare_works(), "-rn", "--"))
    if level == "no-sockets":
        return Plan(level, (), _seccomp_no_sockets)
    return Plan("none")
