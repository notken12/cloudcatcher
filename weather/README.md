# weather

Data-source clients from the validation run (`validation/REPORT.md`). All S3 access is unsigned; run from the repo root
with `uv run python -m weather.<module>`.

| module | source | notes |
|---|---|---|
| `s3.py`, `grib.py` | unsigned boto3, pygrib helpers | |
| `mrms.py` | MRMS composite reflectivity / PrecipRate / ProbSevere | 2-min, 1.5 MB CONUS files, archive 2020-10→; ProbSevere objects are the ready-made event record |
| `glm.py` | GLM flashes | 20-s files, ~4 s lag; goes16 before 2025-04-07, goes19 after |
| `goes_abi.py` | ABI L2 (band 13 CONUS, ACHAC cloud-top height) + lat/lon→pixel | `ACHTC` does not exist on GOES-19 |
| `hrrr.py` | byte-range cloud subset from the .idx, `CloudGrid` sampler | 10 MB / ~1 s; base/top NaN for thin cirrus; mask off-grid points |
| `nws.py` | live alerts (User-Agent required) and IEM VTEC archive | api.weather.gov has no history |
| `spc.py` | SPC daily / yearly storm reports | yearly times are CST |
| `climatology.py` | Open-Meteo ERA5 percentiles | good for cloud layers, useless for precip (zero-inflated); `cape` not in archive |
| `sun.py` | astral wrappers | |
| `sunset_rules.py` | simple mid/high-over-site + clear-ray rule | |
| `sunset_rays.py` | Sunsethue-style ray model | implemented, **not validated** (ρ≈0 on one evening) |
| `sunset_quality.py` | Sunsethue-whitepaper two-phase classifier (reflection-potential fan + view-ray fan + humidity/duration post-processing) | verified on the Lamar 5/5 frame; **not yet wired into detection** |
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
writes `site_scores.json` next to `--out`: per site the sunset score at its next sunset+15 min
(existing ray model), the nearest storm event (km, bearing), and the aurora rule — join on `id`.

Event record:

```json
{"id": "storm-771444-20260920T0430Z", "type": "storm",
 "lat": 41.38, "lon": -91.68, "radius_km": 100,
 "t_start": "2026-09-20T04:30:00Z", "t_end": "2026-09-20T05:00:00Z",
 "score": 0.7,
 "needs_daylight": true, "needs_night_capable_camera": false,
 "look_bearing_hint": null,
 "evidence": {"COMPREF": 62.0, "FLASH_RATE": 21.0, "MESH": 0.64, "ProbSevere": 11.0,
              "motion_east_ms": 5.4, "motion_south_ms": 0.0, "probsevere_id": "771444",
              "valid_time": "2026-09-20T04:30:39Z"}}
```

`type` ∈ `storm | sunset | aurora`; `score` ∈ [0, 1]. `needs_daylight` means the sun is above −6° at
the event (a normal still camera can verify); `needs_night_capable_camera` is its negation — at night
the storm radius shrinks to 30 km (only verify with a close or night-capable/ALERTCalifornia-class
camera). `look_bearing_hint` is degrees; sunset events also carry `anti_solar_bearing` in evidence
(the show is sometimes on the anti-solar side — Lamar's 5/5 mammatus was in the *East* camera).

Rules:

- **storm** — ProbSevere objects with COMPREF ≥ 50 and (FLASH_RATE ≥ 5 or ProbSevere ≥ 30).
  In 2024-era files COMPREF/ProbSevere don't exist; MESH ≥ 0.5 in substitutes for COMPREF and `PS`
  for `ProbSevere`. Evidence carries motion (m/s east/south) so the camera side can lead the target.
- **aurora** (`--aurora`) — 0.5° lattice north of 50°N + Alaska: OVATION probability ≥ 20, sun
  elevation ≤ −6°, HRRR lcc disk ≤ 30 % where the grid covers it. `score = min(1, probability/50)`,
  `needs_night_capable_camera = true` (all-sky/long-exposure cams only).
- **sunset** — not emitted yet (the lattice sweep is parked); per-site scores still come through
  `--sites`/`site_scores.json`. The sunset score is a **prior, not a verdict**: ρ≈0 against observed
  colour on one West-Coast evening — the camera-side colour index / vision model makes the final call.
