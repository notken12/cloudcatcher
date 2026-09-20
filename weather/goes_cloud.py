"""GOES-18/19 CONUS cloud products (ACHA2KM cloud-top height + COD, both 2 km, every 5 min) as a
satellite_cloud.SatelliteCloudField. GOES-18 looks along the western sunset horizon; either satellite covers
all of CONUS."""
import datetime as dt

import netCDF4
import numpy as np

from weather.cloud_grid import CloudGrid
from weather.glm import file_start
from weather.goes_abi import FixedGrid, open_dataset
from weather.s3 import list_keys
from weather.satellite_cloud import SatelliteCloudField

BUCKETS = {"west": "noaa-goes18", "east": "noaa-goes19"}
PRODUCTS = {"HT": "ABI-L2-ACHA2KMC", "COD": "ABI-L2-CODC"}


def bucket_for(lon: float) -> str:
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


def load_field(when: dt.datetime, bucket: str, terrain: CloudGrid) -> SatelliteCloudField | None:
    """The newest complete scan at or before `when`, or None when the bucket has none in the last two hours."""
    keys = scan_keys(when, bucket)
    if keys is None:
        return None
    ht, cod = open_dataset(bucket, keys["HT"]), open_dataset(bucket, keys["COD"])
    return SatelliteCloudField(FixedGrid.from_dataset(ht), (0, 0), accepted(ht, "HT") / 1000.0, accepted(cod, "COD"), terrain, file_start(keys["HT"]))


if __name__ == "__main__":
    from weather.grib_subset import download_subset
    from weather.hrrr import BUCKET, CLOUD_FIELDS, key_for, latest_run

    now = dt.datetime.now(dt.UTC)
    run = latest_run(now, 1)
    assert run is not None, "no HRRR run uploaded in the last 8 hours"
    field = load_field(now, BUCKETS["west"], CloudGrid(download_subset(BUCKET, key_for(run, 1), "/tmp/hrrr", CLOUD_FIELDS)))
    assert field is not None, "no complete GOES-18 scan in the last two hours"
    columns = field.columns([38.77], [-123.53])
    print(field.scanned_at, "Coast Life Support cover/top by layer:", columns.cover[0].round(2), columns.high[0].round(1))
