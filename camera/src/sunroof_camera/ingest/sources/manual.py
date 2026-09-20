"""Hand-picked cameras from `data/manual.yaml` (YouTube 24/7 streams, Explore.org, all-sky...).

Each YAML entry is a partial `Camera`; `id` is derived from `source_id`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import ClassVar

import httpx
import yaml

from ...schema import Camera
from ..base import Frame, default_fetch_frame


class ManualAdapter:
    source: ClassVar[str] = "manual"

    def __init__(self, path: str | Path = "data/manual.yaml"):
        self.path = Path(path)

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        if not self.path.exists():
            return []
        entries = yaml.safe_load(self.path.read_text()) or []
        out = []
        for e in entries:
            e = dict(e)
            sid = e.pop("source_id")
            kind = e.pop("source_kind", "embed" if e.get("embed_url") else "jpeg")
            out.append(Camera(id=f"manual:{sid}", source="manual", source_kind=kind, **e))
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        return await default_fetch_frame(http, cam, ts)
