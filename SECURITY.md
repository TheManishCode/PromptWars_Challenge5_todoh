# Security policy

## Reporting a vulnerability
Email the owner (address in the repository profile) with the steps to reproduce; do not open a public issue for something exploitable. Expect an acknowledgement within three working days. There is no bounty. Please use synthetic data only and do not test against someone else's deployment.

## What is defended, and how it is checked
| Area | Control | Evidence |
|---|---|---|
| Visitor isolation | one workspace per visitor, credential in an HttpOnly Secure cookie (only its hash is stored), a route sweep generated from the route table | `tests/test_workspaces.py`, `docs/WORKSPACES.md`, privacy canary sweep in `docs/REDTEAM.md` |
| Money and sending | proposals only; a human approves an exact content hash; guest workspaces cannot send; at-most-once under crashes and races | `tests/test_conversations.py`, crash-point sweep, `docs/DURABILITY.md` |
| Model boundary | the model has no authority; text is normalised before it is screened; answers may state nothing the evidence did not supply | `tests/test_llm_boundary.py`, `tests/test_properties.py` |
| Abuse limits | writes per address and workspace, new workspaces per hour, model calls and tokens per workspace, request body size; the caller address comes from `X-Forwarded-For` only from a trusted proxy | `tests/test_limits.py`, `tests/test_properties.py` (spoofing property), `tools/loadtest/soak.py` |
| Supply chain | hash-locked installs, blocking `pip-audit`, image scan (Trivy), secret scan over history, pinned CI actions, SBOM artefact, vendored front-end files hash-checked | `.github/workflows/ci.yml` |
| Data at rest | private directories (0700), plan versions read-only, deletion removes content and leaves a minimal tombstone | `docs/PRIVACY.md`, `tests/test_workspaces.py` |

## Known limits (stated, not hidden)
One process and one disk: no high availability. The file store has not been exercised across several machines. The behavioural evals (20 cases: prompt leaks, role and urgency pressure, encoded and translated injections, cross-workspace and personal-data requests, unapproved sends) pass on the no-model path here but have NOT been run against a real model by the builders (`prompts/assistant.lock.json` says `not_run`); an outside verifier ran 24 live probe cases once. Run them with your own key through the live-evals workflow before relying on the model-facing claims. An obedient model can still print a short injected phrase; the answer check blocks new numbers, names, links and addresses, not arbitrary words. No independent penetration test has been done; the red-team passes in `docs/REDTEAM.md` were done by the builders and one outside verifier.
