"""Quote documents (L7): the sandbox holds against hostile files and the reader keeps only amounts that are written in the document."""

import json
import os
import sys
import time

import pytest

from eventmate.documents import quotes, sandbox
from eventmate.documents.sandbox import DocumentError
from eventmate.evidence.ledger import FactState
from eventmate.service import EventMate

TABLE = """# Quotation
| Item | Qty | Rate | Amount |
| --- | --- | --- | --- |
| Veg buffet | 120 | ₹450 | ₹54,000 |
| Stage décor | 1 | | Rs. 1,20,000/- |
| Sub total | | | 1,74,000 |
| GST 18% | | | 31,320 |
| Grand total | | | 2,05,320 |
"""


def read(text, name="q.txt"):
    return quotes.extract(sandbox.convert(text.encode(), name).markdown)


def test_a_markdown_table_becomes_lines_with_the_totals_set_apart():
    e = quotes.extract(TABLE)
    assert [(ln.label, ln.quantity, ln.unit_inr, ln.total_inr, ln.basis) for ln in e.lines] == [("Veg buffet", 120, 450, 54_000, "per_unit"), ("Stage décor", 1, None, 120_000, "total")]
    assert e.summary == {"subtotal": 174_000, "tax": 31_320, "grand_total": 205_320} and e.warnings == []


def test_arithmetic_that_does_not_add_up_is_flagged_and_never_corrected():
    e = quotes.extract("| Item | Qty | Rate | Total |\n|---|---|---|---|\n| Buffet | 100 | 450 | 40,000 |\n| Tent | 1 | | 10,000 |\n| Grand total | | | 99,000 |\n")
    assert e.lines[0].total_inr == 40_000 and "45,000" in e.lines[0].warnings[0]
    assert "50,000" in e.warnings[0] and "99,000" in e.warnings[0]


def test_a_total_worked_out_from_quantity_and_rate_says_the_vendor_did_not_write_it():
    ln = quotes.extract("| Item | Qty | Rate |\n|---|---|---|\n| Buffet | 10 | 450 |\n").lines[0]
    assert ln.total_inr == 4_500 and "did not write" in ln.warnings[0]


def test_a_price_with_no_quantity_does_not_claim_what_it_covers():
    ln = quotes.extract("| Item | Price |\n|---|---|\n| Photography | 40,000 |\n").lines[0]
    assert ln.basis == "unclear" and ln.warnings


@pytest.mark.parametrize("cell,expected", [("₹2,00,000", 200_000), ("Rs. 450/-", 450), ("1.5 lakh", 150_000), ("INR 90k", 90_000), ("450", 450), ("", None), ("450 or 500", None), ("0", None),
                                           ("1,000 crore", None), ("TBD", None)])
def test_an_amount_is_one_written_number(cell, expected):
    assert quotes.read_amount(cell) == expected


def test_prose_lines_are_read_when_there_is_no_table():
    e = read("Catering: Rs 450 per plate\nPhotographer - 40,000\nTotal: 2,00,000\nthanks, regards")
    assert [(ln.label, ln.unit_inr, ln.total_inr, ln.basis) for ln in e.lines] == [("Catering", 450, None, "per_unit"), ("Photographer", None, 40_000, "total")]
    assert e.summary == {"grand_total": 200_000}


def test_nothing_found_says_so():
    assert read("hello, see you soon").warnings == ["no priced lines were found in that document"]


def test_csv_and_html_quotes_are_read_by_the_built_in_engines():
    csv_text = "Item,Qty,Rate,Total\nBuffet,50,400,20000\n"
    assert read(csv_text, "q.csv").lines[0].total_inr == 20_000
    html = "<html><script>alert(1)</script><h1>Quote</h1><table><tr><th>Item</th><th>Qty</th><th>Rate</th></tr><tr><td>Buffet</td><td>50</td><td>Rs 400</td></tr></table></html>"
    c = sandbox.convert(html.encode(), "q.html")
    assert c.engine == "html" and "alert" not in c.markdown and read(html, "q.html").lines[0].total_inr == 20_000


@pytest.mark.parametrize("data,name,why", [(b"x", "q.exe", "send the quote"), (b"", "q.txt", "empty"), (b"%PDF-" + b"0" * 10, "q.docx", "real docx"), (b"hello", "q.pdf", "real pdf"),
                                           (b"a" * (sandbox.MAX_FILE_BYTES + 1), "q.txt", "larger"), (b"x", "noext", "send the quote")])
def test_a_file_that_should_not_be_opened_is_refused_before_it_is(data, name, why):
    with pytest.raises(DocumentError, match=why):
        sandbox.convert(data, name)


def test_a_binary_file_named_text_is_not_read():
    with pytest.raises(DocumentError, match="not a text file"):
        sandbox.convert(b"abc\x00def", "q.txt")


def test_a_pdf_without_docling_says_what_is_needed():
    try:
        import docling  # noqa: F401
        pytest.skip("docling is installed here")
    except ImportError:
        pass
    with pytest.raises(DocumentError, match="Docling"):
        sandbox.convert(b"%PDF-1.4 x", "q.pdf")


def run(code, **kw):
    return sandbox.convert(b"x", "q.txt", command=[sys.executable, "-c", code, "--"], **kw)  # the two trailing arguments (path, extension) land in sys.argv


def test_a_worker_that_never_finishes_is_killed_with_its_children(tmp_path, monkeypatch):
    from eventmate.documents import isolation

    monkeypatch.setattr(isolation, "available", lambda *a, **k: "none")  # this one watches a pid file in tmp_path from outside; the contained level has its own test below
    pidfile = tmp_path / "child.pid"
    started = time.perf_counter()
    with pytest.raises(DocumentError, match="too long"):
        run(f"import subprocess,time\nc = subprocess.Popen(['sleep','60'])\nopen({str(pidfile)!r}, 'w').write(str(c.pid))\ntime.sleep(60)", timeout=1.5)
    assert time.perf_counter() - started < 10
    child = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(child, 9)
        pytest.fail("the worker's child outlived the timeout")


def test_a_worker_that_uses_too_much_memory_is_stopped():
    with pytest.raises(DocumentError):
        run("x = bytearray(900 * 1024 * 1024)\nprint('{\"ok\": true, \"markdown\": \"hi\"}')", memory=256 * 1024 * 1024)


def test_a_worker_that_writes_without_end_is_stopped_by_the_size_cap_not_the_clock():
    started = time.perf_counter()
    with pytest.raises(DocumentError, match="could not be read"):
        run("import sys\nwhile True: sys.stdout.write('a' * 100000); sys.stdout.flush()", timeout=20)
    assert time.perf_counter() - started < 10


def test_a_worker_that_answers_with_nonsense_is_not_believed():
    for code in ("print('not json')", "print('[1, 2]')", "print('{\"ok\": true, \"markdown\": 5}')", "raise SystemExit(3)"):
        with pytest.raises(DocumentError, match="could not be read"):
            run(code)


def test_a_worker_sees_no_secrets_and_only_its_own_directory(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-secret")
    monkeypatch.setenv("EVENTMATE_ENV_FILE", "/home/someone/secret.env")
    c = run("import os, json\nprint(json.dumps({'ok': True, 'engine': 'x', 'markdown': json.dumps(sorted(os.environ)) + os.getcwd()}))")
    assert "KEY" not in c.markdown and "EVENTMATE" not in c.markdown and "eventmate-doc-" in c.markdown


def test_the_temporary_directory_is_gone_afterwards(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.tempfile, "tempdir", str(tmp_path))
    sandbox.convert(b"Catering: Rs 450 per plate", "q.txt")
    with pytest.raises(DocumentError):
        run("raise SystemExit(1)")
    assert list(tmp_path.iterdir()) == []


def test_a_long_document_is_cut_and_says_so(monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_MARKDOWN_CHARS", 1000)
    c = sandbox.convert(("Catering: Rs 450 per plate\n" * 200).encode(), "q.txt")
    assert c.truncated and len(c.markdown) == 1000


def test_reading_a_quote_notes_each_line_as_unreviewed_and_keeps_no_file(tmp_path):
    from tests.conftest import TODAY  # noqa: F401

    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="doc")
    vendor = next(iter(em.vendor_directory()))
    out = em.read_quote_document(vendor, "q.csv", b"Item,Qty,Rate,Total\nBuffet,50,400,20000\n")
    assert out["lines"][0]["total_inr"] == 20_000 and out["engine"] == "csv"
    rec = em.evidence.records(vendor)
    assert len(rec) == 1 and rec[0].state is FactState.EXTRACTED and rec[0].source == f"doc:{out['document']}" and em.evidence.usable_for_hard_constraint(vendor, rec[0].field) is None
    assert not any(p.name.endswith((".csv", ".txt")) for p in (tmp_path / "p").rglob("*"))
    with pytest.raises(ValueError, match="unknown vendor"):
        em.read_quote_document("nobody", "q.csv", b"x")
    assert json.dumps(out)


# ---------------------------------------------------------------- the upload route


def upload(c, vendor, data, name="q.csv", field="file"):
    import io

    return c.post(f"/api/vendors/{vendor}/quote-document", data={field: (io.BytesIO(data), name)}, content_type="multipart/form-data", headers={"X-EventMate-CSRF": c.csrf})


@pytest.fixture
def web(tmp_path):
    from datetime import date

    from eventmate.web.app import create_app

    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="docweb")
    app = create_app(em, today_fn=lambda: date(2026, 10, 30), poll_seconds=0)
    c = app.test_client()
    c.csrf = c.get("/api/session").get_json()["csrf"]
    return c, next(iter(em.vendor_directory()))


def test_the_route_reads_a_quote_and_refuses_what_it_should(web):
    c, vendor = web
    ok = upload(c, vendor, b"Item,Qty,Rate,Total\nBuffet,50,400,20000\n")
    assert ok.status_code == 200 and ok.get_json()["lines"][0]["total_inr"] == 20_000
    for resp, status, code in ((upload(c, vendor, b"x", "q.exe"), 422, "bad_document"), (upload(c, vendor, b"x", field="other"), 400, None),
                               (upload(c, "nobody", b"Item,Total\nA,5\n"), 400, None)):
        assert resp.status_code == status and (code is None or resp.get_json().get("code") == code)


def test_the_route_wants_the_csrf_token_and_a_size_it_can_check(web):
    c, vendor = web
    import io

    no_token = c.post(f"/api/vendors/{vendor}/quote-document", data={"file": (io.BytesIO(b"x"), "q.csv")}, content_type="multipart/form-data")
    assert no_token.status_code == 403
    too_big = upload(c, vendor, b"a" * (6 * 1024 * 1024))
    assert too_big.status_code == 413


# ---------------------------------------------------------------- confirming a line


QUOTE = b"Item,Qty,Rate,Total\nVeg buffet,120,450,54000\nPhotos,1,,\nStage,1,,120000\nGrand total,,,174000\n"


def read_one(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="conf")
    vendor = next(v for v, d in em.vendor_directory().items() if d["origin"] == "seed")
    return em, vendor, em.read_quote_document(vendor, "q.csv", QUOTE)


def test_confirming_a_line_records_a_quote_the_user_vouches_for(tmp_path):
    em, vendor, doc = read_one(tmp_path)
    obs = em.confirm_quote_line(vendor, doc["document"], "1", 14)
    assert obs.amount_inr == 54_000 and obs.basis == "total" and obs.source_type.value == "vendor_quote" and obs.evidence_ref == f"doc:{doc['document']} 1" and "14 days" in obs.note
    assert (obs.expires_at - obs.observed_at.date()).days == 14
    states = [r.state for r in em.evidence.records(vendor, f"quote.{doc['document']}.l1")]
    assert states == [FactState.EXTRACTED, FactState.USER_CONFIRMED]


def test_the_vendors_grand_total_can_be_confirmed_too(tmp_path):
    em, vendor, doc = read_one(tmp_path)
    assert em.confirm_quote_line(vendor, doc["document"], "grand_total", 30).amount_inr == 174_000


@pytest.mark.parametrize("line,days,why", [("9", 14, "no such"), ("0", 14, "no such"), ("../x", 14, "no such"), ("1", 0, "1-90"), ("1", 91, "1-90"), ("1", True, "1-90")])
def test_a_line_that_cannot_be_trusted_is_refused(tmp_path, line, days, why):
    em, vendor, doc = read_one(tmp_path)
    with pytest.raises(ValueError, match=why):
        em.confirm_quote_line(vendor, doc["document"], line, days)
    assert em.observations.quotes(vendor) == []


def test_a_document_that_was_never_read_or_belongs_to_another_vendor_cannot_be_confirmed(tmp_path):
    em, vendor, doc = read_one(tmp_path)
    other = next(v for v, d in em.vendor_directory().items() if v != vendor and d["origin"] == "seed")
    for v, d in ((vendor, "0123456789"), (other, doc["document"]), (vendor, "zz")):
        with pytest.raises(ValueError, match="no such quote line"):
            em.confirm_quote_line(v, d, "1", 14)


def test_the_route_confirms_with_the_csrf_token_and_never_takes_an_amount(web):
    c, vendor = web
    doc = upload(c, vendor, QUOTE).get_json()["document"]
    url = f"/api/vendors/{vendor}/quote-document/{doc}/lines/1/confirm"

    def post(body, token=True):
        return c.post(url, data=json.dumps(body), headers={"Content-Type": "application/json", **({"X-EventMate-CSRF": c.csrf} if token else {})})

    assert post({"valid_days": 7}, token=False).status_code == 403
    assert post({"valid_days": 7, "amount_inr": 1}).status_code == 422  # the amount comes from what was read, not from the caller
    ok = post({"valid_days": 7})
    assert ok.status_code == 201 and ok.get_json()["observation"]["amount_inr"] == 54_000


def test_a_price_that_does_not_say_what_it_covers_cannot_be_confirmed_as_one(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="conf2")
    vendor = next(v for v, d in em.vendor_directory().items() if d["origin"] == "seed")
    doc = em.read_quote_document(vendor, "q.csv", b"Item,Price\nPhotography,40000\n")
    assert doc["lines"][0]["basis"] == "unclear"
    with pytest.raises(ValueError, match="what this price covers"):
        em.confirm_quote_line(vendor, doc["document"], "1", 14)
    assert em.observations.quotes(vendor) == []


def test_a_fact_from_another_source_with_the_same_field_name_cannot_be_confirmed_as_a_quote(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="conf3")
    vendor = next(v for v, d in em.vendor_directory().items() if d["origin"] == "seed")
    em.evidence.record(vendor, "quote.0123456789.l1", {"label": "x", "total_inr": 99_999, "basis": "total"}, FactState.EXTRACTED, "web:abc")
    with pytest.raises(ValueError, match="no such quote line"):
        em.confirm_quote_line(vendor, "0123456789", "1", 14)
    assert em.observations.quotes(vendor) == []


# ---------------------------------------------------------------- files already read


def test_the_files_already_read_come_back_with_each_lines_state(tmp_path):
    em, vendor, doc = read_one(tmp_path)
    em.confirm_quote_line(vendor, doc["document"], "1", 14)
    got = em.quote_documents(vendor)
    assert len(got) == 1 and got[0]["document"] == doc["document"] and got[0]["summary"] == {"grand_total": 174_000} and got[0]["grand_total_confirmed"] is False
    assert [(ln["key"], ln["label"], ln["confirmed"]) for ln in got[0]["lines"]] == [("1", "Veg buffet", True), ("2", "Stage", False)]
    em.confirm_quote_line(vendor, doc["document"], "grand_total", 14)
    assert em.quote_documents(vendor)[0]["grand_total_confirmed"] is True


def test_another_vendors_files_and_look_alike_facts_are_not_listed(tmp_path):
    em, vendor, doc = read_one(tmp_path)
    other = next(v for v, d in em.vendor_directory().items() if v != vendor and d["origin"] == "seed")
    em.evidence.record(vendor, "quote.0123456789.l1", {"label": "forged", "total_inr": 1, "basis": "total"}, FactState.EXTRACTED, "web:abc")
    assert em.quote_documents(other) == [] and [d["document"] for d in em.quote_documents(vendor)] == [doc["document"]]


def test_the_route_lists_them(web):
    c, vendor = web
    doc = upload(c, vendor, QUOTE).get_json()["document"]
    got = c.get(f"/api/vendors/{vendor}/quote-documents").get_json()["documents"]
    assert [d["document"] for d in got] == [doc] and got[0]["lines"][0]["total_inr"] == 54_000


# ---------------------------------------------------------------- from a confirmed quote to the plan's price


@pytest.fixture
def planned(tmp_path):
    from tests.conftest import EXAMPLE, TODAY

    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="reprice")
    plan = em.plan(EXAMPLE, today=TODAY).plan
    cat = next(s for s in plan.selections if s.category.value == "caterer")
    doc = em.read_quote_document(cat.vendor_id, "q.csv", b"Item,Qty,Rate,Total\nBuffet,100,500,50000\nDecor,1,,5000\nGrand total,,,55000\n")
    return em, plan, cat, doc["document"], TODAY


def test_confirming_says_what_it_does_to_the_plans_price_and_when_a_line_is_only_part(planned):
    em, plan, cat, doc, today = planned
    em.confirm_quote_line(cat.vendor_id, doc, "1", 30)
    part = em.price_effect(plan.plan_id, cat.vendor_id, doc, "1", today)
    assert part == {"in_plan": True, "plan_cost": cat.cost, "now_costs": 50_000, "price_stale": True, "covers_whole_quote": False, "quote_total": 55_000}
    assert em.price_effect(plan.plan_id, cat.vendor_id, doc, "grand_total", today)["covers_whole_quote"] is True


def test_re_pricing_makes_a_new_version_with_the_confirmed_price_and_says_why(planned):
    em, plan, cat, doc, today = planned
    em.confirm_quote_line(cat.vendor_id, doc, "grand_total", 30)
    out = em.reprice(plan.plan_id, today)
    assert out.status == "replanned" and out.plan.version == plan.version + 1 and out.plan.change_reason == "re-priced from the quotes you confirmed"
    assert next(s for s in out.plan.selections if s.category.value == "caterer").cost == 55_000
    assert not any(r.price_stale for r in em.quote_status(plan.plan_id, today))


def test_there_is_nothing_to_re_price_until_a_quote_is_confirmed(planned):
    em, plan, cat, doc, today = planned
    out = em.reprice(plan.plan_id, today)
    assert out.status == "failed" and out.violations[0].constraint == "nothing_to_reprice" and em.store.latest(plan.plan_id).version == plan.version


def test_the_routes_confirm_with_the_effect_and_re_price(planned):
    from datetime import date

    from eventmate.web.app import create_app

    em, plan, cat, doc, today = planned
    app = create_app(em, today_fn=lambda: date(2026, 10, 30), poll_seconds=0)
    cl = app.test_client()
    token = cl.get("/api/session").get_json()["csrf"]

    def post(url, body):
        return cl.post(url, data=json.dumps(body), headers={"X-EventMate-CSRF": token, "Content-Type": "application/json"})

    assert post(f"/api/plans/{plan.plan_id}/replan", {"kind": "quote_update"}).status_code == 422  # nothing confirmed yet
    done = post(f"/api/vendors/{cat.vendor_id}/quote-document/{doc}/lines/grand_total/confirm", {"valid_days": 30, "plan_id": plan.plan_id})
    assert done.status_code == 201 and done.get_json()["effect"]["now_costs"] == 55_000 and done.get_json()["effect"]["price_stale"] is True
    assert post(f"/api/plans/{plan.plan_id}/replan", {"kind": "quote_update", "new_budget": 5}).status_code == 422  # a payload does not belong to this kind
    ok = post(f"/api/plans/{plan.plan_id}/replan", {"kind": "quote_update"})
    assert ok.status_code == 200 and ok.get_json()["plan"]["version"] == plan.version + 1


def test_a_booking_the_user_confirmed_keeps_its_vendor_and_takes_the_confirmed_price(planned):
    from eventmate.schemas import VendorCategory

    em, plan, cat, doc, today = planned
    em.confirm(plan.plan_id, VendorCategory.CATERER)
    em.confirm_quote_line(cat.vendor_id, doc, "grand_total", 30)
    out = em.reprice(plan.plan_id, today)
    kept = next(s for s in out.plan.selections if s.category.value == "caterer")
    assert out.status == "replanned" and (kept.vendor_id, kept.cost, kept.booking_status.value) == (cat.vendor_id, 55_000, "confirmed")


# ---------------------------------------------------------------- heavy engines: measured memory, longer time


def run_heavy(code, **kw):
    return sandbox.convert(b"%PDF-1.4 x", "q.pdf", command=[sys.executable, "-c", code, "--"], **kw)


def test_a_heavy_engine_is_held_to_the_memory_it_really_uses():
    started = time.perf_counter()
    with pytest.raises(DocumentError, match="too much memory"):
        run_heavy("b = bytearray(400 * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1\nimport time\ntime.sleep(30)", memory=150 * 1024 * 1024)
    assert time.perf_counter() - started < 15


def test_a_heavy_engine_may_map_far_more_address_space_than_it_touches_but_a_built_in_one_may_not():
    code = "import mmap\nx = mmap.mmap(-1, 6 * 1024 ** 3)\nprint('{\"ok\": true, \"engine\": \"x\", \"markdown\": \"fine\"}')"  # six GB mapped, none touched
    assert run_heavy(code).markdown == "fine"  # what torch does at import
    with pytest.raises(DocumentError):
        sandbox.convert(b"x", "q.txt", command=[sys.executable, "-c", code, "--"])


def test_a_heavy_engine_gets_a_longer_clock_and_more_memory_than_a_built_in_one():
    assert sandbox.HEAVY_TIMEOUT_S > sandbox.TIMEOUT_S and sandbox.HEAVY_MEMORY_BYTES > sandbox.MEMORY_BYTES


DOCLING = os.environ.get("EVENTMATE_DOCLING_PYTHON")  # an interpreter with Docling installed, kept apart from this project's environment


@pytest.mark.skipif(not DOCLING, reason="set EVENTMATE_DOCLING_PYTHON to an interpreter that has Docling to run the real engine")
def test_a_real_word_quote_goes_through_docling_in_the_sandbox_into_the_same_lines(tmp_path):
    import subprocess

    make = ("import docx,sys\nd=docx.Document()\nd.add_paragraph('Quotation')\nt=d.add_table(rows=1,cols=4)\n"
            "for c,x in zip(t.rows[0].cells,['Item','Qty','Rate','Amount']): c.text=x\n"
            "for r in [('Veg buffet','120','Rs 450','Rs 54,000'),('Live counter','1','','Rs. 12,000/-'),('Sub total','','','66,000'),('GST 18%','','','11,880'),('Grand total','','','77,880')]:\n"
            "    cells=t.add_row().cells\n    for c,x in zip(cells,r): c.text=x\nd.save(sys.argv[1])\n")
    path = tmp_path / "quote.docx"
    subprocess.run([DOCLING, "-c", make, str(path)], check=True)
    c = sandbox.convert(path.read_bytes(), "quote.docx", command=[DOCLING, "-m", "eventmate.documents.worker"])
    found = quotes.extract(c.markdown)
    assert c.engine == "docling" and [(ln.label, ln.quantity, ln.unit_inr, ln.total_inr) for ln in found.lines] == [("Veg buffet", 120, 450, 54_000), ("Live counter", 1, None, 12_000)]
    assert found.summary == {"subtotal": 66_000, "tax": 11_880, "grand_total": 77_880} and found.warnings == []


def test_a_heavy_engine_is_told_where_its_models_are_and_to_stay_off_the_network_a_built_in_one_is_not(monkeypatch):
    monkeypatch.setenv("HF_HOME", "/models/hf")
    code = "import os, json\nprint(json.dumps({'ok': True, 'engine': 'x', 'markdown': json.dumps({k: v for k, v in os.environ.items() if k.startswith(('HF_', 'TRANSFORMERS'))})}))"
    heavy = json.loads(run_heavy(code).markdown)
    assert heavy == {"HF_HOME": "/models/hf", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    assert json.loads(sandbox.convert(b"x", "q.txt", command=[sys.executable, "-c", code, "--"]).markdown) == {}


def test_the_model_home_is_hf_home_or_the_real_home_never_the_sandboxs(monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    assert sandbox.model_home() == os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    monkeypatch.setenv("HF_HOME", "/x")
    assert sandbox.model_home() == "/x"


def test_warming_without_docling_says_what_to_install(capsys):
    try:
        import docling  # noqa: F401
        pytest.skip("docling is installed here")
    except ImportError:
        pass
    from eventmate.documents import warm

    assert warm.main() == 1 and "pip install docling" in capsys.readouterr().err


def hand_made_pdf() -> bytes:
    """A one-page PDF with a ruled quote table, written by hand so no PDF library is needed."""
    rows = [("Item", "Qty", "Rate", "Amount"), ("Veg buffet", "120", "Rs 450", "Rs 54,000"), ("Live counter", "1", "", "Rs 12,000"), ("Sub total", "", "", "66,000"), ("GST 18%", "", "", "11,880"),
            ("Grand total", "", "", "77,880")]
    x0, y0, widths, rh = 60, 700, [190, 60, 90, 110], 24
    ops = ["BT /F1 16 Tf 60 750 Td (Quotation - Royal Rasoi) Tj ET"]
    for i in range(len(rows) + 1):
        ops.append(f"{x0} {y0 - i * rh} m {x0 + sum(widths)} {y0 - i * rh} l S")
    x = x0
    for w in widths + [0]:
        ops.append(f"{x} {y0} m {x} {y0 - len(rows) * rh} l S")
        x += w
    for i, row in enumerate(rows):
        x = x0
        for j, cell in enumerate(row):
            ops.append(f"BT /F1 11 Tf {x + 6} {y0 - i * rh - 16} Td ({cell}) Tj ET")
            x += widths[j]
    stream = "\n".join(ops).encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, start)


@pytest.mark.skipif(not DOCLING or not os.path.isdir(os.path.join(sandbox.model_home(), "hub")), reason="needs EVENTMATE_DOCLING_PYTHON and the models (python -m eventmate.documents.warm)")
def test_a_real_pdf_goes_through_docling_offline_in_the_sandbox_into_the_same_lines():
    c = sandbox.convert(hand_made_pdf(), "quote.pdf", command=[DOCLING, "-m", "eventmate.documents.worker"])
    found = quotes.extract(c.markdown)
    assert c.engine == "docling" and [(ln.label, ln.quantity, ln.unit_inr, ln.total_inr) for ln in found.lines] == [("Veg buffet", 120, 450, 54_000), ("Live counter", 1, None, 12_000)]
    assert found.summary == {"subtotal": 66_000, "tax": 11_880, "grand_total": 77_880}
    for bad in (b"%PDF-1.4\n garbage \x00\x01", hand_made_pdf()[:300]):
        with pytest.raises(DocumentError, match="could not be read"):
            sandbox.convert(bad, "q.pdf", command=[DOCLING, "-m", "eventmate.documents.worker"])


def test_the_warm_up_sample_is_a_well_formed_pdf():
    """``warm`` converts this once, with the network, so the OCR weights Docling fetches lazily are in place before the offline sandbox needs them."""
    import re

    from eventmate.documents import warm

    pdf = warm._sample_pdf()
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\n(\d+)\n", pdf).group(1))
    assert pdf[start:start + 4] == b"xref"
    offsets = [int(m) for m in re.findall(rb"^(\d{10}) 00000 n ", pdf[start:], re.M)]
    assert len(offsets) == 5 and all(pdf[o:].startswith(b"%d 0 obj" % i) for i, o in enumerate(offsets, 1))


def _run(plan, code, *args, work=None):
    import subprocess

    r = subprocess.run([*plan.prefix, sys.executable, "-c", code, *args], capture_output=True, text=True, timeout=60, preexec_fn=plan.preexec, cwd=work)
    return (r.stdout.strip() or "STDERR:" + r.stderr.strip()[-300:])


NET_PROBE = "import socket,sys\ntry:\n    s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', int(sys.argv[1]))); print('connected')\nexcept OSError:\n    print('refused')"


@pytest.mark.parametrize("level", ["contained", "confined", "network", "no-sockets"])
def test_each_isolation_level_cuts_the_reader_off_from_the_network(level, tmp_path, monkeypatch):
    """A listener on loopback is reachable from a plain child and not from a child shut in at this level."""
    import socket

    from eventmate.documents import isolation

    usable = {"contained": lambda: isolation._bwrap_works(sys.executable, sandbox.ROOT), "confined": lambda: isolation._landlock_works(sys.executable, sandbox.ROOT) and isolation._seccomp_works(),
              "network": lambda: bool(isolation._unshare_works()), "no-sockets": isolation._seccomp_works}
    if not usable[level]():
        pytest.skip(f"this machine cannot set up the {level} level")
    monkeypatch.setattr(isolation, "available", lambda *a, **k: level)
    plan = isolation.plan(sys.executable, sandbox.ROOT, str(tmp_path), None)
    assert plan.level == level
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = str(srv.getsockname()[1])
        assert _run(isolation.Plan("none"), NET_PROBE, port) == "connected"
        assert _run(plan, NET_PROBE, port, work=tmp_path) == "refused"


def test_the_socket_filter_refuses_socket_and_keeps_local_pairs(tmp_path):
    from eventmate.documents import isolation

    if not isolation._seccomp_works():
        pytest.skip("this machine cannot install a seccomp filter")
    code = "import socket\ntry:\n    socket.socket(socket.AF_UNIX); print('socket allowed')\nexcept PermissionError:\n    a, b = socket.socketpair(); a.send(b'x'); print('denied, pair ok', b.recv(1))"
    assert _run(isolation.Plan("no-sockets", (), isolation._seccomp_no_sockets), code) == "denied, pair ok b'x'"


@pytest.mark.parametrize("level", ["contained", "confined"])
def test_the_levels_that_hide_files_leave_the_reader_only_its_own_directory(level, tmp_path, monkeypatch):
    from eventmate.documents import isolation

    if level == "contained" and not isolation._bwrap_works(sys.executable, sandbox.ROOT) or level == "confined" and not isolation._landlock_works(sys.executable, sandbox.ROOT):
        pytest.skip(f"this machine cannot set up the {level} level")
    monkeypatch.setattr(isolation, "available", lambda *a, **k: level)
    secret = tmp_path / "workspaces" / "other-visitor" / "plan.json"
    secret.parent.mkdir(parents=True)
    secret.write_text("private")
    work = tmp_path / "work"
    work.mkdir()
    (work / "input.pdf").write_text("mine")
    plan = isolation.plan(sys.executable, sandbox.ROOT, str(work), None)
    assert plan.level == level
    code = ("import os,sys\ndef can(p):\n    try:\n        open(p).read(); return True\n    except OSError:\n        return False\n"
            "print(can(sys.argv[1]), can('/etc/passwd'), can(os.path.expanduser('~/.ssh/id_rsa')), can(sys.argv[2]))\nopen(os.path.join(sys.argv[3], 'out'), 'w').write('ok')")
    assert _run(plan, code, str(secret), str(work / "input.pdf"), str(work), work=work) == "False False False True"
    assert (work / "out").read_text() == "ok"  # the reader can write its own directory


def test_a_machine_that_cannot_shut_the_reader_in_can_be_made_to_refuse_to_read_files(monkeypatch):
    from eventmate.documents import isolation

    monkeypatch.setattr(isolation, "available", lambda *a, **k: "none")
    monkeypatch.setenv("EVENTMATE_REQUIRE_NETLESS", "1")
    with pytest.raises(DocumentError, match="cannot shut the file reader in"):
        sandbox.convert(b"x", "q.csv", command=[sys.executable, "-c", "pass"])


def test_a_contained_worker_and_its_children_die_with_the_timeout():
    import subprocess

    from eventmate.documents import isolation

    if not isolation._bwrap_works(sys.executable, sandbox.ROOT):
        pytest.skip("bubblewrap is not usable here")
    marker = "61.7"  # a sleep length nothing else on the machine uses
    with pytest.raises(DocumentError, match="too long"):
        run(f"import subprocess,time\nsubprocess.Popen(['sleep','{marker}'])\ntime.sleep(60)", timeout=1.5)
    for _ in range(50):
        if subprocess.run(["pgrep", "-f", f"sleep {marker}"], capture_output=True).returncode != 0:
            break
        time.sleep(0.1)
    else:
        subprocess.run(["pkill", "-f", f"sleep {marker}"])
        pytest.fail("a child of the contained reader outlived the timeout")


def test_the_confined_reader_cannot_read_another_processes_proc_entry_whatever_ptrace_scope_says(tmp_path):
    """Landlock, not the kernel's ptrace rules, keeps the reader out of the server's /proc entry. /proc/<pid>/cmdline is readable by any user, so only the reader's own rules can refuse it."""
    import subprocess

    from eventmate.documents import isolation

    if not isolation._landlock_works(sys.executable, sandbox.ROOT):
        pytest.skip("Landlock is not usable here")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "another-process-marker"])
    try:
        dirs, files = isolation._confinement_paths(sys.executable, sandbox.ROOT, None)
        plan = isolation.Plan("confined", (), isolation._landlock_enter(dirs, files, str(tmp_path), (sandbox.ROOT,)))
        code = ("import os,sys\ndef can(p):\n    try:\n        open(p,'rb').read(); return True\n    except OSError:\n        return False\n"
                "print(can('/proc/%s/cmdline' % sys.argv[1]), can('/proc/%s/status' % sys.argv[1]), can('/proc/self/status'), can('/proc/cpuinfo'))")
        assert _run(plan, code, str(other.pid), work=tmp_path) == "False False True True"
        assert subprocess.run([sys.executable, "-c", code, str(other.pid)], capture_output=True, text=True).stdout.strip() == "True True True True"  # without the rules it is readable
    finally:
        other.kill()
        other.wait()


@pytest.mark.parametrize("level", ["contained", "confined", "no-sockets"])
def test_a_process_the_reader_starts_is_shut_in_the_same_way(level, tmp_path, monkeypatch):
    """The restrictions are inherited by every child: a grandchild started by the reader cannot reach the network or the server's files either."""
    import socket

    from eventmate.documents import isolation

    usable = {"contained": lambda: isolation._bwrap_works(sys.executable, sandbox.ROOT), "confined": lambda: isolation._landlock_works(sys.executable, sandbox.ROOT) and isolation._seccomp_works(),
              "no-sockets": isolation._seccomp_works}
    if not usable[level]():
        pytest.skip(f"this machine cannot set up the {level} level")
    monkeypatch.setattr(isolation, "available", lambda *a, **k: level)
    secret = tmp_path / "outside" / "plan.json"
    secret.parent.mkdir()
    secret.write_text("private")
    work = tmp_path / "work"
    work.mkdir()
    plan = isolation.plan(sys.executable, sandbox.ROOT, str(work), None)
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]
        grandchild = (f"import socket\ntry:\n    s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1',{port})); n='connected'\nexcept OSError:\n    n='refused'\n"
                      f"try:\n    open({str(secret)!r}).read(); f='read'\nexcept OSError:\n    f='denied'\nprint(n, f)")
        child = f"import subprocess,sys\nprint(subprocess.run([sys.executable,'-c',{grandchild!r}],capture_output=True,text=True).stdout.strip())"  # a grandchild, through a shell as well
        out = _run(plan, child, work=work)
    assert out.split()[0] == "refused"  # the network is cut for the grandchild
    if level != "no-sockets":
        assert out == "refused denied"  # and, where files are hidden, so are they


@pytest.mark.parametrize("how", ["restriction fails to apply", "wrapper cannot start", "wrapper exits at once"])
def test_when_the_restrictions_fail_at_read_time_the_file_is_not_read(how, tmp_path, monkeypatch):
    """The probe passed at start-up, but at read time the setup breaks: the reader must not run, and the person is told, not given a weaker reader."""
    from eventmate.documents import isolation

    marker = tmp_path / "reader-ran"

    def broken():
        raise OSError(1, "could not install the restriction")

    plans = {"restriction fails to apply": isolation.Plan("confined", (), broken), "wrapper cannot start": isolation.Plan("contained", ("/nonexistent/bwrap",)),
             "wrapper exits at once": isolation.Plan("contained", ("/usr/bin/false",))}
    monkeypatch.setattr(isolation, "plan", lambda *a, **k: plans[how])
    monkeypatch.setenv("EVENTMATE_MODE", "shared")
    with pytest.raises(DocumentError, match="could not be (started with its restrictions in place|read)"):
        sandbox.convert(b"Item,Qty\nA,1\n", "q.csv", command=[sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ran')"])
    assert not marker.exists()  # the reader never ran
