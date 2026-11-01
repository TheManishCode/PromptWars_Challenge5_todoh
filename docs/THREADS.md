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
- 2026-11-01 thread B: found worktree `event-agent-b` (`feature/wall-and-pentest`, thread C). I will not touch Phase 8 or wall files. Phase 8 depends on Phases 1 and 5 (and open owner answers), so thread C should start local-only per the prompt.
- 2026-11-01 thread B: claimed Phase 4 from the v6 prompt; branch created from 95aa06c. No overlap with 1.1 expected.
