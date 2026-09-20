"""Windy Webcams API v3 — ~70k worldwide cams, embeddable player with day/month/year history.

Needs WINDY_API_KEY in the environment (free tier). The free tier caps `offset` at 1000, so
we page per country (limit 50) and, for countries over the cap, additionally per sky-relevant
category (the `categories` filter is AND, so one category per pass). Titles carry a heading
('Parma › North-west', 'Vatnsskarð (looking to North)') parsed as `text` confidence. Images are only served through
imgproxy previews (400 px) unless the key has the paid tier; the player embed is the real
frontend asset.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, fetch_image, get_json, parse_heading_text

API = "https://api.windy.com/webcams/api/v3/webcams"
INCLUDE = "location,images,urls,player,categories"
LIMIT = 50
MAX_OFFSET = 1000
SKY_CATEGORIES = {"meteo", "landscape", "mountain", "beach", "port", "lake", "coast", "observatory"}
SKIP_CATEGORIES = {"indoor", "traffic", "sportArea", "square"}

COUNTRIES = (
    "US CA MX BR AR CL PE CO GB IE FR DE AT CH IT ES PT NL BE DK NO SE FI IS PL CZ SK HU SI HR "
    "GR TR RU UA RO BG RS EE LV LT JP KR CN TW HK TH VN ID MY PH AU NZ ZA KE TZ MA EG IL AE IN "
    "NP PK LK GL FO"
).split()


def _api_key() -> str:
    key = os.environ.get("WINDY_API_KEY")
    if not key:
        raise RuntimeError("WINDY_API_KEY not set")
    return key


def _to_camera(w: dict) -> Camera | None:
    loc = w.get("location") or {}
    if loc.get("latitude") is None or w.get("status") != "active":
        return None
    cats = {c["id"] for c in w.get("categories", [])}
    if cats & SKIP_CATEGORIES and not (cats & SKY_CATEGORIES):
        return None
    title = w.get("title", "")
    az = parse_heading_text(title)
    images = w.get("images") or {}
    preview = (images.get("current") or {}).get("preview")
    player = w.get("player") or {}
    urls = w.get("urls") or {}
    return Camera(
        id=f"windy:{w['webcamId']}",
        source="windy",
        source_kind="jpeg",
        name=title,
        lat=loc["latitude"],
        lon=loc["longitude"],
        azimuth_deg=az,
        hfov_deg=70,
        elev_min_deg=-5,
        elev_max_deg=30,
        heading_conf="text" if az is not None else "unknown",
        sky_frac=0.5 if cats & SKY_CATEGORIES else None,
        image_url=preview,
        embed_url=player.get("day"),
        page_url=urls.get("detail"),
        refresh_s=600,
        history_kind="api",
        history_template=player.get("year"),
        history_depth_days=365,
        license="Windy Webcams API terms; embed player + link required",
        attribution="windy.com webcams",
    )


class WindyAdapter:
    source: ClassVar[str] = "windy"

    async def _pages(
        self, http: httpx.AsyncClient, sem: asyncio.Semaphore, **filters: str
    ) -> tuple[int, list[dict]]:
        out: list[dict] = []
        offset = 0
        total = 0
        while offset <= MAX_OFFSET:
            async with sem:
                page = await get_json(
                    http,
                    API,
                    params={"limit": LIMIT, "offset": offset, "include": INCLUDE, **filters},
                    headers={"x-windy-api-key": _api_key()},
                )
            cams = page.get("webcams", [])
            total = page.get("total", 0)
            out.extend(cams)
            if len(cams) < LIMIT or offset + LIMIT >= total:
                break
            offset += LIMIT
        return total, out

    async def _country(
        self, http: httpx.AsyncClient, cc: str, sem: asyncio.Semaphore
    ) -> list[dict]:
        total, out = await self._pages(http, sem, countries=cc)
        if total > MAX_OFFSET + LIMIT:
            for cat in sorted(SKY_CATEGORIES):
                _, more = await self._pages(http, sem, countries=cc, categories=cat)
                out.extend(more)
        return out

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        sem = asyncio.Semaphore(3)
        pages = await asyncio.gather(
            *(self._country(http, cc, sem) for cc in COUNTRIES), return_exceptions=True
        )
        out: list[Camera] = []
        seen: set[str] = set()
        for recs in pages:
            if isinstance(recs, Exception):
                continue
            for w in recs:
                cam = _to_camera(w)
                if cam and cam.id not in seen:
                    seen.add(cam.id)
                    out.append(cam)
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        if ts is not None or not cam.image_url:
            return None
        return await fetch_image(http, cam, cam.image_url)
