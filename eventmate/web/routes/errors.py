"""Error handlers: every exception the app knows is turned into one JSON shape, ``{error, code}``."""

from __future__ import annotations

import errno
from flask import g, jsonify
from pydantic import ValidationError
from eventmate.voice import audio as voice_audio
from eventmate.voice.notes import NotesDisabled
from eventmate.voice.transcribe import TranscribeError
from eventmate.voice.turns import VoiceError
from eventmate.guestlist import MAX_BYTES as MAX_GUEST_BYTES, GuestListTooLarge, StalePreview
from eventmate.documents.sandbox import DocumentError
from eventmate.retention import PlanBusy
from eventmate.agents.guest import GuestDataError
from eventmate.agents.manager import RequestParseError
from eventmate.agents.vendor import DataIntegrityError
from eventmate.integrations.explore import ExploreError, ExploreNotConfigured
from eventmate import consumption
from eventmate.llm import LLMError
from eventmate.plan_store import PlanIntegrityError
from eventmate.integrations.discovery import DiscoveryError
from eventmate.integrations.places import PlacesError, PlacesNotConfigured
from eventmate.integrations.weather import WeatherError, WeatherNotConfigured
from eventmate.followthrough.jobs import StaleJob
from eventmate.sourcing.suppression import NeedsAcknowledgement
from eventmate.integrations.websearch import SearchError
from eventmate.integrations.travel import TravelError
from eventmate.planning.staging import StageClosed, StageConflict, StageGone
from eventmate.web.base import (
    NotOnSharedHost,
    log,
)


def register(app, k):
    _err = k._err

    @app.errorhandler(VoiceError)
    def _voice_error(exc):
        return _err(exc.status, str(exc)[:300])

    @app.errorhandler(TranscribeError)
    def _transcribe_error(exc):
        return _err(exc.status, str(exc)[:300], code=exc.code)

    @app.errorhandler(voice_audio.AudioRejected)
    def _audio_rejected(exc):
        return _err(exc.status, str(exc)[:300], code={413: "audio_too_large", 415: "bad_audio_type"}.get(exc.status, "bad_audio"))

    @app.errorhandler(NotesDisabled)
    def _notes_disabled(exc):
        return _err(503, "keeping voice notes is not turned on")

    @app.errorhandler(GuestListTooLarge)
    def _guests_too_large(exc):
        return _err(413, str(exc)[:200], code="too_large", limit_bytes=MAX_GUEST_BYTES, scope="payload")

    @app.errorhandler(StalePreview)
    def _stale_preview(exc):
        return _err(409, str(exc)[:200])

    @app.errorhandler(DocumentError)
    def _document(exc):
        return _err(422, str(exc)[:300], code="bad_document")

    @app.errorhandler(PlanBusy)
    def _plan_busy(exc):
        return _err(409, str(exc)[:300])

    @app.errorhandler(RequestParseError)
    def _parse(exc):
        return _err(422, "request could not be parsed", problems=list(exc.problems))

    @app.errorhandler(ValidationError)
    def _validation(exc):
        return _err(422, "invalid input", details=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()][:10])

    @app.errorhandler(ExploreNotConfigured)
    def _explore_off(exc):
        return _err(503, str(exc)[:300])

    @app.errorhandler(ExploreError)
    def _explore(exc):
        return _err(422, str(exc)[:400])

    @app.errorhandler(consumption.ConsumptionError)
    def _consumption(exc):
        return _err(429, str(exc)[:300], code=exc.code)

    @app.errorhandler(LLMError)
    def _llm(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(PlacesNotConfigured)
    def _places_off(exc):
        return _err(503, str(exc)[:300])

    @app.errorhandler(PlacesError)
    def _places(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(SearchError)
    def _search(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(WeatherNotConfigured)
    def _weather_off(exc):
        return _err(503, str(exc)[:300])

    @app.errorhandler(WeatherError)
    def _weather(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(DiscoveryError)
    def _discovery(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(TravelError)
    def _travel(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(GuestDataError)
    def _guests(exc):
        return _err(422, "unknown or invalid guest list")

    @app.errorhandler(DataIntegrityError)
    def _data(exc):
        log.error("vendor data integrity failure: %s", exc)
        return _err(500, "vendor data failed validation")

    @app.errorhandler(FileNotFoundError)
    def _nf(exc):
        return _err(404, "not found", code="not_found")

    @app.errorhandler(PlanIntegrityError)
    def _integrity(exc):
        log.error("plan integrity failure (request %s): %s", g.get("rid", "-"), exc)  # which file: server log only
        return _err(503, "stored plan failed its integrity check; nothing was changed. The owner can run `python -m eventmate verify`", code="plan_integrity", request_id=g.get("rid"))

    @app.errorhandler(413)
    def _too_big(exc):
        limit = g.get("upload_limit") or 64 * 1024
        return _err(413, "request too large", code="too_large", limit_bytes=limit, scope="envelope")  # the whole body, as opposed to the file inside it

    @app.errorhandler(405)
    def _method(exc):
        return _err(405, "method not allowed")

    @app.errorhandler(404)
    def _404(exc):
        return _err(404, "not found", code="not_found")  # the one body every miss gets, so a hidden route cannot be told from an unknown one

    @app.errorhandler(NeedsAcknowledgement)
    def _needs_ack(exc):
        return jsonify({"error": str(exc)[:600], "needs_acknowledgement": True, "reason": exc.reason}), 409

    @app.errorhandler(StaleJob)
    def _stale(exc):
        return _err(409, "this job changed while you were looking at it; reload and try again")

    @app.errorhandler(NotOnSharedHost)
    def _not_shared(exc):
        return _err(403, str(exc)[:200])

    @app.errorhandler(StageGone)
    def _stage_gone(exc):
        return _err(410, str(exc)[:300], code="brief_gone")

    @app.errorhandler(StageConflict)
    def _stage_conflict(exc):
        return _err(409, str(exc)[:300], code="brief_changed")

    @app.errorhandler(StageClosed)
    def _stage_closed(exc):
        return _err(409, str(exc)[:300], code="brief_closed")

    @app.errorhandler(ValueError)
    def _value(exc):
        return _err(400, str(exc)[:300])

    @app.errorhandler(RecursionError)
    def _too_deep(exc):
        return _err(400, "the request is nested too deeply", code="bad_request")  # a body or value nested past the parser's limit is the caller's fault, never a 500

    @app.errorhandler(Exception)
    def _boom(exc):
        if hasattr(exc, "code") and isinstance(exc.code, int):
            return _err(exc.code, getattr(exc, "name", "error"))
        rid = g.get("rid", "-")
        if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
            log.error("storage full (request %s): %s", rid, type(exc).__name__)
            return _err(507, "the server has no room to save this, so the change may not have been made; try again once the owner has freed space. If you were deleting, run the delete again to finish it", code="storage_full", request_id=rid)
        log.exception("unhandled error (request %s)", rid)  # the traceback goes to the server's own log only; the answer names the request and nothing else
        return _err(500, "internal error", code="internal", request_id=rid)
