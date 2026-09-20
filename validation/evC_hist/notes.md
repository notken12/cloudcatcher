# Event C — historical back-test: hail/severe storm over Shale Hills CZO, PA, 2025-05-16 19:30Z

## Picking the event
- SPC filtered storm reports CSV, no auth: `https://www.spc.noaa.gov/climo/reports/250516_rpts_filtered.csv` (also tried 240715, 240521, 250315, 240528). Report times are UTC HHMM; columns Time,…,Lat,Lon,Comments; sections switch at lines starting `Time,` (tornado/wind/hail).
- Joined against PhenoCam siteinfo (site lat/lon): 181 reports within 25 km of a PhenoCam site on 2025-05-16. Chose hail report 19:30Z at (40.63, −77.86), 3.5 km from `shalehillsczo` (40.6395, −77.9065), archive 2012→present, daytime (15:30 EDT).
- Other daytime candidates for later: NEON.D02.SERC (MD) 22:10Z wind, manilacotton (AR) 13:05Z wind.

## 1. DETECT (MRMS archive)
- `s3://noaa-mrms-pds/CONUS/MergedReflectivityQCComposite_00.50/20250516/…_20250516-193040.grib2.gz`. Archive on AWS starts **2020-10-14**.
- dBZ at site (2 km): 19:00Z 21.5 → **19:30Z 62.5** → 20:00Z 16.5. 153 px ≥50 dBZ within 30 km at 19:30Z. Detection rule fires at the exact minute of the report.

## 2. RARITY — same caveat as Event A (precip distribution is zero-inflated).

## 3. FIND CAMERAS
- PhenoCam URL pattern is `https://phenocam.nau.edu/data/archive/<site>/<YYYY>/<MM>/<site>_<YYYY>_<MM>_<DD>_<HHMMSS>.jpg` — **no `/DD/` directory** (the handoff pattern is wrong). Seconds vary (…05, …06, …08), so you cannot construct the URL without a listing.
- Listing: `https://phenocam.nau.edu/api/siteimagelist/<site>/` returns **every** image (17 MB / 177k URLs for this site; date params are ignored; capped at 5000 entries for some sites, e.g. proctor2). Directory listings under `/data/archive/` return 403/404. `https://phenocam.nau.edu/api/middayimages/<site>/` gives one image per day (whole history, 640 KB) and **returns the IR frame (`_IR_`) as the last entry** when the site has IR — drop `_IR` from the path to get the RGB twin.
- Filenames are **local standard time** (`tzoffset` in siteinfo, ignore DST): 14:30:05 EST = 19:30:05Z. `Last-Modified` header matches (19:30:39 GMT).
- 28 frames that day; **gap 15:00→20:00 local** (storm/power?), and 12:30→13:30. 2025 overall: 355 days with images, median 38/day, 1 day with <10.

## 4. SKY FILTER
- 14:00 EST frame: sky_frac 0.000; 14:30 EST (in storm): 0.046 (fog misread). Camera looks down at forested hillside — correctly rejected by a ≥0.2 threshold.
- Survey of 80 random live CONUS PhenoCam sites, midday RGB frames: sky ≥0.10: 41 %, **≥0.20: 20 %**, ≥0.30: 8 %, ≥0.40: 4 %, median 0.07. ≈100 usable sky cams in CONUS out of 529 live. Spot checks (shenandoah 0.37, Lamar 0.70) agree with eyes.

## 5. VERIFY
- 14:30 EST frame: heavy rain streaks, mist, water on lens → the storm is unmistakably *there* (4/5 as "storm present"), but 0/5 as a sky view. A vision LLM would say "yes, heavy rain", which is the wrong question for a sky-view product.
- Sky-filter → verify: 0 cameras survive (no sky); the event is verifiable only as "precipitation at camera".
