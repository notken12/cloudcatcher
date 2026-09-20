"""Vectorized great-circle + viewing-cone math (docs/preprocessing-plan.md §3).

Everything takes/returns numpy arrays so a whole catalog is tested at once.
"""

from __future__ import annotations

import numpy as np

R_EARTH_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R_EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, degrees clockwise from north in [0, 360)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlon = lon2 - lon1
    x = np.sin(dlon) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    return np.degrees(np.arctan2(x, y)) % 360


def wrap180(deg):
    return (np.asarray(deg, dtype=float) + 180) % 360 - 180


def apparent_elevation_deg(h_km, d_km, cam_alt_km=0.0):
    """Elevation angle of a feature at altitude `h_km` above ground, `d_km` away.

    atan((h - cam_alt)/d) minus the Earth-curvature depression d/(2R).
    """
    d = np.maximum(np.asarray(d_km, dtype=float), 1e-3)
    dh = np.asarray(h_km, dtype=float) - np.asarray(cam_alt_km, dtype=float)
    return np.degrees(np.arctan2(dh, d) - d / (2 * R_EARTH_KM))


def horizon_km(h_km):
    """Geometric distance at which a feature at altitude h_km drops below the horizon."""
    return 3.57 * np.sqrt(np.maximum(np.asarray(h_km, dtype=float), 0) * 1000.0)


def annulus_km(h_km, elev_min_deg, elev_max_deg, r_cap_km):
    """[d_min, d_max] ground distance over which a feature at altitude h is inside the frame."""
    emax = np.radians(np.clip(np.asarray(elev_max_deg, dtype=float), 0.1, 90))
    emin = np.radians(np.asarray(elev_min_deg, dtype=float))
    d_min = np.where(emax >= np.radians(89.9), 0.0, h_km / np.tan(emax))
    d_max = np.where(emin > 0, h_km / np.tan(np.maximum(emin, 1e-4)), horizon_km(h_km))
    return d_min, np.minimum(d_max, r_cap_km)


HEADING_TOLERANCE_DEG = {
    "catalog": 0.0,
    "text": 22.5,
    "inferred": 30.0,
    "ptz": None,
    "unknown": None,
}


def bearing_ok(bearing, azimuth, hfov_deg, heading_conf, d_km, event_radius_km):
    """Bearing test with FOV, angular size of the event and heading uncertainty.

    Rows with heading_conf ptz/unknown or NaN azimuth pass (the caller penalises them).
    """
    tol = np.array([HEADING_TOLERANCE_DEG.get(c) for c in heading_conf], dtype=object)
    skip = np.array([t is None for t in tol]) | np.isnan(np.asarray(azimuth, dtype=float))
    tol_f = np.where(skip, 0.0, tol).astype(float)
    half = np.asarray(hfov_deg, dtype=float) / 2
    ang_r = np.degrees(np.arctan2(event_radius_km, np.maximum(d_km, 1e-3)))
    delta = np.abs(wrap180(bearing - np.nan_to_num(azimuth)))
    return skip | (half >= 180) | (delta <= half + ang_r + tol_f)


def elevation_ok(h_km, d_km, elev_min_deg, elev_max_deg, cam_alt_km=0.0, slack_deg=2.0):
    alpha = apparent_elevation_deg(h_km, d_km, cam_alt_km)
    return (alpha >= elev_min_deg - slack_deg) & (alpha <= elev_max_deg + slack_deg)


def sector_polygon(lat, lon, azimuth_deg, hfov_deg, d_min_km, d_max_km, n=24):
    """(lon, lat) ring of the annular sector, for map display. Pure numpy, no shapely."""
    if hfov_deg >= 360:
        a = np.linspace(0, 360, n * 4, endpoint=False)
        outer = _dest(lat, lon, a, d_max_km)
        return [tuple(p) for p in zip(outer[1], outer[0])]
    a = azimuth_deg + np.linspace(-hfov_deg / 2, hfov_deg / 2, n)
    outer = _dest(lat, lon, a, d_max_km)
    inner = _dest(lat, lon, a[::-1], d_min_km)
    pts = list(zip(outer[1], outer[0])) + list(zip(inner[1], inner[0]))
    return [tuple(map(float, p)) for p in pts]


def _dest(lat, lon, bearing, d_km):
    lat1, lon1, b = np.radians(lat), np.radians(lon), np.radians(bearing)
    ang = np.asarray(d_km) / R_EARTH_KM
    lat2 = np.arcsin(np.sin(lat1) * np.cos(ang) + np.cos(lat1) * np.sin(ang) * np.cos(b))
    lon2 = lon1 + np.arctan2(
        np.sin(b) * np.sin(ang) * np.cos(lat1), np.cos(ang) - np.sin(lat1) * np.sin(lat2)
    )
    return np.degrees(lat2), (np.degrees(lon2) + 540) % 360 - 180
