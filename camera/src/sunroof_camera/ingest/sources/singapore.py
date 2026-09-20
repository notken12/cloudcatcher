"""Singapore LTA traffic images via data.gov.sg (~90 cams, 1920x1080, ~1-min refresh).

Image URLs are per-capture (uuid path), so `image_url` is left empty and `fetch_frame`
re-queries the API; `?date_time=` gives any past capture (history_kind=api).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar
from zoneinfo import ZoneInfo

import httpx

from ...schema import Camera
from ..base import Frame, fetch_image, get_json

URL = "https://api.data.gov.sg/v1/transport/traffic-images"
TZ = "Asia/Singapore"


def _cameras(payload: dict) -> list[dict]:
    items = payload.get("items") or []
    return items[0].get("cameras", []) if items else []


class SingaporeLTAAdapter:
    source: ClassVar[str] = "sg_lta"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        # A single call only returns the cameras that uploaded in that minute; union the
        # last 15 minutes to see the whole network.
        now = datetime.now(ZoneInfo(TZ))
        seen: dict[str, dict] = {}
        for back in range(0, 15, 3):
            when = (now - timedelta(minutes=back)).strftime("%Y-%m-%dT%H:%M:%S")
            for c in _cameras(await get_json(http, URL, params={"date_time": when})):
                seen.setdefault(str(c.get("camera_id")), c)
        out: list[Camera] = []
        for c in seen.values():
            loc = c.get("location") or {}
            if loc.get("latitude") is None or not c.get("camera_id"):
                continue
            out.append(
                Camera(
                    id=f"sg_lta:{c['camera_id']}",
                    source=self.source,
                    source_kind="jpeg",
                    name=f"LTA camera {c['camera_id']}",
                    lat=loc["latitude"],
                    lon=loc["longitude"],
                    tz=TZ,
                    elev_min_deg=-5,
                    elev_max_deg=25,
                    heading_conf="unknown",
                    page_url="https://data.gov.sg/datasets?query=traffic+images",
                    refresh_s=60,
                    history_kind="api",
                    history_template=URL + "?date_time=%Y-%m-%dT%H:%M:%S",
                    history_depth_days=365,
                    license="Singapore Open Data Licence v1.0",
                    attribution="Land Transport Authority (LTA) via data.gov.sg",
                )
            )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        params = (
            {"date_time": ts.astimezone(ZoneInfo(TZ)).strftime("%Y-%m-%dT%H:%M:%S")} if ts else None
        )
        cam_id = cam.id.split(":", 1)[1]
        for c in _cameras(await get_json(http, URL, params=params)):
            if str(c.get("camera_id")) == cam_id and c.get("image"):
                return await fetch_image(http, cam, c["image"])
        return None
