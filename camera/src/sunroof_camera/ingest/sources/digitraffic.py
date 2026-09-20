"""Finland Digitraffic weather cameras — 800+ stations, presets as direct JPEGs, 24 h history API.

Preset direction codes: 1/2 = along the road (heading unknown without OSM), 9 = pavement (skipped).
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json

STATIONS = "https://tie.digitraffic.fi/api/weathercam/v1/stations"
IMAGE = "https://weathercam.digitraffic.fi/{preset}.jpg"
HISTORY = "https://tie.digitraffic.fi/api/weathercam/v1/stations/{station}/history"
HEADERS = {"Digitraffic-User": "sunroof-hackmit/0.1"}


class DigitrafficAdapter:
    source: ClassVar[str] = "digitraffic"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        data = await get_json(http, STATIONS, headers=HEADERS)
        out: list[Camera] = []
        for f in data["features"]:
            p = f["properties"]
            if p.get("collectionStatus") != "GATHERING":
                continue
            lon, lat = f["geometry"]["coordinates"][:2]
            for pre in p.get("presets", []):
                pid = pre["id"]
                if not pre.get("inCollection") or pid.endswith("09"):
                    continue
                out.append(
                    Camera(
                        id=f"digitraffic:{pid}",
                        source=self.source,
                        source_kind="jpeg",
                        name=f"{p['name']} {pid[-2:]}",
                        lat=lat,
                        lon=lon,
                        azimuth_deg=None,
                        elev_min_deg=-5,
                        elev_max_deg=25,
                        heading_conf="unknown",
                        night_ok=True,  # IR illuminated road cams; verified by health job
                        image_url=IMAGE.format(preset=pid),
                        page_url="https://www.digitraffic.fi/en/road-traffic/",
                        refresh_s=600,
                        history_kind="api",
                        history_template=HISTORY.format(station=p["id"]),
                        history_depth_days=1,
                        license="CC BY 4.0 Fintraffic / digitraffic.fi",
                        attribution="Fintraffic / digitraffic.fi, CC BY 4.0",
                    )
                )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
