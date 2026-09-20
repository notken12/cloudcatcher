"""GOES ABI L2 products. CONUS band 13 (10.3 um) is 3.3 MB / 5 min / ~3 min lag; ACHAC cloud-top height is 0.3 MB at 10 km."""
import datetime as dt

import netCDF4
import numpy as np

from weather.glm import east_bucket, file_start
from weather.s3 import get_bytes, list_keys


def latest_product(product: str, when: dt.datetime, bucket: str, band: str = "") -> dict | None:
    prefix = f"{product}/{when:%Y/%j/%H}/OR_{product}{band}"
    objects = list_keys(bucket, prefix)
    return objects[-1] if objects else None


def open_dataset(bucket: str, key: str) -> netCDF4.Dataset:
    return netCDF4.Dataset("inmem", memory=get_bytes(bucket, key))


class FixedGrid:
    """Maps lat/lon to (row, col) of an ABI fixed-grid dataset."""

    def __init__(self, ds: netCDF4.Dataset):
        proj = ds.variables["goes_imager_projection"]
        self.h = proj.perspective_point_height
        self.lon0 = np.radians(proj.longitude_of_projection_origin)
        self.re = proj.semi_major_axis
        self.rp = proj.semi_minor_axis
        self.x = ds.variables["x"][:]
        self.y = ds.variables["y"][:]

    def index(self, lat_deg: float, lon_deg: float) -> tuple[int, int]:
        lat, lon = np.radians(lat_deg), np.radians(lon_deg)
        e2 = 1 - (self.rp / self.re) ** 2
        phi_c = np.arctan((self.rp / self.re) ** 2 * np.tan(lat))
        r_c = self.rp / np.sqrt(1 - e2 * np.cos(phi_c) ** 2)
        sx = self.h + self.re - r_c * np.cos(phi_c) * np.cos(lon - self.lon0)
        sy = -r_c * np.cos(phi_c) * np.sin(lon - self.lon0)
        sz = r_c * np.sin(phi_c)
        scan_y = np.arctan(sz / sx)
        scan_x = np.arcsin(-sy / np.sqrt(sx**2 + sy**2 + sz**2))
        return int(np.abs(self.y - scan_y).argmin()), int(np.abs(self.x - scan_x).argmin())


def window(ds: netCDF4.Dataset, variable: str, lat: float, lon: float, half: int) -> np.ndarray:
    """(2*half+1)^2 pixel window of `variable` centred on lat/lon, masked→NaN."""
    i, j = FixedGrid(ds).index(lat, lon)
    values = ds.variables[variable][max(i - half, 0):i + half + 1, max(j - half, 0):j + half + 1]
    return np.ma.filled(values.astype(float), np.nan)


if __name__ == "__main__":
    now = dt.datetime.now(dt.UTC)
    bucket = east_bucket(now)
    obj = latest_product("ABI-L2-CMIPC", now, bucket, "-M6C13")
    ds = open_dataset(bucket, obj["Key"])
    print(obj["Key"].split("/")[-1], "scan", file_start(obj["Key"]), "min BT near Phoenix:", np.nanmin(window(ds, "CMI", 33.45, -112.07, 3)))
