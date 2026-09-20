"""Hong Kong Transport Department traffic snapshots (data.gov.hk, ~1,000 cams, 2-min refresh).

Catalog XML lists key/description/lat/lon/url; the JPEG is a stable URL per key.
No heading field; some descriptions end in "- Eastbound" etc., which gives a text heading.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, parse_heading_text

URL = "https://static.data.gov.hk/td/traffic-snapshot-images/code/Traffic_Camera_Locations_En.xml"


def _text(el: ET.Element, tag: str) -> str:
    node = el.find(tag)
    return (node.text or "").strip() if node is not None else ""


class HongKongTDAdapter:
    source: ClassVar[str] = "hk_td"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        r = await http.get(URL)
        r.raise_for_status()
        out: list[Camera] = []
        for im in ET.fromstring(r.content).iter("image"):
            key, url = _text(im, "key"), _text(im, "url")
            lat, lon = _text(im, "latitude"), _text(im, "longitude")
            if not (key and url and lat and lon):
                continue
            desc = _text(im, "description") or key
            az = parse_heading_text(desc.split(" - ")[-1]) if " - " in desc else None
            out.append(
                Camera(
                    id=f"hk_td:{key}",
                    source=self.source,
                    source_kind="jpeg",
                    name=desc,
                    lat=float(lat),
                    lon=float(lon),
                    tz="Asia/Hong_Kong",
                    elev_min_deg=-5,
                    elev_max_deg=20,
                    azimuth_deg=az,
                    heading_conf="text" if az is not None else "unknown",
                    image_url=url,
                    page_url="https://data.gov.hk/en-data/dataset/hk-td-tis_2-traffic-snapshot-images",
                    refresh_s=120,
                    license="data.gov.hk Terms and Conditions of Use (free reuse with attribution)",
                    attribution="Transport Department, HKSAR Government",
                )
            )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
