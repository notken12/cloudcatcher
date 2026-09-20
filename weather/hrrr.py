"""HRRR on s3://noaa-hrrr-bdp-pds. Hourly CONUS runs (f01 lands ~53 min after init), 3-hourly Alaska.
Whole files are 177 MB; the .idx sidecar lets us byte-range just the fields we need (~10 MB)."""
import datetime as dt
import os

import numpy as np
from scipy.spatial import cKDTree

from common.geo import distance_km
from weather.grib import read_first_message, read_messages
from weather.s3 import client, get_bytes

BUCKET = "noaa-hrrr-bdp-pds"

CLOUD_FIELDS = [
    ("LCDC", "low cloud layer"),
    ("MCDC", "middle cloud layer"),
    ("HCDC", "high cloud layer"),
    ("TCDC", "entire atmosphere"),
    ("HGT", "cloud ceiling"),
    ("HGT", "cloud base"),
    ("HGT", "cloud top"),
    ("HGT", "surface"),
    ("HPBL", "surface"),
    ("RH", "2 m above ground"),
]

FIELD_KEYS = {
    "lcc": ("lcc", "lowCloudLayer"),
    "mcc": ("mcc", "middleCloudLayer"),
    "hcc": ("hcc", "highCloudLayer"),
    "tcc": ("tcc", "atmosphere"),
    "ceil": ("gh", "cloudCeiling"),
    "base": ("gh", "cloudBase"),
    "top": ("gh", "cloudTop"),
    "orog": ("orog", "surface"),
    "blh": ("blh", "surface"),
    "rh": ("2r", "heightAboveGround"),
}


def key_for(run: dt.datetime, forecast_hour: int, domain: str = "conus") -> str:
    suffix = "" if domain == "conus" else ".ak"
    return f"hrrr.{run:%Y%m%d}/{domain}/hrrr.t{run:%H}z.wrfsfcf{forecast_hour:02d}{suffix}.grib2"


def latest_run(now: dt.datetime, forecast_hour: int, domain: str = "conus") -> dt.datetime | None:
    """Most recent run whose requested forecast hour is already uploaded."""
    step = 1 if domain == "conus" else 3
    run = now.replace(minute=0, second=0, microsecond=0)
    run -= dt.timedelta(hours=run.hour % step)
    for _ in range(8):
        if exists(key_for(run, forecast_hour, domain)):
            return run
        run -= dt.timedelta(hours=step)
    return None


def exists(key: str) -> bool:
    response = client().list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
    return any(o["Key"] == key for o in response.get("Contents", []))


def read_index(key: str) -> list[tuple[int, int, str, str]]:
    """(message number, byte offset, variable, level) per line of the .idx file."""
    rows = []
    for line in get_bytes(BUCKET, key + ".idx").decode().strip().split("\n"):
        number, offset, _, variable, level, *_ = line.split(":")
        rows.append((int(number), int(offset), variable, level))
    return rows


def byte_ranges(index, wanted: list[tuple[str, str]]) -> list[tuple[int, int | None]]:
    ranges = []
    for i, (_, offset, variable, level) in enumerate(index):
        if (variable, level) not in wanted:
            continue
        end = index[i + 1][1] - 1 if i + 1 < len(index) else None
        ranges.append((offset, end))
    return ranges


def download_subset(key: str, out_dir: str, fields=CLOUD_FIELDS) -> str:
    path = os.path.join(out_dir, os.path.basename(key).replace(".grib2", ".subset.grib2"))
    if os.path.exists(path):
        return path
    os.makedirs(out_dir, exist_ok=True)
    parts = [get_bytes(BUCKET, key, r) for r in byte_ranges(read_index(key), fields)]
    with open(path, "wb") as f:
        f.write(b"".join(parts))
    return path


class CloudGrid:
    """Cloud-layer fields from a subset file plus nearest-neighbour sampling. Heights are m MSL; base/top/ceiling are NaN where there is no (thick enough) cloud."""

    def __init__(self, path: str):
        _, lats, lons = read_first_message(path)
        messages = read_messages(path)
        self.fields = {name: messages[key].ravel() for name, key in FIELD_KEYS.items() if key in messages}
        self.points = np.c_[lats.ravel(), lons.ravel()]
        self.tree = cKDTree(self.points)
        self.spacing_deg = float(np.abs(np.diff(lons[lons.shape[0] // 2, :2])).max()) or 0.03

    def sample(self, name: str, lats, lons) -> np.ndarray:
        """Nearest grid value; NaN where the query point is off-grid (farther than ~2 cells from any grid point)."""
        distance, index = self.tree.query(np.c_[np.asarray(lats), np.asarray(lons)])
        values = self.fields[name][index].astype(float)
        values[distance > 2 * self.spacing_deg] = np.nan
        return values

    def disk_mean(self, name: str, lat: float, lon: float, radius_km: float) -> float:
        inside = distance_km(lat, lon, self.points[:, 0], self.points[:, 1]) < radius_km
        return float(np.nanmean(self.fields[name][inside]))


if __name__ == "__main__":
    now = dt.datetime.now(dt.UTC)
    run = latest_run(now, 1)
    key = key_for(run, 1)
    path = download_subset(key, "/tmp/hrrr")
    grid = CloudGrid(path)
    print(key, "->", path, os.path.getsize(path) / 1e6, "MB; fields:", list(grid.fields))
    print("Lamar CO hcc/mcc/lcc:", [grid.sample(n, [38.077], [-102.696])[0] for n in ("hcc", "mcc", "lcc")])
