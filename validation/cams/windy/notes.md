# Windy Webcams API v3 — tested 2026-09-20 03:30–03:50Z (key supplied by Ken; referenced as $WINDY_KEY, not stored here)

## Endpoints that worked
```
H='x-windy-api-key: $WINDY_KEY'
GET https://api.windy.com/webcams/api/v3/webcams?nearby=LAT,LON,RADIUS_KM&limit=50&offset=N&include=images,location,player,urls,categories
GET https://api.windy.com/webcams/api/v3/webcams?regions=US.KS&categories=meteo&limit=50&offset=N     # regions=US.XX, countries=US, categories=one per call (comma list behaved as AND → 0)
GET https://api.windy.com/webcams/api/v3/categories
# images (documented): images.current.preview = 400×224, thumbnail 200×112, icon 48; images.daylight.* = last daytime frame (burned-in stamps showed ~11:30–12:00 local)
# undocumented but works, no key needed:  https://imgproxy.windy.com/_/full/plain/current/<webcamId>/original.jpg  → 1280×720 (or provider native)
# archive: the day/month players embed a still list:
GET https://webcams.windy.com/webcams/public/embed/player/<webcamId>/day     # HTML; regex  day/<id>/original/(\d+)\.jpg  → 24 frames @ ~50 min for the last ~20 h
GET https://webcams.windy.com/webcams/public/embed/player/<webcamId>/month   # 30 frames, one per day (~17Z)
GET https://imgproxy.windy.com/_/full/plain/day/<webcamId>/original/<epoch>.jpg
```
Response fields: `webcamId, status, lastUpdatedOn, title, categories[], location{latitude,longitude,city,region_code}, images{current,daylight}, player{day,month,year,lifetime}, urls{detail,provider}`. Headers carried no rate-limit info; ~60 requests in a minute went through. Response time 0.1 s. Image `cache-control: max-age=150`.

## Coverage
- US total **33,136** (traffic 30,801 = 93 %, city 4,039, meteo 1,628, landscape 794, mountain 320, lake 227, beach 125).
- Plains gap vs FAA/PhenoCam: KS **392** (FAA 0), NE 262, IA 1,113, TX 3,807, CO 214, SD 34, ND 24, **OK 11**. Non-traffic in the Plains is thin: KS meteo 11 / landscape 2 / city 8; NE 14/4/4; IA 53/6/27; TX 199/5/664; CO 24/89/21 (+65 mountain).
- Event A, 150 km around (38.34, −101.17): **29 cams** (27 KDOT traffic, 2 `landscape` at Meade State Lake, 1 TV skycam Dodge City `city,meteo`), all `active`, `lastUpdatedOn` 7–43 min old (median ~10). Coordinates are junction-precise (4 decimals; Garden City cam sits 4 km NE of downtown at the actual US-50 junction) — **not city centroids** in this sample.

## Sky yield (SegFormer on `daylight` full-size frames)
- 60 random KS+NE cams (traffic-dominated): sky ≥0.10: 38 %, **≥0.20: 13 %**, ≥0.30: 7 %, median 0.05. DOT cams are tilted at the pavement (`survey/sheet_daylight8.jpg`).
- 60 random US `landscape`+`meteo`: ≥0.10: 93 %, **≥0.20: 88 %**, ≥0.40: 58 %, median 0.42; `lastUpdatedOn` median 19 min, 93 % ≤ 1 h, none >1 day.
→ Filter Windy by category (`meteo`, `landscape`, `mountain`, `lake`, `coast`, `beach`) first; traffic cams only after a per-camera sky-fraction pass (≈1 in 8 survive).

## Event A verification via Windy archive (day player)
- Dighton junction cams (1606334644 W / 1606334605 N / 1606334668 E), 65 km ENE of the 02:30Z cell: frames 00:18Z and 01:09Z — road only, ~0–8 % sky; the W cam's 00:18Z sun glare confirms its heading but there is no sky to show a storm. **0/3.**
- Dodge City SkyView (1632431865, 119 km ESE, 800×450): 00:45Z frame = sunset with a dark cloud bank on the WNW horizon at the storm's bearing — plausibly the anvil, not certain → 2/5 storm-visible, 3/5 sunset. 01:45Z night → nothing. `evA_archive/sheet_dodge.jpg`.
- Archive spacing (~50 min) is too coarse to hit the sub-10-minute peaks seen at Lamar.

## Gotchas
- `daylight` image can be many hours old (midday) — fine for the sky-fraction pass, not for verification.
- No bearing/heading field. Titles sometimes encode direction ("… › West", "EB"); `viewCount` and `status` are present.
- Multi-category filter in one call returned 0 — query per category.
- Provider URLs (kandrive.gov/camera/NNNN, ksn.com skyview) are exposed; the DOT feeds themselves usually refresh every 1–5 min if you want to bypass Windy's ~10-min ingest.
