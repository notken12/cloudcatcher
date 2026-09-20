"""FAA WeatherCams (weathercams.faa.gov). No key, but the API 401s without a browser User-Agent.
3,537 cameras (1,942 CONUS, 1,446 AK), every one with an exact bearing; ~8-10 min cadence; only the last 13 frames are kept."""
import datetime as dt
import os

import requests

from common.geo import angle_diff_deg, distance_km

BASE = "https://weathercams.faa.gov/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Referer": "https://weathercams.faa.gov/map/",
    "Accept": "application/json",
}
MAX_ARCHIVE_FRAMES = 13


def _get(path: str) -> dict:
    return requests.get(BASE + path, headers=HEADERS, timeout=60).json()["payload"]


def sites() -> list[dict]:
    return _get("/sites")


def cameras() -> list[dict]:
    return _get("/cameras")


def cameras_in_bounds(south: float, west: float, north: float, east: float) -> list[dict]:
    return _get(f"/cameras?bounds={south},{west}|{north},{east}")


def last_images(camera_id: int, n: int = MAX_ARCHIVE_FRAMES) -> list[dict]:
    """Newest first: imageUri, imageDatetime (ISO Z)."""
    return _get(f"/cameras/{camera_id}/images/last/{n}") or []


def image_time(image: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(image["imageDatetime"].replace("Z", "+00:00"))


def nearest_image(images: list[dict], when: dt.datetime) -> dict | None:
    return min(images, key=lambda im: abs((image_time(im) - when).total_seconds())) if images else None


def download(image: dict, out_dir: str) -> str:
    path = os.path.join(out_dir, image["imageFilename"])
    if not os.path.exists(path):
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(requests.get(image["imageUri"], headers=HEADERS, timeout=60).content)
    return path


def age_minutes(camera: dict, now: dt.datetime) -> float | None:
    stamp = camera.get("cameraLastSuccess")
    if not stamp:
        return None
    return (now - dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() / 60


def usable(camera: dict, now: dt.datetime, max_age_minutes: float = 30) -> bool:
    age = age_minutes(camera, now)
    return age is not None and age <= max_age_minutes and not camera["cameraInMaintenance"] and not camera["cameraOutOfOrder"]


def nearby(all_cameras: list[dict], lat: float, lon: float, radius_km: float) -> list[dict]:
    out = []
    for camera in all_cameras:
        d = float(distance_km(lat, lon, camera["latitude"], camera["longitude"]))
        if d <= radius_km:
            out.append({**camera, "distance_km": round(d, 1)})
    return sorted(out, key=lambda c: c["distance_km"])


def facing(camera_list: list[dict], bearing: float, tolerance_deg: float = 50) -> list[dict]:
    return [c for c in camera_list if angle_diff_deg(c["cameraBearing"], bearing) <= tolerance_deg]


if __name__ == "__main__":
    import sys
    lat, lon, radius = map(float, sys.argv[1:4])
    now = dt.datetime.now(dt.UTC)
    for camera in nearby(cameras(), lat, lon, radius):
        print(camera["cameraId"], camera["distance_km"], "km", camera["cameraDirection"], camera["cameraBearing"], "age", round(age_minutes(camera, now) or -1), "min")
