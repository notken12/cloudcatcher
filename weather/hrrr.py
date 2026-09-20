"""HRRR on s3://noaa-hrrr-bdp-pds. Hourly CONUS runs (f01 lands ~53 min after init), 3-hourly Alaska.
Whole files are 177 MB; the .idx sidecar lets us byte-range just the fields we need (~10 MB) — see
grib_subset.download_subset; cloud_grid.CloudGrid samples the result."""
import datetime as dt

from weather.grib_subset import exists

BUCKET = "noaa-hrrr-bdp-pds"

CLOUD_FIELDS = [
    ("LCDC", "low cloud layer"),
    ("MCDC", "middle cloud layer"),
    ("HCDC", "high cloud layer"),
    ("TCDC", "entire atmosphere"),
    ("HGT", "cloud ceiling"),
    ("HGT", "cloud base"),
    ("HGT", "cloud top"),
    ("HGT", "surface"),
    ("LAND", "surface"),
    ("HPBL", "surface"),
    ("RH", "2 m above ground"),
]


def key_for(run: dt.datetime, forecast_hour: int, domain: str = "conus") -> str:
    suffix = "" if domain == "conus" else ".ak"
    return f"hrrr.{run:%Y%m%d}/{domain}/hrrr.t{run:%H}z.wrfsfcf{forecast_hour:02d}{suffix}.grib2"


def latest_run(now: dt.datetime, forecast_hour: int, domain: str = "conus") -> dt.datetime | None:
    """Most recent run whose requested forecast hour is already uploaded."""
    step = 1 if domain == "conus" else 3
    run = now.replace(minute=0, second=0, microsecond=0)
    run -= dt.timedelta(hours=run.hour % step)
    for _ in range(8):
        if exists(BUCKET, key_for(run, forecast_hour, domain)):
            return run
        run -= dt.timedelta(hours=step)
    return None


if __name__ == "__main__":
    import os

    from weather.cloud_grid import CloudGrid
    from weather.grib_subset import download_subset

    now = dt.datetime.now(dt.UTC)
    run = latest_run(now, 1)
    assert run is not None, "no HRRR run uploaded in the last 8 hours"
    key = key_for(run, 1)
    path = download_subset(BUCKET, key, "/tmp/hrrr", CLOUD_FIELDS)
    grid = CloudGrid(path)
    print(key, "->", path, os.path.getsize(path) / 1e6, "MB; fields:", list(grid.fields))
    print("Lamar CO hcc/mcc/lcc:", [grid.sample(n, [38.077], [-102.696])[0] for n in ("hcc", "mcc", "lcc")])
