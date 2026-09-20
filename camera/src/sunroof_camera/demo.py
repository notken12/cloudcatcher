"""Fake "weather backend" events for the sandbox, picked so that the catalog actually has
cameras that could see them *right now* (daylight cameras for daytime events, a camera
whose azimuth points at the sun for sunset/sunrise, ...). Locations therefore change with
the time of day; ids embed the minute so the feed shows fresh entries.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import solar
from .footage import WeatherEvent
from .query import MAX_CATALOG_AGE_S, Catalog

FALLBACK = [  # used when nothing in the catalog is lit: exercises the failure statuses
    ("thunderstorm", 38.60, -121.60, 25.0),  # Sacramento valley
    ("undercast", 39.10, -120.05, 10.0),  # Tahoe rim
]


def _offset(lat: float, lon: float, bearing_deg: float, dist_km: float) -> tuple[float, float]:
    r = 6371.0088
    b, la1, lo1 = map(math.radians, (bearing_deg, lat, lon))
    d = dist_km / r
    la2 = math.asin(math.sin(la1) * math.cos(d) + math.cos(la1) * math.sin(d) * math.cos(b))
    lo2 = lo1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(la1), math.cos(d) - math.sin(la1) * math.sin(la2)
    )
    return math.degrees(la2), (math.degrees(lo2) + 540) % 360 - 180


def fake_events(
    cat: Catalog, now: datetime | None = None, ignore_night: bool = False
) -> list[WeatherEvent]:
    now = now or datetime.now(timezone.utc)
    df = cat.df
    stamp = now.strftime("%H%M")
    el, az = solar.sun_position_deg(df["lat"].to_numpy(), df["lon"].to_numpy(), now)
    if ignore_night:
        el = np.full_like(el, 45.0)  # pretend it is midday everywhere
    age_s = (pd.Timestamp(now) - df["last_frame_ts"]).dt.total_seconds().to_numpy(dtype=float)
    alive = (df["health"].astype(str) != "dead").to_numpy() & (
        np.isnan(age_s) | (age_s <= MAX_CATALOG_AGE_S)
    )
    heading = df["azimuth_deg"].notna().to_numpy()
    rng = np.random.default_rng(int(now.timestamp() // 600))
    out: list[WeatherEvent] = []

    def pick(mask: np.ndarray) -> pd.Series | None:
        idx = np.flatnonzero(mask)
        return df.iloc[int(rng.choice(idx))] if len(idx) else None

    day = alive & heading & (el > 8)
    c = pick(day)
    if c is not None:
        lat, lon = _offset(c["lat"], c["lon"], float(c["azimuth_deg"]), 50)
        out.append(
            WeatherEvent(
                id=f"fake-ts-{stamp}",
                type="thunderstorm",
                lat=lat,
                lon=lon,
                radius_km=20,
                severity=0.7,
                t_start=now,
            )
        )
    alt = np.nan_to_num(df["alt_m"].to_numpy(dtype=float), nan=0.0)
    c = pick(day & (alt > 1000))
    if c is not None:
        out.append(
            WeatherEvent(
                id=f"fake-uc-{stamp}",
                type="undercast",
                lat=c["lat"],
                lon=c["lon"],
                radius_km=10,
                layer_top_m=float(alt[df.index.get_loc(c.name)]) - 300,
                t_start=now,
            )
        )
    # sunset / sunrise: sun inside the camera's horizontal FOV and near the horizon
    daz = np.abs(
        (az - np.nan_to_num(df["azimuth_deg"].to_numpy(dtype=float), nan=999) + 180) % 360 - 180
    )
    half = np.nan_to_num(df["hfov_deg"].to_numpy(dtype=float), nan=60) / 2
    near_sun = alive & heading & (el > -5) & (el < 4) & (daz < half)
    c = pick(near_sun)
    if c is not None:
        hour_local = (now.hour + c["lon"] / 15) % 24
        typ = "sunset" if 10 <= hour_local <= 23.9 else "sunrise"
        out.append(
            WeatherEvent(
                id=f"fake-{typ[:3]}-{stamp}",
                type=typ,
                lat=c["lat"],
                lon=c["lon"],  # type: ignore[arg-type]
                radius_km=2,
                t_start=now,
            )
        )
    # lightning: dark frames allowed, so any live camera works as a target
    c = pick(alive & heading)
    if c is not None:
        lat, lon = _offset(c["lat"], c["lon"], float(c["azimuth_deg"]), 20)
        out.append(
            WeatherEvent(
                id=f"fake-ltg-{stamp}",
                type="lightning",
                lat=lat,
                lon=lon,
                radius_km=10,
                severity=0.9,
                t_start=now,
            )
        )
    if not out:
        for typ, lat, lon, r in FALLBACK:
            out.append(
                WeatherEvent(id=f"fake-{typ}-{stamp}", type=typ, lat=lat, lon=lon, radius_km=r)
            )  # type: ignore[arg-type]
    return out
