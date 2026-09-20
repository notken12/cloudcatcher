"""Himawari-9 full-disk cloud products (NOAA enterprise ACHA: cloud-top height, optical depth and quality flag
in one AHI-CHGT file, 2 km, every 10 min, s3://noaa-himawari9) as a satellite_cloud.SatelliteCloudField over
Asia, Australia and the western Pacific.

The files are 765 MB uncompressed-size HDF5 with gzip 200 x 200 chunks and no projection metadata, so a
window is read through ranged S3 GETs with h5py (a 1000 x 5500 band costs ~30 MB) and the fixed grid is
built from the AHI constants (verified against the file's own Latitude/Longitude arrays to < 1 pixel)."""
import datetime as dt
import io

import h5py
import numpy as np

from weather.cloud_grid import CloudGrid
from weather.goes_abi import FixedGrid
from weather.s3 import client, get_bytes
from weather.satellite_cloud import SatelliteCloudField

BUCKET = "noaa-himawari9"
PREFIX = "AHI-L2-FLDK-Clouds"
FULL_DISK_PX = 5500
PIXEL_RAD = 5.588e-5           # 2 km at nadir: 65536 / CFAC 20466076 in degrees
SUB_LON_DEG = 140.7
HEIGHT_M = 35785863.0
SEMI_MAJOR_M, SEMI_MINOR_M = 6378137.0, 6356752.3
FILL = -999.0
BLOCK_BYTES = 4 << 20


def fixed_grid() -> FixedGrid:
    centre = (FULL_DISK_PX - 1) / 2
    scan = (np.arange(FULL_DISK_PX) - centre) * PIXEL_RAD
    return FixedGrid(HEIGHT_M, SUB_LON_DEG, SEMI_MAJOR_M, SEMI_MINOR_M, scan, -scan, sweep="y")


class S3File(io.RawIOBase):
    """Read-only file object over an S3 object, fetching BLOCK_BYTES blocks on demand (h5py reads only the chunks it needs)."""

    def __init__(self, bucket: str, key: str):
        self.bucket, self.key = bucket, key
        self.size = client().head_object(Bucket=bucket, Key=key)["ContentLength"]
        self.pos = 0
        self.blocks: dict[int, bytes] = {}

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def block(self, i: int) -> bytes:
        if i not in self.blocks:
            start = i * BLOCK_BYTES
            self.blocks[i] = get_bytes(self.bucket, self.key, (start, min(start + BLOCK_BYTES, self.size) - 1))
        return self.blocks[i]

    def readinto(self, buffer) -> int:
        n = min(len(buffer), self.size - self.pos)
        out = memoryview(buffer)
        done = 0
        while done < n:
            i, offset = divmod(self.pos, BLOCK_BYTES)
            chunk = self.block(i)[offset:offset + n - done]
            out[done:done + len(chunk)] = chunk
            done += len(chunk)
            self.pos += len(chunk)
        return done


def file_start(key: str) -> dt.datetime:
    stamp = key.split("_s")[1][:12]
    return dt.datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=dt.UTC)


def scan_key(when: dt.datetime) -> str | None:
    """The newest AHI-CHGT full-disk file started at or before `when` (today's and yesterday's folders); None if none."""
    keys = []
    for day in (when, when - dt.timedelta(days=1)):
        pages = client().get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=f"{PREFIX}/{day:%Y/%m/%d}/")
        keys += [o["Key"] for page in pages for o in page.get("Contents", []) if "AHI-CHGT_" in o["Key"]]
    usable = [key for key in keys if file_start(key) <= when]
    return max(usable, key=file_start) if usable else None


def window_for(grid: FixedGrid, lats, lons, margin_deg: float) -> tuple[int, int, int, int] | None:
    """(row0, row1, col0, col1) of the grid pixels around the on-disk points, padded by `margin_deg` of latitude
    worth of pixels (the classifier's sunward fan); None when no point is on the disk."""
    row, col, inside = grid.indices(lats, lons)
    if not inside.any():
        return None
    margin = int(np.ceil(np.radians(margin_deg) * SEMI_MAJOR_M / 2000.0))  # 2 km pixels
    rows, cols = row[inside], col[inside]
    return (max(int(rows.min()) - margin, 0), min(int(rows.max()) + margin + 1, FULL_DISK_PX),
            max(int(cols.min()) - margin, 0), min(int(cols.max()) + margin + 1, FULL_DISK_PX))


def read_window(h: h5py.File, name: str, window: tuple[int, int, int, int]) -> np.ndarray:
    dataset = h[name]
    assert isinstance(dataset, h5py.Dataset)
    row0, row1, col0, col1 = window
    return np.asarray(dataset[row0:row1, col0:col1], dtype=np.float32)


def load_field(when: dt.datetime, lats, lons, margin_deg: float, terrain: CloudGrid) -> SatelliteCloudField | None:
    """Cloud field around the given points from the newest full disk at or before `when`; None when there is no
    disk or none of the points is on it."""
    key = scan_key(when)
    grid = fixed_grid()
    window = window_for(grid, lats, lons, margin_deg)
    if key is None or window is None:
        return None
    row0, row1, col0, col1 = window
    with h5py.File(S3File(BUCKET, key), "r") as h:
        top, optical_depth, quality = (read_window(h, name, window) for name in ("CldTopHght", "CldOptDpth", "CloudHgtQF"))
    top = np.where((top != FILL) & (quality <= 2), top / 1000.0, np.nan)
    optical_depth = np.where(optical_depth != FILL, optical_depth, np.nan)
    return SatelliteCloudField(grid, (row0, col0), top, optical_depth, terrain, file_start(key))


if __name__ == "__main__":
    from weather.gfs import BUCKET as GFS_BUCKET, CLOUD_FIELDS, key_for, run_and_hours
    from weather.grib_subset import download_subset

    now = dt.datetime.now(dt.UTC)
    run_hours = run_and_hours(now, 1)
    assert run_hours is not None, "no GFS run with a step valid now"
    run, hours = run_hours
    terrain = CloudGrid(download_subset(GFS_BUCKET, key_for(run, hours[0]), "/tmp/gfs", CLOUD_FIELDS))
    field = load_field(now, [35.68], [139.69], 6.0, terrain)
    assert field is not None, "no Himawari-9 cloud file"
    columns = field.columns([35.68], [139.69])
    print(field.scanned_at, "Tokyo cover/top by layer:", columns.cover[0].round(2), columns.high[0].round(1))
