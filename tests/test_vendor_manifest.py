"""Phase 4.8: a vendored or third-party static file cannot change without its manifest entry changing."""

import hashlib
import json
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "eventmate" / "web"
STATIC = WEB / "static"
BINARY_AND_VENDOR = ("vendor/", ".glb", ".svg")
manifest = json.loads((WEB / "vendor_manifest.json").read_text(encoding="utf-8"))


def test_every_listed_file_matches_its_recorded_hash():
    assert manifest["schema_version"] == 1 and manifest["files"]
    for item in manifest["files"]:
        data = (STATIC / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"], f"{item['path']} changed: update web/vendor_manifest.json (and docs/ATTRIBUTIONS.md) in the same change"


def test_every_vendored_and_binary_file_is_listed_and_every_entry_names_its_source_licence_and_version():
    listed = {i["path"] for i in manifest["files"]}
    on_disk = {p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*") if p.is_file() and any(m in p.relative_to(STATIC).as_posix() for m in BINARY_AND_VENDOR)}
    assert on_disk == listed, f"unlisted: {sorted(on_disk - listed)}; listed but missing: {sorted(listed - on_disk)}"
    for item in manifest["files"]:
        assert all(item.get(k) for k in ("source_url", "licence", "version", "sha256")) and len(item["sha256"]) == 64, item["path"]


def test_the_manifest_is_not_served():
    assert not (STATIC / "vendor_manifest.json").exists()


def test_nothing_downloads_models_or_code_at_runtime():
    import re

    needles = re.compile(r"urlretrieve|snapshot_download|hf_hub_download|from_pretrained|pip\.main\(|subprocess[^\n]*pip install")
    hits = [f"{p.relative_to(WEB.parent)}:{n}" for p in WEB.parent.rglob("*.py") for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if needles.search(line) and not line.lstrip().startswith("#")]
    assert hits == [], hits
