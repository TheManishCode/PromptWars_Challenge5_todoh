/* to;doh-the event planner Arcade — the arcade is a live view of the real pipeline.
 *
 * Honesty rules this file keeps:
 * - a module lights only because /api/arcade/pulse says the real stage ran;
 *   the page never invents progress, and the coin drops only after the user
 *   presses BUILD MY PLAN (the same confirm the plain UI requires);
 * - the dossier shows what the API returned: validation warnings, unverified
 *   items, symbolic-check status and violations stay visible. A failing run
 *   ejects a jam slip, never a fake plan;
 * - the run replay is the recorded stage timings of THIS run, slowed so the
 *   eye can follow; the on-screen CRT and the banner both say so.
 * The 3D scene (cabinet, attendant, liquid loop, paper flight) lives in
 * arcade3d.js; this file owns conversation, API and result content.
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = {
  csrf: "",
  messages: [],
  draft: null,
  running: false,
  pollTimer: null,
  chatWasOpen: true,
  lastPayload: null,
};
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------------------------------------------------------------- API */
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.csrf) headers["X-EventMate-CSRF"] = state.csrf;
  const body = opts.body != null && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body;
  const res = await fetch(path, { ...opts, body, headers });
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON body */ }
  if (res.status === 404 && data && data.code === "not_found" && opts.method && opts.method !== "GET" && !path.startsWith("/api/wall/")) await sessionLost(path);
  return { ok: res.ok, status: res.status, data };
}

/* In shared mode the workspace cookie is HttpOnly, so the page cannot know it was lost: the only sign is the generic 404 a write gets. Ask the server once who
   we are now. A different workspace id means the old session is gone (cookies cleared or expired) and everything made in it is out of reach; say so, take the
   new CSRF value, and never resend the write. */
let sessionChecking = false;
async function sessionLost(path) {
  if (sessionChecking || !state.wsId) return; // local mode (no workspace id) and re-entry: nothing to check
  sessionChecking = true;
  try {
    const r = await fetch("/api/session", { headers: { "Accept": "application/json" } });
    const d = r.ok ? await r.json() : null;
    if (d && d.workspace && d.workspace.id !== state.wsId) {
      state.wsId = d.workspace.id; state.csrf = d.csrf; state.messages = []; state.draft = null;
      bubble("err", "Your session was lost (the browser's cookie was cleared or expired), so your earlier plans are out of reach and this was not saved. A fresh session has started: describe your event again.");
    }
  } catch (e) { /* offline: the caller's own error message stands */ }
  sessionChecking = false;
}

/* ---------------------------------------------------------------- chat */
function bubble(kind, text, notes) {
  const div = document.createElement("div");
  div.className = "bubble " + kind;
  div.textContent = text;
  if (notes && notes.length) {
    const n = document.createElement("span");
    n.className = "notes";
    n.textContent = notes.join("\n");
    div.appendChild(n);
  }
  $("#log").appendChild(div);
  $("#log").scrollTop = $("#log").scrollHeight;
  return div;
}

function fmtINR(n) {
  if (typeof n !== "number") return "—";
  let s = String(Math.round(n)), out = "", first = true;
  while (s.length > 0) {
    const take = first ? 3 : 2;
    const chunk = s.slice(-take);
    out = chunk + (out ? "," + out : "");
    s = s.slice(0, -take);
    first = false;
  }
  return "₹" + out;
}

function fieldLabel(f) {
  return {
    event_type: "event", community_subtype: "community type", guest_count: "guests",
    city: "city", event_date: "date", budget_total: "budget", dietary: "dietary",
    venue_setting: "venue setting", services: "services", reminders: "reminders",
  }[f] || f;
}

function draftValue(draft, f) {
  const v = draft[f];
  if (v === null || v === undefined || v === "") return null;
  if (f === "budget_total") return fmtINR(v);
  if (Array.isArray(v)) return v.length ? v.join(", ") : null;
  return String(v);
}

function renderReview() {
  const d = state.draft;
  if (!d) return;
  const dl = $("#review-fields");
  dl.textContent = "";
  const fields = ["event_type", "community_subtype", "guest_count", "city", "event_date",
    "budget_total", "dietary", "venue_setting", "services", "reminders"];
  for (const f of fields) {
    const val = draftValue(d, f);
    if (val === null) continue;
    const dt = document.createElement("dt");
    dt.textContent = fieldLabel(f);
    const dd = document.createElement("dd");
    dd.textContent = val;
    dl.appendChild(dt); dl.appendChild(dd);
  }
  $("#review").hidden = false;
}

function renderTurn(turn) {
  state.draft = turn.draft;
  const notes = [];
  if (turn.missing && turn.missing.length) notes.push("still missing: " + turn.missing.map(fieldLabel).join(", "));
  if (turn.flags && turn.flags.length) notes.push("please double-check: " + turn.flags.join(", "));
  if (turn.notes && turn.notes.length) notes.push(...turn.notes);

  if (turn.choices && turn.choices.length) {
    for (const ch of turn.choices) {
      const b = bubble("bot", ch.prompt, notes);
      const chips = document.createElement("div");
      chips.className = "chips";
      for (const opt of ch.options) {
        const c = document.createElement("button");
        c.className = "chip";
        c.type = "button";
        c.textContent = opt;
        c.addEventListener("click", () => { sendUser(opt); });
        chips.appendChild(c);
      }
      b.appendChild(chips);
    }
    return;
  }
  if (turn.ready) {
    bubble("bot", "That is everything I need. Check the brief below, then drop the coin.", notes);
    renderReview();
    return;
  }
  bubble("bot", turn.question || "Tell me more about the event.", notes);
}

async function sendUser(text) {
  if (state.running) return;
  state.messages.push(text);
  bubble("user", text);
  $("#msg").value = "";
  $("#review").hidden = true;
  const r = await api("/api/intake", {
    method: "POST",
    body: JSON.stringify({ messages: state.messages, draft: state.draft }),
  });
  if (!r.ok || !r.data) {
    bubble("err", (r.data && r.data.error) || "The intake call failed (" + r.status + "). Try again.");
    return;
  }
  renderTurn(r.data);
}

/* ---------------------------------------------------------------- pulse */
function applyPulse(snap) {
  if (!snap || !snap.stages) return;
  Arcade3D.livePulse(snap.stages);
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(async () => {
    try {
      const r = await api("/api/arcade/pulse");
      if (r.ok && r.data) applyPulse(r.data);
    } catch (e) { /* keep polling */ }
  }, 180);
}
function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}

function setCaption(text) { $("#stage-caption").textContent = text || ""; }

/* Replay the recorded timings of this run, slowed so the eye can follow.
   The cabinet screen says REPLAY and the banner spells it out; block by block,
   each finished stage hands a continuous fill of blue liquid to the next. */
async function replayRun(snap) {
  if (!snap || !snap.stages) return;
  const ran = snap.stages.filter((s) => s.state !== "waiting");
  if (!ran.length) return;
  Arcade3D.resetWorkflow();
  Arcade3D.setCRT("REPLAY");
  $("#replay-banner").hidden = false;
  for (const st of ran) {
    const show = Math.max(380, Math.min(1400, (st.ms || 0) * 8));
    await Arcade3D.stageRun(st.name, show, st.state);
  }
  // stamp the recorded timings onto the blocks
  for (const st of ran) Arcade3D.setStageMs(st.name, st.ms);
  $("#replay-banner").hidden = true;
  setCaption("");
}

/* ---------------------------------------------------------------- dossier */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function renderDossier(payload) {
  const plan = payload.plan;
  const inner = $("#paper-inner");
  inner.textContent = "";
  const paper = $("#paper");
  paper.classList.remove("jammed");
  state.lastPayload = payload;
  paper.dataset.dashPage = "0";
  const oldStamp = paper.querySelector(".stamp");
  if (oldStamp) oldStamp.remove();

  inner.appendChild(sheetNav("plan"));
  inner.appendChild(el("h1", null, "PLAN DOSSIER"));
  // same overall semantics as the classic UI: "Verified" only when nothing
  // that matters is still undecided; blocking unknowns demote the stamp.
  const blocking = (plan.validation.unverified || []).filter((u) => u.blocks_verified !== false);
  const symbolicOk = plan.symbolic && plan.symbolic.status === "valid";
  let stampText, stampCls = "stamp";
  if (location.hash === "#sample-preview") { // dev preview: the sheet itself says the data is sample
    stampText = "SAMPLE DATA PREVIEW";
    stampCls = "stamp warn";
  } else if (blocking.length) {
    stampText = "NEEDS VERIFICATION";
    stampCls = "stamp warn";
  } else if (!symbolicOk) {
    stampText = "CHECKED";
    stampCls = "stamp warn";
  } else {
    stampText = "VERIFIED";
  }
  paper.insertBefore(el("div", stampCls, stampText), paper.firstChild);
  if (blocking.length) {
    inner.appendChild(el("div", "ribbon",
      "Needs verification: " + blocking.map((u) => u.label || (u.name ? String(u.name).replace(/_/g, " ") : "this item")).join("; ")));
  }
  if (!symbolicOk) {
    inner.appendChild(el("div", "ribbon",
      "Symbolic (PDDL) check: " + (plan.symbolic ? plan.symbolic.status : "unknown") +
      " - the schedule was NOT symbolically confirmed. " + (plan.symbolic && plan.symbolic.detail ? plan.symbolic.detail : "")));
  }
  const metaBits = [plan.plan_id, "v" + plan.version];
  if (plan.created_at) metaBits.push("created " + fmtTime(plan.created_at));
  if (plan.change_reason) metaBits.push(plan.change_reason);
  inner.appendChild(el("div", "sub", metaBits.join(" · ")));

  const facts = el("div", "factrow");
  const addFact = (k, v) => {
    const s = el("span");
    s.appendChild(el("b", null, k));
    s.appendChild(document.createTextNode(v));
    facts.appendChild(s);
  };
  addFact("EVENT", String(plan.request.event_type).replace(/_/g, " "));
  addFact("CITY", plan.request.city);
  addFact("DATE", plan.request.event_date);
  addFact("GUESTS", plan.guests.planning_headcount);
  addFact("BUDGET", fmtINR(plan.allocation.total));
  inner.appendChild(facts);

  inner.appendChild(el("h2", null, "VENDORS — highest-scoring eligible combination that passes every hard constraint"));
  const vt = el("table");
  vt.appendChild((() => { const tr = el("tr");
    ["category", "vendor", "cost", "status", "also ran"].forEach((h, i) =>
      tr.appendChild(el("th", i === 2 ? "num" : null, h)));
    return tr; })());
  for (const sel of plan.selections) {
    const tr = el("tr");
    tr.appendChild(el("td", null, sel.category));
    const vn = el("td", null, sel.name);
    tr.appendChild(vn);
    tr.appendChild(el("td", "num", fmtINR(sel.cost)));
    tr.appendChild(el("td", null, sel.booking_status));
    const alt = sel.alternatives && sel.alternatives.length
      ? sel.alternatives.length + " alternative(s) on file"
      : (sel.no_alternative_reason || "none");
    tr.appendChild(el("td", null, alt));
    vt.appendChild(tr);
  }
  inner.appendChild(vt);

  inner.appendChild(el("h2", null, "BUDGET — category caps plus the selected-cost estimate, sums to the rupee"));
  const bt = el("table");
  for (const line of plan.allocation.lines) {
    const tr = el("tr");
    tr.appendChild(el("td", null, line.category));
    tr.appendChild(el("td", "num", fmtINR(line.amount)));
    bt.appendChild(tr);
  }
  const ctr = el("tr");
  ctr.appendChild(el("td", null, "contingency"));
  ctr.appendChild(el("td", "num", fmtINR(plan.allocation.contingency)));
  bt.appendChild(ctr);
  const ttr = el("tr");
  ttr.appendChild(el("td", null, "selected vendor cost (estimate - nothing committed)"));
  ttr.appendChild(el("td", "num", fmtINR(plan.total_cost)));
  bt.appendChild(ttr);
  inner.appendChild(bt);

  if (payload.reality) {
    const r = payload.reality;
    const line = el("p", null, "Budget reality check: " + r.verdict + " — " + r.message);
    line.style.fontSize = "11px";
    inner.appendChild(line);
  }

  inner.appendChild(el("h2", null, "SCHEDULE — CP-SAT solved, times are event-local"));
  const st = el("table");
  for (const it of plan.items.slice(0, 12)) {
    const tr = el("tr");
    tr.appendChild(el("td", null, fmtTime(it.start)));
    tr.appendChild(el("td", null, fmtTime(it.end)));
    tr.appendChild(el("td", null, it.name));
    st.appendChild(tr);
  }
  inner.appendChild(st);

  inner.appendChild(el("h2", null, "CHECKS — re-derived from the database, not trusted from the draft"));
  const v = plan.validation;
  const passed = v.checks.filter((c) => c.passed).length;
  inner.appendChild(el("p", null, passed + " of " + v.checks.length + " hard checks passed. Symbolic (PDDL) check: " + plan.symbolic.status + "."));
  if (v.warnings && v.warnings.length) {
    const ul = el("ul");
    v.warnings.forEach((w) => ul.appendChild(el("li", "warn", "warning: " + w)));
    inner.appendChild(ul);
  }
  if (v.unverified && v.unverified.length) {
    const ul = el("ul");
    v.unverified.forEach((u) => {
      let txt = "needs verification: " + (u.label || (u.name ? String(u.name).replace(/_/g, " ") : "this item"));
      if (u.detail) txt += " — " + u.detail;
      if (u.repair) txt += " To fix: " + u.repair;
      ul.appendChild(el("li", "unverified", txt));
    });
    inner.appendChild(ul);
  }

  if (plan.request.assumptions && plan.request.assumptions.length) {
    inner.appendChild(el("h2", null, "ASSUMPTIONS — stated, never hidden"));
    const ul = el("ul");
    plan.request.assumptions.forEach((a) => ul.appendChild(el("li", null, a)));
    inner.appendChild(ul);
  }

  inner.appendChild(el("div", "foot",
    "Vendor records and prices are synthetic seed data (demo), so every cost above is an estimate from the database, not a booking or a quote; " +
    "a vendor's reply only becomes a quote once you record it yourself. This plan books nothing, commits nothing, sends nothing and pays nothing. " +
    "Confirmed bookings stay pinned when you replan."));

  const close = el("button", "paper-close", "BACK TO THE FLOOR");
  close.type = "button";
  close.addEventListener("click", hidePaper);
  inner.appendChild(close);

  showPaper(false);
}


/* Sheet tabs: the classic UI's plan tabs migrated onto the printed sheet.
   Tabs with a real data path render from the stored plan record; tabs whose
   backend routes are not wired into the arcade client yet say so honestly
   instead of staging fake content. */
const PAPER_TABS = [
  ["plan", "PLAN"], ["overview", "OVERVIEW"], ["ideas", "IDEAS"], ["timeline", "TIMELINE"],
  ["vendors", "VENDORS"], ["budget", "BUDGET"], ["community", "COMMUNITY"],
  ["vendorchats", "VENDOR CHATS"], ["changes", "CHANGES"], ["audit", "AUDIT LOG"], ["ask", "ASK"],
];
const PAPER_PENDING = {
  ideas: "ranked ideas and inspiration for this event from the ideas engine, with reasons each fits the brief",
  community: "the community-event checklist: approvals, helpers, money, questions to settle with others",
  vendorchats: "vendor conversations: drafted messages you approve word by word, follow-ups, and replies in the vendor's own words",
  changes: "the diff between this plan version and the one before it",
  audit: "the decision log: every machine decision with its alternatives, confidence and source",
  ask: "plain-word questions about this plan, answered from the stored record",
};
function sheetNav(view) {
  const nav = el("div", "sheet-nav");
  PAPER_TABS.forEach(function (tb) {
    const b = el("button", "sheet-tab" + (tb[0] === view ? " cur" : ""), tb[1]);
    b.type = "button";
    b.dataset.view = tb[0];
    b.setAttribute("aria-pressed", tb[0] === view ? "true" : "false");
    nav.appendChild(b);
  });
  return nav;
}

function renderPaperView(view) {
  const payload = state.lastPayload; if (!payload || !payload.plan) return;
  if (view === "plan") { renderDossier(payload); return; }
  const plan = payload.plan;
  const inner = $("#paper-inner");
  inner.textContent = "";
  inner.appendChild(sheetNav(view));
  if (view === "overview") return paperOverview(inner, plan, payload);
  if (view === "timeline") return paperTimeline(inner, plan);
  if (view === "vendors") return paperVendors(inner, plan);
  if (view === "budget") return paperBudget(inner, plan, payload);
  const asyncViews = { ideas: paperIdeas, community: paperCommunity, vendorchats: paperChats, changes: paperChanges, audit: paperAudit };
  if (asyncViews[view]) {
    const holder = el("div", "pv-async");
    inner.appendChild(holder);
    return asyncViews[view](holder, plan);
  }
  if (view === "ask") return paperAsk(inner, plan);
  return paperPending(inner, view);
}

function paperStat(host, k, v, sub) {
  const d = el("div", "pv-stat");
  d.appendChild(el("small", null, k));
  d.appendChild(el("b", null, v));
  if (sub) d.appendChild(el("span", null, sub));
  host.appendChild(d);
}

function paperOverview(host, plan, payload) {
  host.appendChild(el("h1", null, "OVERVIEW"));
  const stats = el("div", "pv-stats");
  const total = plan.allocation && plan.allocation.total;
  paperStat(stats, "VENDORS TOTAL", fmtINR(plan.total_cost), total ? "of " + fmtINR(total) : "");
  paperStat(stats, "LEFT IN BUDGET", total ? fmtINR(total - plan.total_cost) : "-",
    plan.allocation ? "includes the " + fmtINR(plan.allocation.contingency) + " contingency reserve, still unspent" : "");
  let days = "-";
  try { days = Math.max(0, Math.ceil((new Date(plan.request.event_date) - Date.now()) / 86400000)) + ""; } catch (e) {}
  paperStat(stats, "DAYS TO EVENT", days, (plan.items || []).length + " scheduled tasks");
  paperStat(stats, "GUESTS", String(plan.guests.planning_headcount), "planning headcount");
  host.appendChild(stats);
  const v = plan.validation;
  const passed = v.checks.filter((c) => c.passed).length;
  host.appendChild(el("h2", null, "WHY THIS PLAN IS VALID"));
  host.appendChild(el("p", null, passed + " of " + v.checks.length + " hard checks passed. Symbolic (PDDL) check: " + (plan.symbolic ? plan.symbolic.status : "unknown") + "."));
  const blocking = (v.unverified || []).filter((u) => u.blocks_verified !== false);
  if (blocking.length) host.appendChild(el("div", "ribbon", "Needs verification: " + blocking.map((u) => u.label || (u.name ? String(u.name).replace(/_/g, " ") : "this item")).join("; ")));
  if (payload.reality) host.appendChild(el("p", null, "Budget reality check: " + payload.reality.verdict + " - " + payload.reality.message));
  if (plan.request.assumptions && plan.request.assumptions.length) {
    host.appendChild(el("h2", null, "ASSUMPTIONS"));
    const ul = el("ul");
    plan.request.assumptions.forEach((a) => ul.appendChild(el("li", null, a)));
    host.appendChild(ul);
  }
  paperAdvisories(host, plan);
  paperWeather(host, plan);
}

/* Overview advisories + weather, parity with the classic UI (renderAdvisories/
   renderWeather). Advisory-only: nothing here changes the plan; weather beyond
   the forecast horizon says so instead of estimating. */
function paperAdvisories(host, plan) {
  const box = el("div", null); host.appendChild(box);
  (async function () {
    const r = await api(pvBase(plan) + "/advisories");
    if (!r.ok || !(r.data || []).length) return; // section stays hidden when there are none
    box.appendChild(el("h2", null, "THINGS TO CONFIRM"));
    box.appendChild(el("p", "pv-alt", "Advisory only: questions to put to your vendors or host. They do not change the plan."));
    (r.data || []).forEach(function (a) {
      const card = el("div", "pv-vendor");
      card.appendChild(el("b", null, a.title));
      card.appendChild(el("p", "pv-alt", a.detail));
      card.appendChild(el("p", "pv-alt", a.pack_id + " v" + a.pack_version + " - " + (a.reviewed ? "reviewed" : "unreviewed draft")));
      box.appendChild(card);
    });
  })();
}

function paperWeather(host, plan) {
  const box = el("div", null); host.appendChild(box);
  box.appendChild(el("h2", null, "WEATHER ON THE DAY"));
  const msg = el("p", "pv-alt", "Loading...");
  box.appendChild(msg);
  (async function () {
    const st = await api("/api/weather/status");
    if (st.ok && st.data && st.data.configured === false) { msg.textContent = "Weather is not configured: add OPENWEATHER_API_KEY to the .env file."; return; }
    const r = await api(pvBase(plan) + "/weather-advice");
    if (!r.ok) { msg.textContent = (r.data && r.data.error) || "The forecast could not be loaded."; return; }
    const w = r.data.forecast || {}, advice = r.data.items || [];
    if (!(w.slots || []).length) {
      msg.textContent = (w.resolved_place || "This venue") + ": " + (w.note || "no forecast yet.");
      if (w.attribution) box.appendChild(el("p", "pv-alt", w.attribution));
      return;
    }
    msg.textContent = w.resolved_place + ": " + w.temp_min + "\u00b0C to " + w.temp_max + "\u00b0C, " + (w.conditions || "no description") + "; chance of rain up to " + Math.round((w.max_rain_chance || 0) * 100) + "%.";
    advice.forEach(function (a) { box.appendChild(el("p", a.level === "warn" ? "pv-bad" : "pv-alt", a.title + ". " + a.detail)); });
    const bt = el("table");
    const thr = el("tr"); ["Time", "\u00b0C", "Rain", "Conditions"].forEach(function (h) { thr.appendChild(el("th", null, h)); });
    bt.appendChild(thr);
    w.slots.forEach(function (x) {
      const tr = el("tr");
      tr.appendChild(el("td", null, String(x.local_time || "").slice(11, 16)));
      tr.appendChild(el("td", "num", String(Math.round(x.temp_c))));
      tr.appendChild(el("td", "num", Math.round((x.rain_chance || 0) * 100) + "%"));
      tr.appendChild(el("td", null, x.description || ""));
      bt.appendChild(tr);
    });
    box.appendChild(bt);
    if (w.attribution) box.appendChild(el("p", "pv-alt", w.attribution));
  })();
}

function paperTimeline(host, plan) {
  host.appendChild(el("h1", null, "TIMELINE"));
  const items = (plan.items || []).map(function (i) {
    return { name: i.name, s: new Date(i.start), e: new Date(i.end), group: i.group || "event_day", resource: i.resource || "organizer" };
  }).filter(function (i) { return !isNaN(i.s) && !isNaN(i.e); });
  if (!items.length) { host.appendChild(el("p", null, "No scheduled items in this plan record.")); return; }
  const ev = plan.request.event_date;
  const pre = items.filter(function (i) { return i.group === "pre_event"; });
  const day = items.filter(function (i) { return i.group !== "pre_event"; });
  const draw = function (list, t0, t1, labelFmt) {
    const g = el("div", "pv-gantt");
    const span = t1 - t0;
    list.forEach(function (i) {
      const row = el("div", "pv-grow");
      const left = Math.max(0, ((i.s - t0) / span) * 100), width = Math.max(1.5, ((i.e - i.s) / span) * 100);
      row.appendChild(el("span", "pv-glabel", i.name));
      const track = el("span", "pv-gtrack");
      const bar = el("i", "pv-gbar " + i.resource);
      bar.style.left = left + "%"; bar.style.width = Math.min(100 - left, width) + "%";
      bar.title = i.name;
      track.appendChild(bar);
      row.appendChild(track);
      row.appendChild(el("span", "pv-gwhen", labelFmt(i)));
      g.appendChild(row);
    });
    return g;
  };
  if (pre.length) {
    const t0 = Math.min.apply(null, pre.map(function (i) { return +i.s; }));
    const t1 = Math.max.apply(null, pre.map(function (i) { return +i.e; })) + 86400000;
    host.appendChild(el("h2", null, "BEFORE THE EVENT"));
    host.appendChild(draw(pre, t0, t1, function (i) { return fmtTime(i.s); }));
  }
  if (day.length) {
    const d0 = new Date(day[0].s); d0.setHours(0, 0, 0, 0);
    const t0 = +d0, t1 = t0 + 86400000;
    host.appendChild(el("h2", null, "EVENT DAY" + (ev ? " - " + ev : "")));
    const fmtClock = function (d) { return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }); };
    host.appendChild(draw(day, t0, t1, function (i) { return fmtClock(i.s) + " - " + fmtClock(i.e); }));
  }
}

function paperVendors(host, plan) {
  host.appendChild(el("h1", null, "VENDORS"));
  const sels = plan.selections || [];
  if (!sels.length) { host.appendChild(el("p", null, "No vendor selections in this plan record.")); return; }
  const chk = (plan.validation && plan.validation.checks) || [];
  const passed = chk.filter((c) => c.passed).length;
  host.appendChild(el("p", null, sels.length + " selected vendor(s) - highest-scoring eligible combination" +
    (chk.length ? " from the solver, " + passed + " of " + chk.length + " hard checks passed" : "") +
    ". Nothing is booked, sent or paid."));
  sels.forEach(function (sel) {
    const card = el("div", "pv-vendor");
    const head = el("div", "pv-vhead");
    head.appendChild(el("b", null, String(sel.category).toUpperCase() + " - " + sel.name));
    head.appendChild(el("span", null, fmtINR(sel.cost) + " · " + sel.booking_status));
    card.appendChild(head);
    if (sel.why) card.appendChild(el("p", null, sel.why));
    const alts = sel.alternatives || [];
    if (alts.length) {
      card.appendChild(el("h2", null, "ALTERNATIVES CONSIDERED (" + alts.length + ")"));
      alts.forEach(function (a) {
        card.appendChild(el("p", "pv-alt", a.name + " - " + (a.eligible ? "eligible" : "rejected") +
          (a.cost != null ? " · " + fmtINR(a.cost) : "") + (a.reason ? " · " + a.reason : "")));
      });
    } else {
      card.appendChild(el("p", "pv-alt", "alternatives list is not part of the stored plan record yet"));
    }
    host.appendChild(card);
  });
}

function paperBudget(host, plan, payload) {
  host.appendChild(el("h1", null, "BUDGET"));
  if (state.paperFlash) { host.appendChild(el("p", "pv-lead", state.paperFlash)); state.paperFlash = null; }
  const a = plan.allocation; if (!a) { host.appendChild(el("p", null, "No allocation in this plan record.")); return; }
  const rows = (a.lines || []).concat([{ category: "contingency", amount: a.contingency }]);
  const total = rows.reduce(function (s2, l) { return s2 + l.amount; }, 0) || 1;
  const inks = ["#1c2430", "#4a6fa5", "#7fb3d5", "#b03a2e", "#7d6608", "#6c3483"];
  let acc = 0;
  const segs = rows.map(function (l, i) {
    const from = acc / total * 100; acc += l.amount;
    return inks[i % inks.length] + " " + from.toFixed(2) + "% " + (acc / total * 100).toFixed(2) + "%";
  });
  const donut = el("div", "pv-donut");
  donut.style.background = "conic-gradient(" + segs.join(", ") + ")";
  const legend = el("div", "pv-legend");
  rows.forEach(function (l, i) {
    const li = el("p", null);
    const sw = el("i", null); sw.style.background = inks[i % inks.length];
    li.appendChild(sw);
    li.appendChild(document.createTextNode(l.category + " cap: " + fmtINR(l.amount)));
    legend.appendChild(li);
  });
  const wrap = el("div", "pv-donutwrap"); wrap.appendChild(donut); wrap.appendChild(legend);
  host.appendChild(wrap);
  host.appendChild(el("h2", null, "CATEGORY CAPS - the most the solver may spend per category, not spends"));
  const bt = el("table");
  rows.forEach(function (l) {
    const tr = el("tr");
    tr.appendChild(el("td", null, l.category + " cap"));
    tr.appendChild(el("td", "num", fmtINR(l.amount)));
    bt.appendChild(tr);
  });
  const ttr = el("tr");
  ttr.appendChild(el("td", null, "selected vendor cost (estimate - nothing committed)"));
  ttr.appendChild(el("td", "num", fmtINR(plan.total_cost)));
  bt.appendChild(ttr);
  host.appendChild(bt);
  paperQuotes(host, plan);
  paperLedger(host, plan);
  paperScores(host, plan);
  paperLeads(host, plan);
  if (payload.reality) host.appendChild(el("p", null, "Budget reality check: " + payload.reality.verdict + " - " + payload.reality.message));
}


/* Vendor scoreboards + saved leads, parity with the classic UI (renderScores/
   renderLeads). Scores come only from recorded quotes; leads are advertised
   prices the user bookmarked and never change the plan's prices. */
async function paperScores(host, plan) {
  const cats = [...new Set((plan.selections || []).map(function (x) { return x.category; }))];
  host.appendChild(el("h2", null, "VENDOR SCORES - computed from the quotes you recorded"));
  const box = el("div", null); host.appendChild(box);
  if (!cats.length) { box.appendChild(el("p", "pv-alt", "No selected vendors to score.")); return; }
  const results = await Promise.all([api("/api/vendors/directory")].concat(cats.map(function (c) { return api("/api/observations/score/" + encodeURIComponent(c)); })));
  const dir = results[0].ok ? results[0].data : {};
  const who = function (id) { const d = dir[id]; return d ? d.name + " (" + (d.label || d.origin) + ")" : id; };
  box.appendChild(el("p", "pv-alt", "Price against the median of at least three current quotes, completeness of the checklist, and how well the quote is evidenced. Response time, terms and ratings are not scored: there is no data for them."));
  let any = false;
  results.slice(1).forEach(function (r, i) {
    if (!r.ok) return;
    const b = r.data || {};
    if (!(b.scores || []).length && !(b.unscored || []).length) return;
    any = true;
    box.appendChild(el("h2", null, String(cats[i]).replace(/_/g, " ").toUpperCase()));
    (b.scores || []).forEach(function (sc) {
      const card = el("div", "pv-vendor");
      const head = el("div", "pv-vhead");
      head.appendChild(el("b", null, who(sc.vendor_id)));
      head.appendChild(el("span", "pv-chip ok", sc.overall === null ? "n/a" : sc.overall + "/100"));
      card.appendChild(head);
      card.appendChild(el("div", "pv-alt", Math.round((sc.coverage || 0) * 100) + "% of the formula had data (" + sc.status + ")"));
      (sc.components || []).forEach(function (c) { card.appendChild(el("div", "pv-alt", "- " + String(c.name).replace(/_/g, " ") + " (" + Math.round(c.weight * 100) + "%): " + (c.score === null ? "left out" : c.score) + "; " + c.detail)); });
      (sc.notes || []).forEach(function (n) { card.appendChild(el("div", "pv-bad", "! " + n)); });
      box.appendChild(card);
    });
    (b.unscored || []).forEach(function (u) { box.appendChild(el("p", "pv-alt", who(u.vendor_id) + ": not scored; " + u.reason)); });
  });
  if (!any) box.appendChild(el("p", "pv-alt", "No quotes recorded yet. Record quotes above to compare vendors."));
}

async function paperLeads(host, plan) {
  host.appendChild(el("h2", null, "SAVED LEADS - advertised prices you bookmarked; never quotes"));
  const box = el("div", null); host.appendChild(box);
  const r = await api(pvBase(plan) + "/leads");
  if (!r.ok) { box.appendChild(el("p", "pv-alt", "Leads could not be loaded.")); return; }
  if (!(r.data || []).length) { box.appendChild(el("p", "pv-alt", "None yet. Use Explore deals and trips, then Save as lead.")); return; }
  if (r.data[0].notice) box.appendChild(el("p", "pv-alt", r.data[0].notice));
  r.data.forEach(function (l) {
    const card = el("div", "pv-vendor");
    const head = el("div", "pv-vhead");
    head.appendChild(el("b", null, String(l.kind).replace(/_/g, " ") + ": " + (l.label || l.domain)));
    head.appendChild(el("span", "pv-chip", (l.low_inr === l.high_inr ? fmtINR(l.low_inr) : fmtINR(l.low_inr) + " to " + fmtINR(l.high_inr)) + " " + String(l.unit).replace(/_/g, " ")));
    card.appendChild(head);
    const a = el("a", "pv-alt", l.domain);
    if (/^https?:\/\//i.test(String(l.url))) { a.href = l.url; a.target = "_blank"; a.rel = "noopener noreferrer nofollow"; } // the server only stores http(s); the page does not rely on it
    card.appendChild(a);
    box.appendChild(card);
  });
}

/* Budget parity: quotes + ledger, ported from the classic UI (app.js renderQuotes/
   renderLedger). Same backend routes, same approval semantics: recorded quotes set
   prices, the ledger only records what the user reports - nothing is paid. */
async function paperQuotes(host, plan) {
  host.appendChild(el("h2", null, "QUOTES - prices you were given set this plan's prices"));
  const box = el("div", null); host.appendChild(box);
  box.appendChild(el("p", "pv-alt", "Loading quotes..."));
  const r = await api(pvBase(plan) + "/quotes");
  box.textContent = "";
  if (!r.ok) { box.appendChild(el("p", "pv-alt", "Quotes could not be loaded.")); return; }
  const rows = r.data || [];
  const stale = rows.filter(function (x) { return x.price_stale; });
  if (!rows.length) box.appendChild(el("p", "pv-alt", "No vendors in this plan to quote for."));
  rows.forEach(function (x) {
    const rep = x.report;
    const card = el("div", "pv-vendor");
    const head = el("div", "pv-vhead");
    head.appendChild(el("b", null, x.category + ": " + x.vendor_name));
    head.appendChild(el("span", "pv-chip " + (rep && rep.status === "expired" ? "" : "ok"), rep ? rep.status : "no quote"));
    card.appendChild(head);
    card.appendChild(el("div", "pv-alt", "Plan " + fmtINR(x.plan_cost) + "; price now " + fmtINR(x.current_price) + (rep ? "; quoted " + fmtINR(rep.effective_total) + " as given, " + String(rep.source_type).replace("_", " ") + ", valid until " + rep.expires_at : "; seed estimate, no quote recorded")));
    ((rep && rep.flags) || []).forEach(function (f) { card.appendChild(el("div", "pv-alt", "- " + f)); });
    box.appendChild(card);
  });
  const msg = el("p", "pv-alt", "");
  if (stale.length) {
    const warn = el("p", "pv-bad", "Recorded quotes differ from this plan's prices: " + stale.map(function (x) { return x.category + " " + fmtINR(x.plan_cost) + " -> " + fmtINR(x.current_price); }).join("; ") + ".");
    box.appendChild(warn);
    const reprice = el("button", "pv-btn", "RE-PRICE THIS PLAN");
    reprice.type = "button";
    reprice.addEventListener("click", async function () {
      const budget = (plan.request && plan.request.budget_total) || (plan.allocation ? plan.allocation.total : 0);
      const res = await api(pvBase(plan) + "/replan", { method: "POST", body: { kind: "budget_change", new_budget: budget } });
      if (res.ok && res.data && res.data.plan) { state.paperFlash = "Plan re-priced."; renderPaperView("budget"); }
      else box.appendChild(el("p", "pv-bad", (res.data && res.data.violations && res.data.violations[0] && res.data.violations[0].detail) || "Could not re-price the plan."));
    });
    box.appendChild(reprice);
  }
  if (!rows.length) return;
  box.appendChild(el("p", "pv-alt", "An expired quote is not used: the vendor is dropped until you record a fresh one. Nothing is sent to vendors."));
  const f = {
    vendor: el("select", "pv-input"),
    source: el("select", "pv-input"),
    amount: el("input", "pv-input"),
    basis: el("select", "pv-input"),
    guests: el("input", "pv-input"),
    expires: el("input", "pv-input"),
    by: el("input", "pv-input"),
    evidence: el("input", "pv-input"),
    hidden: el("input", "pv-input"),
    hiddenAmt: el("input", "pv-input")
  };
  rows.forEach(function (x) { const o = el("option", null, x.category + ": " + x.vendor_name); o.value = x.vendor_id; f.vendor.appendChild(o); });
  [["user_input", "Told to me"], ["vendor_quote", "Vendor's written quote"], ["vendor_confirmation", "Vendor's written confirmation"], ["public_listing", "Public listing (never sets a price)"]].forEach(function (p) { const o = el("option", null, p[1]); o.value = p[0]; f.source.appendChild(o); });
  f.amount.type = "number"; f.amount.min = "1"; f.amount.step = "1"; f.amount.placeholder = "Amount (INR)";
  [["total", "Total"], ["per_plate", "Per plate"]].forEach(function (p) { const o = el("option", null, p[1]); o.value = p[0]; f.basis.appendChild(o); });
  f.guests.type = "number"; f.guests.min = "1"; f.guests.placeholder = "Guests quoted for (per plate)";
  f.expires.type = "date";
  f.by.maxLength = 80; f.by.placeholder = "Your name"; f.by.value = state.paperSenderName || "";
  f.evidence.maxLength = 200; f.evidence.placeholder = "Evidence (email subject, message id)";
  f.hidden.maxLength = 80; f.hidden.placeholder = "Hidden cost label (optional)";
  f.hiddenAmt.type = "number"; f.hiddenAmt.min = "1"; f.hiddenAmt.placeholder = "Hidden cost INR (optional)";
  const add = el("button", "pv-btn", "RECORD QUOTE");
  add.type = "button";
  add.addEventListener("click", async function () {
    const x = rows.find(function (v) { return v.vendor_id === f.vendor.value; });
    const n = parseInt(f.amount.value, 10);
    if (!(n > 0) || !f.expires.value || !f.by.value.trim()) { msg.textContent = "Enter an amount, who you are and the date the quote is valid until."; return; }
    const res = await api("/api/observations/" + encodeURIComponent(f.vendor.value) + "/quotes", { method: "POST", body: {
      category: x.category, source_type: f.source.value, entered_by: f.by.value.trim(), expires_at: f.expires.value,
      confidence: f.source.value === "user_input" ? 0.6 : 0.9, evidence_ref: f.evidence.value.trim(), amount_inr: n, basis: f.basis.value,
      guest_count: f.basis.value === "per_plate" ? parseInt(f.guests.value, 10) || null : null,
      hidden_costs: f.hidden.value.trim() ? [{ label: f.hidden.value.trim(), amount_inr: parseInt(f.hiddenAmt.value, 10) || null }] : [] } });
    if (res.ok) { state.paperSenderName = f.by.value.trim(); state.paperFlash = "Quote recorded for " + x.vendor_name + "."; renderPaperView("budget"); }
    else msg.textContent = (res.data && res.data.details && res.data.details[0] && res.data.details[0].message) || (res.data && res.data.error) || "That quote was refused.";
  });
  const r1 = el("div", "pv-acts"); [f.vendor, f.source, f.amount, f.basis, f.guests, f.expires].forEach(function (n) { r1.appendChild(n); });
  const r2 = el("div", "pv-acts"); [f.by, f.evidence, f.hidden, f.hiddenAmt, add].forEach(function (n) { r2.appendChild(n); });
  box.appendChild(r1); box.appendChild(r2); box.appendChild(msg);
}

async function paperLedger(host, plan) {
  host.appendChild(el("h2", null, "MONEY LEDGER - amounts you report; to;doh records them and never makes a payment"));
  const box = el("div", null); host.appendChild(box);
  box.appendChild(el("p", "pv-alt", "Loading ledger..."));
  const r = await api(pvBase(plan) + "/ledger");
  box.textContent = "";
  if (!r.ok) { box.appendChild(el("p", "pv-alt", "The ledger could not be loaded.")); return; }
  const s = r.data.summary;
  const bt = el("table");
  const thr = el("tr");
  ["Category", "Planned", "Quoted", "Paid", "Pending", "Variance"].forEach(function (h) { thr.appendChild(el("th", null, h)); });
  bt.appendChild(thr);
  s.lines.forEach(function (l) {
    const tr = el("tr");
    tr.appendChild(el("td", null, l.category));
    tr.appendChild(el("td", "num", fmtINR(l.planned)));
    tr.appendChild(el("td", "num", l.quoted === null ? "-" : fmtINR(l.quoted)));
    tr.appendChild(el("td", "num", fmtINR(l.paid)));
    tr.appendChild(el("td", "num", fmtINR(l.pending)));
    tr.appendChild(el("td", "num", (l.variance > 0 ? "+" : "") + fmtINR(l.variance)));
    bt.appendChild(tr);
  });
  const ttr = el("tr");
  ttr.appendChild(el("td", null, "Total"));
  ttr.appendChild(el("td", "num", fmtINR(s.planned_total)));
  ttr.appendChild(el("td", "num", fmtINR(s.quoted_total)));
  ttr.appendChild(el("td", "num", fmtINR(s.paid_total)));
  ttr.appendChild(el("td", "num", fmtINR(s.pending_total)));
  ttr.appendChild(el("td", "num", (s.variance_total > 0 ? "+" : "") + fmtINR(s.variance_total)));
  bt.appendChild(ttr);
  box.appendChild(bt);
  box.appendChild(el("p", s.headroom < 0 ? "pv-bad" : "pv-alt", "Committed " + fmtINR(s.committed_total) + " of " + fmtINR(s.budget_total) + "; " + (s.headroom < 0 ? "over budget by " + fmtINR(-s.headroom) : "headroom " + fmtINR(s.headroom)) + "."));
  if (s.orphaned_paid && Object.keys(s.orphaned_paid).length) box.appendChild(el("p", "pv-bad", "Paid to services no longer in this plan: " + Object.entries(s.orphaned_paid).map(function (kv) { return kv[0] + " " + fmtINR(kv[1]); }).join(", ") + ". Ask the vendor about a refund."));
  const msg = el("p", "pv-alt", "");
  const category = el("select", "pv-input");
  s.lines.forEach(function (l) { const o = el("option", null, l.category); o.value = l.category; category.appendChild(o); });
  const kind = el("select", "pv-input");
  ["quote", "payment", "refund"].forEach(function (k) { const o = el("option", null, k); o.value = k; kind.appendChild(o); });
  const amount = el("input", "pv-input"); amount.type = "number"; amount.min = "1"; amount.step = "1"; amount.placeholder = "Amount (INR)";
  const note = el("input", "pv-input"); note.maxLength = 200; note.placeholder = "Note (optional)";
  const add = el("button", "pv-btn", "RECORD");
  add.type = "button";
  add.addEventListener("click", async function () {
    const n = parseInt(amount.value, 10);
    if (!(n > 0)) { msg.textContent = "Enter a whole amount above zero."; return; }
    const res = await api(pvBase(plan) + "/ledger", { method: "POST", body: { kind: kind.value, category: category.value, amount: n, note: note.value } });
    if (res.ok) { state.paperFlash = "Ledger entry recorded."; renderPaperView("budget"); }
    else msg.textContent = (res.data && res.data.error) || "That entry was refused.";
  });
  const r1 = el("div", "pv-acts"); [category, kind, amount, note, add].forEach(function (nd) { r1.appendChild(nd); });
  box.appendChild(r1); box.appendChild(msg);
}

/* ---------------- live paper tabs: wired to the plan's own routes ----------------
   Every tab below reads/writes the same backend the classic UI used. Loading and
   failure states are shown honestly; no content is staged. Approval-gated actions
   (outreach, assistant proposals) keep their review step. */

function pvLoad(host, label) {
  const p = el("p", "pv-alt", label || "loading...");
  host.appendChild(p);
  return p;
}
function pvFail(p, r, fallback) {
  p.textContent = (r && r.data && (r.data.error || (r.data.details && r.data.details[0] && r.data.details[0].message))) || fallback;
  p.className = "pv-bad";
}
const pvBase = function (plan) { return "/api/plans/" + encodeURIComponent(plan.plan_id); };

function paperAsk(host, plan) {
  host.appendChild(el("h1", null, "ASK"));
  host.appendChild(el("p", "pv-alt", "Answers use only this plan and to;doh-the event planner's own documents, and say where they came from. Nothing changes unless you approve it."));
  const thread = el("div", "pv-askthread");
  const input = el("input", "pv-input");
  input.type = "text"; input.maxLength = 1000;
  input.placeholder = "Ask about this plan: budget, vendors, dietary needs...";
  const send = el("button", "pv-btn", "ASK");
  send.type = "submit";
  const log = [];
  const draw = function () {
    thread.textContent = "";
    log.forEach(function (m) {
      if (m.me) { thread.appendChild(el("div", "pv-q", m.me)); return; }
      if (m.err) { thread.appendChild(el("div", "pv-bad", m.err)); return; }
      const a = m.ai;
      const box = el("div", "pv-a");
      box.appendChild(el("div", null, a.answer));
      const meta = el("div", "pv-askmeta");
      (a.citations || []).forEach(function (c) { meta.appendChild(el("span", "pv-chip", c)); });
      meta.appendChild(el("span", "pv-chip " + (a.outcome === "answered" ? "ok" : "warn"), a.outcome));
      if (a.cache && a.cache !== "miss") meta.appendChild(el("span", "pv-chip", "reused answer"));
      box.appendChild(meta);
      if (a.degraded_reason) box.appendChild(el("div", "pv-alt", "fallback answer: " + a.degraded_reason));
      (a.proposals || []).forEach(function (pr) {
        box.appendChild(el("div", "pv-prop", "Filed for your approval - nothing happens until you approve: " + pr.summary));
      });
      thread.appendChild(box);
    });
    thread.scrollTop = thread.scrollHeight;
  };
  const form = el("form", "pv-askform");
  form.appendChild(input); form.appendChild(send);
  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    const q = input.value.trim(); if (!q) return;
    input.value = ""; send.disabled = true;
    log.push({ me: q }); draw();
    const r = await api("/api/assistant/ask", { method: "POST", body: { plan_id: plan.plan_id, question: q } });
    send.disabled = false;
    log.push(r.ok ? { ai: r.data } : { err: (r.data && r.data.error) || "I could not answer that." });
    draw();
  });
  host.appendChild(thread); host.appendChild(form);
}

async function paperIdeas(host, plan) {
  host.appendChild(el("h1", null, "IDEAS"));
  const load = pvLoad(host);
  const r = await api(pvBase(plan) + "/ideas");
  if (!r.ok) return pvFail(load, r, "Ideas could not be loaded.");
  load.remove();
  const d = r.data;
  const again = () => renderPaperView("ideas");
  const msg = el("div", "pv-alt");
  host.appendChild(el("p", "pv-alt", (d.notice || "Suggestions from this plan's facts.") + " Pin one to keep it as a note or checklist item. Preferences you type here are remembered as your stated facts for the assistant."));
  const prefs = el("input", "pv-input");
  prefs.type = "text"; prefs.maxLength = 300;
  prefs.placeholder = "Preferences, comma separated (optional): rustic, kid friendly";
  const go = el("button", "pv-btn", d.ideas && d.ideas.length ? "SUGGEST AGAIN" : "SUGGEST IDEAS");
  go.type = "button";
  go.addEventListener("click", async function () {
    msg.textContent = "asking the ideas engine...";
    const list = prefs.value.split(",").map(function (x) { return x.trim(); }).filter(Boolean).slice(0, 5);
    const res = await api(pvBase(plan) + "/ideas", { method: "POST", body: { preferences: list } });
    if (res.ok) again(); else msg.textContent = (res.data && res.data.error) || "Could not suggest ideas.";
  });
  const row = el("div", "pv-askform"); row.appendChild(prefs); row.appendChild(go);
  host.appendChild(row); host.appendChild(msg);
  if (d.source) host.appendChild(el("p", "pv-alt", "Made " + (String(d.source).startsWith("llm:") ? "with a model, checked by rules" : "from built-in templates") + " using: " + (d.facts_used || []).join(", ") + "."));
  (d.ideas || []).forEach(function (i) {
    const card = el("div", "pv-vendor");
    const head = el("div", "pv-vhead");
    head.appendChild(el("b", null, i.title));
    head.appendChild(el("span", "pv-chip", i.kind));
    card.appendChild(head);
    card.appendChild(el("div", "pv-alt", i.why));
    if (i.est_cost_note) card.appendChild(el("div", "pv-alt", "Cost: " + i.est_cost_note));
    if (i.review_note) card.appendChild(el("div", "pv-bad", i.review_note));
    const acts = el("div", "pv-acts");
    if ((d.pinned || []).indexOf(i.idea_id) >= 0) acts.appendChild(el("span", "pv-chip ok", "pinned"));
    else {
      const pin = el("button", "pv-btn small", "PIN");
      pin.type = "button";
      pin.addEventListener("click", async function () { const res = await api(pvBase(plan) + "/ideas/" + i.idea_id + "/pin", { method: "POST", body: {} }); if (res.ok) again(); else msg.textContent = (res.data && res.data.error) || "Could not pin."; });
      acts.appendChild(pin);
    }
    const dis = el("button", "pv-btn small", "DISMISS");
    dis.type = "button";
    dis.addEventListener("click", async function () { const res = await api(pvBase(plan) + "/ideas/" + i.idea_id + "/dismiss", { method: "POST", body: {} }); if (res.ok) again(); else msg.textContent = (res.data && res.data.error) || "Could not dismiss."; });
    acts.appendChild(dis);
    card.appendChild(acts);
    host.appendChild(card);
  });
  if (d.dismissed && d.dismissed.length) host.appendChild(el("p", "pv-alt", d.dismissed.length + " dismissed."));
  if ((!d.ideas || !d.ideas.length) && !d.source) host.appendChild(el("p", "pv-alt", "No ideas yet. Press SUGGEST IDEAS."));
  host.appendChild(el("h2", null, "NOTES AND CHECKLIST"));
  if (!d.notes || !d.notes.length) host.appendChild(el("p", "pv-alt", "Nothing pinned yet."));
  (d.notes || []).forEach(function (n) {
    const lab = el("label", "pv-note");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = !!n.done;
    cb.addEventListener("change", async function () { await api(pvBase(plan) + "/notes/" + n.note_id + "/done", { method: "POST", body: { done: cb.checked } }); again(); });
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(" " + n.text + " "));
    lab.appendChild(el("span", "pv-alt", n.kind === "checklist_item" ? "(checklist)" : "(note)"));
    host.appendChild(lab);
  });
}

const PV_ANSWER_LABELS = {
  public_event: "Is it open to the public?", third_party_premises: "Is the space owned by someone else (a venue, society or institution)?", amplified_sound: "Will you use loudspeakers or amplified music?",
  sells_food: "Will food be sold?", collects_money: "Will you collect or receive money from the public?", large_crowd: "Do you expect a large crowd?",
  temporary_structures: "Will you set up stages, tents or electrical rigging?", physical_activities: "Will there be physical or sports activities?", outdoor_area: "Is any part outdoors?",
};
const PV_ACCESS_LABELS = { step_free_access: "Step-free access", accessible_toilet: "Accessible toilet", reserved_seating: "Reserved seating", hearing_support: "Hearing support", accessible_parking: "Accessible parking" };
const PV_PHASES = ["setup", "event", "teardown"];

function pvInput(obj, key, ph, isNum) {
  const i = el("input", "pv-input");
  i.type = isNum ? "number" : "text"; if (isNum) i.min = "0";
  i.placeholder = ph; i.setAttribute("aria-label", ph);
  if (!isNum) i.maxLength = 80;
  i.value = obj[key] == null ? "" : obj[key];
  i.addEventListener("input", function () { obj[key] = isNum ? (i.value === "" ? null : parseInt(i.value, 10)) : i.value; });
  return i;
}
function pvSelect(obj, key, opts, label) {
  const s = el("select", "pv-input");
  s.setAttribute("aria-label", label);
  opts.forEach(function (pair) {
    const o = el("option", null, pair[1]); o.value = pair[0];
    if (obj[key] === pair[0]) o.selected = true;
    s.appendChild(o);
  });
  s.addEventListener("change", function () { obj[key] = s.value; });
  return s;
}
function pvRows(host, title, list, make, blank, addText) {
  host.appendChild(el("h3", null, title));
  list.forEach(function (row, i) {
    const line = el("div", "pv-formrow");
    make(row, i).forEach(function (c) { line.appendChild(c); });
    const rm = el("button", "pv-btn small", "REMOVE"); rm.type = "button";
    rm.addEventListener("click", function () { list.splice(i, 1); redrawCommunityForm(); });
    line.appendChild(rm);
    host.appendChild(line);
  });
  const add = el("button", "pv-btn small", addText); add.type = "button";
  add.addEventListener("click", function () { list.push(blank()); redrawCommunityForm(); });
  host.appendChild(add);
}
let redrawCommunityForm = function () {};
function paperCommunityForm(cd, d) {
  const root = el("div", "pv-form");
  root.appendChild(el("h3", null, "ATTENDANCE"));
  let line = el("div", "pv-formrow");
  line.appendChild(pvInput(cd, "attendance_low", "Lowest expected attendance", true));
  line.appendChild(pvInput(cd, "attendance_high", "Highest expected attendance", true));
  root.appendChild(line);
  root.appendChild(el("p", "pv-alt", "The venue must hold the highest number. Leave blank to plan for the guest count alone."));
  root.appendChild(el("h3", null, "MONEY"));
  line = el("div", "pv-formrow");
  line.appendChild(pvInput(cd, "own_funds_inr", "Your own funds (INR)", true));
  const cc = el("label", "pv-alt");
  const ccb = el("input"); ccb.type = "checkbox"; ccb.checked = !!cd.count_committed;
  ccb.addEventListener("change", function () { cd.count_committed = ccb.checked; });
  cc.appendChild(ccb); cc.appendChild(document.createTextNode(" Also count money people have committed (not yet received)"));
  line.appendChild(cc);
  root.appendChild(line);
  root.appendChild(el("p", "pv-alt", "The plan may spend your own funds plus money recorded as received. Pledged money is never counted; committed money only if you tick the box, and it is then shown as such."));
  pvRows(root, "FUNDING SOURCES", cd.funding, function (f) {
    return [pvInput(f, "label", "Who or what"), pvSelect(f, "status", [["pledged", "Pledged (a hope)"], ["committed", "Committed (promised)"], ["received", "Received (in hand)"]], "Status"), pvInput(f, "amount_inr", "Amount (INR)", true)];
  }, function () { return { label: "", status: "pledged", amount_inr: null }; }, "ADD A FUNDING SOURCE");
  root.appendChild(el("h3", null, "VOLUNTEERS"));
  const vol = el("textarea", "pv-input"); vol.rows = 3; vol.placeholder = "One name per line"; vol.setAttribute("aria-label", "Volunteers");
  vol.value = cd.volunteers.join("\n");
  vol.addEventListener("input", function () { cd.volunteers = vol.value.split("\n").map(function (x) { return x.trim(); }).filter(Boolean); });
  root.appendChild(vol);
  line = el("div", "pv-formrow");
  line.appendChild(pvInput(cd, "setup_owner", "Who owns setup (a volunteer above)"));
  line.appendChild(pvInput(cd, "cleanup_owner", "Who owns cleanup (a volunteer above)"));
  root.appendChild(line);
  root.appendChild(el("p", "pv-alt", "Roles the profile usually suggests (tap to add as a role you confirm you need):"));
  const chips = el("div", "pv-formrow");
  (d.suggested_roles || []).forEach(function (r) {
    const b = el("button", "pv-btn small", r.role); b.type = "button"; b.title = r.note;
    b.addEventListener("click", function () { cd.slots.push({ slot_id: "s" + (cd.slots.length + 1) + Math.floor(Math.random() * 90 + 10), role: r.role, phase: /setup/i.test(r.role) ? "setup" : /cleanup/i.test(r.role) ? "teardown" : "event", needed: 1, start_min: null, end_min: null }); redrawCommunityForm(); });
    chips.appendChild(b);
  });
  root.appendChild(chips);
  pvRows(root, "ROLES YOU NEED (CONFIRMED)", cd.slots, function (sl) {
    return [pvInput(sl, "role", "Role"), pvSelect(sl, "phase", PV_PHASES.map(function (p) { return [p, p]; }), "Phase"), pvInput(sl, "needed", "How many", true), pvInput(sl, "start_min", "From minute (optional)", true), pvInput(sl, "end_min", "To minute (optional)", true)];
  }, function () { return { slot_id: "s" + Math.floor(Math.random() * 9000 + 1000), role: "", phase: "event", needed: 1, start_min: null, end_min: null }; }, "ADD A ROLE");
  pvRows(root, "WHO COVERS WHAT", cd.shifts, function (sh) {
    return [pvInput(sh, "volunteer", "Volunteer"), pvInput(sh, "role", "Role"), pvSelect(sh, "phase", PV_PHASES.map(function (p) { return [p, p]; }), "Phase"), pvInput(sh, "start_min", "From minute (optional)", true), pvInput(sh, "end_min", "To minute (optional)", true)];
  }, function () { return { volunteer: "", role: "", phase: "event", start_min: null, end_min: null }; }, "ADD A SHIFT");
  pvRows(root, "PROGRAMME", cd.program, function (a) {
    return [pvInput(a, "title", "Activity"), pvInput(a, "minutes", "Minutes", true), pvInput(a, "resource", "Stage or place (shared)"),
      pvSelect(a, "after", [["", "No fixed order"]].concat(cd.program.filter(function (x) { return x !== a && x.activity_id; }).map(function (x) { return [x.activity_id, "After: " + (x.title || x.activity_id)]; })), "Comes after")];
  }, function () { return { activity_id: "p" + Math.floor(Math.random() * 90000 + 10000), title: "", minutes: 30, resource: "stage", after: "" }; }, "ADD AN ACTIVITY");
  root.appendChild(el("h3", null, "ACCESS NEEDS OF YOUR GUESTS"));
  line = el("div", "pv-formrow");
  Object.keys(PV_ACCESS_LABELS).forEach(function (k) {
    const lab = el("label", "pv-alt");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = cd.access_needs.indexOf(k) >= 0; cb.setAttribute("aria-label", PV_ACCESS_LABELS[k]);
    cb.addEventListener("change", function () { const set = new Set(cd.access_needs); if (cb.checked) set.add(k); else set.delete(k); cd.access_needs = Array.from(set); });
    lab.appendChild(cb); lab.appendChild(document.createTextNode(" " + PV_ACCESS_LABELS[k] + " "));
    line.appendChild(lab);
  });
  root.appendChild(line);
  root.appendChild(el("h3", null, "QUESTIONS THAT DECIDE WHAT TO CHECK"));
  Object.keys(PV_ANSWER_LABELS).forEach(function (k) {
    line = el("div", "pv-formrow");
    line.appendChild(el("span", "pv-alt", PV_ANSWER_LABELS[k]));
    line.appendChild(pvSelect(cd.answers, k, [["unsure", "Not sure"], ["yes", "Yes"], ["no", "No"]], PV_ANSWER_LABELS[k]));
    root.appendChild(line);
  });
  return root;
}
function pvCleanCommunity(cd) {
  const out = { subtype: cd.subtype, count_committed: !!cd.count_committed, answers: {} };
  ["attendance_low", "attendance_high", "own_funds_inr"].forEach(function (k) { if (cd[k] !== null && cd[k] !== undefined && cd[k] !== "") out[k] = cd[k]; });
  out.funding = cd.funding.filter(function (f) { return f.label && f.amount_inr > 0; }).map(function (f) { return { label: f.label, status: f.status, amount_inr: f.amount_inr }; });
  out.volunteers = cd.volunteers;
  ["setup_owner", "cleanup_owner"].forEach(function (k) { if (cd[k]) out[k] = cd[k]; });
  out.slots = cd.slots.filter(function (x) { return x.role; }).map(function (x) { const o = { slot_id: x.slot_id, role: x.role, phase: x.phase, needed: x.needed || 1 }; if (x.start_min) o.start_min = x.start_min; if (x.end_min) o.end_min = x.end_min; return o; });
  out.shifts = cd.shifts.filter(function (x) { return x.volunteer && x.role; }).map(function (x) { const o = { volunteer: x.volunteer, role: x.role, phase: x.phase }; if (x.start_min) o.start_min = x.start_min; if (x.end_min) o.end_min = x.end_min; return o; });
  out.program = cd.program.filter(function (a) { return a.title; }).map(function (a) { const o = { activity_id: a.activity_id, title: a.title, minutes: a.minutes || 30, resource: (a.resource || "stage").toLowerCase().replace(/[^a-z0-9_]/g, "_").slice(0, 20) }; if (a.after) o.after = a.after; return o; });
  out.access_needs = cd.access_needs;
  Object.keys(cd.answers).forEach(function (k) { if (cd.answers[k]) out.answers[k] = cd.answers[k]; });
  return out;
}

async function paperCommunity(host, plan) {
  host.appendChild(el("h1", null, "COMMUNITY"));
  const load = pvLoad(host);
  const r = await api(pvBase(plan) + "/community");
  if (!r.ok) return pvFail(load, r, "Community details could not be loaded for this plan.");
  load.remove();
  const d = r.data;
  const again = () => renderPaperView("community");
  if (state.paperFlash) { host.appendChild(el("p", "pv-lead", state.paperFlash)); state.paperFlash = null; }
  host.appendChild(el("p", "pv-alt", d.profile_label + ". Profile v" + d.profile_version + (d.profile_reviewed ? "" : ", not reviewed by any authority") + ": its roles, budget shares and timing are adjustable defaults, not rules."));
  host.appendChild(el("p", "pv-lead", d.summary));
  host.appendChild(el("p", "pv-alt", d.summary_detail));
  if (d.checks && d.checks.length) {
    host.appendChild(el("h2", null, "CHECKED FROM YOUR DETAILS AND THE DATABASE"));
    const ul = el("ul", "pv-checks");
    d.checks.forEach(function (c) { ul.appendChild(el("li", c.passed ? "pass" : "fail", (c.passed ? "Passed: " : "Failed: ") + c.name + " - " + c.detail)); });
    host.appendChild(ul);
  }
  if (d.unverified && d.unverified.length) {
    host.appendChild(el("h2", null, "NOT VERIFIED"));
    const ul = el("ul", "pv-checks");
    d.unverified.forEach(function (u) { ul.appendChild(el("li", "warn", (u.label || u.name) + " - " + (u.detail || "") + (u.repair ? " To fix: " + u.repair : ""))); });
    host.appendChild(ul);
  }
  host.appendChild(el("h2", null, "CONDITIONAL ITEMS - MARKED ONLY BY YOU"));
  host.appendChild(el("p", "pv-alt", "to;doh-the event planner never marks these itself. Your marks are recorded with your name and date."));
  (d.items || []).forEach(function (i) {
    const card = el("div", "pv-vendor");
    const head = el("div", "pv-vhead");
    head.appendChild(el("b", null, i.title));
    head.appendChild(el("span", "pv-chip " + (i.status === "confirmed_by_user" ? "ok" : i.status === "not_applicable_by_user" ? "" : "warn"),
      i.status === "confirmed_by_user" ? "confirmed by you" : i.status === "not_applicable_by_user" ? "not applicable" : "not verified"));
    card.appendChild(head);
    card.appendChild(el("div", "pv-alt", i.instruction + ". Triggered by: " + (i.triggered_by || []).join(", ") + "."));
    if (i.recorded_by) card.appendChild(el("div", "pv-alt", "Marked by " + i.recorded_by + (i.recorded_on ? " on " + i.recorded_on : "") + (i.note ? " - " + i.note : "")));
    const acts = el("div", "pv-acts");
    [["confirmed_by_user", "MARK CONFIRMED"], ["not_applicable_by_user", "NOT APPLICABLE"], ["not_verified", "CLEAR MARK"]].forEach(function (pair) {
      if (i.status === pair[0]) return;
      const b = el("button", "pv-btn small", pair[1]);
      b.type = "button";
      b.addEventListener("click", async function () {
        const note = pair[0] === "not_verified" ? "" : (window.prompt("Note (optional): who told you, or why", "") || "");
        const who = (wallui.me && wallui.me.mark && wallui.me.mark.name) || "paper user";
        const res = await api(pvBase(plan) + "/community/items/" + i.item_id, { method: "POST", body: { status: pair[0], by: who, on: true, note: note } });
        if (res.ok) again(); else card.appendChild(el("div", "pv-bad", (res.data && res.data.error) || "Could not record that."));
      });
      acts.appendChild(b);
    });
    card.appendChild(acts);
    host.appendChild(card);
  });
  host.appendChild(el("h2", null, "YOUR DETAILS"));
  host.appendChild(el("p", "pv-alt", "Fill in what you know. Saving re-checks everything and stores a new plan version; if something fails you see what and how to fix it, and nothing changes."));
  const cd = JSON.parse(JSON.stringify(d.details || { subtype: d.subtype }));
  ["funding", "slots", "shifts", "program", "volunteers", "access_needs"].forEach(function (k) { cd[k] = cd[k] || []; });
  cd.answers = cd.answers || {};
  let formBox = el("div", null);
  const msg = el("div", "pv-alt");
  const drawForm = function () {
    const next = paperCommunityForm(cd, d);
    formBox.replaceWith(next); formBox = next;
  };
  redrawCommunityForm = drawForm;
  drawForm();
  const errs = el("div", "pv-bad"); errs.hidden = true;
  const save = el("button", "pv-btn", "CHECK AND SAVE THESE DETAILS"); save.type = "button";
  save.addEventListener("click", async function () {
    errs.hidden = true; errs.textContent = ""; msg.textContent = "";
    save.disabled = true;
    const res = await api(pvBase(plan) + "/community", { method: "POST", body: pvCleanCommunity(cd) });
    save.disabled = false;
    if (res.ok && res.data && res.data.plan) { state.paperFlash = "Saved as version " + res.data.plan.version + ". Every check re-ran against the new details."; again(); return; }
    errs.hidden = false;
    const vs = (res.data && res.data.violations) || [];
    if (vs.length) {
      vs.forEach(function (v) {
        errs.appendChild(el("p", null, (v.constraint || "check") + ": " + (v.detail || "")));
        if (v.repair) errs.appendChild(el("p", "pv-alt", "To fix: " + v.repair));
      });
    } else {
      errs.textContent = (res.data && res.data.error) || "Those details were refused. Nothing was saved.";
    }
  });
  host.appendChild(msg); host.appendChild(formBox); host.appendChild(save); host.appendChild(errs);
}

async function paperChats(host, plan) {
  host.appendChild(el("h1", null, "VENDOR CHATS"));
  host.appendChild(el("p", "pv-alt", "The agent writes to vendors and reads their replies. Nothing leaves without your approval of that exact text."));
  const load = pvLoad(host);
  const r = await api(pvBase(plan) + "/outreach");
  if (!r.ok) return pvFail(load, r, "Vendor chats could not be loaded.");
  load.remove();
  const again = () => renderPaperView("vendorchats");
  if (state.paperFlash) { host.appendChild(el("p", "pv-lead", state.paperFlash)); state.paperFlash = null; }
  const items = r.data || [];
  if (!items.length) host.appendChild(el("p", "pv-alt", "No vendor messages drafted yet."));
  const STATUS = { blocked: "blocked: do not contact", prepared: "waiting for your approval", approved: "approved, sending soon", sending: "sending", sent: "sent", failed: "not sent", outcome_unknown: "unknown: check the chat", denied: "you declined", expired: "expired", marked_sent: "you sent it" };
  items.forEach(function (d) {
    const card = el("div", "pv-vendor");
    const head = el("div", "pv-vhead");
    head.appendChild(el("b", null, d.vendor_name + " - " + d.category));
    head.appendChild(el("span", "pv-chip " + (d.status === "prepared" ? "warn" : d.status === "sent" || d.status === "approved" ? "ok" : ""), STATUS[d.status] || d.status));
    card.appendChild(head);
    card.appendChild(el("div", "pv-alt", d.channel + " to " + d.recipient + (d.subject ? " - " + d.subject : "")));
    const bodyEl = el("div", "pv-quotebody", d.body);
    card.appendChild(bodyEl);
    if (d.notice && d.status === "prepared") card.appendChild(el("div", "pv-alt", d.notice));
    if (d.error) card.appendChild(el("div", "pv-bad", d.error));
    if (d.status === "prepared") {
      const acts = el("div", "pv-acts");
      const ap = el("button", "pv-btn small", "APPROVE THIS TEXT");
      ap.type = "button";
      ap.addEventListener("click", async function () {
        const res = await api(pvBase(plan) + "/outreach/" + d.draft_id + "/approve", { method: "POST", body: { content_hash: d.content_hash } });
        if (res.ok) again(); else card.appendChild(el("div", "pv-bad", (res.data && res.data.error) || "Could not approve."));
      });
      const dn = el("button", "pv-btn small", "DECLINE");
      dn.type = "button";
      dn.addEventListener("click", async function () {
        const res = await api(pvBase(plan) + "/outreach/" + d.draft_id + "/deny", { method: "POST", body: {} });
        if (res.ok) again(); else card.appendChild(el("div", "pv-bad", (res.data && res.data.error) || "Could not decline."));
      });
      acts.appendChild(ap); acts.appendChild(dn);
      card.appendChild(acts);
    }
    if (d.status === "sent") {
      /* Reply follow-ups (parity with the classic thread view): your own words
         or a suggested question; the draft still waits for your approval. */
      const ta = el("textarea", "pv-input");
      ta.maxLength = 1000; ta.rows = 2; ta.placeholder = "Your follow-up, in your own words";
      const sug = el("div", "pv-acts");
      const ferr = el("div", "pv-alt");
      const draftFollow = async function (body) {
        const nm = (state.paperSenderName || "").trim();
        if (!nm) { ferr.textContent = "Enter your name first: it is signed at the end of each message."; return; }
        const res = await api(pvBase(plan) + "/outreach/" + d.draft_id + "/followup", { method: "POST", body: { text: body, sender_name: nm } });
        if (res.ok) { state.paperFlash = "Follow-up drafted - it is waiting for your approval above. Nothing is sent until you approve the exact text."; again(); }
        else ferr.textContent = (res.data && res.data.error) || "That follow-up was refused.";
      };
      const fb = el("button", "pv-btn small", "DRAFT FOLLOW-UP"); fb.type = "button";
      fb.addEventListener("click", function () { if (ta.value.trim()) draftFollow(ta.value.trim()); });
      const sb = el("button", "pv-btn small", "SUGGEST FOLLOW-UPS"); sb.type = "button";
      sb.addEventListener("click", async function () {
        sug.textContent = "";
        const res = await api(pvBase(plan) + "/outreach/" + d.draft_id + "/suggestions");
        if (!res.ok || !res.data || !(res.data.suggestions || []).length) { sug.appendChild(el("span", "pv-alt", "Nothing to suggest until they reply.")); return; }
        res.data.suggestions.forEach(function (q) {
          const chip = el("button", "pv-btn small", q); chip.type = "button";
          chip.addEventListener("click", function () { draftFollow(q); });
          sug.appendChild(chip);
        });
      });
      const frow = el("div", "pv-acts"); frow.appendChild(fb); frow.appendChild(sb);
      card.appendChild(ta); card.appendChild(frow); card.appendChild(sug); card.appendChild(ferr);
    }
    host.appendChild(card);
  });
  host.appendChild(el("h2", null, "NEW QUOTE REQUEST"));
  host.appendChild(el("p", "pv-alt", "to;doh-the event planner writes the request text from your plan; it then waits for your approval above - nothing is sent."));
  const nf = { category: "caterer", channel: "whatsapp", recipient: "", sender_name: state.paperSenderName || (wallui.me && wallui.me.mark && wallui.me.mark.name) || "" };
  let line = el("div", "pv-formrow");
  line.appendChild(pvSelect(nf, "category", [["venue", "Venue"], ["caterer", "Caterer"], ["photographer", "Photographer"]], "Category"));
  line.appendChild(pvSelect(nf, "channel", [["whatsapp", "WhatsApp"], ["email", "Email"]], "Channel"));
  host.appendChild(line);
  line = el("div", "pv-formrow");
  line.appendChild(pvInput(nf, "recipient", "Vendor email, or WhatsApp digits with country code"));
  const nameIn = pvInput(nf, "sender_name", "Your name (signs the request)");
  nameIn.addEventListener("input", function () { state.paperSenderName = nf.sender_name; });
  line.appendChild(nameIn);
  host.appendChild(line);
  const nmsg = el("div", "pv-alt");
  const nerr = el("div", "pv-bad"); nerr.hidden = true;
  const draftBtn = el("button", "pv-btn", "DRAFT REQUEST"); draftBtn.type = "button";
  draftBtn.addEventListener("click", async function () {
    nerr.hidden = true; nmsg.textContent = "";
    if (!nf.recipient.trim() || !nf.sender_name.trim()) { nerr.hidden = false; nerr.textContent = "A recipient and your name are both needed."; return; }
    draftBtn.disabled = true;
    const res = await api(pvBase(plan) + "/outreach", { method: "POST", body: { category: nf.category, channel: nf.channel, recipient: nf.recipient.trim(), sender_name: nf.sender_name.trim() } });
    draftBtn.disabled = false;
    if (res.ok) { state.paperFlash = "Request drafted - it is waiting for your approval above. Nothing is sent until you approve the exact text."; again(); return; }
    nerr.hidden = false; nerr.textContent = (res.data && res.data.error) || "Could not draft that request.";
  });
  host.appendChild(draftBtn); host.appendChild(nmsg); host.appendChild(nerr);
  host.appendChild(el("h2", null, "EVERYONE WITH A SAVED CONTACT"));
  host.appendChild(el("p", "pv-alt", "Drafts one request per vendor with a saved contact. Each one still waits for your own approval."));
  const rall = el("button", "pv-btn small", "DRAFT REQUESTS FOR ALL"); rall.type = "button";
  rall.addEventListener("click", async function () {
    nerr.hidden = true;
    if (!nf.sender_name.trim()) { nerr.hidden = false; nerr.textContent = "Enter your name above first."; return; }
    rall.disabled = true;
    const res = await api(pvBase(plan) + "/outreach/request-all", { method: "POST", body: { sender_name: nf.sender_name.trim() } });
    rall.disabled = false;
    if (res.ok && res.data) {
      const sk = (res.data.skipped || []);
      state.paperFlash = "Drafted " + ((res.data.drafts || []).length) + " request(s), each waiting for your approval." + (sk.length ? " Skipped: " + sk.join("; ") + "." : "");
      again(); return;
    }
    nerr.hidden = false; nerr.textContent = (res.data && res.data.error) || "Could not draft requests.";
  });
  host.appendChild(rall);
  host.appendChild(el("h2", null, "SAVED CONTACTS"));
  host.appendChild(el("p", "pv-alt", "Where requests go for each vendor. Saved here, used for drafts - never contacted without your approval of the exact text."));
  const cr = await api("/api/contacts");
  const contacts = cr.ok ? (cr.data || []) : [];
  contacts.forEach(function (c) {
    host.appendChild(el("p", "pv-alt", c.vendor_id + ": " + (c.preferred === "email" ? "email " + (c.email || "-") : "whatsapp " + (c.whatsapp || "-")) + (c.preferred === "email" ? (c.whatsapp ? " · whatsapp " + c.whatsapp : "") : (c.email ? " · email " + c.email : ""))));
  });
  const nc = { vendor_id: "", email: "", whatsapp: "", preferred: "whatsapp" };
  line = el("div", "pv-formrow");
  line.appendChild(pvInput(nc, "vendor_id", "Vendor id (e.g. royal-rasoi)"));
  line.appendChild(pvInput(nc, "whatsapp", "WhatsApp digits with country code"));
  host.appendChild(line);
  line = el("div", "pv-formrow");
  line.appendChild(pvInput(nc, "email", "Email address"));
  line.appendChild(pvSelect(nc, "preferred", [["whatsapp", "Prefer WhatsApp"], ["email", "Prefer email"]], "Preferred channel"));
  host.appendChild(line);
  const cmsg = el("div", "pv-alt");
  const csave = el("button", "pv-btn small", "SAVE CONTACT"); csave.type = "button";
  csave.addEventListener("click", async function () {
    cmsg.textContent = "";
    if (!nc.vendor_id.trim() || (!nc.email.trim() && !nc.whatsapp.trim())) { cmsg.textContent = "A vendor id and at least one of email or WhatsApp are needed."; return; }
    csave.disabled = true;
    const res = await api("/api/contacts", { method: "POST", body: { vendor_id: nc.vendor_id.trim(), email: nc.email.trim() || null, whatsapp: nc.whatsapp.trim() || null, preferred: nc.preferred } });
    csave.disabled = false;
    if (res.ok) { state.paperFlash = "Contact saved for " + nc.vendor_id.trim() + "."; again(); return; }
    cmsg.textContent = (res.data && res.data.error) || "Could not save that contact.";
  });
  host.appendChild(csave); host.appendChild(cmsg);
  host.appendChild(el("h2", null, "REPLIES AND FOLLOW-UPS"));
  const pollBtn = el("button", "pv-btn small", "CHECK FOR REPLIES NOW"); pollBtn.type = "button";
  const pmsg = el("div", "pv-alt");
  pollBtn.addEventListener("click", async function () {
    pmsg.textContent = ""; pollBtn.disabled = true;
    const res = await api("/api/inbox/poll", { method: "POST", body: {} });
    if (!res.ok) { pollBtn.disabled = false; pmsg.textContent = (res.data && res.data.error) || "Could not check for replies."; return; }
    await api("/api/jobs/tick", { method: "POST", body: {} });
    const rr = res.data || {};
    pollBtn.disabled = false;
    state.paperFlash = (rr.new_messages || 0) + " new replies, " + (rr.sent || 0) + " sent." + ((rr.errors || []).length ? " " + rr.errors.join("; ") : "") + ((rr.waiting || []).length ? " Waiting: " + rr.waiting.join("; ") : "");
    again();
  });
  host.appendChild(pollBtn); host.appendChild(pmsg);

  /* Standing permission (ADR-043), parity with the classic UI: fixed follow-up
     questions only, bounded, revocable; the stop button is always visible. */
  const ar = await api(pvBase(plan) + "/autoreply");
  const ard = ar.ok ? ar.data : null;
  if (ard) {
    const am = ard.mandate || {};
    const abox = el("div", "pv-vendor");
    const ahead = el("div", "pv-vhead");
    ahead.appendChild(el("b", null, "AUTOMATIC FOLLOW-UP QUESTIONS"));
    ahead.appendChild(el("span", "pv-chip " + (ard.killed ? "" : ard.active ? "ok" : ""), ard.killed ? "stopped by the kill switch" : ard.active ? "on until " + fmtTime(am.expires_at) + " - " + (ard.sent_total || 0) + " of " + (am.max_total || 0) + " used" : "off"));
    abox.appendChild(ahead);
    abox.appendChild(el("p", "pv-alt", "When on, the agent may answer a vendor's reply with one fixed question about something still missing (price, what is included, your date, how long the quote is valid) without asking you each time. It never offers or accepts a price, never mentions your budget, never answers a question they asked you, and hands the chat to you if they refuse, ask you something, send a file or ask to stop. It sends only between 09:00 and 20:00 IST and every message says it was automatic."));
    const aerr = el("div", "pv-alt");
    const arow = el("div", "pv-acts");
    if (ard.active) {
      const off = el("button", "pv-btn small", "TURN OFF FOR THIS PLAN"); off.type = "button";
      off.addEventListener("click", async function () { const res = await api(pvBase(plan) + "/autoreply/revoke", { method: "POST", body: {} }); if (res.ok) again(); else aerr.textContent = (res.data && res.data.error) || "Could not turn that off."; });
      arow.appendChild(off);
    } else {
      const hours = el("select", "pv-input");
      [[24, "for 24 hours"], [72, "for 3 days"], [168, "for 7 days"]].forEach(function (p) { const o = el("option", null, p[1]); o.value = p[0]; hours.appendChild(o); });
      const perThread = el("select", "pv-input");
      [1, 2, 3, 5].forEach(function (n) { const o = el("option", null, "up to " + n + " per vendor"); o.value = n; if (n === 3) o.selected = true; perThread.appendChild(o); });
      const onBtn = el("button", "pv-btn small", "ALLOW AUTOMATIC FOLLOW-UP QUESTIONS"); onBtn.type = "button";
      onBtn.addEventListener("click", async function () {
        const nm = (state.paperSenderName || "").trim();
        if (!nm) { aerr.textContent = "Enter your name first: it is signed at the end of each message."; return; }
        const res = await api(pvBase(plan) + "/autoreply", { method: "POST", body: { sender_name: nm, hours: parseInt(hours.value, 10), max_per_thread: parseInt(perThread.value, 10), max_total: 10 } });
        if (res.ok) again(); else aerr.textContent = (res.data && res.data.error) || "Could not turn that on.";
      });
      arow.appendChild(hours); arow.appendChild(perThread); arow.appendChild(onBtn);
    }
    if (ard.killed) {
      const lift = el("button", "pv-btn small", "LIFT THE STOP"); lift.type = "button";
      lift.addEventListener("click", async function () { const res = await api("/api/autoreply/resume", { method: "POST", body: {} }); if (res.ok) again(); else aerr.textContent = (res.data && res.data.error) || "Could not lift it."; });
      arow.appendChild(lift);
    } else {
      const kill = el("button", "pv-btn small", "STOP ALL AUTOMATIC MESSAGES NOW"); kill.type = "button";
      kill.addEventListener("click", async function () { const res = await api("/api/autoreply/kill", { method: "POST", body: {} }); if (res.ok) { state.paperFlash = "Every automatic message is stopped, including queued ones."; again(); } else aerr.textContent = (res.data && res.data.error) || "Could not stop them."; });
      arow.appendChild(kill);
    }
    abox.appendChild(arow); abox.appendChild(aerr);
    host.appendChild(abox);
  } else {
    host.appendChild(el("p", "pv-alt", "The follow-up permission state could not be loaded."));
  }
}

async function paperChanges(host, plan) {
  host.appendChild(el("h1", null, "CHANGES"));
  host.appendChild(el("p", "pv-alt", "Every change writes a new read-only version. Earlier versions are never edited. Pick a row to see what that version changed."));
  if (state.paperFlash) { host.appendChild(el("p", "pv-lead", state.paperFlash)); state.paperFlash = null; }
  const load = pvLoad(host);
  const r = await api(pvBase(plan) + "/versions");
  if (!r.ok) return pvFail(load, r, "Version history could not be loaded.");
  load.remove();
  const vers = r.data || [];
  if (!vers.length) { host.appendChild(el("p", "pv-alt", "No versions yet.")); return; }
  const tbl = el("table", "pv-table");
  host.appendChild(tbl);
  host.appendChild(el("h2", null, "WHAT CHANGED"));
  const dbox = el("div", null);
  host.appendChild(dbox);
  let diffSeq = 0;
  async function showDiff(v) {
    const my = ++diffSeq;
    Array.prototype.forEach.call(tbl.querySelectorAll("tr"), function (tr) { tr.classList.toggle("cur", Number(tr.dataset.ver) === v.version); });
    dbox.textContent = "";
    if (!v.parent_version) { dbox.appendChild(el("p", "pv-alt", "Version " + v.version + " is the first version: nothing to compare it with yet.")); return; }
    dbox.appendChild(el("p", "pv-alt", "loading diff..."));
    const dr = await api(pvBase(plan) + "/diff?old=" + v.parent_version + "&new=" + v.version);
    if (my !== diffSeq) return;
    dbox.textContent = "";
    if (!dr.ok) { dbox.appendChild(el("p", "pv-bad", "Could not load the diff.")); return; }
    dbox.appendChild(el("p", "pv-alt", "Version " + v.parent_version + " to version " + v.version));
    const d = dr.data || {};
    [["added", "ADDED"], ["removed", "REMOVED"], ["changed", "CHANGED"], ["preserved", "PRESERVED"]].forEach(function (pair) {
      const items = d[pair[0]] || [];
      dbox.appendChild(el("h2", null, pair[1] + " (" + items.length + ")"));
      if (!items.length) { dbox.appendChild(el("p", "pv-alt", "none")); return; }
      const ul = el("ul", "pv-difflist");
      items.forEach(function (i) {
        const li = el("li", null);
        li.appendChild(el("b", null, i.kind + " \u00b7 " + String(i.key || "").replace(/_/g, " ") + " "));
        li.appendChild(document.createTextNode(pair[0] === "changed" ? (i.before || "-") + " -> " + (i.after || "-") : (i.before || i.after || "")));
        if (i.note) li.appendChild(el("span", "pv-alt", " " + i.note));
        ul.appendChild(li);
      });
      dbox.appendChild(ul);
    });
  }
  vers.slice().reverse().forEach(function (v) {
    const tr = el("tr", "pv-verrow");
    tr.dataset.ver = v.version;
    tr.title = "Show what changed in version " + v.version;
    tr.appendChild(el("td", null, "v" + v.version + (v.version === plan.version ? " (current)" : "")));
    tr.appendChild(el("td", null, v.change_reason));
    tr.appendChild(el("td", null, fmtTime(v.created_at)));
    tr.appendChild(el("td", "num", fmtINR(v.total_cost)));
    tr.addEventListener("click", function () { showDiff(v); });
    tbl.appendChild(tr);
  });
  const cur = vers.filter(function (v) { return v.version === plan.version; })[0] || vers[vers.length - 1];
  showDiff(cur);
  paperDisruption(host, plan);
}

/* Disruption / replan, parity with the classic UI openReplan dialog. Every
   replan writes a new version; earlier versions stay read-only. A 409 means a
   confirmed booking blocks the change and only the user can release it. */
function paperDisruption(host, plan) {
  host.appendChild(el("h2", null, "REPORT A DISRUPTION"));
  host.appendChild(el("p", "pv-alt", "Replans against current data and writes a new version. Confirmed bookings are kept unless you release them; the previous version stays valid if no new plan passes."));
  const KINDS = [["budget_change", "Budget changed"], ["vendor_cancellation", "A vendor cancelled"], ["guest_count_change", "Guest count changed"], ["date_change", "Date changed"]];
  const st = { kind: "budget_change", override: [] };
  const krow = el("div", "pv-acts");
  KINDS.forEach(function (p) {
    const b = el("button", "pv-btn small", p[1].toUpperCase());
    b.type = "button";
    b.addEventListener("click", function () {
      st.kind = p[0];
      Array.prototype.forEach.call(krow.children, function (x) { x.classList.remove("on"); });
      b.classList.add("on");
      drawFields();
    });
    if (p[0] === st.kind) b.classList.add("on");
    krow.appendChild(b);
  });
  host.appendChild(krow);
  const fbox = el("div", null);
  host.appendChild(fbox);
  const obox = el("div", null);
  host.appendChild(obox);
  const rmsg = el("div", "pv-alt");
  const rerr = el("div", "pv-bad");
  const submit = el("button", "pv-btn", "REPLAN");
  submit.type = "button";
  function fieldVal() {
    if (st.kind === "vendor_cancellation") { const v = fbox.querySelector("select").value; if (!v || v === "undefined" || v === "null") return { vendor_id: "" }; return { vendor_id: v }; }
    if (st.kind === "budget_change") return { new_budget: parseInt(fbox.querySelector("input").value, 10) };
    if (st.kind === "guest_count_change") return { new_guest_count: parseInt(fbox.querySelector("input").value, 10) };
    return { new_date: fbox.querySelector("input").value };
  }
  function drawFields() {
    fbox.textContent = "";
    const r = plan.request || {};
    if (st.kind === "vendor_cancellation") {
      const sel = el("select", "pv-input");
      (plan.selections || []).filter(function (x) { return !!x.vendor_id; }).forEach(function (x) {
        const o = el("option", null, x.category + ": " + x.name + (x.booking_status === "confirmed" ? " (confirmed)" : ""));
        o.value = x.vendor_id; sel.appendChild(o);
      });
      fbox.appendChild(sel);
      if (!sel.options.length) fbox.appendChild(el("p", "pv-alt", "No vendor with a backend id is selected in this plan, so a cancellation cannot be filed from here."));
    } else if (st.kind === "budget_change") {
      const i = el("input", "pv-input"); i.type = "number"; i.min = "1"; i.step = "1"; i.value = String(r.budget_total || "");
      fbox.appendChild(i);
    } else if (st.kind === "guest_count_change") {
      const i = el("input", "pv-input"); i.type = "number"; i.min = "1"; i.max = "100000"; i.step = "1"; i.value = String(r.guest_count || "");
      fbox.appendChild(i);
    } else {
      const i = el("input", "pv-input"); i.type = "date"; i.value = r.event_date || "";
      fbox.appendChild(i);
    }
    obox.textContent = "";
    const confirmed = (plan.selections || []).filter(function (x) { return x.booking_status === "confirmed"; });
    if (confirmed.length) {
      obox.appendChild(el("p", "pv-alt", "Confirmed bookings are kept unless you release them:"));
      confirmed.forEach(function (x) {
        const lab = el("label", "pv-alt");
        const cb = el("input", null); cb.type = "checkbox"; cb.value = x.vendor_id;
        cb.checked = st.override.indexOf(x.vendor_id) >= 0;
        cb.addEventListener("change", function () {
          st.override = cb.checked ? st.override.concat([x.vendor_id]) : st.override.filter(function (v) { return v !== x.vendor_id; });
        });
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(" Release " + x.name));
        obox.appendChild(lab);
      });
    }
  }
  drawFields();
  submit.addEventListener("click", async function () {
    rmsg.textContent = ""; rerr.textContent = "";
    submit.disabled = true; submit.textContent = "REPLANNING...";
    const body = Object.assign({ kind: st.kind, override_confirmed: st.override }, fieldVal());
    if (st.kind === "vendor_cancellation" && !body.vendor_id) { rerr.textContent = "Pick the vendor that cancelled first."; submit.disabled = false; submit.textContent = "REPLAN"; return; }
    if ((st.kind === "budget_change" && !(body.new_budget > 0)) || (st.kind === "guest_count_change" && !(body.new_guest_count > 0)) || (st.kind === "date_change" && !body.new_date)) { rerr.textContent = "Enter the new value first."; submit.disabled = false; submit.textContent = "REPLAN"; return; }
    const res = await api(pvBase(plan) + "/replan", { method: "POST", body });
    submit.disabled = false; submit.textContent = "REPLAN";
    if (res.ok && res.data && res.data.plan) {
      state.lastPayload = { plan: res.data.plan, reality: (state.lastPayload && state.lastPayload.reality) || null };
      state.paperFlash = "Replanned: version " + res.data.plan.version + ". Earlier versions are untouched.";
      renderDossier(state.lastPayload);
      renderPaperView("changes");
      return;
    }
    if (res.status === 409 && res.data) {
      rerr.textContent = "This needs your decision. Nothing was changed:";
      (res.data.violations || []).forEach(function (v) { rerr.appendChild(el("p", null, (v.constraint || "check") + ": " + (v.detail || ""))); });
      const ids = (res.data.violations || []).map(function (v) { const m = /override_confirmed=\['([A-Za-z0-9_-]+)'\]/.exec(v.repair || ""); return m && m[1]; }).filter(Boolean);
      if (ids.length) {
        const rel = el("button", "pv-btn small", "RELEASE THE CONFIRMED BOOKING AND REPLAN");
        rel.type = "button";
        rel.addEventListener("click", function () {
          st.override = [...new Set(st.override.concat(ids))];
          drawFields(); submit.click();
        });
        rerr.appendChild(rel);
      }
      return;
    }
    const vs = (res.data && (res.data.violations || res.data.details)) || [];
    if (vs.length) {
      rerr.textContent = "No valid plan after that change. The previous version is still valid:";
      vs.forEach(function (v) { rerr.appendChild(el("p", null, (v.constraint || v.field || "check") + ": " + (v.detail || v.message || ""))); });
    } else {
      rerr.textContent = (res.data && res.data.error) || "Something went wrong.";
    }
  });
  host.appendChild(submit); host.appendChild(rmsg); host.appendChild(rerr);
}

async function paperAudit(host, plan) {
  host.appendChild(el("h1", null, "AUDIT LOG"));
  host.appendChild(el("p", "pv-alt", "Every material decision with alternatives, constraints checked, justification, confidence and source."));
  const load = pvLoad(host);
  const r = await api(pvBase(plan) + "/decisions?version=" + plan.version);
  if (!r.ok) return pvFail(load, r, "The decision log could not be loaded.");
  load.remove();
  const entries = r.data || [];
  if (!entries.length) { host.appendChild(el("p", "pv-alt", "No entries recorded for this version.")); return; }
  const filterRow = el("div", "pv-acts");
  host.appendChild(filterRow);
  const list = el("div", null);
  host.appendChild(list);
  const agents = ["all"].concat(Array.from(new Set(entries.map(function (e) { return e.agent; }))));
  const draw = function (agent) {
    filterRow.textContent = "";
    agents.forEach(function (a) {
      const c = el("button", "pv-btn small" + (a === agent ? " cur" : ""), a);
      c.type = "button";
      c.addEventListener("click", function () { draw(a); });
      filterRow.appendChild(c);
    });
    list.textContent = "";
    entries.filter(function (e) { return agent === "all" || e.agent === agent; }).forEach(function (e) {
      const card = el("div", "pv-vendor");
      const head = el("div", "pv-vhead");
      head.appendChild(el("b", null, e.summary));
      head.appendChild(el("span", "pv-chip " + (e.decision_type === "failure" ? "warn" : "ok"), e.agent + " · " + e.decision_type + " · " + Math.round((e.confidence || 0) * 100) + "%"));
      card.appendChild(head);
      card.appendChild(el("div", "pv-alt", e.justification + " (source: " + e.source + ")"));
      if (e.alternatives && e.alternatives.length) {
        const det = el("details", null);
        det.appendChild(el("summary", null, "Alternatives (" + e.alternatives.length + ")"));
        const ul = el("ul", "pv-difflist");
        e.alternatives.forEach(function (a) { ul.appendChild(el("li", null, a.option + " - " + a.outcome + ": " + a.reason_not_chosen)); });
        det.appendChild(ul);
        card.appendChild(det);
      } else if (e.no_alternative_reason) card.appendChild(el("div", "pv-alt", "No alternative: " + e.no_alternative_reason));
      if (e.constraints_checked && e.constraints_checked.length) {
        const failed = e.constraints_checked.filter(function (c) { return !c.passed; }).length;
        const det = el("details", null);
        det.appendChild(el("summary", null, "Constraints checked (" + e.constraints_checked.length + (failed ? ", " + failed + " failed" : "") + ")"));
        const ul = el("ul", "pv-checks");
        e.constraints_checked.forEach(function (c) { ul.appendChild(el("li", c.passed ? "pass" : "fail", c.name + " - " + c.detail)); });
        det.appendChild(ul);
        card.appendChild(det);
      }
      list.appendChild(card);
    });
  };
  draw("all");
}

function paperPending(host, view) {
  const label = (PAPER_TABS.find(function (t) { return t[0] === view; }) || [view, view])[1];
  host.appendChild(el("h1", null, label));
  host.appendChild(el("div", "ribbon", "NOT AVAILABLE IN THIS PREVIEW YET"));
  host.appendChild(el("p", null, "This part of the paper is on its way. It will hold " + (PAPER_PENDING[view] || "plan detail") + " - nothing is staged or faked here in the meantime."));
}


function renderDashboard(payload) {
  const plan = payload.plan;
  const inner = $("#paper-inner");
  inner.textContent = "";
  inner.appendChild(sheetNav("dashboard"));
  inner.appendChild(el("h1", null, "DASHBOARD"));
  inner.appendChild(el("div", "sub",
    plan.plan_id + " · v" + plan.version + " — read straight from the stored plan record the dossier prints. Nothing booked, sent or paid."));

  const pages = [];
  pages.push({ name: "SUMMARY", build: (host) => {
    const facts = el("div", "factrow");
    const addFact = (k, v) => {
      const s = el("span");
      s.appendChild(el("b", null, k));
      s.appendChild(document.createTextNode(String(v)));
      facts.appendChild(s);
    };
    addFact("EVENT", String(plan.request.event_type).replace(/_/g, " "));
    addFact("CITY", plan.request.city);
    addFact("DATE", plan.request.event_date);
    addFact("GUESTS", plan.guests.planning_headcount);
    addFact("BUDGET", fmtINR(plan.allocation.total));
    host.appendChild(facts);
    const v = plan.validation;
    const passed = v.checks.filter((c) => c.passed).length;
    host.appendChild(el("p", null, passed + " of " + v.checks.length + " hard checks passed. Symbolic (PDDL) check: " + (plan.symbolic ? plan.symbolic.status : "unknown") + "."));
    const blocking = (v.unverified || []).filter((u) => u.blocks_verified !== false);
    if (blocking.length) {
      host.appendChild(el("div", "ribbon", "Needs verification: " + blocking.map((u) => u.label || (u.name ? String(u.name).replace(/_/g, " ") : "this item")).join("; ")));
    }
    if (payload.reality) {
      host.appendChild(el("p", null, "Budget reality check: " + payload.reality.verdict + " — " + payload.reality.message));
    }
    if (plan.request.assumptions && plan.request.assumptions.length) {
      host.appendChild(el("h2", null, "ASSUMPTIONS"));
      const ul = el("ul");
      plan.request.assumptions.forEach((a) => ul.appendChild(el("li", null, a)));
      host.appendChild(ul);
    }
  }});
  if (plan.selections && plan.selections.length) pages.push({ name: "VENDORS", build: (host) => {
    const chk = (plan.validation && plan.validation.checks) || [];
    const passed = chk.filter((c) => c.passed).length;
    const verdict = chk.length && passed === chk.length
      ? "highest-scoring eligible combination passing every hard constraint"
      : chk.length
        ? "highest-scoring eligible combination from the solver - " + passed + " of " + chk.length + " hard checks passed"
        : "lowest-cost combination from the solver - validation record not present in this plan";
    host.appendChild(el("h2", null, plan.selections.length + " selected vendor(s) — " + verdict));
    const vt = el("table");
    vt.appendChild((() => { const tr = el("tr");
      ["category", "vendor", "cost", "status"].forEach((h, i) => tr.appendChild(el("th", i === 2 ? "num" : null, h)));
      return tr; })());
    for (const sel of plan.selections) {
      const tr = el("tr");
      tr.appendChild(el("td", null, sel.category));
      tr.appendChild(el("td", null, sel.name));
      tr.appendChild(el("td", "num", fmtINR(sel.cost)));
      tr.appendChild(el("td", null, sel.booking_status));
      vt.appendChild(tr);
    }
    host.appendChild(vt);
  }});
  if (plan.allocation && plan.allocation.lines && plan.allocation.lines.length) pages.push({ name: "BUDGET", build: (host) => {
    host.appendChild(el("h2", null, "BUDGET — exact split, sums to the rupee"));
    const bt = el("table");
    for (const line of plan.allocation.lines) {
      const tr = el("tr");
      tr.appendChild(el("td", null, line.category));
      tr.appendChild(el("td", "num", fmtINR(line.amount)));
      bt.appendChild(tr);
    }
    const ctr = el("tr");
    ctr.appendChild(el("td", null, "contingency"));
    ctr.appendChild(el("td", "num", fmtINR(plan.allocation.contingency)));
    bt.appendChild(ctr);
    const ttr = el("tr");
    ttr.appendChild(el("td", null, "selected vendor cost (estimate - nothing committed)"));
    ttr.appendChild(el("td", "num", fmtINR(plan.total_cost)));
    bt.appendChild(ttr);
    host.appendChild(bt);
  }});
  if (plan.items && plan.items.length) pages.push({ name: "SCHEDULE", build: (host) => {
    host.appendChild(el("h2", null, plan.items.length + " scheduled item(s) — CP-SAT solved, event-local times"));
    const st = el("table");
    for (const it of plan.items) {
      const tr = el("tr");
      tr.appendChild(el("td", null, fmtTime(it.start)));
      tr.appendChild(el("td", null, fmtTime(it.end)));
      tr.appendChild(el("td", null, it.name));
      st.appendChild(tr);
    }
    host.appendChild(st);
  }});

  const paper = $("#paper");
  let cur = parseInt(paper.dataset.dashPage || "0", 10);
  if (isNaN(cur) || cur < 0) cur = 0;
  if (cur >= pages.length) cur = pages.length - 1;
  paper.dataset.dashPage = String(cur);

  const host = el("div", "dash-host");
  inner.appendChild(host);
  pages[cur].build(host);
  host.insertBefore(el("div", "dash-head", "page " + (cur + 1) + " of " + pages.length + " — " + pages[cur].name), host.firstChild);

  const pager = el("div", "dash-pager");
  const prev = el("button", "dash-nav", "\u2190 Previous");
  prev.type = "button"; prev.dataset.step = "-1"; prev.disabled = cur === 0;
  pager.appendChild(prev);
  pages.forEach((p, i) => {
    const b = el("button", "dash-page" + (i === cur ? " cur" : ""), p.name);
    b.type = "button"; b.dataset.page = String(i);
    if (i === cur) b.setAttribute("aria-current", "page");
    pager.appendChild(b);
  });
  const next = el("button", "dash-nav", "Next \u2192");
  next.type = "button"; next.dataset.step = "1"; next.disabled = cur === pages.length - 1;
  pager.appendChild(next);
  inner.appendChild(pager);

  const close = el("button", "paper-close", "BACK TO THE FLOOR");
  close.type = "button";
  close.addEventListener("click", hidePaper);
  inner.appendChild(close);
}

$("#paper").addEventListener("click", (ev) => {
  const t = ev.target.closest("button");
  if (!t || !state.lastPayload) return;
  if (t.dataset.view) renderPaperView(t.dataset.view);
});

/* Trade-offs (parity with the classic UI renderTradeoffs): only options the
   backend already built and validated as full plans; applying one is the user's
   explicit click, and the new plan version comes straight from the reply. */
function paperTradeoffs(holder, report, body) {
  if (report.status !== "options") {
    holder.appendChild(el("p", "pv-bad", report.status === "not_needed"
      ? "This request plans fine; nothing to trade off."
      : "No verified trade-off exists: " + ((report && report.violations) || []).map(function (v) { return (v.detail || "") + " " + (v.repair || ""); }).join(" ")));
    return;
  }
  holder.appendChild(el("h2", null, "VERIFIED TRADE-OFFS"));
  holder.appendChild(el("p", "pv-alt", "Ranked by least harm to your preferences and least extra spend. Each one was built and validated as a full plan; nothing is applied until you choose."));
  (report.options || []).forEach(function (o) {
    const harm = Math.round((o.preference_harm || 0) * 100);
    const card = el("div", "pv-vendor");
    card.appendChild(el("b", null, o.title));
    card.appendChild(el("p", "pv-alt", o.detail));
    const badges = el("div", "pv-acts");
    badges.appendChild(el("span", "pv-chip " + (harm >= 50 ? "" : harm >= 15 ? "warn" : "ok"), "Preference impact " + harm + "%"));
    badges.appendChild(el("span", "pv-chip " + (o.extra_spend ? "warn" : "ok"), o.extra_spend ? "Costs " + fmtINR(o.extra_spend) + " more" : "No extra spend"));
    badges.appendChild(el("span", "pv-chip " + ((o.needs_verification && o.needs_verification.length) ? "warn" : "ok"),
      (o.needs_verification && o.needs_verification.length) ? "Plan built, needs verification: " + o.needs_verification.join("; ") : "Verified: full plan built"));
    card.appendChild(badges);
    const ap = el("button", "pv-btn", "APPLY THIS OPTION");
    ap.type = "button";
    ap.addEventListener("click", async function () {
      ap.disabled = true; ap.textContent = "PLANNING...";
      const payload = { option: o };
      if (body.guest_list_id) payload.guest_list_id = body.guest_list_id;
      const r = await api("/api/tradeoffs/apply", { method: "POST", body: payload });
      if (r.status === 201 && r.data && r.data.plan) {
        state.lastPayload = { plan: r.data.plan, reality: null };
        renderDossier(state.lastPayload);
      } else {
        ap.disabled = false; ap.textContent = "APPLY THIS OPTION";
        card.appendChild(el("p", "pv-bad", (r.data && (r.data.error || (r.data.violations && r.data.violations[0] && r.data.violations[0].detail))) || "That option no longer plans."));
      }
    });
    card.appendChild(ap);
    holder.appendChild(card);
  });
}

function renderJam(payload, httpStatus) {
  const inner = $("#paper-inner");
  inner.textContent = "";
  const paper = $("#paper");
  state.lastPayload = null;
  paper.classList.add("jammed");
  const oldStamp = paper.querySelector(".stamp");
  if (oldStamp) oldStamp.remove();

  inner.appendChild(el("h1", null, "PAPER JAM"));
  paper.insertBefore(el("div", "stamp jam", "NO PLAN"), paper.firstChild);
  inner.appendChild(el("div", "sub", "The machine refused to print — it could not make this brief pass the hard checks. Nothing was stored."));

  if (payload && payload.violations && payload.violations.length) {
    inner.appendChild(el("h2", null, "WHAT BLOCKED IT"));
    const ul = el("ul", "badlist");
    for (const v of payload.violations) {
      const li = el("li", null, v.constraint + ": " + v.detail);
      if (v.repair) li.appendChild(el("span", "repair", " → fix: " + v.repair));
      ul.appendChild(li);
    }
    inner.appendChild(ul);
  } else {
    inner.appendChild(el("p", null, "The server returned status " + httpStatus + " with no violations listed."));
  }
  if (payload && payload.reality && payload.reality.message) {
    inner.appendChild(el("p", null, "Budget reality: " + payload.reality.message));
  }
  inner.appendChild(el("p", null, "Rephrase or relax the brief in chat and drop another coin when it is ready."));

  const toBox = el("div", null);
  const toBtn = el("button", "pv-btn", "SHOW VERIFIED TRADE-OFFS");
  toBtn.type = "button";
  toBtn.title = "Each option is built and validated as a full plan before it is shown; nothing changes until you pick one.";
  toBtn.addEventListener("click", async function () {
    toBtn.disabled = true; toBtn.textContent = "CHECKING EVERY OPTION AGAINST THE FULL PIPELINE...";
    const body = { request: (state.messages || []).join("\n") };
    if (state.guestListId) body.guest_list_id = state.guestListId;
    const r = await api("/api/tradeoffs", { method: "POST", body });
    toBtn.remove();
    if (!r.ok) { toBox.appendChild(el("p", "pv-bad", (r.data && r.data.error) || "Could not compute trade-offs.")); return; }
    paperTradeoffs(toBox, r.data, body);
  });
  inner.appendChild(toBtn);
  inner.appendChild(toBox);

  const close = el("button", "paper-close", "BACK TO THE FLOOR");
  close.type = "button";
  close.addEventListener("click", hidePaper);
  inner.appendChild(close);

  showPaper(true);
}

/* The 3D sheet has already flown to the glass; the readable page is the press. */
function showPaper(jammed) {
  const layer = $("#flight-layer");
  const paper = $("#paper");
  layer.hidden = false;
  paper.classList.remove("stuck");
  void paper.offsetWidth; // restart animation
  paper.classList.add("stuck");
  Arcade3D.hideSheets();
}

function syncChatToggle() {
  const b = $("#chat-toggle"); if (!b) return;
  const c = $("#chat").classList.contains("collapsed");
  b.setAttribute("aria-pressed", c ? "true" : "false");
  b.title = c ? "show the conversation" : "hide the conversation to see the whole room";
}

function hidePaper() {
  $("#flight-layer").hidden = true;
  if (state.chatWasOpen) {
    $("#chat").classList.remove("collapsed");
    syncChatToggle();
  }
  Arcade3D.hideSheets();
  Arcade3D.resetWorkflow();
  Arcade3D.setCRT("INSERT COIN");
}

/* ---------------------------------------------------------------- run */
async function build() {
  if (state.running || !state.draft) return;
  state.running = true;
  $("#build").disabled = true;
  $("#composer").querySelector("button").disabled = true;

  const chat = $("#chat");

  state.chatWasOpen = !chat.classList.contains("collapsed");
  if (state.chatWasOpen) {
    chat.classList.add("collapsed");
    syncChatToggle();
  }

  Arcade3D.userGesture(); // audio starts here, on the user's own click
  await Arcade3D.coinInsert();
  await Arcade3D.zoomIn(); // the cabinet screen becomes the main focus
  setCaption("LIVE · modules light as the real pipeline runs");

  startPolling();
  const r = await api("/api/intake/confirm", {
    method: "POST",
    body: JSON.stringify({
      draft: state.draft,
      raw_text: state.messages.join("\n"),
      source: "rule:parser",
    }),
  });
  stopPolling();

  let snap = null;
  try { snap = (await api("/api/arcade/pulse")).data; } catch (e) { /* replay skipped */ }

  setCaption("");
  await replayRun(snap);

  const ok = !!(r.ok && r.data && r.data.plan);
  Arcade3D.setCRT(ok ? "PLAN READY" : "JAMMED");
  await Arcade3D.finishRun(ok); // pull back, shudder + cogs, sheets drop, paper flies

  if (ok) {
    renderDossier(r.data);
  } else {
    renderJam(r.data, r.status);
  }

  state.running = false;
  $("#build").disabled = false;
  $("#composer").querySelector("button").disabled = false;
}

/* ---------------------------------------------------------------- boot */
/* ---------------------------------------------------------------- wall
   Signature-wall + Hall of Fame client, contract 8.6 (agreed, backend
   not built yet). Read-only this pass: bootstrap, meta/hall polling and
   the holographic table reveal. All writes (marks, rounds, hall opt-in)
   land with the wall UI pass. Honest states: 404/501 = wall backend
   absent, the table simply never appears; a 401 re-bootstraps on the
   next poll, never auto-resends. */
const wall = {
  available: null, // null = not probed yet
  csrf: null,
  metaEtag: null,
  hallEtag: null,
  meta: null,
  pollMs: 45000,
};

async function wallGet(path, etag) {
  const headers = { "Accept": "application/json" };
  if (etag) headers["If-None-Match"] = etag;
  const res = await fetch(path, { headers });
  if (res.status === 304) return { status: 304, data: null, etag };
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  return { status: res.status, data, etag: res.headers.get("ETag") || etag };
}

async function wallBootstrap() {
  try {
    const r = await wallGet("/api/wall/session");
    if (r.status === 404 || r.status === 501) { wall.available = false; return false; }
    if (r.status !== 200 || !r.data) { wall.available = false; return false; }
    wall.available = true;
    wall.csrf = r.data.csrf || null; // X-EventMate-Wall-CSRF on wall writes (wall UI pass)
    return true;
  } catch (e) { wall.available = false; return false; }
}

function wallFmtMs(ms) {
  if (ms == null) return "";
  const m = Math.floor(ms / 60000), sec = Math.floor((ms % 60000) / 1000), pad = String(ms % 1000).padStart(3, "0");
  return m > 0 ? m + ":" + String(sec).padStart(2, "0") + "." + pad : sec + "." + pad + "s";
}

function renderHallList(entries) {
  const ol = $("#hall-list");
  if (!ol) return;
  ol.textContent = "";
  const per = wall.meta && wall.meta.hall_period;
  if (per && per.ends_at) {
    const cap = document.createElement("li");
    cap.textContent = "Ranked times reset " + new Date(per.ends_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }) + ". Wall names are permanent.";
    ol.appendChild(cap);
  }
  if (!entries.length) {
    const li = document.createElement("li");
    li.textContent = "Hall of Fame unlocked. No ranked runs yet - beat the wall challenge to appear.";
    ol.appendChild(li);
    return;
  }
  entries.forEach(function (r, i) {
    const li = document.createElement("li");
    li.textContent = (r.rank != null ? r.rank : i + 1) + ". " + (r.name || "?") + " - " + wallFmtMs(r.elapsed_ms);
    ol.appendChild(li);
  });
}

async function wallPollHall() {
  const r = await wallGet("/api/wall/hall?order=fastest&limit=5", wall.hallEtag);
  if (r.status === 304) return;
  if (r.status !== 200 || !r.data || !Array.isArray(r.data.entries)) return;
  wall.hallEtag = r.etag;
  Arcade3D.holoSetHall(r.data.entries);
  renderHallList(r.data.entries);
}

async function wallPollMeta() {
  const r = await wallGet("/api/wall/meta", wall.metaEtag);
  if (r.status === 401) { wall.csrf = null; wall.available = null; scheduleWall(1000); return; } // re-bootstrap next tick, never resend
  if (r.status === 304) { scheduleWall(); return; }
  if (r.status !== 200 || !r.data) { scheduleWall(); return; }
  wall.metaEtag = r.etag;
  wall.meta = r.data;
  Arcade3D.holoSetPeriod(r.data.hall_period || null);
  const unlocked = !!r.data.hall_unlocked;
  Arcade3D.holoSetUnlocked(unlocked);
  if (unlocked) await wallPollHall();
  if (!wallui.round && !document.body.classList.contains("wall-focus")) wallRefresh(); // the board mirrors live state even before the first visit
  scheduleWall();
}

function scheduleWall(ms) {
  setTimeout(wallTick, ms || wall.pollMs);
}

async function wallTick() {
  if (document.hidden) { scheduleWall(); return; }
  if (wall.available === null) {
    const ok = await wallBootstrap();
    if (!ok) { wall.available = null; scheduleWall(wall.pollMs * 4); return; } // backend absent: retry rarely, stay silent
  }
  await wallPollMeta();
}


/* ---------------------------------------------------------------- wall UI
   The signature wall's interactive pass (contract 8.6, agreed). Focus the
   left wall: sign once (permanent), play the return challenge (a name
   secretly moves - find where), earn the Hall opt-in on a found round.
   The round view is drawn ONLY from the round's frozen snapshot; a 409
   round_stale offers a one-tap NEW ROUND (never a silent reissue); a wall
   401 re-bootstraps and shows a retry choice (never an automatic resend).
   Backend absent: the board shows its honest sleeping state and the panel
   says so plainly. */
const wallui = {
  me: null,
  sectionId: 0,
  section: null,
  round: null,        // the issued round response (holds the frozen snapshot)
  phase: "idle",      // idle | issued | go | done
  skewMs: 0,          // server clock offset for the countdown
  deadlineLocal: 0,
  timer: null,
};

async function wallPost(path, body, idem) {
  const headers = { "Content-Type": "application/json", "Accept": "application/json" };
  if (wall.csrf) headers["X-EventMate-Wall-CSRF"] = wall.csrf;
  if (idem) headers["Idempotency-Key"] = idem;
  try {
    const res = await fetch(path, { method: "POST", headers, body: JSON.stringify(body || {}) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (res.status === 401) { // stale cookie/CSRF: re-bootstrap, human retries
      wall.csrf = null; wall.available = null;
      await wallBootstrap();
      return { status: 401, data: { error: "session refreshed - tap again to retry", code: "unauthorized" } };
    }
    return { status: res.status, data };
  } catch (e) {
    return { status: 0, data: { error: "no connection to the wall" } };
  }
}

function wallLine(txt) { const el = $("#wall-status"); if (el) el.textContent = txt || ""; }

function wallSolved() { try { return sessionStorage.getItem("em_wall_solved") === "1"; } catch (e) { return false; } }
Arcade3D.onWallSolve(function () {
  try { sessionStorage.setItem("em_wall_solved", "1"); } catch (e) {}
  wallLine("the scratches sit true - a latch clicks open");
  wallRoundUi("idle");
  wallRefresh();
});

function wallBoardSync() {
  if (!wallSolved() && wall.available !== false) { Arcade3D.wallSetData({ mode: "locked" }); return; }
  if (wallui.round && wallui.phase !== "idle") {
    Arcade3D.wallSetData({
      mode: "round", snapshot: wallui.round.snapshot,
      from_slot: wallui.round.from_slot, to_slot: wallui.round.to_slot,
      my_mark_id: wallui.me && wallui.me.mark ? wallui.me.mark.mark_id : null,
      result: wallui.phase === "done" ? wallui.result : null,
    });
    return;
  }
  if (wall.available === false) { Arcade3D.wallSetData({ mode: "sleep" }); return; }
  const sec = wallui.section;
  if (!sec || !(sec.slots || []).length) {
    Arcade3D.wallSetData({ mode: "blank", capacity: (sec && sec.capacity) || undefined });
    return;
  }
  Arcade3D.wallSetData({
    mode: "section", capacity: sec.capacity, slots: sec.slots,
    my_mark_id: wallui.me && wallui.me.mark ? wallui.me.mark.mark_id : null,
  });
}

function wallPaintPanel() {
  const mine = wallui.me && wallui.me.mark;
  const signBox = $("#wall-signbox"), mineBox = $("#wall-minebox");
  const accepting = wall.meta && wall.meta.accepting !== false;
  const gated = !wallSolved() && wall.available !== false;
  if (signBox) signBox.hidden = gated || !!mine || wall.available === false;
  if (mineBox) {
    mineBox.hidden = gated || !mine;
    if (mine) $("#wall-mine").textContent = mine.status === "removed"
      ? "your name was removed"
      : "your name: " + (mine.name || "?") + (mine.status === "visible" ? "" : " (" + mine.status + ")");
  }
  const lim = (wall.meta && wall.meta.limits) || { name_min: 2, name_max: 24 };
  const inp = $("#wall-name");
  if (inp) { inp.minLength = lim.name_min; inp.maxLength = lim.name_max; }
  const canPlay = !!mine && mine.status === "visible" && wall.available !== false;
  const ch = $("#wall-challenge");
  if (ch) ch.hidden = gated || !canPlay;
  const ho2 = $("#wall-halloffer"); if (ho2 && gated) ho2.hidden = true;
  const closed = wall.meta && wall.meta.accepting === false;
  if (closed) wallLine("the wall is closed right now");
  const hallOn = wallui.me && wallui.me.hall;
  const hallLine = $("#wall-hallstate");
  if (hallLine) hallLine.textContent = hallOn ? "you are on the Hall of Fame" : "";
}

async function wallRefresh() {
  if (wall.available === null) await wallBootstrap();
  if (wall.available === false) { wallBoardSync(); wallPaintPanel(); wallLine("the wall's backend is not built yet - this is its sleeping state"); return; }
  const me = await wallGet("/api/wall/me");
  if (me.status === 200 && me.data) wallui.me = me.data;
  const sec = await wallGet("/api/wall/sections/" + wallui.sectionId + "?limit=300");
  if (sec.status === 200 && sec.data) wallui.section = sec.data;
  wallBoardSync();
  wallPaintPanel();
  if (!wallui.me || !wallui.me.mark) wallLine("sign the wall - one name, permanent");
  else wallLine("");
}

// crypto.randomUUID() exists only in a secure context (HTTPS or localhost); getRandomValues works everywhere, so SIGN never throws on plain HTTP
function wallIdemKey() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "k" + Array.from(crypto.getRandomValues(new Uint8Array(16))).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
}

async function wallSign() {
  const inp = $("#wall-name");
  const name = (inp && inp.value || "").trim();
  if (name.length < 2) { wallLine("name needs at least 2 characters"); return; }
  wallLine("scratching it in...");
  const r = await wallPost("/api/wall/marks", { name: name }, wallIdemKey());
  if (r.status === 201) {
    wallLine("permanent now. Come back for the challenge.");
    if (inp) inp.value = "";
    await wallRefresh();
    return;
  }
  if (r.status === 409 && r.data && r.data.code === "already_marked") { await wallRefresh(); wallLine("this browser already signed"); return; }
  if (r.status === 400) { wallLine((r.data && r.data.error) || "that name does not work"); return; }
  if (r.status === 503) { wallLine("the wall is closed right now"); return; }
  if (r.status === 401) { wallLine((r.data && r.data.error) || "session refreshed - tap SIGN again"); return; }
  wallLine("the wall did not answer - nothing was written");
}

function wallRoundUi(phase) {
  document.body.classList.toggle("wall-phase-go", phase === "go"); // compact strip: never blocks board taps
  ["#wall-startrow", "#wall-readybtn", "#wall-waitline", "#wall-resultrow", "#wall-halloffer"].forEach(function (sel) {
    const el = $(sel); if (el) el.hidden = true;
  });
  if (phase === "idle") { const el = $("#wall-startrow"); if (el) el.hidden = false; }
  if (phase === "issued") { const el = $("#wall-readybtn"); if (el) el.hidden = false; }
  if (phase === "go") { const el = $("#wall-waitline"); if (el) el.hidden = false; }
  if (phase === "done") { const el = $("#wall-resultrow"); if (el) el.hidden = false; }
}

async function wallStartRound(mode) {
  wallLine("asking the wall for a round...");
  const r = await wallPost("/api/wall/rounds", { mode: mode });
  if (r.status !== 200 || !r.data) { wallLine("the wall did not answer"); return; }
  if (!r.data.available) {
    const reasons = { not_visible: "your name must be visible on the wall first", no_target: "no free spot to hide your name right now",
      daily_limit: "enough rounds for today - come back tomorrow", round_in_progress: "a round is already open" };
    wallLine(reasons[r.data.reason] || ("no round: " + r.data.reason));
    return;
  }
  wallui.round = r.data;
  wallui.phase = "issued";
  wallui.result = null;
  wallBoardSync(); // frozen snapshot view, swap already applied - find where your name went
  wallRoundUi("issued");
  wallLine(wallui.round.mode === "timed"
    ? "your name moved. Tap READY, then tap its new spot before the clock runs out"
    : "your name moved. Tap READY, then tap your name - no clock");
}

async function wallReady() {
  if (!wallui.round) return;
  const r = await wallPost("/api/wall/rounds/" + wallui.round.round_id + "/ready", {});
  if (r.status === 409 && r.data && r.data.code === "round_stale") {
    wallui.phase = "done"; wallui.result = null;
    wallRoundUi("done");
    $("#wall-result").textContent = "the wall changed under this round - it did not count. Ask for a new one.";
    wallBoardSync();
    return;
  }
  if (r.status !== 200 || !r.data) { wallLine("the wall did not answer"); return; }
  // the backend sends ISO 8601 strings (docs/WALL.md); the first frontend build read them as epoch seconds and showed NaN
  const wallTs = function (v) { return typeof v === "number" ? v * 1000 : Date.parse(v); };
  wallui.skewMs = Date.now() - wallTs(r.data.server_now);
  wallui.phase = "go";
  wallRoundUi("go");
  const cd = $("#wall-countdown");
  if (r.data.deadline_at) {
    wallui.deadlineLocal = wallTs(r.data.deadline_at) + wallui.skewMs;
    clearInterval(wallui.timer);
    wallui.timer = setInterval(function () {
      const left = wallui.deadlineLocal - Date.now();
      if (cd) cd.textContent = (Math.max(0, left) / 1000).toFixed(1) + "s";
      if (left <= 0) { wallTimeout(); }
    }, 100);
    wallLine("GO - tap the spot where your name landed");
  } else {
    if (cd) cd.textContent = "";
    wallLine("GO - tap your name, no clock");
  }
}

async function wallTimeout() {
  clearInterval(wallui.timer);
  if (wallui.round) await wallPost("/api/wall/rounds/" + wallui.round.round_id + "/cancel", {});
  wallui.phase = "idle";
  wallui.round = null;
  wallRoundUi("idle");
  wallBoardSync();
  wallLine("too slow - the round was cancelled, nothing counted");
}

async function wallTap(cell) {
  if (wallui.phase !== "go" || !wallui.round) return;
  clearInterval(wallui.timer);
  const mode = wallui.round.mode;
  const body = mode === "timed" ? { slot: cell.slot } : { mark_id: cell.mark_id };
  if (mode === "untimed" && cell.mark_id == null) { wallLine("that is an empty spot - tap a name"); return; }
  const r = await wallPost("/api/wall/rounds/" + wallui.round.round_id + "/tap", body);
  if (r.status === 409 && r.data && r.data.code === "not_ready") { wallLine("tap READY first"); return; }
  if (r.status !== 200 || !r.data) { wallLine("the wall did not answer"); return; }
  wallui.phase = "done";
  wallui.result = r.data.result;
  wallBoardSync(); // reveals the true destination
  wallRoundUi("done");
  const res = $("#wall-result");
  if (r.data.result === "found") {
    if (res) res.textContent = (mode === "timed" && typeof r.data.elapsed_ms === "number")
      ? "FOUND - " + wallFmtMs(r.data.elapsed_ms) + ". that earns a place on the Hall, if you want it."
      : "FOUND. that earns a place on the Hall, if you want it."; // elapsed_ms exists only on a timed find; never invent one
    if (r.data.hall_offer) { const ho = $("#wall-halloffer"); if (ho) ho.hidden = false; }
    await wallRefreshMe();
  } else if (r.data.result === "missed") {
    if (res) res.textContent = "not that spot. The round is used - the reveal shows where it went.";
  } else {
    if (res) res.textContent = "the clock won. The round is used - the reveal shows where it went.";
  }
}

async function wallRefreshMe() {
  const me = await wallGet("/api/wall/me");
  if (me.status === 200 && me.data) { wallui.me = me.data; wallPaintPanel(); }
}

async function wallHallOpt(optIn) {
  const r = await wallPost("/api/wall/me/hall", { opt_in: optIn });
  if (r.status === 409 && r.data && r.data.code === "not_eligible") { wallLine("the Hall needs a found round first"); return; }
  if (r.status !== 200) { wallLine("the wall did not answer"); return; }
  const ho = $("#wall-halloffer"); if (ho) ho.hidden = true;
  await wallRefreshMe();
  await wallPollHall(); // the holographic table updates
  wallLine(optIn ? "you are on the Hall of Fame" : "no Hall - the wall keeps your name either way");
}

async function wallNewRound() {
  wallui.round = null; wallui.phase = "idle"; wallui.result = null;
  wallBoardSync();
  wallRoundUi("idle");
  wallLine("");
}

async function wallLeaveFocus() {
  clearInterval(wallui.timer);
  if (wallui.round && (wallui.phase === "issued" || wallui.phase === "go")) {
    await wallPost("/api/wall/rounds/" + wallui.round.round_id + "/cancel", {});
  }
  wallui.round = null; wallui.phase = "idle"; wallui.result = null;
  wallBoardSync();
  wallRoundUi("idle");
}

/* Focused scene views (signature wall, holo table, dossier) have no back
   button: a clean tap on empty room background returns to the room. Taps on
   the puzzle board, the open paper, or any control keep working and never
   close the view; a drag (the gate puzzle) never counts as a tap; the tap
   that opened the view cannot immediately close it. Escape still works. */
const focusExit = { openedAt: 0, leavers: [] };
function noteFocusOpened() { focusExit.openedAt = performance.now(); }
function wireBackgroundExit() {
  const UI = "#paper, #wall-panel, #voice-panel, #chat, .hud, #credits-panel, #flight-layer, #gl-fallback, button, input, select, textarea, a, audio";
  let down = null;
  document.addEventListener("pointerdown", function (ev) {
    down = { x: ev.clientX, y: ev.clientY, t: performance.now(), ui: !!(ev.target.closest && ev.target.closest(UI)) };
  }, true);
  document.addEventListener("pointerup", function (ev) {
    const d = down; down = null;
    if (!d) return;
    if (performance.now() - focusExit.openedAt < 450) return; // the opening tap must not close the view
    if (Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 7) return; // a drag (gate puzzle, camera), not a tap
    if (performance.now() - d.t > 600) return; // a long press is not a tap
    if (d.ui || (ev.target.closest && ev.target.closest(UI))) return; // a control, the paper or the panel
    if (performance.now() - (Arcade3D.lastSceneTap ? Arcade3D.lastSceneTap() : 0) < 300) return; // a 3D board/control took this tap
    focusExit.leavers.forEach(function (leave) { leave(); });
  }, true);
}

/* wall focus: same tuck-away pattern as the holo table and the dossier */
function wireWallFocus() {
  Arcade3D.onWallFocus(function () {
    noteFocusOpened();
    const chat = $("#chat");
    chat.dataset.wallWasCollapsed = chat.classList.contains("collapsed") ? "1" : "";
    chat.classList.add("collapsed");
    document.body.classList.add("wall-focus");
    Arcade3D.wallFocus();
    const panel = $("#wall-panel");
    if (panel) panel.hidden = false;
    if (!wallSolved() && wall.available !== false) {
      Arcade3D.wallSetData({ mode: "locked" });
      wallPaintPanel();
      wallLine("something is scratched into the paint - drag the view sideways until the lines sit true");
      if (wall.available === null) wallBootstrap().then(function (ok) {
        if (!ok) { wall.available = false; wallBoardSync(); wallPaintPanel(); wallLine("the wall's backend is not built yet - this is its sleeping state"); }
      });
    } else {
      wallRoundUi(wallui.phase === "idle" ? "idle" : wallui.phase);
      wallRefresh();
    }
  });
  function leave() {
    if (!Arcade3D.wallFocused()) return;
    wallLeaveFocus();
    Arcade3D.wallUnfocus();
    const chat = $("#chat");
    if (!chat.dataset.wallWasCollapsed) chat.classList.remove("collapsed");
    document.body.classList.remove("wall-focus");
    delete chat.dataset.wallWasCollapsed;
    const panel = $("#wall-panel");
    if (panel) panel.hidden = true;
  }
  focusExit.leavers.push(leave);
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") leave(); });
  Arcade3D.onWallTap(function (cell) { wallTap(cell); });
  const signBtn = $("#wall-sign-btn");
  if (signBtn) signBtn.addEventListener("click", wallSign);
  const nameInp = $("#wall-name");
  if (nameInp) nameInp.addEventListener("keydown", function (ev) { if (ev.key === "Enter") wallSign(); });
  const rt = $("#wall-start-timed"), ru = $("#wall-start-untimed");
  if (rt) rt.addEventListener("click", function () { wallStartRound("timed"); });
  if (ru) ru.addEventListener("click", function () { wallStartRound("untimed"); });
  const rb = $("#wall-readybtn");
  if (rb) rb.addEventListener("click", wallReady);
  const nr = $("#wall-newround");
  if (nr) nr.addEventListener("click", wallNewRound);
  const hy = $("#wall-hall-yes"), hn = $("#wall-hall-no");
  if (hy) hy.addEventListener("click", function () { wallHallOpt(true); });
  if (hn) hn.addEventListener("click", function () { wallHallOpt(false); });
}

/* holographic table focus: chat tucks away, BACK chip returns */
function wireHoloFocus() {
  Arcade3D.onHoloFocus(function () {
    noteFocusOpened();
    const chat = $("#chat");
    const wasCollapsed = chat.classList.contains("collapsed");
    chat.dataset.holoWasCollapsed = wasCollapsed ? "1" : "";
    chat.classList.add("collapsed");
    document.body.classList.add("holo-focus");
    Arcade3D.holoFocus();
  });
  function leave() {
    if (!Arcade3D.holoFocused()) return;
    Arcade3D.holoUnfocus();
    const chat = $("#chat");
    if (!chat.dataset.holoWasCollapsed) chat.classList.remove("collapsed");
    document.body.classList.remove("holo-focus");
    delete chat.dataset.holoWasCollapsed;
  }
  focusExit.leavers.push(leave);
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") leave(); });
}


/* vending corner focus: chat tucks away, the machine's name shows, a tap elsewhere or Escape returns */
function wireVendFocus() {
  Arcade3D.onVendFocus(function (i) {
    noteFocusOpened();
    const chat = $("#chat");
    chat.dataset.vendWasCollapsed = chat.classList.contains("collapsed") ? "1" : "";
    chat.classList.add("collapsed");
    document.body.classList.add("vend-focus");
    const shown = Arcade3D.vendFocus(i);
    if (shown) setCaption(shown.label);
  });
  function leave() {
    if (!Arcade3D.vendFocused()) return;
    Arcade3D.vendUnfocus();
    const chat = $("#chat");
    if (!chat.dataset.vendWasCollapsed) chat.classList.remove("collapsed");
    document.body.classList.remove("vend-focus");
    delete chat.dataset.vendWasCollapsed;
    setCaption("");
  }
  focusExit.leavers.push(leave);
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") leave(); });
}

/* dossier easter egg focus: same tuck-away pattern as the holo table */
function wireDossierFocus() {
  Arcade3D.onDossierFocus(function () {
    noteFocusOpened();
    const chat = $("#chat");
    chat.dataset.dossierWasCollapsed = chat.classList.contains("collapsed") ? "1" : "";
    chat.classList.add("collapsed");
    document.body.classList.add("dossier-focus");
    Arcade3D.dossierFocus();
  });
  function leave() {
    if (!Arcade3D.dossierFocused()) return;
    Arcade3D.dossierUnfocus();
    const chat = $("#chat");
    if (!chat.dataset.dossierWasCollapsed) chat.classList.remove("collapsed");
    document.body.classList.remove("dossier-focus");
    delete chat.dataset.dossierWasCollapsed;
  }
  focusExit.leavers.push(leave);
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") leave(); });
}

function boot() {
  const glOk = Arcade3D.init();
  $("#gl-fallback").hidden = glOk;
  wireHoloFocus();
  wireVendFocus();
  wireDossierFocus();
  wireWallFocus();
  wireBackgroundExit();
  wallTick(); // wall backend absent today: probes fail quietly, table stays hidden

  // small screens: start with the chat tucked away so the machine is the
  // first thing seen; the CHAT toggle brings it back.
  if (window.innerWidth < 700) {
    $("#chat").classList.add("collapsed");
    syncChatToggle();
  }

  $("#credits-toggle").addEventListener("click", () => {
  const p = $("#credits-panel");
  const open = p.hidden;
  p.hidden = !open;
  $("#credits-toggle").setAttribute("aria-expanded", String(open));
});

$("#chat-toggle").addEventListener("click", () => {
    const chat = $("#chat");

    chat.classList.toggle("collapsed");
    syncChatToggle();
  });
  const sndBtn = $("#sound-toggle");
  const paintSnd = () => {
    const m = Arcade3D.isMuted();
    sndBtn.classList.toggle("off", m);
    sndBtn.setAttribute("aria-pressed", m ? "true" : "false");
    sndBtn.title = m ? "machine sounds off - turn them on" : "machine sounds on - mute";
  };
  paintSnd();
  sndBtn.addEventListener("click", () => {
    Arcade3D.setMuted(!Arcade3D.isMuted());
    if (!Arcade3D.isMuted()) Arcade3D.userGesture();
    paintSnd();
  });

  $("#composer").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const t = $("#msg").value.trim();
    if (t) sendUser(t).catch((e) => bubble("err", "Network error: " + e.message));
  });
/* ---------------------------------------------------------------- voice + attach
   Capability-driven, per the agreed contracts (backend prompt section 5):
   the panel asks the backend what exists (GET /api/voice/capabilities; the
   guest routes under /api/plans/<id>/guests/*) and wires the real route when
   it answers. When a route is absent the UI says so in one line and nothing
   is simulated. A voice note is never sent as audio: only the text the user
   approves reaches the assistant. */
const voiceCapsState = { caps: null };
async function voiceCaps() {
  if (voiceCapsState.caps) return voiceCapsState.caps; // re-check after a miss: the backend may come up later
  try {
    const r = await api("/api/voice/capabilities");
    voiceCapsState.caps = (r.ok && r.data && r.data.transcribe) ? r.data : null;
  } catch (e) { voiceCapsState.caps = null; }
  return voiceCapsState.caps;
}
function currentPlanId() {
  return (state.lastPayload && state.lastPayload.plan && state.lastPayload.plan.plan_id) || "";
}

/* One voice session per built plan (contract: POST /api/voice/sessions). */
const voiceSession = { id: "", planId: "", generation: 0 };
async function ensureVoiceSession(planId) {
  if (voiceSession.id && voiceSession.planId === planId) return voiceSession.id;
  let r;
  try { r = await api("/api/voice/sessions", { method: "POST", body: JSON.stringify({ plan_id: planId }) }); }
  catch (e) { return null; }
  if (!r.ok || !r.data || !r.data.session_id) return null;
  voiceSession.id = r.data.session_id; voiceSession.planId = planId; voiceSession.generation = 0;
  return voiceSession.id;
}
async function endVoiceSession() {
  if (!voiceSession.id) return;
  const id = voiceSession.id;
  voiceSession.id = ""; voiceSession.planId = ""; voiceSession.generation = 0;
  try { await api("/api/voice/sessions/" + encodeURIComponent(id), { method: "DELETE" }); }
  catch (e) {
    try { await api("/api/voice/sessions", { method: "DELETE", body: JSON.stringify({ session_id: id }) }); } catch (e2) { /* best effort */ }
  }
}

/* POST /api/voice/turns - SSE or plain JSON fallback, both in the contract.
   Returns {kind:"ok", answer, proposals} | {kind:"unavailable"} (route absent:
   caller may fall back to typed chat) | {kind:"failed", turnId} (the turn WAS
   accepted but the reply could not be read - never silently re-send). */
async function postVoiceTurn(text, inputMode) {
  const caps = await voiceCaps();
  if (caps) { // v2 capability groups: explicit off means do not even probe
    if (caps.sessions && caps.sessions.enabled === false) return { kind: "unavailable" };
    if (caps.turns && caps.turns.enabled === false) return { kind: "unavailable" };
  }
  const pid = currentPlanId();
  if (!pid) return { kind: "unavailable" };
  const sid = await ensureVoiceSession(pid);
  if (!sid) return { kind: "unavailable" };
  const turnId = "tn-" + Date.now().toString(36) + "-" + Math.floor(Math.random() * 1e6).toString(36);
  const gen = ++voiceSession.generation;
  let res;
  try {
    res = await fetch("/api/voice/turns", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-EventMate-CSRF": state.csrf, "Accept": "application/json" },
      body: JSON.stringify({ session_id: sid, turn_id: turnId, generation: gen, text: text, input_mode: inputMode }),
    });
  } catch (e) {
    // the request may already have reached the server - never fall back to
    // typed intake and risk sending the same text twice
    return { kind: "failed", turnId: turnId };
  }
  // After dispatch, a bare status PROVES nothing about whether the turn was
  // processed. Only the contract's structured pre-processing rejections are
  // safe to fall back on: {"code":"stale_generation"} (409, refused) and
  // {"code":"not_found"} (404, unknown/foreign ids refused before processing).
  // Anything else - bare 404/501 from a proxy, malformed body, 5xx - is
  // ambiguous: report the turn id, never re-send.
  let ebody = null;
  try { ebody = await res.clone().json(); } catch (e) { /* not JSON */ }
  const ecode = ebody && ebody.code;
  if ((res.status === 409 && ecode === "stale_generation") ||
      (res.status === 404 && ecode === "not_found")) return { kind: "unavailable" };
  if (!res.ok) return { kind: "failed", turnId: turnId };
  const ct = res.headers.get("content-type") || "";
  let fin = null;
  try {
    if (ct.indexOf("text/event-stream") >= 0) {
      const raw = await res.text();
      raw.split(/\n\n+/).forEach((blk) => {
        const m = blk.match(/^data:\s*(\{[\s\S]*?\})\s*$/m);
        if (!m) return;
        let ev; try { ev = JSON.parse(m[1]); } catch (e) { return; }
        if (ev.error) fin = null;
        else if (ev.final) fin = ev.final;
      });
    } else {
      const data = await res.json();
      if (data) fin = data.error ? null : data.final;
    }
  } catch (e) { return { kind: "failed", turnId: turnId }; }
  if (!fin || !fin.answer) return { kind: "failed", turnId: turnId };
  return { kind: "ok", answer: fin.answer, spoken: fin.spoken_text || "", proposals: fin.proposals || [] };
}

async function sendVoiceNoteText(text) {
  const r = await postVoiceTurn(text, "voice_note");
  if (r.kind === "ok") {
    bubble("user", text);
    bubble("bot", r.answer);
    if (r.proposals.length)
      bubble("bot", "That turn produced " + r.proposals.length + " proposal(s); proposals still need the normal on-screen approval - voice never approves anything.");
    return;
  }
  if (r.kind === "failed") {
    bubble("user", text);
    bubble("err", "The voice turn (" + r.turnId + ") was accepted but its reply could not be read. Your text was NOT sent again - re-send it as a normal message if you want it answered here.");
    return;
  }
  await sendUser(text); // no voice route: the same text goes through typed intake
}

const vn = { stream: null, rec: null, chunks: [], blob: null, url: null, mime: "", secs: 0, timer: null, t0: 0, actx: null, duration: 0, decodeFailed: false };
function vnCleanup() {
  if (vn.timer) { clearInterval(vn.timer); vn.timer = null; }
  if (vn.stream) { vn.stream.getTracks().forEach((t) => t.stop()); vn.stream = null; }
  if (vn.actx) { vn.actx.close().catch(() => {}); vn.actx = null; }
  if (vn.url) { URL.revokeObjectURL(vn.url); vn.url = null; }
  vn.rec = null; vn.chunks = []; vn.blob = null; vn.secs = 0; vn.duration = 0; vn.decodeFailed = false;
}
function pickAudioMime() {
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
  const cands = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4", "audio/wav"];
  for (const c of cands) if (MediaRecorder.isTypeSupported(c)) return c;
  return "";
}
function fmtSecs(s) { return String(Math.floor(s / 60)) + ":" + String(s % 60).padStart(2, "0"); }

function closeVoicePanel() {
  endLive();
  vnCleanup();
  const p = $("#voice-panel");
  p.hidden = true;
  p.textContent = "";
}

async function openVoicePanel(mode) {
  const p = $("#voice-panel");
  p.hidden = false;
  p.textContent = "";
  // never leave the panel blank while the capabilities fetch is in flight
  p.appendChild(el("div", "vp-title", mode === "live" ? "LIVE CONVERSATION" : "VOICE NOTE"));
  const wait = el("div", "vp-status", "Checking what this backend can do…");
  p.appendChild(wait);
  const caps = await voiceCaps();
  if (p.hidden) return; // user closed meanwhile
  p.textContent = "";

  if (mode === "live") { openLivePanel(p, caps); return; }

  // voice note
  p.appendChild(el("div", "vp-title", "VOICE NOTE"));
  const status = el("div", "vp-status");
  p.appendChild(status);
  const body = el("div");
  p.appendChild(body);

  if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder)) {
    status.textContent = "Recording is not available in this browser. You can still type your message below.";
    const row0 = el("div", "vp-btns");
    const c0 = el("button", null, "CLOSE");
    c0.type = "button"; c0.addEventListener("click", closeVoicePanel);
    row0.appendChild(c0); body.appendChild(row0);
    return;
  }

  status.textContent = "Record, listen back, then decide. Nothing is uploaded or sent on its own - only the text you approve goes to the assistant.";
  const row = el("div", "vp-btns");
  const recBtn = el("button", "primary", "RECORD");
  recBtn.type = "button";
  const closeBtn = el("button", null, "CLOSE");
  closeBtn.type = "button";
  closeBtn.addEventListener("click", closeVoicePanel);
  row.appendChild(recBtn); row.appendChild(closeBtn);
  body.appendChild(row);
  if (caps && caps.transcribe && caps.transcribe.route === "server") {
    body.appendChild(el("div", "vp-status",
      "Transcription is available on this backend (" + (caps.transcribe.engine || "server engine") +
      (caps.transcribe.model ? " · " + caps.transcribe.model : "") + "). You choose per note: it goes once to the todoh backend on the owner's machine, is used only to transcribe, and is deleted right after."));
  }

  recBtn.addEventListener("click", async () => {
    try {
      vn.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true } });
    } catch (e) {
      status.textContent = "Microphone not available here: " + (e && e.message ? e.message : e) + ". You can type instead.";
      return;
    }
    vn.mime = pickAudioMime();
    try {
      vn.rec = vn.mime ? new MediaRecorder(vn.stream, { mimeType: vn.mime }) : new MediaRecorder(vn.stream);
    } catch (e) {
      status.textContent = "This browser refused to start recording: " + e.message;
      vnCleanup();
      return;
    }
    vn.chunks = [];
    vn.rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) vn.chunks.push(ev.data); };
    vn.rec.start(250);
    vn.t0 = Date.now();

    // level meter + timer
    const meter = el("span", "vp-meter"); const fill = document.createElement("i"); meter.appendChild(fill);
    vn.actx = new (window.AudioContext || window.webkitAudioContext)();
    const srcNode = vn.actx.createMediaStreamSource(vn.stream);
    const ana = vn.actx.createAnalyser(); ana.fftSize = 512;
    srcNode.connect(ana);
    const buf = new Uint8Array(ana.fftSize);

    p.textContent = "";
    p.appendChild(el("div", "vp-title", "RECORDING - stop never sends"));
    const rrow = el("div", "vp-row");
    rrow.appendChild(el("span", "vp-dot"));
    const tEl = el("span", "vp-timer", "0:00");
    rrow.appendChild(tEl); rrow.appendChild(meter);
    const stopBtn = el("button", "primary", "STOP");
    stopBtn.type = "button";
    const cancelBtn = el("button", "danger", "CANCEL");
    cancelBtn.type = "button";
    rrow.appendChild(stopBtn); rrow.appendChild(cancelBtn);
    p.appendChild(rrow);
    p.appendChild(el("div", "vp-status", "Up to 2:00. Audio stays on this machine."));

    vn.timer = setInterval(() => {
      vn.secs = Math.floor((Date.now() - vn.t0) / 1000);
      tEl.textContent = fmtSecs(vn.secs);
      ana.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) { const d = (buf[i] - 128) / 128; sum += d * d; }
      fill.style.width = Math.min(100, Math.round(Math.sqrt(sum / buf.length) * 260)) + "%";
      if (vn.secs >= 120) stopBtn.click();
    }, 120);

    cancelBtn.addEventListener("click", closeVoicePanel);
    stopBtn.addEventListener("click", () => {
      if (vn.timer) { clearInterval(vn.timer); vn.timer = null; }
      vn.rec.onstop = async () => {
        vn.blob = new Blob(vn.chunks, { type: vn.rec.mimeType || "audio/webm" });
        // prove the recording actually decodes (Chromium-recorded webm often
        // has no duration in its header, so measure by decoding, not metadata)
        vn.duration = 0; vn.decodeFailed = false;
        try {
          const ab = await vn.blob.arrayBuffer();
          const ac = new (window.AudioContext || window.webkitAudioContext)();
          const decoded = await ac.decodeAudioData(ab);
          vn.duration = decoded.duration;
          ac.close().catch(() => {});
        } catch (e) { vn.decodeFailed = true; }
        vn.url = URL.createObjectURL(vn.blob);
        renderVoiceReview(voiceCapsState.caps);
      };
      vn.rec.stop();
      if (vn.stream) { vn.stream.getTracks().forEach((t) => t.stop()); vn.stream = null; }
      if (vn.actx) { vn.actx.close().catch(() => {}); vn.actx = null; }
    });
  });
}

/* Live mode: capability-driven. With no voice service the panel says so in one
   line. With one, START wires the real contract: session, recognition (browser
   route, disclosed), turns, spoken replies, barge-in cancel, session end.
   Endpointing is minimal on purpose - real-device tuning is still owed and is
   disclosed as such, never claimed. */
let liveState = null;
async function openLivePanel(p, caps) {
  p.appendChild(el("div", "vp-title", "LIVE CONVERSATION"));
  const st = el("div", "vp-status");
  p.appendChild(st);
  const row = el("div", "vp-btns");
  const close = el("button", null, "CLOSE");
  close.type = "button";
  close.addEventListener("click", () => { endLive(); closeVoicePanel(); });
  p.appendChild(row);

  if (!caps) {
    st.textContent = "Live voice conversation isn't available yet - it needs the todoh voice service, which this backend doesn't have. Record a voice note (MIC) or type below.";
    row.appendChild(close);
    return;
  }
  // addendum shape declares live explicitly; an omitted live field means the
  // older capabilities shape, so START still probes the session route live
  if (caps.live && caps.live.enabled === false) {
    st.textContent = "Live voice conversation is not set up here" + (caps.live.reason_unavailable ? " (" + caps.live.reason_unavailable + ")" : "") + ". Record a voice note (MIC) or type below.";
    row.appendChild(close);
    return;
  }
  const pid = currentPlanId();
  if (!pid) {
    st.textContent = "Live voice binds to one built plan. Build your plan first (describe the event, press BUILD MY PLAN), then start live.";
    row.appendChild(close);
    return;
  }
  st.textContent = "Live listens with this browser's speech recognition - your browser vendor's speech service may process that audio. Replies come from the todoh backend as text and play aloud when this browser has speech output; no audio is sent to todoh in live mode. Headphones recommended.";
  const start = el("button", "primary", "START LIVE");
  start.type = "button";
  row.appendChild(start); row.appendChild(close);
  start.addEventListener("click", async () => {
    start.disabled = true;
    st.textContent = "Starting the voice session…";
    const sid = await ensureVoiceSession(pid);
    if (!sid) {
      st.textContent = "The backend has no live session route yet (sessions unavailable). Voice notes and typed chat still work.";
      start.disabled = false;
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      await endVoiceSession();
      st.textContent = "The voice session started, but this browser has no speech recognition, so live mode cannot listen here. The session was ended. Record a voice note instead.";
      return;
    }
    runLiveLoop(p, SR);
  });
}

function endLive() {
  if (liveState) {
    liveState.running = false;
    try { liveState.rec && liveState.rec.abort(); } catch (e) {}
    try { window.speechSynthesis && speechSynthesis.cancel(); } catch (e) {}
    liveState = null;
  }
  endVoiceSession();
}

function runLiveLoop(p, SR) {
  p.textContent = "";
  p.appendChild(el("div", "vp-title", "LIVE - session open"));
  const stEl = el("div", "vp-status", "LISTENING - speak");
  p.appendChild(stEl);
  const log = el("div", "vp-live-log");
  p.appendChild(log);
  const row = el("div", "vp-btns");
  const endBtn = el("button", "danger", "END LIVE");
  endBtn.type = "button";
  row.appendChild(endBtn);
  p.appendChild(row);
  const addLog = (cls, t) => { const d = el("div", cls, t); log.appendChild(d); while (log.children.length > 8) log.removeChild(log.firstChild); };

  const rec = new SR();
  rec.lang = "en-IN";
  rec.interimResults = false;
  rec.continuous = false;
  liveState = { running: true, rec: rec, speaking: false, playbackChars: 0, thinking: false, currentTurn: "" };

  const listen = () => {
    if (!liveState || !liveState.running || liveState.thinking || liveState.speaking) return;
    stEl.textContent = "LISTENING - speak";
    try { rec.start(); } catch (e) { /* already started */ }
  };

  rec.onresult = async (ev) => {
    if (!liveState) return;
    const txt = ev.results[ev.results.length - 1][0].transcript.trim();
    if (!txt) { listen(); return; }
    if (liveState.speaking) {
      // barge-in: stop local playback, tell the backend how much had played
      try { speechSynthesis.cancel(); } catch (e) {}
      const chars = liveState.playbackChars | 0;
      liveState.speaking = false;
      if (liveState.currentTurn)
        api("/api/voice/turns/" + encodeURIComponent(liveState.currentTurn) + "/cancel",
            { method: "POST", body: JSON.stringify({ session_id: voiceSession.id, generation: voiceSession.generation, playback_chars: chars }) }).catch(() => {});
      addLog("vp-live-sys", "(interrupted the reply; backend told " + chars + " characters had played)");
    }
    addLog("vp-live-you", "you: " + txt);
    liveState.thinking = true;
    stEl.textContent = "THINKING…";
    const r = await postVoiceTurn(txt, "live");
    liveState.thinking = false;
    if (!liveState) return;
    if (r.kind !== "ok") {
      addLog("vp-live-sys", r.kind === "failed"
        ? "(turn " + r.turnId + " was accepted but the reply could not be read - not re-sent)"
        : "(the voice turn route is unavailable - type below instead)");
      listen();
      return;
    }
    addLog("vp-live-bot", "todoh: " + r.answer);
    if (window.speechSynthesis) {
      const u = new SpeechSynthesisUtterance(r.spoken || r.answer);
      liveState.speaking = true; liveState.playbackChars = 0;
      u.onboundary = (e) => { if (typeof e.charIndex === "number") liveState.playbackChars = e.charIndex; };
      u.onend = () => { if (liveState) { liveState.speaking = false; listen(); } };
      u.onerror = () => { if (liveState) { liveState.speaking = false; listen(); } };
      stEl.textContent = "SPEAKING - talk over it to interrupt";
      speechSynthesis.speak(u);
    } else {
      stEl.textContent = "answered above (no speech output in this browser)";
      listen();
    }
  };
  rec.onerror = (e) => {
    if (!liveState) return;
    if (e.error === "not-allowed" || e.error === "service-not-allowed" || e.error === "audio-capture") {
      const ttl = p.querySelector(".vp-title");
      if (ttl) ttl.textContent = "LIVE - ENDED";
      endBtn.textContent = "CLOSE";
      stEl.textContent = "Microphone/speech error: " + e.error + ". Live session ended - use a voice note instead.";
      const dead = liveState;
      liveState = null;
      try { dead && dead.rec && dead.rec.abort(); } catch (e2) {}
      try { window.speechSynthesis && speechSynthesis.cancel(); } catch (e2) {}
      endVoiceSession(); // never leak a session on a dead recognizer
      return;
    }
    listen(); // no-speech and friends: just keep listening
  };
  rec.onend = () => { listen(); };
  endBtn.addEventListener("click", () => { endLive(); closeVoicePanel(); });
  listen();
}

function renderVoiceReview(caps) {
  const p = $("#voice-panel");
  p.textContent = "";
  p.appendChild(el("div", "vp-title", "REVIEW YOUR NOTE"));
  const aw = el("div", "vp-audio");
  const au = document.createElement("audio");
  au.controls = true; au.src = vn.url;
  // Chromium writes recorded webm without a duration header; the seek trick
  // makes the control show the real length instead of 0:00 / 0:00.
  au.addEventListener("loadedmetadata", () => {
    if (au.duration === Infinity || isNaN(au.duration)) {
      const fix = () => {
        au.removeEventListener("durationchange", fix);
        if (au.duration && au.duration !== Infinity && !isNaN(au.duration)) au.currentTime = 0;
      };
      au.addEventListener("durationchange", fix);
      au.currentTime = 1e7;
    }
  });
  aw.appendChild(au);
  p.appendChild(aw);

  const st = el("div", "vp-status");
  st.textContent = vn.decodeFailed
    ? "This browser could not decode the recording - please re-record. (Nothing was uploaded.)"
    : (vn.duration ? vn.duration.toFixed(1) : fmtSecs(vn.secs)) + "s · " + (vn.blob.type || "audio") + " · " +
      (vn.blob.size / 1024).toFixed(1) + " KB · decoded OK - plays back from this machine only; nothing was uploaded.";
  p.appendChild(st);

  const ta = document.createElement("textarea");
  ta.id = "vp-transcript";
  ta.placeholder = "type or edit the text of your note…";
  ta.maxLength = 2000;

  const route = caps && caps.transcribe && caps.transcribe.route;
  const note = el("div", "vp-status");
  let trBtn = null;
  if (route === "server") {
    note.textContent = "Check the text before anything is sent. TRANSCRIBE sends the recording once to the todoh backend on the owner's machine (deleted right after); or type it yourself.";
    trBtn = el("button", null, "TRANSCRIBE");
    trBtn.type = "button";
  } else {
    note.textContent = "Voice transcription is not set up here, so the assistant cannot read the audio. Type what you said - only the text is sent, as a normal chat message.";
  }
  p.appendChild(note);
  p.appendChild(ta);

  const row = el("div", "vp-btns");
  const send = el("button", "primary", "SEND TEXT");
  send.type = "button"; send.disabled = true;
  if (trBtn) row.appendChild(trBtn);
  const rerec = el("button", null, "RE-RECORD");
  rerec.type = "button";
  const disc = el("button", "danger", "DISCARD");
  disc.type = "button";
  row.appendChild(send); row.appendChild(rerec); row.appendChild(disc);
  p.appendChild(row);

  ta.addEventListener("input", () => { send.disabled = !ta.value.trim(); });

  if (trBtn) trBtn.addEventListener("click", async () => {
    const lim = caps.transcribe;
    if (vn.decodeFailed) { note.textContent = "The recording does not decode in this browser - re-record or type the text."; return; }
    if (lim.max_bytes && vn.blob.size > lim.max_bytes) {
      note.textContent = "The recording is over the backend limit of " + Math.round(lim.max_bytes / 1024) + " KB - type the text instead."; return;
    }
    if (lim.max_seconds && vn.duration && vn.duration > lim.max_seconds) {
      note.textContent = "The recording is over the backend limit of " + lim.max_seconds + "s - type the text instead."; return;
    }
    trBtn.disabled = true; trBtn.textContent = "TRANSCRIBING…";
    try {
      const fd = new FormData();
      fd.append("audio", vn.blob, "note.webm");
      const res = await fetch("/api/voice/transcribe", { method: "POST", headers: { "X-EventMate-CSRF": state.csrf }, body: fd });
      const data = await res.json().catch(() => null);
      if (res.ok && data && typeof data.transcript === "string") {
        ta.value = data.transcript;
        ta.dispatchEvent(new Event("input", { bubbles: true }));
        note.textContent = "Transcript from the backend is in the box - read it, fix anything wrong. Only the text you approve is sent.";
      } else {
        const tlim = res.status === 413 && data && data.limit_bytes ? "limit " + Math.round(data.limit_bytes / 1024) + " KB" : null;
        const treason = data && data.error ? data.error + (tlim ? " (" + tlim + ")" : "")
          : res.status === 413 ? "the file is too large for this server" + (tlim ? " (" + tlim + ")" : "")
          : res.status === 415 ? "this server does not take that audio type" : "HTTP " + res.status;
        note.textContent = "The backend could not transcribe that: " + treason + ". Your recording is still here - re-record a shorter one or type the text instead.";
      }
    } catch (e) {
      note.textContent = "Transcription failed (network). Type the text instead.";
    }
    trBtn.disabled = false; trBtn.textContent = "TRANSCRIBE";
  });

  send.addEventListener("click", () => {
    const t = ta.value.trim();
    closeVoicePanel();
    if (t) sendVoiceNoteText(t).catch((e) => bubble("err", "Network error: " + e.message));
  });
  rerec.addEventListener("click", () => { vnCleanup(); openVoicePanel("note"); });
  disc.addEventListener("click", closeVoicePanel);
}

/* ---------------------------------------------------------------- guest CSV attach
   Client-side preview only. Mirrors the agreed import rules (header row,
   party_size 1-20, rsvp yes/no/pending, dietary from the known values, 500
   rows, formula cells rejected, control characters stripped, names capped at
   120) so the preview matches what the backend will accept. The browser
   preview is never saved; importing runs only when a built plan exists AND
   the backend guest routes answer (contract section 5), via preview token. */
const GUEST_DIETS = ["vegetarian", "non_vegetarian", "vegan", "jain", "halal", "gluten_free"];
function parseGuestCsv(text) {
  const out = { rows: [], truncated: false, error: null };
  const lines = text.split(/\r\n|\r|\n/).filter((l) => l.trim().length);
  if (!lines.length) { out.error = "The file is empty."; return out; }
  const splitRow = (l) => l.split(",").map((c) => c.trim().replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, ""));
  const head = splitRow(lines[0]).map((c) => c.toLowerCase());
  const col = { name: head.indexOf("name"), party_size: head.indexOf("party_size"), rsvp: head.indexOf("rsvp"), dietary: head.indexOf("dietary") };
  if (col.party_size < 0 || col.rsvp < 0) {
    out.error = "The first row must be a header naming at least party_size and rsvp (optional: name, dietary). Simple comma-separated rows only - quoted cells with commas are not supported.";
    return out;
  }
  const dataLines = lines.slice(1);
  if (dataLines.length > 500) out.truncated = true;
  dataLines.slice(0, 500).forEach((l, i) => {
    const cells = splitRow(l);
    const row = { line: i + 2, name: "", party_size: null, rsvp: "", dietary: [], errors: [] };
    const get = (idx) => (idx >= 0 && idx < cells.length) ? cells[idx] : "";
    cells.forEach((c) => { if (/^[=+\-@]/.test(c)) row.errors.push("cell rejected: starts with '" + c.charAt(0) + "'"); });
    row.name = get(col.name);
    if (row.name.length > 120) row.errors.push("name longer than 120 characters");
    const ps = get(col.party_size);
    if (!/^\d{1,2}$/.test(ps) || +ps < 1 || +ps > 20) row.errors.push("party_size must be a whole number 1-20");
    else row.party_size = +ps;
    const rv = get(col.rsvp).toLowerCase();
    if (["yes", "no", "pending"].indexOf(rv) < 0) row.errors.push("rsvp must be yes, no or pending");
    else row.rsvp = rv;
    get(col.dietary).split(";").map((s) => s.trim().toLowerCase()).filter(Boolean).forEach((d) => {
      if (GUEST_DIETS.indexOf(d) < 0) row.errors.push("unknown dietary value: " + d);
      else row.dietary.push(d);
    });
    out.rows.push(row);
  });
  return out;
}

function renderGuestPreview(parsed, fileName, fileBytes, csvText) {
  const inner = $("#paper-inner");
  inner.textContent = "";
  const paper = $("#paper");
  paper.classList.remove("jammed");
  const oldStamp = paper.querySelector(".stamp");
  if (oldStamp) oldStamp.remove();
  paper.appendChild(el("div", "stamp warn", "PREVIEW ONLY"));

  inner.appendChild(el("h1", null, "GUEST LIST - PREVIEW"));
  const gsub = el("div", "sub",
    fileName + " · " + (fileBytes / 1024).toFixed(1) + " KB · parsed in this browser only - nothing uploaded, nothing saved.");
  inner.appendChild(gsub);

  if (parsed.error) {
    inner.appendChild(el("h2", null, "CANNOT READ THIS FILE"));
    const ul0 = el("ul", "badlist");
    ul0.appendChild(el("li", null, parsed.error));
    inner.appendChild(ul0);
  } else {
    const okRows = parsed.rows.filter((r) => !r.errors.length);
    const badRows = parsed.rows.filter((r) => r.errors.length);
    const party = okRows.reduce((a, r) => a + (r.party_size || 0), 0);
    const rsvpC = { yes: 0, no: 0, pending: 0 };
    okRows.forEach((r) => { rsvpC[r.rsvp] += r.party_size || 0; });
    const dietC = {};
    okRows.forEach((r) => r.dietary.forEach((d) => { dietC[d] = (dietC[d] || 0) + 1; }));
    inner.appendChild(el("h2", null, "TOTALS"));
    const ul = el("ul");
    ul.appendChild(el("li", null, parsed.rows.length + " row(s) read: " + okRows.length + " ok, " + badRows.length + " with errors" + (parsed.truncated ? " - file over the 500-row limit, only the first 500 shown" : "")));
    ul.appendChild(el("li", null, "party size across ok rows: " + party));
    ul.appendChild(el("li", null, "rsvp (heads): " + rsvpC.yes + " yes · " + rsvpC.no + " no · " + rsvpC.pending + " pending"));
    const dk = Object.keys(dietC);
    ul.appendChild(el("li", null, dk.length ? "dietary: " + dk.map((d) => d.replace(/_/g, " ") + " ×" + dietC[d]).join(" · ") : "no dietary requirements listed"));
    inner.appendChild(ul);

    if (state.draft && state.draft.guest_count && state.draft.guest_count !== party) {
      inner.appendChild(el("div", "ribbon",
        "Heads-up: your brief says " + state.draft.guest_count + " guests but this list totals " + party +
        ". The planning headcount stays the number you stated; the list is just a list."));
    }

    inner.appendChild(el("h2", null, "ROWS"));
    const t = el("table");
    const hr = el("tr");
    ["#", "name", "party", "rsvp", "dietary", "check"].forEach((h, i) => hr.appendChild(el("th", i === 2 ? "num" : null, h)));
    t.appendChild(hr);
    parsed.rows.forEach((r) => {
      const tr = el("tr", r.errors.length ? "rowerr" : null);
      tr.appendChild(el("td", null, String(r.line)));
      tr.appendChild(el("td", null, r.name || "—"));
      tr.appendChild(el("td", "num", r.party_size === null ? "—" : String(r.party_size)));
      tr.appendChild(el("td", null, r.rsvp || "—"));
      tr.appendChild(el("td", null, r.dietary.map((d) => d.replace(/_/g, " ")).join(", ") || "—"));
      tr.appendChild(el("td", "rowmsg", r.errors.length ? r.errors.join("; ") : "ok"));
      t.appendChild(tr);
    });
    inner.appendChild(t);
  }

  const imp = el("button", "paper-close gprev-import", "IMPORT");
  imp.type = "button";
  imp.disabled = true;
  const gnote = el("p", "gprev-note", "Checking whether this backend can import guest lists…");
  inner.appendChild(gnote);
  inner.appendChild(imp);

  // Capability-driven import: the routes live under a built plan
  // (POST /api/plans/<id>/guests/preview then /guests/commit with the preview
  // token - contract section 5). Absent plan or absent route = honest off.
  // Import is all-or-nothing: any row error blocks it, client and server side.
  (async () => {
    const noSave = " Guests are never messaged. The list is never shown to the model as names - counts only.";
    const badCount = (parsed.rows || []).filter((r) => r.errors && r.errors.length).length;
    if (badCount) {
      gnote.textContent = "Not importable until errors are fixed: " + badCount + " row(s) have problems (see the table). Import is all-or-nothing - fix the file and attach it again." + noSave;
      imp.title = "Fix the row errors first.";
      return;
    }
    const pid = currentPlanId();
    if (!pid) {
      gnote.textContent = "Import is off for now: the guest routes hang off a built plan, and this session has not built one yet. Build your plan, attach the list again, and the check runs against the backend." + noSave;
      imp.title = "Build a plan first; importing then uses the backend guest routes.";
      return;
    }
    const csvBytes = new TextEncoder().encode(csvText).length;
    if (csvBytes > 262144) {
      gnote.textContent = "The list text is " + Math.ceil(csvBytes / 1024) + " KB - over the 256 KB limit for checking against the backend. Nothing was sent. The sheet above is your parsed copy; trim the file and attach again." + noSave;
      return;
    }
    let r;
    try {
      r = await api("/api/plans/" + encodeURIComponent(pid) + "/guests/preview",
        { method: "POST", body: JSON.stringify({ csv: csvText }) });
    } catch (e) { r = null; }
    // The CSV text left this browser for the check: the "nothing uploaded"
    // subtitle is no longer accurate from here on.
    gsub.textContent = fileName + " · " + (fileBytes / 1024).toFixed(1) + " KB · browser preview - sent for checking, not saved.";
    if (!r || r.status === 404 || r.status === 501) {
      gnote.textContent = "Import is off: this backend has no guest routes yet. The list was sent for checking, but there was nowhere to save it - nothing is stored. The sheet above is the browser's parsed copy." + noSave;
      imp.title = "The backend guest endpoints are not built yet.";
      return;
    }
    if (!r.ok) {
      const glim = r.status === 413 && r.data && r.data.limit_bytes ? "limit " + Math.round(r.data.limit_bytes / 1024) + " KB" : null;
      const greason = r.data && r.data.error ? r.data.error + (glim ? " (" + glim + ")" : "")
        : r.status === 413 ? "the file is too large for this server" + (glim ? " (" + glim + ")" : "")
        : r.status === 415 ? "this server does not take that content type" : "HTTP " + r.status;
      gnote.textContent = "The backend guest preview refused the file: " + greason + ". Nothing was saved - the sheet above is your parsed copy; adjust the file and attach again." + noSave;
      return;
    }
    const token = r.data && r.data.preview_token;
    const totals = r.data && r.data.totals;
    const heads = totals && totals.heads;
    if (r.data && r.data.can_commit === false) {
      const errs = (r.data.row_errors || []).length;
      gnote.textContent = "The backend found " + (errs || "row") + " row error(s) - not importable until they are fixed (import is all-or-nothing)." + noSave;
      return;
    }
    if (!token) {
      gnote.textContent = "The backend answered but sent no preview token, so there is nothing safe to commit against. Import stays off." + noSave;
      return;
    }
    gsub.textContent = fileName + " · " + (fileBytes / 1024).toFixed(1) + " KB · checked by the backend - not saved yet.";
    gnote.textContent = "Backend preview accepted" +
      (totals ? " (" + (totals.rows != null ? totals.rows + " guest entries" : "entries ok") +
        (heads != null ? ", " + heads + " people" : "") + ")" : "") +
      ". Import saves the list to plan " + pid + " and replaces any list already saved there. Dietary counts are per entry (the CSV cannot say how many in a party share one)." + noSave;
    imp.disabled = false;
    imp.title = "Save the previewed list to plan " + pid;
    imp.addEventListener("click", async () => {
      imp.disabled = true;
      let c;
      try {
        c = await api("/api/plans/" + encodeURIComponent(pid) + "/guests/commit",
          { method: "POST", body: JSON.stringify({ preview_token: token }) });
      } catch (e) { c = null; }
      if (c && c.ok) {
        if (c.data && c.data.guest_list_id) state.guestListId = c.data.guest_list_id;
        const t2 = c.data && c.data.totals;
        const n2 = t2 && t2.rows;
        gnote.textContent = "Imported to plan " + pid +
          (n2 != null ? ": " + n2 + " guest entries saved" : ": list saved") + "." + noSave;
        gsub.textContent = fileName + " · " + (fileBytes / 1024).toFixed(1) + " KB · imported - the list is saved to your plan on this backend.";
        const stamp = paper.querySelector(".stamp");
        if (stamp) { stamp.textContent = "IMPORTED"; stamp.className = "stamp ok"; }
        const rm = el("button", "paper-close", "REMOVE LIST");
        rm.type = "button";
        rm.title = "Delete the saved guest list from this plan";
        rm.addEventListener("click", async () => {
          rm.disabled = true;
          let d;
          try { d = await api("/api/plans/" + encodeURIComponent(pid) + "/guests", { method: "DELETE" }); }
          catch (e2) { d = null; }
          if (d && d.ok) {
            state.guestListId = "";
            gnote.textContent = "The saved list was removed from plan " + pid + ". The browser preview above is just the file you attached - nothing else is stored." + noSave;
            gsub.textContent = fileName + " · " + (fileBytes / 1024).toFixed(1) + " KB · removed again - nothing saved on the backend.";
            const st2 = paper.querySelector(".stamp");
            if (st2) { st2.textContent = "PREVIEW ONLY"; st2.className = "stamp warn"; }
            rm.remove();
          } else {
            gnote.textContent = "Removing the saved list failed" + (d && d.data && d.data.error ? ": " + d.data.error : d ? " (HTTP " + d.status + ")" : " (network error)") + ".";
            rm.disabled = false;
          }
        });
        gnote.parentNode.insertBefore(rm, gnote.nextSibling);
      } else {
        gnote.textContent = "The commit failed" +
          (c && c.data && c.data.error ? ": " + c.data.error : c ? " (HTTP " + c.status + ")" : " (network error)") +
          ". The preview is still here - you can try again.";
        imp.disabled = false;
      }
    });
  })();

  const close = el("button", "paper-close", "BACK TO THE FLOOR");
  close.type = "button";
  close.addEventListener("click", hidePaper);
  inner.appendChild(close);

  // the sheet needs the full height: tuck the chat away like a build does;
  // hidePaper puts it back
  state.chatWasOpen = !$("#chat").classList.contains("collapsed");
  if (state.chatWasOpen) {
    $("#chat").classList.add("collapsed");
    syncChatToggle();
  }
  showPaper(false);
}

function onAttachFile() {
  const f = $("#attach-file").files && $("#attach-file").files[0];
  $("#attach-file").value = "";
  if (!f) return;
  if (f.size > 256 * 1024) {
    bubble("err", "That file is " + (f.size / 1024).toFixed(0) + " KB - the guest-list limit is 256 KB. Nothing was read.");
    return;
  }
  const rd = new FileReader();
  rd.onerror = () => bubble("err", "Could not read that file.");
  rd.onload = () => {
    let parsed;
    try { parsed = parseGuestCsv(String(rd.result || "")); }
    catch (e) { parsed = { rows: [], error: "Could not parse the file: " + e.message }; }
    renderGuestPreview(parsed, f.name, f.size, String(rd.result || ""));
  };
  rd.readAsText(f); // UTF-8
}

  $("#attach-btn").addEventListener("click", () => { $("#attach-file").click(); });
  $("#attach-file").addEventListener("change", onAttachFile);
  $("#mic-btn").addEventListener("click", () => { openVoicePanel("note"); });
  $("#live-btn").addEventListener("click", () => { openVoicePanel("live"); });

  $("#build").addEventListener("click", () => build().catch((e) => {
    state.running = false;
    stopPolling();
    $("#replay-banner").hidden = true;
    $("#build").disabled = false;
    Arcade3D.setCRT("INSERT COIN");
    Arcade3D.zoomOut();
    bubble("err", "The run failed before a result came back: " + e.message);
  }));

  (async () => {
    try {
      const s = await api("/api/session");
      state.csrf = s.data.csrf;
      state.wsId = s.data.workspace && s.data.workspace.mode === "shared" ? s.data.workspace.id : "";
      $("#conn").className = "ok";
      $("#conn").title = "online · " + s.data.today;
    } catch (e) {
      $("#conn").className = "bad";
      $("#conn").title = "offline";
    }
    if (glOk) Arcade3D.powerOn(); // the attendant walks over and turns the cabinet on
    // minimal home: no seeded boilerplate - the log shows only the actual conversation
    $("#msg").focus();
  })();
}

document.addEventListener("DOMContentLoaded", boot);

/* screenshot/view option: start with the chat card tucked away */
if (new URLSearchParams(location.search).has("nochat")) {
  var nc = document.getElementById("chat");
  if (nc) nc.classList.add("collapsed");
  syncChatToggle();
}

