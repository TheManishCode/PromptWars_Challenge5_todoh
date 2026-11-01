"""The wall's HTTP surface (docs/WALL.md, 8.6): exactly 14 routes, authenticated by the wall's own cookie and CSRF value.

The wall does not use the private session. The allow-list below is matched on the registered route rule, never on a path prefix, so an unknown or future
``/api/wall/...`` path is a plain 404 and gets no exemption from the bearer token. No wall response contains the private CSRF value, and the wall's CSRF
value does not authorise any private route.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Any, Callable

from flask import Flask, Request, Response, jsonify, request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, ValidationError

from eventmate.wall.limits import WallLimits
from eventmate.wall.store import WallError, WallStore

COOKIE = "todoh_wall"
CSRF_HEADER = "X-EventMate-Wall-CSRF"
COOKIE_MAX_AGE = 365 * 24 * 3600  # longevity is the goal: a visitor comes back to find their name
_CRED = re.compile(r"^[A-Za-z0-9_-]{43}$")  # token_urlsafe(32)
_IDEM = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_ROUND = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The exact list, by registered rule. A test enumerates the app's /api/wall/ rules and fails if one is missing here or extra.
PUBLIC_READS = ("/api/wall/meta", "/api/wall/sections/<int:section_id>", "/api/wall/hall")
PRIVATE_READS = ("/api/wall/session", "/api/wall/me")
WRITES = ("/api/wall/marks", "/api/wall/me/recovery-code", "/api/wall/recover", "/api/wall/me/remove", "/api/wall/me/hall", "/api/wall/rounds",
          "/api/wall/rounds/<round_id>/ready", "/api/wall/rounds/<round_id>/tap", "/api/wall/rounds/<round_id>/cancel")
WALL_RULES: frozenset[str] = frozenset(PUBLIC_READS + PRIVATE_READS + WRITES)

PUBLIC_MAX_AGE, HALL_MAX_AGE = 60, 30


def hash_credential(cred: str) -> str:
    return hashlib.sha256(cred.encode()).hexdigest()


def csrf_for(cred: str) -> str:
    """The wall CSRF value is an HMAC keyed by the credential itself: bound to this cookie, derivable only by someone who holds it, nothing stored."""
    return base64.urlsafe_b64encode(hmac.new(cred.encode(), b"todoh-wall-csrf-v1", hashlib.sha256).digest()).decode().rstrip("=")


def new_credential() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=")  # 128 bits, 26 characters
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def code_hash(code: str) -> str | None:
    plain = re.sub(r"[\s-]", "", code).upper()
    if not re.fullmatch(r"[A-Z2-7]{26}", plain):
        return None
    return hashlib.sha256(plain.encode()).hexdigest()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarkBody(_Strict):
    name: str


class CodeBody(_Strict):
    recovery_code: str


class IssueCodeBody(_Strict):
    replace_existing: StrictBool = False


class RemoveBody(_Strict):
    recovery_code: str | None = None


class HallBody(_Strict):
    opt_in: StrictBool


class RoundBody(_Strict):
    mode: str


class EmptyBody(_Strict):
    pass


class TapBody(_Strict):
    slot: StrictInt | None = None
    mark_id: StrictInt | None = None


class Wall:
    """Registers the 14 wall routes on an app and answers the app's guard about them."""

    def __init__(self, app: Flask, store: WallStore, limits: WallLimits | None = None, *, bearer_check: Callable[[Request], bool] | None = None,
                 secure_cookies: bool = False, client_ip: Callable[[], str] = lambda: request.remote_addr or "?"):
        self.store, self.limits = store, limits or WallLimits()
        self.bearer_check, self.secure_cookies, self.client_ip = bearer_check, secure_cookies, client_ip
        self._register(app)

    # ------------------------------------------------------------ guard (called by the app's before_request for every /api/wall/ path)

    @staticmethod
    def owns(req: Request) -> bool:
        """Is this request for one of the 14 registered wall rules? Matched on the rule, never on a path prefix."""
        return req.url_rule is not None and req.url_rule.rule in WALL_RULES

    def unknown(self) -> Response:
        return self._err(404, "not_found", "not found")

    def guard(self, req: Request) -> Response | None:
        """None means the app's private checks (bearer token, private CSRF) are skipped and the route runs. A Response is the answer."""
        if self.bearer_check is not None and not self.bearer_check(req):
            return self._err(401, "bearer_required", "authentication required")
        if not self.owns(req):
            return self.unknown()
        if req.method not in ("GET", "HEAD", "OPTIONS"):
            origin = req.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] != req.host:
                return self._err(403, "unauthorized", "cross-origin request refused")
            if req.mimetype != "application/json":
                return self._err(415, "bad_request", "JSON body required")
            if not self.limits.writes_ip.allow(self.client_ip()):
                return self._err(429, "rate_limited", "too many requests")
        return None

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _err(status: int, code: str, message: str, **extra: Any) -> Response:
        resp = jsonify({"error": message, "code": code, **extra})
        resp.status_code = status
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def _private(self, payload: dict[str, Any], status: int = 200) -> Response:
        resp = jsonify(payload)
        resp.status_code = status
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Vary"] = "Cookie"
        return resp

    def _public(self, payload: dict[str, Any], max_age: int) -> Response:
        body = jsonify(payload)
        tag = 'W/"' + hashlib.sha1(body.get_data(), usedforsecurity=False).hexdigest() + '"'  # nosec B324: an ETag, not a security control
        if request.headers.get("If-None-Match") == tag:
            resp = Response(status=304)
        else:
            resp = body
        resp.headers["ETag"] = tag
        resp.headers["Cache-Control"] = f"public, max-age={max_age}"
        return resp

    def _set_cookie(self, resp: Response, cred: str) -> Response:
        resp.set_cookie(COOKIE, cred, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax", secure=self.secure_cookies or request.is_secure, path="/")
        return resp

    def _cred(self) -> str | None:
        raw = request.cookies.get(COOKIE, "")
        return raw if _CRED.fullmatch(raw) else None

    def _owner(self, write: bool) -> tuple[str, str]:
        """(credential, its hash) of a valid session; for a write also the wall CSRF header. Anything else is 401 and changes nothing."""
        cred = self._cred()
        if cred is None or not self.store.session_exists(hash_credential(cred)):
            raise WallError(401, "unauthorized", "start a wall session first")
        if write and not hmac.compare_digest(request.headers.get(CSRF_HEADER, ""), csrf_for(cred)):
            raise WallError(401, "unauthorized", "missing or invalid wall token; reload the session and try again")
        return cred, hash_credential(cred)

    @staticmethod
    def _body(model: type[BaseModel]) -> Any:
        try:
            data = request.get_json(silent=True)
        except RecursionError:  # nesting deep enough to exhaust the parser: some Python versions raise this instead of returning None (found by an outside verifier on 3.10)
            data = None
        if not isinstance(data, dict):
            raise WallError(400, "bad_request", "JSON object body required")
        try:
            return model.model_validate(data)
        except ValidationError:
            raise WallError(400, "bad_request", "the request body is not valid") from None

    @staticmethod
    def _int_arg(name: str, default: int, low: int, high: int) -> int:
        raw = request.args.get(name)
        if raw is None:
            return default
        if not re.fullmatch(r"-?\d{1,6}", raw) or not low <= int(raw) <= high:
            raise WallError(400, "bad_request", f"{name} is not valid")
        return int(raw)

    # ------------------------------------------------------------ routes

    def _register(self, app: Flask) -> None:
        store = self.store

        def handled(fn):
            def wrapper(*a, **k):
                try:
                    return fn(*a, **k)
                except WallError as exc:
                    return self._err(exc.status, exc.code, exc.message, **exc.extra)
            wrapper.__name__ = "wall_" + fn.__name__
            return wrapper

        def route(rule: str, methods: tuple[str, ...]):
            def deco(fn):
                app.add_url_rule(rule, endpoint="wall_" + fn.__name__, view_func=handled(fn), methods=list(methods))
                return fn
            return deco

        @route("/api/wall/meta", ("GET",))
        def meta():
            return self._public(store.meta(), PUBLIC_MAX_AGE)

        @route("/api/wall/sections/<int:section_id>", ("GET",))
        def section(section_id):
            after = self._int_arg("after_slot", -1, -1, 100_000)
            limit = self._int_arg("limit", 100, 1, 300)
            return self._public(store.section(section_id, after, limit), PUBLIC_MAX_AGE)

        @route("/api/wall/hall", ("GET",))
        def hall():
            order = request.args.get("order", "newest")
            cursor = request.args.get("cursor")
            if cursor is not None and len(cursor) > 200:
                raise WallError(400, "bad_request", "that page marker is not valid")
            limit = self._int_arg("limit", 0, 0, 100) or None
            return self._public(store.hall(order, cursor, limit), HALL_MAX_AGE)

        @route("/api/wall/session", ("GET",))
        def session():
            cred = self._cred()
            if cred is not None and store.session_exists(hash_credential(cred)):
                return self._private({"csrf": csrf_for(cred), **store.session_state(hash_credential(cred))})
            ip = self.client_ip()
            if not (self.limits.bootstrap_ip.allow(ip) and self.limits.bootstrap_all.allow("*")):
                raise WallError(429, "rate_limited", "too many new visitors from here; try again later")
            cred = new_credential()
            store.create_session(hash_credential(cred))
            store.maybe_sweep()
            return self._set_cookie(self._private({"csrf": csrf_for(cred), "has_mark": False, "mark_status": None}), cred)

        @route("/api/wall/me", ("GET",))
        def me():
            _cred, h = self._owner(write=False)
            return self._private(store.me(h))

        @route("/api/wall/marks", ("POST",))
        def marks():
            _cred, h = self._owner(write=True)
            key = request.headers.get("Idempotency-Key", "")
            if not _IDEM.fullmatch(key):
                raise WallError(400, "bad_request", "an Idempotency-Key header (8 to 128 letters, digits, - or _) is required")
            body = self._body(MarkBody)
            ip = self.client_ip()
            if not self.limits.submit_ip.peek(ip):
                raise WallError(429, "rate_limited", "too many names from here; try again later")
            status, payload, created = store.submit(h, key, body.name)
            if created:
                self.limits.submit_ip.add(ip)
            resp = self._private(payload, status)
            return resp

        @route("/api/wall/me/recovery-code", ("POST",))
        def recovery_code():
            _cred, h = self._owner(write=True)
            body = self._body(IssueCodeBody)
            code = new_recovery_code()
            store.issue_recovery_code(h, code_hash(code), body.replace_existing)
            return self._private({"recovery_code": code})  # shown once: only its hash is stored

        def code_attempt(raw: str, action: Callable[[str], Any]):
            """A recovery code guess: limits first, the same 404 for a malformed, unknown or used code, and every failure counted."""
            ip = self.client_ip()
            if not (self.limits.recover_fail_ip.peek(ip) and self.limits.recover_fail_all.peek("*")):
                raise WallError(429, "rate_limited", "too many wrong codes; try again later")
            hashed = code_hash(raw) if isinstance(raw, str) and len(raw) <= 64 else None
            try:
                if hashed is None:
                    raise WallError(404, "not_found", "not found")
                return action(hashed)
            except WallError as exc:
                if exc.status == 404:
                    self.limits.recover_fail_ip.add(ip)
                    self.limits.recover_fail_all.add("*")
                raise

        @route("/api/wall/recover", ("POST",))
        def recover():
            _cred, caller = self._owner(write=True)
            body = self._body(CodeBody)
            fresh = new_credential()
            result = code_attempt(body.recovery_code, lambda hashed: self.store.recover(hashed, caller, hash_credential(fresh)))
            return self._set_cookie(self._private(result), fresh)

        @route("/api/wall/me/remove", ("POST",))
        def remove():
            _cred, h = self._owner(write=True)
            body = self._body(RemoveBody)
            if body.recovery_code is None:
                store.remove_mine(h)
            else:
                code_attempt(body.recovery_code, lambda hashed: self.store.remove_by_code(hashed, h))
            return self._private({"removed": True})

        @route("/api/wall/me/hall", ("POST",))
        def me_hall():
            _cred, h = self._owner(write=True)
            body = self._body(HallBody)
            return self._private({"hall": store.set_hall(h, body.opt_in)})

        @route("/api/wall/rounds", ("POST",))
        def rounds():
            _cred, h = self._owner(write=True)
            body = self._body(RoundBody)
            return self._private(store.issue_round(h, body.mode))

        def round_id_ok(round_id: str) -> str:
            if not _ROUND.fullmatch(round_id):
                raise WallError(404, "not_found", "not found")
            return round_id

        @route("/api/wall/rounds/<round_id>/ready", ("POST",))
        def round_ready(round_id):
            _cred, h = self._owner(write=True)
            self._body(EmptyBody)
            return self._private(store.ready_round(h, round_id_ok(round_id)))

        @route("/api/wall/rounds/<round_id>/tap", ("POST",))
        def round_tap(round_id):
            _cred, h = self._owner(write=True)
            body = self._body(TapBody)
            if (body.slot is None) == (body.mark_id is None):
                raise WallError(400, "bad_request", "send exactly one of slot or mark_id")
            return self._private(store.tap_round(h, round_id_ok(round_id), body.slot, body.mark_id))

        @route("/api/wall/rounds/<round_id>/cancel", ("POST",))
        def round_cancel(round_id):
            _cred, h = self._owner(write=True)
            self._body(EmptyBody)
            return self._private({"cancelled": store.cancel_round(h, round_id_ok(round_id))})
