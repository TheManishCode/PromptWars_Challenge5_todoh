"""Phase 5.4: the upload routes' error shapes (code, limit, scope), the required Content-Length, and the measured worst case of the JSON envelope."""

import io
import json

import pytest

from eventmate.guestlist import MAX_BYTES
from eventmate.service import EventMate
from eventmate.voice import audio
from eventmate.web.app import AUDIO_UPLOAD_LIMIT, GUEST_UPLOAD_LIMIT, create_app
from tests.conftest import EXAMPLE, TODAY


@pytest.fixture
def client(tmp_path):
    return create_app(EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="up"), today_fn=lambda: TODAY).test_client()


@pytest.fixture
def pid(client):
    return client.post("/api/plans", data=json.dumps({"request": EXAMPLE}), headers={**csrf(client), "Content-Type": "application/json"}).get_json()["plan"]["plan_id"]


def csrf(client):
    return {"X-EventMate-CSRF": client.get("/api/session").get_json()["csrf"]}


def post_json(client, url, body):
    return client.post(url, data=json.dumps(body), headers={**csrf(client), "Content-Type": "application/json"})


def test_a_body_over_64_kb_on_an_ordinary_route_is_413_with_the_limit_and_the_envelope_scope(client, pid):
    r = post_json(client, f"/api/plans/{pid}/ledger", {"junk": "x" * 70_000})
    assert r.status_code == 413 and r.get_json() == {"error": "request too large", "code": "too_large", "limit_bytes": 64 * 1024, "scope": "envelope"}


def test_an_upload_over_the_envelope_reports_that_routes_own_limit(client, pid):
    r = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": "x" * 700_000})
    assert r.status_code == 413 and r.get_json()["scope"] == "envelope" and r.get_json()["limit_bytes"] == GUEST_UPLOAD_LIMIT and r.get_json()["code"] == "too_large"
    # the limit belongs to the route: the same body to another route is held to 64 KB, and a small body to the upload route is fine
    assert post_json(client, f"/api/plans/{pid}/ledger", {"junk": "x" * 70_000}).get_json()["limit_bytes"] == 64 * 1024


def test_a_csv_over_its_own_limit_inside_the_envelope_reports_the_payload_scope(client, pid):
    r = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": "name\n" + "A" * (MAX_BYTES + 10)})
    assert r.status_code == 413 and r.get_json() == {"error": r.get_json()["error"], "code": "too_large", "limit_bytes": MAX_BYTES, "scope": "payload"}


def test_the_worst_case_json_envelope_of_a_valid_csv_fits_the_route_limit():
    """Measured, not guessed. A CSV at the 256 KB payload limit, wrapped as {"csv": "..."} the way a browser does (JSON.stringify keeps non-ASCII as it is)."""
    worst = {
        "quotes": '"' * MAX_BYTES,  # each quote is escaped to two bytes
        "newlines": "\n" * MAX_BYTES,
        "backslashes": "\\" * MAX_BYTES,
        "non-ascii (3 bytes each)": "€" * (MAX_BYTES // 3),
    }
    sizes = {k: len(json.dumps({"csv": v}, ensure_ascii=False).encode()) for k, v in worst.items()}
    assert all(size <= 2 * MAX_BYTES + 64 for size in sizes.values()), sizes  # about twice the payload
    assert max(sizes.values()) < GUEST_UPLOAD_LIMIT, sizes  # 640 KB leaves room: the largest measured is 512 KB plus the wrapper
    # the one shape that does not fit: a client that escapes every non-ASCII character (ensure_ascii) and sends four-byte characters, which cost 12 bytes each
    assert len(json.dumps({"csv": "😀" * (MAX_BYTES // 4)}).encode()) > GUEST_UPLOAD_LIMIT


def test_a_wrong_content_type_on_an_upload_route_has_a_code_and_elsewhere_keeps_todays_shape(client, pid):
    on_upload = client.post(f"/api/plans/{pid}/guests/preview", data="name\nA", headers=csrf(client), content_type="text/plain")
    elsewhere = client.post(f"/api/plans/{pid}/ledger", data="x", headers=csrf(client), content_type="text/plain")
    assert on_upload.status_code == 415 and on_upload.get_json()["code"] == "unsupported_type"
    assert elsewhere.status_code == 415 and elsewhere.get_json() == {"error": "JSON body required"}


def test_an_upload_without_a_content_length_is_refused_before_anything_is_read(client, pid):
    chunked = {"CONTENT_LENGTH": "", "HTTP_TRANSFER_ENCODING": "chunked"}
    r = client.open(f"/api/plans/{pid}/guests/preview", method="POST", input_stream=io.BytesIO(b'{"csv": "name\\nA"}'), headers={**csrf(client), "Content-Type": "application/json"}, environ_overrides=chunked)
    assert r.status_code == 411 and r.get_json()["code"] == "length_required"
    ordinary = client.open(f"/api/plans/{pid}/ledger", method="POST", input_stream=io.BytesIO(b"{}"), headers={**csrf(client), "Content-Type": "application/json"}, environ_overrides=chunked)
    assert ordinary.status_code != 411  # only upload routes insist


@pytest.mark.parametrize("data,status,code", [(b"<html>not audio</html>", 415, "bad_audio_type"), (b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * (audio.MAX_BYTES + 10), 413, "audio_too_large")])
def test_audio_errors_carry_their_codes(tmp_path, data, status, code):
    from tests.test_voice_audio import FakeEngine
    from eventmate.voice.transcribe import Transcriber

    em = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="aud")
    client = create_app(em, today_fn=lambda: TODAY, transcriber=Transcriber(FakeEngine(), tmp_path / "tmp")).test_client()
    r = client.post("/api/voice/transcribe", data={"audio": (io.BytesIO(data), "clip.wav", "audio/wav")}, headers=csrf(client), content_type="multipart/form-data")
    assert r.status_code == status and r.get_json()["code"] == code, r.get_json()


def test_without_a_transcriber_the_route_says_so_with_its_code(client):
    r = client.post("/api/voice/transcribe", data={"audio": (io.BytesIO(b"x"), "clip.wav", "audio/wav")}, headers=csrf(client), content_type="multipart/form-data")
    assert r.status_code == 503 and r.get_json()["code"] == "transcriber_unavailable"


def test_the_audio_envelope_leaves_room_for_the_multipart_wrapper():
    assert AUDIO_UPLOAD_LIMIT > audio.MAX_BYTES  # the wrapper is allowed for; the audio check enforces the real 5 MB
