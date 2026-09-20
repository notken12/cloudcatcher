# Handoff: build the minimal weather side (`weather/events.py`)

You are picking up the weather half of a HackMIT project ("nimbly"): detect atmospheric events from live weather data, then
a teammate's camera side finds public webcams looking at them and verifies the event in the frame. The data sources have
already been validated end-to-end (read `validation/REPORT.md` first — especially §3 "where automation breaks" and §4 "recommended
minimal source set"). Your job is the glue: a 10-minute loop that emits an `events.json` the camera side can consume, plus a
replay mode. Do not build camera logic; do not touch `camera_ken/` or the teammate's files.

## Repo facts
- Path `/Users/ken/dev/nimbly`, branch `ken/data-validation` (base your work on it; commit to a new branch `ken/weather-events`).
- `uv` project. Run modules from the repo root: `uv run python -m weather.<module>`. No `__init__.py` anywhere (namespace packages;
  import directly from modules). All S3 access is unsigned via `weather/s3.py`. pygrib prints harmless `ECCODES ERROR ... Truncating time` lines.
- Coding rules (from the owner's CLAUDE.md, non-negotiable): simple and precise; reuse existing functions (restructure them if needed);
  no adapters/fallbacks/try-except unless truly necessary — ask instead; self-documenting names over comments; dependency injection;
  guard clauses over if/else nesting; imports at the top, never inside functions; resolve all type errors; think about 1000× scale
  (batch, never per-item network calls in loops); if something looks like a huge effort, stop and say so.

## Existing modules you will call (all verified live on 2026-09-20)
| module | use |
|---|---|
| `weather/mrms.py` | `probsevere_objects(when: datetime) -> list[GeoJSON feature]` — storm objects nearest `when`. Properties are **strings**; each feature gets `centroid_lat/centroid_lon/valid_time` added. Keys: `ID`, `COMPREF` (max dBZ), `FLASH_RATE` (/min), `MESH` (hail in), `EchoTop_50`, `MOTION_EAST`, `MOTION_SOUTH`, `SIZE`, `ProbSevere/ProbHail/ProbWind/ProbTor` (%; **absent in 2024-era archive files** — use `.get`). Polygon coords are `[lon, lat]`. Also `latest_object/object_at/download_grib/read_grid/cells_above` for raw MRMS if ever needed (not for v1). |
| `weather/hrrr.py` | `latest_run(now, forecast_hour, domain="conus"|"alaska")` (checks the file exists; CONUS hourly, f01 lands ~53 min after init; Alaska 3-hourly), `key_for(run, fhour, domain)`, `download_subset(key, out_dir) -> path` (byte-ranges ~10 MB of cloud fields via the `.idx`), `CloudGrid(path)` with `.sample(name, lats, lons)` for `lcc/mcc/hcc/tcc/ceil/base/top/orog/blh` (NaN off-grid; base/top/ceil NaN for thin cloud) and `.disk_mean(name, lat, lon, radius_km)`. |
| `weather/sun.py` | `sunset_utc(lat, lon, local_date, utc_offset_hours)`, `sun_azimuth(lat, lon, when)`, `sun_elevation(lat, lon, when)`. |
| `weather/sunset_rays.py` | `score_site(grid, lat, lon, sun_azimuth) -> {quality, depression_deg, horizon_block, site_lcc/mcc/hcc, site_base/ceil/top_km}`. ~10 ms per site. |
| `weather/sunset_rules.py` | `simple_rule(grid, lat, lon, sun_azimuth) -> {fires, ...}`. |
| `weather/swpc_aurora.py` | `ovation()`, `probability_at(ov, lat, lon)`, `aurora_rule(ov, lat, lon, when)`, `kp_1m()`, `solar_wind()`, `hemispheric_power_gw()`. |
| `common/geo.py` | `distance_km`, `bearing_deg`, `angle_diff_deg`, `point_along` (numpy-vectorised). |
| not needed for v1 | `weather/glm.py`, `goes_abi.py`, `nws.py`, `spc.py`, `climatology.py`, `openmeteo.py` (Open-Meteo is only for outside HRRR coverage; rate-limited per point). |

## What to build: `weather/events.py`
A CLI: `uv run python -m weather.events --out out/events.json [--when 2026-09-20T04:30Z] [--sites sites.json] [--loop 600]`.
Every run (or every `--loop` seconds) writes `events.json` atomically (write temp file, rename). With `--when`, everything reads the
archives for that instant instead of "now" (ProbSevere and HRRR archives go back to 2020-10 and 2014; `probsevere_objects(when)`
and `key_for(run, ...)` already support this).

### Event record (the contract with the camera side)
```json
{"id": "storm-771444-20260920T0430Z", "type": "storm", 
 "lat": 41.38, "lon": -91.68, "radius_km": 100,
 "t_start": "2026-09-20T04:30:00Z", "t_end": "2026-09-20T05:00:00Z",
 "score": 0.7,
 "needs_daylight": true, "needs_night_capable_camera": false,
 "look_bearing_hint": null,
 "evidence": {"COMPREF": 62.0, "FLASH_RATE": 21.0, "MESH": 0.64, "ProbSevere": 11.0, "motion_east_ms": 5.4, "motion_south_ms": 0.0, "probsevere_id": "771444", "valid_time": "2026-09-20T04:30:39Z"}}
```
`type` ∈ `storm | sunset | aurora`. `score` ∈ [0, 1]. `look_bearing_hint` is degrees (sunset: the solar azimuth; also emit
`anti_solar_bearing` in `evidence` because the show is sometimes on the anti-solar side — Lamar CO's 5/5 mammatus was in the East camera).
Write the file as `{"generated_at": ..., "when": ..., "sources": {...latency info...}, "events": [...]}`.

### The three rules (no ML)
1. **Storm** — from ProbSevere objects at `when`: keep objects with `COMPREF >= 50` and (`FLASH_RATE >= 5` or `ProbSevere >= 30`).
   `lat/lon` = polygon centroid; `radius_km` = 100 if the sun is above −6° at the centroid else 30; `t_start` = object valid time,
   `t_end` = +30 min; `score` = min(1, FLASH_RATE / 30); `needs_daylight` = sun elevation < −6° at centroid is false → true means
   "only verify with daylight or night-capable (ALERTCalifornia-class) cameras"; set `needs_night_capable_camera` = not daylight.
   Attach motion (m/s east/south from `MOTION_EAST/MOTION_SOUTH`) so the camera side can lead the target.
2. **Sunset** — on a 0.5° lattice over CONUS (24–50 N, −125…−66 E, ~2,900 points): for each point compute the next local sunset
   after `when` (use `sunset_utc` with `utc_offset_hours = round(lon / 15)` and today's/tomorrow's local date; take the first sunset > when);
   keep points whose sunset+15 min is within the next 3 h. Group points by the HRRR forecast hour nearest their sunset+15 (use
   `latest_run(when, fhour)` for fhour 1–3 of the latest run; one `CloudGrid` per forecast hour — never one download per point).
   Score with `score_site(grid, lat, lon, sun_azimuth(lat, lon, sunset))`; emit where `quality >= 0.2` with `radius_km` = 40,
   `t_start` = sunset+5, `t_end` = sunset+25, `score` = min(1, quality / 0.5), `look_bearing_hint` = solar azimuth,
   `evidence` = the score dict + `simple_rule(...)['fires']`. Tell the camera side in the README that this is a **prior, not a
   classifier**: ρ≈0 against observed colour on one West-Coast evening, positive in Alaska/Lamar/Zugspitze; their colour index / vision
   model must make the final call. Alaska: only add the `alaska` domain if the camera side confirms they use FAA's Alaska cams.
3. **Aurora** (optional, behind `--aurora`) — same lattice north of 50 N plus Alaska: `aurora_rule(ov, lat, lon, when)['fires']`
   and HRRR `lcc` disk mean ≤ 30 %; `radius_km` 150, `t_start` = now, `t_end` = OVATION forecast time + 30 min, `score` =
   probability / 50, `needs_night_capable_camera` = true (aurora only shows on all-sky/long-exposure cams).

### `--sites sites.json` (optional, for direct integration)
If the camera team hands you their site catalog (`[{id, lat, lon}]`), also write `out/site_scores.json`: per site the sunset score
at its next sunset+15, the nearest storm event (km, bearing from the site), and the aurora rule — so they can join on `id`.

## Verified facts and gotchas you must respect
- ProbSevere: 2-min files, ~2 min upload lag; key `ProbSevere/YYYYMMDD/MRMS_PROBSEVERE_YYYYMMDD_HHMMSS.json`; `MLON` is truncated —
  never use it, use the centroid. ~200 objects CONUS-wide on an active evening.
- HRRR: the "latest" run's f01 is 0–1.9 h stale at any moment — record the run and forecast hour in `sources`. Cloud base/top are NaN
  for thin cirrus even at 100 % high cloud; the ray model already handles that. Off-grid points return NaN (mask them; the lattice
  edge in Canada/Mexico will be NaN).
- Timing: sunset colour peaks 10–20 min after sunset (2–3° depression), so windows are sunset+5 … +25. Storm "peak" frames last <8 min
  while FAA cams refresh every 8–10 min — irrelevant to you but explains why the camera side wants `t_end` short and motion vectors.
- Night: still cameras cannot verify a storm after dark (validated: 0/3 at 136 km). That's what `needs_daylight` is for.
- GOES-East is `noaa-goes19` (goes16 is empty) if anyone adds GLM later; `api.weather.gov` needs a User-Agent; none of this is needed for v1.

## Acceptance checks (write them as `weather/test_events.py`, runnable with `uv run python -m weather.test_events`; no pytest needed)
1. Regression: HRRR `hrrr.20260920/conus/hrrr.t00z.wrfsfcf01.grib2` subset, site (38.077, −102.696), azimuth 271.8 → `score_site` quality
   **0.418**, depression 1, horizon_block 0.0, site_hcc 93 (this frame was a 5/5 sunset).
2. Replay: `--when 2024-04-26T20:30Z` emits a storm event whose centroid is ≤ 15 km from (41.1447, −96.4616) with `MESH` 1.30 and
   `FLASH_RATE` 38 (EF4 day near Mead NE; note the 2024 file has no `ProbSevere` key).
3. Live: a run with no `--when` completes in < 90 s, `events.json` validates against the schema above, and `sources` reports ProbSevere
   age ≤ 5 min and the HRRR run/fhour used.
4. Aurora replay check: `--when 2026-09-20T04:40Z --aurora` fires at Yellowknife (62.45, −114.37) with probability ≈ 26 % (OVATION is
   live-only, so this check may only hold approximately; say so in the test).

## Deliverables
- `weather/events.py`, `weather/test_events.py`, a short section in `weather/README.md` describing the CLI and the JSON contract
  (copy the record example verbatim), and a sample `out/events.json` from a live run committed under `validation/` (not `out/`, which
  should be git-ignored).
- One commit on `ken/weather-events` with a message that says what was built and what the acceptance checks returned. End the commit
  message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- If anything in the existing modules needs restructuring to avoid duplication, do it there rather than re-implementing; if you find a
  data problem the report doesn't mention, add it to `validation/REPORT.md` §3.
