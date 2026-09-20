# Replay-mode mock run (historical data only) — 2026-09-20 03:40–04:20Z

Question: if demo day is boring, can the whole pipeline run on a past event? Every stage was exercised with archived data only.

## Archive depth (all free, unsigned)
| Source | Depth | Tested | Notes |
|---|---|---|---|
| MRMS composite / PrecipRate | 2020-10-14 → | 2023-08-01, 2024-04-26, 2025-05-16 | same 1.5 MB files, same 2-min cadence |
| **ProbSevere** `noaa-mrms-pds/ProbSevere/YYYYMMDD/` | 2020-10-14 → | 2021-05-01, 2024-04-26 | schema changed: 2024 files lack `ProbTor/ProbSevere` keys under those names (got None); `MESH`, `FLASH_RATE`, `ID`, polygon present |
| GLM LCFA | 2017 → | 2024-07-15 (goes16), 2025-05-16 (goes19) | **goes16 is empty for 2025-05-16**; GOES-East moved to goes19 in spring 2025 → choose bucket by date (goes16 ≤ ~2025-04, goes19 after) |
| ABI CMIPC | same | 2025-05-16 on goes16 empty (same reason) | |
| HRRR sfc | 2014 → | 2023-08-01, 2025-05-16 | `.idx` byte-range works historically |
| NWS warnings | `api.weather.gov` has **no history** → IEM: `https://mesonet.agron.iastate.edu/json/vtec_events.py?wfo=OAX&year=2024&phenomena=TO&significance=W` (per WFO-year) and `https://mesonet.agron.iastate.edu/api/1/vtec/sbw_interval.geojson?begints=2024-04-26T20:00Z&endts=2024-04-26T21:30Z&wfo=OAX` (polygons) | 2024-04-26 OAX → 25 polygons | `geojson/sbw.py?ts=` wants ISO time |
| SPC yearly reports | 1950 → | `https://www.spc.noaa.gov/wcm/data/2025_hail.csv` / `_wind.csv` / `_torn.csv` (2024, 2025: 64k rows) | `time` is CST (tz=3) → +6 h for UTC |
| Open-Meteo ERA5 | 1940 → | done earlier | |

## Camera archives (the hard part)
| Source | Verdict | Evidence |
|---|---|---|
| **PhenoCam** | **only US archive at event-time resolution** (30 min, 2008→) | `siteimagelist` is complete (126k–163k URLs per Mead site — not capped; proctor2's 5000 was coincidence). One listed URL returned an HTML 404 page saved as .jpg → check content-type/size |
| FAA WeatherCams | none (13 frames) | |
| Windy | day player 24×~50 min (last 20 h); **year player = weekly, lifetime = monthly** → no | `webcams.windy.com/webcams/public/embed/player/<id>/{year,lifetime}` |
| Wayback Machine | sporadic snapshots of `imgproxy.windy.com/_/full/plain/current/<id>/original.jpg` etc.; not event-timed → no | CDX API |
| HPWREN | CDN pattern `cdn.hpwren.ucsd.edu/MTA/<cam>/large/YYYYMMDD/Q<n>/<epoch>.jpg` but **403 on all directory listings**, filenames not constructible → NO-GO | |
| NPS air webcams | landing page has no archive links any more → not pursued | |
| foto-webcam.eu | ≥1 yr, 10-min (`/webcam/<cam>/YYYY/MM/DD/HHMM_la.jpg`) — Europe only | tested 2025-09-19 |

## Replay camera pool (precomputed, `cams/phenocam_whitelist/phenocam_sky_whitelist.json`)
All 454 live CONUS PhenoCam sites scored on a midday RGB frame: **89 with ≥20 % sky, 33 ≥30 %, 11 ≥40 %; 73 of the 89 have archives from before 2024**. Orientation: 41 N, 8 S, ~10 W-ish. 5 sites 404 on `middayimages`.

## Storm replay
- Candidate search: SPC 2024–25 reports × whitelist, ≤15 km, sun elevation >5° → **406 reports / 197 site-days** (`storm_candidates.json`).
- Full replay of **2024-04-26 20:30Z, EF4 near Mead NE** (`evD_mead_2024/`): IEM → TO.W polygons 11–25 km from the camera at 20:29Z; ProbSevere → object 12 km away, MESH 1.30", 38 flashes/min; MRMS → 66.5 dBZ within 36 km (589 px ≥50 dBZ); GLM goes16 → 106 flashes in 4 min within 40 km. Detection is *perfect and instant* (~15 s of S3 fetches).
- Frames (`meadpasturesw`, SW-facing, 24 % sky, 12.6 km): 20:19Z = rain-soaked lens, exposure 384→927; sky strip blown white; **no storm structure visible** (2/5). Same for mead1/mead2/meadpasture (5–12 % sky).
- Batch of 8 more tornado-day site-hits (`storm_batch/sheet_storms.jpg`, frame within ±30 min): `uiefprairie2`/`uiefswitchgrass2` 2025-05-13 → dark storm base + rain shaft, **4/5**; NEON PRPO 2024-06-02, Worcester 2025-09-06 → whiteout/rain on lens 3/5; Shenandoah 2025-05-30 → lowered deck + showers 3/5; santacruz2 → fog 2/5; NEON KONA 2024-05-07 and Buffalo 2024-08-05 → nothing (1/5).
- **Hit rate: 2/9 demo-worthy, 5/9 "something visible", 2/9 nothing** — for the *best* candidates. Pre-screen the 197 site-days offline and hand-pick 5–10 for the demo.

## Sunset replay
- Frames exist: 3/3 W-facing whitelist sites shoot 2–5 h past sunset (statenrice1 314/315 days of 2025 have a frame within ±20 min of sunset+15; worcester 365/365; meadpasturesw 324/343).
- `statenrice1` (W, 31 % sky at midday but only ~10 % in the fixed dusk mask): colour index over 314 days → top days are only mildly pink; **summer "sunset+15" frames still show the sun on the horizon → the camera clock runs on DST**, not standard time as PhenoCam's convention claims (winter frames are correct). Must verify each camera's clock against sun position before trusting timestamps.
- HRRR side is fully available for any 2025 date (tested); ray-model / simple-rule scoring of historical days is ~1 s per day after a 10 MB byte-range fetch. Not run for statenrice1 because the frames can't be trusted to ±60 min.

## Feasibility verdict
- **Storm replay: YES.** Weather side is complete, deterministic, and fast; the camera side is PhenoCam-only, so pre-screen and curate. Demo recipe: pick from `storm_candidates.json` (e.g. 2025-05-13 21:24Z Urbana IL; 2025-05-30 22:06Z Shenandoah VA), fetch MRMS/ProbSevere/GLM/IEM at T, PhenoCam frames T±30, sky filter, verify.
- **Sunset replay: PARTIAL.** HRRR + astral + ERA5 rarity replay fine; PhenoCam supplies frames, but the pool of W-facing, sky-rich, deep-archive cams is ~6 sites, sky strips are thin, and clocks need per-camera validation. For a convincing sunset demo, record FAA/Windy frames live from now until demo day (13-frame FAA archive means you must poll; 1,700 West-Coast frames captured tonight are a start).
- Cost: one replay event ≈ 5 MB MRMS + 0.6 MB ProbSevere + 5 MB GLM + 10 MB HRRR + 13 MB PhenoCam list + 3 MB frames; ~30 s wall-clock.
