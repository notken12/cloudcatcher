"""Cron step after a weather reanalysis: rank cameras for every new observation and write
`event_cameras` (+ optionally run the fetch/gate/VLM resolver and write `event_footage`).

    uv run sunroof-camera match --db data/events.db            # rank only (ms per event)
    uv run sunroof-camera match --db data/events.db --resolve  # + footage for the top events
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
    pending = edb.unmatched(conn, rt)
    results: list[FootageResult] = []
    for obs in pending:
        ev = to_weather_event(obs)
        q = Event(
            type=ev.type,
            lat=ev.lat,
            lon=ev.lon,
            radius_km=ev.radius_km,
            t=ev.t_start,
            ignore_night=ignore_night,
        )
        cands = cat.find_cameras(q, k)
        rows = _camera_rows(cands)
        if resolve and len(results) < resolve_top and rows and http is not None:
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
            results.append(res)
            if on_footage is not None:
                await on_footage(res)
        edb.write_cameras(conn, ev.id, rt, rows)
        log.info("matched %s (%s): %d cameras", ev.id[:8], ev.type, len(rows))
    return results
