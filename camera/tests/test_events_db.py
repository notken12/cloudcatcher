"""SQLite event store: merge rule, lookback, ranking, GET /events with cron-matched cameras."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from test_gates_resolve_server import NOW, STORM, _catalog, patched_fetch  # noqa: F401

from sunroof_camera import events_db as edb
from sunroof_camera.query import Catalog

T0 = NOW - timedelta(hours=1)
T1 = NOW - timedelta(minutes=30)
T2 = NOW


def storm(lat, lon, **kw):
    return {"id": "ps-1", "type": "storm", "lat": lat, "lon": lon, "radius_km": 30, **kw}


@pytest.fixture
def conn(tmp_path):
    return edb.connect(tmp_path / "events.db")


def test_upsert_merges_nearby_same_type_only(conn):
    (a,) = edb.upsert_run(conn, [storm(39.7, -104.9, score=0.5)], T0)
    ids = edb.upsert_run(
        conn,
        [
            storm(39.9, -104.7, score=0.8),  # ~28 km away, same type -> merge
            storm(41.5, -104.9, score=0.3),  # 200 km -> new
            {"id": "x", "type": "aurora", "lat": 39.7, "lon": -104.9},  # same spot, other type
            {"id": "y", "type": "fog", "lat": 39.7, "lon": -104.9},  # not a camera type: dropped
        ],
        T1,
    )
    assert ids[0] == a and len(ids) == 3 and len(set(ids)) == 3
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM events")}
    assert len(rows) == 3
    assert rows[a]["type"] == "thunderstorm" and rows[a]["time"] == edb.iso(T1)
    assert rows[a]["first_seen"] == edb.iso(T0) and rows[a]["severity"] == 0.8
    assert rows[a]["rarity"] == edb.RARITY_PRIOR["thunderstorm"]
    assert conn.execute("SELECT COUNT(*) FROM event_observations").fetchone()[0] == 4
    assert all(len(i) == 36 for i in ids)  # uuid4


def test_no_merge_after_window_or_within_one_run(conn):
    (a,) = edb.upsert_run(conn, [storm(39.7, -104.9)], T0)
    (b,) = edb.upsert_run(conn, [storm(39.7, -104.9)], T0 + timedelta(hours=3))
    assert a != b
    c, d = edb.upsert_run(
        conn, [storm(39.7, -104.9), storm(39.71, -104.9)], T0 + timedelta(hours=4)
    )
    assert c != d and b in (c, d)


def test_lookback_and_ranking(conn):
    edb.upsert_run(
        conn,
        [
            storm(39.7, -104.9, score=0.9),
            {
                "id": "a",
                "type": "aurora",
                "lat": 64.8,
                "lon": -147.7,
                "severity": 0.2,
                "rarity": 0.95,
            },
        ],
        T0,
    )
    edb.upsert_run(conn, [storm(39.9, -104.7, score=0.4)], T1)

    latest = edb.list_events(conn)
    assert [e["type"] for e in latest] == ["thunderstorm"]  # aurora not re-observed at T1
    assert latest[0]["severity"] == 0.4 and latest[0]["lat"] == 39.9

    past = edb.list_events(conn, time=T0 + timedelta(minutes=5))
    assert [e["type"] for e in past] == ["aurora", "thunderstorm"]  # rarity-weighted order
    assert past[1]["severity"] == 0.9 and past[1]["lat"] == 39.7 and past[1]["rank_score"] > 0
    assert past[0]["rank_score"] == pytest.approx(0.6 * 0.95 + 0.4 * 0.2)

    assert edb.list_events(conn, type="aurora", time=T0) and not edb.list_events(
        conn, type="aurora"
    )
    assert edb.list_events(conn, time=T0 - timedelta(days=1)) == []
    many = [storm(30 + i, -100, score=i / 40) for i in range(25)]
    edb.upsert_run(conn, many, T2)
    top = edb.list_events(conn)
    assert len(top) == 20 and top[0]["severity"] == 24 / 40


def test_import_events_json(conn, tmp_path):
    p = tmp_path / "events.json"
    p.write_text(
        json.dumps(
            {
                "when": "2026-09-20T04:00:00Z",
                "events": [storm(39.7, -104.9, score=0.5, evidence={"MESH": 3})],
            }
        )
    )
    (a,) = edb.import_events_json(conn, p)
    ev = edb.list_events(conn)[0]
    assert ev["id"] == a and ev["evidence"] == {"MESH": 3} and ev["time"] == "2026-09-20T04:00:00Z"
    assert ev["source_id"] == "ps-1" and ev["cameras"] == [] and ev["footage"] is None


async def test_match_run_and_get_events(tmp_path, conn, patched_fetch, monkeypatch):  # noqa: F811
    from sunroof_camera.match import match_run
    from sunroof_camera.server import create_app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cat = Catalog.load(_catalog(tmp_path))
    (eid,) = edb.upsert_run(conn, [STORM.model_dump()], T2)
    assert len(edb.unmatched(conn, edb.iso(T2))) == 1

    async with httpx.AsyncClient() as http:
        results = await match_run(conn, cat, k=5, resolve=True, http=http)
    assert len(results) == 1 and results[0].status == "FOOTAGE_FOUND"
    assert edb.unmatched(conn, edb.iso(T2)) == []
    ev = edb.list_events(conn)[0]
    cams = ev["cameras"]
    assert [c["rank"] for c in cams] == [1, 2] and {c["camera_id"] for c in cams} == {
        "east1",
        "east2",
    }
    assert cams[0]["media"] == {
        "kind": "image",
        "src": "/proxy/frame/" + cams[0]["camera_id"],
        "refresh_s": 300,
    }
    assert cams[0]["status"] == "FOOTAGE_FOUND" and cams[0]["verified"] is False
    assert ev["footage"]["event_id"] == eid and ev["footage"]["status"] == "FOOTAGE_FOUND"

    # rerun is a no-op; a later run with cameras not yet matched falls back to the older match
    async with httpx.AsyncClient() as http:
        assert await match_run(conn, cat, resolve=True, http=http) == []
    # the cron's periodic refresh re-resolves matched events (per-type budget), no new rows
    from sunroof_camera.match import refresh_run

    async with httpx.AsyncClient() as http:
        again = await refresh_run(conn, cat, per_type=1, http=http)
        assert [r.event_id for r in again] == [eid] and again[0].status == "FOOTAGE_FOUND"
        assert await refresh_run(conn, cat, per_type=0, http=http) == []
    assert len(edb.list_events(conn)[0]["cameras"]) == 2
    edb.upsert_run(conn, [STORM.model_dump()], T2 + timedelta(minutes=10))
    ev = edb.list_events(conn)[0]
    assert ev["id"] == eid and ev["cameras"][0]["matched_at"] == edb.iso(T2)

    app = create_app(
        catalog_path=_catalog(tmp_path), verdict_log=None, db=tmp_path / "events.db", watch_db_s=0
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/events")
            assert (
                r.status_code == 200
                and r.json()[0]["id"] == eid
                and len(r.json()[0]["cameras"]) == 2
            )
            assert (await c.get("/events", params={"type": "aurora"})).json() == []
            assert (await c.get("/events", params={"type": "fog"})).status_code == 422
            r = await c.get("/events", params={"time": (T2 - timedelta(days=1)).isoformat()})
            assert r.json() == []
            assert (await c.get("/health")).json()["db"] == edb.iso(T2 + timedelta(minutes=10))
            # the watcher path: match whatever the cron left unmatched
            st = app.state.st
            assert st.match_pending is not None
            assert len(await st.match_pending(None)) == 1
            assert edb.unmatched(conn, edb.iso(T2 + timedelta(minutes=10))) == []
            # the Refresh button: POST starts a pass, GET reports it, second POST is throttled
            assert (await c.get("/refresh")).json() == {"running": False}
            r = await c.post("/refresh")
            assert r.status_code == 202 and r.json()["running"] is True
            await st.refresh_task
            status = (await c.get("/refresh")).json()
            assert status["running"] is False and status["resolved"] == 1
            assert status["with_footage"] == 1 and len((await c.get("/feed")).json()) == 1
            r = await c.post("/refresh")
            assert r.status_code == 429 and "Retry-After" in r.headers

    app2 = create_app(catalog_path=_catalog(tmp_path), verdict_log=None)
    async with app2.router.lifespan_context(app2):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app2), base_url="http://t"
        ) as c:
            assert (await c.get("/events")).status_code == 503
            assert (await c.post("/refresh")).status_code == 503
