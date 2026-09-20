"""Sky segmentation with SegFormer-B0 (ADE20K, class 2 = sky). ~300 ms/frame on CPU.
Reliable on daytime frames (matches eyes within ~0.05); unreliable at night. Cache per camera - framing doesn't change."""
import sys
from functools import lru_cache

import numpy as np
import torch
from PIL import Image
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

MODEL = "nvidia/segformer-b0-finetuned-ade-512-512"
SKY_CLASS = 2
DEFAULT_MIN_SKY = 0.2


@lru_cache(maxsize=1)
def load_model():
    processor = SegformerImageProcessor.from_pretrained(MODEL)
    model = SegformerForSemanticSegmentation.from_pretrained(MODEL).eval()
    assert model.config.id2label[SKY_CLASS] == "sky"
    return processor, model


def sky_mask(image: Image.Image) -> np.ndarray:
    processor, model = load_model()
    with torch.no_grad():
        logits = model(**processor(images=image, return_tensors="pt")).logits
    upsampled = torch.nn.functional.interpolate(logits, size=image.size[::-1], mode="bilinear", align_corners=False)
    return upsampled.argmax(1)[0].numpy() == SKY_CLASS


def sky_fraction(image: Image.Image) -> float:
    return float(sky_mask(image).mean())


def top_band_luminance(image: Image.Image, band_fraction: float = 0.33) -> float:
    """Mean luminance of the top of the frame; >235 means the sky is blown out, <25 means night."""
    grey = np.asarray(image.convert("L"), dtype=float)
    return float(grey[: int(grey.shape[0] * band_fraction)].mean())


def is_usable_sky_frame(image: Image.Image, min_sky: float = DEFAULT_MIN_SKY) -> bool:
    luminance = top_band_luminance(image)
    return 25 <= luminance <= 235 and sky_fraction(image) >= min_sky


if __name__ == "__main__":
    for path in sys.argv[1:]:
        try:
            image = Image.open(path).convert("RGB")
        except Exception as error:
            print(f"{path}\tunreadable\t{error}")
            continue
        print(f"{path}\tsky_frac={sky_fraction(image):.3f}\ttop_lum={top_band_luminance(image):.0f}")
