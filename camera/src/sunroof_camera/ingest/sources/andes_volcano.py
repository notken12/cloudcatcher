"""Andean volcano-observatory cameras: Peru IGP/CENVUL, Ecuador IG-EPN, Colombia SGC.

South America has almost no keyless road/weather cam feeds (see docs/sources.md); the three
national volcano observatories are the exception — fixed HD cams pointed at a summit, i.e. at
the sky, refreshed every 1-10 min, several of them thermal (usable at night).

* Peru   — Strapi JSON `cenvul.igp.gob.pe/backend/api/volcanoes?populate=camera` gives volcano
           lat/lon + per-camera `title` ("Sector Noreste") and `link` (ide.igp.gob.pe/ltImages).
* Ecuador — per-volcano pages embed `setImageWithFallback("<key>", "<webp>", ...)`.
* Colombia — per-volcano SharePoint pages link `amenazas.sgc.gov.co/**/<cam>.jpg`.

None of the three publishes camera coordinates. Position is taken from a hand-curated site
table (SITES, ±1-3 km) or, for a sector name, by offsetting SECTOR_KM from the summit in that
direction; the azimuth is the bearing site→summit (`heading_conf="inferred"`). Cameras whose
site is unknown are placed at the summit with `heading_conf="unknown"`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

import httpx

from ...geometry import bearing_deg
from ...schema import Camera
from ..base import Frame, default_fetch_frame, get_json

SECTOR_KM = 7.0
R_EARTH_KM = 6371.0

_SECTOR_ES = {
    "noreste": 45.0,
    "nordeste": 45.0,
    "sureste": 135.0,
    "sudeste": 135.0,
    "suroeste": 225.0,
    "sudoeste": 225.0,
    "noroeste": 315.0,
    "norte": 0.0,
    "sur": 180.0,
    "este": 90.0,
    "oeste": 270.0,
}


def sector_bearing_es(text: str | None) -> float | None:
    """'Sector Noreste' -> 45 (direction of the camera site as seen from the summit)."""
    if not text:
        return None
    t = text.lower()
    for word, deg in _SECTOR_ES.items():  # multi-word keys first (dict order)
        if re.search(rf"\b{word}\b", t):
            return deg
    return None


def offset_km(lat: float, lon: float, bearing: float, d_km: float) -> tuple[float, float]:
    b = math.radians(bearing)
    la1, lo1 = math.radians(lat), math.radians(lon)
    ad = d_km / R_EARTH_KM
    la2 = math.asin(math.sin(la1) * math.cos(ad) + math.cos(la1) * math.sin(ad) * math.cos(b))
    lo2 = lo1 + math.atan2(
        math.sin(b) * math.sin(ad) * math.cos(la1), math.cos(ad) - math.sin(la1) * math.sin(la2)
    )
    return math.degrees(la2), math.degrees(lo2)


@dataclass(frozen=True)
class Volcano:
    name: str
    lat: float
    lon: float
    alt_m: float


@dataclass(frozen=True)
class Site:
    lat: float
    lon: float
    alt_m: float
    label: str


def _is_ir(key: str) -> bool:
    k = key.lower()
    return k.endswith("ir") or "_ir_" in k


def _camera(
    *,
    source: str,
    cid: str,
    volcano: Volcano,
    label: str,
    site: Site | None,
    sector: float | None,
    image_url: str,
    page_url: str,
    tz: str,
    ir: bool,
    license_: str,
    attribution: str,
    refresh_s: int,
) -> Camera:
    if site is not None:
        lat, lon, alt = site.lat, site.lon, site.alt_m
        az: float | None = bearing_deg(lat, lon, volcano.lat, volcano.lon)
        conf = "inferred"
    elif sector is not None:
        lat, lon = offset_km(volcano.lat, volcano.lon, sector, SECTOR_KM)
        alt = max(volcano.alt_m - 1500.0, 0.0)
        az = (sector + 180.0) % 360.0
        conf = "inferred"
    else:
        lat, lon, alt = volcano.lat, volcano.lon, volcano.alt_m
        az = None
        conf = "unknown"
    name = f"{volcano.name} — {label}" + (" (IR)" if ir else "")
    return Camera(
        id=f"{source}:{cid}",
        source=source,
        source_kind="jpeg",
        name=name,
        lat=lat,
        lon=lon,
        alt_m=alt,
        tz=tz,
        azimuth_deg=az,
        hfov_deg=50,
        elev_min_deg=-2,
        elev_max_deg=30,
        heading_conf=conf,
        sky_frac=0.5,
        night_ok=ir,
        image_url=image_url,
        page_url=page_url,
        refresh_s=refresh_s,
        license=license_,
        attribution=attribution,
    )


# ----------------------------------------------------------------------------- Peru (IGP)

IGP_API = "https://cenvul.igp.gob.pe/backend/api/volcanoes"
IGP_SITES: dict[str, Site] = {
    "pinchollo": Site(-15.63, -71.83, 3600, "Pinchollo"),
}


class IGPPeruAdapter:
    source: ClassVar[str] = "pe_igp"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        payload = await get_json(http, IGP_API, params={"populate": "camera"})
        out: list[Camera] = []
        for v in payload.get("data") or []:
            if v.get("latitud") is None or v.get("longitud") is None:
                continue
            volcano = Volcano(
                str(v.get("name") or v.get("slug") or "").strip(),
                float(v["latitud"]),
                float(v["longitud"]),
                float(v.get("elevation") or 0),
            )
            for c in v.get("camera") or []:
                link = c.get("link")
                if not link or c.get("status") is False or c.get("id") is None:
                    continue
                title = str(c.get("title") or "").strip()
                site = next((s for k, s in IGP_SITES.items() if k in title.lower()), None)
                out.append(
                    _camera(
                        source=self.source,
                        cid=str(c["id"]),
                        volcano=volcano,
                        label=title or link.rsplit("/", 1)[-1],
                        site=site,
                        sector=sector_bearing_es(title),
                        image_url=link,
                        page_url=f"https://cenvul.igp.gob.pe/volcanes/{v.get('slug') or ''}",
                        tz="America/Lima",
                        ir=False,
                        license_="IGP public volcano monitoring imagery; credit required",
                        attribution="Instituto Geofísico del Perú — CENVUL",
                        refresh_s=300,
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)


# -------------------------------------------------------------------------- Ecuador (IG-EPN)

IGEPN_BASE = "https://www.igepn.edu.ec"
IGEPN_PAGES: dict[str, Volcano] = {
    "cotopaxi-camaras": Volcano("Cotopaxi", -0.677, -78.436, 5897),
    "tungurahua-camaras": Volcano("Tungurahua", -1.467, -78.442, 5023),
    "reventador-camaras": Volcano("Reventador", -0.077, -77.656, 3562),
    "sangay-camaras": Volcano("Sangay", -2.005, -78.341, 5230),
    "ggp-camaras": Volcano("Guagua Pichincha", -0.171, -78.598, 4784),
    "islas-galapagos-camaras": Volcano("Sierra Negra", -0.830, -91.170, 1124),
}
IGEPN_SITES: dict[str, Site] = {
    "sincholagua": Site(-0.550, -78.372, 4000, "Sincholagua"),
    "lasso": Site(-0.775, -78.610, 3000, "Lasso"),
    "rumvis": Site(-0.600, -78.500, 4200, "Rumiñahui"),
    "rumir": Site(-0.600, -78.500, 4200, "Rumiñahui"),
    "putzvis": Site(-0.973, -78.567, 3500, "Putzalahua"),
    "putzir": Site(-0.973, -78.567, 3500, "Putzalahua"),
    "ovt": Site(-1.390, -78.450, 2900, "OVT Guadalupe"),
    "pillate": Site(-1.470, -78.510, 2400, "Pillate"),
    "achupashal": Site(-1.440, -78.480, 3000, "Achupashal"),
    "runtun": Site(-1.410, -78.420, 2300, "Runtún"),
    "ggp": Site(-0.175, -78.585, 4500, "Refugio"),
    "ggpir": Site(-0.175, -78.585, 4500, "Refugio"),
    "vch1": Site(-0.780, -91.140, 1000, "Volcán Chico"),
}
_IGEPN_RE = re.compile(r"""setImageWithFallback\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]""")


class IGEPNAdapter:
    source: ClassVar[str] = "ec_igepn"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        seen: set[str] = set()
        for slug, volcano in IGEPN_PAGES.items():
            page_url = f"{IGEPN_BASE}/{slug}"
            try:
                r = await http.get(page_url)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            for key, url in _IGEPN_RE.findall(r.text):
                if "loading" in url or key in seen:
                    continue
                seen.add(key)
                site = IGEPN_SITES.get(key.lower())
                label = site.label if site else key
                out.append(
                    _camera(
                        source=self.source,
                        cid=key,
                        volcano=volcano,
                        label=label,
                        site=site,
                        sector=None,
                        image_url=url,
                        page_url=page_url,
                        tz="Pacific/Galapagos" if "galapagos" in slug else "America/Guayaquil",
                        ir=_is_ir(key),
                        license_="IG-EPN public volcano monitoring imagery; credit required",
                        attribution="Instituto Geofísico — Escuela Politécnica Nacional (Ecuador)",
                        refresh_s=300,
                    )
                )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)


# ------------------------------------------------------------------------- Colombia (SGC)

SGC_BASE = "https://www2.sgc.gov.co/sgc/volcanes"
SGC_PAGES: dict[str, Volcano] = {
    "VolcanPurace": Volcano("Puracé", 2.320, -76.400, 4650),
    "VolcanGaleras": Volcano("Galeras", 1.220, -77.360, 4276),
    "VolcanCumbal": Volcano("Cumbal", 0.950, -77.880, 4764),
    "VolcanAzufral": Volcano("Azufral", 1.080, -77.680, 4070),
    "VolcanSotara": Volcano("Sotará", 2.108, -76.592, 4400),
    "VolcanAnimas": Volcano("Las Ánimas", 1.470, -76.930, 4150),
}
SGC_SITES: dict[str, Site] = {
    "galeras-consaca": Site(1.207, -77.466, 1700, "Consacá"),
    "galeras-ovsp": Site(1.213, -77.280, 2600, "OVSP Pasto"),
    "morasurco": Site(1.270, -77.250, 3300, "Morasurco"),
    "barranco": Site(1.225, -77.335, 3800, "Barranco Alto"),
    "cumbal": Site(0.907, -77.790, 3100, "Cumbal (town)"),
    "chiles-boliche": Site(0.820, -77.860, 3200, "Chiles–Boliche"),
    "azufral-mallama": Site(1.140, -77.860, 2300, "Mallama"),
    "azufral-lag-hd": Site(1.085, -77.700, 3900, "Laguna Verde"),
}
_SGC_RE = re.compile(r'https?://amenazas\.sgc\.gov\.co/[^"\'<>\s]+\.jpg')


def sgc_cam_key(url: str) -> str:
    """'.../ovspa/camaras/cumbal000.jpg' -> 'cumbal'; '.../popayan/webcams/Mina2_IR/imagen_web.jpg' -> 'mina2_ir'."""
    parts = url.rsplit("/", 2)
    stem = parts[-1].rsplit(".", 1)[0]
    if stem.lower() == "imagen_web" and len(parts) >= 2:
        stem = parts[-2]
    return re.sub(r"\d{3,4}m?$", "", stem, flags=re.I).lower()


class SGCColombiaAdapter:
    source: ClassVar[str] = "co_sgc"

    async def catalog(self, http: httpx.AsyncClient) -> list[Camera]:
        out: list[Camera] = []
        chosen: dict[str, str] = {}
        page_of: dict[str, str] = {}
        for slug in SGC_PAGES:
            page_url = f"{SGC_BASE}/{slug}/Paginas/imagenes-en-linea.aspx"
            try:
                r = await http.get(page_url)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            for url in _SGC_RE.findall(r.text):
                # /img-mini/ are thumbnails; /webcam/pasto/ is a dead legacy mirror of /ovspa/
                if "/img-mini/" in url or "/webcam/pasto/" in url:
                    continue
                key = sgc_cam_key(url)
                if key not in chosen:
                    chosen[key] = url.replace("http://", "https://")
                    page_of[key] = slug
        for key, url in chosen.items():
            slug = page_of[key]
            volcano = SGC_PAGES[slug]
            site = SGC_SITES.get(key)
            label = (
                site.label if site else re.sub(r"[_\-]+", " ", re.sub(r"_?ir$", "", key)).title()
            )
            out.append(
                _camera(
                    source=self.source,
                    cid=key,
                    volcano=volcano,
                    label=label,
                    site=site,
                    sector=None,
                    image_url=url,
                    page_url=f"{SGC_BASE}/{slug}/Paginas/imagenes-en-linea.aspx",
                    tz="America/Bogota",
                    ir=_is_ir(key),
                    license_="SGC public volcano monitoring imagery; credit required",
                    attribution="Servicio Geológico Colombiano",
                    refresh_s=300,
                )
            )
        return out

    async def fetch_frame(self, http, cam: Camera, ts: datetime | None = None) -> Frame | None:
        return await default_fetch_frame(http, cam, None)
