# Red-team record

What was attacked, what broke, what was fixed and what was not. Append-only: a new pass adds a dated section; a finding is closed by naming its fix and test, never by deleting it.
This is a record of attacks I actually ran, not a certificate. "Defended" means the attack I tried did not work; it does not mean no attack works.
Harnesses are kept outside the repository (`_audit/redteam/`: `attack_web.py`, `attack_state.py`, `attack_mime.py`, `redos_sweep.py`); every fix has a regression test inside it.

## Pass 1: 2026-11-01, branch `fix/phase-4-llm-hardening` (single-user local mode; the identity phase is not in this branch)

Environment: Python 3.14.8, the real Werkzeug server on a free port with temporary directories, a real plan built with Fast Downward and VAL present. No real mailbox, model or network service was contacted.

### Findings fixed

| # | Attack | Result before | Impact | Fix | Regression test |
|---|---|---|---|---|---|
| R-1 | A compromised model says "Done, I recorded the payment" when only a proposal was filed | The false claim reached the user (nothing was applied) | The user is misled about state | `_verify` refuses completion claims on a turn that filed proposals; code appends "Waiting for your approval (nothing has changed yet)" | `tests/test_llm_boundary.py` |
| R-2 | A provider or library error that echoes the API key | The explainer wrote it to the LLM trace log | Key in a log file | `redact_secrets` on every logged exception text | `tests/test_secret_hygiene.py` (fails without the fix) |
| R-3 | One long string of the right shape sent to `mask_pii`, `Placeholders.mask`, `_LINK_FULL`, the speech and price patterns | 20,000 characters took 5 to 9 seconds (quadratic regexes: `_EMAIL`, `_LINK_FULL`, `fake_authority`, `_HEADING_OR_BULLET`, `_PRICE_WORDS`, and my own new `_EMAIL_SPOKEN`) | CPU denial of service; reachable through inbound vendor mail (4,000 characters, about 0.4 s per message per pattern) and tool results | Patterns bounded and anchored | `tests/test_regex_time.py` sweeps every compiled pattern in the package against 28 hostile shapes |
| R-4 | A deeply nested MIME message (3,000 or more nested multiparts, or nested `message/rfc822`) | `RecursionError` escaped the parser and aborted the whole inbox poll; the message stays unread, so every later poll fails the same way | One email blocks all vendor replies, stop requests and bounces | Header lines counted before parsing; too many, or any unexpected parser failure, becomes a headers-only "could not be read safely" notice; `_parse` can no longer raise | `tests/test_redteam_mail.py` |
| R-5 | An HTML mail body made of unclosed `<script>` tags | 10 seconds of CPU per message (regex backtracking), 256 KB cap | Poll stalls | Replaced with a linear `HTMLParser` text extractor that keeps paragraph breaks | `tests/test_redteam_mail.py` |
| R-6 | 300 half-open connections to the server | All 300 stayed open for ever; no cap | Thread and memory exhaustion by anyone who can reach the port | `eventmate/web/server.py`: idle timeout (30 s) and a connection cap (128), both configurable | `tests/test_redteam_server.py` |
| R-7 | Escape sequences, OSC title and clipboard sequences and bidi controls in an inbound email, a web page label or a weather note, printed by the CLI | Printed raw (only newlines were replaced) | Terminal injection against the operator: lines overwritten, fake output, window title, clipboard on some terminals | `strip_terminal` on every CLI line that prints third-party text | `tests/test_redteam_terminal.py` |

### Defended (the attack did not work)

- Host header: foreign, suffix-spoofed (`127.0.0.1.evil.com`), `@` tricks, duplicated, absolute-URI request line: all refused with 400.
- CSRF and content type: no token, wrong token, `text/plain`, urlencoded form, foreign `Origin`, `Origin: null`: refused (403 or 415). Quota GET routes refuse without the header. No CORS headers are ever sent; a preflight is not granted. TRACE is refused (403).
- Path traversal: nine static-file variants (`..`, encoded `..`, double slash, `%2f`, backslash, NUL, `vendor/../app.py`, the manifest) all 404 and leak nothing; eleven odd plan ids (`..`, `%2e%2e`, a 5,000-character id, CR/LF injection, bidi, `con`, double-encoded) are refused with 400/404 and no header injection.
- Framing: a 3 MB chunked body to a JSON route (400), content-length plus transfer-encoding smuggling (one response only), negative content length (400).
- JSON: 30,000-deep nesting (400), 12,000-deep objects (413), a 4,400-digit integer (400), `1e999999`, `NaN`, invalid UTF-8, a BOM, duplicate keys, NUL escapes and a 60 KB string: none caused a 500 or a traceback.
- Error pages: no stack trace, file path or library name in 404 and unknown-route bodies.
- Races: 16 parallel replans of one plan leave a consecutive version chain (one succeeds, fifteen report failure, nothing is corrupted); 30 parallel approvals of one proposal apply it once.
- File modes: plan versions are 0444, logs and stores 0600, directories not world-accessible (umask 022).
- Web fetcher SSRF: connections are pinned to the address that was checked, so a second DNS answer cannot redirect it (read from source and covered by the existing fake-resolver tests; no live rebinding server was used).
- Model boundary: sixteen obedient-model attacks in `tests/test_llm_boundary.py` (unlisted tool, hostile arguments, foreign plan id, replay, stale approval, flooding, forged approval text).
- Hostile MIME other than nesting: 10,000 headers, a single header of 250 KB, encoded-word bombs, an unknown charset, invalid base64, NUL bytes, bad UTF-8, 2,000 attachments, 5,000 addresses in `From`, an overflowing date: all parsed or dropped in under 0.6 s.

### Not fixed or not tested (honest gaps)

- **No identity or isolation.** Every route serves every plan to anyone who can reach the port (single-user local design). The workspace phase (v6 Phase 2) is another thread's work and is the largest open item for any hosted use. Do not expose this server to other people until it lands.
- **Slow trickle.** The new idle timeout does not stop a client that sends one byte just inside it. A reverse proxy with header and body timeouts is required in front of anything exposed.
- **Planning CPU.** 40 simultaneous plan creations all succeeded in 4.7 s: planning is cheap here (about 0.1 s each), but there is no concurrency cap beyond the per-address write rate limit (60 a minute). Behind a proxy, all clients share one address.
- **Reads are not rate limited** (300 sequential reads of a plan took 0.7 s). Harmless at this cost per read, but unlimited.
- **Transcriber isolation (design gap, read from source, engine not run).** The optional local engine runs in a thread. When a job exceeds its time limit the caller gets a 504, but the thread keeps decoding (a thread cannot be killed), so one crafted or simply slow clip can keep the single worker busy and make the next jobs time out behind it. The container parsers themselves are safe: a lying header can only shorten the time limit, the 5 MB cap and the 120 second check bound the rest, and the mp4, ogg and wav walkers always advance. The proper fix is to run the engine in a child process that is killed and restarted on timeout; it is not built, because it cannot be verified without the real engine and the feature is off by default.
- Not attacked yet: polyglot audio fed to a real decoder, the guest CSV beyond the existing tests, the transcriber process, JSONL stores with hostile content on disk, symlink planting in the store directories, timing side channels, TLS and proxy configuration (nothing here terminates TLS), the CLI argument surface, the vendored JavaScript. A browser-level XSS pass was not done in this pass.
- The vendor-email "stop" and bounce matching is documented as forgeable (ADR-049); not re-tested.
- Real model behaviour was not exercised; every model-facing test uses a scripted fake.
