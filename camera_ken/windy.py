"""Windy Webcams API v3. Key via WINDY_API_KEY env var. 33k US cams, 93% traffic; filter by category for sky views.
Undocumented: the imgproxy 'full' path gives 1280x720, and the day player embeds ~24 stills at ~50-min spacing."""
import datetime as dt
import os
import re

import requests

BASE = "https://api.windy.com/webcams/api/v3"
INCLUDE = "images,location,player,urls,categories"
SKY_CATEGORIES = ("meteo", "landscape", "mountain", "lake", "coast", "beach")
BROWSER_UA = {"User-Agent": "Mozilla/5.0"}


def _headers() -> dict:
    return {"x-windy-api-key": os.environ["WINDY_API_KEY"]}


def _page(params: str, limit: int, offset: int) -> dict:
    url = f"{BASE}/webcams?{params}&limit={limit}&offset={offset}&include={INCLUDE}"
    return requests.get(url, headers=_headers(), timeout=60).json()


def _all(params: str, max_results: int) -> list[dict]:
    webcams, offset = [], 0
    while offset < max_results:
        page = _page(params, min(50, max_results - offset), offset)
        webcams += page["webcams"]
        offset += 50
        if offset >= page["total"]:
            break
    return webcams


def nearby(lat: float, lon: float, radius_km: int, max_results: int = 500) -> list[dict]:
    """radius must be an integer number of km (the API rejects decimals)."""
    return _all(f"nearby={lat},{lon},{int(radius_km)}", max_results)


def in_region(region_code: str, category: str | None = None, max_results: int = 5000) -> list[dict]:
    """region_code like US.KS; one category per call (comma lists are ANDed and return nothing)."""
    params = f"regions={region_code}" + (f"&categories={category}" if category else "")
    return _all(params, max_results)


def full_image_url(webcam_id: int, kind: str = "current") -> str:
    """kind: current | daylight (most recent daytime frame, can be hours old)."""
    return f"https://imgproxy.windy.com/_/full/plain/{kind}/{webcam_id}/original.jpg"


def download(url: str, path: str) -> str:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(requests.get(url, headers=BROWSER_UA, timeout=60).content)
    return path


def archive(webcam_id: int, span: str = "day") -> list[tuple[dt.datetime, str]]:
    """Stills embedded in the timelapse player. day: ~24 @ 50 min; month: 30 daily; year: weekly; lifetime: monthly."""
    html = requests.get(f"https://webcams.windy.com/webcams/public/embed/player/{webcam_id}/{span}", headers=BROWSER_UA, timeout=60).text
    epochs = sorted(set(int(e) for e in re.findall(rf"{span}/{webcam_id}/original/(\d+)\.jpg", html)))
    return [(dt.datetime.fromtimestamp(e, dt.UTC), f"https://imgproxy.windy.com/_/full/plain/{span}/{webcam_id}/original/{e}.jpg") for e in epochs]


def age_minutes(webcam: dict, now: dt.datetime) -> float:
    return (now - dt.datetime.fromisoformat(webcam["lastUpdatedOn"].replace("Z", "+00:00"))).total_seconds() / 60


def is_sky_category(webcam: dict) -> bool:
    return any(c["id"] in SKY_CATEGORIES for c in webcam["categories"])


if __name__ == "__main__":
    import sys
    lat, lon = float(sys.argv[1]), float(sys.argv[2])
    now = dt.datetime.now(dt.UTC)
    for w in nearby(lat, lon, int(sys.argv[3])):
        print(w["webcamId"], w["status"], f"{age_minutes(w, now):.0f} min", [c["id"] for c in w["categories"]], w["location"]["latitude"], w["location"]["longitude"], w["title"][:50])
