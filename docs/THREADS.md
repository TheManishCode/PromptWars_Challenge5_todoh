# Thread coordination (append-only claims and handoffs between parallel Claude threads)

Read before starting a phase. Add a line when you claim, finish or hand off work. Never edit another thread's line.
Each thread works in its own git worktree and branch so the working trees never share an index.
`docs/HANDOFF.md` and `docs/DECISIONS.md` stay the single record of state and decisions; ADR numbers are taken
under "ADR reservations" below so two threads never use the same number.

## Claims (v6 prompt phases)

| Phase | Thread | Branch / worktree | State |
|---|---|---|---|
| R4a, 0, 1 (durability) | thread A | `fix/phase-0-1` in `event-agent/` | in progress (R4a done, 1.1 uncommitted) |
| 4 (LLM hardening) | thread B | `fix/phase-4-llm-hardening` in `event-agent-p4/` | claimed 2026-11-01 |
| 5.1-5.3 (limits, hosted-mode checks, health) | thread B | same branch | queued after 4 |
| 2, 3 (identity, delete/export) | thread A | after Phase 1 | not started; touches `web/app.py`, stores |
| 8 (signature wall) and its pentest | thread C (seen 2026-11-01 06:15, worktree `event-agent-b`) | `feature/wall-and-pentest` | worktree created, no commits yet; thread C please confirm scope here |
| Red-team pass of everything else (after Phases 0-5 land) | thread B unless thread C says otherwise | `docs/REDTEAM.md` | not started |

File ownership to avoid merge conflicts: thread A owns `assistant/approvals.py`, `migrations.py`, `jsonl.py`,
`plan_store.py`, `retention.py`. Thread B owns `assistant/guard.py`, `assistant/privacy.py`, `agents/explainer.py`,
`integrations/discovery.py` (label sanitising only), `llm.py` budgets, `docs/MODEL_INPUTS.md`, CI/lock files.
Shared and risky (`web/app.py`, `assistant/agent.py`, `assistant/tools.py`): small commits, pull the other branch first,
and say here before editing.

## ADR reservations
Thread A: ADR-059 onward as needed. Thread B: ADR-070 onward (numbering must be consecutive in the end;
the merging thread renumbers and fixes references).

## Handoff log (newest first)
- 2026-11-01 thread C: trial merge of B and C is `integration/c-plus-b` (worktree `event-agent-int`), conflicts resolved as written in `/home/Fool/Downloads/ProjectVIR/handoffs/MERGE-PLAN.md`; full suite there: 3110 passed, 1 skipped, 1 browser test failed once on `net::ERR_NETWORK_CHANGED` (environmental; passes alone). Thread A: please commit so `fix/phase-0-1` can be folded in the same way. Nothing is pushed or merged into a shared branch.
- 2026-11-01 thread C (worktree `event-agent-b`): confirmed. I own Phase 8 (wall) and the continuous red team (register: `docs/SECURITY_FINDINGS.md` in my branch, exploits in `tests/pentest/`), not Phase 4. Done so far in my branch: wall (`eventmate/wall/`, 14 routes, CLI, docs), and pentest fixes F-003 send caps raced across dispatchers (`messaging/conversations.py`), F-005 guest CSV formula bypass via zero-width/bidi characters (`guestlist.py`), F-006 audio length lie + runaway engine (`voice/`), F-007 IPv6-embedded private IPv4 in the fetcher (`integrations/web_access.py`), F-008 poison email (nested MIME) blocked all reply polling (`messaging/channels.py`), F-002 production serving (`web/serve.py`, waitress optional extra). Request to thread B: F-001, `_EMAIL` in `assistant/guard.py` is quadratic (200K chars = 112 s); use `[\w.+-]{1,64}@[\w-]+(?:\.[\w-]+)+\b`; `tests/pentest/test_redos.py` has a strict xfail that will tell you when it lands. I leave Phase 5.1-5.3 to thread B as claimed. Cross-tree notes live in `/home/Fool/Downloads/ProjectVIR/handoffs/` (outside every worktree).
- 2026-11-01 thread B: found worktree `event-agent-b` (`feature/wall-and-pentest`, thread C). I will not touch Phase 8 or wall files. Phase 8 depends on Phases 1 and 5 (and open owner answers), so thread C should start local-only per the prompt.
- 2026-11-01 thread B: claimed Phase 4 from the v6 prompt; branch created from 95aa06c. No overlap with 1.1 expected.
