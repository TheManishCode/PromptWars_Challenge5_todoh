"""8.3, 8.5, 8.7, 8.8, 8.11 (items 2, 3, 7, 8, 10, 13, 14) at the store level: one SQLite file, many credentials, a controllable clock."""

import hashlib
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, UTC

import pytest

from eventmate.wall import layout
from eventmate.wall.store import SCHEMA_VERSION, WallConfig, WallError, WallSchemaError, WallStore
from tests.wall_helpers import make_wall, wall_db
from zoneinfo import ZoneInfoNotFoundError


def h(n):
    return hashlib.sha256(f"cred-{n}".encode()).hexdigest()


def visitor(store, n, name=None, approve=True):
    store.create_session(h(n))
    status, body, created = store.submit(h(n), f"idem-{n}-aaaaaaaa", name or f"Name{n}x")
    assert status == 201 and created
    if approve:
        store.approve(body["mark_id"])
    return body["mark_id"]


# ------------------------------------------------------------------ schema (8.3)

def test_the_schema_contains_the_required_constraints(tmp_path):
    store, _ = make_wall(tmp_path)
    with wall_db(store.path) as c:
        sql = " ".join(r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")).replace("\n", " ")
    assert "mark_id INTEGER UNIQUE REFERENCES marks(mark_id)" in sql
    assert "CHECK (capacity BETWEEN 1 AND 300)" in sql
    assert "CREATE UNIQUE INDEX marks_slot ON marks(section_id, slot) WHERE section_id IS NOT NULL" in sql
    assert "PRIMARY KEY (period_id, mark_id)" in sql and "hall_results_top ON hall_results(period_id, best_elapsed_ms, best_elapsed_at, mark_id)" in sql


def test_the_file_is_private_and_a_newer_schema_is_refused(tmp_path):
    store, _ = make_wall(tmp_path)
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"
    with wall_db(store.path) as c:
        c.execute("UPDATE meta SET schema_version = ?", (SCHEMA_VERSION + 1,))
    with pytest.raises(WallSchemaError, match="schema version"):
        WallStore(store.path)
    other = tmp_path / "x.db"
    with wall_db(other) as c:
        c.execute("CREATE TABLE t (a)")
    with pytest.raises(WallSchemaError, match="not a wall database"):
        WallStore(other)


def test_the_capacity_is_bounded_by_config_and_by_the_database(tmp_path):
    with pytest.raises(ValueError):
        WallConfig(section_capacity=301)
    with pytest.raises(ValueError):
        WallConfig(section_capacity=0)
    store, _ = make_wall(tmp_path)
    with wall_db(store.path) as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO sections(section_id, layout_version, capacity) VALUES (0, 1, 301)")


# ------------------------------------------------------------------ slots (8.11 item 3)

def test_slots_are_assigned_once_never_reused_and_never_move(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=4)
    ids = [visitor(store, i) for i in range(10)]
    placed = {m["mark_id"]: (m["section_id"], m["slot"]) for m in store.list_marks("visible")}
    assert sorted(placed.values()) == [(s, k) for s in range(3) for k in range(4)][:10]
    store.hide(ids[2])
    store.remove(ids[3])
    store.restore(ids[2])
    new = visitor(store, 99)
    again = {m["mark_id"]: (m["section_id"], m["slot"]) for m in store.list_marks("visible")}
    for mid, pos in placed.items():
        if mid != ids[3]:
            assert again[mid] == pos, "an existing mark moved"
    assert again[new] == (2, 2) and len(set(again.values())) == len(again)  # the removed mark's slot (0, 3) stayed a tombstone; the new mark went to the end


def test_a_section_fills_and_a_new_one_opens_with_the_stored_capacity_and_layout(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=2)
    visitor(store, 1), visitor(store, 2)
    assert store.meta()["section_count"] == 1
    visitor(store, 3)
    assert store.meta()["section_count"] == 2
    s0, s1 = store.section(0), store.section(1)
    assert (s0["capacity"], s1["capacity"]) == (2, 2) and (s0["layout_version"], s1["layout_version"]) == (1, 1)
    # a larger default applies to new sections only
    bigger = WallStore(store.path, replace(store.config, section_capacity=5), clock=store.clock)
    visitor(bigger, 4)
    visitor(bigger, 5)  # fills section 1 (capacity 2 stored), then opens section 2 with 5
    assert [bigger.section(i)["capacity"] for i in range(3)] == [2, 2, 5]


def test_sections_are_zero_based_contiguous_and_unknown_ids_404(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=1)
    for i in range(3):
        visitor(store, i)
    assert [store.section(i)["section_id"] for i in range(3)] == [0, 1, 2]
    with pytest.raises(WallError) as e:
        store.section(3)
    assert e.value.status == 404


def test_slot_uniqueness_holds_under_concurrent_approvals(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=7)
    ids = [visitor(store, i, approve=False) for i in range(30)]
    errors = []

    def go(chunk):
        try:
            for mid in chunk:
                store.approve(mid)
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(ids[i::6],)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors
    pos = [(m["section_id"], m["slot"]) for m in store.list_marks("visible", 100)]
    assert len(pos) == 30 and len(set(pos)) == 30
    assert sorted({p[0] for p in pos}) == list(range(5))


def test_removing_a_pending_mark_leaves_no_position_and_a_hidden_one_keeps_its_slot(tmp_path):
    store, _ = make_wall(tmp_path)
    a, b = visitor(store, 1, approve=False), visitor(store, 2)
    store.remove(a)
    removed = [m for m in store.list_marks("removed")][0]
    assert removed["section_id"] is None and removed["name"] == ""
    before = store.list_marks("visible")[0]
    store.hide(b)
    store.restore(b)
    assert store.list_marks("visible")[0]["slot"] == before["slot"]


def test_removal_blanks_the_name_at_once_everywhere(tmp_path):
    store, _ = make_wall(tmp_path)
    m = visitor(store, 1, "Visible One")
    store.remove(m)
    assert "Visible One" not in str(store.section(0)) and store.me(h(1))["mark"] == {"mark_id": m, "status": "removed", "name": None, "section_id": None, "slot": None}
    with wall_db(store.path) as c:
        assert c.execute("SELECT display_name FROM marks").fetchone()[0] == ""


def test_counts_visible_and_lifetime(tmp_path):
    store, _ = make_wall(tmp_path)
    ids = [visitor(store, i) for i in range(3)]
    store.hide(ids[0])
    store.remove(ids[1])
    st = store.stats()
    assert st["visible_count"] == 1 and st["accepted_lifetime"] == 3
    store.restore(ids[0])
    assert store.stats()["visible_count"] == 2 and store.stats()["accepted_lifetime"] == 3


def test_layout_positions_resolve_for_old_versions_after_a_new_one_is_added(monkeypatch):
    before = [layout.slot_position(1, 48, s) for s in range(48)]
    monkeypatch.setitem(layout.LAYOUTS, 2, (6, 0.1))
    assert [layout.slot_position(1, 48, s) for s in range(48)] == before
    assert layout.slot_position(2, 48, 0) != before[0]
    assert len(set(before)) == 48 and all(0 <= u <= 1 and 0 <= v <= 1 for u, v in before)
    with pytest.raises(layout.UnknownLayout):
        layout.slot_position(99, 48, 0)
    with pytest.raises(ValueError):
        layout.slot_position(1, 48, 48)


# ------------------------------------------------------------------ one mark per credential (8.11 item 2)

def test_one_credential_one_mark_and_a_retry_returns_the_original(tmp_path):
    store, _ = make_wall(tmp_path)
    store.create_session(h(1))
    first = store.submit(h(1), "key-one-aaaa", "Asha")
    again = store.submit(h(1), "key-one-aaaa", "Asha")
    assert first[0] == 201 and again == (201, first[1], False)
    other_key = store.submit(h(1), "key-two-aaaa", "Different")
    assert other_key[0] == 409 and other_key[1]["code"] == "already_marked" and other_key[1]["mark_id"] == first[1]["mark_id"]
    assert store.stats()["marks_by_status"] == {"pending": 1}


def test_parallel_submits_with_one_credential_make_exactly_one_mark(tmp_path):
    store, _ = make_wall(tmp_path)
    store.create_session(h(1))
    out = []
    gate = threading.Barrier(8)

    def go(i):
        gate.wait()
        out.append(store.submit(h(1), f"key-{i}-aaaaaaaa", f"Name{i}x")[0])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert sorted(out) == [201] + [409] * 7 and store.stats()["marks_by_status"] == {"pending": 1}


def test_two_credentials_with_the_same_name_both_succeed_and_a_name_grants_nothing(tmp_path):
    store, _ = make_wall(tmp_path)
    a, b = visitor(store, 1, "Same Name"), visitor(store, 2, "Same Name")
    assert a != b
    with pytest.raises(WallError):
        store.set_hall(h(3), True)  # an unrelated credential with the same name text owns nothing


def test_an_invalid_name_changes_nothing_and_uses_no_idempotency_key(tmp_path):
    store, _ = make_wall(tmp_path)
    store.create_session(h(1))
    with pytest.raises(WallError) as e:
        store.submit(h(1), "key-one-aaaa", "<b>x</b>")
    assert e.value.code == "invalid_name"
    assert store.submit(h(1), "key-one-aaaa", "Asha")[0] == 201


def test_a_submit_without_a_session_is_refused(tmp_path):
    store, _ = make_wall(tmp_path)
    with pytest.raises(WallError) as e:
        store.submit(h(9), "key-one-aaaa", "Asha")
    assert e.value.status == 401


def test_post_moderation_makes_a_mark_visible_at_once(tmp_path):
    store, _ = make_wall(tmp_path, moderation="post")
    mid = visitor(store, 1, approve=False)
    assert store.me(h(1))["mark"]["status"] == "visible" and store.section(0)["slots"][0]["mark_id"] == mid


def test_caps_close_the_wall_cleanly(tmp_path):
    store, clock = make_wall(tmp_path, daily_submission_cap=2, pending_cap=100)
    visitor(store, 1, approve=False), visitor(store, 2, approve=False)
    assert store.meta()["accepting"] is False
    store.create_session(h(3))
    with pytest.raises(WallError) as e:
        store.submit(h(3), "key-three-aaa", "Third")
    assert (e.value.status, e.value.code) == (503, "wall_closed")
    clock.advance(hours=25)
    assert store.meta()["accepting"] is True
    store2, _ = make_wall(tmp_path / "t", total_cap=1)
    visitor(store2, 1, approve=False)
    assert store2.meta()["accepting"] is False
    store3, _ = make_wall(tmp_path / "p", pending_cap=1)
    visitor(store3, 1, approve=False)
    assert store3.meta()["accepting"] is False
    store3.set_accepting(False)
    assert store3.meta()["accepting"] is False


def test_unclaimed_sessions_are_bounded_and_swept(tmp_path):
    store, clock = make_wall(tmp_path, unclaimed_sessions_max=3)
    visitor(store, 50)  # a session that holds a mark is not counted as unclaimed
    for i in range(3):
        store.create_session(h(i))
    with pytest.raises(WallError) as e:
        store.create_session(h(9))
    assert e.value.status == 429
    clock.advance(days=31)
    assert store.sweep()["unclaimed_sessions"] == 3
    assert store.me(h(50))["has_mark"] is True


# ------------------------------------------------------------------ recovery and removal (8.11 item 8)

CODE_A, CODE_B = hashlib.sha256(b"code-a").hexdigest(), hashlib.sha256(b"code-b").hexdigest()


def test_recovery_code_issue_replace_and_recover_are_atomic(tmp_path):
    store, _ = make_wall(tmp_path)
    mid = visitor(store, 1, "Owner")
    store.issue_recovery_code(h(1), CODE_A, False)
    with pytest.raises(WallError) as e:
        store.issue_recovery_code(h(1), CODE_B, False)
    assert e.value.code == "code_exists"
    store.issue_recovery_code(h(1), CODE_B, True)  # replaces; the old one is dead
    store.create_session(h(2))
    with pytest.raises(WallError) as e:
        store.recover(CODE_A, h(2), h(3))
    assert (e.value.status, e.value.code) == (404, "not_found")
    got = store.recover(CODE_B, h(2), h(3))
    assert got == {"mark": {"mark_id": mid, "name": "Owner", "status": "visible", "section_id": 0, "slot": 0}, "has_recovery_code": False}
    assert store.me(h(3))["has_mark"] and store.me(h(3))["has_recovery_code"] is False
    with pytest.raises(WallError):
        store.me(h(1))  # the old credential is gone
    with pytest.raises(WallError):
        store.me(h(2))  # the spent anonymous session is gone
    store.create_session(h(4))
    with pytest.raises(WallError) as e:
        store.recover(CODE_B, h(4), h(5))  # a replayed code fails
    assert e.value.code == "not_found"


def test_recover_carries_rounds_and_submissions_to_the_new_credential(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=20)
    visitor(store, 1)
    for i in range(2, 6):
        visitor(store, i)
    rid = store.issue_round(h(1), "untimed")["round_id"]
    store.issue_recovery_code(h(1), CODE_A, False)
    store.create_session(h(9))
    store.recover(CODE_A, h(9), h(10))
    assert store.cancel_round(h(10), rid) is True
    assert store.submit(h(10), "idem-fresh-aaaa", "Anything")[1]["code"] == "already_marked"


def test_remove_with_the_cookie_or_with_the_code(tmp_path):
    store, _ = make_wall(tmp_path)
    visitor(store, 1, "Cookie"), visitor(store, 2, "Coded")
    store.remove_mine(h(1))
    store.remove_mine(h(1))  # idempotent
    assert store.me(h(1))["mark"]["status"] == "removed" and store.me(h(1))["has_mark"] is True  # a removed credential still counts as marked
    store.issue_recovery_code(h(2), CODE_A, False)
    store.create_session(h(3))
    with pytest.raises(WallError):
        store.remove_by_code(CODE_B, h(3))
    store.remove_by_code(CODE_A, h(3))
    assert store.me(h(2))["mark"]["status"] == "removed" and store.me(h(2))["has_recovery_code"] is False  # the code is consumed
    with pytest.raises(WallError):
        store.remove_by_code(CODE_A, h(3))
    assert "Cookie" not in str(store.section(0)) and "Coded" not in str(store.section(0))
    with pytest.raises(WallError) as e:
        store.remove_mine(h(3))  # no mark
    assert e.value.status == 404
    st = store.stats()
    assert st["visible_count"] == 0 and st["marks_by_status"] == {"removed": 2}


def test_a_removed_credential_cannot_submit_again(tmp_path):
    store, _ = make_wall(tmp_path)
    visitor(store, 1)
    store.remove_mine(h(1))
    assert store.submit(h(1), "idem-new-aaaaaa", "Second Try")[0] == 409


# ------------------------------------------------------------------ rounds (8.11 item 7)

def crowd(store, n, start=10):
    return [visitor(store, start + i) for i in range(n)]


def test_a_lone_visible_mark_gets_a_round_that_moves_it_to_an_empty_position(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=10)
    visitor(store, 1)
    r = store.issue_round(h(1), "timed")
    assert r["available"] and r["from_slot"] == 0 and r["to_slot"] >= 2  # not the neighbour; at or above next_slot (1)
    assert [s["slot"] for s in r["snapshot"]["slots"]] == [0] and r["snapshot"]["next_slot"] == 1 and r["snapshot"]["capacity"] == 10


def test_a_swap_target_is_a_visible_name_and_never_overlaps(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=6)
    crowd(store, 6, start=20)
    seen = set()
    for i in range(20, 26):
        r = store.issue_round(h(i), "untimed")
        if r["available"]:
            names = {s["slot"] for s in r["snapshot"]["slots"]}
            assert r["to_slot"] in names and r["to_slot"] != r["from_slot"] and abs(r["to_slot"] - r["from_slot"]) > 1
            seen.add(r["to_slot"])
    assert seen


def test_never_a_hidden_marks_slot_and_removed_tombstones_are_valid_targets(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=5)
    ids = crowd(store, 5, start=30)
    store.hide(ids[3])
    store.remove(ids[4])
    seen = set()
    for _k in range(40):
        r = store.issue_round(h(30), "untimed")
        assert r["available"], r
        seen.add(r["to_slot"])
        store.cancel_round(h(30), r["round_id"])
        with wall_db(store.path) as c:
            c.execute("DELETE FROM rounds")  # keep the daily limit out of this loop
    assert 3 not in seen and seen <= {2, 4}  # 3 is hidden; 1 is the neighbour of slot 0; 2 and 4 (a tombstone) remain


def test_available_false_reasons(tmp_path):
    store, clock = make_wall(tmp_path, section_capacity=3, rounds_per_day=2)
    store.create_session(h(1))
    assert store.issue_round(h(1), "timed") == {"available": False, "reason": "not_visible"}  # no mark
    visitor(store, 2, approve=False)
    assert store.issue_round(h(2), "timed")["reason"] == "not_visible"  # pending
    visitor(store, 3)
    # capacity 3, slot 0 is the owner; neighbour 1 excluded; only slot 2 left: ok, then the daily limit and the open round
    first = store.issue_round(h(3), "timed")
    assert first["available"]
    assert store.issue_round(h(3), "timed")["reason"] == "round_in_progress"
    store.cancel_round(h(3), first["round_id"])
    second = store.issue_round(h(3), "timed")
    assert second["available"]
    store.cancel_round(h(3), second["round_id"])
    assert store.issue_round(h(3), "timed")["reason"] == "daily_limit"
    clock.advance(hours=25)
    assert store.issue_round(h(3), "timed")["available"]
    tiny, _ = make_wall(tmp_path / "tiny", section_capacity=2)
    visitor(tiny, 1)
    assert tiny.issue_round(h(1), "timed")["reason"] == "no_target"  # capacity 2: the only other slot is the neighbour


def test_recent_destinations_are_excluded_until_the_bag_runs_out(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=8, rounds_per_day=100, recent_destinations=3)
    visitor(store, 1)
    seq = []
    for _ in range(12):
        r = store.issue_round(h(1), "untimed")
        seq.append(r["to_slot"])
        store.cancel_round(h(1), r["round_id"])
    for i in range(3, len(seq)):
        assert seq[i] not in seq[i - 3:i] or len(set(range(2, 8)) - set(seq[i - 3:i])) == 0
    assert set(seq) <= set(range(2, 8)) and len(set(seq)) > 3


def test_ready_is_idempotent_and_never_resets_the_deadline(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    rid = store.issue_round(h(1), "timed")["round_id"]
    a = store.ready_round(h(1), rid)
    clock.advance(seconds=2)
    b = store.ready_round(h(1), rid)
    assert (a["ready_at"], a["deadline_at"]) == (b["ready_at"], b["deadline_at"]) and b["server_now"] != a["server_now"]
    assert datetime.fromisoformat(a["deadline_at"].replace("Z", "+00:00")) - datetime.fromisoformat(a["ready_at"].replace("Z", "+00:00")) == __import__("datetime").timedelta(milliseconds=6500)


def test_untimed_has_no_deadline_in_the_response(tmp_path):
    store, _ = make_wall(tmp_path)
    visitor(store, 1)
    rid = store.issue_round(h(1), "untimed")["round_id"]
    assert store.ready_round(h(1), rid)["deadline_at"] is None


def played(store, clock, n, mode="timed", wait_ms=1000, hit=True):
    r = store.issue_round(h(n), mode)
    store.ready_round(h(n), r["round_id"])
    clock.advance(milliseconds=wait_ms)
    mid = store.me(h(n))["mark"]["mark_id"]
    if mode == "timed":
        return r, store.tap_round(h(n), r["round_id"], r["to_slot"] if hit else (r["to_slot"] + 1), None)
    return r, store.tap_round(h(n), r["round_id"], None, mid if hit else mid + 1000)


def test_first_tap_consumes_hit_or_miss_and_a_second_tap_is_round_used(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    r, res = played(store, clock, 1, hit=False)
    assert res["result"] == "missed" and res["elapsed_ms"] is None and res["ranked"] is False and res["hall_offer"] is False
    with pytest.raises(WallError) as e:
        store.tap_round(h(1), r["round_id"], r["to_slot"], None)
    assert (e.value.status, e.value.code) == (409, "round_used")
    assert store.stats()["hall_unlocked"] is False


def test_found_sets_found_at_hall_unlocked_and_the_time(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    assert store.meta()["hall_unlocked"] is False and store.me(h(1))["hall_eligible"] is False
    _, res = played(store, clock, 1, wait_ms=1234)
    assert res == {"result": "found", "hall_offer": True, "elapsed_ms": 1234, "ranked": True, "server_now": res["server_now"]}
    assert store.meta()["hall_unlocked"] is True and store.me(h(1))["hall_eligible"] is True and store.me(h(1))["best_elapsed_ms"] == 1234


def test_a_tap_before_ready_is_not_ready_and_not_consumed(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    r = store.issue_round(h(1), "timed")
    with pytest.raises(WallError) as e:
        store.tap_round(h(1), r["round_id"], r["to_slot"], None)
    assert (e.value.status, e.value.code) == (409, "not_ready")
    store.ready_round(h(1), r["round_id"])
    assert store.tap_round(h(1), r["round_id"], r["to_slot"], None)["result"] == "found"


def test_a_late_tap_is_expired_and_consumes_the_round(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    r, res = played(store, clock, 1, wait_ms=6501)
    assert res["result"] == "expired" and res["elapsed_ms"] is None
    assert store.meta()["hall_unlocked"] is False
    with pytest.raises(WallError) as e:
        store.tap_round(h(1), r["round_id"], r["to_slot"], None)
    assert e.value.code == "round_used"


def test_the_deadline_is_window_plus_allowance(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    _, res = played(store, clock, 1, wait_ms=6500)
    assert res["result"] == "found"


def test_wrong_keys_for_the_mode_are_400_and_do_not_consume(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    r = store.issue_round(h(1), "timed")
    store.ready_round(h(1), r["round_id"])
    mid = store.me(h(1))["mark"]["mark_id"]
    for slot, mark in ((None, mid), (r["to_slot"], mid), (None, None)):
        with pytest.raises(WallError) as e:
            store.tap_round(h(1), r["round_id"], slot, mark)
        assert (e.value.status, e.value.code) == (400, "bad_request")
    assert store.tap_round(h(1), r["round_id"], r["to_slot"], None)["result"] == "found"
    u = visitor(store, 2)
    r2 = store.issue_round(h(2), "untimed")
    store.ready_round(h(2), r2["round_id"])
    with pytest.raises(WallError) as e:
        store.tap_round(h(2), r2["round_id"], r2["to_slot"], None)
    assert e.value.status == 400
    assert store.tap_round(h(2), r2["round_id"], None, u)["result"] == "found"


def test_unknown_and_foreign_rounds_are_the_same_404(tmp_path):
    store, _ = make_wall(tmp_path)
    visitor(store, 1), visitor(store, 2)
    rid = store.issue_round(h(1), "timed")["round_id"]
    errs = []
    for fn in (lambda: store.ready_round(h(2), rid), lambda: store.ready_round(h(2), "nope"), lambda: store.tap_round(h(2), rid, 1, None), lambda: store.tap_round(h(2), "nope", 1, None),
               lambda: store.cancel_round(h(2), rid), lambda: store.cancel_round(h(2), "nope")):
        with pytest.raises(WallError) as e:
            fn()
        errs.append((e.value.status, e.value.code, e.value.message))
    assert set(errs) == {(404, "not_found", "not found")}


def test_cancel_works_once_and_reports_false_after(tmp_path):
    store, _ = make_wall(tmp_path)
    visitor(store, 1)
    rid = store.issue_round(h(1), "timed")["round_id"]
    assert store.cancel_round(h(1), rid) is True and store.cancel_round(h(1), rid) is False
    assert store.issue_round(h(1), "timed")["available"]  # no open round any more


def test_an_unstarted_round_expires_and_ready_says_so(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    rid = store.issue_round(h(1), "timed")["round_id"]
    clock.advance(seconds=121)
    with pytest.raises(WallError) as e:
        store.ready_round(h(1), rid)
    assert (e.value.status, e.value.code) == (409, "round_expired")
    assert store.issue_round(h(1), "timed")["available"]


def test_untimed_rounds_never_rank_but_still_set_found_and_unlock(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    _, res = played(store, clock, 1, mode="untimed", wait_ms=60_000)
    assert res["result"] == "found" and res["elapsed_ms"] is None and res["ranked"] is False and res["hall_offer"] is True
    assert store.meta()["hall_unlocked"] is True and store.me(h(1))["hall_eligible"] is True and store.me(h(1))["best_elapsed_ms"] is None
    assert store.hall("fastest")["entries"] == []


def test_an_untimed_round_has_a_hard_safety_limit(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    r, res = played(store, clock, 1, mode="untimed", wait_ms=601_000)
    assert res["result"] == "expired"


def test_a_win_under_the_floor_is_found_unranked_and_still_unlocks(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    _, res = played(store, clock, 1, wait_ms=100)
    assert res["result"] == "found" and res["ranked"] is False and res["elapsed_ms"] == 100
    assert store.meta()["hall_unlocked"] is True and store.me(h(1))["best_elapsed_ms"] is None


# stale rounds ----------------------------------------------------------------

def test_a_hide_restore_removal_or_layout_change_makes_ready_stale_without_counting(tmp_path):
    for action in ("hide", "restore", "remove", "layout"):
        store, clock = make_wall(tmp_path / action, section_capacity=10, rounds_per_day=1)
        visitor(store, 1)
        other = visitor(store, 2)
        if action == "restore":
            store.hide(other)
        rid = store.issue_round(h(1), "timed")["round_id"]
        if action == "hide":
            store.hide(other)
        elif action == "restore":
            store.restore(other)
        elif action == "remove":
            store.remove(other)
        else:
            store.new_layout(1)
        with pytest.raises(WallError) as e:
            store.ready_round(h(1), rid)
        assert (e.value.status, e.value.code) == (409, "round_stale"), action
        again = store.issue_round(h(1), "timed")  # not consumed, not counted against rounds_per_day=1
        assert again["available"], (action, again)


def test_a_plain_approval_does_not_make_a_round_stale(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=10)
    visitor(store, 1)
    rid = store.issue_round(h(1), "timed")["round_id"]
    visitor(store, 2)
    visitor(store, 3, approve=False)
    store.approve(store.list_marks("pending")[0]["mark_id"])
    assert store.ready_round(h(1), rid)["ready_at"]


def test_the_owner_being_hidden_before_ready_is_stale_and_hidden_before_tap_cannot_win(tmp_path):
    store, clock = make_wall(tmp_path, section_capacity=10)
    mine = visitor(store, 1)
    rid = store.issue_round(h(1), "timed")
    store.hide(mine)  # cancels the round and bumps the sequence
    with pytest.raises(WallError):
        store.ready_round(h(1), rid["round_id"])
    store.restore(mine)
    r2 = store.issue_round(h(1), "timed")
    store.ready_round(h(1), r2["round_id"])
    with wall_db(store.path) as c:
        c.execute("UPDATE marks SET status = 'hidden' WHERE mark_id = ?", (mine,))  # hidden behind the store's back after ready
    assert store.tap_round(h(1), r2["round_id"], r2["to_slot"], None)["result"] == "expired"
    assert store.meta()["hall_unlocked"] is False


def test_the_snapshot_is_frozen_names_are_public_and_it_is_bounded(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=300)
    ids = [visitor(store, 100 + i) for i in range(5)]
    r = store.issue_round(h(100), "timed")
    snap = r["snapshot"]
    assert [s["mark_id"] for s in snap["slots"]] == ids and len(snap["slots"]) <= snap["capacity"] == 300 and len(snap["slots"]) <= 300
    late = visitor(store, 500)  # approved after issue: not in the frozen view
    visitor(store, 501, approve=False)
    store.hide(ids[2])  # a later hide cannot change what the client already holds (it is the client's copy), and ready then says stale
    assert late not in [s["mark_id"] for s in snap["slots"]] and ids[2] in [s["mark_id"] for s in snap["slots"]]
    assert all(set(s) == {"slot", "mark_id", "name"} for s in snap["slots"])


def test_a_restore_or_an_old_pending_mark_approved_after_issue_is_not_in_the_view(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=10)
    visitor(store, 1)
    hidden = visitor(store, 2)
    store.hide(hidden)
    old_pending = visitor(store, 3, approve=False)
    r = store.issue_round(h(1), "timed")
    store.approve(old_pending)
    ids = [s["mark_id"] for s in r["snapshot"]["slots"]]
    assert hidden not in ids and old_pending not in ids
    hidden_slot = [m for m in __import__("sqlite3").connect(store.path).execute("SELECT slot FROM marks WHERE mark_id = ?", (hidden,))][0][0]
    assert r["to_slot"] != hidden_slot  # never a hidden mark's slot as an empty target


# ------------------------------------------------------------------ Hall (8.11 items 13, 14)

def test_opt_in_needs_a_found_round_but_opt_out_always_works(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    with pytest.raises(WallError) as e:
        store.set_hall(h(1), True)
    assert (e.value.status, e.value.code) == (409, "not_eligible")
    assert store.set_hall(h(1), False) is False and store.set_hall(h(1), False) is False
    played(store, clock, 1)
    assert store.set_hall(h(1), True) is True and store.me(h(1))["hall"] is True


def test_the_fastest_list_orders_by_time_then_time_of_win_then_mark_id_and_excludes_everything_not_public(tmp_path):
    store, clock = make_wall(tmp_path, section_capacity=30)
    for i in range(1, 8):
        visitor(store, i, f"Player{i}x")
    times = {1: 900, 2: 700, 3: 700, 4: 1500, 5: 400, 6: 800, 7: 2000}
    for i, t in times.items():
        played(store, clock, i, wait_ms=t)
        store.set_hall(h(i), True)
    top = store.hall("fastest")
    assert [(e["rank"], e["elapsed_ms"]) for e in top["entries"]] == [(1, 400), (2, 700), (3, 700), (4, 800), (5, 900)]
    assert [e["name"] for e in top["entries"]][1:3] == ["Player2x", "Player3x"]  # a tie: the earlier win first
    assert top["next_cursor"] is None and top["period"]["id"] == "2026-11"
    assert len(store.hall("fastest", limit=100)["entries"]) == 7  # bounded to 10, only 7 exist
    store.set_hall(h(5), False)  # opted out: gone on the next read, and the row is deleted
    store.hide(store.me(h(2))["mark"]["mark_id"])
    store.remove(store.me(h(3))["mark"]["mark_id"])
    names = [e["name"] for e in store.hall("fastest")["entries"]]
    assert names == ["Player6x", "Player1x", "Player4x", "Player7x"]
    assert store.me(h(5))["best_elapsed_ms"] is None


def test_a_faster_win_replaces_the_best_for_the_period_and_a_slower_one_does_not(tmp_path):
    store, clock = make_wall(tmp_path, section_capacity=30, rounds_per_day=50)
    visitor(store, 1)
    played(store, clock, 1, wait_ms=1000)
    played(store, clock, 1, wait_ms=1500)
    assert store.me(h(1))["best_elapsed_ms"] == 1000
    played(store, clock, 1, wait_ms=600)
    assert store.me(h(1))["best_elapsed_ms"] == 600


def test_newest_order_pages_and_never_shows_non_opted_in_marks(tmp_path):
    store, clock = make_wall(tmp_path, section_capacity=30, rounds_per_day=50)
    for i in range(1, 8):
        visitor(store, i, f"Player{i}x")
        played(store, clock, i, wait_ms=500)
        if i != 4:
            store.set_hall(h(i), True)
        clock.advance(seconds=1)
    page = store.hall("newest", limit=3)
    assert [e["name"] for e in page["entries"]] == ["Player7x", "Player6x", "Player5x"] and page["next_cursor"]
    page2 = store.hall("newest", cursor=page["next_cursor"], limit=3)
    assert [e["name"] for e in page2["entries"]] == ["Player3x", "Player2x", "Player1x"] and page2["next_cursor"] is None
    assert all(e["rank"] is None for e in page["entries"]) and page["entries"][0]["elapsed_ms"] == 500
    for bad in ("###", "e30", "W1tdXQ", "x" * 10):
        with pytest.raises(WallError) as e:
            store.hall("newest", cursor=bad)
        assert e.value.status == 400
    with pytest.raises(WallError):
        store.hall("sideways")


def test_the_period_resets_ranked_times_only_and_on_first_use_in_the_new_month(tmp_path):
    # 2026-11-30 18:29 UTC is 23:59 IST; one minute later it is December in Asia/Calcutta
    store, clock = make_wall(tmp_path, section_capacity=30)
    clock.t = datetime(2026, 11, 30, 18, 28, tzinfo=UTC)
    mid = visitor(store, 1, "Keeper")
    played(store, clock, 1, wait_ms=500)
    store.set_hall(h(1), True)
    assert store.hall("fastest")["period"]["id"] == "2026-11" and store.hall("fastest")["entries"][0]["elapsed_ms"] == 500
    clock.advance(minutes=3)
    assert store.meta()["hall_period"] == {"id": "2026-12", "kind": "month", "starts_at": "2026-11-30T18:30:00Z", "ends_at": "2026-12-31T18:30:00Z"}
    assert store.hall("fastest")["entries"] == []  # a new period starts empty ...
    with wall_db(store.path) as c:
        assert c.execute("SELECT COUNT(*) FROM hall_results").fetchone()[0] == 0  # ... and the old row is gone
    assert store.meta()["hall_unlocked"] is True  # never relocks
    assert store.me(h(1))["hall"] is True and store.me(h(1))["hall_eligible"] is True and store.me(h(1))["mark"]["name"] == "Keeper"  # opt-in, found_at and the name persist
    assert store.section(0)["slots"][0]["mark_id"] == mid


def test_the_period_follows_the_server_zone_and_year_end_and_weeks(tmp_path):
    store, _ = make_wall(tmp_path)
    p = store.period_at(int(datetime(2026, 12, 31, 20, 0, tzinfo=UTC).timestamp() * 1000))
    assert p["id"] == "2027-01" and p["ends_at"] == "2027-01-31T18:30:00Z"
    weekly, _ = make_wall(tmp_path / "w", hall_kind="week")
    w = weekly.period_at(int(datetime(2026, 11, 1, 6, 0, tzinfo=UTC).timestamp() * 1000))
    assert w == {"id": "2026-W44", "kind": "week", "starts_at": "2026-10-25T18:30:00Z", "ends_at": "2026-11-01T18:30:00Z"}


def test_hall_unlocked_survives_everything_and_comes_from_any_found_tap(tmp_path):
    store, clock = make_wall(tmp_path, section_capacity=30)
    mid = visitor(store, 1)
    played(store, clock, 1, mode="untimed")  # an untimed win by someone who never opts in
    assert store.meta()["hall_unlocked"] is True
    store.remove(mid)
    assert store.meta()["hall_unlocked"] is True and store.stats()["visible_count"] == 0
    assert store.hall("fastest")["entries"] == [] and store.hall("newest")["entries"] == []


def test_me_hall_eligible_stays_after_opting_out(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    played(store, clock, 1)
    store.set_hall(h(1), True)
    store.set_hall(h(1), False)
    me = store.me(h(1))
    assert me["hall"] is False and me["hall_eligible"] is True


def test_opting_out_deletes_the_time_and_removing_deletes_all_of_them(tmp_path):
    store, clock = make_wall(tmp_path, rounds_per_day=50)
    mid = visitor(store, 1)
    played(store, clock, 1)
    store.set_hall(h(1), True)
    store.set_hall(h(1), False)
    with wall_db(store.path) as c:
        assert c.execute("SELECT COUNT(*) FROM hall_results").fetchone()[0] == 0
    played(store, clock, 1)
    store.remove(mid)
    with wall_db(store.path) as c:
        assert c.execute("SELECT COUNT(*) FROM hall_results").fetchone()[0] == 0


# ------------------------------------------------------------------ housekeeping, backup, export (8.11 item 10)

def test_sweep_purges_old_keys_rounds_and_sessions_but_never_a_mark(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1)
    rid = store.issue_round(h(1), "timed")["round_id"]
    store.cancel_round(h(1), rid)
    store.create_session(h(2))
    clock.advance(days=31)
    out = store.sweep()
    assert out == {"submissions": 1, "unclaimed_sessions": 1, "rounds": 1}
    assert store.stats()["visible_count"] == 1
    store.submit(h(1), "idem-1-aaaaaaaa", "Anything")  # the key was purged; the credential still holds its mark
    store.maybe_sweep()


def test_backup_restore_round_trip_and_export_public(tmp_path):
    store, clock = make_wall(tmp_path, section_capacity=5)
    visitor(store, 1, "Shown One")
    b, c_ = visitor(store, 2, "Hidden One"), visitor(store, 3, "Removed One")
    visitor(store, 4, "Pending One", approve=False)
    store.hide(b)
    store.remove(c_)
    store.issue_recovery_code(h(1), CODE_A, False)
    out = store.backup(tmp_path / "bk" / "w.bak")
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(FileExistsError):
        store.backup(out)
    from eventmate.wall_admin import restore_backup
    dest = tmp_path / "restored" / "wall.sqlite3"
    restore_backup(out, dest)
    again = WallStore(dest, clock=clock)
    assert again.list_marks("visible") == store.list_marks("visible") and again.stats() == store.stats()
    with pytest.raises(FileExistsError):
        restore_backup(out, dest)
    restore_backup(out, dest, force=True)
    pub = again.export_public()
    assert pub == [{"section_id": 0, "slot": 0, "name": "Shown One"}]
    text = str(pub)
    for forbidden in ("Hidden One", "Removed One", "Pending One", "cred", "recovery"):
        assert forbidden not in text
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        restore_backup(junk, tmp_path / "nope.db")


def test_an_older_schema_fixture_would_be_upgraded_in_order(tmp_path, monkeypatch):
    """No second schema exists yet; this pins the mechanism: step n runs when the file says n and the build wants more."""
    import eventmate.wall.store as ws
    store, _ = make_wall(tmp_path)
    ran = []

    def step1(conn):
        ran.append(1)
        conn.execute("UPDATE meta SET schema_version = 2")

    monkeypatch.setattr(ws, "SCHEMA_VERSION", 2)
    monkeypatch.setitem(ws.UPGRADERS, 1, step1)
    WallStore(store.path)
    assert ran == [1]
    with wall_db(store.path) as c:
        assert c.execute("SELECT schema_version FROM meta").fetchone()[0] == 2


def test_admin_moderation_errors(tmp_path):
    store, _ = make_wall(tmp_path)
    m = visitor(store, 1, approve=False)
    for fn in (lambda: store.approve(999), lambda: store.hide(m), lambda: store.restore(m), lambda: store.reject(999)):
        with pytest.raises(WallError):
            fn()
    store.approve(m)
    with pytest.raises(WallError):
        store.reject(m)
    store.remove(m)
    with pytest.raises(WallError):
        store.approve(m)
    with pytest.raises(WallError):
        store.new_layout(77)


def test_the_owner_can_change_the_main_settings_from_the_environment():
    cfg = WallConfig.from_env({"EVENTMATE_WALL_CAPACITY": "24", "EVENTMATE_WALL_MODERATION": "post", "EVENTMATE_WALL_ZONE": "Europe/London", "EVENTMATE_WALL_HALL_KIND": "week",
                               "EVENTMATE_WALL_WINDOW_MS": "4000", "EVENTMATE_WALL_ROUNDS_PER_DAY": "3", "EVENTMATE_WALL_DAILY_CAP": "50"})
    assert (cfg.section_capacity, cfg.moderation, cfg.hall_zone, cfg.hall_kind, cfg.window_ms, cfg.rounds_per_day, cfg.daily_submission_cap) == (24, "post", "Europe/London", "week", 4000, 3, 50)
    assert WallConfig.from_env({}) == WallConfig() and WallConfig.from_env({"EVENTMATE_WALL_CAPACITY": " "}) == WallConfig()


@pytest.mark.parametrize("env", [{"EVENTMATE_WALL_CAPACITY": "301"}, {"EVENTMATE_WALL_CAPACITY": "0"}, {"EVENTMATE_WALL_CAPACITY": "-5"}, {"EVENTMATE_WALL_CAPACITY": "abc"}, {"EVENTMATE_WALL_MODERATION": "never"},
                                 {"EVENTMATE_WALL_ZONE": "Mars/Base"}, {"EVENTMATE_WALL_HALL_KIND": "year"}])
def test_a_bad_setting_stops_the_app_at_start(env):
    with pytest.raises((ValueError, ZoneInfoNotFoundError)):
        WallConfig.from_env(env)
