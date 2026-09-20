"""FastAPI service: the weather backend POSTs events, the frontend reads /feed or /stream.

    uv run sunroof-camera serve                 # http://127.0.0.1:8080  (sandbox page at /)
    uv run sunroof-camera serve --fake-events   # also replays fake events every 90 s

Endpoints
    GET  /events?type=&time=&limit=20   events from the SQLite store (--db), rarest/most severe
                                 first, each with its cron-matched ranked cameras + footage
    POST /events                 WeatherEvent -> FootageResult (also pushed on /stream)
    GET  /feed                   latest FootageResult per event, newest first
    GET  /events/{id}/footage    one FootageResult
    GET  /stream                 SSE: `footage` (FootageResult JSON) and `ping`
    GET  /proxy/frame/{cam_id}   the verified JPEG (cached); frontend never talks to cameras
    GET  /proxy/history/{cam_id}?ts=   archive JPEG at an instant (fotowebcam / phenocam / iem);
                                 ts carries the camera's UTC offset, e.g. 2023-06-15T18:00-06:00
    GET  /                       sandbox page (static/index.html)
    /push/*, /users/*            Web Push subscriptions + user preferences (push.py); every
                                 FOOTAGE_FOUND result fans out to subscribers, throttled
"""

from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field

from . import events_db as edb
from . import push as pushmod
from . import vlm
from .archive import archive_time, camera_from_id
from .fetch import fetch_frame, row_to_camera
from .footage import FootageResult, WeatherEvent
from .ingest.base import make_client
from .query import Catalog, EventType
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


class SubscribeBody(BaseModel):
    subscription: dict
    user_id: str | None = None


class EndpointBody(BaseModel):
    endpoint: str


class UserBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str | None = Field(None, max_length=120)
    likes: list[EventType] = []


class PrefsBody(BaseModel):
    likes: list[EventType]


class State:
    def __init__(self, catalog_path: Path, verdict_log: Path | None, db: Path | None = None):
        self.catalog_path = catalog_path
        self.db: sqlite3.Connection | None = edb.connect(db) if db else None
        self.verdict_log = str(verdict_log) if verdict_log else None
        self.catalog: Catalog | None = None
        self.cache = FrameCache()
        self.results: OrderedDict[str, FootageResult] = OrderedDict()
        self.subscribers: set[asyncio.Queue] = set()
        self.http = make_client(timeout=15.0)
        self.lock = asyncio.Lock()
        self.handle: Callable[[WeatherEvent], Awaitable[FootageResult]] | None = None
        self.match_pending: Callable[[str | None], Awaitable[list[FootageResult]]] | None = None
        self.push: pushmod.Notifier | None = None
        self.push_tasks: set[asyncio.Task] = set()

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
    push_db: Path | None = None,
    push_key: Path = Path("data/vapid.pem"),
    public_url: str | None = None,
) -> FastAPI:
    st = State(catalog_path, verdict_log, db)
    if push_db is not None:
        st.push = pushmod.Notifier(
            pushmod.PushStore(push_db), pushmod.load_vapid(push_key), public_url
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st.catalog = Catalog.load(st.catalog_path)
        log.info("catalog: %d cameras from %s", len(st.catalog.df), st.catalog_path)
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
        if st.push is not None:
            t = asyncio.create_task(st.push.notify(res))
            st.push_tasks.add(t)
            t.add_done_callback(st.push_tasks.discard)
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

    st.handle = handle
    st.match_pending = match_pending

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

    @app.get("/feed")
    async def feed() -> list[FootageResult]:
        return list(reversed(st.results.values()))

    @app.get("/events/{event_id}/footage", response_model=FootageResult)
    async def event_footage(event_id: str) -> FootageResult:
        if event_id not in st.results:
            raise HTTPException(404)
        return st.results[event_id]

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
        fr = await fetch_frame(st.http, cam, archive_time(cam.source, ts))
        if fr is None:
            raise HTTPException(502, "archive fetch failed")
        content, ctype = fr.content, fr.content_type or "image/jpeg"
        if w is not None:
            content, ctype = await asyncio.to_thread(downscale_jpeg, content, w)
        return Response(
            content,
            media_type=ctype,
            headers={"Cache-Control": "public, max-age=86400", "X-Frame-Url": fr.url},
        )

    def push_on() -> pushmod.Notifier:
        if st.push is None:
            raise HTTPException(503, "server started without --push-db")
        return st.push

    @app.get("/push/vapid-public-key")
    async def vapid_public_key() -> dict:
        return {"key": pushmod.public_key_b64(push_on().vapid)}

    @app.post("/push/subscribe", status_code=201)
    async def push_subscribe(body: SubscribeBody) -> dict:
        n = push_on()
        sub = body.subscription
        if not isinstance(sub.get("endpoint"), str) or "keys" not in sub:
            raise HTTPException(422, "not a PushSubscription")
        n.store.subscribe(sub, body.user_id)
        return {"ok": True, "subscriptions": n.store.count()}

    @app.post("/push/unsubscribe")
    async def push_unsubscribe(body: EndpointBody) -> dict:
        return {"removed": push_on().store.unsubscribe(body.endpoint)}

    @app.post("/users", status_code=201)
    async def create_user(body: UserBody) -> dict:
        return push_on().store.create_user(body.name, body.email, list(dict.fromkeys(body.likes)))

    @app.get("/users/{user_id}")
    async def get_user(user_id: str) -> dict:
        u = push_on().store.get_user(user_id)
        if u is None:
            raise HTTPException(404)
        return u

    @app.put("/users/{user_id}/prefs")
    async def set_prefs(user_id: str, body: PrefsBody) -> dict:
        u = push_on().store.set_likes(user_id, list(dict.fromkeys(body.likes)))
        if u is None:
            raise HTTPException(404)
        return u

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
            "push": (
                {
                    "subscriptions": st.push.store.count(),
                    "sent": st.push.sent,
                    "failed": st.push.failed,
                }
                if st.push is not None
                else None
            ),
        }

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
