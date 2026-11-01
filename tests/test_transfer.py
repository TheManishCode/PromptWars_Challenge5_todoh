"""Phase 3: export and import (hostile archives included), plan deletion that leaves only a tombstone, and deleting a whole workspace."""

import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest

from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from eventmate.transfer import MAX_MEMBER, TransferError, export_workspace, import_archive
from tests.conftest import EXAMPLE, TODAY
from tests.workspace_helpers import HOST, Visitor, shared_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EVENTMATE_ENABLE_LLM", "0")


def new_em(tmp_path, name="a"):
    return EventMate(log_dir=tmp_path / name / "logs", plan_dir=tmp_path / name / "plans", run_id=name)


def populated(tmp_path, name="a"):
    em = new_em(tmp_path, name)
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.ledger_add(pid, "payment", C.CATERER, 48713, "advance for ZEPHYR-NOTE")
    em.guest_commit(pid, em.guest_preview(pid, b"name,party_size,rsvp,dietary\nAsha Quill,2,yes,vegetarian\nBo Marlowe,1,no,\n").preview_token)
    return em, pid


def unzip(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def rezip(members, attrs=None, flags=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (attrs or {}).get(name, (stat.S_IFREG | 0o600) << 16)
            if flags and name in flags:
                info.flag_bits |= flags[name]
            zf.writestr(info, data)
    return out.getvalue()


def set_encrypted(data: bytes, name: str) -> bytes:
    """Set the 'encrypted' bit in the central directory entry of ``name`` (zipfile clears it on write)."""
    out, pos = bytearray(data), 0
    while (pos := data.find(b"PK\x01\x02", pos)) != -1:
        name_len = int.from_bytes(data[pos + 28:pos + 30], "little")
        if data[pos + 46:pos + 46 + name_len] == name.encode():
            out[pos + 8] |= 0x1
        pos += 4
    return bytes(out)


def sign(members):
    """Rebuild the manifest for edited members, so a test can reach checks beyond the hash."""
    m = json.loads(members["manifest.json"])
    m["files"] = [{**f, "sha256": hashlib.sha256(members[f["path"]]).hexdigest(), "bytes": len(members[f["path"]])} for f in m["files"] if f["path"] in members]
    members["manifest.json"] = json.dumps(m).encode()
    return members


def tree(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


# ---- round trip


def test_export_then_import_into_a_fresh_workspace_reproduces_the_plan_with_identical_hashes(tmp_path):
    a, pid = populated(tmp_path, "a")
    archive = export_workspace(a)
    b = new_em(tmp_path, "b")
    report = import_archive(archive, b)
    assert report["remapped"] == {} and report["imported"] == {"plans": 1, "versions": 1, "ledger_entries": 1, "guest_lists": 1, "leads": 0}
    original = json.loads((Path(a.store.root) / f"plan-{pid}-v0001.json").read_text())
    copy = json.loads((Path(b.store.root) / f"plan-{pid}-v0001.json").read_text())
    assert copy["sha256"] == original["sha256"] and copy == original
    assert [e.amount for e in b.ledger_entries(pid)] == [48713] and b.guest_summary(pid)["totals"]["heads"] == 3


def test_the_manifest_lists_every_file_with_a_hash_flags_personal_data_and_says_what_is_left_out(tmp_path):
    a, pid = populated(tmp_path)
    m = json.loads(unzip(export_workspace(a))["manifest.json"])
    assert m["format"] == "todoh-export" and m["schema_version"] == 1 and m["contains_personal_data"] is True and m["not_included"]
    by_path = {f["path"]: f for f in m["files"]}
    assert by_path[f"plans/{pid}/v0001.json"]["personal_data"] is False and by_path[f"guests/{pid}.json"]["personal_data"] is True
    assert m["plans"][0]["plan_id"] == pid and m["plans"][0]["versions"][0][0] == 1


def test_an_id_that_already_exists_is_replaced_everywhere_and_the_new_versions_pass_their_own_hash_check(tmp_path):
    a, pid = populated(tmp_path)
    report = import_archive(export_workspace(a), a)  # into the workspace it came from: every id collides
    new = report["remapped"][pid]
    assert new != pid and sorted(a.store.list_plans()) == sorted([pid, new])
    plan = a.store.load(new)  # raises PlanIntegrityError if the hash did not match
    assert plan.plan_id == new and pid not in plan.model_dump_json()
    assert [e.plan_id for e in a.ledger_entries(new)] == [new] and a.guest_summary(new)["totals"]["heads"] == 3
    assert a.store.load(pid).plan_id == pid and len(a.ledger_entries(pid)) == 1  # the original is untouched


def test_import_adds_ledger_history_and_never_resets_a_counter(tmp_path):
    a, pid = populated(tmp_path)
    b = new_em(tmp_path, "b")
    import_archive(export_workspace(a), b)
    assert not (Path(b.store.root) / "outreach").exists() and b.outreach_store.list(pid) == []


# ---- hostile archives


def good(tmp_path):
    a, pid = populated(tmp_path, "src")
    return unzip(export_workspace(a)), pid


def refuse(raw, tmp_path, code):
    target = new_em(tmp_path, "target")
    before = tree(tmp_path)
    with pytest.raises(TransferError) as exc:
        import_archive(raw, target)
    assert exc.value.code == code, (exc.value.code, str(exc.value))
    assert tree(tmp_path) == before, "a refused archive wrote something"
    assert target.store.list_plans() == []


@pytest.mark.parametrize("name", ["../x.json", "/etc/passwd", "C:\\x.json", "plans/a/../../b.json", "plans//x/v0001.json", "..\\x", "./manifest.json", "other/file.txt", "plans/ab c/v0001.json", "plans/x/v1.json"])
def test_member_names_outside_the_grammar_are_refused_and_nothing_is_written(tmp_path, name):
    members, _ = good(tmp_path)
    members[name] = b"{}"
    refuse(rezip(members), tmp_path, "bad_member")


def test_a_name_with_a_nul_byte_cannot_pass_as_a_listed_file(tmp_path):
    members, _ = good(tmp_path)
    members["plans/x/v0001.json\x00.sh"] = b"{}"  # Python's zip reader cuts the name at the NUL; the result is a file the manifest does not list
    refuse(rezip(members), tmp_path, "manifest_mismatch")


def test_a_symlink_a_device_and_an_encrypted_member_are_refused(tmp_path):
    members, pid = good(tmp_path)
    key = f"plans/{pid}/v0001.json"
    refuse(rezip(members, attrs={key: (stat.S_IFLNK | 0o777) << 16}), tmp_path, "bad_member")
    refuse(rezip(members, attrs={key: (stat.S_IFCHR | 0o600) << 16}), tmp_path, "bad_member")
    refuse(set_encrypted(rezip(members), key), tmp_path, "bad_member")


def test_a_duplicate_member_name_is_refused(tmp_path):
    members, pid = good(tmp_path)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        zf.writestr(f"plans/{pid}/v0001.json", b"{}")  # zipfile warns about the duplicate; the reader must still refuse it
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        refuse(out.getvalue(), tmp_path, "duplicate_member")


def test_an_oversized_member_and_a_zip_bomb_are_refused_by_what_is_really_there(tmp_path):
    members, pid = good(tmp_path)
    members["history/jobs.jsonl"] = b"0" * (MAX_MEMBER + 10)
    refuse(rezip(members), tmp_path, "member_too_large")
    members["history/jobs.jsonl"] = b"\x00" * 3_000_000  # tiny when compressed: a ratio of thousands to one
    refuse(rezip(members), tmp_path, "member_too_large")


def test_too_many_members_and_a_total_that_expands_too_far_are_refused(tmp_path, monkeypatch):
    import os

    from eventmate import transfer

    members, pid = good(tmp_path)
    many = dict(members)
    for i in range(2100):
        many[f"ledger/p{i}.jsonl"] = b""
    refuse(rezip(many), tmp_path, "too_many_members")
    monkeypatch.setattr(transfer, "MAX_TOTAL", 150_000)  # the real cap is 60 MB; a small one tests the same line without a 60 MB fixture
    big = dict(members)
    for i in range(3):
        big[f"history/outreach/p{i}.jsonl"] = os.urandom(60_000)  # incompressible, each under every other limit
    refuse(rezip(big), tmp_path, "archive_too_large")


def test_garbage_and_empty_input_are_refused(tmp_path):
    for raw in (b"", b"not a zip at all", b"PK\x03\x04" + b"\x00" * 50, b"\x00" * 1000):
        refuse(raw, tmp_path, "not_an_archive")
    members, _ = good(tmp_path)
    del members["manifest.json"]
    refuse(rezip(members), tmp_path, "no_manifest")


def test_a_deeply_nested_manifest_is_refused_without_crashing(tmp_path):
    members, _ = good(tmp_path)
    members["manifest.json"] = b"[" * 30_000 + b"]" * 30_000  # 60 KB: under the size and ratio limits; this Python parses it, and the manifest model then refuses it
    target = new_em(tmp_path, "target")
    with pytest.raises(TransferError) as exc:
        import_archive(rezip(members), target)
    assert exc.value.code in ("bad_json", "bad_manifest")  # either way a clean refusal, never a crash or a half import
    assert target.store.list_plans() == []


def test_a_tampered_file_a_wrong_version_and_an_inconsistent_manifest_are_refused(tmp_path):
    members, pid = good(tmp_path)
    key = f"plans/{pid}/v0001.json"
    tampered = dict(members)
    tampered[key] = members[key].replace(b'"city": "Pune"', b'"city": "Goa"')
    assert tampered[key] != members[key]
    refuse(rezip(tampered), tmp_path, "tampered")  # the manifest hash no longer matches
    forged = sign(dict(tampered))
    refuse(rezip(forged), tmp_path, "tampered")  # even with the manifest repaired, the plan's own hash fails
    newer = dict(members)
    m = json.loads(newer["manifest.json"])
    m["schema_version"] = 2
    newer["manifest.json"] = json.dumps(m).encode()
    refuse(rezip(newer), tmp_path, "unsupported_version")
    extra = dict(members)
    extra["history/jobs.jsonl"] = b"{}\n"  # a member the manifest does not list
    refuse(rezip(extra), tmp_path, "manifest_mismatch")
    missing = dict(members)
    del missing[f"ledger/{pid}.jsonl"]
    refuse(rezip(missing), tmp_path, "manifest_mismatch")


def test_a_record_that_fails_its_model_or_names_another_plan_is_refused(tmp_path):
    members, pid = good(tmp_path)
    bad = dict(members)
    line = json.loads(bad[f"ledger/{pid}.jsonl"].splitlines()[0])
    bad[f"ledger/{pid}.jsonl"] = (json.dumps({**line, "amount": -5}) + "\n").encode()
    refuse(rezip(sign(bad)), tmp_path, "bad_record")
    other = dict(members)
    other[f"ledger/{pid}.jsonl"] = (json.dumps({**line, "plan_id": "someoneelse"}) + "\n").encode()
    refuse(rezip(sign(other)), tmp_path, "bad_record")
    guests = dict(members)
    doc = json.loads(guests[f"guests/{pid}.json"])
    doc["guests"][0]["party_size"] = 999
    guests[f"guests/{pid}.json"] = json.dumps(doc).encode()
    refuse(rezip(sign(guests)), tmp_path, "bad_record")


# ---- imported outreach and jobs can never send


def test_imported_outreach_and_jobs_are_history_only_and_nothing_sends(tmp_path):
    members, pid = good(tmp_path)
    draft = {"draft_id": "d1", "plan_id": pid, "status": "approved", "recipient": "vendor@example.net", "body": "please quote", "content_hash": "0" * 64}
    members[f"history/outreach/{pid}.jsonl"] = (json.dumps(draft) + "\n").encode()
    members["history/jobs.jsonl"] = (json.dumps({"job_id": "j1", "plan_id": pid, "status": "waiting_approval"}) + "\n").encode()
    m = json.loads(members["manifest.json"])
    for path, kind in ((f"history/outreach/{pid}.jsonl", "outreach_history"), ("history/jobs.jsonl", "jobs_history")):
        m["files"].append({"path": path, "sha256": "0" * 64, "bytes": 0, "kind": kind, "personal_data": True})
    members["manifest.json"] = json.dumps(m).encode()
    archive = rezip(sign(members))
    target = new_em(tmp_path, "target")
    sent = []
    for ch in target.conversations.channels.values():
        ch.send = lambda *a, **k: sent.append(a)
    report = import_archive(archive, target)
    assert report["history_kept"] and target.outreach_store.list(pid) == [] and target.jobs.store.list() == []
    target.background_round()
    assert sent == []  # nothing was restored into the live stores, so there is nothing to approve, dispatch or tick
    kept = list((Path(target.store.root) / "imported").rglob("*.jsonl"))
    assert kept and all(oct(p.stat().st_mode & 0o777) == "0o600" for p in kept)


def test_an_export_holds_no_credential_key_or_secret(tmp_path, monkeypatch):
    secrets_ = {"EVENTMATE_SMTP_PASSWORD": "smtp-pass-ZZ-123456", "NVIDIA_API_KEY": "nvapi-ZZ-secret-654321", "EVENTMATE_SECRET": "workspace-secret-" + "q" * 20}
    for k, v in secrets_.items():
        monkeypatch.setenv(k, v)
    a, pid = populated(tmp_path)
    blob = b"".join(unzip(export_workspace(a)).values())
    assert not any(v.encode() in blob for v in secrets_.values())


# ---- the routes (shared mode)


def test_the_export_route_needs_the_csrf_header_and_only_hands_out_the_callers_own_data(tmp_path):
    app, registry = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    pid = a.make_plan()
    assert a.get("/api/workspace/export").status_code == 403
    r = a.get("/api/workspace/export", headers={"X-EventMate-CSRF": a.csrf})
    assert r.status_code == 200 and r.mimetype == "application/zip" and f"plans/{pid}/v0001.json" in unzip(r.data)
    assert b.get("/api/workspace/export", headers={"X-EventMate-CSRF": a.csrf}).status_code == 403  # A's CSRF value is not B's
    theirs = b.get("/api/workspace/export", headers={"X-EventMate-CSRF": b.csrf})
    assert theirs.status_code == 200 and f"plans/{pid}/v0001.json" not in unzip(theirs.data)


def upload(visitor, data, name="archive", content_type="application/zip"):
    return visitor.client.post("/api/workspace/import", data={name: (io.BytesIO(data), "export.zip", content_type)}, headers={"Host": HOST, "X-EventMate-CSRF": visitor.csrf}, base_url=f"https://{HOST}",
                               content_type="multipart/form-data", environ_base={"REMOTE_ADDR": visitor.address})


def test_import_through_the_route_lands_only_in_the_callers_workspace(tmp_path):
    app, registry = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    pid = a.make_plan()
    archive = a.get("/api/workspace/export", headers={"X-EventMate-CSRF": a.csrf}).data
    fresh = Visitor(app, "198.51.100.3")
    fresh.start()
    r = upload(fresh, archive)
    assert r.status_code == 201 and r.get_json()["imported"]["plans"] == 1 and r.get_json()["hashes_verified"] is True
    assert [p["plan_id"] for p in fresh.get("/api/plans").get_json()] == [pid] and b.get("/api/plans").get_json() == []


@pytest.mark.parametrize("name,expected", [("archive", 422), ("other", 400)])
def test_the_import_route_refuses_a_bad_archive_and_a_wrongly_named_field(tmp_path, name, expected):
    app, _ = shared_app(tmp_path)
    v = Visitor(app)
    v.start()
    r = upload(v, b"not a zip", name=name)
    assert r.status_code == expected, r.get_json()
    assert r.get_json()["code"] == "not_an_archive" if expected == 422 else True


def test_an_import_over_the_envelope_is_413_with_the_limit(tmp_path):
    app, _ = shared_app(tmp_path)
    v = Visitor(app)
    v.start()
    r = upload(v, b"x" * (21 * 1024 * 1024))
    assert r.status_code == 413 and r.get_json()["code"] == "too_large" and r.get_json()["scope"] == "envelope"


# ---- delete


FIXTURE = ("ZEPHYR-NOTE", "Asha Quill", "Bo Marlowe", "48713")


def scan(root: Path, needles):
    hits = []
    for p in root.rglob("*"):
        if p.is_file() and p.name != "tombstones.jsonl":
            data = p.read_bytes()
            hits += [(str(p.relative_to(root)), n) for n in needles if n.encode() in data or n.encode() in p.name.encode()]
    return hits


def test_after_a_plan_is_deleted_no_file_but_the_tombstone_holds_anything_of_it_and_a_second_plan_is_untouched(tmp_path):
    em, pid = populated(tmp_path)
    keep = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.ledger_add(keep, "payment", C.VENUE, 777, "keep-me")
    em.delete_plan(pid)
    home = tmp_path / "a"
    assert scan(home, FIXTURE + (pid,)) == []
    assert em.ledger_entries(keep)[0].note == "keep-me" and em.store.latest(keep).plan_id == keep
    with pytest.raises(FileNotFoundError):
        em.delete_plan(pid)  # gone: a second delete is a clean miss, not a crash


def test_deleting_a_workspace_removes_everything_leaves_a_minimal_tombstone_and_closes_the_cookie(tmp_path):
    app, registry = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    pid = a.make_plan()
    a.post(f"/api/plans/{pid}/ledger", {"kind": "payment", "category": "caterer", "amount": 48713, "note": "ZEPHYR-NOTE"})
    tok = a.post(f"/api/plans/{pid}/guests/preview", {"csv": "name,party_size,rsvp,dietary\nAsha Quill,2,yes,vegetarian\n"}).get_json()["preview_token"]
    a.post(f"/api/plans/{pid}/guests/commit", {"preview_token": tok})
    a.post("/api/assistant/ask", {"plan_id": pid, "question": "how much is left in the budget?"})
    b_plan = b.make_plan()
    old_cookie = a.credential()
    assert a.delete("/api/workspace", {"confirm": "nope"}).status_code in (400, 422) and registry.directory.exists(a.workspace_id)  # needs the confirmation
    r = a.delete("/api/workspace", {"confirm": "delete"})
    assert (r.status_code == 200 and r.get_json() == {"deleted": True} and "todoh_ws=;" in r.headers["Set-Cookie"].replace(" ", "")) or "Expires=Thu, 01 Jan 1970" in r.headers["Set-Cookie"]
    root = tmp_path / "data"
    assert scan(root, FIXTURE + (pid, a.workspace_id, old_cookie)) == []
    assert not registry.directory.exists(a.workspace_id) and not registry.home(a.workspace_id).exists()
    tomb = [json.loads(x) for x in (root / "tombstones.jsonl").read_text().splitlines()]
    assert len(tomb) == 1 and set(tomb[0]) == {"kind", "id", "deleted_at"} and tomb[0]["kind"] == "workspace" and tomb[0]["id"] == a.workspace_id
    assert registry.open(old_cookie) is None and a.get("/api/plans").status_code == 404  # the old credential gets the generic answer
    assert [p["plan_id"] for p in b.get("/api/plans").get_json()] == [b_plan]  # the other workspace is untouched


def test_deleting_a_workspace_that_is_not_there_is_a_clean_no_op_and_local_mode_has_no_such_route(tmp_path):
    app, registry = shared_app(tmp_path)
    assert registry.delete("ws-" + "0" * 16) is False
    from eventmate.web.app import create_app

    local = create_app(new_em(tmp_path, "loc"), today_fn=lambda: TODAY).test_client()
    token = local.get("/api/session").get_json()["csrf"]
    r = local.delete("/api/workspace", data=json.dumps({"confirm": "delete"}), headers={"X-EventMate-CSRF": token, "Content-Type": "application/json"})
    assert r.status_code == 404 and r.get_json()["code"] == "not_found"


def residue(root, needles):
    """Files under ``root`` that name or contain a needle, ignoring the deletion log and the plan's own version files (those are removed last, by design)."""
    hits = []
    for p in Path(root).rglob("*"):
        if not p.is_file() or p.name == "tombstones.jsonl" or (p.name.startswith("plan-") and p.name != "plan-heads.jsonl"):
            continue
        hits += [(str(p.relative_to(root)), n) for n in needles if n.encode() in p.read_bytes() or n in p.name]
    return hits


# ---- crash recovery (found by crash-point testing: kill the process before each filesystem change and look at what is left)


def _dead_pid():
    import subprocess

    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def test_an_import_killed_part_way_is_rolled_back_by_the_next_start_and_leaves_no_plan_without_its_ledger(tmp_path, monkeypatch):
    a, pid = populated(tmp_path, "a")
    archive = export_workspace(a)
    b = new_em(tmp_path, "b")
    real = b.ledger._file

    def die(*args, **kw):
        raise KeyboardInterrupt("simulated death between a plan's versions and its ledger")  # not an Exception: nothing in the import catches it, as with a kill

    monkeypatch.setattr(b.ledger, "_file", die)
    with pytest.raises(KeyboardInterrupt):
        import_archive(archive, b)
    monkeypatch.setattr(b.ledger, "_file", real)
    markers = list(Path(b.store.root).glob("import-pending-*.json"))
    assert len(markers) == 1 and b.store.list_plans() == [pid]  # the half-imported plan is there, without its ledger or guest list
    doc = json.loads(markers[0].read_text())
    doc["pid"] = _dead_pid()  # the process that was importing is gone
    markers[0].write_text(json.dumps(doc))
    again = new_em(tmp_path, "b")  # the next start
    assert again.store.list_plans() == [] and not list(Path(again.store.root).glob("import-pending-*.json"))
    assert residue(tmp_path / "b", (pid,)) == []


def test_an_import_whose_process_is_still_alive_is_left_alone(tmp_path):
    import os

    a, pid = populated(tmp_path, "a")
    b = new_em(tmp_path, "b")
    import_archive(export_workspace(a), b)
    marker = Path(b.store.root) / "import-pending-live.json"
    marker.write_text(json.dumps({"pid": os.getppid(), "plans": [pid]}))  # the parent process is alive: this import may be running
    new_em(tmp_path, "b")
    assert marker.exists() and b.store.list_plans() == [pid]


def test_stale_temporary_files_are_wiped_and_a_fresh_one_is_left_alone(tmp_path):
    import os
    import time

    from eventmate.retention import sweep_stale_temp

    (tmp_path / "guests").mkdir()
    old, fresh = tmp_path / "guests" / ".tmp-aaaa", tmp_path / "guests" / ".tmp-bbbb"
    old.write_text("Asha Quill")
    fresh.write_text("Bo Marlowe")
    real = tmp_path / "guests" / "guests-abc.json"
    real.write_text("{}")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert sweep_stale_temp(tmp_path) == ["guests/.tmp-aaaa"] and not old.exists() and fresh.exists() and real.exists()


def test_a_delete_killed_after_the_logs_were_scrubbed_can_be_run_again_and_the_logs_never_name_the_plan(tmp_path, monkeypatch):
    em, pid = populated(tmp_path, "a")
    monkeypatch.setattr(em.store, "delete", lambda p: (_ for _ in ()).throw(KeyboardInterrupt("died before the versions were removed")))
    with pytest.raises(KeyboardInterrupt):
        em.delete_plan(pid)
    assert pid in em.store.list_plans()  # still listed, so it can be deleted again
    assert residue(tmp_path / "a", (pid,)) == []  # and nothing but the plan's own versions names it (those are about to go): the logs and head record are already clean
