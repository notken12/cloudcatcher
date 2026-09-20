"""Travel Midwest (Illinois DOT / Gary-Chicago-Milwaukee corridor, ~530 sites, keyless GeoJSON).

`travelmidwest.com/lmiga/cameras.json` has one Point feature per site with up to four
`urls` entries, each {direction: "N"/"E"/..., url: full-size JPEG on cctv.travelmidwest.com}.
One Camera row per view; the direction letter gives the heading.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

URL = "https://travelmidwest.com/lmiga/cameras.json"


class TravelMidwestAdapter:
    source: ClassVar[str] = "travelmidwest"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        payload = await get_json(http, URL)
        out: list[Camera] = []
        for feat in payload.get("features") or []:
            p = feat.get("properties") or {}
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            sid = p.get("id")
            if not sid or len(coords) < 2:
                continue
            loc = str(p.get("description") or sid).strip()
            for v in p.get("urls") or []:
                img = v.get("url")
                if not img:
                    continue
                d = str(v.get("direction") or "").upper()
                az = parse_heading_text(d) if d not in ("", "NONE") else None
                out.append(
                    Camera(
                        id=f"travelmidwest:{sid}:{d or 'x'}",
                        source=self.source,
                        source_kind="jpeg",
                        name=f"{loc} — {d}" if az is not None else loc,
                        lat=float(coords[1]),
                        lon=float(coords[0]),
                        tz="America/Chicago",
                        azimuth_deg=az,
                        heading_conf="text" if az is not None else "unknown",
                        elev_min_deg=-5,
                        elev_max_deg=25,
                        image_url=img,
                        page_url="https://www.travelmidwest.com/lmiga/cameras.jsp",
                        refresh_s=300,
                        license="Travel Midwest / Illinois DOT public data",
                        attribution="Illinois Department of Transportation — Travel Midwest",
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
