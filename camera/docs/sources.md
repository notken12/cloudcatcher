# Camera sources — what to parse, in what order, and what's actually alive

Every endpoint below was probed with `curl` on 2026-09-20 (see `probe.sh` in the
session; the same script becomes `camera/ingest/healthcheck.py` later). Status
codes are real, not copied from the docs. "Heading" = whether the catalog gives
us camera azimuth for free (this matters a lot — see `preprocessing-plan.md`).

Legend: **L** live frame, **H** history/archive, **N** usable at night,
**HLS** video stream (needs a player or a frame grab via ffmpeg).

## Tier 1 — parse first (public, JSON catalog, direct JPEG, no key)

| Source | Catalog endpoint (verified) | Count | Frame | Heading | H | N | Notes |
|---|---|---|---|---|---|---|---|
| **ALERTCalifornia / AlertWest** | `https://raw.githubusercontent.com/Deasus/firestorm-cameras/main/data/cameras.json` (community mirror of the official catalog) | 1,890 | L JPEG, `image_url` per cam | partial (many are PTZ on fire lookout towers; some have `direction`) | some (official site keeps a timelapse) | yes (many are IR/low-light) | Best western-US sky coverage: mountaintop, horizon-to-horizon, often above marine layer → great for undercast, sunset, lenticular, thunderstorm anvils. Attribution required (UCSD/ALERTCalifornia). |
| **Caltrans CWWP2** | `https://cwwp2.dot.ca.gov/data/d{01..12}/cctv/cctvStatusD{01..12}.json` | ~275 / district (~2k total) | L JPEG `currentImageURL`, HLS `streamingVideoURL` | **yes** (`direction` field N/S/E/W…) | **yes**: `previousImageURL[]` array (~last 12 frames) | no | Cleanest US DOT source. Per-record `inService`, `imageUpdateFrequency`. |
| **NY 511** | `https://511ny.org/api/getcameras?format=json&key=` (empty key works) | 2,933 (1,565 enabled) | HLS `VideoUrl` + JPEG `Url` | no (name text only) | no | no | Dense NE-US coverage incl. Adirondacks/Catskills/Long Island shoreline. |
| **Ontario 511** | `https://511on.ca/api/v2/get/cameras?format=json` | 945 | JPEG per `Views[]` | **yes** (`Views[].Direction`) | no | no | CARS standard schema; same parser works for other CARS deployments once a key is obtained (see Tier 2). |
| **Finland Digitraffic** | `https://tie.digitraffic.fi/api/weathercam/v1/stations` (needs `Accept-Encoding: gzip` + `Digitraffic-User:` header) | 811 stations, ~3 presets each | JPEG `https://weathercam.digitraffic.fi/{presetId}.jpg` | **yes-ish** (`direction` = road direction, `presentationName` = place looked at; derive azimuth from road geometry) | yes (`/history` endpoint, 24 h) | yes (Lapland winter = long night, aurora hits) | Excellent metadata quality, CC BY 4.0, `dataUpdatedTime` per station. Top aurora candidate. |
| **Iceland Vegagerðin** | `https://gagnaveita.vegagerdin.is/api/vefmyndavelar2014_1` | 497 | JPEG `Slod` | **yes** (text: `séð til norðurs/vesturs/…` = looking N/W/…; ~200 of 497 parse) | no | yes (polar night, aurora) | Also lat/lon `Breidd/Lengd`. Free, no key. |
| **Panomax** | `https://api.panomax.com/1.0/maps/panomaxweb` (gzip) → `instances{}` | 626 | JPEG `https://panodata.panomax.com/cams/{id}/recent_small.jpg` (+`preview_og.jpg`), timestamp via `/1.0/cams/{id}/images/recent` | **yes**: `zeroDirection` + `viewAngle` (often 180–360°) | yes (per-cam day archive on `{slug}.panomax.com`) | **`nightVision` flag** (316 of 626 true) | Alpine 360° panoramas. This is the single richest heading source. Frame grab is for internal VLM use; public display should link to the panomax page (ToS). |
| **foto-webcam.eu** | `https://www.foto-webcam.eu/webcam/` index (HTML) → `/webcam/{slug}/current/400.jpg` | ~500 | JPEG at 400/1200/full | in page text (FOV/direction in description) | **yes**: `/webcam/{slug}/YYYY/MM/DD/HHMM_la.jpg` back years | yes (long-exposure HDR, stars visible) | Highest image quality of any source; ideal for sunset/mammatus/lenticular/fog demo. Scrape politely (1 req/s); attribution required. |
| **NOAA NDBC BuoyCAMs** | `https://www.ndbc.noaa.gov/buoycams.php` → JSON list | 90 | JPEG (6-panel strip, 360° in 60° slices) | **yes** (implicit: 6 panels = 6 azimuth bins; panel order fixed) | yes (recent images page) | no | Only open-ocean source. Sunrise/sunset/thunderstorm over water. Public domain. |
| **PhenoCam** | `https://phenocam.nau.edu/api/cameras/?format=json` | 1,084 | JPEG `https://phenocam.nau.edu/data/latest/{site}.jpg` | **yes** (`camera_orientation`) | **yes**: full archive every 30 min back to 2008 | some (IR variants) | Camera points at canopy; sky is top 20–40 % of frame. Use for **historical backtesting** (fog/undercast/sunrise labels) rather than live hero shots. |
| **IEM (Iowa Mesonet) webcams** | `https://mesonet.agron.iastate.edu/geojson/webcam.geojson?network={KCCI,KELO,KCRG,ISUC,...}`; per-time: `/json/webcam.py?network=X&ts=YYYYMMDDHHMM` | ~100 | JPEG | **yes** (`angle` / `drct`, PTZ but logged per frame) | **yes**: 5-min archive back to ~2003 | no | Storm-chaser-grade Midwest coverage; heading logged **per frame** — ideal thunderstorm/mammatus backtest set. |
| **USGS volcano cams** | HTML pages per observatory (`usgs.gov/volcanoes/{kilauea,mount-st-helens,...}/webcams`) | ~40 | JPEG | in captions | no | **yes** (thermal/low-light) | Steam/ash plumes ≠ weather, but Kīlauea/HVO cams are great night sky + rainbow spots. |
| **NPS air-quality webcams** | `https://www.nps.gov/subjects/air/webcams.htm` (HTML) | ~30 | JPEG | in captions (fixed, documented view) | **yes** (hourly archive) | no | Grand Canyon, Yosemite, Great Smokies, Acadia (sunrise!). Public domain. |

## Tier 2 — parse once we have a key / header (10-minute signup, still free)

| Source | How to unlock | Why bother |
|---|---|---|
| **Windy Webcams API v3** `https://api.windy.com/webcams/api/v3/webcams` | free key → `x-windy-api-key` header; docs at `/webcams/docs` | ~70k cams worldwide, `location`, `categories` (incl. *sky*, *mountain*, *beach*), `player`/`images` URLs, `lastUpdatedOn`. **Fills every hole outside NA/EU** (Japan, NZ, S. America, Africa). URLs are signed & expire → resolve at display time, never cache. Attribution/embed rules in ToS. |
| **CARS 511 family** (UT `udottraffic.utah.gov`, IA, MN, NE, LA `511la.org`, KS, WI, MA, GA…) | each portal gives a free developer key; endpoint `/api/v2/get/cameras?key=…` | Same schema as Ontario → one parser, +5–8k cameras with `Direction`. Probed UT/LA → `400 Invalid Key` = alive, just needs key. |
| **WSDOT** `wsdot.wa.gov/Traffic/api/HighwayCameras/HighwayCamerasREST.svc/GetCamerasAsJson?AccessCode=` | free access code | Cascades passes: lenticular, fog, undercast. Probed → `401` (alive). |
| **FAA WeatherCams** `https://weathercams.faa.gov/api/sites` | returns `401` without the bearer token the SPA fetches on load; capture it in DevTools (or Playwright) — token is public-facing but session-scoped | ~1,000 Alaska/Hawaii/CONUS sites, 4 fixed cams per site with **documented heading**, 10-min refresh, and a 24-h archive. Alaska = aurora at night. Best single US source if the token flow is stable. |
| **NSW Live Traffic (AU)** `api.transport.nsw.gov.au/v1/live/cameras` | free Open Data key | Only structured Australian source we found; needed for southern-hemisphere coverage. |
| **Norway (Statens vegvesen)** | The old `webkamera.atlas.vegvesen.no` service is **shut down** (the SPA literally says so). Images now only via DATEX II (registration) at `kamera.atlas.vegvesen.no/api/images/{id}` | Skip unless we want Lofoten aurora cams badly; Finland + Iceland cover the same use case with zero friction. |

## Tier 3 — hand-picked / embed-only (for the demo hero panel)

These don't have catalogs; we store them as individual `Camera` rows with
`source_kind = embed`. The frontend renders an iframe / `<video>`; the VLM
gets a frame via a screenshot or (for YouTube) `yt-dlp -g` → ffmpeg single frame.

**YouTube 24/7 streams — yes, this is easy.** `yt-dlp` gives us the HLS
manifest for a live video ID; ffmpeg grabs one JPEG in ~2 s; the page embeds
`https://www.youtube.com/embed/{VIDEO_ID}?autoplay=1&mute=1`. Store
`{video_id, lat, lon, heading_est, hfov_est, night_ok}` by hand. Things I'd like
you to find and paste video IDs for (search terms in quotes; pick streams with
>1 yr uptime and a fixed view):

- "Jackson Hole town square live" (SeeJH) — fixed, W-facing, classic sunset.
- "Yellowstone Old Faithful live" (NPS) — wide sky, great thunderstorm anvils.
- "Fairbanks aurora live cam" / "Explore.org northern lights" — night.
- "Tromsø live cam" or "Abisko aurora sky station live" — night, Europe.
- "Denver skyline live 4K" / "Boulder Flatirons live" — Front Range = mammatus & lenticular capital.
- "Mauna Kea live" (Subaru/Keck "Mauna Kea webcam live") — undercast + night sky.
- "Table Mountain Cape Town live" — southern hemisphere, tablecloth fog.
- "Mount Fuji live cam" (Fujigoko TV) — lenticular over Fuji is a meme for a reason.
- "Cape Town / Sydney Opera House / Rio Copacabana live" — S-hemisphere sunsets to balance the catalog.
- "Iceland Reykjavik live" or "Vestrahorn live" — aurora + fog.
- "Miami Beach live" / "Key West live" — daily thunderstorm + rainbow.

Other embed-only catalogs worth a small manual list (each ~10 cams):
- **Explore.org** (`explore.org/livecams`) — Katmai, Fairbanks aurora, Kauai. Embeds allowed.
- **SkylineWebcams** — HLS player only, ~2k cams, embed code provided, heavy on Med/EU sunsets.
- **UAF Poker Flat all-sky** `allsky.gi.alaska.edu` — page renders latest frame from `/images/initial.jpg` then JS-refreshes; grab with Playwright. Night only.
- **Roundshot** (`roundshot.com` reference list) — Swiss 360° panos; each cam has its own `{name}.roundshot.com` with `/current/` JPEG. Add 5–10 by hand (Jungfraujoch, Zermatt, Titlis).
- **Global Meteor Network / AllSkyCam** — dozens of hobby all-sky cams with latest JPEG; low resolution, but real night coverage. Add 5 by hand.
- **SpaceWeatherLive webcam directory** — it's a *directory*, not a source; use it to pick aurora cams (Kiruna IRF `irf.se/alis`, Sodankylä, Yellowknife AuroraMAX) and record their direct JPEG URL after checking in a browser (IRF redirected our probe).

## Dead / skip (verified)

| Source | Result | Verdict |
|---|---|---|
| Meteocam.net | `503 maintenance` | skip |
| yr.no webcams | `410 Gone` | skip |
| Wunderground webcams | `404` | discontinued |
| snow-forecast / mountainwatch cams | `404` | skip |
| Bergfex | `429` on first request | aggressive rate-limit; feratel iframe only → skip |
| timeanddate / webcamtaxi / worldcam | Cloudflare challenge / bad cert | aggregators anyway; skip |
| trafficnz.info | `500` | NZ: use Windy instead |
| EarthCam, Surfline, Instagram/Snap Map | ToS forbid | never |
| "Insecam"-style unsecured cam lists | — | never (ethics + policy) |

## Coverage summary after Tier 1+2

- North America: ~10k cams (dense), Alaska night via FAA/UAF.
- Europe: ~3k (Alps dense via Panomax/foto-webcam; Nordics via Digitraffic/Iceland).
- Oceans: NDBC only (90).
- Rest of world: **Windy is the only scalable answer**; hand-picks for hero shots.
- Night-capable with real signal: Digitraffic Lapland, Iceland, Panomax `nightVision`, foto-webcam long exposures, ALERTCalifornia IR, FAA Alaska, all-sky hobby nets. Expect ~15 % of the catalog to be `night_ok=true`.
- Historical depth for backtesting: PhenoCam (2008→), IEM (2003→, with heading), foto-webcam (years), Caltrans (last 12 frames only), FAA (24 h), Digitraffic (24 h).
