"""Sunsethue-style 2D ray model on HRRR cloud layers.

View rays leave the observer toward the sun at elevations 1-45 deg. Wherever a ray meets cloud, a sun ray is traced from the
cloud's underside toward the sun (depression `delta`), curving with the Earth; the cloud counts if that sun ray is not blocked.
Validated on one evening only (rho ~ 0 against camera colour, see validation/REPORT.md) - HRRR's layer fields are the bottleneck."""
import numpy as np

from common.geo import point_along
from weather.hrrr import CloudGrid

EARTH_RADIUS_KM = 6371.0
TROPOPAUSE_KM = 13.5
STEP_KM = 3.0
CLOUD_COHERENCE_KM = 20.0
LAYERS = [("lcc", 0.0, 3.5, 0.35), ("mcc", 3.5, 8.0, 0.8), ("hcc", 8.0, 13.5, 1.0)]
OPACITY = {"lcc": 1.0, "mcc": 0.8, "hcc": 0.4}
DEPRESSIONS_DEG = (0, 1, 2, 3, 4)
VIEW_ELEVATIONS_DEG = np.arange(1, 46, 1.5)
HORIZON_ELEVATIONS_DEG = (0.5, 1.0, 1.5)
MAX_RANGE_KM = 700.0


def ray_profile(grid: CloudGrid, lat: float, lon: float, sun_azimuth: float) -> dict[str, np.ndarray]:
    """Cloud columns along the sun azimuth, one every STEP_KM. Heights in km MSL."""
    distances = np.arange(0, MAX_RANGE_KM + STEP_KM, STEP_KM)
    lats, lons = point_along(lat, lon, sun_azimuth, distances)
    profile = {"d": distances}
    for name in ("lcc", "mcc", "hcc"):
        profile[name] = np.clip(np.nan_to_num(grid.sample(name, lats, lons)) / 100.0, 0, 1)
    for name in ("base", "top", "ceil", "orog"):
        profile[name] = grid.sample(name, lats, lons) / 1000.0
    profile["orog"] = np.nan_to_num(profile["orog"], nan=float(np.nanmean(profile["orog"])))
    return profile


def layer_at(height_km: float):
    for name, bottom, top, weight in LAYERS:
        if bottom <= height_km < top:
            return name, weight
    return None, 0.0


def hit_probability(cover: float) -> float:
    return 1 - (1 - cover) ** (STEP_KM / CLOUD_COHERENCE_KM)


def envelope(profile, column: int, layer: str, opaque: bool) -> tuple[float, float]:
    """Vertical extent of `layer`'s cloud in one column: the layer band, narrowed by HRRR base/ceiling/top when they fall inside it."""
    bottom, top = next((b, t) for n, b, t, _ in LAYERS if n == layer)
    base = profile["ceil"][column] if opaque and bottom <= profile["ceil"][column] < top else profile["base"][column]
    low = base if bottom <= base < top else bottom
    cloud_top = profile["top"][column]
    high = cloud_top if bottom < cloud_top <= top else top
    return low, high


def cover_at(profile, column: int, height_km: float, opaque: bool = False) -> tuple[float, float]:
    layer, weight = layer_at(height_km)
    if not layer:
        return 0.0, 0.0
    low, high = envelope(profile, column, layer, opaque)
    if height_km < low - 0.15 or height_km > high + 0.15:
        return 0.0, weight
    return profile[layer][column] * (OPACITY[layer] if opaque else 1.0), weight


def underside(profile, column: int, height_km: float) -> float:
    layer, _ = layer_at(height_km)
    if not layer:
        return height_km
    low, _ = envelope(profile, column, layer, opaque=True)
    return max(low, min(height_km, low + 0.3)) if height_km >= low else height_km


def sun_transmission(profile, x0: float, h0: float, depression: float) -> float:
    """Fraction of sunlight reaching (x0, h0) with the sun `depression` rad below the horizon; the ray is walked toward the sun."""
    transmission, x = 1.0, x0
    last = len(profile["d"]) - 1
    while True:
        x += STEP_KM
        column = min(int(x / STEP_KM), last)
        height = h0 + (x * x - x0 * x0) / (2 * EARTH_RADIUS_KM) - depression * (x - x0)
        if height <= profile["orog"][column]:
            return 0.0
        if height >= TROPOPAUSE_KM or column >= last:
            return transmission
        cover, _ = cover_at(profile, column, height, opaque=True)
        if cover:
            transmission *= 1 - hit_probability(cover)
        if transmission < 1e-3:
            return 0.0


def view_ray_quality(profile, elevation_deg: float, depression: float) -> float:
    ground = profile["orog"][0]
    transmission, quality, x = 1.0, 0.0, 0.0
    last = len(profile["d"]) - 1
    while True:
        x += STEP_KM
        column = int(x / STEP_KM)
        height = ground + x * np.tan(np.radians(elevation_deg)) + x * x / (2 * EARTH_RADIUS_KM)
        if height >= TROPOPAUSE_KM or column >= last:
            return quality
        cover, weight = cover_at(profile, column, height)
        if not cover:
            continue
        p = hit_probability(cover)
        quality += transmission * p * weight * sun_transmission(profile, x, underside(profile, column, height), depression)
        transmission *= 1 - p
        if transmission < 1e-3:
            return quality


def horizon_block(profile) -> float:
    """How much cloud sits on the low rays toward the sun within 150 km (does the sun disk itself get through)."""
    ground = profile["orog"][0]
    blocked = []
    for elevation_deg in HORIZON_ELEVATIONS_DEG:
        transmission, x = 1.0, 0.0
        while x < 150:
            x += STEP_KM
            height = ground + x * np.tan(np.radians(elevation_deg)) + x * x / (2 * EARTH_RADIUS_KM)
            cover, _ = cover_at(profile, int(x / STEP_KM), height)
            if cover:
                transmission *= 1 - hit_probability(cover)
        blocked.append(1 - transmission)
    return float(np.mean(blocked))


def score(profile) -> dict:
    """Best quality over sun depressions, plus diagnostics. Low view elevations are weighted up (where sunset colour lives)."""
    weights = 1.0 / (1 + VIEW_ELEVATIONS_DEG / 15.0)
    best = {"quality": -1.0, "depression_deg": None}
    for depression_deg in DEPRESSIONS_DEG:
        depression = np.radians(depression_deg)
        qualities = [view_ray_quality(profile, e, depression) for e in VIEW_ELEVATIONS_DEG]
        quality = float(np.dot(weights, qualities) / weights.sum())
        if quality > best["quality"]:
            best = {"quality": round(quality, 3), "depression_deg": depression_deg}
    best["horizon_block"] = round(horizon_block(profile), 2)
    for name in ("lcc", "mcc", "hcc"):
        best[f"site_{name}"] = round(float(profile[name][:10].mean() * 100))
    for name in ("base", "ceil", "top"):
        value = profile[name][0]
        best[f"site_{name}_km"] = None if np.isnan(value) else round(float(value), 1)
    return best


def score_site(grid: CloudGrid, lat: float, lon: float, sun_azimuth: float) -> dict:
    return score(ray_profile(grid, lat, lon, sun_azimuth))
