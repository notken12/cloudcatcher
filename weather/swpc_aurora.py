"""Live aurora sensing from NOAA SWPC (no key). The chain: L1 solar wind (1-min) -> OVATION probability grid (~5-min, valid ~1 h ahead)
-> hemispheric power (GW) and Kp. Aurora is *visible* when probability is high, the sky is dark (sun < -12 deg) and clear."""
import datetime as dt

import numpy as np
import requests

from weather.sun import sun_elevation

BASE = "https://services.swpc.noaa.gov"
DARK_SUN_ELEVATION = -12.0


def ovation() -> dict:
    """1-degree global grid of aurora probability (%): lon 0-359, lat -90..90, plus observation/forecast times."""
    payload = requests.get(f"{BASE}/json/ovation_aurora_latest.json", timeout=60).json()
    coordinates = np.array(payload["coordinates"])
    grid = np.zeros((181, 360))
    grid[(coordinates[:, 1] + 90).astype(int), coordinates[:, 0].astype(int)] = coordinates[:, 2]
    return {"grid": grid, "observed": payload["Observation Time"], "valid": payload["Forecast Time"]}


def probability_at(ov: dict, lat: float, lon: float) -> float:
    return float(ov["grid"][int(round(lat)) + 90, int(round(lon)) % 360])


def kp_1m() -> dict:
    return requests.get(f"{BASE}/json/planetary_k_index_1m.json", timeout=60).json()[-1]


def solar_wind() -> dict:
    """Latest 1-minute IMF and plasma from the L1 monitor (southward Bz = bz_gsm < 0 drives aurora)."""
    mag = requests.get(f"{BASE}/json/rtsw/rtsw_mag_1m.json", timeout=60).json()[0]
    wind = requests.get(f"{BASE}/json/rtsw/rtsw_wind_1m.json", timeout=60).json()[0]
    return {"time": mag["time_tag"], "bz_gsm": mag["bz_gsm"], "bt": mag["bt"], "speed": wind["proton_speed"], "density": wind["proton_density"], "source": mag["source"]}


def hemispheric_power_gw() -> tuple[int, int]:
    """(north, south) GW from the latest nowcast line."""
    lines = [l for l in requests.get(f"{BASE}/text/aurora-nowcast-hemi-power.txt", timeout=60).text.split("\n") if l and not l.startswith("#")]
    _, _, north, south = lines[-1].split()
    return int(north), int(south)


def aurora_rule(ov: dict, lat: float, lon: float, when: dt.datetime, min_probability: float = 10.0) -> dict:
    probability = probability_at(ov, lat, lon)
    elevation = sun_elevation(lat, lon, when)
    return {"probability": probability, "sun_elevation": round(elevation, 1), "dark": elevation < DARK_SUN_ELEVATION,
            "fires": probability >= min_probability and elevation < DARK_SUN_ELEVATION}


if __name__ == "__main__":
    now = dt.datetime.now(dt.UTC)
    ov = ovation()
    print("OVATION observed", ov["observed"], "valid", ov["valid"], "| Kp", kp_1m()["estimated_kp"], "| wind", solar_wind(), "| hemi power GW", hemispheric_power_gw())
    for name, lat, lon in [("Yellowknife", 62.45, -114.37), ("Fairbanks", 64.8, -147.7), ("Tromso", 69.6, 18.9), ("Reykjavik", 64.1, -21.9)]:
        print(name, aurora_rule(ov, lat, lon, now))
