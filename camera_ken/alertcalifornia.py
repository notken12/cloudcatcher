"""ALERTCalifornia (UCSD) PTZ fire cameras: ~1,300 geolocated CA cams, 1920x1080, ~2-min frames, near-IR at night, live pan/FOV.
Imagery is CC BY-NC-ND 4.0: display the live frame with attribution, don't cache or re-host."""
import datetime as dt

import requests

from common.geo import distance_km

FEED = "https://cameras.alertcalifornia.org/public-camera-data/all_cameras-v3.json"
BROWSER_UA = {"User-Agent": "Mozilla/5.0"}


def cameras() -> list[dict]:
    """Geolocated cameras with lat, lon, heading (az_current), fov, last_frame_ts and id."""
    features = requests.get(FEED, headers=BROWSER_UA, timeout=120).json()["features"]
    out = []
    for feature in features:
        lon, lat, *_ = feature["geometry"]["coordinates"]
        if lat is None:
            continue
        p = feature["properties"]
        out.append({"id": p["id"], "lat": lat, "lon": lon, "bearing": p.get("az_current"), "fov_deg": p.get("fov"),
                    "last_frame_ts": p.get("last_frame_ts"), "patrolling": bool(p.get("is_currently_patrolling")), "state": p.get("state")})
    return out


def latest_frame_url(camera_id: str) -> str:
    return f"https://cameras.alertcalifornia.org/public-camera-data/{camera_id}/latest-frame.jpg"


def age_minutes(camera: dict, now: dt.datetime) -> float | None:
    ts = camera.get("last_frame_ts")
    return None if not ts else (now.timestamp() - ts) / 60


def nearby(all_cameras: list[dict], lat: float, lon: float, radius_km: float) -> list[dict]:
    out = [{**c, "distance_km": float(distance_km(lat, lon, c["lat"], c["lon"]))} for c in all_cameras]
    return sorted([c for c in out if c["distance_km"] <= radius_km], key=lambda c: c["distance_km"])


if __name__ == "__main__":
    import sys
    lat, lon, radius = map(float, sys.argv[1:4])
    now = dt.datetime.now(dt.UTC)
    for camera in nearby(cameras(), lat, lon, radius)[:10]:
        print(camera["id"], round(camera["distance_km"]), "km", "brg", camera["bearing"], "fov", camera["fov_deg"], "age", round(age_minutes(camera, now) or -1, 1), "min", latest_frame_url(camera["id"]))
