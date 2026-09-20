"""Cron step after a weather reanalysis: rank cameras for every new observation and write
`event_cameras` (+ optionally run the fetch/gate/VLM resolver and write `event_footage`).

    uv run sunroof-camera match --db data/events.db            # rank only (ms per event)
    uv run sunroof-camera match --db data/events.db --resolve  # + footage for the top events
    uv run sunroof-camera cron --db data/events.db             # weather -> import -> match -> refresh loop
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import httpx
import pandas as pd

from . import events_db as edb
from .fetch import row_to_camera
from .footage import FootageResult, WeatherEvent
from .query import Catalog, Event
from .resolve import FrameCache, _media_for, resolve_footage

log = logging.getLogger(__name__)


def _camera_rows(cands: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for rank, (_, r) in enumerate(cands.iterrows(), 1):
        cam = row_to_camera(r)
        media = _media_for(cam, "", None)
        rows.append(
            {
                "rank": rank,
                "camera_id": cam.id,
                "name": cam.name,
                "lat": cam.lat,
                "lon": cam.lon,
                "distance_km": round(float(r["distance_km"]), 1),
                "bearing_deg": round(float(r["bearing_to_event"]), 0),
                "score": round(float(r["score"]), 3),
                "media_kind": media.kind,
                "media_src": media.src,
                "page_url": cam.page_url,
                "refresh_s": cam.refresh_s,
                "health": None if pd.isna(r.get("health")) else str(r["health"]),
                "why": str(r["reason"]),
                "status": None,
                "verified": None,
                "frame_ts": None,
            }
        )
    return rows


def _apply_footage(rows: list[dict[str, Any]], res: FootageResult) -> None:
    by_cam = {f.camera_id: f for f in res.footage}
    rejected = {r.camera_id: r for r in res.rejected}
    for row in rows:
        f = by_cam.get(row["camera_id"])
        if f is not None:
            row["status"] = "FOOTAGE_FOUND"
            row["verified"] = int(f.verified)
            row["frame_ts"] = edb.iso(f.frame_ts) if f.frame_ts else None
        elif row["camera_id"] in rejected:
            row["status"] = f"REJECTED_{rejected[row['camera_id']].stage.upper()}"
        else:
            row["status"] = res.status


def to_weather_event(obs: sqlite3.Row) -> WeatherEvent:
    return WeatherEvent(
        id=obs["event_id"],
        type=obs["type"],
        lat=obs["lat"],
        lon=obs["lon"],
        radius_km=obs["radius_km"],
        severity=obs["severity"],
        rarity=obs["rarity"],
        t_start=edb.parse_iso(obs["t_start"]) if obs["t_start"] else None,
    )


async def _match_one(
    conn: sqlite3.Connection,
    cat: Catalog,
    rt: str,
    obs: sqlite3.Row,
    k: int,
    resolve: bool,
    http: httpx.AsyncClient | None,
    cache: FrameCache | None,
    deadline_s: float,
    ignore_night: bool,
    verdict_log: str | None,
    on_footage,
) -> FootageResult | None:
    """Rank cameras for one observation -> event_cameras; with `resolve` and cameras in range
    also fetch/gate/VLM -> event_footage. Returns the FootageResult if one was produced."""
    ev = to_weather_event(obs)
    q = Event(
        type=ev.type,
        lat=ev.lat,
        lon=ev.lon,
        radius_km=ev.radius_km,
        t=ev.t_start,
        ignore_night=ignore_night,
    )
    rows = _camera_rows(cat.find_cameras(q, k))
    res = None
    if resolve and rows and http is not None:
        res = await resolve_footage(
            ev,
            cat,
            http,
            cache or FrameCache(),
            k=min(k, 3),
            deadline_s=deadline_s,
            verdict_log=verdict_log,
            ignore_night=ignore_night,
        )
        _apply_footage(rows, res)
        edb.write_footage(conn, ev.id, rt, res.model_dump_json())
        if on_footage is not None:
            await on_footage(res)
    edb.write_cameras(conn, ev.id, rt, rows)
    log.info(
        "matched %s (%s): %d cameras%s",
        ev.id[:8],
        ev.type,
        len(rows),
        f" -> {res.status}" if res else "",
    )
    return res


async def match_run(
    conn: sqlite3.Connection,
    cat: Catalog,
    run_time: str | None = None,
    k: int = 5,
    resolve: bool = False,
    resolve_top: int = 5,
    http: httpx.AsyncClient | None = None,
    cache: FrameCache | None = None,
    deadline_s: float = 30.0,
    ignore_night: bool = False,
    verdict_log: str | None = None,
    on_footage=None,
) -> list[FootageResult]:
    """Match (and optionally resolve) every unmatched observation of `run_time` (default latest).
    Returns the FootageResults produced."""
    rt = run_time or edb.run_time_at(conn, None)
    if rt is None:
        return []
    results: list[FootageResult] = []
    for obs in edb.unmatched(conn, rt):
        res = await _match_one(
            conn,
            cat,
            rt,
            obs,
            k,
            resolve and len(results) < resolve_top,
            http,
            cache,
            deadline_s,
            ignore_night,
            verdict_log,
            on_footage,
        )
        if res is not None:
            results.append(res)
    return results


async def refresh_run(
    conn: sqlite3.Connection,
    cat: Catalog,
    run_time: str | None = None,
    per_type: int = 3,
    k: int = 5,
    http: httpx.AsyncClient | None = None,
    cache: FrameCache | None = None,
    deadline_s: float = 30.0,
    ignore_night: bool = False,
    verdict_log: str | None = None,
    on_footage=None,
) -> list[FootageResult]:
    """Re-resolve live footage for the latest run: per event type, the `per_type` rarest / most
    severe events that have cameras in range. Cheap enough to run every few minutes (the
    weather reanalysis itself is much slower), so the feed follows the cameras, not the cron."""
    rt = run_time or edb.run_time_at(conn, None)
    if rt is None or http is None:
        return []
    picked: dict[str, int] = {}
    results: list[FootageResult] = []
    for obs in edb.with_cameras(conn, rt):
        if picked.get(obs["type"], 0) >= per_type:
            continue
        picked[obs["type"]] = picked.get(obs["type"], 0) + 1
        res = await _match_one(
            conn,
            cat,
            rt,
            obs,
            k,
            True,
            http,
            cache,
            deadline_s,
            ignore_night,
            verdict_log,
            on_footage,
        )
        if res is not None:
            results.append(res)
    return results
