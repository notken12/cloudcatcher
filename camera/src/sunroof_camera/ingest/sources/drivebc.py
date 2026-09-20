"""DriveBC (British Columbia) highway cams — `orientation` compass field + elevation.

Coast Mountains / Rockies passes; PNG frames despite the .jpg extension.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import COMPASS_8, Frame, default_fetch_frame, get_json

URL = "https://images.drivebc.ca/webcam/api/v1/webcams"


class DriveBCAdapter:
    source: ClassVar[str] = "drivebc"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for w in (await get_json(http, URL))["webcams"]:
            loc = w.get("location") or {}
            if not w.get("isOn") or not w.get("shouldAppear") or loc.get("latitude") is None:
                continue
            az = COMPASS_8.get((w.get("orientation") or "").upper())
            out.append(
                Camera(
                    id=f"drivebc:{w['id']}",
                    source=self.source,
                    source_kind="jpeg",
                    name=w.get("camName") or w.get("caption", ""),
                    lat=loc["latitude"],
                    lon=loc["longitude"],
                    alt_m=loc.get("elevation"),
                    azimuth_deg=az,
                    elev_min_deg=-5,
                    elev_max_deg=25,
                    heading_conf="catalog" if az is not None else "unknown",
                    image_url=w["links"]["imageDisplay"],
                    page_url=f"https://images.drivebc.ca/bchighwaycam/pub/html/www/{w['id']}.html",
                    refresh_s=900,
                    history_kind="api",
                    history_template=w["links"].get("replayTheDay"),
                    history_depth_days=1,
                    license="DriveBC terms; attribution DriveBC.ca",
                    attribution="DriveBC / BC Ministry of Transportation",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
