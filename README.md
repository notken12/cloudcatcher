# cloudcatcher

Sky-event webcam project (HackMIT): detect atmospheric events from live weather data, find public webcams looking at them,
verify in the frame.

- `validation/REPORT.md` — data-source validation (what works, latency, coverage, where automation breaks, replay mode).
- `weather/` — weather-side clients: MRMS/ProbSevere, GLM, GOES ABI, HRRR byte-range subsets, NWS/IEM, SPC, ERA5 climatology, sunset rules.
- `camera_ken/` — Ken's camera clients (FAA WeatherCams, Windy, PhenoCam) and image filters (sky fraction, colour index). Teammate's camera work lives elsewhere.
- `common/geo.py` — small geo helpers shared by both.

Setup: `uv sync` (add `--extra sky` for the SegFormer sky-fraction filter in `camera_ken/sky_fraction.py`; torch has no wheels for Intel Macs). Run modules from the repo root: `uv run python -m weather.mrms`, `uv run python -m camera_ken.faa_weathercams 38.34 -101.17 200`.
Windy needs `WINDY_API_KEY` in the environment. Event detector: `uv run python -m weather.events --out out/events.json` (see `weather/README.md`).
