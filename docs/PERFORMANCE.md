# Performance: what was measured, on what, and the budget

All numbers are from one development machine (Linux, software rendering for the browser, loopback network). They show shape and regressions, not capacity for a given host.

## Server (`tools/loadtest/soak.py`, real waitress server, shared mode, synthetic visitors who pause 0.8 to 2.5 s between actions)
| Run | Requests | Errors | p50 | p95 | p99 | Memory start -> end | Open files | Threads |
|---|---|---|---|---|---|---|---|---|
| 25 visitors, 60 s | 919 | 0 (all 2xx) | 4.2 ms | 145 ms | 906 ms | 173 -> 175 MB | 8 -> 8 | 25 -> 25 |
The slow tail is plan creation (the planner and two solvers run per plan; about 270 ms at p95 in an earlier 6-visitor run). Reads and ledger writes are a few milliseconds. The same test runs in CI for 60 s (20 visitors) and fails on any 5xx, p95 over 3 s, more than 5% rate limited, or growth in memory (35%), files (25) or threads (10). A 30-minute run is `python tools/loadtest/soak.py --seconds 1800 --visitors 20`; it has not been run here.
Budget: p95 under 3 s for any route at 20 concurrent visitors (checked in CI); p95 under 250 ms for everything except plan creation (not checked automatically; the table above is the baseline).

## Front end (assets)
- The room's models were 1.3 GB as downloaded; the originals that ship in the page were 49 MB for the cabinet alone, with 4096 x 4096 textures (89 MB of GPU memory each).
- Every model now ships twice, produced by `tools/assets/optimize_models.sh` (glTF-Transform: resize, WebP, prune, dedup; the originals are untouched): "high" (textures at most 2048) 16.1 MB for the whole room, "low" (at most 1024) 9.8 MB; the cabinet went from 49 MB to 1.1 MB (high) and 364 KB (low).
- `detectTier()` in `arcade3d.js` picks "low" for a phone-sized screen, 2 GB or less of memory, or Save-Data; `?tier=low|high` forces it. Small devices draw at most 1.25x pixel ratio, with no bloom pass and smaller shadow maps.
- Models are served with a versioned URL and an immutable one-year cache; the page itself is `no-cache`, so a changed file is a new address.
- The big cat vending machine (54 MB, CC BY-NC-ND: no derivatives allowed, so it cannot be optimised) loads only on request.
Frame time on a real phone is the front-end thread's measurement: see `docs/ARCADE_UI.md`.

## Not measured
Throughput beyond 25 concurrent visitors, behaviour with thousands of stored workspaces, a real network with latency, and a real proxy with TLS in front.
