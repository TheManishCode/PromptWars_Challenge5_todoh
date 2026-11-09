"""The reader's sandbox works for an interpreter reached through symlinks (F10). A uv or setup-python interpreter is `venv/bin/python -> .../cpython-3.12-linux-x86_64-gnu/bin/python3.12`, where
the middle directory is itself an alias of `cpython-3.12.13-...`. The sandbox mounted only the real directory, so exec found nothing at the path the symlink names, and every `contained` level test
skipped (on a CI runner the skip guard would have failed the first run). Each prefix the interpreter is reached through is now mounted, with the real files, and nothing else of the host."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from eventmate.documents import isolation

ROOT = str(Path(__file__).resolve().parents[1])


def layout(tmp_path):
    """tmp/real/cpython-3.12.13/bin/python3.12, an alias dir tmp/tools/cpython-3.12 -> real, a venv tmp/venv/bin/python -> alias/bin/python3.12 (as uv lays them out)."""
    real = tmp_path / "real" / "cpython-3.12.13"
    (real / "bin").mkdir(parents=True)
    (real / "bin" / "python3.12").write_text("")
    alias = tmp_path / "tools" / "cpython-3.12"
    alias.parent.mkdir()
    alias.symlink_to(real)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = x\n")
    (venv / "bin" / "python").symlink_to(alias / "bin" / "python3.12")
    return venv, alias, real


def test_every_prefix_the_interpreter_is_reached_through_is_named(tmp_path):
    venv, alias, real = layout(tmp_path)
    got = isolation._interpreter_prefixes(str(venv / "bin" / "python"))
    assert got == [venv, alias, real], "the venv, the alias the symlink names, and where it really is"


def test_a_plain_interpreter_has_just_its_own_prefix(tmp_path):
    exe = tmp_path / "usr-like" / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert isolation._interpreter_prefixes(str(exe)) == [tmp_path / "usr-like"]


def test_the_sandbox_mounts_the_real_files_at_the_alias_path_and_adds_nothing_else(tmp_path):
    venv, alias, real = layout(tmp_path)
    binds = isolation._binds(str(venv / "bin" / "python"), ROOT, str(tmp_path / "work"), None)
    pairs = [(binds[i + 1], binds[i + 2]) for i, a in enumerate(binds) if a == "--ro-bind"]
    assert (str(real), str(alias)) in pairs and (str(venv), str(venv)) in pairs and (str(real), str(real)) in pairs
    host_paths = {src for src, _ in pairs}
    assert host_paths <= {"/usr", str(real), str(venv), os.path.join(ROOT, "eventmate"), "/lib", "/lib64", "/bin", "/sbin"}, "no host path beyond the interpreter's own prefixes and the package"
    dirs, _ = isolation._confinement_paths(str(venv / "bin" / "python"), ROOT, None)
    assert str(alias) in dirs and str(real) in dirs and str(venv) in dirs


def test_an_interpreter_behind_an_alias_directory_starts_inside_the_contained_sandbox(tmp_path):
    if not isolation._bwrap_works(sys.executable, ROOT):
        pytest.skip("this machine cannot set up the contained level")
    base = Path(os.path.realpath(sys.executable)).parent.parent
    alias = tmp_path / "tools" / "alias-of-the-interpreter"
    alias.parent.mkdir()
    alias.symlink_to(base)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(f"home = {base}/bin\n")
    (venv / "bin" / "python").symlink_to(alias / "bin" / Path(os.path.realpath(sys.executable)).name)
    exe = shutil_which_bwrap()
    argv = [exe, "--unshare-all", "--die-with-parent", *isolation._binds(str(venv / "bin" / "python"), ROOT, str(tmp_path), None), "--chdir", "/", str(venv / "bin" / "python"), "-c", "print('started')"]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and out.stdout.strip() == "started", out.stderr


def shutil_which_bwrap():
    import shutil

    return shutil.which("bwrap", path=isolation._BIN)
