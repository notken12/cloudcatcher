"""Event detector: the weather half of cloudcatcher, worldwide.

Every run (or every --loop seconds) writes an events.json of camera-side WeatherEvent records
(type, lat, lon, radius_km, t_start, t_end, severity, replay) and, with --post, POSTs each one to the
camera service:

    uv run python -m weather.events --out out/events.json [--when 2026-09-20T04:30Z] [--sunset] [--aurora]
                                    [--sites sites.json] [--post http://localhost:8000] [--loop 600]

Rules, no ML:
- thunderstorm: over CONUS, ProbSevere objects with COMPREF >= 50 and (FLASH_RATE >= 5 or ProbSevere >= 30);
  elsewhere a model prior, GFS simulated composite reflectivity >= 45 dBZ with CAPE >= 300 J/kg, one event
  per connected blob.
- aurora (--aurora): OVATION probability >= 20, dark sky, and little low cloud (GFS lcc), both hemispheres.
- sunset (--sunset): a 1-degree land lattice worldwide; points whose sunset+15 min is within 90 min are
  scored by the Sunsethue-whitepaper classifier (weather/sunset_quality.py) on the best cloud field there:
  GOES over CONUS, Himawari-9 over Asia-Pacific (both see the thin cirrus models miss), else HRRR or GFS
  layer cover. Every point with severity >= 0.5 becomes an event.

--sites scores individual camera sites for their next sunset+15 min the same way.
"""
import argparse
import datetime as dt
import json
import os
import tempfile
import time

import numpy as np
import requests
from scipy.ndimage import label

from common.geo import bearing_deg, distance_km
from weather import gfs, goes_cloud, himawari_cloud, hrrr, mrms
from weather.cloud_columns import CloudField
from weather.cloud_grid import CloudGrid, LayerCloudField
from weather.grib_subset import download_subset
from weather.satellite_cloud import SatelliteCloudField
from weather.sun import golden_hour_minutes, sun_azimuth, sun_elevation, sunset_utc
from weather.sunset_quality import score_site
from weather.swpc_aurora import aurora_rule, ovation

AURORA_LAT = np.r_[np.arange(-71.0, -49.99, 0.5), np.arange(50.0, 71.01, 0.5)]
AURORA_LON = np.arange(-180.0, 179.51, 0.5)

STORM_MIN_COMPREF = 50.0
STORM_MIN_FLASH = 5.0
STORM_MIN_PROBSEVERE = 30.0
PROBSEVERE_DOMAIN = (20.0, 55.0, -130.0, -60.0)   # lat_min, lat_max, lon_min, lon_max: where the observed detector runs
MODEL_STORM_MIN_REFC = 45.0                       # GFS simulated composite reflectivity, dBZ; 40 alone also flags stratiform rain
MODEL_STORM_FULL_REFC = 60.0
MODEL_STORM_MIN_CAPE = 300.0                      # J/kg: convective, not a bright band
DAYLIGHT_ELEVATION_DEG = -6.0
AURORA_RADIUS_KM = 150
AURORA_MAX_LCC = 30.0
COLOUR_PEAK_AFTER_SUNSET = dt.timedelta(minutes=15)
COLOUR_WINDOW = (dt.timedelta(minutes=5), dt.timedelta(minutes=25))
SUNSET_LEAD = dt.timedelta(minutes=90)  # a satellite scan is a nowcast; sweep only the band whose sunset is this close
SUNSET_LAT = np.arange(-60.0, 70.01, 1.0)
SUNSET_LON = np.arange(-180.0, 179.01, 1.0)
SUNSET_RADIUS_KM = 60          # half a lattice cell's diagonal; the camera side looks 50 km around the point
SUNSET_QUALITY_FULL = 0.06     # classifier quality worth severity 1: the West-Coast test's best sites, Lamar 5/5 via GOES 0.05
SUNSET_MIN_SEVERITY = 0.5
FAN_MARGIN_DEG = 6.0           # the classifier looks 600 km sunward; satellite windows extend this far beyond the points

GRIB_DIR = os.environ.get("CLOUDCATCHER_GRIB_DIR", "/tmp/cloudcatcher-grib")

Grids = list[tuple[dt.datetime, int, CloudGrid]]


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


def in_box(lat: float, lon: float, box: tuple[float, float, float, float]) -> bool:
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


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


def model_storm_events(run: dt.datetime, fhour: int, grid: CloudGrid) -> list[dict]:
    """Storm prior outside the ProbSevere domain: each connected blob of GFS composite reflectivity >= 45 dBZ with CAPE >= 300."""
    valid = run + dt.timedelta(hours=fhour)
    n_lat = np.unique(grid.points[:, 0]).size
    refc = grid.fields["refc"].reshape(n_lat, -1)
    cape = grid.fields["cape"].reshape(n_lat, -1)
    lats, lons = grid.points[:, 0].reshape(n_lat, -1), grid.points[:, 1].reshape(n_lat, -1)
    blobs = np.zeros(refc.shape, dtype=np.int32)
    label((refc >= MODEL_STORM_MIN_REFC) & (cape >= MODEL_STORM_MIN_CAPE), output=blobs)
    events = []
    for blob in range(1, int(blobs.max()) + 1):
        cells = blobs == blob
        lat, lon = float(lats[cells].mean()), float(lons[cells].mean())
        if in_box(lat, lon, PROBSEVERE_DOMAIN):
            continue
        peak = float(refc[cells].max())
        daylight = sun_elevation(lat, lon, valid) > DAYLIGHT_ELEVATION_DEG
        events.append({
            "id": f"gfsstorm-{stamp(valid)}-{lat:.1f}n{lon:.1f}e",
            "type": "thunderstorm",
            "lat": round(lat, 3), "lon": round(lon, 3),
            "radius_km": round(max(30.0, 14.0 * np.sqrt(cells.sum()))),  # 0.25 deg cells are ~28 km across
            "t_start": iso(valid - dt.timedelta(minutes=30)), "t_end": iso(valid + dt.timedelta(minutes=30)),
            "severity": round(min(1.0, (peak - MODEL_STORM_MIN_REFC) / (MODEL_STORM_FULL_REFC - MODEL_STORM_MIN_REFC)), 3),
            "needs_daylight": daylight,
            "needs_night_capable_camera": not daylight,
            "look_bearing_hint": None,
            "evidence": {"model": "gfs_refc", "gfs_run": iso(run), "valid_time": iso(valid), "max_refc_dbz": round(peak, 1),
                         "max_cape_jkg": round(float(cape[cells].max())), "cells": int(cells.sum())},
        })
    return events


def hrrr_grids(when: dt.datetime, domain: str = "conus") -> Grids:
    """One CloudGrid per forecast hour f01-f03 of the latest run that has each hour uploaded."""
    grids = []
    for fhour in (1, 2, 3):
        run = hrrr.latest_run(when, fhour, domain)
        if run is not None:
            grids.append((run, fhour, CloudGrid(download_subset(hrrr.BUCKET, hrrr.key_for(run, fhour, domain), GRIB_DIR, hrrr.CLOUD_FIELDS))))
    return grids


def gfs_grids(when: dt.datetime) -> Grids:
    """The GFS steps valid at `when` and the next two hours, from the newest run that has them."""
    run_hours = gfs.run_and_hours(when, 3)
    if run_hours is None:
        return []
    run, hours = run_hours
    return [(run, fhour, CloudGrid(download_subset(gfs.BUCKET, gfs.key_for(run, fhour), GRIB_DIR, gfs.CLOUD_FIELDS))) for fhour in hours]


def nearest_grid(grids: Grids, target: dt.datetime) -> tuple[dt.datetime, int, CloudGrid]:
    return min(grids, key=lambda g: abs((g[0] + dt.timedelta(hours=g[1])) - target))


def model_grid(hrrr_conus: Grids, gfs_global: Grids, lat: float, lon: float, target: dt.datetime) -> tuple[str, CloudGrid]:
    """HRRR where its grid covers the point (3 km, hourly), else GFS."""
    if hrrr_conus:
        grid = nearest_grid(hrrr_conus, target)[2]
        if np.isfinite(grid.sample("land", [lat], [lon])[0]):
            return "hrrr", grid
    return "gfs", nearest_grid(gfs_global, target)[2]


def aurora_events(when: dt.datetime, gfs_global: Grids) -> tuple[list[dict], dict]:
    ov = ovation()
    lcc_grid = nearest_grid(gfs_global, when)[2] if gfs_global else None
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
                "id": f"aurora-{stamp(when)}-{lat:.1f}n{lon:.1f}e",
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


def imminent_sunset(lat: float, lon: float, when: dt.datetime) -> dt.datetime | None:
    """The next sunset when its colour peak is within SUNSET_LEAD of `when`, else None."""
    sunset = next_sunset(lat, lon, when)
    if sunset is None or sunset + COLOUR_PEAK_AFTER_SUNSET - when > SUNSET_LEAD:
        return None
    return sunset


class CloudFields:
    """The satellite fields loaded for one run, and the choice of field for a point: GOES over CONUS, Himawari-9
    over Asia-Pacific, otherwise the HRRR or GFS layer cover."""

    def __init__(self, when: dt.datetime, hrrr_conus: Grids, gfs_global: Grids, points: list[tuple[float, float]]):
        self.hrrr, self.gfs = hrrr_conus, gfs_global
        terrain = nearest_grid(gfs_global, when)[2]
        lats = np.array([p[0] for p in points])
        lons = np.array([p[1] for p in points])
        self.goes: dict[str, SatelliteCloudField | None] = {}
        for bucket in {goes_cloud.bucket_for(float(lon)) for lat, lon in points if in_box(float(lat), float(lon), PROBSEVERE_DOMAIN)}:
            self.goes[bucket] = goes_cloud.load_field(when, bucket, terrain)
        self.himawari = himawari_cloud.load_field(when, lats, lons, FAN_MARGIN_DEG, terrain) if points else None

    def satellite(self, lat: float, lon: float) -> tuple[str, SatelliteCloudField] | None:
        candidates = [("goes", self.goes.get(goes_cloud.bucket_for(lon))), ("himawari", self.himawari)]
        for name, field in candidates:
            if field is not None and field.covers(lat, lon):
                return name, field
        return None

    def choose(self, lat: float, lon: float, target: dt.datetime) -> tuple[str, CloudField, CloudGrid]:
        """(field name, cloud field, model grid for humidity) for scoring `target` at the point."""
        model_name, grid = model_grid(self.hrrr, self.gfs, lat, lon, target)
        satellite = self.satellite(lat, lon)
        if satellite is None:
            return model_name, LayerCloudField(grid), grid
        return satellite[0], satellite[1], grid

    def scans(self) -> dict[str, str | None]:
        scans = {bucket: None if field is None else iso(field.scanned_at) for bucket, field in self.goes.items()}
        scans["himawari9"] = None if self.himawari is None else iso(self.himawari.scanned_at)
        return scans


def sunset_score(fields: CloudFields, lat: float, lon: float, sunset: dt.datetime) -> dict:
    name, field, grid = fields.choose(lat, lon, sunset + COLOUR_PEAK_AFTER_SUNSET)
    offset = local_offset_hours(lon)
    rh = float(grid.sample("rh", [lat], [lon])[0])
    golden = golden_hour_minutes(lat, lon, (sunset + dt.timedelta(hours=offset)).date(), offset)
    return {"model": f"sunset_quality/{name}", **score_site(field, lat, lon, sun_azimuth(lat, lon, sunset), rh, golden)}


def sunset_lattice(when: dt.datetime, land_grid: CloudGrid) -> list[tuple[float, float, dt.datetime]]:
    """Land lattice points whose sunset colour peak is imminent, with their sunset time."""
    lat_grid, lon_grid = np.meshgrid(SUNSET_LAT, SUNSET_LON, indexing="ij")
    land = land_grid.sample("land", lat_grid.ravel(), lon_grid.ravel()) == 1
    points = []
    for lat, lon in zip(lat_grid.ravel()[land], lon_grid.ravel()[land]):
        sunset = imminent_sunset(float(lat), float(lon), when)
        if sunset is not None:
            points.append((float(lat), float(lon), sunset))
    return points


def sunset_events(fields: CloudFields, points: list[tuple[float, float, dt.datetime]]) -> list[dict]:
    """Every lattice point over the severity threshold becomes an event (no peak-picking: the camera side only
    looks SUNSET_RADIUS_KM around each point, so a good region must be tiled)."""
    events = []
    for lat, lon, sunset in points:
        score = sunset_score(fields, lat, lon, sunset)
        severity = min(1.0, score["quality"] / SUNSET_QUALITY_FULL)
        if severity < SUNSET_MIN_SEVERITY:
            continue
        azimuth = sun_azimuth(lat, lon, sunset)
        events.append({
            "id": f"sunset-{stamp(sunset)}-{lat:.0f}n{lon:.0f}e",
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


def site_scores(sites: list[dict], when: dt.datetime, fields: CloudFields, storms: list[dict], aurora_on: bool) -> list[dict]:
    """Per-site join for the camera side: next-sunset score, nearest storm, aurora rule."""
    ov = ovation() if aurora_on else None
    rows = []
    for site in sites:
        row = {"id": site["id"], "lat": site["lat"], "lon": site["lon"]}
        sunset = next_sunset(site["lat"], site["lon"], when)
        if sunset is not None:
            row["sunset"] = {"sunset_utc": iso(sunset), **sunset_score(fields, site["lat"], site["lon"], sunset)}
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
    gfs_global = gfs_grids(when)
    hrrr_conus = hrrr_grids(when) if (sites or args.sunset) else []
    events = list(storms)
    sources = {"probsevere": storm_source,
               "hrrr": [{"run": iso(run), "fhour": f, "valid": iso(run + dt.timedelta(hours=f))} for run, f, _ in hrrr_conus],
               "gfs": [{"run": iso(run), "fhour": f, "valid": iso(run + dt.timedelta(hours=f))} for run, f, _ in gfs_global]}
    if gfs_global:
        events += model_storm_events(*nearest_grid(gfs_global, when))
    if args.aurora:
        aurora, aurora_source = aurora_events(when, gfs_global)
        events += aurora
        sources["ovation"] = aurora_source
    lattice = sunset_lattice(when, nearest_grid(gfs_global, when)[2]) if (args.sunset and gfs_global) else []
    points = [(lat, lon) for lat, lon, _ in lattice] + [(site["lat"], site["lon"]) for site in sites]
    fields = CloudFields(when, hrrr_conus, gfs_global, points) if (points and gfs_global) else None
    if fields is not None:
        sources["satellite"] = fields.scans()
        events += sunset_events(fields, lattice)
    for event in events:
        event["replay"] = args.when is not None
    payload = {"generated_at": iso(dt.datetime.now(dt.UTC)), "when": iso(when), "sources": sources, "events": events}
    write_atomic(args.out, payload)
    if sites and fields is not None:
        site_path = os.path.join(os.path.dirname(args.out) or ".", "site_scores.json")
        write_atomic(site_path, {"when": iso(when), "sites": site_scores(sites, when, fields, storms, args.aurora)})
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
    parser.add_argument("--sunset", action="store_true", help="sweep the world's land lattice for imminent sunsets")
    parser.add_argument("--post", default=None, help="camera service base URL; each event is POSTed to {url}/events")
    args = parser.parse_args()
    while True:
        run_once(parse_when(args.when), args)
        if not args.loop or args.when:  # replay is a fixed instant; looping makes no sense
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
