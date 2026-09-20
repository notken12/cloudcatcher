"""CARS-platform 511 portals (Ontario, New York; same JSON shape for UT/LA/IA/... with a key).

Ontario: one record per camera with `Views[]`, each view URL is a JPEG.
New York: one record per view, `VideoUrl` = HLS, `Url` = JPEG.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

ROAD_ELEV = (-5.0, 25.0)


class Ontario511Adapter:
    source: ClassVar[str] = "cars_on"
    url = "https://511on.ca/api/v2/get/cameras?format=json"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for rec in await get_json(http, self.url):
            for v in rec.get("Views", []):
                if v.get("Status") != "Enabled":
                    continue
                desc = v.get("Description") or ""
                if "down" in desc.lower():
                    continue  # pavement view
                az = parse_heading_text(desc) or parse_heading_text(rec.get("Direction"))
                out.append(
                    Camera(
                        id=f"cars_on:{rec['Id']}:{v['Id']}",
                        source=self.source,
                        source_kind="jpeg",
                        name=f"{rec['Location']} — {desc}".strip(" —"),
                        lat=rec["Latitude"],
                        lon=rec["Longitude"],
                        azimuth_deg=az,
                        elev_min_deg=ROAD_ELEV[0],
                        elev_max_deg=ROAD_ELEV[1],
                        heading_conf="text" if az is not None else "unknown",
                        image_url=v["Url"],
                        page_url="https://511on.ca/",
                        refresh_s=300,
                        license="Ontario 511 open data",
                        attribution="Ontario Ministry of Transportation",
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)


class NewYork511Adapter:
    source: ClassVar[str] = "cars_ny"

    @property
    def url(self) -> str:
        return f"https://511ny.org/api/getcameras?format=json&key={os.environ.get('NY511_API_KEY', '')}"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for rec in await get_json(http, self.url):
            if rec.get("Disabled") or rec.get("Blocked"):
                continue
            az = parse_heading_text(rec.get("DirectionOfTravel")) or parse_heading_text(rec["Name"])
            out.append(
                Camera(
                    id=f"cars_ny:{rec['ID']}",
                    source=self.source,
                    source_kind="hls" if rec.get("VideoUrl") else "jpeg",
                    name=rec["Name"],
                    lat=rec["Latitude"],
                    lon=rec["Longitude"],
                    azimuth_deg=az,
                    elev_min_deg=ROAD_ELEV[0],
                    elev_max_deg=ROAD_ELEV[1],
                    heading_conf="text" if az is not None else "unknown",
                    image_url=rec["Url"],
                    stream_url=rec.get("VideoUrl"),
                    page_url="https://511ny.org/",
                    refresh_s=60 if rec.get("VideoUrl") else 300,
                    license="NY 511 developer terms",
                    attribution="New York State DOT / 511NY",
                )
            )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
