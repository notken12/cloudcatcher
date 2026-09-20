"""Worldwide cloud layers from Open-Meteo's forecast API (no key; ICON-D2 2 km over Central Europe, ICON-EU, ECMWF IFS, GFS, or best_match).
Provides the same `sample(name, lats, lons)` interface as hrrr.CloudGrid so the sunset rules run anywhere; cloud base/top/ceiling are unavailable (NaN)."""
import datetime as dt

import numpy as np
import requests

from common.geo import point_along
from weather.sunset_rays import MAX_RANGE_KM

URL = "https://api.open-meteo.com/v1/forecast"
POINT_SPACING_KM = 15.0   # free tier counts each point as a call (600/min, 5k/h): 47 points per ray
VARIABLES = ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "cloud_cover", "cape", "visibility", "relative_humidity_2m")
NAME_TO_VARIABLE = {"lcc": "cloud_cover_low", "mcc": "cloud_cover_mid", "hcc": "cloud_cover_high", "tcc": "cloud_cover"}


def hourly_at_points(lats, lons, model: str = "best_match", forecast_days: int = 2, past_days: int = 0) -> list[dict]:
    params = {"latitude": ",".join(f"{x:.4f}" for x in lats), "longitude": ",".join(f"{x:.4f}" for x in lons),
              "hourly": ",".join(VARIABLES), "models": model, "forecast_days": forecast_days, "past_days": past_days, "timezone": "UTC"}
    payload = requests.get(URL, params=params, timeout=60).json()
    return payload if isinstance(payload, list) else [payload]


class RayCloudSampler:
    """Cloud layers along one sun ray at one valid hour, fetched in a single request. Drop-in for CloudGrid in sunset_rays/sunset_rules."""

    def __init__(self, lat: float, lon: float, sun_azimuth: float, valid: dt.datetime, model: str = "best_match", past_days: int = 0):
        self.distances = np.arange(0, MAX_RANGE_KM + POINT_SPACING_KM, POINT_SPACING_KM)
        self.lats, self.lons = point_along(lat, lon, sun_azimuth, self.distances)
        points = hourly_at_points(self.lats, self.lons, model, past_days=past_days)
        hour = valid.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
        if "hourly" not in points[0]:
            raise RuntimeError(points[0].get("reason"))
        index = points[0]["hourly"]["time"].index(hour)
        self.fields = {name: np.array([p["hourly"][var][index] for p in points], dtype=float) for name, var in NAME_TO_VARIABLE.items()}
        self.fields["orog"] = np.array([p["elevation"] for p in points], dtype=float)
        self.fields["humidity"] = np.array([p["hourly"]["relative_humidity_2m"][index] for p in points], dtype=float)
        nan = np.full(len(points), np.nan)
        self.fields.update({"base": nan, "top": nan, "ceil": nan})

    def sample(self, name: str, lats, lons) -> np.ndarray:
        """Nearest ray point by distance from the ray origin (callers only ask for points on this ray)."""
        d = np.hypot(np.asarray(lats) - self.lats[0], (np.asarray(lons) - self.lons[0]) * np.cos(np.radians(self.lats[0]))) * 111.0
        index = np.clip(np.round(d / POINT_SPACING_KM).astype(int), 0, len(self.distances) - 1)
        return self.fields[name][index]

    def disk_mean(self, name: str, lat: float, lon: float, radius_km: float) -> float:
        return float(np.nanmean(self.fields[name][self.distances <= radius_km]))
