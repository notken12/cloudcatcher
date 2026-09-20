"""Vectorized solar position (NOAA low-accuracy algorithm, ~0.01° — plenty for gating).

`astral` is per-location and Python-loop bound; we need sun az/el for 50k rows at once.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np


def _julian_century(t: datetime) -> float:
    t = t.astimezone(timezone.utc)
    jd = t.timestamp() / 86400.0 + 2440587.5
    return (jd - 2451545.0) / 36525.0


def sun_position_deg(lat, lon, t: datetime):
    """Return (elevation_deg, azimuth_deg) arrays of the sun for each (lat, lon) at time t."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    jc = _julian_century(t)
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = np.radians(m)
    c = (
        np.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + np.sin(2 * mr) * (0.019993 - 0.000101 * jc)
        + np.sin(3 * mr) * 0.000289
    )
    true_long = l0 + c
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * np.sin(np.radians(omega))
    obliq0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliq = obliq0 + 0.00256 * np.cos(np.radians(omega))
    decl = np.degrees(np.arcsin(np.sin(np.radians(obliq)) * np.sin(np.radians(app_long))))
    y = np.tan(np.radians(obliq / 2)) ** 2
    l0r, mr2 = np.radians(l0), mr
    eqtime = 4 * np.degrees(
        y * np.sin(2 * l0r)
        - 2 * e * np.sin(mr2)
        + 4 * e * y * np.sin(mr2) * np.cos(2 * l0r)
        - 0.5 * y * y * np.sin(4 * l0r)
        - 1.25 * e * e * np.sin(2 * mr2)
    )
    tu = t.astimezone(timezone.utc)
    minutes = tu.hour * 60 + tu.minute + tu.second / 60
    tst = (minutes + eqtime + 4 * lon) % 1440
    ha = np.where(tst / 4 < 0, tst / 4 + 180, tst / 4 - 180)
    latr, declr, har = np.radians(lat), np.radians(decl), np.radians(ha)
    cos_zen = np.sin(latr) * np.sin(declr) + np.cos(latr) * np.cos(declr) * np.cos(har)
    zen = np.degrees(np.arccos(np.clip(cos_zen, -1, 1)))
    elev = 90 - zen
    # atmospheric refraction
    er = np.radians(elev)
    refr = (
        np.where(
            elev > 85,
            0.0,
            np.where(
                elev > 5,
                58.1 / np.tan(er) - 0.07 / np.tan(er) ** 3 + 0.000086 / np.tan(er) ** 5,
                np.where(
                    elev > -0.575,
                    1735 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711))),
                    -20.772 / np.tan(er),
                ),
            ),
        )
        / 3600
    )
    elev = elev + refr
    zr = np.radians(zen)
    with np.errstate(divide="ignore", invalid="ignore"):
        az_cos = (np.sin(latr) * np.cos(zr) - np.sin(declr)) / (np.cos(latr) * np.sin(zr))
    az = np.degrees(np.arccos(np.clip(az_cos, -1, 1)))
    az = np.where(ha > 0, (az + 180) % 360, (540 - az) % 360)
    return elev, az
