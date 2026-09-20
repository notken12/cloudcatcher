"""`uv run sunroof-camera --help`"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import typer

from .ingest import build
from .ingest.registry import ADAPTERS
from .query import Catalog, Event

app = typer.Typer(no_args_is_help=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@app.command()
def refresh(
    source: list[str] = typer.Option(None, help="adapter names; default all"),
    data_dir: Path = Path("data"),
):
    """Pull catalogs -> data/shards/*.parquet -> data/cameras.parquet."""
    df = asyncio.run(build.refresh(source or None, data_dir))
    typer.echo(df["source"].value_counts().to_string())


@app.command()
def sources():
    for s in ADAPTERS:
        typer.echo(s)


@app.command()
def find(
    type: str,
    lat: float = typer.Option(...),
    lon: float = typer.Option(...),
    radius_km: float = 10,
    k: int = 10,
    t: datetime | None = None,
    catalog: Path = Path("data/cameras.parquet"),
):
    """Query: sunroof-camera find thunderstorm --lat 39.7 --lon=-104.9 --radius-km 20"""
    ev = Event(type=type, lat=lat, lon=lon, radius_km=radius_km, t=t)
    res = Catalog.load(catalog).find_cameras(ev, k)
    cols = [
        "id",
        "name",
        "distance_km",
        "bearing_to_event",
        "azimuth_deg",
        "score",
        "health",
        "reason",
        "image_url",
    ]
    typer.echo(res[cols].to_string(index=False))


@app.command()
def health(
    source: list[str] = typer.Option(None, help="adapter names; default all"),
    sample: int | None = typer.Option(None, help="probe a random subset of N cameras"),
    tier: str | None = typer.Option(
        None, help="only cameras with this health: live|stale|dead|unverified"
    ),
    concurrency: int = 64,
    data_dir: Path = Path("data"),
):
    """Fetch one frame per camera -> data/health_log.parquet -> health columns in cameras.parquet."""
    from .health import probe

    res = asyncio.run(probe(data_dir, source or None, sample, tier, concurrency))
    typer.echo(res["reason"].str.split(":").str[0].value_counts().to_string())


@app.command()
def describe(catalog: Path = Path("data/cameras.parquet")):
    """Row counts by source / heading_conf / night_ok / health."""
    df = Catalog.load(catalog).df
    typer.echo(f"{len(df)} rows")
    for c in ["source", "source_kind", "heading_conf", "night_ok", "health", "history_kind"]:
        typer.echo(f"\n{c}:\n{df[c].value_counts(dropna=False).to_string()}")


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8080,
    catalog: Path = Path("data/cameras.parquet"),
    fake_events: bool = typer.Option(False, help="replay fake weather-backend events"),
    fake_period_s: float = 90.0,
    k: int = 3,
    deadline_s: float = 30.0,
    ignore_night: bool = typer.Option(
        False, help="demo: skip the solar night gate and accept dark frames"
    ),
    db: Path | None = typer.Option(
        None, help="SQLite event store (events_db.py); enables GET /events and the db watcher"
    ),
    watch_db_s: float = typer.Option(
        30.0, help="poll --db for unmatched runs every N s (0 = rely on `match` cron only)"
    ),
    frontend: Path | None = typer.Option(
        None, help="serve the built SPA (frontend/dist) at /; sandbox page moves to /sandbox"
    ),
):
    """Run the camera service + sandbox page (see server.py for endpoints)."""
    from .server import run

    run(
        host,
        port,
        catalog_path=catalog,
        fake_events=fake_events,
        fake_period_s=fake_period_s,
        k=k,
        deadline_s=deadline_s,
        ignore_night=ignore_night,
        db=db,
        watch_db_s=watch_db_s,
        frontend=frontend,
    )


@app.command("import-events")
def import_events(
    path: Path = typer.Argument(..., help="weather/events.py output (events.json)"),
    db: Path = Path("data/events.db"),
):
    """Merge one weather analysis run (events.json) into the SQLite store."""
    from . import events_db as edb

    conn = edb.connect(db)
    ids = edb.import_events_json(conn, path)
    typer.echo(f"{len(ids)} events merged into {db} (run {edb.run_time_at(conn, None)})")


@app.command()
def match(
    db: Path = Path("data/events.db"),
    catalog: Path = Path("data/cameras.parquet"),
    k: int = 5,
    resolve: bool = typer.Option(False, help="also fetch/gate/VLM the top events -> event_footage"),
    resolve_top: int = 5,
    deadline_s: float = 30.0,
    ignore_night: bool = False,
    run_time: str | None = typer.Option(None, help="ISO run to match; default latest"),
    verdict_log: Path = typer.Option(
        Path("data/verdicts.jsonl"), help="VLM log: read for the camera track record, appended to"
    ),
):
    """Cron step after `import-events`: rank cameras per new event -> event_cameras."""
    from . import events_db as edb
    from .ingest.base import make_client
    from .match import match_run
    from .resolve import FrameCache

    async def go():
        conn = edb.connect(db)
        async with make_client(timeout=15.0) as http:
            return await match_run(
                conn,
                Catalog.load(catalog, verdict_log=verdict_log),
                run_time,
                k=k,
                resolve=resolve,
                resolve_top=resolve_top,
                http=http,
                cache=FrameCache(),
                deadline_s=deadline_s,
                ignore_night=ignore_night,
                verdict_log=str(verdict_log),
            )

    results = asyncio.run(go())
    for r in results:
        typer.echo(f"{r.event_id[:8]} {r.status} footage={len(r.footage)}")


@app.command()
def resolve(
    type: str,
    lat: float = typer.Option(...),
    lon: float = typer.Option(...),
    radius_km: float = 10,
    k: int = 3,
    deadline_s: float = 30.0,
    ignore_night: bool = False,
    catalog: Path = Path("data/cameras.parquet"),
    save_frames: Path | None = typer.Option(None, help="dir to dump the chosen JPEGs"),
):
    """One-shot: fake one event, print the FootageResult JSON."""
    from .footage import WeatherEvent
    from .ingest.base import make_client
    from .resolve import FrameCache, resolve_footage

    async def go():
        ev = WeatherEvent(id=f"cli-{type}", type=type, lat=lat, lon=lon, radius_km=radius_km)
        cache = FrameCache()
        async with make_client(timeout=15.0) as http:
            res = await resolve_footage(
                ev,
                Catalog.load(catalog),
                http,
                cache,
                k=k,
                deadline_s=deadline_s,
                ignore_night=ignore_night,
            )
        if save_frames:
            save_frames.mkdir(parents=True, exist_ok=True)
            for f in res.footage:
                cf = cache.get(f.camera_id)
                if cf:
                    (save_frames / f"{f.camera_id.replace(':', '_')}.jpg").write_bytes(cf.content)
        return res

    typer.echo(asyncio.run(go()).model_dump_json(indent=2))


@app.command()
def schema():
    """Print the Parquet column -> dtype map as JSON."""
    from .schema import CAMERA_DTYPES

    typer.echo(json.dumps(CAMERA_DTYPES, indent=2))


@app.command()
def track(
    verdict_log: Path = Path("data/verdicts.jsonl"),
    type: str | None = typer.Option(None, help="only this event type"),
    top: int = 20,
):
    """Cameras with confirmed sightings so far (the demo shortlist), from the VLM log."""
    from .track import CameraTrack

    rows = CameraTrack.from_log(verdict_log).summary(type)[:top]
    for r in rows:
        typer.echo(
            f"{r['camera_id']:<36} {r['event_type']:<12} hits={r['hits']:>4.1f}/{r['judged']:<4} "
            f"rate={r['hit_rate']:.2f} Q={r['mean_q']:.2f} vlmQ={r['mean_vlm_quality']:.1f} "
            f"x{r['multiplier']:.2f}"
        )
