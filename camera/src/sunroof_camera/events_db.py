"""SQLite event store shared by the weather reanalysis job and `GET /events`.

The weather job calls `upsert_run(conn, events, run_time)` after every analysis (or writes
events.json and the cron runs `sunroof-camera import-events`). Then `sunroof-camera match`
ranks cameras for the new observations (match.py). The API only reads.

Tables (time = ISO-8601 UTC text, sorts lexicographically):
    events              one row per physical event, stable uuid; `time` = last analysis that
                        saw it, lat/lon/severity/... = latest values
    event_observations  one row per (event, analysis run) -> `?time=` looks back per run
    event_cameras       ranked cameras per (event, run) as matched by the cron step
    event_footage       FootageResult JSON per (event, run) when the resolver ran

Merge rule: an incoming event joins an existing one of the same type seen within
MERGE_WINDOW whose centre is within max(old.radius_km, MERGE_KM[type]); otherwise new uuid.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import geometry as g
from .query import EventType

# weather-side vocabulary -> camera/frontend vocabulary
TYPE_MAP: dict[str, EventType] = {
    "storm": "thunderstorm",
    "severe_storm": "thunderstorm",
    "convective": "thunderstorm",
    "thunderstorm": "thunderstorm",
    "lightning": "lightning",
    "sunset": "sunset",
    "sunrise": "sunrise",
    "aurora": "aurora",
    "mammatus": "mammatus",
    "lenticular": "lenticular",
    "undercast": "undercast",
    "rainbow": "rainbow",
}

# climatological prior used when the weather side sends no `rarity` (0 common .. 1 very rare)
RARITY_PRIOR: dict[str, float] = {
    "thunderstorm": 0.2,
    "lightning": 0.3,
    "sunset": 0.1,
    "sunrise": 0.1,
    "rainbow": 0.5,
    "undercast": 0.5,
    "mammatus": 0.8,
    "lenticular": 0.8,
    "aurora": 0.9,
}

# merge distance floor per type (storms move ~50 km per 30 min; aurora blobs are huge)
MERGE_KM: dict[str, float] = {
    "thunderstorm": 60.0,
    "lightning": 40.0,
    "mammatus": 30.0,
    "lenticular": 30.0,
    "undercast": 30.0,
    "aurora": 300.0,
    "sunrise": 50.0,
    "sunset": 50.0,
    "rainbow": 15.0,
}
MERGE_WINDOW = timedelta(hours=2)

RANK_W_RARITY, RANK_W_SEVERITY = 0.6, 0.4

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    time TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL, radius_km REAL NOT NULL,
    severity REAL, rarity REAL,
    evidence TEXT,
    source_id TEXT,
    t_start TEXT, t_end TEXT
);
CREATE INDEX IF NOT EXISTS events_type_time ON events(type, time);
CREATE TABLE IF NOT EXISTS event_observations (
    event_id TEXT NOT NULL REFERENCES events(id),
    time TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL, radius_km REAL NOT NULL,
    severity REAL, rarity REAL,
    evidence TEXT,
    PRIMARY KEY (event_id, time)
);
CREATE INDEX IF NOT EXISTS obs_time ON event_observations(time);
CREATE TABLE IF NOT EXISTS event_cameras (
    event_id TEXT NOT NULL, time TEXT NOT NULL,
    rank INTEGER NOT NULL,
    camera_id TEXT NOT NULL,
    name TEXT, lat REAL, lon REAL,
    distance_km REAL, bearing_deg REAL, score REAL,
    media_kind TEXT, media_src TEXT, page_url TEXT, refresh_s INTEGER,
    health TEXT, why TEXT,
    status TEXT, verified INTEGER, frame_ts TEXT,
    PRIMARY KEY (event_id, time, camera_id)
);
CREATE TABLE IF NOT EXISTS event_footage (
    event_id TEXT NOT NULL, time TEXT NOT NULL,
    result TEXT NOT NULL,
    PRIMARY KEY (event_id, time)
);
"""


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def rank_score(rarity: float | None, severity: float | None) -> float:
    return RANK_W_RARITY * (rarity or 0.0) + RANK_W_SEVERITY * (severity or 0.0)


def normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Weather-side dict (events.json item or WeatherEvent.model_dump()) -> row values.
    Returns None for types the camera side does not handle."""
    typ = TYPE_MAP.get(str(raw.get("type", "")).lower())
    if typ is None:
        return None
    sev = raw.get("severity", raw.get("score"))
    rar = raw.get("rarity")
    if rar is None:
        rar = RARITY_PRIOR[typ]
    ts = raw.get("t_start")
    te = raw.get("t_end")
    return {
        "type": typ,
        "lat": float(raw["lat"]),
        "lon": float(raw["lon"]),
        "radius_km": float(raw.get("radius_km") or 10.0),
        "severity": None if sev is None else float(sev),
        "rarity": float(rar),
        "evidence": json.dumps(raw.get("evidence")) if raw.get("evidence") is not None else None,
        "source_id": raw.get("id"),
        "t_start": iso(ts) if isinstance(ts, datetime) else ts,
        "t_end": iso(te) if isinstance(te, datetime) else te,
    }


def _find_merge_target(conn: sqlite3.Connection, ev: dict[str, Any], run_time: str) -> str | None:
    since = iso(parse_iso(run_time) - MERGE_WINDOW)
    rows = conn.execute(
        "SELECT id, lat, lon, radius_km FROM events WHERE type=? AND time>=? AND time<=?",
        (ev["type"], since, run_time),
    ).fetchall()
    best, best_d = None, float("inf")
    for r in rows:
        d = float(g.haversine_km(ev["lat"], ev["lon"], r["lat"], r["lon"]))
        if d <= max(float(r["radius_km"]), MERGE_KM[ev["type"]]) and d < best_d:
            best, best_d = r["id"], d
    return best


def upsert_run(
    conn: sqlite3.Connection, events: list[dict[str, Any]], run_time: datetime | str
) -> list[str]:
    """Merge one analysis run into the store; returns the event ids touched (in input order)."""
    t = run_time if isinstance(run_time, str) else iso(run_time)
    ids: list[str] = []
    with conn:
        for raw in events:
            ev = normalize(raw)
            if ev is None:
                continue
            eid = _find_merge_target(conn, ev, t)
            if eid is None or eid in ids:  # two objects in one run never merge into each other
                eid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO events(id,type,time,first_seen,lat,lon,radius_km,severity,rarity,"
                    "evidence,source_id,t_start,t_end) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        ev["type"],
                        t,
                        t,
                        ev["lat"],
                        ev["lon"],
                        ev["radius_km"],
                        ev["severity"],
                        ev["rarity"],
                        ev["evidence"],
                        ev["source_id"],
                        ev["t_start"],
                        ev["t_end"],
                    ),
                )
            else:
                conn.execute(
                    "UPDATE events SET time=?,lat=?,lon=?,radius_km=?,severity=?,rarity=?,"
                    "evidence=?,source_id=?,t_start=?,t_end=? WHERE id=?",
                    (
                        t,
                        ev["lat"],
                        ev["lon"],
                        ev["radius_km"],
                        ev["severity"],
                        ev["rarity"],
                        ev["evidence"],
                        ev["source_id"],
                        ev["t_start"],
                        ev["t_end"],
                        eid,
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO event_observations(event_id,time,lat,lon,radius_km,"
                "severity,rarity,evidence) VALUES (?,?,?,?,?,?,?,?)",
                (
                    eid,
                    t,
                    ev["lat"],
                    ev["lon"],
                    ev["radius_km"],
                    ev["severity"],
                    ev["rarity"],
                    ev["evidence"],
                ),
            )
            ids.append(eid)
    return ids


def import_events_json(conn: sqlite3.Connection, path: str | Path) -> list[str]:
    """weather/events.py output ({"when": ..., "events": [...]}) -> upsert_run."""
    payload = json.loads(Path(path).read_text())
    when = payload.get("when") or payload.get("generated_at") or iso(datetime.now(timezone.utc))
    return upsert_run(conn, payload["events"], when)


def run_time_at(conn: sqlite3.Connection, time: datetime | str | None) -> str | None:
    """Latest analysis run at or before `time` (None = latest overall)."""
    if time is None:
        row = conn.execute("SELECT MAX(time) FROM event_observations").fetchone()
    else:
        t = time if isinstance(time, str) else iso(time)
        row = conn.execute(
            "SELECT MAX(time) FROM event_observations WHERE time<=?", (t,)
        ).fetchone()
    return row[0] if row and row[0] else None


def runs_with_footage(conn: sqlite3.Connection, limit: int = 48) -> list[str]:
    """Run times that stored at least one FOOTAGE_FOUND result, newest first."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT time FROM event_footage WHERE result LIKE '%\"FOOTAGE_FOUND\"%' "
            "ORDER BY time DESC LIMIT ?",
            (limit,),
        )
    ]


def unmatched(conn: sqlite3.Connection, run_time: str) -> list[sqlite3.Row]:
    """Observations of `run_time` that have no event_cameras rows yet."""
    return conn.execute(
        "SELECT o.*, e.type, e.t_start FROM event_observations o JOIN events e ON e.id=o.event_id "
        "WHERE o.time=? AND NOT EXISTS (SELECT 1 FROM event_cameras c WHERE c.event_id=o.event_id "
        "AND c.time=o.time) ORDER BY o.rarity DESC, o.severity DESC",
        (run_time,),
    ).fetchall()


def with_cameras(conn: sqlite3.Connection, run_time: str) -> list[sqlite3.Row]:
    """Observations of `run_time` that have at least one matched camera, rarest first."""
    return conn.execute(
        "SELECT o.*, e.type, e.t_start FROM event_observations o JOIN events e ON e.id=o.event_id "
        "WHERE o.time=? AND EXISTS (SELECT 1 FROM event_cameras c WHERE c.event_id=o.event_id "
        "AND c.time=o.time) ORDER BY o.rarity DESC, o.severity DESC",
        (run_time,),
    ).fetchall()


def write_cameras(
    conn: sqlite3.Connection, event_id: str, run_time: str, rows: list[dict[str, Any]]
) -> None:
    with conn:
        conn.execute("DELETE FROM event_cameras WHERE event_id=? AND time=?", (event_id, run_time))
        conn.executemany(
            "INSERT INTO event_cameras(event_id,time,rank,camera_id,name,lat,lon,distance_km,"
            "bearing_deg,score,media_kind,media_src,page_url,refresh_s,health,why,status,verified,"
            "frame_ts) VALUES (:event_id,:time,:rank,:camera_id,:name,:lat,:lon,:distance_km,"
            ":bearing_deg,:score,:media_kind,:media_src,:page_url,:refresh_s,:health,:why,:status,"
            ":verified,:frame_ts)",
            [{"event_id": event_id, "time": run_time, **r} for r in rows],
        )


def write_footage(conn: sqlite3.Connection, event_id: str, run_time: str, result_json: str) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO event_footage(event_id,time,result) VALUES (?,?,?)",
            (event_id, run_time, result_json),
        )


def list_events(
    conn: sqlite3.Connection,
    type: str | None = None,
    time: datetime | str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """`GET /events`: events as known at the latest run <= time, rarest/most severe first,
    each with the cameras the cron step matched for that run (or the newest earlier match)."""
    rt = run_time_at(conn, time)
    if rt is None:
        return []
    sql = (
        "SELECT o.event_id AS id, e.type, o.time, e.first_seen, o.lat, o.lon, o.radius_km, "
        "o.severity, o.rarity, o.evidence, e.source_id, e.t_start, e.t_end "
        "FROM event_observations o JOIN events e ON e.id=o.event_id WHERE o.time=?"
    )
    args: list[Any] = [rt]
    if type:
        sql += " AND e.type=?"
        args.append(type)
    out = []
    for r in conn.execute(sql, args):
        ev = dict(r)
        ev["evidence"] = json.loads(ev["evidence"]) if ev["evidence"] else None
        ev["rank_score"] = round(rank_score(ev["rarity"], ev["severity"]), 4)
        ev["cameras"] = cameras_for(conn, ev["id"], rt)
        fr = conn.execute(
            "SELECT result FROM event_footage WHERE event_id=? AND time<=? ORDER BY time DESC LIMIT 1",
            (ev["id"], rt),
        ).fetchone()
        ev["footage"] = json.loads(fr["result"]) if fr else None
        out.append(ev)
    out.sort(key=lambda e: (-e["rank_score"], -(e["severity"] or 0), e["id"]))
    return out[:limit]


def cameras_for(conn: sqlite3.Connection, event_id: str, run_time: str) -> list[dict[str, Any]]:
    t = conn.execute(
        "SELECT MAX(time) FROM event_cameras WHERE event_id=? AND time<=?", (event_id, run_time)
    ).fetchone()[0]
    if t is None:
        return []
    rows = conn.execute(
        "SELECT * FROM event_cameras WHERE event_id=? AND time=? ORDER BY rank", (event_id, t)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("event_id")
        d["matched_at"] = d.pop("time")
        d["verified"] = None if d["verified"] is None else bool(d["verified"])
        d["media"] = {
            "kind": d.pop("media_kind"),
            "src": d.pop("media_src"),
            "refresh_s": d.pop("refresh_s"),
        }
        out.append(d)
    return out
