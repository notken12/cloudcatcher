"""GLM lightning flashes (20-s files, ~4 s upload lag). GOES-East is goes16 before spring 2025 and goes19 after."""
import datetime as dt

import netCDF4
import numpy as np

from common.geo import distance_km
from weather.s3 import get_bytes, list_keys

GOES_EAST_CUTOVER = dt.date(2025, 4, 7)


def east_bucket(when: dt.datetime) -> str:
    return "noaa-goes16" if when.date() < GOES_EAST_CUTOVER else "noaa-goes19"


def file_start(key: str) -> dt.datetime:
    stamp = key.split("_s")[1][:13]
    return dt.datetime.strptime(stamp, "%Y%j%H%M%S").replace(tzinfo=dt.UTC)


def flashes(start: dt.datetime, end: dt.datetime, bucket: str | None = None) -> dict[str, np.ndarray]:
    """All flashes with start time in [start, end): lat, lon, energy, time."""
    bucket = bucket or east_bucket(start)
    hours = {start.replace(minute=0, second=0, microsecond=0)}
    hours.add(end.replace(minute=0, second=0, microsecond=0))
    keys = [o["Key"] for h in sorted(hours) for o in list_keys(bucket, f"GLM-L2-LCFA/{h:%Y/%j/%H}/")]
    keys = [k for k in keys if start <= file_start(k) < end]
    lats, lons, energies, times = [], [], [], []
    for key in keys:
        ds = netCDF4.Dataset("inmem", memory=get_bytes(bucket, key))
        lats.append(ds.variables["flash_lat"][:])
        lons.append(ds.variables["flash_lon"][:])
        energies.append(ds.variables["flash_energy"][:])
        times.append(np.full(len(ds.variables["flash_lat"]), file_start(key).timestamp()))
    concat = lambda parts: np.concatenate(parts) if parts else np.array([])
    return {"lat": concat(lats), "lon": concat(lons), "energy": concat(energies), "time": concat(times), "n_files": len(keys)}


def count_near(flash_set: dict, lat: float, lon: float, radius_km: float) -> int:
    if len(flash_set["lat"]) == 0:
        return 0
    return int((distance_km(lat, lon, flash_set["lat"], flash_set["lon"]) < radius_km).sum())


if __name__ == "__main__":
    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(minutes=5)
    fs = flashes(start, end)
    print(f"{fs['n_files']} files, {len(fs['lat'])} flashes full-disk in last 5 min")
