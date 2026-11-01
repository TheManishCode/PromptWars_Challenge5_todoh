#!/usr/bin/env bash
# Optimize the room's models for the web, in two tiers, without touching the originals.
#   high: textures <= 2048 (mural 4096 x 1024), WebP, pruned. For laptops and phones with room to spare.
#   low : textures <= 1024 (mural 2048 x 512), WebP, pruned. For phones and small computers: about a quarter of the GPU memory of "high" and a tenth of the original.
# The cat vending machine is CC BY-NC-ND (no derivatives): it is never processed; the page loads the original only on a capable device or on request.
# Needs: node (npm i @gltf-transform/cli sharp in a scratch directory), and the original .glb files in $SRC.
set -euo pipefail
SRC=${1:?usage: optimize_models.sh SRC_DIR OUT_DIR}; OUT=${2:?}; GT=${GLTF_TRANSFORM:-gltf-transform}
mkdir -p "$OUT"
# name-in-room : original file in $SRC  [max texture side for high | low]
while read -r name orig hi lo mw; do
  [ -f "$SRC/$orig" ] || { echo "skip $name: $SRC/$orig missing"; continue; }
  for tier in high low; do
    side=$hi; [ "$tier" = low ] && side=$lo
    w=$side; h=$side; [ -n "${mw:-}" ] && { w=$side; h=$((side / 4)); }   # the mural is 4:1
    dest="$OUT/$name.glb"; [ "$tier" = low ] && dest="$OUT/$name.low.glb"
    tmp=$(mktemp -d)
    $GT resize "$SRC/$orig" "$tmp/a.glb" --width "$w" --height "$h" >/dev/null
    $GT webp "$tmp/a.glb" "$tmp/b.glb" --quality 82 --slots '*' >/dev/null 2>&1 || cp "$tmp/a.glb" "$tmp/b.glb"
    $GT prune "$tmp/b.glb" "$tmp/c.glb" >/dev/null
    $GT dedup "$tmp/c.glb" "$tmp/d.glb" >/dev/null
    $GT meshopt "$tmp/d.glb" "$dest" --level high >/dev/null   # geometry compression: about a third of the bytes; the page ships the WebAssembly decoder (vendor/meshopt_decoder.js)
    rm -rf "$tmp"
    printf "%-16s %-5s %8s -> %8s\n" "$name" "$tier" "$(du -h "$SRC/$orig" | cut -f1)" "$(du -h "$dest" | cut -f1)"
  done
done <<'LIST'
cabinet           pacman_arcade_machine.glb                          2048 1024
alley_van         vandalized.glb                                     2048 1024
alley_dumpster    dumpster_low_poly.glb                              2048 1024
alley_candy       candy_vending_machine.glb                          1024  512
vend_nuka         nuka_cola_vending_machine.glb                      2048 1024
vend_retro        vending_machine2k_retro-futuristic.glb             2048 1024
wall_mural        native_by_tito_ferrara_-_girl_mural.glb            4096 2048 mural
wall_posters      missing_people_poster_wall_overlay_decal_dirty.glb 2048 1024
holo_table        sci-fi_hologram_table.glb                           512  512
secret_dossier    worm_dossier_m.e.g_game_ready.glb                  1024  512
LIST
