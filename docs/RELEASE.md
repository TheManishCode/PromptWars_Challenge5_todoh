# Release notes and checklist: release candidate 1

## What is in this release
- Everything since `main`: the signature wall, red-team fixes (F-001 to F-021, outside verification, crash/fault/privacy sweeps), durability (schema upgraders, integrity scan, repair, plan hash chain), workspaces, abuse limits, the arcade room as the only interface, accessibility (keyboard path, text-only mode, contrast, reduced motion), tiered 3D models.
- Operations: `/metrics`, alert rules, dashboard, Dockerfile and compose stack, CI with a Python 3.12 to 3.14 matrix, coverage gate, blocking dependency audit, soak and container jobs, SBOM artefact, image scan.
- Planning data: a nine-city demonstration catalogue so weddings, birthdays and community events plan end to end.

## Before you deploy (owner decisions and secrets this release cannot make)
1. **Licences.** `vend_retro`, the cat machine and `wall_mural` are CC BY-NC(-ND): fine for a private demo, not for public or commercial hosting. Replace them or get permission (`docs/ATTRIBUTIONS.md`).
2. **Secrets.** Copy `deploy/env.example` to `deploy/.env`: a 40-character `EVENTMATE_SECRET`, the public URL and host name, the proxy's network, an owner token (also written to `deploy/prometheus/owner_token`). Model keys only if you want the model features (`NVIDIA_API_KEY`, `OPENROUTER_API_KEY`).
3. **TLS and proxy.** Put a TLS-terminating proxy in front; it must set `X-Forwarded-For` and `X-Forwarded-Proto` and its network must be in `EVENTMATE_TRUSTED_PROXY` (`docs/HOSTING.md`).
4. **Alert routing.** The rules exist; connect Prometheus to Alertmanager or your cloud's paging.
5. **Real data.** The catalogue is synthetic. Real vendors, prices and availability need an integration.

## Deploy, check, roll back
- Deploy: `docker compose -f deploy/docker-compose.yml up -d --build`. Check: `/readyz` is `{"ready": true}`; Prometheus target `todoh` is up; the container is `healthy`.
- Before a release: `python tools/loadtest/soak.py --seconds 1800 --visitors 20` (and `make check`).
- Back up: `docs/RUNBOOK.md` (backup and restore commands, verified manifest). Roll back by starting the previous image on the same volume; stored files are versioned and older readers refuse a newer file by name rather than misreading it.

## Not done in this release (honest list)
- No independent penetration test; the behavioural model evals have not been run against a real model (the `live-evals` workflow does it with your key).
- One process on one disk: no high availability, no multi-machine file locking, no zero-downtime upgrade.
- Only venue, caterer and photographer are planned vendor categories.
- Frame times on real phones were measured on one device (`docs/ARCADE_UI.md`); no screen-reader session with a person; no Lighthouse run.
- The 30-minute soak and a Grafana start-up were not run here; the CI and live-evals workflows have not run on GitHub (they were linted with actionlint).
