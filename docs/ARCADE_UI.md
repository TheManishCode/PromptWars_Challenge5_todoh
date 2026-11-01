# to;doh-the event planner Arcade UI (revision 2 — cinematic 3D)

Process map for the `/arcade` page: a 3D arcade floor with a working cabinet, an
attendant character, and a chat. Every moving part below maps to real pipeline
code — nothing on screen claims a stage happened that did not.

## What the user sees, in order

1. **Room powers on** — fluorescent light flickers, the cabinet marquee lights up
   (`TO;DOH // PLANNING ENGINE`), and the attendant character (a small rounded
   robot on a pedestal) wakes and turns to face the cabinet.
2. **Dormant loop inside the cabinet screen** — before any coin, the CRT runs the
   real workflow loop slowly and *uniquely*: parse -> decompose -> optimize ->
   verify -> dossier, as a water-cooled circuit (rigid tubes, elbow fittings, a
   BRIEF reservoir on the left that drains and refills). Continuous liquid, never
   dashed packets. Module state comes only from `/api/arcade/pulse`.
3. **Chat stays a chat** — the intake conversation happens in the normal chat
   overlay at the bottom. It drives the intake API.
4. **Coin only after confirmation** — the BUILD MY PLAN button is the coin. The
   attendant presses it, a coin arcs into the slot, the machine accepts it, and
   the camera pushes in until the CRT is nearly full-screen.
5. **Stage run with liquid fuel** — each stage's real duration (from the pulse
   timestamps) fills the active module's tube with liquid blue; the reservoir
   level drains as the run consumes it. `REPLAY` labeling applies whenever the
   run is a replay of recorded timings (slowed for watchability) — shown on the
   CRT header and in a fixed banner.
6. **Completion** — camera pulls back, the cabinet shakes strongly (subtle
   cogwheel audio), and sheets drop into the bottom tray. The attendant reaches
   for them, and the top sheet flies off and presses against the glass in front
   of the camera — that pressed sheet is the real, readable dossier (or the
   honest PAPER JAM slip on failure).
7. **Failure path** — an infeasible brief jams: the slip lists real violations
   and suggested repairs, a red PAPER JAM stamp, and any in-progress replay is
   stopped honestly rather than looping a fake success.

## Element -> code map

| On screen | Backed by |
|---|---|
| Module lights / stage order / stage durations | `GET /api/arcade/pulse` -> `eventmate/arcade_pulse.py`, which records real planner hooks (`eventmate/agents/planner.py`, `eventmate/agents/manager.py`) |
| Vendor table, budget split, schedule, check counts | The real plan JSON returned by the intake/build API  |
| Dossier stamp | `verification_label()` — amber CHECKED unless every check including the symbolic solver passed; never a blanket VERIFIED |
| Symbolic (PDDL) ribbon | Shown only when the solver actually ran; otherwise an explicit "unavailable - NOT symbolically confirmed" warning box |
| PAPER JAM slip | Real constraint violations + repair suggestions from the planner response |
| REPLAY banner | Set only when the scene replays recorded stage timings rather than live pulses |

## 3D implementation

- `eventmate/web/static/arcade3d.js` — the whole scene (room, cabinet, attendant,
  coin, sheets, CRT canvas texture with snake-routed rigid tubes, elbow fittings,
  reservoir, camera tweens, WebAudio-synthesized sound). No build step.
- `eventmate/web/static/vendor/three.min.js` — Three.js r128, MIT license, vendored
  locally so the page is CSP-safe and offline-capable.
- `eventmate/web/static/arcade.js` — chat/intake/dossier/jam logic; calls into
  the scene (`Arcade3D.powerOn / coinInsert / stageRun / finishRun / livePulse /
  resetWorkflow / zoomIn / setStageMs`).
- `eventmate/web/static/arcade.html`, `arcade.css` — page shell, chat overlay,
  paper dossier, replay banner, sound toggle, WebGL fallback message.
- `web/app.py` — `STATIC_FILES` lists every servable file by flat name; files in
  `VENDOR_FILES` are read from `static/vendor/` (third-party code, kept apart from
  ours). Text and model files are gzipped when the browser asks.

## Honesty guarantees (load-bearing)

- Module lights come only from recorded pulse events; if the pulse feed is empty
  the screen says so instead of inventing progress.
- Replays are labeled REPLAY on the CRT header and in a fixed banner, and state
  that timings are recorded and slowed.
- Amber CHECKED / NEEDS VERIFICATION semantics are unchanged from
  `verification_label()`; the symbolic-check ribbon appears only on a real run.
- The dossier footer still states the plan books nothing, commits nothing, sends
  nothing, pays nothing.
- Failure renders the real violation list; the UI never converts a jam into a
  success animation.

## Accessibility and sound

Target: WCAG 2.2 AA, with no change to the look. Proved three ways, all in `tests/` and runnable with `pytest tests/test_a11y_e2e.py`:
axe-core 4.14.0 (vendored in `tests/vendor`, hash-checked, never served) finds nothing in any state on a laptop and a phone; text contrast is **measured from rendered pixels**
(`tests/browser_helpers.py`: axe can only say "incomplete" over a canvas or a gradient); and the keyboard flows below are driven with a real keyboard in a real Chromium.
Lighthouse (13.6.0, mobile and desktop emulation, software GL, 2026-11-01) scores accessibility 100 and best practices 100 on both (best practices was 96: the console error was the road theatre's three missing models).

- **Keyboard.** The room is a canvas, so everything a tap opens has a real button in `#room-nav`, shown only while the keyboard is on it (a pointer user never sees it).
  Tab reaches it; Enter opens the signature wall, the Hall of Fame table (once unlocked), each vending machine or the secret dossier; focus moves to Back to the room;
  Escape or Back closes the view and returns focus to the button that opened it. On the wall gate, Left/Right arrow keys line up the scratches exactly as a drag does, and
  say "Closer." or "Further out of line."; in a round the board's spots are real buttons in the canvas's order, so the keyboard plays the same search a pointer does (no
  shortcut: names are listed in the spots the board shows, and not at all before the round); the wall pages through its sections (PREV/NEXT) and opens on the visitor's own.
- **Screen readers.** One polite live region (`#sr-live`) announces what the canvas shows and what changed (view opened, gate closer/further, latch, plan ready, paper jam).
  The paper is a labelled dialog that takes focus when it appears, closes with Escape and hands focus back; tab clicks keep focus on the tab row. Every control has a name
  (a safety net names any new paper field from its placeholder). Wide tables scroll inside a named region that is focusable only when it really scrolls.
- **No WebGL = text-only mode.** The planner runs without the room: describe the event, check the brief, BUILD, and the same dossier (plan, overview, ideas, timeline,
  vendors, budget, ...) appears. `Arcade3D.disable()` makes every scene call a no-op. (Before this, BUILD threw and the notice linked to a classic page that no longer exists.)
- **Contrast.** AA (4.5:1, 3:1 for large text) over the live room on a laptop and a phone, in text-only mode, on the credits panel, the review card, all ten paper tabs and
  every state of the wall panel. A soft dark floor under the chat strip (`body::after`, below the paper) and lighter hint, placeholder and link text did most of it; the
  wall panel is darker glass. Found and fixed: hint 1.38:1, title over the van 1.44:1, wall buttons 3.6:1, NEW ROUND 1.98:1.
- **Touch and size.** Every control is at least 24 px each way (WCAG 2.2 2.5.8) and 44 px on a touch screen (`pointer: coarse`). The open paper always ends above the chat
  strip: its clearance is the strip's measured height (`--bar-h`), not three hard-coded breakpoints.
- **Phones.** The chat starts tucked away under 700 px; sending a message now reopens it. (Before, the reply and the BUILD button were invisible after sending.)
- **Reduced motion** stops the camera glide and sway, tube-light flicker, drifting dust, the slot-glow pulse, the attendant's head scan and visor blink, and the recording dot's pulse.
- Audio is synthesized in-page (WebAudio) and starts only after the user clicks BUILD MY PLAN; a SOUND toggle in the chat header mutes everything.

**Not done, and what that means.** No human screen-reader pass (NVDA, VoiceOver, TalkBack) was made: the claims above are automated checks plus keyboard flows. The 3D scene's
own contents are not described (the names on the wall and the machines' labels are listed instead). Contrast was measured in the states listed, not in every possible one
(error bubbles, the live-voice panel, the guest-list preview). The wall round is a visual-search game; its keyboard form is the same search over the same list.

## Running it

    python -m eventmate.web
    # then open http://127.0.0.1:8765/arcade

Seed data has Pune venues for 2026-12-12 and 2026-12-19. Example brief:
"engagement party for 120 guests in Pune on 2026-12-12 with budget 800000
rupees, catering and photography". An infeasible brief (e.g. 20000 rupees for
200 guests) exercises the jam path.

## Performance

- **Interactive in about a tenth of a second.** `boot()` used to build the room (scene, shaders, models) before wiring the chat, so the composer was dead for 2-4 s on this
  software-GL machine and Enter reloaded the page. Now everything a visitor can touch is wired first and the room starts after the first paint. Measured here
  (`performance` marks `todoh:ui-ready` and `todoh:room-ready`): DOMContentLoaded 2.3 s (phone size) and 4.2 s (laptop) before, 0.05-0.18 s after; the room itself still
  arrives at 1.9-2.4 s. First-paint figures on this machine move between 0.2 s and 1.3 s with load and are not a result. `build()` waits for the room, so a very quick visitor is safe.
- **Quality ladder on p95, not the average.** A device that averages 28 ms but stutters is a stuttering device. The 95th-percentile frame over a 75-frame window above 34 ms
  (a PROPOSED budget of about 30 fps, not a measurement from a device) first drops the render density to 75%, then takes the light path (pixel ratio 1, no bloom, throttled CRT
  redraw, every second frame skipped). The check starts again when a heavy prop lands. A gap over 1 s (a hidden tab) is not a frame. `Arcade3D.frameStats()` returns
  `{n, p50, p95, max, tier, ladder, lowFx, pixelRatio}` over the last 150 frames. Test: a device throttled 4x and 8x always ends inside budget or on the light path.
- **The road theatre is opt-in** (`<html data-road-assets="1">`, once `road.glb`, `car.glb`, `barrier.glb` exist). Without it the page makes no second WebGL context and no
  request that can only 404; the cabinet screen says "ROAD THEATRE NOT INSTALLED".
- **Payload, measured 2026-11-01** (first 25 s, gzip on the wire): 273 KB of html, css and js; models 14.2 MB on a laptop and 7.7 MB on a phone (the low tier), of which
  `wall_mural.low.glb` is 3.6 MB. The 55 MB cat machine is the visitor's choice on a phone. `tests/test_front_performance_e2e.py` holds these as ratchets that only go down.
  Two levers are not taken here (they sit in files outside this lane; see `handoffs/thread-Y.md`): raising `GZIP_MAX` above the mural and the retro machine, and meshopt
  geometry (measured: mural 3.61 -> 0.90 MB, holo table 1.53 -> 0.46 MB; the CSP and the loader are ready, the decoder file is not vendored).
- **Not measured: a real phone.** No device was attached, so there are no real-GPU frame times; this sandbox renders WebGL in software and its frame times (and Lighthouse's
  performance score of 46 mobile / 61 desktop, total blocking time dominated by software rendering) say nothing about a phone. `tools/device/` drives a real one over USB.
- The standby CRT is static and is not redrawn until state changes; rendering pauses with the tab (requestAnimationFrame), so an idle hidden tab costs nothing.
- `?lowfx=1` forces the light path immediately (used for software-GL verification).

## Known limits

- Dev-machine preview may lack Fast Downward / VAL; in that case the dossier
  honestly shows the symbolic check as unavailable (amber), by design.
- The room at `/` is the only interface. The classic page, its script and style, and the `<model-viewer>` that drew its avatar were removed on the owner's
  instruction; `/arcade` redirects to `/`. Features that only the classic page had a screen for are listed in `docs/archive/FRONTEND_INTEGRATION.md`.
- The scene targets desktop WebGL; mobile renders the same scene at reduced
  resolution with the chat overlay collapsing to a header bar.


Credits: the page's footer and `docs/ATTRIBUTIONS.md` list the third-party models and their licences. They record what the package claims; the provenance of the rebuilt cabinet was not independently verified.

## v9 updates (back-alley composition + opt-in heavy asset)

- Composition (user-directed): cabinet pulled back against the mural wall; the back wall carries ONLY the full-wall girl mural (adapted scan, see ATTRIBUTIONS); van in the left foreground with its rear half intentionally out of frame, nose toward the cabinet; both neon signs removed; side walls are generated graffiti + cropped poster pieces.
- The big cat vending machine (54.9 MB, CC BY-NC-ND, original bytes) is OPT-IN via the "+ CAT 55MB" HUD button: measured to freeze the software-rendered preview; no auto-load, no real-GPU performance claims.
- Zoomed CRT view frames the screen into the visible band above the persistent bottom bar so the 10-stop progress strip stays readable.
- The CRT runs the road/car module theatre (10 stops, gates, stop labels) on top of the 355ee57 backend and pulse semantics.
