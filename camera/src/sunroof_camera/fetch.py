"""Get one (or a burst of) frames for a catalog row (plan §4, stage ②a)."""

from __future__ import annotations

import asyncio
import math
import shutil
from datetime import datetime, timezone

import httpx
import pandas as pd

from .ingest.base import Adapter, Frame, default_fetch_frame
from .ingest.registry import ADAPTERS, OWNS_FETCH
from .schema import Camera

_ADAPTER_CACHE: dict[str, Adapter | None] = {}


def row_to_camera(row: pd.Series) -> Camera:
    d = {}
    for k, v in row.items():
        if k not in Camera.model_fields:
            continue
        if v is pd.NaT or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
            v = None
        elif isinstance(v, pd.Timestamp):
            v = v.to_pydatetime()
        d[k] = v
    return Camera(**d)


def _adapter(source: str) -> Adapter | None:
    if source not in _ADAPTER_CACHE:
        cls = ADAPTERS.get(source)
        _ADAPTER_CACHE[source] = cls() if cls else None
    return _ADAPTER_CACHE[source]


async def fetch_hls_frame(cam: Camera, timeout_s: float = 12) -> Frame | None:
    """One JPEG out of an HLS stream via ffmpeg (needs ffmpeg on PATH)."""
    if not cam.stream_url or not shutil.which("ffmpeg"):
        return None
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-y",
        "-i",
        cam.stream_url,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "3",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    if not out:
        return None
    return Frame(cam.id, datetime.now(timezone.utc), cam.stream_url, out, "image/jpeg")


async def fetch_frame(
    http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
) -> Frame | None:
    """Dispatch on source adapter, then source_kind. `ts` = replay time (archive frame)."""
    adapter = _adapter(cam.source)
    if adapter is not None:
        fr = await adapter.fetch_frame(http, cam, ts)
        if fr is not None or ts is not None or cam.source in OWNS_FETCH:
            return fr
    if cam.source_kind == "hls" and ts is None:
        return await fetch_hls_frame(cam)
    return await default_fetch_frame(http, cam, ts)


async def fetch_burst(
    http: httpx.AsyncClient, cam: Camera, n: int, spacing_s: float = 3.0
) -> list[Frame]:
    frames: list[Frame] = []
    for i in range(n):
        fr = await fetch_frame(http, cam)
        if fr is not None:
            frames.append(fr)
        if i < n - 1:
            await asyncio.sleep(spacing_s)
    return frames
