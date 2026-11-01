"""Phase 2.5 and 5.1 in shared mode: quotas per workspace, lookups per workspace, and nothing a guest does can send."""

import socket
import urllib.request

import pytest

from tests.workspace_helpers import Visitor, shared_app


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the network was reached")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setenv("EVENTMATE_ENABLE_LLM", "0")


def pair(tmp_path):
    app, registry = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    return app, registry, a, b


def test_a_workspace_over_its_storage_quota_cannot_add_more_but_can_delete_and_another_is_unaffected(tmp_path, monkeypatch):
    app, registry, a, b = pair(tmp_path)
    plan = a.make_plan()
    monkeypatch.setenv("EVENTMATE_WS_MAX_BYTES", "1000")  # smaller than one plan
    registry._usage_cache.clear()
    r = a.post("/api/plans", {"request": "anything"})
    assert r.status_code == 507 and r.get_json() == {"error": "this workspace is full; delete something before adding more", "code": "workspace_quota"}
    assert a.get(f"/api/plans/{plan}").status_code == 200  # reading is never refused
    monkeypatch.setenv("EVENTMATE_WS_MAX_BYTES", str(50 * 1024 * 1024))
    registry._usage_cache.clear()
    assert b.make_plan()  # B was never at its limit


def test_a_workspace_that_has_made_its_plans_for_today_is_refused_with_a_code_and_another_is_not(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_WS_MAX_PLANS_PER_DAY", "2")
    app, registry, a, b = pair(tmp_path)
    for _ in range(2):
        a.make_plan()
        registry._usage_cache.clear()
    third = a.post("/api/plans", {"request": "anything"})
    assert third.status_code == 429 and third.get_json()["code"] == "plans_per_day"
    assert b.make_plan()


def test_outside_lookups_are_capped_per_workspace_with_a_clear_code(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_WS_LOOKUPS_PER_HOUR", "2")
    app, registry, a, b = pair(tmp_path)
    codes = [a.get("/api/weather?city=Pune&date=2026-12-12", headers={"X-EventMate-CSRF": a.csrf}).status_code for _ in range(3)]
    assert 429 not in codes[:2] and codes[2] == 429
    assert a.get("/api/weather?city=Pune&date=2026-12-12", headers={"X-EventMate-CSRF": a.csrf}).get_json()["code"] == "lookup_quota"
    assert b.get("/api/weather?city=Pune&date=2026-12-12", headers={"X-EventMate-CSRF": b.csrf}).status_code != 429


SENDING = [
    "/api/plans/{p}/outreach/{d}/approve", "/api/plans/{p}/outreach/{d}/followup", "/api/plans/{p}/autoreply", "/api/inbox/poll", "/api/jobs/tick",
    "/api/jobs/{d}/approve-requests", "/api/jobs/{d}/items/{d}/approve-nudge", "/api/notify/propose", "/api/notify/confirm",
]


@pytest.mark.parametrize("path", SENDING)
def test_a_guest_cannot_trigger_any_sending_route(tmp_path, path):
    app, registry, a, b = pair(tmp_path)
    plan = a.make_plan()
    r = a.post(path.format(p=plan, d="aaaaaaaaaaaa"))
    assert r.status_code == 403 and r.get_json() == {"error": "sending is not enabled for guest workspaces", "code": "sending_disabled"}, (path, r.get_json())


def test_a_guest_workspace_has_no_channel_that_could_send_even_if_a_route_were_missed(tmp_path):
    app, registry, a, b = pair(tmp_path)
    ws = registry.open(a.credential())
    channels = ws.em.conversations.channels
    assert all(c.status().can_send is False and c.status().can_receive is False for c in channels.values())
    calls = []
    for c in channels.values():
        c.send = lambda *args, **kw: calls.append(args)
    a.make_plan()
    assert calls == []  # planning, asking and drafting reach no channel


def test_the_owners_own_mailbox_settings_never_reach_a_guest_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_SMTP_HOST", "smtp.owner.example")
    monkeypatch.setenv("EVENTMATE_SMTP_USER", "owner@owner.example")
    monkeypatch.setenv("EVENTMATE_SMTP_PASSWORD", "owner-secret-password")
    monkeypatch.setenv("EVENTMATE_MAIL_FROM", "owner@owner.example")
    app, registry, a, b = pair(tmp_path)
    ws = registry.open(a.credential())
    assert ws.em.conversations.channels["email"].config.smtp_host == "" and ws.em.conversations.channels["email"].config.password == ""
    assert "owner-secret-password" not in a.get("/api/session").get_data(as_text=True)
