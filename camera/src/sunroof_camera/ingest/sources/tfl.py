"""Transport for London JamCams (~900 cams, keyless Unified API, 5-min JPEG + 10 s MP4 clip).

`/Place/Type/JamCam` returns lat/lon plus additionalProperties: available, imageUrl,
videoUrl, view ("West", "North East", ...). Cameras with available=false are skipped.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

URL = "https://api.tfl.gov.uk/Place/Type/JamCam"


def _props(place: dict) -> dict[str, str]:
    return {p["key"]: p.get("value") or "" for p in place.get("additionalProperties") or []}


class TfLJamCamAdapter:
    source: ClassVar[str] = "tfl"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for place in await get_json(http, URL):
            p = _props(place)
            img = p.get("imageUrl")
            if p.get("available") != "true" or not img:
                continue
            lat, lon = place.get("lat"), place.get("lon")
            if lat is None or lon is None:
                continue
            key = str(place["id"]).removeprefix("JamCams_")
            az = parse_heading_text(p.get("view"))
            out.append(
                Camera(
                    id=f"tfl:{key}",
                    source=self.source,
                    source_kind="jpeg",
                    name=place.get("commonName") or key,
                    lat=float(lat),
                    lon=float(lon),
                    tz="Europe/London",
                    azimuth_deg=az,
                    heading_conf="text" if az is not None else "unknown",
                    elev_min_deg=-5,
                    elev_max_deg=20,
                    image_url=img,
                    stream_url=p.get("videoUrl") or None,
                    page_url=f"https://www.tfljamcams.net/{key}",
                    refresh_s=300,
                    license="TfL Open Data licence (OGL-compatible, attribution required)",
                    attribution="Powered by TfL Open Data",
                )
            )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
