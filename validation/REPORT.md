# Data-source validation — sky-event webcam project

Manual end-to-end walkthrough, 2026-09-19 22:30 → 2026-09-20 00:30 EDT (02:30–04:30Z). Everything below was actually run; artifacts are under `validation/<event>/`, per-event commands in each `notes.md`. Python env: `uv` project at repo root (`pyproject.toml`); code in `weather/`, `camera_ken/`, `common/`.

Not provided, therefore not tested: **Voloridge dataset path**, **OpenAI/Anthropic key** (vision-LLM step skipped; my own judgement is recorded instead). Windy key arrived later and was tested (§1, `cams/windy/notes.md`).

## 1. Source scorecard

| Source | Reachable | Auth | Latency (newest vs now) | Coverage | Cadence | Archive depth | Gotchas | 10-min loop |
|---|---|---|---|---|---|---|---|---|
| **MRMS composite refl** `s3://noaa-mrms-pds` | yes (unsigned) | none | **2.2 min** (upload lag ~50 s) | CONUS 0.01° (+ALASKA/, HAWAII/ prefixes exist) | 2 min | 2020-10-14 → | 1.55 MB gz per full-CONUS file, no subsetting needed. eccodes warns about non-zero seconds (harmless). 243 products incl. PrecipRate, Reflectivity_-10C, LightningProbability | **GO** |
| **MRMS ProbSevere** `s3://noaa-mrms-pds/ProbSevere/YYYYMMDD/MRMS_PROBSEVERE_YYYYMMDD_HHMMSS.json` | yes | none | **~2 min** | CONUS | 2 min | 2020 → | GeoJSON, 615 KB, ~200 storm objects with polygon, tracking `ID`, `MOTION_EAST/SOUTH`, `COMPREF`, `MESH`, `EchoTop_50`, `FLASH_RATE`, `ProbSevere/ProbHail/ProbWind/ProbTor`, CAPE/shear. Values are strings; `MLON` is truncated to integer — use the polygon centroid. Also `ALASKA/`, `HAWAII/`, `CARIB/`, `GUAM/` composites exist | **GO** (use instead of hand-rolled dBZ clustering) |
| **GLM lightning** `s3://noaa-goes19/GLM-L2-LCFA` (+goes18) | yes | none | **~20 s** (4 s upload lag on 20-s files) | full disk | 20 s | 2017 → (goes16 archive), goes19 since 2025 | `noaa-goes16` has **no current data** (decommissioned) — pipeline must use goes19/goes18. 380 KB/file → ~1 MB/min | **GO** |
| **GOES ABI** `ABI-L2-CMIPC` (CONUS, per band) | yes | none | 3.5 min (scan start → upload ~3 min) | CONUS 2 km | 5 min | same | Band 13 = 3.3 MB, fine. `MCMIPC` 40 MB, `MCMIPF` 250 MB, `ACHTF` 30 MB/10-min/12-min lag — too heavy. `ACHTC` **does not exist** on GOES-19 | GO (C13 only) / RISKY (full products) |
| **NEXRAD L2** | `unidata-nexrad-level2` yes; `noaa-nexrad-level2` **AccessDenied** for unsigned list | none | 6–11 min per volume | per radar | ~4–6 min | deep | Chunked `_MDM` objects; needs pyart/nexradaws. MRMS makes this unnecessary | NO-GO (not needed) |
| **HRRR** `s3://noaa-hrrr-bdp-pds` | yes | none | f01 available **~53 min after init** → effective age 0–1.9 h at valid time | CONUS 3 km; Alaska 3 km **3-hourly** | 1 h (CONUS) | 2014 → | 177 MB/file but `.idx` byte-ranges: cloud fields (LCDC/MCDC/HCDC/TCDC, cloud base/ceiling/top, HGT sfc, HPBL) = 8–10 MB in ~1 s. Layers are pressure-defined (~<3.5 / 3.5–8 / 8–13.5 km MSL). **Cloud base/top are NaN for thin cirrus** even at HCDC 100 %. Domain edge: points outside CONUS grid silently get edge values with nearest-neighbour sampling (Crowsnest Pass AB) — mask them | **GO** (fetch f01–f03 of the latest run) |
| **NWS alerts** `api.weather.gov/alerts/active` | yes | User-Agent header required | seconds | US | push | live only | 1.3 MB for all active; polygons only on warnings (watches are county lists, no geometry). Timestamps are local-offset ISO | **GO** |
| **Voloridge historical** | — | — | — | — | — | — | path not provided | untested |
| **Open-Meteo ERA5 archive** (fallback) | yes | none | ~5-day lag | global 0.25° hourly, 1940 → | hourly | 86 yrs | 10 yrs × 5 vars = 3.4 MB in 10 s. `cape` is **all-null** in the archive endpoint. precip + low/mid/high cloud complete | GO for cloud-layer climatology; **useless for storm rarity** (zero-inflated) |
| **PhenoCam** | yes | none | siteinfo `date_end` is same-day for 619 sites | 1084 sites, 771 flagged active, **529 live in CONUS**; **0 within 150 km of W Kansas** | 30 min | 2008 → (per site) | URL is `data/archive/<site>/<YYYY>/<MM>/<site>_YYYY_MM_DD_HHMMSS.jpg` (no `/DD/`); seconds vary so URLs aren't constructible; `api/siteimagelist/<site>/` returns the *whole* history (17 MB), ignores date params, caps at 5000, lags ~2 days; dir listings 403; `api/middayimages/<site>/` returns the **IR** frame last — strip `_IR`; filenames are **local standard time**; `active=True` is stale for 107 sites (>30 d); only **20 % of sites have ≥20 % sky** (most look down at canopy, most face N); a 5-h gap on the storm day | RISKY (great archive for back-tests, thin live value) |
| **FAA WeatherCams** `weathercams.faa.gov/api/…` | yes | none, but **must send a browser User-Agent** (curl UA → 401) | median **5.4 min** since last image; 1826/1942 CONUS cams <15 min | **3537 cams / 977 sites**: 1446 AK, 1942 CONUS (CA 120, MT 62, CO 43, OR 41, ME 35, NV 29…), none in KS/NE/OK | ~8–10 min | **13 frames (~1 h 45 m)** only | Every camera has `cameraBearing` (exact), `cameraLastSuccess`, maintenance flags; sites have sunrise/sunset; images are plain JPEGs on `images.wcams-static.faa.gov` with burned-in UTC+local timestamp. `/api/cameras/state/US` and `/api/summary` 404/500. Auto-exposure blows out the sky on many cams | **GO** (primary camera source) |
| **Windy Webcams v3** `api.windy.com/webcams/api/v3` | yes | `x-windy-api-key` | `lastUpdatedOn` median ~10–19 min; 93 % ≤1 h | **33,136 US cams** (93 % traffic); fills the Plains: KS 392, NE 262, IA 1,113, TX 3,807 (OK only 11) | ~10 min ingest | day player: 24 stills @ ~50 min; month player: 1/day ×30 (undocumented, scraped from embed HTML) | Documented images are 400×224; undocumented `imgproxy.windy.com/_/full/plain/current/<id>/original.jpg` gives 1280×720, no key. No heading field. Multi-category filter returns 0 (query per category). Sky yield: traffic cams **13 % ≥0.2 sky**, `landscape/meteo` **88 %**. Lat/lon junction-precise in the KS sample, not centroids. `daylight` frame can be ~12 h old | **GO** for `meteo/landscape/mountain/lake/coast` (~2,400 US cams) + Plains traffic cams after a sky-fraction pass |
| **foto-webcam.eu** | yes | none | `Last-Modified` 1 min before fetch, `max-age=300` | Alps/Europe | 10 min | ≥1 yr (`/webcam/<cam>/YYYY/MM/DD/HHMM_la.jpg` worked for 2025) | Europe only | GO (if Europe is in scope) |
| **Panomax** `api.panomax.com/1.0/instances/lists/public` | yes | none | — | 671 cams: AT 290, IT 174, DE 127, **US 0** | — | — | has lat/lon, `zeroDirection`, `viewAngleDegree` | NO-GO for US |
| **Roundshot** | 404 on guessed endpoint | — | — | — | — | — | not pursued (timebox) | untested |
| **ALERTCalifornia** `cameras.alertcalifornia.org/public-camera-data/all_cameras-v3.json` + `/<id>/latest-frame.jpg` | yes | none (browser UA) | median **1.6 min**, 97 % ≤5 min | 2,249 features, **1,311 geolocated, CA only** in the official feed (the community `firestorm-cameras` catalog adds NV/OR/MT/HI/ID/WA but with dated snapshot URLs) | ~2 min | none public | 1920×1080; live `az_current` heading + `fov`; PTZ patrol flag; **near-IR at night — sky, skyline, moon and stars visible** (the only source that can verify a storm after dark). CC BY-NC-ND: display-only, no caching/re-hosting | **GO** (night + CA) |
| **NPS ARD air-quality cams** `nps.gov/featurecontent/ard/webcams/images/<park>large.jpg` | yes | none | ~4 min | ~15–20 parks (grsm, grca, acad… hand-curate; yell/others use different hosts) | minutes | none | **3840×2160** horizon views by design; black at night (no IR) | GO (daytime, curated) |
| **CARS 511 `/api/v2/get/cameras`** (LA, WI, KS, NE, IA, MN, SD, ND, TX, CO probed) | LA/WI: `Invalid Key`; KS/NE/IA/MN: HTML SPA; others 404/500 | per-state developer key | — | — | — | — | Windy already relays the KDOT/NDOT/IowaDOT cams (~10 min lag), and they're pavement-aimed anyway | NO-GO for tonight (key + reverse-engineering) |
| **NOAA ISD** `s3://noaa-isd-pds` (`data/YYYY/`, `isd-lite/data/YYYY/`), `s3://noaa-global-hourly-pds/YYYY/` | yes | none | **~13 months behind**: newest year is 2025, files last written 2025-08-30; no 2026 on AWS or at NCEI (`global-hourly/access/2026/` 404) | 29,661 stations, airports (= FAA cam sites) | hourly | 1901 → 2025-08 | Observed cloud layers with base heights (GA1–3/GD1), visibility, present weather incl. TS (AW1/MW1); ceilometers blind above ~12 kft (no cirrus). Fine for observational climatology through Aug 2025 | **NO-GO** for live/replay this year |
| **Live METAR** `aviationweather.gov/api/data/metar?ids=KLAA,KGCK&format=json` | yes | none | 0–56 min (hourly + SPECI) | US/global airports | hourly | ~days | The live feed ISD archives: `clouds[{cover,base}]`, `visib`, `wxString` (TS), raw ob. Same cirrus blindness | GO (observed low-cloud/visibility ground truth at FAA cam airports) |
| **SPC storm reports** `spc.noaa.gov/climo/reports/YYMMDD_rpts_filtered.csv` | yes | none | next-day | US | daily | 2004 → | ideal for picking back-test events | GO (offline) |

### 1b. Camera sources from the extended list — tried vs. not worth it (for a CONUS storms/lightning/sunset scope)
- **Tried, keep**: ALERTCalifornia (night vision + heading/FOV; CA only officially), NPS ARD (4K daytime horizon, curated). Clients: `camera_ken/alertcalifornia.py`; NPS is a URL pattern.
- **Tried, drop**: CARS 511 (keys/SPA; Windy relays them). HPWREN archive (403). Roundshot (404 on guessed API).
- **Not tried, wouldn't**: foto-webcam.eu / Panomax / Roundshot / feratel / bergfex — superb cameras but Europe-only, and MRMS/HRRR/GLM/ProbSevere are US-only; only worth it with a European weather stack (ICON-D2 + Meteosat LI). Aurora/all-sky cams (UAF, Kiruna, AllSkyCam, meteor nets) — aurora isn't one of our event types. Caltrans CWWP2 — redundant with FAA-CA (120) + ALERTCalifornia (1,311) + Windy-CA. NDBC buoy cams — few and ocean-only. YouTube live / explore.org / OnTheSnow — hero-feed material, not detection/verification; hand-pick 8–12 later.
- **Corrections to that list from testing**: Windy image URLs are *not* token-signed on this key — `imgproxy.windy.com/_/full/plain/current/<id>/original.jpg` works with no key and `max-age=150`; the API also rejects non-integer `nearby` radii and ANDs comma-separated categories. PhenoCam `latest` was minutes old for the sites checked, not 30–90 min.
- **The Plains gap is a placement problem, not an access problem**: every source we can reach there is a DOT cam aimed at pavement (13 % ≥20 % sky). The only sky views are TV-station skycams (Windy `city/meteo`, ~20 per state) — detect events there anyway, verify with whichever few exist, and don't expect night verification outside ALERTCalifornia's footprint.

## 2. Events

### Event A — live severe thunderstorm, W Kansas (02:30Z)
- **Detected**: NWS SVR warning (Wichita Co, sent 02:29Z) + MRMS 600 px ≥50 dBZ, peak **66.5 dBZ** at (38.34, −101.17), 2.2 min old + **315 GLM flashes** within 50 km in 5 min + GOES-19 C13 cloud-top BT **−65 °C**. Three independent sources agree within 4 minutes of real time.
- **Rarity**: ERA5 precip climatology p99 = 1.9 mm/h vs MRMS 33 mm/h → "100th pct". True but useless: distribution is 96 % zeros; every storm scores the same.
- **Cameras**: geo 200 km → 8 FAA, 0 PhenoCam, Windy untested → fresh 7 → sky filter 3 (daytime frame 0.70 matches eyes; **night frames mis-segmented**) → verify **0/3 in the live frames** (night, 136 km away, no lightning in 1/500 s stills).
- **But**: the oldest archived frame (00:48Z, 2 min before sunset) from Lamar CO East (bearing 100°) showed **sunset-lit mammatus under this storm's anvil — 5/5**, with MRMS confirming a 60 dBZ cell at bearing 100°, 59 km. Eight minutes later the gold was gone. `evA_storm_ks/faa_frames/cam12144_20260920T004825.jpg`.

### Event B — sunset (three sub-tests)
- **B1 Lamar back-test** (HRRR 00Z f01): simple rule fires (hcc 87 %, low 0 % along ray, deck ends 75 km west). Sunsethue-style ray model: 0.06 → 0.02 → **0.42** across three fixes (see §3). The show was in the **anti-solar** camera.
- **B2 West-Coast blind test**: scored 234 FAA sites at ~02:00Z sunset with HRRR 01Z f01 *before* looking, then rated frames: model top-8 mean 2.3/5 with both 4/5 frames (Barry Ridge above the marine layer, Shelter Cove cirrus) in the top-8; random zero-scored 8 → mean 1.6/5. Two false positives were **camera exposure** (sky blown to white), one miss was HRRR having no cloud where there was cloud, one "miss" was a site **outside the HRRR domain**. Ukiah went from 2/5 at sunset+5 to **4/5 at sunset+15** — colour peaks at 2–3° sun depression.
- **B2, objectively**: I also computed a "sunset colour index" (warm-hue chroma inside the SegFormer sky mask, sunset+15) for 189 sites. Spearman(model score, colour) = **0.01** (0.13 excluding the 85 frames with a blown-out sky; not significant); raw HRRR hcc/mcc/lcc correlate no better. Model top-20 contained 1 of the top-20 % most colourful frames (chance = 4). The index itself is sound — its top-8 (`sheet_colortop8.jpg`) are the evening's real winners: Coast Life Support **5/5** (underlit altocumulus), Merrell Rd, Sugarloaf Ridge, Plains Airport, Sea Ranch ~4/5 — and HRRR reported **hcc 0–2 %, mcc 0** at four of them. GOES-18 C13 IR also read ~290 K ("clear") at Merrell Rd/Sugarloaf (thin cirrus is transparent at 10.3 µm); GOES-18 `ACHAC` cloud-top height (10 km, 0.3 MB) did flag 7–8 km cloud at Joseph Airport and Wagontire where HRRR had none. Conclusion: on this evening the limiting factor was the **cloud input**, not the ray logic.
- **B2, camera-side nowcast probe**: I also fetched sunset−30 frames for 174 of those cameras. Crude sky features at −30 min (whitish/low-saturation fraction, horizon-band paleness) correlate with colour at +15 at ρ = **−0.37 / −0.34** (p<1e-4, n=128 non-blown) — paler/hazier sky before sunset → less colour after — while the ray model is at ρ = 0.02 on the same frames. The feature is too crude to call "cloud fraction" (it mostly measures haze/whiteness), but it is the only predictor with signal tonight and echoes Sunsethue's humidity correction.
- **B3 live Alaska** (111 sites with sunset 03:35–04:40Z, HRRR-AK 00Z f04, frames at sunset+15): ray-model top-5 → Livengood 3, Eagle 2, Minto 3, Fort Yukon 3, Central 3 (mean **2.8/5**); bottom two → Chignik 1, Knob Ridge 2 (mean 1.5). Knob Ridge was the *simple rule's* only pick and the ray model scored it 0.0 — correctly (grey overcast). Nothing above 3/5; anti-solar cams 1–2 everywhere. Small positive for the ray model on a mediocre evening (`evB_sunset/sheet_ak.jpg`, `sheet_ak2.jpg`).

### Event C — historical back-test, hail at Shale Hills CZO, PA, 2025-05-16 19:30Z
- **Detected** from SPC report → MRMS archive: 21.5 dBZ (19:00Z) → **62.5 dBZ at 19:30:40Z** over the site → 16.5 (20:00Z). Fires at the exact minute of the report.
- **Cameras**: PhenoCam 3.5 km away, 30-min archive: 14:30 EST frame (=19:30Z) shows heavy rain streaks, mist, water on lens — storm undeniably present — but the camera looks down at a forest with **0–5 % sky**. Sky filter correctly rejects it → **0 cameras survive**. Then a **5-hour archive gap** right after.
- PhenoCam survey (80 random live CONUS sites, midday RGB frames): only **20 % have ≥20 % sky**, 8 % ≥30 %. ≈100 usable sites nationally.

## 3. Where automation breaks (specific)
1. **Night.** After civil dusk, still cameras show nothing of a storm 100+ km away; SegFormer sky fraction is wrong on night frames (0.40 and 0.88 for frames that are both ~0.7). Run the sky filter on daytime frames and cache per camera; only attempt storm verification in daylight or for cameras < ~30 km from the cell.
2. **Peak windows are shorter than camera cadence.** Lamar mammatus: 5/5 at 00:48Z, 3/5 at 00:56Z. FAA cadence 8–10 min, PhenoCam 30 min. Expect to miss peaks; prefer selecting the best of the last 2–3 frames rather than "the latest frame".
3. **Sunset colour peaks 10–20 min *after* sunset** (δ≈2–3°). Ukiah: 2/5 at +5, 4/5 at +15. Frame selection and HRRR valid-time must target sunset+15.
4. **FAA has no real archive** — `images/last/N` caps at 13 frames. Anything you want to back-test must be fetched live and stored. (I grabbed 1732 West-Coast frames at 02:57Z for exactly this reason.)
5. **FAA coverage is CA/MT/CO/OR/ME/NV + Alaska**; zero cameras in KS/NE/OK/IA — the Plains storm belt is dark. PhenoCam is also empty in W Kansas. Windy does have 29 cams within 150 km of the Kansas storm, but 27 are DOT cams aimed at pavement (0–8 % sky): the Dighton junction cams 65 km from the cell showed road, not storm. Non-traffic Windy cams in KS/NE number ~20 each. Expect **a few dozen usable sky cams per Plains state**, not hundreds.
6. **HRRR under-reports the thin/scattered mid-high cloud that makes good sunsets** (4 of the 8 most colourful West-Coast sites had HRRR hcc ≤2 %), and **its layer fields are too coarse vertically for the sunsethue method.** `cloud top` is NaN for thin cirrus (the clouds that make sunsets); `cloud base` reports the lowest scrap of cloud, not the deck. Fixes that worked: light the deck underside from the `cloud ceiling` height, block sun-rays only inside [ceiling, top], and treat high cloud as 40 % opaque. Proper fix: HRRR `wrfprs` per-level condensate (~35 levels × 2 fields, ~60–100 MB/hour by byte-range) or use Sunsethue's own API if terms allow.
7. **HRRR-Alaska is 3-hourly** and the run arrives ~50 min after init → 1–4 h stale at sunset time.
8. **Camera auto-exposure**: 2 of the model's top-8 sunsets were white-out frames (Fiddler, Patton Hill). A "sky mean luminance > 240" reject is needed before any vision-LLM call.
9. **Domain edges**: nearest-neighbour sampling of HRRR for a Canadian site returned plausible-looking nonsense. Mask points outside the grid (check distance to nearest grid point < 5 km).
10. **PhenoCam plumbing**: no constructible URLs, 17 MB listings that lag 2 days, IR frames mixed in, local-time filenames, 20 % sky yield. Good for back-tests only.
11. **Rarity via precip/CAPE is non-informative** for storms (zero-inflated); use it for cloud-layer fields (sunset) and for "unusual" cloud-top temperature or dBZ percentiles among *non-zero* hours only. `cape` isn't in the ERA5 archive endpoint.
12. **Heading from frames failed** (0/2) on the day tested: overcast, and PhenoCams face N by design. Use metadata: FAA `cameraBearing` (exact) and PhenoCam `camera_orientation` (16-point compass, 60 % populated).
13. **noaa-goes16 is empty**; **noaa-nexrad-level2 denies unsigned listing** (use unidata-nexrad-level2).

## 4. Recommended minimal source set for the hackathon
- **Detect storms**: MRMS `MergedReflectivityQCComposite_00.50` (2 min, 1.5 MB) + GLM `noaa-goes19` LCFA (20 s). Skip ABI unless you want a pretty picture; skip NEXRAD.
- **Detect sunsets**: HRRR byte-ranged cloud fields (f01–f03) + GOES `ABI-L2-ACHAC/ACHAF` cloud-top height (0.3 MB, 5-min, catches mid/high cloud HRRR misses) + astral. The ray model is implemented (`weather/sunset_rays.py`) but **not validated** — one evening, ρ≈0 against observed colour. Cheapest high-value alternative for a hackathon: **camera-in-the-loop nowcast** — at sunset−30 min pull the sunward FAA frame, measure cloud in the sky mask (fraction, texture, height cue from colour) and horizon clarity toward the sun; predict; verify at sunset+15. The cameras are a better cloud sensor than the model for this purpose. Keep cameras within ±60° of both the solar and anti-solar azimuth.
- **Alerts**: NWS active alerts for labelling / gating only.
- **Cameras**: **FAA WeatherCams as the primary source** (bearing, freshness, 1900 CONUS + 1400 AK cams, no key) **plus Windy `meteo/landscape/mountain/lake/coast/beach`** (~2,400 US cams, 88 % sky-rich, 1280×720 via the `_/full/` imgproxy path) for everything east of the Rockies. Windy traffic cams only via a precomputed sky-fraction whitelist (≈1 in 8). PhenoCam only for the ~100 sky-rich sites (precompute the list) and for historical back-tests. Windy has no heading — infer from title suffixes (EB/WB/"› West") or a one-time glare/shadow pass on clear days.
- **Verify**: SegFormer sky fraction once per camera (cache), luminance/exposure reject, then the vision LLM on the best of the last 3 frames, daytime only.
- **Rarity**: Open-Meteo ERA5 per camera site (cache; 10 s each) for cloud layers; drop precip/CAPE rarity. Worked example: Lamar at 00–01Z, DOY 263±10 (n=430 h): hcc 87 % → 87th pct, mcc 51 % → **94th pct**, so "unusually cloudy aloft for the date" is computable and meaningful.
- **Detect storms, simpler**: ProbSevere objects (polygon + probabilities + motion) already are the event record; MRMS reflectivity is then only needed for pixels/plots.

Rules I'd change: (a) sunset rule → ray model with underside lighting + anti-solar cameras + sunset+15 timing; (b) storm rule: require daylight (sun elevation > −6°) *or* camera distance < 30 km; (c) add "exposure sanity" and "archive freshness < 2× cadence" filters before spending an LLM call; (d) treat PhenoCam `active` as false unless `date_end` ≥ today−1.

## 4b. Replay mode (historical data) — see `hist/notes.md`
- **Weather stages replay completely** from S3: MRMS + ProbSevere (2020-10→), GLM (goes16 ≤ spring 2025, goes19 after — pick bucket by date), HRRR (2014→), NWS warnings via IEM (`json/vtec_events.py`, `api/1/vtec/sbw_interval.geojson`), SPC yearly CSVs for event picking. One event ≈ 25 MB, ~30 s.
- **Cameras: PhenoCam is the only US still archive at event-time resolution.** Windy's long players are weekly/monthly, FAA keeps 13 frames, HPWREN's archive is closed, Wayback is sporadic. Precomputed whitelist: 89 sky-rich live sites, 73 with archives before 2024.
- Storm replay of the 2024-04-26 Mead NE EF4: detection perfect (TO.W 11 km, ProbSevere 12 km, 66.5 dBZ, 106 GLM flashes) but the camera shows rain on the lens and a blown-out sky strip. Across the 9 best tornado-day candidates: 2 demo-worthy frames, 5 "storm present", 2 nothing. 197 candidate site-days exist for 2024–25 → curate offline.
- Sunset replay: frames exist past sunset, but W-facing sky-rich deep-archive sites number ~6, sky strips are thin, and at least one camera clock runs on DST (frames mistimed by 1 h in summer). Verdict: storm replay YES, sunset replay PARTIAL — start recording FAA/Windy frames now for a live-captured sunset library.

## 4c. Worldwide stack and an aurora event type — both feasible (see `global/notes.md`)
- **Worldwide sunsets, keyless**: Open-Meteo returns low/mid/high cloud, CAPE, visibility and RH for a whole sun ray in one 0.4-s call from ICON-D2 (2 km), ICON-EU, ECMWF or GFS; `weather/openmeteo.py` plugs it into the same ray model. End-to-end check: yesterday's Zugspitze sunset scored 0.42 on ICON-D2 and foto-webcam.eu's archive shows pink underlit cirrus + alpenglow (4/5). Gotchas: the free tier counts each ray point as a call (use ~15-km spacing, ~100 cams/h; or pull DWD ICON-D2 / GFS grids directly for scale), foto-webcam archive times are local and the current day 502s.
- **Worldwide storms** are weaker: no open Europe-wide radar (DWD only), lightning needs EUMETSAT registration (MTG-LI); otherwise it's model-based (ICON-D2 `lpi`, CAPE) + MeteoAlarm warnings + RainViewer tiles. Fine for "there is a storm", not for the ≥50 dBZ + flashes rule.
- **Aurora**: a genuinely good live sensor chain exists at NOAA SWPC — IMAP L1 solar wind (1-min) → OVATION probability grid (5-min, ~1 h lead) → hemispheric power / Kp. `weather/swpc_aurora.py`. Rule = OVATION ≥10 % ∧ sun < −12° ∧ clear. Verified tonight: it fired at Yellowknife (26 %) and the Windy-relayed **AuroraMAX all-sky cam showed a green arc** at 04:32Z; ordinary cams beside it showed nothing. Cameras must be all-sky/long-exposure: Windy `meteo/landscape` at Tromsø 33, Reykjavik 14, Yellowknife 4 (incl. AuroraMAX), Churchill 0; UAF all-sky only serves images when dark.
- Recommendation: add both. Sunsets/aurora go worldwide almost for free; keep storm detection CONUS-only (MRMS/GLM) unless you register for EUMETSAT.
- Where Open-Meteo belongs: **not** in the CONUS loop (HRRR is one 10 MB fetch/hour for the whole grid with base/top/ceiling, scored 234 sites in 2 s). Use it only (a) outside HRRR coverage until GFS/ICON-D2 grids are wired in the same way, and (b) the ERA5 archive endpoint for per-site climatology, cached once.

## 5. Endpoints / URL patterns that worked (copy-paste)
```
# NWS
curl -H "User-Agent: nimbly (you@example.com)" -H "Accept: application/geo+json" \
  "https://api.weather.gov/alerts/active?status=actual&message_type=alert"

# MRMS (boto3 unsigned; region us-east-1)
s3://noaa-mrms-pds/CONUS/MergedReflectivityQCComposite_00.50/YYYYMMDD/MRMS_MergedReflectivityQCComposite_00.50_YYYYMMDD-HHMMSS.grib2.gz
s3://noaa-mrms-pds/CONUS/PrecipRate_00.00/YYYYMMDD/MRMS_PrecipRate_00.00_YYYYMMDD-HHMMSS.grib2.gz
# read: gzip.decompress → pygrib.open(path)[1]; .values (masked), .latlons() (lon 230–300 → subtract 360)

# GLM (GOES-East is goes19; West goes18)
s3://noaa-goes19/GLM-L2-LCFA/YYYY/DDD/HH/OR_GLM-L2-LCFA_G19_sYYYYDDDHHMMSSs_e..._c....nc   # netCDF4.Dataset('x', memory=bytes); flash_lat/flash_lon/flash_energy
# ABI band 13 CONUS
s3://noaa-goes19/ABI-L2-CMIPC/YYYY/DDD/HH/OR_ABI-L2-CMIPC-M6C13_G19_s...nc            # CMI + goes_imager_projection (geodetic→scan angle formula in evA notes)

# HRRR byte-range subset
s3://noaa-hrrr-bdp-pds/hrrr.YYYYMMDD/conus/hrrr.tHHz.wrfsfcfFF.grib2(.idx)
s3://noaa-hrrr-bdp-pds/hrrr.YYYYMMDD/alaska/hrrr.tHHz.wrfsfcfFF.ak.grib2(.idx)      # HH in 00,03,06,...
# idx lines "N:offset:d=...:VAR:level:fcst:"; want VAR in {LCDC,MCDC,HCDC,TCDC(entire atmosphere)} and HGT at {cloud ceiling, cloud base, cloud top, surface}
# s3.get_object(Bucket, Key, Range=f"bytes={offset_N}-{offset_N+1 - 1}")  → concatenate → pygrib

# NEXRAD L2 (if ever needed)
s3://unidata-nexrad-level2/YYYY/MM/DD/KGLD/KGLDYYYYMMDD_HHMMSS_V06

# Open-Meteo ERA5 (no key)
https://archive-api.open-meteo.com/v1/archive?latitude=LAT&longitude=LON&start_date=2016-01-01&end_date=YYYY-MM-DD&hourly=precipitation,cloud_cover_low,cloud_cover_mid,cloud_cover_high&timezone=UTC

# FAA WeatherCams (send a browser UA + Referer)
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
curl -A "$UA" -H "Referer: https://weathercams.faa.gov/map/" https://weathercams.faa.gov/api/sites            # 977 sites, 2.7 MB (lat/lon, elevation, sunrise/sunset, state, cameras[])
curl -A "$UA" -H "Referer: https://weathercams.faa.gov/map/" https://weathercams.faa.gov/api/cameras          # 3537 cams, 1.5 MB (cameraId, siteId, cameraBearing, cameraLastSuccess, cameraInMaintenance, lat/lon)
curl -A "$UA" -H "Referer: https://weathercams.faa.gov/map/" "https://weathercams.faa.gov/api/cameras?bounds=S,W|N,E"
curl -A "$UA" -H "Referer: https://weathercams.faa.gov/map/" "https://weathercams.faa.gov/api/sites?bounds=S,W|N,E"
curl -A "$UA" -H "Referer: https://weathercams.faa.gov/map/" https://weathercams.faa.gov/api/cameras/{cameraId}/images/last/13   # → payload[].imageUri, imageDatetime (max 13)
curl -A "$UA" https://images.wcams-static.faa.gov/webimages/{siteId}/{dd}/{cameraId}-{epoch_ms}.jpg   # 640×480 or 1920×1080 JPEG, Last-Modified header

# PhenoCam
https://phenocam.nau.edu/webcam/network/siteinfo/            # 1.1 MB JSON: site, lat, lon, tzoffset, active, date_start, date_end, nimage, camera_orientation
https://phenocam.nau.edu/api/cameras/?limit=N&offset=M      # paginated variant
https://phenocam.nau.edu/data/latest/<site>.jpg              # live frame
https://phenocam.nau.edu/api/middayimages/<site>/            # [{imgdate, imgpath}] whole history; last entries may be _IR_ → strip "_IR"
https://phenocam.nau.edu/api/siteimagelist/<site>/           # every image URL (huge, lags ~2 d, caps 5000)
https://phenocam.nau.edu/data/archive/<site>/YYYY/MM/<site>_YYYY_MM_DD_HHMMSS.jpg   # HHMMSS local standard time

# SPC storm reports
https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_filtered.csv                 # one day
https://www.spc.noaa.gov/wcm/data/2025_hail.csv  (_wind.csv, _torn.csv)         # whole year; time is CST (+6h → UTC)

# IEM NWS warning archive (api.weather.gov has no history)
https://mesonet.agron.iastate.edu/json/vtec_events.py?wfo=OAX&year=2024&phenomena=TO&significance=W
https://mesonet.agron.iastate.edu/api/1/vtec/sbw_interval.geojson?begints=2024-04-26T20:00Z&endts=2024-04-26T21:30Z&wfo=OAX
# ProbSevere archive
s3://noaa-mrms-pds/ProbSevere/YYYYMMDD/MRMS_PROBSEVERE_YYYYMMDD_HHMMSS.json

# Windy Webcams v3 (header: x-windy-api-key)
https://api.windy.com/webcams/api/v3/webcams?nearby=LAT,LON,KM&limit=50&offset=N&include=images,location,player,urls,categories
https://api.windy.com/webcams/api/v3/webcams?regions=US.KS&categories=meteo&limit=50          # one category per call
https://imgproxy.windy.com/_/full/plain/current/<webcamId>/original.jpg                       # 1280x720, no key (undocumented)
https://webcams.windy.com/webcams/public/embed/player/<webcamId>/day                          # scrape  day/<id>/original/<epoch>.jpg  (24 stills, ~50 min)
https://imgproxy.windy.com/_/full/plain/day/<webcamId>/original/<epoch>.jpg

# Worldwide cloud layers (no key; ~15 km ray spacing to respect the per-point rate limit)
https://api.open-meteo.com/v1/forecast?latitude=a,b,c&longitude=x,y,z&hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,cape,visibility,relative_humidity_2m&models=icon_d2&past_days=1&timezone=UTC
https://opendata.dwd.de/weather/nwp/icon-d2/grib/00/clch/            # ICON-D2 grids, bz2 GRIB per variable/hour
https://feeds.meteoalarm.org/api/v1/warnings/feeds-germany           # CAP warnings per country
https://api.rainviewer.com/public/weather-maps.json                  # global radar tiles

# Aurora (NOAA SWPC, no key)
https://services.swpc.noaa.gov/json/ovation_aurora_latest.json       # 1-deg probability grid, valid ~1 h ahead
https://services.swpc.noaa.gov/json/planetary_k_index_1m.json
https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json ; rtsw_wind_1m.json
https://services.swpc.noaa.gov/text/aurora-nowcast-hemi-power.txt

# Europe-only extras
https://www.foto-webcam.eu/webcam/<cam>/current/400.jpg ; /webcam/<cam>/YYYY/MM/DD/HHMM_la.jpg
https://api.panomax.com/1.0/instances/lists/public
```

## 6. Files
- `hist/` (replay mock: archive checks, `storm_candidates.json`, Mead 2024 replay, 8-event storm batch, statenrice1 2025 sunset series), `cams/phenocam_whitelist/`
- `nws_alerts_active.json`, `cams/` (FAA + PhenoCam listings, sky survey, heading test, optional sources, `windy/` with its own notes and Event-A archive frames), `evA_storm_ks/`, `evB_sunset/` (HRRR subsets, West-Coast frames + scores + contact sheets, Alaska), `evC_hist/`.
- Code: the throwaway scripts were promoted to `weather/` and `camera_ken/` (see their READMEs). Raw frames/GRIBs are git-ignored; contact sheets and a few key frames are kept.
