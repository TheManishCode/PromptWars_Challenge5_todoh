"""The room page, its static files and the session route."""

from __future__ import annotations

import gzip
import re
from flask import Response, abort, g, jsonify, redirect, request, send_file
from eventmate.web.base import (
    GZIP_MAX,
    GZIP_MIN,
    LONG_CACHE,
    STATIC_DIR,
    STATIC_FILES,
    VENDOR_FILES,
    _gzip_cache,
)


def register(app, k):
    P = k.P
    asset_version = k.asset_version
    em = k.em
    shared = k.shared
    today_fn = k.today_fn

    @app.get("/")
    def index():
        """The arcade room is the only interface (the classic page was removed on the owner's instruction)."""
        version = asset_version()
        html = (STATIC_DIR / "arcade.html").read_text(encoding="utf-8")
        html = re.sub(r'((?:src|href)="/static/[A-Za-z0-9_.-]+\.(?:js|css|svg))"', lambda m: f'{m.group(1)}?v={version}"', html)
        html = html.replace("<head>", f'<head>\n<meta name="asset-version" content="{version}">', 1)
        return Response(html, mimetype="text/html", headers={"Cache-Control": "no-cache"})  # the page itself is always asked for again; what it names is cached for good

    @app.get("/arcade")
    def arcade():
        return redirect("/", 308)  # one interface: the room lives at /, this is only for old links

    @app.get("/static/<name>")
    def static_file(name: str):
        if name not in STATIC_FILES:
            abort(404)
        path = STATIC_DIR / ("vendor" if name in VENDOR_FILES else "") / name
        versioned = request.args.get("v") == asset_version()  # asked for by the address the page was given: it can never be stale, so it may be kept for a year
        cache = "public, max-age=31536000, immutable" if versioned else ("public, max-age=3600" if name in LONG_CACHE else "no-cache")
        if path.stat().st_size > GZIP_MAX:
            resp = send_file(path, mimetype=STATIC_FILES[name].split(";")[0], conditional=True, etag=True, max_age=None)  # streamed, with ETag and Range, never read whole into memory
            resp.headers["Cache-Control"] = cache if versioned else "no-cache"
            return resp
        body = path.read_bytes()
        headers = {"Cache-Control": cache, "Vary": "Accept-Encoding"}
        if len(body) >= GZIP_MIN and "gzip" in request.headers.get("Accept-Encoding", "").lower():
            key = (name, path.stat().st_mtime_ns)
            if key not in _gzip_cache:
                _gzip_cache[key] = gzip.compress(body, compresslevel=9, mtime=0)
            body, headers["Content-Encoding"] = _gzip_cache[key], "gzip"
        return Response(body, mimetype=STATIC_FILES[name].split(";")[0], headers=headers)

    @app.get("/api/session")
    def session():
        return jsonify({"csrf": g.ws.csrf, "today": today_fn().isoformat(), "roles": [r.value for r in P().roles], "guest_lists": list(em.manager.guest.repo.list_ids()),
                        "workspace": {"id": g.ws.id, "created": g.ws.created, "mode": "shared" if shared else "local"}})
