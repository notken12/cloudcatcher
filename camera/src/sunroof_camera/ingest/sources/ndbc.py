"""NOAA NDBC BuoyCAMs — offshore buoys with a 6-view (360°) camera strip, hourly, daylight only.

NDBC publishes no machine-readable BuoyCAM list; `buoycam.php?station=X` returns a fixed
"no image" placeholder for buoys without a camera, so we probe NDBC-owned buoys and keep
the ones that return a real image. Over-water horizon cams: ideal for sunrise/sunset/storms.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, fetch_image

STATIONS = "https://www.ndbc.noaa.gov/activestations.xml"
IMAGE = "https://www.ndbc.noaa.gov/buoycam.php?station={sid}"
PLACEHOLDER_SIZE = 12979  # bytes; identical body for every camera-less station


class NDBCAdapter:
    source: ClassVar[str] = "ndbc"

    async def _has_cam(self, http: httpx.AsyncClient, sid: str, sem: asyncio.Semaphore) -> bool:
        async with sem:
            try:
                r = await http.get(IMAGE.format(sid=sid))
            except httpx.HTTPError:
                return False
        return (
            r.status_code == 200
            and r.headers.get("content-type", "").startswith("image/")
            and len(r.content) != PLACEHOLDER_SIZE
        )

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        r = await http.get(STATIONS)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        cands = [
            s
            for s in root.iter("station")
            if s.get("type") == "buoy" and s.get("owner", "").startswith("NDBC")
        ]
        sem = asyncio.Semaphore(4)
        flags = await asyncio.gather(*(self._has_cam(http, s.get("id", ""), sem) for s in cands))
        out: list[Camera] = []
        for s, ok in zip(cands, flags):
            if not ok:
                continue
            sid = s.get("id", "")
            out.append(
                Camera(
                    id=f"ndbc:{sid}",
                    source=self.source,
                    source_kind="jpeg",
                    name=f"BuoyCAM {sid} — {s.get('name', '')}",
                    lat=float(s.get("lat", "nan")),
                    lon=float(s.get("lon", "nan")),
                    alt_m=3.0,
                    azimuth_deg=None,
                    hfov_deg=360,
                    elev_min_deg=-2,
                    elev_max_deg=25,
                    heading_conf="catalog",
                    all_sky=False,
                    over_water=True,
                    sky_frac=0.6,
                    image_url=IMAGE.format(sid=sid),
                    page_url=f"https://www.ndbc.noaa.gov/station_page.php?station={sid}",
                    refresh_s=3600,
                    license="US Government public domain",
                    attribution="NOAA National Data Buoy Center",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        fr = await fetch_image(http, cam, cam.image_url or "")
        if fr and len(fr.content) == PLACEHOLDER_SIZE:
            return None
        return fr
