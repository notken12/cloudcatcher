"""Taiwan MOTC TDX road CCTV (keyless `basic/v2` endpoints, ~4.2k usable cams).

- Highway (THB, provincial highways): `VideoImageURL` is a plain JPEG snapshot.
- Freeway (NFB, national freeways): only `VideoStreamURL`, an MJPEG multipart stream;
  fetch_frame reads the stream until the first JPEG end-of-image marker.
City datasets (Taipei, Kaohsiung, ...) are HTML players / HLS only and are skipped.
Records carry RoadDirection ("N", "SE", ...) -> text heading.
The keyless tier answers 401 to non-browser User-Agents, so the catalog request sends a
`Mozilla/5.0 (compatible; ...)` UA that still identifies this project.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json, parse_heading_text

BASE = "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/CCTV"
DATASETS = ("Highway", "Freeway")
MJPEG_HOST = "cctvn.freeway.gov.tw"
MAX_STREAM_BYTES = 600_000
UA = "Mozilla/5.0 (compatible; sunroof-camera/0.1; +https://github.com/notken12/sunroof)"


def _name(c: dict) -> str:
    road = c.get("RoadName") or c.get("RoadID") or ""
    desc = c.get("SurveillanceDescription") or ""
    sec = c.get("RoadSection") or {}
    seg = " - ".join(s for s in (sec.get("Start"), sec.get("End")) if s)
    mile = c.get("LocationMile") or ""
    parts = [p for p in (road, desc or seg, mile) if p]
    return " ".join(parts) or str(c.get("CCTVID"))


def first_mjpeg_frame(buf: bytes) -> bytes | None:
    start = buf.find(b"\xff\xd8")
    end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
    return buf[start : end + 2] if end > start >= 0 else None


class TaiwanTDXAdapter:
    source: ClassVar[str] = "tw_tdx"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for ds in DATASETS:
            payload = await get_json(
                http, f"{BASE}/{ds}", params={"$format": "JSON"}, headers={"User-Agent": UA}
            )
            for c in payload.get("CCTVs") or []:
                lat, lon = c.get("PositionLat"), c.get("PositionLon")
                cid = c.get("CCTVID")
                if not cid or lat is None or lon is None:
                    continue
                img = c.get("VideoImageURL")
                stream = c.get("VideoStreamURL") or ""
                mjpeg = MJPEG_HOST in stream and not img
                if not (img or mjpeg):
                    continue
                az = parse_heading_text(c.get("RoadDirection"))
                out.append(
                    Camera(
                        id=f"tw_tdx:{cid}",
                        source=self.source,
                        source_kind="jpeg",
                        name=_name(c),
                        lat=float(lat),
                        lon=float(lon),
                        tz="Asia/Taipei",
                        azimuth_deg=az,
                        elev_min_deg=-5,
                        elev_max_deg=20,
                        heading_conf="text" if az is not None else "unknown",
                        image_url=img or stream,
                        stream_url=stream if mjpeg else None,
                        page_url="https://tdx.transportdata.tw/",
                        refresh_s=60,
                        license="Taiwan Open Government Data License v1.0",
                        attribution="MOTC Taiwan (TDX) / THB / Freeway Bureau",
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        if not cam.image_url or MJPEG_HOST not in cam.image_url:
            return await default_fetch_frame(http, cam, None)
        buf = b""
        try:
            async with http.stream("GET", cam.image_url) as r:
                if r.status_code != 200:
                    return None
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if b"\xff\xd9" in buf or len(buf) > MAX_STREAM_BYTES:
                        break
        except httpx.HTTPError:
            return None
        jpg = first_mjpeg_frame(buf)
        if not jpg:
            return None
        return Frame(cam.id, None, cam.image_url, jpg, "image/jpeg")
