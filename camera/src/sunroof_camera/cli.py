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
def schema():
    """Print the Parquet column -> dtype map as JSON."""
    from .schema import CAMERA_DTYPES

    typer.echo(json.dumps(CAMERA_DTYPES, indent=2))
