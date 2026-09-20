"""NWS alerts: live from api.weather.gov (no history), historical from IEM's VTEC archive."""
import datetime as dt

import numpy as np
import requests

from common.geo import distance_km

USER_AGENT = "nimbly (kenzhou084@gmail.com)"
CONVECTIVE_EVENTS = ("Severe Thunderstorm Warning", "Tornado Warning", "Flash Flood Warning", "Special Weather Statement")


def active_alerts() -> list[dict]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    url = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"
    return requests.get(url, headers=headers, timeout=60).json()["features"]


def polygon_centroid(feature: dict) -> tuple[float, float] | None:
    geometry = feature.get("geometry")
    if not geometry or geometry["type"] != "Polygon":
        return None
    ring = np.array(geometry["coordinates"][0])
    return float(ring[:, 1].mean()), float(ring[:, 0].mean())


def convective_alerts_near(features: list[dict], lat: float, lon: float, radius_km: float) -> list[dict]:
    out = []
    for feature in features:
        if feature["properties"]["event"] not in CONVECTIVE_EVENTS:
            continue
        centroid = polygon_centroid(feature)
        if centroid and distance_km(lat, lon, *centroid) < radius_km:
            out.append(feature)
    return out


def iem_warning_polygons(begin: dt.datetime, end: dt.datetime, wfo: str) -> list[dict]:
    """Storm-based warning polygons active in [begin, end] for one WFO, from IEM."""
    url = ("https://mesonet.agron.iastate.edu/api/1/vtec/sbw_interval.geojson"
           f"?begints={begin:%Y-%m-%dT%H:%MZ}&endts={end:%Y-%m-%dT%H:%MZ}&wfo={wfo}")
    return requests.get(url, timeout=120).json()["features"]


def iem_vtec_events(wfo: str, year: int, phenomena: str = "TO", significance: str = "W") -> list[dict]:
    url = (f"https://mesonet.agron.iastate.edu/json/vtec_events.py?wfo={wfo}&year={year}"
           f"&phenomena={phenomena}&significance={significance}")
    return requests.get(url, timeout=120).json()["events"]


if __name__ == "__main__":
    features = active_alerts()
    print(len(features), "active alerts;", len(convective_alerts_near(features, 39.0, -98.0, 1000)), "convective within 1000 km of Kansas")
