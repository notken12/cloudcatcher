"""Do our cloud fields agree with what human observers report? SYNOP genus stations worldwide give total cover in
oktas and which of the low/middle/high levels hold cloud; the same is sampled from the GFS layer cover and, where a
satellite covers the station, from the Himawari-9 / GOES field. This is the only free worldwide ground truth for the
fields the sunset classifier runs on outside the US (there is no colour index abroad).

Run from the repo root: uv run python -m validation.synop_field_check [2026-09-20T00:00Z]
"""
import datetime as dt
import sys

import numpy as np
from scipy.stats import spearmanr

from weather import gfs, goes_cloud, himawari_cloud, synop
from weather.cloud_grid import CloudGrid, LayerCloudField
from weather.grib_subset import download_subset

CACHE = "/tmp/cloudcatcher-grib"
MAX_AGE = dt.timedelta(hours=1)


def combined_cover(cover: np.ndarray) -> np.ndarray:
    """Total cover from three independent layers."""
    return 1.0 - np.prod(1.0 - cover, axis=-1)


def agreement(name: str, observed: np.ndarray, field: np.ndarray, oktas: np.ndarray) -> None:
    rho, p = spearmanr(oktas, combined_cover(field))
    print(f"{name:10} n={len(oktas):4d}  spearman(oktas, total cover) = {rho:+.3f} (p={p:.3f})")
    for li, level in enumerate(("low", "middle", "high")):
        reported = observed[:, li]
        present = field[:, li] > 0.1
        hit = (present & reported).sum() / max(reported.sum(), 1)
        false_alarm = (present & ~reported).sum() / max((~reported).sum(), 1)
        print(f"           {level:6}: observer reports it at {int(reported.sum()):3d} stations; field >10% at {hit:.0%} of those, at {false_alarm:.0%} of the others")


def main() -> None:
    when = dt.datetime.fromisoformat((sys.argv[1] if len(sys.argv) > 1 else "2026-09-20T00:00Z").replace("Z", "+00:00"))
    observations = [r for r in synop.reports(when, CACHE) if r.total_oktas is not None and r.total_oktas <= 8
                    and when - r.observed_at <= MAX_AGE]
    run_hours = gfs.run_and_hours(when, 1)
    assert run_hours is not None, "no GFS step valid at that time"
    run, hours = run_hours
    grid = CloudGrid(download_subset(gfs.BUCKET, gfs.key_for(run, hours[0]), CACHE, gfs.CLOUD_FIELDS))
    lats = np.array([r.lat for r in observations])
    lons = np.array([r.lon for r in observations])
    oktas = np.array([r.total_oktas for r in observations], dtype=float)
    observed = np.array([[bool(r.low), bool(r.middle), bool(r.high)] for r in observations])
    print(f"{len(observations)} genus reports within {MAX_AGE} before {when:%Y-%m-%dT%H:%MZ}; GFS {run:%Hz} f{hours[0]:03d}")
    gfs_cover = LayerCloudField(grid).columns(lats, lons).cover
    agreement("GFS", observed, gfs_cover, oktas)
    japan = (lats > 24) & (lats < 46) & (lons > 122) & (lons < 146)  # a window, not the 1.4 GB full disk
    himawari = himawari_cloud.load_field(when, lats[japan], lons[japan], 0.5, grid) if japan.any() else None
    if himawari is not None:
        agreement("Himawari", observed[japan], himawari.columns(lats[japan], lons[japan]).cover, oktas[japan])
    for bucket in goes_cloud.BUCKETS.values():
        goes = goes_cloud.load_field(when, bucket, grid)
        if goes is None:
            continue
        inside = np.array([goes.covers(lat, lon) for lat, lon in zip(lats, lons)])
        if inside.sum() >= 5:
            agreement(bucket, observed[inside], goes.columns(lats[inside], lons[inside]).cover, oktas[inside])


if __name__ == "__main__":
    main()
