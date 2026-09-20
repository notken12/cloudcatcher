"""Event detector: the weather half of cloudcatcher.

Every run (or every --loop seconds) writes an events.json of camera-side WeatherEvent records
(type, lat, lon, radius_km, t_start, t_end, severity, replay) and, with --post, POSTs each one to the
camera service:

    uv run python -m weather.events --out out/events.json [--when 2026-09-20T04:30Z] [--sunset] [--aurora]
                                    [--sites sites.json] [--post http://localhost:8000] [--loop 600]

Rules, no ML:
- thunderstorm: ProbSevere objects with COMPREF >= 50 and (FLASH_RATE >= 5 or ProbSevere >= 30)
- aurora (--aurora): OVATION probability >= 20, dark sky, and little low cloud (HRRR lcc)
- sunset (--sunset): a 1-degree CONUS lattice; points whose sunset+15 min lies within 90 min after
  the newest GOES scan are scored by the Sunsethue-whitepaper classifier (weather/sunset_quality.py)
  on the GOES cloud field (weather/goes_cloud.py; it sees the thin cirrus HRRR misses). Every land
  point with severity >= 0.5 becomes an event.

--sites scores individual camera sites for their next sunset+15 min the same way; when no recent
GOES scan covers the site, the HRRR ray model (weather/sunset_rays.score_site) scores it from the
forecast instead.

With --when, storms and site scores replay from the S3 archives (ProbSevere back to 2020-10, HRRR to
2014); OVATION is live-only, so --aurora with --when uses *current* space weather.
"""
import argparse
import datetime as dt
import json
import os
import tempfile
import time

import numpy as np
import requests

from common.geo import bearing_deg, distance_km
from weather import mrms
from weather.goes_cloud import BUCKETS, GoesCloudField, bucket_for, load_field
from weather.hrrr import CloudGrid, download_subset, key_for, latest_run
from weather.sun import golden_hour_minutes, sun_azimuth, sun_elevation, sunset_utc
from weather.sunset_quality import score_site as quality_score
from weather.sunset_rays import score_site as rays_score
from weather.swpc_aurora import aurora_rule, ovation

AURORA_LAT = np.arange(50.0, 71.01, 0.5)
AURORA_LON = np.arange(-169.0, -66.01, 0.5)

STORM_MIN_COMPREF = 50.0
STORM_MIN_FLASH = 5.0
STORM_MIN_PROBSEVERE = 30.0
DAYLIGHT_ELEVATION_DEG = -6.0
AURORA_RADIUS_KM = 150
AURORA_MAX_LCC = 30.0
COLOUR_PEAK_AFTER_SUNSET = dt.timedelta(minutes=15)
COLOUR_WINDOW = (dt.timedelta(minutes=5), dt.timedelta(minutes=25))
GOES_MAX_LEAD = dt.timedelta(minutes=90)  # a GOES scan is a nowcast; beyond this lead the HRRR forecast is the better input
SUNSET_LAT = np.arange(25.0, 49.01, 1.0)
SUNSET_LON = np.arange(-125.0, -66.99, 1.0)
SUNSET_RADIUS_KM = 60          # half a lattice cell's diagonal; the camera side looks 50 km around the point
SUNSET_QUALITY_FULL = 0.06     # classifier quality worth severity 1: the West-Coast test's best sites, Lamar 5/5 via GOES 0.05
SUNSET_MIN_SEVERITY = 0.5

GRIB_DIR = os.environ.get("CLOUDCATCHER_GRIB_DIR", "/tmp/cloudcatcher-hrrr")


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y%m%dT%H%MZ")


def parse_when(text: str | None) -> dt.datetime:
    if text is None:
        return dt.datetime.now(dt.UTC)
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt.UTC)


def write_atomic(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, path)


def storm_events(when: dt.datetime) -> tuple[list[dict], dict]:
    features = mrms.probsevere_objects(when)
    events = []
    for feature in features:
        p = feature["properties"]
        compref = p.get("COMPREF")
        compref = float(compref) if compref is not None else None
        mesh = float(p.get("MESH") or 0)
        flash = float(p.get("FLASH_RATE") or 0)
        prob_severe = float(p.get("ProbSevere") or p.get("PS") or 0)  # PS is the pre-2024 ProbSevere field
        # 2024-era archive files lack COMPREF entirely; MESH >= 0.5 in is the closest intensity gate there.
        strong = compref >= STORM_MIN_COMPREF if compref is not None else mesh >= 0.5
        if not strong or (flash < STORM_MIN_FLASH and prob_severe < STORM_MIN_PROBSEVERE):
            continue
        lat, lon = p["centroid_lat"], p["centroid_lon"]
        daylight = sun_elevation(lat, lon, when) > DAYLIGHT_ELEVATION_DEG
        valid = dt.datetime.fromisoformat(p["valid_time"])
        events.append({
            "id": f"storm-{p['ID']}-{stamp(valid)}",
            "type": "thunderstorm",
            "lat": round(lat, 3), "lon": round(lon, 3),
            "radius_km": 100 if daylight else 30,
            "t_start": iso(valid), "t_end": iso(valid + dt.timedelta(minutes=30)),
            "severity": round(min(1.0, max(flash / 30.0, prob_severe / 100.0)), 3),
            "needs_daylight": daylight,
            "needs_night_capable_camera": not daylight,
            "look_bearing_hint": None,
            "evidence": {
                "COMPREF": compref, "FLASH_RATE": flash,
                "MESH": mesh, "ProbSevere": prob_severe,
                "motion_east_ms": float(p.get("MOTION_EAST") or 0),
                "motion_south_ms": float(p.get("MOTION_SOUTH") or 0),
                "probsevere_id": p["ID"], "valid_time": iso(valid),
            },
        })
    source = {"probsevere_valid_time": None, "probsevere_age_s": None}
    if features:
        valid = dt.datetime.fromisoformat(features[0]["properties"]["valid_time"])
        source = {"probsevere_valid_time": iso(valid), "probsevere_age_s": round((when - valid).total_seconds())}
    return events, source


def hrrr_grids(when: dt.datetime, domain: str = "conus") -> list[tuple[dt.datetime, int, CloudGrid]]:
    """One CloudGrid per forecast hour f01-f03 of the latest run that has each hour uploaded."""
    grids = []
    for fhour in (1, 2, 3):
        run = latest_run(when, fhour, domain)
        if run is not None:
            grids.append((run, fhour, CloudGrid(download_subset(key_for(run, fhour, domain), GRIB_DIR))))
    return grids


def nearest_grid(grids, target: dt.datetime):
    return min(grids, key=lambda g: abs((g[0] + dt.timedelta(hours=g[1])) - target))


def aurora_events(when: dt.datetime, grids) -> tuple[list[dict], dict]:
    ov = ovation()
    lcc_grid = nearest_grid(grids, when)[2] if grids else None
    events = []
    for lat in AURORA_LAT:
        for lon in AURORA_LON:
            rule = aurora_rule(ov, lat, lon, when)
            if not rule["fires"]:
                continue
            lcc = lcc_grid.disk_mean("lcc", lat, lon, 100) if lcc_grid else np.nan
            if not np.isnan(lcc) and lcc > AURORA_MAX_LCC:
                continue
            events.append({
                "id": f"aurora-{stamp(when)}-{lat:.1f}n{-lon:.1f}w",
                "type": "aurora",
                "lat": float(lat), "lon": float(lon),
                "radius_km": AURORA_RADIUS_KM,
                "t_start": iso(when), "t_end": iso(ov["forecast_at"] + dt.timedelta(minutes=30)),
                "severity": round(min(1.0, rule["probability"] / 50.0), 3),
                "needs_daylight": False,
                "needs_night_capable_camera": True,
                "look_bearing_hint": None,
                "evidence": {**rule, "site_lcc_disk100": None if np.isnan(lcc) else round(lcc, 1),
                             "ovation_forecast_at": iso(ov["forecast_at"])},
            })
    return events, {"ovation_observed_at": iso(ov["observed_at"]), "ovation_forecast_at": iso(ov["forecast_at"])}


def local_offset_hours(lon: float) -> int:
    return round(lon / 15)


def next_sunset(lat: float, lon: float, when: dt.datetime) -> dt.datetime | None:
    """First local sunset whose colour window (sunset+15) is still ahead of `when`."""
    offset = local_offset_hours(lon)
    today = (when + dt.timedelta(hours=offset)).date()
    for local_date in (today, today + dt.timedelta(days=1)):
        try:
            sunset = sunset_utc(lat, lon, local_date, offset)
        except ValueError:  # polar day/night
            continue
        if sunset + COLOUR_PEAK_AFTER_SUNSET >= when:
            return sunset
    return None


def goes_fields(when: dt.datetime, buckets: set[str], terrain: CloudGrid) -> dict[str, GoesCloudField | None]:
    """Newest GOES scan per satellite bucket; None where the bucket has no recent scan."""
    return {bucket: load_field(when, bucket, terrain) for bucket in buckets}


def goes_usable(goes: GoesCloudField | None, lat: float, lon: float, target: dt.datetime) -> bool:
    return goes is not None and target - goes.scanned_at <= GOES_MAX_LEAD and goes.covers(lat, lon)


def goes_sunset_score(goes: GoesCloudField, lat: float, lon: float, sunset: dt.datetime, grid: CloudGrid) -> dict:
    offset = local_offset_hours(lon)
    rh = float(grid.sample("rh", [lat], [lon])[0])
    golden = golden_hour_minutes(lat, lon, (sunset + dt.timedelta(hours=offset)).date(), offset)
    azimuth = sun_azimuth(lat, lon, sunset)
    return {"model": "sunset_quality/goes", "goes_scan": iso(goes.scanned_at), **quality_score(goes, lat, lon, azimuth, rh, golden)}


def sunset_score(site: dict, sunset: dt.datetime, grid: CloudGrid, goes: GoesCloudField | None) -> dict:
    """Classifier on the GOES cloud field when a recent scan covers the site, else the HRRR ray model."""
    lat, lon = site["lat"], site["lon"]
    if not goes_usable(goes, lat, lon, sunset + COLOUR_PEAK_AFTER_SUNSET):
        return {"model": "sunset_rays/hrrr", **rays_score(grid, lat, lon, sun_azimuth(lat, lon, sunset))}
    assert goes is not None
    return goes_sunset_score(goes, lat, lon, sunset, grid)


def sunset_events(when: dt.datetime, grids, goes: dict[str, GoesCloudField | None]) -> list[dict]:
    """Land lattice points whose sunset+15 a recent GOES scan can nowcast, scored by the classifier.

    Every point over the severity threshold becomes an event (no peak-picking): the camera side only
    looks SUNSET_RADIUS_KM around each point, so a good region must be tiled."""
    lat_grid, lon_grid = np.meshgrid(SUNSET_LAT, SUNSET_LON, indexing="ij")
    land = nearest_grid(grids, when)[2].sample("land", lat_grid.ravel(), lon_grid.ravel()) == 1
    events = []
    for lat, lon in zip(lat_grid.ravel()[land], lon_grid.ravel()[land]):
        lat, lon = float(lat), float(lon)
        sunset = next_sunset(lat, lon, when)
        field = goes[bucket_for(lon)]
        if sunset is None or not goes_usable(field, lat, lon, sunset + COLOUR_PEAK_AFTER_SUNSET):
            continue
        assert field is not None
        score = goes_sunset_score(field, lat, lon, sunset, nearest_grid(grids, sunset + COLOUR_PEAK_AFTER_SUNSET)[2])
        severity = min(1.0, score["quality"] / SUNSET_QUALITY_FULL)
        if severity < SUNSET_MIN_SEVERITY:
            continue
        azimuth = sun_azimuth(lat, lon, sunset)
        events.append({
            "id": f"sunset-{stamp(sunset)}-{lat:.0f}n{-lon:.0f}w",
            "type": "sunset",
            "lat": lat, "lon": lon,
            "radius_km": SUNSET_RADIUS_KM,
            "t_start": iso(sunset + COLOUR_WINDOW[0]), "t_end": iso(sunset + COLOUR_WINDOW[1]),
            "severity": round(severity, 3),
            "needs_daylight": True,
            "needs_night_capable_camera": False,
            "look_bearing_hint": round(azimuth, 1),
            "evidence": {**score, "sunset_utc": iso(sunset), "anti_solar_bearing": round((azimuth + 180) % 360, 1)},
        })
    return events


def site_scores(sites: list[dict], when: dt.datetime, grids, goes: dict[str, GoesCloudField | None],
                storms: list[dict], aurora_on: bool) -> list[dict]:
    """Per-site join for the camera side: next-sunset score, nearest storm, aurora rule."""
    valid_times = [run + dt.timedelta(hours=f) for run, f, _ in grids]
    ov = ovation() if aurora_on else None
    rows = []
    for site in sites:
        row = {"id": site["id"], "lat": site["lat"], "lon": site["lon"]}
        sunset = next_sunset(site["lat"], site["lon"], when)
        if sunset is not None and any(abs(v - sunset - COLOUR_PEAK_AFTER_SUNSET) <= dt.timedelta(hours=1) for v in valid_times):
            run, fhour, grid = nearest_grid(grids, sunset + COLOUR_PEAK_AFTER_SUNSET)
            row["sunset"] = {"sunset_utc": iso(sunset), "hrrr_valid": iso(run + dt.timedelta(hours=fhour)),
                             **sunset_score(site, sunset, grid, goes[bucket_for(site["lon"])])}
        nearest = None
        for event in storms:
            km = distance_km(site["lat"], site["lon"], event["lat"], event["lon"])
            if nearest is None or km < nearest["km"]:
                nearest = {"km": round(float(km), 1),
                           "bearing_deg": round(float(bearing_deg(site["lat"], site["lon"], event["lat"], event["lon"])), 1),
                           "event_id": event["id"]}
        row["nearest_storm"] = nearest
        if ov is not None:
            row["aurora"] = aurora_rule(ov, site["lat"], site["lon"], when)
        rows.append(row)
    return rows


def post_events(url: str, events: list[dict]) -> None:
    """POST each record to the camera service; its /events endpoint takes one WeatherEvent and resolves footage for it."""
    for event in events:
        requests.post(f"{url.rstrip('/')}/events", json=event, timeout=120).raise_for_status()


def run_once(when: dt.datetime, args) -> dict:
    storms, storm_source = storm_events(when)
    sites = json.load(open(args.sites)) if args.sites else []
    grids = hrrr_grids(when) if (sites or args.aurora or args.sunset) else []
    events = list(storms)
    sources = {"probsevere": storm_source,
               "hrrr": [{"run": iso(run), "fhour": f, "valid": iso(run + dt.timedelta(hours=f))} for run, f, _ in grids]}
    if args.aurora:
        aurora, aurora_source = aurora_events(when, grids)
        events += aurora
        sources["ovation"] = aurora_source
    buckets = (set(BUCKETS.values()) if args.sunset else set()) | {bucket_for(site["lon"]) for site in sites}
    goes = goes_fields(when, buckets, nearest_grid(grids, when)[2]) if buckets and grids else {}
    if buckets:
        sources["goes"] = {bucket: None if field is None else iso(field.scanned_at) for bucket, field in goes.items()}
    if args.sunset and grids:
        events += sunset_events(when, grids, goes)
    for event in events:
        event["replay"] = args.when is not None
    payload = {"generated_at": iso(dt.datetime.now(dt.UTC)), "when": iso(when), "sources": sources, "events": events}
    write_atomic(args.out, payload)
    if sites:
        site_path = os.path.join(os.path.dirname(args.out) or ".", "site_scores.json")
        write_atomic(site_path, {"when": iso(when), "sites": site_scores(sites, when, grids, goes, storms, args.aurora)})
    if args.post:
        post_events(args.post, events)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", default="out/events.json")
    parser.add_argument("--when", default=None, help="ISO instant to evaluate instead of now (replay)")
    parser.add_argument("--sites", default=None, help="camera site catalog JSON [{id, lat, lon}]")
    parser.add_argument("--loop", type=float, default=0, help="re-run every N seconds")
    parser.add_argument("--aurora", action="store_true")
    parser.add_argument("--sunset", action="store_true", help="sweep the CONUS lattice for sunset events (GOES classifier)")
    parser.add_argument("--post", default=None, help="camera service base URL; each event is POSTed to {url}/events")
    args = parser.parse_args()
    while True:
        run_once(parse_when(args.when), args)
        if not args.loop or args.when:  # replay is a fixed instant; looping makes no sense
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
