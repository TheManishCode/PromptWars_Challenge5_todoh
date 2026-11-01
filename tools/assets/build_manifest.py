"""Add the room's optimised models to eventmate/web/vendor_manifest.json (run after tools/assets/optimize_models.sh and copying its output into web/static).
usage: python tools/assets/build_manifest.py ORIGINALS_DIR
Records, for every model: where it came from, its licence, the SHA-256 of the shipped file and of the original it was made from, and what was done to it."""
import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC, MANIFEST = ROOT / "eventmate/web/static", ROOT / "eventmate/web/vendor_manifest.json"
ORIG = Path(sys.argv[1])
SK = "https://sketchfab.com/"
# name in the room: (original file, licence, author and title, page)
MODELS = {
    "cabinet": ("pacman_arcade_machine.glb", "CC-BY-4.0", "PacMan Arcade Machine by Matthew", SK + "3d-models/pacman-arcade-machine-297816a91a0947fb9dc917741d4c0be4"),
    "alley_van": ("vandalized.glb", "CC-BY-4.0", "VANdalized by billionlioe", SK),
    "alley_dumpster": ("dumpster_low_poly.glb", "CC-BY-4.0", "dumpster by DigitalBirb", SK),
    "alley_candy": ("candy_vending_machine.glb", "CC-BY-4.0", "candy machine by Etman", SK),
    "vend_nuka": ("nuka_cola_vending_machine.glb", "CC-BY-4.0", "Nuka Cola vending machine by gunduzhan", SK),
    "vend_retro": ("vending_machine2k_retro-futuristic.glb", "CC-BY-NC-4.0", "retro vending machine by Minh Nguyen (non-commercial use only)", SK),
    "wall_mural": ("native_by_tito_ferrara_-_girl_mural.glb", "CC-BY-NC-SA-4.0", "Native girl mural scan by Guillermo, after Tito Ferrara (adapted: share-alike applies)", SK),
    "wall_posters": ("missing_people_poster_wall_overlay_decal_dirty.glb", "CC-BY-4.0", "missing-person poster decal by honky", SK),
    "holo_table": ("sci-fi_hologram_table.glb", "CC-BY-4.0", "sci-fi hologram table by irzafy", SK + "3d-models/sci-fi-hologram-table-b1f81f7e06f74877ae34786142064a8d"),
    "secret_dossier": ("worm_dossier_m.e.g_game_ready.glb", "CC-BY-4.0", "Worm Dossier M.E.G Game Ready by NightmareBunny", SK + "3d-models/worm-dossier-meg-game-ready-e94be6e9166440e69bbfd58be613957e"),
}
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
m = json.loads(MANIFEST.read_text())
m["files"] = [f for f in m["files"] if not f["path"].endswith(".glb") or f["path"] == "avatar.glb"]
for name, (orig, lic, title, url) in MODELS.items():
    o = sha(ORIG / orig)
    for tier, path, side in (("high", f"{name}.glb", "textures at most 2048"), ("low", f"{name}.low.glb", "textures at most 1024")):
        if name == "wall_mural": side = "mural 4096 x 1024" if tier == "high" else "mural 2048 x 512"
        m["files"].append({"path": path, "source_url": url, "licence": lic, "version": f"optimised-{tier}", "sha256": sha(STATIC / path),
                           "note": f"{title}. DERIVED from {orig} (sha256 {o}) by tools/assets/optimize_models.sh: {side}, WebP textures, pruned and de-duplicated; geometry, materials and look unchanged. Changes indicated as CC BY requires."})
m["optional"] = [
    {"path": "vend_cat.glb", "source_url": SK, "licence": "CC-BY-NC-ND-4.0", "version": "original", "sha256": sha(ORIG / "cat_vending_machine_4k.glb"),
     "note": "cat vending machine by Minh Nguyen. No derivatives allowed, so the 54.9 MB original is the only form and is NOT committed to the repository: copy it here yourself. If present it must match this hash. A laptop-class device loads it by itself; a phone or small computer loads it only on the CAT button."},
    {"path": "road.glb", "source_url": "(the user's earlier arcade line)", "licence": "(see docs/ATTRIBUTIONS.md)", "version": "n/a", "sha256": "", "note": "CRT road theatre asset, provided separately; the page shows its own message when it is absent"},
    {"path": "car.glb", "source_url": "(the user's earlier arcade line)", "licence": "(see docs/ATTRIBUTIONS.md)", "version": "n/a", "sha256": "", "note": "CRT road theatre asset, provided separately"},
    {"path": "barrier.glb", "source_url": "(the user's earlier arcade line)", "licence": "(see docs/ATTRIBUTIONS.md)", "version": "n/a", "sha256": "", "note": "CRT road theatre asset, provided separately"},
]
MANIFEST.write_text(json.dumps(m, indent=2) + "\n")
print(len(m["files"]), "files,", len(m["optional"]), "optional")
