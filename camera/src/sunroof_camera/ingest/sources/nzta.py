"""NZTA / Waka Kotahi state-highway cameras (New Zealand) — public XML, `direction` = Northbound etc.

Our only structured Southern-Hemisphere source besides Windy/PhenoCam."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, parse_heading_text

URL = "https://trafficnz.info/service/traffic/rest/4/cameras/all"
BASE = "https://trafficnz.info"


def _text(el: ET.Element, tag: str) -> str:
    node = el.find(tag)
    return (node.text or "").strip() if node is not None else ""


class NZTAAdapter:
    source: ClassVar[str] = "nzta"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        r = await http.get(URL)
        r.raise_for_status()
        out: list[Camera] = []
        for cam in ET.fromstring(r.content).iter("camera"):
            if _text(cam, "offline") == "true" or _text(cam, "underMaintenance") == "true":
                continue
            lat, lon = _text(cam, "latitude"), _text(cam, "longitude")
            if not lat or not lon:
                continue
            desc = _text(cam, "description")
            az = parse_heading_text(_text(cam, "direction")) or parse_heading_text(desc)
            out.append(
                Camera(
                    id=f"nzta:{_text(cam, 'id')}",
                    source=self.source,
                    source_kind="jpeg",
                    name=_text(cam, "name") or desc,
                    lat=float(lat),
                    lon=float(lon),
                    azimuth_deg=az,
                    elev_min_deg=-5,
                    elev_max_deg=25,
                    heading_conf="text" if az is not None else "unknown",
                    image_url=BASE + _text(cam, "imageUrl"),
                    page_url=BASE + _text(cam, "viewUrl"),
                    refresh_s=600,
                    license="CC BY 4.0 (NZTA open data)",
                    attribution="NZ Transport Agency Waka Kotahi",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
