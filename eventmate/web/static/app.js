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
  function errText(r, fb) {
    var d = r && r.data; if (!d || !(d.error || d.detail)) return fb + ' (' + (r ? r.status : '?') + ').';
    var why = Array.isArray(d.details) ? d.details.map(function (x) { return (x.field ? words(String(x.field).split('.').pop()) + ': ' : '') + x.message; }).join('; ') : '';
    return String(d.error || d.detail) + (why ? ': ' + why : '');  // the server names the field it refused; "invalid input" alone does not say which
  }

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
  var briefDlg = $('#brief-dialog'), FIELD = [['event_type', 'Event'], ['community_subtype', 'Kind of gathering'], ['guest_count', 'Guests'], ['city', 'City'], ['event_date', 'Date'], ['budget_total', 'Budget'], ['dietary', 'Food'], ['diet_groups', 'Food groups'], ['allergens', 'Allergies'], ['allergy_unclear', 'Allergy to clarify'], ['diet_notes', 'Food to confirm'], ['diet_unclear', 'Food rule to clarify'], ['venue_setting', 'Setting'], ['services', 'Services'], ['reminders', 'Reminders']];
  function openDialog(d) { if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); }
  function closeDialog(d) { if (typeof d.close === 'function') d.close(); else d.removeAttribute('open'); }
  function openBrief() { openDialog(briefDlg); $('#brief-input').focus(); }
  function fieldValue(d, f) {
    var v = d[f]; if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) return null;
    if (f === 'budget_total') return inr(v); if (f === 'event_date') return longDate(v);
    if (f === 'diet_groups') return v.map(function (g) { return (g.diet ? cap(g.diet) : 'Other food need') + ': ' + (g.count ? g.count + (g.count === 1 ? ' guest' : ' guests') : (g.share || 'some guests')) + (g.exception ? ' (an exception)' : ''); }).join('; ');
    if (Array.isArray(v)) return v.map(cap).join(', ');
    return f === 'event_type' || f === 'venue_setting' ? cap(v) : String(v);
  }
  var NO_ALLERGY = 'None stated. If anyone has an allergy, write it above before you build.';
  function fieldLabel(f) { for (var i = 0; i < FIELD.length; i++) if (FIELD[i][0] === f) return FIELD[i][1]; return words(f); }
  // FUTURE(page-resume): the server keeps the staged brief (GET /api/intake/<id>) but the page does not look for one on load, so a reload starts an empty dialog. Keep the id in sessionStorage and offer "Continue your brief".
  // FUTURE(page-edit): only guests, city, date and budget can be typed onto the card. Event type, diets, allergies and services still need a sentence; add typed edits for them (the schema in intake.edited already checks any field in EDITABLE).
  // FUTURE(page-provenance): the card flags only the fields a model filled that the rules could not confirm. Show per field whether it was read by the rules, filled by a model, pressed by the user or assumed ("year not given; assumed" belongs on the date).
  // FUTURE(page-graph): draw the plan graph (result.graph, or GET /api/plans/<id>/graph) as a "why this plan" view.
  var EDIT = { guest_count: 'number', city: 'text', event_date: 'date', budget_total: 'number' };  // the facts that can be typed straight onto the card
  function editButton(key, label, dd, shown, t) {
    return h('button', { type: 'button', class: 'linkish', 'aria-label': 'Edit ' + label.toLowerCase(), onclick: function () {
      var input = h('input', { type: EDIT[key], value: t.draft[key] === null || t.draft[key] === undefined ? '' : String(t.draft[key]), 'aria-label': label, required: true, min: EDIT[key] === 'number' ? '1' : undefined }), msg = h('small', { class: 'notice bad', role: 'alert', hidden: true });
      var form = h('form', { class: 'row' }, input, h('button', { type: 'submit', text: 'Save' }), h('button', { type: 'button', text: 'Cancel', onclick: function () { renderUnderstood(); } }), msg);
      form.addEventListener('submit', function (ev) {
        ev.preventDefault(); var raw = input.value.trim(); if (!raw) return;
        var value = EDIT[key] === 'number' ? Number(raw) : raw, patch = {}; patch[key] = value;
        api('/api/intake/' + t.staged_id + '/fields', { method: 'PATCH', body: { fields: patch, version: t.version } }).then(function (r) {
          if (r.ok && r.data) { st.turn = r.data; st.draft = r.data.draft; renderUnderstood(); announce(label + ' changed.'); return; }
          msg.hidden = false; msg.textContent = r.status === 409 ? 'This brief changed somewhere else. Reloading it.' : errText(r, 'That could not be used');
          if (r.status === 409 || r.status === 410) reloadStaged(t.staged_id);
        });
      });
      clear(dd); dd.appendChild(form); input.focus();
    } });
  }
  function reloadStaged(id) {  // the stored brief, as the card shows it
    api('/api/intake/' + id).then(function (r) { if (r.ok && r.data) { st.turn = r.data; st.draft = r.data.draft; renderUnderstood(); } });
  }
  function renderUnderstood() {
    var box = clear($('#understood')), t = st.turn; if (!t) return;
    var dl = h('dl', { class: 'facts' }); FIELD.forEach(function (p) {
      var v = fieldValue(t.draft, p[0]);
      if (v === null && p[0] === 'allergens' && !(t.draft.allergy_unclear || []).length) v = NO_ALLERGY;  // a missed allergy must not look the same as "none": the card always says what it holds
      if (v !== null) { var dd = h('dd', null, v); if (t.staged_id && t.state === 'pending_validation' && EDIT[p[0]]) dd.appendChild(editButton(p[0], p[1], dd, v, t)); dl.appendChild(h('dt', { text: p[1] })); dl.appendChild(dd); }
    });
    var card = h('div', { class: 'card' }, h('h2', { text: 'What I understood' }), dl), notes = [].concat(t.flags && t.flags.length ? ['Please double-check: ' + t.flags.map(fieldLabel).join(', ')] : [], t.notes || []);
    if (t.changes && t.changes.length) card.appendChild(h('ul', { class: 'list changes', 'aria-label': 'What changed' }, t.changes.map(function (c) { return h('li', { text: c }); })));
    if (notes.length) card.appendChild(h('ul', { class: 'list' }, notes.map(function (n) { return h('li', { text: n }); }))); box.appendChild(card);
    var open = (t.draft && t.draft.unresolved) || [];
    open.forEach(function (q) {
      var c = (t.choices || []).filter(function (x) { return x.key === q.key; })[0];
      if (!c) { box.appendChild(h('p', { class: 'hint q', role: 'status', text: q.question })); return; }  // a question with nothing to press: the user types the answer
      box.appendChild(h('div', { class: 'card', role: 'group', 'aria-label': q.question }, h('p', { text: q.question }), h('div', { class: 'chips' }, c.labels.map(function (label, i) {
        return h('button', { type: 'button', text: label, onclick: function () { sendBrief(label, { key: q.key, pick: i }); } });  // the number of the option is sent, not text to be read again
      }))));
    });
    if (t.ready && !t.no_vendors) { box.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'pill pill--k', id: 'build', text: 'Build my plan', onclick: build }))); box.appendChild(h('p', { class: 'hint', text: 'Nothing is planned until you press this. Add more detail above to change what was understood.' })); }
    else if (t.ready && t.no_vendors) renderFound(box, t.draft, t);
    else if (!open.length) { box.appendChild(h('p', { class: 'hint', text: t.question || 'Tell me more about the event.' })); }
  }
  // FUTURE(quotes-fresh): wording for a pending fresh request; see pricing/observations.py.
  function earlierNote(q) {  // what this vendor said before, dated, and why it may not hold: shown for orientation only, never used as a price
    var when = new Date(q.observed_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    var said = 'Earlier quote: ' + inr(q.amount_inr) + (q.basis === 'per_plate' ? ' per plate' : ' in total') + (q.guest_count ? ' for ' + q.guest_count + ' guests' : '') + ', ' + when + '.';
    var why = q.old && q.old.length ? ' Out of date: ' + q.old.join('; ') + '.' : ' It may have changed.';
    return h('small', { class: 'foot', role: 'note', text: said + why + ' Confirm with the vendor before relying on it.' });
  }
  var FOUND_KINDS = [['caterer', 'Caterers'], ['venue', 'Venues'], ['photographer', 'Photographers']];
  function renderFound(box, draft, t) {
    var city = draft.city;  // a city with no vendor records: the agent looks the businesses up itself, once per city, and shows what it found (no prices: a listing has none)
    var card = h('div', { class: 'card', id: 'found' }, h('h2', { text: 'Found online in ' + city }), h('p', { class: 'hint', role: 'status', text: 'I have no vendor records for ' + city + ' yet, so I cannot build a plan there. These businesses were found online. They are listings, not quotes: there is no price or availability until a vendor confirms. Listing details are not stored.' }));
    card.appendChild(gmaps());
    box.appendChild(card);
    var covered = (st.turn && st.turn.covered_cities) || [];
    if (covered.length) card.appendChild(h('div', null, h('p', { class: 'hint', text: 'I can build a full plan in:' }), h('div', { class: 'chips' }, covered.map(function (c) {
      return h('button', { type: 'button', text: c, onclick: function () { sendBrief('change city to ' + c); } });
    }))));
    var hit = st.found && st.found.city === city ? st.found : (st.found = { city: city, kinds: {} });
    var earlier = {};  // a business's last quote by its place id, so it can sit under its listing while a fresh one is awaited
    api('/api/earlier-quotes?city=' + encodeURIComponent(city) + (draft.guest_count ? '&guests=' + draft.guest_count : '') + (draft.event_date ? '&date=' + draft.event_date : '')).then(function (r) {
      ((r.ok && r.data && r.data.quotes) || []).forEach(function (q) { earlier[q.external_id] = q; });
      card.querySelectorAll('li[data-place]').forEach(function (li) { var q = earlier['gplaces:' + li.getAttribute('data-place')]; if (q) li.appendChild(earlierNote(q)); });
    });
    FOUND_KINDS.forEach(function (k) {
      var slot = h('div', null, h('h3', { text: k[1] }), h('p', { class: 'hint', text: 'Looking...' })); card.appendChild(slot);
      var done = function (r) {
        if (r.ok) hit.kinds[k[0]] = r; if (!slot.isConnected) return;
        var list = r.ok && r.data && r.data.places ? r.data.places : null; clear(slot); slot.appendChild(h('h3', { text: k[1] }));
        if (!list) {  // never the server's wording: it can name a setting a visitor cannot change
          slot.appendChild(h('p', { class: 'hint', text: r.status === 503 ? 'Looking businesses up online is not switched on here.' : r.status === 429 ? 'This hour\'s online lookups are used up. Try again later.' : 'I could not look up ' + k[1].toLowerCase() + ' just now.' }));
          if (r.status !== 503) slot.appendChild(h('button', { type: 'button', text: 'Try again', onclick: function () { renderUnderstood(); } }));
          return;
        }
        if (!list.length) { slot.appendChild(h('p', { class: 'hint', text: 'Nothing found.' })); return; }
        slot.appendChild(h('ul', { class: 'list' }, list.slice(0, 6).map(function (p) {
          var line = p.name + (p.address ? ', ' + p.address.split(',').slice(-3).join(',').trim() : '') + (p.rating ? ' (' + p.rating + ' from ' + (p.rating_count || 0) + ' reviews)' : '');
          var li = h('li', { 'data-place': p.place_id || '' }, /^https:\/\//.test(p.maps_uri || '') ? h('a', { href: p.maps_uri, target: '_blank', rel: 'noopener noreferrer', text: line }) : line);
          if (earlier['gplaces:' + p.place_id]) li.appendChild(earlierNote(earlier['gplaces:' + p.place_id]));
          return li;
        })));
      };
      if (hit.kinds[k[0]]) done(hit.kinds[k[0]]); else api('/api/places?kind=' + k[0] + '&city=' + encodeURIComponent(city)).then(done);
    });
    renderAsk(card, t);
  }

  /* a brief with no plan: find the vendors, confirm an email for each, write the requests, approve each one. The same stores and approvals as a plan, filed under brief_<id>. */
  function renderAsk(card, t) {
    var d = t && t.draft, key = t && t.staged_id ? 'brief_' + t.staged_id : null; if (!key || !d.city || !d.event_date || !d.guest_count) return;
    var name = h('input', { type: 'text', id: 'ask-name', maxlength: '60', 'aria-label': 'Your name', placeholder: 'Your name (the vendors see it)', value: st.askName || '' }), list = h('div', { id: 'ask-list' }), drafts = h('div', { id: 'ask-drafts' }), msg = h('p', { class: 'hint', role: 'status' });
    var go = h('button', { type: 'button', text: st.askStarted === key ? 'Look again' : 'Find vendors I can email', onclick: function () { st.askName = name.value.trim(); findVendors(key, go, list, drafts, name, msg); } });
    name.addEventListener('input', function () { st.askName = name.value.trim(); });
    card.appendChild(h('div', { id: 'ask-vendors' }, h('h3', { text: 'Ask these vendors for a quote' }), h('p', { class: 'hint', text: 'I write one email per vendor, from your mailbox. Nothing is sent until you approve each message, word for word. Your budget is never in it.' }),
      h('div', { class: 'row' }, name, go), msg, list, drafts));
    if (st.askStarted === key) { loadAsk(key, list, drafts, name, msg); }
  }
  function gmaps() {  // Google's own logo, unmodified, 18dp high with its clear space (CSS), and the accessibility label Google asks for
    return h('span', { class: 'gmaps' }, h('img', { src: '/static/google-maps-logo.svg', alt: 'Google Maps', width: '98', height: '18' }));
  }
  var SRC = { user_confirmed: 'You confirmed this', vendor_website: 'Found on the vendor\'s own website', osm_listing: 'OpenStreetMap listing', vendor_quote: 'Quote from the vendor', vendor_reply: 'The vendor\'s reply', app_request: 'Request written here' };
  var FACT = { name: 'Name', website: 'Website', 'contact.email': 'Email', 'contact.whatsapp': 'WhatsApp', quote: 'Quote', request: 'Request', reply: 'Reply' };
  st.place = st.place || {};  // a Google listing as fetched in this visit: shown, never kept past it
  function listingFor(place_id) {
    if (!place_id) return null; if (st.place[place_id]) return st.place[place_id];
    var hit = null; if (st.found) Object.keys(st.found.kinds || {}).forEach(function (k) { var r = st.found.kinds[k]; ((r && r.data && r.data.places) || []).forEach(function (p) { if (p.place_id === place_id) hit = p; }); });
    return hit;
  }
  function safeUrl(u) { return /^https?:\/\/[^\s<>"']+$/.test(u || '') ? u : null; }
  function vendorCard(c, key, nameInput, msg, again, quotes) {
    var base = '/api/plans/' + key, live = listingFor(c.place_id), title = (live && live.name) || c.name, box = h('article', { class: 'vcard', 'data-vendor': c.vendor_id, 'aria-label': title });
    var head = h('h4', null, title, h('small', { class: 'foot', text: ' ' + c.category + (c.label ? ' · ' + c.label : '') })); box.appendChild(head);
    var line = h('p', { class: 'hint' }); box.appendChild(line);
    var fill = function (p) {
      st.place[c.place_id] = p; clear(line); head.firstChild.nodeValue = p.name;
      line.appendChild(document.createTextNode((p.address ? p.address + ' · ' : '') + (p.rating ? p.rating + ' from ' + (p.rating_count || 0) + ' reviews · ' : '')));
      if (safeUrl(p.maps_uri)) line.appendChild(h('a', { href: p.maps_uri, target: '_blank', rel: 'noopener noreferrer', text: 'Open in Maps' }));
      if (safeUrl(p.website)) { line.appendChild(document.createTextNode(' · ')); line.appendChild(h('a', { href: p.website, target: '_blank', rel: 'noopener noreferrer', text: 'Website' })); }
      line.appendChild(gmaps()); line.appendChild(h('small', { class: 'foot', text: ' Listing details, fetched just now and not stored.' }));
    };
    if (c.place_id) {
      if (live && live.address !== undefined) fill(live);
      else { line.textContent = 'Fetching the listing...'; api('/api/places/' + encodeURIComponent(c.place_id)).then(function (r) { if (r.ok && r.data) fill(r.data); else line.textContent = r.status === 502 || r.status === 503 ? 'The listing could not be fetched just now.' : 'Google no longer lists this business under that id.'; }); }
    }
    var facts = h('ul', { class: 'list facts' });
    (c.facts || []).forEach(function (f) {
      var when = f.captured_at ? new Date(f.captured_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : null, src = safeUrl(f.source_ref) && f.source_type !== 'user_confirmed';
      facts.appendChild(h('li', { 'data-field': f.field, 'data-status': f.status }, h('b', { text: FACT[f.field] || f.field }), ': ' + f.value + ' ', h('small', { class: 'foot', text: '(' + SRC[f.source_type] + (when ? ', ' + when : '') + ' · ' + (f.user_confirmed ? 'confirmed by you' : 'not confirmed') + (f.status && f.status !== 'held' && f.status !== 'confirmed' ? ' · ' + f.status : '') + ')' }),
        src ? h('a', { href: f.source_ref, target: '_blank', rel: 'noopener noreferrer', text: ' source' }) : null,
        f.conflict ? h('small', { class: 'notice bad', text: ' Conflicts with another address or value held for this business: neither is chosen for you.' }) : null,
        f.note ? h('small', { class: 'foot', text: ' ' + f.note }) : null,
        f.evidence ? h('blockquote', { class: 'mono', text: '\u201c' + f.evidence + '\u201d' }) : null));
      var li = facts.lastChild;
      if ((f.field === 'contact.email') && f.status === 'found') {  // confirming keeps the address and, for a Google listing, the name the user wants in their own records
        li.appendChild(h('button', { type: 'button', text: 'Use this email', onclick: function () { confirmForm(li, c, f, key, nameInput, msg, again); } }));
        li.appendChild(h('button', { type: 'button', text: 'Not this one', onclick: function () { api(base + '/shortlist/' + encodeURIComponent(c.vendor_id) + '/contacts/' + encodeURIComponent(f.candidate_id || '') + '/reject', { method: 'POST', body: {} }).then(again); } }));
      }
    });
    if (!facts.children.length) facts.appendChild(h('li', { class: 'hint', text: 'Nothing held yet beyond the listing: no email found, no quote, no request.' }));
    box.appendChild(h('details', { open: '' }, h('summary', { text: 'What this app holds, and where it came from' }), facts));
    (quotes || []).forEach(function (a) { box.appendChild(quoteBlock(a, key, nameInput, msg, again)); });
    var acts = h('div', { class: 'row' }); box.appendChild(acts);
    var hasEmail = (c.facts || []).some(function (f) { return f.field === 'contact.email' && f.status !== 'rejected'; });
    if (!hasEmail && !c.contact_ready) acts.appendChild(h('button', { type: 'button', text: 'Find email on their website', onclick: function (ev) {
      ev.currentTarget.disabled = true; api(base + '/shortlist/' + encodeURIComponent(c.vendor_id) + '/find-contacts', { method: 'POST', body: {} }).then(function (x) {
        msg.textContent = x.ok && x.data && !(x.data.added || []).length ? 'No email address on that site.' : (x.ok ? '' : (x.status === 400 ? 'No website to read for this business.' : errText(x, 'The site could not be read'))); again();
      });
    } }));
    acts.appendChild(h('button', { type: 'button', text: 'Write to them myself', onclick: function () { contactPanel(box, c, key, nameInput, title); } }));
    return box;
  }
  var STATE = { confirmed: 'Confirmed', read: 'Read from the reply, not confirmed', unclear: 'Unclear', missing: 'Not answered' };
  function shown(f) {
    if (f.value === null || f.value === undefined) return null;
    if (f.money) return inr(Number(f.value)) + (f.key === 'price' ? ' per plate' : '');
    if (f.key === 'taxes_included' || f.key === 'available') return f.value === 'yes' ? 'Yes' : 'No';
    if (f.key === 'diets') return f.value.split(',').map(function (d) { return d.replace('_', ' '); }).join(', ');
    if (f.key === 'valid_days') return f.value + ' days';
    return f.value;
  }
  function quoteBlock(a, key, nameInput, msg, again) {  // the reply read field by field: nothing counts until the user confirms that field, and an amount is confirmed on its own
    var base = '/api/plans/' + key + '/quote-readings/' + encodeURIComponent(a.message_id), when = new Date(a.received_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    var box = h('section', { class: 'qblock', 'data-message': a.message_id, 'aria-label': 'Quote from the reply of ' + when });
    box.appendChild(h('h5', null, 'Quote from the reply of ' + when, h('small', { class: a.comparable ? 'ok' : 'foot', text: a.comparable ? ' · comparable' : ' · not comparable yet' })));
    box.appendChild(h('p', { class: 'hint', text: a.confirmed_required + ' of ' + a.required_total + ' required fields confirmed by you · ' + (a.read_required - a.confirmed_required) + ' read and waiting' + (a.template_used ? '' : ' · the reply did not use the template, so only what it clearly says was read') }));
    box.appendChild(h('progress', { max: String(a.required_total), value: String(a.confirmed_required), 'aria-label': 'Required fields confirmed' }));
    var who = function () { var v = (nameInput.value || '').trim(); if (!v) { msg.textContent = 'Type your name first: it is recorded as the person who confirmed this.'; nameInput.focus(); return null; } return v.slice(0, 60); };
    var send = function (fields, extra) { var by = who(); if (!by) return; api(base + '/confirm', { method: 'POST', body: Object.assign({ fields: fields, by: by }, extra || {}) }).then(function (x) { msg.textContent = x.ok ? '' : errText(x, 'That could not be confirmed'); again(); }); };
    var byKey = {}; a.fields.forEach(function (f) { byKey[f.key] = f; });
    var list = h('ul', { class: 'list qfields' });
    a.fields.forEach(function (f) {
      var li = h('li', { 'data-field': f.key, 'data-state': f.state }, h('b', { text: f.label }), ': ', shown(f) || '—', ' ',
        h('small', { class: 'foot', text: '(' + STATE[f.state] + (f.state === 'confirmed' ? ' by ' + f.confirmed_by + (f.edited ? ', a value you typed' : '') : '') + (f.source === 'free_text' ? ' · from free text' : '') + (f.note && f.state !== 'confirmed' ? ' · ' + f.note : '') + ')' }));
      if (f.evidence && f.state !== 'confirmed') li.appendChild(h('blockquote', { class: 'mono', text: '“' + f.evidence + '”' }));
      var row = h('div', { class: 'row' });
      var typed = function (label) {  // a value the user types: the same parser as the vendor's line decides whether it can be used
        row.appendChild(h('button', { type: 'button', text: label, onclick: function (ev) {
          ev.currentTarget.remove(); var input = h('input', { type: 'text', maxlength: '120', 'aria-label': f.label, value: f.value || '' }), go = h('button', { type: 'button', text: 'Confirm what I typed' });
          go.addEventListener('click', function () { if (!input.value.trim()) return; var o = {}; o[f.key] = input.value.trim(); send(o, f.key === 'price' || f.key === 'price_total' || f.key === 'package' ? { valid_days: (byKey.valid_days.value || undefined) } : undefined); });
          row.appendChild(input); row.appendChild(go); input.focus();
        } }));
      };
      if (f.state === 'read') {
        var isPrice = f.key === 'price' || f.key === 'price_total' || f.key === 'package';
        row.appendChild(h('button', { type: 'button', class: f.money ? 'pill pill--k' : '', text: f.money ? 'Confirm ' + shown(f) : 'Confirm', onclick: function () {
          var o = {}; o[f.key] = null;
          if (isPrice && byKey.valid_days.state !== 'confirmed') {  // the price is a quote only with how long it holds
            var days = h('input', { type: 'number', min: '1', max: '365', 'aria-label': 'How many days the price is valid', value: byKey.valid_days.value || '', required: true }), go = h('button', { type: 'button', text: 'Confirm price and validity' });
            go.addEventListener('click', function () { if (days.value) send(o, { valid_days: String(days.value) }); });
            row.appendChild(h('label', null, 'Valid for how many days? ', days)); row.appendChild(go); days.focus(); return;
          }
          send(o);
        } }));
        typed('Change');
      } else if (f.state === 'unclear' || f.state === 'missing') typed('Type the value');
      else row.appendChild(h('button', { type: 'button', text: 'Take back', onclick: function () { var by = who(); if (!by) return; api(base + '/retract', { method: 'POST', body: { field: f.key, by: by } }).then(function (x) { msg.textContent = x.ok ? '' : errText(x, 'That could not be taken back'); again(); }); } }));
      li.appendChild(row); list.appendChild(li);
    });
    box.appendChild(list);
    var rest = a.fields.filter(function (f) { return f.state === 'read' && !f.money && f.key !== 'inclusions' && f.key !== 'valid_days'; });  // how long a price holds is confirmed with the price, never in a batch
    if (rest.length > 1) box.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', text: 'Confirm the ' + rest.length + ' other fields as read', onclick: function () { var o = {}; rest.forEach(function (f) { o[f.key] = null; }); send(o); } }), h('small', { class: 'foot', text: 'Amounts are never confirmed in a batch.' })));
    if (a.issues.length) box.appendChild(h('ul', { class: 'list' }, a.issues.map(function (i) { return h('li', { class: i.blocking ? 'notice bad' : 'hint', text: (i.blocking ? 'Does not fit: ' : 'Note: ') + i.text + (i.tentative ? ' (as read, not confirmed yet)' : '') }); })));
    return box;
  }
  function confirmForm(li, c, f, key, nameInput, msg, again) {
    var who = h('input', { type: 'text', maxlength: '60', 'aria-label': 'Your name', value: nameInput.value || '', placeholder: 'Your name' }), nm = h('input', { type: 'text', maxlength: '120', 'aria-label': 'Name to keep for this business', value: (listingFor(c.place_id) || {}).name || (c.name_confirmed ? c.name : ''), placeholder: 'Business name for your records' });
    var form = h('form', { class: 'row' }, h('label', null, 'Business name to keep in your records ', nm), h('label', null, 'Confirmed by ', who), h('button', { type: 'submit', text: 'Confirm' }), h('button', { type: 'button', text: 'Cancel', onclick: function () { again(); } }));
    form.addEventListener('submit', function (ev) {
      ev.preventDefault(); if (!who.value.trim()) return; nameInput.value = who.value.trim();
      var body = { by: who.value.trim().slice(0, 60) }; if (c.source === 'gplaces') body.business_name = nm.value.trim();
      api('/api/plans/' + key + '/shortlist/' + encodeURIComponent(c.vendor_id) + '/contacts/' + encodeURIComponent(f.candidate_id || '') + '/confirm', { method: 'POST', body: body }).then(function (x) { msg.textContent = x.ok ? '' : errText(x, 'That address could not be confirmed'); again(); });
    });
    li.appendChild(form); nm.focus();
  }
  function contactPanel(box, c, key, nameInput, title) {  // the request as text, for the user to send from their own mail or WhatsApp: nothing here sends, stores or records it
    var old = box.querySelector('.contact'); if (old) { old.remove(); return; }
    var panel = h('div', { class: 'contact' }, h('p', { class: 'hint', text: 'Writing it...' })); box.appendChild(panel);
    var q = '?category=' + encodeURIComponent(c.category) + '&name=' + encodeURIComponent(title) + '&sender=' + encodeURIComponent(nameInput.value || '');
    api('/api/plans/' + key + '/request-text' + q).then(function (r) {
      clear(panel); if (!r.ok || !r.data) { panel.appendChild(h('p', { class: 'notice bad', text: errText(r, 'The request could not be written') })); return; }
      var t = r.data, emails = (c.facts || []).filter(function (f) { return f.field === 'contact.email' && f.status !== 'rejected'; });
      panel.appendChild(h('p', { class: 'hint', text: 'This is the request as the app would write it. Sending it yourself is outside the app: it is not recorded and replies do not come back here.' }));
      panel.appendChild(h('p', { class: 'mono exact', text: t.subject + '\n\n' + t.body }));
      var row = h('div', { class: 'row' }); panel.appendChild(row);
      emails.forEach(function (f) { row.appendChild(h('a', { class: 'pill', href: 'mailto:' + encodeURIComponent(f.value).replace('%40', '@') + '?subject=' + encodeURIComponent(t.subject) + '&body=' + encodeURIComponent(t.body), text: 'Email ' + f.value })); });
      var wa = h('button', { type: 'button', text: 'Show phone and WhatsApp' }); row.appendChild(wa);
      wa.addEventListener('click', function () {
        wa.disabled = true;
        api('/api/places/' + encodeURIComponent(c.place_id || 'none0000') + '?phone=1').then(function (x) {
          if (!c.place_id || !x.ok || !x.data || !x.data.phone) { row.appendChild(h('small', { class: 'foot', text: c.place_id ? 'Google lists no phone number for this business.' : 'This business has no Google listing to read a phone from.' })); return; }
          var digits = x.data.phone.replace(/\D/g, ''), clean = x.data.phone;
          row.appendChild(h('small', { class: 'foot', text: clean + ' (from Google, shown now, not stored) ' }));
          row.appendChild(h('a', { class: 'pill', href: 'tel:' + (clean.charAt(0) === '+' ? '+' : '') + digits, text: 'Call' }));
          row.appendChild(h('a', { class: 'pill', href: 'https://wa.me/' + digits + '?text=' + encodeURIComponent(t.body), target: '_blank', rel: 'noopener noreferrer', text: 'WhatsApp them' }));
        });
      });
    });
  }
  function findVendors(key, btn, list, drafts, name, msg) {
    btn.disabled = true; btn.textContent = 'Looking...';
    Promise.all(['caterer', 'venue', 'photographer'].map(function (c) { return api('/api/plans/' + key + '/shortlist/search', { method: 'POST', body: { category: c, source: 'places', limit: 6 } }); })).then(function () {
      btn.disabled = false; btn.textContent = 'Look again'; st.askStarted = key; loadAsk(key, list, drafts, name, msg);
    });
  }
  function loadAsk(key, list, drafts, name, msg) {
    var base = '/api/plans/' + key, again = function () { loadAsk(key, list, drafts, name, msg); };
    Promise.all([api(base + '/vendor-cards'), api(base + '/quote-readings')]).then(function (both) {
      var r = both[0], quotes = both[1].ok && both[1].data && Array.isArray(both[1].data.quotes) ? both[1].data.quotes : [];
      clear(list); var cards = r.ok && r.data && Array.isArray(r.data.cards) ? r.data.cards : [];
      if (!cards.length) { list.appendChild(h('p', { class: 'hint', text: 'No businesses to ask yet.' })); }
      cards.forEach(function (c) { list.appendChild(vendorCard(c, key, name, msg, again, quotes.filter(function (q) { return q.vendor_id === c.vendor_id; }))); });
      var ready = cards.filter(function (c) { return c.contact_ready; }).length;
      if (ready) list.appendChild(h('div', { class: 'row' }, h('button', { type: 'button', class: 'pill pill--k', text: 'Write the requests (' + ready + ')', onclick: function () {
        if (!name.value.trim()) { msg.textContent = 'Type your name first: the email is signed with it.'; name.focus(); return; }
        api(base + '/outreach/request-all', { method: 'POST', body: { sender_name: name.value.trim().slice(0, 80) } }).then(function (x) { msg.textContent = x.ok ? '' : errText(x, 'The requests could not be written'); again(); });
      } })));
    });
    api(base + '/outreach').then(function (r) {
      clear(drafts); (r.ok && Array.isArray(r.data) ? r.data : []).forEach(function (dr) {
        if (dr.status === 'prepared') {
          var card = h('div', { class: 'card', 'data-draft': dr.draft_id }, h('p', null, h('b', { text: 'Approve to send' }), ' \u00b7 email to ' + dr.vendor_name + ' ', h('span', { class: 'mono', text: dr.recipient })), h('p', { class: 'mono exact', text: dr.subject + '\n\n' + dr.body }));
          var yes = h('button', { type: 'button', class: 'pill pill--k', text: 'Approve exactly this and send' }), no = h('button', { type: 'button', text: 'Do not send' }), say2 = h('small', { class: 'foot', role: 'status' });
          yes.addEventListener('click', function () { yes.disabled = no.disabled = true; api(base + '/outreach/' + encodeURIComponent(dr.draft_id) + '/approve', { method: 'POST', body: { content_hash: dr.content_hash } }).then(function (x) { say2.textContent = x.ok ? 'Approved: sent through your mailbox.' : errText(x, 'Not approved, and nothing was sent'); if (x.ok) again(); else yes.disabled = no.disabled = false; }); });
          no.addEventListener('click', function () { yes.disabled = no.disabled = true; api(base + '/outreach/' + encodeURIComponent(dr.draft_id) + '/deny', { method: 'POST', body: {} }).then(function () { again(); }); });
          card.appendChild(h('div', { class: 'row' }, yes, no)); card.appendChild(say2); drafts.appendChild(card);
        } else if (dr.status === 'stale') drafts.appendChild(h('p', { class: 'hint', text: 'The request to ' + dr.vendor_name + ' no longer matches your brief. Write the requests again.' }));
        else if (['sent', 'approved', 'sending'].indexOf(dr.status) >= 0) drafts.appendChild(h('p', { class: 'hint', text: 'Asked ' + dr.vendor_name + ' (' + dr.status + '). Waiting for a reply.' }));
      });
    });
    api(base + '/inbox').then(function (r) {  // what the vendors wrote back: their own words as text, and the amounts and answers read from them. Nothing here records a quote.
      var box = $('#ask-replies'); if (box) box.remove(); var threads = r.ok && r.data && r.data.threads ? r.data.threads.filter(function (t2) { return (t2.replies || []).length; }) : []; if (!threads.length) return;
      box = h('div', { id: 'ask-replies' }, h('h3', { text: 'Replies' }));
      threads.forEach(function (t2) {
        (t2.replies || []).forEach(function (m) {
          var a = m.analysis || {}, facts = [];
          (a.amounts || []).forEach(function (x) { facts.push(inr(x.low_inr) + (x.high_inr !== x.low_inr ? ' to ' + inr(x.high_inr) : '') + (x.unit && x.unit !== 'unspecified' ? ' ' + x.unit : '')); });
          if (a.says_available) facts.push('says it is available'); if (a.says_unavailable) facts.push('says it is NOT available'); if (a.asks_to_stop) facts.push('asks you to stop');
          box.appendChild(h('div', { class: 'card', 'data-reply': m.message_id }, h('p', null, h('b', { text: m.vendor_name }), ' wrote:'), h('p', { class: 'mono exact', text: String(m.body).slice(0, 1200) }),
            facts.length ? h('p', { class: 'hint', text: 'I read: ' + facts.join('; ') + '. This is only what the text says: check it before you rely on it.' }) : null));
        });
      });
      drafts.parentNode.appendChild(box);
    });
  }
  function busy(on) { st.busy = on; ['#read-brief', '#build'].forEach(function (s) { var b = $(s); if (b) b.disabled = on; }); $('#read-brief').textContent = on ? 'Reading...' : 'Read my brief'; }
  function sendBrief(text, answer) {
    text = String(text || '').trim(); if (st.busy) return;
    if (!text) { renderUnderstood(); $('#understood').appendChild(h('p', { class: 'notice bad', role: 'alert', text: 'Type a few words about the event first, for example: 120 guests in Pune on 12 December, budget 2 lakh.' })); return; }
    if (!answer) st.messages.push(text); $('#brief-input').value = ''; busy(true);  // the history is kept only for the plan's own record of what was typed; the server reads just the newest message
    api('/api/intake', { method: 'POST', body: { messages: [text], draft: st.draft, expecting: (st.turn && st.turn.asking) || undefined, answer: answer, staged_id: (st.turn && st.turn.staged_id) || undefined } }).then(function (r) {
      busy(false);
      if (!r.ok || !r.data) { if (!answer) st.messages.pop(); if (!r.lost) { clear($('#understood')); $('#understood').appendChild(h('p', { class: 'notice bad', role: 'alert', text: errText(r, 'The brief could not be read') })); } return; }
      st.turn = r.data; st.draft = r.data.draft; renderUnderstood(); announce(r.data.ready && r.data.no_vendors ? 'I have no vendors for that city yet.' : r.data.ready ? 'Everything needed is understood. Press Build my plan.' : (r.data.question || 'Tell me more.'));
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
  function planReady(plan_id, reality) {
    st.reality = reality || null; window.setTimeout(function () { closeDialog(briefDlg); }, reduce.matches ? 0 : 450); st.messages = []; st.draft = null; st.turn = null; clear($('#understood'));
    refreshAll(plan_id).then(function () { announce('Plan ready.'); openAssistant(); say('ai', (reality && reality.message) || 'Plan ready.'); });
  }
  function build() {
    if (st.busy || !st.draft) return; var t = st.turn;
    busy(true); announce('Building the plan and checking every hard constraint.'); startProgress();
    if (t && t.staged_id) {  // the approved brief goes to the build queue; this page follows it
      api('/api/intake/' + t.staged_id + '/commit', { method: 'POST', body: { version: t.version, draft_hash: t.draft_hash } }).then(function (r) {
        if (r.ok && r.data) { follow(t.staged_id, Date.now()); return; }
        busy(false); stopProgress(); if (r.lost) return;
        var box = clear($('#understood')); box.appendChild(h('p', { class: 'notice bad', role: 'alert', text: r.status === 409 ? 'This brief is not the one you reviewed any more. It has been reloaded: check it and press Done again.' : errText(r, 'The brief could not be sent to build') }));
        reloadStaged(t.staged_id);
      });
      return;
    }
    var src = t && /^[A-Za-z0-9:._-]+$/.test(t.source || '') ? t.source : 'rule:parser';
    api('/api/intake/confirm', { method: 'POST', body: { draft: st.draft, raw_text: st.messages.join('\n').slice(0, 2000), messages: st.messages, source: src } }).then(function (r) {
      busy(false); window.setTimeout(stopProgress, 600);
      if (r.ok && r.data && r.data.plan) { planReady(r.data.plan.plan_id, r.data.reality); return; }
      if (!r.lost) failure(r);
    });
  }
  function follow(id, since) {  // poll the build; the answer is the stored outcome, so a page reloaded half way finds the same one
    api('/api/intake/' + id + '/status').then(function (r) {
      if (r.ok && r.data && r.data.state === 'processing' && Date.now() - since < 300000) { window.setTimeout(function () { follow(id, since); }, 700); return; }
      busy(false); window.setTimeout(stopProgress, 600);
      var d = r.data || {};
      if (r.ok && d.state === 'done' && d.result && d.result.plan_id) { planReady(d.result.plan_id, d.result.reality); return; }
      if (r.lost) return;
      failure({ data: r.ok ? Object.assign({}, d.result || {}, { error: d.state === 'processing' ? 'The build is taking longer than expected. Check again in a moment.' : d.error }) : d, status: r.status });
      if (r.ok && d.state === 'failed') $('#understood').appendChild(h('div', { class: 'row' }, h('button', { type: 'button', text: 'Edit the brief and try again', onclick: function () {
        api('/api/intake/' + id + '/reopen', { method: 'POST', body: {} }).then(function (x) { if (x.ok && x.data) { st.turn = x.data; st.draft = x.data.draft; renderUnderstood(); } });
      } })));
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
    api('/api/tradeoffs', { method: 'POST', body: { request: st.messages.join('\n').slice(0, 2000), draft: st.draft, messages: st.messages } }).then(function (r) {
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
      chipBtn.textContent = !s ? 'Sending: unknown' : (s.paused ? 'Sending paused' : (s.automatic_stopped ? 'Automatic stopped' : 'Sending on'));
      chipBtn.setAttribute('data-paused', s && s.paused ? 'true' : 'false');
      var held = s ? s.approved_waiting : 0;
      $('#sending-state').textContent = !s ? 'The sending state could not be read.' : (s.paused ? 'Sending is paused. Nothing new goes out' + (held ? '; ' + held + ' approved message' + (held === 1 ? ' is' : 's are') + ' held.' : '.') : 'Sending is on. Messages go out only after you approve their exact text' + (held ? ' (' + held + ' approved and waiting for the next send).' : '.'));
      $('#sending-toggle').textContent = s && s.paused ? 'Resume sending' : 'Pause all sending';
      $('#auto-state').textContent = !s ? '' : (s.automatic_stopped ? 'Automatic messages are stopped.' : 'Automatic messages are allowed (only under a standing permission you granted).');
      $('#auto-toggle').textContent = s && s.automatic_stopped ? 'Allow automatic messages again' : 'Stop automatic messages';
      var lapsed = s && s.lapsed ? s.lapsed : [], note = $('#lapsed-note');
      note.hidden = !lapsed.length;
      note.textContent = lapsed.length ? lapsed.length + ' approval' + (lapsed.length === 1 ? ' ran' : 's ran') + ' out before the message went, so ' + (lapsed.length === 1 ? 'it was' : 'they were') + ' not sent (' + lapsed.slice(0, 5).map(function (m) { return m.vendor + ' by ' + m.channel; }).join('; ') + (lapsed.length > 5 ? '; and more' : '') + '). Write a fresh draft for ' + (lapsed.length === 1 ? 'it' : 'each') + '.' : '';
      if (!s || !s.paused) $('#release-panel').hidden = true;
    }
    function showRelease(s, note) {
      var panel = $('#release-panel'), ul = clear($('#release-list')), n = s.approved_waiting;
      $('#release-text').textContent = (note ? note + ' ' : '') + n + ' approved message' + (n === 1 ? ' is' : 's are') + ' held, across every plan in this workspace.';
      (s.held || []).forEach(function (m) { ul.appendChild(h('li', null, h('b', { text: m.vendor }), ' by ' + m.channel + ': ' + m.subject + (m.approved_by === 'mandate' ? ' (under a standing permission)' : '') + (m.approved_hours_ago !== null && m.approved_hours_ago !== undefined ? ', approved ' + m.approved_hours_ago + ' h ago' : ''))); });
      if (n > (s.held || []).length) ul.appendChild(h('li', { class: 'hint', text: 'and ' + (n - s.held.length) + ' more' }));
      $('#release-go').textContent = n === 1 ? 'Release 1 queued message' : 'Release ' + n + ' queued messages';
      panel.setAttribute('data-count', String(n)); panel.setAttribute('data-snapshot', s.held_snapshot || ''); panel.hidden = false;
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
      var cur = st.sending;
      if (cur && cur.paused) {  // resuming releases what is held: say what it is first, and send nothing until the person has seen it
        if (cur.approved_waiting > 0) { showRelease(cur, ''); smsg(''); return; }
        api('/api/sending/resume', { method: 'POST', body: {} }).then(function (r) { smsg(r.ok ? 'Sending resumed.' : errText(r, 'That did not work')); loadAll(); });
        return;
      }
      api('/api/sending/pause', { method: 'POST', body: {} }).then(function (r) { smsg(r.ok ? 'Sending is paused. Nothing new will be sent; a message already on its way can\'t be called back.' : errText(r, 'That did not work')); loadAll(); });
    });
    $('#release-keep').addEventListener('click', function () { $('#release-panel').hidden = true; smsg('Still paused.'); });
    $('#release-go').addEventListener('click', function () {
      var n = parseInt($('#release-panel').getAttribute('data-count') || '0', 10), snap = $('#release-panel').getAttribute('data-snapshot') || undefined;
      api('/api/sending/resume', { method: 'POST', body: { snapshot: snap } }).then(function (r) {
        if (r.ok) {
          var o = r.data || {};
          $('#release-panel').hidden = true;
          smsg('Sending resumed. Of ' + (o.queued !== undefined ? o.queued : n) + ' queued: ' + (o.sent || 0) + ' sent, ' + (o.waiting || 0) + ' still waiting (a limit or the sending window), ' + ((o.stopped || []).length) + ' stopped by a check' + ((o.stopped || []).length ? ' (' + o.stopped.slice(0, 3).map(function (m) { return m.vendor + ': ' + m.why; }).join('; ') + ')' : '') + '.');
          loadAll(); return;
        }
        if (r.status === 409 && r.data && r.data.waiting !== undefined) {  // something changed since the list was shown: nothing was released, so show it again
          loadSending().then(function () { showRelease(st.sending || { approved_waiting: r.data.waiting, held: r.data.held, held_snapshot: r.data.held_snapshot }, 'The list changed while you were looking.'); smsg('Nothing was sent.'); });
          return;
        }
        smsg(errText(r, 'That did not work'));
      });
    });
    $('#auto-toggle').addEventListener('click', function () {
      var stop = !(st.sending && st.sending.automatic_stopped);
      api(stop ? '/api/autoreply/kill' : '/api/autoreply/resume', { method: 'POST', body: {} }).then(function (r) { smsg(r.ok ? (stop ? 'Automatic messages are stopped.' : 'Automatic messages are allowed again.') : errText(r, 'That did not work')); loadAll(); });
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
