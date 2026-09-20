# Event A — severe thunderstorm, Wichita/Scott County KS, 2026-09-20 00:48–03:00Z

All times UTC. Wall clock at start of work: 02:31Z. Commands run with `uv run` from `/Users/ken/dev/nimbly` (pyproject has boto3, pygrib, cfgrib+eccodeslib, netCDF4, scipy, astral, torch, transformers).

## 1. DETECT
- NWS alerts: `curl -H "User-Agent: nimbly-validation (email)" -H "Accept: application/geo+json" "https://api.weather.gov/alerts/active?status=actual&message_type=alert"` → 321 alerts, 1.3 MB, 0.27 s. Saved `../nws_alerts_active.json`.
  - Severe Thunderstorm Warning, Wichita County KS, sent 21:29 CDT (02:29Z), polygon centroid (38.524, −101.392). NOTE: `sent`/`expires` are local-offset ISO strings, not UTC.
- MRMS composite reflectivity: `s3://noaa-mrms-pds/CONUS/MergedReflectivityQCComposite_00.50/20260920/MRMS_MergedReflectivityQCComposite_00.50_20260920-023041.grib2.gz` (1.55 MB gz, full CONUS 3500×7000 @ 0.01°). Newest file valid 02:30:41Z, uploaded 02:31:30Z, fetched 02:32:50Z → **age 2.2 min, upload lag ~50 s, cadence 2 min**.
  - Rule: connected clusters of ≥50 dBZ ≥10 px. 369 clusters CONUS-wide; largest 600 px, peak 66.5 dBZ, centroid (38.342, −101.171). `mrms/cells_ge50dbz_023041.json`.
- GLM lightning: `s3://noaa-goes19/GLM-L2-LCFA/2026/263/02/` 20-s files, 380 KB each; newest uploaded **4 s** after its end time. 15 files (02:29–02:34Z): 5864 flashes full-disk, **315 within 50 km of the cell**. `glm/flashes_near_cell.json`. `noaa-goes16` has **no data** (decommissioned) — use goes19 (East) / goes18 (West).
- GOES ABI: `ABI-L2-CMIPC` band 13 CONUS (3.3 MB, 5-min, scan 02:31:17Z → uploaded 02:34:37Z, ~3.5 min lag). Min 10.3 µm BT within 32 km of cell = 207.9 K (−65 °C). `ABI-L2-ACHTC` does not exist on GOES-19 (cloud-top temp is full-disk `ACHTF` only: 30 MB, 10-min, ~12 min lag). `MCMIPC` 40 MB/5 min, `MCMIPF` 250 MB/10 min — don't.
- Event record: `event.json`.

## 2. RARITY
- Voloridge dataset path not provided. Fallback: Open-Meteo ERA5 archive (no key): `https://archive-api.open-meteo.com/v1/archive?latitude=38.342&longitude=-101.171&start_date=2016-01-01&end_date=2026-09-14&hourly=cape,precipitation,cloud_cover_low,cloud_cover_mid,cloud_cover_high&timezone=UTC` → 3.4 MB, 10 s, 93,840 hours. **`cape` is all-null in the archive endpoint** (only in forecast API). precipitation + 3 cloud layers are complete. Lags ~5 days.
- Climatology window DOY 263±15, 23–05Z, n=1920 h: precip p50=0, p90=0, p95=0, p99=1.9, max 8.8 mm/h. MRMS PrecipRate 14-km-mean at cell = 33.1 mm/h → "100th pct, z=76". Meaningless: zero-inflated distribution; every storm is "off the charts". Rarity is only informative for continuous fields (cloud layers) — see Event B.
- Feasibility: one HTTP call per event (~10 s for 10 yrs hourly, 5 vars) — fine for a hackathon loop without any precompute. Precompute for CONUS at 0.25°: ~11k cells × 3.4 MB = 37 GB of JSON, or just cache per camera site (~2k sites × 10 s).

## 3. FIND CAMERAS (radius 150–200 km around 38.34, −101.17)
- PhenoCam (`https://phenocam.nau.edu/webcam/network/siteinfo/`, 1.1 MB, 1084 sites): **0 within 150 km**, 3 within 300 km (NEON Arikaree 193 km, Sterling CO 286 km).
- FAA WeatherCams: **8 within 200 km** (Lamar CO 136 km, Burlington CO 140 km), 7 fresh (<15 min), 1 in maintenance since 2023. All have `cameraBearing`.
- Windy: no key → 403. Untested.

## 4. SKY FILTER (segformer-b0 ADE20K, class 2 = sky, ~300 ms/frame CPU)
| frame | model sky_frac | my eyes | ok? |
|---|---|---|---|
| Lamar E 00:48Z (day) | 0.70 | ~0.70 (horizon at 70% height) | yes |
| Lamar E 02:31Z (night) | 0.40 | ~0.70 | **no** |
| Kit Carson S 02:35Z (night) | 0.88 | ~0.65 (dark ground read as sky) | **no** |
| Kit Carson E 02:36Z (night) | 0.41 | ~0.68 | **no** |
→ run sky filter only on daytime frames; cache per camera (framing doesn't change).

## 5. VERIFY
- Live frames (02:31–02:36Z, all 3 cams): storm **not visible** (night, 136–140 km away, no lightning caught in ~1/500 s stills). Score 1/5.
- FAA "archive" is only the last 13 frames (`/images/last/N` caps at 13 ≈ 1 h 45 min). Oldest frame 00:48Z = 2 min before local sunset. At 00:48Z MRMS shows a 60 dBZ cell 59 km from Lamar at bearing 100° = the East camera's bearing exactly. Frame `faa_frames/cam12144_20260920T004825.jpg`: **sunset-lit mammatus under the anvil, 5/5**. Next frame 00:56Z: mammatus still there, gold gone → 3/5. **Peak window < 8 min, shorter than the 8–9 min cadence.**
- Vision LLM: no OpenAI/Anthropic key in env → skipped.

## Cameras surviving each stage
geo (200 km): 8 FAA + 0 PhenoCam → fresh: 7 → sky filter (day frames only; night frames unusable): 3 evaluated → verify live: 0/3; verify archive 00:48Z: 1/1 (5/5).
