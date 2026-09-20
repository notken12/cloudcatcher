"""City of Austin traffic cameras (~1,000 cams, keyless Socrata open data, JPEG snapshot).

`data.austintexas.gov/resource/b4k4-adkb.json` (Austin Transportation, public domain)
lists every CCTV with a GeoJSON `location` and `screenshot_address`
(`cctv.austinmobility.io/image/<id>.jpg`, refreshed every few minutes). Only
`camera_status == TURNED_ON` rows are kept. Heading is unknown.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json

URL = "https://data.austintexas.gov/resource/b4k4-adkb.json?$limit=5000"


class AustinCCTVAdapter:
    source: ClassVar[str] = "austin"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for c in await get_json(http, URL):
            coords = (c.get("location") or {}).get("coordinates") or []
            img = c.get("screenshot_address")
            if c.get("camera_status") != "TURNED_ON" or not img or len(coords) < 2:
                continue
            cid = c.get("camera_id")
            if not cid:
                continue
            out.append(
                Camera(
                    id=f"austin:{cid}",
                    source=self.source,
                    source_kind="jpeg",
                    name=str(c.get("location_name") or cid).strip(),
                    lat=float(coords[1]),
                    lon=float(coords[0]),
                    tz="America/Chicago",
                    elev_min_deg=-5,
                    elev_max_deg=25,
                    heading_conf="unknown",
                    image_url=img,
                    page_url="https://data.mobility.austin.gov/",
                    refresh_s=300,
                    license="Public Domain (City of Austin open data)",
                    attribution="City of Austin Transportation & Public Works",
                )
            )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
