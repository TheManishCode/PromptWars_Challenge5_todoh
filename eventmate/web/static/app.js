/* to;doh planner (/app). Talks to the same JSON API as everything else: /api/session, /api/intake, /api/intake/confirm, /api/plans, /api/evidence,
   /api/plans/<id>/changes. Rules this file keeps: the page computes no fact (it shows what the API returned), text from the server is only ever put in
   with textContent, and there are no inline handlers or styles (the CSP forbids them). */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var state = { csrf: null, wsId: null, mode: 'local', messages: [], draft: null, turn: null, plan: null, tab: 'overview', busy: false, delta: null };

  /* ------------------------------------------------------------ helpers */
  function h(tag, props) {
    var e = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) add(e, arguments[i]);
    return e;
  }
  function add(parent, kid) {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { add(parent, k); }); return; }
    parent.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function announce(text) { var l = $('#live'); l.textContent = ''; window.setTimeout(function () { l.textContent = text; }, 30); }

  function inr(n) {
    if (typeof n !== 'number') return '-';
    var s = String(Math.round(Math.abs(n))), out = '', first = true;
    while (s.length > 0) { var take = first ? 3 : 2; var chunk = s.slice(-take); out = chunk + (out ? ',' + out : ''); s = s.slice(0, -take); first = false; }
    return (n < 0 ? '-' : '') + '₹' + out;
  }
  function parseDay(iso) { var p = String(iso).split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function longDate(iso) { var d = parseDay(iso); return isNaN(d) ? String(iso) : d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }); }
  function clock(iso) { var d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }); }
  function dayKey(iso) { return String(iso).slice(0, 10); }
  function words(s) { return String(s || '').replace(/_/g, ' '); }
  function cap(s) { s = words(s); return s.charAt(0).toUpperCase() + s.slice(1); }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  /* ------------------------------------------------------------ API */
  function api(path, opts) {
    opts = opts || {};
    var headers = { 'Accept': 'application/json' };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (state.csrf) headers['X-EventMate-CSRF'] = state.csrf;
    return fetch(path, { method: opts.method || 'GET', headers: headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined })
      .then(function (res) {
        return res.json().catch(function () { return null; }).then(function (data) {
          if (res.status === 404 && data && data.code === 'not_found' && opts.method && opts.method !== 'GET') return sessionLost().then(function () { return { ok: false, status: 404, data: data, lost: true }; });
          return { ok: res.ok, status: res.status, data: data };
        });
      })
      .catch(function () { return { ok: false, status: 0, data: { error: 'The server could not be reached. Check that it is running and try again.' } }; });
  }
  function sessionLost() {
    if (!state.wsId) return Promise.resolve();
    return fetch('/api/session', { headers: { 'Accept': 'application/json' } }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (d && d.workspace && d.workspace.id !== state.wsId) {
        state.wsId = d.workspace.id; state.csrf = d.csrf; state.messages = []; state.draft = null; state.turn = null; state.plan = null;
        renderUnderstood(); renderPlan();
        notice($('#understood'), 'bad', 'Your session was lost (the browser cookie was cleared or expired), so earlier plans are out of reach and this was not saved. A fresh workspace has started: describe your event again, or use a recovery phrase.');
      }
    }).catch(function () { /* offline: the caller's own message stands */ });
  }
  function errorText(r, fallback) { return (r && r.data && (r.data.error || r.data.detail)) ? String(r.data.error || r.data.detail) : fallback + ' (' + (r ? r.status : '?') + ').'; }
  function notice(into, kind, text) { into.insertBefore(h('p', { class: 'notice notice--' + kind, role: kind === 'bad' ? 'alert' : null, text: text }), into.firstChild); }

  /* ------------------------------------------------------------ the brief */
  var FIELD = [['event_type', 'Event'], ['community_subtype', 'Kind of gathering'], ['guest_count', 'Guests'], ['city', 'City'], ['event_date', 'Date'], ['budget_total', 'Budget'], ['dietary', 'Food'], ['venue_setting', 'Setting'], ['services', 'Services'], ['reminders', 'Reminders']];
  function fieldValue(d, f) {
    var v = d[f];
    if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) return null;
    if (f === 'budget_total') return inr(v);
    if (f === 'event_date') return longDate(v);
    if (Array.isArray(v)) return v.map(cap).join(', ');
    return f === 'event_type' || f === 'venue_setting' ? cap(v) : String(v);
  }
  function fieldLabel(f) { for (var i = 0; i < FIELD.length; i++) if (FIELD[i][0] === f) return FIELD[i][1]; return words(f); }

  function renderUnderstood() {
    var box = clear($('#understood'));
    var t = state.turn;
    if (!t) return;
    var facts = h('dl', { class: 'facts' });
    FIELD.forEach(function (p) { var v = fieldValue(t.draft, p[0]); if (v !== null) facts.appendChild(h('dt', { text: p[1] })); if (v !== null) facts.appendChild(h('dd', { text: v })); });
    var card = h('div', { class: 'card' }, h('h2', { text: 'What I understood' }), facts);
    var notes = [].concat(t.flags && t.flags.length ? ['Please double-check: ' + t.flags.map(fieldLabel).join(', ')] : [], t.notes || []);
    if (notes.length) card.appendChild(h('ul', { class: 'notes' }, notes.map(function (n) { return h('li', { text: n }); })));
    box.appendChild(card);

    if (t.choices && t.choices.length) {
      t.choices.forEach(function (c) {
        box.appendChild(h('div', { class: 'card', role: 'group', 'aria-label': c.prompt },
          h('p', { class: 'question', text: c.prompt }),
          h('div', { class: 'chips' }, c.options.map(function (o) { return h('button', { type: 'button', class: 'chip', text: o, onclick: function () { sendBrief(o); } }); }))));
      });
      return;
    }
    if (t.ready) {
      box.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'btn', id: 'build', text: 'Build my plan', onclick: build, disabled: state.busy })));
      box.appendChild(h('p', { class: 'hint', text: 'Nothing is planned until you press this. Add more detail above to change what was understood.' }));
    } else {
      box.appendChild(h('p', { class: 'question', text: t.question || 'Tell me more about the event.' }));
      if (t.missing && t.missing.length) box.appendChild(h('p', { class: 'hint', text: 'Still missing: ' + t.missing.map(fieldLabel).join(', ') }));
    }
  }

  function sendBrief(text) {
    text = String(text || '').trim();
    if (!text || state.busy) return Promise.resolve();
    state.messages.push(text);
    $('#brief-input').value = '';
    setBusy(true);
    return api('/api/intake', { method: 'POST', body: { messages: state.messages, draft: state.draft } }).then(function (r) {
      setBusy(false);
      if (!r.ok || !r.data) {
        state.messages.pop();
        if (!r.lost) { clear($('#understood')); notice($('#understood'), 'bad', errorText(r, 'The brief could not be read')); }
        return;
      }
      state.turn = r.data; state.draft = r.data.draft;
      renderUnderstood();
      announce(r.data.ready ? 'Everything needed is understood. Press Build my plan.' : (r.data.question || 'Tell me more about the event.'));
      var focus = $('#build') || $('#brief-input');
      if (focus) focus.focus();
    });
  }

  function setBusy(on) {
    state.busy = on;
    ['#read-brief', '#build'].forEach(function (s) { var b = $(s); if (b) b.disabled = on; });
    $('#read-brief').textContent = on ? 'Reading...' : 'Read my brief';
  }

  function build() {
    if (state.busy || !state.draft) return;
    setBusy(true);
    var source = state.turn && /^[A-Za-z0-9:._-]+$/.test(state.turn.source || '') ? state.turn.source : 'rule:parser';
    announce('Building the plan and checking every hard constraint.');
    api('/api/intake/confirm', { method: 'POST', body: { draft: state.draft, raw_text: state.messages.join('\n').slice(0, 2000), source: source } }).then(function (r) {
      setBusy(false);
      if (r.ok && r.data && r.data.plan) { state.plan = r.data.plan; state.reality = r.data.reality || null; state.tab = 'overview'; refreshPlans(r.data.plan.plan_id); renderPlan(); announce('Plan ready.'); $('#plan-panel').focus(); return; }
      if (!r.lost) renderFailure(r);
    });
  }

  /* ------------------------------------------------------------ plan list */
  function refreshPlans(select) {
    api('/api/plans').then(function (r) {
      var sel = clear($('#plan-select'));
      var rows = r.ok && Array.isArray(r.data) ? r.data : [];
      $('#plan-picker').hidden = !rows.length;
      sel.appendChild(h('option', { value: '', text: 'Choose a plan...' }));
      rows.forEach(function (p) { sel.appendChild(h('option', { value: p.plan_id, text: cap(p.event_type) + ' in ' + p.city + ', ' + longDate(p.event_date) + ' (version ' + p.version + ')' })); });
      if (select) sel.value = select;
    });
  }
  function openPlan(id) {
    if (!id) return;
    api('/api/plans/' + encodeURIComponent(id)).then(function (r) {
      if (!r.ok || !r.data) { notice($('#understood'), 'bad', errorText(r, 'That plan could not be opened')); return; }
      state.plan = r.data; state.reality = null; state.tab = 'overview'; renderPlan(); announce('Plan opened.');
    });
  }

  /* ------------------------------------------------------------ a plan that could not be made */
  function renderFailure(r) {
    var view = clear($('#plan-view'));
    $('#plan-empty').hidden = true; view.hidden = false;
    var d = r.data || {};
    view.appendChild(h('div', { class: 'plan-head' }, h('div', null, h('h2', { text: 'No plan yet' }), h('p', { text: 'The plan could not pass the hard checks, so nothing was stored.' })), h('span', { class: 'stamp stamp--bad', text: 'Not built' })));
    if (d.violations && d.violations.length) {
      view.appendChild(h('h3', { text: 'What blocked it' }));
      view.appendChild(h('ul', { class: 'list' }, d.violations.map(function (v) { return h('li', null, h('strong', { text: words(v.constraint) + ': ' }), v.detail, v.repair ? h('span', { class: 'muted', text: ' To fix: ' + v.repair }) : null); })));
    } else {
      view.appendChild(h('p', { class: 'notice notice--bad', role: 'alert', text: errorText(r, 'The plan could not be built') }));
    }
    var reality = d.reality || state.reality;
    if (reality && reality.message) view.appendChild(h('p', { class: 'notice notice--warn', text: 'Budget reality: ' + reality.message }));
    var box = h('div', { id: 'tradeoffs' });
    view.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'btn', id: 'tradeoff-btn', text: 'Show verified trade-offs', onclick: function (ev) { loadTradeoffs(ev.currentTarget, box); } })));
    view.appendChild(h('p', { class: 'hint', text: 'Each option is built and validated as a full plan before it is shown. Nothing changes until you pick one.' }));
    view.appendChild(box);
  }
  function loadTradeoffs(btn, box) {
    btn.disabled = true; btn.textContent = 'Checking every option...';
    api('/api/tradeoffs', { method: 'POST', body: { request: state.messages.join('\n').slice(0, 2000) } }).then(function (r) {
      btn.disabled = false; btn.textContent = 'Show verified trade-offs'; clear(box);
      if (!r.ok || !r.data) { notice(box, 'bad', errorText(r, 'Trade-offs could not be worked out')); return; }
      var t = r.data;
      if (t.status === 'not_needed') { box.appendChild(h('p', { class: 'notice notice--ok', text: 'This brief needs no trade-off. Press Build my plan again.' })); return; }
      if (t.status === 'no_option') { box.appendChild(h('p', { class: 'notice notice--warn', text: 'No single change makes this work.' })); box.appendChild(h('ul', { class: 'list' }, (t.violations || []).map(function (v) { return h('li', { text: words(v.constraint) + ': ' + v.detail }); }))); return; }
      box.appendChild(h('ul', { class: 'list' }, t.options.map(function (o) {
        return h('li', null, h('strong', { text: o.title }), h('p', { class: 'muted', text: o.detail }),
          h('p', { class: 'small muted', text: 'Estimated total ' + inr(o.estimated_total) + (o.needs_verification && o.needs_verification.length ? '. Still to verify: ' + o.needs_verification.join(', ') : '. Fully verified.') }),
          h('div', { class: 'row' }, h('button', { type: 'button', class: 'btn', text: 'Use this option', onclick: function (ev) { applyTradeoff(o, ev.currentTarget); } })));
      })));
    });
  }
  function applyTradeoff(option, btn) {
    btn.disabled = true;
    api('/api/tradeoffs/apply', { method: 'POST', body: { option: option } }).then(function (r) {
      btn.disabled = false;
      if (r.ok && r.data && r.data.plan) { state.plan = r.data.plan; state.tab = 'overview'; refreshPlans(r.data.plan.plan_id); renderPlan(); announce('Plan ready.'); return; }
      notice($('#tradeoffs'), 'bad', errorText(r, 'That option could not be applied'));
    });
  }

  /* ------------------------------------------------------------ the plan */
  var TABS = [['overview', 'Overview'], ['timeline', 'Timeline'], ['vendors', 'Vendors'], ['budget', 'Budget'], ['evidence', 'Evidence'], ['changes', 'Changes']];

  function stamp(plan) {
    var blocking = (plan.validation.unverified || []).filter(function (u) { return u.blocks_verified !== false; });
    if (blocking.length) return ['warn', 'Needs verification'];
    if (!plan.symbolic || plan.symbolic.status !== 'valid') return ['warn', 'Checked'];
    return ['ok', 'Verified'];
  }

  function renderPlan() {
    var plan = state.plan, view = clear($('#plan-view'));
    $('#plan-empty').hidden = !!plan; view.hidden = !plan;
    if (!plan) return;
    var req = plan.request, st = stamp(plan);
    view.appendChild(h('div', { class: 'plan-head' },
      h('div', null, h('h2', { text: cap(req.event_type) + ' in ' + req.city }), h('p', { text: longDate(req.event_date) + ' · ' + plural(req.guest_count, 'guest', 'guests') + ' · version ' + plan.version })),
      h('span', { class: 'stamp stamp--' + st[0], text: st[1] })));
    var tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Plan sections' });
    TABS.forEach(function (t) {
      tabs.appendChild(h('button', { type: 'button', role: 'tab', class: 'tab', id: 'tab-' + t[0], 'aria-selected': String(state.tab === t[0]), 'aria-controls': 'tabpanel', tabindex: state.tab === t[0] ? '0' : '-1', text: t[1],
        onclick: function () { selectTab(t[0]); },
        onkeydown: function (ev) { tabKey(ev, t[0]); } }));
    });
    view.appendChild(tabs);
    view.appendChild(h('div', { id: 'tabpanel', role: 'tabpanel' }));
    renderTab();
  }
  function selectTab(name) { state.tab = name; var tabs = document.querySelectorAll('.tab'); Array.prototype.forEach.call(tabs, function (b) { var on = b.id === 'tab-' + name; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; }); renderTab(); }
  function tabKey(ev, name) {
    var i = TABS.map(function (t) { return t[0]; }).indexOf(name), n = TABS.length, to = null;
    if (ev.key === 'ArrowRight') to = (i + 1) % n; else if (ev.key === 'ArrowLeft') to = (i + n - 1) % n; else if (ev.key === 'Home') to = 0; else if (ev.key === 'End') to = n - 1;
    if (to === null) return;
    ev.preventDefault(); selectTab(TABS[to][0]); $('#tab-' + TABS[to][0]).focus();
  }
  function renderTab() {
    var panel = clear($('#tabpanel'));
    panel.id = 'tabpanel'; panel.setAttribute('aria-labelledby', 'tab-' + state.tab);
    ({ overview: tabOverview, timeline: tabTimeline, vendors: tabVendors, budget: tabBudget, evidence: tabEvidence, changes: tabChanges })[state.tab](panel);
  }

  function tile(label, value) { return h('div', { class: 'tile' }, h('div', { class: 'tile__label', text: label }), h('div', { class: 'tile__value', text: value })); }

  function tabOverview(p) {
    var plan = state.plan, left = plan.request.budget_total - plan.total_cost;
    p.appendChild(h('div', { class: 'tiles' }, tile('Vendors', inr(plan.total_cost)), tile('Budget', inr(plan.request.budget_total)), tile(left >= 0 ? 'Left over' : 'Over budget', inr(Math.abs(left))), tile('Guests', String(plan.request.guest_count))));
    var checks = plan.validation.checks || [], ok = checks.filter(function (c) { return c.passed; }).length;
    p.appendChild(h('h3', { text: 'Independent checks' }));
    p.appendChild(h('p', { class: 'muted', text: ok + ' of ' + checks.length + ' hard checks passed. A plan version exists only if the validator passes it, and the validator re-derives every fact from the data.' }));
    if (plan.symbolic) p.appendChild(h('p', { class: 'muted small', text: 'Symbolic check: ' + words(plan.symbolic.status) + (plan.symbolic.detail ? ' (' + plan.symbolic.detail + ')' : '') }));
    var un = plan.validation.unverified || [];
    if (un.length) {
      p.appendChild(h('h3', { text: 'Still undecided' }));
      p.appendChild(h('ul', { class: 'list' }, un.map(function (u) { return h('li', null, h('strong', { text: (u.label || u.name) + '. ' }), u.detail, h('span', { class: 'muted', text: ' To fix: ' + u.repair })); })));
    }
    p.appendChild(h('details', null, h('summary', { text: 'Show all ' + checks.length + ' checks' }),
      h('ul', { class: 'list checks' }, checks.map(function (c) { return h('li', null, h('span', { class: 'mark mark--' + (c.passed ? 'ok' : 'bad'), text: c.passed ? 'Passed' : 'Failed' }), h('span', null, h('strong', { text: words(c.name) }), h('span', { class: 'muted', text: ' ' + c.detail }))); }))));
    var notes = [].concat(plan.warnings || [], plan.validation.warnings || []);
    if (notes.length) { p.appendChild(h('h3', { text: 'Warnings' })); p.appendChild(h('ul', { class: 'list' }, notes.map(function (w) { return h('li', { text: w }); }))); }
    if (state.reality && state.reality.message) p.appendChild(h('p', { class: 'notice notice--ok', text: state.reality.message }));
    p.appendChild(h('h3', { text: 'Why this plan' }));
    var why = h('div', { class: 'explain' });
    p.appendChild(why);
    p.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'btn btn--ghost', text: 'Explain this plan', onclick: function (ev) { explain(ev.currentTarget, why); } })));
    if (plan.request.assumptions && plan.request.assumptions.length) p.appendChild(h('details', null, h('summary', { text: 'What was assumed' }), h('ul', { class: 'list' }, plan.request.assumptions.map(function (a) { return h('li', { text: a }); }))));
  }
  function explain(btn, into) {
    btn.disabled = true;
    api('/api/plans/' + encodeURIComponent(state.plan.plan_id) + '/explanation?version=' + state.plan.version).then(function (r) {
      btn.disabled = false; clear(into);
      if (!r.ok || !r.data) { notice(into, 'bad', errorText(r, 'The explanation could not be loaded')); return; }
      btn.hidden = true; markdown(into, r.data.markdown);
    });
  }
  function markdown(into, md) { /* headings, bullets and paragraphs only; every line becomes text, never HTML */
    var list = null;
    String(md || '').split('\n').forEach(function (line) {
      var m = /^(#{1,4})\s+(.*)$/.exec(line), b = /^[-*]\s+(.*)$/.exec(line);
      if (b) { if (!list) { list = h('ul'); into.appendChild(list); } list.appendChild(h('li', { text: b[1] })); return; }
      list = null;
      if (m) into.appendChild(h('h3', { text: m[2].replace(/`/g, '') }));
      else if (line.trim()) into.appendChild(h('p', { text: line.replace(/`/g, '') }));
    });
  }

  function tabTimeline(p) {
    var items = (state.plan.items || []).slice().sort(function (a, b) { return a.start < b.start ? -1 : a.start > b.start ? 1 : 0; });
    var by = {}, order = [];
    items.forEach(function (i) { var k = dayKey(i.start); if (!by[k]) { by[k] = []; order.push(k); } by[k].push(i); });
    var eventDay = state.plan.request.event_date;
    order.forEach(function (day) {
      p.appendChild(h('h3', { class: 'day', text: longDate(day) + (day === eventDay ? ' (event day)' : '') }));
      p.appendChild(h('ul', { class: 'list timeline' }, by[day].map(function (i) { return h('li', null, h('span', { class: 'when', text: clock(i.start) + ' to ' + clock(i.end) }), h('span', null, i.name, i.resource ? h('span', { class: 'tag', text: words(i.resource) }) : null)); })));
    });
    p.appendChild(h('p', { class: 'hint', text: 'Times come from the constraint solver and were checked again by the validator. Overlaps on a shared resource are not possible in a plan that exists.' }));
  }

  function tabVendors(p) {
    var sel = state.plan.selections || [];
    p.appendChild(h('div', { class: 'vendors' }, sel.map(function (s) {
      var card = h('div', { class: 'vendor' }, h('div', { class: 'vendor__cat', text: s.category }), h('div', { class: 'vendor__name', text: s.name }),
        h('div', { class: 'vendor__cost' }, inr(s.cost), h('span', { class: 'tag', text: s.booking_status })), h('p', { text: s.justification }));
      if (s.alternatives && s.alternatives.length) card.appendChild(h('details', null, h('summary', { text: plural(s.alternatives.length, 'alternative', 'alternatives') + ' considered' }), h('ul', { class: 'alts' }, s.alternatives.map(function (a) { return h('li', { text: a.option + ': ' + a.outcome + '. ' + a.reason_not_chosen }); }))));
      else if (s.no_alternative_reason) card.appendChild(h('p', { class: 'small', text: s.no_alternative_reason }));
      card.appendChild(h('p', { class: 'small', text: 'Source: ' + s.source + '. Prices are estimates from the vendor database, not quotes.' }));
      return card;
    })));
  }

  function tabBudget(p) {
    var plan = state.plan, lines = (plan.allocation && plan.allocation.lines) || [];
    var cost = {}; (plan.selections || []).forEach(function (s) { cost[s.category] = s.cost; });
    p.appendChild(h('h3', { text: 'Where the budget goes' }));
    lines.forEach(function (l) {
      var spent = cost[l.category] || 0, over = spent > l.amount, fill = h('div', { class: 'bar__fill' + (over ? ' bar__fill--over' : '') });
      fill.style.width = Math.min(100, l.amount ? Math.round((spent / l.amount) * 100) : 0) + '%';
      p.appendChild(h('div', { class: 'line-item' }, h('div', { class: 'line-item__top' }, h('span', { text: cap(l.category) }), h('span', { text: inr(spent) + ' of ' + inr(l.amount) })), h('div', { class: 'bar' }, fill),
        h('div', { class: 'muted small', text: Math.round(l.share * 100) + '% of the budget' + (over ? ', over its share' : '') })));
    });
    p.appendChild(h('div', { class: 'line-item' }, h('div', { class: 'line-item__top' }, h('span', { text: 'Contingency' }), h('span', { text: inr(plan.allocation.contingency) })), h('div', { class: 'muted small', text: 'Held back for surprises; not spent on vendors.' })));
    p.appendChild(h('div', { class: 'line-item' }, h('div', { class: 'line-item__top' }, h('span', { text: 'Budget' }), h('span', { text: inr(plan.request.budget_total) }))));
    if (state.reality && state.reality.message) p.appendChild(h('p', { class: 'notice notice--ok', text: state.reality.message }));
    if (state.reality && state.reality.basis) p.appendChild(h('p', { class: 'hint', text: state.reality.basis }));
  }

  /* ------------------------------------------------------------ evidence */
  var LADDER = [['suggested', 'Suggested', "A model's idea. Never feeds a hard constraint."], ['discovered', 'Discovered', 'A listing or page says it exists.'], ['extracted', 'Extracted', 'A value read from a page or reply, not yet reviewed.'],
    ['user_confirmed', 'Confirmed', 'You said it is right. Shown as "you told us", never trusted as verified.'], ['verified', 'Verified', 'Confirmed, then re-checked by code.'], ['booked', 'Booked', 'You state it is booked. The plan holds it in place.']];
  function tabEvidence(p) {
    p.appendChild(h('p', { class: 'muted', text: 'Every fact says how far to trust it. A model can only suggest. Only you can confirm or book. Only code can verify.' }));
    var box = h('div', { class: 'states' }, h('p', { class: 'muted', text: 'Loading...' }));
    p.appendChild(box);
    api('/api/evidence').then(function (r) {
      clear(box);
      if (!r.ok || !r.data) { notice(box, 'bad', errorText(r, 'Evidence could not be loaded')); return; }
      var total = 0;
      LADDER.forEach(function (s) {
        var rows = r.data[s[0]] || []; total += rows.length;
        var node = h('div', { class: 'state' }, h('div', { class: 'state__head' }, h('span', { class: 'state__name', text: s[1] }), h('span', { class: 'state__count', text: plural(rows.length, 'fact', 'facts') })), h('div', { class: 'state__what', text: s[2] }));
        rows.slice(0, 50).forEach(function (rec) {
          var src = h('span', { class: 'muted', text: ' · ' + rec.source });
          var link = rec.url && /^https:\/\//.test(rec.url) ? h('a', { href: rec.url, rel: 'noopener noreferrer', target: '_blank', text: ' (page)' }) : null;
          node.appendChild(h('div', { class: 'rec' }, h('span', { class: 'rec__field', text: words(String(rec.field).replace(/:[0-9a-f]{8,}.*$/, '')) + ': ' }), String(rec.value === null ? 'unknown' : (typeof rec.value === 'object' ? JSON.stringify(rec.value) : rec.value)).slice(0, 300), src, link));
        });
        if (rows.length > 50) node.appendChild(h('p', { class: 'hint', text: plural(rows.length - 50, 'more fact', 'more facts') + ' not shown.' }));
        box.appendChild(node);
      });
      if (!total) box.insertBefore(h('p', { class: 'notice notice--warn', text: 'Nothing has been found or confirmed yet. Vendor discovery, contact finding and page watches write here.' }), box.firstChild);
    });
  }

  /* ------------------------------------------------------------ changes */
  function tabChanges(p) {
    var plan = state.plan;
    p.appendChild(h('h3', { text: 'Change something' }));
    p.appendChild(h('p', { class: 'muted', text: 'Say what changed in your own words: the caterer pulled out, now 80 guests, move it to 20 December, budget 1.5 lakh. You see what was understood before anything changes.' }));
    var area = h('textarea', { id: 'change-input', rows: '3', maxlength: '1900', 'aria-label': 'Describe the change' });
    var out = h('div', { id: 'change-out' });
    p.appendChild(area);
    p.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'btn', text: 'Read my change', onclick: function (ev) { readChange(area, ev.currentTarget, out); } })));
    p.appendChild(out);
    p.appendChild(h('h3', { text: 'Versions' }));
    var vbox = h('div', null, h('p', { class: 'muted', text: 'Loading...' }));
    p.appendChild(vbox);
    api('/api/plans/' + encodeURIComponent(plan.plan_id) + '/versions').then(function (r) {
      clear(vbox);
      if (!r.ok || !Array.isArray(r.data)) { notice(vbox, 'bad', errorText(r, 'Versions could not be loaded')); return; }
      vbox.appendChild(h('ul', { class: 'list' }, r.data.slice().reverse().map(function (v) { return h('li', null, h('strong', { text: 'Version ' + v.version }), ' · ' + inr(v.total_cost) + ' · ' + plural(v.guest_count, 'guest', 'guests'), h('div', { class: 'muted small', text: v.change_reason })); })));
      if (r.data.length > 1) {
        var last = r.data[r.data.length - 1], prev = r.data[r.data.length - 2], d = h('div', { class: 'diff' });
        vbox.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'btn btn--ghost', text: 'What changed in version ' + last.version, onclick: function (ev) { showDiff(plan.plan_id, prev.version, last.version, ev.currentTarget, d); } })));
        vbox.appendChild(d);
      }
    });
  }
  function showDiff(id, a, b, btn, into) {
    btn.disabled = true;
    api('/api/plans/' + encodeURIComponent(id) + '/diff?old=' + a + '&new=' + b).then(function (r) { btn.disabled = false; clear(into); if (!r.ok || !r.data) { notice(into, 'bad', errorText(r, 'The comparison could not be loaded')); return; } diffList(into, r.data); });
  }
  function diffList(into, d) {
    var rows = [];
    [['added', 'Added'], ['removed', 'Removed'], ['changed', 'Changed'], ['preserved', 'Kept']].forEach(function (g) { (d[g[0]] || []).forEach(function (i) { rows.push([g[1], i]); }); });
    if (!rows.length) { into.appendChild(h('p', { class: 'muted', text: 'Nothing differs.' })); return; }
    into.appendChild(h('ul', { class: 'list diff' }, rows.map(function (x) { var i = x[1]; return h('li', null, h('span', { class: 'badge', text: x[0] }), h('span', null, h('strong', { text: words(i.key) + ': ' }), (i.before ? i.before + ' → ' : '') + (i.after || ''), i.note ? h('span', { class: 'muted', text: ' ' + i.note }) : null)); })));
  }

  function readChange(area, btn, out) {
    var text = area.value.trim();
    if (!text) { area.focus(); return; }
    btn.disabled = true; clear(out);
    api('/api/plans/' + encodeURIComponent(state.plan.plan_id) + '/changes', { method: 'POST', body: { message: text } }).then(function (r) {
      btn.disabled = false;
      if (!r.ok || !r.data) { if (!r.lost) notice(out, 'bad', errorText(r, 'The change could not be read')); return; }
      state.delta = r.data; renderDelta(out);
    });
  }
  function vendorName(id) { var s = (state.plan.selections || []).filter(function (x) { return x.vendor_id === id; })[0]; return s ? s.name + ' (' + s.category + ')' : id; }
  function renderDelta(out) {
    clear(out);
    var t = state.delta, d = t.delta, lines = [];
    (d.cancel_vendor || []).forEach(function (v) { lines.push('Let go of ' + vendorName(v) + ' and find another'); });
    if (d.set_budget_inr) lines.push('Budget becomes ' + inr(d.set_budget_inr));
    if (d.set_guest_count) lines.push('Guests become ' + d.set_guest_count);
    if (d.set_date) lines.push('The event moves to ' + longDate(d.set_date));
    var tasks = d.add_tasks || [], lengthInputs = [];
    tasks.forEach(function (x, i) { lines.push('Add "' + x.name + '"' + (x.days_before_event ? ', ' + plural(x.days_before_event, 'day', 'days') + ' before the event' : ' on the event day') + (x.duration_minutes ? ' (' + x.duration_minutes + ' min)' : '')); });
    var card = h('div', { class: 'card' }, h('h3', { text: 'What I understood' }));
    if (lines.length) card.appendChild(h('ul', { class: 'list' }, lines.map(function (l) { return h('li', { text: l }); })));
    (t.questions || []).forEach(function (q) { card.appendChild(h('p', { class: 'question', text: q.question })); });
    (t.notes || []).forEach(function (n) { card.appendChild(h('p', { class: 'hint', text: n })); });
    tasks.forEach(function (x, i) {
      if (x.duration_minutes) return;
      var inp = h('input', { type: 'number', min: '1', max: '1440', id: 'len-' + i, 'aria-label': 'Minutes for ' + x.name });
      lengthInputs.push([i, inp]);
      card.appendChild(h('div', { class: 'field' }, h('label', { for: 'len-' + i, text: 'How many minutes does "' + x.name + '" take?' }), inp));
    });
    var finished = finishedPicker();
    if (lines.length) {
      card.appendChild(finished.node);
      card.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'btn', text: 'Apply this change', onclick: function (ev) { applyChange(ev.currentTarget, out, lengthInputs, finished.get); } })));
      card.appendChild(h('p', { class: 'hint', text: 'This makes a new version. Confirmed bookings are kept unless the change releases them.' }));
    }
    out.appendChild(card);
  }
  function finishedPicker() {
    var items = (state.plan.items || []).filter(function (i) { return /^book_/.test(i.task_id) || /^extra_/.test(i.task_id); }), boxes = [];
    var node = h('details', null, h('summary', { text: 'Some of this is already done' }), h('p', { class: 'hint', text: 'Tick finished work. It stays exactly where it is and what depends on it waits for it.' }));
    items.forEach(function (i) { var c = h('input', { type: 'checkbox', id: 'done-' + i.task_id, value: i.task_id }); boxes.push(c); node.appendChild(h('div', { class: 'field' }, c, ' ', h('label', { for: 'done-' + i.task_id, text: i.name }))); });
    return { node: items.length ? node : h('span'), get: function () { return boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }); } };
  }
  function applyChange(btn, out, lengthInputs, doneOf) {
    var delta = JSON.parse(JSON.stringify(state.delta.delta));
    for (var k = 0; k < lengthInputs.length; k++) {
      var n = parseInt(lengthInputs[k][1].value, 10);
      if (!(n >= 1 && n <= 1440)) { lengthInputs[k][1].focus(); return; }
      delta.add_tasks[lengthInputs[k][0]].duration_minutes = n;
    }
    btn.disabled = true;
    api('/api/plans/' + encodeURIComponent(state.plan.plan_id) + '/changes/apply', { method: 'POST', body: { delta: delta, done: doneOf(), in_flight: {} } }).then(function (r) {
      btn.disabled = false;
      if (r.ok && r.data && r.data.plan) {
        state.plan = r.data.plan; state.delta = null; state.tab = 'changes'; refreshPlans(r.data.plan.plan_id); renderPlan(); announce('Version ' + r.data.plan.version + ' created.');
        var box = $('#change-out'); if (box && r.data.diff) { box.appendChild(h('p', { class: 'notice notice--ok', text: 'Version ' + r.data.plan.version + ' created. What changed:' })); var d = h('div', { class: 'diff' }); box.appendChild(d); diffList(d, r.data.diff); }
        return;
      }
      if (r.lost) return;
      var why = (r.data && r.data.violations || []).map(function (v) { return words(v.constraint) + ': ' + v.detail + (v.repair ? ' To fix: ' + v.repair : ''); });
      notice(out, r.status === 409 ? 'warn' : 'bad', (r.status === 409 ? 'This needs your decision. ' : 'The change was not applied; nothing was saved. ') + (why.join(' ') || errorText(r, 'The change failed')));
    });
  }

  /* ------------------------------------------------------------ workspace dialog */
  function download(blob, name) { var u = URL.createObjectURL(blob), a = h('a', { href: u, download: name }); document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(function () { URL.revokeObjectURL(u); }, 4000); }
  function wmsg(t) { $('#workspace-msg').textContent = t; }
  function setupWorkspace() {
    var dlg = $('#workspace-dialog');
    $('#workspace-open').addEventListener('click', function () {
      $('#workspace-mode').textContent = state.mode === 'shared' ? 'This is a shared server: your workspace is private to this browser.' : 'This is your own server: everything is stored in its data folder.';
      $('#shared-only').hidden = state.mode !== 'shared'; $('#phrase-out').hidden = true; wmsg('');
      if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    });
    $('#workspace-close').addEventListener('click', function () { if (typeof dlg.close === 'function') dlg.close(); else dlg.removeAttribute('open'); });
    $('#export-data').addEventListener('click', function () {
      wmsg('Preparing your export...');
      fetch('/api/workspace/export', { headers: { 'X-EventMate-CSRF': state.csrf } }).then(function (res) { if (!res.ok) throw new Error('status ' + res.status); return res.blob(); })
        .then(function (b) { download(b, 'todoh-export.zip'); wmsg('Your export has been downloaded.'); }).catch(function () { wmsg('The export could not be made.'); });
    });
    $('#phrase-make').addEventListener('click', function () {
      api('/api/workspace/recovery', { method: 'POST', body: {} }).then(function (r) {
        if (!r.ok || !r.data) { wmsg(errorText(r, 'A phrase could not be made')); return; }
        var o = $('#phrase-out'); o.textContent = r.data.phrase; o.hidden = false; wmsg('Write this down now. It is shown once, and making another one cancels this one.');
      });
    });
    $('#recover-form').addEventListener('submit', function (ev) {
      ev.preventDefault();
      api('/api/workspace/recover', { method: 'POST', body: { phrase: $('#recover-input').value } }).then(function (r) {
        if (!r.ok || !r.data) { wmsg(r.status === 409 ? 'This browser already holds a workspace with plans. Export or delete it first.' : errorText(r, 'That phrase did not work')); return; }
        var o = $('#phrase-out'); o.textContent = r.data.phrase; o.hidden = false; wmsg('Recovered. Your new recovery phrase is shown above; the old one no longer works. Reloading...');
        window.setTimeout(function () { window.location.reload(); }, 2500);
      });
    });
    $('#delete-form').addEventListener('submit', function (ev) {
      ev.preventDefault();
      if ($('#delete-input').value.trim() !== 'delete') { wmsg('Type the word delete to confirm.'); return; }
      api('/api/workspace', { method: 'DELETE', body: { confirm: 'delete' } }).then(function (r) { if (r.ok) window.location.assign('/'); else wmsg(errorText(r, 'The workspace could not be deleted')); });
    });
  }

  /* ------------------------------------------------------------ start */
  function init() {
    $('#brief-form').addEventListener('submit', function (ev) { ev.preventDefault(); sendBrief($('#brief-input').value); });
    $('#brief-input').addEventListener('keydown', function (ev) { if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); sendBrief($('#brief-input').value); } });
    $('#start-over').addEventListener('click', function () { state.messages = []; state.draft = null; state.turn = null; $('#brief-input').value = ''; renderUnderstood(); $('#brief-input').focus(); announce('Started over.'); });
    $('#plan-select').addEventListener('change', function (ev) { openPlan(ev.target.value); });
    setupWorkspace();
    api('/api/session').then(function (r) {
      if (!r.ok || !r.data) { notice($('#understood'), 'bad', errorText(r, 'The planner could not start')); return; }
      state.csrf = r.data.csrf; state.mode = r.data.workspace ? r.data.workspace.mode : 'local'; state.wsId = state.mode === 'shared' ? r.data.workspace.id : null;
      refreshPlans();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
}());
