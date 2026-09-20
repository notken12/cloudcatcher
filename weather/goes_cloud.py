"""GOES-18/19 cloud products as the sunset model's cloud field.

ACHA2KM cloud-top height (2 km, day and night; NaN where the cloud mask is clear) places each pixel's cloud in
a height band, and COD (cloud optical depth, 2 km; the night algorithm runs at twilight) gives its opacity and
geometric depth. HRRR under-reports the thin cirrus that makes sunsets; the satellite sees it directly. Terrain
(and, in events.py, humidity) still come from HRRR.

Per 10 km cell each band gets the fraction of cloudy pixels reaching into it and the mean top/base/opacity of
those pixels, so the columns have the same shape as hrrr.HrrrCloudField's.
"""
import datetime as dt

import netCDF4
import numpy as np
from scipy.ndimage import uniform_filter

from weather.cloud_columns import LAYER_BOUNDS, LAYER_NAMES, OPACITY, RAY_STEP_KM, CloudColumns
from weather.glm import file_start
from weather.goes_abi import FixedGrid, open_dataset
from weather.hrrr import CloudGrid, terrain_km
from weather.s3 import list_keys

BUCKETS = {"west": "noaa-goes18", "east": "noaa-goes19"}
PRODUCTS = {"HT": "ABI-L2-ACHA2KMC", "COD": "ABI-L2-CODC"}
CELL_PX = 5                   # 10 km box on the 2 km grid: the ray models' column size
DEPTH_KM = (0.5, 4.0)         # geometric depth sqrt(COD), clipped: thin cirrus 0.5 km, deep decks 4 km
DEPTH_KM_WITHOUT_COD = 1.0


def bucket_for(lon: float) -> str:
    """GOES-18 looks along the western sunset horizon; either satellite covers all of CONUS."""
    return BUCKETS["west"] if lon < -103 else BUCKETS["east"]


def scan_keys(when: dt.datetime, bucket: str) -> dict[str, str] | None:
    """{HT, COD} keys of the newest CONUS scan started at or before `when` with both products; None if none in the last two hours."""
    by_start = {}
    for variable, product in PRODUCTS.items():
        keys = [o["Key"] for hour in (when, when - dt.timedelta(hours=1)) for o in list_keys(bucket, f"{product}/{hour:%Y/%j/%H}/")]
        by_start[variable] = {file_start(key): key for key in keys if file_start(key) <= when}
    complete = set(by_start["HT"]) & set(by_start["COD"])
    if not complete:
        return None
    newest = max(complete)
    return {variable: by_start[variable][newest] for variable in PRODUCTS}


def accepted(ds: netCDF4.Dataset, variable: str) -> np.ndarray:
    """The product field with fill and rejected-quality pixels as NaN. DQF semantics differ per product: COD's is a
    bitmask (bit0 day algorithm ran, bit1 night ran; either is accepted since sunset is twilight and thin cirrus
    trips the degraded bits); ACHA's is an enum (0 good, 1 marginal, 2 attempted, 3 bad) and HT NaN means clear."""
    values = np.ma.filled(ds.variables[variable][:].astype(np.float32), np.nan)
    dqf = np.asarray(ds.variables["DQF"][:])
    ok = (dqf & 3) != 0 if variable == "COD" else dqf <= 2
    return np.where(ok, values, np.nan)


def cell_mean(values: np.ndarray, member: np.ndarray) -> np.ndarray:
    """Mean of `values` over the `member` pixels of each CELL_PX box; NaN where a box has none."""
    count = uniform_filter(member.astype(np.float32), CELL_PX)
    total = uniform_filter(np.where(member, values, 0.0).astype(np.float32), CELL_PX)
    return np.where(count > 0, total / np.where(count > 0, count, 1.0), np.nan)


class GoesCloudField:
    def __init__(self, ht: netCDF4.Dataset, cod: netCDF4.Dataset, terrain: CloudGrid, scanned_at: dt.datetime):
        self.grid = FixedGrid(ht)
        self.terrain = terrain
        self.scanned_at = scanned_at
        top = accepted(ht, "HT") / 1000.0
        optical_depth = accepted(cod, "COD")
        cloudy = np.isfinite(top)
        depth = np.clip(np.sqrt(np.where(np.isfinite(optical_depth), optical_depth, DEPTH_KM_WITHOUT_COD**2)), *DEPTH_KM)
        base = top - depth
        opacity = 1.0 - np.exp(-optical_depth * RAY_STEP_KM / depth)  # a grazing sun ray crosses RAY_STEP_KM of the slab per step
        shape = (len(LAYER_BOUNDS), *top.shape)
        self.cover, self.low, self.high, self.opacity = (np.empty(shape, np.float32) for _ in range(4))
        for li, (band_lo, band_hi) in enumerate(LAYER_BOUNDS):
            member = cloudy & (base < band_hi) & (top > band_lo)
            default_opacity = OPACITY[LAYER_NAMES[li]]
            self.cover[li] = uniform_filter(member.astype(np.float32), CELL_PX)
            self.low[li] = np.nan_to_num(cell_mean(np.maximum(base, band_lo), member), nan=band_lo)
            self.high[li] = np.nan_to_num(cell_mean(np.minimum(top, band_hi), member), nan=band_hi)
            self.opacity[li] = np.nan_to_num(cell_mean(np.where(np.isfinite(opacity), opacity, default_opacity), member), nan=default_opacity)

    def covers(self, lat: float, lon: float) -> bool:
        return bool(self.grid.indices([lat], [lon])[2][0])

    def columns(self, lats, lons) -> CloudColumns:
        row, col, inside = self.grid.indices(lats, lons)
        cover = np.where(inside[:, None], self.cover[:, row, col].T, 0.0)
        low, high, opacity = (a[:, row, col].T.astype(float) for a in (self.low, self.high, self.opacity))
        return CloudColumns(cover, opacity, low, high, low, terrain_km(self.terrain, lats, lons))


def load_field(when: dt.datetime, bucket: str, terrain: CloudGrid) -> GoesCloudField | None:
    """The newest complete scan at or before `when`, or None when the bucket has none in the last two hours."""
    keys = scan_keys(when, bucket)
    if keys is None:
        return None
    return GoesCloudField(open_dataset(bucket, keys["HT"]), open_dataset(bucket, keys["COD"]), terrain, file_start(keys["HT"]))


if __name__ == "__main__":
    from weather.hrrr import download_subset, key_for, latest_run

    now = dt.datetime.now(dt.UTC)
    run = latest_run(now, 1)
    assert run is not None, "no HRRR run uploaded in the last 8 hours"
    field = load_field(now, BUCKETS["west"], CloudGrid(download_subset(key_for(run, 1), "/tmp/hrrr")))
    assert field is not None, "no complete GOES-18 scan in the last two hours"
    columns = field.columns([38.77], [-123.53])
    print(field.scanned_at, "Coast Life Support cover/top by layer:", columns.cover[0].round(2), columns.high[0].round(1))
