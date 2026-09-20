"""Per-point climatology from Open-Meteo's ERA5 archive (no key, ~10 s per 10-year pull, ~5-day lag).
Good for continuous fields (cloud layers); precipitation is zero-inflated and its percentiles are not informative."""
import datetime as dt

import numpy as np
import requests

CLOUD_VARIABLES = ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high")


def era5_hourly(lat: float, lon: float, start: dt.date, end: dt.date, variables=CLOUD_VARIABLES) -> dict:
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}&hourly={','.join(variables)}&timezone=UTC")
    hourly = requests.get(url, timeout=120).json()["hourly"]
    times = np.array([dt.datetime.fromisoformat(t) for t in hourly["time"]])
    return {"time": times, "doy": np.array([t.timetuple().tm_yday for t in times]), "hour": np.array([t.hour for t in times]),
            **{v: np.array(hourly[v], dtype=float) for v in variables}}


def percentile_of(series: dict, variable: str, day_of_year: int, hour_utc: int, value: float,
                  doy_window: int = 10, hour_window: int = 1) -> dict:
    """Where `value` falls among historical hours within ±doy_window days and ±hour_window hours."""
    doy_diff = np.abs((series["doy"] - day_of_year + 183) % 366 - 183)
    hour_diff = np.abs((series["hour"] - hour_utc + 12) % 24 - 12)
    sample = series[variable][(doy_diff <= doy_window) & (hour_diff <= hour_window)]
    sample = sample[~np.isnan(sample)]
    return {"n": int(len(sample)), "percentile": float((sample < value).mean() * 100),
            "p50": float(np.percentile(sample, 50)), "p90": float(np.percentile(sample, 90)), "max": float(sample.max())}


if __name__ == "__main__":
    series = era5_hourly(38.077, -102.696, dt.date(2016, 1, 1), dt.date.today() - dt.timedelta(days=7))
    print("Lamar CO, hcc=87% on DOY 263 at 00Z:", percentile_of(series, "cloud_cover_high", 263, 0, 87))
