"""Cheap, LLM-free frame checks (plan §4 stage ②b–d). Bytes → timestamp → pixels.

`check_frame` returns a `GateResult`; `ok=False` frames never reach the VLM.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageOps

from .ingest.base import Frame

MIN_BYTES = 3000
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG"
RIFF_MAGIC = b"RIFF"  # WebP: 'RIFF....WEBP'

# SHA-1s of known "camera unavailable" placeholder images, learned offline; extend per source.
PLACEHOLDER_SHA1: set[str] = set()


@dataclass
class GateResult:
    ok: bool
    reason: str = ""
    frame_ts: datetime | None = None
    ts_source: str = "unknown"
    age_s: float | None = None
    width: int | None = None
    height: int | None = None
    mean_lum: float | None = None
    std_lum: float | None = None
    sharpness: float | None = None
    phash: int | None = None
    night: bool = False
    score_mult: float = 1.0
    notes: list[str] = field(default_factory=list)


def _exif_ts(img: Image.Image) -> datetime | None:
    try:
        exif = img.getexif()
    except Exception:
        return None
    raw = exif.get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def phash64(gray: np.ndarray) -> int:
    """8x8 DCT perceptual hash on a 32x32 grayscale array (no imagehash dependency)."""
    x = gray.astype(np.float64)
    n = x.shape[0]
    k = np.arange(n)
    c = np.cos(np.pi * (2 * k[:, None] + 1) * k[None, :] / (2 * n))
    dct = c.T @ x @ c
    low = dct[:8, :8]
    med = np.median(low.flatten()[1:])
    bits = (low > med).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def analyze_pixels(img: Image.Image) -> tuple[float, float, float, int]:
    g = ImageOps.grayscale(img)
    small = np.asarray(g.resize((256, 256)), dtype=np.float64)
    mean_lum = float(small.mean())
    std_lum = float(small.std())
    lap = (
        -4 * small[1:-1, 1:-1]
        + small[:-2, 1:-1]
        + small[2:, 1:-1]
        + small[1:-1, :-2]
        + small[1:-1, 2:]
    )
    sharpness = float(lap.var())
    ph = phash64(np.asarray(g.resize((32, 32)), dtype=np.float64))
    return mean_lum, std_lum, sharpness, ph


def check_frame(
    fr: Frame,
    refresh_s: int,
    now: datetime | None = None,
    max_age_s: float | None = None,
    allow_night: bool = False,
    last_sha1: str | None = None,
    last_phash: int | None = None,
    last_ts: datetime | None = None,
) -> GateResult:
    now = now or datetime.now(timezone.utc)
    res = GateResult(ok=False)

    # -- bytes --------------------------------------------------------------
    if not fr.content or len(fr.content) < MIN_BYTES:
        res.reason = f"too small ({len(fr.content or b'')} B)"
        return res
    head = fr.content[:12]
    is_webp = head.startswith(RIFF_MAGIC) and head[8:12] == b"WEBP"
    if not (head.startswith(JPEG_MAGIC) or head.startswith(PNG_MAGIC) or is_webp):
        res.reason = f"not an image (content-type {fr.content_type!r})"
        return res
    sha1 = fr.sha1
    if sha1 in PLACEHOLDER_SHA1:
        res.reason = "placeholder image"
        return res
    if last_sha1 and sha1 == last_sha1:
        res.reason = "frozen: identical bytes to previous frame"
        return res
    try:
        img = Image.open(io.BytesIO(fr.content))
        img.load()
    except Exception as e:  # noqa: BLE001 - PIL raises many types
        res.reason = f"undecodable image: {type(e).__name__}"
        return res
    res.width, res.height = img.size
    if img.width < 160 or img.height < 120:
        res.reason = f"tiny image {img.width}x{img.height}"
        return res

    # -- timestamp ------------------------------------------------------------
    if fr.ts is not None:
        res.frame_ts = fr.ts if fr.ts.tzinfo else fr.ts.replace(tzinfo=timezone.utc)
        res.ts_source = "source_api"  # adapters set ts from their API or Last-Modified
    else:
        ex = _exif_ts(img)
        if ex:
            res.frame_ts, res.ts_source = ex, "exif"
        else:
            res.frame_ts, res.ts_source = now, "fetch_time"
            res.notes.append("no timestamp on frame; using fetch time")
    res.age_s = (now - res.frame_ts).total_seconds()
    cap = max_age_s if max_age_s is not None else 2.0 * refresh_s
    if res.ts_source != "fetch_time" and res.age_s > cap:
        res.reason = f"stale: frame is {res.age_s / 60:.0f} min old (cap {cap / 60:.0f})"
        return res
    if last_ts is not None and res.frame_ts <= last_ts and res.ts_source != "fetch_time":
        res.reason = "not updated since last fetch"
        return res

    # -- pixels ----------------------------------------------------------------
    res.mean_lum, res.std_lum, res.sharpness, res.phash = analyze_pixels(img)
    if last_phash is not None and hamming(res.phash, last_phash) <= 2 and last_ts is not None:
        res.notes.append("near-identical to previous frame")
    res.night = res.mean_lum < 12
    if res.night:
        if not allow_night:
            res.reason = (
                f"dark frame (mean {res.mean_lum:.1f}) and camera not night-capable for this event"
            )
            return res
    elif res.std_lum < 4:
        res.reason = f"uniform frame (std {res.std_lum:.1f}) — white / grey / colour card"
        return res
    elif res.mean_lum > 240:
        res.reason = f"blown-out exposure (mean {res.mean_lum:.0f})"
        return res
    if res.sharpness < 15:
        res.score_mult *= 0.7
        res.notes.append("soft / low-detail frame")
    res.ok = True
    res.reason = "passed"
    return res


def freshness_note(age_s: float | None, refresh_s: int) -> str:
    if age_s is None or math.isnan(age_s):
        return "age unknown"
    return f"{age_s / 60:.0f} min old (cadence {refresh_s / 60:.0f} min)"
