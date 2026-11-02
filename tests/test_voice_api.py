"""The voice routes: access rules, upload limits, streaming, cancel, and the opt-in storage of original notes."""

import io
import json
import os
import stat
import time

import pytest

import eventmate.web.app as webapp
from eventmate.assistant.identity import Principal, Role
from eventmate.service import EventMate
from eventmate.voice import audio
from eventmate.voice.transcribe import Transcriber
from eventmate.web.app import create_app
from tests.conftest import EXAMPLE, TODAY
from tests.test_assistant import answer, tool
from tests.test_voice_audio import WEBM, FakeEngine, mp4, wav
from tests.test_voice_turns import World

QUESTION = "compare the vendors for me please"
EVENT_TOOL = tool("get_public_event_info")
EVENT_ANSWER = answer("The event is in Pune on 2026-12-12.", "get_public_event_info")


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="voiceapi")


@pytest.fixture
def pid(em):
    return em.plan(EXAMPLE, today=TODAY).plan.plan_id


class Rig:
    def __init__(self, em, engine=None, *replies, **kw):
        self.world = World(em, *(replies or (EVENT_TOOL, EVENT_ANSWER)))
        self.engine = engine
        self.transcriber = Transcriber(engine, em.store.root / "voice" / "tmp", **kw)
        self.client = create_app(em, today_fn=lambda: TODAY, assistant=self.world.assistant, voice=self.world.svc, transcriber=self.transcriber).test_client()

    def csrf(self):
        return {"X-EventMate-CSRF": self.client.get("/api/session").get_json()["csrf"]}

    def post(self, url, body, **headers):
        return self.client.post(url, data=json.dumps(body), headers={**self.csrf(), "Content-Type": "application/json", **headers})

    def send_audio(self, data=None, declared="audio/wav", field="audio", name="note.wav", extra=None, csrf=True):
        files = {field: (io.BytesIO(wav() if data is None else data), name, declared)}
        files.update(extra or {})
        return self.client.post("/api/voice/transcribe", data=files, headers=self.csrf() if csrf else {}, content_type="multipart/form-data")

    def open(self, pid):
        return self.post("/api/voice/sessions", {"plan_id": pid}).get_json()["session_id"]


@pytest.fixture
def rig(em):
    return Rig(em, FakeEngine())


def sse(response):
    events = []
    for block in response.get_data(as_text=True).strip().split("\n\n"):
        name, data = block.split("\n", 1)
        events.append({"event": name.removeprefix("event: "), "data": json.loads(data.removeprefix("data: "))})
    return events


# ------------------------------------------------------------------ capabilities and transcription


def test_capabilities_say_what_is_on_and_default_to_nothing(em):
    plain = create_app(em, today_fn=lambda: TODAY).test_client()
    got = plain.get("/api/voice/capabilities").get_json()
    assert got["transcribe"] == {"route": "none", "engine": None, "model": None, "languages": [], "max_seconds": 120, "max_bytes": 5 * 1024 * 1024}
    assert got["keep_original"] is False
    on = Rig(em, FakeEngine()).client.get("/api/voice/capabilities").get_json()
    assert on["transcribe"]["route"] == "server" and on["transcribe"]["engine"] == "fake" and on["transcribe"]["model"] == "fake-tiny" and on["keep_original"] is False


def test_capabilities_state_the_groups_the_page_gates_on_explicitly(em):  # R3 b
    for client in (create_app(em, today_fn=lambda: TODAY).test_client(), Rig(em, FakeEngine()).client):
        got = client.get("/api/voice/capabilities").get_json()
        for group in ("sessions", "turns", "live"):
            assert isinstance(got[group]["enabled"], bool), group
        assert got["live"]["reason_unavailable"] is None or isinstance(got["live"]["reason_unavailable"], str)
        assert {"route", "engine", "model", "max_seconds", "max_bytes"} <= set(got["transcribe"])


def test_a_note_is_transcribed_and_nothing_is_sent_to_the_assistant_or_kept(rig, em):
    r = rig.send_audio(wav(2.0), name="../../etc/evil.wav", extra=None)
    assert r.status_code == 200 and r.get_json() == {"transcript": "I want a vegetarian caterer", "language": "en", "duration_s": 3.0}
    assert rig.world.model.prompts == [] and rig.world.assistant.traces.read() == []  # transcribing is not asking
    assert list((em.store.root / "voice" / "tmp").iterdir()) == [] and not (em.store.root / "voicenotes").exists()
    assert not (em.store.root.parent / "etc").exists() and b"vegetarian" not in b"".join(p.read_bytes() for p in em.store.root.rglob("*") if p.is_file())


def test_with_no_engine_transcribe_says_so_plainly(em):
    r = Rig(em, None).send_audio()
    assert r.status_code == 503 and r.get_json()["error"] == "voice transcription is not set up here; type your message instead"


def test_the_transcribe_route_needs_csrf_one_audio_part_and_a_known_language(rig):
    assert rig.send_audio(csrf=False).status_code == 403
    assert rig.send_audio(field="file").status_code == 400
    assert rig.send_audio(extra={"second": (io.BytesIO(wav()), "b.wav", "audio/wav")}).status_code == 400
    assert rig.client.post("/api/voice/transcribe", data={}, headers=rig.csrf(), content_type="multipart/form-data").status_code == 400
    assert rig.client.post("/api/voice/transcribe", data=json.dumps({"audio": "x"}), headers={**rig.csrf(), "Content-Type": "application/json"}).status_code == 400
    assert rig.client.post("/api/voice/transcribe", data=b"x", headers=rig.csrf(), content_type="text/plain").status_code == 415
    bad = rig.client.post("/api/voice/transcribe", data={"audio": (io.BytesIO(wav()), "a.wav", "audio/wav"), "language": "english!"}, headers=rig.csrf(), content_type="multipart/form-data")
    assert bad.status_code == 400
    good = rig.client.post("/api/voice/transcribe", data={"audio": (io.BytesIO(wav()), "a.wav", "audio/wav"), "language": "hi-IN"}, headers=rig.csrf(), content_type="multipart/form-data")
    assert good.status_code == 200 and rig.engine.seen[-1]["language"] == "hi-IN"


@pytest.mark.parametrize("data,declared,status", [(b"", "audio/wav", 400), (b"<html>", "audio/wav", 415), (wav(), "audio/webm", 415), (WEBM, "text/plain", 415)])
def test_bad_audio_is_refused_with_the_right_status(rig, data, declared, status):
    r = rig.send_audio(data, declared)
    assert r.status_code == status and r.get_json()["error"] and rig.engine.seen == []


def test_an_oversize_recording_is_refused_and_the_normal_routes_keep_their_small_limit(rig, pid):
    assert rig.send_audio(WEBM + b"\x00" * audio.MAX_BYTES, "audio/webm").status_code == 413  # past the audio limit, before the engine
    assert rig.send_audio(b"x" * (audio.MAX_BYTES + 400_000), "audio/webm").status_code == 413  # past what the route will even read
    assert rig.post(f"/api/plans/{pid}/ledger", {"junk": "x" * 70_000}).status_code == 413
    assert rig.engine.seen == []


def test_a_recording_is_judged_by_its_bytes_not_its_filename(rig):
    r = rig.send_audio(mp4(3), "audio/mp4", name="innocent.txt")
    assert r.status_code == 200
    assert rig.engine.seen[-1]["path"].suffix == ".m4a"


# ------------------------------------------------------------------ sessions, turns, cancel


def test_sessions_open_close_and_need_csrf_and_a_real_plan(rig, pid):
    assert rig.client.post("/api/voice/sessions", data=json.dumps({"plan_id": pid}), headers={"Content-Type": "application/json"}).status_code == 403
    r = rig.post("/api/voice/sessions", {"plan_id": pid})
    body = r.get_json()
    assert r.status_code == 201 and body["limits"]["idle_timeout_s"] == 90 and body["expires_at"] and len(body["session_id"]) >= 20
    assert rig.post("/api/voice/sessions", {"plan_id": "nope"}).status_code == 404
    assert rig.post("/api/voice/sessions", {"plan_id": pid, "extra": 1}).status_code == 422
    assert rig.post("/api/voice/sessions", {"plan_id": "../x"}).status_code == 422
    sid = body["session_id"]
    assert rig.client.delete(f"/api/voice/sessions/{sid}", data="{}", headers={**rig.csrf(), "Content-Type": "application/json"}).get_json() == {"ended": True}
    assert rig.client.delete(f"/api/voice/sessions/{sid}", data="{}", headers={**rig.csrf(), "Content-Type": "application/json"}).status_code == 404
    assert rig.client.delete("/api/voice/sessions/!!", data="{}", headers={**rig.csrf(), "Content-Type": "application/json"}).status_code == 404


def turn_body(sid, n=1, **kw):
    return {"session_id": sid, "turn_id": f"turn-{n:04d}", "generation": n, "text": QUESTION, "input_mode": "live", **kw}


def test_a_turn_streams_server_sent_events(rig, pid):
    sid = rig.open(pid)
    r = rig.post("/api/voice/turns", turn_body(sid), Accept="text/event-stream")
    assert r.status_code == 200 and r.mimetype == "text/event-stream" and r.headers["Cache-Control"] == "no-store" and r.headers["X-Accel-Buffering"] == "no"
    ev = sse(r)
    assert [e["event"] for e in ev] == ["status", "sentence", "final"] and ev[2]["data"]["answer"] == "The event is in Pune on 2026-12-12." and ev[2]["data"]["turn_id"] == "turn-0001"
    assert ev[1]["data"]["seq"] == 0 and ev[2]["data"]["spoken_text"]


def test_without_asking_for_a_stream_the_same_fields_come_back_as_json(rig, pid):
    sid = rig.open(pid)
    r = rig.post("/api/voice/turns", turn_body(sid))
    body = r.get_json()
    assert r.status_code == 200 and r.is_json and body["turn_id"] == "turn-0001" and body["error"] is None
    assert body["final"]["answer"] == "The event is in Pune on 2026-12-12." and body["sentences"] == [{"seq": 0, "text_spoken": "The event is in Pune on 2026-12-12.", "text_display": "The event is in Pune on 2026-12-12."}]


def test_a_very_short_transcript_in_json_mode_gets_the_clarifying_question(rig, pid):
    sid = rig.open(pid)
    body = rig.post("/api/voice/turns", turn_body(sid, text="uh")).get_json()
    assert body["final"]["outcome"] == "clarify" and body["error"] is None


def test_turn_requests_are_validated_and_stale_or_reused_turns_are_conflicts(rig, pid):
    sid = rig.open(pid)
    assert rig.post("/api/voice/turns", turn_body(sid, generation="1")).status_code == 422  # a number, not text
    assert rig.post("/api/voice/turns", turn_body(sid, generation=True)).status_code == 422
    assert rig.post("/api/voice/turns", {**turn_body(sid), "extra": 1}).status_code == 422
    assert rig.post("/api/voice/turns", turn_body(sid, input_mode="typed")).status_code == 400
    assert rig.post("/api/voice/turns", turn_body(sid, text="w " * 400)).status_code == 400
    assert rig.post("/api/voice/turns", turn_body(sid, 5)).status_code == 200
    assert rig.post("/api/voice/turns", turn_body(sid, 5)).status_code == 409
    assert rig.post("/api/voice/turns", {**turn_body(sid, 6), "turn_id": "turn-0005"}).status_code == 409
    assert rig.post("/api/voice/turns", turn_body("no-such-session-id")).status_code == 404
    assert rig.client.post("/api/voice/turns", data=json.dumps(turn_body(sid, 9)), headers={"Content-Type": "application/json"}).status_code == 403


def cancel_body(sid, n=1, chars=0, **kw):
    return {"session_id": sid, "generation": n, "playback_chars": chars, **kw}


def test_cancel_over_http_follows_the_same_rules(rig, pid):
    sid = rig.open(pid)
    rig.post("/api/voice/turns", turn_body(sid))
    r = rig.post("/api/voice/turns/turn-0001/cancel", cancel_body(sid, 1, 10))
    assert r.status_code == 200 and r.get_json()["state"] == "interrupted"
    assert rig.post("/api/voice/turns/turn-0001/cancel", cancel_body(sid, 1, 10)).get_json()["state"] == "interrupted"
    assert rig.post("/api/voice/turns/turn-9999/cancel", cancel_body(sid)).status_code == 404
    assert rig.post("/api/voice/turns/x/cancel", cancel_body(sid)).status_code == 404  # too short to be an id: the same answer
    assert rig.post("/api/voice/turns/turn-0001/cancel", {"playback_chars": 0}).status_code == 422  # session_id and generation are required
    assert rig.post("/api/voice/turns/turn-0001/cancel", {"session_id": sid, "playback_chars": 0}).status_code == 422
    assert rig.post("/api/voice/turns/turn-0001/cancel", {"session_id": sid, "generation": 1}).status_code == 422
    assert rig.post("/api/voice/turns/turn-0001/cancel", cancel_body(sid, 1, "5")).status_code == 422
    assert rig.post("/api/voice/turns/turn-0001/cancel", cancel_body(sid, 1, -1)).status_code == 400
    assert rig.post("/api/voice/turns/turn-0001/cancel", cancel_body(sid, 1, 0, extra=1)).status_code == 422
    assert rig.client.post("/api/voice/turns/turn-0001/cancel", data="{}", headers={"Content-Type": "application/json"}).status_code == 403


def test_cancel_binds_to_the_session_and_generation_over_http(rig, pid):  # R3 c
    a, b = rig.open(pid), rig.open(pid)
    rig.post("/api/voice/turns", turn_body(a, 3))
    rig.post("/api/voice/turns", {**turn_body(b, 1), "turn_id": "turn-other-0001"})
    unknown = rig.post("/api/voice/turns/turn-nope-0001/cancel", cancel_body(a, 3))
    assert unknown.status_code == 404
    wrong_session = rig.post("/api/voice/turns/turn-0003/cancel", cancel_body(b, 3))  # a real turn id, named under the wrong session
    ended = rig.client.delete(f"/api/voice/sessions/{b}", headers=rig.csrf())
    assert ended.status_code == 200
    ended_cancel = rig.post("/api/voice/turns/turn-other-0001/cancel", cancel_body(b, 1))
    assert wrong_session.status_code == ended_cancel.status_code == 404
    assert wrong_session.get_json() == ended_cancel.get_json() == unknown.get_json()  # one generic body for every miss: no oracle
    assert rig.post("/api/voice/turns/turn-0003/cancel", cancel_body(a, 2)).status_code == 409  # right session, wrong generation
    assert rig.world.svc._sessions[a].turns["turn-0003"].state == "answered"  # and nothing changed
    assert rig.post("/api/voice/turns/turn-0003/cancel", cancel_body(a, 3)).get_json()["state"] == "already_final"


def test_a_bodyless_delete_closes_the_callers_own_session_and_gives_the_generic_404_otherwise(rig, pid, em):  # R3 d
    sid = rig.open(pid)
    r = rig.client.delete(f"/api/voice/sessions/{sid}", headers=rig.csrf())  # no body, no content type
    assert r.status_code == 200 and r.get_json() == {"ended": True}
    assert rig.post("/api/voice/turns", turn_body(sid)).status_code == 404  # it no longer accepts turns
    other = Rig(em, FakeEngine())
    theirs = other.world.svc.create_session(Principal(user_id="someone", roles=(Role.ORGANIZER,)), pid)["session_id"]
    miss = other.client.delete(f"/api/voice/sessions/{theirs}", headers=other.csrf())
    unknown = other.client.delete("/api/voice/sessions/no-such-session-id", headers=other.csrf())
    assert miss.status_code == unknown.status_code == 404 and miss.get_json() == unknown.get_json()
    assert rig.client.delete(f"/api/voice/sessions/{sid}", headers={}).status_code == 403  # the CSRF header is still required
    assert rig.post(f"/api/plans/{pid}/guests/preview", {"csv": "name\nA\n"}).status_code == 200  # other routes still insist on JSON
    assert rig.client.post("/api/voice/sessions", data="x", headers=rig.csrf()).status_code == 415


def test_too_many_open_streams_get_a_busy_answer_and_the_refused_turn_is_not_left_thinking(em, pid, monkeypatch):
    monkeypatch.setattr(webapp, "VOICE_STREAMS_MAX", 1)
    r = Rig(em, FakeEngine(), EVENT_TOOL, EVENT_ANSWER)
    sid = r.open(pid)
    first = r.client.post("/api/voice/turns", data=json.dumps(turn_body(sid, 1)), headers={**r.csrf(), "Content-Type": "application/json", "Accept": "text/event-stream"}, buffered=False)
    assert first.status_code == 200
    next(iter(first.response))  # the stream is open and holding the only slot
    second = r.post("/api/voice/turns", turn_body(sid, 2), Accept="text/event-stream")
    assert second.status_code == 429 and "streams" in second.get_json()["error"]
    assert r.world.svc._sessions[sid].turns["turn-0002"].state == "cancelled"
    first.close()
    third = r.post("/api/voice/turns", turn_body(sid, 3), Accept="text/event-stream")  # the slot came back when the stream closed
    assert third.status_code == 200


def test_the_whole_voice_note_flow_transcribe_then_review_then_send(rig, pid):
    text = rig.send_audio(wav(2.5)).get_json()["transcript"]
    assert text == "I want a vegetarian caterer"
    sid = rig.open(pid)
    body = rig.post("/api/voice/turns", {**turn_body(sid), "text": text, "input_mode": "voice_note", "language": "en", "confidence": 0.9}).get_json()
    assert body["final"]["outcome"] == "answered"


def test_a_session_from_one_plan_cannot_be_used_for_another_and_a_deleted_plan_fails_plainly(em, pid):
    r = Rig(em, FakeEngine(), EVENT_TOOL, EVENT_ANSWER)
    sid = r.open(pid)
    em.delete_plan(pid)
    body = r.post("/api/voice/turns", turn_body(sid)).get_json()
    assert body["final"] is None and body["error"]["code"] == "failed" and pid not in json.dumps(body)


# ------------------------------------------------------------------ kept originals (opt-in)


def test_keeping_the_original_is_off_by_default_and_says_so(rig, pid, em):
    r = rig.client.post(f"/api/plans/{pid}/voice-notes", data={"audio": (io.BytesIO(wav()), "a.wav", "audio/wav")}, headers=rig.csrf(), content_type="multipart/form-data")
    assert r.status_code == 503 and "not turned on" in r.get_json()["error"] and not (em.store.root / "voicenotes").exists()


@pytest.fixture
def keep(rig, em):
    em.voice_notes.enabled = True
    return rig


def save(rig, pid, data=None, declared="audio/wav"):
    return rig.client.post(f"/api/plans/{pid}/voice-notes", data={"audio": (io.BytesIO(wav(2.0) if data is None else data), "a.wav", declared)}, headers=rig.csrf(), content_type="multipart/form-data")


def test_a_kept_note_is_private_served_back_with_no_cache_and_deletable(keep, pid, em):
    r = save(keep, pid)
    note = r.get_json()
    assert r.status_code == 201 and note["bytes"] == len(wav(2.0)) and note["duration_s"] == 2.0 and len(note["note_id"]) == 32
    assert keep.client.get("/api/voice/capabilities").get_json()["keep_original"] is True
    f = next((em.store.root / "voicenotes" / pid).iterdir())
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE(f.parent.stat().st_mode) == 0o700 and f.name == note["note_id"] + ".wav"
    got = keep.client.get(f"/api/plans/{pid}/voice-notes/{note['note_id']}")
    assert got.status_code == 200 and got.data == wav(2.0) and got.mimetype == "audio/wav" and got.headers["Cache-Control"] == "no-store" and "attachment" in got.headers["Content-Disposition"]
    gone = keep.client.delete(f"/api/plans/{pid}/voice-notes/{note['note_id']}", data="{}", headers={**keep.csrf(), "Content-Type": "application/json"})
    assert gone.get_json() == {"deleted": True} and not f.exists()
    assert keep.client.get(f"/api/plans/{pid}/voice-notes/{note['note_id']}").status_code == 404


def test_kept_notes_are_checked_like_any_upload_and_need_csrf_and_a_real_plan(keep, pid):
    assert save(keep, pid, b"<html>").status_code == 415 and save(keep, pid, b"").status_code == 400 and save(keep, "nope").status_code == 404
    assert save(keep, pid, WEBM + b"\x00" * audio.MAX_BYTES, "audio/webm").status_code == 413
    assert keep.client.post(f"/api/plans/{pid}/voice-notes", data={"audio": (io.BytesIO(wav()), "a.wav", "audio/wav")}, content_type="multipart/form-data").status_code == 403
    for bad in ("../../x", "g" * 32, "A" * 32, "abc", "0" * 31):
        assert keep.client.get(f"/api/plans/{pid}/voice-notes/{bad}").status_code == 404
    assert keep.client.get(f"/api/plans/{pid}/voice-notes/{'0' * 32}").status_code == 404


def test_a_kept_note_never_reaches_a_log_a_trace_a_prompt_or_another_plan(keep, pid, em):
    note = save(keep, pid, wav(1.0)).get_json()["note_id"]
    other = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    assert keep.client.get(f"/api/plans/{other}/voice-notes/{note}").status_code == 404  # a note id alone does not open it under another plan
    blob = b"".join(p.read_bytes() for p in list(em.logs.decisions.log_dir.rglob("*")) if p.is_file())
    assert note.encode() not in blob and keep.world.model.prompts == []


def test_kept_notes_expire_and_go_with_the_plan(keep, pid, em):
    note = save(keep, pid).get_json()["note_id"]
    f = next((em.store.root / "voicenotes" / pid).iterdir())
    old = time.time() - 31 * 86400
    os.utime(f, (old, old))
    assert keep.client.get(f"/api/plans/{pid}/voice-notes/{note}").status_code == 404  # past retention it is already unreadable
    assert em.purge_logs() == [f"voice note {note}"] and not f.exists() and not (em.store.root / "voicenotes" / pid).exists()
    save(keep, pid)
    save(keep, pid)
    removed = em.delete_plan(pid)
    assert removed["message_and_ledger_files"] >= 2 and not (em.store.root / "voicenotes" / pid).exists()


def test_the_retention_period_is_configurable_and_has_a_floor(em, monkeypatch):
    monkeypatch.delenv("EVENTMATE_VOICE_NOTE_DAYS", raising=False)
    assert em.voice_notes.retention_days == 30
    monkeypatch.setenv("EVENTMATE_VOICE_NOTE_DAYS", "7")
    assert em.voice_notes.retention_days == 7
    monkeypatch.setenv("EVENTMATE_VOICE_NOTE_DAYS", "0")
    assert em.voice_notes.retention_days == 1
    monkeypatch.delenv("EVENTMATE_VOICE_KEEP_ORIGINAL", raising=False)
    fresh = EventMate(log_dir=em.store.root.parent / "l2", plan_dir=em.store.root.parent / "p2", run_id="x")
    assert fresh.voice_notes.enabled is False
    monkeypatch.setenv("EVENTMATE_VOICE_KEEP_ORIGINAL", "1")
    assert fresh.voice_notes.enabled is True
    monkeypatch.setenv("EVENTMATE_VOICE_KEEP_ORIGINAL", "no")
    assert fresh.voice_notes.enabled is False


def test_the_csp_has_no_recorder_allowance_because_the_pages_have_no_recorder(rig):
    """The 3D room recorded and played back audio in the page, which needed ``blob:`` for media. The landing page and the planner do not, so the policy does not allow it;
    a page that records again must widen the policy on purpose, and this test is where that choice is seen."""
    csp = rig.client.get("/").headers["Content-Security-Policy"]
    assert "media-src 'self';" in csp and "blob:" not in csp and "default-src 'none'" in csp and "connect-src 'self';" in csp and "unsafe-inline" not in csp
