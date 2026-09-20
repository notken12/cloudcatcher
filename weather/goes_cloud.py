"""GOES-18/19 cloud products as a sunset-model input: COD (cloud optical depth, 2 km,
daylight only — the retrieval needs reflected sunlight) and ACHAC HT (cloud-top height,
10 km, day/night). HRRR under-reports the thin cirrus that makes sunsets; the satellite
sees it directly.
"""
import datetime as dt

import netCDF4
import numpy as np

from weather.goes_abi import FixedGrid, latest_product, open_dataset

BUCKETS = {"west": "noaa-goes18", "east": "noaa-goes19"}


def bucket_for(lon: float) -> str:
    # GOES-18 (west) covers CONUS west of ~103W best; either sees all of CONUS.
    return BUCKETS["west"] if lon < -103 else BUCKETS["east"]


def latest_grids(when: dt.datetime, lon_hint: float = -120) -> dict:
    """Most recent CODC and ACHAC scans starting at or before `when` (same 5-min slot)."""
    bucket = bucket_for(lon_hint)
    grids = {}
    for product, variable in (("ABI-L2-CODC", "COD"), ("ABI-L2-ACHAC", "HT")):
        for hour in (when, when - dt.timedelta(hours=1)):
            obj = latest_product(product, hour, bucket)
            if obj is None:
                continue
            ds = open_dataset(bucket, obj["Key"])
            grids[variable] = (ds, FixedGrid(ds), obj["Key"])
            break
    return grids


def value_at(grid: tuple[netCDF4.Dataset, FixedGrid, str], lat: float, lon: float, half: int = 2) -> float:
    """DQF semantics differ per product. COD's is a bitmask — bit0 day algorithm ran,
    bit1 night ran — accept either (the sunset scene is twilight by definition and
    thin cirrus trips the degraded flags); COD=0 on a retrieved pixel means clear,
    fill means no retrieval. ACHAC's is an enum: 0 good, 1 marginal, 2 attempted,
    3 bad — accept <=2. HT is NaN where no cloud was detected: NaN ≈ clear, not
    missing."""
    ds, fg, _ = grid
    variable = "COD" if "COD" in ds.variables else "HT"
    i, j = fg.index(lat, lon)
    values = ds.variables[variable][max(i - half, 0):i + half + 1, max(j - half, 0):j + half + 1]
    values = np.ma.filled(values.astype(float), np.nan)
    dqf = np.asarray(ds.variables["DQF"][max(i - half, 0):i + half + 1, max(j - half, 0):j + half + 1])
    values = np.where((dqf & 3) != 0 if variable == "COD" else dqf <= 2, values, np.nan)
    return float(np.nanmedian(values)) if np.isfinite(values).any() else float("nan")


def cloud_at(when: dt.datetime, lat: float, lon: float) -> dict:
    """{cod, cloud_top_m, files} at one site; NaN where the retrieval had no valid pixel."""
    grids = latest_grids(when, lon)
    out = {"cod": float("nan"), "cloud_top_m": float("nan"), "files": {}}
    for variable, grid in grids.items():
        value = value_at(grid, lat, lon)
        out["cod" if variable == "COD" else "cloud_top_m"] = value
        out["files"][variable] = grid[2].split("/")[-1]
    return out


if __name__ == "__main__":
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    print(cloud_at(now, 38.77, -123.53))
