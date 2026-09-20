"""Sunset/sunrise quality classifier following the Sunsethue whitepaper (sunsethue.com/whitepaper).

Two phases, both vectorised on a polar grid of cloud columns around the observer:

1. Reflection potential: from every sampled cloud column a fan of sun rays (3 azimuths around the
   solar azimuth x 5 sun depressions) is walked sunward; the fraction of rays that exit the
   troposphere unblocked is the column's hypothetical reflection potential.
2. Quality score: view rays leave the observer on an azimuth fan (solar azimuth +/-40 deg) and a
   range of elevations; each accumulates the reflection potential of the cloud cells it meets,
   attenuated by the ray's cumulative cloud cover (a saturated ray sees nothing beyond).

Post-processing, per the whitepaper: the base score is reduced by surface relative humidity (haze =
low visibility) and slightly skewed by golden-hour duration (long high-latitude sunsets score higher).

Keeps sunset_rays.score_site untouched (its output is pinned by the Lamar regression check); this
module's `quality` is on a similar scale (Lamar 5/5 ~ 0.4-0.6) so `min(1, quality/0.5)` still works.
"""
import numpy as np

from common.geo import point_along
from weather.hrrr import CloudGrid
from weather.sunset_rays import EARTH_RADIUS_KM, LAYERS, OPACITY, TROPOPAUSE_KM

AZIMUTH_OFFSETS_DEG = np.arange(-40.0, 40.1, 10.0)          # view fan, 9 directions
DISTANCES_KM = np.arange(5.0, 601.0, 5.0)
SUN_DEPRESSIONS_DEG = np.array([0.5, 1.5, 2.5, 3.5, 4.5])
SUN_AZIMUTH_OFFSETS_DEG = np.array([-10.0, 0.0, 10.0])
VIEW_ELEVATIONS_DEG = np.arange(1.0, 30.1, 1.5)
CLOUD_COHERENCE_KM = 20.0
ENVELOPE_MARGIN_KM = 0.15
MIN_TRANSMISSION = 1e-3

STEP_KM = float(DISTANCES_KM[1] - DISTANCES_KM[0])
N_AZ = len(AZIMUTH_OFFSETS_DEG)
N_DIST = len(DISTANCES_KM)
LAYER_NAMES = [name for name, *_ in LAYERS]
LAYER_BOUNDS = [(b, t) for _, b, t, _ in LAYERS]
LAYER_WEIGHTS = np.array([w for *_, w in LAYERS])
OPACITY_VEC = np.array([OPACITY[n] for n in LAYER_NAMES])
BAND_EDGES_KM = np.array([t for _, _, t, _ in LAYERS])  # 3.5, 8, 13.5


def hit_prob(cover: np.ndarray) -> np.ndarray:
    """Probability a ray meets cloud in one STEP_KM column given the column's cover fraction."""
    return 1.0 - (1.0 - np.clip(cover, 0, 1)) ** (STEP_KM / CLOUD_COHERENCE_KM)


def polar_profile(grid: CloudGrid, lat: float, lon: float, sun_azimuth: float) -> dict[str, np.ndarray]:
    """Cloud columns on an (azimuth, distance) polar grid centred on the site."""
    azimuths = (sun_azimuth + AZIMUTH_OFFSETS_DEG) % 360.0
    a, d = np.meshgrid(azimuths, DISTANCES_KM, indexing="ij")
    lats, lons = point_along(lat, lon, a.ravel(), d.ravel())
    profile = {"azimuths_deg": azimuths, "min_azimuth_deg": float((sun_azimuth - 40.0) % 360.0)}
    profile["cover"] = np.stack(
        [np.clip(np.nan_to_num(grid.sample(n, lats, lons)) / 100.0, 0, 1).reshape(N_AZ, N_DIST) for n in LAYER_NAMES],
        axis=-1,
    )
    for name in ("base", "top", "ceil", "orog"):
        profile[name] = (grid.sample(name, lats, lons) / 1000.0).reshape(N_AZ, N_DIST)
    profile["orog"] = np.nan_to_num(profile["orog"], nan=float(np.nanmean(profile["orog"])))
    return profile


def envelopes(profile) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(A, R, 3) cloud bottom/top per layer; `low_opaque` uses the HRRR ceiling for the sun-lit underside."""
    low = np.empty((N_AZ, N_DIST, 3))
    high = np.empty_like(low)
    low_opaque = np.empty_like(low)
    for li, (band_lo, band_hi) in enumerate(LAYER_BOUNDS):
        base, top, ceil = profile["base"], profile["top"], profile["ceil"]
        low[..., li] = np.where((base >= band_lo) & (base < band_hi), base, band_lo)
        low_opaque[..., li] = np.where((ceil >= band_lo) & (ceil < band_hi), ceil, low[..., li])
        high[..., li] = np.where((top > band_lo) & (top <= band_hi), top, band_hi)
    return low, high, low_opaque


def column_index(xs_km: np.ndarray, ys_km: np.ndarray, min_azimuth_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Site-centric (x east, y north) km -> polar (azimuth index, distance index, in-fan mask)."""
    r = np.hypot(xs_km, ys_km)
    ri = np.rint(r / STEP_KM).astype(int) - 1
    az_rel = (np.degrees(np.arctan2(xs_km, ys_km)) - min_azimuth_deg) % 360.0
    ai = np.rint(az_rel / 10.0).astype(int)
    ok = (az_rel <= 80.5) & (ai >= 0) & (ai < N_AZ) & (ri >= 0) & (ri < N_DIST)
    return np.clip(ai, 0, N_AZ - 1), np.clip(ri, 0, N_DIST - 1), ok


def cell_cover_at(cover: np.ndarray, low: np.ndarray, high: np.ndarray, ci, h) -> np.ndarray:
    """Cover of the layer whose band contains h in flat column `ci`; 0 when h is outside the cell's envelope.

    cover/low/high are flat (n_cells, 3) arrays; ci and h are same-shaped integer/float arrays of any shape.
    """
    m = np.searchsorted(BAND_EDGES_KM, h)
    ci_c, m_c = np.clip(ci, 0, cover.shape[0] - 1).ravel(), np.clip(m, 0, 2).ravel()
    hf = h.ravel()
    in_env = (m.ravel() < 3) & (hf >= low[ci_c, m_c] - ENVELOPE_MARGIN_KM) & (hf <= high[ci_c, m_c] + ENVELOPE_MARGIN_KM)
    return np.where(in_env, cover[ci_c, m_c], 0.0).reshape(h.shape)


def reflection_potential(profile, low_opaque, high) -> np.ndarray:
    """(A, R, 3): fraction of sun rays reaching each cell's cloud underside, times layer weight.

    All (depression x sun-azimuth) rays are walked together along a walk axis W, so the per-step
    numpy ops run once for the whole fan.
    """
    cells = N_AZ * N_DIST
    azimuths_rad = np.radians(profile["azimuths_deg"])[:, None]
    cx = (DISTANCES_KM[None, :] * np.sin(azimuths_rad)).ravel()          # site-centric km east
    cy = (DISTANCES_KM[None, :] * np.cos(azimuths_rad)).ravel()          # site-centric km north
    orog = profile["orog"].ravel()
    r2_origin = cx**2 + cy**2
    dirs = np.radians(profile["min_azimuth_deg"] + 40.0 + SUN_AZIMUTH_OFFSETS_DEG)  # solar azimuth +/- offsets
    slopes = np.tan(np.radians(SUN_DEPRESSIONS_DEG))
    ux = np.tile(np.sin(dirs), slopes.size)                              # (W,) per ray
    uy = np.tile(np.cos(dirs), slopes.size)
    slope = np.repeat(slopes, dirs.size)
    n_walks = ux.size
    cover_flat = profile["cover"].reshape(cells, 3)
    block_cover = cover_flat * OPACITY_VEC
    low_flat = low_opaque.reshape(cells, 3)
    high_flat = high.reshape(cells, 3)
    transmission = np.ones((cells, 3, n_walks))
    alive = np.broadcast_to(cover_flat[:, :, None] > 0.02, transmission.shape).copy()
    height = np.broadcast_to(low_flat[:, :, None], transmission.shape)
    for t in DISTANCES_KM:
        xt = np.broadcast_to(cx[:, None, None] + t * ux, transmission.shape)
        yt = np.broadcast_to(cy[:, None, None] + t * uy, transmission.shape)
        ai, ri, in_fan = column_index(xt, yt, profile["min_azimuth_deg"])
        h = height + (xt**2 + yt**2 - r2_origin[:, None, None]) / (2 * EARTH_RADIUS_KM) - slope * t
        exited = h >= TROPOPAUSE_KM
        grounded = h <= orog[:, None, None]
        reached_end = (t == DISTANCES_KM[-1]) | ~in_fan
        block = cell_cover_at(block_cover, low_flat, high_flat, ai * N_DIST + ri, h)
        transmission *= 1.0 - hit_prob(np.where(alive & in_fan & ~exited, block, 0.0))
        transmission[grounded] = 0.0
        alive &= ~exited & ~grounded & ~reached_end & (transmission > MIN_TRANSMISSION)
        if not alive.any():
            break
    lit = (transmission > MIN_TRANSMISSION).mean(axis=-1)
    return lit.reshape(N_AZ, N_DIST, 3) * LAYER_WEIGHTS


def view_fan_score(profile, potential, low, high) -> tuple[np.ndarray, np.ndarray]:
    """(A, E) quality per view ray: accumulated reflection potential attenuated by cumulative cover."""
    ground = float(profile["orog"][0, 0])
    transmission = np.ones((N_AZ, len(VIEW_ELEVATIONS_DEG)))
    quality = np.zeros_like(transmission)
    tan_e = np.tan(np.radians(VIEW_ELEVATIONS_DEG))[None, :]
    for ri, r in enumerate(DISTANCES_KM):
        h = ground + r * tan_e + r * r / (2 * EARTH_RADIUS_KM)  # (A, E)
        ai = np.arange(N_AZ)[:, None]
        m = np.searchsorted(BAND_EDGES_KM, h)
        over_top = m >= 3
        m_c = np.clip(m, 0, 2)
        lo, hi = low[ai, ri, m_c], high[ai, ri, m_c]
        in_env = ~over_top & (h >= lo - ENVELOPE_MARGIN_KM) & (h <= hi + ENVELOPE_MARGIN_KM)
        hit = hit_prob(profile["cover"][ai, ri, m_c]) * in_env
        quality += transmission * hit * potential[ai, ri, m_c]
        transmission *= 1.0 - hit
        if (transmission < MIN_TRANSMISSION).all():
            break
    return quality, transmission


def horizon_clouds(profile, low, high) -> float:
    """Whitepaper 'horizon clouds' metric: mean cover on low view rays (0.5-1.5 deg) within 150 km."""
    ground = float(profile["orog"][0, 0])
    blocked = []
    for elevation_deg in (0.5, 1.0, 1.5):
        transmission = np.ones(N_AZ)
        for ri, r in enumerate(DISTANCES_KM):
            if r > 150:
                break
            h = ground + r * np.tan(np.radians(elevation_deg)) + r * r / (2 * EARTH_RADIUS_KM)
            ai = np.arange(N_AZ)
            m = np.clip(np.searchsorted(BAND_EDGES_KM, h), 0, 2)
            in_env = (h >= low[ai, ri, m] - ENVELOPE_MARGIN_KM) & (h <= high[ai, ri, m] + ENVELOPE_MARGIN_KM)
            transmission *= 1.0 - hit_prob(profile["cover"][ai, ri, m]) * in_env
        blocked.append(1.0 - transmission[3:6].mean())  # central +/-10 deg around the solar azimuth
    return float(np.mean(blocked))


def humidity_factor(rh_percent: float | None) -> float:
    """High surface humidity -> haze -> slightly lower quality (whitepaper post-processing)."""
    if rh_percent is None or np.isnan(rh_percent):
        return 1.0
    return float(1.0 - 0.25 * (np.clip(rh_percent, 0, 100) / 100.0) ** 2)


def duration_factor(golden_minutes: float | None) -> float:
    """Longer sunsets (high latitude / solstice) score slightly higher; 30 min is the reference."""
    if golden_minutes is None or golden_minutes <= 0:
        return 1.0
    return float(np.clip(np.sqrt(golden_minutes / 30.0), 0.85, 1.15))


def score_site(grid: CloudGrid, lat: float, lon: float, sun_azimuth: float,
               rh_percent: float | None = None, golden_minutes: float | None = None) -> dict:
    profile = polar_profile(grid, lat, lon, sun_azimuth)
    low, high, low_opaque = envelopes(profile)
    potential = reflection_potential(profile, low_opaque, high)
    ray_quality, _ = view_fan_score(profile, potential, low, high)
    weights = 1.0 / (1.0 + VIEW_ELEVATIONS_DEG / 15.0)
    per_azimuth = ray_quality @ weights / weights.sum()
    base = float(per_azimuth @ np.ones(N_AZ) / N_AZ)
    best_az = int(np.argmax(per_azimuth))
    quality = base * humidity_factor(rh_percent) * duration_factor(golden_minutes)
    result = {
        "quality": round(quality, 3),
        "quality_raw": round(base, 3),
        "best_azimuth_deg": round(float(profile["azimuths_deg"][best_az]), 1),
        "horizon_block": round(horizon_clouds(profile, low, high), 2),
    }
    near = DISTANCES_KM <= 30
    for li, name in enumerate(LAYER_NAMES):
        result[f"site_{name}"] = round(float(profile["cover"][:, near, li].mean() * 100))
    for name in ("base", "ceil", "top"):
        value = float(grid.sample(name, [lat], [lon])[0]) / 1000.0
        result[f"site_{name}_km"] = None if np.isnan(value) else round(value, 1)
    if rh_percent is not None and not np.isnan(rh_percent):
        result["site_rh"] = round(float(rh_percent))
    if golden_minutes:
        result["golden_minutes"] = round(golden_minutes)
    return result
