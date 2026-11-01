# Red-team probes (re-runnable)

Exploits that must never work again are regression tests in `tests/pentest/` (`make redteam` runs them). These are the throw-away probes behind the red-team passes, kept so the
evidence can be reproduced. Run from the repository root with the project's Python (and `EVENTMATE_FD`/`EVENTMATE_VAL` set if the solvers are not in the default place).

| Script | What it does | Expect |
|---|---|---|
| `live_attacks.py` | starts the real built-in server and attacks it over raw sockets: Host header tricks, header and URL bombs, chunked oversize bodies, CL+TE, 1,500 half-open connections | every malformed request refused; the built-in server holds the half-open connections indefinitely (F-002: use waitress) |
| `probe_parsers.py` | 3,000 hostile strings through ten parsers of third-party text | only deliberate refusals (`GuestListError`, `RequestParseError`) |
| `probe_redos.py` | times regex-bearing paths on adversarial input (`REDOS_SIZE`, default 60,000 characters) | anything over 0.25 s is listed; `guard.mask_pii` is slow until F-001 is fixed |

None of this proves the system is secure; it shows these specific attacks and what happened.
