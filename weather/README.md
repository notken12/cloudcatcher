# weather

Data-source clients from the validation run (`validation/REPORT.md`). All S3 access is unsigned; run from the repo root
with `uv run python -m weather.<module>`.

| module | source | notes |
|---|---|---|
| `s3.py`, `grib.py` | unsigned boto3, pygrib helpers | |
| `mrms.py` | MRMS composite reflectivity / PrecipRate / ProbSevere | 2-min, 1.5 MB CONUS files, archive 2020-10→; ProbSevere objects are the ready-made event record |
| `glm.py` | GLM flashes | 20-s files, ~4 s lag; goes16 before 2025-04-07, goes19 after |
| `goes_abi.py` | ABI L2 (band 13 CONUS, ACHAC cloud-top height) + lat/lon→pixel | `ACHTC` does not exist on GOES-19 |
| `hrrr.py` | byte-range cloud subset from the .idx, `CloudGrid` sampler | 10 MB / ~1 s; base/top NaN for thin cirrus; mask off-grid points |
| `nws.py` | live alerts (User-Agent required) and IEM VTEC archive | api.weather.gov has no history |
| `spc.py` | SPC daily / yearly storm reports | yearly times are CST |
| `climatology.py` | Open-Meteo ERA5 percentiles | good for cloud layers, useless for precip (zero-inflated); `cape` not in archive |
| `openmeteo.py` | worldwide cloud layers along a sun ray (ICON-D2/ICON-EU/ECMWF/GFS), CloudGrid-compatible | free tier counts each point as a call → 15-km spacing |
| `swpc_aurora.py` | OVATION grid, Kp, L1 solar wind, hemispheric power, aurora rule | verified against AuroraMAX all-sky cam |
| `sun.py` | astral wrappers | |
| `sunset_rules.py` | simple mid/high-over-site + clear-ray rule | |
| `sunset_rays.py` | Sunsethue-style ray model | implemented, **not validated** (ρ≈0 on one evening) |
| `sunset_scan.py` | score a site list against a subset | `uv run python -m weather.sunset_scan subset.grib2 sites.json out.json` |
