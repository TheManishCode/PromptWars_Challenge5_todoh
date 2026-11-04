"""Fetch the models Docling needs, once, so that reading a PDF or an image never touches the network: ``python -m eventmate.documents.warm``.

This is the one place document reading uses the network, and it runs on its own, outside the sandbox, when the person asks for it. Reading a quote file afterwards runs offline: the reader is also shut in as far as the machine allows (``documents/isolation.py``)."""

from __future__ import annotations

import sys

from eventmate.documents.sandbox import model_home


def _sample_pdf() -> bytes:
    """A one-line PDF, enough to make Docling build its whole pipeline (layout, table and OCR models) once, with the network available."""
    stream = b"BT /F1 14 Tf 40 100 Td (Quote: 1 x Rs 100 = Rs 100) Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, start)


def main() -> int:
    try:
        from docling.utils.model_downloader import download_models
    except ImportError:
        sys.stderr.write("Docling is not installed (pip install docling, or pip install '.[documents]'); text, CSV and HTML quotes work without it.\n")
        return 1
    download_models()
    # download_models() does not fetch everything: the OCR engine pulls its own weights the first time a pipeline is built. Build one now, here, with the network, so the
    # offline sandbox never has to (without this a first PDF in a clean install failed with "could not be read").
    import tempfile
    from pathlib import Path

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.ocr_options.force_full_page_ocr = True  # a text PDF never reaches the OCR engine, whose weights are the ones fetched lazily: force it to run once
    with tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp) / "warm.pdf"
        sample.write_bytes(_sample_pdf())
        DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}).convert(str(sample))
    sys.stdout.write(f"models are in {model_home()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
