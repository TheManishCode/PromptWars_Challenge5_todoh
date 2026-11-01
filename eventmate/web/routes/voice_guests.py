"""Voice, voice notes and the guest list."""

from __future__ import annotations

import json
from flask import Response, abort, jsonify, request
from eventmate.voice.turns import ID as VOICE_ID, LANGUAGE as VOICE_LANGUAGE
from eventmate.guestlist import MAX_BYTES as MAX_GUEST_BYTES
from eventmate.web.base import (
    GuestCommitBody,
    GuestCsvBody,
    VoiceCancelBody,
    VoiceSessionBody,
    VoiceTurnBody,
)


def register(app, k):
    P = k.P
    _err = k._err
    audio_upload = k.audio_upload
    body = k.body
    em = k.em
    lock = k.lock
    no_body_or_empty_object = k.no_body_or_empty_object
    out = k.out
    pid = k.pid
    stream_slots = k.stream_slots
    transcriber = k.transcriber
    voice_svc = k.voice_svc

    @app.get("/api/voice/capabilities")
    def voice_capabilities():
        return jsonify({
            "transcribe": transcriber.capabilities(),
            "sessions": {"enabled": True},
            "turns": {"enabled": True},
            "live": {"enabled": True, "reason_unavailable": None},  # the server side of live mode is always ready; whether the browser can listen is the page's to check
            "keep_original": em.voice_notes.enabled,
        })

    @app.post("/api/voice/transcribe")
    def voice_transcribe():
        """Turn one recorded note into text on this machine. Nothing is sent to the assistant by this call; the audio is deleted before it returns."""
        data, declared = audio_upload()
        language = request.form.get("language") or None
        if language is not None and not VOICE_LANGUAGE.fullmatch(language):
            raise ValueError("language must be a language code such as en or hi-IN")
        got = transcriber.transcribe(data, declared, language)
        return jsonify({"transcript": got.text, "language": got.language, "duration_s": got.duration_s})

    @app.post("/api/voice/sessions")
    def voice_session_open():
        b = body(VoiceSessionBody)
        return jsonify(voice_svc.create_session(P(), b.plan_id)), 201

    @app.delete("/api/voice/sessions/<session_id>")
    def voice_session_close(session_id):
        voice_svc.end_session(P(), session_id if VOICE_ID.fullmatch(session_id) else "")  # no body: the id in the path is all it needs
        return jsonify({"ended": True})

    @app.post("/api/voice/turns")
    def voice_turn():
        """One transcript through the same assistant pipeline as typed chat. Server-sent events when asked for (Accept: text/event-stream), else one JSON body."""
        b = body(VoiceTurnBody)
        turn = voice_svc.start_turn(P(), b.session_id, b.turn_id, b.generation, b.text, b.input_mode, b.confidence, b.language)
        events = voice_svc.run_turn(P(), b.session_id, turn)
        if "text/event-stream" not in request.headers.get("Accept", ""):
            got = list(events)
            by = {e["event"]: e["data"] for e in got if e["event"] != "sentence"}
            return jsonify({"turn_id": turn.turn_id, "sentences": [e["data"] for e in got if e["event"] == "sentence"], "final": by.get("final"), "error": by.get("error")})
        if not stream_slots.acquire(blocking=False):
            events.close()
            turn.request_cancel()  # nothing was started, so nothing is left running
            return _err(429, "too many voice streams are open; try again in a moment")

        def stream():
            try:
                for e in events:
                    yield f"event: {e['event']}\ndata: {json.dumps(e['data'])}\n\n"
            finally:
                events.close()
                stream_slots.release()

        return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/voice/turns/<turn_id>/cancel")
    def voice_turn_cancel(turn_id):
        """The user spoke over the answer or walked away. ``playback_chars`` is how much speech was played: an estimate, not proof of what was heard."""
        b = body(VoiceCancelBody)
        return jsonify(voice_svc.cancel_turn(P(), b.session_id, turn_id if VOICE_ID.fullmatch(turn_id) else "", b.generation, b.playback_chars))

    @app.post("/api/plans/<plan_id>/voice-notes")
    def voice_note_save(plan_id):
        """Keep the original recording with the plan. Off unless the owner turned it on (EVENTMATE_VOICE_KEEP_ORIGINAL=1)."""
        plan_id = pid(plan_id)
        data, declared = audio_upload()
        with lock:
            em.store.latest(plan_id)
            return jsonify(em.voice_notes.save(plan_id, data, declared)), 201

    @app.get("/api/plans/<plan_id>/voice-notes/<note_id>")
    def voice_note_get(plan_id, note_id):
        plan_id = pid(plan_id)
        with lock:
            em.store.latest(plan_id)
            got = em.voice_notes.read(plan_id, note_id)
        if got is None:
            abort(404)
        return Response(got[0], mimetype=got[1], headers={"Cache-Control": "no-store", "Content-Disposition": "attachment; filename=voice-note"})

    @app.delete("/api/plans/<plan_id>/voice-notes/<note_id>")
    def voice_note_delete(plan_id, note_id):
        plan_id = pid(plan_id)
        no_body_or_empty_object()
        with lock:
            em.store.latest(plan_id)
            return jsonify({"deleted": em.voice_notes.delete(plan_id, note_id)})

    @app.post("/api/plans/<plan_id>/guests/preview")
    def guests_preview(plan_id):
        """Parse a guest CSV (JSON {"csv": "..."} or one multipart file) without saving it. Row problems are listed by line; a clean file gets a single-use token."""
        plan_id = pid(plan_id)
        if request.mimetype == "multipart/form-data":
            f = request.files.get("file")
            if f is None or len(request.files) != 1:
                raise ValueError("send exactly one file, in the field named file")
            raw = f.stream.read(MAX_GUEST_BYTES + 1)
        else:
            raw = body(GuestCsvBody).csv.encode("utf-8")
        with lock:
            preview = em.guest_preview(plan_id, raw)
        return out(preview)

    @app.post("/api/plans/<plan_id>/guests/commit")
    def guests_commit(plan_id):
        plan_id, b = pid(plan_id), body(GuestCommitBody)
        with lock:
            saved = em.guest_commit(plan_id, b.preview_token)
        return jsonify({"saved": True, "totals": saved["totals"], "check": saved["check"]}), 201

    @app.get("/api/plans/<plan_id>/guests")
    def guests_get(plan_id):
        """Totals for the saved list beside the stated headcount. Never names."""
        plan_id = pid(plan_id)
        with lock:
            saved = em.guest_summary(plan_id)
        return jsonify({"has_list": saved is not None, **(saved or {})})

    @app.delete("/api/plans/<plan_id>/guests")
    def guests_delete(plan_id):
        plan_id = pid(plan_id)
        no_body_or_empty_object()
        with lock:
            gone = em.guest_delete(plan_id)
        return jsonify({"deleted": gone})
