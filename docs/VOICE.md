# Voice

Voice is another way to produce and hear text. A spoken question goes through the same assistant as a typed one, so every guard that applies to typed chat applies unchanged; only the **checked** answer is ever spoken; and anything that changes something (approve, send, spend, the standing permission, importing a guest list) stays a click (ADR-056).

This page is the backend. The page, microphone, voice-activity detection, text-to-speech and barge-in are built separately; see "Not verified" at the end for what this change did not and could not check.

## What is available at zero cost

| Capability | Where it runs | Status |
|---|---|---|
| Speech to text for a recorded **voice note** | On this machine, through an engine the owner installs (`faster-whisper`, MIT) | **Off until the owner approves and installs it.** The code path is tested with a fake engine; the real engine has not been run by the author of this change |
| Speech to text while talking (live mode) | The browser's own speech recognition, on the page | Frontend. May send audio to the browser vendor's speech service; the page must say so |
| Text to speech | The browser's built-in speech synthesis | Frontend |
| Voice-activity detection, in-browser Whisper, in-browser neural voices | The browser | Optional upgrades; none is built or vendored here; each needs the owner's approval, a licence check and offline loading |
| WhatsApp voice notes | | Not built (see below) |

The browser's speech recognition API is built for live microphone input; there is no verified way to feed it a saved clip. So a **voice note** needs the server engine. If none is enabled, the capability says `none`, the page says "Voice transcription is not set up here" and offers typing, and nothing is ever transcribed by guesswork. **Cloud or paid speech services are not assumed and not used.**

"Zero cost" means zero money, not zero compute: the engine needs a model download, disk, memory and CPU time per note.

## Turning the server transcriber on

Needs the owner's approval, because it adds a dependency.

```sh
pip install faster-whisper                       # MIT
# fetch one model into a folder, once, while online (the app never downloads at run time).
# The faster-whisper README shows how (it has a download helper); that step was not run in writing this page.
export EVENTMATE_VOICE_ENGINE=faster-whisper
export EVENTMATE_VOICE_MODEL=/path/to/models/base
python -m eventmate.web
```

These are read from the process environment. They are deliberately **not** in the `.env` allow-list, so set them in the shell or the service definition. Optional: `EVENTMATE_VOICE_DEVICE` (default `cpu`), `EVENTMATE_VOICE_COMPUTE` (default `int8`). Start with the multilingual `base` or `tiny` model, then measure it on your own English, Hindi and Marathi recordings before promising anything:

```sh
EVENTMATE_VOICE_ENGINE=faster-whisper EVENTMATE_VOICE_MODEL=/path/to/models/base \
EVENTMATE_VOICE_SAMPLE=/path/to/your-note.wav python -m pytest tests/test_voice_real_engine.py -s
```

It prints the measured seconds (and times real time), the language it detected and the transcript, so you can compare with what you said. No accuracy figure exists yet and none should be quoted.

## Endpoints

All state-changing calls carry the CSRF header and follow the local-origin rules of the other routes. Voice sessions and turns need the same principal that opened the session.

```
GET /api/voice/capabilities
-> {"transcribe": {"route": "none"|"server", "engine": "faster-whisper"|null, "model": "base"|null, "languages": ["en","hi","mr"],
                   "max_seconds": 120, "max_bytes": 5242880}, "keep_original": false}
```

```
POST /api/voice/transcribe        multipart/form-data: one audio file in the field "audio"; optional text field "language" (en, hi-IN, ...)
-> 200 {"transcript": "...", "language": "en", "duration_s": 12.4}
-> 503 voice transcription is not set up here; type your message instead
-> 400 empty / no file    415 not audio, or its type does not match its contents    413 too large or too long
-> 422 no speech was recognised    429 busy    504 took too long
```
Nothing is sent to the assistant by this call. The recording is deleted before the call returns, on every path.

```
POST   /api/voice/sessions        {"plan_id": "..."}  -> 201 {"session_id": "...", "expires_at": "...", "limits": {"idle_timeout_s": 90, "session_cap_s": 900, "max_turn_chars": 600, "turns_per_minute": 20}}
DELETE /api/voice/sessions/<id>   {}                  -> {"ended": true}   (drops that session's memory)
```

```
POST /api/voice/turns
  {"session_id": "...", "turn_id": "turn-0001", "generation": 1, "text": "compare the vendors", "input_mode": "voice_note"|"live", "confidence": 0.9, "language": "en"}
  With  Accept: text/event-stream  the reply is a stream of events; without it, one JSON body with the same fields.
```
```
event: status    data: {"turn_id": "turn-0001", "state": "thinking"}                      (immediately)
event: sentence  data: {"seq": 0, "text_spoken": "...", "text_display": "..."}            (one per sentence to say)
event: final     data: {"turn_id": "turn-0001", "answer": "...", "spoken_text": "...", "outcome": "answered",
                        "proposals": [{"id": "...", "tool": "propose_ledger_entry", "summary": "...", "status": "pending"}]}
event: error     data: {"turn_id": "turn-0001", "code": "cancelled"|"failed", "message": "..."}
```
JSON fallback: `{"turn_id": "...", "sentences": [...], "final": {...}|null, "error": {...}|null}`.

```
POST /api/voice/turns/<turn_id>/cancel   {"playback_chars": 124}
-> {"turn_id": "...", "state": "cancelled", "rolled_back": true|false} | {"state": "interrupted", "played_chars": 124} | {"state": "done"}
```

Optional, off by default (below): `POST /api/plans/<id>/voice-notes`, `GET` and `DELETE .../voice-notes/<note_id>`.

Errors that are about someone else's or an expired id are all the same `404 that voice session or turn is not available`.

## The two modes

**Voice note: record, transcribe, review, then send.** The page records on an explicit "record" with a visible timer and level meter; "stop" never sends; it calls `/api/voice/transcribe` and shows progress; the review screen has playback, the editable transcript, "re-record", "discard" and "send". Only "send" submits the text, as a normal turn with `input_mode: "voice_note"`. Discard deletes the audio. Playback of the reviewed recording needs `media-src blob:`, which the page's security policy now allows (and nothing wider).

**Live mode: continuous.** An explicit "start conversation", an always-visible mic state, mute and end controls, auto-end after 60 seconds of silence, after 30 seconds with the tab hidden, or at the session cap. Live mode sends transcripts only; it never uploads audio to this backend.

## What a turn does differently from typed chat

A turn runs the same `Assistant.ask` (masking, input checks, injection scan, tools, answer fact check). The differences are all about not committing too early:

- **Proposals are staged.** A change the turn proposes waits in a private queue and is filed in the real approval queue only if the turn still stands when it finishes.
- **The answer cache is read but never written** by a voice turn.
- **Each session has its own memory**, separate from typed chat and from every other session. Ending or expiring a session clears it.
- **Cancellation is checked after every blocking step.** The model client cannot abort a call in flight, so cancelling means "discard the result and commit nothing"; per-call timeouts stay short.

Cancel, in the three cases:

| Situation | Result |
|---|---|
| Cancelled while thinking (before the answer is ready) | `cancelled`. Nothing is committed: no memory, no proposal, no cache entry, no trace. A result that arrives later is dropped |
| Cancelled after the answer is ready but before anything was played (`playback_chars` 0) | `cancelled`, `rolled_back: true`: the memory entry is removed and the turn's pending proposals are withdrawn (the approval log records `proposed` then `withdrawn`) |
| Interrupted while playing | `interrupted`. The full checked answer stays on screen and in the chat history. Session memory keeps only a prefix, marked `(interrupted)`: whole sentences played plus a proportional share of the one in progress. Proposals stay pending on screen |
| Played to the end | `done`; nothing changes |

`playback_chars` is an estimate from the speech engine's boundary events. It is **not** proof that the user heard or understood anything: it is described as "played" everywhere and never used as evidence of what the user was told. A second cancel changes nothing. Closing the stream early is treated as a cancel. A turn with a newer `generation` supersedes one still thinking, and a stale `generation` is refused (409).

## What is said aloud

`spoken_text` is derived deterministically from the checked answer: markup, tables and links removed; Indian amounts written the way people say them (`INR 1,39,000` becomes "1 lakh 39 thousand rupees"; plain numbers under a lakh are left to the speech engine); at most 3 sentences or about 55 words (about 20 seconds), then "Want details? The full answer is on screen." The screen always has the full answer. A change the turn proposes is **read back** ("Please check on screen: ...") so a mis-heard number is caught. `verify_spoken` recomputes every number said and checks every name against the answer; if the rewrite ever added one, the plain words are said, and failing that only "The answer is on screen." A model's tokens are never streamed raw: sentences come only after the whole answer has been checked, so the first audio waits for one model call plus the check.

A very short transcript (under two words) or one with `confidence` below 0.5 gets one clarifying question and nothing runs. Never more than one question per turn. A number, date or vendor name is never guessed from speech.

## Limits

| | |
|---|---|
| Recording | `audio/webm`, `audio/ogg`, `audio/mp4`, `audio/wav`, decided from the file's own bytes and checked against the declared type; at most 120 seconds and 5 MB; empty files refused. A live-recorded webm usually carries no length, so for those the 5 MB cap and the decoder's own length are what enforce the 120 seconds |
| Transcription | One at a time, a queue of 3 (then 429), a timeout of twice the audio length plus 30 seconds. The client's filename is never used: audio is written to a random name in a private folder (mode 0600, folder 0700) and removed straight away |
| Sessions | 3 open per person, idle timeout 90 s, cap 15 minutes, 20 turns a minute and 60 cancels a minute per session, 600 characters a turn, 50 turns remembered per session |
| Streams | 4 at a time |

## Privacy and effects

- **Where the audio goes.** Voice-note audio goes to this backend (the owner's machine in the current local setup) and is deleted after transcription. If this app is ever hosted elsewhere, that sentence changes and the consent text must change with it. Live mode sends no audio here. The browser's own speech service may process audio separately; the page must say so before first use and offer the local route where enabled.
- **Transcripts are untrusted text.** Same input checks, placeholder masking and injection scan as typed text. Logs hold masked text only. Errors in numbers, dates and names are expected: confirm them.
- **Voice can read, explain and propose. It cannot approve, send, spend, change the standing permission or the kill switch, add a recipient, or import a guest list.** A spoken "yes", "send it" or "approve" is just text (a test says it to a pending proposal and checks nothing happens).
- **Voice is never authentication.**

## Keeping the original recording (opt-in)

Off unless the owner sets `EVENTMATE_VOICE_KEEP_ORIGINAL=1`, and then only for notes the user chooses to keep. A kept note is checked like any upload, stored under a random name in `voicenotes/<plan_id>/` (0600), never put in a log, a trace or a prompt, never sent to a model, and removed when the note or the plan is deleted or after the retention period (`EVENTMATE_VOICE_NOTE_DAYS`, default 30; `purge-logs` removes expired ones). Past retention a note is unreadable even before it is purged. With it off the routes answer 503 "keeping voice notes is not turned on".

## Measured (fake model, this machine)

With a model that answers instantly, the server adds about **6 ms** from the turn starting to the first sentence event (p95 8 ms), and about 609 ms with a model that takes 300 ms per call over two calls. Rewriting an answer for speech takes under 1 ms. These are the server's own overhead with a fake model, not the latency a person will see with a real model, a real speech engine or a browser; no real-model latency has been measured. Over a real HTTP connection the `status` event arrived immediately and the sentences only after the model finished.

## Not verified, not built

- **Frontend behaviour** is unverified because it is not here: endpointing (the 700 to 900 ms pause rule), barge-in and echo handling, text-to-speech quality and browser support, the consent screen, the level meter. These need real-device tests.
- **The real transcription engine has not been run**, so there is no measured speed and no accuracy figure for English, Hindi or Marathi.
- **WhatsApp voice notes are not built.** Only in-app notes are. An unofficial WhatsApp gateway's account restrictions and terms of use were not checked; if wanted later, an incoming note would reuse the same transcription route, a user-approved text and the same click-only approvals.
- **Token streaming** (speaking while the model is still writing) is out of scope: it would need a design that keeps the answer check intact.
- A sentence that ends in a single capital letter ("option A.") is not split from the next; it is merged, never dropped.
