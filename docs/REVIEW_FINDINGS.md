# Reviewer findings ledger (reviewer-todoh)

| ID | Sev | Milestone | Finding | Evidence | Status |
|---|---|---|---|---|---|
| R-001 | BLOCKER | L1 | Borrowed art (plan-story-*, plan-reference-*) in STATIC_FILES, served when on disk | web/base.py:40-42; ui/CHARACTER-SOURCE.md | fixed 0e8bcaa (inline authored SVG; verified) |
| R-002 | MAJOR | L1 | favicon.svg, plan-instrument-serif-italic.ttf unattributed; landing-poster/hero (unconfirmed licence) on served list | test_static_provenance (069cab8) | fixed 0e8bcaa |
| R-003 | BLOCKER | L0 | docker build context would bake eventmate/plans, logs, wall_data (owner data) into the image | .dockerignore at 605a917 | fixed aa733ef (.dockerignore); image built from clean tree verified |
| R-004 | MINOR | L0 | approvals race test used real clock; expired once calendar passed T0+7d | baseline 1 fail; fad36e1 | fixed fad36e1 |
| R-005 | MAJOR | L0 | plan-story-* git-ignored but not dockerignored, still served and requested by plan.js | c7b17eb | fixed 50bf2a4 |
| R-006 | BLOCKER | L0 | clean clone red: 3 tests need untracked borrowed art | gate c7b17eb: 3 failed / 5009 passed | fixed 0e8bcaa; clean-tree gate 6f1d7d5 |
| R-007 | MAJOR | L0/L4 | check_image leak step is vacuous (looks in /app, package lives in site-packages) | negative control: planted leak.jsonl, check said ok | fixed 50bf2a4; reviewer negative controls (2 plants) FAIL as expected |
| R-008 | MAJOR | L5 | /plan a.brand label-content-name-mismatch (serious) | review/l5-plan-axe 1381b50 | fixed 6f1d7d5 |
| R-009 | MINOR | L5 | /plan .continuum outside landmarks (moderate) on every room | review/l5-plan-axe 1381b50 | fixed 6f1d7d5 |
| R-010 | MINOR | L2 | test_assistant.py:620 asserts '98765' absent from whole JSON; uuid4 id collided (p~5e-5/run). Masking correct. | gate 6f1d7d5 | fixed 63b8ccb |
| R-011 | MINOR | L4 | reference nginx `Host $host` drops port; Origin check (app.py:232) then refuses every write on non-443 ports | review/l4-hosted-journey e525c9c, first run POST /api/intake 403 | fixed 31bca2a ($http_host; journey runs the shipped file unmodified) |
| R-012 | MAJOR | L4 | /plan 'Build the plan' enabled before any vendor chosen; server 400 says 'no confirmed quote' after user confirmed 14 lines; single quote hides 'Plan with this vendor' until 'done waiting' | e525c9c run log | fixed 31bca2a; reviewer mutant (old gating) killed by hosted journey |
| R-013 | MAJOR | L3 | shared earlier-quote pool never written in any shipped config (shared: no mail, pasted excluded; local: no shared_dir) | hosted journey basetemp: no shared/ dir after 2 confirmed quotes | documented dormant 9886515; owner decision in BLOCKED.md |
| R-014 | MINOR | L3 | shared pool keeps exact quoted_for_date: event date+size disclosure to anyone knowing the vendor | shared_quotes.py SharedQuote.quoted_for_date | fixed 9886515 (month only, server-side flag) |
| R-015 | MAJOR | L2+ | footer invites STOP; STOP with signature / please / thanks not honoured (7 shapes) | review/l2-stop-shapes 484084c | fixed 3290cba (word list, first line) |
| P-001 | PROCESS | all | two accidental `git add -f` sweeps (.env; pycache+logs+plans+sqlite) caught+purged before push | reviewer verified purge both times | fixed fe6e294 (tools/pre-commit, make hooks, installed) |
| R-016 | MINOR | L4/L5 | flake class: test_retention.py:239 '4321' in tombstone (hex id/microseconds), test_approvals_durable.py:219 b'424242' in sqlite blob | sweep | fixed 7d080a4 |

## Verified passing (with evidence)
- c7b17eb: ruff, pyflakes, node --check, bandit -ll (0), gitleaks history + tracked (0), .env untracked, docs tracked.
- c7b17eb: tests/test_proxy_deploy.py + tests/pentest/test_proxy_smuggling.py, 10 passed (nginx, TLS).
- c7b17eb: tools/check_image.sh on clean-tree image: all checks passed (but see R-007).
- c7b17eb: /plan keyboard path to Yes passes.
- 6f1d7d5 PASS L0+L1: pytest 5018 passed / 4 skipped (opt-in: Docling x2, live flights, voice) / 1 failed (R-010, root-caused, test defect); ui build reproducible; fresh image check all ok.
- .env accidental local commit: verified purged (blob absent, no reachable .env, unreachable tree has no .env).
- e525c9c: hosted journey (shared mode + shipped nginx/TLS + Chromium) / -> /plan -> built plan, nothing sent: PASS (with R-011 substitution in fixture).
- 857281e PASS L2: pytest 5025/4 skipped/0 failed clean tree; mutants M1-M5 killed.
- 3290cba PASS L3+R-015: pytest 5042/4 skipped/0 failed clean tree; mutants M1-M3 (privacy) killed.
- 7008ebd: Chromium+Firefox x 390/820/1440 on /, /app, all /plan rooms: 6/6 pass (axe, overflow, console).
- 3882561 L4+L5: clean-tree gate PASS 5050/4 skipped/0 failed; fresh image check all ok; R-012 browser mutant killed.
