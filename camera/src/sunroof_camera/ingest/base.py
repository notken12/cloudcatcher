"""Adapter protocol + shared HTTP client (plan §2)."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Protocol

import httpx

from ..schema import Camera

USER_AGENT = (
    "sunroof-camera/0.1 (HackMIT weather-watching app; contact via github.com/notken12/sunroof)"
)

COMPASS_8 = {
    "N": 0,
    "NE": 45,
    "E": 90,
    "SE": 135,
    "S": 180,
    "SW": 225,
    "W": 270,
    "NW": 315,
    "NNE": 22.5,
    "ENE": 67.5,
    "ESE": 112.5,
    "SSE": 157.5,
    "SSW": 202.5,
    "WSW": 247.5,
    "WNW": 292.5,
    "NNW": 337.5,
}
_WORDS = {
    "north": "N",
    "south": "S",
    "east": "E",
    "west": "W",
    # de / is / fi / sv
    "nord": "N",
    "süd": "S",
    "sued": "S",
    "ost": "E",
    "west_": "W",
    "norður": "N",
    "suður": "S",
    "austur": "E",
    "vestur": "W",
    "norðurs": "N",
    "suðurs": "S",
    "austurs": "E",
    "vesturs": "W",
    "pohjoinen": "N",
    "etelä": "S",
    "itä": "E",
    "länsi": "W",
    "norr": "N",
    "söder": "S",
    "öster": "E",
    "väster": "W",
}


def parse_heading_text(text: str | None) -> float | None:
    """'NB', 'Looking West', 'séð til vesturs', 'Richtung Süd' -> azimuth deg, or None."""
    if not text:
        return None
    t = text.lower()
    m = re.search(r"\b(nne|ene|ese|sse|ssw|wsw|wnw|nnw|ne|se|sw|nw|n|e|s|w)\b", t)
    if m:
        return COMPASS_8[m.group(1).upper()]
    m = re.search(r"\b([nsew])b\b", t)  # NB/SB/EB/WB (bound)
    if m:
        return COMPASS_8[m.group(1).upper()]
    m = re.search(r"\b(north|south)[ -]?(east|west)\b", t)  # "North East", "south-west"
    if m:
        return COMPASS_8[m.group(1)[0].upper() + m.group(2)[0].upper()]
    for word, key in _WORDS.items():
        if word.rstrip("_") in t:
            return COMPASS_8[key]
    return None


@dataclass
class Frame:
    camera_id: str
    ts: datetime | None
    url: str
    content: bytes
    content_type: str

    @property
    def sha1(self) -> str:
        return hashlib.sha1(self.content).hexdigest()

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/") and len(self.content) > 3000


class Adapter(Protocol):
    source: ClassVar[str]

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]: ...

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None: ...


class RateLimiter:
    """Per-host token bucket; `rps` requests per second."""

    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps
        self._last: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, host: str) -> None:
        async with self._lock:
            now = time.monotonic()
            nxt = self._last.get(host, 0.0) + self.min_interval
            delay = max(0.0, nxt - now)
            self._last[host] = max(now, nxt)
        if delay:
            await asyncio.sleep(delay)


def make_client(headers: dict[str, str] | None = None, timeout: float = 30.0) -> httpx.AsyncClient:
    h = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    if headers:
        h.update(headers)
    return httpx.AsyncClient(headers=h, timeout=timeout, follow_redirects=True, http2=True)


async def get_json(http: httpx.AsyncClient, url: str, **kw: Any) -> Any:
    r = await http.get(url, **kw)
    r.raise_for_status()
    return r.json()


async def fetch_image(http: httpx.AsyncClient, cam: Camera, url: str) -> Frame | None:
    try:
        r = await http.get(url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    ts = None
    lm = r.headers.get("last-modified")
    if lm:
        try:
            from email.utils import parsedate_to_datetime

            ts = parsedate_to_datetime(lm)
        except (TypeError, ValueError):
            ts = None
    return Frame(cam.id, ts, url, r.content, r.headers.get("content-type", ""))


async def default_fetch_frame(
    http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
) -> Frame | None:
    """Works for any camera with image_url (current) or history_template (past)."""
    if ts is not None:
        if not cam.history_template:
            return None
        return await fetch_image(http, cam, ts.strftime(cam.history_template))
    if not cam.image_url:
        return None
    return await fetch_image(http, cam, cam.image_url)
