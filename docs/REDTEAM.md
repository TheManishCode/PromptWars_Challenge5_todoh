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

## Pass 2: 2026-11-01, branch `feature/workspaces` (shared mode: per-visitor workspaces, delete, export, import)

Environment: the real Werkzeug server on a free port with the workspace registry, raw HTTP over sockets for the cookie and flood attacks, `_audit/redteam/attack_ws.py`; no outside network, model or mailbox.

### Findings fixed
| # | Attack or check | Result before | Fix | Test |
|---|---|---|---|---|
| R-8 | Delete a plan, then scan the workspace for its id, its ledger note, guest names and amounts | The id and the assistant's masked question and answer text stayed in `decisions` and `assistant_traces` (the plan's files were gone) | `delete_plan` removes the plan's lines from every log file, keeps the other lines, and writes a tombstone with only `{kind, id, deleted_at}`; the log entry about the deletion no longer carries the id | `tests/test_retention.py`, `tests/test_transfer.py` |
| R-9 | A plan the caller does not own, over the real server | Correct (404, nothing leaked), but the plan-miss body was `{"error":"not found"}` while the workspace-level miss carried `code: not_found`: two shapes for one answer | one body for every miss | `tests/test_limits.py`, `tests/test_two_workspaces.py` |
| R-10 | A flood of `GET /api/session` with no cookie, as an attacker rotating addresses would send, to fill every workspace slot | The cap held (503), but nothing ever freed the slots: empty throwaway workspaces stayed until the owner ran a command | the app sweeps empty workspaces idle for 6 hours (`EVENTMATE_WS_EMPTY_IDLE_HOURS`) every 10 minutes; one holding any data is never swept | `tests/test_workspaces.py` |
| R-11 | The two-workspace sweep itself, made to fail on purpose by sharing one `EventMate` between workspaces | It failed on 33 routes, so it does detect broken isolation | none needed; kept as the evidence the sweep means something | the mutant is described in ADR-062 |

### Defended
Cross-workspace reads, writes, deletes and approvals over HTTP (A's plan, ledger, state; B's own CSRF value for A's routes: 403); hostile cookies (empty, 100 KB, 2,000 cookies, duplicates, CR/LF, Unicode, quotes, SQL text, path text, NUL): never a 5xx, never a new workspace, no cookie set on a miss; a workspace deleted while four threads use it (only 200 and 404, never 5xx); a credential rotated while four threads use it (only 200 and 404); a zip bomb, a truncated zip and a 25 MB upload through the real server (422, 422, 413); the import grammar tests (30 hostile archive shapes, nothing written for any of them, see `tests/test_transfer.py`).

### Not fixed, honest gaps
- **Slot pinning by a patient attacker.** Each throwaway workspace that also saves one tiny plan is never swept, and 60 new workspaces an hour in all (5 per address) fill 1,000 slots in about 17 hours. The limits slow this and `workspaces_total` on the owner status route shows it, but nothing stops it. Real fixes are owner decisions (idle expiry of workspaces that hold data, which needs a recovery route first; a proof of work or other check before a workspace is made; a higher cap).
- **Cookie planting.** A site on a sibling name or a network attacker on plain HTTP could plant their own credential in a victim's browser, so the victim's data lands in the attacker's workspace. The cookie is `Secure`, `SameSite=Strict` and host-only, so only an attacker who controls a response over HTTPS for this host, or the browser, can do it; HSTS from the proxy matters (docs/HOSTING.md). Not tested.
- **No recovery, no device sessions.** Clearing the browser loses the workspace unless the person exported first.
- **Memory-only state** (pending proposals, voice sessions, the answer cache) is lost when a workspace is dropped from the 128-entry memory cache; durable approvals are the durability phase.
- **The wall, a real browser, a real proxy, a real host and load** were not part of this pass.

## Pass 3: an outside verification of d55671a (reports `eventmate_verification_final` and `approval_gate_addendum`), 2026-11-01

Someone else ran the suite and probes on a fresh clone of `d55671a` (Python 3.10, solvers built locally, outbound network blocked except the free OpenRouter route for 24 live probe cases). Their numbers: 3,233 passed, 19 failed, 8 skipped. This section says what each finding led to. The reports are the verifier's; I did not repeat their live model probes (no key was used).

| Their finding | What it was | What was done | Test |
|---|---|---|---|
| Bug 1: 18 wall tests 500 on 10k to 30k deep JSON bodies | Real, and version dependent. `wall/routes.py` `_body` did not catch `RecursionError`, which the JSON parser raises on 3.10 (3.14, which the suite here runs on, returns without it, so the same tests passed here) | `_body` catches it; the app also answers any uncaught `RecursionError` with 400 `bad_request` instead of 500 | `test_a_parser_that_raises_recursion_error_is_still_a_400` (forces the exception on every Python version; it passes through either layer, so it does not show the wall change alone) |
| Bug 2: the live assistant showed raw JSON ("Here is what I found: - {\"passages\": [...]}") | Real. The degraded-answer path printed tool results verbatim | tool results are turned into words (`_readable`); an empty search or an error result is now the honest "could not find that", not an empty list | `test_a_degraded_answer_is_words_not_raw_json`, `test_an_empty_search_is_an_honest_no_not_an_empty_list`, `test_readable_turns_tool_json_into_plain_text` (the first two fail without the fix) |
| Bug 3: `alley_van.glb` whitelisted but not committed | Real in `d55671a`; the optimised models are committed on `feature/room-vending-lighting` and listed in the vendor manifest; `road/car/barrier.glb` remain absent and are declared optional | see ADR-068 | `test_arcade.py`, `test_vendor_manifest.py` |
| Approval gate addendum: T1 to T7 all held | Nothing to fix. T1 to T5 were already tests; T6 and T7 existed only in their scripts | pinned: twelve simultaneous approvals send once; six approvals racing six denials leave one consistent state and a denied draft never sends | `test_twelve_simultaneous_approvals_send_exactly_once`, `test_approve_and_deny_racing_leave_one_consistent_state_and_a_denied_draft_never_sends` |
| Gap: real tool-output poisoning untested | A tool result can carry an injection that a real model might obey | a poisoned tool result plus an obedient model: the payment stays a pending proposal, nothing is applied, and the attacker's address and link never reach the user | `test_a_poisoned_tool_result_followed_by_an_obedient_model_changes_nothing_and_leaks_nothing` |

Found while re-running the suite after those fixes (not in their report): the way back from a focus view was a fixed pose recorded when the view opened, so rotating the screen during that move left the lens on the new orientation and the camera on the old pose, with the wall board off screen. The way back to the wide shot is now recomputed every frame, so it lands on the current orientation's shot (`test_rotating_the_screen_while_the_camera_is_still_moving_back_from_a_focus_view_lands_on_the_new_orientations_shot`, which fails on the old code). A related test had passed an hour earlier by timing luck.

Left open, as the verifier also lists: per-class live attack depth is about one or two vectors; cross-turn jailbreaks (their harness was stateless, and so is this test); email, places, search and weather paths; the rate-limit thresholds and recovery windows of the wall are not characterised; multi-machine file locking. One point worth stating: an obedient model can still make the assistant print a short injected phrase such as "PWNED" if it cites valid evidence, because the answer check forbids new numbers, names, links and addresses, not arbitrary words. That is text only and cannot act, but it is a limit, not a defence.
The verifier ran Python 3.10 although the project declares 3.12 or later; the 3.10-only failure above shows that other version differences may exist, and nothing else was run on 3.10 here.
