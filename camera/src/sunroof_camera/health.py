"""Health probe (plan §4 job 2): fetch one frame per camera, log it, update the catalog's
health columns.

    data/health_log.parquet   one row per (camera, probe): ok, reason, sha1, lum, age, night
    data/cameras.parquet      last_frame_ts / last_ok_ts / fail_streak / health /
                              night_usable_frac / quality_score derived from the log

health:  live   = last probe OK and frame age <= 2*refresh_s (>= 15 min)
         stale  = last probe OK but old frame, or 1-2 consecutive failures after a success
         dead   = >= 3 consecutive failures, or no success for 24 h after having one
         unverified = never probed successfully and < 3 failures

Placeholders ("camera unavailable" cards) are detected per source: a sha1 shared by >= 3
cameras in one run, or a frame that is byte-identical to the previous probe of the same camera
while older than 6 h (frozen). Night usability = fraction of samples taken at solar elevation
< -6° whose frame passed the gates with mean luminance >= 12.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import numpy as np
import pandas as pd

from . import gates
from .fetch import fetch_frame, row_to_camera
from .ingest.base import make_client
from .schema import coerce, read_parquet, write_parquet
from .solar import sun_position_deg

log = logging.getLogger(__name__)

LOG_COLUMNS = {
    "id": "string",
    "source": "string",
    "ts": "datetime64[ns, UTC]",
    "ok": "bool",
    "reason": "string",
    "sha1": "string",
    "bytes": "int32",
    "age_s": "float64",
    "mean_lum": "float64",
    "sharpness": "float64",
    "solar_elev": "float64",
    "night": "bool",
}
DEAD_STREAK = 3
DEAD_AFTER = timedelta(hours=24)
FROZEN_AFTER = timedelta(hours=6)
PLACEHOLDER_MIN_CAMS = 3
NIGHT_WINDOW = 30  # samples per camera used for night_usable_frac


@dataclass
class Sample:
    id: str
    source: str
    ts: datetime
    ok: bool
    reason: str
    sha1: str | None
    bytes: int
    age_s: float | None
    mean_lum: float | None
    sharpness: float | None
    solar_elev: float
    night: bool


def _host(row: pd.Series) -> str:
    for col in ("image_url", "stream_url"):
        u = row[col]
        if isinstance(u, str) and u:
            return urlsplit(u).netloc or "?"
    return "?"


async def _probe_one(
    http: httpx.AsyncClient,
    row: pd.Series,
    now: datetime,
    solar_elev: float,
    prev_sha1: str | None,
    prev_ts: datetime | None,
    host_sem: asyncio.Semaphore,
) -> Sample:
    cam = row_to_camera(row)
    s = Sample(cam.id, cam.source, now, False, "", None, 0, None, None, None, solar_elev, False)
    try:
        async with host_sem:
            fr = await fetch_frame(http, cam)
    except Exception as e:  # noqa: BLE001 - any transport error is a failed probe
        s.reason = f"fetch error: {type(e).__name__}"
        return s
    if fr is None:
        s.reason = "no frame"
        return s
    s.bytes = len(fr.content or b"")
    g = gates.check_frame(fr, cam.refresh_s, now=now, max_age_s=float("inf"), allow_night=True)
    s.sha1 = fr.sha1
    s.age_s, s.mean_lum, s.sharpness = g.age_s, g.mean_lum, g.sharpness
    s.night = g.night
    if not g.ok:
        s.reason = g.reason
        return s
    if (
        prev_sha1 == fr.sha1
        and prev_ts is not None
        and now - prev_ts > FROZEN_AFTER
        and g.ts_source == "fetch_time"
    ):
        s.reason = "frozen: identical to probe >6h ago"
        return s
    s.ok, s.reason = True, "ok"
    return s


def _mark_placeholders(samples: list[Sample]) -> set[str]:
    """Same bytes from >= PLACEHOLDER_MIN_CAMS cameras of one source = 'unavailable' card."""
    counts: Counter[tuple[str, str]] = Counter(
        (s.source, s.sha1) for s in samples if s.sha1 and s.ok
    )
    bad = {sha for (_, sha), n in counts.items() if n >= PLACEHOLDER_MIN_CAMS}
    for s in samples:
        if s.sha1 in bad:
            s.ok, s.reason = False, "placeholder image (shared by several cameras)"
    return bad


def _select(
    df: pd.DataFrame, sources: list[str] | None, sample: int | None, tier: str | None
) -> pd.DataFrame:
    if sources:
        df = df[df["source"].astype(str).isin(sources)]
    if tier:
        df = df[df["health"].astype(str) == tier]
    if sample and len(df) > sample:
        df = df.sample(sample, random_state=None)
    return df


def read_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame({c: pd.Series(dtype=t) for c, t in LOG_COLUMNS.items()})
    return pd.read_parquet(path)


def apply_log(catalog: pd.DataFrame, hlog: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Derive the health columns of `catalog` from the full probe log."""
    if hlog.empty:
        return catalog
    hlog = hlog.sort_values("ts")
    last = hlog.groupby("id").tail(1).set_index("id")
    ok_rows = hlog[hlog["ok"]]
    last_ok = ok_rows.groupby("id").tail(1).set_index("id")

    def streak(g: pd.DataFrame) -> int:
        n = 0
        for ok in g["ok"].to_numpy()[::-1]:
            if ok:
                break
            n += 1
        return n

    streaks = hlog.groupby("id").apply(streak, include_groups=False)
    night = hlog[hlog["solar_elev"] < -6]
    night_frac = (
        night.groupby("id")
        .tail(NIGHT_WINDOW)
        .assign(usable=lambda d: d["ok"] & (d["mean_lum"].fillna(0) >= 12))
        .groupby("id")["usable"]
        .mean()
    )
    sharp = ok_rows.groupby("id").tail(NIGHT_WINDOW).groupby("id")["sharpness"].median()

    cat = catalog.set_index("id")
    cat["health"] = cat["health"].astype(str)
    ids = cat.index.intersection(last.index)
    cat.loc[ids, "fail_streak"] = streaks.reindex(ids).fillna(0).astype("int32")
    ok_ids = cat.index.intersection(last_ok.index)
    cat.loc[ok_ids, "last_ok_ts"] = last_ok.loc[ok_ids, "ts"]
    frame_ts = last_ok.loc[ok_ids, "ts"] - pd.to_timedelta(
        last_ok.loc[ok_ids, "age_s"].fillna(0), unit="s"
    )
    cat.loc[ok_ids, "last_frame_ts"] = frame_ts
    nf_ids = cat.index.intersection(night_frac.index)
    cat.loc[nf_ids, "night_usable_frac"] = night_frac.reindex(nf_ids)
    sh_ids = cat.index.intersection(sharp.index)
    cat.loc[sh_ids, "quality_score"] = 0.4 + 0.6 * np.clip(
        sharp.reindex(sh_ids).to_numpy(dtype=float) / 200.0, 0, 1
    )

    st = cat.loc[ids, "fail_streak"].to_numpy()
    last_ok_ts = cat.loc[ids, "last_ok_ts"]
    since_ok = (pd.Timestamp(now) - last_ok_ts).dt.total_seconds().to_numpy(dtype=float)
    has_ok = ~np.isnan(since_ok)
    frame_age = (pd.Timestamp(now) - cat.loc[ids, "last_frame_ts"]).dt.total_seconds()
    cap = np.maximum(2 * cat.loc[ids, "refresh_s"].to_numpy(dtype=float), 900)
    last_ok_now = last.loc[ids, "ok"].to_numpy()
    health = np.full(len(ids), "unverified", dtype=object)
    health[(st >= DEAD_STREAK) | (has_ok & (since_ok > DEAD_AFTER.total_seconds()))] = "dead"
    stale = (health == "unverified") & has_ok & (~last_ok_now | (frame_age.to_numpy() > cap))
    health[stale] = "stale"
    health[(health == "unverified") & has_ok & last_ok_now] = "live"
    cat.loc[ids, "health"] = health
    return coerce(cat.reset_index())


async def probe(
    data_dir: Path = Path("data"),
    sources: list[str] | None = None,
    sample: int | None = None,
    tier: str | None = None,
    concurrency: int = 64,
    per_host: int = 6,
    timeout_s: float = 15.0,
) -> pd.DataFrame:
    """Probe the selected cameras once, append to the log, rewrite the catalog. Returns samples."""
    cat_path, log_path = data_dir / "cameras.parquet", data_dir / "health_log.parquet"
    catalog = read_parquet(cat_path)
    hlog = read_log(log_path)
    sel = _select(catalog, sources, sample, tier)
    now = datetime.now(timezone.utc)
    sun_el, _ = sun_position_deg(sel["lat"].to_numpy(), sel["lon"].to_numpy(), now)
    prev = hlog.sort_values("ts").groupby("id").tail(1).set_index("id") if not hlog.empty else None

    sems: dict[str, asyncio.Semaphore] = {}
    gate = asyncio.Semaphore(concurrency)

    async def one(i: int, row: pd.Series) -> Sample:
        sem = sems.setdefault(_host(row), asyncio.Semaphore(per_host))
        p_sha, p_ts = None, None
        if prev is not None and row["id"] in prev.index:
            p_sha = prev.at[row["id"], "sha1"]
            p_ts = prev.at[row["id"], "ts"].to_pydatetime()
            p_sha = p_sha if isinstance(p_sha, str) else None
        async with gate:
            return await _probe_one(http, row, now, float(sun_el[i]), p_sha, p_ts, sem)

    async with make_client(timeout=timeout_s) as http:
        samples = await asyncio.gather(*(one(i, r) for i, (_, r) in enumerate(sel.iterrows())))

    placeholders = _mark_placeholders(samples)
    if placeholders:
        log.info("placeholder sha1s this run: %d", len(placeholders))
    new = pd.DataFrame([s.__dict__ for s in samples], columns=list(LOG_COLUMNS))
    for c, t in LOG_COLUMNS.items():
        new[c] = pd.to_datetime(new[c], utc=True) if t.startswith("datetime") else new[c].astype(t)
    hlog = pd.concat([hlog, new], ignore_index=True) if not hlog.empty else new
    hlog.to_parquet(log_path, index=False, compression="zstd")
    write_parquet(apply_log(catalog, hlog, now), cat_path)
    ok = int(new["ok"].sum())
    log.info("probed %d cameras: %d ok, %d failed", len(new), ok, len(new) - ok)
    return new
