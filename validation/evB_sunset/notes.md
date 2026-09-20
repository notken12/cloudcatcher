# Event B — sunset. (B1) back-test Lamar CO 00:50Z; (B2) blind test 234 West-Coast FAA sites at ~02:00Z; (B3) live Alaska 03:35–04:40Z

## HRRR access
- `s3://noaa-hrrr-bdp-pds/hrrr.YYYYMMDD/conus/hrrr.tHHz.wrfsfcfFF.grib2` (+ `.idx`). Hourly runs; **f01 lands ~53 min after init** (t01z f01 uploaded 01:53Z). Full file 177 MB; the `.idx` gives byte offsets — fetching LCDC/MCDC/HCDC/TCDC (msgs 112–116), HGT cloud ceiling/base/top (117/118/121), HGT surface (63), HPBL (150) by `Range:` = **8–10 MB in ~1 s**.
- Alaska: `hrrr.YYYYMMDD/alaska/hrrr.tHHz.wrfsfcfFF.ak.grib2`, **3-hourly runs** (00,03,06…), 00z f04 uploaded 00:48Z. 919×1299 grid.
- Layer definitions are pressure-based (low <~3.5 km, mid 3.5–8 km, high 8–13.5 km MSL), not 2/6/11 km. Cloud base/top/ceiling are m MSL and are **NaN for thin cirrus** even when HCDC=100 % (condensate threshold) — see model notes.
- Byte-range script: `scripts/` (inline in this session; pattern: parse idx, `s3.get_object(Range=f'bytes={start}-{next_start-1}')`).

## Sun geometry
astral: `azimuth(Observer(lat,lon), t)`, `sunset(obs, date, tzinfo=UTC)`. Lamar sunset 00:52Z az 271.8°.

## B1 — Lamar CO (38.077, −102.696), valid 01Z from t00z f01, frame at 00:48Z was 5/5 (mammatus lit gold)
- Simple rule (mid/high ≥30 % over site, low ≤30 % along sun ray 0–100 km): site lcc 0, mcc 51, hcc 87 %; ray low 0 % → **FIRE**. High deck ends ~75 km west → gap.
- Sunsethue-style ray model (`weather/sunset_rays.py`, HRRR layers gated by base/ceiling/top, opacity low 1.0/mid 0.8/high 0.4, δ = 0–4° sun depression, view rays 1–45° elevation): v1 (uniform 6–11 km high layer) **0.056 — wrong** (deck blocks its own underside). v2 (gate to [base,top]) 0.018 — worse, because `cloud base` 7.6 km came from a thin mid layer, not the anvil. v3 (light the deck **underside at the ceiling height**, block sun rays only inside [ceiling, top], cirrus opacity 0.4) → **0.418 at δ=1°**. HRRR ceiling 10.2 km / top 11.0 km = a thin anvil with clear air beneath, exactly what the frame shows.
- Lesson: the show was in the **East** (anti-solar) camera. The rule must consider cameras facing away from the sun; the West cam here was dead since 2023 anyway.

## B2 — West Coast blind test (234 FAA sites, 866 cams, frames at sunset+5 and +15 min, HRRR t01z f01 valid 02Z)
- Frames: `west_frames/` + `index.json` (grabbed at 02:57Z before the 13-frame archive rolled off). Scores: `west_scores.json`. Ranking done **before** looking at frames; contact sheets `sheet_top8.jpg`, `sheet_zero8.jpg` (west-facing cam, sunset+15).
- Model top-8 (my 1–5 rating): Cave Junction 0.61→2, Barry Ridge 0.52→**4**, Fiddler 0.49→1 (sky blown out by camera exposure), Hornbrook 0.40→1, Henkle Butte 0.38→3, Patton Hill 0.34→1 (blown out), High Divide 0.32→2, Shelter Cove 0.30→**4**. Mean 2.3.
- Random 8 with score 0: Shafer Butte→2 (real clouds, HRRR had none), Fairview→1, Pierce Co→2, Seaside→1.5, Fox Mtn→1, Crowsnest Pass AB→3 (**outside HRRR domain**; nearest-neighbour sampling silently returned edge values — must mask), Potosi→1, Black Mtn→1. Mean 1.6.
- Ukiah (hcc 94 %, thin cirrus, base 2.1 km, top NaN): sunset+5 → 2/5, **sunset+15 → 4/5** (pink underlit cirrus). Model v2 gave 0.0 (top NaN → gated out); v3 gives it a score. Peak colour is at δ≈2–3° (10–20 min after sunset), so frame selection must target sunset+10…+20, not sunset.
- 185/234 sites had no cloud in HRRR (ridge over the West) — mostly bland sunsets; the two 4/5 frames were both in the top-8. Signal is real but the discriminator is weak; ~2/8 precision at the top.

## B3 — live Alaska (HRRR-AK t00z f04 valid 04Z; 111 sites with sunset 03:30–04:40Z and a sunward fresh cam)
- Ranked in `ak_scores.json`; top: Livengood 0.27 (04:05Z), Eagle 0.23 (03:35Z), Minto 0.22 (04:08Z), Fort Yukon 0.16, Central 0.13; earlier simple-rule picks Knob Ridge / Delta Junction.
- Frames fetched after the fact: see `ak_frames/` and the result section appended below.

## Heading estimate from frames (step 4)
- (dropped: did not work) on 2026-09-17 full-day frames (siteimagelist lags ~2 days): norriepoint (declared SSE 157°) → one saturated frame → 118° (off 39°); proctor2 (declared WSW 247°) → "246°" but the frame was overcast with no sun — coincidence. **0/2 usable.** Needs clear days and the sun actually crossing the FOV; most PhenoCams face N by design so it never does. Use metadata (`camera_orientation` PhenoCam, `cameraBearing` FAA) instead.
