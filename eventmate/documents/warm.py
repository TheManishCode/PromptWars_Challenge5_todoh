"""Fetch the models Docling needs, once, so that reading a PDF or an image never touches the network: ``python -m eventmate.documents.warm``.

This is the one place document reading uses the network, and it runs on its own, outside the sandbox, when the person asks for it. Reading a quote file afterwards runs offline."""

from __future__ import annotations

import sys

from eventmate.documents.sandbox import model_home


def main() -> int:
    try:
        from docling.utils.model_downloader import download_models
    except ImportError:
        sys.stderr.write("Docling is not installed (pip install docling, or pip install '.[documents]'); text, CSV and HTML quotes work without it.\n")
        return 1
    download_models()
    sys.stdout.write(f"models are in {model_home()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
