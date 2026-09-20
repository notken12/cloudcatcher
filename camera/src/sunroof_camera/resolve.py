"""`resolve_footage(event)`: find cameras → fetch → cheap gates → VLM → Footage / status.

One call per (event, ~10 min). Everything after find_cameras runs concurrently per camera
under a hard deadline; whatever is verified by then is returned.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
from PIL import Image

from . import gates, quality, solar, vlm
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


@dataclass(eq=False)  # identity semantics: `row` is a Series, which has no truth value
class _Candidate:
    row: pd.Series
    cam: Camera
    frame: Frame | None = None
    gate: gates.GateResult | None = None
    verdict: Verdict | None = None
    score: float = 0.0
    q: float = 0.5  # deterministic Stage-A quality, quality.score()
    rejected: Rejected | None = None
    burst: list[Frame] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_query_event(ev: WeatherEvent, ignore_night: bool = False) -> Event:
    return Event(
        ignore_night=ignore_night,
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
    ignore_night: bool = False,
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
        allow_night=prof.allow_night_frames or c.cam.night_ok or ignore_night,
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
    if g.features is None:
        g.features = quality.extract(Image.open(io.BytesIO(c.frame.content)))
    c.q = quality.score(g.features, prof.q)
    # Q only re-orders among frames that passed the gates; it never rejects here
    c.score = float(c.row["score"]) * g.score_mult * (0.5 + 0.5 * c.q)


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


def _vlm_reject(v: Verdict, ev_type: str, prof: EventProfile) -> str | None:
    """Per-type acceptance rules on the VLM verdict; None = passes."""
    if not v.usable:
        return "unusable frame"
    if v.event_visible not in ("yes", "partial"):
        return f"{v.event_visible}"
    if prof.require_yes and v.event_visible != "yes":
        return "only partial; this type needs a clear view"
    if v.event_type_seen != ev_type:
        return f"saw {v.event_type_seen}"
    if v.confidence < prof.min_conf:
        return f"confidence {v.confidence:.2f} < {prof.min_conf}"
    if v.night and not prof.allow_night_frames:
        return "night frame for a daytime event type"
    return None


def _passes(v: Verdict, ev_type: str, prof: EventProfile) -> bool:
    return _vlm_reject(v, ev_type, prof) is None


def is_night_for(ev: WeatherEvent, prof: EventProfile, at: datetime) -> float | None:
    """Solar elevation at the event if the event should be skipped as night, else None."""
    if prof.night_below_deg is None:
        return None
    el, _ = solar.sun_position_deg(ev.lat, ev.lon, at)
    el = float(el)
    return el if el < prof.night_below_deg else None


def vlm_top_n(prof: EventProfile, k: int) -> int:
    n = int(os.environ.get("SUNROOF_VLM_TOP_N", prof.vlm_top_n))
    return max(n, 1) if n > 0 else max(2 * k, 1)  # <=0 disables the cut (old 2k behaviour)


async def resolve_footage(
    ev: WeatherEvent,
    catalog: Catalog,
    http: httpx.AsyncClient,
    cache: FrameCache,
    k: int = 3,
    deadline_s: float = 30.0,
    proxy_base: str = "",
    verdict_log: str | None = None,
    ignore_night: bool = False,
) -> FootageResult:
    t0 = time.monotonic()
    now = ev.t_start if (ev.replay and ev.t_start) else _now()
    prof = profile(ev.type)
    res = FootageResult(event_id=ev.id, status="NO_CAMERAS_IN_RANGE")

    # ⓪ night: daytime-only types are not worth a single fetch after civil twilight
    if not ignore_night and (el := is_night_for(ev, prof, ev.t_start or now)) is not None:
        res.status = "CAMERAS_DARK"
        res.note = f"night at the event (sun {el:.0f}°); {ev.type} is a daytime type"
        res.retry_after_s = 3600
        res.elapsed_s = time.monotonic() - t0
        return res

    # ① which cameras
    hits = catalog.find_cameras(_to_query_event(ev, ignore_night), k=k * prof.fetch_mult)
    res.candidates = len(hits)
    if hits.empty:
        # distinguish "nothing nearby" from "nearby but filtered out (night / heading)"
        d = haversine_km(catalog.df["lat"].to_numpy(), catalog.df["lon"].to_numpy(), ev.lat, ev.lon)
        nearby = int((d <= PARAMS[ev.type].r_cap_km + ev.radius_km).sum())
        if nearby and ignore_night:  # night gate is off, so it was heading/health, not darkness
            res.status = "NO_FOOTAGE_FOUND"
        else:
            res.status = "CAMERAS_DARK" if nearby else "NO_CAMERAS_IN_RANGE"
        res.retry_after_s = 3600 if res.status == "CAMERAS_DARK" else None
        res.elapsed_s = time.monotonic() - t0
        return res

    cands = [_Candidate(row=r, cam=row_to_camera(r)) for _, r in hits.iterrows()]

    # ② fetch + cheap gates, concurrently, under the deadline
    budget = deadline_s * 0.6
    tasks = [
        asyncio.create_task(_fetch_and_gate(c, http, prof, ev, cache, now, ignore_night))
        for c in cands
    ]
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
    res.passed_gates = len(good)
    if not good:
        res.rejected = [c.rejected for c in cands if c.rejected]
        stale = [r for r in res.rejected if r.reason.startswith(("stale", "frozen", "not updated"))]
        res.status = "ALL_STALE" if stale and len(stale) == res.fetched else "NO_FOOTAGE_FOUND"
        res.retry_after_s = prof.retry_after_s
        res.elapsed_s = time.monotonic() - t0
        return res

    # ②b CV pre-gate + rank-then-cut (design doc §4.2): decide which frames the VLM sees.
    # Both only remove VLM calls; whatever survives is still judged by the VLM.
    if prof.min_sky_share > 0:
        for c in good:
            f = c.gate.features if c.gate else None
            if f is not None and f.sky_share < prof.min_sky_share:
                c.rejected = Rejected(
                    camera_id=c.cam.id,
                    stage="cv",
                    reason=f"cv: sky_share {f.sky_share:.2f} < {prof.min_sky_share} (no sky in view)",
                )
        good = [c for c in good if c.rejected is None]
    b = vlm.backend()
    top_n = vlm_top_n(prof, k) if b is not None else max(2 * k, 1)
    for c in good[top_n:]:
        c.rejected = Rejected(
            camera_id=c.cam.id, stage="cv", reason=f"cv: ranked below top-{top_n} by Q"
        )
    good = good[:top_n]
    res.cv_skipped = sum(1 for c in cands if c.rejected and c.rejected.stage == "cv")
    if not good:
        res.rejected = [c.rejected for c in cands if c.rejected]
        res.status = "EVENT_NOT_VISIBLE"
        res.retry_after_s = prof.retry_after_s
        res.elapsed_s = time.monotonic() - t0
        return res

    # ③ VLM
    remaining = max(deadline_s - (time.monotonic() - t0), 1.0)
    judged = False
    if b is not None:
        # local CPU models take ~30-100 s/frame and serialise requests, so judge only the
        # best-ranked candidates, `parallel` at a time, with at least `min_budget_s`
        sem = asyncio.Semaphore(b.parallel)

        async def _one(c: _Candidate) -> None:
            async with sem:
                await _judge(c, ev, prof, cache)

        jt = [asyncio.create_task(_one(c)) for c in good]
        _, pend = await asyncio.wait(jt, timeout=max(remaining, b.min_budget_s))
        for t in pend:
            t.cancel()
        res.vlm_calls = sum(1 for c in good if c.verdict is not None)
        judged = res.vlm_calls > 0
        if verdict_log:
            for c in good:
                if c.verdict and c.frame and c.gate:
                    vlm.log_verdict(
                        verdict_log,
                        c.cam.id,
                        ev.type,
                        c.verdict,
                        c.frame.sha1,
                        q=c.q,
                        features=c.gate.features.as_dict() if c.gate.features else None,
                    )
        if not judged:  # every call timed out / errored: degrade to gate-only rather than nothing
            for c in good:
                c.verdict = vlm.skipped_verdict(f"VLM ({vlm.describe()}) gave no verdict in time")
    else:
        for c in good:
            c.verdict = vlm.skipped_verdict("VLM skipped: no backend (OPENAI_API_KEY or Ollama)")

    # ④ route
    footage: list[Footage] = []
    if judged:
        passed = [c for c in good if c.verdict and _passes(c.verdict, ev.type, prof)]
        # VLM confidence decides presence; the deterministic Q decides which passing frame looks best
        passed.sort(key=lambda c: -(c.verdict.confidence * (0.5 + 0.5 * c.q)))  # type: ignore[union-attr]
        for c in good:
            if c not in passed:
                if c.verdict is None:
                    why = "vlm timeout"
                else:
                    rule = _vlm_reject(c.verdict, ev.type, prof)
                    why = f"vlm: {rule} — {c.verdict.caption}"
                c.rejected = Rejected(camera_id=c.cam.id, stage="vlm", reason=why)
        chosen, verified = passed[:k], True
    else:
        chosen, verified = good[:k], False
    # ⑤ worth showing? — a frame that passes everything but scores far below the type's floor
    low_q = [c for c in chosen if c.q < prof.min_q]
    for c in low_q:
        c.rejected = Rejected(
            camera_id=c.cam.id, stage="quality", reason=f"low quality Q={c.q:.2f}"
        )
    chosen = [c for c in chosen if c.q >= prof.min_q]

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
                quality=round(c.q, 3),
                features=g.features.as_dict() if g.features else None,
                why=f"{c.row['reason']}; {gates.freshness_note(g.age_s, c.cam.refresh_s)}; Q={c.q:.2f}",
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
    elif low_q:
        res.status = "LOW_QUALITY"
        res.retry_after_s = prof.retry_after_s
    elif judged and any(c.verdict and c.verdict.event_visible != "yes" for c in good):
        res.status = "EVENT_NOT_VISIBLE"
        res.retry_after_s = prof.retry_after_s
    elif any(c.rejected and c.rejected.stage == "vlm" and c.verdict is None for c in good):
        res.status = "TIMEOUT"
        res.retry_after_s = 60
    else:
        res.status = "NO_FOOTAGE_FOUND"
        res.retry_after_s = prof.retry_after_s
    res.elapsed_s = time.monotonic() - t0
    return res
