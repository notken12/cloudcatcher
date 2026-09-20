"""PhenoCam network — ~1,000 research sites (mostly N. America), fixed cameras with compass
orientation, latest JPEG at /data/latest/<site>.jpg and archives back to 2000s
(/data/archive/<site>/YYYY/MM/<site>_YYYY_MM_DD_HHMMSS.jpg, 30-min cadence)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, fetch_image, get_json, parse_heading_text

API = "https://phenocam.nau.edu/api/cameras/?format=json&limit=500"
LATEST = "https://phenocam.nau.edu/data/latest/{site}.jpg"
ARCHIVE = "https://phenocam.nau.edu/data/archive/{site}/%Y/%m/{site}_%Y_%m_%d_%H%M%S.jpg"
# Archive filenames carry the capture second, so a frame at time t is found by listing the
# day's browse page and taking the closest one (30-min cadence, local site time).
BROWSE = "https://phenocam.nau.edu/webcam/browse/{site}/%Y/%m/%d/"
_FRAME = re.compile(
    r"/data/archive/[^/\"']+/\d{4}/\d{2}/[^/\"']+_(\d{4})_(\d{2})_(\d{2})_(\d{6})\.jpg"
)


def closest_frame_path(html: str, ts: datetime) -> str | None:
    best: tuple[float, str] | None = None
    for m in _FRAME.finditer(html):
        y, mo, d, hms = m.groups()
        t = datetime(int(y), int(mo), int(d), int(hms[:2]), int(hms[2:4]), int(hms[4:]))
        dt = abs((t - ts.replace(tzinfo=None)).total_seconds())
        if best is None or dt < best[0]:
            best = (dt, m.group(0))
    return best[1] if best else None


class PhenoCamAdapter:
    source: ClassVar[str] = "phenocam"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        url: str | None = API
        today = datetime.utcnow().date()
        while url:
            page = await get_json(http, url)
            for rec in page["results"]:
                if not rec.get("active") or rec.get("Lat") is None:
                    continue
                last = rec.get("date_last")
                if last and (today - datetime.fromisoformat(last).date()).days > 30:
                    continue
                meta = rec.get("sitemetadata") or {}
                az = parse_heading_text(meta.get("camera_orientation"))
                first = rec.get("date_first")
                depth = (today - datetime.fromisoformat(first).date()).days if first else 0
                site = rec["Sitename"]
                out.append(
                    Camera(
                        id=f"phenocam:{site}",
                        source=self.source,
                        source_kind="jpeg",
                        name=meta.get("site_description") or site,
                        lat=rec["Lat"],
                        lon=rec["Lon"],
                        alt_m=rec.get("Elev"),
                        azimuth_deg=az,
                        hfov_deg=50,
                        elev_min_deg=-5,
                        elev_max_deg=20,
                        heading_conf="text" if az is not None else "unknown",
                        sky_frac=0.3,
                        night_ok=False,  # "infrared" = NIR channel for vegetation, not low-light
                        image_url=LATEST.format(site=site),
                        page_url=f"https://phenocam.nau.edu/webcam/sites/{site}/",
                        refresh_s=1800,
                        history_kind="url_template",
                        history_template=ARCHIVE.format(site=site),
                        history_depth_days=depth,
                        license="CC BY 4.0 (PhenoCam)",
                        attribution="PhenoCam Network, Northern Arizona University",
                    )
                )
            url = page.get("next")
        return out

    async def fetch_frame(
        self, http: httpx.AsyncClient, cam: Camera, ts: datetime | None = None
    ) -> Frame | None:
        if ts is None:
            return await default_fetch_frame(http, cam, None)
        site = cam.id.split(":", 1)[1]
        try:
            r = await http.get(ts.strftime(BROWSE.format(site=site)))
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        path = closest_frame_path(r.text, ts)
        if path is None:
            return None
        return await fetch_image(http, cam, "https://phenocam.nau.edu" + path)
