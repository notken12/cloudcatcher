"""Deterministic "is this frame worth showing?" score (docs/match-and-filter-design.md, Stage A).

All features come from a 128x128 RGB copy of the frame, ~1-2 ms, numpy only. They are meant to
*rank* frames (and reject only extremes); none of them is a detector. The sky region is
approximated as the upper `SKY_ROWS` fraction of the frame — fixed webcams keep the horizon put.

Features (all mapped to [0, 1], higher = better for the event type unless the type says otherwise):
  sky_share      fraction of the upper band that looks like sky (grey/white or blue), HSV heuristic
  colourfulness  Hasler & Süsstrunk (2003) metric on the upper band, /80
  warm_share     fraction of the upper band with a saturated warm hue (sunrise/sunset/aurora reds)
  texture        Laplacian variance of the upper band (cloud structure), /300
  clarity        1 - mean dark-channel (He et al. 2009) of the upper band: low for haze / white-out
  sharpness      Laplacian variance of the whole frame, /200
  exposure       1 - 4 * fraction of near-white pixels (clipped highlights)
The /N scale constants are provisional (set on synthetic frames); recalibrate from the
`features` column of data/verdicts.jsonl after a day of daylight runs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image

SIZE = 128
MARGIN = 0.07  # top/bottom strip dropped: burned-in caption bars would dominate texture/sharpness
SKY_ROWS = 0.6  # upper fraction of the (cropped) frame treated as "sky band"

FEATURES = (
    "sky_share",
    "colourfulness",
    "warm_share",
    "texture",
    "clarity",
    "sharpness",
    "exposure",
)


@dataclass(frozen=True)
class FrameFeatures:
    sky_share: float
    colourfulness: float
    warm_share: float
    texture: float
    clarity: float
    sharpness: float
    exposure: float
    mean_lum: float  # raw 0-255, not a ranking feature; kept for logging / night rules

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in asdict(self).items()}


def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    d = mx - mn
    v = mx
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0.0)
    h = np.zeros_like(mx)
    nz = d > 1e-6
    rm, gm, bm = (mx == r) & nz, (mx == g) & nz & (mx != r), (mx == b) & nz & (mx != r) & (mx != g)
    dd = np.maximum(d, 1e-6)
    h[rm] = ((g - b)[rm] / dd[rm]) % 6
    h[gm] = (b - r)[gm] / dd[gm] + 2
    h[bm] = (r - g)[bm] / dd[bm] + 4
    return h * 60.0, s, v


def _lap_var(gray: np.ndarray) -> float:
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    lap = (
        -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    )
    return float(lap.var())


def _dark_channel(rgb01: np.ndarray, patch: int = 8) -> float:
    """Mean of the patch-wise minimum over RGB (He et al. 2009). ~1 for haze / white-out."""
    dc = rgb01.min(-1)
    h, w = dc.shape
    h2, w2 = h - h % patch, w - w % patch
    blocks = dc[:h2, :w2].reshape(h2 // patch, patch, w2 // patch, patch).min(axis=(1, 3))
    return float(blocks.mean())


def _clip01(x: float) -> float:
    return float(min(max(x, 0.0), 1.0))


def extract(img: Image.Image) -> FrameFeatures:
    rgb = np.asarray(img.convert("RGB").resize((SIZE, SIZE)), dtype=np.float64)
    m = int(SIZE * MARGIN)
    rgb = rgb[m : SIZE - m]
    rgb01 = rgb / 255.0
    gray = rgb @ np.array([0.299, 0.587, 0.114])
    top = int(rgb.shape[0] * SKY_ROWS)
    sky_rgb, sky_gray = rgb01[:top], gray[:top]

    h, s, v = _rgb_to_hsv(sky_rgb)
    grey_sky = (s < 0.25) & (v > 0.45)
    blue_sky = (h > 185) & (h < 255) & (s > 0.15) & (v > 0.3)
    sky_share = float((grey_sky | blue_sky).mean())

    warm = ((h < 45) | (h > 320)) & (s > 0.35) & (v > 0.4)
    warm_share = float(warm.mean())

    r, g, b = sky_rgb[..., 0] * 255, sky_rgb[..., 1] * 255, sky_rgb[..., 2] * 255
    rg, yb = r - g, 0.5 * (r + g) - b
    colourfulness = float(
        np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    )

    texture = _lap_var(sky_gray)
    clarity = 1.0 - _dark_channel(sky_rgb)
    sharpness = _lap_var(gray)
    exposure = 1.0 - 4.0 * float((gray > 250).mean())

    return FrameFeatures(
        sky_share=_clip01(sky_share),
        colourfulness=_clip01(colourfulness / 80.0),
        warm_share=_clip01(warm_share),
        texture=_clip01(texture / 300.0),
        clarity=_clip01(clarity),
        sharpness=_clip01(sharpness / 200.0),
        exposure=_clip01(exposure),
        mean_lum=float(gray.mean()),
    )


def score(f: FrameFeatures, weights: dict[str, float]) -> float:
    """Weighted mean of the [0,1] features; weights are per event type (profiles.EventProfile.q)."""
    tot = sum(weights.values())
    if tot <= 0:
        return 0.5
    d = asdict(f)
    return _clip01(sum(w * d[k] for k, w in weights.items()) / tot)
