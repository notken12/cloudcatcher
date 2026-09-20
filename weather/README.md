# weather

Data-source clients from the validation run (`validation/REPORT.md`). All S3 access is unsigned; run from the repo root
with `uv run python -m weather.<module>`.

| module | source | notes |
|---|---|---|
| `s3.py`, `grib.py` | unsigned boto3, pygrib helpers | |
| `mrms.py` | MRMS composite reflectivity / PrecipRate / ProbSevere | 2-min, 1.5 MB CONUS files, archive 2020-10→; ProbSevere objects are the ready-made event record |
| `glm.py` | GLM flashes | 20-s files, ~4 s lag; goes16 before 2025-04-07, goes19 after |
| `goes_abi.py` | ABI L2 (band 13 CONUS, ACHAC cloud-top height) + lat/lon→pixel | `ACHTC` does not exist on GOES-19 |
| `goes_cloud.py` | `GoesCloudField`: ACHA2KM cloud-top height + COD (both 2 km) as the classifier's cloud columns | COD DQF is a bitmask; HT NaN = clear. Sees the cirrus HRRR misses (REPORT §3.15-16) |
| `cloud_columns.py` | the cloud seam: `CloudColumns` (cover/opacity/envelope per band) + layer constants | implemented by `hrrr.HrrrCloudField` and `goes_cloud.GoesCloudField` |
| `hrrr.py` | byte-range cloud subset from the .idx (cover, base/ceiling/top, terrain, land mask, RH), `CloudGrid` sampler, `HrrrCloudField` | 10 MB / ~1 s; base/top NaN for thin cirrus; mask off-grid points |
| `nws.py` | live alerts (User-Agent required) and IEM VTEC archive | api.weather.gov has no history |
| `spc.py` | SPC daily / yearly storm reports | yearly times are CST |
| `climatology.py` | Open-Meteo ERA5 percentiles | good for cloud layers, useless for precip (zero-inflated); `cape` not in archive |
| `sun.py` | astral wrappers | |
| `sunset_rules.py` | simple mid/high-over-site + clear-ray rule | |
| `sunset_rays.py` | Sunsethue-style ray model | implemented, **not validated** (ρ≈0 on one evening) |
| `sunset_quality.py` | Sunsethue-whitepaper two-phase classifier (reflection-potential fan + view-ray fan + humidity/duration post-processing) on any `CloudField` | wired into `--sites` on the GOES field; validation in `validation/sunset_goes_eval.py` |
| `sunset_scan.py` | score a site list against a subset | `uv run python -m weather.sunset_scan subset.grib2 sites.json out.json` |
| `swpc_aurora.py` | SWPC OVATION / 1-min Kp / RTSW solar wind / hemispheric power | live-only |
| `events.py` | event detector: storms + optional aurora + per-site scores | see below |
| `test_events.py` | acceptance checks | `uv run python -m weather.test_events` |

## events.py — the events.json contract

```
uv run python -m weather.events --out out/events.json [--when 2026-09-20T04:30Z] [--sites sites.json] [--loop 600] [--aurora]
```

Writes `events.json` atomically every run (or every `--loop` seconds). `--when` replays an instant
from the S3 archives (ProbSevere back to 2020-10, HRRR to 2014; OVATION is live-only, so `--aurora`
with `--when` scores current space weather). `--sites sites.json` (`[{id, lat, lon}]`) additionally
writes `site_scores.json` next to `--out`: per site the sunset score at its next sunset+15 min,
the nearest storm event (km, bearing), and the aurora rule — join on `id`. The sunset score comes
from the classifier on the newest GOES scan (`model: sunset_quality/goes`) when that scan is within
90 min before the target and covers the site (CONUS), otherwise from the HRRR ray model
(`model: sunset_rays/hrrr`; Alaska, Hawaii, and targets further ahead).

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

`type` ∈ `thunderstorm | sunset | aurora` (camera-side `EventType` names); `severity` ∈ [0, 1];
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

- **thunderstorm** — ProbSevere objects with COMPREF ≥ 50 and (FLASH_RATE ≥ 5 or ProbSevere ≥ 30).
  In 2024-era files COMPREF/ProbSevere don't exist; MESH ≥ 0.5 in substitutes for COMPREF and `PS`
  for `ProbSevere`. Evidence carries motion (m/s east/south) so the camera side can lead the target.
- **aurora** (`--aurora`) — 0.5° lattice north of 50°N + Alaska: OVATION probability ≥ 20, sun
  elevation ≤ −6°, HRRR lcc disk ≤ 30 % where the grid covers it. `score = min(1, probability/50)`,
  `needs_night_capable_camera = true` (all-sky/long-exposure cams only).
- **sunset** (`--sunset`) — 1° CONUS lattice; a point is scored when its sunset+15 min lies within
  90 min after the newest GOES scan of its satellite (GOES-18 west of 103°W, GOES-19 east), by the
  Sunsethue-whitepaper classifier on the GOES cloud field. `severity = min(1, quality/0.06)` (0.06 ≈
  the best sites of the West-Coast test; Lamar 5/5 via GOES 0.05); every land point (HRRR `LAND`)
  with severity ≥ 0.5 becomes an event with `radius_km` 60, `t_start`/`t_end` = sunset+5/+25 min,
  `look_bearing_hint` = solar azimuth — no peak-picking, because the camera side only searches
  50 km around each point, so a good region has to be tiled. Only the ~22° longitude band whose
  sunset is imminent is scored (~500 points; clear points cost ~30 ms, cloudy ones ~0.3 s; the
  West-Coast replay sweep takes ~1 min), so a `--loop 600` run keeps up as sunset crosses CONUS. Per-site scores for a camera catalog still come through
  `--sites`/`site_scores.json`. The sunset severity is a **prior, not a verdict**: ρ=0.48 against
  observed colour on one West-Coast evening (REPORT §3.16) — the camera-side colour index / vision
  model makes the final call.
