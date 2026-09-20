"""PhenoCam network — ~1,000 research sites (mostly N. America), fixed cameras with compass
orientation, latest JPEG at /data/latest/<site>.jpg and archives back to 2000s
(/data/archive/<site>/YYYY/MM/<site>_YYYY_MM_DD_HHMMSS.jpg, 30-min cadence)."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

API = "https://phenocam.nau.edu/api/cameras/?format=json&limit=500"
LATEST = "https://phenocam.nau.edu/data/latest/{site}.jpg"
ARCHIVE = "https://phenocam.nau.edu/data/archive/{site}/%Y/%m/{site}_%Y_%m_%d_%H%M%S.jpg"


class PhenoCamAdapter:
    source: ClassVar[str] = "phenocam"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        url: str | None = API
        today = datetime.utcnow().date()
        while url:
            page = await get_json(http, url)
            for rec in page["results"]:
                if not rec.get("active") or rec.get("Lat") is None:
                    continue
                last = rec.get("date_last")
                if last and (today - datetime.fromisoformat(last).date()).days > 30:
                    continue
                meta = rec.get("sitemetadata") or {}
                az = parse_heading_text(meta.get("camera_orientation"))
                first = rec.get("date_first")
                depth = (today - datetime.fromisoformat(first).date()).days if first else 0
                site = rec["Sitename"]
                out.append(
                    Camera(
                        id=f"phenocam:{site}",
                        source=self.source,
                        source_kind="jpeg",
                        name=meta.get("site_description") or site,
                        lat=rec["Lat"],
                        lon=rec["Lon"],
                        alt_m=rec.get("Elev"),
                        azimuth_deg=az,
                        hfov_deg=50,
                        elev_min_deg=-5,
                        elev_max_deg=20,
                        heading_conf="text" if az is not None else "unknown",
                        sky_frac=0.3,
                        night_ok=False,  # "infrared" = NIR channel for vegetation, not low-light
                        image_url=LATEST.format(site=site),
                        page_url=f"https://phenocam.nau.edu/webcam/sites/{site}/",
                        refresh_s=1800,
                        history_kind="url_template",
                        history_template=ARCHIVE.format(site=site),
                        history_depth_days=depth,
                        license="CC BY 4.0 (PhenoCam)",
                        attribution="PhenoCam Network, Northern Arizona University",
                    )
                )
            url = page.get("next")
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        if ts is not None:
            ts = ts.replace(minute=(ts.minute // 30) * 30, second=5, microsecond=0)
        return await default_fetch_frame(http, cam, ts)
