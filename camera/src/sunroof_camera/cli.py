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
    )


@app.command()
def resolve(
    type: str,
    lat: float = typer.Option(...),
    lon: float = typer.Option(...),
    radius_km: float = 10,
    k: int = 3,
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
            res = await resolve_footage(ev, Catalog.load(catalog), http, cache, k=k)
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
