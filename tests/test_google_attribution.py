"""Google's logo, as Google supplies it, beside Google listing details. A changed file is a licence breach, so its hash is pinned."""

import hashlib

from eventmate.web.base import STATIC_DIR, STATIC_FILES

GOOGLE_SHA256 = "62c52df68d8f450c860d5d65068fc96d63c2dfaec849b91ac64b3256a898936e"  # GoogleMaps_Logo_DarkGray.svg from Google_Maps_Attribution_Assets.zip, fetched 2026-11-07


def test_the_logo_is_google_s_own_file_unmodified():
    assert hashlib.sha256((STATIC_DIR / "google-maps-logo.svg").read_bytes()).hexdigest() == GOOGLE_SHA256
    assert STATIC_FILES["google-maps-logo.svg"] == "image/svg+xml"


def test_the_page_shows_it_at_google_s_size_with_the_label_google_asks_for():
    js = (STATIC_DIR / "app.js").read_text()
    assert "alt: 'Google Maps', width: '98', height: '18'" in js, "98x18 is the file's own size: 18dp is inside Google's 16 to 19dp"
    css = (STATIC_DIR / "app.css").read_text()
    assert ".gmaps { display: inline-block; padding: 10px 10px 5px;" in css, "clear space: 10dp left, right and top, 5dp below"
