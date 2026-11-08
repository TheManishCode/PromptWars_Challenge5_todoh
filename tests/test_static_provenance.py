"""Nothing is served, or shipped in the image, unless we know where it came from and may publish it.

Every media file the server will serve (`STATIC_FILES`) must be named in `docs/ATTRIBUTIONS.md`, either as third-party material with its licence or under
"Authored for this project". Nothing cropped from an outside image (`ui/artwork-source-map.json`) may be served, and a git-ignored file under the package
must be kept out of the container's build context too, since `COPY eventmate` copies what is on disk, not what git tracks.
"""
import fnmatch
import json
import re
import subprocess
from pathlib import Path

from eventmate.web.base import STATIC_FILES

ROOT = Path(__file__).resolve().parents[1]
MEDIA = (".svg", ".ttf", ".otf", ".woff", ".woff2", ".webp", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".mp4", ".webm")


def attributed():
    return set(re.findall(r"`([^`/]+\.[a-z0-9]+)`", (ROOT / "docs/ATTRIBUTIONS.md").read_text()))


def borrowed():
    src = ROOT / "ui/artwork-source-map.json"
    return {"plan-" + a["asset"] for a in json.loads(src.read_text())} if src.exists() else set()


def test_every_served_media_file_names_its_source_and_licence():
    missing = sorted(n for n in STATIC_FILES if n.endswith(MEDIA) and n not in attributed())
    assert not missing, f"served media with no entry in docs/ATTRIBUTIONS.md: {missing}"


def test_nothing_cropped_from_an_outside_image_is_served_or_referenced():
    served = sorted(set(STATIC_FILES) & borrowed())
    assert not served, f"borrowed artwork in STATIC_FILES: {served}"
    built = "".join((ROOT / "eventmate/web/static" / n).read_text() for n in ("plan.js", "plan.css") if (ROOT / "eventmate/web/static" / n).exists())
    used = sorted(n for n in borrowed() if n in built)
    assert not used, f"the built /plan page still asks for borrowed artwork: {used}"


def test_nothing_named_as_unconfirmed_is_on_the_served_list():
    text = (ROOT / "docs/ATTRIBUTIONS.md").read_text()
    unconfirmed = {n for n in re.findall(r"`([^`/]+\.[a-z0-9]+)`", text) if re.search(re.escape(n) + r"[^\n]*(not confirmed|unconfirmed|unverified)", text)}
    assert not sorted(unconfirmed & set(STATIC_FILES)), "a file whose licence is not confirmed is still on the served list"


def dockerignored(path, patterns):
    return any(fnmatch.fnmatch(path, p.rstrip("/")) or fnmatch.fnmatch(path, p.rstrip("/") + "/*") or path.startswith(p.rstrip("/") + "/") for p in patterns)


def test_git_ignored_files_under_the_package_stay_out_of_the_image():
    patterns = [ln.strip() for ln in (ROOT / ".dockerignore").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    ignored = subprocess.run(["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "eventmate", "prompts"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    leaks = sorted(p for p in ignored if not dockerignored(p.rstrip("/"), patterns) and "__pycache__" not in p and not p.endswith((".pyc", ".egg-info/")))
    assert not leaks, f"git ignores these but `docker build` would copy them into the image: {leaks}"
