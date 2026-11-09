"""The stage stream sends every stage a run goes through, however fast the run is (F9). It used to sample the pulse every 0.1 s, so a run that took 0.09 s (Python 3.12 here) was seen only as
"done": no progress, only the end. The pulse now keeps each state the run went through and a stream sends them all, in order."""
import json
import threading

from eventmate.arcade_pulse import STAGES, ArcadePulse
from eventmate.web.app import create_app
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY
from tests.test_pages import events, post


def test_a_run_quicker_than_any_poll_still_leaves_every_stage_in_order():
    p = ArcadePulse()
    first, pos = p.watch(None)
    assert first[0]["state"] == "idle"
    p.begin_run("x")
    for name in STAGES:  # the whole run, with no pause for a reader to look in between
        p.stage_started(name)
        p.stage_done(name)
    p.finish(True)
    pictures, pos2 = p.watch(pos)
    states = [tuple(s["state"] for s in pic["stages"]) for pic in pictures]
    assert pictures[0]["state"] == "running" and pictures[-1]["state"] == "done" and pos2 > pos
    for i, name in enumerate(STAGES):  # each stage was seen active, then done, one after the other
        assert any(st[i] == "active" and all(x == "done" for x in st[:i]) for st in states), name
    assert len(set(states)) >= 2 * len(STAGES), "waiting, then each stage active and done"


def test_a_second_look_sees_only_what_is_new_and_a_new_run_starts_a_new_trail():
    p = ArcadePulse()
    p.begin_run("a")
    p.stage_started("parse")
    _, pos = p.watch(None)
    assert p.watch(pos)[0] == []
    p.stage_done("parse")
    (pic,), pos = p.watch(pos)
    assert pic["stages"][0]["state"] == "done"
    p.finish(True)
    p.begin_run("b")
    new, _ = p.watch(pos)
    assert [x["run_id"] for x in new] == [2], "the old run's trail is gone; the new run starts its own"
    assert new[-1]["run_id"] == 2 and new[-1]["state"] == "running" and all(s["state"] == "waiting" for s in new[-1]["stages"])


def test_the_stream_of_a_fast_real_run_sends_each_stage(tmp_path):
    c = create_app(EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="trail"), today_fn=lambda: TODAY, poll_seconds=0).test_client()
    resp = c.get("/api/pulse/stream", buffered=False)
    got = []
    th = threading.Thread(target=lambda: got.extend(events(resp)))
    th.start()
    assert post(c, "/api/plans", {"request": EXAMPLE}).status_code in (201, 422)
    th.join(70)
    seen = {tuple(s["state"] for s in d["stages"]) for n, d in got if n == "progress"}
    assert any("active" in st for st in seen), "a stage was shown running"
    assert json.dumps(sorted(seen))  # every picture is plain data
