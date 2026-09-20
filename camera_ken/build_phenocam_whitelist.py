"""Score every live CONUS PhenoCam site's latest midday RGB frame for sky fraction and write the whitelist.
usage: uv run python -m camera_ken.build_phenocam_whitelist out_dir  (~10 min: 450 downloads + segmentation)"""
import concurrent.futures
import datetime as dt
import json
import os
import sys

from PIL import Image

from camera_ken.phenocam import download, live_sites, midday_images, rgb_twin, site_info
from camera_ken.sky_fraction import sky_fraction

out_dir = sys.argv[1]
frames_dir = os.path.join(out_dir, "midday")
sites = [s for s in live_sites(site_info(), dt.date.today()) if 24 < s["lat"] < 50 and -125 < s["lon"] < -66]


def fetch(site: dict) -> tuple[dict, str | None]:
    images = midday_images(site["site"])
    if not images:
        return site, None
    return site, download("https://phenocam.nau.edu" + rgb_twin(images[-1]["imgpath"]), frames_dir)


with concurrent.futures.ThreadPoolExecutor(10) as pool:
    fetched = list(pool.map(fetch, sites))

rows = []
for site, path in fetched:
    if path is None:
        continue
    rows.append({
        "site": site["site"], "lat": site["lat"], "lon": site["lon"], "tz": site["tzoffset"],
        "orientation": site.get("camera_orientation"), "date_start": site["date_start"],
        "sky_frac": round(sky_fraction(Image.open(path).convert("RGB")), 3),
    })
rows.sort(key=lambda r: -r["sky_frac"])
json.dump(rows, open(os.path.join(out_dir, "phenocam_sky_whitelist.json"), "w"), indent=1)
print(len(rows), "sites scored;", sum(r["sky_frac"] >= 0.2 for r in rows), "with sky >= 0.2")
