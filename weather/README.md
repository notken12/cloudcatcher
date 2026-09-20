# weather

Data-source clients from the validation run (`validation/REPORT.md`). All S3 access is unsigned; run from the repo root
with `uv run python -m weather.<module>`.

| module | source | notes |
|---|---|---|
| `s3.py`, `grib.py` | unsigned boto3, pygrib helpers | |
| `grib_subset.py` | byte-range subset of a GRIB2 file from its .idx sidecar (HRRR, GFS) | instantaneous messages only; cached per field count |
| `mrms.py` | MRMS composite reflectivity / PrecipRate / ProbSevere | 2-min, 1.5 MB CONUS files, archive 2020-10→; ProbSevere objects are the ready-made event record; **CONUS only** |
| `glm.py` | GLM flashes | 20-s files, ~4 s lag; goes16 before 2025-04-07, goes19 after |
| `goes_abi.py` | ABI L2 products + `FixedGrid` (geostationary lat/lon→pixel, x or y sweep axis) | `ACHTC` does not exist on GOES-19 |
| `cloud_columns.py` | the cloud seam: `CloudColumns` (cover/opacity/envelope per band) + layer constants | implemented by `cloud_grid.LayerCloudField` and `satellite_cloud.SatelliteCloudField` |
| `cloud_grid.py` | `CloudGrid` sampler for HRRR/GFS subsets, `LayerCloudField` (layer cover → columns) | GFS has no cloud base/top: its clouds fill their bands |
| `hrrr.py` | HRRR keys/runs and its field list (cover, base/ceiling/top, terrain, land, RH) | 3 km, hourly, CONUS + Alaska; 10 MB / ~1 s; base/top NaN for thin cirrus |
| `gfs.py` | GFS 0.25° keys/runs and its field list (cover, ceiling, terrain, land, RH, REFC, CAPE) | **global**; 6-hourly runs, hourly steps, ~4 h lag; 10 MB / ~1 s |
| `satellite_cloud.py` | `SatelliteCloudField`: cloud-top height + optical depth on a fixed grid → columns | sees the cirrus models miss (REPORT §3.15-16); 48 B/pixel |
| `goes_cloud.py` | GOES-18/19 CONUS ACHA2KM + COD (2 km, 5 min) → `SatelliteCloudField` | COD DQF is a bitmask; HT NaN = clear |
| `himawari_cloud.py` | Himawari-9 full-disk AHI-CHGT (height + COD + QF, 2 km, 10 min) → `SatelliteCloudField` over Asia-Pacific | 765 MB files read by ranged HDF5 chunk reads (~30 MB per band window, 10 s); ~25 min upload lag; no projection metadata, grid from AHI constants (y sweep, pixel-exact) |
| `nws.py` | live alerts (User-Agent required) and IEM VTEC archive | api.weather.gov has no history |
| `spc.py` | SPC daily / yearly storm reports | yearly times are CST |
| `climatology.py` | Open-Meteo ERA5 percentiles | good for cloud layers, useless for precip (zero-inflated); `cape` not in archive |
| `synop.py` | SYNOP (FM-12) surface reports from OGIMET: observed cloud **genus** (low/middle/high) and oktas at manned stations; ISD station coordinates | the only free worldwide observation of cloud type; ~4,500 genus stations per 3 h, none in the US (ASOS); one 3.5 MB world request per hour |
| `sun.py` | astral wrappers | |
| `sunset_rules.py` | simple mid/high-over-site + clear-ray rule | |
| `sunset_rays.py` | Sunsethue-style ray model | implemented, **not validated** (ρ≈0 on one evening) |
| `sunset_quality.py` | Sunsethue-whitepaper two-phase classifier (reflection-potential fan + view-ray fan + humidity/duration post-processing) on any `CloudField` | drives `--sunset` and `--sites`; validation in `validation/sunset_goes_eval.py` |
| `sunset_scan.py` | score a site list against a subset | `uv run python -m weather.sunset_scan subset.grib2 sites.json out.json` |
| `swpc_aurora.py` | SWPC OVATION / 1-min Kp / RTSW solar wind / hemispheric power | live-only, both hemispheres |
| `events.py` | worldwide event detector: storms (observed over CONUS, GFS prior elsewhere) + optional aurora + optional sunset sweep + per-site scores | see below |
| `test_events.py` | acceptance checks | `uv run python -m weather.test_events` |

## events.py — the events.json contract

```
uv run python -m weather.events --out out/events.json [--when 2026-09-20T04:30Z] [--sunset] [--aurora] [--sites sites.json] [--post http://localhost:8000] [--loop 600]
```

Writes `events.json` atomically every run (or every `--loop` seconds), worldwide. `--when` replays an
instant from the S3 archives (ProbSevere back to 2020-10, HRRR to 2014, GFS and the satellites for the
buckets' retention; OVATION is live-only, so `--aurora` with `--when` scores current space weather).
`--sites sites.json` (`[{id, lat, lon}]`) additionally writes `site_scores.json` next to `--out`: per
site the sunset score at its next sunset+15 min, the nearest storm event (km, bearing), and the aurora
rule — join on `id`.

**Cloud field per point** (`events.CloudFields.choose`): the newest GOES CONUS scan where it covers
the point (`model: sunset_quality/goes`), else the newest Himawari-9 full disk (`…/himawari`, Asia,
Australia, western Pacific), else the HRRR layer cover where its grid covers the point (`…/hrrr`), else
GFS (`…/gfs`, everywhere). Humidity for the classifier comes from HRRR or GFS the same way. On the
West-Coast test evening the classifier scores ρ 0.48 on GOES, 0.23 on HRRR and 0.21 on GFS against
observed colour (`validation/sunset_goes_eval.py`); Himawari is unvalidated (no colour index outside the US).

**Observed cloud genus** (`synop.py`): the nearest manned SYNOP station within 150 km that reported in the
3 h before the target attaches its observation to the score (`synop: {station, observed_at, total_oktas,
low, middle, high, genus_factor, distance_km}`) and scales the severity by a bounded genus prior:
×1.2 per level reporting thin or broken middle/high cloud (altocumulus, cirrus, cirrocumulus, cirrostratus
not covering the sky), ×0.7 per level reporting an opaque layer (altostratus opacus, cirrostratus covering
the sky, stratocumulus, stratus, fractus), clipped to 0.5–1.4; `severity = min(1, quality × factor / 0.06)`.
The factor is physics, not fitted: no colour index exists where genus is reported (the US test frames have
none), so it is deliberately a nudge. What SYNOP *does* validate is the fields themselves —
`validation/synop_field_check.py` compares observer oktas and per-level presence with GFS, Himawari and
GOES cover at the stations.

Event record — a camera-side `WeatherEvent` (`camera/src/sunroof_camera/footage.py`), which the
camera service accepts on `POST /events`; `--post http://host:port` sends each record there:

```json
{"id": "storm-771444-20260920T0430Z", "type": "thunderstorm",
 "lat": 41.38, "lon": -91.68, "radius_km": 100,
 "t_start": "2026-09-20T04:30:00Z", "t_end": "2026-09-20T05:00:00Z",
 "severity": 0.7, "replay": false,
 "needs_daylight": true, "needs_night_capable_camera": false,
 "look_bearing_hint": null,
 "evidence": {"COMPREF": 62.0, "FLASH_RATE": 21.0, "MESH": 0.64, "ProbSevere": 11.0,
              "motion_east_ms": 5.4, "motion_south_ms": 0.0, "probsevere_id": "771444",
              "valid_time": "2026-09-20T04:30:39Z"}}
```

`type` ∈ `thunderstorm | sunset | aurora | lenticular | rare_cloud` (camera-side `EventType` names, except
`rare_cloud`, which the camera side still has to add); `severity` ∈ [0, 1];
`replay` is true under `--when` (the camera side then fetches archive frames at `t_start`). The
camera service reads `id, type, lat, lon, radius_km, t_start, t_end, severity, replay` and picks
cameras itself (for sunset: sun-facing cameras within 50 km). The remaining keys are advisory:
`needs_daylight` means the sun is above −6° at the event (a normal still camera can verify);
`needs_night_capable_camera` is its negation — at night the storm radius shrinks to 30 km (only
verify with a close or night-capable/ALERTCalifornia-class camera). `look_bearing_hint` is degrees;
sunset events also carry `anti_solar_bearing` in evidence (the show is sometimes on the anti-solar
side — Lamar's 5/5 mammatus was in the *East* camera; the camera side has no anti-solar path for
sunset yet).

Rules:

- **thunderstorm** — over CONUS (20–55°N, 130–60°W), ProbSevere objects with COMPREF ≥ 50 and
  (FLASH_RATE ≥ 5 or ProbSevere ≥ 30); in 2024-era files COMPREF/ProbSevere don't exist; MESH ≥ 0.5 in
  substitutes for COMPREF and `PS` for `ProbSevere`. Evidence carries motion (m/s east/south) so the
  camera side can lead the target. Elsewhere a **model prior** (`evidence.model = gfs_refc`): each
  connected blob of GFS simulated composite reflectivity ≥ 45 dBZ with CAPE ≥ 300 J/kg at the step
  nearest `when` (≥ 40 dBZ alone flags stratiform rain: 181 blobs vs 12 on the test evening),
  `severity = (max REFC − 45)/15`, `radius_km = max(30, 14·√cells)`, ±30 min. There is no free
  global radar or lightning feed; this is a forecast, not a detection.
- **rare_cloud** / **lenticular** — always on: every manned SYNOP station that reported, within the last
  90 min, a genus that is a sight in itself becomes an event at the station (`radius_km` 50, valid 90 min,
  `evidence.genus` names it, `evidence.synop` carries the whole observation). Genera and severities, from
  their share of the world's reports: chaotic sky 1.0, cirrus from cumulonimbus 0.9, cirrocumulus 0.8,
  altocumulus castellanus 0.8, cirrostratus covering the sky (halo weather) 0.6, cirrus uncinus 0.6;
  altocumulus lenticularis goes out as the camera side's own `lenticular` type (0.9). A lee-wave day
  produces one event per station in the wave train (38 over Czechia/Slovakia at 07Z on 2026-09-20); the
  camera side needs `rare_cloud` added to its `EventType` before it will accept the six.
- **aurora** (`--aurora`) — 0.5° lattice, 50–71° in both hemispheres, all longitudes: OVATION
  probability ≥ 20, sun elevation ≤ −6°, GFS lcc disk ≤ 30 %. `severity = min(1, probability/50)`,
  `needs_night_capable_camera = true` (all-sky/long-exposure cams only).
- **sunset** (`--sunset`) — 1° land lattice (GFS `LAND`), 60°S–70°N; a point is scored when its
  sunset+15 min is within 90 min, by the Sunsethue-whitepaper classifier on the best cloud field
  there (see above). `severity = min(1, quality/0.06)` (0.06 ≈ the best sites of the West-Coast test;
  Lamar 5/5 via GOES 0.05); every point with severity ≥ 0.5 becomes an event with `radius_km` 60,
  `t_start`/`t_end` = sunset+5/+25 min, `look_bearing_hint` = solar azimuth — no peak-picking,
  because the camera side only searches 50 km around each point, so a good region has to be tiled.
  Only the ~22° longitude band whose sunset is imminent is scored (clear points cost ~30 ms, cloudy
  ones ~0.3 s), so a `--loop 600` run keeps up as sunset circles the globe. The sunset severity is a
  **prior, not a verdict** (REPORT §3.16) — the camera-side colour index / vision model makes the
  final call.
