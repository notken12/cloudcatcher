# sunroof · camera — work summary

Everything that has been built in `camera/` so far (hackathon day, ~30 PRs), the libraries it
uses, and *why* each design choice was made. Detailed design records live next to the code and are
linked from each section; this file is the map.

```
camera/
  README.md                      ops + API + source inventory + demo queries
  docs/sources.md                every camera source probed: accepted / rejected / why
  docs/source-probes.yaml        the same, machine-readable (for a re-check agent)
  docs/preprocessing-plan.md     schema, coverage math, night logic, ranking
  docs/query-and-routing-plan.md event → cameras → frames → VLM → frontend
  docs/match-and-filter-design.md "is this frame worth showing?" staged plan + CV literature
  docs/*.svg                     the two one-page diagrams
  src/sunroof_camera/            ~6.7k lines Python, 31 adapter modules
  tests/                         pytest (adapters, geometry/query, gates/resolve/server, health, quality, events_db)
  data/cameras.parquet           the catalog (42,583 cameras, 53 sources) + data/shards/<source>.parquet
```

---

## 1. What the camera half does

Given a weather event (type, lat, lon, radius) from the weather backend, find public webcams
that can plausibly *see* it, fetch a current frame, check that the frame is real and fresh,
optionally ask a VLM "is the event visible?", and hand ranked `Footage` to the frontend.

```
adapters ──► cameras.parquet ──► find_cameras() ──► fetch + gates ──► VLM ──► Footage / status
   (ingest, offline)     (ms)                    (1–3 s)      (~1.5 s)
            ▲                                       │
            └──────── health probe (cron) ◄─────────┘
```

Two halves, deliberately decoupled:

| half | runs | output |
|---|---|---|
| **Preprocessing** — adapters, catalog, health probe | offline / cron | `data/cameras.parquet` |
| **Query service** — match, fetch, gate, VLM, route | FastAPI, per event | `FootageResult` JSON, SSE, proxied frames |

---

## 2. Libraries and why

| library | used for | why this one |
|---|---|---|
| **pandas + pyarrow (Parquet)** | the catalog: one DataFrame in memory, one Parquet file on disk, one shard per source | The catalog is ~40k static rows. A vectorised haversine + bearing filter over all rows takes milliseconds, so no database is needed. Parquet keeps dtypes (timestamps, bools, nulls, categoricals) and is 5–10× smaller than CSV; readable by DuckDB/Polars/Spark if anyone wants SQL. **DuckDB-spatial was the first proposal and was dropped** — it only bought concurrent upserts and SQL, both solved by per-source shards + a Python API. |
| **numpy** | geometry (annular sector, bearings, solar position) and the pixel-statistics gates/quality features | vectorised over the whole catalog / a 256² grey copy; no OpenCV, no torch |
| **pydantic v2** | `Camera`, `WeatherEvent`, `FootageResult`, `Footage`, VLM verdict schema | the *contract* with the weather backend and frontend is typed code, not prose; also validates open-model VLM output |
| **httpx[http2]** | all fetching (catalog pulls, frames, HLS playlists) | async, connection pooling, per-host limits; concurrent probe of 38k cameras in ~7 min |
| **Pillow** | decode frames, EXIF timestamps, downscale for VLM / history proxy, WebP support | lightweight; deterministic gates need only PIL + numpy |
| **timezonefinder** | `tz` column per camera | camera-local time for display and for archive "local time of day" lookups |
| **solar position** (own `solar.py`, NOAA-style formula, vectorised) | day/twilight/night per camera per query | astral was the initial plan; a 40-line numpy implementation avoids per-row Python calls over 40k cameras |
| **FastAPI + uvicorn** | `POST /events`, `GET /feed`, `/stream` (SSE), `/proxy/frame`, `/proxy/history`, `/events`, `/health` + the sandbox page | async, pydantic-native, one process serves API + static page |
| **openai** SDK | VLM gate — but as an *OpenAI-compatible* client | every backend (OpenAI, Groq, local Ollama, vLLM/OpenRouter) speaks the same chat-completions format, so switching provider is env-only |
| **typer** | `sunroof-camera refresh / describe / health / find / resolve / serve / import-events / match` | one CLI, subcommands map 1:1 to pipeline stages |
| **python-dotenv** | `camera/.env` auto-load | so `git pull && uv run …` works on a laptop; keys never enter the repo |
| **pyyaml** | `data/manual.yaml` hand-curated cameras, `docs/source-probes.yaml` | |
| **sqlite3** (stdlib) | `events.db` event store (`events_db.py`) | the weather job pushes runs, cron matches cameras, API reads — a file DB is enough and needs no server |
| **ffmpeg** (external, optional) | one frame from HLS streams (`source_kind=hls`, ~1.9k cameras) | only for the ~4% of cameras that are streams |
| dev: **pytest, pytest-asyncio, ruff** | CI (`.github/workflows/camera.yml`: `ruff check` + `pytest`) | |

Deliberately **not** used: torch/CLIP/OpenCV (dependency weight; a possible iOS port wants
Core ML/ONNX-portable metrics — see `match-and-filter-design.md` §2), Elasticsearch (judged a poor
fit for a 40k-row static point lookup; only sensible on the *events* side), DuckDB/PostGIS (see above),
GeoPandas (polygons are only needed for display; coverage is computed per query).

---

## 3. Preprocessing: catalog, coverage, night, health

### 3.1 Data model (`schema.py`, `docs/preprocessing-plan.md` §1)

One flat row per camera *view*. Key columns:

- identity/location: `id` (`<source>:<native id>`), `source`, `source_kind` (jpeg/hls/page),
  `name`, `lat`, `lon`, `alt_m`, `tz`
- **coverage**: `azimuth_deg`, `hfov_deg` (default 60), `elev_min_deg` (−5), `elev_max_deg` (25),
  `heading_conf` ∈ {catalog, text, inferred, ptz, unknown}, `all_sky`, `ptz`, `over_water`, `sky_frac`
- access: `image_url`, `stream_url`, `embed_url`, `page_url`, `refresh_s`, `embed_allowed`,
  `license`, `attribution`
- history: `history_kind`, `history_template` (strftime), `history_depth_days`
- health (filled by the probe): `health` ∈ {live, stale, dead, unverified}, `last_frame_ts`,
  `last_ok_ts`, `fail_streak`, `quality_score`, `night_ok`, `night_usable_frac`

**Why a flat table and not a normalised schema:** every query is "all rows × a few vector ops";
joins would only add latency and code. Multi-view stations (PTZ presets, IR twins) are separate
rows, de-duplicated on `(lat, lon, azimuth/15°, night_ok)` so a thermal twin of a visible camera survives.

### 3.2 "Which region of the sky" — the annular-sector model (`geometry.py`, plan §3)

The user asked for the *easiest way to store the range*. Answer: don't store a footprint at all;
store the viewing cone `(azimuth, hfov, elev_min, elev_max)` and compute the footprint per query,
because it depends on the event's altitude `h`:

- a feature at height `h` over ground distance `d` appears at elevation
  `α = atan(h/d) − d/(2·R_earth)` (second term = curvature, ~0.7° at 150 km);
- camera sees it iff `|wrap(bearing − azimuth)| ≤ hfov/2 + slack` **and** `elev_min ≤ α ≤ elev_max`;
- that gives a **near limit** `d_min = h/tan(elev_max)` (a horizon-facing DOT cam with
  `elev_max≈20°` cannot see an anvil closer than ~33 km — it's above the frame) and a far limit
  `d_max = h/tan(elev_min)`, which is infinite when the camera includes the horizon, so a
  per-event-type visibility cap (storm 150 km, aurora 300–600 km) does the real work;
- heading uncertainty widens the bearing slack (`text` ±22.5°, `inferred` ±30°, `ptz/unknown` →
  no bearing filter, score ×0.6); `all_sky` cameras skip the cone entirely;
- undercast is the "look down" special case: camera `alt_m` above the deck, distance-only;
  sunrise/sunset/rainbow use the sun/antisolar azimuth instead of `h`.

Per-type parameters live in one table, `profiles.py` (`EventProfile`): adding an event type is
adding a row, not a code path. Terrain occlusion is not modelled — the VLM catches it.

### 3.3 Night (`solar.py`, plan §4)

Sun elevation is computed per camera at query time. Above −6° every camera is eligible; between
−6° and −18° `night_ok` cameras are preferred; below −18° they are required. Aurora *requires*
darkness. `night_ok` is set from source metadata (IR/thermal, all-sky) and then **measured**: the
health probe records luminance at night and derives `night_usable_frac`, so night gating uses
evidence rather than guesses. The `--ignore-night` demo flag exists only because the first
demos ran at US night; it fakes midday and is documented as such.

### 3.4 Sources and adapters (`ingest/`, `docs/sources.md`, `docs/source-probes.yaml`)

An `Adapter` protocol (`catalog() -> list[Camera]`, optional `frame_ts`) per source; `refresh`
writes `data/shards/<source>.parquet` and merges into `cameras.parquet`. Per-source shards mean a
single flaky source can be re-pulled without touching the rest and concurrent ingests don't clobber
one file.

Current catalog: **42,583 cameras / 53 sources**, all keyless except Windy (`WINDY_API_KEY`) and
NSW Live Traffic (`NSW_API_KEY`, adapter not wired — user chose to skip):

| region | sources |
|---|---|
| North America (~36k) | Caltrans, ALERTCalifornia, Oregon TripCheck, DriveBC, NDBC BuoyCAMs, Iowa Mesonet, FAA WeatherCams (adapter exists; not in the current merged catalog), 25 CARS-platform 511 portals (NY, FL, UT, MN, PA, ON, …), DelDOT, Alabama ALGO, Seattle/WSDOT, Austin, Travel Midwest, CTroads |
| Europe (~5k) | Finland Digitraffic, Iceland Vegagerðin, Norway Vegvesen, Panomax (Alps), foto-webcam.eu, TfL JamCams, Ireland (CARS GraphQL), PhenoCam sites, Kiruna all-sky |
| Asia / Pacific (~4.4k) | Taiwan TDX, Hong Kong TD, Singapore LTA, NZTA, Queensland, NSW Maritime coastal bars |
| South America (59) | Peru IGP/CENVUL, Ecuador IG-EPN, Colombia SGC volcano observatories (5 thermal cams `night_ok`) |
| global filler | Windy (~1k/country, keyed) |

Two adapter discoveries mattered most: the CARS 511 map UI's `/List/GetData/Cameras` and GraphQL
routes are open while the official API is keyed (+15k cameras); and "keyless" often means a static
GeoJSON/CKAN dataset rather than an API.

**Rejected/deferred sources are recorded, not dropped**: `docs/source-probes.yaml` has 60+ entries
with `status / observed / reason / re-probe recipe` (needs-key: WSDOT, Ohio OHGO, FAA token;
placeholder-heavy: Georgia 511; Cloudflare/HTML-only: SA/TAS, Japan MLIT/NEXCO, Italy, Netherlands;
embedded players: Chile SERNAGEOMIN, Nevado del Ruiz). This was requested so a later agent can
mechanically re-verify rejections.

### 3.5 Health probe (`health.py`, PR #19)

`sunroof-camera health` fetches one frame per camera (64 concurrent, 6/host), runs the LLM-free
gates and appends a row to `data/health_log.parquet`; the catalog's health columns are *derived*
from that log (live/stale/dead rules in the README). Per run it detects placeholder cards
(identical bytes from ≥3 cameras of one source — DriveBC, QLD, several CARS states) and frozen
encoders (same bytes >6 h apart, no source timestamp). `find_cameras` drops `dead` rows and
anything whose last frame is >24 h old and weights freshness `exp(−age/3·refresh_s)`.

Why a separate log rather than overwriting columns: history gives `night_usable_frac`, sharpness
medians for `quality_score`, and lets the derivation rules change without re-probing. Measured
on ~2.5k real cameras: ~91% live.

---

## 4. Query service: match → gate → VLM → route

Design record: `docs/query-and-routing-plan.md`, `docs/match-and-filter-design.md`.

### 4.1 Match (`query.py`, `profiles.py`)

`Catalog.find_cameras(event)` = annular sector ∧ viewing cone ∧ elevation band ∧ night rule ∧
health/freshness, then one relative score: distance-fit × cone-centredness × heading_conf ×
sky_frac × quality_score × source prior. One engine for all types.

### 4.2 Fetch + gates (`fetch.py`, `gates.py`)

Concurrent fetch of the top candidates (a multiple of the requested `k`), then cheapest-first deterministic checks, each with a
human-readable reject reason (shown on the sandbox page and logged): bytes/magic (JPEG/PNG/WebP),
decodable, placeholder SHA-1, frozen frame, timestamp freshness vs cadence (`ts_source` =
source_api / exif / last_modified / fetch_time tells how trustworthy it is), dark, uniform,
blown-out; softness only down-weights. **Principle: gates reject only what we are sure about;
scene-dependent judgements are ranking signals, never rejects** — a grainy road cam with a huge
anvil behind it must not be filtered before the VLM sees it (user decision, 2026-09-20).

### 4.3 Deterministic quality `Q` (`quality.py`, Stage A of the design record, PR #30)

Seven [0,1] features on a 256² grey + 128² colour copy (~2 ms, numpy only): sky share,
Hasler–Süsstrunk colourfulness, warm-hue share, sky texture, dark-channel haze, sharpness,
exposure; per-type weighted mean `Q`. Used to **re-rank** VLM-passed frames
(`confidence × (0.5 + 0.5·Q)`) and to order which candidates hit the VLM first. Features are
logged next to every verdict (`data/verdicts.jsonl`) for later calibration. Stages B–E
(CLIP pre-rank, per-type learned heads, best-of-N, camera-level learning) are planned but not
built; the design record explains the literature and why each was deferred.

### 4.4 VLM (`vlm.py`)

One structured call per gate-passed frame: `usable, sky_visible, night, event_visible
(yes|partial|no|unsure), event_type_seen, confidence, quality, caption, burned_in_time`.
Yes/no on the type rather than open captioning (VLMs are reliable at coarse recognition and at
reading burned-in timestamps, unreliable at numeric self-ratings and fine cloud taxonomy).
Backends by env: OpenAI gpt-4o-mini (`detail: low`, ≈$0.0005/frame, native structured output),
Groq (free tier), local Ollama Qwen2.5-VL-3B (works but 30–100 s/frame on CPU — why hosted is
the demo default), any OpenAI-compatible URL, or `off`. Backend is resolved at startup so a
misconfigured key fails fast; verdicts are cached per frame hash; `/health` reports tokens and
running USD. Without a backend the service still serves gate-passed frames as `verified: false`.

### 4.5 Route (`resolve.py`, `footage.py`)

Pass = `usable ∧ event_visible ∈ {yes, partial} ∧ event_type_seen == type ∧ confidence ≥ min_conf`
(+ per-type rules: lightning/rainbow need a hard `yes`, daytime types reject `night:true`).
Top-k become `Footage` — one universal envelope `{media: {kind: image|hls|iframe, src,
refresh_s}, camera, verdict, why, frame_ts, ts_source, hold_until}` and three renderers on the
frontend, nothing per-source. Statuses: `FOOTAGE_FOUND | NO_CAMERAS_IN_RANGE | CAMERAS_DARK |
ALL_STALE | EVENT_NOT_VISIBLE | LOW_QUALITY | NO_FOOTAGE_FOUND | TIMEOUT`, each with
`retry_after_s` so the weather backend can back off. The frontend never talks to a camera host:
`/proxy/frame/{id}` and `/proxy/history/{id}?ts=&w=` (archive frames, Pillow-downscaled) sit in
between.

### 4.6 Event store + cron matching (`events_db.py`, `match.py`, PR #26)

Requested architecture: the weather reanalysis job pushes `events.json` → `import-events` upserts
into SQLite (stable uuid, merge rule: same type, <2 h, within radius); a cron `match` step writes
`event_cameras` / `event_footage`; `GET /events?type=&time=&limit=` is a pure read (ranked by
`0.6·rarity + 0.4·severity`, `?time=` looks back through `event_observations`). Matching moved
out of request time so the API is fast and reproducible per run.

---

## 5. Timeline of PRs (camera)

| PR | what | key decision |
|---|---|---|
| #1 | source inventory + preprocessing plan | pandas/Parquet over DuckDB; annular-sector coverage; Elastic not for the catalog |
| #2 | schema, `find_cameras`, adapter protocol | typed contract first so other Devins could build against it |
| #3, #5 | query & routing plan + diagram | one `find_cameras()` for all types, universal `Footage` envelope |
| #4 | 34 keyless sources, ~29k cams, demo queries | CARS map endpoint is open |
| #6 | FastAPI service + sandbox page | SSE, proxied frames, fake-event loop |
| #9, #12 | open-model VLM (Ollama), Groq preset | OpenAI-compatible layer so provider = env var |
| #11 | CARS GraphQL states, Taiwan, Norway, TfL (~38k) | |
| #13 | Queensland + machine-readable probe ledger | rejections must be re-checkable |
| #14 | `.env` auto-load | laptop `git pull && uv run` works |
| #15 | NSW Maritime coastal bars (HLS) | keyless GeoJSON instead of keyed API |
| #16, #18, #24 | `--ignore-night`, FAA null-payload fallback, VLM startup check | fixes found by the user's local runs |
| #19 | health probe | derived health from a probe log |
| #20 | drop `fog` everywhere | user decision; `undercast` kept |
| #22 | OpenAI backend verified live, cost tally | measured ≈$0.0005/frame (low-detail tile bills ~2.8k tokens) |
| #23 | +4.2k US cams | |
| #26 | SQLite event store, cron match, `GET /events` | "cron matches, API reads" |
| #27 | South America volcano cams, WebP gate, IR-twin dedupe | |
| #28 | match & filter design record + CV literature | staged plan, trust levels per metric |
| #30 | Stage A deterministic `Q` | no new deps, ranking not rejecting |
| #31 (camera part) | `/proxy/history` archive frames | for the frontend's time-travel view |
| #32 (open) | `Q`/`sky_frac` ranking-only, fog dropped from weather/, off-vs-OpenAI comparison | CV sanity gates + VLM as sole content judge |

---

## 6. State of play and known gaps

- Demo verified 2026-09-20: 12 non-US daytime events → live frames for all 10 lit locations
  (Alps, Norway, Finland, London, Ireland, HK, Taiwan, Singapore); Iceland/Brisbane correctly
  `CAMERAS_DARK`. Frames are fresh, but without the VLM ~60% are road cams pointing at asphalt —
  the VLM is what turns "traffic" into "weather".
- Night coverage is thin: only a few all-sky/IR cameras; `night_usable_frac` needs probe runs at
  night in the region of interest.
- Coordinates for volcano cams are offset estimates from the summit; heading is `text`-grade for
  most DOT cams.
- Keyed sources awaiting keys: NSW Live Traffic, WSDOT statewide, Ohio OHGO, FAA token.
- Satellite tiles (GOES/Himawari/Meteosat) were scoped (~1 h adapter) and explicitly deferred.
- CV-vs-VLM comparison on ~500 historical events (ProbSevere × IEM archive) is running in the
  routing session; results will decide whether any content-level CV gate is worth keeping.
