"""Iceland Vegagerðin road cameras — heading from Icelandic description ('séð til vesturs')."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

URL = "https://gagnaveita.vegagerdin.is/api/vefmyndavelar2014_1"


class IcelandAdapter:
    source: ClassVar[str] = "iceland"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for rec in await get_json(http, URL):
            url = rec.get("Slod")
            if not url or rec.get("Breidd") is None:
                continue
            desc = rec.get("Skyring") or rec.get("Myndavel") or ""
            az = parse_heading_text(desc)
            out.append(
                Camera(
                    id=f"iceland:{rec['Maelist_nr']}:{url.rsplit('/', 1)[-1]}",
                    source=self.source,
                    source_kind="jpeg",
                    name=desc,
                    lat=rec["Breidd"],
                    lon=rec["Lengd"],
                    azimuth_deg=az,
                    elev_min_deg=-5,
                    elev_max_deg=30,
                    heading_conf="text" if az is not None else "unknown",
                    night_ok=True,  # aurora country; many are low-light capable, health job decides
                    image_url=url,
                    page_url="https://www.vegagerdin.is/",
                    refresh_s=600,
                    license="Vegagerðin open data",
                    attribution="Vegagerðin (Icelandic Road and Coastal Administration)",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
