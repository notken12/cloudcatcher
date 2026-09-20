"""ALGO Traffic (Alabama DOT, ~620 cams, keyless JSON, JPEG snapshot + public HLS).

`api.algotraffic.com/v4.0/Cameras` is what algotraffic.com's map loads. Each record has
`location.{latitude,longitude,direction}`, `snapshotImageUrl` (fresh JPEG) and
`playbackUrls.hls` (Wowza CDN). Only `accessLevel == "Public"` records are kept.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

URL = "https://api.algotraffic.com/v4.0/Cameras"


def _name(loc: dict) -> str:
    road = loc.get("displayRouteDesignator") or loc.get("routeDesignator") or ""
    cross = loc.get("displayCrossStreet") or loc.get("crossStreet") or ""
    city = loc.get("city") or ""
    base = " @ ".join(s for s in (road, cross) if s)
    return f"{base} ({city})" if base and city else base or city


class AlgoTrafficAdapter:
    source: ClassVar[str] = "al_algo"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for c in await get_json(http, URL):
            loc = c.get("location") or {}
            lat, lon = loc.get("latitude"), loc.get("longitude")
            img = c.get("snapshotImageUrl")
            if c.get("accessLevel") != "Public" or not img or lat is None or lon is None:
                continue
            direction = loc.get("direction")
            az = parse_heading_text(direction) if direction and direction != "Any" else None
            out.append(
                Camera(
                    id=f"al_algo:{c['id']}",
                    source=self.source,
                    source_kind="jpeg",
                    name=_name(loc) or str(c["id"]),
                    lat=float(lat),
                    lon=float(lon),
                    tz="America/Chicago",
                    azimuth_deg=az,
                    heading_conf="text" if az is not None else "unknown",
                    elev_min_deg=-5,
                    elev_max_deg=25,
                    image_url=img,
                    stream_url=(c.get("playbackUrls") or {}).get("hls"),
                    page_url=c.get("permLink") or "https://algotraffic.com/",
                    refresh_s=60,
                    license="ALGO Traffic public data (ALDOT)",
                    attribution="Alabama Department of Transportation / ALGO Traffic",
                )
            )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
