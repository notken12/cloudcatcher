"""GOES ABI L2 products and the geostationary fixed grid (shared with Himawari AHI, which uses the same projection).
CONUS band 13 (10.3 um) is 3.3 MB / 5 min / ~3 min lag; ACHAC cloud-top height is 0.3 MB at 10 km."""
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
    """Maps lat/lon to (row, col) of a normalized geostationary projection (GOES-R PUG 5.1.2.8): `x`/`y` are the
    column/row scan angles in radians, `height` the satellite height above the ellipsoid in metres. GOES scans
    with the x sweep axis, Himawari and Meteosat with y (proj's `sweep`), which swaps the two angle formulas."""

    def __init__(self, height: float, lon0_deg: float, semi_major: float, semi_minor: float, x: np.ndarray, y: np.ndarray,
                 sweep: str = "x"):
        self.h = height
        self.lon0 = np.radians(lon0_deg)
        self.re = semi_major
        self.rp = semi_minor
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self.dx = float(self.x[1] - self.x[0])
        self.dy = float(self.y[1] - self.y[0])
        self.sweep = sweep

    @classmethod
    def from_dataset(cls, ds: netCDF4.Dataset) -> "FixedGrid":
        proj = ds.variables["goes_imager_projection"]
        return cls(proj.perspective_point_height, proj.longitude_of_projection_origin, proj.semi_major_axis, proj.semi_minor_axis,
                   ds.variables["x"][:], ds.variables["y"][:], proj.sweep_angle_axis)

    def indices(self, lats_deg, lons_deg) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(row, col, inside) per point; row/col are clipped to the grid, `inside` is False off the scan or behind the limb."""
        lat, lon = np.radians(np.asarray(lats_deg, dtype=float)), np.radians(np.asarray(lons_deg, dtype=float))
        e2 = 1 - (self.rp / self.re) ** 2
        phi_c = np.arctan((self.rp / self.re) ** 2 * np.tan(lat))
        r_c = self.rp / np.sqrt(1 - e2 * np.cos(phi_c) ** 2)
        sx = self.h + self.re - r_c * np.cos(phi_c) * np.cos(lon - self.lon0)
        sy = -r_c * np.cos(phi_c) * np.sin(lon - self.lon0)
        sz = r_c * np.sin(phi_c)
        if self.sweep == "x":
            scan_y = np.arctan(sz / sx)
            scan_x = np.arcsin(-sy / np.sqrt(sx**2 + sy**2 + sz**2))
        else:
            scan_x = np.arctan(-sy / sx)
            scan_y = np.arcsin(sz / np.sqrt(sx**2 + sy**2 + sz**2))
        visible = np.cos(phi_c) * np.cos(lon - self.lon0) > self.re / (self.h + self.re)  # this side of the limb
        col = np.rint(np.nan_to_num((scan_x - self.x[0]) / self.dx)).astype(int)
        row = np.rint(np.nan_to_num((scan_y - self.y[0]) / self.dy)).astype(int)
        inside = visible & (col >= 0) & (col < self.x.size) & (row >= 0) & (row < self.y.size)
        return np.clip(row, 0, self.y.size - 1), np.clip(col, 0, self.x.size - 1), inside

    def index(self, lat_deg: float, lon_deg: float) -> tuple[int, int]:
        row, col, _ = self.indices([lat_deg], [lon_deg])
        return int(row[0]), int(col[0])


def window(ds: netCDF4.Dataset, variable: str, lat: float, lon: float, half: int) -> np.ndarray:
    """(2*half+1)^2 pixel window of `variable` centred on lat/lon, masked→NaN."""
    i, j = FixedGrid.from_dataset(ds).index(lat, lon)
    values = ds.variables[variable][max(i - half, 0):i + half + 1, max(j - half, 0):j + half + 1]
    return np.ma.filled(values.astype(float), np.nan)


if __name__ == "__main__":
    now = dt.datetime.now(dt.UTC)
    bucket = east_bucket(now)
    obj = latest_product("ABI-L2-CMIPC", now, bucket, "-M6C13")
    assert obj is not None, "no band-13 CONUS scan this hour"
    ds = open_dataset(bucket, obj["Key"])
    print(obj["Key"].split("/")[-1], "scan", file_start(obj["Key"]), "min BT near Phoenix:", np.nanmin(window(ds, "CMI", 33.45, -112.07, 3)))
