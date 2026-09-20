"""CARS "OneWeb" 511 portals (React front end) via their keyless GraphQL endpoint.

These states rewrote their 511 site on top of `POST /api/graphql`; the old
`/List/GetData/Cameras` endpoint returns the app shell. One `mapFeaturesQuery` over the
whole state bbox at zoom 15 returns every camera un-clustered, with a JPEG poster URL per
view (also for VIDEO views) and the HLS source when public.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import ClassVar

import httpx

from ...schema import Camera
from ..base import Frame, default_fetch_frame, parse_heading_text

log = logging.getLogger(__name__)

ROAD_ELEV = (-5.0, 25.0)

QUERY = """
query MapFeatures($input: MapFeaturesArgs!) {
  mapFeaturesQuery(input: $input) {
    mapFeatures {
      bbox title uri __typename
      ... on Camera {
        active
        views(limit: 8) {
          uri category
          ... on CameraView { url title sources { type src } }
        }
      }
    }
    error { message type }
  }
}
"""

# source suffix -> (host, attribution, (north, south, east, west)). Kansas (kandrive.gov)
# is on the same platform but returns url=null for every view, so it is left out.
PORTALS: dict[str, tuple[str, str, tuple[float, float, float, float]]] = {
    "mn": ("511mn.org", "Minnesota DOT / 511MN", (49.5, 43.4, -89.4, -97.4)),
    "ia": ("511ia.org", "Iowa DOT / 511IA", (43.6, 40.3, -90.0, -96.7)),
    "ma": ("mass511.com", "MassDOT / Mass511", (43.0, 41.1, -69.8, -73.6)),
    "ne": ("511.nebraska.gov", "Nebraska DOT / 511", (43.1, 39.9, -95.2, -104.2)),
    "in": ("511in.org", "INDOT / TrafficWise", (41.8, 37.7, -84.7, -88.1)),
    "ie": ("traffic.tii.ie", "Transport Infrastructure Ireland", (55.5, 51.3, -5.9, -10.7)),
}


def _view_id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


class CarsGqlAdapter:
    source: ClassVar[str] = "cars_gql"
    host: ClassVar[str] = ""
    attribution: ClassVar[str] = ""
    bbox: ClassVar[tuple[float, float, float, float]] = (0, 0, 0, 0)

    async def _features(self, http: httpx.AsyncClient) -> list[dict]:
        n, s, e, w = self.bbox
        variables = {
            "input": {
                "north": n,
                "south": s,
                "east": e,
                "west": w,
                "zoom": 15,
                "layerSlugs": ["normalCameras"],
                "nonClusterableUris": ["dashboard"],
            }
        }
        r = await http.post(
            f"https://{self.host}/api/graphql",
            json={"query": QUERY, "variables": variables},
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        q = r.json()["data"]["mapFeaturesQuery"]
        if q.get("error"):
            log.warning("%s: %s", self.source, q["error"])
        return q.get("mapFeatures") or []

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        for f in await self._features(http):
            if f.get("__typename") != "Camera" or not f.get("active"):
                continue
            bbox = f.get("bbox") or []
            if len(bbox) < 2:
                continue
            lon, lat = float(bbox[0]), float(bbox[1])
            site = f.get("title") or ""
            site_id = _view_id(f["uri"])
            for vw in f.get("views") or []:
                url = vw.get("url")
                if not url or url.endswith(".svg"):
                    continue
                url = url.split("?", 1)[0]
                title = vw.get("title") or site
                hls = next(
                    (s["src"] for s in vw.get("sources") or [] if "mpegurl" in s["type"].lower()),
                    None,
                )
                az = parse_heading_text(title)
                out.append(
                    Camera(
                        id=f"{self.source}:{site_id}:{_view_id(vw['uri'])}",
                        source=self.source,
                        source_kind="jpeg",
                        name=title,
                        lat=lat,
                        lon=lon,
                        azimuth_deg=az,
                        elev_min_deg=ROAD_ELEV[0],
                        elev_max_deg=ROAD_ELEV[1],
                        heading_conf="text" if az is not None else "unknown",
                        image_url=url,
                        stream_url=hls,
                        page_url=f"https://{self.host}/",
                        refresh_s=120 if hls else 300,
                        license=f"{self.attribution} terms of use",
                        attribution=self.attribution,
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)


def make_adapters() -> list[type[CarsGqlAdapter]]:
    return [
        type(
            f"CarsGql{suffix.upper()}Adapter",
            (CarsGqlAdapter,),
            {"source": f"cars_{suffix}", "host": host, "attribution": attr, "bbox": bbox},
        )
        for suffix, (host, attr, bbox) in PORTALS.items()
    ]


CARS_GQL_ADAPTERS = make_adapters()
