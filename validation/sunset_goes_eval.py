"""Does the Sunsethue-style classifier beat the ray model once GOES supplies the cloud field?

West-Coast blind-test evening (2026-09-20, sunsets 01:38-02:23Z): the 189 FAA sites with an objective colour
index (validation/evB_sunset/west_color_index.json). Every site is scored for sunset+15 min three ways: the
ray model on HRRR (the `quality` already in the index file), the classifier on HRRR, and the classifier on the
newest GOES-18 scan before sunset+15. HRRR t01z f01 (valid 02Z) supplies terrain and humidity throughout.
Frames with a blown-out sky or too little sky are excluded from the metrics.

Run from the repo root: uv run python -m validation.sunset_goes_eval
"""
import datetime as dt
import json

import numpy as np
from scipy.stats import spearmanr

from weather.goes_cloud import BUCKETS, load_field
from weather.hrrr import CloudGrid, HrrrCloudField, download_subset
from weather.sun import golden_hour_minutes
from weather.sunset_quality import score_site

SITES = "validation/evB_sunset/west_sites.json"
COLOUR = "validation/evB_sunset/west_color_index.json"
OUT = "validation/evB_sunset/west_goes_scores.json"
HRRR_KEY = "hrrr.20260920/conus/hrrr.t01z.wrfsfcf01.grib2"
WINNERS = ("Coast Life Support", "Merrell Rd", "Sugarloaf Ridge", "Plains", "Sea Ranch")
MODELS = ("rays_hrrr", "quality_hrrr", "quality_goes")


def score_all(sites: dict[int, dict], rows: list[dict], grid: CloudGrid) -> list[dict]:
    hrrr = HrrrCloudField(grid)
    fields = {}
    for row in rows:
        site = sites[row["id"]]
        sunset = dt.datetime.fromisoformat(site["sunset"])
        target = sunset + dt.timedelta(minutes=15)
        slot = target.replace(minute=target.minute - target.minute % 5, second=0, microsecond=0)
        if slot not in fields:
            fields[slot] = load_field(slot, BUCKETS["west"], grid)
        rh = float(grid.sample("rh", [site["lat"]], [site["lon"]])[0])
        golden = golden_hour_minutes(site["lat"], site["lon"], sunset.date(), 0)
        row["rays_hrrr"] = row["quality"]
        row["quality_hrrr"] = score_site(hrrr, site["lat"], site["lon"], site["az"], rh, golden)["quality"]
        goes = score_site(fields[slot], site["lat"], site["lon"], site["az"], rh, golden)
        row["quality_goes"] = goes["quality"]
        row["goes_scan"] = fields[slot].scanned_at.isoformat()
        row["goes_site"] = {k: v for k, v in goes.items() if k.startswith("site_") or k == "horizon_block"}
    return rows


def report(rows: list[dict]) -> None:
    usable = [r for r in rows if r["blown"] < 0.5 and r["sky"] > 0.2]
    colour = np.array([r["color"] for r in usable])
    by_colour = sorted(usable, key=lambda r: -r["color"])
    colour_top8 = {r["site"] for r in by_colour[:8]}
    print(f"n usable = {len(usable)}; mean colour {colour.mean():.3f}; colour top-8: {sorted(colour_top8)}")
    print(f"{'model':14} {'rho':>7} {'p':>7} {'top20 colour':>13} {'top8 hits':>10} {'winners in top decile':>22}")
    for model in MODELS:
        scores = np.array([r[model] for r in usable])
        rho, p = spearmanr(scores, colour)
        ranked = sorted(usable, key=lambda r: -r[model])
        top20 = np.mean([r["color"] for r in ranked[:20]])
        hits = len({r["site"] for r in ranked[:8]} & colour_top8)
        decile = {r["site"] for r in ranked[: len(ranked) // 10]}
        winners = sum(any(w in r for r in decile) for w in WINNERS)
        print(f"{model:14} {rho:+7.3f} {p:7.3f} {top20:13.3f} {hits:10d} {winners:15d}/{len(WINNERS)}")
    print(f"\n{'site':28} {'colour':>6} {'rays':>6} {'q_hrrr':>7} {'q_goes':>7}  goes site cover l/m/h top")
    for r in by_colour[:12]:
        g = r["goes_site"]
        print(f"{r['site']:28} {r['color']:6.3f} {r['rays_hrrr']:6.3f} {r['quality_hrrr']:7.3f} {r['quality_goes']:7.3f}  "
              f"{g['site_lcc']:3d}/{g['site_mcc']:3d}/{g['site_hcc']:3d} {g['site_top_km']}")
    print("\nclassifier+GOES top-8:")
    for r in sorted(usable, key=lambda r: -r["quality_goes"])[:8]:
        print(f"  {r['site']:28} q_goes {r['quality_goes']:.3f} colour {r['color']:.3f}")


def main() -> None:
    sites = {s["id"]: s for s in json.load(open(SITES))}
    rows = json.load(open(COLOUR))
    grid = CloudGrid(download_subset(HRRR_KEY, "/tmp/hrrr"))
    rows = score_all(sites, rows, grid)
    json.dump(rows, open(OUT, "w"), indent=1)
    report(rows)


if __name__ == "__main__":
    main()
