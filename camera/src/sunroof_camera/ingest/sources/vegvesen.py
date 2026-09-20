"""Statens vegvesen (Norway) road weather + webcamera sites (~890 cams, keyless).

The vegvesen.no/trafikk map calls `road-weather-and-view.atlas.vegvesen.no`; it needs
`X-System-ID` and a vendor Accept header but no key. Each measurement site carries
altitude, road, county and 1-3 cameras with a stable JPEG URL, optional HLS, `status`
("OK"/"OUT_OF_SERVICE") and `orientationDescription` (the place the camera points at,
not a compass direction -> heading stays unknown unless it parses as one).
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

URL = "https://road-weather-and-view.atlas.vegvesen.no/weather-information/measurement-sites"
HEADERS = {
    "X-System-ID": "vvtraf",
    "Accept": "application/vnd.svv.v1+json; charset=utf-8",
}


class VegvesenAdapter:
    source: ClassVar[str] = "no_vegvesen"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        payload = await get_json(http, URL, headers=HEADERS)
        out: list[Camera] = []
        for site in payload.get("measurementSites") or []:
            loc = site.get("location") or {}
            coords = (loc.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            alt = loc.get("heightAboveSeaLevel")
            road = (loc.get("road") or {}).get("number") or ""
            site_name = site.get("name") or site.get("id")
            for c in site.get("cameras") or []:
                img = c.get("stillImageUrl")
                if not img or c.get("status") != "OK":
                    continue
                orient = c.get("orientationDescription") or ""
                az = parse_heading_text(orient)
                name = f"{road} {site_name}".strip()
                if orient:
                    name = f"{name} ({orient})"
                out.append(
                    Camera(
                        id=f"no_vegvesen:{c['id']}",
                        source=self.source,
                        source_kind="jpeg",
                        name=name,
                        lat=lat,
                        lon=lon,
                        alt_m=float(alt) if alt is not None else None,
                        tz="Europe/Oslo",
                        azimuth_deg=az,
                        heading_conf="text" if az is not None else "unknown",
                        elev_min_deg=-5,
                        elev_max_deg=25,
                        image_url=img,
                        stream_url=c.get("videoUrl") or None,
                        page_url="https://www.vegvesen.no/trafikk/hvaskjer?layers=ctv",
                        refresh_s=300,
                        license="NLOD 2.0 (Norwegian Licence for Open Government Data)",
                        attribution="Statens vegvesen",
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
