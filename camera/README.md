# sunroof · camera catalog

Finds public webcams that can plausibly *see* a weather event, so the backend can
fetch a frame, run the VLM gate and broadcast it. Design docs: `docs/sources.md`
(where cameras come from), `docs/preprocessing-plan.md` (schema, coverage math,
night logic, ranking), `docs/preprocessing-schema.svg` (one-page diagram).

```
uv sync --extra dev
uv run sunroof-camera refresh                  # adapters -> data/shards/*.parquet -> data/cameras.parquet
uv run sunroof-camera describe                 # counts by source / heading_conf / night_ok / health
uv run sunroof-camera find thunderstorm --lat 39.7 --lon=-104.9 --radius-km 20
uv run pytest

uv run sunroof-camera refresh --source faa    # 3.5k FAA WeatherCams, no key, ~3 s
uv run sunroof-camera serve --fake-events     # camera service + sandbox page on http://127.0.0.1:8080
uv run sunroof-camera resolve thunderstorm --lat 67.6 --lon=-164 --radius-km 20   # one-shot FootageResult JSON
```

## Camera service (query → gate → VLM → route)

Design: `docs/query-and-routing-plan.md` + `docs/query-routing-schema.svg`.
The weather backend posts a `WeatherEvent` and gets a `FootageResult` back
(`src/sunroof_camera/footage.py` is the contract for both the backend and the frontend):

```
POST /events                {"id":"…","type":"thunderstorm","lat":..,"lon":..,"radius_km":20}
  -> {"status": "FOOTAGE_FOUND" | "NO_CAMERAS_IN_RANGE" | "CAMERAS_DARK" | "ALL_STALE"
                | "EVENT_NOT_VISIBLE" | "NO_FOOTAGE_FOUND" | "TIMEOUT",
      "footage": [Footage…], "rejected": [...], "retry_after_s": ...}
GET  /stream                SSE, one `footage` event per FootageResult (what the sandbox page consumes)
GET  /feed, /events/{id}/footage, /proxy/frame/{camera_id}, /health
```

`Footage.media` is `{kind: image|hls|iframe, src, refresh_s}` — the frontend renders
that and never talks to cameras directly; `frame_ts` + `ts_source`
(`source_api` / `exif` / `last_modified` / `fetch_time`) say how trustworthy the timestamp is.

Pipeline per event (`resolve.py`): `Catalog.find_cameras` → concurrent fetch of `k×3`
candidates → `gates.check_frame` (bytes/magic/decode, placeholder + frozen-frame SHA-1,
freshness vs cadence, uniform / blown-out / dark, pHash de-dupe, sharpness) →
`vlm.judge` (one structured verdict per frame) → top-`k` `Footage`. Without any VLM
backend the service still runs and returns gate-passed frames marked `verified: false`.

### VLM backends (`vlm.py`)

All backends speak the OpenAI chat-completions API, so switching is env-only:

| setup | env | model |
|---|---|---|
| OpenAI (hosted, ~2 s/frame) | `OPENAI_API_KEY` | `gpt-4o-mini`, escalates to `gpt-4o` |
| **Groq (hosted, free tier, ~0.5–1 s/frame) — current default for dev/demo** | `GROQ_API_KEY` ([console.groq.com/keys](https://console.groq.com/keys)) | `qwen/qwen3.8-27b` |
| local Ollama (auto-detected on `127.0.0.1:11434`) | none — `ollama pull qwen2.5vl:3b` | `qwen2.5vl:3b` |
| any OpenAI-compatible server (vLLM, OpenRouter, remote Ollama) | `SUNROOF_VLM_BASE_URL`, `SUNROOF_VLM_API_KEY` | `SUNROOF_VLM_MODEL` |
| disabled (CI / offline) | `SUNROOF_VLM_BACKEND=off` | — |

Precedence: `off` > `OPENAI_API_KEY` > `SUNROOF_VLM_BASE_URL` > `GROQ_API_KEY` > local Ollama.

Overrides: `SUNROOF_VLM_MODEL` / `SUNROOF_VLM_MODEL_LARGE`, `SUNROOF_VLM_PARALLEL`
(concurrent calls; default 1 for localhost, 4 for other custom URLs, 8 for OpenAI),
`SUNROOF_VLM_BUDGET_S` (minimum time given to the VLM stage; default 150 s on
localhost so a CPU-only model gets at least one verdict). OpenAI uses native
structured output; open models get a JSON example prompt and the reply is
validated by the same Pydantic model. Expect ~30–100 s/frame for Qwen2.5-VL-3B on
8 CPU cores (a few seconds on any GPU); with a serial backend only the top `k`
gate-passed candidates are judged, and if no verdict arrives in time the frames
are still served as `verified: false`. `/health` reports the active backend.

Other environment: `WINDY_API_KEY` (only for the Windy source), `NSW_API_KEY` (Transport for NSW).

Keys are read from the environment; `camera/.env` (git-ignored) is loaded automatically on
import — `cp .env.example .env` and fill in what you have. Existing env vars win over `.env`.

## For the backend / other Devin: the contract

**One file, `data/cameras.parquet`, one row per camera view.** Read it with
`pd.read_parquet` (or `sunroof_camera.schema.read_parquet`, which pins dtypes).
Column semantics live in `src/sunroof_camera/schema.py::Camera` (pydantic model
= documentation) and `CAMERA_DTYPES` (exact pandas dtypes). `sunroof-camera schema`
prints the dtype map.

| group | columns |
|---|---|
| identity | `id` (`{source}:{source_id}[:{view}]`), `source`, `source_kind` (`jpeg`/`hls`/`embed`/`page`), `name` |
| location | `lat`, `lon`, `alt_m`, `tz` |
| **sky coverage** | `azimuth_deg` (view centre, NaN if unknown), `hfov_deg` (360 = all-sky), `elev_min_deg`, `elev_max_deg` (frame bottom/top above horizon), `heading_conf` (`catalog`/`text`/`inferred`/`ptz`/`unknown`), `sky_frac` |
| flags | `night_ok`, `all_sky`, `ptz`, `over_water` |
| fetch | `image_url`, `stream_url`, `embed_url`, `page_url`, `refresh_s`, `history_kind`, `history_template` (strftime URL), `history_depth_days`, `license`, `attribution`, `embed_allowed` |
| health | `last_frame_ts`, `last_ok_ts`, `fail_streak`, `health` (`live`/`stale`/`dead`/`unverified`), `quality_score`, `night_usable_frac` |

Coverage is **not** a stored polygon. It's the viewing cone
`(azimuth, hfov, elev_min, elev_max)`; `find_cameras` computes distance + bearing
to the event and tests whether a feature at the event type's altitude falls in
the frame (annular sector — see plan §3). So a camera row is valid for every
event type; nothing per-event is precomputed.

### Query API

```python
from sunroof_camera.query import Catalog, Event

cat = Catalog.load("data/cameras.parquet")        # load once, keep in memory
res = cat.find_cameras(Event(type="thunderstorm", lat=39.7, lon=-104.9, radius_km=20, t=None), k=10)
# -> DataFrame: all camera columns + distance_km, bearing_to_event, solar_elev, score, reason
```

`Event.type` ∈ `sunrise sunset thunderstorm lightning mammatus lenticular fog undercast aurora rainbow`.
`t=None` means now; a past `t` gives replay candidates (rows with history).
Returns up to `k` rows, best first, deduped to one view per ~1 km. Feed the top
`k*3` to the VLM and keep what passes. `reason` is a short human string for
"why this camera".

### Adding a source

`src/sunroof_camera/ingest/sources/<name>.py` with `source: ClassVar[str]`,
`async catalog(http) -> list[Camera]`, `async fetch_frame(http, cam, ts=None)`;
register in `ingest/registry.py`. `ingest/base.py` has the shared httpx client,
per-host rate limiter, compass-text heading parser and a default `fetch_frame`
for plain JPEG cameras. Hand-picked cameras (YouTube, all-sky) go in
`data/manual.yaml`.

Secrets: `WINDY_API_KEY` etc. via environment or a git-ignored `.env` only; never in the repo.

### Sources implemented

| source | rows (Sep 2026) | heading | night | history | notes |
|---|---|---|---|---|---|
| `caltrans` | ~3,300 | text 88% | – | last 12 frames | 12 district JSONs, JPEG + HLS |
| `cars_ny` / `cars_on` | ~1,800 / ~1,400 | text ~50-70% | – | – | 511 portals; NY optional `NY511_API_KEY` |
| `cars_fl` `cars_ut` `cars_pa` `cars_nc` `cars_az` `cars_nv` `cars_id` `cars_wi` `cars_ne6` `cars_la` `cars_ak` `cars_ab` `cars_ns` `cars_nb` `cars_nl` `cars_yt` | ~14,000 total (FL 4.9k, UT 2.1k, PA 1.4k, NC 1.1k) | text 0–100% (`direction` + view description) | – | – | keyless `/List/GetData/Cameras` on every CARS 511 portal; video-only sites return a 15 KB placeholder PNG that `fetch_frame` rejects. Georgia (4.3k) excluded: ~85% placeholder + auth-walled HLS |
| `cars_mn` `cars_ia` `cars_ma` `cars_ne` `cars_in` `cars_ie` | ~4,600 (MN 1.7k, IA 1.0k, IN/NE ~650, MA 300, Ireland 230) | text (view title) | – | – | CARS portals whose list endpoint is a React shell; `cars_gql.py` calls the OneWeb `/api/graphql` `mapFeaturesQuery` with a state-wide bbox at zoom 15 -> JPEG poster + public HLS. Kansas skipped (`url=null` views) |
| `tw_tdx` | ~2,770 | text (`RoadDirection`) | – | – | Taiwan MOTC TDX highway (JPEG) + freeway (MJPEG; `fetch_frame` pulls the first frame) CCTV, 1-min, needs a browser UA |
| `no_vegvesen` | ~840 | – | – | – | Statens vegvesen road-weather sites: altitude, `status`, HLS; NLOD 2.0; mountain passes (Sognefjellet 1,413 m) + Finnmark for aurora |
| `tfl` | ~800 | `view` text 71% | – | 10 s MP4 clip | London JamCams, 5-min, TfL Open Data licence |
| `au_qld` | ~136 | `direction` 100% | – | – | Queensland (Brisbane–Cairns, Toowoomba range), keyless GeoJSON, 1-min JPEG, CC BY 4.0 |
| `alertca` | ~1,800 | catalog (pan) | IR subset | – | ridge-top PTZ, firestorm mirror |
| `digitraffic` | ~1,700 | – | yes | 24 h API | Finland, CC BY 4.0 |
| `panomax` | ~630 | catalog (zeroDirection+viewAngle/2) | `nightVision` | recent API | Alpine panoramas |
| `phenocam` | ~550 | text | – | archive to 2000s, 30 min | research sites |
| `iceland` | ~480 | text (is) | yes | – | Vegagerðin |
| `fotowebcam` | ~340 | catalog (`direction`, `sector`=hfov) | yes | 10-min archive, years | best quality/attribution |
| `tripcheck` | ~1,150 | filename suffix (NB/SW…) 47% | – | – | Oregon DOT: Cascades, coast, Gorge |
| `drivebc` | ~1,040 | `orientation` 100% + elevation | – | ReplayTheDay | British Columbia passes, PNG frames |
| `nzta` | ~250 | `direction` 99% | – | – | New Zealand state highways, CC BY 4.0 |
| `hk_td` | ~1,010 | text ("- Eastbound" suffix) ~78% | – | – | Hong Kong Transport Dept snapshots, 2-min, data.gov.hk |
| `sg_lta` | ~8 live (90 in archive) | – | – | any past minute via `?date_time=` | Singapore LTA 1080p; `image_url` empty, `fetch_frame` re-queries the API |
| `ndbc` | ~90 | 360° strip | – | – | BuoyCAMs, found by probing `buoycam.php` |
| `iem` | live only | catalog (`angle`) | – | per-minute archive | rows accumulate across refreshes |
| `windy` | ~1k/country | text from title | – | embed player day/month/year | needs `WINDY_API_KEY`; offset ≤1000/free tier |
| `manual` | yaml | – | – | – | hand-picked: UAF Poker Flat + IRF Kiruna all-sky (aurora, `night_ok`) |

`uv run sunroof-camera refresh` builds every keyless source in ~3 min (~38k rows; the CARS portals are paged 100 at a time).

## Demo cameras & sample queries

Known-good rows (frames verified live, Sep 2026) to hard-code into demos/tests:

| id | where | why it's a good demo |
|---|---|---|
| `panomax:17` Edelweißspitze | 47.124, 12.831, 2,570 m | 360° Alpine panorama, `night_ok`, 10-min history API |
| `panomax:7` Großglockner Kaiser-Franz-Josefs-Höhe | 47.07, 12.75 | glacier panorama, lenticular/sunset showcase |
| `fotowebcam:adlersruhe` | 47.070, 12.702, 3,454 m | az 275°, hfov 113°; 10-min archive back years (`history_template`) |
| `alertca:Mt_Tamalpais_East` / `alertca:Mt_Diablo_West` | SF Bay ridge tops | exact pan heading, fog/undercast over the Bay |
| `caltrans:d3:37` Hwy 50 @ Hwy 89 South Lake Tahoe | 38.913, -120.005 | DOT cam with last-12-frames history |
| `ndbc:41002` BuoyCAM South Hatteras | 31.74, -74.96 | 6-panel 360° strip at sea, sunrise/storms offshore |
| `iceland:7001:hellisheidi_1.jpg` Hellisheiði W | 64.018, -21.343 | Iceland road cam, aurora candidate |
| `digitraffic:C0150301` Inkoo | 60.054, 23.996 | Finland, 24 h history API, CC BY 4.0 |
| `phenocam:alfacada` Ebro Delta | 40.68, 0.84 | archive to 2000s at 30 min, S-facing |
| `manual:irf-kiruna-allsky` | 67.84, 20.41 | all-sky aurora camera, 1-min JPEG, `night_ok` |
| `hk_td:H421F` Aberdeen Tunnel | 22.250, 114.176 | Hong Kong, 2-min refresh, typhoon/fog demo |
| `cars_ak:*` / `cars_ut:*` | Alaska / Utah | 511 cams with text headings (Richardson Hwy, Wasatch) |
| `no_vegvesen:0529029_1` F55 Sognefjellet | 61.565, 7.998, 1,413 m | highest Norwegian pass, lenticular/undercast; `no_vegvesen:2000065_1` Aisaroaivi (70.28 N) for aurora |
| `tfl:00001.06570` Hammersmith Bridge Rd | 51.491, -0.227 | London, S-facing, 5-min JPEG + MP4 clip |
| `tw_tdx:CCTV-N1-S-0.000-M` National Fwy 1 Keelung | 25.123, 121.736 | Taiwan, 1-min MJPEG stream, typhoon/thunderstorm demo |
| `cars_ie:127:1733092217` N59 Maam Cross | 53.456, -9.537 | Connemara, Atlantic fronts/rainbows |
| `au_qld:84` Murarrie – Port of Brisbane, W | -27.452, 153.114 | southern hemisphere; Brisbane summer thunderstorms, sunset over the city |

Ready-to-run queries (`--t` is UTC, omit for now):

```bash
# Alpine sunset: Panomax panoramas around Großglockner, sun az≈276°
uv run sunroof-camera find sunset      --lat 47.07 --lon 12.70   --radius-km 5  --t 2026-09-20T17:30:00
# lenticulars over the Hohe Tauern (annulus 16–80 km for a 6 km cloud)
uv run sunroof-camera find lenticular  --lat 47.2  --lon 12.9    --radius-km 10
# Central Valley anvil seen from Bay Area ridge cams (33–150 km annulus)
uv run sunroof-camera find thunderstorm --lat 37.5 --lon=-121.5  --radius-km 20
# Golden Gate fog at 08:00 PDT -> ALERTCalifornia ridge cams
uv run sunroof-camera find fog         --lat 37.8  --lon=-122.45 --radius-km 5  --t 2026-09-20T15:00:00
# rainbow over South Lake Tahoe, 17:30 PDT (antisolar az≈77° -> E-facing Caltrans cams)
uv run sunroof-camera find rainbow     --lat 38.9  --lon=-120.0  --radius-km 5  --t 2026-09-21T00:30:00
# aurora over Iceland at local midnight -> night_ok Vegagerðin cams
uv run sunroof-camera find aurora      --lat 64.5  --lon=-21.0   --radius-km 100 --t 2026-09-20T23:30:00
# undercast / lenticular over Jotunheimen from Sognefjellet + Valdresflye (1.4 km passes)
uv run sunroof-camera find undercast   --lat 61.5  --lon 8.2     --radius-km 10
# London thunderstorm: TfL JamCams within 30 km of a cell over Croydon
uv run sunroof-camera find thunderstorm --lat 51.37 --lon=-0.10  --radius-km 5
# Brisbane thunderstorm (S-hemisphere demo): QLD cams within the 33–150 km anvil annulus
uv run sunroof-camera find thunderstorm --lat=-27.6 --lon 152.7  --radius-km 10
# sunrise on the Gulf of Finland
uv run sunroof-camera find sunrise     --lat 60.05 --lon 24.0    --radius-km 5  --t 2026-09-21T04:00:00
```

Same thing from Python: `find_cameras(Event("sunset", 47.07, 12.70, 5, t), k=10)`.
