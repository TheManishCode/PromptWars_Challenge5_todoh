"""The landing page, the planner page, their static files and the session route."""

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
    _gzip_cache,
)


OPTIONAL_MEDIA = {"landing-poster.jpg": r'<img class="hero__poster"[^>]*>\s*', "landing-hero.mp4": r'<video class="hero__video".*?</video>\s*'}


def register(app, k):
    P = k.P
    asset_version = k.asset_version
    em = k.em
    shared = k.shared
    today_fn = k.today_fn

    def page(name: str) -> Response:
        """An HTML page of ours, with every script, style, image and video it names given the asset-version address (``?v=``): a changed file gets a new address, so
        what the page names can be kept for a year and a visitor never runs new markup against an old script."""
        version = asset_version()
        html = (STATIC_DIR / name).read_text(encoding="utf-8")
        for media, tag in OPTIONAL_MEDIA.items():  # the hero poster and video are not in every checkout: no tag for a file that is absent, so no request fails
            if not (STATIC_DIR / media).is_file():
                html = re.sub(tag, "", html, flags=re.S)
        if not (STATIC_DIR / "landing-poster.jpg").is_file():
            html = re.sub(r'\sposter="[^"]*"', "", html)
        html = re.sub(r'((?:src|href)="/static/[A-Za-z0-9_.-]+\.(?:js|css|svg|jpg|mp4))"', lambda m: f'{m.group(1)}?v={version}"', html)
        html = html.replace("<head>", f'<head>\n<meta name="asset-version" content="{version}">', 1)
        return Response(html, mimetype="text/html", headers={"Cache-Control": "no-cache"})  # the page itself is always asked for again; what it names is cached for good

    @app.get("/")
    def index():
        """What to;doh is and does; its buttons lead to the planner."""
        return page("landing.html")

    @app.get("/app")
    def planner():
        """The planner: describe an event, check what was understood, get the plan, its evidence and your changes."""
        return page("app.html")

    @app.get("/plan")
    def plan_front_door():
        """Describe an event, check what was understood, then find vendors, ask them for quotes and plan from the answers."""
        return page("plan.html")

    @app.get("/welcome")
    def welcome():
        return redirect("/", 308)  # the landing page used to live here

    @app.get("/arcade")
    def arcade():
        return redirect("/", 308)  # the 3D room was replaced by the landing page and the planner; this is only for old links

    @app.get("/static/<name>")
    def static_file(name: str):
        if name not in STATIC_FILES:
            abort(404)
        path = STATIC_DIR / name
        if not path.is_file():
            abort(404)  # a listed file that is not in this checkout (the hero media): the page copes
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
