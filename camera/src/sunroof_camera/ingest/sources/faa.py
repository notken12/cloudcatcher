"""FAA WeatherCams (weathercams.faa.gov) — ~3,500 cams (CONUS + Alaska), every one with an
exact `cameraBearing`, ~8–10 min cadence, only the last 13 frames kept. No key, but the API
401s without a browser User-Agent + Referer (see ken/data-validation `camera_ken/faa_weathercams.py`).

The current frame has no stable URL: `image_url` points at the `images/last/1` API endpoint
and `fetch_frame` resolves it to the JPEG (`imageUri`) and its source timestamp (`imageDatetime`).
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, get_json

API = "https://weathercams.faa.gov/api"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Referer": "https://weathercams.faa.gov/map/",
    "Accept": "application/json",
}
MAX_ARCHIVE_FRAMES = 13


def _iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def camera_row(site: dict, cam: dict) -> Camera:
    return Camera(
        id=f"faa:{cam['cameraId']}",
        source="faa",
        source_kind="jpeg",
        name=f"{site.get('siteArea') or site.get('siteName')} · {cam.get('cameraDirection') or ''}".strip(
            " ·"
        ),
        lat=cam.get("latitude") or site["latitude"],
        lon=cam.get("longitude") or site["longitude"],
        alt_m=float(site["elevation"]) * 0.3048 if site.get("elevation") is not None else None,
        tz=site.get("timeZone"),
        azimuth_deg=float(cam["cameraBearing"]) if cam.get("cameraBearing") is not None else None,
        hfov_deg=float(cam.get("mapWedgeAngle") or 45),
        elev_min_deg=-5.0,
        elev_max_deg=30.0,
        heading_conf="catalog" if cam.get("cameraBearing") is not None else "unknown",
        image_url=f"{API}/cameras/{cam['cameraId']}/images/last/1",
        page_url=f"https://weathercams.faa.gov/cameras/site/{site['siteId']}",
        refresh_s=600,
        history_kind="last_n",
        history_depth_days=0,
        license="US Government work (FAA); third-party sites carry their own attribution",
        attribution=site.get("operatedBy") or "FAA WeatherCams",
        last_frame_ts=_iso(cam.get("cameraLastSuccess")),
        health="dead"
        if cam.get("cameraOutOfOrder") or cam.get("cameraInMaintenance")
        else "unverified",
    )


class FaaAdapter:
    source: ClassVar[str] = "faa"

    def __init__(self, bounds: tuple[float, float, float, float] | None = None):
        self.bounds = bounds  # (south, west, north, east) to limit the pull

    def _q(self, path: str) -> str:
        if self.bounds:
            s, w, n, e = self.bounds
            return f"{API}{path}?bounds={s},{w}|{n},{e}"
        return f"{API}{path}"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        sites = (await get_json(http, self._q("/sites"), headers=HEADERS))["payload"]
        by_site = {s["siteId"]: s for s in sites}
        cams = (await get_json(http, self._q("/cameras"), headers=HEADERS))["payload"]
        return [camera_row(by_site[c["siteId"]], c) for c in cams if c["siteId"] in by_site]

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        cid = cam.id.split(":")[1]
        n = MAX_ARCHIVE_FRAMES if ts else 1
        try:
            imgs = (await get_json(http, f"{API}/cameras/{cid}/images/last/{n}", headers=HEADERS))[
                "payload"
            ] or []
        except (httpx.HTTPError, KeyError, ValueError):
            return None
        if not imgs:
            return None
        pick = imgs[0]
        if ts is not None:
            pick = min(imgs, key=lambda im: abs((_iso(im["imageDatetime"]) - ts).total_seconds()))
        try:
            r = await http.get(pick["imageUri"], headers={"User-Agent": HEADERS["User-Agent"]})
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        return Frame(
            cam.id,
            _iso(pick["imageDatetime"]),
            pick["imageUri"],
            r.content,
            r.headers.get("content-type", ""),
        )
