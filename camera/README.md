# sunroof · camera catalog

Finds public webcams that can plausibly *see* a weather event, so the backend can
fetch a frame, run the VLM gate and broadcast it. Design docs: `docs/sources.md`
(where cameras come from), `docs/preprocessing-plan.md` (schema, coverage math,
night logic, ranking), `docs/preprocessing-schema.svg` (one-page diagram).

```
uv sync --extra dev
uv run sunroof-camera refresh                  # adapters -> data/shards/*.parquet -> data/cameras.parquet
uv run sunroof-camera describe                 # counts by source / heading_conf / night_ok / health
uv run sunroof-camera health --sample 2000      # probe frames -> data/health_log.parquet -> health columns (~7 min for all 38k)
uv run sunroof-camera find thunderstorm --lat 39.7 --lon=-104.9 --radius-km 20
uv run pytest

uv run sunroof-camera refresh --source faa    # 3.5k FAA WeatherCams, no key, ~3 s
uv run sunroof-camera serve --fake-events     # camera service + sandbox page on http://127.0.0.1:8080
uv run sunroof-camera resolve thunderstorm --lat 67.6 --lon=-164 --radius-km 20   # one-shot FootageResult JSON
```

Night in the US/Alaska (where the FAA cams are) makes daytime events return `CAMERAS_DARK`.
To exercise the pipeline anyway: `SUNROOF_VLM_BACKEND=off uv run sunroof-camera serve --fake-events --ignore-night`
(skips the solar gate and the dark-frame gate; with the VLM on, it will correctly reject night frames as `EVENT_NOT_VISIBLE`).

## Health probe (`health.py`)

`sunroof-camera health [--source X] [--sample N] [--tier stale]` fetches one frame per camera
(64 concurrent, 6 per host, HLS via ffmpeg), runs the LLM-free gates, and appends one row per
probe to `data/health_log.parquet`. The catalog's health columns are then *derived* from the log:

| column | rule |
|---|---|
| `health` | `live` = last probe OK and frame age ≤ max(2·refresh_s, 15 min); `stale` = OK but old, or 1–2 failures after a success; `dead` = ≥3 consecutive failures or no success for 24 h; else `unverified` |
| `last_frame_ts` / `last_ok_ts` | from the last OK probe (frame ts from source API / EXIF / Last-Modified when available) |
| `fail_streak` | consecutive failed probes |
| `night_usable_frac` | over the last 30 probes taken at solar elevation < −6°: share that passed gates with mean luminance ≥ 12 |
| `quality_score` | 0.4 + 0.6·clip(median sharpness / 200) |

Placeholder "camera unavailable" cards are caught per run: identical bytes from ≥3 cameras of one
source (DriveBC, QLD, some CARS states do this) → failed probe. A frame byte-identical to the
previous probe >6 h earlier with no source timestamp → `frozen`. `find_cameras` drops `dead`
rows and any camera whose `last_frame_ts` is older than 24 h (same cutoff as `dead`, so unprobed
cameras with a stale ingest timestamp are skipped too), and weights `fresh = exp(-age / 3·refresh_s)`,
so run the probe before a demo (`--tier unverified` first, then `--tier stale` every ~10 min). Run it at night in your region
of interest once to get `night_usable_frac` populated for the aurora / lightning gates.

## Camera service (query → gate → VLM → route)

Design: `docs/query-and-routing-plan.md` + `docs/query-routing-schema.svg`.
The weather backend posts a `WeatherEvent` and gets a `FootageResult` back
(`src/sunroof_camera/footage.py` is the contract for both the backend and the frontend):

```
POST /events                {"id":"…","type":"thunderstorm","lat":..,"lon":..,"radius_km":20}
  -> {"status": "FOOTAGE_FOUND" | "NO_CAMERAS_IN_RANGE" | "CAMERAS_DARK" | "ALL_STALE"
                | "EVENT_NOT_VISIBLE" | "LOW_QUALITY" | "NO_FOOTAGE_FOUND" | "TIMEOUT",
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

"Worth showing?" (`quality.py`, design in `docs/match-and-filter-design.md` Stage A): every
gate-passed frame gets seven deterministic [0,1] features (sky share, Hasler–Süsstrunk
colourfulness, warm-hue share, sky texture, dark-channel clarity, sharpness, exposure; ~2 ms,
numpy only) and a per-type weighted mean `Q` (`EventProfile.q`). `Q` re-ranks — it never
decides presence: with the VLM on, passing frames sort by `confidence × (0.5 + 0.5·Q)`. No
content heuristic (catalog `sky_frac`, Q, sharpness) hard-rejects a frame — only the sanity gates
do (bytes / placeholder / frozen / stale / uniform / blown-out / near-black without night
capability); the VLM is the sole content judge. `EventProfile.min_q` (default 0 = off) can turn
Q into a floor → `LOW_QUALITY`. The VLM verdict is also held to per-type rules: `require_yes` (lightning, rainbow: "partial" is not
enough) and `night` ⇒ reject for daytime-only types. `Footage.quality` / `Footage.features`
and `data/verdicts.jsonl` (`q`, `features` next to the verdict) expose all of it for calibration.

### Event store + `GET /events` (`events_db.py`, `match.py`)

The weather reanalysis job pushes its events into a SQLite file; cameras are matched by
the cron step, not at query time; the API only reads:

```
weather run  ──► events.json ──► sunroof-camera import-events out/events.json --db data/events.db
                                 (or: from sunroof_camera.events_db import connect, upsert_run)
cron         ──► sunroof-camera match --db data/events.db [--resolve]     # ranks cameras, optional footage
API          ──► sunroof-camera serve --db data/events.db                 # GET /events?type=&time=&limit=20
```

`GET /events` → up to 20 events as known at the latest run ≤ `time` (default: latest run),
sorted by `rank_score = 0.6·rarity + 0.4·severity` desc, each with
`cameras: [{rank, camera_id, name, lat, lon, distance_km, bearing_deg, score, media{kind,src,refresh_s},
page_url, health, why, status, verified, frame_ts}]` (the cron's ranking for that run) and
`footage: FootageResult | null` if the resolver ran.

Tables: `events` (stable uuid, `time` = last analysis, latest values), `event_observations`
(one row per event per run → `?time=` looks back), `event_cameras`, `event_footage`.
Merge rule on import: same type, seen < 2 h ago, centre within `max(radius_km, MERGE_KM[type])`
→ the old uuid is updated; otherwise a new one. Weather `type: "storm"` maps to `thunderstorm`,
`score` → `severity`; missing `rarity` falls back to `RARITY_PRIOR[type]`. `serve --db` also
polls the file (`--watch-db-s`, default 30) and matches/resolves any run the cron did not, and
`--fake-events --db` drives fake events through the same import → match → footage path.

### Web Push + users (`push.py`)

Every `FOOTAGE_FOUND` result that `serve` publishes on `/stream` is also fanned out as a
Web Push notification (`pywebpush`, VAPID) to the devices in `--push-db`
(default `data/push.sqlite`; tables `users`, `subscriptions`, `sends`). The VAPID private
key is created on first start at `--push-key` (default `data/vapid.pem`) — keep it: rotating
it invalidates every subscription. `--public-url https://…` makes notification images and
links absolute.

Routes: `GET /push/vapid-public-key`, `POST /push/subscribe {subscription, user_id?}`,
`POST /push/unsubscribe {endpoint}`, `POST /users {name, email?, likes}`,
`GET /users/{id}`, `PUT /users/{id}/prefs {likes}`. `/health` reports `push` counters.

Send policy, per device: only liked types once a user has likes (anonymous devices get
everything); at most one push per 3 h (1.5 h for a liked type); never the same event
twice; TTL 20 min so stale pushes are dropped rather than delivered late; 404/410
endpoints are deleted. Payload: `"🌌 Aurora dancing right now" / "<camera> — <VLM caption>"`.

### VLM backends (`vlm.py`)

All backends speak the OpenAI chat-completions API, so switching is env-only:

| setup | env | model |
|---|---|---|
| **OpenAI (hosted, ~1–2 s/frame, ≈ $0.0005/frame)** | `OPENAI_API_KEY` ([platform.openai.com/api-keys](https://platform.openai.com/api-keys)) | `gpt-4o-mini`, `detail: low`; set `SUNROOF_VLM_MODEL_LARGE=gpt-4o` to escalate unsure verdicts (~25× the price) |
| Groq (hosted, free tier, ~0.5–1 s/frame) | `GROQ_API_KEY` ([console.groq.com/keys](https://console.groq.com/keys)) | `qwen/qwen3.8-27b` |
| local Ollama (auto-detected on `127.0.0.1:11434`) | none — `ollama pull qwen2.5vl:3b` | `qwen2.5vl:3b` |
| any OpenAI-compatible server (vLLM, OpenRouter, remote Ollama) | `SUNROOF_VLM_BASE_URL`, `SUNROOF_VLM_API_KEY` | `SUNROOF_VLM_MODEL` |
| disabled (CI / offline) | `SUNROOF_VLM_BACKEND=off` | — |

Precedence when several are configured: `OPENAI_API_KEY` > `SUNROOF_VLM_BASE_URL` >
`GROQ_API_KEY` > local Ollama; force one with `SUNROOF_VLM_BACKEND=openai|groq|ollama|custom|off`.

Cost: a gate call is ~3.2k input + ~60 output tokens (the image is downscaled to 768 px and sent
at `detail: low`; gpt-4o-mini bills that flat low-detail tile at ~2.8k tokens), i.e. about
$0.0005 per frame — the demo loop (a few events/min, top-k ≤ 5 frames each) is ~$0.10–0.30 per hour. Verdicts are cached per
frame hash. `GET /health` → `vlm_usage` reports calls, tokens and the running USD estimate, and
each call is logged with its token counts.

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

`Event.type` ∈ `sunrise sunset thunderstorm lightning mammatus lenticular undercast aurora rainbow`.
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
| `cars_fl` `cars_ut` `cars_pa` `cars_nc` `cars_az` `cars_nv` `cars_id` `cars_wi` `cars_ne6` `cars_ct` `cars_la` `cars_ak` `cars_ab` `cars_ns` `cars_nb` `cars_nl` `cars_yt` | ~14,000 total (FL 4.9k, UT 2.1k, PA 1.4k, NC 1.1k) | text 0–100% (`direction` + view description) | – | – | keyless `/List/GetData/Cameras` on every CARS 511 portal; video-only sites return a 15 KB placeholder PNG that `fetch_frame` rejects. Georgia (4.3k) excluded: ~85% placeholder + auth-walled HLS |
| `cars_mn` `cars_ia` `cars_ma` `cars_ne` `cars_in` `cars_ie` | ~4,600 (MN 1.7k, IA 1.0k, IN/NE ~650, MA 300, Ireland 230) | text (view title) | – | – | CARS portals whose list endpoint is a React shell; `cars_gql.py` calls the OneWeb `/api/graphql` `mapFeaturesQuery` with a state-wide bbox at zoom 15 -> JPEG poster + public HLS. Kansas skipped (`url=null` views) |
| `tw_tdx` | ~2,770 | text (`RoadDirection`) | – | – | Taiwan MOTC TDX highway (JPEG) + freeway (MJPEG; `fetch_frame` pulls the first frame) CCTV, 1-min, needs a browser UA |
| `no_vegvesen` | ~840 | – | – | – | Statens vegvesen road-weather sites: altitude, `status`, HLS; NLOD 2.0; mountain passes (Sognefjellet 1,413 m) + Finnmark for aurora |
| `tfl` | ~800 | `view` text 71% | – | 10 s MP4 clip | London JamCams, 5-min, TfL Open Data licence |
| `au_qld` | ~136 | `direction` 100% | – | – | Queensland (Brisbane–Cairns, Toowoomba range), keyless GeoJSON, 1-min JPEG, CC BY 4.0 |
| `au_nsw_maritime` | 23 | – | – | – | NSW coastal bars + Lake Eucumbene, keyless TfNSW GeoJSON → public 1080p HLS (ffmpeg frame), over water, CC BY 4.0 |
| `alertca` | ~1,800 | catalog (pan) | IR subset | – | ridge-top PTZ, firestorm mirror |
| `digitraffic` | ~1,700 | – | yes | 24 h API | Finland, CC BY 4.0 |
| `panomax` | ~630 | catalog (zeroDirection+viewAngle/2) | `nightVision` | recent API | Alpine panoramas |
| `phenocam` | ~550 | text | – | archive to 2000s, 30 min | research sites |
| `iceland` | ~480 | text (is) | yes | – | Vegagerðin |
| `fotowebcam` | ~340 | catalog (`direction`, `sector`=hfov) | yes | 10-min archive, years | best quality/attribution |
| `tripcheck` | ~1,150 | filename suffix (NB/SW…) 47% | – | – | Oregon DOT: Cascades, coast, Gorge |
| `travelmidwest` | ~1,400 | `direction` letter 99% | – | – | Illinois DOT / Chicago corridor, one row per directional view, keyless GeoJSON |
| `austin` | ~820 | – | – | – | City of Austin CCTV (Socrata, public domain); ~20% placeholder frames, flagged by the health probe |
| `seattle` | ~650 | – | – | – | Seattle Travelers map: SDOT + WSDOT Puget Sound JPEGs (the only keyless WSDOT footage) |
| `al_algo` | ~610 | `direction` 96% | – | – | Alabama ALGO Traffic v4 API: JPEG snapshot + public HLS |
| `deldot` | ~360 | – | – | – | Delaware DOT, HLS only (ffmpeg frame) |
| `drivebc` | ~1,040 | `orientation` 100% + elevation | – | ReplayTheDay | British Columbia passes, PNG frames |
| `nzta` | ~250 | `direction` 99% | – | – | New Zealand state highways, CC BY 4.0 |
| `hk_td` | ~1,010 | text ("- Eastbound" suffix) ~78% | – | – | Hong Kong Transport Dept snapshots, 2-min, data.gov.hk |
| `sg_lta` | ~8 live (90 in archive) | – | – | any past minute via `?date_time=` | Singapore LTA 1080p; `image_url` empty, `fetch_frame` re-queries the API |
| `ndbc` | ~90 | 360° strip | – | – | BuoyCAMs, found by probing `buoycam.php` |
| `iem` | live only | catalog (`angle`) | – | per-minute archive | rows accumulate across refreshes |
| `windy` | ~1k/country | text from title | – | embed player day/month/year | needs `WINDY_API_KEY`; offset ≤1000/free tier |
| `pe_igp` | 13 | sector word (`Sector Noreste`) 100% | – | – | Instituto Geofísico del Perú (CENVUL) volcano cams: Sabancaya, Ubinas, Misti, Coropuna, Ticsani, Chachani, Yucamane, Sara Sara; JSON API, JPEG every ~1 min |
| `ec_igepn` | 26 | known site → summit bearing 54% | 3 thermal (IR) | – | IG-EPN Ecuador: Cotopaxi, Tungurahua, Reventador, Sangay, Guagua Pichincha, Sierra Negra (Galápagos); 1296×960 WebP ~2 min; "sin señal" card auto-flagged by the health probe |
| `co_sgc` | 20 | known site → summit bearing 40% | 2 thermal (IR) | – | Servicio Geológico Colombiano: Puracé, Galeras, Cumbal, Azufral, Sotará, Las Ánimas; up to 1920×1080 JPEG |
| `manual` | yaml | – | – | – | hand-picked: UAF Poker Flat + IRF Kiruna all-sky (aurora, `night_ok`) |

`uv run sunroof-camera refresh` builds every keyless source in ~3 min (~42k rows; the CARS portals are paged 100 at a time).

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
| `au_nsw_maritime:1` Merimbula bar | -36.889, 149.919 | 1080p HLS over the Pacific: sunrise, storms offshore, rainbows |
| `pe_igp:1` Sabancaya — Sector Noreste | -15.743, -71.810, ~4,800 m | South America; active volcano (ash plumes), high-Andes lenticulars, thunderstorms |
| `ec_igepn:sincholagua` Cotopaxi from Sincholagua | -0.550, -78.372, ~4,000 m | 1296×960, looks SSW at Cotopaxi's cone; `ec_igepn:rumIR` is the thermal twin (`night_ok`) |
| `co_sgc:galeras-consaca` Galeras from Consacá | 1.207, -77.466 | 1080p, looks E at Galeras; Andean afternoon convection |

Ready-to-run queries (`--t` is UTC, omit for now):

```bash
# Alpine sunset: Panomax panoramas around Großglockner, sun az≈276°
uv run sunroof-camera find sunset      --lat 47.07 --lon 12.70   --radius-km 5  --t 2026-09-20T17:30:00
# Andes: afternoon storm over Cotopaxi (Ecuador) — daytime returns the visible cams, at night only the IR twins
uv run sunroof-camera find thunderstorm --lat -0.68 --lon -78.44 --radius-km 15 --t 2026-09-20T20:00:00
# lenticulars over the Hohe Tauern (annulus 16–80 km for a 6 km cloud)
uv run sunroof-camera find lenticular  --lat 47.2  --lon 12.9    --radius-km 10
# Central Valley anvil seen from Bay Area ridge cams (33–150 km annulus)
uv run sunroof-camera find thunderstorm --lat 37.5 --lon=-121.5  --radius-km 20
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
# storm cell off the NSW south coast: Merimbula/Bermagui/Narooma 1080p HLS bar cams
uv run sunroof-camera find thunderstorm --lat=-36.7 --lon 150.3  --radius-km 20
# sunrise on the Gulf of Finland
uv run sunroof-camera find sunrise     --lat 60.05 --lon 24.0    --radius-km 5  --t 2026-09-21T04:00:00
```

Same thing from Python: `find_cameras(Event("sunset", 47.07, 12.70, 5, t), k=10)`.
