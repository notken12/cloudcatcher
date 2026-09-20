"""GFS 0.25 deg on s3://noaa-gfs-bdp-pds: the global counterpart of the HRRR cloud subset (runs every 6 h,
hourly steps, ~4 h upload lag). Same layer-cover, humidity, terrain and land fields as HRRR plus simulated
composite reflectivity and CAPE for a storm prior; no cloud base/top."""
import datetime as dt

from weather.grib_subset import exists

BUCKET = "noaa-gfs-bdp-pds"
RUN_STEP = dt.timedelta(hours=6)

CLOUD_FIELDS = [
    ("LCDC", "low cloud layer"),
    ("MCDC", "middle cloud layer"),
    ("HCDC", "high cloud layer"),
    ("HGT", "cloud ceiling"),
    ("HGT", "surface"),
    ("LAND", "surface"),
    ("RH", "2 m above ground"),
    ("REFC", "entire atmosphere"),
    ("CAPE", "surface"),
]


def key_for(run: dt.datetime, forecast_hour: int) -> str:
    return f"gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{forecast_hour:03d}"


def run_and_hours(when: dt.datetime, n_hours: int) -> tuple[dt.datetime, list[int]] | None:
    """The newest run whose steps valid at `when` .. `when + n_hours - 1` are all uploaded (steps land in order,
    so the last one existing is enough), with those step numbers; None if no run in the last two days has them."""
    first_valid = when.replace(minute=0, second=0, microsecond=0)
    run = first_valid - dt.timedelta(hours=first_valid.hour % 6)
    for _ in range(8):
        first = int((first_valid - run).total_seconds() // 3600)
        if exists(BUCKET, key_for(run, first + n_hours - 1)):
            return run, list(range(first, first + n_hours))
        run -= RUN_STEP
    return None
