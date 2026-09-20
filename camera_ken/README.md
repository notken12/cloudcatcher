# camera_ken

Ken's camera-source clients and image filters from the data-source validation (see `validation/REPORT.md`).
Kept separate from the teammate's camera work; nothing here is wired into a pipeline yet.

Run modules from the repo root with `uv run python -m camera_ken.<module> ...`.

| module | what | key facts learned |
|---|---|---|
| `faa_weathercams.py` | sites/cameras/last-13-images for weathercams.faa.gov | needs browser UA; exact `cameraBearing`; 8-10 min cadence; no archive beyond 13 frames; none in the Plains |
| `windy.py` | Windy v3 nearby/region queries, full-size image URL, scraped day/month archive | `WINDY_API_KEY` env; 93% traffic cams (13% see sky), `meteo/landscape` cams 88% see sky; no heading field |
| `phenocam.py` | site list, live filter, image lists, local-time filename parsing | only US still archive (30 min, 2008→); ~20% of sites see sky; filenames in standard time (mostly) |
| `sky_fraction.py` | SegFormer sky mask / fraction + exposure sanity | daytime only; cache per camera |
| `colour_index.py` | warm-hue chroma in the sky mask (sunset quality proxy) | ranked the West-Coast evening correctly |
| `build_phenocam_whitelist.py` | scores all live CONUS PhenoCam sites | 89 of 449 have ≥20% sky; output in `validation/cams/phenocam_whitelist/` |

Examples:
```
uv run python -m camera_ken.faa_weathercams 38.34 -101.17 200
WINDY_API_KEY=... uv run python -m camera_ken.windy 38.34 -101.17 150
uv run python -m camera_ken.phenocam 38.34 -101.17 300
uv run python -m camera_ken.sky_fraction some/frames/*.jpg
```
