"""FastAPI service: the weather backend POSTs events, the frontend reads /feed or /stream.

    uv run sunroof-camera serve                 # http://127.0.0.1:8080  (sandbox page at /)
    uv run sunroof-camera serve --fake-events   # also replays fake events every 90 s

Endpoints
    GET  /events?type=&time=&limit=20   events from the SQLite store (--db), rarest/most severe
                                 first, each with its cron-matched ranked cameras + footage
    POST /events                 WeatherEvent -> FootageResult (also pushed on /stream)
    GET  /feed                   frontend contract: best Footage per event with footage (each
                                 carries `event{type,lat,lon,place,rarity,severity}`), newest first;
                                 in-memory results first, then the store's latest run (--db)
    GET  /events/{id}/footage    Footage[] for one event (all ranks)
    GET  /events/{id}/result     the full FootageResult (statuses, rejections)
    GET  /cameras.geojson        Point per catalog camera (id, source, health) for the globe
    GET  /stream                 SSE: `footage` (FootageResult JSON) and `ping`
    GET  /proxy/frame/{cam_id}   the verified JPEG (cached); frontend never talks to cameras
    GET  /proxy/history/{cam_id}?ts=   archive JPEG at an instant (fotowebcam / phenocam / iem);
                                 ts carries the camera's UTC offset, e.g. 2023-06-15T18:00-06:00
    GET  /sandbox                sandbox page (static/index.html); also / unless --frontend DIR
                                 mounts the built SPA (frontend/dist) there — one origin, no CORS
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import events_db as edb
from . import vlm
from .archive import archive_time, camera_from_id
from .fetch import fetch_frame, row_to_camera
from .footage import EventRef, Footage, FootageResult, WeatherEvent, place_label
from .ingest.base import make_client
from .query import Catalog
from .resolve import FrameCache, resolve_footage

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def downscale_jpeg(content: bytes, width: int) -> tuple[bytes, str]:
    """Shrink to `width` px (JPEG q=80); passes non-image bytes through untouched."""
    try:
        im = Image.open(io.BytesIO(content))
        im.load()
    except (OSError, ValueError):
        return content, "image/jpeg"
    if im.width > width:
        im = im.convert("RGB").resize((width, round(im.height * width / im.width)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue(), "image/jpeg"
    return content, f"image/{(im.format or 'jpeg').lower()}"


class HistoryCache:
    """LRU of archive frames keyed by (camera, instant, width): the card and its globe pin ask
    for the same instant seconds apart, and a scrub back over a slider step is free."""

    def __init__(self, capacity: int = 600):
        self.capacity = capacity
        self._d: OrderedDict[tuple[str, str, int | None], tuple[bytes, str, str]] = OrderedDict()

    def get(self, key: tuple[str, str, int | None]) -> tuple[bytes, str, str] | None:
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def put(self, key: tuple[str, str, int | None], value: tuple[bytes, str, str]) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.capacity:
            self._d.popitem(last=False)


class State:
    def __init__(self, catalog_path: Path, verdict_log: Path | None, db: Path | None = None):
        self.catalog_path = catalog_path
        self.db: sqlite3.Connection | None = edb.connect(db) if db else None
        self.verdict_log = str(verdict_log) if verdict_log else None
        self.catalog: Catalog | None = None
        self.cache = FrameCache()
        self.results: OrderedDict[str, FootageResult] = OrderedDict()
        self.history = HistoryCache()
        self.subscribers: set[asyncio.Queue] = set()
        self.http = make_client(timeout=15.0)
        self.lock = asyncio.Lock()
        self.handle: Callable[[WeatherEvent], Awaitable[FootageResult]] | None = None
        self.match_pending: Callable[[str | None], Awaitable[list[FootageResult]]] | None = None
        self.refresh_task: asyncio.Task | None = None
        self.refresh_status: dict = {"running": False}

    def publish(self, kind: str, data: str) -> None:
        for q in list(self.subscribers):
            q.put_nowait((kind, data))


def create_app(
    catalog_path: Path = Path("data/cameras.parquet"),
    verdict_log: Path | None = Path("data/verdicts.jsonl"),
    fake_events: bool = False,
    fake_period_s: float = 90.0,
    k: int = 3,
    deadline_s: float = 30.0,
    ignore_night: bool = False,
    db: Path | None = None,
    watch_db_s: float = 30.0,
    frontend: Path | None = None,
    refresh_per_type: int = 3,
    refresh_min_s: float = 60.0,
) -> FastAPI:
    st = State(catalog_path, verdict_log, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st.catalog = Catalog.load(st.catalog_path, verdict_log=st.verdict_log)
        log.info("catalog: %d cameras from %s", len(st.catalog.df), st.catalog_path)
        if st.catalog.track is not None:
            n = sum(s.judged for s in st.catalog.track.per_type.values())
            log.info("camera track record: %d past verdicts from %s", n, st.verdict_log)
        log.info("vlm: %s", vlm.describe())  # raises here on a misconfigured backend
        tasks = []
        if fake_events:
            tasks.append(asyncio.create_task(_fake_loop(st, fake_period_s, ignore_night)))
        if st.db is not None and watch_db_s > 0:
            tasks.append(asyncio.create_task(_db_watch_loop(st, watch_db_s)))
        yield
        for t in tasks:
            t.cancel()
        await st.http.aclose()

    app = FastAPI(title="sunroof camera", lifespan=lifespan)
    app.state.st = st

    async def record(res: FootageResult, ev_type: str | None = None) -> None:
        ev_type = ev_type or (res.footage[0].event_type if res.footage else "?")
        st.results[res.event_id] = res
        st.results.move_to_end(res.event_id)
        while len(st.results) > 50:
            st.results.popitem(last=False)
        st.publish("footage", res.model_dump_json())
        log.info(
            "event %s (%s): %s — %d/%d candidates fetched, %d passed gates, %d vlm calls, %.1fs",
            res.event_id,
            ev_type,
            res.status,
            res.fetched,
            res.candidates,
            res.passed_gates,
            res.vlm_calls,
            res.elapsed_s,
        )

    async def handle(ev: WeatherEvent) -> FootageResult:
        assert st.catalog is not None
        async with st.lock:  # one event at a time keeps the demo predictable
            res = await resolve_footage(
                ev,
                st.catalog,
                st.http,
                st.cache,
                k=k,
                deadline_s=deadline_s,
                verdict_log=st.verdict_log,
                ignore_night=ignore_night,
            )
        await record(res, ev.type)
        return res

    async def match_pending(run_time: str | None = None) -> list[FootageResult]:
        """Rank + resolve every observation of the latest run that the cron has not matched."""
        assert st.catalog is not None and st.db is not None
        from .match import match_run

        async with st.lock:
            return await match_run(
                st.db,
                st.catalog,
                run_time,
                k=max(k, 5),
                resolve=True,
                http=st.http,
                cache=st.cache,
                deadline_s=deadline_s,
                ignore_night=ignore_night,
                verdict_log=st.verdict_log,
                on_footage=record,
            )

    async def refresh_now() -> None:
        """Manual footage refresh (the Refresh button): same pass the cron runs every few minutes."""
        assert st.catalog is not None and st.db is not None
        from .match import refresh_run

        st.refresh_status = {"running": True, "started": edb.iso(datetime.now(timezone.utc))}
        try:
            async with st.lock:
                results = await refresh_run(
                    st.db,
                    st.catalog,
                    per_type=refresh_per_type,
                    k=max(k, 5),
                    http=st.http,
                    cache=st.cache,
                    deadline_s=deadline_s,
                    ignore_night=ignore_night,
                    verdict_log=st.verdict_log,
                    on_footage=record,
                )
            st.refresh_status = {
                "running": False,
                "finished": edb.iso(datetime.now(timezone.utc)),
                "resolved": len(results),
                "with_footage": sum(1 for r in results if r.footage),
            }
        except Exception as e:  # noqa: BLE001
            log.exception("manual refresh failed")
            st.refresh_status = {"running": False, "error": str(e)}
        finally:
            st.publish("refresh", json.dumps(st.refresh_status))

    st.handle = handle
    st.match_pending = match_pending

    @app.post("/refresh", status_code=202)
    async def refresh() -> dict:
        """Start a footage refresh pass now; poll GET /refresh (or listen for the `refresh` SSE
        event) for completion. One at a time, at most one per --refresh-min-s."""
        if st.db is None:
            raise HTTPException(503, "server started without --db")
        if st.refresh_task is not None and not st.refresh_task.done():
            return st.refresh_status
        fin = st.refresh_status.get("finished")
        if fin is not None:
            wait = refresh_min_s - (datetime.now(timezone.utc) - edb.parse_iso(fin)).total_seconds()
            if wait > 0:
                raise HTTPException(
                    429,
                    f"refreshed {int(refresh_min_s - wait)}s ago; retry in {int(wait) + 1}s",
                    headers={"Retry-After": str(int(wait) + 1)},
                )
        st.refresh_task = asyncio.create_task(refresh_now())
        return {"running": True}

    @app.get("/refresh")
    async def refresh_status() -> dict:
        return st.refresh_status

    @app.get("/events")
    async def list_events(
        type: str | None = None,
        time: datetime | None = Query(None, description="ISO instant; default latest run"),
        limit: int = Query(20, ge=1, le=100),
    ) -> list[dict]:
        if st.db is None:
            raise HTTPException(503, "server started without --db")
        if type is not None and type not in edb.RARITY_PRIOR:
            raise HTTPException(422, f"unknown type {type!r}")
        if time is not None and time.tzinfo is None:
            time = time.replace(tzinfo=timezone.utc)
        return edb.list_events(st.db, type, time, limit)

    @app.post("/events", response_model=FootageResult)
    async def post_event(ev: WeatherEvent) -> FootageResult:
        return await handle(ev)

    def stored_results(
        limit: int = 1000, run_time: str | None = None
    ) -> list[tuple[FootageResult, EventRef]]:
        """Footage the cron/watcher resolved into the store for the latest run, rarest first."""
        if st.db is None:
            return []
        out = []
        for ev in edb.list_events(st.db, None, run_time, limit):
            if not ev.get("footage"):
                continue
            res = FootageResult.model_validate(ev["footage"])
            ref = EventRef(
                type=ev["type"],
                lat=ev["lat"],
                lon=ev["lon"],
                radius_km=ev["radius_km"] or 10.0,
                place=place_label(ev["lat"], ev["lon"]),
                rarity=ev["rarity"],
                severity=ev["severity"],
            )
            out.append((res, ref))
        return out

    def with_event(f: Footage, ref: EventRef) -> Footage:
        return f if f.event is not None else f.model_copy(update={"event": ref})

    def find_result(event_id: str) -> tuple[FootageResult, EventRef | None] | None:
        if event_id in st.results:
            return st.results[event_id], None
        runs = [None] + (edb.runs_with_footage(st.db) if st.db is not None else [])
        for rt in runs:
            for res, ref in stored_results(run_time=rt):
                if res.event_id == event_id:
                    return res, ref
        return None

    @app.get("/feed")
    async def feed() -> list[Footage]:
        """One card per event that has footage: rank-1 frame, `event` joined in."""
        seen: set[str] = set()
        rows: list[Footage] = []
        for res in reversed(st.results.values()):
            if res.footage:
                seen.add(res.event_id)
                rows.append(res.footage[0])
        for res, ref in stored_results():
            if res.footage and res.event_id not in seen:
                seen.add(res.event_id)
                rows.append(with_event(res.footage[0], ref))
        if rows or st.db is None:
            return rows
        # Fallback: nothing verified in the current run (night, no cameras in range) —
        # serve the newest earlier run that did have verified sights rather than an empty page.
        for rt in edb.runs_with_footage(st.db):
            for res, ref in stored_results(run_time=rt):
                if res.footage and res.event_id not in seen:
                    seen.add(res.event_id)
                    rows.append(with_event(res.footage[0], ref))
            if rows:
                break
        return rows

    @app.get("/events/{event_id}/footage")
    async def event_footage(event_id: str) -> list[Footage]:
        hit = find_result(event_id)
        if hit is None:
            raise HTTPException(404)
        res, ref = hit
        return [with_event(f, ref) if ref else f for f in res.footage]

    @app.get("/events/{event_id}/result", response_model=FootageResult)
    async def event_result(event_id: str) -> FootageResult:
        hit = find_result(event_id)
        if hit is None:
            raise HTTPException(404)
        return hit[0]

    @app.get("/cameras.geojson")
    async def cameras_geojson() -> Response:
        assert st.catalog is not None
        df = st.catalog.df
        feats = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
                "properties": {"id": cid, "source": src, "health": health},
            }
            for cid, src, health, lat, lon in zip(
                df["id"].astype(str),
                df["source"].astype(str),
                df["health"].astype(str),
                df["lat"].astype(float),
                df["lon"].astype(float),
            )
        ]
        body = json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":"))
        return Response(
            body,
            media_type="application/geo+json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/stream")
    async def stream(request: Request) -> StreamingResponse:
        q: asyncio.Queue = asyncio.Queue()
        st.subscribers.add(q)

        async def gen():
            try:
                for r in list(st.results.values())[-5:]:
                    yield f"event: footage\ndata: {r.model_dump_json()}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: {kind}\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        yield "event: ping\ndata: {}\n\n"
            finally:
                st.subscribers.discard(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/proxy/frame/{camera_id:path}")
    async def proxy_frame(camera_id: str) -> Response:
        cf = st.cache.get(camera_id)
        if cf is None:
            assert st.catalog is not None
            rows = st.catalog.df.loc[st.catalog.df["id"] == camera_id]
            if rows.empty:
                raise HTTPException(404)
            fr = await fetch_frame(st.http, row_to_camera(rows.iloc[0]))
            if fr is None:
                raise HTTPException(502, "camera fetch failed")
            return Response(fr.content, media_type=fr.content_type or "image/jpeg")
        return Response(
            cf.content,
            media_type=cf.content_type,
            headers={
                "Cache-Control": "no-store",
                "X-Frame-Ts": cf.frame_ts.isoformat() if cf.frame_ts else "",
            },
        )

    @app.get("/proxy/history/{camera_id:path}")
    async def proxy_history(
        camera_id: str,
        ts: datetime = Query(
            ...,
            description="ISO time with the camera's UTC offset (e.g. 2023-06-15T18:00-06:00); "
            "naive = camera-local wall clock",
        ),
        w: int | None = Query(None, ge=64, le=2048, description="downscale to this width"),
    ) -> Response:
        cam = None
        if st.catalog is not None:
            rows = st.catalog.df.loc[st.catalog.df["id"] == camera_id]
            if not rows.empty:
                cam = row_to_camera(rows.iloc[0])
        if cam is None:
            cam = camera_from_id(camera_id)
        if cam is None:
            raise HTTPException(404)
        if cam.history_kind == "none":
            raise HTTPException(422, "camera has no archive")
        when = archive_time(cam.source, ts)
        key = (camera_id, when.isoformat(timespec="minutes"), w)
        hit = st.history.get(key)
        if hit is None:
            fr = await fetch_frame(st.http, cam, when)
            if fr is None:
                raise HTTPException(502, "archive fetch failed")
            content, ctype = fr.content, fr.content_type or "image/jpeg"
            if w is not None:
                content, ctype = await asyncio.to_thread(downscale_jpeg, content, w)
            hit = (content, ctype, fr.url)
            st.history.put(key, hit)
        content, ctype, url = hit
        return Response(
            content,
            media_type=ctype,
            headers={"Cache-Control": "public, max-age=86400", "X-Frame-Url": url},
        )

    @app.get("/sandbox")
    async def sandbox() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    if frontend is None:

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(STATIC / "index.html")

    @app.get("/health")
    async def health() -> dict:
        return {
            "cameras": len(st.catalog.df) if st.catalog is not None else 0,
            "results": len(st.results),
            "db": edb.run_time_at(st.db, None) if st.db is not None else None,
            "vlm": vlm.describe() if vlm.available() else None,
            "vlm_usage": vlm.USAGE.as_dict(),
        }

    if frontend is not None:
        if not (frontend / "index.html").is_file():
            raise FileNotFoundError(f"--frontend {frontend}: no index.html (run `pnpm build`)")
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")

    return app


async def _fake_loop(st: State, period_s: float, ignore_night: bool = False) -> None:
    """Pretend to be Ken's weather backend: post one fake event every `period_s`."""
    from .demo import fake_events

    await asyncio.sleep(1)
    while True:
        assert st.catalog is not None and st.handle is not None
        for ev in fake_events(st.catalog, ignore_night=ignore_night):
            try:
                if st.db is not None:  # same path as the real cron: upsert -> match -> footage
                    now = datetime.now(timezone.utc)
                    edb.upsert_run(st.db, [ev.model_dump()], now)
                    assert st.match_pending is not None
                    await st.match_pending(edb.iso(now))
                else:
                    await st.handle(ev)
            except Exception:  # noqa: BLE001
                log.exception("fake event %s failed", ev.id)
            await asyncio.sleep(period_s)


async def _db_watch_loop(st: State, period_s: float) -> None:
    """Poll the SQLite store: whenever the weather job wrote a run the cron did not match, do it."""
    while True:
        await asyncio.sleep(period_s)
        try:
            assert st.db is not None and st.match_pending is not None
            rt = edb.run_time_at(st.db, None)
            if rt and edb.unmatched(st.db, rt):
                await st.match_pending(rt)
        except Exception:  # noqa: BLE001
            log.exception("db watch failed")


def run(host: str, port: int, **kw) -> None:
    import uvicorn

    uvicorn.run(create_app(**kw), host=host, port=port, log_level="info")
