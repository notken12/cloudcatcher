"""SWPC aurora nowcast: OVATION probability grid, 1-min Kp, real-time solar wind, hemispheric power.

OVATION is live-only (a ~30-70 min forecast); in replay mode it reflects now, not `when`.
"""
import datetime as dt

import numpy as np
import requests

from weather.sun import sun_elevation

OVATION_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
KP_1M_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
RTSW_MAG_URL = "https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json"
RTSW_WIND_URL = "https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json"
HEMI_POWER_URL = "https://services.swpc.noaa.gov/text/aurora-nowcast-hemi-power.txt"

FIRES_PROBABILITY = 20.0
NIGHT_ELEVATION_DEG = -6.0


def ovation() -> dict:
    """Latest OVATION grid: {observed_at, forecast_at, grid} where grid[lat+90, lon%360] = aurora value 0-100."""
    payload = requests.get(OVATION_URL, timeout=30).json()
    coordinates = np.asarray(payload["coordinates"], dtype=float)
    grid = np.zeros((181, 360))
    lons = coordinates[:, 0].astype(int) % 360
    lats = coordinates[:, 1].astype(int) + 90
    grid[lats, lons] = coordinates[:, 2]
    return {
        "observed_at": dt.datetime.fromisoformat(payload["Observation Time"]),
        "forecast_at": dt.datetime.fromisoformat(payload["Forecast Time"]),
        "grid": grid,
    }


def probability_at(ov: dict, lat: float, lon: float) -> float:
    """Bilinear interpolation of the 1-degree OVATION grid."""
    y = np.clip(lat + 90.0, 0, 180)
    x = lon % 360.0
    y0, x0 = int(y) % 181, int(x) % 360
    y1, x1 = min(y0 + 1, 180), (x0 + 1) % 360
    fy, fx = y - int(y), x - int(x)
    grid = ov["grid"]
    return float(
        grid[y0, x0] * (1 - fx) * (1 - fy)
        + grid[y0, x1] * fx * (1 - fy)
        + grid[y1, x0] * (1 - fx) * fy
        + grid[y1, x1] * fx * fy
    )


def aurora_rule(ov: dict, lat: float, lon: float, when: dt.datetime) -> dict:
    """Fires when the OVATION probability is decent and the site is dark enough to see it."""
    probability = probability_at(ov, lat, lon)
    elevation = sun_elevation(lat, lon, when)
    fires = probability >= FIRES_PROBABILITY and elevation <= NIGHT_ELEVATION_DEG
    return {"fires": bool(fires), "probability": round(probability, 1), "sun_elevation_deg": round(elevation, 1)}


def kp_1m() -> dict:
    """Latest 1-min estimated Kp: {time_tag, kp_index, estimated_kp}."""
    return requests.get(KP_1M_URL, timeout=30).json()[-1]


def solar_wind() -> dict:
    """Latest real-time solar wind: {mag: {bt, bz_gsm, ...}, wind: {proton_speed, proton_density, ...}}."""
    mag = requests.get(RTSW_MAG_URL, timeout=30).json()
    wind = requests.get(RTSW_WIND_URL, timeout=30).json()
    keep_mag = ("time_tag", "bt", "bz_gsm", "by_gsm")
    keep_wind = ("time_tag", "proton_speed", "proton_density", "proton_temperature")
    return {"mag": _latest_valid(mag, keep_mag), "wind": _latest_valid(wind, keep_wind)}


def _latest_valid(rows: list[dict], keys: tuple[str, ...]) -> dict:
    for row in rows:
        if all(row.get(k) is not None for k in keys):
            return {k: row[k] for k in keys}
    return {}


def hemispheric_power_gw() -> dict:
    """Latest north/south hemispheric power in GW from the OVATION text product."""
    lines = [l for l in requests.get(HEMI_POWER_URL, timeout=30).text.splitlines() if l and not l.startswith("#")]
    parts = lines[-1].split()
    return {"observed_at": parts[0], "forecast_at": parts[1], "north_gw": float(parts[2]), "south_gw": float(parts[3])}
