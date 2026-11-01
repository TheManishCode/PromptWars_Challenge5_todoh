# ---- /metrics (Prometheus)


def test_metrics_is_a_404_without_the_owner_credential_and_text_with_it(tmp_path, monkeypatch):
    from datetime import date

    from eventmate.service import EventMate
    from eventmate.web.app import create_app

    monkeypatch.setenv("EVENTMATE_OWNER_TOKEN", "owner-token-0123456789abcdef")
    app = create_app(EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="m"), today_fn=lambda: date(2026, 10, 30))
    c = app.test_client()
    assert c.get("/metrics").status_code == 404 and c.get("/metrics", headers={"X-EventMate-Owner": "wrong-wrong-wrong-wrong"}).status_code == 404
    c.get("/healthz")
    r = c.get("/metrics", headers={"Authorization": "Bearer owner-token-0123456789abcdef"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/plain"
    assert 'eventmate_http_requests_total{route="/healthz",status="2xx"}' in body
    assert "eventmate_http_request_duration_seconds_bucket{le=\"+Inf\"}" in body and "eventmate_disk_free_bytes" in body and "eventmate_uptime_seconds" in body


def test_metrics_labels_hold_no_ids_addresses_or_content():
    from eventmate.web import observe

    m = observe.Metrics()
    m.end(200, 12, "/api/plans/<plan_id>/state")
    m.end(404, 400, None)
    text = observe.prometheus(m, {"uptime_seconds": 5}, {"calls": 3})
    assert 'route="/api/plans/<plan_id>/state"' in text and 'route="unmatched",status="4xx"' in text and "eventmate_model_calls 3" in text
    assert 'le="0.05"} 1' in text and 'le="0.5"} 2' in text and "_count 2" in text


def test_the_route_label_set_is_bounded():
    from eventmate.web import observe

    m = observe.Metrics()
    for i in range(2000):
        m.end(200, 1, f"/r/{i}")
    assert len(m._routes) <= 600
