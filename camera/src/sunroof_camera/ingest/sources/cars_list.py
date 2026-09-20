"""CARS-platform 511 portals via the keyless web-list endpoint.

`/api/v2/get/cameras` needs a per-portal key, but the map UI's
`/List/GetData/Cameras?query={"columns":[],"start":N,"length":100}` is open and
returns the same sites (100 per page). Frames come from `/map/Cctv/{imageId}` on the
same host. One adapter class per portal, generated from PORTALS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import ClassVar
from urllib.parse import quote

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

log = logging.getLogger(__name__)

ROAD_ELEV = (-5.0, 25.0)
PAGE = 100
_WKT = re.compile(r"POINT \(([-\d.]+) ([-\d.]+)\)")

# Snapshot body served for video-only sites ("snapshot unavailable" PNG); same bytes on
# every portal. Georgia (511ga.org, 4.3k sites) is ~85% this + auth-walled HLS, so it is
# left out of PORTALS until we have a video path.
PLACEHOLDER_SIZE = 15136

# source suffix -> (host, attribution). ON/NY have dedicated API adapters in cars.py.
PORTALS: dict[str, tuple[str, str]] = {
    "fl": ("fl511.com", "Florida DOT / FL511"),
    "ut": ("udottraffic.utah.gov", "Utah DOT"),
    "pa": ("www.511pa.com", "PennDOT / 511PA"),
    "nc": ("drivenc.gov", "NCDOT / DriveNC"),
    "az": ("az511.gov", "Arizona DOT / AZ511"),
    "nv": ("nvroads.com", "Nevada DOT"),
    "wi": ("511wi.gov", "Wisconsin DOT / 511WI"),
    "id": ("511.idaho.gov", "Idaho Transportation Department"),
    "ne6": ("newengland511.org", "New England 511 (VT/NH/ME)"),
    "ct": ("ctroads.org", "Connecticut DOT / CTroads"),
    "la": ("511la.org", "Louisiana DOTD / 511LA"),
    "ak": ("511.alaska.gov", "Alaska DOT&PF / 511"),
    "ab": ("511.alberta.ca", "Alberta 511"),
    "ns": ("511.novascotia.ca", "Nova Scotia 511"),
    "nb": ("511.gnb.ca", "New Brunswick 511"),
    "nl": ("511nl.ca", "Newfoundland and Labrador 511"),
    "yt": ("511yukon.ca", "Yukon 511"),
}


def _list_url(host: str, start: int) -> str:
    q = json.dumps({"columns": [], "start": start, "length": PAGE}, separators=(",", ":"))
    return f"https://{host}/List/GetData/Cameras?query={quote(q)}"


def parse_wkt_point(wkt: str | None) -> tuple[float, float] | None:
    m = _WKT.match(wkt or "")
    return (float(m.group(2)), float(m.group(1))) if m else None


def _is_pavement(desc: str) -> bool:
    d = desc.lower()
    return "looking down" in d or "pavement" in d or "road surface" in d


class CarsListAdapter:
    source: ClassVar[str] = "cars_list"
    host: ClassVar[str] = ""
    attribution: ClassVar[str] = ""

    async def _page(self, http: httpx.AsyncClient, start: int) -> list[dict]:
        """Sequential + retried: the portals 500 intermittently under parallel paging."""
        for attempt in range(3):
            try:
                return (await get_json(http, _list_url(self.host, start)))["data"]
            except httpx.HTTPError as e:
                err = e
                await asyncio.sleep(1.5 * (attempt + 1))
        log.warning("%s: page start=%d failed after retries: %s", self.source, start, err)
        return []

    async def _pages(self, http: httpx.AsyncClient) -> list[dict]:
        first = await get_json(http, _list_url(self.host, 0))
        rows = list(first["data"])
        for start in range(PAGE, int(first.get("recordsTotal") or 0), PAGE):
            rows.extend(await self._page(http, start))
        return rows

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for site in await self._pages(http):
            pt = parse_wkt_point(
                (site.get("latLng") or {}).get("geography", {}).get("wellKnownText")
            )
            if pt is None:
                continue
            lat, lon = pt
            loc = site.get("location") or ""
            site_az = parse_heading_text(site.get("direction"))
            for im in site.get("images") or []:
                if im.get("disabled") or im.get("blocked") or not im.get("imageUrl"):
                    continue
                desc = im.get("description") or ""
                if _is_pavement(desc):
                    continue
                az = parse_heading_text(desc) if desc != loc else None
                az = az if az is not None else site_az
                video = im.get("videoUrl") if not im.get("isVideoAuthRequired") else None
                out.append(
                    Camera(
                        id=f"{self.source}:{site['id']}:{im['id']}",
                        source=self.source,
                        source_kind="jpeg",
                        name=loc if not desc or desc == loc else f"{loc} — {desc}",
                        lat=lat,
                        lon=lon,
                        azimuth_deg=az,
                        elev_min_deg=ROAD_ELEV[0],
                        elev_max_deg=ROAD_ELEV[1],
                        heading_conf="text" if az is not None else "unknown",
                        image_url=f"https://{self.host}{im['imageUrl']}",
                        stream_url=video or None,
                        page_url=f"https://{self.host}/",
                        refresh_s=300,
                        license=f"{self.attribution} 511 terms of use",
                        attribution=self.attribution,
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        fr = await default_fetch_frame(http, cam, None)
        if fr and len(fr.content) == PLACEHOLDER_SIZE:
            return None
        return fr


def make_adapters() -> list[type[CarsListAdapter]]:
    return [
        type(
            f"Cars{suffix.upper()}Adapter",
            (CarsListAdapter,),
            {"source": f"cars_{suffix}", "host": host, "attribution": attr},
        )
        for suffix, (host, attr) in PORTALS.items()
    ]


CARS_LIST_ADAPTERS = make_adapters()
