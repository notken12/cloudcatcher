"""Score every site in a JSON list ([{id, lat, lon, az}, ...]) against an HRRR cloud subset with both sunset rules.
usage: uv run python -m weather.sunset_scan subset.grib2 sites.json out.json"""
import json
import sys

from weather.cloud_grid import CloudGrid
from weather.sunset_rays import score_site
from weather.sunset_rules import simple_rule

subset_path, sites_path, out_path = sys.argv[1:4]
grid = CloudGrid(subset_path)
results = []
for site in json.load(open(sites_path)):
    rays = score_site(grid, site["lat"], site["lon"], site["az"])
    simple = simple_rule(grid, site["lat"], site["lon"], site["az"])
    results.append({**site, **rays, "simple_rule_fires": simple["fires"], "simple_low_along_ray": round(simple["low_along_ray"])})
json.dump(results, open(out_path, "w"), indent=1)
for row in sorted(results, key=lambda r: -r["quality"])[:10]:
    print(row)
