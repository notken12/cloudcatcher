"""Iowa Environmental Mesonet webcams — Iowa TV/DOT cams with a per-frame pan `angle` and a
5-minute JPEG archive back to 2012, used by storm chasers. `webcam.geojson?network=&ts=` lists the
cameras that had a frame at that instant (with their angle then) — the historical catalog."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, fetch_image, get_json

LIVE = "https://mesonet.agron.iastate.edu/geojson/webcam.geojson"
ARCHIVE = "https://mesonet.agron.iastate.edu/json/webcam.py?ts=%Y%m%d%H%M"
# Direct 5-minute archive frame (UTC); the JSON endpoint above does not honour `ts`.
ARCHIVE_FRAME = (
    "https://mesonet.agron.iastate.edu/archive/data/%Y/%m/%d/camera/{cid}/{cid}_%Y%m%d%H%M.jpg"
)
NETWORKS = ("KCCI", "KCRG", "KELO", "ISUC", "MCFC", "IDOT")


class IEMAdapter:
    source: ClassVar[str] = "iem"

    async def catalog(self, http: httpx.AsyncClient, at: datetime | None = None) -> list[Camera]:
        """Live catalog (`at=None`) or the cameras that had a frame at `at` (replay)."""
        urls = (
            [LIVE]
            if at is None
            else [f"{LIVE}?network={n}&ts={at:%Y-%m-%dT%H:%M:00Z}" for n in NETWORKS]
        )
        seen: dict[str, Camera] = {}
        for url in urls:
            data = await get_json(http, url)
            for f in data["features"]:
                cam = self._camera(f)
                seen.setdefault(cam.id, cam)
        return list(seen.values())

    def _camera(self, f: dict) -> Camera:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        angle = p.get("angle")
        cid = p.get("cid") or f["id"]
        return Camera(
            id=f"iem:{cid}",
            source=self.source,
            source_kind="jpeg",
            name=p.get("name", cid),
            lat=lat,
            lon=lon,
            azimuth_deg=float(angle) if angle is not None else None,
            hfov_deg=55,
            elev_min_deg=-5,
            elev_max_deg=30,
            heading_conf="catalog" if angle is not None else "ptz",
            ptz=cid.startswith("KCCI") or cid.startswith("KELO"),
            sky_frac=0.5,
            night_ok=True,  # TV-station cams auto-expose at night (stars / city lights visible)
            image_url=p.get("imgurl") or p.get("url"),
            page_url=f"https://mesonet.agron.iastate.edu/current/webcam.php?cid={cid}",
            refresh_s=300,
            history_kind="api",
            history_template=ARCHIVE,
            history_depth_days=5000,
            license="IEM public data",
            attribution="Iowa Environmental Mesonet / partner TV stations",
        )

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        if ts is None:
            return await default_fetch_frame(http, cam, None)
        cid = cam.id.split(":", 1)[1]
        ts = ts.replace(minute=(ts.minute // 5) * 5, second=0, microsecond=0)
        return await fetch_image(http, cam, ts.strftime(ARCHIVE_FRAME.format(cid=cid)))
