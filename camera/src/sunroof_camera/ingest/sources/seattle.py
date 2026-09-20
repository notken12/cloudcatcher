"""Seattle Travelers map cameras (~650 cams: 390 SDOT + 260 WSDOT Puget Sound, keyless).

`web.seattle.gov/Travelers/api/Map/Data?zoomId=13&type=2` returns clustered features,
each with a `Cameras` list of {Id, Description, ImageUrl (bare filename), Type}. The
filename resolves against a per-type image host; WSDOT's `images.wsdot.wa.gov/nw/` is
the same open JPEG host the keyed WSDOT API points to. Heading is unknown.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json

URL = "https://web.seattle.gov/Travelers/api/Map/Data?zoomId=13&type=2"
IMAGE_BASE = {
    "sdot": "https://www.seattle.gov/trafficcams/images/",
    "wsdot": "https://images.wsdot.wa.gov/nw/",
}
ATTRIBUTION = {
    "sdot": "Seattle Department of Transportation",
    "wsdot": "Washington State Department of Transportation",
}


class SeattleTravelersAdapter:
    source: ClassVar[str] = "seattle"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        payload = await get_json(http, URL)
        out: list[Camera] = []
        seen: set[str] = set()
        for feat in payload.get("Features") or []:
            pt = feat.get("PointCoordinate") or []
            if len(pt) < 2:
                continue
            for c in feat.get("Cameras") or []:
                kind = str(c.get("Type") or "").lower()
                base = IMAGE_BASE.get(kind)
                fn = c.get("ImageUrl")
                cid = c.get("Id")
                if not base or not fn or not cid or cid in seen:
                    continue
                seen.add(cid)
                out.append(
                    Camera(
                        id=f"seattle:{kind}:{cid}",
                        source=self.source,
                        source_kind="jpeg",
                        name=str(c.get("Description") or cid).strip(),
                        lat=float(pt[0]),
                        lon=float(pt[1]),
                        tz="America/Los_Angeles",
                        elev_min_deg=-5,
                        elev_max_deg=25,
                        heading_conf="unknown",
                        image_url=base + fn,
                        page_url="https://web.seattle.gov/Travelers/",
                        refresh_s=120 if kind == "sdot" else 90,
                        license=f"{ATTRIBUTION[kind]} public traffic cameras",
                        attribution=ATTRIBUTION[kind],
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
