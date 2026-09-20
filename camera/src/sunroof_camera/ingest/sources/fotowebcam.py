"""foto-webcam.eu — ~300 Alpine HD cams; the homepage embeds a JSON `metadata` blob with
direction (deg), sector (hfov deg), elevation, captureInterval, offline flag and a full
per-10-minute archive at /webcam/<id>/YYYY/MM/DD/HHMM_la.jpg (years deep)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame

HOME = "https://www.foto-webcam.eu/"
_META = re.compile(r"var metadata\s*=\s*new Object\((\{.*?\})\);", re.S)


def parse_metadata(html: str) -> list[dict]:
    m = _META.search(html)
    if not m:
        return []
    return json.loads(m.group(1)).get("cams", [])


class FotoWebcamAdapter:
    source: ClassVar[str] = "fotowebcam"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        r = await http.get(HOME)
        r.raise_for_status()
        out: list[Camera] = []
        for c in parse_metadata(r.text):
            if c.get("offline") or c.get("hidden") or c.get("latitude") is None:
                continue
            slug = c["id"]
            direction = c.get("direction")
            out.append(
                Camera(
                    id=f"fotowebcam:{slug}",
                    source=self.source,
                    source_kind="jpeg",
                    name=c.get("title") or c.get("name", slug),
                    lat=c["latitude"],
                    lon=c["longitude"],
                    alt_m=c.get("elevation"),
                    azimuth_deg=float(direction) if direction is not None else None,
                    hfov_deg=float(c.get("sector") or 60),
                    elev_min_deg=-10,
                    elev_max_deg=25,
                    heading_conf="catalog" if direction is not None else "unknown",
                    night_ok=True,  # long-exposure DSLR-class cams; stars/aurora visible
                    image_url=f"https://www.foto-webcam.eu/webcam/{slug}/current/1200.jpg",
                    page_url=c.get("link") or f"https://www.foto-webcam.eu/webcam/{slug}/",
                    refresh_s=int(c.get("captureInterval") or 600),
                    history_kind="url_template",
                    history_template=f"https://www.foto-webcam.eu/webcam/{slug}/%Y/%m/%d/%H%M_la.jpg",
                    history_depth_days=3650,
                    license="foto-webcam.eu; embedding with link allowed, no redistribution",
                    attribution="foto-webcam.eu",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        if ts is not None:
            ts = ts.replace(minute=(ts.minute // 10) * 10, second=0, microsecond=0)
        return await default_fetch_frame(http, cam, ts)
