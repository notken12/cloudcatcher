"""ALERTCalifornia / firestorm mirror — ~1,100 ridge-top PTZ cameras with pan angle, many IR at night."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json

URL = "https://raw.githubusercontent.com/Deasus/firestorm-cameras/main/data/cameras.json"


class AlertCaliforniaAdapter:
    source: ClassVar[str] = "alertca"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        data = await get_json(http, URL)
        recs = data if isinstance(data, list) else data.get("cameras", [])
        out: list[Camera] = []
        for rec in recs:
            if rec.get("lat") is None or not rec.get("img"):
                continue
            pan = rec.get("pan")
            name = rec.get("name", "")
            out.append(
                Camera(
                    id=f"alertca:{rec.get('src') or name}",
                    source=self.source,
                    source_kind="jpeg",
                    name=name,
                    lat=rec["lat"],
                    lon=rec["lng"],
                    alt_m=rec.get("elev"),
                    azimuth_deg=float(pan) if pan is not None else None,
                    hfov_deg=float(rec.get("fov") or 60),
                    elev_min_deg=-10,
                    elev_max_deg=20,
                    heading_conf="catalog" if pan is not None else "ptz",
                    ptz=True,
                    night_ok="ir" in name.lower() or "-ir" in str(rec.get("src", "")).lower(),
                    image_url=rec["img"],
                    page_url="https://cameras.alertcalifornia.org/",
                    refresh_s=60,
                    license="ALERTCalifornia / UCSD; non-commercial, attribution required",
                    attribution="ALERTCalifornia, UC San Diego",
                )
            )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
