/* to;doh-the event planner Arcade — 3D floor (Three.js r128, vendored locally, MIT).
 *
 * "Our own version" of a classic game-ready upright cabinet (silhouette and
 * layout inspired by CC-BY reference cabinets the user picked on Sketchfab;
 * every mesh, texture and sprite here is generated in code - no third-party
 * art, fonts or model data).
 *
 * Realism layer: PBR materials with a procedural environment map, real
 * shadows, UnrealBloom on the emissive parts (marquee, neon, CRT), a curved
 * CRT under glass, laminate/metal/concrete canvas textures, dust in the air
 * and a hand-held camera sway. Falls back to a plain fast path (?lowfx=1 or
 * auto-degrade) that skips bloom and halves the pixel ratio.
 *
 * The cabinet screen shows the REAL pipeline as a night road: one stop per
 * module, a boom barrier past each stop, and a car that waits at a stop while
 * that module's real work runs, then drives on when it clears. Nothing here
 * invents state: arcade.js drives every light from /api/arcade/pulse (the
 * recorded timings of the run, replayed slowed and labelled).
 */
/* global THREE */
"use strict";

window.Arcade3D = (function () {
  const S = {
    ok: false, reduced: false, muted: localStorage.getItem("em-arcade-muted") === "1",
    renderer: null, scene: null, camera: null, clock: null,
    composer: null, bloom: null, dust: null,
    tweens: [], cabinet: null, marquee: null, marqueeLight: null,
    screenMesh: null, glassMesh: null, screenLight: null, lever: null, tray: null,
    attendant: null, attBody: null, attHead: null, coin: null, sheets: [], flySheet: null,
    audio: null, master: null, slotGlow: null,
    camWide: { pos: [0, 2.35, 6.4], tgt: [0, 1.72, 0] },
    camZoom: { pos: [0.02, 1.74, 1.95], tgt: [0.02, 1.72, 0.45] },
    powered: false, shaking: 0, zoomed: false, camImpulse: 0,
    swayPrev: new THREE.Vector3(),
  };

  /* ---------------- tween engine ---------------- */
  const easeInOut = (t) => t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  function tween(durMs, ease, onUpdate) {
    return new Promise((resolve) => {
      if (S.reduced || durMs <= 0) { onUpdate(1); resolve(); return; }
      S.tweens.push({ t0: performance.now(), dur: durMs, ease: ease || easeInOut, onUpdate, resolve });
    });
  }
  function stepTweens(now) {
    S.tweens = S.tweens.filter((tw) => {
      const t = Math.min(1, (now - tw.t0) / tw.dur);
      tw.onUpdate(tw.ease(t));
      if (t >= 1) { tw.resolve(); return false; }
      return true;
    });
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, S.reduced ? 0 : ms));

  /* ---------------- synthesized audio (no assets; user-gesture gated) ---------------- */
  function userGesture() {
    if (S.muted) return;
    try {
      if (!S.audio) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        S.audio = new AC();
        S.master = S.audio.createGain();
        S.master.gain.value = 0.5;
        S.master.connect(S.audio.destination);
      }
      if (S.audio.state === "suspended") S.audio.resume();
    } catch (e) { /* audio unavailable: stay silent */ }
  }
  function env(node, t0, a, d, peak) {
    node.gain.setValueAtTime(0, t0);
    node.gain.linearRampToValueAtTime(peak, t0 + a);
    node.gain.exponentialRampToValueAtTime(0.0001, t0 + a + d);
  }
  function ping(freq, dur, peak) {
    if (!S.audio || S.muted) return;
    const t0 = S.audio.currentTime;
    const o = S.audio.createOscillator(), g = S.audio.createGain();
    o.type = "triangle"; o.frequency.value = freq;
    env(g, t0, 0.004, dur, peak);
    o.connect(g); g.connect(S.master);
    o.start(t0); o.stop(t0 + dur + 0.05);
  }
  function noiseBurst(dur, freq, q, peak, when) {
    if (!S.audio || S.muted) return;
    const t0 = when || S.audio.currentTime;
    const len = Math.max(1, Math.floor(S.audio.sampleRate * dur));
    const buf = S.audio.createBuffer(1, len, S.audio.sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < len; i++) ch[i] = Math.random() * 2 - 1;
    const src = S.audio.createBufferSource(); src.buffer = buf;
    const f = S.audio.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = freq; f.Q.value = q;
    const g = S.audio.createGain(); env(g, t0, 0.005, dur, peak);
    src.connect(f); f.connect(g); g.connect(S.master);
    src.start(t0);
  }
  const snd = {
    clink() { ping(2400, 0.09, 0.10); setTimeout(() => ping(3100, 0.12, 0.07), 45); },
    clunk() { noiseBurst(0.08, 300, 1.2, 0.16); ping(140, 0.1, 0.08); },
    servo() { /* small motor whir for the attendant's arm */
      if (!S.audio || S.muted) return;
      const t0 = S.audio.currentTime;
      const o = S.audio.createOscillator(), g = S.audio.createGain();
      const f = S.audio.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = 900; f.Q.value = 2;
      o.type = "sawtooth";
      o.frequency.setValueAtTime(150, t0);
      o.frequency.linearRampToValueAtTime(320, t0 + 0.16);
      env(g, t0, 0.01, 0.18, 0.028);
      o.connect(f); f.connect(g); g.connect(S.master);
      o.start(t0); o.stop(t0 + 0.24);
    },
    hum() { if (!S.audio || S.muted) return;
      const t0 = S.audio.currentTime;
      const o = S.audio.createOscillator(), g = S.audio.createGain();
      o.type = "sawtooth"; o.frequency.value = 68;
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(0.045, t0 + 0.3);
      g.gain.linearRampToValueAtTime(0, t0 + 1.1);
      o.connect(g); g.connect(S.master); o.start(t0); o.stop(t0 + 1.2);
    },
    cogs(durMs) { /* subtle ratchet of cogwheels under the shake */
      if (!S.audio || S.muted) return;
      const t0 = S.audio.currentTime;
      const n = Math.floor(durMs / 85);
      for (let i = 0; i < n; i++) {
        noiseBurst(0.03, 900 + Math.random() * 700, 3, 0.05, t0 + i * 0.085 + Math.random() * 0.02);
      }
      noiseBurst(durMs / 1000, 120, 0.8, 0.05, t0);
    },
    flutter() { noiseBurst(0.28, 2400, 0.7, 0.05); noiseBurst(0.2, 3600, 0.7, 0.035); },
    press() { noiseBurst(0.12, 500, 1, 0.09); },
  };

  /* ---------------- procedural textures (canvas, no fetched assets) ---------------- */
  function canvasTex(w, h, draw, opts) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    draw(c.getContext("2d"), w, h);
    const t = new THREE.CanvasTexture(c);
    if (opts && opts.repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; }
    return t;
  }
  function textTexture(text, opts) {
    const o = opts || {};
    return canvasTex((o.w || 512) * 2, (o.h || 128) * 2, (x, w, h) => {
      x.fillStyle = o.bg || "#000";
      x.fillRect(0, 0, w, h);
      x.font = (o.weight || "bold") + " " + ((o.size || 64) * 2) + "px ui-monospace, Menlo, Consolas, monospace";
      x.textAlign = "center"; x.textBaseline = "middle";
      x.fillStyle = o.fg || "#ffb54d";
      x.fillText(text, w / 2, h / 2);
    });
  }
  const rnd = (() => { let s = 42; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();

  /* wood-effect laminate with vertical grain + wear (cabinet shell) */
  function laminateTexture() {
    return canvasTex(512, 512, (x, w, h) => {
      x.fillStyle = "#232838"; x.fillRect(0, 0, w, h);
      for (let i = 0; i < 240; i++) {
        const gx = rnd() * w, gw = 1 + rnd() * 3, a = 0.03 + rnd() * 0.08;
        x.fillStyle = rnd() < 0.5 ? "rgba(8,10,16," + a + ")" : "rgba(52,60,82," + (a * 0.8) + ")";
        x.fillRect(gx, 0, gw, h);
      }
      for (let i = 0; i < 40; i++) { // worn scratches
        x.strokeStyle = "rgba(120,132,160," + (0.02 + rnd() * 0.05) + ")";
        x.lineWidth = 1;
        x.beginPath();
        const sx = rnd() * w, sy = rnd() * h;
        x.moveTo(sx, sy); x.lineTo(sx + (rnd() - 0.5) * 30, sy + rnd() * 90); x.stroke();
      }
    }, { repeat: true });
  }
  /* speckled dark concrete (floor) */
  function floorTexture() {
    return canvasTex(512, 512, (x, w, h) => {
      x.fillStyle = "#101319"; x.fillRect(0, 0, w, h);
      for (let i = 0; i < 2600; i++) {
        const g = 18 + rnd() * 30;
        x.fillStyle = "rgba(" + g + "," + (g + 4) + "," + (g + 10) + "," + (0.25 + rnd() * 0.5) + ")";
        x.fillRect(rnd() * w, rnd() * h, 1.6, 1.6);
      }
      for (let i = 0; i < 7; i++) { // stains
        const sx = rnd() * w, sy = rnd() * h, r = 40 + rnd() * 90;
        const g = x.createRadialGradient(sx, sy, 4, sx, sy, r);
        g.addColorStop(0, "rgba(4,6,10,0.35)"); g.addColorStop(1, "rgba(4,6,10,0)");
        x.fillStyle = g; x.beginPath(); x.arc(sx, sy, r, 0, 7); x.fill();
      }
      x.strokeStyle = "rgba(0,0,0,0.5)"; x.lineWidth = 3; // expansion joints
      x.strokeRect(0, 0, w, h);
      x.strokeStyle = "rgba(90,100,120,0.18)"; x.lineWidth = 1;
      x.strokeRect(2, 2, w - 4, h - 4);
    }, { repeat: true });
  }
  /* brushed metal (control deck) */
  function brushedMetalTexture() {
    return canvasTex(512, 256, (x, w, h) => {
      x.fillStyle = "#232a36"; x.fillRect(0, 0, w, h);
      for (let i = 0; i < 900; i++) {
        const gy = rnd() * h, gl = 20 + rnd() * 120, gx = rnd() * w;
        const v = 30 + rnd() * 46;
        x.strokeStyle = "rgba(" + v + "," + (v + 6) + "," + (v + 14) + "," + (0.12 + rnd() * 0.3) + ")";
        x.lineWidth = 1;
        x.beginPath(); x.moveTo(gx, gy); x.lineTo(gx + gl, gy); x.stroke();
      }
    }, { repeat: true });
  }
  /* dark acoustic wall panels */
  function wallTexture() {
    return canvasTex(512, 512, (x, w, h) => {
      x.fillStyle = "#0a0d14"; x.fillRect(0, 0, w, h);
      for (let px = 0; px < w; px += 128) {
        x.fillStyle = "rgba(20,26,38,0.8)"; x.fillRect(px + 3, 3, 122, h - 6);
        x.fillStyle = "rgba(4,5,9,0.9)"; x.fillRect(px, 0, 3, h);
        for (let i = 0; i < 60; i++) {
          x.fillStyle = "rgba(60,72,96," + (0.04 + rnd() * 0.08) + ")";
          x.fillRect(px + 6 + rnd() * 116, rnd() * h, 1.5, 1.5);
        }
        x.fillStyle = "rgba(90,104,130,0.5)"; // rivets
        [10, h / 2, h - 10].forEach((ry) => { x.beginPath(); x.arc(px + 8, ry, 2, 0, 7); x.fill(); });
      }
      const g = x.createLinearGradient(0, h * 0.7, 0, h); // grime at the floor line
      g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, "rgba(0,0,0,0.55)");
      x.fillStyle = g; x.fillRect(0, 0, w, h);
    }, { repeat: true });
  }
  /* cabinet side art: original to;doh-the event planner vinyl wrap (no third-party art) */
  function sideArtTexture() {
    return canvasTex(640, 1600, (x, w, h) => {
      const bg = x.createLinearGradient(0, 0, 0, h);
      bg.addColorStop(0, "#141b30"); bg.addColorStop(0.55, "#0d1220"); bg.addColorStop(1, "#161126");
      x.fillStyle = bg; x.fillRect(0, 0, w, h);
      // circuit traces
      x.strokeStyle = "rgba(63,210,255,0.16)"; x.lineWidth = 3;
      for (let i = 0; i < 26; i++) {
        let px = rnd() * w, py = rnd() * h;
        x.beginPath(); x.moveTo(px, py);
        for (let s = 0; s < 4; s++) {
          if (rnd() < 0.5) px += (rnd() - 0.5) * 160; else py += (rnd() - 0.5) * 200;
          x.lineTo(px, py);
        }
        x.stroke();
        x.fillStyle = "rgba(63,210,255,0.30)";
        x.beginPath(); x.arc(px, py, 4, 0, 7); x.fill();
      }
      // coolant loop motif along the lower third
      x.save();
      x.strokeStyle = "rgba(30,159,224,0.75)"; x.lineWidth = 12; x.lineCap = "round";
      x.shadowColor = "rgba(50,190,255,0.8)"; x.shadowBlur = 18;
      x.beginPath(); x.moveTo(60, h * 0.66);
      x.bezierCurveTo(w * 0.4, h * 0.60, w * 0.6, h * 0.74, w - 60, h * 0.68);
      x.stroke();
      x.strokeStyle = "rgba(127,228,255,0.85)"; x.lineWidth = 3.5;
      x.beginPath(); x.moveTo(60, h * 0.66);
      x.bezierCurveTo(w * 0.4, h * 0.60, w * 0.6, h * 0.74, w - 60, h * 0.68);
      x.stroke();
      x.restore();
      // coin + little attendant glyph
      x.save();
      x.translate(w * 0.5, h * 0.80);
      x.fillStyle = "#22304a";
      if (x.roundRect) { x.beginPath(); x.roundRect(-46, -40, 92, 70, 14); x.fill(); }
      x.fillStyle = "#3fd2ff";
      x.beginPath(); x.arc(-18, -12, 7, 0, 7); x.fill();
      x.beginPath(); x.arc(18, -12, 7, 0, 7); x.fill();
      x.strokeStyle = "#3fd2ff"; x.lineWidth = 5;
      x.beginPath(); x.arc(0, 4, 22, 0.15 * Math.PI, 0.85 * Math.PI); x.stroke();
      x.fillStyle = "#d9a93f";
      x.beginPath(); x.arc(0, 58, 15, 0, 7); x.fill();
      x.strokeStyle = "#8a6414"; x.lineWidth = 3;
      x.beginPath(); x.arc(0, 58, 9, 0, 7); x.stroke();
      x.restore();
      // vertical logo
      x.save();
      x.translate(w * 0.5, h * 0.34);
      x.rotate(-Math.PI / 2);
      x.textAlign = "center"; x.textBaseline = "middle";
      x.font = "bold 92px ui-monospace, Menlo, Consolas, monospace";
      x.shadowColor = "rgba(63,210,255,0.9)"; x.shadowBlur = 26;
      x.fillStyle = "#bfeaff";
      x.fillText("TO;DOH", 0, 0);
      x.shadowBlur = 0;
      x.font = "bold 30px ui-monospace, Menlo, Consolas, monospace";
      x.fillStyle = "#ffb54d";
      x.fillText("· VERIFIED PLANNING ENGINE ·", 0, 64);
      x.restore();
      // print grain
      for (let i = 0; i < 1800; i++) {
        x.fillStyle = "rgba(255,255,255," + (rnd() * 0.03) + ")";
        x.fillRect(rnd() * w, rnd() * h, 1.3, 1.3);
      }
    });
  }
  /* soft radial dot (dust + glow decals) */
  function glowDotTexture() {
    return canvasTex(64, 64, (x) => {
      const g = x.createRadialGradient(32, 32, 2, 32, 32, 30);
      g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(1, "rgba(255,255,255,0)");
      x.fillStyle = g; x.fillRect(0, 0, 64, 64);
    });
  }
  /* procedural environment: what the metals and glass "see" around them */
  function environmentTexture() {
    return canvasTex(1024, 512, (x, w, h) => {
      x.fillStyle = "#05070c"; x.fillRect(0, 0, w, h);
      const sky = x.createLinearGradient(0, 0, 0, h * 0.55);
      sky.addColorStop(0, "#0a1420"); sky.addColorStop(1, "#060a12");
      x.fillStyle = sky; x.fillRect(0, 0, w, h * 0.55);
      const floor = x.createLinearGradient(0, h * 0.55, 0, h);
      floor.addColorStop(0, "#0c0f16"); floor.addColorStop(1, "#030407");
      x.fillStyle = floor; x.fillRect(0, h * 0.55, w, h);
      const blob = (cx, cy, r, col) => {
        const g = x.createRadialGradient(cx, cy, 2, cx, cy, r);
        g.addColorStop(0, col); g.addColorStop(1, "rgba(0,0,0,0)");
        x.fillStyle = g; x.beginPath(); x.arc(cx, cy, r, 0, 7); x.fill();
      };
      blob(w * 0.5, h * 0.10, 190, "rgba(168,212,245,0.85)");   // cold tube light overhead
      blob(w * 0.13, h * 0.38, 130, "rgba(255,61,127,0.55)");   // magenta neon, left
      blob(w * 0.87, h * 0.38, 130, "rgba(63,210,255,0.55)");   // cyan neon, right
      blob(w * 0.5, h * 0.52, 150, "rgba(255,181,77,0.40)");    // warm marquee bounce
      blob(w * 0.5, h * 0.95, 260, "rgba(40,70,110,0.35)");     // floor sheen
    });
  }

  /* ================= the workflow screen (module road) =================
   * The CRT shows the real pipeline as a night road: one stop per module,
   * a boom barrier just past each stop, and a car that waits at a stop
   * while that module's real work runs, then drives on when it clears.
   * Same honesty rule as before: states change only from /api/arcade/pulse
   * data or the slowed replay of this run's recorded timings; the car never
   * moves on its own timer. Assets (all Sketchfab, CC-BY 4.0): car "2024
   * Porsche 992 GT3 R" by Dave Love SketchFab, road pieces "Road Modular"
   * by mortalityrexotable, barrier "Road Barrier" by Sousinho. Textures
   * downscaled and car geometry decimated for page weight.
   */
  const STAGE_ORDER = ["parse", "decompose", "budget", "guests", "vendors",
    "select", "schedule", "symbolic", "validate", "store"];
  const STAGE_LABEL = { parse: "PARSE", decompose: "DECOMPOSE", budget: "BUDGET", guests: "GUESTS",
    vendors: "VENDORS", select: "SELECT", schedule: "CP-SAT", symbolic: "PDDL",
    validate: "VALIDATE", store: "STORE" };
  const SW = 2048, SH = 1280, SC = 2.0;
  const scr = { // everything drawn on the cabinet CRT
    canvas: null, ctx: null, tex: null, scan: null,
    crt: "STANDBY", nodes: {}, dirty: true,
  };
  // the 3D road viewport inside the CRT's logical 1024x640 space
  const TV = { x: 26, y: 74, w: 972, h: 488 };
  const STATE_COL = { waiting: "#2b3846", active: "#3fd2ff", done: "#35b57f", failed: "#ff5a5a" };

  /* -------- the road scene: own small renderer, composited into the CRT -------- */
  const TRK = {
    ok: false, error: null,
    renderer: null, scene: null, cam: null,
    car: null, brakeMats: [], stops: [],
    pieceLen: 8, spacing: 5.2, startX: -4.4,
    carX: -4.4, prevX: -4.4, targetX: -4.4, camX: -4.4,
    drive: null,
  };

  function groundCenter(obj, baseY) {
    const b = new THREE.Box3().setFromObject(obj);
    const c = b.getCenter(new THREE.Vector3());
    obj.position.x -= c.x; obj.position.z -= c.z;
    obj.position.y -= b.min.y - (baseY || 0);
  }

  function trackSetup() {
    const c = document.createElement("canvas");
    const ss = S.lowFx ? 1 : 1.5; // supersample: the CRT magnifies this
    c.width = Math.round(TV.w * ss); c.height = Math.round(TV.h * ss);
    try { TRK.renderer = new THREE.WebGLRenderer({ canvas: c, antialias: true }); }
    catch (e) {
      try { TRK.renderer = new THREE.WebGLRenderer({ canvas: c, antialias: false }); }
      catch (e2) { TRK.error = "this browser refused a second WebGL context"; return; }
    }
    const sc = TRK.scene = new THREE.Scene();
    sc.background = new THREE.Color(0x070b13);
    sc.fog = new THREE.Fog(0x070b13, 24, 64);
    TRK.cam = new THREE.PerspectiveCamera(38, TV.w / TV.h, 0.1, 200);
    sc.add(new THREE.HemisphereLight(0xb9d3f2, 0x2c2236, 1.55));
    const moon = new THREE.DirectionalLight(0xcfe2ff, 0.95);
    moon.position.set(-6, 12, 7); sc.add(moon);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 120),
      new THREE.MeshStandardMaterial({ color: 0x0b0e14, roughness: 0.95 }));
    ground.rotation.x = -Math.PI / 2; ground.position.y = -0.08;
    sc.add(ground);
    const loader = new THREE.GLTFLoader();
    const load = (url) => new Promise((res, rej) => loader.load(url, res, undefined,
      () => rej(new Error(url + " failed to load"))));
    Promise.all([load("/static/road.glb"), load("/static/car.glb"), load("/static/barrier.glb")])
      .then(function (g) {
        try { trackBuild(g[0], g[1], g[2]); TRK.ok = true; }
        catch (e) { TRK.error = String((e && e.message) || e); }
        scr.dirty = true;
      })
      .catch(function (e) { TRK.error = String((e && e.message) || e); scr.dirty = true; });
  }

  function trackBuild(gRoad, gCar, gBar) {
    const sc = TRK.scene;
    /* road: the straight dual-carriageway piece from the modular kit, tiled */
    const kit = gRoad.scene;
    const piece = kit.getObjectByName("Double_road_36_7") ||
      kit.getObjectByName("Double_road_start_43_5") || kit;
    const proto = new THREE.Group();
    proto.add(piece.clone(true));
    groundCenter(proto.children[0], 0);
    let pb = new THREE.Box3().setFromObject(proto);
    let ps = pb.getSize(new THREE.Vector3());
    if (ps.z > ps.x) { proto.rotation.y = Math.PI / 2; pb.setFromObject(proto); ps = pb.getSize(new THREE.Vector3()); }
    proto.scale.setScalar(6.2 / ps.z);
    pb.setFromObject(proto); ps = pb.getSize(new THREE.Vector3());
    TRK.pieceLen = ps.x;
    TRK.spacing = 7.6;
    TRK.startX = -TRK.spacing * 0.85;
    TRK.carX = TRK.prevX = TRK.targetX = TRK.camX = TRK.startX;
    pb.setFromObject(proto);
    const roadTop = pb.max.y;
    const xEnd = (STAGE_ORDER.length - 1) * TRK.spacing + TRK.pieceLen * 1.8;
    for (let x = TRK.startX - TRK.pieceLen * 1.2; x < xEnd; x += TRK.pieceLen) {
      const inst = proto.clone(true);
      inst.position.x = x + TRK.pieceLen / 2;
      inst.position.y -= roadTop; // asphalt surface at y=0
      sc.add(inst);
    }
    /* night lighting: simple lamp posts (the kit's pole meshes ship lying
       flat, so posts are procedural: bulb + real point light every 2 stops) */
    for (let i = 0; i < STAGE_ORDER.length; i += 2) {
      const px = i * TRK.spacing + TRK.spacing / 2;
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.07, 4.6, 8),
        new THREE.MeshStandardMaterial({ color: 0x2a3440, roughness: 0.6, metalness: 0.6 }));
      post.position.set(px, 2.3, -3.4);
      sc.add(post);
      const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.14, 10, 8),
        new THREE.MeshBasicMaterial({ color: 0xd8ecff }));
      bulb.position.set(px, 4.65, -3.4);
      sc.add(bulb);
      const lamp = new THREE.PointLight(0xbfd8ff, 1.0, 18);
      lamp.position.set(px, 4.5, -2.2);
      sc.add(lamp);
    }
    /* one stop per module: sign + status lamp at the stop, barrier at its exit */
    const signSrc = kit.getObjectByName("Stop_sign_45_87") || kit.getObjectByName("Stop_sign001_23_83");
    const barSrc = gBar.scene;
    STAGE_ORDER.forEach(function (name, i) {
      const st = { name: name, x: i * TRK.spacing, state: "waiting", sign: null, lamp: null, gate: null };
      if (signSrc) {
        const g = new THREE.Group();
        g.add(signSrc.clone(true));
        let b = new THREE.Box3().setFromObject(g);
        g.scale.setScalar(2.1 / b.getSize(new THREE.Vector3()).y);
        g.position.set(st.x, 0, 3.0);
        sc.add(g);
        g.updateMatrixWorld(true);
        b.setFromObject(g);
        g.position.y -= b.min.y; // base on the asphalt
        g.position.x -= b.getCenter(new THREE.Vector3()).x - st.x;
        st.sign = g;
      }
      const lampMat = new THREE.MeshBasicMaterial({ color: STATE_COL.waiting });
      const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.11, 12, 10), lampMat);
      lamp.position.set(st.x, 2.45, 3.0);
      sc.add(lamp);
      st.lamp = lamp;
      const bg = new THREE.Group();
      bg.add(barSrc.clone(true));
      let b = new THREE.Box3().setFromObject(bg);
      let s = b.getSize(new THREE.Vector3());
      if (s.x > s.z) bg.rotation.y = Math.PI / 2; // pole spans the carriageway
      b.setFromObject(bg); s = b.getSize(new THREE.Vector3());
      bg.scale.setScalar(4.6 / s.z);
      bg.position.set(st.x + TRK.spacing * 0.5, 0, 0);
      sc.add(bg);
      bg.updateMatrixWorld(true);
      b.setFromObject(bg);
      const bc = b.getCenter(new THREE.Vector3());
      bg.position.y -= b.min.y; // stands on the asphalt
      bg.position.x -= bc.x - (st.x + TRK.spacing * 0.5);
      bg.position.z -= bc.z; // centered across the carriageway
      const pole = bg.getObjectByName("pole");
      st.gate = { root: bg, pole: pole, baseQ: pole ? pole.quaternion.clone() : null, open: 0, openT: 0 };
      TRK.stops.push(st);
    });
    /* the car: normalized length, nose pointed at +X via its own light meshes */
    const car = gCar.scene;
    const carG = new THREE.Group();
    carG.add(car);
    let cb = new THREE.Box3().setFromObject(carG);
    let cs = cb.getSize(new THREE.Vector3());
    if (cs.z > cs.x) { carG.rotation.y = Math.PI / 2; cb.setFromObject(carG); cs = cb.getSize(new THREE.Vector3()); }
    carG.scale.setScalar(3.9 / cs.x);
    const front = new THREE.Vector3(), rear = new THREE.Vector3();
    let nf = 0, nr = 0;
    carG.traverse(function (o) {
      if (!o.isMesh) return;
      const mn = ((o.material && o.material.name) || "") + " " + (o.name || "");
      const p = new THREE.Box3().setFromObject(o).getCenter(new THREE.Vector3());
      if (/Light_Front/i.test(mn)) { front.add(p); nf++; }
      if (/Light_Rear/i.test(mn)) { rear.add(p); nr++; }
      if (/Emissive_Light_Rear/i.test(o.material && o.material.name || "") && o.material.emissive) {
        TRK.brakeMats.push(o.material);
      }
    });
    if (nf && nr) {
      front.multiplyScalar(1 / nf); rear.multiplyScalar(1 / nr);
      if (Math.abs(front.x - rear.x) >= Math.abs(front.z - rear.z)) {
        if (front.x < rear.x) carG.rotation.y += Math.PI;
      } else if (front.z < rear.z) { carG.rotation.y += Math.PI; }
    }
    /* place at the very end, in world space on the root group */
    carG.updateMatrixWorld(true);
    cb.setFromObject(carG);
    const cc = cb.getCenter(new THREE.Vector3());
    carG.position.set(TRK.startX - cc.x, 0.03 - cb.min.y, -cc.z);
    /* headlight: one real spot so the road ahead of the car reads at night */
    const head = new THREE.SpotLight(0xd8ecff, 1.4, 16, 0.62, 0.5, 1);
    head.position.set(cc.x + cs.x * 0.45, 0.7, cc.z);
    head.target.position.set(cc.x + cs.x * 0.45 + 7, 0.1, cc.z);
    carG.add(head); carG.add(head.target);
    sc.add(carG);
    TRK.car = carG;
  }

  function stopState(idx, state) {
    const st = TRK.stops[idx];
    if (!st) return;
    st.state = state;
    if (st.lamp) st.lamp.material.color.set(STATE_COL[state] || STATE_COL.waiting);
  }

  function gateSet(idx, open, instant) {
    const st = TRK.stops[idx];
    if (!st || !st.gate) return;
    st.gate.openT = open ? 1 : 0;
    if (instant || S.reduced) st.gate.open = st.gate.openT;
  }

  function trackDriveTo(x, durMs) {
    return new Promise(function (resolve) {
      if (S.reduced || durMs <= 0 || !TRK.car) {
        TRK.carX = TRK.targetX = TRK.prevX = x; resolve(); return;
      }
      TRK.targetX = x;
      TRK.drive = { from: TRK.carX, to: x, t0: performance.now(), dur: durMs, resolve: resolve };
    });
  }

  const _qx = new THREE.Quaternion(), _ax = new THREE.Vector3(1, 0, 0);
  function trackUpdate(dt, t) {
    if (!TRK.ok) return;
    if (TRK.drive) {
      const d = TRK.drive;
      const k = Math.min(1, (performance.now() - d.t0) / d.dur);
      TRK.carX = d.from + (d.to - d.from) * easeInOut(k);
      if (k >= 1) { TRK.drive = null; d.resolve(); }
    } else {
      const dx = TRK.targetX - TRK.carX;
      if (Math.abs(dx) > 0.004) TRK.carX += dx * Math.min(1, dt * 5);
    }
    TRK.car.position.x = TRK.carX;
    TRK.prevX = TRK.carX;
    /* barriers ease toward their commanded position around the authored axis */
    for (const st of TRK.stops) {
      const g = st.gate;
      if (!g || !g.pole || g.open === g.openT) continue;
      const step = Math.min(Math.abs(g.openT - g.open), dt * 1.6);
      g.open += (g.openT > g.open ? step : -step);
      _qx.setFromAxisAngle(_ax, -g.open * 1.22);
      g.pole.quaternion.copy(g.baseQ).multiply(_qx);
    }
    /* camera tracks the car, slightly ahead of it */
    TRK.camX += (TRK.carX - TRK.camX) * Math.min(1, dt * 3.2);
    TRK.cam.position.set(TRK.camX + 0.4, 3.1, 9.6);
    TRK.cam.lookAt(TRK.camX + 1.2, 0.6, 0);
    /* failed stop: brake lights pulse */
    const anyFailed = STAGE_ORDER.some(function (n) { return scr.nodes[n] && scr.nodes[n].state === "failed"; });
    for (const m of TRK.brakeMats) {
      m.emissiveIntensity = anyFailed ? 1.6 + 1.4 * Math.sin(t / 90) : 1;
    }
  }

  function trackRender() {
    if (TRK.ok) TRK.renderer.render(TRK.scene, TRK.cam);
  }

  function buildScreenModel() {
    scr.canvas = document.createElement("canvas");
    scr.canvas.width = SW; scr.canvas.height = SH;
    scr.ctx = scr.canvas.getContext("2d");
    scr.ctx.scale(SC, SC);
    STAGE_ORDER.forEach((n) => { scr.nodes[n] = { state: "waiting", ms: null }; });
    // scanline pattern, drawn once
    scr.scan = document.createElement("canvas");
    scr.scan.width = SW; scr.scan.height = SH;
    const sx = scr.scan.getContext("2d");
    sx.fillStyle = "rgba(0,0,0,.10)";
    for (let y = 0; y < SH; y += 12) sx.fillRect(0, y, SW, 5);
    scr.tex = new THREE.CanvasTexture(scr.canvas);
    scr.tex.minFilter = THREE.LinearFilter;
    trackSetup();
  }

  const _pv = new THREE.Vector3();
  function drawStopLabels(x) {
    TRK.stops.forEach(function (st) {
      _pv.set(st.x, 3.2, 0).project(TRK.cam);
      if (_pv.z > 1 || _pv.x < -1.04 || _pv.x > 1.04) return;
      const px = TV.x + (_pv.x + 1) / 2 * TV.w;
      const py = TV.y + (1 - (_pv.y + 1) / 2) * TV.h;
      const n = scr.nodes[st.name];
      x.textAlign = "center"; x.textBaseline = "alphabetic";
      x.font = "bold 22px ui-monospace, Menlo, Consolas, monospace";
      x.fillStyle = n.state === "waiting" ? "#7d90a6" : (n.state === "failed" ? "#ffb0b0" : "#e4f4ff");
      x.shadowColor = "rgba(0,0,0,.9)"; x.shadowBlur = 5;
      x.fillText(STAGE_LABEL[st.name], px, py);
      const sub = n.state === "done" && n.ms !== null ? n.ms + " ms"
        : n.state === "failed" ? "FAILED" : n.state === "active" ? "RUNNING" : "";
      if (sub) {
        x.font = "16px ui-monospace, Menlo, Consolas, monospace";
        x.fillStyle = n.state === "failed" ? "#ff8a8a" : "#8fb4d4";
        x.fillText(sub, px, py + 20);
      }
      x.shadowBlur = 0;
    });
  }

  function drawProgressStrip(x, t) {
    const y = 592, x0 = 150, x1 = 874; // keep labels off the curved glass edges: the screen rolls away at the canvas extremes and silhouettes whatever sits there
    const step = (x1 - x0) / (STAGE_ORDER.length - 1);
    x.strokeStyle = "#223042"; x.lineWidth = 2;
    x.beginPath(); x.moveTo(x0, y); x.lineTo(x1, y); x.stroke();
    x.textAlign = "center"; x.textBaseline = "alphabetic";
    STAGE_ORDER.forEach(function (n, i) {
      const cx = x0 + i * step;
      const st = scr.nodes[n].state;
      x.fillStyle = STATE_COL[st] || STATE_COL.waiting;
      if (st === "active") {
        const pulse = 0.7 + 0.3 * Math.sin(t / 130);
        x.save(); x.shadowColor = "rgba(63,210,255," + 0.85 * pulse + ")"; x.shadowBlur = 12 * pulse;
      }
      x.beginPath(); x.arc(cx, y, 6.5, 0, Math.PI * 2); x.fill();
      if (st === "active") x.restore();
      x.font = "12px ui-monospace, Menlo, Consolas, monospace";
      x.fillStyle = st === "waiting" ? "#54677e" : "#9db8d0";
      x.fillText(STAGE_LABEL[n], cx, y + 20);
    });
  }

  function drawScreen(t) {
    const x = scr.ctx;
    const g = x.createLinearGradient(0, 0, 0, SH);
    g.addColorStop(0, "#0b1424"); g.addColorStop(1, "#070d18");
    x.fillStyle = g; x.fillRect(0, 0, SW, SH);
    // header
    x.textAlign = "left"; x.textBaseline = "alphabetic";
    x.font = "bold 27px ui-monospace, Menlo, Consolas, monospace";
    x.fillStyle = "#ffb54d";
    x.fillText("TO;DOH // PLANNING ENGINE", 28, 46);
    x.textAlign = "right";
    const crtCol = { "STANDBY": "#3a4a5c", "INSERT COIN": "#ffb54d", "RUNNING": "#3fd2ff",
      "REPLAY": "#ffb54d", "PLAN READY": "#59d68f", "JAMMED": "#ff5a5a" }[scr.crt] || "#3fd2ff";
    x.fillStyle = crtCol;
    x.font = "bold 33px ui-monospace, Menlo, Consolas, monospace";
    x.fillText(scr.crt, SW - 28, 48);
    x.strokeStyle = "#1d2836"; x.beginPath(); x.moveTo(28, 62); x.lineTo(SW - 28, 62); x.stroke();

    if (TRK.ok) {
      trackRender();
      x.drawImage(TRK.renderer.domElement, TV.x, TV.y, TV.w, TV.h);
      x.strokeStyle = "#1d2836"; x.lineWidth = 2;
      x.strokeRect(TV.x - 1, TV.y - 1, TV.w + 2, TV.h + 2);
      drawStopLabels(x);
    } else {
      x.fillStyle = "#0a101a"; x.fillRect(TV.x, TV.y, TV.w, TV.h);
      x.strokeStyle = "#1d2836"; x.lineWidth = 2; x.strokeRect(TV.x, TV.y, TV.w, TV.h);
      x.textAlign = "center";
      x.font = "bold 26px ui-monospace, Menlo, Consolas, monospace";
      x.fillStyle = "#3a4a5c";
      x.fillText(TRK.error ? "ROAD ASSETS FAILED TO LOAD" : "WARMING UP THE ROAD…",
        TV.x + TV.w / 2, TV.y + TV.h / 2 - 6);
      if (TRK.error) {
        x.font = "17px ui-monospace, Menlo, Consolas, monospace";
        x.fillStyle = "#ff8a8a";
        x.fillText(String(TRK.error).slice(0, 90), TV.x + TV.w / 2, TV.y + TV.h / 2 + 24);
      }
    }
    drawProgressStrip(x, t);

    x.drawImage(scr.scan, 0, 0, 1024, 640);
    // vignette
    const v = x.createRadialGradient(SW / 2, SH / 2, SH / 3, SW / 2, SH / 2, SH);
    v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,.28)");
    x.fillStyle = v; x.fillRect(0, 0, SW, SH);
    scr.tex.needsUpdate = true;
  }

  /* ================= scene construction ================= */
  const mat = (c, r, m, e) => new THREE.MeshStandardMaterial({
    color: c, roughness: r === undefined ? 0.85 : r, metalness: m || 0,
    emissive: e || 0x000000,
  });
  /* bright basic material that UnrealBloom picks up (colors pushed past 1.0) */
  const glowMat = (map, boost) => new THREE.MeshBasicMaterial({
    map, color: new THREE.Color(boost || 1.6, boost || 1.6, boost || 1.6),
  });

  /* ---------------- device tier and model choice ----------------
     The room's models are heavy in memory, not just in bytes: the originals hold textures up to 4096 x 4096 (89 MB of GPU memory each, 1.2 GB for the whole
     room), which a phone cannot hold. Every model ships twice, optimised by tools/assets/optimize_models.sh: "high" (textures <= 2048, WebP) and "low"
     (<= 1024, about a quarter of the GPU memory). A phone-sized screen, a device with 2 GB or less, or a Save-Data connection gets "low"; ?tier=low|high forces it.
     The cat vending machine (CC BY-NC-ND: no derivatives, so only its 54 MB original exists) is loaded only on a "high" device, or when asked for. */
  function detectTier() {
    const forced = new URLSearchParams(location.search).get("tier");
    if (forced === "low" || forced === "high") return forced;
    const nav = navigator, conn = nav.connection || {};
    const shortSide = Math.min(window.screen.width, window.screen.height);
    if (conn.saveData || (nav.deviceMemory && nav.deviceMemory <= 2)) return "low";
    return shortSide < 700 ? "low" : "high"; // a phone: its GPU memory is the scarce thing, whatever its CPU
  }
  const TIER = detectTier();
  const HAS_LOW = { cabinet: 1, alley_van: 1, alley_dumpster: 1, alley_candy: 1, vend_nuka: 1, vend_retro: 1, wall_mural: 1, wall_posters: 1, holo_table: 1, secret_dossier: 1 };
  // Every model is asked for by the version address the page was given (<meta name="asset-version">), and in the tier's size: a replaced file is a new address, so no
  // browser keeps the old one, and a phone never downloads the laptop's textures.
  (function () {
    const meta = document.querySelector('meta[name="asset-version"]');
    const v = meta && meta.content;
    if (!THREE.GLTFLoader) return;
    const orig = THREE.GLTFLoader.prototype.load;
    THREE.GLTFLoader.prototype.load = function (url) {
      const args = Array.prototype.slice.call(arguments);
      if (typeof url === "string" && url.indexOf("/static/") === 0 && url.indexOf("?") < 0) {
        let u = url;
        const m = /^\/static\/([a-z_]+)\.glb$/.exec(u);
        if (TIER === "low" && m && HAS_LOW[m[1]]) u = "/static/" + m[1] + ".low.glb";
        args[0] = v ? u + "?v=" + v : u;
      }
      return orig.apply(this, args);
    };
  })();
  /* ---------------- loading order ----------------
     First paint needs only the room, the cabinet and the attendant. Everything else is queued and fetched one model at a time, most important first (the mural, then the
     vending machines, then the van and the small props, the posters, the Hall table, the dossier, and last the 54 MB cat machine), with a gap between them so a phone
     is never decoding two large models at once and the page stays responsive while they arrive. The queue starts after the first frames have been drawn. */
  const LAZY = { q: [], timer: null, started: false, n: 0 };
  function lazy(prio, fn) { LAZY.q.push({ prio: prio, fn: fn, n: LAZY.n++ }); if (LAZY.started && !LAZY.timer) lazyPump(); }
  function lazyPump() {
    LAZY.timer = null;
    if (!LAZY.q.length) return;
    LAZY.q.sort(function (a, b) { return a.prio - b.prio || a.n - b.n; });
    const job = LAZY.q.shift();
    try { job.fn(); } catch (e) { console.error("lazy load:", e && e.message || e); }
    LAZY.timer = setTimeout(lazyPump, TIER === "low" ? 450 : 120);
  }
  function lazyStart() { LAZY.started = true; if (!LAZY.timer) lazyPump(); }

  const CAB_Z = -3.25; // cabinet centred, pulled closer to the mural wall (user, 01:18)

  function shadowed(mesh, cast, receive) {
    mesh.castShadow = cast !== false;
    mesh.receiveShadow = !!receive;
    return mesh;
  }

  /* generated spray-tag graffiti for the side walls: original art drawn in
     code, dark base with layered neon strokes so no bare wall shows through */
  function graffitiTexture() {
    const c = document.createElement("canvas");
    c.width = 1024; c.height = 512;
    const x = c.getContext("2d");
    x.fillStyle = "#0b0e15"; x.fillRect(0, 0, 1024, 512);
    // grime streaks
    for (let i = 0; i < 90; i++) {
      x.fillStyle = "rgba(" + (10 + rnd() * 30 | 0) + "," + (12 + rnd() * 26 | 0) + "," + (18 + rnd() * 30 | 0) + "," + (0.12 + rnd() * 0.2) + ")";
      x.fillRect(rnd() * 1024, rnd() * 512, 20 + rnd() * 120, 2 + rnd() * 26);
    }
    // layered spray strokes: wide faded throws under thin sharp tags
    const cols = ["#ff3d7f", "#3fd2ff", "#ffb35c", "#7de08a", "#c88bff", "#f4efe3"];
    for (let i = 0; i < 46; i++) {
      const col = cols[(rnd() * cols.length) | 0];
      x.strokeStyle = col; x.globalAlpha = 0.05 + rnd() * 0.10;
      x.lineWidth = 12 + rnd() * 30; x.lineCap = "round";
      x.shadowColor = col; x.shadowBlur = 18;
      x.beginPath();
      const px = rnd() * 1024, py = 60 + rnd() * 400;
      x.moveTo(px, py);
      x.bezierCurveTo(px + (rnd() - 0.5) * 320, py + (rnd() - 0.5) * 200,
        px + (rnd() - 0.5) * 320, py + (rnd() - 0.5) * 200,
        px + (rnd() - 0.5) * 420, py + (rnd() - 0.5) * 240);
      x.stroke();
    }
    for (let i = 0; i < 26; i++) {
      const col = cols[(rnd() * cols.length) | 0];
      x.strokeStyle = col; x.globalAlpha = 0.30 + rnd() * 0.35;
      x.lineWidth = 2 + rnd() * 4; x.shadowBlur = 6; x.shadowColor = col;
      x.beginPath();
      const px = rnd() * 1024, py = 80 + rnd() * 360;
      x.moveTo(px, py);
      for (let j = 0; j < 4; j++) x.lineTo(px + (rnd() - 0.5) * 260 * (j + 1) / 4, py + (rnd() - 0.5) * 130);
      x.stroke();
    }
    x.globalAlpha = 1; x.shadowBlur = 0;
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    return tex;
  }

  /* A weathered stone wall for the left side, where the visitor's name is cut (docs: wall carving strategy). Procedural, no asset, no licence surface:
     a mid-grey mottled base with faint block seams and damp streaks. The mottling is small-scale and low-alpha on purpose: it ages the stone without becoming a pattern. */
  function plasterTexture() {
    const c = document.createElement("canvas"); c.width = 1024; c.height = 512;
    const x = c.getContext("2d");
    x.fillStyle = "#3b3b40"; x.fillRect(0, 0, 1024, 512);
    let seed = 90210; const r = function () { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    for (let i = 0; i < 9000; i++) { // fine mottling: many tiny, faint marks, so the surface is aged and never shows a pattern
      const g = (r() * 60 - 30) | 0, a = 0.015 + r() * 0.025, rad = 0.8 + r() * 3.2;
      x.fillStyle = g > 0 ? "rgba(" + (150 + g) + "," + (155 + g) + "," + (165 + g) + "," + a + ")" : "rgba(20,24,34," + a + ")";
      x.beginPath(); x.arc(r() * 1024, r() * 512, rad, 0, 6.283); x.fill();
    }
    x.strokeStyle = "rgba(14,18,26,0.28)"; x.lineWidth = 2; // block seams
    for (let row = 0; row < 4; row++) {
      const y = row * 128 + 4; x.beginPath(); x.moveTo(0, y); x.lineTo(1024, y + (r() * 3 - 1.5)); x.stroke();
      for (let col = 0; col < 5; col++) { const bx = ((col * 205 + (row % 2) * 102) % 1024); x.beginPath(); x.moveTo(bx, y); x.lineTo(bx + (r() * 3 - 1.5), y + 128); x.stroke(); }
    }
    for (let i = 0; i < 14; i++) { // damp streaks running down from the top
      const sx = r() * 1024, len = 120 + r() * 300, g = x.createLinearGradient(0, 0, 0, len);
      g.addColorStop(0, "rgba(12,16,24,0.20)"); g.addColorStop(1, "rgba(12,16,24,0)");
      x.fillStyle = g; x.fillRect(sx, 0, 6 + r() * 26, len);
    }
    for (let i = 0; i < 9; i++) { // lichen: a few pale, greenish patches
      x.fillStyle = "rgba(150,170,130," + (0.04 + r() * 0.04) + ")"; x.beginPath(); x.ellipse(r() * 1024, r() * 512, 6 + r() * 18, 4 + r() * 10, r() * 3, 0, 6.283); x.fill();
    }
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace; tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    return tex;
  }

  function buildRoom(sc) {
    sc.fog = new THREE.FogExp2(0x070b18, 0.03); // thinner than before and re-scaled in resize(): a camera that backs off for a tall screen must not drown the room in fog
    sc.background = new THREE.Color(0x030509);
    const floorTex = floorTexture();
    floorTex.repeat.set(6, 6);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40),
      new THREE.MeshStandardMaterial({ map: floorTex, bumpMap: floorTex, bumpScale: 0.012,
        roughness: 0.82, metalness: 0.05, envMapIntensity: 0.35 })); // matte enough that a light does not leave a glare pool on it
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    sc.add(floor);
    const rug = new THREE.Mesh(new THREE.CircleGeometry(1.15, 40), mat(0x120e18, 1));
    rug.rotation.x = -Math.PI / 2; rug.position.set(0, 0.005, 2.6); rug.receiveShadow = true; sc.add(rug);
    const manhole = new THREE.Mesh(new THREE.CircleGeometry(0.42, 32), mat(0x141920, 0.55, 0.55));
    manhole.rotation.x = -Math.PI / 2; manhole.position.set(-1.9, 0.005, 4.2); sc.add(manhole);
    const wallTex = wallTexture(); wallTex.repeat.set(7, 1.6);
    const wall = new THREE.Mesh(new THREE.PlaneGeometry(40, 12),
      new THREE.MeshStandardMaterial({ map: wallTex, roughness: 0.92, envMapIntensity: 0.25 }));
    wall.position.set(0, 6, -4.6); sc.add(wall);
    const grafTex = graffitiTexture(); grafTex.repeat.set(3.2, 1.5);
    const wallL = new THREE.Mesh(new THREE.PlaneGeometry(30, 12),
      new THREE.MeshStandardMaterial({ map: grafTex, roughness: 0.92, envMapIntensity: 0.25 }));
    wallL.rotation.y = Math.PI / 2; wallL.position.set(-5.6, 6, 5); sc.add(wallL);
    const stoneTex = plasterTexture(); stoneTex.repeat.set(7, 3.5);
    wallL.material = new THREE.MeshStandardMaterial({ map: stoneTex, color: 0x8a847a, roughness: 0.96, envMapIntensity: 0.1 }); // the left wall is plain weathered stone: the name is cut into it
    const wallR = wallL.clone(); wallR.material = new THREE.MeshStandardMaterial({ map: grafTex, roughness: 0.92, envMapIntensity: 0.25 }); wallR.rotation.y = -Math.PI / 2; wallR.position.x = 5.6; sc.add(wallR);
    [[-2.8, -3.6, 0.7], [2.2, -3.9, 0.95]].forEach(([cx, cz, drop]) => {
      const pts = [new THREE.Vector3(cx, 5.2, cz), new THREE.Vector3(cx + 0.35, 5.2 - drop, cz + 0.15),
        new THREE.Vector3(cx - 0.15, 5.2 - drop * 1.35, cz + 0.4)];
      const cable = new THREE.Mesh(
        new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 20, 0.022, 6),
        mat(0x0a0c10, 0.6, 0.1));
      sc.add(cable);
    });
    const fixture = new THREE.Mesh(new THREE.BoxGeometry(2.6, 0.1, 0.24), mat(0x11161f, 0.6, 0.4));
    fixture.position.set(0, 4.55, -1.5); sc.add(fixture);
    const tube = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.06, 0.12),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(1.55, 1.75, 1.95) }));
    tube.position.set(0, 4.49, -1.5); sc.add(tube);
    S.tubeLight = new THREE.PointLight(0xa8d4f5, 1.5, 20);
    S.tubeLight.position.set(0, 4.3, -1.2); sc.add(S.tubeLight);
    const glowDot = glowDotTexture();
    // off = outward-from-wall direction for the glow decal and light
    const addNeon = (txt, opt, pos, ry, glowCol, off) => {
      const sign = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 0.5), glowMat(textTexture(txt, opt), 1.9));
      sign.position.set(pos[0], pos[1], pos[2]); sign.rotation.y = ry; sc.add(sign);
      const decal = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 1.6),
        new THREE.MeshBasicMaterial({ map: glowDot, color: glowCol, transparent: true,
          opacity: 0.30, blending: THREE.AdditiveBlending, depthWrite: false }));
      decal.position.set(pos[0] - off[0] * 0.06, pos[1], pos[2] - off[2] * 0.06); decal.rotation.y = ry; sc.add(decal);
      const l = new THREE.PointLight(glowCol, 0.9, 9);
      l.position.set(pos[0] + off[0] * 0.5, pos[1], pos[2] + off[2] * 0.5); sc.add(l);
    };
    // neon lives on the SIDE walls now: the back wall carries only the girl mural
    // neon signs removed on the user's instruction (00:07): bare graffiti + posters only
    [[-3.4, -1.6, 0.35], [-4.6, -2.4, 0.5], [3.4, -1.6, -0.35], [4.6, -2.4, -0.5]].forEach(([x, z, ry]) => {
      const cab = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1.9, 0.8),
        new THREE.MeshStandardMaterial({ map: laminateTexture(), color: 0x59606e, roughness: 0.85, envMapIntensity: 0.12 }));
      cab.position.set(x, 0.95, z); cab.rotation.y = ry;
      shadowed(cab); sc.add(cab); (S.roomCabs = S.roomCabs || []).push(cab);
      const scrn = new THREE.Mesh(new THREE.PlaneGeometry(0.55, 0.4),
        mat(0x0a141c, 0.4, 0, 0x06121a));
      scrn.position.set(x + Math.sin(ry) * 0.42, 1.25, z + Math.cos(ry) * 0.42);
      scrn.rotation.y = ry; sc.add(scrn); (S.roomCabs = S.roomCabs || []).push(scrn);
    });
    sc.add(new THREE.AmbientLight(0x4a5f9a, 1.75));
    const frontFill = new THREE.PointLight(0xa8c4e8, 0.55, 14);
    frontFill.position.set(0.6, 2.1, 4.6); sc.add(frontFill);
    const rim = new THREE.PointLight(0x6fa8ff, 0.55, 9);
    rim.position.set(-2.4, 3.0, -2.2); sc.add(rim);
    // the key light used to be aimed at the floor in the middle, which drew a bright pool there and left the walls dim. It now looks at the cabinet and the mural
    // behind it, with a wide soft cone, so the wall with the art is the brightest surface in the room.
    const key = new THREE.SpotLight(0xd2e6fa, 2.6, 24, 0.9, 0.85);
    key.position.set(1.2, 5.0, 5.8);
    key.target.position.set(0, 2.9, -3.6);
    key.castShadow = true;
    key.shadow.mapSize.width = key.shadow.mapSize.height = S.lowFx || S.smallGpu ? 512 : 1024;
    key.shadow.camera.near = 1.5; key.shadow.camera.far = 16;
    key.shadow.bias = -0.0004;
    sc.add(key); sc.add(key.target);
    S.keyLight = key;
    // two coloured washes on the mural from either side (magenta and cyan, the alley's neon), and a gentle glow on the side walls so their graffiti reads at a glancing angle
    const floorFill = new THREE.PointLight(0x8fb4ff, 0.9, 10); floorFill.position.set(0, 3.2, 3.4); sc.add(floorFill); // soft light on the foreground floor (the floor is matte now, so it glows rather than glares)
    const washL = new THREE.PointLight(0xff4fb8, 1.5, 11); washL.position.set(-1.6, 4.0, -3.0); sc.add(washL);
    const washR = new THREE.PointLight(0x35d6ff, 1.5, 11); washR.position.set(1.6, 4.0, -2.4); sc.add(washR);
    [wallR].forEach(function (w) { w.material.emissiveMap = w.material.map; w.material.emissive = new THREE.Color(0xffffff); w.material.emissiveIntensity = 0.38; }); // the graffiti wall glows; the stone wall does not
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.22, 1.3, 64), new THREE.MeshBasicMaterial({ color: 0x35d6ff, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.set(0, 0.012, 2.6); sc.add(ring); // a lit edge round the rug: the floor in front of the cabinet is no longer a dark hole
    const n = 140, pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      pos[i * 3] = (rnd() - 0.5) * 9;
      pos[i * 3 + 1] = 0.2 + rnd() * 4.2;
      pos[i * 3 + 2] = (rnd() - 0.5) * 8 + 1.5;
    }
    const dg = new THREE.BufferGeometry();
    dg.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    S.dust = new THREE.Points(dg, new THREE.PointsMaterial({
      map: glowDot, color: 0x8fb6d8, size: 0.03, transparent: true, opacity: 0.32,
      blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
    }));
    if (!S.lowFx && !S.smallGpu) sc.add(S.dust);
  }

  /* -------- cabinet: classic upright silhouette, original to;doh-the event planner art --------
     If the user-selected CC-BY model ships as /static/cabinet.glb, the GLB
     loader replaces this shell and reuses the same mount points. */
  const PROFILE = [ // (depth front+/back-, height) — the classic upright side view
    [0.42, 0.03], [0.46, 0.10], [0.46, 0.98], [0.60, 1.00], [0.60, 1.07],
    [0.32, 1.18], [0.28, 1.30], [0.16, 2.06], [0.24, 2.12], [0.28, 2.40],
    [0.16, 2.52], [-0.40, 2.52], [-0.42, 0.03],
  ];
  const CAB_W = 1.9;

  function extrudeCabinetShell() {
    const shape = new THREE.Shape();
    PROFILE.forEach(([px, py], i) => { if (i === 0) shape.moveTo(px, py); else shape.lineTo(px, py); });
    shape.closePath();
    const geo = new THREE.ExtrudeGeometry(shape, {
      depth: CAB_W, bevelEnabled: true, bevelThickness: 0.018, bevelSize: 0.018, bevelSegments: 2, steps: 1,
    });
    // normalize cap UVs to 0..1 so the side art spans the panel; mirror the far side
    const xs = PROFILE.map((p) => p[0]), ys = PROFILE.map((p) => p[1]);
    const minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
    const minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    const capCount = geo.groups.length && geo.groups[0].materialIndex === 0 ? geo.groups[0].count : 0;
    const posA = geo.attributes.position, uvA = geo.attributes.uv;
    for (let i = 0; i < capCount; i++) {
      let u = (posA.getX(i) - minX) / (maxX - minX);
      const v = (posA.getY(i) - minY) / (maxY - minY);
      if (Math.abs(posA.getZ(i) - CAB_W) < 1e-6) u = 1 - u; // far cap reads correctly
      uvA.setXY(i, u, v);
    }
    geo.rotateY(-Math.PI / 2);           // extrusion axis -> world X (width)
    geo.translate(CAB_W / 2, 0, 0);
    const capMat = new THREE.MeshStandardMaterial({ map: sideArtTexture(), roughness: 0.55,
      metalness: 0.08, envMapIntensity: 0.9 });
    const edgeMat = new THREE.MeshStandardMaterial({ map: laminateTexture(), color: 0x8890a0,
      roughness: 0.6, metalness: 0.15, envMapIntensity: 0.85 });
    const shell = new THREE.Mesh(geo, [capMat, edgeMat]);
    shadowed(shell);
    return shell;
  }

  function addTMolding(cab) {
    [-1, 1].forEach((side) => {
      const pts = PROFILE.map(([px, py]) => new THREE.Vector3(side * (CAB_W / 2 + 0.012), py, px));
      const curve = new THREE.CatmullRomCurve3(pts, true, "catmullrom", 0.0);
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 90, 0.017, 8, true),
        new THREE.MeshStandardMaterial({ color: 0x5b3fd4, roughness: 0.32, metalness: 0.1,
          emissive: 0x241063, emissiveIntensity: 0.9, envMapIntensity: 1.1 }));
      cab.add(tube);
    });
  }

  /* curved CRT: bulged plane; the workflow texture and the glass share the curve */
  function curvedScreenGeometry(w, h, bulge) {
    const g = new THREE.PlaneGeometry(w, h, 24, 16);
    const p = g.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const nx = p.getX(i) / (w / 2), ny = p.getY(i) / (h / 2);
      p.setZ(i, bulge * (1 - 0.5 * (nx * nx + ny * ny)));
    }
    g.computeVertexNormals();
    return g;
  }

  function buildCabinet(sc) {
    const cab = new THREE.Group();
    cab.add(extrudeCabinetShell());
    addTMolding(cab);

    // marquee: recessed, backlit printed acrylic under clear glass
    const marqueeFrame = new THREE.Mesh(new THREE.BoxGeometry(1.74, 0.4, 0.05),
      mat(0x0a0d13, 0.6, 0.2));
    marqueeFrame.position.set(0, 2.30, 0.31); marqueeFrame.rotation.x = -0.10; cab.add(shadowed(marqueeFrame));
    S.marquee = new THREE.Mesh(new THREE.PlaneGeometry(1.62, 0.3),
      glowMat(textTexture("TO;DOH", { fg: "#8a6a28", w: 640, h: 128, size: 76 }), 1.05));
    S.marquee.position.set(0, 2.30, 0.345); S.marquee.rotation.x = -0.10; cab.add(S.marquee);
    const marqueeGlass = new THREE.Mesh(new THREE.PlaneGeometry(1.66, 0.34),
      new THREE.MeshStandardMaterial({ color: 0xaac4dd, transparent: true, opacity: 0.09,
        roughness: 0.06, metalness: 0.9, envMapIntensity: 1.4 }));
    marqueeGlass.position.set(0, 2.30, 0.355); marqueeGlass.rotation.x = -0.10; cab.add(marqueeGlass);
    S.marqueeLight = new THREE.PointLight(0xffb54d, 0, 5);
    S.marqueeLight.position.set(0, 2.35, 1.2); cab.add(S.marqueeLight);

    // tilted screen bay: bezel, curved CRT, curved glass, glare streak
    const bay = new THREE.Group();
    bay.position.set(0, 1.70, 0.285); bay.rotation.x = -0.157;
    const bezel = new THREE.Mesh(new THREE.BoxGeometry(1.74, 1.16, 0.09),
      new THREE.MeshStandardMaterial({ map: laminateTexture(), color: 0x39404e,
        roughness: 0.55, metalness: 0.1, envMapIntensity: 0.6 }));
    shadowed(bezel); bay.add(bezel);
    buildScreenModel();
    const scrGeo = curvedScreenGeometry(1.56, 0.975, 0.055);
    S.screenMesh = new THREE.Mesh(scrGeo, new THREE.MeshBasicMaterial({ map: scr.tex }));
    S.screenMesh.position.z = 0.048; bay.add(S.screenMesh);
    S.glassMesh = new THREE.Mesh(scrGeo.clone(),
      new THREE.MeshStandardMaterial({ color: 0x9fc0dd, transparent: true, opacity: 0.075,
        roughness: 0.05, metalness: 0.9, envMapIntensity: 1.5, depthWrite: false }));
    S.glassMesh.position.z = 0.052; bay.add(S.glassMesh);
    const glare = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 0.9),
      new THREE.MeshBasicMaterial({ map: (function () { return canvasTex(128, 128, (x, w, h) => {
        const g = x.createLinearGradient(0, 0, w, h);
        g.addColorStop(0, "rgba(255,255,255,0)");
        g.addColorStop(0.42, "rgba(210,230,250,0.55)");
        g.addColorStop(0.5, "rgba(235,245,255,0.75)");
        g.addColorStop(0.58, "rgba(210,230,250,0.5)");
        g.addColorStop(1, "rgba(255,255,255,0)");
        x.fillStyle = g; x.fillRect(0, 0, w, h);
      }); })(), transparent: true, opacity: 0.10, blending: THREE.AdditiveBlending, depthWrite: false }));
    glare.position.z = 0.075; bay.add(glare);
    cab.add(bay);
    S.screenLight = new THREE.PointLight(0x3fd2ff, 0, 4);
    S.screenLight.position.set(0, 1.75, 1.1); cab.add(S.screenLight);

    // control deck: brushed metal plate with joystick + glossy buttons
    const deckTex = brushedMetalTexture();
    const deck = new THREE.Mesh(new THREE.BoxGeometry(1.72, 0.09, 0.54),
      new THREE.MeshStandardMaterial({ map: deckTex, roughness: 0.42, metalness: 0.75, envMapIntensity: 1.0 }));
    deck.position.set(0, 1.075, 0.475); deck.rotation.x = -0.36; cab.add(shadowed(deck));
    const stickBase = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.09, 0.02, 20),
      mat(0x10141c, 0.5, 0.6));
    stickBase.position.set(-0.45, 1.14, 0.52); stickBase.rotation.x = -0.36; cab.add(stickBase);
    const stick = new THREE.Mesh(new THREE.CylinderGeometry(0.016, 0.02, 0.17, 12),
      mat(0x3a4452, 0.3, 0.85));
    stick.position.set(-0.45, 1.23, 0.545); stick.rotation.x = -0.36; cab.add(shadowed(stick));
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.048, 20, 14),
      new THREE.MeshStandardMaterial({ color: 0xc22434, roughness: 0.18, metalness: 0.05,
        envMapIntensity: 1.3 }));
    ball.position.set(-0.45, 1.325, 0.575); cab.add(shadowed(ball));
    [[0.18, 0x1f8ab0], [0.36, 0xd08a1d], [0.54, 0xa02535]].forEach(function (bc) {
      const bx = bc[0], col = bc[1];
      const rim = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.055, 0.016, 18), mat(0x0c0f15, 0.5, 0.5));
      rim.position.set(bx, 1.135, 0.575); rim.rotation.x = -0.36; cab.add(rim);
      const btn = new THREE.Mesh(new THREE.SphereGeometry(0.042, 18, 10, 0, Math.PI * 2, 0, Math.PI * 0.45),
        new THREE.MeshStandardMaterial({ color: col, roughness: 0.15, metalness: 0.05, envMapIntensity: 1.4 }));
      btn.rotation.x = -Math.PI / 2 - 0.36;
      btn.position.set(bx, 1.145, 0.585); cab.add(shadowed(btn));
    });

    // coin door: steel panel, two slots, return button, hex bolts
    const door = new THREE.Mesh(new THREE.BoxGeometry(0.56, 0.5, 0.04),
      new THREE.MeshStandardMaterial({ map: brushedMetalTexture(), color: 0x707a88,
        roughness: 0.45, metalness: 0.8, envMapIntensity: 0.9 }));
    door.position.set(0.30, 0.58, 0.462); cab.add(shadowed(door));
    [[0.22, 0.66], [0.38, 0.66]].forEach(function (sp) {
      const slot = new THREE.Mesh(new THREE.BoxGeometry(0.045, 0.13, 0.02), mat(0x020304, 0.4));
      slot.position.set(sp[0], sp[1], 0.486); cab.add(slot);
      const frame = new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.18, 0.012), mat(0x161c26, 0.5, 0.6));
      frame.position.set(sp[0], sp[1], 0.480); cab.add(frame);
    });
    const ret = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.02, 16),
      mat(0x8a94a4, 0.3, 0.85));
    ret.rotation.x = Math.PI / 2; ret.position.set(0.30, 0.44, 0.484); cab.add(ret);
    [[0.07, 0.36], [0.53, 0.36], [0.07, 0.80], [0.53, 0.80]].forEach(function (bp) {
      const bolt = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.014, 0.012, 6),
        mat(0x9aa4b2, 0.3, 0.9));
      bolt.rotation.x = Math.PI / 2; bolt.position.set(bp[0], bp[1], 0.484); cab.add(bolt);
    });
    S.coinSlotPos = new THREE.Vector3(0.22, 0.66, 0.52);
    S.slotGlow = new THREE.Mesh(new THREE.PlaneGeometry(0.05, 0.14),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(1.8, 1.2, 0.4), transparent: true,
        opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
    S.slotGlow.position.set(0.22, 0.66, 0.492); cab.add(S.slotGlow);
    const coinLabel = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.09),
      new THREE.MeshBasicMaterial({ map: textTexture("25c · ONE BRIEF = ONE PLAN", { fg: "#7d8ea0", w: 512, h: 64, size: 34 }) }));
    coinLabel.position.set(0.30, 0.30, 0.468); cab.add(coinLabel);

    // power lever on the right flank
    S.lever = new THREE.Group();
    const leverBase = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.2, 0.08), mat(0x1a2330, 0.6, 0.4));
    S.lever.add(leverBase);
    const handle = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.3, 10), mat(0x8a94a4, 0.3, 0.85));
    handle.position.y = 0.17; S.lever.add(handle);
    const knob = new THREE.Mesh(new THREE.SphereGeometry(0.045, 14, 10),
      new THREE.MeshStandardMaterial({ color: 0xc22434, roughness: 0.2, envMapIntensity: 1.3 }));
    knob.position.y = 0.33; S.lever.add(knob);
    S.lever.position.set(1.0, 1.5, 0.1); S.lever.rotation.z = 0.6;
    cab.add(S.lever);

    // reward tray at the bottom front
    const trayFrame = new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.34, 0.16), mat(0x05070b, 0.8));
    trayFrame.position.set(-0.25, 0.28, 0.44); cab.add(shadowed(trayFrame));
    S.tray = new THREE.Mesh(new THREE.BoxGeometry(0.92, 0.06, 0.3), mat(0x161d28, 0.45, 0.5));
    S.tray.position.set(-0.25, 0.24, 0.52); cab.add(S.tray);
    const trayLabel = new THREE.Mesh(new THREE.PlaneGeometry(0.7, 0.1),
      new THREE.MeshBasicMaterial({ map: textTexture("PLAN OUT", { fg: "#5a6c80", w: 512, h: 64, size: 40 }) }));
    trayLabel.position.set(-0.25, 0.12, 0.45); cab.add(trayLabel);
    const out = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.04, 0.06), mat(0x000000, 0.5));
    out.position.set(-0.25, 0.44, 0.46); cab.add(out);
    S.paperSlotPos = new THREE.Vector3(-0.25, 0.44, 0.52 + CAB_Z);

    sc.add(cab);
    cab.position.z = CAB_Z; // centred, close to the mural wall (user, 01:18)
    S.cabinet = cab;
    S.bay = bay; S.marqueeFrame = marqueeFrame; S.marqueeGlass = marqueeGlass;
    S.trayFrame = trayFrame; S.trayLabel = trayLabel; S.outStrip = out;
    mountGLBCabinet(sc);
  }

  /* ================= back-alley dressing (v7, user's own models) =================
     van "VANdalized" by billionlioe (CC-BY-4.0), dumpster by DigitalBirb (CC-BY-4.0),
     candy machine by Etman (CC-BY-4.0), Nuka Cola machine by gunduzhan (CC-BY-4.0),
     retro machine by Minh Nguyen (CC-BY-NC-4.0), cat machine by Minh Nguyen
     (CC-BY-NC-ND-4.0), girl mural scan by Guillermo after Tito Ferrara's "Native"
     (CC-BY-NC-SA-4.0), missing-person poster decal by honky (CC-BY-4.0),
     Textures <=2K, geometry quantized;
     the r128 loader reads all of them without extra decoders. */
  function propPrep(root, opts) {
    root.traverse(function (o) { if (o.isMesh) { o.castShadow = !S.smallGpu; o.receiveShadow = true; } }); // the shadow pass draws every caster a second time: on a small GPU only the cabinet and the attendant cast
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    const ctr = box.getCenter(new THREE.Vector3());
    let k = 1;
    if (opts.len) k = opts.len / Math.max(size.x, size.z);
    else if (opts.h) k = opts.h / size.y;
    else if (opts.w) k = opts.w / size.x;
    root.scale.setScalar(k);
    root.position.set(-ctr.x * k, -box.min.y * k, -ctr.z * k);
    const g2 = new THREE.Group();
    g2.add(root);
    g2.rotation.y = opts.rotY || 0;
    g2.position.set(opts.pos[0], opts.y || 0, opts.pos[1]);
    // backs to the wall: the prop's back face is brought exactly to the given plane, whatever its depth (a machine facing +z against the back wall, or facing -x against the right wall)
    if (opts.backZ !== undefined || opts.backX !== undefined) {
      g2.updateMatrixWorld(true);
      const wb = new THREE.Box3().setFromObject(g2);
      if (opts.backZ !== undefined) g2.position.z += opts.backZ - wb.min.z;
      if (opts.backX !== undefined) g2.position.x += opts.backX - wb.max.x;
    }
    return g2;
  }

  /* ---------------- holographic Hall of Fame table ----------------
     "sci-fi hologram table" by irzafy (CC-BY-4.0), docs/ATTRIBUTIONS.md.
     The stock display plane (Object_8) and hologram content mesh (Object_10)
     are hidden at load; the live top-five list is drawn as holographic
     lettering (transparent carrier, additive glow) inside the glow column.
     The whole table stays hidden until the wall's first completion sets
     meta.hall_unlocked (global reveal, never re-locks - owner's answer). */
  const HOLO_POS = { x: 2.5, z: -2.1, s: 0.65, rot: -0.25 }; // right-back Hall zone (operating-table spot freed for it), inside the vending sightline, facing the wide camera
  function holoFmtMs(ms) {
    if (ms == null) return "";
    const m = Math.floor(ms / 60000), sec = Math.floor((ms % 60000) / 1000), mm = ms % 1000;
    return (m > 0 ? m + ":" + String(sec).padStart(2, "0") : sec + "") + "." + String(mm).padStart(3, "0") + (m > 0 ? "" : "s");
  }
  function drawHoloList() {
    const st = S.holo; if (!st || !st.cv) return;
    const resetLine = st.period && st.period.ends_at ? "TIMES RESET " + new Date(st.period.ends_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }).toUpperCase() : "";
    const c2 = st.cv.getContext("2d");
    c2.clearRect(0, 0, 768, 480);
    c2.shadowColor = "rgba(86,220,255,0.85)"; c2.shadowBlur = 18;
    c2.fillStyle = "rgba(158,236,255,0.96)";
    c2.strokeStyle = "rgba(2,10,16,0.92)"; c2.lineWidth = 7; c2.lineJoin = "round";
    c2.font = "bold 38px monospace"; c2.textAlign = "center";
    c2.strokeText("HALL OF FAME", 384, 58); c2.fillText("HALL OF FAME", 384, 58);
    c2.font = "30px monospace";
    const rows = (st.entries || []).slice(0, 5);
    if (!rows.length) {
      c2.strokeText("NO RANKED RUNS YET", 384, 236); c2.fillText("NO RANKED RUNS YET", 384, 236);
      c2.shadowBlur = 8; c2.font = "21px monospace"; c2.fillStyle = "rgba(125,232,255,0.6)";
      c2.fillText("beat the wall challenge to appear here", 384, 284);
    } else {
      rows.forEach(function (r, i) {
        const y = 100 + i * 64; // rows sit high: the pedestal rim only ever covers empty canvas below
        const rank = (r.rank != null ? r.rank : i + 1) + ".";
        const nm = (r.name || "?").slice(0, 14);
        const tm = holoFmtMs(r.elapsed_ms);
        c2.textAlign = "left"; c2.strokeText(rank, 66, y); c2.fillText(rank, 66, y);
        c2.strokeText(nm, 156, y); c2.fillText(nm, 156, y);
        c2.textAlign = "right"; c2.strokeText(tm, 702, y); c2.fillText(tm, 702, y);
      });
    }
    const foot = [resetLine, rows.some(function (r) { return r && r.fixture; }) ? "SAMPLE DATA" : ""].filter(Boolean).join("  \u00b7  ");
    if (foot) { // period reset + fixture honesty, never implied permanent
      c2.shadowBlur = 6; c2.font = "17px monospace"; c2.textAlign = "center"; c2.fillStyle = "rgba(125,232,255,0.55)";
      c2.fillText(foot, 384, 462);
    }
    if (st.tex) st.tex.needsUpdate = true;
  }
  function holoSetVisible(v) {
    const st = S.holo; if (!st) return;
    st.unlocked = v;
    ["root", "list", "light"].forEach(function (k) { if (st[k]) st[k].visible = v; });
    if (!v && st.focused) holoUnfocus();
  }
  function holoFocus() {
    const st = S.holo; if (!st || !st.unlocked || st.focused) return null;
    st.focused = true;
    st.prevCam = snapPrev();
    S.zoomed = true;
    const pose = { pos: [HOLO_POS.x - 0.9, 2.95, HOLO_POS.z + 2.6], tgt: [HOLO_POS.x - 0.4, 1.62, HOLO_POS.z] }; // above the attendant, who now parks nearer the cabinet
    camTo(pose, 900);
    return pose;
  }
  function holoUnfocus() {
    const st = S.holo; if (!st || !st.focused) return;
    st.focused = false;
    S.zoomed = false;
    if (st.prevCam) camTo(exitPose(st.prevCam), 900);
    else camTo(S.camWide, 900);
  }
  function mountHoloTable(sc) {
    if (!THREE.GLTFLoader) return;
    const st = { unlocked: false, entries: [], root: null, list: null, light: null, cv: null, tex: null, focused: false, prevCam: null };
    S.holo = st;
    lazy(4, function () { new THREE.GLTFLoader().load("/static/holo_table.glb", function (g) {
      const root = g.scene;
      root.scale.setScalar(HOLO_POS.s);
      root.position.set(HOLO_POS.x, 0, HOLO_POS.z);
      root.rotation.y = HOLO_POS.rot;
      root.traverse(function (o) {
        if (!o.isMesh) return;
        o.castShadow = true; o.receiveShadow = true;
        if (o.name === "Object_8" || o.name === "Object_10") o.visible = false; // replaced by the live list
      });
      root.visible = false;
      sc.add(root); st.root = root;
      const cv = document.createElement("canvas"); cv.width = 768; cv.height = 480;
      st.cv = cv;
      const tex = new THREE.CanvasTexture(cv); st.tex = tex;
      const list = new THREE.Mesh(
        new THREE.PlaneGeometry(2.7 * HOLO_POS.s, 1.69 * HOLO_POS.s),
        new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide, toneMapped: false, depthWrite: false, blending: THREE.AdditiveBlending }));
      list.position.set(HOLO_POS.x - 0.4, 2.62 * HOLO_POS.s, HOLO_POS.z); // up-left of the pedestal: every glyph sits over the dark mural, fully clear of the disc rim
      list.rotation.y = -0.24; // faces the wide and focus cameras head-on (nearly collinear from this spot) - an angled board foreshortens the times column away
      list.visible = false;
      sc.add(list); st.list = list;
      const gl = new THREE.PointLight(0x56dcff, 0.9, 4.5);
      gl.position.set(HOLO_POS.x, 1.4 * HOLO_POS.s, HOLO_POS.z); gl.visible = false;
      sc.add(gl); st.light = gl;
      drawHoloList();
      holoSetVisible(st.unlocked);
    }, undefined, function () { /* asset missing: the Hall has no physical display, data still reachable via the API */ }); });
    // click / tap the table for a clean board view; arcade.js owns the DOM side (chat hide + back control)
    const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
    S.renderer.domElement.addEventListener("pointerdown", function (ev) {
      const st2 = S.holo;
      if (!st2 || !st2.unlocked || !st2.root || st2.focused) return;
      if (S.wallb && S.wallb.focused) return;
      ptr.x = (ev.clientX / window.innerWidth) * 2 - 1;
      ptr.y = -(ev.clientY / window.innerHeight) * 2 + 1;
      ray.setFromCamera(ptr, S.camera);
      const hits = ray.intersectObjects([st2.root, st2.list].filter(Boolean), true);
      if (hits.length) { S.lastSceneTap = performance.now(); if (S.holoFocusCb) S.holoFocusCb(); }
    });
  }


  /* ---------------- TOP SECRET dossier (easter egg) ----------------
     "Worm Dossier M.E.G Game Ready" by NightmareBunny (CC-BY-4.0),
     docs/ATTRIBUTIONS.md. Lies on the floor
     beside the gumball machine, TOP SECRET stamp face up. Clicking it
     focuses the camera; the paper and sticky note rise out of the
     folder. The cover is one solid mesh with no hinge pivot, so a
     fold-open would distort the model - the lift-out is the faithful
     reveal. The note's source texture is blank, so the handwriting is
     drawn at runtime on a canvas card riding the note mesh.
     Clue honesty: the note points at the hidden left-wall secret and
     the return challenge; the monthly Hall of Fame is earned by the
     timed run, never promised for signing alone. */
  const DOSSIER_POS = { x: -2.35, z: -2.45, len: 0.55, rotY: 0.65 };
  const DOSSIER_NOTE_LINES = [
    "AGENT'S NOTE -",
    "the LEFT WALL keeps secrets.",
    "Sign it, then look closer:",
    "what hides in the paint leads",
    "to the real challenge.",
    "Beat the clock and the",
    "HALL OF FAME opens a slot -",
    "new round every month.  - E.",
  ];
  function drawDossierNote(cv) {
    const c2 = cv.getContext("2d");
    c2.fillStyle = "#b5a670"; // kept under the bloom threshold (0.8): a bright note whites out
    c2.fillRect(0, 0, cv.width, cv.height);
    c2.strokeStyle = "rgba(60,45,10,0.55)"; c2.lineWidth = 6;
    c2.strokeRect(3, 3, cv.width - 6, cv.height - 6);
    c2.fillStyle = "#1c1c2c"; c2.textAlign = "left";
    c2.font = "26px 'Comic Sans MS', 'Segoe Print', cursive";
    DOSSIER_NOTE_LINES.forEach(function (ln, i) { c2.fillText(ln, 22, 52 + i * 40); });
  }
  function dossierSetOpen(open) {
    const st = S.dossier; if (!st || !st.paper || !st.note || st.open === open) return;
    st.open = open;
    const k = st.root.scale.x || 1;
    const pTo = open ? st.paperHome.clone().add(new THREE.Vector3(-0.06 / k, 0.03 / k, 0.02 / k)) : st.paperHome;
    const nTo = open ? st.noteHome.clone().add(new THREE.Vector3(0.04 / k, 0.115 / k, 0.06 / k)) : st.noteHome;
    const p0 = st.paper.position.clone(), n0 = st.note.position.clone();
    tween(650, easeInOut, function (t) {
      st.paper.position.lerpVectors(p0, pTo, t);
      st.note.position.lerpVectors(n0, nTo, t);
    }); // tween jumps to the end state when prefers-reduced-motion is set
  }
  function dossierFocus() {
    const st = S.dossier; if (!st || st.focused) return null;
    st.focused = true;
    st.prevCam = snapPrev();
    S.zoomed = true;
    const pose = { pos: [DOSSIER_POS.x + 0.1, 0.5, DOSSIER_POS.z + 0.28], tgt: [DOSSIER_POS.x + 0.05, 0.1, DOSSIER_POS.z - 0.16] }; // tight on the risen note so the writing fills the frame
    camTo(pose, 800);
    dossierSetOpen(true);
    return pose;
  }
  function dossierUnfocus() {
    const st = S.dossier; if (!st || !st.focused) return;
    st.focused = false;
    S.zoomed = false;
    dossierSetOpen(false);
    if (st.prevCam) camTo(exitPose(st.prevCam), 800);
    else camTo(S.camWide, 800);
  }
  function mountDossier(sc) {
    if (!THREE.GLTFLoader) return;
    new THREE.GLTFLoader().load("/static/secret_dossier.glb", function (g) {
     try {
      const root = g.scene;
      const grp = propPrep(root, { len: DOSSIER_POS.len, pos: [DOSSIER_POS.x, DOSSIER_POS.z], rotY: DOSSIER_POS.rotY });
      sc.add(grp);
      const st = { root: root, grp: grp, paper: null, note: null, card: null, open: false, focused: false, prevCam: null };
      S.dossier = st;
      root.traverse(function (o) {
        if (/^SM_Paper/.test(o.name)) st.paper = o;
        if (/^SM_Note/.test(o.name)) st.note = o;
      });
      // the paper and note materials ship white-on-white and blow out under the
      // room's bloom pass; tame their color/emissive at load so the reveal and
      // the writing stay readable (the source file itself is not modified)
      [st.paper, st.note].forEach(function (nd) {
        if (!nd) return;
        nd.traverse(function (o) {
          if (!o.isMesh || !o.material) return;
          if (o.material.color) o.material.color.setRGB(0.2, 0.18, 0.13); // dim manila: pure-white albedo under ~2.7 irradiance sails past the 0.8 bloom threshold and whites out
          if (o.material.emissive) o.material.emissive.setScalar(0);
          if ("metalness" in o.material) o.material.metalness = 0;
          if ("specularIntensity" in o.material) o.material.specularIntensity = 0;
        });
      });
      sc.updateMatrixWorld(true);
      const k = root.scale.x || 1;
      if (st.note) {
        const cv = document.createElement("canvas"); cv.width = 512; cv.height = 384;
        drawDossierNote(cv);
        const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
        const nb = new THREE.Box3().setFromObject(st.note);
        const card = new THREE.Mesh(
          new THREE.PlaneGeometry(((nb.max.x - nb.min.x) * 0.94) / k, ((nb.max.z - nb.min.z) * 0.94) / k),
          new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2 }));
        card.material.toneMapped = true;
        card.position.copy(st.note.worldToLocal(new THREE.Vector3((nb.min.x + nb.max.x) / 2, nb.max.y + 0.002, (nb.min.z + nb.max.z) / 2)));
        card.quaternion.copy(st.note.getWorldQuaternion(new THREE.Quaternion()).invert());
        card.rotateX(-Math.PI / 2); // plane faces +Y in world: readable from above
        card.renderOrder = 3;
        st.note.add(card);
        st.card = card;
      }
      if (st.paper) st.paperHome = st.paper.position.clone();
      if (st.note) st.noteHome = st.note.position.clone();
      const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
      S.renderer.domElement.addEventListener("pointerdown", function (ev) {
        const st2 = S.dossier;
        if (!st2 || st2.focused) return;
        if (S.holo && S.holo.focused) return;
        if (S.wallb && S.wallb.focused) return;
        ptr.x = (ev.clientX / window.innerWidth) * 2 - 1;
        ptr.y = -(ev.clientY / window.innerHeight) * 2 + 1;
        ray.setFromCamera(ptr, S.camera);
        if (ray.intersectObject(grp, true).length) { S.lastSceneTap = performance.now(); if (S.dossierFocusCb) S.dossierFocusCb(); }
      });
     } catch (err) { console.error('dossier model:', err); }
    }, undefined, function (err) { console.error('dossier model load:', err && err.message || err); });
  }


  /* ---------------- the vending corner: tap a machine to look at it ----------------
     Same tuck-away pattern as the holo table and the dossier: the page collapses the chat, shows the machine's name, and a tap anywhere else (or Escape) returns. */
  function vendPose(v) {
    // the camera stands in front of the machine, looking at its middle. The one on the side wall is seen from a little further along the room, at an angle, so the two
    // machines on the back wall are not in the way.
    const fx = Math.sin(v.ry), fz = Math.cos(v.ry), d = v.h * 1.2 + 0.8, along = v.ry !== 0 ? 2.3 : 0;
    return { pos: [v.x + fx * d, v.h * 0.7 + 0.25, v.z + fz * d + along], tgt: [v.x, v.h * 0.52, v.z] };
  }
  function vendFocus(i) {
    const v = (S.vend || [])[i]; if (!v || S.vendFocused != null) return null;
    S.vendFocused = i;
    S.vendPrevCam = snapPrev();
    S.zoomed = true;
    const pose = vendPose(v);
    camTo(pose, 850);
    return { pose: pose, label: v.label };
  }
  function vendUnfocus() {
    if (S.vendFocused == null) return;
    S.vendFocused = null; S.zoomed = false;
    if (S.vendPrevCam) camTo(exitPose(S.vendPrevCam), 850);
    else camTo(S.camWide, 850);
  }
  function wireVendTaps() {
    const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
    S.renderer.domElement.addEventListener("pointerdown", function (ev) {
      if (S.vendFocused != null || !S.vend || !S.vend.length) return;
      if (S.zoomed || (S.holo && S.holo.focused) || (S.dossier && S.dossier.focused) || (S.wallb && S.wallb.focused)) return;
      ptr.x = (ev.clientX / window.innerWidth) * 2 - 1;
      ptr.y = -(ev.clientY / window.innerHeight) * 2 + 1;
      ray.setFromCamera(ptr, S.camera);
      let best = -1, bestD = Infinity;
      S.vend.forEach(function (v, i) {
        const h = ray.intersectObject(v.grp, true);
        if (h.length && h[0].distance < bestD) { best = i; bestD = h[0].distance; }
      });
      if (best >= 0) { S.lastSceneTap = performance.now(); if (S.vendFocusCb) S.vendFocusCb(best); }
    });
  }

  /* ---------------- the signature wall (I WAS HERE) ----------------
     Contract 8.6: one permanent name per visitor scratched on the left
     wall, a return-visit challenge (a name secretly moves; find where),
     Hall opt-in on a found round. The board is a transparent canvas
     plane on the wall; its content always mirrors the real backend
     state: sleeping (backend absent), blank, a section page, or the
     frozen round view drawn ONLY from the round's snapshot (never the
     live list) with the move/swap applied. */
  const WALLB = { x: -5.56, y: 1.62, z: -2.35, w: 3.0, h: 1.95 };
  function wallDraw() {
    const st = S.wallb; if (!st || !st.cv) return;
    const c2 = st.cv.getContext("2d");
    const W = st.cv.width, H = st.cv.height;
    c2.clearRect(0, 0, W, H);
    const d = st.data || { mode: "sleep" };
    /* Everything on this wall is CUT into the stone, never printed on it (strategy: wall carving). Each glyph is two fills: a dark shadowed upper edge (offset -1.4, -2.0)
       under a lighter chalk-stone cut face, with a tiny per-glyph rotation so it reads hand-cut. The clue is an incised serif capital face; monospace stays only for the
       mechanics (names, slot numbers). No rectangles, no marker strokes, no panel: legibility comes from a soft radial wash, never a box. */
    const SERIF = 'Georgia, "Times New Roman", serif', MONO = "monospace";
    const FACE = "rgba(226,222,206,0.95)", FAINT = "rgba(226,222,206,0.30)", MINE = "rgba(125,232,255,0.97)";
    const carve = function (txt, x, y, font, face) {
      c2.font = font; c2.textAlign = "left"; c2.textBaseline = "alphabetic";
      const chars = Array.from(String(txt)), widths = chars.map(function (ch) { return c2.measureText(ch).width; });
      let cx = x - widths.reduce(function (a2, b2) { return a2 + b2; }, 0) / 2;
      chars.forEach(function (ch, i) {
        const jitter = (((Math.abs(x * 7 + y * 13 + i * 31) | 0) % 9) - 4) * 0.006;
        c2.save(); c2.translate(cx, y); c2.rotate(jitter); c2.lineJoin = "round";
        c2.fillStyle = "rgba(8,10,16,0.82)"; c2.fillText(ch, -1.4, -2.0); // the shadowed upper edge of the cut
        c2.fillStyle = face || FACE; c2.fillText(ch, 0, 0);               // the lit cut face
        c2.restore();
        cx += widths[i];
      });
    };
    const groove = function (x0, y0, x1, y1, col) {
      c2.lineCap = "round";
      c2.strokeStyle = "rgba(8,10,16,0.55)"; c2.lineWidth = 3; c2.beginPath(); c2.moveTo(x0 - 1, y0 - 1.6); c2.lineTo(x1 - 1, y1 - 1.6); c2.stroke();
      c2.strokeStyle = col || "rgba(226,222,206,0.55)"; c2.lineWidth = 2; c2.beginPath(); c2.moveTo(x0, y0); c2.lineTo(x1, y1); c2.stroke();
    };
    const wash = function (cx, cy, rx, ry, a) { // a soft dark halo behind the lettering, fading to nothing: enough to read over the mottling, never a panel
      c2.save(); c2.translate(cx, cy); c2.scale(1, ry / rx);
      const g = c2.createRadialGradient(0, 0, 0, 0, 0, rx);
      g.addColorStop(0, "rgba(8,10,16," + (a == null ? 0.22 : a) + ")"); g.addColorStop(1, "rgba(8,10,16,0)");
      c2.fillStyle = g; c2.beginPath(); c2.arc(0, 0, rx, 0, 6.283); c2.fill(); c2.restore();
    };
    const ghostLine = function (a2, fn) { c2.save(); c2.globalAlpha = a2; fn(); c2.restore(); };
    if (!st.focused) {
      // idle room view: the secret is a faint carved ghost - no panel, no status copy (backend status shows only in the focused view). The render loop ties the
      // mesh opacity to the viewing angle, so the cut catches the eye at a grazing angle and fades square-on.
      ghostLine(0.2, function () {
        carve("I  W A S  H E R E", W / 2, 84, "bold 44px " + SERIF);
        groove(240, 108, W - 260, 112);
        if (d.slots && d.slots.length) {
          d.slots.forEach(function (m, i) { carve(String(m.name || "?").slice(0, 12), 150 + (i % 4) * 240, 196 + Math.floor(i / 4) * 130, "bold 30px " + MONO); });
        }
        for (let k = 0; k < 3; k++) { const sx = 130 + k * 330, sy = H - 96 - ((k * 53) % 60); groove(sx, sy, sx + 52, sy + ((k % 2) ? 24 : -18)); } // a few stray grooves
      });
      if (st.tex) st.tex.needsUpdate = true;
      return;
    }
    wash(W / 2, H / 2, W * 0.5, H * 0.5, 0.22);
    if (d.mode === "locked") { // the entry puzzle: torn halves line up only at off = 0
      const off = st.alignOff == null ? 120 : st.alignOff;
      const titleY = H * 0.44, font = "bold 80px " + SERIF;
      c2.save(); c2.beginPath(); c2.rect(0, 0, W, titleY); c2.clip();
      carve("I  W A S  H E R E", W / 2 + off, titleY + 24, font); c2.restore();
      c2.save(); c2.beginPath(); c2.rect(0, titleY, W, 78); c2.clip();
      carve("I  W A S  H E R E", W / 2 - off * 0.8, titleY + 24, font); c2.restore();
      for (let k = 0; k < 7; k++) {
        const sx = 140 + k * 118 + ((k % 2) ? off : -off * 0.8), sy = H * 0.62 + ((k * 37) % 90);
        groove(sx, sy, sx + 42, sy + ((k % 2) ? 26 : -20));
      }
      carve("the lines refuse to sit still", W / 2, H - 96, "italic 26px " + SERIF, "rgba(226,222,206,0.85)");
      carve("drag the view sideways until they sit true", W / 2, H - 58, "italic 26px " + SERIF, MINE);
      if (st.tex) st.tex.needsUpdate = true;
      return;
    }
    carve("I  W A S  H E R E", W / 2, 62, "bold 44px " + SERIF);
    groove(200, 86, W - 200, 90);
    st.cells = [];
    if (d.mode === "sleep") {
      carve("the wall sleeps", W / 2, H / 2 + 6, "italic 34px " + SERIF, "rgba(226,222,206,0.82)"); // one quiet line; the backend's reason is in the small status line below
      if (st.tex) st.tex.needsUpdate = true;
      return;
    }
    if (d.mode === "blank") {
      carve("no names yet", W / 2, H / 2 - 8, "30px " + SERIF, FAINT);
      carve("be the first to sign", W / 2, H / 2 + 36, "italic 22px " + SERIF, FAINT);
      if (st.tex) st.tex.needsUpdate = true;
      return;
    }
    // section or round: build the drawn view (round = frozen snapshot + move/swap applied)
    let cap, view;
    if (d.mode === "round") {
      cap = d.snapshot.capacity;
      view = new Array(cap).fill(null);
      (d.snapshot.slots || []).forEach(function (m) { if (m.slot >= 0 && m.slot < cap) view[m.slot] = m; });
      const mineM = view[d.from_slot] && view[d.from_slot].mark_id === d.my_mark_id ? view[d.from_slot] : null;
      if (mineM) {
        const other = view[d.to_slot] || null;
        view[d.to_slot] = mineM;        // the secret move
        view[d.from_slot] = other;      // swap back, or empty
      }
    } else {
      cap = d.capacity || 12;
      view = new Array(cap).fill(null);
      (d.slots || []).forEach(function (m) { if (m.slot >= 0 && m.slot < cap) view[m.slot] = m; });
    }
    const cols = cap <= 12 ? 4 : 6, rows = Math.ceil(cap / cols);
    const mx = 70, top = 116, cw = (W - 2 * mx) / cols, ch = (H - top - 30) / rows;
    for (let i = 0; i < cap; i++) {
      const cx = mx + (i % cols) * cw + cw / 2, cy = top + Math.floor(i / cols) * ch + ch / 2;
      const m = view[i];
      const cell = { slot: i, mark_id: m ? m.mark_id : null, name: m ? m.name : null,
        x0: cx - cw / 2, y0: cy - ch / 2, x1: cx + cw / 2, y1: cy + ch / 2 };
      st.cells.push(cell);
      if (m) {
        const liveSearch = d.mode === "round" && !d.result; // pre-reveal every name looks alike
        const isMine = !liveSearch && d.my_mark_id != null && m.mark_id === d.my_mark_id;
        carve(String(m.name || "?").slice(0, 12), cx, cy + 12, "bold " + (cols === 4 ? 38 : 26) + "px " + MONO, isMine ? MINE : FACE);
        if (isMine) groove(cx - 60, cy + 26, cx + 60, cy + 28, "rgba(125,232,255,0.8)");
      } else {
        c2.save();
        c2.setLineDash([7, 9]);
        c2.strokeStyle = "rgba(226,222,206,0.14)"; c2.lineWidth = 2;
        c2.strokeRect(cx - cw / 2 + 14, cy - ch / 2 + 10, cw - 28, ch - 20);
        c2.restore();
        if (d.mode === "round") carve(String(i), cx, cy + 8, "20px " + MONO, "rgba(226,222,206,0.4)"); // tap reference
      }
    }
    if (d.mode === "round" && d.result) { // reveal: circle the true destination
      const c0 = st.cells[d.to_slot];
      if (c0) {
        c2.strokeStyle = d.result === "found" ? "rgba(89,214,143,0.95)" : "rgba(255,90,90,0.95)";
        c2.lineWidth = 5;
        c2.strokeRect(c0.x0 + 8, c0.y0 + 6, c0.x1 - c0.x0 - 16, c0.y1 - c0.y0 - 12);
      }
    }
    if (st.tex) st.tex.needsUpdate = true;
  }
  function wallFocus() {
    const st = S.wallb; if (!st || st.focused) return null;
    st.focused = true;
    st.prevCam = snapPrev();
    S.zoomed = true;
    // fit the whole board in frame at any aspect: portrait phones need to be much farther back.
    // the board occupies the upper frame so the DOM panel below never covers cells;
    // past the cabinet (x ~ -1) the camera shifts toward +z so the cabinet no longer
    // sits between it and the board's right columns.
    const halfFov = (S.camera.fov || 55) * Math.PI / 360;
    const tan = Math.tan(halfFov);
    const aspect = window.innerWidth / Math.max(1, window.innerHeight);
    const dist = Math.max(3.1, 1.9 / (tan * aspect), 1.95 / (2 * tan * 0.58));
    const zOff = Math.max(0, dist - 4.0) * 0.3;
    const drop = 0.10 * 2 * tan * dist;
    const pose = { pos: [WALLB.x + dist, WALLB.y + 0.02, WALLB.z + zOff], tgt: [WALLB.x, WALLB.y - drop, WALLB.z] };
    camTo(pose, 850);
    return pose;
  }
  function wallUnfocus() {
    const st = S.wallb; if (!st || !st.focused) return;
    st.focused = false;
    S.zoomed = false;
    if (st.prevCam) camTo(exitPose(st.prevCam), 850);
    else camTo(S.camWide, 850);
  }
  function mountWallBoard(sc, wallL) {
    const cv = document.createElement("canvas"); cv.width = 1024; cv.height = 640;
    const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(WALLB.w, WALLB.h),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.FrontSide, toneMapped: false, depthWrite: false }));
    mesh.position.set(wallL + 0.11, WALLB.y, WALLB.z); // in front of the poster decals at +0.05: no occlusion
    mesh.renderOrder = 3; // poster decals are renderOrder 2, transparent, depthWrite off: the board must paint after them or they cover the names
    mesh.rotation.y = Math.PI / 2; // faces into the room
    sc.add(mesh);
    S.wallb = { cv: cv, tex: tex, mesh: mesh, data: null, cells: [], focused: false, prevCam: null, alignOff: null };
    wallDraw(); // starts in the honest "sleeping" state until the backend answers
    let dragX = null;
    S.renderer.domElement.addEventListener("pointerdown", function (ev) {
      if (S.wallb.focused && S.wallb.data && S.wallb.data.mode === "locked") dragX = ev.clientX;
    });
    window.addEventListener("pointermove", function (ev) {
      if (dragX == null || !S.wallb.focused) return;
      const dx = ev.clientX - dragX; dragX = ev.clientX;
      const scale = cv.width / (S.renderer.domElement.clientWidth || window.innerWidth || 1);
      let off = S.wallb.alignOff == null ? 120 : S.wallb.alignOff;
      off -= dx * scale;
      if (off > 260) off = 260; if (off < -260) off = -260;
      if (Math.abs(off) <= 14) {
        S.wallb.alignOff = 0; dragX = null; wallDraw();
        if (S.wallSolveCb) S.wallSolveCb();
        return;
      }
      S.wallb.alignOff = off; wallDraw();
    });
    window.addEventListener("pointerup", function () { dragX = null; });
    const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
    S.renderer.domElement.addEventListener("pointerdown", function (ev) {
      const st = S.wallb; if (!st) return;
      ptr.x = (ev.clientX / window.innerWidth) * 2 - 1;
      ptr.y = -(ev.clientY / window.innerHeight) * 2 + 1;
      ray.setFromCamera(ptr, S.camera);
      const hits = ray.intersectObject(mesh, false);
      if (!hits.length) return;
      S.lastSceneTap = performance.now(); // a board tap is never a background click
      if (!st.focused) {
        if ((S.holo && S.holo.focused) || (S.dossier && S.dossier.focused)) return;
        if (S.wallFocusCb) S.wallFocusCb();
        return;
      }
      // focused: a tap lands on a cell (the challenge's answer)
      const uv = hits[0].uv; if (!uv) return;
      const px = uv.x * cv.width, py = (1 - uv.y) * cv.height;
      const cell = (st.cells || []).find(function (c) { return px >= c.x0 && px <= c.x1 && py >= c.y0 && py <= c.y1; });
      if (cell && S.wallTapCb) S.wallTapCb(cell);
    });
  }

  function mountAlley(sc, wallBack, wallL, wallR) {
    if (!THREE.GLTFLoader) return;
    const loader = new THREE.GLTFLoader();
    const put = function (url, opts, onPlaced, prio) {
      lazy(prio == null ? 2 : prio, function () {
        loader.load(url, function (g) { const grp = propPrep(g.scene, opts); sc.add(grp); if (onPlaced) onPlaced(false, grp); }, undefined,
          function () { /* prop missing: the den still works */ if (onPlaced) onPlaced(true); });
      });
    };
    // the procedural filler cabinets step aside for the user's real props
    (S.roomCabs || []).forEach(function (c) { c.visible = false; });
    // graffiti van pulled up close to the camera, nose angled toward the cabinet;
    // only its front half stays in frame - the rear hangs out of view on purpose
    put('/static/alley_van.glb', { len: 3.8, pos: [-3.35, 3.05], rotY: 2.05 });
    put('/static/alley_dumpster.glb', { h: 1.42, pos: [2.6, 4.5], rotY: -0.5 }); // front floor, out of every hologram sightline (user)
    // THE VENDING CORNER (user, 15:00): the three machines stand together where the right wall meets the back wall, each turned to face the arcade cabinet in the
    // middle of the room. Two against the back wall, the tall cat one against the right wall; the holo table keeps its own spot in front of them.
    // Facing is computed, not guessed: a prop's front looks along (sin rotY, cos rotY), so rotY = atan2(dx, dz) to the point it should look at.
    // Backs flat to the wall (user, 15:30): the two on the back wall face straight out into the room, the cat one on the right wall faces straight across it. The mural's
    // slab stands about 0.4 in front of the wall plane, so "the wall" for a machine's back is that slab.
    const BACK_Z = wallBack + 0.42, BACK_X = wallR - 0.08;
    const corner = function (key, label, url, h, x, z, side, onLoaded, prio) {
      const ry = side === 'right' ? -Math.PI / 2 : 0;
      const o = { h: h, pos: [x, z], rotY: ry };
      if (side === 'right') o.backX = BACK_X; else o.backZ = BACK_Z;
      put(url, o, function (failed, grp) {
        if (!failed && grp) { const bx = new THREE.Box3().setFromObject(grp), ctr = bx.getCenter(new THREE.Vector3()); (S.vend = S.vend || []).push({ key: key, label: label, grp: grp, x: ctr.x, z: ctr.z, ry: ry, h: h }); }
        if (onLoaded) onLoaded(failed, grp);
      }, prio == null ? 1 : prio);
    };
    corner('retro', 'RETRO-FUTURE VENDING MACHINE', '/static/vend_retro.glb', 1.78, 2.9, -4.0);
    corner('nuka', 'NUKA-COLA VENDING MACHINE', '/static/vend_nuka.glb', 1.85, 4.05, -4.0);
    // the big cat vending machine (CC BY-NC-ND: the original 54 MB file ships unmodified, so it is the one heavy asset). A laptop-class device fetches it
    // by itself once the room has settled; a phone, a small computer, a Save-Data or slow connection leaves it to the visitor (the CAT button, or ?cat=1).
    const catBtn = document.getElementById('cat-load');
    let catAsked = false;
    const loadCat = function () {
      if (catAsked) return; catAsked = true;
      if (catBtn) { catBtn.disabled = true; catBtn.classList.add('done'); catBtn.title = 'cat vending machine loading\u2026'; }
      corner('cat', 'CAT VENDING MACHINE', '/static/vend_cat.glb', 1.82, 5.0, -2.85, 'right', function (failed) {
        if (catBtn) {
          catBtn.classList.toggle('failed', !!failed);
          catBtn.classList.toggle('done', !failed);
          catBtn.title = failed ? 'cat vending machine unavailable' : 'cat vending machine loaded';
          catBtn.setAttribute('aria-label', catBtn.title);
        }
      }, 9);
    };
    if (catBtn) catBtn.addEventListener('click', loadCat);
    const conn = navigator.connection || {};
    const slowLink = conn.saveData || /(^|-)(2g|3g)$/.test(conn.effectiveType || '');
    const softGL = (function () { // a software rasteriser (SwiftShader, llvmpipe) cannot hold a 54 MB model's textures: leave the cat to the button
      try { const gl = S.renderer.getContext(), x = gl.getExtension('WEBGL_debug_renderer_info'); return !!x && /swiftshader|llvmpipe|software/i.test(String(gl.getParameter(x.UNMASKED_RENDERER_WEBGL))); } catch (e) { return false; }
    })();
    if (new URLSearchParams(location.search).has('cat')) loadCat();
    else if (TIER === 'high' && !slowLink && !softGL) setTimeout(function () { (window.requestIdleCallback || function (f) { f(); })(loadCat, { timeout: 4000 }); }, 3500);
    // gumball machine near the front-left corner, out of the attendant's path
    put('/static/alley_candy.glb', { h: 1.3, pos: [-1.45, -3.05], rotY: 0.12 }); // directly LEFT of the cabinet (user)

    // THE wall behind the cabinet: only the girl mural, nothing else
    lazy(0, function () { loader.load('/static/wall_mural.glb', function (g) {
      const root = g.scene;
      root.traverse(function (o) { if (o.isMesh) o.receiveShadow = true; });
      const box = new THREE.Box3().setFromObject(root);
      const size = box.getSize(new THREE.Vector3());
      const ctr = box.getCenter(new THREE.Vector3());
      // full back-wall coverage: floor to above the camera's sightline at the wall;
      // the horizontal spill hides behind the two side walls
      const k = 9.4 / size.y; // taller (user, 15:30): the art runs up toward the ceiling instead of stopping short of a bare wall above it
      // the scan's art slab sits near model z -0.55 with street junk out to +0.7;
      // flatten depth to 12% so the slab hugs the wall and the junk reads as shallow relief
      root.scale.set(k, k, k * 0.12);
      root.position.set(-ctr.x * k, -box.min.y * k - 0.04, wallBack + 0.30 + 0.55 * k * 0.12);
      if (new URLSearchParams(location.search).has('muraldebug')) {
        root.traverse(function (o) { if (o.isMesh) o.material = new THREE.MeshBasicMaterial({ color: 0xff2222 }); });
      }
      sc.updateMatrixWorld(true);
      const wb = new THREE.Box3().setFromObject(root);
      sc.add(root);
      const muralLight = new THREE.PointLight(0xffd9b0, 1.4, 9);
      muralLight.position.set(0, 3.4, wallBack + 2.1); sc.add(muralLight);
    }, undefined, function () { }); });
    // missing-person poster decal by honky (CC-BY-4.0): cropped pieces of the
    // source overlay, layered over the generated graffiti - never the raw wall
    const posterCrop = function (img, sx, sy, sw, sh, x, y, z, faceLeft, w, tilt) {
      const cv = document.createElement('canvas');
      cv.width = Math.max(2, Math.round(sw)); cv.height = Math.max(2, Math.round(sh));
      cv.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, cv.width, cv.height);
      const tex = new THREE.CanvasTexture(cv);
      tex.colorSpace = THREE.SRGBColorSpace;
      const hgt = w * (cv.height / cv.width);
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, hgt),
        new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
      m.rotation.y = faceLeft ? Math.PI / 2 : -Math.PI / 2;
      m.rotation.z = tilt + Math.PI; // source overlay is upside-down; flip in-plane
      m.position.set(x, y, z);
      m.renderOrder = 2;
      sc.add(m);
    };
    lazy(3, function () { loader.load('/static/wall_posters.glb', function (g) {
      let img = null;
      g.scene.traverse(function (o) { if (!img && o.isMesh && o.material && o.material.map && o.material.map.image) img = o.material.map.image; });
      if (!img) return;
      const iw = img.width, ih = img.height;
      // right wall: three cropped clusters at different heights and tilts
      posterCrop(img, 0, 0, iw * 0.52, ih * 0.58, wallR - 0.05, 2.75, -2.6, false, 2.5, 0.03);
      posterCrop(img, iw * 0.45, ih * 0.30, iw * 0.55, ih * 0.62, wallR - 0.05, 2.15, -0.4, false, 2.1, -0.04);
      posterCrop(img, iw * 0.10, ih * 0.55, iw * 0.45, ih * 0.45, wallR - 0.05, 3.15, 0.9, false, 1.6, 0.06);
      // the left wall carries no posters: it is plain stone for the carved name
    }, undefined, function () { }); });
    // a little warm life over the vending row
    const rowGlow = new THREE.PointLight(0xffb35c, 0.3, 6.5);
    rowGlow.position.set(4.1, 2.9, -2.6); sc.add(rowGlow);
    const cornerNeon = new THREE.PointLight(0xff4fd0, 0.5, 7.5); cornerNeon.position.set(3.6, 3.4, -1.6); sc.add(cornerNeon); // a magenta wash for the machines' faces
  }

  // If the user-selected CC-BY model ships as /static/cabinet.glb, mount it as
  // the cabinet shell and keep only the functional overlays from the
  // procedural stand-in (live CRT, lit marquee, lever, tray, slot targets).
  function mountGLBCabinet(sc) {
    if (!THREE.GLTFLoader) return;
    new THREE.GLTFLoader().load('/static/cabinet.glb', function (g) {
      const root = g.scene;
      let glbScreen = null;
      root.traverse(function (o) {
        if (o.isMesh) {
          o.castShadow = true; o.receiveShadow = true;
          if (o.material && o.material.name === 'Arcade_Screen') {
            glbScreen = o; // the cabinet's own CRT: wired to the live road canvas below
          } else if (o.material) {
            o.material.emissiveIntensity = 0.05; o.material.envMapIntensity = 0.7; o.material.needsUpdate = true;
          }
        }
      });
      let box = new THREE.Box3().setFromObject(root);
      const size = box.getSize(new THREE.Vector3());
      const k = 2.52 / size.y;
      root.scale.setScalar(k);
      root.rotation.y = 0; // model screen normal is +z (measured from GLB): front already faces the room
      box = new THREE.Box3().setFromObject(root);
      const ctr = box.getCenter(new THREE.Vector3());
      root.position.set(-ctr.x, -box.min.y, -ctr.z + CAB_Z); // same wall pull as the cabinet
      sc.add(root); S.cabGLB = root;
      // hide the procedural stand-in entirely; the GLB is the whole cabinet now
      S.cabinet.children.forEach(function (ch) { ch.visible = false; });
      // world-space front face of the mounted cabinet (for slot targets + glow light)
      const wbox = new THREE.Box3().setFromObject(root);
      const frontZ = wbox.max.z;
      if (glbScreen) {
        // live road module on the cabinet's own screen; basic material + no tone
        // mapping so it reads clean and moderately brighter than the old dim CRT.
        // measured GLB screen UVs are canonical (U+ with +X, V+ with +Y): no mirroring
        glbScreen.material = new THREE.MeshBasicMaterial({ map: scr.tex, toneMapped: false });
        glbScreen.material.color.setScalar(1.15);
        S.screenMesh = glbScreen; // fitZoom and focus shots track this mesh
        // measured world normal of the GLB screen (tilts up ~57deg): fitZoom must
        // sit along THIS axis, not the old procedural cabinet's shallow tilt
        const na = glbScreen.geometry.attributes.normal;
        if (na) {
          // average ALL vertex normals: the screen is curved, so any single
          // edge vertex points sideways and pushes the zoom camera off-axis
          const wn = new THREE.Vector3();
          for (let vi = 0; vi < na.count; vi++) {
            wn.x += na.getX(vi); wn.y += na.getY(vi); wn.z += na.getZ(vi);
          }
          wn.normalize();
          glbScreen.updateWorldMatrix(true, false);
          wn.transformDirection(glbScreen.matrixWorld);
          if (wn.z < 0) wn.negate();
          S.screenN = wn;
        }
      }
      // CRT glow light survives (it is a light child of the hidden procedural group)
      if (S.screenLight) { S.screenLight.visible = true; S.screenLight.position.set(0, 1.6, frontZ + 2.6 + 0.6); }
      S.paperSlotPos.set(-0.1, 0.44, frontZ + 0.06);
      S.coinSlotPos.set(0.15, 0.62, frontZ + 0.05);
      fitZoom();
    }, undefined, function () { /* no GLB shipped: keep the procedural cabinet */ });
  }

  /* ================= the attendant (3D character) ================= */
  function capsule(r, len, material) {
    const g = new THREE.Group();
    const cyl = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 12), material);
    g.add(cyl);
    const t = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 8), material);
    t.position.y = len / 2; g.add(t);
    const b = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 8), material);
    b.position.y = -len / 2; g.add(b);
    g.traverse(function (m) { if (m.isMesh) shadowed(m); });
    return g;
  }

  function buildAttendant(sc) {
    const g = new THREE.Group();
    const shellMat = new THREE.MeshStandardMaterial({ color: 0x2c3550, roughness: 0.32,
      metalness: 0.85, envMapIntensity: 1.15 });
    const darkMat = new THREE.MeshStandardMaterial({ color: 0x141a26, roughness: 0.5,
      metalness: 0.6, envMapIntensity: 0.8 });
    // hover base with glow ring + down light
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.21, 0.09, 24), darkMat);
    base.position.y = 0.055; shadowed(base); g.add(base);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.185, 0.014, 8, 32),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(0.5, 1.7, 2.0) }));
    ring.rotation.x = Math.PI / 2; ring.position.y = 0.03; g.add(ring);
    const glow = new THREE.Mesh(new THREE.CircleGeometry(0.26, 24),
      new THREE.MeshBasicMaterial({ map: glowDotTexture(), color: 0x3fd2ff, transparent: true,
        opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false }));
    glow.rotation.x = -Math.PI / 2; glow.position.y = 0.006; g.add(glow);
    const hover = new THREE.PointLight(0x3fd2ff, 0.55, 1.8);
    hover.position.y = 0.12; g.add(hover);
    // torso: lathed rounded shell
    const torsoPts = [];
    [[0.0, 0.0], [0.13, 0.0], [0.16, 0.06], [0.17, 0.16], [0.15, 0.28], [0.10, 0.35], [0.04, 0.38], [0.0, 0.385]]
      .forEach(function (p) { torsoPts.push(new THREE.Vector2(p[0], p[1])); });
    const torso = new THREE.Mesh(new THREE.LatheGeometry(torsoPts, 24), shellMat);
    torso.position.y = 0.12; shadowed(torso); g.add(torso);
    S.attBody = torso;
    const chest = new THREE.Mesh(new THREE.CircleGeometry(0.04, 16),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(0.35, 1.5, 1.9) }));
    chest.position.set(0, 0.36, 0.155); g.add(chest);
    S.attChest = chest;
    // head: rounded dome + visor band + antenna
    const headG = new THREE.Group();
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.135, 24, 16), shellMat);
    head.scale.set(1.05, 0.82, 0.95); shadowed(head); headG.add(head);
    const visor = new THREE.Mesh(new THREE.CylinderGeometry(0.128, 0.128, 0.045, 24, 1, true, -0.7, 1.4),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(0.4, 1.6, 2.0), side: THREE.DoubleSide }));
    visor.position.y = 0.008; headG.add(visor);
    S.attVisor = visor;
    const ant = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.12, 6), darkMat);
    ant.position.set(0.09, 0.16, 0); headG.add(ant);
    const antTip = new THREE.Mesh(new THREE.SphereGeometry(0.015, 8, 6),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(2.0, 1.4, 0.5) }));
    antTip.position.set(0.09, 0.23, 0); headG.add(antTip);
    headG.position.y = 0.62;
    g.add(headG);
    S.attHead = headG;
    // right arm: shoulder pivot, capsule segments, claw ball
    const shoulder = new THREE.Group();
    shoulder.position.set(0.19, 0.44, 0);
    const sBall = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 8), darkMat);
    shoulder.add(sBall);
    const upper = capsule(0.032, 0.14, shellMat);
    upper.position.y = -0.09; shoulder.add(upper);
    const elbow = new THREE.Group(); elbow.position.y = -0.19; shoulder.add(elbow);
    const eBall = new THREE.Mesh(new THREE.SphereGeometry(0.036, 12, 8), darkMat);
    elbow.add(eBall);
    const fore = capsule(0.027, 0.13, shellMat);
    fore.position.y = -0.08; elbow.add(fore);
    const claw = new THREE.Mesh(new THREE.SphereGeometry(0.038, 12, 8),
      new THREE.MeshStandardMaterial({ color: 0x8a94a4, roughness: 0.25, metalness: 0.9, envMapIntensity: 1.3 }));
    claw.position.y = -0.18; shadowed(claw); elbow.add(claw);
    g.add(shoulder);
    S.attArm = { shoulder, elbow, claw };
    const armL = capsule(0.03, 0.2, shellMat);
    armL.position.set(-0.19, 0.32, 0); g.add(armL);
    g.scale.set(1.3, 1.3, 1.3);
    g.position.set(-2.6, 0, 0.6);
    sc.add(g);
    S.attendant = g;
    mountGLBCharacter();
  }

  // If the user-supplied character ships as /static/avatar.glb, it becomes
  // the attendant: the procedural robot hides and the GLB rides the same
  // group choreography. Its only clip is a run, retimed as a walk while the
  // group glides; interactions lean the whole body (no arm clip exists).
  function mountGLBCharacter() {
    if (!THREE.GLTFLoader) return;
    new THREE.GLTFLoader().load('/static/avatar.glb', function (g) {
      const root = g.scene;
      root.traverse(function (o) { if (o.isMesh) { o.castShadow = true; o.frustumCulled = false; } });
      const box = new THREE.Box3().setFromObject(root);
      const size = box.getSize(new THREE.Vector3());
      root.scale.setScalar(1.32 / size.y);
      root.position.y = 0;
      root.rotation.y = Math.PI / 2; // model ships facing -x; faceTo() assumes +z-forward
      S.attendant.add(root);
      S.character = root;
      S.attendant.children.forEach(function (ch) { if (ch !== root) ch.visible = false; });
      S.mixer = new THREE.AnimationMixer(root);
      S.anims = {};
      g.animations.forEach(function (a) {
        const key = a.name.replace(/\.\d+$/, '');
        if (!S.anims[key]) S.anims[key] = S.mixer.clipAction(a);
      });
      setAnim('Idle', 0);
      // the posed bbox is bigger than the load-time bbox: rescale from the actual Idle pose
      S.mixer.update(0.05);
      root.updateMatrixWorld(true);
      const pb = new THREE.Box3().setFromObject(root);
      const ph = pb.getSize(new THREE.Vector3()).y;
      if (ph > 0.01) root.scale.multiplyScalar(1.32 / ph);
      root.position.y = -new THREE.Box3().setFromObject(root).min.y * 0; // feet already at 0 via attendant group
    }, undefined, function () { /* keep the procedural attendant */ });
  }
  function setAnim(name, fade) {
    if (!S.anims || !S.anims[name]) return;
    const next = S.anims[name];
    if (S.curAnim === next) return;
    next.reset(); next.play();
    if (S.curAnim) next.crossFadeFrom(S.curAnim, fade == null ? 0.25 : fade, false);
    S.curAnim = next;
  }

  function faceTo(x, z) {
    const p = S.attendant.position;
    S.attendant.rotation.y = Math.atan2(x - p.x, z - p.z);
  }
  function glideTo(x, z, durMs) {
    const from = S.attendant.position.clone();
    faceTo(x, z);
    if (S.character) setAnim('Walk'); else if (S.runAction) S.runAction.paused = false;
    return tween(durMs || 900, easeInOut, function (t) {
      S.attendant.position.x = from.x + (x - from.x) * t;
      S.attendant.position.z = from.z + (z - from.z) * t;
      S.attendant.position.y = S.character ? 0 : Math.abs(Math.sin(t * Math.PI * 6)) * 0.03; // hover bob (robot only)
      if (S.attBody) S.attBody.rotation.x = Math.sin(t * Math.PI) * 0.09; // lean into the move
    }).then(function () { if (S.character) setAnim('Idle'); else if (S.runAction) S.runAction.paused = true; });
  }
  function raiseArm(toShoulder, toElbow, durMs) {
    const a = S.attArm;
    snd.servo();
    if (S.character) {
      // the supplied rig has no arm gesture clip: Point reads as the interaction tell
      if (Math.abs(toShoulder) > 0.1) setAnim('Point');
      return tween(durMs || 420, easeInOut, function () {}).then(function () { setAnim('Idle'); });
    }
    const s0 = a.shoulder.rotation.z, e0 = a.elbow.rotation.z;
    return tween(durMs || 420, easeInOut, function (t) {
      a.shoulder.rotation.z = s0 + (toShoulder - s0) * t;
      a.elbow.rotation.z = e0 + (toElbow - e0) * t;
    });
  }

  /* ================= coin + sheets ================= */
  function buildProps(sc) {
    const coinG = new THREE.Group();
    const coinFace = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.013, 24),
      new THREE.MeshStandardMaterial({ color: 0xd9a93f, roughness: 0.22, metalness: 0.95,
        emissive: 0x3a2a08, envMapIntensity: 1.4 }));
    coinG.add(coinFace);
    const coinRim = new THREE.Mesh(new THREE.TorusGeometry(0.048, 0.006, 8, 24),
      new THREE.MeshStandardMaterial({ color: 0xb78c2e, roughness: 0.35, metalness: 0.9, envMapIntensity: 1.2 }));
    coinRim.rotation.x = Math.PI / 2; coinG.add(coinRim);
    coinG.visible = false; sc.add(coinG);
    S.coin = coinG;
    const sheetTex = (function () {
      const c = document.createElement("canvas"); c.width = 256; c.height = 340;
      const x = c.getContext("2d");
      x.fillStyle = "#f4efe3"; x.fillRect(0, 0, 256, 340);
      x.fillStyle = "#22201a"; x.fillRect(20, 22, 150, 10);
      x.fillStyle = "#6b6350";
      for (let y = 52; y < 310; y += 16) x.fillRect(20, y, 200 - (y % 48), 5);
      return new THREE.CanvasTexture(c);
    })();
    for (let i = 0; i < 3; i++) {
      const sg = new THREE.PlaneGeometry(0.34, 0.45, 8, 8);
      const sp = sg.attributes.position;
      for (let vi = 0; vi < sp.count; vi++) { // slight paper curl
        const px = sp.getX(vi);
        sp.setZ(vi, 0.028 * Math.pow(px / 0.17, 2));
      }
      sg.computeVertexNormals();
      const sh = new THREE.Mesh(sg,
        new THREE.MeshStandardMaterial({ map: sheetTex, side: THREE.DoubleSide, roughness: 0.9 }));
      shadowed(sh);
      sh.visible = false; sc.add(sh); S.sheets.push(sh);
    }
  }

  /* ================= camera ================= */
  // ``dst`` is a pose {pos, tgt} or a function returning one. A function is asked again on every frame, so a view that is on its way back to the wide shot lands on the
  // wide shot of the screen as it is when the move ends: rotating a phone half-way through the move no longer leaves the camera on the old orientation's pose.
  function camTo(dst, durMs) {
    const c = S.camera;
    const p0 = c.position.clone();
    const t0 = S.camTarget ? S.camTarget.clone() : new THREE.Vector3(0, 1.5, 0);
    const p1 = new THREE.Vector3(), t1 = new THREE.Vector3();
    return tween(durMs, easeInOut, function (t) {
      const d = typeof dst === "function" ? dst() : dst;
      p1.set(d.pos[0], d.pos[1], d.pos[2]); t1.set(d.tgt[0], d.tgt[1], d.tgt[2]);
      c.position.lerpVectors(p0, p1, t);
      S.camTarget.lerpVectors(t0, t1, t);
    });
  }
  // where the camera was when a focus view opened, and whether that was the wide shot (then the way back is to the CURRENT wide shot, which a rotation may have changed)
  function snapPrev() {
    const here = S.camera.position;
    return { pos: here.clone(), tgt: S.camTarget.clone(), wide: here.distanceTo(new THREE.Vector3(S.camWide.pos[0], S.camWide.pos[1], S.camWide.pos[2])) < 0.3 };
  }
  function exitPose(prev) {
    return function () { return prev.wide ? S.camWide : { pos: prev.pos.toArray(), tgt: prev.tgt.toArray() }; };
  }

  /* ================= sequences (the choreography the user asked for) ================= */
  // 1. power-on: the attendant crosses the floor and flips the cabinet's lever
  async function powerOn() {
    if (S.powered) return;
    S.powered = true;
    S.attChest.material.color.setRGB(0.35, 1.5, 1.9);
    await glideTo(1.35, 1.1 + CAB_Z, 1100);
    faceTo(1.0, 0.1 + CAB_Z);
    await raiseArm(-1.9, -0.4, 420);
    snd.clunk();
    const l0 = S.lever.rotation.z;
    await tween(320, easeInOut, function (t) { S.lever.rotation.z = l0 + (-0.7 - l0) * t; });
    snd.hum();
    if (S.cabGLB) {
      tween(700, easeInOut, function (tt) {
        S.cabGLB.traverse(function (o) {
          if (o.isMesh && o.material && o.material.emissiveMap) o.material.emissiveIntensity = 0.05 + tt * 1.75;
        });
      });
    }
    S.marquee.material = glowMat(textTexture("TO;DOH", { fg: "#ffb54d", w: 640, h: 128, size: 76 }), 1.7);
    S.marqueeLight.intensity = 1.8;
    S.screenLight.intensity = 1.2;
    scr.crt = "INSERT COIN"; scr.dirty = true;
    await raiseArm(0, 0, 350);
    await glideTo(1.35, 0.0, 1000); // park nearer the cabinet, out of the Hall zone (user)
    faceTo(0, 0);
  }

  // 2. coin: attendant carries it to the door, the coin arcs into the slot
  async function coinInsert() {
    await glideTo(0.85, 1.35 + CAB_Z, 900);
    faceTo(0.22, 0.4 + CAB_Z);
    await raiseArm(-2.1, -0.5, 420);
    const clawPos = new THREE.Vector3();
    S.attArm.claw.getWorldPosition(clawPos);
    S.coin.visible = true;
    S.coin.position.copy(clawPos);
    const end = S.coinSlotPos;
    snd.clink();
    await tween(650, easeOut, function (t) {
      const mx = clawPos.x + (end.x - clawPos.x) * t;
      const my = clawPos.y + (end.y - clawPos.y) * t + Math.sin(t * Math.PI) * 0.28;
      const mz = clawPos.z + (end.z - clawPos.z) * t;
      S.coin.position.set(mx, my, mz);
      S.coin.rotation.x += 0.3; S.coin.rotation.z += 0.2;
      const s = 1 - t * 0.45; S.coin.scale.set(s, s, s);
    });
    S.coin.visible = false; S.coin.scale.set(1, 1, 1);
    scr.crt = "RUNNING"; scr.dirty = true;
    await raiseArm(0, 0, 350);
    await glideTo(1.35, 0.0, 800); // same cabinet-side park spot
  }

  // 3. one stage of the slowed replay: the car drives to this module's stop,
  //    waits while its recorded work time passes, then the barrier lifts
  async function stageRun(name, showMs, endState) {
    const idx = STAGE_ORDER.indexOf(name);
    const node = scr.nodes[name];
    if (!node) return;
    if (!TRK.ok) { // road assets unavailable: state only, no theatre
      node.state = "active";
      await wait(showMs * 0.8);
      node.state = endState === "failed" ? "failed" : "done";
      if (endState === "failed") { scr.crt = "JAMMED"; scr.dirty = true; }
      return;
    }
    const stop = TRK.stops[idx];
    const driveMs = Math.max(320, Math.min(1700, showMs * 0.5));
    const gateMs = 320;
    node.state = "active";
    stopState(idx, "active");
    await trackDriveTo(stop.x, driveMs);
    await wait(Math.max(60, showMs - driveMs - gateMs)); // parked at the stop: the real work
    if (endState === "failed") {
      node.state = "failed";
      stopState(idx, "failed");
      scr.crt = "JAMMED"; scr.dirty = true;
      return;
    }
    node.state = "done";
    stopState(idx, "done");
    snd.servo();
    gateSet(idx, true); // barrier lifts: this module cleared the car
    await wait(gateMs);
  }

  // instant state set from a live pulse snapshot (no theatre, mid-run):
  // stops take the real states and the car parks at the furthest stage
  // the run has actually reached
  function livePulse(stages) {
    for (const st of stages) {
      const node = scr.nodes[st.name];
      if (!node) continue;
      if (node.state === "done" || node.state === "failed") continue;
      node.state = st.state === "waiting" ? "waiting" : st.state;
      node.ms = st.ms;
      const idx = STAGE_ORDER.indexOf(st.name);
      if (idx >= 0) stopState(idx, node.state);
    }
    if (!TRK.ok) return;
    let furthest = -1;
    STAGE_ORDER.forEach(function (n, i) { if (scr.nodes[n].state !== "waiting") furthest = i; });
    if (furthest >= 0) {
      TRK.targetX = TRK.stops[furthest].x;
      for (let i = 0; i < furthest; i++) {
        if (scr.nodes[STAGE_ORDER[i]].state === "done") gateSet(i, true, true);
      }
    }
  }
  function resetWorkflow() {
    STAGE_ORDER.forEach(function (n) { scr.nodes[n] = { state: "waiting", ms: null }; });
    if (!TRK.ok) return;
    TRK.stops.forEach(function (st, i) {
      stopState(i, "waiting");
      if (st.gate) { st.gate.open = 0; st.gate.openT = 0;
        if (st.gate.pole) st.gate.pole.quaternion.copy(st.gate.baseQ); }
    });
    TRK.drive = null;
    TRK.carX = TRK.prevX = TRK.targetX = TRK.camX = TRK.startX;
    if (TRK.car) TRK.car.position.x = TRK.startX;
  }

  // 4. completion: camera pulls back, cabinet shudders hard, sheets drop,
  //    the attendant reaches for them and the top sheet flies to the glass
  async function finishRun(ok) {
    scr.crt = ok ? "PLAN READY" : "JAMMED"; scr.dirty = true;
    await camTo(S.camWide, 1300);
    snd.cogs(1200);
    const cab = S.cabGLB || S.cabinet;
    const baseP = cab.position.clone(), baseR = cab.rotation.clone();
    await tween(1200, function (t) { return t; }, function (t) {
      const amp = (1 - t) * (ok ? 0.06 : 0.025);
      cab.position.x = baseP.x + (Math.random() - 0.5) * amp;
      cab.position.z = baseP.z + (Math.random() - 0.5) * amp;
      cab.rotation.z = baseR.z + (Math.random() - 0.5) * amp * 0.25;
      cab.rotation.x = baseR.x + (Math.random() - 0.5) * amp * 0.12;
      S.camImpulse = (1 - t) * amp * 0.7;
    });
    cab.position.copy(baseP); cab.rotation.copy(baseR);
    S.camImpulse = 0;
    snd.flutter();
    const nSheets = ok ? S.sheets.length : 1;
    for (let i = 0; i < nSheets; i++) {
      const sh = S.sheets[i];
      sh.visible = true;
      sh.position.set(S.paperSlotPos.x, S.paperSlotPos.y, S.paperSlotPos.z + 0.02);
      sh.rotation.set(-0.4, 0, (i - 1) * 0.12);
      const targetY = 0.29 + i * 0.012;
      await tween(420, easeOut, function (t) {
        sh.position.y = S.paperSlotPos.y + (targetY - S.paperSlotPos.y) * t;
        sh.rotation.x = -0.4 + (-Math.PI / 2 + 0.4) * t;
        sh.rotation.z = (i - 1) * 0.12 + Math.sin(t * 9) * 0.08 * (1 - t);
      });
    }
    await glideTo(0.55, 1.15 + CAB_Z, 900);
    faceTo(-0.25, 0.45 + CAB_Z);
    await raiseArm(-1.7, -0.6, 500);
    snd.flutter();
    const sheet = S.sheets[ok ? S.sheets.length - 1 : 0];
    S.flySheet = sheet;
    const cam = S.camera;
    const start = sheet.position.clone();
    const endP = cam.position.clone()
      .add(new THREE.Vector3(0, -0.02, -0.62).applyQuaternion(cam.quaternion));
    await tween(950, easeOut, function (t) {
      sheet.position.lerpVectors(start, endP, t);
      sheet.position.y += Math.sin(t * Math.PI) * 0.35;
      sheet.rotation.x = -Math.PI / 2 + (Math.PI / 2) * t + Math.sin(t * 12) * 0.12 * (1 - t);
      sheet.rotation.z = Math.sin(t * 8) * 0.2 * (1 - t);
      sheet.lookAt(cam.position);
    });
    sheet.rotation.set(0, 0, 0);
    sheet.lookAt(cam.position);
    snd.press();
    await wait(180);
    raiseArm(0, 0, 300);
    glideTo(-2.6, 0.6, 1000); // back to its parking spot, fire and forget
  }

  function hideSheets() {
    S.sheets.forEach(function (sh) { sh.visible = false; });
    S.flySheet = null;
  }

  /* ================= init + render loop ================= */
  function fitZoom() {
    if (!S.screenMesh || !S.camera) return;
    // Fit the REAL screen bounds by projecting its corners: the tilted screen's
    // near edge is much closer to the zoom camera than its far edge, so a
    // center-distance fit under-sizes the near half (the stop strip slipped
    // behind the chat bar that way). Iterate: grow distance until every
    // corner clears the HUD/composer bands, then recenter inside the band.
    S.scene.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(S.screenMesh);
    const c = box.getCenter(new THREE.Vector3());
    // fit the ACTUAL canvas quad (the mesh's UV corners in world space), not
    // the axis-aligned Box3: the box overhangs the tilted, curved surface and
    // the slack it leaves is exactly where the marquee bled into frame
    const corners = [];
    const posA = S.screenMesh.geometry.attributes.position, uvA = S.screenMesh.geometry.attributes.uv;
    if (uvA && posA) {
      S.screenMesh.updateWorldMatrix(true, false);
      [[0, 0], [1, 0], [0, 1], [1, 1]].forEach(function (t2) {
        let bi = 0, bd = 1e9;
        for (let i = 0; i < uvA.count; i++) {
          const du = uvA.getX(i) - t2[0], dv = uvA.getY(i) - t2[1], dd = du * du + dv * dv;
          if (dd < bd) { bd = dd; bi = i; }
        }
        corners.push(new THREE.Vector3().fromBufferAttribute(posA, bi).applyMatrix4(S.screenMesh.matrixWorld));
      });
    } else {
      [box.min.x, box.max.x].forEach(function (x2) {
        [box.min.y, box.max.y].forEach(function (y2) {
          [box.min.z, box.max.z].forEach(function (z2) { corners.push(new THREE.Vector3(x2, y2, z2)); });
        });
      });
    }
    const dir = (S.screenN ? S.screenN.clone() : new THREE.Vector3(0, 0.157, 1)).normalize();
    const upVec = new THREE.Vector3(0, dir.z, -dir.y); // level camera up, dir lives in the y-z plane
    const W = window.innerWidth, H = window.innerHeight;
    // occlusion bands measured from the live DOM: the whole bottom chat panel
    // (header row sits above the composer) plus the replay banner up top,
    // which is always reserved so live and replay share one framing
    const chat = document.getElementById('chat');
    const chatTop = chat ? chat.getBoundingClientRect().top : H;
    const botPx = Math.max(0, H - chatTop) + 8;
    const topPx = 56; // replay banner pill + margin
    const padXpx = Math.round(W * 0.025);
    const xLim = 1 - 2 * padXpx / W;
    const yTop = 1 - 2 * topPx / H, yBot = -1 + 2 * botPx / H;
    const availMid = (yTop + yBot) / 2, availHalf = Math.max(0.05, (yTop - yBot) / 2);
    const cam = S.camera;
    const t = Math.tan(THREE.MathUtils.degToRad(cam.fov / 2));
    let d = Math.max(0.45, (box.max.x - box.min.x) / (2 * t * cam.aspect * xLim));
    let lift = 0;
    const keepPos = cam.position.clone(), keepTgt = S.camTarget.clone();
    const v = new THREE.Vector3();
    for (let i = 0; i < 10; i++) {
      cam.position.set(c.x + dir.x * d - upVec.x * lift, c.y + dir.y * d - upVec.y * lift, c.z + dir.z * d - upVec.z * lift + 0.05);
      cam.lookAt(c.x - upVec.x * lift, c.y - upVec.y * lift, c.z - upVec.z * lift);
      cam.updateMatrixWorld(true);
      let maxX = 0, maxY = -2, minY = 2;
      corners.forEach(function (p) {
        v.copy(p).project(cam);
        maxX = Math.max(maxX, Math.abs(v.x));
        maxY = Math.max(maxY, v.y); minY = Math.min(minY, v.y);
      });
      const midY = (maxY + minY) / 2, halfR = Math.max(1e-4, (maxY - minY) / 2);
      // s is the exact scale the CURRENT framing is off by: >1 too big, <1 too
      // small. Move d both ways (perspective makes this nonlinear, so iterate)
      // and re-center every pass - growing only would overshoot and stick.
      const s = Math.max(maxX / xLim, halfR / availHalf);
      const deltaNdc = midY - availMid; // >0: content sits too high in the band
      lift -= deltaNdc * t * d * 0.9; // rig down pushes content up
      if (Math.abs(s - 1) < 0.02 && Math.abs(deltaNdc) < 0.01) break;
      d = Math.max(0.3, d * s * 1.01);
    }
    cam.position.copy(keepPos); cam.lookAt(keepTgt); cam.updateMatrixWorld(true);
    S.camZoom = {
      pos: [c.x + dir.x * d - upVec.x * lift, c.y + dir.y * d - upVec.y * lift, c.z + dir.z * d - upVec.z * lift + 0.05],
      tgt: [c.x - upVec.x * lift, c.y - upVec.y * lift, c.z - upVec.z * lift]
    };
  }

  function resize() {
    if (!S.ok) return;
    const w = window.innerWidth, h = window.innerHeight, aspect = w / h;
    S.camera.aspect = aspect;
    S.camera.updateProjectionMatrix();
    S.renderer.setSize(w, h, false);
    if (S.composer) S.composer.setSize(w, h);
    // The room is one composition at every screen size: the laptop shot (camera at [0, 2.35, 6.4] looking at [0, 1.72, 0], fov 55) is the reference. On a narrower
    // screen the SAME shot is kept and the camera backs off along the same line of sight, by the ratio of the screens' widths, so the same room - mural, both side
    // walls, van, cabinet, attendant - is in frame. It is smaller, never cropped and never turned.
    const REF = { pos: [0, 2.35, 6.4], tgt: [0, 1.72, 0], aspect: 1.78 };
    // Tall screens (user, 15:40): both side walls, the mural, the cabinet row and the van must stay in frame, so the camera still backs off by the ratio of the
    // screens' widths. What a tall screen has spare is height, and a flat view of the same shot would leave half of it as bare floor and bare wall. So the camera also
    // RISES as the screen gets taller and looks down at the room, like a diorama: the floor with its props and the taller mural fill the height, and the walls
    // slope in from the sides instead of being cropped.
    const tall = Math.max(0, Math.min(1, (1.3 - aspect) / 0.7)); // 0 on a laptop, 1 on a phone held upright
    const fov = 55 + 14 * tall; // a wider lens on a tall screen: the side walls show their inner faces (graffiti, posters) instead of edge-on slivers
    const back = Math.max(1, Math.min(REF.aspect / aspect, 3.2)) * (Math.tan(27.5 * Math.PI / 180) / Math.tan(fov / 2 * Math.PI / 180)); // same width of room at the back wall, nearer camera
    S.camWide.tgt = [REF.tgt[0], REF.tgt[1] + 0.9 * tall, REF.tgt[2] - 0.6 * tall];
    S.camWide.pos = [REF.pos[0], REF.tgt[1] + (REF.pos[1] - REF.tgt[1]) * back + 3.4 * tall, REF.tgt[2] + (REF.pos[2] - REF.tgt[2]) * back];
    S.camera.fov = fov;
    S.camera.updateProjectionMatrix();
    if (S.scene && S.scene.fog) S.scene.fog.density = 0.03 / back; // the same amount of air between the viewer and the wall at every screen size
    const idleNow = !S.zoomed && !(S.wallb && S.wallb.focused) && !(S.holo && S.holo.focused) && !(S.dossier && S.dossier.focused);
    if (idleNow) {
      S.camera.position.set(S.camWide.pos[0], S.camWide.pos[1], S.camWide.pos[2]);
      S.camTarget.set(S.camWide.tgt[0], S.camWide.tgt[1], S.camWide.tgt[2]);
    }
    fitZoom();
  }

  /* film grain + vignette overlay (subtle; generated at runtime, no assets) */
  function filmOverlay() {
    const el = document.getElementById("film");
    if (!el) return;
    const c = document.createElement("canvas");
    c.width = 128; c.height = 128;
    const x = c.getContext("2d");
    const img = x.createImageData(128, 128);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = 110 + Math.random() * 60;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
      img.data[i + 3] = 14;
    }
    x.putImageData(img, 0, 0);
    el.style.backgroundImage = "url(" + c.toDataURL() + ")";
  }

  function init() {
    S.reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const canvas = document.getElementById("gl");
    try {
      S.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" }); // a hint to the browser and OS to use the stronger GPU, not a guarantee
    } catch (e) {
      try {
        S.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "high-performance" });
      } catch (e2) {
        console.error('webgl:', e2 && e2.message || e2, '| first:', e && e.message || e);
        return false;
      }
    }
    S.lowFx = new URLSearchParams(location.search).has("lowfx");
    S.tier = TIER;
    S.smallGpu = TIER === "low";
    // sharpness is paid for in pixels: a phone with a 2.8x screen would draw 8 pixels for every one on a 1x screen. Small devices draw at most 1.25x, laptops at most 2x.
    S.renderer.setPixelRatio(S.lowFx ? 1 : Math.min(window.devicePixelRatio || 1, S.smallGpu ? 1.25 : 2));
    S.renderer.shadowMap.enabled = true;
    S.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    S.scene = new THREE.Scene();
    S.camera = new THREE.PerspectiveCamera(55, 1, 0.05, 60);
    S.camTarget = new THREE.Vector3(S.camWide.tgt[0], S.camWide.tgt[1], S.camWide.tgt[2]);
    S.camera.position.set(S.camWide.pos[0], S.camWide.pos[1], S.camWide.pos[2]);
    S.clock = new THREE.Clock();
    try {
      // procedural environment: metals/glass reflect the room they stand in
      const envTex = environmentTexture();
      envTex.mapping = THREE.EquirectangularReflectionMapping;
      const pmrem = new THREE.PMREMGenerator(S.renderer);
      pmrem.compileEquirectangularShader();
      S.scene.environment = pmrem.fromEquirectangular(envTex).texture;
      buildRoom(S.scene);
      mountAlley(S.scene, -4.6, -5.6, 5.6); // back wall / left wall / right wall planes
      mountHoloTable(S.scene); // holographic Hall of Fame table (hidden until hall_unlocked)
      lazy(5, function () { mountDossier(S.scene); }); // TOP SECRET dossier easter egg by the gumball machine
      wireVendTaps(); // tap a vending machine to look at it
      mountWallBoard(S.scene, -5.6); // the I WAS HERE signature wall (left wall)
      buildCabinet(S.scene);
      buildAttendant(S.scene);
      buildProps(S.scene);
    } catch (e) {
      console.error('scene:', e && e.message || e);
      return false;
    }
    // bloom on the emissive parts; skipped entirely on the light path
    if (!S.lowFx && !S.smallGpu && THREE.EffectComposer && THREE.RenderPass && THREE.UnrealBloomPass) {
      try {
        S.composer = new THREE.EffectComposer(S.renderer);
        S.composer.addPass(new THREE.RenderPass(S.scene, S.camera));
        S.bloom = new THREE.UnrealBloomPass(
          new THREE.Vector2(window.innerWidth / 2, window.innerHeight / 2), window.innerWidth < window.innerHeight ? 0.34 : 0.5, window.innerWidth < window.innerHeight ? 0.45 : 0.55, 0.8);
        S.composer.addPass(S.bloom);
      } catch (e) {
        S.composer = null; S.bloom = null; // bloom is garnish: never a hard failure
      }
    }
    scr.tex.anisotropy = Math.min(4, S.renderer.capabilities.getMaxAnisotropy()); // 4 is plenty for a screen seen at this angle; 16 costs bandwidth for nothing
    S.ok = true;
    fitZoom();
    resize();
    window.addEventListener("resize", resize);
    filmOverlay();

    requestAnimationFrame(function () { requestAnimationFrame(lazyStart); }); // two frames drawn: now the rest of the room may arrive
    let frameNo = 0;
    const frameTimes = [];
    let lastNow = 0;
    const loop = function () {
      requestAnimationFrame(loop);
      frameNo++;
      const now = performance.now();
      // auto-degrade ladder for slow devices. Sustained slow frames first drop the render density to 75% (bloom stays); if the next 90 frames are still slow, take the
      // light path (pixel ratio 1, no bloom, throttled CRT redraw, every second frame skipped). 34 ms (30 fps) is a PROPOSED budget, not a measurement from a device;
      // measuring stops as soon as a device proves fast.
      if (!S.lowFx && !S.fxChecked) {
        if (lastNow) frameTimes.push(now - lastNow);
        lastNow = now;
        if (frameTimes.length >= 90) {
          const tail = frameTimes.slice(15);
          const avg = tail.reduce(function (a, b) { return a + b; }, 0) / tail.length;
          frameTimes.length = 0; lastNow = 0;
          if (avg <= 34) S.fxChecked = true;
          else if (!S.ladder) {
            S.ladder = 1;
            const pr = S.renderer.getPixelRatio() * 0.75;
            S.renderer.setPixelRatio(pr);
            if (S.composer && S.composer.setPixelRatio) S.composer.setPixelRatio(pr);
            S.renderer.setSize(window.innerWidth, window.innerHeight, false);
            if (S.composer) S.composer.setSize(window.innerWidth, window.innerHeight);
          } else {
            S.fxChecked = true; S.ladder = 2;
            S.lowFx = true;
            S.composer = null; S.bloom = null;
            S.renderer.setPixelRatio(1);
            S.renderer.setSize(window.innerWidth, window.innerHeight, false);
            if (S.dust) S.dust.visible = false;
          }
        }
      }
      stepTweens(now);
      const dt = Math.min(0.05, S.clock.getDelta()); // a tab that comes back from hiding must not make every animation jump
      const t = S.clock.elapsedTime * 1000;
      if (S.mixer) S.mixer.update(dt || 0.016);
      trackUpdate(dt || 0.016, t);
      // standby screen is static: redraw only when something changed
      if (!S.powered && !scr.dirty) { /* skip CRT redraw */ }
      else if (!S.lowFx || frameNo % 3 === 0) { drawScreen(t); scr.dirty = false; }
      if (S.lowFx && frameNo % 2 === 1) return;
      // cold tube-light flicker
      if (S.tubeLight) {
        const f = Math.random() < 0.03 ? 0.35 : 1;
        S.tubeLight.intensity = 1.5 * f * (0.92 + 0.08 * Math.sin(t / 90));
      }
      if (S.screenLight && S.powered) {
        S.screenLight.intensity = 1.2 + 0.08 * Math.sin(t / 210);
      }
      if (S.slotGlow) {
        S.slotGlow.material.opacity = (S.powered && scr.crt === "INSERT COIN")
          ? 0.35 + 0.3 * Math.sin(t / 300) : 0;
      }
      if (S.attVisor) S.attVisor.material.color.setRGB(0.4, 1.6, 2.0);
      if (S.attVisor && Math.sin(t / 1700) > 0.96) S.attVisor.material.color.setRGB(0.12, 0.5, 0.7);
      if (S.attHead) S.attHead.rotation.y = Math.sin(t / 2400) * 0.3; // idle scan
      if (S.dust && S.dust.visible) {
        S.dust.rotation.y = t * 0.00004;
        S.dust.position.y = Math.sin(t * 0.00005) * 0.12;
      }
      // the wall secret reveals by viewing angle: faint square-on, caught at
      // a grazing angle like raking light; fully readable only when focused
      if (S.wallb && S.wallb.mesh) {
        const wbm = S.wallb.mesh;
        if (S.wallb.focused) { if (wbm.material.opacity !== 1) wbm.material.opacity = 1; }
        else {
          const wdx = S.camera.position.x - wbm.position.x, wdz = S.camera.position.z - wbm.position.z;
          const squareOn = Math.abs(wdx) / Math.max(0.001, Math.sqrt(wdx * wdx + wdz * wdz));
          wbm.material.opacity = 0.25 + 0.75 * (1 - squareOn);
        }
      }
      // hand-held sway + impact impulse, without fighting the camera tweens
      if (!S.reduced) {
        const amp = (S.zoomed ? 0.006 : 0.018);
        const sway = new THREE.Vector3(
          Math.sin(t * 0.0005) * amp + (Math.random() - 0.5) * S.camImpulse,
          Math.cos(t * 0.00037) * amp * 0.7 + (Math.random() - 0.5) * S.camImpulse,
          0);
        S.camera.position.sub(S.swayPrev).add(sway);
        S.swayPrev.copy(sway);
      }
      S.camera.lookAt(S.camTarget);
      if (S.composer) S.composer.render();
      else S.renderer.render(S.scene, S.camera);
    };
    loop();
    return true;
  }

  return {
    init: init, resize: resize, userGesture: userGesture,
    powerOn: powerOn, coinInsert: coinInsert, stageRun: stageRun,
    livePulse: livePulse, resetWorkflow: resetWorkflow, finishRun: finishRun, hideSheets: hideSheets,
    setCRT: function (t) { scr.crt = t; scr.dirty = true; },
    setStageMs: function (name, ms) { const n = scr.nodes[name]; if (n) n.ms = ms; },
    zoomIn: function () { S.zoomed = true; fitZoom(); return camTo(S.camZoom, 1400); },
    holoSetUnlocked: function (v) { holoSetVisible(!!v); },
    holoSetHall: function (entries) { if (!S.holo) return; S.holo.entries = Array.isArray(entries) ? entries : []; drawHoloList(); },
    holoSetPeriod: function (period) { if (!S.holo) return; S.holo.period = period || null; drawHoloList(); },
    holoFocus: function () { return holoFocus(); },
    holoUnfocus: function () { holoUnfocus(); },
    holoFocused: function () { return !!(S.holo && S.holo.focused); },
    onHoloFocus: function (cb) { S.holoFocusCb = cb; },
    vendFocus: function (i) { return vendFocus(i); },
    vendUnfocus: function () { vendUnfocus(); },
    vendFocused: function () { return S.vendFocused != null; },
    vendCount: function () { return (S.vend || []).length; },
    vendLabels: function () { return (S.vend || []).map(function (v) { return v.label; }); },
    onVendFocus: function (cb) { S.vendFocusCb = cb; },
    dossierFocus: function () { return dossierFocus(); },
    dossierUnfocus: function () { dossierUnfocus(); },
    dossierFocused: function () { return !!(S.dossier && S.dossier.focused); },
    dossierOpen: function () { return !!(S.dossier && S.dossier.open); },
    dossierReady: function () { return !!(S.dossier && S.dossier.grp); },
    cabReady: function () { return !!S.cabGLB; },
    screenDebug: function () {
      const m = S.screenMesh;
      return { cabGLB: !!S.cabGLB, screenMesh: !!m, screenIsCRT: !!(m && m.material && m.material.map === scr.tex) };
    },
    onDossierFocus: function (cb) { S.dossierFocusCb = cb; },
    wallSetData: function (d) { if (!S.wallb) return; S.wallb.data = d; wallDraw(); },
    lastSceneTap: function () { return S.lastSceneTap || 0; },
    wallFocus: function () { return wallFocus(); },
    wallUnfocus: function () { wallUnfocus(); },
    wallFocused: function () { return !!(S.wallb && S.wallb.focused); },
    onWallFocus: function (cb) { S.wallFocusCb = cb; },
    onWallTap: function (cb) { S.wallTapCb = cb; },
    onWallSolve: function (cb) { S.wallSolveCb = cb; },
    zoomOut: function () { S.zoomed = false; return camTo(S.camWide, 1100); },
    setMuted: function (m) {
      S.muted = m;
      localStorage.setItem("em-arcade-muted", m ? "1" : "0");
      if (m && S.audio) S.audio.suspend(); else if (!m && S.audio) S.audio.resume();
    },
    isMuted: function () { return S.muted; },
  };
})();
