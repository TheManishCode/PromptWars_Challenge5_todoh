"""Guest-list import: strict parsing, preview before save, single-use tokens, names kept out of every model prompt, log and trace."""

import io
import json
import stat
from pathlib import Path

import pytest

from eventmate.assistant.agent import Assistant
from eventmate.assistant.identity import LOCAL_ORGANIZER
from eventmate.guestlist import (
    MAX_BYTES, MAX_ROWS, GuestListError, GuestListStore, StalePreview, check_against, parse_guest_csv, totals_of,
)
from eventmate.service import EventMate
from eventmate.state import build_state
from eventmate.web.app import create_app
from tests.conftest import EXAMPLE, TODAY
from tests.test_assistant import Scripted, answer, tool
from tests.test_conversations import FakeChannel

HEADER = "name,party_size,rsvp,dietary\n"
GOOD = HEADER + "Zubin Marzban,2,yes,vegetarian;jain\nKaveri Naidu,1,no,vegan\nTarun Oberoi,4,pending,halal\nIshita Rao,3,yes,vegetarian\n"
NAMES = ("Zubin", "Marzban", "Kaveri", "Naidu", "Tarun", "Oberoi", "Ishita")


def lines(result):
    return sorted((e.line, e.column) for e in result.errors)


# ------------------------------------------------------------------ parsing


def test_a_valid_file_is_parsed_with_counts_by_party_size():
    r = parse_guest_csv(GOOD)
    assert not r.errors and [g.name for g in r.guests][:2] == ["Zubin Marzban", "Kaveri Naidu"] and [g.id for g in r.guests] == ["g001", "g002", "g003", "g004"]
    t = r.totals
    assert (t.rows, t.heads, t.yes, t.no, t.pending) == (4, 10, 5, 1, 4)
    assert t.dietary_counts == {"halal": 1, "jain": 1, "vegetarian": 2} and t.required_diets == ("halal", "jain", "vegetarian")  # the declined guest's vegan is not counted


def test_optional_columns_blank_cells_a_bom_crlf_and_blank_lines():
    r = parse_guest_csv("\ufeffname,rsvp\r\nAsha,yes\r\n\r\n,\r\nRohan,\r\n")
    assert not r.errors and [(g.name, g.party_size, g.rsvp.value) for g in r.guests] == [("Asha", 1, "yes"), ("Rohan", 1, "pending")]
    nameless = parse_guest_csv("party_size,rsvp\n3,yes\n")
    assert nameless.guests[0].name == "Guest 1" and nameless.totals.heads == 3
    assert [g.rsvp.value for g in parse_guest_csv("rsvp\nYES\nNo\n PENDING \n").guests] == ["yes", "no", "pending"]


def test_every_bad_row_is_reported_by_its_file_line_and_column():
    r = parse_guest_csv(HEADER + "Ok One,2,yes,vegan\nBad Size,0,yes,\nBad Rsvp,2,maybe,\nBad Diet,2,yes,pork\nBig,21,yes,\nWord,two,yes,\n")
    assert lines(r) == [(3, "party_size"), (4, "rsvp"), (5, "dietary"), (6, "party_size"), (7, "party_size")]
    assert len(r.guests) == 1 and "pork" not in r.model_dump_json() and all(e.message for e in r.errors)  # a cell is never echoed back in an error
    assert "vegetarian" in next(e.message for e in r.errors if e.column == "dietary")


def test_several_problems_in_one_row_are_all_listed():
    r = parse_guest_csv(HEADER + "X,99,perhaps,pork\n")
    assert lines(r) == [(2, "dietary"), (2, "party_size"), (2, "rsvp")]


@pytest.mark.parametrize("cell", ["=1+1", "+44 20", "-5", "@SUM(A1)", "  =cmd|' /C calc'!A0", "\t=1", "\x07+1", "\u0001 @x"])
def test_formula_starts_are_rejected_even_when_hidden_behind_control_characters_or_spaces(cell):
    for column in ("name", "dietary", "rsvp", "party_size"):
        cols = ["Name", "1", "yes", ""]
        cols[("name", "party_size", "rsvp", "dietary").index(column)] = cell
        text = HEADER + ",".join('"' + c.replace('"', '""') + '"' for c in cols) + "\n"
        r = parse_guest_csv(text)
        assert not r.guests and any(e.column == column and "formula" in e.message for e in r.errors), (column, cell)


def test_a_dash_inside_a_cell_is_fine():
    r = parse_guest_csv(HEADER + "Anne-Marie O'Neil,2,yes,\n")
    assert not r.errors and r.guests[0].name == "Anne-Marie O'Neil"


def test_control_characters_are_stripped_from_names():
    r = parse_guest_csv(HEADER + 'Asha\x07 K\x1bumar,1,yes,\n')
    assert r.guests[0].name == "Asha Kumar"


def test_name_and_cell_length_boundaries():
    ok = "N" * 120
    assert not parse_guest_csv(HEADER + f"{ok},1,yes,\n").errors
    over = parse_guest_csv(HEADER + f"{ok}N,1,yes,\n")
    assert lines(over) == [(2, "name")]
    assert lines(parse_guest_csv(HEADER + "A," + "9" * 401 + ",yes,\n")) == [(2, "party_size")]


@pytest.mark.parametrize("header", ["name,email,rsvp\n", "name,phone\n", "name,Mobile\n", "email\n", "name,name\n", "Asha,2,yes,vegan\n", "name,rsvp,notes\n", "\n"])
def test_the_header_is_strict_and_contact_columns_are_refused_with_a_reason(header):
    with pytest.raises(GuestListError) as e:
        parse_guest_csv(header + "Asha,yes\n")
    assert "Asha" not in str(e.value)  # a first row mistaken for a header is not echoed
    if any(w in header.lower() for w in ("email", "phone", "mobile")):
        assert "does not store guest contact details" in str(e.value)


def test_file_level_problems():
    for raw, needle in ((b"", "empty"), (b"\n\n  \n", "empty"), (b"\xff\xfe\x00bad", "UTF-8"), (b"name\nA\x00B\n", "binary"), (123, "text")):
        with pytest.raises(GuestListError, match=needle):
            parse_guest_csv(raw)
    with pytest.raises(GuestListError, match="not valid CSV"):
        parse_guest_csv("name\n" + "x" * 200_000 + "\n")
    assert parse_guest_csv(HEADER).totals.rows == 0  # a header alone is an empty list, not an error


def test_size_and_row_limits_are_exact():
    rows = "".join(f"G{i},1,yes,\n" for i in range(MAX_ROWS))
    assert parse_guest_csv(HEADER + rows).totals.rows == MAX_ROWS
    with pytest.raises(GuestListError, match="501 rows"):
        parse_guest_csv(HEADER + rows + "One More,1,yes,\n")
    exact = b"name\nA\n" + b"\n" * (MAX_BYTES - 7)  # blank lines parse as nothing, so this is exactly the limit
    assert len(exact) == MAX_BYTES and parse_guest_csv(exact).totals.rows == 1
    with pytest.raises(GuestListError, match="larger than 256 KB"):
        parse_guest_csv(exact + b"\n")


def test_a_row_with_more_cells_than_the_header_is_an_error_not_silently_cut():
    r = parse_guest_csv(HEADER + "Asha,2,yes,vegan,extra\nBo,1,yes,\n")
    assert lines(r) == [(2, "row")] and [g.name for g in r.guests] == ["Bo"] and "5 cells but the header has 4" in r.errors[0].message
    assert not parse_guest_csv(HEADER + "Asha,2\n").errors  # fewer cells are fine: the rest are blank


def test_the_cell_length_limit_is_exact_and_a_diet_listed_twice_counts_once():
    ok = "vegan;" * 66 + "jain"
    assert len(ok) == 400 and not parse_guest_csv(HEADER + f"A,1,yes,{ok}\n").errors
    over = parse_guest_csv(HEADER + f"A,1,yes,{ok};\n")
    assert [e.message for e in over.errors] == ["longer than 400 characters"]
    assert [d.value for d in parse_guest_csv(HEADER + "A,1,yes,vegan;VEGAN; vegan ;jain\n").guests[0].dietary] == ["vegan", "jain"]


def test_a_quoted_name_with_a_comma_survives_even_after_a_space():
    r = parse_guest_csv(HEADER + 'Plain,1,yes,\n "Kumar, Asha",2,yes,\n')
    assert not r.errors and [g.name for g in r.guests] == ["Plain", "Kumar, Asha"]


def test_the_check_against_the_headcount_is_exact_at_the_edges():
    t = parse_guest_csv(HEADER + "A,3,yes,\n").totals
    assert check_against(t, 3).flag == "" and check_against(t, 2).flag.startswith("the list expects 3") and check_against(t, 4).flag.startswith("the list accounts for 3 of the 4")


def test_the_list_is_checked_against_the_stated_headcount_without_changing_it():
    t = parse_guest_csv(GOOD).totals  # expects 9 (yes 5 + pending 4)
    assert check_against(t, 9).flag == ""
    assert "expects 9 people but you planned for 8" in check_against(t, 8).flag and "trim the list" in check_against(t, 8).flag
    under = check_against(t, 12)
    assert under.planning_headcount == 12 and under.expected == 9 and "9 of the 12" in under.flag and "keeps 12" in under.flag
    assert check_against(totals_of(()), 12).flag == ""


# ------------------------------------------------------------------ store


@pytest.fixture
def now():
    box = [1000.0]
    box.append(lambda: box[0])
    return box


@pytest.fixture
def store(tmp_path, now):
    return GuestListStore(tmp_path, clock=now[1])


def test_a_preview_token_is_single_use_and_bound_to_its_plan(store):
    r = parse_guest_csv(GOOD)
    t = store.preview("p1", r)
    with pytest.raises(StalePreview):
        store.commit("p2", t)  # another plan's token
    t = store.preview("p1", r)
    assert len(store.commit("p1", t)) == 4
    with pytest.raises(StalePreview):
        store.commit("p1", t)  # already used


def test_a_newer_preview_replaces_an_older_one(store):
    r = parse_guest_csv(GOOD)
    old, new = store.preview("p1", r), store.preview("p1", r)
    with pytest.raises(StalePreview):
        store.commit("p1", old)
    assert store.commit("p1", new)


def test_a_preview_lives_fifteen_minutes_exactly(store, now):
    r = parse_guest_csv(GOOD)
    last_second = store.preview("p1", r)
    now[0] += 15 * 60 - 1
    assert store.commit("p1", last_second)
    expired = store.preview("p2", r)
    now[0] += 15 * 60
    with pytest.raises(StalePreview):
        store.commit("p2", expired)


def test_a_change_to_the_list_after_a_preview_makes_that_preview_stale(store):
    r = parse_guest_csv(GOOD)
    a = store.preview("p1", r)
    store.delete("p1")  # the list was deleted (or replaced) after the preview was taken
    with pytest.raises(StalePreview):
        store.commit("p1", a)


@pytest.mark.parametrize("token", ["", "nope", None, 5, "../../etc/passwd"])
def test_unknown_tokens_are_stale(store, token):
    with pytest.raises(StalePreview):
        store.commit("p1", token)


def test_a_file_with_row_errors_or_no_rows_gets_no_token(store):
    with pytest.raises(GuestListError):
        store.preview("p1", parse_guest_csv(HEADER + "A,99,yes,\n"))
    with pytest.raises(GuestListError):
        store.preview("p1", parse_guest_csv(HEADER))


def test_preview_memory_is_bounded_and_drops_the_oldest(store, now):
    r = parse_guest_csv(GOOD)
    tokens = []
    for i in range(12):
        now[0] += 1
        tokens.append(store.preview(f"plan{i}", r))
    results = []
    for i, tok in enumerate(tokens):
        try:
            store.commit(f"plan{i}", tok)
            results.append(True)
        except StalePreview:
            results.append(False)
    assert results == [False] * 4 + [True] * 8  # at most eight are held; the four oldest were dropped
    assert all(len(t) >= 32 for t in tokens) and len(set(tokens)) == 12  # unguessable and unique


def test_a_symlink_where_the_list_should_be_is_refused_not_followed(store, tmp_path):
    store.commit("p1", store.preview("p1", parse_guest_csv(GOOD)))
    f = tmp_path / "guests" / "guests-p1.json"
    f.unlink()
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"guests": []}))
    f.symlink_to(elsewhere)
    with pytest.raises(OSError):
        store.load("p1")


def test_the_file_is_private_replaces_the_old_list_and_is_wiped_on_delete(store, tmp_path):
    r = parse_guest_csv(GOOD)
    store.commit("p1", store.preview("p1", r))
    f = tmp_path / "guests" / "guests-p1.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE((tmp_path / "guests").stat().st_mode) == 0o700
    assert [g.name for g in store.load("p1")][0] == "Zubin Marzban"
    store.commit("p1", store.preview("p1", parse_guest_csv(HEADER + "Solo Guest,1,yes,\n")))
    assert [g.name for g in store.load("p1")] == ["Solo Guest"] and not list((tmp_path / "guests").glob(".tmp-*"))
    assert store.delete("p1") and not f.exists() and store.load("p1") is None and store.delete("p1") is False


def test_wipe_overwrites_before_unlinking(tmp_path, monkeypatch):
    from eventmate import retention

    f = tmp_path / "secret.json"
    f.write_text("Zubin Marzban")
    seen = {}
    real = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (seen.setdefault("content", self.read_bytes()), real(self, *a, **k)))
    assert retention.wipe(f) and not f.exists()
    assert seen["content"] == b"\x00" * len("Zubin Marzban")  # what was on disk at the moment of unlinking
    assert retention.wipe(f) is False


def test_a_symlink_is_removed_not_followed(tmp_path):
    from eventmate import retention

    target = tmp_path / "precious"
    target.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert retention.wipe(link) and target.read_text() == "keep" and not link.exists()


def test_plan_ids_are_validated(store):
    for bad in ("../x", "a/b", "", "x" * 65):
        with pytest.raises(ValueError):
            store.delete(bad)


# ------------------------------------------------------------------ service, API, assistant


@pytest.fixture
def chans():
    return {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp")}


@pytest.fixture
def em(tmp_path, chans):
    return EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="guests", channels=chans)


@pytest.fixture
def pid(em):
    return em.plan(EXAMPLE, today=TODAY).plan.plan_id


@pytest.fixture
def client(em):
    return create_app(em, today_fn=lambda: TODAY).test_client()


def hdr(client):
    return {"X-EventMate-CSRF": client.get("/api/session").get_json()["csrf"]}


def post_json(client, url, body):
    return client.post(url, data=json.dumps(body), headers={**hdr(client), "Content-Type": "application/json"})


def upload(client, pid, text, name="guests.csv", extra=None):
    data = {"file": (io.BytesIO(text.encode() if isinstance(text, str) else text), name)}
    data.update(extra or {})
    return client.post(f"/api/plans/{pid}/guests/preview", data=data, headers=hdr(client), content_type="multipart/form-data")


def commit_good(client, pid, text=GOOD):
    token = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": text}).get_json()["preview_token"]
    return post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": token})


def test_preview_returns_rows_errors_and_totals_beside_the_stated_headcount_and_saves_nothing(client, em, pid):
    r = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD})
    body = r.get_json()
    assert r.status_code == 200 and body["preview_token"] and len(body["guests"]) == 4 and body["row_errors"] == []
    assert body["totals"]["heads"] == 10 and body["check"]["planning_headcount"] == 120 and "9 of the 120" in body["check"]["flag"]
    assert not (em.store.root / "guests").exists() and client.get(f"/api/plans/{pid}/guests").get_json() == {"has_list": False}


def test_a_file_with_problems_gets_no_token_and_cannot_be_committed(client, pid):
    r = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": HEADER + "Ok,1,yes,\nBad,0,yes,\n"})
    b = r.get_json()
    assert r.status_code == 200 and b["preview_token"] is None and [(e["line"], e["column"]) for e in b["row_errors"]] == [(3, "party_size")] and len(b["guests"]) == 1
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": "anything"}).status_code == 409


def test_multipart_upload_works_and_the_same_rules_apply(client, pid):
    ok = upload(client, pid, GOOD)
    assert ok.status_code == 200 and ok.get_json()["preview_token"]
    assert upload(client, pid, HEADER + "A,99,yes,\n").get_json()["preview_token"] is None
    assert upload(client, pid, b"\xff\xfe").status_code == 400
    assert upload(client, pid, GOOD, extra={"second": (io.BytesIO(b"x"), "b.csv")}).status_code == 400
    two = client.post(f"/api/plans/{pid}/guests/preview", data={"other": (io.BytesIO(b"x"), "a.csv")}, headers=hdr(client), content_type="multipart/form-data")
    assert two.status_code == 400


def test_oversize_is_413_for_both_ways_of_sending_it(client, pid):
    big = "name\n" + "A" * MAX_BYTES
    assert upload(client, pid, big).status_code == 413
    assert post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": big}).status_code == 413
    huge = "x" * 700_000
    assert upload(client, pid, huge).status_code == 413 and post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": huge}).status_code in (413, 422)


def test_only_the_preview_route_takes_a_larger_body_or_multipart(client, pid):
    assert client.post(f"/api/plans/{pid}/guests/commit", data={"preview_token": "x"}, headers=hdr(client), content_type="multipart/form-data").status_code == 415
    assert client.post(f"/api/plans/{pid}/guests/preview", data="name\nA", headers=hdr(client), content_type="text/plain").status_code == 415
    assert post_json(client, f"/api/plans/{pid}/ledger", {"junk": "x" * 70_000}).status_code == 413  # the 64 KB limit still holds everywhere else


def test_the_guest_routes_need_csrf_and_a_real_plan(client, pid):
    for method, url, body in (("post", f"/api/plans/{pid}/guests/preview", {"csv": GOOD}), ("post", f"/api/plans/{pid}/guests/commit", {"preview_token": "t"}), ("delete", f"/api/plans/{pid}/guests", {})):
        r = getattr(client, method)(url, data=json.dumps(body), headers={"Content-Type": "application/json"})
        assert r.status_code == 403, url
    assert upload(client, "nope", GOOD).status_code == 404
    assert client.get("/api/plans/nope/guests").status_code == 404
    assert client.delete("/api/plans/nope/guests", data="{}", headers={**hdr(client), "Content-Type": "application/json"}).status_code == 404
    assert client.get("/api/plans/..%2Fx/guests").status_code in (400, 404)


def test_commit_saves_returns_counts_and_leaves_the_plan_and_its_headcount_alone(client, em, pid):
    before = em.store.latest(pid)
    r = commit_good(client, pid)
    assert r.status_code == 201 and r.get_json()["totals"]["yes"] == 5 and "Zubin" not in r.get_data(as_text=True)
    after = em.store.latest(pid)
    assert after == before and after.version == 1 and after.guests.planning_headcount == 120 and after.guests.guest_list_id is None  # the plan keeps the stated number
    got = client.get(f"/api/plans/{pid}/guests").get_json()
    assert got["has_list"] and got["totals"]["dietary_counts"] == {"halal": 1, "jain": 1, "vegetarian": 2} and not any(n in json.dumps(got) for n in NAMES)


def test_a_stale_token_is_a_409_and_a_second_commit_with_the_same_token_fails(client, pid):
    token = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD}).get_json()["preview_token"]
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": token}).status_code == 201
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": token}).status_code == 409
    old = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD}).get_json()["preview_token"]
    post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD})  # a newer preview
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": old}).status_code == 409
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": "x", "extra": 1}).status_code == 422


def test_delete_removes_the_list_and_invalidates_waiting_previews(client, em, pid):
    commit_good(client, pid)
    waiting = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD}).get_json()["preview_token"]
    f = em.store.root / "guests" / f"guests-{pid}.json"
    assert f.exists()
    r = client.delete(f"/api/plans/{pid}/guests", data="{}", headers={**hdr(client), "Content-Type": "application/json"})
    assert r.get_json() == {"deleted": True} and not f.exists()
    assert client.get(f"/api/plans/{pid}/guests").get_json() == {"has_list": False}
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": waiting}).status_code == 409
    assert client.delete(f"/api/plans/{pid}/guests", data="{}", headers={**hdr(client), "Content-Type": "application/json"}).get_json() == {"deleted": False}


def test_deleting_the_plan_wipes_the_guest_list_and_its_previews(em, pid):
    em.guest_commit(pid, em.guest_preview(pid, GOOD).preview_token)
    token = em.guest_preview(pid, GOOD).preview_token
    f = em.store.root / "guests" / f"guests-{pid}.json"
    assert f.exists() and any(n.encode() in f.read_bytes() for n in NAMES)
    em.delete_plan(pid)
    assert not f.exists()
    for p in em.store.root.rglob("*"):
        assert not p.is_file() or not any(n.encode() in p.read_bytes() for n in NAMES), p
    with pytest.raises(StalePreview):
        em.guest_lists.commit(pid, token)


def test_nothing_is_ever_sent_to_a_guest(em, pid, chans):
    em.guest_commit(pid, em.guest_preview(pid, GOOD).preview_token)
    em.poll_conversations()
    assert chans["email"].sent == [] and chans["whatsapp"].sent == [] and em.outreach(pid) == []


def test_names_reach_no_model_prompt_log_trace_or_state(em, pid, client):
    em.guest_commit(pid, em.guest_preview(pid, GOOD).preview_token)
    model = Scripted(tool("get_guest_counts"), answer("5 guests said yes.", "get_guest_counts"))
    a = Assistant(em, {"large": model, "small": model})
    for q in ("compare the guests who said yes with the planned headcount", "which guests need halal food, by name?"):
        a.ask(q, pid, LOCAL_ORGANIZER)
    assert model.prompts
    blob = "\n".join(model.prompts) + build_state(em, pid).model_dump_json() + client.get(f"/api/plans/{pid}/state").get_data(as_text=True) + client.get(f"/api/plans/{pid}/guests").get_data(as_text=True)
    scanned = [p for p in list(em.logs.decisions.log_dir.rglob("*")) + list(em.store.root.rglob("*")) if p.is_file() and p.name != f"guests-{pid}.json"]
    assert scanned and any(p.suffix == ".jsonl" for p in scanned)
    for p in scanned:
        blob += p.read_text(errors="ignore")
    assert not [n for n in NAMES if n in blob], "a guest name leaked outside the one private file"


def test_the_assistant_answers_how_many_vegetarian_guests_from_the_counts(em, pid):
    em.guest_commit(pid, em.guest_preview(pid, GOOD).preview_token)
    model = Scripted(tool("get_guest_counts"), answer("2 guests need vegetarian meals.", "get_guest_counts"))
    r = Assistant(em, {"large": model, "small": model}).ask("compare how many vegetarian guests we have against the vegan ones", pid, LOCAL_ORGANIZER)
    assert r.outcome == "answered" and "2" in r.answer
    seen = "\n".join(model.prompts)
    assert '"vegetarian": 2' in seen and "Zubin" not in seen and "Kaveri" not in seen


def test_without_a_list_the_tool_says_so_and_an_invented_count_is_not_accepted(em, pid):
    model = Scripted(tool("get_guest_counts"), answer("7 guests need vegetarian meals.", "get_guest_counts"))
    r = Assistant(em, {"large": model, "small": model}).ask("compare how many vegetarian guests we have against the vegan ones", pid, LOCAL_ORGANIZER)
    assert "No guest list has been imported" in "\n".join(model.prompts)
    assert r.outcome != "answered" and "7 guests need vegetarian" not in r.answer


def test_state_uses_the_saved_list_for_rsvp_counts_and_flags_a_mismatch(em, pid):
    before = build_state(em, pid)
    assert {f.key: f.value for f in before.facts}["rsvp_pending"] == 120
    em.guest_commit(pid, em.guest_preview(pid, GOOD).preview_token)
    s = build_state(em, pid)
    facts = {f.key: f for f in s.facts}
    assert (facts["rsvp_yes"].value, facts["rsvp_no"].value, facts["rsvp_pending"].value) == (5, 1, 4) and facts["rsvp_yes"].source == "user"
    assert "9 of the 120" in facts["guest_list_check"].value and facts["guest_count"].value == 120
    item = next(i for i in s.open_items if i.kind == "pending_rsvps")
    assert item.label == "4 guest(s) on your list have not answered"
    assert not any(n in s.model_dump_json() for n in NAMES)


def test_the_guest_tool_is_visible_to_the_organizer_only():
    from eventmate.assistant.identity import Principal, Role
    from eventmate.assistant.tools import build_tools, visible_tools

    names = lambda p: {t.name for t in visible_tools(build_tools(), p)}  # noqa: E731
    assert "get_guest_counts" in names(LOCAL_ORGANIZER) and "get_guest_counts" not in names(Principal(user_id="g", roles=(Role.GUEST,)))


# ------------------------------------------------------------------ the contract the page reads (Phase R3)

FRONTEND_PREVIEW_KEYS = {"can_commit": bool, "preview_token": (str, type(None)), "row_errors": list, "totals": dict}


def test_the_preview_has_exactly_the_keys_the_page_reads_with_the_right_types(client, pid):  # R3 a
    ok = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD}).get_json()
    for key, kind in FRONTEND_PREVIEW_KEYS.items():
        assert isinstance(ok[key], kind), key
    assert ok["can_commit"] is True and isinstance(ok["preview_token"], str) and ok["row_errors"] == []
    assert isinstance(ok["totals"]["rows"], int) and isinstance(ok["totals"]["heads"], int) and ok["totals"]["rows"] == 4 and ok["totals"]["heads"] == 10
    bad = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": HEADER + "Ok,1,yes,\nBad,0,yes,\n"}).get_json()
    assert bad["can_commit"] is False and bad["preview_token"] is None and [(e["line"], e["column"]) for e in bad["row_errors"]] == [(3, "party_size")]
    assert {"line", "column", "message"} == set(bad["row_errors"][0])
    assert not {"token", "errors"} & set(ok) and "headcount" not in ok["totals"]  # no older alias names are emitted


def test_commit_takes_preview_token_and_answers_saved_true_with_totals(client, pid):  # R3 a
    token = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD}).get_json()["preview_token"]
    r = post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": token})
    assert 200 <= r.status_code < 300 and r.get_json()["saved"] is True and r.get_json()["totals"]["heads"] == 10
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"token": token}).status_code == 422  # the old name is not accepted
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": token}).status_code == 409  # single use, still


def test_the_frontends_exact_request_bodies_pass_backend_validation(client, pid):  # R3 e
    assert post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD}).status_code == 200
    token = post_json(client, f"/api/plans/{pid}/guests/preview", {"csv": GOOD}).get_json()["preview_token"]
    assert post_json(client, f"/api/plans/{pid}/guests/commit", {"preview_token": token}).status_code == 201
    sid = post_json(client, "/api/voice/sessions", {"plan_id": pid}).get_json()["session_id"]
    turn = {"session_id": sid, "turn_id": "turn-0001", "generation": 1, "text": "how many guests said yes", "input_mode": "live"}
    assert post_json(client, "/api/voice/turns", turn).status_code == 200
    assert post_json(client, "/api/voice/turns/turn-0001/cancel", {"session_id": sid, "generation": 1, "playback_chars": 0}).status_code == 200
    assert client.delete(f"/api/voice/sessions/{sid}", headers=hdr(client)).status_code == 200


def test_a_bodyless_delete_removes_the_list_as_the_page_sends_it_and_a_stray_body_is_still_refused(client, pid):
    """The page calls DELETE with no body at all (found by running it in a browser); the backend tests only ever sent {}."""
    commit_good(client, pid)
    h = hdr(client)
    assert client.delete(f"/api/plans/{pid}/guests", data='{"x": 1}', headers={**h, "Content-Type": "application/json"}).status_code == 422
    assert client.get(f"/api/plans/{pid}/guests").get_json()["has_list"] is True
    assert client.delete(f"/api/plans/{pid}/guests", headers=h).get_json() == {"deleted": True}
    assert client.delete(f"/api/plans/{pid}/guests", headers={**h, "Content-Type": "application/json"}).get_json() == {"deleted": False}  # JSON type, no body: also fine
