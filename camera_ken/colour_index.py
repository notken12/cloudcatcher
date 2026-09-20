"""Sunset colour index: warm-hue chroma inside the sky mask. Picks real sunsets out of a batch of frames
(validated on 189 FAA West-Coast frames: its top-8 were the evening's best). Also reports how much of the sky is blown out."""
import sys

import numpy as np
from PIL import Image

from camera_ken.sky_fraction import sky_mask

WARM_HUE_MAX = 25      # PIL hue 0-255: red/orange
PINK_HUE_MIN = 200     # magenta/pink
MIN_SKY_PIXELS = 2000


def colour_index(image: Image.Image, mask: np.ndarray | None = None) -> dict | None:
    small = image.convert("RGB").resize((640, 360))
    sky = sky_mask(small) if mask is None else mask
    if sky.sum() < MIN_SKY_PIXELS:
        return None
    hsv = np.asarray(small.convert("HSV"), dtype=float)
    hue, sat, val = hsv[..., 0][sky], hsv[..., 1][sky] / 255, hsv[..., 2][sky] / 255
    warm = (hue <= WARM_HUE_MAX) | (hue >= PINK_HUE_MIN)
    return {"colour": float((sat * val * warm).mean()), "blown_out": float((val > 0.93).mean()), "sky_fraction": float(sky.mean())}


if __name__ == "__main__":
    for path in sys.argv[1:]:
        print(path, colour_index(Image.open(path)))
