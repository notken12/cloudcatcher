"""ODOT TripCheck (Oregon) — Esri feature inventory, heading from the filename suffix
(`...MeglerBrNB_pid392.jpg` -> NB). Cascades, coast, Columbia Gorge."""

from __future__ import annotations

import re
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import COMPASS_8, Frame, default_fetch_frame, get_json

URL = "https://tripcheck.com/Scripts/map/data/cctvinventory.js"
IMG = "https://tripcheck.com/RoadCams/cams/{filename}"
_SUFFIX = re.compile(r"(NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|NE|SE|SW|NW|N|S|E|W)B?_pid\d+\.jpg$", re.I)


def heading_from_filename(filename: str) -> float | None:
    m = _SUFFIX.search(filename)
    return COMPASS_8.get(m.group(1).upper()) if m else None


class TripCheckAdapter:
    source: ClassVar[str] = "tripcheck"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for f in (await get_json(http, URL))["features"]:
            a = f["attributes"]
            lat, lon = a.get("latitude"), a.get("longitude")
            if lat is None or lon is None or not a.get("filename"):
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):  # a few rows have garbage coords
                continue
            az = heading_from_filename(a["filename"])
            out.append(
                Camera(
                    id=f"tripcheck:{a['cameraId']}",
                    source=self.source,
                    source_kind="jpeg",
                    name=(a.get("title") or "").strip(),
                    lat=a["latitude"],
                    lon=a["longitude"],
                    azimuth_deg=az,
                    elev_min_deg=-5,
                    elev_max_deg=25,
                    heading_conf="text" if az is not None else "unknown",
                    image_url=IMG.format(filename=a["filename"]),
                    page_url="https://tripcheck.com/",
                    refresh_s=600,
                    license="ODOT public data",
                    attribution="Oregon DOT TripCheck",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
