"""`resolve_footage(event)`: find cameras → fetch → cheap gates → VLM → Footage / status.

One call per (event, ~10 min). Everything after find_cameras runs concurrently per camera
under a hard deadline; whatever is verified by then is returned.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
from PIL import Image

from . import gates, vlm
from .fetch import fetch_burst, fetch_frame, row_to_camera
from .footage import (
    CameraInfo,
    Footage,
    FootageResult,
    Media,
    Rejected,
    Verdict,
    WeatherEvent,
)
from .geometry import haversine_km
from .ingest.base import Frame
from .profiles import EventProfile, profile
from .query import PARAMS, Catalog, Event
from .schema import Camera

log = logging.getLogger(__name__)


@dataclass
class CachedFrame:
    content: bytes
    content_type: str
    sha1: str
    phash: int | None
    frame_ts: datetime | None
    fetched_at: datetime
    width: int | None = None
    height: int | None = None


class FrameCache:
    """Latest good frame per camera; what `/proxy/frame/{camera_id}` serves and what the
    frozen-frame check compares against."""

    def __init__(self) -> None:
        self.frames: dict[str, CachedFrame] = {}
        self.verdicts: dict[tuple[str, str, str], Verdict] = {}  # (cam, type, sha1)

    def put(self, cam_id: str, fr: Frame, g: gates.GateResult, now: datetime) -> None:
        self.frames[cam_id] = CachedFrame(
            fr.content,
            fr.content_type or "image/jpeg",
            fr.sha1,
            g.phash,
            g.frame_ts,
            now,
            g.width,
            g.height,
        )

    def get(self, cam_id: str) -> CachedFrame | None:
        return self.frames.get(cam_id)


@dataclass
class _Candidate:
    row: pd.Series
    cam: Camera
    frame: Frame | None = None
    gate: gates.GateResult | None = None
    verdict: Verdict | None = None
    score: float = 0.0
    rejected: Rejected | None = None
    burst: list[Frame] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_query_event(ev: WeatherEvent) -> Event:
    return Event(
        type=ev.type,
        lat=ev.lat,
        lon=ev.lon,
        radius_km=ev.radius_km,
        t=ev.t_start,  # sun geometry at event time; None -> now
        layer_top_m=ev.layer_top_m,
    )


def _media_for(cam: Camera, proxy_base: str, g: gates.GateResult | None) -> Media:
    if cam.source_kind == "hls" and cam.stream_url:
        return Media(
            kind="hls",
            src=f"{proxy_base}/proxy/hls/{cam.id}",
            poster=f"{proxy_base}/proxy/frame/{cam.id}",
            refresh_s=cam.refresh_s,
        )
    if cam.source_kind == "embed" and cam.embed_url and cam.embed_allowed:
        return Media(kind="iframe", src=cam.embed_url, refresh_s=cam.refresh_s)
    return Media(
        kind="image",
        src=f"{proxy_base}/proxy/frame/{cam.id}",
        refresh_s=cam.refresh_s,
        width=g.width if g else None,
        height=g.height if g else None,
    )


async def _fetch_and_gate(
    c: _Candidate,
    http: httpx.AsyncClient,
    prof: EventProfile,
    ev: WeatherEvent,
    cache: FrameCache,
    now: datetime,
) -> None:
    ts = ev.t_start if ev.replay else None
    try:
        if prof.frames > 1 and not ev.replay:
            c.burst = await fetch_burst(http, c.cam, prof.frames)
            c.frame = c.burst[-1] if c.burst else None
        else:
            c.frame = await fetch_frame(http, c.cam, ts)
    except Exception as e:  # noqa: BLE001
        c.rejected = Rejected(camera_id=c.cam.id, stage="fetch", reason=f"{type(e).__name__}: {e}")
        return
    if c.frame is None:
        c.rejected = Rejected(camera_id=c.cam.id, stage="fetch", reason="no frame returned")
        return
    prev = cache.get(c.cam.id)
    g = gates.check_frame(
        c.frame,
        refresh_s=c.cam.refresh_s,
        now=now,
        max_age_s=None if ev.replay else min(prof.max_age_mult * c.cam.refresh_s, prof.max_age_s),
        allow_night=prof.allow_night_frames or c.cam.night_ok,
        last_sha1=prev.sha1 if prev else None,
        last_phash=prev.phash if prev else None,
    )
    # a frozen frame (same bytes) is only a rejection if it is also stale
    if not g.ok and g.reason.startswith("frozen") and prev is not None:
        age = (now - (prev.frame_ts or prev.fetched_at)).total_seconds()
        if age <= prof.max_age_mult * c.cam.refresh_s:
            g.ok, g.reason = True, "unchanged but within cadence"
            g.frame_ts, g.ts_source = prev.frame_ts, "source_api"
            g.width, g.height, g.phash = prev.width, prev.height, prev.phash
            g.mean_lum, g.std_lum, g.sharpness, _ = gates.analyze_pixels(
                Image.open(io.BytesIO(c.frame.content))
            )
    c.gate = g
    if not g.ok:
        c.rejected = Rejected(camera_id=c.cam.id, stage="gate", reason=g.reason)
        return
    cache.put(c.cam.id, c.frame, g, now)
    c.score = float(c.row["score"]) * g.score_mult * (1 + 0.2 * min((g.sharpness or 0) / 200, 1))


def _dedupe_phash(cands: list[_Candidate]) -> None:
    seen: list[tuple[int, _Candidate]] = []
    for c in sorted(cands, key=lambda c: -c.score):
        if c.gate is None or c.gate.phash is None:
            continue
        if any(gates.hamming(c.gate.phash, ph) <= 4 for ph, _ in seen):
            c.rejected = Rejected(camera_id=c.cam.id, stage="gate", reason="duplicate view (pHash)")
            continue
        seen.append((c.gate.phash, c))


async def _judge(c: _Candidate, ev: WeatherEvent, prof: EventProfile, cache: FrameCache) -> None:
    assert c.frame is not None and c.gate is not None
    key = (c.cam.id, ev.type, c.frame.sha1)
    if key in cache.verdicts:
        c.verdict = cache.verdicts[key]
        return
    ctx = (
        f"{c.cam.name}; {c.row['distance_km']:.0f} km from the event, camera faces "
        f"{c.cam.azimuth_deg:.0f}°, event at bearing {c.row['bearing_to_event']:.0f}°"
        if c.cam.azimuth_deg is not None
        else f"{c.cam.name}; {c.row['distance_km']:.0f} km from the event, heading unknown"
    )
    v = await vlm.judge(c.frame.content, ev.type, prof.vlm_definition, ctx)
    if v is not None:
        cache.verdicts[key] = v
    c.verdict = v


def _passes(v: Verdict, ev_type: str, prof: EventProfile) -> bool:
    return (
        v.usable
        and v.event_visible in ("yes", "partial")
        and v.event_type_seen == ev_type
        and v.confidence >= prof.min_conf
    )


async def resolve_footage(
    ev: WeatherEvent,
    catalog: Catalog,
    http: httpx.AsyncClient,
    cache: FrameCache,
    k: int = 3,
    deadline_s: float = 30.0,
    proxy_base: str = "",
    verdict_log: str | None = None,
) -> FootageResult:
    t0 = time.monotonic()
    now = ev.t_start if (ev.replay and ev.t_start) else _now()
    prof = profile(ev.type)
    res = FootageResult(event_id=ev.id, status="NO_CAMERAS_IN_RANGE")

    # ① which cameras
    hits = catalog.find_cameras(_to_query_event(ev), k=k * prof.fetch_mult)
    res.candidates = len(hits)
    if hits.empty:
        # distinguish "nothing nearby" from "nearby but filtered out (night / heading)"
        d = haversine_km(catalog.df["lat"].to_numpy(), catalog.df["lon"].to_numpy(), ev.lat, ev.lon)
        nearby = int((d <= PARAMS[ev.type].r_cap_km + ev.radius_km).sum())
        res.status = "CAMERAS_DARK" if nearby else "NO_CAMERAS_IN_RANGE"
        res.retry_after_s = 3600 if res.status == "CAMERAS_DARK" else None
        res.elapsed_s = time.monotonic() - t0
        return res

    cands = [_Candidate(row=r, cam=row_to_camera(r)) for _, r in hits.iterrows()]

    # ② fetch + cheap gates, concurrently, under the deadline
    budget = deadline_s * 0.6
    tasks = [asyncio.create_task(_fetch_and_gate(c, http, prof, ev, cache, now)) for c in cands]
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for t in pending:
        t.cancel()
    for c, t in zip(cands, tasks):
        if t.cancelled() or (not t.done()):
            c.rejected = Rejected(camera_id=c.cam.id, stage="fetch", reason="timeout")
    res.fetched = sum(1 for c in cands if c.frame is not None)
    _dedupe_phash(cands)
    good = [c for c in cands if c.rejected is None and c.gate is not None and c.gate.ok]
    good.sort(key=lambda c: -c.score)
    good = good[: 2 * k]
    res.passed_gates = len(good)
    if not good:
        res.rejected = [c.rejected for c in cands if c.rejected]
        stale = [r for r in res.rejected if r.reason.startswith(("stale", "frozen", "not updated"))]
        res.status = "ALL_STALE" if stale and len(stale) == res.fetched else "NO_FOOTAGE_FOUND"
        res.retry_after_s = prof.retry_after_s
        res.elapsed_s = time.monotonic() - t0
        return res

    # ③ VLM
    remaining = max(deadline_s - (time.monotonic() - t0), 1.0)
    if vlm.available():
        jt = [asyncio.create_task(_judge(c, ev, prof, cache)) for c in good]
        _, pend = await asyncio.wait(jt, timeout=remaining)
        for t in pend:
            t.cancel()
        res.vlm_calls = sum(1 for c in good if c.verdict is not None)
        if verdict_log:
            for c in good:
                if c.verdict and c.frame:
                    vlm.log_verdict(verdict_log, c.cam.id, ev.type, c.verdict, c.frame.sha1)
    else:
        for c in good:
            c.verdict = vlm.skipped_verdict("VLM skipped: OPENAI_API_KEY not set")

    # ④ route
    footage: list[Footage] = []
    if vlm.available():
        passed = [c for c in good if c.verdict and _passes(c.verdict, ev.type, prof)]
        passed.sort(key=lambda c: -(c.verdict.confidence * (0.6 + 0.1 * c.verdict.quality)))  # type: ignore[union-attr]
        for c in good:
            if c not in passed:
                why = (
                    "vlm timeout"
                    if c.verdict is None
                    else (
                        f"vlm: {c.verdict.event_visible}, saw {c.verdict.event_type_seen} "
                        f"({c.verdict.confidence:.2f}) — {c.verdict.caption}"
                    )
                )
                c.rejected = Rejected(camera_id=c.cam.id, stage="vlm", reason=why)
        chosen, verified = passed[:k], True
    else:
        chosen, verified = good[:k], False

    for rank, c in enumerate(chosen, 1):
        g = c.gate
        assert g is not None
        footage.append(
            Footage(
                event_id=ev.id,
                event_type=ev.type,
                camera_id=c.cam.id,
                rank=rank,
                verified=verified,
                media=_media_for(c.cam, proxy_base, g),
                verdict=c.verdict,
                why=f"{c.row['reason']}; {gates.freshness_note(g.age_s, c.cam.refresh_s)}",
                camera=CameraInfo(
                    id=c.cam.id,
                    name=c.cam.name,
                    lat=c.cam.lat,
                    lon=c.cam.lon,
                    alt_m=c.cam.alt_m,
                    tz=c.cam.tz,
                    azimuth_deg=c.cam.azimuth_deg,
                    source=c.cam.source,
                    page_url=c.cam.page_url,
                    attribution=c.cam.attribution,
                    license=c.cam.license,
                    distance_km=float(c.row["distance_km"]),
                    bearing_to_event=float(c.row["bearing_to_event"]),
                ),
                frame_ts=g.frame_ts,
                ts_source=g.ts_source,  # type: ignore[arg-type]
                fetched_at=now,
                hold_until=now + timedelta(seconds=prof.hold_s),
            )
        )

    res.footage = footage
    res.rejected = [c.rejected for c in cands if c.rejected]
    if footage:
        res.status = "FOOTAGE_FOUND"
    elif any(c.rejected and c.rejected.stage == "vlm" and c.verdict is None for c in good):
        res.status = "TIMEOUT"
        res.retry_after_s = 60
    elif any(c.verdict and c.verdict.event_visible in ("partial", "unsure") for c in good):
        res.status = "EVENT_NOT_VISIBLE"
        res.retry_after_s = prof.retry_after_s
    else:
        res.status = "NO_FOOTAGE_FOUND"
        res.retry_after_s = prof.retry_after_s
    res.elapsed_s = time.monotonic() - t0
    return res
