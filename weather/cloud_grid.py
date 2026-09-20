"""Model cloud fields on a lat/lon grid (HRRR, GFS): nearest-neighbour sampling and the layer-cover
implementation of the CloudField seam."""
import numpy as np
from scipy.spatial import KDTree

from weather.cloud_columns import LAYER_BOUNDS, LAYER_NAMES, OPACITY_VEC, CloudColumns
from weather.grib import read_first_message, read_messages

FIELD_KEYS = {
    "lcc": ("lcc", "lowCloudLayer"),
    "mcc": ("mcc", "middleCloudLayer"),
    "hcc": ("hcc", "highCloudLayer"),
    "tcc": ("tcc", "atmosphere"),
    "ceil": ("gh", "cloudCeiling"),
    "base": ("gh", "cloudBase"),
    "top": ("gh", "cloudTop"),
    "orog": ("orog", "surface"),
    "land": ("lsm", "surface"),
    "blh": ("blh", "surface"),
    "rh": ("2r", "heightAboveGround"),
    "refc": ("refc", "atmosphere"),
    "cape": ("cape", "surface"),
}


class CloudGrid:
    """Fields from a subset file plus nearest-neighbour sampling. Heights are m MSL; base/top/ceiling are NaN where there is no (thick enough) cloud."""

    def __init__(self, path: str):
        _, lats, lons = read_first_message(path)
        messages = read_messages(path)
        self.fields = {name: messages[key].ravel() for name, key in FIELD_KEYS.items() if key in messages}
        self.points = np.c_[lats.ravel(), lons.ravel()]
        self.tree = KDTree(self.points)
        self.spacing_deg = float(np.abs(np.diff(lons[lons.shape[0] // 2, :2])).max()) or 0.03

    def sample(self, name: str, lats, lons) -> np.ndarray:
        """Nearest grid value; NaN where the query point is off-grid (farther than ~2 cells from any grid point)."""
        lons = (np.asarray(lons, dtype=float) + 180.0) % 360.0 - 180.0
        distance, index = self.tree.query(np.c_[np.asarray(lats, dtype=float), lons])
        values = self.fields[name][index].astype(float)
        values[distance > 2 * self.spacing_deg] = np.nan
        return values

    def optional(self, name: str, lats, lons) -> np.ndarray:
        """Like sample, but all-NaN when the file has no such field (GFS carries no cloud base/top)."""
        if name not in self.fields:
            return np.full(np.asarray(lats).shape, np.nan)
        return self.sample(name, lats, lons)

    def disk_mean(self, name: str, lat: float, lon: float, radius_km: float) -> float:
        inside = distance_km(lat, lon, self.points[:, 0], self.points[:, 1]) < radius_km
        return float(np.nanmean(self.fields[name][inside]))


def distance_km(lat1, lon1, lat2, lon2):
    """Equirectangular distance; accurate to ~1% within a few hundred km. Accepts scalars or numpy arrays."""
    mean_lat = np.radians((np.asarray(lat1) + np.asarray(lat2)) / 2)
    dlat = np.asarray(lat2) - np.asarray(lat1)
    dlon = ((np.asarray(lon2) - np.asarray(lon1) + 180) % 360 - 180) * np.cos(mean_lat)
    return np.hypot(dlat, dlon) * 111.0


def terrain_km(grid: CloudGrid, lats, lons) -> np.ndarray:
    """Surface height in km; off the grid (ocean, for a sunward fan from a CONUS site) is sea level."""
    return np.nan_to_num(grid.sample("orog", lats, lons) / 1000.0, nan=0.0)


class LayerCloudField:
    """Cloud columns from a model's layer cover (HRRR, GFS). Each band's cloud fills the band, narrowed by
    cloud base/top when the model reports them inside it; the sun-lit underside is the cloud ceiling
    (the deck, not the lowest scrap of cloud). GFS has no base/top, so its clouds fill their bands."""

    def __init__(self, grid: CloudGrid):
        self.grid = grid

    def columns(self, lats, lons) -> CloudColumns:
        cover = np.stack(
            [np.clip(np.nan_to_num(self.grid.sample(name, lats, lons)) / 100.0, 0, 1) for name in LAYER_NAMES], axis=-1
        )
        base, top, ceil = (self.grid.optional(name, lats, lons) / 1000.0 for name in ("base", "top", "ceil"))
        low, high, underside = np.empty_like(cover), np.empty_like(cover), np.empty_like(cover)
        for li, (band_lo, band_hi) in enumerate(LAYER_BOUNDS):
            low[:, li] = np.where((base >= band_lo) & (base < band_hi), base, band_lo)
            underside[:, li] = np.where((ceil >= band_lo) & (ceil < band_hi), ceil, low[:, li])
            high[:, li] = np.where((top > band_lo) & (top <= band_hi), top, band_hi)
        opacity = np.broadcast_to(OPACITY_VEC, cover.shape)
        return CloudColumns(cover, opacity, low, high, underside, terrain_km(self.grid, lats, lons))
