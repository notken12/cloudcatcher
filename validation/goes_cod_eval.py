"""Does GOES-18 see the cirrus HRRR missed? Samples COD + ACHAC HT at every
West-Coast site in the 2026-09-20 ~02:00Z sunset test (scan starting 01:56:17Z)
and compares detection against the sunset colour index. Run from repo root:
uv run python validation/goes_cod_eval.py
"""
import json
import sys

import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, ".")
from weather.goes_abi import FixedGrid, open_dataset  # noqa: E402
from weather.goes_cloud import value_at  # noqa: E402

SCAN = "20262630156177"
KEYS = {
    "COD": f"ABI-L2-CODC/2026/263/01/OR_ABI-L2-CODC-M6_G18_s{SCAN}_e20262630158551_c20262630200313.nc",
    "HT": f"ABI-L2-ACHAC/2026/263/01/OR_ABI-L2-ACHAC-M6_G18_s{SCAN}_e20262630158551_c20262630200122.nc",
}


def main() -> None:
    sites = {s["id"]: s for s in json.load(open("validation/evB_sunset/west_sites.json"))}
    rows = json.load(open("validation/evB_sunset/west_color_index.json"))
    grids = {v: (open_dataset("noaa-goes18", k), k) for v, k in KEYS.items()}
    grids = {v: (ds, FixedGrid(ds), k) for v, (ds, k) in grids.items()}

    for r in rows:
        s = sites[r["id"]]
        r["cod"] = value_at(grids["COD"], s["lat"], s["lon"], half=2)
        r["ht_km"] = value_at(grids["HT"], s["lat"], s["lon"], half=1) / 1000

    usable = [r for r in rows if r["blown"] < 0.5 and r["sky"] > 0.2]
    for field in ("cod", "ht_km"):
        ok = [r for r in usable if np.isfinite(r[field])]
        rho, p = spearmanr([r[field] for r in ok], [r["color"] for r in ok])
        print(f"spearman({field}, color) = {rho:+.3f}  p={p:.3f}  n={len(ok)}")

    top = sorted(usable, key=lambda r: -r["color"])[:20]
    n_goes_cloud = sum(np.isfinite(r["cod"]) and r["cod"] > 0 or np.isfinite(r["ht_km"]) for r in top)
    n_hrrr_clear = sum(r["hcc"] <= 2 and r["mcc"] == 0 for r in top)
    print(f"\ntop-20 colour sites: GOES sees cloud at {n_goes_cloud}/20, HRRR reported hcc<=2%,mcc=0 at {n_hrrr_clear}/20")
    print(f"{'site':28} {'color':>6} {'hcc':>4} {'cod':>6} {'ht_km':>6}")
    for r in top[:12]:
        print(f"{r['site']:28} {r['color']:6.3f} {r['hcc']:4.0f} {r['cod']:6.1f} {r['ht_km']:6.2f}")

    json.dump(rows, open("validation/evB_sunset/west_goes_cod.json", "w"), indent=1)


if __name__ == "__main__":
    main()
