"""QLDTraffic (Queensland, Australia) webcams (~140 cams, keyless GeoJSON, CC BY 4.0).

The keyed `api.qldtraffic.qld.gov.au` REST API is not needed: the qldtraffic.qld.gov.au map
loads `data.qldtraffic.qld.gov.au/webcameras.geojson`, whose `image_url`s are plain JPEGs
refreshed every ~minute. `direction` is a compass word ("NorthEast", "South").
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

URL = "https://data.qldtraffic.qld.gov.au/webcameras.geojson"


class QLDTrafficAdapter:
    source: ClassVar[str] = "au_qld"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        payload = await get_json(http, URL)
        out: list[Camera] = []
        for feat in payload.get("features") or []:
            p = feat.get("properties") or {}
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            img = p.get("image_url")
            if len(coords) < 2 or not img or p.get("id") is None:
                continue
            az = parse_heading_text(p.get("direction"))
            out.append(
                Camera(
                    id=f"au_qld:{p['id']}",
                    source=self.source,
                    source_kind="jpeg",
                    name=(p.get("description") or p.get("locality") or str(p["id"])).strip(),
                    lat=float(coords[1]),
                    lon=float(coords[0]),
                    tz="Australia/Brisbane",
                    azimuth_deg=az,
                    heading_conf="text" if az is not None else "unknown",
                    elev_min_deg=-5,
                    elev_max_deg=20,
                    image_url=img,
                    page_url="https://qldtraffic.qld.gov.au/",
                    refresh_s=120,
                    license="CC BY 4.0",
                    attribution="© State of Queensland (Department of Transport and Main Roads)",
                )
            )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
