"""Panomax — Alpine/European gigapixel panoramas with numeric orientation + nightVision flag.

`zeroDirection` is the compass bearing of the left image edge, `viewAngle` the sweep;
centre = zeroDirection + viewAngle/2.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, fetch_image, get_json

CATALOG = "https://api.panomax.com/1.0/maps/panomaxweb"
RECENT_IMG = "https://panodata.panomax.com/cams/{id}/recent_small.jpg"
RECENT_META = "https://api.panomax.com/1.0/cams/{id}/images/recent"


class PanomaxAdapter:
    source: ClassVar[str] = "panomax"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        data = await get_json(http, CATALOG)
        out: list[Camera] = []
        for inst in data["instances"].values():
            cam = inst.get("cam") or {}
            if cam.get("latitude") is None:
                continue
            sweep = float(cam.get("viewAngle") or 180)
            zero = cam.get("zeroDirection")
            az = (float(zero) + sweep / 2) % 360 if zero is not None else None
            out.append(
                Camera(
                    id=f"panomax:{cam['id']}",
                    source=self.source,
                    source_kind="jpeg",
                    name=inst.get("name") or cam.get("name", ""),
                    lat=cam["latitude"],
                    lon=cam["longitude"],
                    azimuth_deg=az,
                    hfov_deg=sweep,
                    elev_min_deg=-10,
                    elev_max_deg=20,
                    heading_conf="catalog" if az is not None else "unknown",
                    night_ok=bool(cam.get("nightVision")),
                    image_url=RECENT_IMG.format(id=cam["id"]),
                    page_url=f"https://www.panomax.com/{inst.get('slug', '')}",
                    refresh_s=600,
                    history_kind="api",
                    history_template=RECENT_META.format(id=cam["id"]),
                    history_depth_days=1,
                    license="Panomax terms; attribution + link required",
                    attribution="Panomax",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await fetch_image(http, cam, cam.image_url or "")
