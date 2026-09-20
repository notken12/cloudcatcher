"""The cloud input of the sunset models: three height bands per column, each with a cover fraction, an
opacity to sunlight, and the vertical envelope the cloud occupies. HRRR and GOES both produce it."""
from dataclasses import dataclass
from typing import Protocol

import numpy as np

EARTH_RADIUS_KM = 6371.0
TROPOPAUSE_KM = 13.5
RAY_STEP_KM = 5.0  # the ray models walk in steps of this length; `opacity` is the light a step inside the cloud loses
LAYERS = [("lcc", 0.0, 3.5, 0.35), ("mcc", 3.5, 8.0, 0.8), ("hcc", 8.0, 13.5, 1.0)]  # name, bottom km, top km, sunset weight
OPACITY = {"lcc": 1.0, "mcc": 0.8, "hcc": 0.4}  # per-step opacity of HRRR's bands (thin high cloud lets light through)

LAYER_NAMES = [name for name, *_ in LAYERS]
LAYER_BOUNDS = [(bottom, top) for _, bottom, top, _ in LAYERS]
LAYER_WEIGHTS = np.array([weight for *_, weight in LAYERS])
OPACITY_VEC = np.array([OPACITY[name] for name in LAYER_NAMES])
BAND_EDGES_KM = np.array([top for _, _, top, _ in LAYERS])


@dataclass(frozen=True)
class CloudColumns:
    """Per-point cloud columns; (n, 3) arrays are per layer band, heights in km MSL."""

    cover: np.ndarray
    opacity: np.ndarray  # fraction of a sun ray's light lost per RAY_STEP_KM of travel inside the layer's cloud
    low: np.ndarray
    high: np.ndarray
    underside: np.ndarray  # where a sun ray reflects off the layer, for lighting the deck from below
    orog: np.ndarray


class CloudField(Protocol):
    def columns(self, lats: np.ndarray, lons: np.ndarray) -> CloudColumns: ...
