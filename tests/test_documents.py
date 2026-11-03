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


def test_a_worker_that_never_finishes_is_killed_with_its_children(tmp_path):
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
