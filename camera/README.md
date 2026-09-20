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
```

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

Secrets: `WINDY_API_KEY` etc. via environment only; never in the repo.

### Sources implemented

| source | rows (Sep 2026) | heading | night | history | notes |
|---|---|---|---|---|---|
| `caltrans` | ~3,300 | text 88% | – | last 12 frames | 12 district JSONs, JPEG + HLS |
| `cars_ny` / `cars_on` | ~1,800 / ~1,400 | text ~50-70% | – | – | 511 portals; NY optional `NY511_API_KEY` |
| `alertca` | ~1,800 | catalog (pan) | IR subset | – | ridge-top PTZ, firestorm mirror |
| `digitraffic` | ~1,700 | – | yes | 24 h API | Finland, CC BY 4.0 |
| `panomax` | ~630 | catalog (zeroDirection+viewAngle/2) | `nightVision` | recent API | Alpine panoramas |
| `phenocam` | ~550 | text | – | archive to 2000s, 30 min | research sites |
| `iceland` | ~480 | text (is) | yes | – | Vegagerðin |
| `fotowebcam` | ~340 | catalog (`direction`, `sector`=hfov) | yes | 10-min archive, years | best quality/attribution |
| `tripcheck` | ~1,150 | filename suffix (NB/SW…) 47% | – | – | Oregon DOT: Cascades, coast, Gorge |
| `drivebc` | ~1,040 | `orientation` 100% + elevation | – | ReplayTheDay | British Columbia passes, PNG frames |
| `nzta` | ~250 | `direction` 99% | – | – | New Zealand state highways, CC BY 4.0 |
| `ndbc` | ~90 | 360° strip | – | – | BuoyCAMs, found by probing `buoycam.php` |
| `iem` | live only | catalog (`angle`) | – | per-minute archive | rows accumulate across refreshes |
| `windy` | ~1k/country | text from title | – | embed player day/month/year | needs `WINDY_API_KEY`; offset ≤1000/free tier |
| `manual` | yaml | – | – | – | hand-picked (all-sky, YouTube) |

`uv run sunroof-camera refresh` builds every keyless source in ~15 s (~14.5k rows).

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
# sunrise on the Gulf of Finland
uv run sunroof-camera find sunrise     --lat 60.05 --lon 24.0    --radius-km 5  --t 2026-09-21T04:00:00
```

Same thing from Python: `find_cameras(Event("sunset", 47.07, 12.70, 5, t), k=10)`.
