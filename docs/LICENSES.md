# Licences: what this project uses, and what each licence asks of it

Audit of 2026-11-04. Every upstream licence below was read from its own repository (GitHub's licence endpoint, then the file itself where the answer was not a standard identifier);
package licences were read from PyPI metadata for the exact versions the lock files and a clean install resolve. This is research, not legal advice, and it does **not** choose a licence
for to;doh itself: that is the owner's decision, listed first.

## 1. This project has no licence yet (owner decision)
There is no `LICENSE` file. With none, the code is "all rights reserved": nobody else may use, change or redistribute it, whatever the README says. Nothing found below limits the choice:
no copyleft code is linked or copied into it (section 3), the one GPL program ships as a separate program, and no upstream's terms reach code it did not copy (section 5).
Whoever chooses should also decide who may contribute under what terms (`CONTRIBUTING.md` says nothing about it).

## 2. What the container image ships, and what that obliges
| Component | Licence | What it asks when the image is passed on | Where it is met |
|---|---|---|---|
| Fast Downward 24.06.1 (`/opt/solvers/downward`) | GPL-3.0-or-later | Pass on the licence text and the **Corresponding Source** of the binary, with it or by a written offer | The image now keeps `src/` and the build scripts (3 MB) beside the binary, with `LICENSE.md` and `/opt/solvers/NOTICE.txt` naming the exact commit (`1eef26b…`). Before this audit the Dockerfile deleted `src/`, so the image carried a GPL binary without its source. |
| VAL (`/opt/solvers/VAL`) | BSD-3-Clause | Binary redistribution reproduces the copyright notice, conditions and disclaimer | The image now copies VAL's `LICENSE` and names the commit. Before, only the binaries were copied. The Dockerfile also cloned VAL's moving `HEAD`; it is pinned to `3c7a1f3…`, the commit the tested builds used. |
| 57 Python runtime packages (`requirements-runtime.lock`) | 51 permissive (MIT, BSD, Apache-2.0, PSF, ISC, Zlib); `orjson` and `certifi` MPL-2.0 (file-level: unmodified, so the licence text and a pointer to the source suffice); `waitress` ZPL-2.1 (permissive) | Keep each package's licence file, which pip installs beside it | Met by installing normally (the licence files stay in `site-packages`). No GPL, LGPL or AGPL package is in the runtime set. |
| Debian base image (`python:3.14-slim-bookworm`) | various, including LGPL (glibc) | Debian publishes the matching source | Unchanged. |

Fast Downward is run as a **separate program** with a fixed command line and its output read back (`symbolic.py`); it is not linked into to;doh, so the GPL covers it and not to;doh's own code.
The image builds both tools from source; the Docker build was run to check the changed Dockerfile (see `docs/REFERENCES.md`).

## 3. Optional extras
| Extra | Licence | Notes |
|---|---|---|
| `flights` (fli) | MIT | Talks to Google Flights through an unofficial interface: a terms-of-service matter, section 6. |
| `browser` (Playwright for Python) | Apache-2.0 | Chromium is the system's, not shipped. |
| `documents` (Docling) | MIT | Its dependency tree is 122 packages: 100+ permissive, `certifi` and `tqdm` MPL-2.0, and, **on Linux with the default torch, about 15 NVIDIA CUDA packages under NVIDIA's proprietary terms**. Running them locally is fine; shipping them in an image needs NVIDIA's redistribution terms. The documented clean install uses the CPU torch index (`--extra-index-url https://download.pytorch.org/whl/cpu`), which has none of them and is a third of the size. The shipped Dockerfile does not install Docling at all, so the image reads text, CSV and HTML quotes only. |
| Docling's downloaded models | CDLA-Permissive-2.0 (`docling-models`, `CodeFormulaV2`), Apache-2.0 (`docling-layout-heron`, the ONNX build, Qwen2.5-1.5B-Instruct), MIT (`DocumentFigureClassifier-v2.5`) | Read from each model's Hugging Face card. RapidOCR's OCR weights are PaddleOCR's (Apache-2.0); not independently re-read here. |

## 4. Served and bundled assets
- **Fonts** (Inter, Instrument Serif): SIL OFL 1.1. The licence asks for its text to travel with every copy; `eventmate/web/static/LICENSES.txt` now contains both full texts (it was only a link before).
- **axe-core 4.14.0** (tests only, never served): MPL-2.0, unmodified, licence kept beside it.
- **Hero poster and video**: licence not confirmed; not in the repository.
- **OpenStreetMap data** (venue discovery): ODbL 1.0 asks for visible "© OpenStreetMap contributors" wherever the data is shown. The API responses carried it; the dashboard tile did not. It now does, with a browser test.
- **Google Places** (optional key): Google Maps Platform terms ask for its attribution beside Places results (the tile now says "Powered by Google") and limit how long place content may be cached (section 6).
- **OpenWeather**: asks for attribution; it is in the API response (the dashboard does not show weather).

## 5. Reference repositories: what each one's terms mean here
| Repo | Licence read | Used as | Measured overlap with our code | Obligation |
|---|---|---|---|---|
| `jiayizx/async_planning` | MIT (code), CC BY 4.0 (its datasets) | idea re-implemented | at most 3% of 8-token runs in `cpsat.py` (two identical code lines); about 0% in `exec_state.py`, `delta.py` and the schedule oracle | None for ideas. We use none of its data; the benchmark scores quoted in `docs/eventmate_architecture.md` come from its paper and README, credited there. The notice is kept below. |
| `CopilotKit/openmuse` | MIT | idea re-implemented | `jobs.py`: 0 of 134 lines. `pagediff.py`: no identical line, but one regular expression is shared ("5 minutes ago"), a 48-token run | The notice is kept below. The `pagediff.py` docstring said "nothing copied"; it now says exactly that. |
| `langgenius/dify` | Apache-2.0 **plus** extra terms: no multi-tenant service without written authorisation, and the logo may not be removed | idea only | 0 identical lines, longest shared run 4 to 13 tokens | None, because no Dify code is used. **If anyone ever ports Dify code, the multi-tenant clause would apply to shared mode** (one workspace per visitor is what Dify calls a tenant). |
| `guardrails-ai/guardrails`, `deepset-ai/haystack` | Apache-2.0 | idea only | 0 identical lines | None. |
| `Portkey-AI/gateway` | MIT | idea only (per-stage caps) | not compared | None. |
| `langfuse/langfuse` | MIT, except its `ee/` directories (separate licence) | a service, over its ingestion API | no code | None; the `ee/` terms matter only if code from those directories is used. |
| WAHA (`devlikeapro/waha`) | Apache-2.0 | a service, over HTTP | no code | None for the licence. Using WhatsApp Web automation is a terms-of-service matter, section 6. |
| `lalaliat/Agent-Oriented-Planning`, `CraftJarvis/MC-Planner` | **no licence file in either repository** | the published ideas only | no code | No licence means no copying; none was copied. Cite the papers. |
| `CrimsonWorks-Inc/agent-vault` | Apache-2.0 | not read, not used | none | None. |
| Fast Downward, VAL, fli, Docling, Playwright | GPL-3.0, BSD-3, MIT, MIT, Apache-2.0 | run or imported | not applicable | Sections 2 and 3. |

How the overlap was measured: each upstream file the code says it follows was fetched, comments removed, and the share of our 8-token and 12-token runs found in it was counted, with the count of identical code lines of 25 characters or more
and the longest common run. Percentages are small everywhere except the one regular expression above. A low score shows no copying of text; it does not show that nothing was learned from the upstream, which is what the docstrings say.

## 6. Terms of service, which are not licences
These need the owner's decision before anything is switched on in public:
- **Google Flights through fli**: an unofficial interface; Google's terms do not bless it.
- **WhatsApp through WAHA**: WhatsApp's terms do not allow automation of WhatsApp Web; the account can be banned.
- **Google Places**: the terms limit caching place content (other than the place id) to a short period; the shortlist file keeps names, websites and ratings without an expiry. Leave the key unset, or add an expiry, before enabling it for real users.
- **Public Overpass server and DuckDuckGo's HTML search**: both have usage policies, and neither is meant for an unattended production service.

## Notices that must be kept
MIT, the two upstreams whose ideas are re-implemented here (the text is identical apart from the copyright line):

> Copyright (c) 2026 OpenMuse contributors; Copyright (c) 2026 The Authors (async_planning)
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished
> to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
> IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
> SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
