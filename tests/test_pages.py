"""The landing page (/) and the planner (/app): served, local, cached by version; and the pulse the pipeline reports while it runs (kept as an API for progress)."""

import gzip
import json
import re
from pathlib import Path

import pytest

from eventmate.arcade_pulse import STAGES, ArcadePulse
from eventmate.service import EventMate
from eventmate.web.app import STATIC_FILES, create_app
from tests.conftest import EXAMPLE, TODAY
from tests.test_web import post

STATIC = Path(__file__).resolve().parent.parent / "eventmate/web/static"
OPTIONAL = {"landing-poster.jpg", "landing-hero.mp4"}  # not in the repository (licence unconfirmed); the page works without them


@pytest.fixture
def client(tmp_path):
    em = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="pages")
    return create_app(em, today_fn=lambda: TODAY).test_client()


def states(snap):
    return {s["name"]: s["state"] for s in snap["stages"]}


# ---------------------------------------------------------------- the pulse (progress of a run)


def test_pulse_idle_until_a_run_begins():
    p = ArcadePulse()
    p.stage_started("parse")  # hook outside a run is a no-op
    snap = p.snapshot()
    assert snap["state"] == "idle" and snap["elapsed_ms"] is None
    assert [s["name"] for s in snap["stages"]] == list(STAGES)
    assert set(states(snap).values()) == {"waiting"}


def test_pulse_only_lights_stages_the_pipeline_reports():
    p = ArcadePulse()
    p.begin_run("x")
    p.stage_started("parse")
    p.stage_done("parse")
    p.stage_started("budget")
    snap = p.snapshot()
    assert states(snap)["parse"] == "done" and states(snap)["budget"] == "active"
    assert states(snap)["schedule"] == "waiting"
    p.stage_started("nonsense")  # unknown stage names are ignored
    p.finish(True)
    snap = p.snapshot()
    assert snap["state"] == "done" and states(snap)["budget"] == "done" and states(snap)["schedule"] == "waiting"


def test_pulse_failure_marks_active_stage_and_truncates_text():
    p = ArcadePulse()
    p.begin_run("y" * 500)
    p.stage_started("select")
    p.finish(False, "e" * 1000)
    snap = p.snapshot()
    assert snap["state"] == "failed" and states(snap)["select"] == "failed"
    assert len(snap["label"]) == 120 and len(snap["error"]) == 300
    p.stage_started("validate")  # nothing moves after a run has ended
    assert states(p.snapshot())["validate"] == "waiting"


def test_pulse_endpoint_is_read_only_json(client):
    r = client.get("/api/arcade/pulse")
    assert r.status_code == 200 and r.is_json
    assert [s["name"] for s in r.get_json()["stages"]] == list(STAGES)
    assert client.post("/api/arcade/pulse").status_code in (403, 405)


def test_plan_run_drives_the_pulse(client):
    assert post(client, "/api/plans", {"request": EXAMPLE}).status_code == 201
    snap = client.get("/api/arcade/pulse").get_json()
    assert snap["state"] == "done" and snap["label"].startswith("plan: ")
    assert set(states(snap).values()) == {"done"}


# ---------------------------------------------------------------- the pages


def test_every_listed_static_file_exists_and_page_scripts_and_styles_are_local():
    for name in STATIC_FILES:
        assert (STATIC / name).is_file() or name in OPTIONAL, name
    assert not [n for n in STATIC_FILES if (STATIC / n).is_dir()] and not (STATIC / "vendor").exists()  # nothing third-party is served except the two fonts
    for page in ("landing.html", "app.html"):
        html = (STATIC / page).read_text()
        for src in re.findall(r'<script[^>]+src="([^"]+)"', html) + re.findall(r'<link[^>]+href="(/[^"]+)"', html):
            assert src.startswith("/static/") and src.rsplit("/", 1)[-1] in STATIC_FILES, (page, src)
        assert "http://" not in html and "https://" not in re.sub(r"<meta[^>]+>", "", html), page  # nothing is fetched from, or sent to, another site
        assert not re.search(r"\sstyle=|\son[a-z]+=", html), page  # the CSP forbids inline styles and handlers, so none may be written


def test_the_stylesheets_load_only_from_this_origin():
    for name in ("site.css", "landing.css", "app.css"):
        css = (STATIC / name).read_text()
        assert not re.search(r"url\(\s*['\"]?(?!/static/|data:)", css) and "@import" not in css, name


def test_assets_are_gzipped_only_when_asked_and_round_trip(client):
    for name in ("app.js", "app.css", "site.css", "landing-inter-400.ttf"):
        raw = (STATIC / name).read_bytes()
        plain = client.get(f"/static/{name}")
        assert plain.status_code == 200 and "Content-Encoding" not in plain.headers and plain.data == raw
        zipped = client.get(f"/static/{name}", headers={"Accept-Encoding": "gzip, deflate"})
        assert zipped.headers["Content-Encoding"] == "gzip" and "Accept-Encoding" in zipped.headers["Vary"]
        assert len(zipped.data) < len(raw) and gzip.decompress(zipped.data) == raw


def test_a_listed_file_that_is_not_in_the_checkout_is_a_plain_404_not_an_error(client, monkeypatch):
    monkeypatch.setattr("eventmate.web.routes.pages.STATIC_DIR", STATIC / "nowhere")
    assert client.get("/static/landing-hero.mp4").status_code == 404


def test_the_hero_video_when_present_is_streamed_with_ranges_not_read_whole(client):
    if not (STATIC / "landing-hero.mp4").is_file():
        pytest.skip("the hero video is not in this checkout")
    r = client.get("/static/landing-hero.mp4", headers={"Range": "bytes=0-99", "Accept-Encoding": "gzip"})
    assert r.status_code == 206 and len(r.data) == 100 and "Content-Encoding" not in r.headers


def test_a_page_names_no_hero_media_that_is_not_in_the_checkout(client, monkeypatch, tmp_path):
    import shutil

    for name in ("landing.html", "app.html"):
        shutil.copy(STATIC / name, tmp_path / name)
    monkeypatch.setattr("eventmate.web.routes.pages.STATIC_DIR", tmp_path)
    html = client.get("/").get_data(as_text=True)
    assert "landing-hero.mp4" not in html and "landing-poster.jpg" not in html and "<video" not in html and 'class="hero__poster"' not in html and "poster=" not in html
    assert "<h1" in html and "hero__content" in html  # the page itself is whole without them
    (tmp_path / "landing-poster.jpg").write_bytes(b"x")
    assert 'class="hero__poster"' in client.get("/").get_data(as_text=True)  # and with a file present its tag is kept


def test_the_two_pages_link_to_each_other_and_nothing_else_is_an_interface(client):
    landing, planner = client.get("/").get_data(as_text=True), client.get("/app").get_data(as_text=True)
    assert 'href="/app"' in landing and 'href="/"' in planner and "<h1" in landing and 'id="brief-form"' in planner
    for gone in ("/classic", "/static/index.html", "/static/style.css", "/static/arcade.html"):
        assert client.get(gone).status_code == 404, gone


# ---------------------------------------------------------------- live progress (server-sent events)


def events(resp):
    out, name = [], None
    for raw in resp.response:
        for line in (raw.decode() if isinstance(raw, bytes) else raw).splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                out.append((name, json.loads(line[6:])))
    return out


def test_the_stream_follows_a_real_run_stage_by_stage_and_ends_when_it_does(tmp_path):
    import threading

    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="sse")
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    resp = c.get("/api/pulse/stream", buffered=False)
    got = []
    th = threading.Thread(target=lambda: got.extend(events(resp)))
    th.start()
    assert post(c, "/api/plans", {"request": EXAMPLE}).status_code in (201, 422)  # a real run in this workspace drives the stream
    th.join(70)
    assert not th.is_alive()
    assert {n for n, _ in got} == {"progress"}  # it ended because the run did, not because it gave up waiting
    runs = [d for n, d in got if n == "progress" and d["state"] in ("running", "done", "failed")]
    assert runs and runs[-1]["state"] in ("done", "failed") and runs[-1]["run_id"] >= 1
    assert [s["name"] for s in runs[-1]["stages"]] == list(STAGES)
    assert len({tuple(s["state"] for s in d["stages"]) for d in runs}) >= 2  # it moved: more than one picture of the run was sent


def test_a_stream_with_no_run_gives_up_and_says_so(tmp_path, monkeypatch):
    import eventmate.web.routes.assistant_plans as ap

    monkeypatch.setattr(ap, "PULSE_STREAM_MAX_S", 0.4)
    c = create_app(EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="sse2"), today_fn=lambda: TODAY, poll_seconds=0).test_client()
    got = events(c.get("/api/pulse/stream", buffered=False))
    assert [n for n, _ in got] == ["progress", "timeout"] and got[0][1]["state"] == "idle"


def test_streams_are_limited_and_the_slot_comes_back(tmp_path, monkeypatch):
    import eventmate.web.routes.assistant_plans as ap
    from eventmate.web.base import VOICE_STREAMS_MAX

    monkeypatch.setattr(ap, "PULSE_STREAM_MAX_S", 0.3)
    c = create_app(EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="sse3"), today_fn=lambda: TODAY, poll_seconds=0).test_client()
    held = [c.get("/api/pulse/stream", buffered=False) for _ in range(VOICE_STREAMS_MAX)]
    assert c.get("/api/pulse/stream").status_code == 429
    for r in held:
        events(r)  # each finishes (times out) and gives its slot back
    assert c.get("/api/pulse/stream", buffered=False).status_code == 200


def test_the_stream_is_read_only(tmp_path):
    c = create_app(EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="sse4"), today_fn=lambda: TODAY, poll_seconds=0).test_client()
    assert c.post("/api/pulse/stream").status_code in (403, 405)
