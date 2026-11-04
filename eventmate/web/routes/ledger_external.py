"""State, ledger, quotes, weather, travel, discovery, deals and trade-offs."""

from __future__ import annotations

import json
from datetime import date, datetime, UTC
from flask import jsonify, request
from eventmate.documents import sandbox
from eventmate.state import build_state
from eventmate.integrations.explore import route as explore_route
from eventmate.schemas import DealQuery, TripRequest, VendorCategory
from eventmate.integrations.travel import check_date
from eventmate.web.base import (
    ApplyBody,
    CheapestDatesBody,
    QuoteLineConfirmBody,
    ExploreBody,
    FlightBody,
    LedgerBody,
    MultiCityBody,
    TradeoffsBody,
    QuoteBody,
)


def register(app, k):
    body = k.body
    em = k.em
    lock = k.lock
    out = k.out
    pid = k.pid
    today_fn = k.today_fn

    @app.get("/api/plans/<plan_id>/state")
    def plan_state(plan_id):
        """Where the plan stands: facts with their source and status, vendors, what is still open, and what changed. Read-only."""
        plan_id = pid(plan_id)
        since = None
        if request.args.get("changes_since"):
            try:
                since = datetime.fromisoformat(request.args["changes_since"].replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("changes_since must be an ISO 8601 time") from None
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
        with lock:
            state = build_state(em, plan_id, changes_since=since, today=today_fn())
        return out(state)

    @app.get("/api/plans/<plan_id>/ledger")
    def ledger_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            summary, entries = em.ledger_summary(plan_id), em.ledger_entries(plan_id)
        return jsonify({"summary": json.loads(summary.model_dump_json()), "entries": [json.loads(e.model_dump_json()) for e in entries]})

    @app.post("/api/plans/<plan_id>/ledger")
    def ledger_add(plan_id):
        """User-reported money only: a quote, a payment or a refund. to;doh-the event planner never initiates a payment."""
        plan_id, b = pid(plan_id), body(LedgerBody)
        with lock:
            em.ledger_add(plan_id, b.kind.value, b.category, b.amount, b.note)
            summary = em.ledger_summary(plan_id)
        return out(summary, 201)

    @app.get("/api/vendors/<vendor_id>/quote-documents")
    def quote_documents(vendor_id):
        """The quote files already read for this vendor, with the state of each line. Read-only."""
        with lock:
            return jsonify({"documents": em.quote_documents(pid(vendor_id))})

    @app.post("/api/vendors/<vendor_id>/quote-document")
    def quote_document(vendor_id):
        """A quote file the user was sent, read into priced lines for them to confirm. Converted in a sandbox, not kept; the lines are unreviewed evidence, never a price."""
        if k.shared:
            return k._err(403, "reading quote files runs on your own machine, not on shared hosting", code="local_only")
        f = request.files.get("file")
        if f is None or len(request.files) != 1:
            raise ValueError("send exactly one file, in the field named file")
        data = f.stream.read(sandbox.MAX_FILE_BYTES + 1)
        with lock:
            return jsonify(em.read_quote_document(pid(vendor_id), f.filename or "", data))

    @app.post("/api/vendors/<vendor_id>/quote-document/<document>/lines/<line>/confirm")
    def quote_line_confirm(vendor_id, document, line):
        """The only place a line read from a quote file becomes a recorded quote. The user chooses how long the price is treated as valid."""
        b = body(QuoteLineConfirmBody)
        with lock:
            obs = em.confirm_quote_line(pid(vendor_id), document, line, b.valid_days)
            report = em.observations.report(vendor_id, today_fn())
            effect = em.price_effect(pid(b.plan_id), vendor_id, document, line, today_fn()) if b.plan_id else None
        return jsonify({"observation": json.loads(obs.model_dump_json()), "report": json.loads(report.model_dump_json()), "effect": effect}), 201

    @app.post("/api/observations/<vendor_id>/quotes")
    def observation_add(vendor_id):
        """A quote the user was given. Evidence is required when the vendor is the source; nothing is sent to the vendor."""
        vendor_id, b = pid(vendor_id), body(QuoteBody)
        fields = b.model_dump(exclude={"category"})
        with lock:
            obs = em.observe_quote(vendor_id, b.category, observed_at=datetime.now(UTC), **fields)
            report = em.observations.report(vendor_id, today_fn())
        return jsonify({"observation": json.loads(obs.model_dump_json()), "report": json.loads(report.model_dump_json())}), 201

    @app.get("/api/observations/<vendor_id>")
    def observation_get(vendor_id):
        vendor_id = pid(vendor_id)
        with lock:
            quotes, report = em.observations.quotes(vendor_id), em.observations.report(vendor_id, today_fn())
        return jsonify({"quotes": [json.loads(q.model_dump_json()) for q in quotes], "report": json.loads(report.model_dump_json()) if report else None})

    @app.get("/api/observations/compare/<category>")
    def observation_compare(category):
        with lock:
            cmp_ = em.observations.compare(VendorCategory(category), today_fn())
        return out(cmp_)

    @app.get("/api/observations/score/<category>")
    def observation_score(category):
        with lock:
            board = em.vendor_scores(VendorCategory(category), today_fn())
        return out(board)

    @app.get("/api/weather/status")
    def weather_status():
        """Lets the UI ask whether weather is set up without provoking an error response."""
        return jsonify({"configured": em.weather.configured()})

    @app.get("/api/weather")
    def weather_get():
        """Forecast for a city and date. Past the provider's horizon the answer says so instead of guessing."""
        raw = request.args.get("date", "")
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD") from None
        check_date(day, today_fn())
        return out(em.weather_forecast(request.args.get("city", ""), day))

    @app.get("/api/plans/<plan_id>/weather-advice")
    def plan_weather_advice(plan_id):
        """The forecast plus plain-code advice (rain backup, heat) for the chosen venue. Advice only."""
        return out(em.weather_advice(pid(plan_id)))

    @app.get("/api/plans/<plan_id>/weather")
    def plan_weather(plan_id):
        return out(em.plan_weather(pid(plan_id)))

    @app.get("/api/discover/venues")
    def discover_venues():
        """Read-only: places mapped on OpenStreetMap. Candidates only; no price or availability."""
        city, kind = request.args.get("city", ""), request.args.get("kind", "venue")
        try:
            limit = int(request.args.get("limit", "20"))
        except ValueError:
            raise ValueError("limit must be a whole number") from None
        res = em.discover_venues(city, kind, limit)  # no app-wide lock: a slow public server must not block other requests
        return out(res)

    @app.post("/api/travel/flights")
    def travel_flights():
        """Indicative fares and schedules from fli. Nothing is booked or paid."""
        b = body(FlightBody)
        check_date(b.date, today_fn())
        res = em.search_flights(b.origin, b.destination, b.date, adults=b.adults, cabin=b.cabin, max_stops=b.max_stops, limit=b.limit)
        return out(res)

    @app.post("/api/travel/cheapest-dates")
    def travel_cheapest_dates():
        """The lowest indicative fare for each day in a range, to pick when to fly. Nothing is booked or paid."""
        b = body(CheapestDatesBody)
        return out(em.cheapest_flight_dates(b.origin, b.destination, b.from_date, b.to_date, today=today_fn(), adults=b.adults, cabin=b.cabin, max_stops=b.max_stops))

    @app.get("/api/places/status")
    def places_status():
        return jsonify({"configured": em.places.configured()})

    @app.get("/api/places")
    def places_get():
        """Ratings and review counts (optionally reviews, which cost more) for a service in a city. Opinions, not prices."""
        return out(em.find_places(request.args.get("kind", ""), request.args.get("city", ""), request.args.get("reviews") == "1"))

    @app.get("/api/explore/status")
    def explore_status():
        return jsonify({"configured": bool(em.router_clients())})

    @app.post("/api/explore")
    def explore():
        """Free text in, one read-only search out. A model picks the search; the same validators as the forms check it; nothing is written."""
        r = explore_route(em, body(ExploreBody).text, today_fn(), em.router_clients())
        return jsonify({"action": r.action, "params": r.params, "result": r.result, "note": r.note, "model": r.model})

    @app.post("/api/deals")
    def deals():
        """The agent searches the web, reads pages safely and ranks advertised prices. Leads only: not quotes, and no plan price changes."""
        return out(em.find_deals(body(DealQuery)))  # no app-wide lock: this waits on the network

    @app.post("/api/travel/trip")
    def travel_trip():
        """Give the stops and nights; the system tries the orders and dates, searches each leg once and ranks packages. Nothing is booked."""
        res = em.plan_trip(body(TripRequest), today_fn())  # no app-wide lock: this waits on the network
        return out(res)

    @app.post("/api/travel/multi-city")
    def travel_multi_city():
        """Built from separate one-way searches (Google does not serve multi-city to fli); the response says so."""
        b = body(MultiCityBody)
        for leg in b.legs:
            check_date(leg.date, today_fn())
        res = em.search_multi_city([(l.origin, l.destination, l.date) for l in b.legs], adults=b.adults, cabin=b.cabin, max_stops=b.max_stops, limit=b.limit)
        return out(res)

    @app.post("/api/tradeoffs")
    def tradeoffs():
        b = body(TradeoffsBody)
        fares = em.guest_fares(b.guest_travel, today_fn()) if b.guest_travel else None  # waits on the network: before the lock
        with lock:
            if b.draft is not None:
                report = em.tradeoffs_for_draft(b.draft, b.request, today=today_fn(), guest_list_id=b.guest_list_id, travel=b.guest_travel, fares=fares, messages=b.messages)
            else:
                report = em.tradeoffs(b.request, today=today_fn(), guest_list_id=b.guest_list_id, travel=b.guest_travel, fares=fares)
        return out(report)

    @app.post("/api/tradeoffs/apply")
    def apply_tradeoff():
        """Runs the chosen option through the full pipeline; only ``option.request`` is used, and it is re-validated."""
        b = body(ApplyBody)
        with lock:
            outcome = em.apply_tradeoff(b.option, today=today_fn(), guest_list_id=b.guest_list_id)
        payload = {
            "status": outcome.status,
            "plan": json.loads(outcome.plan.model_dump_json()) if outcome.plan else None,
            "violations": [v.model_dump() for v in outcome.violations],
            "tasks": [{"id": t.id, "agent": t.agent.value, "status": t.status.value, "depends_on": list(t.depends_on)} for t in outcome.task_graph.tasks],
        }
        return jsonify(payload), (201 if outcome.plan else 422)
