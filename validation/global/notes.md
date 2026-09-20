# Worldwide weather stack + aurora event type — probed 2026-09-20 04:35–04:50Z

## Worldwide / Europe weather (all keyless unless noted)
| Source | Result | Notes |
|---|---|---|
| **Open-Meteo forecast API** `api.open-meteo.com/v1/forecast?latitude=a,b,c&longitude=…&hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,cape,visibility,relative_humidity_2m&models=icon_d2` | 21 ray points × 48 h in **0.4 s** for ICON-D2 (2 km), ICON-EU, `ecmwf_ifs025`, `gfs_global`, `best_match` | Worldwide cloud layers for the sunset rules with no download at all. No cloud base/top/ceiling (ray model falls back to the layer bands). **Free tier counts every point as a call** (600/min, 5k/h, 10k/day): a 3-km ray to 700 km = 234 calls → hit "Minutely API request limit" immediately. At 15-km spacing (47 points) it's ~100 cameras/hour. `past_days=N` gives model hindcasts. Scalable alternative: DWD ICON-D2 grids (below) or GFS on AWS (`noaa-gfs-bdp-pds`, same `.idx` byte-range pattern as HRRR). |
| **DWD ICON-D2 open data** `opendata.dwd.de/weather/nwp/icon-d2/grib/HH/clch/icon-d2_germany_regular-lat-lon_single-level_YYYYMMDDHH_FFF_2d_clch.grib2.bz2` | dir listing 200; per-variable per-hour bz2 GRIB | Central Europe at 2 km, 3-hourly runs, `clcl/clcm/clch/clct`, plus `lpi` (lightning potential). Unlimited; the equivalent of our HRRR path. |
| **DWD radar composite** `opendata.dwd.de/weather/radar/composite/rv/` | 200 | Germany only, 5-min. Europe-wide OPERA composite is not open. |
| **RainViewer** `api.rainviewer.com/public/weather-maps.json` | 200; 13 past frames @10 min | Global radar *tiles* (PNG), not dBZ — OK for "is there precip", not for ≥50 dBZ rules. |
| **MeteoAlarm** `feeds.meteoalarm.org/api/v1/warnings/feeds-<country>` | 200, 5.4 MB CAP JSON for Germany | Europe's NWS-alerts equivalent, per country. |
| **EUMETSAT MTG Lightning Imager / MSG imagery** | not tested | Data Store needs (free) registration + API key; the GLM equivalent for Europe/Africa. Without it, European "lightning" = ICON-D2 `lpi` + CAPE, i.e. model-based, not observed. |
| **foto-webcam.eu archive** `/webcam/<slug>/YYYY/MM/DD/HHMM_la.jpg` | ≥1 yr, 10 min, 1920 px | **Times are local (CEST/CET)**, and the **current day's archive 502s** until the day closes — use `/current/1920.jpg` live. |

### End-to-end European sunset check (Zugspitze, 47.42 N 10.98 E, sunset 2026-09-19 17:20Z, az 273°)
Open-Meteo `past_days=1`, valid 17Z: ICON-D2 → ray quality **0.42** (hcc 84 % overhead, deck ends ~200 km west, no mid/low, RH 89 %), ECMWF → 0.26; simple rule fires on both.
foto-webcam.eu `2026/09/19/1930_la.jpg` (17:30Z): pink underlit cirrus + alpenglow → **4/5** (`zugspitze/sheet_zugspitze_sunset.jpg`). 0.42 is the same score Lamar's 5/5 got.
Same for today (09-20, 17:17Z): all three models → mid/high deck with horizon blocked (ray 0.005–0.007, simple rule fires) → predicts grey; frame not retrievable until tomorrow.

## Aurora — live sensors (NOAA SWPC, keyless; `weather/swpc_aurora.py`)
| Product | URL | Cadence / lead | Tested |
|---|---|---|---|
| OVATION aurora nowcast | `services.swpc.noaa.gov/json/ovation_aurora_latest.json` | ~5 min; valid ~1 h ahead; 1° global grid (65,160 cells), 924 KB | yes — northern max 28 % over Nunavut at 04:39Z |
| Kp (1-min estimated) | `services.swpc.noaa.gov/json/planetary_k_index_1m.json` | 1 min | yes — Kp 3 |
| L1 solar wind (IMAP) | `services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json`, `rtsw_wind_1m.json` | 1 min | yes — Bz +1.6 nT (northward = quiet), 413 km/s, 1.4 /cc |
| Hemispheric power | `services.swpc.noaa.gov/text/aurora-nowcast-hemi-power.txt` | 5 min | yes — 31/32 GW (quiet; storms 50–150+) |
| (404 now) | `products/solar-wind/mag-1-day.json`, `products/aurora-forecast/…` | — | old paths; use the `json/rtsw/` ones |
Rule: OVATION ≥ 10 % at the camera AND sun < −12° AND low cloud low (from HRRR-AK / Open-Meteo). Tonight it fires at Yellowknife (26 %, sun −19°) and Reykjavik (10 %, −15°), not Fairbanks (twilight) or Tromsø (dawn).

### Verification tonight (quiet night, Kp 3)
- Windy `nearby=62.45,-114.37,150` → 6 cams; the first, "Yellowknife › North: Canada" (`meteo`, webcamId 1575483316, 853×480, 1-min stamp) **is the CSA AuroraMAX all-sky camera** and its 04:32Z frame shows a **green aurora arc** (`aurora/sheet_yellowknife.jpg`) — 3/5 on a quiet night, exactly where OVATION said 27 %. The five normal-exposure cams beside it show nothing: aurora needs all-sky / long-exposure cameras.
- Windy sky-category cams at aurora latitudes: Tromsø 33, Reykjavik 14, Yellowknife 4, Churchill 0. UAF Poker Flat all-sky (`allsky.gi.alaska.edu`) serves `offline-notdark-source.jpg` until dark; the CSA AuroraMAX page itself exposes no image URL (Windy relays it).
- Ground-truth sensors beyond cameras: real-time magnetometers exist (e.g. Tromsø, Kiruna, GIMA) but weren't needed — OVATION + a Windy all-sky cam closed the loop.
