# Third-party material

What is in this repository that is not ours, and the terms it comes under. The 3D room's models and libraries (several under non-commercial Creative Commons licences) were removed with the room; their credits travel with the copy kept outside the repository.

## Served to users (`eventmate/web/static/`, listed again in `LICENSES.txt` there)

| File | What | Licence |
|---|---|---|
| `landing-instrument-serif.ttf` | Instrument Serif, Copyright 2022 The Instrument Serif Project Authors (https://github.com/Instrument/instrument-serif) | SIL Open Font License 1.1 |
| `landing-inter-400.ttf`, `landing-inter-500.ttf` | Inter, Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter) | SIL Open Font License 1.1 |

The fonts are served from this origin (the page's CSP allows no third party). The licence lets them be used, embedded and redistributed with the notice, and not sold on their own.

**Not in the repository: the landing hero poster and video** (`landing-poster.jpg`, `landing-hero.mp4`). They came from `designerstephen.github.io/public-assets` and their licence is not confirmed, so they are git-ignored and the page works without them (a plain gradient shows). Confirm the right to publish them before adding them back.

## Used by the tests only (never served)

- `tests/vendor/axe.min.js`: axe-core 4.14.0 by Deque Systems (MPL-2.0), injected into the browser over the DevTools protocol; its hash is recorded in `tests/vendor/MANIFEST.txt`.

## Ideas read from open-source projects (re-written here, no code copied)

Each is also credited in the docstring of the module that uses it, and in the ADR that adopted it.

- jiayizx/async_planning (MIT): the CP-SAT model design, and the state-aware repair and delta ideas (`cpsat.py`, `planning/exec_state.py`, `planning/delta.py`).
- CopilotKit/openmuse (MIT): job leases, the monitor shape and the page comparison (`followthrough/jobs.py`, `followthrough/watches.py`, `followthrough/pagediff.py`).
- langgenius/dify, guardrails-ai/guardrails (annotation reply, sensitive-word moderation, re-ask once): `assistant/`.
- Fast Downward 24.06.1 and VAL are built and run as separate programs (`solvers/`, git-ignored); they are not part of this repository.
