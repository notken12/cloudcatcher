# Camera preprocessing & query plan (reference for the implementer)

Goal: turn the sources in `sources.md` into one mini-database that answers

```
find_cameras(event: {type, lat, lon, radius_km, t_start, t_end}, k) -> [CameraHit]
```

in < 50 ms, with each hit carrying a URL the VLM can fetch **right now** and a
reason string the frontend can show ("14 km NW of the storm, camera faces 310°").

## 0. Tooling

- `uv` project under `camera/`, `ruff` + `ty` for lint/types (Astral suite, as
  requested). `uv run camera ingest --source caltrans` style CLI via `typer`.
- **`astral`** (the PyPI package; unrelated to the company) for sun
  elevation/azimuth, civil/nautical/astronomical dawn & dusk, moon phase. It's
  pure Python, timezone-aware, and does everything we need. Don't reach for
  `skyfield`/`pvlib` unless we need moon *altitude* (only matters for
  aurora-vs-moonlight scoring; skip for the hackathon).
- `httpx` (async, HTTP/2, per-host rate limits), `pydantic` models, `duckdb`
  with the `spatial` extension as the mini-database (single file, zero ops,
  `ST_Within`/`ST_Distance_Sphere`, reads/writes GeoParquet). `shapely` to
  build the sector polygons. `pillow` + `numpy` for sky-fraction heuristics,
  `ffmpeg`/`yt-dlp` for HLS & YouTube frame grabs. `playwright` only for the
  FAA token and UAF all-sky page.
- Why not Postgres/PostGIS: the catalog is ~20–90k rows. DuckDB file in the
  repo (or S3) is faster to set up, and the other Devin can `duckdb.connect()`
  it immediately. If the backend already runs Postgres, the same schema ports
  1:1 (`GEOMETRY` → `geography`).

## 1. Data model

One table, `cameras`, one row per **view** (a PTZ preset, an NDBC panel, a
Panomax instance are each a row). Keep it flat; the backend team should not
need joins.

```sql
CREATE TABLE cameras (
  id            TEXT PRIMARY KEY,   -- '{source}:{source_id}[:{view}]'
  source        TEXT,               -- 'caltrans','digitraffic','panomax',...
  source_kind   TEXT,               -- 'jpeg' | 'hls' | 'embed' | 'page'
  name          TEXT,
  lat DOUBLE, lon DOUBLE, alt_m DOUBLE,      -- alt from catalog or SRTM lookup
  tz            TEXT,               -- IANA, via timezonefinder, for astral
  -- orientation (the "region of sky" answer, see §3)
  azimuth_deg   DOUBLE,             -- center of view, NULL if unknown
  hfov_deg      DOUBLE,             -- 360 for all-sky/panorama, ~60 default
  elev_min_deg  DOUBLE,             -- bottom of frame above horizon (neg = looks down)
  elev_max_deg  DOUBLE,             -- top of frame; 90 for all-sky
  heading_conf  TEXT,               -- 'catalog' | 'text' | 'inferred' | 'ptz' | 'unknown'
  footprint     GEOMETRY,           -- ground-projected sector polygon (WGS84), §3
  sky_frac      DOUBLE,             -- fraction of frame that is sky in a daytime sample
  -- capability flags
  night_ok      BOOLEAN,            -- IR / long exposure / all-sky / nightVision
  all_sky       BOOLEAN,
  ptz           BOOLEAN,
  over_water    BOOLEAN,
  -- fetch
  image_url     TEXT,               -- direct JPEG (may be templated with {ts})
  stream_url    TEXT,               -- HLS
  embed_url     TEXT,               -- iframe / youtube embed
  page_url      TEXT,               -- attribution / deep link
  refresh_s     INTEGER,            -- expected cadence
  history_kind  TEXT,               -- 'none' | 'last_n' | 'url_template' | 'api'
  history_template TEXT,            -- e.g. foto-webcam '/{slug}/{Y}/{m}/{d}/{H}{M}_la.jpg'
  history_depth_days INTEGER,
  license       TEXT, attribution TEXT, embed_allowed BOOLEAN,
  -- health (updated by the freshness job, §4)
  last_frame_ts TIMESTAMP, last_ok_ts TIMESTAMP, fail_streak INTEGER,
  health        TEXT,               -- 'live' | 'stale' | 'dead' | 'unverified'
  quality_score DOUBLE              -- 0..1, §5
);
CREATE INDEX cameras_geo ON cameras USING RTREE (footprint);
```

Plus `camera_samples(camera_id, ts, url, sha1, bytes, solar_elev, vlm_label)`
— every frame we ever fetched, so backtests and VLM labels accumulate.

## 2. Ingest = one adapter per source

`camera/ingest/sources/{name}.py` exposes

```python
class Adapter(Protocol):
    source: str
    async def catalog(self) -> list[RawCamera]      # hit the catalog endpoint
    def normalize(self, raw: RawCamera) -> Camera   # fill the row above
    async def fetch_frame(self, cam: Camera, ts: datetime | None) -> Frame
```

Adapters in build order (each is ~50 lines; the CARS one is reused 6×):
`alertca`, `caltrans`, `cars` (Ontario, NY, later UT/LA/IA…), `digitraffic`,
`iceland`, `panomax`, `fotowebcam`, `ndbc`, `phenocam`, `iem`, `nps`, `usgs`,
`windy` (key), `faa` (token), `manual` (YAML of hand-picked YouTube/embeds).

Adapter-level edge cases already seen in probing:
- gzip-only responses (Panomax, Digitraffic) → `httpx` handles; send
  `Accept-Encoding: gzip` explicitly.
- Required identity headers (`Digitraffic-User`), empty-but-required key (`511ny key=`).
- Catalog says enabled but frame is a placeholder ("camera unavailable" JPEG):
  hash the frame; if the sha1 matches a known placeholder set for that source
  → treat as no frame. Also reject frames < 3 KB and frames identical to the
  previous fetch beyond 3× `refresh_s`.
- Redirect-to-HTML instead of image (IRF) → content-type check, mark `page`.
- Rate limits (Bergfex 429, foto-webcam etiquette) → per-host token bucket,
  `Retry-After` honoured, exponential backoff, and **never** more than 1 rps
  to a hobby/volunteer host.
- Signed/expiring URLs (Windy) → store the *API* reference, resolve at query time.
- Timestamps: Caltrans gives local strings without tz, Panomax gives ISO with
  offset, IEM gives UTC. Normalize with the camera's `tz`. If a source gives no
  timestamp, read EXIF `DateTimeOriginal`, else use fetch time and flag
  `ts_source='fetch'`.
- Duplicate cameras across sources (Windy re-lists Panomax/foto-webcam) →
  dedupe on (round(lat,3), round(lon,3), azimuth±15°) and keep the row from
  the higher-priority native source.

## 3. Storing "which region of the sky" — the easiest thing that works

Don't store sky polygons on the celestial sphere. Store the camera's **viewing
cone** (azimuth, hfov, elev range) and precompute a **ground footprint**: the
sector of the Earth's surface over which sky features at typical altitude are
inside the frame. Then "does camera C see event E?" becomes a plain
point-in-polygon / distance test, which every geo index does.

Footprint construction (`shapely`, once at ingest):

```
R(type)  = max ground distance at which the feature is visible above the frame bottom
         = clamp( h_feature / tan(max(elev_min_deg, 0.5°)) , 5 km, R_cap(type) )
sector   = wedge centred at (lat,lon), bearing azimuth_deg ± hfov/2, radius R
all-sky / hfov>=300  → circle of radius R
unknown heading      → circle of radius R * 0.6 (penalised, still returned)
```

We store **one generic footprint** using h_feature = 8 km (mid-troposphere,
R_cap 120 km) for the index, and apply the per-type radius at query time from
this table:

| event type | h_feature | R_cap | extra directional rule |
|---|---|---|---|
| sunrise / sunset | — | 0 km (event *is* the camera's location) | sun azimuth at t (astral) must be inside `[azimuth ± hfov/2]`; solar elev in [-6°, +6°]; camera must have `elev_min <= 2°` |
| thunderstorm | 12 km (anvil) | 150 km | prefer cameras **outside** the storm core (10–80 km) looking at it |
| lightning | 8 km | 60 km | night_ok not required (lightning lights the frame), but `refresh_s <= 60` or HLS strongly preferred |
| mammatus | 6 km | 40 km | usually under/at edge of anvil → cameras within 40 km, any heading, `elev_max >= 40°` |
| lenticular | 6 km | 80 km | mountain lee; heading toward the mountain wins; Panomax/foto-webcam boost |
| fog / undercast | 0.3 km | 15 km | **camera above the fog**: `alt_m > fog_top` (from METAR/ceiling) → undercast; camera inside → fog. Both are valid, label differently |
| aurora | 110 km | 600 km | `night_ok` required, solar elev < -12°, bearing to magnetic-north-ish arc inside hfov or all-sky; moon phase penalty |
| rainbow | — | 5 km | camera must look at antisolar azimuth (sun az + 180 ± hfov/2), sun elev in (0°, 42°), rain within 10 km on the antisolar side |

How each source fills orientation:

| heading_conf | how | sources |
|---|---|---|
| `catalog` | numeric az/FOV in metadata | Panomax (`zeroDirection`, `viewAngle`), PhenoCam (`camera_orientation`), IEM (`angle` per frame), FAA (per cam) |
| `text` | parse N/NE/…/"séð til vesturs"/"looking west"/"Richtung Süd" → 8-point compass, hfov default 60 | Caltrans `direction`, CARS `Views[].Direction`, Iceland `Skyring`, NPS/USGS captions |
| `inferred` | road geometry (Digitraffic `INCREASING_DIRECTION` + OSM way bearing), NDBC panel index × 60° | Digitraffic, NDBC |
| `ptz` | none stable; footprint = circle; re-estimate at query time from the frame if we ever add a horizon-matching model | ALERTCalifornia PTZ, DOT PTZ |
| `unknown` | circle × 0.6 | NY 511, Windy without direction, YouTube unless we hand-enter |

Cheap `sky_frac` estimator at ingest (one daytime frame per camera): fraction
of pixels in the top 60 % of the image that are blue/grey-dominant with low
local variance. It is wrong often enough that it only feeds `quality_score`,
never a hard filter; the VLM is the real gate.

## 4. Freshness, night, and the "is it maintained" problem

Two scheduled jobs:

1. **Catalog refresh** (daily): re-pull every catalog, upsert rows, mark rows
   that disappeared as `dead` after 3 misses.
2. **Health probe** (every 10 min for `live`, hourly for `stale`, daily for
   `dead`): `HEAD`/`GET` the frame, record `last_frame_ts` (from the source
   timestamp, EXIF, or `Last-Modified`) and the sha1.
   `health = live` if `now - last_frame_ts <= 2 * refresh_s`,
   `stale` up to 24 h, else `dead`. `fail_streak` gives hysteresis.

Night handling (this is where most naive pipelines throw away half the planet):

- Compute `solar_elev(cam, t)` with `astral` at query time, not ingest.
- `solar_elev > -6°` → all healthy cameras eligible.
- `-6° … -18°` (twilight) → eligible if `night_ok` **or** event ∈ {lightning,
  sunset/sunrise afterglow}; ordinary cams get a 0.5 score multiplier.
- `< -18°` → only `night_ok` cameras, except lightning (any cam with
  `refresh_s <= 60`). Aurora requires this band.
- `night_ok` is set from: Panomax `nightVision`, foto-webcam (all, long
  exposure), all-sky flags, ALERTCalifornia IR models, FAA Alaska sites
  (verify), manual YAML. It is **confirmed or revoked** by the VLM: if three
  night frames of a `night_ok` camera come back "black/unusable", flip it.
- Reliability data on night cameras is thin in the catalogs, so we generate it:
  the health job stores mean luminance + a "usable" bit per sample; after one
  night we have per-camera night usability curves. Ship that as
  `night_usable_frac` on the row.

## 5. Ranking

```
score = w_geo * geo_fit(type, dist, bearing_delta)     # from §3 table
      + w_fresh * exp(-(now - last_frame_ts) / refresh_s)
      + w_sky   * sky_frac
      + w_src   * source_prior            # panomax/fotowebcam .9, DOT .5, unknown .3
      + w_night * night_multiplier
      - dup_penalty (same location already in top-k)
```

Return top `k*3` to the orchestrator; it fetches frames for those, runs the
VLM (gated/deduped so we don't pay for identical frames — this is the Token
Company angle), and publishes the top `k` that pass. Every query is logged
with its explanation so the frontend can show "why this camera".

## 6. Historical / replay mode

Same `find_cameras` with `t_end` in the past. Only rows with
`history_kind != 'none'` and `history_depth_days` covering `t` are eligible;
`fetch_frame(cam, ts)` fills the template (foto-webcam, IEM, PhenoCam) or
walks `previousImageURL` (Caltrans). Ship a `replay/` directory with 3–4
canned events (a Front Range mammatus day from IEM/Denver, a Lapland aurora
night from Digitraffic, an Alpine undercast morning from foto-webcam, a Miami
thunderstorm from FL 511) so the demo never depends on tonight's weather.

## 7. Sponsor tools — verdict

- **Elasticsearch: don't make it the source of truth.** A plain
  `geo_distance` filter is exactly the "trivial query" that wins nothing and
  costs debugging time. There *is* a legitimately sophisticated use: index
  `footprint` as `geo_shape`, events as `geo_shape` too, use
  `function_score`/`rank_feature` for §5, `geo_shape` intersection for the
  sector-vs-event-polygon test, and ES|QL for the "find the signal"
  dashboards over `camera_samples` (which cameras are reliable at night, per
  region, over time). If we do this, do it as a **mirror**: DuckDB stays
  canonical, a 30-line job pushes rows to Elastic, and `find_cameras` has a
  `backend=` switch. If Elastic misbehaves during the hackathon we flip the
  switch and lose nothing. Decide on day 2 based on time left.
- **Voloridge (data/quant challenge): yes, cheaply.** `camera_samples` +
  IEM/PhenoCam/foto-webcam archives give a labelled time series; rarity
  scoring (how unusual is *this* mammatus day for *this* region) and
  detector backtests are precisely their brief. Reuse, don't build extra.
- **The Token Company: yes, it falls out of the design.** Frame dedupe by
  sha1, luminance gating, "only send the sky crop", and caching VLM verdicts
  per (camera, 10-min bucket) are all cost cuts we need anyway; instrument
  them and report tokens saved.
- **OpenAI: the VLM gate itself.** Structured output
  `{usable: bool, sky_visible: float, event_visible: enum, night: bool}`.

## 8. Build order (for the next session)

1. `uv init`, models, DuckDB schema, `manual.yaml` with 10 hand-picked cams → `find_cameras` works end-to-end on 10 rows in hour 1 so the backend Devin can integrate.
2. Adapters: caltrans → cars(ontario, ny) → panomax → digitraffic → iceland → alertca → fotowebcam → ndbc → iem → phenocam.
3. Health job + `astral` night logic + sky_frac.
4. Windy + FAA (keys), replay fixtures, Elastic mirror if time.
