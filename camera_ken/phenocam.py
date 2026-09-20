"""PhenoCam (phenocam.nau.edu): 1,084 sites, 30-min stills since 2008, the only US archive at event-time resolution.
Filenames are <site>_YYYY_MM_DD_HHMMSS.jpg in the site's *standard* time (not always honoured: statenrice1 runs on DST).
Only ~20% of sites see sky (most look down at canopy, most face north)."""
import datetime as dt
import os
import re

import requests

from common.geo import distance_km

BASE = "https://phenocam.nau.edu"
FILENAME_TIME = re.compile(r"_(\d{4})_(\d\d)_(\d\d)_(\d\d)(\d\d)(\d\d)")


def site_info() -> list[dict]:
    """All sites: site, lat, lon, tzoffset, active, date_start, date_end, camera_orientation (16-point compass or empty)."""
    return requests.get(f"{BASE}/webcam/network/siteinfo/", timeout=120).json()


def live_sites(sites: list[dict], today: dt.date, max_stale_days: int = 1) -> list[dict]:
    """`active` is stale for ~100 sites; trust date_end instead."""
    out = []
    for site in sites:
        end = site.get("date_end")
        if not site["active"] or not end or end == "None" or site["lat"] is None:
            continue
        if (today - dt.date.fromisoformat(end)).days <= max_stale_days:
            out.append(site)
    return out


def nearby(sites: list[dict], lat: float, lon: float, radius_km: float) -> list[dict]:
    out = [{**s, "distance_km": float(distance_km(lat, lon, s["lat"], s["lon"]))} for s in sites if s["lat"] is not None]
    return sorted([s for s in out if s["distance_km"] <= radius_km], key=lambda s: s["distance_km"])


def latest_url(site: str) -> str:
    return f"{BASE}/data/latest/{site}.jpg"


def midday_images(site: str) -> list[dict]:
    """One image per day for the whole history ({imgdate, imgpath}); the IR twin may be listed last."""
    return requests.get(f"{BASE}/api/middayimages/{site}/", timeout=120).json()


def image_list(site: str) -> list[str]:
    """Every image URL for the site (10-20 MB for old sites; lags ~2 days)."""
    return requests.get(f"{BASE}/api/siteimagelist/{site}/", timeout=300).json()["imagelist"]


def rgb_twin(url: str) -> str:
    return url.replace("_IR_", "_")


def is_infrared(url: str) -> bool:
    return "_IR_" in url


def frame_time_local(url: str) -> dt.datetime:
    return dt.datetime(*map(int, FILENAME_TIME.search(url).groups()))


def frames_near(urls: list[str], local_time: dt.datetime, tolerance_minutes: float) -> list[str]:
    """RGB frames within tolerance of a local-standard-time instant, nearest first."""
    rgb = [u for u in urls if not is_infrared(u)]
    close = [u for u in rgb if abs((frame_time_local(u) - local_time).total_seconds()) <= tolerance_minutes * 60]
    return sorted(close, key=lambda u: abs((frame_time_local(u) - local_time).total_seconds()))


def download(url: str, out_dir: str) -> str | None:
    """Returns None when the archive serves an HTML error page instead of a JPEG."""
    path = os.path.join(out_dir, url.rsplit("/", 1)[-1])
    if os.path.exists(path):
        return path
    response = requests.get(url, timeout=60)
    if not response.headers.get("content-type", "").startswith("image/"):
        return None
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "wb") as f:
        f.write(response.content)
    return path


if __name__ == "__main__":
    import sys
    lat, lon, radius = map(float, sys.argv[1:4])
    for site in nearby(live_sites(site_info(), dt.date.today(), max_stale_days=3), lat, lon, radius):
        print(site["site"], round(site["distance_km"]), "km", site.get("camera_orientation"), site["date_start"], site["date_end"])
