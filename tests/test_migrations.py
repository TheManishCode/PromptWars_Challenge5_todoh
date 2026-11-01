"""v6 1.2: schema versions with upgraders. Data in the formats written before versions existed (tests/fixtures/v1) keeps loading; a later version is
upgraded in memory with the bytes on disk untouched; a version from the future is refused by name; a plan's stored hash still checks."""

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from eventmate import migrations
from eventmate.followthrough.jobs import JobStore
from eventmate.messaging.outreach import OutreachStore
from eventmate.plan_store import PlanIntegrityError, PlanStore
from eventmate.pricing.ledger import Ledger

FIXTURES = Path(__file__).parent / "fixtures" / "v1"
PID = (FIXTURES / "PLAN_ID.txt").read_text().strip()


@pytest.fixture
def store(tmp_path):
    """A store directory laid out as the application lays it out, filled with the committed v1 files."""
    root = tmp_path / "plans"
    (root / "ledger").mkdir(parents=True)
    (root / "outreach").mkdir()
    shutil.copy(FIXTURES / "plan-v1.json", root / f"plan-{PID}-v0001.json")
    (root / f"plan-{PID}-v0001.json").chmod(0o444)
    shutil.copy(FIXTURES / "ledger-v1.jsonl", root / "ledger" / f"ledger-{PID}.jsonl")
    shutil.copy(FIXTURES / "outreach-v1.jsonl", root / "outreach" / f"outreach-{PID}.jsonl")
    jobs = JobStore(root)
    with sqlite3.connect(jobs.path) as db:
        for line in (FIXTURES / "jobs-v1.jsonl").read_text().splitlines():
            row = json.loads(line)
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (row["job_id"], row["status"], None, row["version"], line))
    return root


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_committed_v1_fixtures_have_no_schema_version_so_they_stand_for_old_files():
    for name in ("ledger-v1.jsonl", "outreach-v1.jsonl", "jobs-v1.jsonl"):
        assert all("schema_version" not in json.loads(x) for x in (FIXTURES / name).read_text().splitlines()), name
    assert json.loads((FIXTURES / "plan-v1.json").read_text())["schema_version"] == 1


def test_every_v1_fixture_loads_through_the_real_stores(store):
    plan = PlanStore(store).load(PID)
    assert plan.plan_id == PID and plan.version == 1
    assert [e.amount for e in Ledger(store).entries(PID)] == [5000, 60000]
    drafts = OutreachStore(store, {}).list(PID)
    assert len(drafts) == 3 and "sales@annapurna.in" in {d.recipient for d in drafts}  # one written by hand and two by the job
    jobs = JobStore(store).list(PID)
    assert len(jobs) == 1 and jobs[0].goal == "two comparable quotes"


def test_new_records_carry_the_schema_version_first_and_old_ones_still_read_beside_them(store):
    from eventmate.schemas import VendorCategory as C
    from eventmate.pricing.ledger import Ledger as L

    ledger = L(store)
    ledger.add(PlanStore(store).load(PID), "payment", C.CATERER, 777, "new")
    lines = (store / "ledger" / f"ledger-{PID}.jsonl").read_text().splitlines()
    assert "schema_version" not in json.loads(lines[0]) and list(json.loads(lines[-1]))[0] == "schema_version" and json.loads(lines[-1])["schema_version"] == 1
    assert [e.amount for e in ledger.entries(PID)] == [5000, 60000, 777]


def test_a_later_version_is_upgraded_in_memory_and_the_bytes_on_disk_do_not_change(store, monkeypatch):
    path = store / "ledger" / f"ledger-{PID}.jsonl"
    before = digest(path)
    monkeypatch.setitem(migrations.CURRENT, "ledger", 2)
    monkeypatch.setitem(migrations.UPGRADERS, ("ledger", 1), lambda d: {**d, "note": d["note"].upper()})  # a fake v2: the note is upper case
    assert [e.note for e in Ledger(store).entries(PID)] == ["ADVANCE", "HALL QUOTE"]
    assert digest(path) == before  # nothing was rewritten
    from eventmate.schemas import VendorCategory as C

    Ledger(store).add(PlanStore(store).load(PID), "payment", C.CATERER, 1, "later")
    last = json.loads(path.read_text().splitlines()[-1])
    assert last["schema_version"] == 2  # records written now say which version wrote them
    assert [e.note for e in Ledger(store).entries(PID)] == ["ADVANCE", "HALL QUOTE", "later"]  # a v2 record is not upgraded again


def test_a_chain_of_upgraders_runs_in_order(monkeypatch):
    monkeypatch.setitem(migrations.CURRENT, "ledger", 3)
    monkeypatch.setitem(migrations.UPGRADERS, ("ledger", 1), lambda d: {**d, "trail": d.get("trail", "") + "1>2;"})
    monkeypatch.setitem(migrations.UPGRADERS, ("ledger", 2), lambda d: {**d, "trail": d.get("trail", "") + "2>3;"})
    assert migrations.upgrade("ledger", {"x": 1})["trail"] == "1>2;2>3;"
    assert migrations.upgrade("ledger", {"x": 1, "schema_version": 2})["trail"] == "2>3;"


def test_a_missing_step_is_an_error_not_a_guess(monkeypatch):
    monkeypatch.setitem(migrations.CURRENT, "ledger", 3)
    monkeypatch.setitem(migrations.UPGRADERS, ("ledger", 1), lambda d: d)  # nothing from 2 to 3
    with pytest.raises(migrations.StoreCorrupt, match="no upgrade from schema 2"):
        migrations.upgrade("ledger", {}, source="ledger-x.jsonl", line=4)


def test_a_record_from_the_future_is_refused_naming_the_file_the_line_and_both_versions(store):
    path = store / "ledger" / f"ledger-{PID}.jsonl"
    lines = path.read_text().splitlines()
    lines[1] = json.dumps({"schema_version": 99, **json.loads(lines[1])})
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(migrations.SchemaTooNew) as exc:
        Ledger(store).entries(PID)
    message = str(exc.value)
    assert f"ledger-{PID}.jsonl line 2" in message and "schema 99" in message and "reads up to 1" in message and "upgrade the application" in message


@pytest.mark.parametrize("bad", ["0", "-1", '"2"', "1.5", "true", "null"])
def test_a_schema_version_that_is_not_a_positive_whole_number_is_refused(bad):
    with pytest.raises(migrations.StoreCorrupt, match="positive whole number"):
        migrations.parse_line("ledger", '{"schema_version": %s}' % bad, "ledger-x.jsonl", 1)


def test_a_torn_or_damaged_line_is_named_without_its_content(store):
    path = store / "ledger" / f"ledger-{PID}.jsonl"
    path.write_text(path.read_text() + '{"amount": 5000, "category": "cat')  # a crash in the middle of an append
    with pytest.raises(migrations.StoreCorrupt) as exc:
        Ledger(store).entries(PID)
    assert f"ledger-{PID}.jsonl line 3" in str(exc.value) and "torn or damaged" in str(exc.value) and "caterer" not in str(exc.value)


def test_a_valid_json_line_that_is_not_a_record_names_the_line_through_the_models_own_error(store):
    path = store / "ledger" / f"ledger-{PID}.jsonl"
    path.write_text(path.read_text() + json.dumps({"amount": -1}) + "\n")
    with pytest.raises(Exception) as exc:
        Ledger(store).entries(PID)
    assert any(f"ledger-{PID}.jsonl line 3" in n for n in getattr(exc.value, "__notes__", []))


def test_a_plans_stored_hash_covers_the_stored_schema_and_still_checks_after_an_upgrade(store, monkeypatch):
    path = store / f"plan-{PID}-v0001.json"
    before = digest(path)
    monkeypatch.setitem(migrations.CURRENT, "plan_version", 2)
    monkeypatch.setitem(migrations.UPGRADERS, ("plan_version", 1), lambda d: d)  # a fake v2 that changes nothing a v1 plan has
    plan = PlanStore(store).load(PID)  # the hash is checked against the stored (v1) body, then the body is upgraded
    assert plan.plan_id == PID and digest(path) == before
    doc = json.loads(path.read_text())
    doc["plan"]["request"]["city"] = "Goa"  # tampering still fails, upgrade or not
    path.chmod(0o644)
    path.write_text(json.dumps(doc))
    with pytest.raises(PlanIntegrityError, match="integrity hash"):
        PlanStore(store).load(PID)


def test_a_new_plan_version_is_written_at_the_current_schema_and_the_old_one_still_loads(store, monkeypatch):

    monkeypatch.setitem(migrations.CURRENT, "plan_version", 2)
    monkeypatch.setitem(migrations.UPGRADERS, ("plan_version", 1), lambda d: d)
    ps = PlanStore(store)
    first = ps.load(PID)
    second = first.model_copy(update={"version": 2, "parent_version": 1})
    ps.save(second)
    assert json.loads((store / f"plan-{PID}-v0002.json").read_text())["schema_version"] == 2
    assert json.loads((store / f"plan-{PID}-v0001.json").read_text())["schema_version"] == 1
    assert [p.version for p in ps.versions(PID)] == [1, 2]


def test_a_plan_from_the_future_is_refused_by_name(store):
    path = store / f"plan-{PID}-v0001.json"
    doc = json.loads(path.read_text())
    doc["schema_version"] = 7
    path.chmod(0o644)
    path.write_text(json.dumps(doc))
    with pytest.raises(PlanIntegrityError) as exc:
        PlanStore(store).load(PID)
    assert f"plan-{PID}-v0001.json" in str(exc.value) and "plan schema 7" in str(exc.value) and "upgrade the application" in str(exc.value)


def test_a_job_row_from_the_future_is_refused_and_an_old_row_reads(store):
    jobs = JobStore(store)
    assert jobs.list(PID)
    with sqlite3.connect(jobs.path) as db:
        job_id, data = db.execute("SELECT job_id, data FROM jobs").fetchone()
        db.execute("UPDATE jobs SET data=? WHERE job_id=?", (json.dumps({"schema_version": 3, **json.loads(data)}), job_id))
    with pytest.raises(migrations.SchemaTooNew, match="schema 3"):
        JobStore(store).list(PID)
