"""Contracts between the weather backend, this camera service and the frontend
(docs/query-and-routing-plan.md §1, §7)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .query import EventType

Status = Literal[
    "FOOTAGE_FOUND",
    "NO_CAMERAS_IN_RANGE",
    "CAMERAS_DARK",
    "ALL_STALE",
    "EVENT_NOT_VISIBLE",
    "LOW_QUALITY",
    "NO_FOOTAGE_FOUND",
    "TIMEOUT",
]

MediaKind = Literal["image", "hls", "iframe"]
TsSource = Literal["source_api", "exif", "last_modified", "fetch_time", "unknown"]


class WeatherEvent(BaseModel):
    """What Ken's weather backend posts to us (`POST /events`)."""

    id: str
    type: EventType
    lat: float
    lon: float
    radius_km: float = 10.0
    t_start: datetime | None = None
    t_end: datetime | None = None
    severity: float | None = Field(None, description="0..1 from the weather side")
    rarity: float | None = None
    layer_top_m: float | None = Field(None, description="undercast: top of the layer")
    region: list[tuple[float, float]] | None = Field(None, description="polygon (lat, lon)")
    replay: bool = Field(False, description="historical event: fetch archive frames at t_start")


class Verdict(BaseModel):
    usable: bool
    sky_visible: float = Field(0.0, ge=0, le=1)
    night: bool = False
    event_visible: Literal["yes", "partial", "no", "unsure"] = "unsure"
    event_type_seen: str = "none"
    confidence: float = Field(0.0, ge=0, le=1)
    quality: int = Field(1, ge=1, le=5)
    caption: str = ""
    burned_in_time: str | None = None
    model: str | None = None


class Media(BaseModel):
    kind: MediaKind
    src: str = Field(description="proxy URL the frontend can load directly")
    poster: str | None = None
    refresh_s: int = 600
    expires_at: datetime | None = None
    width: int | None = None
    height: int | None = None


class CameraInfo(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    alt_m: float | None = None
    tz: str | None = None
    azimuth_deg: float | None = None
    source: str
    page_url: str | None = None
    attribution: str | None = None
    license: str | None = None
    distance_km: float
    bearing_to_event: float


class Footage(BaseModel):
    """Universal envelope: same shape for every camera source and every event type."""

    event_id: str
    event_type: EventType
    camera_id: str
    rank: int
    verified: bool = Field(description="VLM said the target event is visible")
    media: Media
    verdict: Verdict | None
    quality: float | None = Field(
        None, description="deterministic 0-1 'worth showing' score (Stage A)"
    )
    features: dict[str, float] | None = Field(
        None, description="quality.FrameFeatures behind `quality`"
    )
    why: str = Field(description="human 'why this camera' string from find_cameras")
    camera: CameraInfo
    frame_ts: datetime | None = Field(None, description="when the frame was taken (best guess)")
    ts_source: TsSource = "unknown"
    fetched_at: datetime
    hold_until: datetime


class Rejected(BaseModel):
    camera_id: str
    stage: Literal["fetch", "gate", "cv", "vlm", "quality"]
    reason: str


class FootageResult(BaseModel):
    """Always returned to the weather backend, success or not."""

    event_id: str
    status: Status
    footage: list[Footage] = []
    candidates: int = 0
    fetched: int = 0
    passed_gates: int = 0
    cv_skipped: int = Field(
        0, description="gate-passing frames the CV pre-gate / top-N cut kept from the VLM"
    )
    vlm_calls: int = 0
    note: str | None = Field(None, description="why the event was skipped before any fetch")
    rejected: list[Rejected] = []
    elapsed_s: float = 0.0
    retry_after_s: int | None = None
