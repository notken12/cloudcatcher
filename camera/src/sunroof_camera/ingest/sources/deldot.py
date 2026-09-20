"""DelDOT traffic cameras (Delaware, ~360 cams, keyless JSON, public HLS).

`tmc.deldot.gov/json/videocamera.json` lists every camera with lat/lon, title and a
`urls` block; `m3u8s` is an open Wowza playlist on video.deldot.gov. No snapshot JPEG
is published, so frames come from the HLS stream via ffmpeg. Heading is unknown.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, get_json

URL = "https://tmc.deldot.gov/json/videocamera.json"


class DelDOTAdapter:
    source: ClassVar[str] = "deldot"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        payload = await get_json(http, URL)
        out: list[Camera] = []
        for c in payload.get("videoCameras") or []:
            stream = (c.get("urls") or {}).get("m3u8s") or (c.get("urls") or {}).get("m3u8")
            if not c.get("id") or not stream or not c.get("enabled"):
                continue
            if str(c.get("status") or "").lower() not in ("", "active"):
                continue
            lat, lon = c.get("lat"), c.get("lon")
            if lat is None or lon is None:
                continue
            out.append(
                Camera(
                    id=f"deldot:{c['id']}",
                    source=self.source,
                    source_kind="hls",
                    name=str(c.get("title") or c["id"]).strip(),
                    lat=float(lat),
                    lon=float(lon),
                    tz="America/New_York",
                    elev_min_deg=-5,
                    elev_max_deg=25,
                    heading_conf="unknown",
                    stream_url=stream,
                    page_url=f"https://deldot.gov/map/?camera={c['id']}",
                    refresh_s=60,
                    license="DelDOT public traffic data",
                    attribution="Delaware Department of Transportation",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return None  # live HLS only: fetch.py falls through to fetch_hls_frame (ffmpeg)
