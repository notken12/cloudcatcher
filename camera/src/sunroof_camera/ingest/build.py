"""Run adapters -> per-source Parquet shards -> cameras.parquet (plan §2, §4 catalog refresh)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pandas as pd

from ..schema import empty_frame, read_parquet, to_frame, upsert, write_parquet
from .base import Adapter, make_client
from .registry import ADAPTERS

log = logging.getLogger(__name__)

DATA = Path("data")
SHARDS = DATA / "shards"
CATALOG = DATA / "cameras.parquet"


async def build_shard(adapter: Adapter, data_dir: Path = DATA) -> pd.DataFrame:
    async with make_client() as http:
        cams = await adapter.catalog(http)
    df = to_frame(cams)
    df = fill_tz(df)
    df = dedupe_views(df)
    out = data_dir / "shards" / f"{adapter.source}.parquet"
    write_parquet(df, out)
    log.info("%s: %d rows -> %s", adapter.source, len(df), out)
    return df


def fill_tz(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    from timezonefinder import TimezoneFinder

    tf = TimezoneFinder(in_memory=True)
    missing = df["tz"].isna()
    df.loc[missing, "tz"] = [
        tf.timezone_at(lat=la, lng=lo)
        for la, lo in zip(df.loc[missing, "lat"], df.loc[missing, "lon"])
    ]
    return df


def dedupe_views(df: pd.DataFrame) -> pd.DataFrame:
    """Within one source, drop rows with identical (lat, lon, azimuth) rounded to ~100 m / 15°."""
    if df.empty:
        return df
    key = (
        df["lat"].round(3).astype(str)
        + "|"
        + df["lon"].round(3).astype(str)
        + "|"
        + (df["azimuth_deg"] / 15).round().astype("Int64").astype(str)
        + "|"
        + df["id"].astype(str)
    )
    return df.loc[~key.duplicated()]


SOURCE_PRIORITY = [
    "manual",
    "panomax",
    "fotowebcam",
    "phenocam",
    "iem",
    "caltrans",
    "alertca",
    "digitraffic",
    "iceland",
    "cars_ny",
    "cars_on",
    "ndbc",
    "nps",
    "windy",
]


def merge_shards(data_dir: Path = DATA) -> pd.DataFrame:
    """Concat shards; cross-source dedupe keeps the higher-priority source (Windy re-lists Panomax etc.)."""
    frames = []
    for p in sorted((data_dir / "shards").glob("*.parquet")):
        frames.append(read_parquet(p))
    if not frames:
        return empty_frame()
    df = pd.concat(frames, ignore_index=True)
    rank = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
    df["_rank"] = df["source"].astype(str).map(lambda s: rank.get(s, len(rank)))
    df = df.sort_values("_rank")
    key = (
        df["lat"].round(3).astype(str)
        + "|"
        + df["lon"].round(3).astype(str)
        + "|"
        + (df["azimuth_deg"] / 15).round().astype("Int64").astype(str)
    )
    df = df.loc[~key.duplicated()].drop(columns="_rank")
    return df.reset_index(drop=True)


async def refresh(sources: list[str] | None = None, data_dir: Path = DATA) -> pd.DataFrame:
    names = sources or list(ADAPTERS)
    adapters = [ADAPTERS[n]() for n in names]
    results = await asyncio.gather(
        *(build_shard(a, data_dir) for a in adapters), return_exceptions=True
    )
    for a, r in zip(adapters, results):
        if isinstance(r, Exception):
            log.error("%s failed: %r", a.source, r)
    merged = merge_shards(data_dir)
    catalog_path = data_dir / "cameras.parquet"
    if catalog_path.exists():
        merged = upsert(read_parquet(catalog_path), merged)
    write_parquet(merged, catalog_path)
    log.info("catalog: %d rows -> %s", len(merged), catalog_path)
    return merged
