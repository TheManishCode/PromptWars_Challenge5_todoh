# Problem statement coverage

Problem statement (as quoted by the project owner): **"Create a GenAI-powered planning assistant that helps users organize weddings, birthdays, and community events with budgets, timelines, vendor checklists, and personalized ideas."**

This file maps each clause of the statement to the code that implements it and points at related tests, so a reviewer can check coverage without reading the whole tree. The test files are references for where to look; they were not all re-run for this document.

| Requirement | Where it is implemented | Related tests |
|---|---|---|
| **GenAI-powered assistant** | Models are used for wording (brief reading, ideas, answers, explanations): `eventmate/assistant/`, `eventmate/agents/manager.py`, prompts versioned in `prompts/`. The stated design rule is that facts (prices, availability, validity) come from code and data rather than model output; see `docs/GENAI.md` and `docs/MODEL_INPUTS.md` | `tests/test_assistant.py`, `tests/test_idea_guard.py`, `tests/test_guards_precise.py` |
| **Weddings, birthdays, community events** | `EventType.WEDDING / BIRTHDAY / COMMUNITY` in `eventmate/schemas.py`; community profiles in `eventmate/planning/community.py` + `eventmate/data/community_profiles.json` | `tests/test_community.py`, `tests/test_community_checks.py`, `tests/test_community_items.py`, `tests/test_intake_community.py` |
| **Budgets** | Allocation with contingency and an up-front reality check: `eventmate/agents/budget.py`, `eventmate/pricing/reality.py`; ledger with provenance and expiry: `eventmate/pricing/ledger.py` | `tests/test_budget.py`, `tests/test_vendorscore.py` |
| **Timelines** | CP-SAT scheduling with a PDDL cross-check (Fast Downward + VAL): `eventmate/agents/scheduler.py`, `eventmate/agents/planner.py` | `tests/test_scheduler.py`, `tests/test_planner.py`, `tests/test_planner_select.py` |
| **Vendor checklists** | Constraint-checked shortlists with per-vendor reasons: `eventmate/agents/vendor.py`; a separate validator pass before a plan is shown: `eventmate/agents/validator.py`; outreach state machine (draft, approve exact text, send, track replies): `eventmate/messaging/` | `tests/test_vendor.py`, `tests/test_external_vendors.py`, `tests/test_validator.py`, `tests/test_channels.py`, `tests/test_conversations.py` |
| **Personalized ideas** | `eventmate/planning/ideas.py`: template ideas work with no model; model ideas pass a guard intended to block invented prices, vendors, availability or cultural claims; suggestions are labelled as suggestions, not verified offers | `tests/test_ideas.py`, `tests/test_idea_guard.py` |
| **Organize over time** | Plan versions with diffs on replan (`eventmate/agents/replanner.py`), hash-checked plan store, durable approvals and jobs (`eventmate/followthrough/`) | `tests/test_replanning.py`, `tests/test_fuzz_replanning.py`, `tests/test_plan_store.py`, `tests/test_plan_consistency.py`, `tests/test_approvals_durable.py` |

## Design intent for validity

The intended rule in the code is that a plan version is only stored after the validator's checks pass (`eventmate/agents/validator.py`), and that a stated need that cannot be checked from data is marked **Needs verification** rather than called verified. This is the design as written; treat it as a claim to test, not a guarantee.

## Honest limits (also in README)

Vendor data is synthetic seed data; email has been tested with fake SMTP/IMAP only. These are stated in the README status note and in `docs/HANDOFF.md`.
