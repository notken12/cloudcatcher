"""Caltrans CWWP2 — 12 districts, JSON per district, JPEG + HLS + 12 previous frames."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

DISTRICTS = range(1, 13)
URL = "https://cwwp2.dot.ca.gov/data/d{d}/cctv/cctvStatusD{d:02d}.json"


def _float(s: str | None) -> float | None:
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


def _previous_template(current_url: str) -> str:
    """.../image/<slug>/<slug>.jpg -> .../image/<slug>/previous/<slug>-{n}.jpg, n in 1..12."""
    base, fname = current_url.rsplit("/", 1)
    slug = fname.removesuffix(".jpg")
    return f"{base}/previous/{slug}-{{n}}.jpg"


class CaltransAdapter:
    source: ClassVar[str] = "caltrans"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        pages = await asyncio.gather(
            *(get_json(http, URL.format(d=d)) for d in DISTRICTS), return_exceptions=True
        )
        out: list[Camera] = []
        for page in pages:
            if isinstance(page, Exception):
                continue
            for rec in page.get("data", []):
                c = rec["cctv"]
                if c.get("inService") != "true":
                    continue
                loc, img = c["location"], c["imageData"]
                cur = img.get("static", {}).get("currentImageURL")
                if not cur:
                    continue
                direction = loc.get("direction") or ""
                az = parse_heading_text(direction)
                freq_min = _float(img["static"].get("currentImageUpdateFrequency")) or 15
                out.append(
                    Camera(
                        id=f"caltrans:d{loc['district']}:{c['index']}",
                        source=self.source,
                        source_kind="jpeg",
                        name=f"{loc['locationName']} ({loc.get('nearbyPlace', '')})".strip(),
                        lat=float(loc["latitude"]),
                        lon=float(loc["longitude"]),
                        alt_m=_float(loc.get("elevation")),
                        azimuth_deg=az,
                        hfov_deg=60,
                        elev_min_deg=-5,
                        elev_max_deg=25,
                        heading_conf="text" if az is not None else "ptz",
                        ptz=az is None,
                        image_url=cur,
                        stream_url=img.get("streamingVideoURL") or None,
                        page_url="https://cwwp2.dot.ca.gov/vm/iframemap.htm",
                        refresh_s=int(freq_min * 60),
                        history_kind="last_n",
                        history_template=_previous_template(cur),
                        history_depth_days=1,
                        license="Caltrans public data",
                        attribution="Caltrans",
                    )
                )
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
