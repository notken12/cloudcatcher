"""Geostationary cloud retrievals (GOES ABI, Himawari AHI) as the sunset model's cloud field.

Cloud-top height (2 km, day and night; NaN where the cloud mask is clear) places each pixel's cloud in a height
band, and cloud optical depth gives its opacity and geometric depth. Models under-report the thin cirrus that
makes sunsets; the satellite sees it directly. Terrain (and, in events.py, humidity) come from a model grid.

Around every pixel a CELL_PX box (10 km) gives each band the fraction of cloudy pixels reaching into it and the
mean top/base/opacity of those pixels, so the columns have the same shape as cloud_grid.LayerCloudField's.
The field may cover a window of the satellite grid (Himawari full disks are 5500 x 5500; only the region that
matters is read). Memory is 48 bytes per pixel: ~180 MB for a GOES CONUS scan.
"""
import datetime as dt

import numpy as np
from scipy.ndimage import uniform_filter

from weather.cloud_columns import LAYER_BOUNDS, LAYER_NAMES, OPACITY, RAY_STEP_KM, CloudColumns
from weather.cloud_grid import CloudGrid, terrain_km
from weather.goes_abi import FixedGrid

CELL_PX = 5                   # 10 km box on the 2 km grid: the ray models' column size
DEPTH_KM = (0.5, 4.0)         # geometric depth sqrt(COD), clipped: thin cirrus 0.5 km, deep decks 4 km
DEPTH_KM_WITHOUT_COD = 1.0


def cell_mean(values: np.ndarray, member: np.ndarray) -> np.ndarray:
    """Mean of `values` over the `member` pixels of each CELL_PX box; NaN where a box has none."""
    count = uniform_filter(member.astype(np.float32), CELL_PX)
    total = uniform_filter(np.where(member, values, 0.0).astype(np.float32), CELL_PX)
    return np.where(count > 0, total / np.where(count > 0, count, 1.0), np.nan)


class SatelliteCloudField:
    def __init__(self, grid: FixedGrid, origin: tuple[int, int], top_km: np.ndarray, optical_depth: np.ndarray,
                 terrain: CloudGrid, scanned_at: dt.datetime):
        """`top_km`/`optical_depth` cover the grid window starting at `origin` (row, col); NaN top = clear."""
        self.grid = grid
        self.origin = origin
        self.shape = top_km.shape
        self.terrain = terrain
        self.scanned_at = scanned_at
        cloudy = np.isfinite(top_km)
        depth = np.clip(np.sqrt(np.where(np.isfinite(optical_depth), optical_depth, DEPTH_KM_WITHOUT_COD**2)), *DEPTH_KM)
        base = top_km - depth
        opacity = 1.0 - np.exp(-optical_depth * RAY_STEP_KM / depth)  # a grazing sun ray crosses RAY_STEP_KM of the slab per step
        shape = (len(LAYER_BOUNDS), *top_km.shape)
        self.cover, self.low, self.high, self.opacity = (np.empty(shape, np.float32) for _ in range(4))
        for li, (band_lo, band_hi) in enumerate(LAYER_BOUNDS):
            member = cloudy & (base < band_hi) & (top_km > band_lo)
            default_opacity = OPACITY[LAYER_NAMES[li]]
            self.cover[li] = uniform_filter(member.astype(np.float32), CELL_PX)
            self.low[li] = np.nan_to_num(cell_mean(np.maximum(base, band_lo), member), nan=band_lo)
            self.high[li] = np.nan_to_num(cell_mean(np.minimum(top_km, band_hi), member), nan=band_hi)
            self.opacity[li] = np.nan_to_num(cell_mean(np.where(np.isfinite(opacity), opacity, default_opacity), member), nan=default_opacity)

    def locate(self, lats, lons) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(window row, window col, inside the window) per point; the cell arrays are pixel-resolution box means."""
        row, col, inside = self.grid.indices(lats, lons)
        r, c = row - self.origin[0], col - self.origin[1]
        inside &= (r >= 0) & (r < self.shape[0]) & (c >= 0) & (c < self.shape[1])
        return np.clip(r, 0, self.shape[0] - 1), np.clip(c, 0, self.shape[1] - 1), inside

    def covers(self, lat: float, lon: float) -> bool:
        return bool(self.locate([lat], [lon])[2][0])

    def columns(self, lats, lons) -> CloudColumns:
        r, c, inside = self.locate(lats, lons)
        cover = np.where(inside[:, None], self.cover[:, r, c].T, 0.0)
        low, high, opacity = (a[:, r, c].T.astype(float) for a in (self.low, self.high, self.opacity))
        return CloudColumns(cover, opacity, low, high, low, terrain_km(self.terrain, lats, lons))
