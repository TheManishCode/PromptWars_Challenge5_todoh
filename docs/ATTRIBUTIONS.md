# Third-party assets

Two files in `eventmate/web/static/` are not ours. Both are served only by the allow-list in `eventmate/web/app.py`, and both are loaded lazily, so a failure to load either leaves the page working without the avatar.

## Assistant avatar: `avatar.glb`

- Title: Polytoria Avatar V3 "Pauly"
- Author: dantheman (https://sketchfab.com/beachr267)
- Source: https://sketchfab.com/3d-models/polytoria-avatar-v3-pauly-2c1fbaf2dec1411ca1ae5bfd0afaaa9e
- Licence: Creative Commons Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/). Attribution is required and is shown in the room's footer.
- Changes: none. The file is byte-for-byte what was downloaded (it carries its own licence and author in its metadata).
- SHA-256: `cf98b66b20b9480db1e981eb65c56861a88da547af91784919c6263100729673`
- Why this model: 880 KB and about 3,500 triangles, and it ships 23 animations (Idle, Wave, Point, Dance and others) so the avatar can react without any extra tooling. A second candidate (a commercial brand mascot) was not used: it had no animations, was larger, and is a trademarked character.

## Content-Security-Policy impact

Found by running it in a real browser against the strict policy, then loosened as narrowly as possible:

- `style-src` gained one exact hash (`sha256-PXtZZkJMMyc18o/DN1ZEFOJ2qEGq95IQbVaJFMOXOb0=`), the one fixed `<style>` block the viewer injects. It is a hash, not `unsafe-inline`, so any other inline style is still blocked. A viewer upgrade may change the block and its hash; the browser test fails loudly if so.
- `img-src` and `connect-src` gained `blob:`: the viewer decodes the model's textures from the model file and fetches them through `blob:` URLs that its own script creates. Only same-origin script can create one, so this admits nothing from outside the page.
- `script-src` gained `'wasm-unsafe-eval'`: the viewer compiles a small WebAssembly decoder at start-up (used for meshopt-compressed models; this avatar is not one, but without the source the browser logs an error on every page load). The keyword permits WebAssembly compilation only; string `eval` stays forbidden, and only same-origin scripts can run at all.
- Still absent: `unsafe-inline`, `unsafe-eval`, `worker-src`, any `http:`/`https:` source. A test pins all of this.

To update either file: replace it, update the hash above, and re-run `tests/test_web.py` and `tests/test_ui_e2e.py`.

## The /arcade page (`cabinet.glb`, `three.min.js` and the loaders)

- `cabinet.glb`: "Game-ready arcade machine" by Snji (https://sketchfab.com/snji_), https://sketchfab.com/3d-models/game-ready-arcade-machine-25fb590707034cff8b555953d66a0f7b, CC BY 4.0 (the file's own metadata says so). The patch's notes say the original artwork was replaced with to;doh-the event planner's own art; that is the patch author's change and was not independently checked beyond the file's licence metadata. Modified by to;doh-the event planner: the 2048 px metallic-roughness texture was downscaled to 1024 px to halve the download (2.1 MB to 1.1 MB); nothing else was changed. SHA-256: `de45e67aa8889972fa17f415aeb7ac1b12f88a6550724e9cfc188714573b84c4`
- The attendant on that page is the same Pauly model as the chat avatar (`avatar.glb`, credited above); the patch shipped a byte-identical copy as `character.glb`, which was removed.
- `three.min.js` (three.js r128, MIT, licence in `three.min.LICENSE.txt`) with the standard EffectComposer, bloom and GLTFLoader add-ons. SHA-256 of `three.min.js`: `9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2`
- Credit is shown at the bottom of the `/arcade` page and in `docs/ARCADE_UI.md`.

## Back-alley dressing and road module (v9, user-supplied models)

Placed in the `/arcade` room (exact Sketchfab pages are linked from the source
files' listings; licences as declared by their authors):

- `alley_van.glb`: "VANdalized" van by billionlioe, CC BY 4.0. Shown with the rear half intentionally out of frame.
- `alley_dumpster.glb`: dumpster by DigitalBirb, CC BY 4.0.
- `alley_candy.glb`: candy/gumball machine by Etman, CC BY 4.0.
- `vend_nuka.glb`: Nuka Cola vending machine by gunduzhan, CC BY 4.0.
- `vend_retro.glb`: retro vending machine by Minh Nguyen, CC BY-NC 4.0 (non-commercial use only).
- `vend_cat.glb`: cat vending machine by Minh Nguyen, CC BY-NC-ND 4.0. Non-commercial, and the ND terms restrict sharing adapted copies, so the original 54.9 MB scan ships UNMODIFIED. Measured effect: this file freezes the software-GL (SwiftShader) preview and may stutter on low-end devices, so it is never auto-loaded - the visitor loads it explicitly via the "+ CAT 55MB" button (or `?cat=1`), with that warning on the button.
- `wall_mural.glb`: scan by Guillermo of the "Native" girl mural after Tito Ferrara, CC BY-NC-SA 4.0. ADAPTED for this page: depth-flattened (z-scale 12%) and rescaled to cover the full back wall; torn edges retained. Share-alike obligations apply to the adapted render arrangement.
- `wall_posters.glb`: missing-person poster decal by honky, CC BY 4.0. Shown only as cropped pieces layered over the generated graffiti, never as the raw decal wall.
- `holo_table.glb`: sci-fi hologram table by irzafy, CC BY 4.0, https://sketchfab.com/3d-models/sci-fi-hologram-table-b1f81f7e06f74877ae34786142064a8d. Stock display plane and hologram content mesh hidden at runtime; the live Hall of Fame list is drawn as floating lettering inside the glow column.
- `road.glb` / `car.glb` / `barrier.glb`: the CRT road theatre (10 module stops, gates, car) carried over from the user's own earlier arcade line; same internal asset provenance as that line.
- Side-wall graffiti is original art generated in code at runtime (no third-party material). Neon signs removed per user request.

### TOP SECRET dossier (easter egg clue prop)
- "Worm Dossier M.E.G Game Ready" by NightmareBunny (@NightMareBunny_Officiel), CC-BY-4.0
  https://sketchfab.com/3d-models/worm-dossier-meg-game-ready-e94be6e9166440e69bbfd58be613957e
  Lies by the gumball machine as the easter-egg clue prop; the cover is a single
  mesh, so the paper and sticky note rise out on click instead of a hinge fold.
  The author's own description notes the note is "textured separately for easy
  customization of the contents": the sticky-note writing is drawn at runtime on
  a canvas card riding the note mesh. The source model is shipped UNMODIFIED.
