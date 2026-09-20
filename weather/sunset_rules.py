"""Simple sunset rule: mid/high cloud over the site, little low cloud along the sun ray."""
from common.geo import point_along
from weather.cloud_grid import CloudGrid

RAY_DISTANCES_KM = range(0, 125, 5)


def simple_rule(grid: CloudGrid, lat: float, lon: float, sun_azimuth: float) -> dict:
    ray = [point_along(lat, lon, sun_azimuth, d) for d in RAY_DISTANCES_KM]
    ray_lats, ray_lons = zip(*ray)
    site = {name: grid.disk_mean(name, lat, lon, 30) for name in ("lcc", "mcc", "hcc")}
    low_along_ray = float(grid.sample("lcc", ray_lats, ray_lons).mean())
    fires = max(site["mcc"], site["hcc"]) >= 30 and low_along_ray <= 30 and site["lcc"] <= 50
    return {"fires": bool(fires), "site_lcc": site["lcc"], "site_mcc": site["mcc"], "site_hcc": site["hcc"], "low_along_ray": low_along_ray}
