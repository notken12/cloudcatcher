"""Transport for NSW Maritime coastal-bar / alpine-lake webcams (23 cams, keyless, HLS 1080p).

TfNSW Open Data publishes a static GeoJSON (LOCATION, LINK, LIVE_FEED); LIVE_FEED is a
CoastalCOMS video widget page whose HTML embeds a public CloudFront `playlist.m3u8`.
Cameras look out over river bars / the ocean (over_water), so the horizon and a wide
sky band are in frame; heading is unknown. Frames come from the HLS stream via ffmpeg.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, get_json

log = logging.getLogger(__name__)

URL = (
    "https://opendata.transport.nsw.gov.au/data/dataset/8a7bf1ca-6c3c-447f-94eb-4205069a81db/"
    "resource/0a414134-8f38-4c12-854c-f5b02f67b25f/download/maritime_web_camera.geojson"
)
_M3U8 = re.compile(r"https?://[^\s\"']+\.m3u8")


async def _stream_url(http: httpx.AsyncClient, widget: str) -> str | None:
    try:
        r = await http.get(widget)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("nsw_maritime widget %s: %s", widget, e)
        return None
    m = _M3U8.search(r.text)
    return m.group(0) if m else None


class NSWMaritimeAdapter:
    source: ClassVar[str] = "au_nsw_maritime"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        payload = await get_json(http, URL)
        feats = []
        for feat in payload.get("features") or []:
            p = feat.get("properties") or {}
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2 or p.get("WEB_CAMERA_ID") is None or not p.get("LIVE_FEED"):
                continue
            feats.append((p, coords))
        streams = await asyncio.gather(*(_stream_url(http, p["LIVE_FEED"]) for p, _ in feats))
        out: list[Camera] = []
        for (p, coords), stream in zip(feats, streams):
            if not stream:
                continue
            out.append(
                Camera(
                    id=f"au_nsw_maritime:{p['WEB_CAMERA_ID']}",
                    source=self.source,
                    source_kind="hls",
                    name=str(p.get("LOCATION") or p["WEB_CAMERA_ID"]).strip(),
                    lat=float(coords[1]),
                    lon=float(coords[0]),
                    tz="Australia/Sydney",
                    hfov_deg=90,
                    elev_min_deg=-3,
                    elev_max_deg=30,
                    heading_conf="unknown",
                    over_water=True,
                    stream_url=stream,
                    embed_url=p["LIVE_FEED"],
                    page_url=p.get("LINK"),
                    refresh_s=60,
                    license="CC BY 4.0",
                    attribution="© Transport for NSW (Maritime)",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return None  # live HLS only: fetch.py falls through to fetch_hls_frame (ffmpeg)
