"""FastAPI service: the weather backend POSTs events, the frontend reads /feed or /stream.

    uv run sunroof-camera serve                 # http://127.0.0.1:8080  (sandbox page at /)
    uv run sunroof-camera serve --fake-events   # also replays fake events every 90 s

Endpoints
    POST /events                 WeatherEvent -> FootageResult (also pushed on /stream)
    GET  /feed                   latest FootageResult per event, newest first
    GET  /events/{id}/footage    one FootageResult
    GET  /stream                 SSE: `footage` (FootageResult JSON) and `ping`
    GET  /proxy/frame/{cam_id}   the verified JPEG (cached); frontend never talks to cameras
    GET  /                       sandbox page (static/index.html)
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from . import vlm
from .fetch import fetch_frame, row_to_camera
from .footage import FootageResult, WeatherEvent
from .ingest.base import make_client
from .query import Catalog
from .resolve import FrameCache, resolve_footage

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class State:
    def __init__(self, catalog_path: Path, verdict_log: Path | None):
        self.catalog_path = catalog_path
        self.verdict_log = str(verdict_log) if verdict_log else None
        self.catalog: Catalog | None = None
        self.cache = FrameCache()
        self.results: OrderedDict[str, FootageResult] = OrderedDict()
        self.subscribers: set[asyncio.Queue] = set()
        self.http = make_client(timeout=15.0)
        self.lock = asyncio.Lock()
        self.handle: Callable[[WeatherEvent], Awaitable[FootageResult]] | None = None

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
) -> FastAPI:
    st = State(catalog_path, verdict_log)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st.catalog = Catalog.load(st.catalog_path)
        log.info("catalog: %d cameras from %s", len(st.catalog.df), st.catalog_path)
        task = asyncio.create_task(_fake_loop(st, fake_period_s)) if fake_events else None
        yield
        if task:
            task.cancel()
        await st.http.aclose()

    app = FastAPI(title="sunroof camera", lifespan=lifespan)
    app.state.st = st

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
        st.results[ev.id] = res
        st.results.move_to_end(ev.id)
        while len(st.results) > 50:
            st.results.popitem(last=False)
        st.publish("footage", res.model_dump_json())
        log.info(
            "event %s (%s): %s — %d/%d candidates fetched, %d passed gates, %d vlm calls, %.1fs",
            ev.id,
            ev.type,
            res.status,
            res.fetched,
            res.candidates,
            res.passed_gates,
            res.vlm_calls,
            res.elapsed_s,
        )
        return res

    st.handle = handle

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

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    async def health() -> dict:
        return {
            "cameras": len(st.catalog.df) if st.catalog is not None else 0,
            "results": len(st.results),
            "vlm": vlm.describe() if vlm.available() else None,
        }

    return app


async def _fake_loop(st: State, period_s: float) -> None:
    """Pretend to be Ken's weather backend: post one fake event every `period_s`."""
    from .demo import fake_events

    await asyncio.sleep(1)
    while True:
        assert st.catalog is not None and st.handle is not None
        for ev in fake_events(st.catalog):
            try:
                await st.handle(ev)
            except Exception:  # noqa: BLE001
                log.exception("fake event %s failed", ev.id)
            await asyncio.sleep(period_s)


def run(host: str, port: int, **kw) -> None:
    import uvicorn

    uvicorn.run(create_app(**kw), host=host, port=port, log_level="info")
