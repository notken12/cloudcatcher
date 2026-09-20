"""`find_cameras(event, k)` — the function the backend calls (plan §3–§5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from . import geometry as g
from . import solar
from .health import DEAD_AFTER
from .schema import read_parquet
from .track import CameraTrack

EventType = Literal[
    "sunrise",
    "sunset",
    "thunderstorm",
    "lightning",
    "mammatus",
    "lenticular",
    "undercast",
    "aurora",
    "rainbow",
]


class Event(BaseModel):
    type: EventType
    lat: float
    lon: float
    radius_km: float = Field(10.0, description="horizontal extent of the phenomenon")
    t: datetime | None = Field(None, description="time of interest; None = now")
    layer_top_m: float | None = Field(None, description="undercast: top of the layer")
    ignore_night: bool = Field(False, description="demo/testing: skip the solar night gate")


@dataclass(frozen=True)
class TypeParams:
    h_km: float | None  # feature altitude; None = solar rule / distance-only
    r_cap_km: float
    prefer_km: tuple[float, float] | None = None  # distance band that gets a bonus
    needs_night: bool = False
    source_prior_boost: tuple[str, ...] = ()


PARAMS: dict[str, TypeParams] = {
    "thunderstorm": TypeParams(12.0, 150.0, prefer_km=(30, 100)),
    "lightning": TypeParams(6.0, 60.0, prefer_km=(5, 40)),
    "mammatus": TypeParams(4.0, 40.0, prefer_km=(0, 15)),
    "lenticular": TypeParams(
        6.0, 80.0, prefer_km=(10, 50), source_prior_boost=("panomax", "fotowebcam")
    ),
    "undercast": TypeParams(None, 30.0, source_prior_boost=("panomax", "fotowebcam")),
    "aurora": TypeParams(110.0, 600.0, needs_night=True),
    "sunrise": TypeParams(None, 50.0),
    "sunset": TypeParams(None, 50.0),
    "rainbow": TypeParams(None, 5.0),
}

SOURCE_PRIOR = {
    "panomax": 0.9,
    "fotowebcam": 0.9,
    "manual": 0.8,
    "ndbc": 0.75,
    "windy": 0.7,
    "phenocam": 0.7,
    "alertca": 0.65,
    "iem": 0.6,
    "iceland": 0.55,
    "drivebc": 0.55,
    "nzta": 0.5,
    "sg_lta": 0.45,
    "hk_td": 0.35,
    "tripcheck": 0.45,
    "digitraffic": 0.5,
    "caltrans": 0.45,
    "cars_ny": 0.4,
    "cars_on": 0.4,
    "cars_ak": 0.5,
    "cars_ab": 0.5,
    "cars_id": 0.5,
    "cars_ut": 0.45,
    "cars_nv": 0.45,
    "cars_az": 0.4,
    "cars_yt": 0.5,
    "cars_ne6": 0.45,
    "cars_nl": 0.45,
    "cars_ns": 0.4,
    "cars_nb": 0.4,
    "cars_pa": 0.35,
    "cars_nc": 0.35,
    "cars_wi": 0.35,
    "cars_la": 0.35,
    "cars_fl": 0.3,
    "no_vegvesen": 0.6,  # rural, mountain passes, altitude known
    "tw_tdx": 0.35,
    "tfl": 0.3,  # dense urban, low mount
    "au_qld": 0.4,
    "au_nsw_maritime": 0.65,  # coastal bars, horizon + wide sky, 1080p HLS
    "cars_mn": 0.4,
    "cars_ia": 0.4,
    "cars_ne": 0.45,
    "cars_in": 0.35,
    "cars_ma": 0.35,
}
DEFAULT_PRIOR = 0.5
# last_frame_ts older than this -> offline, same cutoff the health probe uses for `dead`; covers
# cameras the probe has not reached yet (health still `unverified`, timestamp from ingest)
MAX_CATALOG_AGE_S = DEAD_AFTER.total_seconds()


class Catalog:
    """Loads cameras.parquet once and answers find_cameras()."""

    def __init__(self, df: pd.DataFrame, track: CameraTrack | None = None):
        self.df = df.reset_index(drop=True)
        self.track = track  # per-camera VLM track record; None = geometry only

    @classmethod
    def load(
        cls, path: str | Path = "data/cameras.parquet", verdict_log: str | Path | None = None
    ) -> Catalog:
        return cls(read_parquet(path), CameraTrack.from_log(verdict_log) if verdict_log else None)

    def find_cameras(
        self, event: Event, k: int = 10, include_unverified: bool = True
    ) -> pd.DataFrame:
        df = self.df
        p = PARAMS[event.type]
        t = event.t or datetime.now(timezone.utc)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)

        lat, lon = df["lat"].to_numpy(), df["lon"].to_numpy()
        d = g.haversine_km(lat, lon, event.lat, event.lon)
        b = g.bearing_deg(lat, lon, event.lat, event.lon)
        cam_alt_km = np.nan_to_num(df["alt_m"].to_numpy(dtype=float)) / 1000.0
        sun_el, sun_az = solar.sun_position_deg(lat, lon, t)

        # -- hard geometry --------------------------------------------------
        ok = d <= p.r_cap_km + event.radius_km
        reason = np.full(len(df), "", dtype=object)
        if p.h_km is not None:
            d_min, d_max = g.annulus_km(p.h_km, df["elev_min_deg"], df["elev_max_deg"], p.r_cap_km)
            d_eff = np.maximum(d - event.radius_km, 0)  # nearest edge of the event
            ok &= (d + event.radius_km >= d_min) & (d_eff <= d_max)
            ok &= g.bearing_ok(
                b, df["azimuth_deg"], df["hfov_deg"], df["heading_conf"], d, event.radius_km
            )
            reason[:] = [
                f"d={x:.0f}km in [{lo:.0f},{hi:.0f}]" for x, lo, hi in zip(d, d_min, d_max)
            ]
        elif event.type in ("sunrise", "sunset"):
            ok &= (sun_el >= -6) & (sun_el <= 6)
            ok &= df["elev_min_deg"].to_numpy() <= 2
            ok &= g.bearing_ok(
                sun_az,
                df["azimuth_deg"],
                df["hfov_deg"],
                df["heading_conf"],
                np.full_like(d, 1e6),
                0,
            )
            reason[:] = [f"sun az={a:.0f} el={e:.1f}" for a, e in zip(sun_az, sun_el)]
        elif event.type == "rainbow":
            anti = (sun_az + 180) % 360
            ok &= (sun_el > 0) & (sun_el < 42)
            ok &= g.bearing_ok(
                anti,
                df["azimuth_deg"],
                df["hfov_deg"] + 84,
                df["heading_conf"],
                np.full_like(d, 1e6),
                0,
            )
            reason[:] = [f"antisolar az={a:.0f} sun el={e:.1f}" for a, e in zip(anti, sun_el)]
        elif event.type == "undercast":
            top = (event.layer_top_m or 800.0) / 1000.0
            ok &= (cam_alt_km > top) & (df["elev_min_deg"].to_numpy() < 0)
            reason[:] = "camera above layer, looks down"

        # -- night gate (plan §4) -----------------------------------------------
        night_ok = df["night_ok"].to_numpy()
        night_mult = np.ones(len(df))
        twilight = (sun_el <= -6) & (sun_el > -18)
        dark = sun_el <= -18
        fast = df["refresh_s"].to_numpy() <= 60
        if event.ignore_night:
            pass
        elif p.needs_night:
            ok &= night_ok & (sun_el < -12)
        else:
            lightning = event.type == "lightning"
            afterglow = event.type in ("sunrise", "sunset")
            if not (lightning or afterglow):
                ok &= ~(twilight & ~night_ok)
            if lightning:
                ok &= ~(dark & ~night_ok & ~fast)
            else:
                ok &= ~(dark & ~night_ok)
            night_mult = np.where(twilight & ~night_ok, 0.5, night_mult)

        health = df["health"].astype(str).to_numpy()
        ok &= health != "dead"
        if not include_unverified:
            ok &= health == "live"
        age_s = (pd.Timestamp(t) - df["last_frame_ts"]).dt.total_seconds().to_numpy(dtype=float)
        ok &= np.isnan(age_s) | (age_s <= MAX_CATALOG_AGE_S)  # camera silent for a day+: skip it

        # -- score (plan §5) -------------------------------------------------------
        geo_fit = np.ones(len(df))
        if p.prefer_km:
            lo, hi = p.prefer_km
            geo_fit = np.where((d >= lo) & (d <= hi), 1.0, 0.7)
        if event.type == "mammatus":
            geo_fit *= np.where(df["elev_max_deg"].to_numpy() >= 40, 1.2, 1.0)
        heading_mult = np.where(df["heading_conf"].isin(["ptz", "unknown"]).to_numpy(), 0.6, 1.0)
        fresh = np.where(
            np.isnan(age_s), 0.3, np.exp(-np.maximum(age_s, 0) / (3 * df["refresh_s"].to_numpy()))
        )
        prior = (
            df["source"]
            .astype(str)
            .map(lambda s: SOURCE_PRIOR.get(s, DEFAULT_PRIOR))
            .to_numpy(dtype=float)
        )
        prior = np.where(
            df["source"].astype(str).isin(p.source_prior_boost).to_numpy(), prior + 0.1, prior
        )
        sky = np.nan_to_num(df["sky_frac"].to_numpy(dtype=float), nan=0.4)
        track_mult = np.ones(len(df))
        if self.track is not None and self.track.per_type.get(event.type):
            track_mult = np.fromiter(
                (self.track.multiplier(cid, event.type) for cid in df["id"].astype(str)),
                dtype=float,
                count=len(df),
            )
        score = (
            (
                0.35 * geo_fit
                + 0.25 * fresh
                + 0.15 * sky
                + 0.15 * prior
                + 0.10 * df["quality_score"].to_numpy()
            )
            * heading_mult
            * night_mult
            * track_mult
        )
        reason = np.array(
            [
                f"{r}; track x{m:.2f}" if abs(m - 1.0) > 0.01 else r
                for r, m in zip(reason, track_mult)
            ],
            dtype=object,
        )

        out = df.loc[ok].copy()
        out["distance_km"] = d[ok]
        out["bearing_to_event"] = b[ok]
        out["solar_elev"] = sun_el[ok]
        out["score"] = score[ok]
        out["reason"] = reason[ok]
        out = out.sort_values("score", ascending=False)
        out = _dedupe_nearby(out)
        return out.head(k).reset_index(drop=True)


def _dedupe_nearby(df: pd.DataFrame, cell_deg: float = 0.01) -> pd.DataFrame:
    """Keep the best-scoring view per ~1 km cell so top-k isn't ten presets of one PTZ."""
    key = (
        (df["lat"] / cell_deg).round().astype(int).astype(str)
        + ","
        + (df["lon"] / cell_deg).round().astype(int).astype(str)
    )
    return df.loc[~key.duplicated()]


def find_cameras(
    event: Event, k: int = 10, path: str | Path = "data/cameras.parquet"
) -> pd.DataFrame:
    return Catalog.load(path).find_cameras(event, k)
