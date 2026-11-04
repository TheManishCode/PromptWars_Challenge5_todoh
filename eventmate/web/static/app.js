/* to;doh dashboard (/app). Three panes over the real API: plans and filters | calendar and tiles | assistant, plus the plan dossier.
   Rules this file keeps: it computes no fact (it shows what the API returned and the state each fact is in), text from the server goes in only with textContent,
   and there are no inline handlers or styles (the CSP forbids them; styles are set through the CSSOM). */
(function () {
  'use strict';
  /* lite (?lite=1 or the browser's data-saver): no animation, same content */
  if (/[?&]lite=1\b/.test(location.search) || (navigator.connection && navigator.connection.saveData)) document.documentElement.classList.add('lite');

  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  /* what a data source's licence or terms ask to be shown wherever its data appears (OpenStreetMap: ODbL 1.0; Google Maps Platform: its attribution rule) */
  var ATTRIBUTION = { osm: ' \u00b7 \u00a9 OpenStreetMap contributors (ODbL)', gplaces: ' \u00b7 Powered by Google' };
  var UNK = 'Unknown \u2014 request quote';
  var LABEL = { suggested: 'Suggested', discovered: 'Discovered', extracted: 'Extracted', user_confirmed: 'You confirmed', verified: 'Verified', booked: 'Booked' };
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  var DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var CATS = ['venue', 'caterer', 'photographer'];
  var HSTART = 6, HEND = 24, HROW = 56;
  var BUDGET_COLORS = ['#6c5ce7', '#2bb0c9', '#c8d81f', '#f08bb5', '#9a9aa6'];
  var BOOK = { book_venue: 'venue', venue_setup: 'venue', book_caterer: 'caterer', catering_setup: 'caterer', book_photographer: 'photographer', photography_prep: 'photographer' };

  var st = { csrf: null, mode: 'local', wsId: null, today: null, plans: [], plan: null, reality: null, view: 'month', cur: new Date(), layers: { milestone: true, deadline: true, approval: true },
    cats: { venue: true, caterer: true, photographer: true, other: true }, events: [], places: { venue: [], caterer: [], photographer: [], other: [] }, drafts: [], jobs: [], watches: [],
    evidence: null, assist: 'ask', delta: null, messages: [], draft: null, turn: null, busy: false };

  /* ------------------------------------------------------------ helpers */
  function h(tag, props) {
    var e = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) add(e, arguments[i]);
    return e;
  }
  function add(p, kid) {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { add(p, k); }); return; }
    p.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function chip(s) { return h('span', { class: 'chip', 'data-s': s, text: LABEL[s] || s }); }
  function announce(t) { var l = $('#live'); l.textContent = ''; window.setTimeout(function () { l.textContent = t; }, 30); }
  function iso(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function parse(s) { var p = String(s).slice(0, 10).split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function inr(n) {
    if (typeof n !== 'number') return '-';
    var s = String(Math.round(Math.abs(n))), out = '', first = true;
    while (s.length) { var take = first ? 3 : 2; out = s.slice(-take) + (out ? ',' + out : ''); s = s.slice(0, -take); first = false; }
    return (n < 0 ? '-' : '') + '\u20b9' + out;
  }
  function words(s) { return String(s || '').replace(/_/g, ' '); }
  function cap(s) { s = words(s); return s.charAt(0).toUpperCase() + s.slice(1); }
  function plural(n, a, b) { return n + ' ' + (n === 1 ? a : b); }
  function longDate(s) { var d = parse(s); return isNaN(d) ? String(s) : d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear(); }
  function hours(isoTime) { var t = String(isoTime).slice(11, 16).split(':'); return (+t[0]) + (+t[1]) / 60; }
  function hhmm(x) { var hh = Math.floor(x), mm = Math.round((x - hh) * 60); return ('0' + hh).slice(-2) + ':' + ('0' + mm).slice(-2); }
  function weekStart(d) { var x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
  function errText(r, fb) { return r && r.data && (r.data.error || r.data.detail) ? String(r.data.error || r.data.detail) : fb + ' (' + (r ? r.status : '?') + ').'; }

  /* ------------------------------------------------------------ API */
  function api(path, opts) {
    opts = opts || {};
    var headers = { 'Accept': 'application/json' };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (st.csrf) headers['X-EventMate-CSRF'] = st.csrf;
    return fetch(path, { method: opts.method || 'GET', headers: headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        if (res.status === 404 && data && data.code === 'not_found' && opts.method && opts.method !== 'GET') return lost().then(function () { return { ok: false, status: 404, data: data, lost: true }; });
        return { ok: res.ok, status: res.status, data: data };
      });
    }).catch(function () { return { ok: false, status: 0, data: { error: 'The server could not be reached. Check that it is running and try again.' } }; });
  }
  function lost() {
    if (!st.wsId) return Promise.resolve();
    return fetch('/api/session', { headers: { 'Accept': 'application/json' } }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (d && d.workspace && d.workspace.id !== st.wsId) {
        st.wsId = d.workspace.id; st.csrf = d.csrf; st.plan = null; st.plans = []; refreshAll();
        say('ai', 'Your session was lost (the browser cookie was cleared or expired), so earlier plans are out of reach and that was not saved. A fresh workspace has started; use a recovery phrase in Workspace if you made one.');
      }
    }).catch(function () { /* offline */ });
  }

  /* ------------------------------------------------------------ data to calendar events and tiles */
  function catOf(taskId, resource) { return BOOK[taskId] || (CATS.indexOf(resource) >= 0 ? resource : 'other'); }
  function selectionOf(cat) { return ((st.plan && st.plan.selections) || []).filter(function (s) { return s.category === cat; })[0] || null; }

  function buildEvents() {
    var ev = [], p = st.plan;
    if (!p) { st.events = ev; return; }
    (p.items || []).forEach(function (i) {
      var cat = catOf(i.task_id, i.resource), sel = BOOK[i.task_id] ? selectionOf(BOOK[i.task_id]) : null, booked = !!(sel && sel.booking_status === 'confirmed');
      var extra = /^extra_/.test(i.task_id), s = booked ? 'booked' : (extra ? 'user_confirmed' : 'verified');
      var start = hours(i.start), end = dayKeyOf(i.end) !== dayKeyOf(i.start) ? 24 : hours(i.end);
      ev.push({ date: dayKeyOf(i.start), start: start, end: Math.max(end, start + 0.25), title: i.name, cat: cat, layer: 'milestone', s: s, lock: booked, vendor: sel ? sel.vendor_id : null,
        facts: [['When', dayKeyOf(i.start) + ' ' + hhmm(start) + '\u2013' + hhmm(hours(i.end)), 'plan v' + p.version],
          ['Resource', i.resource ? words(i.resource) : null, 'plan v' + p.version], ['Checked by', 'Solver, then the validator', 'validator'],
          sel ? [cap(sel.category) + ' vendor', sel.name + ' (' + inr(sel.cost) + ')', sel.source] : null, booked ? ['Booking', 'Locked: you told us it is booked', 'user_input'] : null, extra ? ['Added by', 'A change you confirmed', 'user_input'] : null].filter(Boolean) });
    });
    st.jobs.forEach(function (j) {
      var got = (j.claims || []).filter(function (c) { return c.status === 'confirmed'; }).length;
      ev.push({ date: dayKeyOf(j.deadline), title: 'Quotes due: ' + j.goal, cat: CATS.indexOf(j.category) >= 0 ? j.category : 'other', layer: 'deadline', s: 'user_confirmed',
        facts: [['Status', words(j.status), 'job ' + j.job_id.slice(0, 8)], ['Asked', plural(Object.keys(j.vendors || {}).length, 'vendor', 'vendors'), 'job'], ['Confirmed quotes', got + ' of ' + j.min_quotes, 'you confirmed them'], ['Next step', j.next_action || null, 'job']] });
    });
    st.drafts.filter(function (d) { return d.status === 'prepared'; }).forEach(function (d) {
      ev.push({ date: dayKeyOf(d.created_at), title: 'Approve: ' + d.subject, cat: CATS.indexOf(d.category) >= 0 ? d.category : 'other', layer: 'approval', s: 'suggested',
        facts: [['Waiting on', 'Your approval of the exact text', 'user_input'], ['To', d.vendor_name + ' <' + d.recipient + '>', 'draft'], ['Content hash', d.content_hash.slice(0, 16) + '\u2026', 'sha256']] });
    });
    st.watches.forEach(function (w) {
      ev.push({ date: dayKeyOf(w.next_check_at), title: 'Watch: ' + w.host, cat: 'other', layer: 'deadline', s: 'discovered',
        facts: [['Condition', w.condition === 'price_below' ? 'Price below ' + inr(w.amount_inr) : (w.condition === 'contains' ? 'Mentions "' + w.text + '"' : 'Page changes'), 'watch'], ['Last checked', w.last_checked_at ? w.last_checked_at.slice(0, 16).replace('T', ' ') : null, 'web'], ['Status', words(w.status), 'watch']] });
    });
    st.events = ev;
  }
  function dayKeyOf(t) { return String(t).slice(0, 10); }

  function buildPlaces() {
    var pl = { venue: [], caterer: [], photographer: [], other: [] };
    ((st.plan && st.plan.selections) || []).forEach(function (s) {
      var booked = s.booking_status === 'confirmed';
      pl[s.category].push({ n: s.name, s: booked ? 'booked' : 'verified', v: inr(s.cost) + (booked ? ' \u00b7 locked' : ' \u00b7 in your plan'), src: s.source, lock: booked });
      (s.alternatives || []).slice(0, 2).forEach(function (a) { pl[s.category].push({ n: a.option, s: 'verified', v: a.outcome, src: 'not chosen: ' + a.reason_not_chosen.slice(0, 60), alt: true }); });
    });
    (st.shortlist || []).forEach(function (i) {
      var b = i.business, ok = !!i.contact;
      pl[i.category].push({ n: b.name, s: ok ? 'user_confirmed' : 'discovered', v: ok ? 'Contact confirmed by ' + i.contact.confirmed_by : null, label: 'Price', src: 'tool:' + b.source + (ATTRIBUTION[b.source] || '') + (b.website ? ' \u00b7 ' + b.website.replace(/^https?:\/\//, '').slice(0, 30) : '') });
    });
    ((st.ideas || [])).forEach(function (i) { pl.other.push({ n: i.title, s: 'suggested', v: i.est_cost_note, label: 'Cost', src: i.source }); });
    st.places = pl;
  }

  /* ------------------------------------------------------------ calendar */
  function visible(e) { return st.layers[e.layer] && st.cats[e.cat]; }
  function onDay(k) { return st.events.filter(function (e) { return e.date === k && visible(e); }); }
  function timeLabel(e) { return e.start != null ? hhmm(e.start) + '\u2013' + hhmm(e.end) : 'All day'; }
  function eventDay() { return st.plan ? st.plan.request.event_date : ''; }

  function evBtn(e) {
    var b = h('button', { class: 'ev', type: 'button', 'data-cat': e.cat, 'data-s': e.s, 'aria-label': e.title + ', ' + e.date + ', ' + LABEL[e.s], text: (e.lock ? '\ud83d\udd12 ' : '') + e.title });
    if (e.lock) b.setAttribute('data-lock', '');
    b.addEventListener('click', function (ev) { openPop(e, b); ev.stopPropagation(); });
    return b;
  }
  function renderMonth(box) {
    var y = st.cur.getFullYear(), m = st.cur.getMonth(), start = weekStart(new Date(y, m, 1));
    var dow = h('div', { class: 'cal__dow' }); DOW.forEach(function (d) { dow.appendChild(h('span', { text: d })); }); box.appendChild(dow);
    var grid = h('div', { class: 'cal__month' });
    for (var i = 0; i < 42; i++) {
      var d = new Date(start); d.setDate(start.getDate() + i); var k = iso(d);
      if (i >= 35 && d.getMonth() !== m) break;
      var c = h('div', { class: 'cell' + (d.getMonth() !== m ? ' out' : '') + (k === st.today ? ' today' : '') + (k === eventDay() ? ' event-day' : '') }, h('span', { class: 'dn', text: String(d.getDate()) }));
      var evs = onDay(k); evs.slice(0, 3).forEach(function (e) { c.appendChild(evBtn(e)); });
      if (evs.length > 3) c.appendChild(h('button', { class: 'more', type: 'button', text: '+' + (evs.length - 3) + ' more', onclick: function (dd) { return function () { st.cur = dd; st.view = 'day'; render(); }; }(d) }));
      grid.appendChild(c);
    }
    box.appendChild(grid);
  }
  function lanes(list) {
    var sorted = list.slice().sort(function (a, b) { return a.start - b.start || a.end - b.end; }), cols = [], out = [];
    sorted.forEach(function (e) {
      var c = 0; while (cols[c] !== undefined && cols[c] > e.start) c++;
      cols[c] = e.end; out.push({ e: e, c: c });
    });
    var n = cols.length || 1;
    out.forEach(function (o) { o.n = list.filter(function (x) { return x.start < o.e.end && x.end > o.e.start; }).length; o.n = Math.max(o.n, 1); });
    return { items: out, n: n };
  }
  function renderWeek(box, days) {
    var wk = h('div', { class: 'wk' }), head = h('div', { class: 'wk__head' }), body = h('div', { class: 'wk__body' }), H = (HEND - HSTART) * HROW;
    head.style.gridTemplateColumns = 'repeat(' + days.length + ', minmax(0, 1fr))'; body.style.gridTemplateColumns = '58px repeat(' + days.length + ', minmax(0, 1fr))';
    days.forEach(function (d) { var k = iso(d); head.appendChild(h('div', { class: 'wk__day' + (k === st.today ? ' today' : '') + (k === eventDay() ? ' event-day' : '') }, h('span', { text: DOW[(d.getDay() + 6) % 7] }), h('b', { text: String(d.getDate()) }))); });
    var hrs = h('div', { class: 'wk__hours' }); hrs.style.height = H + 'px';
    for (var x = HSTART; x < HEND; x++) { var s = h('span', { class: 'mono', text: (x > 12 ? x - 12 : x) + (x >= 12 ? ' pm' : ' am') }); s.style.top = ((x - HSTART) * HROW + 8) + 'px'; hrs.appendChild(s); }
    body.appendChild(hrs);
    days.forEach(function (d) {
      var col = h('div', { class: 'wk__col' }); col.style.height = H + 'px';
      var all = onDay(iso(d)), timed = all.filter(function (e) { return e.start != null; }), plan = lanes(timed);
      plan.items.forEach(function (o) {
        var e = o.e, b = h('button', { class: 'blk', type: 'button', 'data-cat': e.cat, 'data-s': e.s }, h('b', { text: (e.lock ? '\ud83d\udd12 ' : '') + e.title }), h('span', { class: 'mono', text: timeLabel(e) }));
        if (e.lock) b.setAttribute('data-lock', '');
        b.style.top = Math.max(0, (e.start - HSTART) * HROW) + 'px'; b.style.height = Math.max(34, (e.end - e.start) * HROW - 3) + 'px';
        var w = 100 / plan.n; b.style.left = (o.c * w) + '%'; b.style.width = 'calc(' + w + '% - 4px)'; b.style.right = 'auto';
        b.addEventListener('click', function (ev) { openPop(e, b); ev.stopPropagation(); }); col.appendChild(b);
      });
      all.filter(function (e) { return e.start == null; }).forEach(function (e, i) { var b = evBtn(e); b.style.position = 'absolute'; b.style.top = (4 + i * 26) + 'px'; b.style.left = '4px'; b.style.right = '4px'; b.style.width = 'auto'; col.appendChild(b); });
      body.appendChild(col);
    });
    wk.appendChild(head); wk.appendChild(body); box.appendChild(wk);
    var firstH = st.events.filter(function (e) { return e.start != null && visible(e) && days.some(function (d) { return iso(d) === e.date; }); }).map(function (e) { return e.start; });
    body.scrollTop = Math.max(0, ((firstH.length ? Math.min.apply(null, firstH) : 9) - HSTART) * HROW - 10);
  }
  function render() {
    var box = clear($('#cal')), y = st.cur.getFullYear(), m = st.cur.getMonth(), title;
    if (st.view === 'month') { title = MONTHS[m] + ' ' + y; renderMonth(box); }
    else if (st.view === 'week') { var ws = weekStart(st.cur), we = new Date(ws); we.setDate(ws.getDate() + 6); title = MONTHS[ws.getMonth()].slice(0, 3) + ' ' + ws.getDate() + ' \u2013 ' + (ws.getMonth() !== we.getMonth() ? MONTHS[we.getMonth()].slice(0, 3) + ' ' : '') + we.getDate() + ', ' + we.getFullYear(); var ds = []; for (var i = 0; i < 7; i++) { var d = new Date(ws); d.setDate(ws.getDate() + i); ds.push(d); } renderWeek(box, ds); }
    else { title = MONTHS[m] + ' ' + st.cur.getDate() + ', ' + y; renderWeek(box, [st.cur]); }
    $('#cal-title').textContent = title; renderMini();
    $$('.seg [data-view]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-view') === st.view)); });
  }
  function renderMini() {
    var g = clear($('#mini-grid')), y = st.cur.getFullYear(), m = st.cur.getMonth();
    $('#mini-title').textContent = MONTHS[m] + ' ' + y;
    DOW.forEach(function (d) { g.appendChild(h('span', { class: 'dow', text: d.slice(0, 2) })); });
    var start = weekStart(new Date(y, m, 1)), ws = iso(weekStart(st.cur));
    for (var i = 0; i < 42; i++) {
      var d = new Date(start); d.setDate(start.getDate() + i); if (i >= 35 && d.getMonth() !== m) break; var k = iso(d);
      var b = h('button', { type: 'button', class: (d.getMonth() !== m ? 'out ' : '') + (k === iso(st.cur) ? 'sel ' : '') + (st.view === 'week' && iso(weekStart(d)) === ws && k !== iso(st.cur) ? 'wk ' : '') + (onDay(k).length ? 'has' : ''), 'aria-label': d.getDate() + ' ' + MONTHS[d.getMonth()], text: String(d.getDate()) });
      b.addEventListener('click', function (dd) { return function () { st.cur = dd; if (st.view === 'month') st.view = 'day'; render(); }; }(d)); g.appendChild(b);
    }
  }
  function nav(dir) { var d = new Date(st.cur); if (st.view === 'month') d.setMonth(d.getMonth() + dir, 1); else if (st.view === 'week') d.setDate(d.getDate() + 7 * dir); else d.setDate(d.getDate() + dir); st.cur = d; render(); }

  /* ------------------------------------------------------------ popover: every fact shows its value, how sure we are, and where it came from */
  var pop = $('#pop'), popFrom = null;
  function openPop(e, from) {
    popFrom = from; $('#pop-t').textContent = e.title; $('#pop-when').textContent = e.date + ' \u00b7 ' + timeLabel(e);
    var dl = clear($('#pop-facts'));
    e.facts.forEach(function (f) {
      var unk = f[1] === null || f[1] === undefined, s = unk ? (e.s === 'suggested' ? 'suggested' : 'discovered') : e.s;
      dl.appendChild(h('div', null, h('dt', { text: f[0] }), h('dd', { class: unk ? 'unk' : 'mono' }, unk ? UNK : String(f[1]), ' ', chip(s), h('small', { text: 'Source: ' + f[2] }))));
    });
    if (e.lock) dl.appendChild(h('div', null, h('dt', { text: 'Locked' }), h('dd', { text: 'A confirmed booking. Nothing moves it unless you release it.' })));
    quoteControl(e); pop.hidden = false; var r = from.getBoundingClientRect(), w = pop.offsetWidth, hh = pop.offsetHeight;
    pop.style.left = Math.max(12, Math.min(innerWidth - w - 12, r.right + 10 > innerWidth - w - 12 ? r.left - w - 10 : r.right + 10)) + 'px';
    pop.style.top = Math.max(12, Math.min(innerHeight - hh - 12, r.top)) + 'px'; $('#pop-x').focus();
  }
  /* a quote file the vendor sent: read on this machine into priced lines, shown as extracted (unreviewed) until the user confirms them */
  function showQuote(out, d, vendor) {
    var days = h('input', { type: 'number', min: '1', max: '90', value: '14', 'aria-label': 'Days this price stays valid', class: 'pop__days' });
    function showEffect(row, fx) {  // what the confirmed price does to this plan, and the one tap that applies it
      if (!fx || !fx.in_plan) return;
      if (!fx.price_stale) { row.appendChild(h('small', { class: 'ok', text: 'The plan already prices this vendor at ' + inr(fx.now_costs) + '.' })); return; }
      row.appendChild(h('small', { class: 'ok', text: 'The plan priced this vendor at ' + inr(fx.plan_cost) + '; it now costs ' + inr(fx.now_costs) + '. The plan is unchanged until you re-plan.' }));
      if (!fx.covers_whole_quote) row.appendChild(h('small', { text: 'This is one line of a longer quote and now stands for the vendor\u2019s whole price' + (fx.quote_total ? '; the quote\u2019s total is ' + inr(fx.quote_total) : '') + '.' }));
      row.appendChild(h('button', { type: 'button', class: 'pill pill--k', text: 'Re-plan with this price', onclick: function (ev) {
        var b = ev.currentTarget; b.disabled = true;
        api('/api/plans/' + st.plan.plan_id + '/replan', { method: 'POST', body: { kind: 'quote_update' } }).then(function (rr) {
          if (rr.ok && rr.data && rr.data.plan) { b.replaceWith(h('small', { class: 'ok', text: 'Re-planned: version ' + rr.data.plan.version + ' uses this price.' })); refreshAll(rr.data.plan.plan_id); }
          else { b.disabled = false; row.appendChild(h('small', { text: errText(rr, 'The plan could not be re-priced') })); }
        });
      } }));
    }
    function confirmBtn(key, row) {
      return h('button', { type: 'button', class: 'pill pill--k', text: 'Confirm this price', onclick: function (ev) {
        var b = ev.currentTarget; b.disabled = true;
        api('/api/vendors/' + encodeURIComponent(vendor) + '/quote-document/' + d.document + '/lines/' + key + '/confirm', { method: 'POST', body: { valid_days: parseInt(days.value, 10) || 0, plan_id: st.plan ? st.plan.plan_id : null } }).then(function (rr) {
          if (rr.ok) { showEffect(row, rr.data.effect); var old = row.querySelector('.chip'), done = chip('user_confirmed'); if (old) { old.replaceWith(done); b.replaceWith(h('small', { class: 'ok', text: 'recorded as a quote for ' + days.value + ' days' })); } else b.replaceWith(done, h('small', { class: 'ok', text: ' recorded as a quote for ' + days.value + ' days' })); loadEvidence().then(renderEvidence); }
          else { b.disabled = false; row.appendChild(h('small', { text: errText(rr, 'That price could not be confirmed') })); }
        });
      } });
    }
    var box = h('div', { class: 'pop__doc' });
    if (!d.lines.length) box.appendChild(h('p', { class: 'foot', text: (d.warnings && d.warnings[0]) || 'No priced lines were found.' }));
    else box.appendChild(h('label', { class: 'foot' }, 'Treat a confirmed price as valid for ', days, ' days'));
    d.lines.forEach(function (ln) {
      var price = ln.basis === 'per_unit' ? inr(ln.unit_inr) + ' \u00d7 ' + ln.quantity + (ln.total_inr !== null ? ' = ' + inr(ln.total_inr) : '') : inr(ln.total_inr !== null ? ln.total_inr : ln.unit_inr);
      var row = h('div', { class: 'pop__line' }, h('b', { text: ln.label }), ' ', h('span', { class: 'mono', text: price }), ' ', chip(ln.confirmed ? 'user_confirmed' : 'extracted'), (ln.warnings || []).map(function (w) { return h('small', { text: w }); }));
      if (!ln.confirmed) { if (ln.total_inr !== null) { if (d.lines.length > 1 || d.summary.grand_total) row.appendChild(h('small', { text: 'One line of the quote: confirming it makes it this vendor\u2019s whole price.' })); row.appendChild(confirmBtn(ln.key, row)); } else row.appendChild(h('small', { text: 'It does not say what this covers, so it cannot be confirmed as a price.' })); }
      box.appendChild(row);
    });
    Object.keys(d.summary || {}).forEach(function (k) {
      var row = h('div', { class: 'pop__line' }, h('b', { text: words(k) }), ' ', h('span', { class: 'mono', text: inr(d.summary[k]) }), ' ');
      if (k === 'grand_total') { if (d.grand_total_confirmed) row.appendChild(chip('user_confirmed')); else row.appendChild(confirmBtn('grand_total', row)); }
      box.appendChild(row);
    });
    (d.warnings || []).forEach(function (w) { if (d.lines.length) box.appendChild(h('p', { class: 'notice bad', text: w })); });
    if (d.lines.length) box.appendChild(h('p', { class: 'foot', text: 'Read from the file, not confirmed. The file was not kept.' }));
    out.appendChild(box);
  }
  function quoteControl(e) {
    var box = clear($('#pop-quote')); box.hidden = !e.vendor; if (!e.vendor) return;
    var out = h('div', { class: 'pop__quote-out', 'aria-live': 'polite' }), earlier = h('div', { class: 'pop__quote-out' });
    var input = h('input', { type: 'file', accept: '.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.csv,.html,.htm,.txt,.md', hidden: true });
    api('/api/vendors/' + encodeURIComponent(e.vendor) + '/quote-documents').then(function (r) {  // files already read for this vendor, uploaded or attached to a reply
      if (r.ok && r.data && r.data.documents.length) { earlier.appendChild(h('p', { class: 'foot', text: 'Quote files already read' })); r.data.documents.forEach(function (d) { showQuote(earlier, d, e.vendor); }); }
    });
    input.addEventListener('change', function () {
      var f = input.files && input.files[0]; if (!f) return;
      clear(out).appendChild(h('p', { class: 'foot', text: 'Reading ' + f.name + '\u2026' }));
      var fd = new window.FormData(); fd.append('file', f);
      var headers = { 'Accept': 'application/json' }; if (st.csrf) headers['X-EventMate-CSRF'] = st.csrf;
      window.fetch('/api/vendors/' + encodeURIComponent(e.vendor) + '/quote-document', { method: 'POST', headers: headers, body: fd }).then(function (res) {
        return res.json().catch(function () { return null; }).then(function (d) { return { ok: res.ok, data: d }; });
      }).catch(function () { return { ok: false, data: null }; }).then(function (r) {
        clear(out); input.value = '';
        if (!r.ok || !r.data) { out.appendChild(h('p', { class: 'notice bad', text: (r.data && r.data.error) || 'That file could not be read.' })); return; }
        showQuote(out, r.data, e.vendor); loadEvidence().then(renderEvidence);
      });
    });
    box.appendChild(h('button', { type: 'button', class: 'pill', text: 'Add a quote file', onclick: function () { input.click(); } })); box.appendChild(input); box.appendChild(out); box.appendChild(earlier);
  }
  function closePop() { pop.hidden = true; if (popFrom) { popFrom.focus(); popFrom = null; } }

  /* ------------------------------------------------------------ tiles */
  function placeEl(p) {
    var b = h('div', { class: 'place', 'data-s': p.s }, h('b', null, p.lock ? lockIco() : null, p.n), chip(p.s),
      h('span', { class: 'place__v' + (p.v === null || p.v === undefined ? ' unk' : ' mono') }, p.v === null || p.v === undefined ? (p.label ? p.label + ': ' : '') + UNK : String(p.v)), h('span', { class: 'place__src mono', text: p.src || '' }));
    return b;
  }
  function lockIco() { var NS = 'http://www.w3.org/2000/svg', s = document.createElementNS(NS, 'svg'); s.setAttribute('class', 'lock ico'); s.setAttribute('viewBox', '0 0 24 24'); s.innerHTML = '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'; return s; }
  function empty(into, title, text) { into.appendChild(h('li', null, h('div', { class: 'empty-d' }, h('b', { text: title }), text))); }
  function renderTiles() {
    ['venue', 'caterer', 'photographer', 'other'].forEach(function (k) {
      var ul = clear($('#p-' + k)), list = st.places[k];
      $('#n-' + k).textContent = list.length ? list.length + (k === 'other' ? ' ideas' : ' listed') : '';
      if (!list.length) empty(ul, st.plan ? 'Nothing yet' : 'No plan yet', st.plan ? (k === 'other' ? 'Press Get ideas.' : 'Search nearby to find some.') : 'Start with New plan.');
      list.forEach(function (p) { ul.appendChild(h('li', null, placeEl(p))); });
    });
    $$('[data-find]').forEach(function (b) { b.disabled = !st.plan; }); $('#get-ideas').disabled = !st.plan;
    renderBudget(); renderEvidence(); renderAppr(); renderTasks(); renderEventline();
  }
  function renderBudget() {
    var p = st.plan, legend = clear($('#legend')), donut = $('#donut');
    if (!p) { donut.style.background = '#ececf0'; $('#n-budget').textContent = ''; legend.appendChild(h('li', { text: 'No plan yet' })); donut.setAttribute('aria-label', 'No budget yet'); return; }
    var total = p.request.budget_total, lines = (p.allocation && p.allocation.lines) || [], cost = {};
    (p.selections || []).forEach(function (s) { cost[s.category] = s.cost; });
    var parts = lines.map(function (l, i) { return { name: cap(l.category), pct: l.share * 100, amt: cost[l.category] || 0, col: BUDGET_COLORS[i % 4] }; });
    parts.push({ name: 'Reserve', pct: (p.allocation.contingency / total) * 100, amt: p.allocation.contingency, col: BUDGET_COLORS[4] });
    var acc = 0; donut.style.background = 'conic-gradient(' + parts.map(function (x) { var a = acc; acc += x.pct; return x.col + ' ' + a + '% ' + acc + '%'; }).join(',') + ')';
    donut.setAttribute('aria-label', 'Budget split: ' + parts.map(function (x) { return x.name + ' ' + Math.round(x.pct) + '%'; }).join(', '));
    $('#n-budget').textContent = inr(total) + ' cap';
    parts.forEach(function (x) { var i = h('i'); i.style.background = x.col; legend.appendChild(h('li', null, i, x.name, h('span', { class: 'mono', text: inr(x.amt) }))); });
  }
  function renderEvidence() {
    var counts = clear($('#evcounts')), list = clear($('#evlist')), ev = st.evidence;
    if (!ev) { $('#n-evidence').textContent = ''; return; }
    var order = ['suggested', 'discovered', 'extracted', 'user_confirmed', 'verified', 'booked'], total = 0, recent = [];
    order.forEach(function (s) { var rows = ev[s] || []; total += rows.length; rows.forEach(function (r) { recent.push([s, r]); }); counts.appendChild(h('li', null, chip(s), h('b', { text: String((ev[s] || []).length) }))); });
    $('#n-evidence').textContent = plural(total, 'fact', 'facts');
    recent.sort(function (a, b) { return a[1].captured_at < b[1].captured_at ? 1 : -1; }).slice(0, 4).forEach(function (x) {
      var r = x[1], v = r.value === null ? 'unknown' : (typeof r.value === 'object' ? JSON.stringify(r.value) : String(r.value));
      list.appendChild(h('li', null, h('span', { class: 'mono', text: words(String(r.field).replace(/:[0-9a-f]{6,}.*$/, '')) + ': ' + v.slice(0, 70) }), h('small', { text: r.source + ' \u00b7 ' + LABEL[x[0]] })));
    });
    if (!total) list.appendChild(h('li', null, h('small', { text: 'Nothing found or confirmed yet. Searching nearby and finding contacts writes here.' })));
  }
  function waiting() {
    var out = [];
    st.drafts.filter(function (d) { return d.status === 'prepared'; }).forEach(function (d) { out.push({ t: 'Send ' + d.subject, s: cap(d.channel) + ' to ' + d.vendor_name + ' \u00b7 exact text and hash shown before it sends', draft: d }); });
    st.jobs.forEach(function (j) { (j.items || []).filter(function (i) { return !i.resolved; }).forEach(function (i) { out.push({ t: i.text.slice(0, 90), s: 'Job: ' + j.goal.slice(0, 50) }); }); });
    return out;
  }
  function renderAppr() {
    var ul = clear($('#appr')), w = waiting();
    $('#n-appr').textContent = w.length ? plural(w.length, 'approval', 'approvals') : '';
    if (!w.length) empty(ul, st.plan ? 'All clear' : 'No plan yet', st.plan ? 'Nothing is waiting on you.' : 'Nothing to approve yet.');
    w.slice(0, 5).forEach(function (x) { ul.appendChild(h('li', null, x.t, h('small', { text: x.s }))); });
    $('#review-appr').disabled = !st.drafts.some(function (d) { return d.status === 'prepared'; });
  }
  function renderTasks() {
    var ul = clear($('#tasks')), items = [];
    if (st.plan) {
      (st.plan.validation.unverified || []).forEach(function (u) { items.push((u.label || u.name) + ': ' + u.repair); });
      waiting().forEach(function (x) { items.push(x.t); });
    }
    if (!items.length) ul.appendChild(h('li', null, h('span', { class: 'tx', text: st.plan ? 'Nothing needs you right now' : 'Start a plan first' })));
    items.slice(0, 6).forEach(function (t) { ul.appendChild(h('li', null, h('span', { class: 'tx', text: t }))); });
    $('#doc-btn').disabled = !st.plan; $('#doc-v').textContent = st.plan ? 'v' + st.plan.version : '-'; $('#doc-cap').textContent = st.plan ? st.plan.plan_id.slice(0, 10) : '';
  }
  function stamp(plan) {
    var blocking = (plan.validation.unverified || []).filter(function (u) { return u.blocks_verified !== false; });
    if (blocking.length) return 'NEEDS VERIFICATION';
    return plan.symbolic && plan.symbolic.status === 'valid' ? 'VERIFIED' : 'CHECKED';
  }
  function renderEventline() {
    var p = st.plan, l = clear($('#eventline'));
    if (!p) { l.appendChild(document.createTextNode('No plan yet. Press ')); l.appendChild(h('b', { text: 'New plan' })); l.appendChild(document.createTextNode(' to begin your first.')); return; }
    var r = p.request;
    l.appendChild(document.createTextNode(cap(r.event_type) + ' \u00b7 ' + r.city + ' \u00b7 ' + longDate(r.event_date) + ' \u00b7 ' + plural(r.guest_count, 'guest', 'guests') + ' \u00b7 cap ' + inr(r.budget_total) + ' \u00b7 version ' + p.version + ' \u00b7 '));
    l.appendChild(h('b', { text: stamp(p).toLowerCase() }));
  }

  /* ------------------------------------------------------------ loading */
  function loadPlans(select) {
    return api('/api/plans').then(function (r) {
      st.plans = r.ok && Array.isArray(r.data) ? r.data : [];
      var sel = clear($('#plan-select'));
      if (!st.plans.length) sel.appendChild(h('option', { value: '', text: 'No plans yet' }));
      st.plans.forEach(function (p) { sel.appendChild(h('option', { value: p.plan_id, text: cap(p.event_type) + ', ' + p.city + ', ' + longDate(p.event_date) + ' (v' + p.version + ')' })); });
      var want = select || (st.plan && st.plan.plan_id) || sessionStorage.getItem('todoh-plan') || (st.plans.length ? st.plans[st.plans.length - 1].plan_id : '');
      if (want && st.plans.some(function (p) { return p.plan_id === want; })) sel.value = want; else if (st.plans.length) sel.value = st.plans[st.plans.length - 1].plan_id;
      return sel.value;
    });
  }
  function loadPlan(id) {
    if (!id) { st.plan = null; st.drafts = []; st.jobs = []; st.watches = []; st.shortlist = []; st.ideas = []; return Promise.resolve(); }
    sessionStorage.setItem('todoh-plan', id);
    var q = encodeURIComponent(id);
    return Promise.all([api('/api/plans/' + q), api('/api/plans/' + q + '/outreach'), api('/api/plans/' + q + '/jobs'), api('/api/plans/' + q + '/shortlist'), api('/api/plans/' + q + '/ideas'), api('/api/watches')]).then(function (rs) {
      st.plan = rs[0].ok ? rs[0].data : null;
      st.drafts = rs[1].ok && Array.isArray(rs[1].data) ? rs[1].data : [];
      st.jobs = rs[2].ok && Array.isArray(rs[2].data) ? rs[2].data : [];
      st.shortlist = rs[3].ok && Array.isArray(rs[3].data) ? rs[3].data : [];
      st.ideas = rs[4].ok && rs[4].data && Array.isArray(rs[4].data.ideas) ? rs[4].data.ideas.filter(function (i) { return i.status !== 'dismissed'; }) : [];
      st.watches = rs[5].ok && Array.isArray(rs[5].data) ? rs[5].data.filter(function (w) { return w.subject === id; }) : [];
    });
  }
  function loadEvidence() { return api('/api/evidence').then(function (r) { st.evidence = r.ok ? r.data : null; }); }
  function refreshAll(planId) {
    return loadPlans(planId).then(loadPlan).then(loadEvidence).then(function () {
      buildEvents(); buildPlaces();
      if (st.plan) st.cur = parse(st.plan.request.event_date); else st.cur = parse(st.today || iso(new Date()));
      render(); renderTiles();
    });
  }
  function reloadPlanOnly() { return loadPlan(st.plan ? st.plan.plan_id : '').then(loadEvidence).then(function () { buildEvents(); buildPlaces(); render(); renderTiles(); }); }

  /* ------------------------------------------------------------ assistant */
  var right = $('#right'), msgs = $('#msgs');
  function say(who, content) {
    right.classList.add('chatting');
    var m = h('li', { class: 'msg msg--' + who }); add(m, content); msgs.appendChild(m); msgs.scrollTop = msgs.scrollHeight; return m;
  }
  function paras(text) { return String(text).split(/\n{2,}/).map(function (p) { return h('p', { text: p.trim() }); }); }
  function exactCard(d) {
    var m = say('ai', [h('p', null, h('b', { text: 'Approval needed' }), ' \u00b7 ' + d.channel + ' to ' + d.vendor_name + ' ', h('span', { class: 'mono', text: d.recipient })), h('p', { class: 'mono exact', text: d.subject + '\n\n' + d.body }),
      h('p', null, 'Content hash ', h('span', { class: 'mono', text: 'sha256:' + d.content_hash })), h('div', { class: 'acts' })]);
    var acts = $('.acts', m), go = h('button', { class: 'go', type: 'button', text: 'Approve exact content' }), no = h('button', { type: 'button', text: 'Deny' });
    acts.appendChild(go); acts.appendChild(no);
    go.addEventListener('click', function () {
      go.disabled = no.disabled = true;
      api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/outreach/' + encodeURIComponent(d.draft_id) + '/approve', { method: 'POST', body: { content_hash: d.content_hash } }).then(function (r) {
        say('ai', r.ok ? 'Approved exactly that text (sha256:' + d.content_hash.slice(0, 12) + '\u2026). The agent sends it through your configured channel within its limits.' : errText(r, 'It was not approved, and nothing was sent'));
        reloadPlanOnly();
      });
    });
    no.addEventListener('click', function () {
      go.disabled = no.disabled = true;
      api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/outreach/' + encodeURIComponent(d.draft_id) + '/deny', { method: 'POST', body: {} }).then(function (r) { say('ai', r.ok ? 'Denied. Nothing was sent.' : errText(r, 'It could not be denied')); reloadPlanOnly(); });
    });
  }
  function openAssistant() { right.classList.add('open'); $('#toggle-right').setAttribute('aria-expanded', 'true'); }
  function ask(q) {
    if (!st.plan) { say('ai', 'Start a plan first: press New plan and describe your event.'); return; }
    say('me', q); var wait = say('ai', 'Thinking...'); wait.setAttribute('aria-busy', 'true');
    api('/api/assistant/ask', { method: 'POST', body: { question: q, plan_id: st.plan.plan_id } }).then(function (r) {
      msgs.removeChild(wait);
      if (!r.ok || !r.data) { say('ai', errText(r, 'The assistant could not answer')); return; }
      var d = r.data, body = paras(d.answer);
      if (d.citations && d.citations.length) body.push(h('p', { class: 'mono', text: 'Sources: ' + d.citations.join(', ') }));
      if (d.outcome !== 'answered') body.push(h('p', { class: 'hint', text: d.outcome === 'refused' ? 'The assistant declined that.' : 'Answered without a model: ' + (d.degraded_reason || 'it was unavailable') }));
      var m = say('ai', body);
      (d.proposals || []).forEach(function (p) {
        var go = h('button', { class: 'go', type: 'button', text: 'Approve' }), no = h('button', { type: 'button', text: 'Reject' });
        var box = say('ai', [h('p', null, h('b', { text: 'Proposal: ' }), p.summary), h('p', { class: 'mono', text: 'sha256:' + p.content_sha256 }), h('div', { class: 'acts' }, go, no)]);
        [[go, 'approve'], [no, 'reject']].forEach(function (x) { x[0].addEventListener('click', function () { go.disabled = no.disabled = true; api('/api/assistant/approvals/' + encodeURIComponent(p.id) + '/' + x[1], { method: 'POST', body: {} }).then(function (rr) { say('ai', rr.ok ? 'Proposal ' + (x[1] === 'approve' ? 'approved' : 'rejected') + '. ' + ((rr.data && rr.data.result) || '') : errText(rr, 'That could not be decided')); reloadPlanOnly(); }); }); });
      });
    });
  }
  function readChange(q) {
    if (!st.plan) { say('ai', 'Start a plan first.'); return; }
    say('me', q);
    api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/changes', { method: 'POST', body: { message: q } }).then(function (r) {
      if (!r.ok || !r.data) { if (!r.lost) say('ai', errText(r, 'The change could not be read')); return; }
      st.delta = r.data; deltaCard(r.data);
    });
  }
  function vendorName(id) { var s = ((st.plan && st.plan.selections) || []).filter(function (x) { return x.vendor_id === id; })[0]; return s ? s.name + ' (' + s.category + ')' : id; }
  function deltaCard(t) {
    var d = t.delta, lines = [];
    (d.cancel_vendor || []).forEach(function (v) { lines.push('Let go of ' + vendorName(v) + ' and find another'); });
    if (d.set_budget_inr) lines.push('Budget becomes ' + inr(d.set_budget_inr));
    if (d.set_guest_count) lines.push('Guests become ' + d.set_guest_count);
    if (d.set_date) lines.push('The event moves to ' + longDate(d.set_date));
    var tasks = d.add_tasks || [], inputs = [], kids = [h('p', null, h('b', { text: 'What I understood' }))];
    tasks.forEach(function (x) { lines.push('Add "' + x.name + '"' + (x.days_before_event ? ', ' + plural(x.days_before_event, 'day', 'days') + ' before the event' : ' on the event day') + (x.duration_minutes ? ' (' + x.duration_minutes + ' min)' : '')); });
    if (lines.length) kids.push(h('ul', null, lines.map(function (l) { return h('li', { text: l }); })));
    (t.questions || []).forEach(function (q) { kids.push(h('p', { class: 'q', text: q.question })); });
    (t.notes || []).forEach(function (n) { kids.push(h('p', { class: 'hint', text: n })); });
    tasks.forEach(function (x, i) { if (x.duration_minutes) return; var inp = h('input', { type: 'number', min: '1', max: '1440', id: 'len-' + i, 'aria-label': 'Minutes for ' + x.name }); inputs.push([i, inp]); kids.push(h('label', { for: 'len-' + i }, 'Minutes for "' + x.name + '": ', inp)); });
    var done = ((st.plan && st.plan.items) || []).filter(function (i) { return /^book_|^extra_/.test(i.task_id); }), boxes = [];
    if (lines.length && done.length) {
      var det = h('details', null, h('summary', { text: 'Some of this is already done' }));
      done.forEach(function (i) { var c = h('input', { type: 'checkbox', value: i.task_id, id: 'dn-' + i.task_id }); boxes.push(c); det.appendChild(h('label', { for: 'dn-' + i.task_id }, c, ' ' + i.name)); });
      kids.push(det);
    }
    var apply = null;
    if (lines.length) { apply = h('button', { class: 'go', type: 'button', text: 'Apply this change' }); kids.push(h('div', { class: 'acts' }, apply)); kids.push(h('p', { class: 'hint', text: 'Makes a new version. Confirmed bookings are kept unless the change releases them.' })); }
    var card = say('ai', kids);
    if (apply) apply.addEventListener('click', function () {
      var delta = JSON.parse(JSON.stringify(st.delta.delta));
      for (var k = 0; k < inputs.length; k++) { var n = parseInt(inputs[k][1].value, 10); if (!(n >= 1 && n <= 1440)) { inputs[k][1].focus(); return; } delta.add_tasks[inputs[k][0]].duration_minutes = n; }
      apply.disabled = true;
      api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/changes/apply', { method: 'POST', body: { delta: delta, done: boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }), in_flight: {} } }).then(function (r) {
        if (r.ok && r.data && r.data.plan) { say('ai', 'Version ' + r.data.plan.version + ' created: ' + r.data.plan.change_reason); refreshAll(r.data.plan.plan_id); announce('Version ' + r.data.plan.version + ' created.'); return; }
        apply.disabled = false; if (r.lost) return;
        var why = ((r.data && r.data.violations) || []).map(function (v) { return words(v.constraint) + ': ' + v.detail + (v.repair ? ' To fix: ' + v.repair : ''); }).join(' ');
        say('ai', (r.status === 409 ? 'This needs your decision. ' : 'Not applied; nothing was saved. ') + (why || errText(r, 'The change failed')));
      });
    });
  }
  function explain() {
    if (!st.plan) return;
    openAssistant(); say('me', 'Why this plan?');
    api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/explanation?version=' + st.plan.version).then(function (r) {
      if (!r.ok || !r.data) { say('ai', errText(r, 'The explanation could not be loaded')); return; }
      var kids = [], list = null;
      String(r.data.markdown || '').split('\n').forEach(function (line) {
        var m = /^(#{1,4})\s+(.*)$/.exec(line), b = /^[-*]\s+(.*)$/.exec(line);
        if (b) { if (!list) { list = h('ul'); kids.push(list); } list.appendChild(h('li', { text: b[1].replace(/`/g, '') })); return; }
        list = null; if (m) kids.push(h('h4', { text: m[2].replace(/`/g, '') })); else if (line.trim()) kids.push(h('p', { text: line.replace(/`/g, '') }));
      });
      say('ai', kids);
    });
  }
  function find(cat, btn) {
    if (!st.plan) return; btn.disabled = true; var old = btn.textContent; btn.textContent = 'Searching...';
    api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/shortlist/search', { method: 'POST', body: { category: cat } }).then(function (r) {
      btn.disabled = false; btn.textContent = old;
      if (!r.ok || !r.data) { openAssistant(); say('ai', errText(r, 'The search could not run') + ' Searching needs a map or places source to be set up (see the configuration notes).'); return; }
      say('ai', 'Found ' + r.data.added + ' new ' + cat + ' listings near ' + r.data.city + ' (' + r.data.total + ' in all) from ' + r.data.source + '. They are listings only: no price, capacity or availability is known until you ask.');
      reloadPlanOnly();
    });
  }
  function ideas(btn) {
    btn.disabled = true;
    api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/ideas', { method: 'POST', body: { preferences: [] } }).then(function (r) { btn.disabled = false; if (!r.ok) { openAssistant(); say('ai', errText(r, 'Ideas could not be made')); return; } reloadPlanOnly(); });
  }

  /* ------------------------------------------------------------ the plan dossier: the original paper, stacked and re-ordered on click */
  var stage = $('#stage'), area = $('#sheets'), noteCard = $('#note-card'), front = null, built = false, opener = null, SHEETS = [];
  function sheetDefs() {
    var p = st.plan, r = p.request, v = p.validation, checks = v.checks || [], ok = checks.filter(function (c) { return c.passed; }).length, st2 = stamp(p);
    var cost = {}; p.selections.forEach(function (s) { cost[s.category] = s.cost; });
    var parts = (p.allocation.lines || []).map(function (l, i) { return { name: cap(l.category), pct: Math.round(l.share * 100), cap: l.amount, col: BUDGET_COLORS[i % 4], spent: cost[l.category] }; });
    parts.push({ name: 'Reserve', pct: Math.round(p.allocation.contingency / r.budget_total * 100), cap: p.allocation.contingency, col: BUDGET_COLORS[4], spent: null });
    var evItems = (p.items || []).filter(function (i) { return dayKeyOf(i.start) === r.event_date; });
    return [
      { id: 'overview', tab: 'Overview', note: 'The brief, the checks and what is still open. Hard constraints re-derived by the validator.', build: function (n) {
        n.appendChild(h('span', { class: 'stamp' + (st2 === 'VERIFIED' ? '' : ' warn'), text: st2 })); n.appendChild(h('h1', { text: 'PLAN OVERVIEW' }));
        n.appendChild(h('p', { class: 'sub', text: cap(r.event_type) + ' \u00b7 ' + r.city + ' \u00b7 v' + p.version }));
        n.appendChild(h('div', { class: 'pv-stats' }, stat('GUESTS', String(r.guest_count), 'from your brief'), stat('DATE', longDate(r.event_date), ''), stat('CAP', inr(r.budget_total), 'you set it')));
        n.appendChild(h('h2', { text: 'CHECKS' }));
        var ul = h('ul', null, h('li', { text: ok + ' of ' + checks.length + ' hard checks passed' }), h('li', { text: 'Symbolic check: ' + words(p.symbolic.status) }));
        (v.unverified || []).forEach(function (u) { ul.appendChild(h('li', { class: 'warn', text: (u.label || u.name) + ': ' + u.detail })); }); n.appendChild(ul);
        n.appendChild(h('p', { class: 'foot', text: 'Books nothing, commits nothing, sends nothing, pays nothing.' })); } },
      { id: 'timeline', tab: 'Timeline', note: 'The event-day route as solved. A confirmed booking stays pinned.', build: function (n) {
        n.appendChild(h('h1', { text: 'TIMELINE' })); n.appendChild(h('p', { class: 'sub', text: longDate(r.event_date) }));
        var g = h('div'); evItems.forEach(function (i) { var c = catOf(i.task_id, i.resource), bar = h('i', { class: 'pv-gbar ' + c }), s = hours(i.start), e = dayKeyOf(i.end) !== dayKeyOf(i.start) ? 24 : hours(i.end);
          bar.style.left = ((s - HSTART) / (HEND - HSTART) * 100) + '%'; bar.style.width = Math.max(3, (e - s) / (HEND - HSTART) * 100) + '%';
          g.appendChild(h('div', { class: 'pv-grow' }, h('span', { text: i.name }), h('span', { class: 'pv-gtrack' }, bar), h('span', { class: 'pv-gwhen', text: hhmm(s) + '\u2013' + hhmm(hours(i.end)) }))); }); n.appendChild(g);
        n.appendChild(h('h2', { text: 'ALL STEPS' }));
        var tb = h('tbody'); (p.items || []).forEach(function (i) { tb.appendChild(h('tr', null, h('td', { text: dayKeyOf(i.start) + ' ' + hhmm(hours(i.start)) }), h('td', { text: i.name }), h('td', { text: i.group === 'event_day' ? 'event day' : 'before' }))); });
        n.appendChild(h('table', null, h('thead', null, h('tr', null, h('th', { text: 'WHEN' }), h('th', { text: 'WHAT' }), h('th', { text: 'PART' }))), tb)); } },
      { id: 'vendors', tab: 'Vendors', note: 'Selected vendors and how each price is known. Unknown stays unknown.', build: function (n) {
        n.appendChild(h('h1', { text: 'VENDORS' })); n.appendChild(h('p', { class: 'sub', text: 'selected for v' + p.version }));
        p.selections.forEach(function (s) { n.appendChild(h('div', { class: 'pv-vendor' }, h('div', { class: 'pv-vhead' }, h('b', { text: s.name }), h('span', { text: s.category + ' \u00b7 ' + s.booking_status })), h('p', { class: 'pv-alt', text: inr(s.cost) + ' \u00b7 ' + s.source }), h('p', { class: 'pv-alt', text: s.justification }))); }); } },
      { id: 'budget', tab: 'Budget', note: 'Lines are caps. A price counts once the plan holds it.', build: function (n) {
        n.appendChild(h('h1', { text: 'BUDGET' })); n.appendChild(h('p', { class: 'sub', text: 'cap ' + inr(r.budget_total) }));
        var dn = h('div', { class: 'pv-donut' }), acc = 0; dn.style.background = 'conic-gradient(' + parts.map(function (x) { var a = acc; acc += x.pct; return x.col + ' ' + a + '% ' + acc + '%'; }).join(',') + ')';
        n.appendChild(h('div', { class: 'pv-donutwrap' }, dn, h('div', { class: 'pv-legend' }, parts.map(function (x) { var i = h('i'); i.style.background = x.col; return h('p', null, i, x.name + ' ' + x.pct + '%'); }))));
        var tb = h('tbody'); parts.forEach(function (x) { tb.appendChild(h('tr', null, h('td', { text: x.name }), h('td', { class: 'num', text: inr(x.cap) }), h('td', { text: x.spent === null ? 'held back' : inr(x.spent) + ' in plan' }))); });
        n.appendChild(h('table', null, h('thead', null, h('tr', null, h('th', { text: 'LINE' }), h('th', { class: 'num', text: 'CAP' }), h('th', { text: 'PLAN' }))), tb)); } },
      { id: 'questions', tab: 'Questions', note: 'What the planner still needs from you before more of the plan can be verified.', build: function (n) {
        var qs = []; (v.unverified || []).forEach(function (u) { qs.push([(u.label || u.name) + '. ' + u.repair, 'unknown']); }); waiting().forEach(function (x) { qs.push([x.t, 'waiting']); });
        n.appendChild(h('h1', { text: 'OPEN QUESTIONS' })); n.appendChild(h('p', { class: 'sub', text: qs.length + ' waiting' }));
        n.appendChild(h('ul', null, qs.length ? qs.map(function (q) { return h('li', null, q[0] + ' ', h('span', { class: q[1] === 'unknown' ? 'unknown' : 'warn', text: q[1] })); }) : h('li', { text: 'Nothing is open.' })));
        n.appendChild(h('p', { class: 'foot', text: 'Answering turns an unknown or extracted fact into one you confirmed.' })); } }
    ];
  }
  function stat(a, b, c) { return h('div', { class: 'pv-stat' }, h('small', { text: a }), h('b', { text: b }), h('span', { text: c })); }
  function layout() {
    var w = area.clientWidth, hh = area.clientHeight, narrow = w < 700, fi = front == null ? -1 : SHEETS.findIndex(function (s) { return s.id === front; });
    var scatter = narrow ? [[-0.2, -0.28, -5], [0.2, -0.2, 4], [-0.18, 0.1, 3], [0.2, 0.2, -3], [0, 0.0, 1]] : [[-0.3, -0.1, -6], [-0.12, 0.08, 3], [0.06, -0.1, -2], [0.24, 0.06, 5], [0.34, -0.14, -4]];
    var slots = narrow ? [[-0.3, 0.36], [0.3, 0.36], [-0.3, -0.36], [0.3, -0.36]] : [[-0.38, -0.2], [-0.36, 0.24], [0.38, 0.27], [0.34, -0.24]], si = 0, fit = Math.min(1, (hh - 80) / 520, (w - 20) / 560);
    SHEETS.forEach(function (s, i) {
      var e = $('[data-sheet="' + s.id + '"]', area), x, y, r, sc, z;
      if (fi === -1) { x = scatter[i][0] * w; y = scatter[i][1] * hh; r = scatter[i][2]; sc = (narrow ? 0.46 : 0.6) * fit; z = 1 + i; e.classList.add('is-back'); e.classList.remove('is-front'); }
      else if (i === fi) { x = narrow ? 0 : -0.07 * w; y = 0; r = 0; sc = fit; z = 20; e.classList.add('is-front'); e.classList.remove('is-back'); }
      else { var sl = slots[si++ % slots.length]; x = sl[0] * w; y = sl[1] * hh; r = (si % 2 ? -1 : 1) * (3 + si); sc = 0.44 * fit; z = 2 + si; e.classList.add('is-back'); e.classList.remove('is-front'); }
      e.style.setProperty('--x', x + 'px'); e.style.setProperty('--y', y + 'px'); e.style.setProperty('--r', r + 'deg'); e.style.setProperty('--sc', sc); e.style.setProperty('--bsc', sc); e.style.setProperty('--z', z);
      var pk = $('.sheet__pick', e); pk.textContent = (i === fi ? 'In front: ' : 'Bring forward: ') + s.tab; pk.setAttribute('aria-pressed', String(i === fi));
      $('.sheet__page', e).setAttribute('tabindex', i === fi ? '0' : '-1');
    });
    var s = fi === -1 ? null : SHEETS[fi];
    noteCard.classList.add('swap');
    window.setTimeout(function () { noteCard.hidden = !s; if (s) { $('#note-t').textContent = s.tab; $('#note-p').textContent = s.note; noteCard.classList.remove('swap'); } }, reduce.matches ? 0 : 160);
  }
  function openStage() {
    if (!st.plan) { openBrief(); return; }
    opener = document.activeElement; clear(area); SHEETS = sheetDefs();
    SHEETS.forEach(function (s, i) {
      var inner = h('div', { class: 'sheet__in' }); s.build(inner);
      var pick = h('button', { class: 'sheet__pick', type: 'button' }), b = h('div', { class: 'sheet', role: 'group', 'aria-label': s.tab, 'data-sheet': s.id }, pick, h('div', { class: 'sheet__page' }, inner)); b.style.setProperty('--n', i);
      function bring() { if (front !== s.id) { front = s.id; layout(); } }
      b.addEventListener('click', bring); pick.addEventListener('click', function (ev) { ev.stopPropagation(); bring(); });
      area.appendChild(b);
    });
    $('#stage-l').textContent = 'PLAN DOSSIER \u00b7 v' + st.plan.version; $('#stage-r').textContent = cap(st.plan.request.event_type) + ' \u00b7 ' + st.plan.request.city + ' \u00b7 ' + st.plan.plan_id;
    front = null; stage.hidden = false; document.body.style.overflow = 'hidden';
    $$('.sheet', area).forEach(function (s) { s.classList.remove('drop'); void s.offsetWidth; if (!reduce.matches) s.classList.add('drop'); });
    layout(); $('#stage-x').focus();
  }
  function closeStage() { stage.hidden = true; document.body.style.overflow = ''; if (opener) opener.focus(); }

  /* ------------------------------------------------------------ new plan: brief, review, build */
  var briefDlg = $('#brief-dialog'), FIELD = [['event_type', 'Event'], ['community_subtype', 'Kind of gathering'], ['guest_count', 'Guests'], ['city', 'City'], ['event_date', 'Date'], ['budget_total', 'Budget'], ['dietary', 'Food'], ['allergens', 'Allergies'], ['allergy_unclear', 'Allergy to clarify'], ['diet_notes', 'Food to confirm'], ['diet_unclear', 'Food rule to clarify'], ['venue_setting', 'Setting'], ['services', 'Services'], ['reminders', 'Reminders']];
  function openDialog(d) { if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); }
  function closeDialog(d) { if (typeof d.close === 'function') d.close(); else d.removeAttribute('open'); }
  function openBrief() { openDialog(briefDlg); $('#brief-input').focus(); }
  function fieldValue(d, f) {
    var v = d[f]; if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) return null;
    if (f === 'budget_total') return inr(v); if (f === 'event_date') return longDate(v); if (Array.isArray(v)) return v.map(cap).join(', ');
    return f === 'event_type' || f === 'venue_setting' ? cap(v) : String(v);
  }
  function fieldLabel(f) { for (var i = 0; i < FIELD.length; i++) if (FIELD[i][0] === f) return FIELD[i][1]; return words(f); }
  function renderUnderstood() {
    var box = clear($('#understood')), t = st.turn; if (!t) return;
    var dl = h('dl', { class: 'facts' }); FIELD.forEach(function (p) { var v = fieldValue(t.draft, p[0]); if (v !== null) { dl.appendChild(h('dt', { text: p[1] })); dl.appendChild(h('dd', { text: v })); } });
    var card = h('div', { class: 'card' }, h('h2', { text: 'What I understood' }), dl), notes = [].concat(t.flags && t.flags.length ? ['Please double-check: ' + t.flags.map(fieldLabel).join(', ')] : [], t.notes || []);
    if (notes.length) card.appendChild(h('ul', { class: 'list' }, notes.map(function (n) { return h('li', { text: n }); }))); box.appendChild(card);
    if (t.choices && t.choices.length) { t.choices.forEach(function (c) { box.appendChild(h('div', { class: 'card', role: 'group', 'aria-label': c.prompt }, h('p', { text: c.prompt }), h('div', { class: 'chips' }, c.options.map(function (o) { return h('button', { type: 'button', text: o, onclick: function () { sendBrief(o); } }); })))); }); return; }
    if (t.ready) { box.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'pill pill--k', id: 'build', text: 'Build my plan', onclick: build }))); box.appendChild(h('p', { class: 'hint', text: 'Nothing is planned until you press this. Add more detail above to change what was understood.' })); }
    else { box.appendChild(h('p', { class: 'hint', text: t.question || 'Tell me more about the event.' })); }
  }
  function busy(on) { st.busy = on; ['#read-brief', '#build'].forEach(function (s) { var b = $(s); if (b) b.disabled = on; }); $('#read-brief').textContent = on ? 'Reading...' : 'Read my brief'; }
  function sendBrief(text) {
    text = String(text || '').trim(); if (!text || st.busy) return; st.messages.push(text); $('#brief-input').value = ''; busy(true);
    api('/api/intake', { method: 'POST', body: { messages: st.messages, draft: st.draft } }).then(function (r) {
      busy(false);
      if (!r.ok || !r.data) { st.messages.pop(); if (!r.lost) { clear($('#understood')); $('#understood').appendChild(h('p', { class: 'notice bad', role: 'alert', text: errText(r, 'The brief could not be read') })); } return; }
      st.turn = r.data; st.draft = r.data.draft; renderUnderstood(); announce(r.data.ready ? 'Everything needed is understood. Press Build my plan.' : (r.data.question || 'Tell me more.'));
      var f = $('#build') || $('#brief-input'); if (f) f.focus();
    });
  }
  var STAGE_NAME = { parse: 'Reading the brief', decompose: 'Breaking it into tasks', budget: 'Splitting the budget', guests: 'Counting guests and diets', vendors: 'Checking every vendor', select: 'Choosing the best combination',
    schedule: 'Solving the schedule', symbolic: 'Cross-checking the timeline', validate: 'Independent validator', store: 'Saving the version' };
  var stream = null;
  function stopProgress() { if (stream) { stream.close(); stream = null; } }
  function startProgress() {
    stopProgress(); var ol = clear($('#progress')); ol.hidden = false;
    if (typeof window.EventSource !== 'function') { ol.hidden = true; return; }
    stream = new window.EventSource('/api/pulse/stream');
    stream.addEventListener('progress', function (ev) {
      var snap; try { snap = JSON.parse(ev.data); } catch (e) { return; }
      clear(ol); snap.stages.forEach(function (s) { ol.appendChild(h('li', { 'data-state': s.state }, STAGE_NAME[s.name] || s.name, s.ms !== null && s.state === 'done' ? h('small', { text: Math.round(s.ms) + ' ms' }) : null)); });
      if (snap.state === 'done' || snap.state === 'failed') stopProgress();
    });
    stream.addEventListener('timeout', stopProgress); stream.onerror = stopProgress;
  }
  function build() {
    if (st.busy || !st.draft) return; busy(true); var src = st.turn && /^[A-Za-z0-9:._-]+$/.test(st.turn.source || '') ? st.turn.source : 'rule:parser';
    announce('Building the plan and checking every hard constraint.'); startProgress();
    api('/api/intake/confirm', { method: 'POST', body: { draft: st.draft, raw_text: st.messages.join('\n').slice(0, 2000), source: src } }).then(function (r) {
      busy(false); window.setTimeout(stopProgress, 600);
      if (r.ok && r.data && r.data.plan) { st.reality = r.data.reality || null; window.setTimeout(function () { closeDialog(briefDlg); }, reduce.matches ? 0 : 450); st.messages = []; st.draft = null; st.turn = null; clear($('#understood')); refreshAll(r.data.plan.plan_id).then(function () { announce('Plan ready.'); openAssistant(); say('ai', (r.data.reality && r.data.reality.message) || 'Plan ready.'); }); return; }
      if (!r.lost) failure(r);
    });
  }
  function failure(r) {
    var box = clear($('#understood')), d = r.data || {};
    box.appendChild(h('p', { class: 'notice bad', role: 'alert', text: 'The plan could not pass the hard checks, so nothing was stored.' }));
    if (d.violations && d.violations.length) box.appendChild(h('ul', { class: 'list' }, d.violations.map(function (v) { return h('li', null, h('b', { text: words(v.constraint) + ': ' }), v.detail + (v.repair ? ' To fix: ' + v.repair : '')); })));
    else box.appendChild(h('p', { class: 'notice bad', text: errText(r, 'The plan could not be built') }));
    var reality = d.reality; if (reality && reality.message) box.appendChild(h('p', { class: 'notice', text: 'Budget reality: ' + reality.message }));
    var out = h('div'); box.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'pill pill--k', text: 'Show verified trade-offs', onclick: function (ev) { tradeoffs(ev.currentTarget, out); } }))); box.appendChild(out);
  }
  function tradeoffs(btn, out) {
    btn.disabled = true; clear(out);
    api('/api/tradeoffs', { method: 'POST', body: { request: st.messages.join('\n').slice(0, 2000) } }).then(function (r) {
      btn.disabled = false;
      if (!r.ok || !r.data) { out.appendChild(h('p', { class: 'notice bad', text: errText(r, 'Trade-offs could not be worked out') })); return; }
      var t = r.data;
      if (t.status === 'not_needed') { out.appendChild(h('p', { class: 'notice', text: 'This brief needs no trade-off. Press Build my plan again.' })); return; }
      if (t.status === 'no_option') { out.appendChild(h('p', { class: 'notice', text: 'No single change makes this work.' })); return; }
      t.options.forEach(function (o) { out.appendChild(h('div', { class: 'card' }, h('h2', { text: o.title }), h('p', { text: o.detail }), h('p', { class: 'hint', text: 'Estimated total ' + inr(o.estimated_total) + (o.needs_verification && o.needs_verification.length ? '. Still to verify: ' + o.needs_verification.join(', ') : '. Fully verified.') }),
        h('div', { class: 'row' }, h('button', { type: 'button', class: 'pill pill--k', text: 'Use this option', onclick: function (ev) { ev.currentTarget.disabled = true; api('/api/tradeoffs/apply', { method: 'POST', body: { option: o } }).then(function (rr) { if (rr.ok && rr.data && rr.data.plan) { closeDialog(briefDlg); st.messages = []; st.draft = null; st.turn = null; refreshAll(rr.data.plan.plan_id); } else out.appendChild(h('p', { class: 'notice bad', text: errText(rr, 'That option could not be applied') })); }); } })))); });
    });
  }

  /* ------------------------------------------------------------ workspace dialog */
  function wmsg(t) { $('#workspace-msg').textContent = t; }
  function setupWorkspace() {
    var dlg = $('#workspace-dialog');
    $('#open-workspace').addEventListener('click', function () {
      $('#workspace-mode').textContent = st.mode === 'shared' ? 'This is a shared server: your workspace is private to this browser.' : 'This is your own server: everything is stored in its data folder.';
      $('#shared-only').hidden = st.mode !== 'shared'; $('#phrase-out').hidden = true; wmsg(''); openDialog(dlg);
    });
    $('#workspace-close').addEventListener('click', function () { closeDialog(dlg); });
    $('#export-data').addEventListener('click', function () {
      wmsg('Preparing your export...');
      fetch('/api/workspace/export', { headers: { 'X-EventMate-CSRF': st.csrf } }).then(function (res) { if (!res.ok) throw new Error('status'); return res.blob(); }).then(function (b) {
        var u = URL.createObjectURL(b), a = h('a', { href: u, download: 'todoh-export.zip' }); document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(function () { URL.revokeObjectURL(u); }, 4000); wmsg('Your export has been downloaded.');
      }).catch(function () { wmsg('The export could not be made.'); });
    });
    $('#phrase-make').addEventListener('click', function () { api('/api/workspace/recovery', { method: 'POST', body: {} }).then(function (r) { if (!r.ok || !r.data) { wmsg(errText(r, 'A phrase could not be made')); return; } var o = $('#phrase-out'); o.textContent = r.data.phrase; o.hidden = false; wmsg('Write this down now. It is shown once, and making another cancels this one.'); }); });
    $('#recover-form').addEventListener('submit', function (ev) { ev.preventDefault(); api('/api/workspace/recover', { method: 'POST', body: { phrase: $('#recover-input').value } }).then(function (r) { if (!r.ok || !r.data) { wmsg(r.status === 409 ? 'This browser already holds a workspace with plans. Export or delete it first.' : errText(r, 'That phrase did not work')); return; } var o = $('#phrase-out'); o.textContent = r.data.phrase; o.hidden = false; wmsg('Recovered. Your new recovery phrase is shown above; the old one no longer works. Reloading...'); window.setTimeout(function () { window.location.reload(); }, 2500); }); });
    $('#delete-form').addEventListener('submit', function (ev) { ev.preventDefault(); if ($('#delete-input').value.trim() !== 'delete') { wmsg('Type the word delete to confirm.'); return; } api('/api/workspace', { method: 'DELETE', body: { confirm: 'delete' } }).then(function (r) { if (r.ok) window.location.assign('/'); else wmsg(errText(r, 'The workspace could not be deleted')); }); });
  }

  /* ------------------------------------------------------------ sending controls: pause, standing permission, do not contact */
  var SUP_WHY = { stop_request: 'they asked us to stop', user: 'you added it', bad_address: 'the address bounced' };
  function setupSending() {
    var dlg = $('#sending-dialog'), chipBtn = $('#sending-chip');
    function smsg(t) { $('#sending-msg').textContent = t; }
    function paintSending(s) {
      st.sending = s;
      chipBtn.textContent = s ? (s.paused ? 'Sending paused' : 'Sending on') : 'Sending: unknown';
      chipBtn.setAttribute('data-paused', s && s.paused ? 'true' : 'false');
      $('#sending-state').textContent = !s ? 'The sending state could not be read.' : (s.paused ? 'Sending is paused. Nothing goes out' + (s.approved_waiting ? '; ' + s.approved_waiting + ' approved message' + (s.approved_waiting === 1 ? ' is' : 's are') + ' waiting.' : '.') : 'Sending is on. Messages go out only after you approve their exact text' + (s.approved_waiting ? ' (' + s.approved_waiting + ' approved and waiting for the next send).' : '.'));
      $('#sending-toggle').textContent = s && s.paused ? 'Resume sending' : 'Pause all sending';
    }
    function loadSending() { return api('/api/sending').then(function (r) { paintSending(r.ok && r.data ? r.data : null); }); }
    function loadPermission() {
      var btn = $('#perm-revoke'), line = $('#perm-state');
      btn.disabled = true;
      if (!st.plan) { line.textContent = 'Open a plan to see its standing permission.'; return Promise.resolve(); }
      return api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/autoreply').then(function (r) {
        var a = r.ok && r.data ? r.data : null;
        line.textContent = !a ? 'The standing permission could not be read.' : (a.active ? 'A standing permission is active for this plan: fixed follow-up questions may go out by themselves within its limits.' : (a.killed ? 'Paused: no automatic message goes out.' : 'None is active for this plan. Nothing goes out by itself.'));
        btn.disabled = !(a && a.active);
      });
    }
    function loadSuppressions() {
      var ul = clear($('#sup-list'));
      return api('/api/suppressions').then(function (r) {
        var items = r.ok && Array.isArray(r.data) ? r.data : [];
        if (!items.length) { ul.appendChild(h('li', { class: 'hint', text: 'No address is on the list.' })); return; }
        items.forEach(function (s) {
          var li = h('li', { class: 'sup' }, h('b', { class: 'mono', text: (s.channel === 'whatsapp' ? 'WhatsApp ' : 'Email ') + s.value }), ' ', h('span', { class: 'hint', text: s.active ? 'active: ' + (SUP_WHY[s.reason] || s.reason) : 'lifted by ' + (s.lifted_by || '?') + ': ' + (s.lift_reason || '') }));
          if (s.detail) li.appendChild(h('div', { class: 'hint', text: 'Server said: ' + s.detail }));
          if (s.active) {
            var by = h('input', { type: 'text', maxlength: 80, 'aria-label': 'Your name', placeholder: 'Your name' }), why = h('input', { type: 'text', maxlength: 200, 'aria-label': 'Why lift it', placeholder: 'Why lift it' });
            var ack = h('input', { type: 'checkbox', 'aria-label': 'I have read why it was added' }), form = h('form', { class: 'sup-lift', novalidate: true, hidden: true }, by, why, h('label', { class: 'inline' }, ack, ' I have read why it was added'), h('button', { type: 'submit', class: 'pill pill--k', text: 'Lift it' }));
            var open = h('button', { type: 'button', class: 'pill pill--ghost', text: 'Lift\u2026', onclick: function () { form.hidden = !form.hidden; } });
            form.addEventListener('submit', function (ev) {
              ev.preventDefault();
              api('/api/suppressions/' + encodeURIComponent(s.suppression_id) + '/lift', { method: 'POST', body: { by: by.value, reason: why.value, acknowledge_reason: ack.checked } }).then(function (r2) {
                smsg(r2.ok ? 'Taken off the list. Messages that were stopped stay stopped: write a new one.' : errText(r2, 'It could not be lifted')); loadAll();
              });
            });
            li.appendChild(h('div', { class: 'row' }, open)); li.appendChild(form);
          }
          ul.appendChild(li);
        });
      });
    }
    function loadAll() { return Promise.all([loadSending(), loadPermission(), loadSuppressions()]); }
    function openIt() { smsg(''); openDialog(dlg); loadAll(); }
    $('#open-sending').addEventListener('click', openIt); chipBtn.addEventListener('click', openIt);
    $('#sending-close').addEventListener('click', function () { closeDialog(dlg); });
    $('#sending-toggle').addEventListener('click', function () {
      var pause = !(st.sending && st.sending.paused);
      api(pause ? '/api/sending/pause' : '/api/sending/resume', { method: 'POST', body: {} }).then(function (r) { smsg(r.ok ? (pause ? 'Sending is paused.' : 'Sending resumed.') : errText(r, 'That did not work')); loadAll(); });
    });
    $('#perm-revoke').addEventListener('click', function () {
      if (!st.plan) return;
      api('/api/plans/' + encodeURIComponent(st.plan.plan_id) + '/autoreply/revoke', { method: 'POST', body: {} }).then(function (r) { smsg(r.ok ? 'The standing permission is revoked.' : errText(r, 'It could not be revoked')); loadAll(); });
    });
    $('#sup-form').addEventListener('submit', function (ev) {
      ev.preventDefault();
      api('/api/suppressions', { method: 'POST', body: { channel: $('#sup-channel').value, value: $('#sup-value').value, by: $('#sup-by').value, note: $('#sup-note').value } }).then(function (r) {
        if (r.ok) { $('#sup-value').value = ''; $('#sup-note').value = ''; smsg('Added. Nothing more goes to that address, and anything queued to it is stopped.'); } else smsg(errText(r, 'It could not be added'));
        loadAll();
      });
    });
    loadSending();
    window.setInterval(function () { if (!document.hidden) loadSending(); }, 30000);
  }

  /* ------------------------------------------------------------ wiring */
  function init() {
    $$('[data-nav]').forEach(function (b) { b.addEventListener('click', function () { nav(+b.getAttribute('data-nav')); }); });
    $$('.seg [data-view]').forEach(function (b) { b.addEventListener('click', function () { st.view = b.getAttribute('data-view'); render(); }); });
    $('#today').addEventListener('click', function () { st.cur = parse(st.today || iso(new Date())); render(); });
    $$('[data-layer]').forEach(function (c) { c.addEventListener('change', function () { st.layers[c.getAttribute('data-layer')] = c.checked; render(); }); });
    $$('[data-cat]').forEach(function (c) { c.addEventListener('change', function () { st.cats[c.getAttribute('data-cat')] = c.checked; render(); }); });
    $('#pop-x').addEventListener('click', closePop);
    document.addEventListener('click', function (ev) { if (!pop.hidden && !pop.contains(ev.target)) { pop.hidden = true; popFrom = null; } });
    $('#plan-select').addEventListener('change', function (ev) { loadPlan(ev.target.value).then(loadEvidence).then(function () { buildEvents(); buildPlaces(); if (st.plan) st.cur = parse(st.plan.request.event_date); render(); renderTiles(); }); });
    ['#new-plan', '#new-plan-2'].forEach(function (s) { $(s).addEventListener('click', openBrief); });
    $('#brief-close').addEventListener('click', function () { closeDialog(briefDlg); });
    $('#brief-form').addEventListener('submit', function (ev) { ev.preventDefault(); sendBrief($('#brief-input').value); });
    $('#brief-input').addEventListener('keydown', function (ev) { if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); sendBrief($('#brief-input').value); } });
    $('#brief-reset').addEventListener('click', function () { st.messages = []; st.draft = null; st.turn = null; $('#brief-input').value = ''; renderUnderstood(); $('#brief-input').focus(); });
    $$('[data-find]').forEach(function (b) { b.addEventListener('click', function () { find(b.getAttribute('data-find'), b); }); });
    $('#get-ideas').addEventListener('click', function (ev) { ideas(ev.currentTarget); });
    $('#review-appr').addEventListener('click', function () { openAssistant(); var ds = st.drafts.filter(function (d) { return d.status === 'prepared'; }); say('ai', ds.length ? 'Here ' + (ds.length === 1 ? 'is the message' : 'are the messages') + ' waiting on you. Nothing leaves until you approve the exact text.' : 'Nothing is waiting on you.'); ds.forEach(exactCard); });
    $('#doc-btn').addEventListener('click', explain);
    $$('.modes [data-mode]').forEach(function (b) { b.addEventListener('click', function () {
      st.assist = b.getAttribute('data-mode'); $$('.modes [data-mode]').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      $('#ask-in').placeholder = st.assist === 'ask' ? 'Ask about a place, quote or the plan' : 'Say what changed: the caterer pulled out, now 80 guests...';
      $('#ask-hint').textContent = st.assist === 'ask' ? 'Answers come from your plan and the knowledge base. The model never decides a price or a date.' : 'You see what was understood before anything changes.'; }); });
    $('#ask').addEventListener('submit', function (ev) { ev.preventDefault(); var i = $('#ask-in'), v = i.value.trim(); if (!v) return; i.value = ''; if (st.assist === 'ask') ask(v); else readChange(v); });
    var tr = $('#toggle-right'), lf = $('#toggle-left'), left = $('#left');
    tr.addEventListener('click', function () { var o = right.classList.toggle('open'); tr.setAttribute('aria-expanded', String(o)); if (o) $('#ask-in').focus(); });
    $('#close-right').addEventListener('click', function () { right.classList.remove('open'); tr.setAttribute('aria-expanded', 'false'); tr.focus(); });
    lf.addEventListener('click', function () { var o = left.classList.toggle('open'); lf.setAttribute('aria-expanded', String(o)); });
    document.addEventListener('click', function (ev) { if (left.classList.contains('open') && !left.contains(ev.target) && ev.target !== lf) { left.classList.remove('open'); lf.setAttribute('aria-expanded', 'false'); } });
    $('#open-paper').addEventListener('click', openStage); $('#open-paper-2').addEventListener('click', openStage); $('#stage-x').addEventListener('click', closeStage);
    area.addEventListener('click', function (ev) { if (ev.target === area && front) { front = null; layout(); } });
    $('#note-btn').addEventListener('click', function () { window.print(); });
    window.addEventListener('resize', function () { if (!stage.hidden) layout(); });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { if (!pop.hidden) closePop(); else if (!stage.hidden) { if (front) { front = null; layout(); } else closeStage(); } else if (right.classList.contains('open')) $('#close-right').click(); else if (left.classList.contains('open')) left.classList.remove('open'); }
      if (!stage.hidden && (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { var i = front ? SHEETS.findIndex(function (s) { return s.id === front; }) : -1; i = (i + (ev.key === 'ArrowRight' ? 1 : -1) + SHEETS.length) % SHEETS.length; front = SHEETS[i].id; layout(); }
    });
    setupWorkspace();
    setupSending();
    api('/api/session').then(function (r) {
      if (!r.ok || !r.data) { $('#eventline').textContent = errText(r, 'The dashboard could not start'); return; }
      st.csrf = r.data.csrf; st.today = r.data.today; st.mode = r.data.workspace ? r.data.workspace.mode : 'local'; st.wsId = st.mode === 'shared' ? r.data.workspace.id : null; st.cur = parse(st.today);
      refreshAll();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
}());
