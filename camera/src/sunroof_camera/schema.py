"""The `cameras` table: one row per camera *view*.

This module is the contract between the ingest side (adapters produce `Camera`
rows) and the query side (`find_cameras` reads the Parquet file). Column
semantics are documented once, here, in `Camera`; `CAMERA_DTYPES` is the exact
pandas dtype of every column in `cameras.parquet`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

SourceKind = Literal["jpeg", "hls", "embed", "page"]
HeadingConf = Literal["catalog", "text", "inferred", "ptz", "unknown"]
HistoryKind = Literal["none", "last_n", "url_template", "api"]
Health = Literal["live", "stale", "dead", "unverified"]

# Default vertical extent of the frame by camera style, degrees above horizon.
ELEV_DEFAULTS: dict[str, tuple[float, float]] = {
    "road": (-5.0, 25.0),
    "panorama": (-10.0, 20.0),
    "allsky": (0.0, 90.0),
    "sky": (5.0, 60.0),
}


class Camera(BaseModel):
    """One camera view. Field comments are the column docs."""

    # identity
    id: str = Field(description="'{source}:{source_id}[:{view}]', stable across refreshes")
    source: str = Field(description="adapter name: caltrans, digitraffic, panomax, ...")
    source_kind: SourceKind = Field(description="how we get pixels: jpeg | hls | embed | page")
    name: str

    # location
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_m: float | None = Field(None, description="camera altitude; catalog or DEM lookup")
    tz: str | None = Field(None, description="IANA zone, for local-time display")

    # orientation = 'which region of the sky' (docs/preprocessing-plan.md §3)
    azimuth_deg: float | None = Field(None, description="view centre, 0=N cw; None if unknown")
    hfov_deg: float = Field(60.0, description="horizontal field of view; 360 for all-sky/pano")
    elev_min_deg: float = Field(-5.0, description="bottom edge of frame, deg above horizon")
    elev_max_deg: float = Field(25.0, description="top edge of frame; 90 for all-sky")
    heading_conf: HeadingConf = Field(
        "unknown", description="catalog | text (8-pt compass) | inferred | ptz | unknown"
    )
    sky_frac: float | None = Field(None, description="fraction of a daytime frame that is sky")

    # capability flags
    night_ok: bool = Field(False, description="IR / long exposure / nightVision / all-sky")
    all_sky: bool = False
    ptz: bool = False
    over_water: bool = False

    # fetch
    image_url: str | None = Field(None, description="direct JPEG; may contain {ts} template")
    stream_url: str | None = Field(None, description="HLS .m3u8")
    embed_url: str | None = Field(None, description="iframe / YouTube embed URL")
    page_url: str | None = Field(None, description="attribution deep link")
    refresh_s: int = Field(600, description="expected cadence between frames, seconds")
    history_kind: HistoryKind = "none"
    history_template: str | None = Field(
        None, description="strftime-style URL template for a frame at time t"
    )
    history_depth_days: int = 0
    license: str | None = None
    attribution: str | None = None
    embed_allowed: bool = True

    # health (written by the health job, not by adapters)
    last_frame_ts: datetime | None = None
    last_ok_ts: datetime | None = None
    fail_streak: int = 0
    health: Health = "unverified"
    quality_score: float = Field(0.5, description="0..1 prior used by ranking")
    night_usable_frac: float | None = Field(None, description="learned from night samples")


CAMERA_DTYPES: dict[str, str] = {
    "id": "string",
    "source": "category",
    "source_kind": "category",
    "name": "string",
    "lat": "float64",
    "lon": "float64",
    "alt_m": "float64",
    "tz": "string",
    "azimuth_deg": "float64",
    "hfov_deg": "float64",
    "elev_min_deg": "float64",
    "elev_max_deg": "float64",
    "heading_conf": "category",
    "sky_frac": "float64",
    "night_ok": "bool",
    "all_sky": "bool",
    "ptz": "bool",
    "over_water": "bool",
    "image_url": "string",
    "stream_url": "string",
    "embed_url": "string",
    "page_url": "string",
    "refresh_s": "int32",
    "history_kind": "category",
    "history_template": "string",
    "history_depth_days": "int32",
    "license": "string",
    "attribution": "string",
    "embed_allowed": "bool",
    "last_frame_ts": "datetime64[ns, UTC]",
    "last_ok_ts": "datetime64[ns, UTC]",
    "fail_streak": "int32",
    "health": "category",
    "quality_score": "float64",
    "night_usable_frac": "float64",
}

COLUMNS = list(CAMERA_DTYPES)


def to_frame(cameras: list[Camera]) -> pd.DataFrame:
    """Rows -> typed DataFrame with exactly `COLUMNS`."""
    df = pd.DataFrame([c.model_dump() for c in cameras], columns=COLUMNS)
    return coerce(df)


def coerce(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reindex(columns=COLUMNS)
    for col, dt in CAMERA_DTYPES.items():
        if dt.startswith("datetime64"):
            df[col] = pd.to_datetime(df[col], utc=True)
        elif dt == "bool":
            df[col] = df[col].fillna(False).astype(bool)
        elif dt.startswith("int"):
            df[col] = df[col].fillna(0).astype(dt)
        else:
            df[col] = df[col].astype(dt)
    return df


def empty_frame() -> pd.DataFrame:
    return coerce(pd.DataFrame(columns=COLUMNS))


def write_parquet(df: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    coerce(df).to_parquet(path, index=False, compression="zstd")


def read_parquet(path: str | Path) -> pd.DataFrame:
    return coerce(pd.read_parquet(path))


def upsert(base: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Replace rows of `base` that share an `id` with `new`; keep health columns from base."""
    health_cols = [
        "last_frame_ts",
        "last_ok_ts",
        "fail_streak",
        "health",
        "quality_score",
        "night_usable_frac",
        "sky_frac",
    ]
    new = coerce(new).set_index("id")
    old = coerce(base).set_index("id")
    keep = old.loc[old.index.intersection(new.index), health_cols]
    new.loc[keep.index, health_cols] = keep
    merged = pd.concat([old.drop(index=new.index, errors="ignore"), new])
    return coerce(merged.reset_index())
