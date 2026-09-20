"""Acceptance checks for weather/events.py. Run from the repo root: uv run python -m weather.test_events

1. Regression: Lamar CO on the 2026-09-20 00Z f01 subset (a verified 5/5 sunset frame) ->
   sunset_rays.score_site quality 0.418; the sunset_quality classifier scores it 0.12 on HRRR and
   0.05 on the GOES-19 00:46Z scan (top of the GOES scale: the West-Coast winners reach 0.04-0.06).
2. Replay: --when 2024-04-26T20:30Z emits a storm <= 15 km from Mead NE (41.1447, -96.4616)
   with MESH 1.30 and FLASH_RATE 38 (EF4 day; the 2024 file has no ProbSevere/COMPREF keys).
3. Live: a run with no --when completes and events.json validates; ProbSevere age <= 5 min.
4. Aurora (approximate): --aurora fires at Yellowknife. OVATION is live-only, so the ~26%
   probability expected on 2026-09-20T04:40Z drifts; the check only requires fires=True.
5. Sunset sweep: --sunset at 2026-09-20T02:00Z emits a sunset event within 90 km of Wagontire OR
   Plains MT (the West-Coast test evening's 4/5 frames), as valid camera-side records.
6. Global fields: the classifier on GFS scores Lamar; the Himawari-9 fixed grid navigates to the pixel and
   its field covers Tokyo but not Colorado.
7. SYNOP: the FM-12 decoder reads cloud genus, and OGIMET's live feed yields genus at hundreds of stations.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

from common.geo import distance_km

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    return ok


def run_events(*args: str, timeout: int = 180) -> tuple[dict, float]:
    out = tempfile.mktemp(suffix=".json")
    started = dt.datetime.now(dt.UTC)
    subprocess.run([sys.executable, "-m", "weather.events", "--out", out, *args],
                   cwd=REPO_ROOT, check=True, timeout=timeout)
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
    return json.load(open(out)), elapsed


def regression_lamar() -> bool:
    import pygrib  # noqa: F401  (pygrib prints harmless ECCODES warnings)
    from weather.cloud_grid import CloudGrid, LayerCloudField
    from weather.goes_cloud import load_field
    from weather.grib_subset import download_subset
    from weather.hrrr import BUCKET, CLOUD_FIELDS
    from weather.sunset_rays import score_site as rays_score
    from weather.sunset_quality import score_site as quality_score

    grid = CloudGrid(download_subset(BUCKET, "hrrr.20260920/conus/hrrr.t00z.wrfsfcf01.grib2", "/tmp/hrrr", CLOUD_FIELDS))
    old = rays_score(grid, 38.077, -102.696, 271.8)
    new = quality_score(LayerCloudField(grid), 38.077, -102.696, 271.8)
    goes = load_field(dt.datetime(2026, 9, 20, 0, 48, tzinfo=dt.UTC), "noaa-goes19", grid)
    if goes is None:
        return check("regression Lamar score_site", False, "no GOES-19 scan before 2026-09-20T00:48Z")
    satellite = quality_score(goes, 38.077, -102.696, 271.8)
    ok = abs(old["quality"] - 0.418) < 0.01 and old["depression_deg"] == 1 and old["horizon_block"] == 0.0
    ok &= check("classifier on HRRR scores Lamar high", new["quality"] >= 0.1, f"quality={new['quality']}")
    ok &= check("classifier on GOES-19 scores Lamar high", satellite["quality"] >= 0.04 and satellite["site_hcc"] >= 90,
                f"quality={satellite['quality']} hcc={satellite['site_hcc']} scan={goes.scanned_at:%H:%M}Z")
    return check("regression Lamar score_site", ok, f"quality={old['quality']} depression={old['depression_deg']}")


def global_fields() -> bool:
    """GFS (the worldwide model fallback) and Himawari-9 (Asia-Pacific satellite) load and navigate: Lamar on the
    GFS 00z f001 step (valid 01Z, the 5/5 evening) scores above the sweep threshold's half, and the Himawari grid
    built from AHI constants lands on the file's own coordinates to the pixel."""
    import h5py
    from weather import gfs, himawari_cloud
    from weather.cloud_grid import CloudGrid, LayerCloudField
    from weather.grib_subset import download_subset
    from weather.sunset_quality import score_site

    grid = CloudGrid(download_subset(gfs.BUCKET, "gfs.20260920/00/atmos/gfs.t00z.pgrb2.0p25.f001", "/tmp/gfs", gfs.CLOUD_FIELDS))
    lamar = score_site(LayerCloudField(grid), 38.077, -102.696, 271.8)
    ok = check("classifier on GFS scores Lamar", lamar["quality"] >= 0.03 and lamar["site_hcc"] >= 50,
               f"quality={lamar['quality']} hcc={lamar['site_hcc']}")
    key = himawari_cloud.scan_key(dt.datetime(2026, 9, 20, 7, 0, tzinfo=dt.UTC))
    if key is None:
        return check("himawari scan available", False)
    pixel = (1500, 1501, 800, 801)
    with h5py.File(himawari_cloud.S3File(himawari_cloud.BUCKET, key), "r") as h:
        lat, lon = (float(himawari_cloud.read_window(h, name, pixel)[0, 0]) for name in ("Latitude", "Longitude"))
    row, col, inside = himawari_cloud.fixed_grid().indices([lat], [lon])
    ok &= check("himawari navigation", bool(inside[0]) and int(row[0]) == 1500 and int(col[0]) == 800, f"pixel (1500, 800) -> ({row[0]}, {col[0]})")
    field = himawari_cloud.load_field(dt.datetime(2026, 9, 20, 7, 0, tzinfo=dt.UTC), [35.68], [139.69], 6.0, grid)
    ok &= check("himawari field covers Tokyo, not Lamar", field is not None and field.covers(35.68, 139.69) and not field.covers(38.077, -102.696))
    return ok


def replay_mead() -> bool:
    payload, _ = run_events("--when", "2024-04-26T20:30Z")
    storms = [e for e in payload["events"] if e["type"] == "thunderstorm"]
    if not storms:
        return check("replay Mead storm", False, "no storm events")
    best = min(storms, key=lambda e: distance_km(41.1447, -96.4616, e["lat"], e["lon"]))
    km = distance_km(41.1447, -96.4616, best["lat"], best["lon"])
    ev = best["evidence"]
    ok = bool(km <= 15) and ev["MESH"] == 1.30 and ev["FLASH_RATE"] == 38.0
    return check("replay Mead storm", ok, f"km={km:.1f} MESH={ev['MESH']} FLASH={ev['FLASH_RATE']}")


REQUIRED_KEYS = {"id", "type", "lat", "lon", "radius_km", "t_start", "t_end", "severity", "replay",
                 "needs_daylight", "needs_night_capable_camera", "look_bearing_hint", "evidence"}
EVENT_TYPES = ("thunderstorm", "sunset", "aurora", "lenticular", "rare_cloud")  # camera-side EventType names (+ rare_cloud, to be added there)


def valid_events(events: list[dict]) -> bool:
    return all(REQUIRED_KEYS <= set(e) and e["type"] in EVENT_TYPES and 0 <= e["severity"] <= 1 for e in events)


def live() -> bool:
    payload, elapsed = run_events()
    valid = valid_events(payload["events"])
    age = payload["sources"]["probsevere"]["probsevere_age_s"]
    ok = check("live schema", valid, f"{len(payload['events'])} events")
    ok &= check("live runtime < 90 s", elapsed < 90, f"{elapsed:.0f}s")
    ok &= check("probsevere age <= 5 min", age is not None and age <= 300, f"age={age}s")
    return ok


def sunset_sweep() -> bool:
    """--sunset at 02:00Z on the West-Coast test evening: the lattice sweep must flag the Wagontire / Plains
    area (the colour index's 4/5 frames, REPORT §3.16) as sunset events within 90 km, with valid records."""
    payload, elapsed = run_events("--when", "2026-09-20T02:00Z", "--sunset", timeout=900)
    sunsets = [e for e in payload["events"] if e["type"] == "sunset"]
    near = [e for e in sunsets if min(distance_km(43.25, -119.88, e["lat"], e["lon"]),
                                      distance_km(47.47, -114.90, e["lat"], e["lon"])) <= 90]
    ok = check("sunset sweep flags Wagontire or Plains", bool(near), f"{len(sunsets)} sunset events, {len(near)} near; {elapsed:.0f}s")
    ok &= check("sunset records valid", valid_events(sunsets) and all(e["replay"] for e in sunsets))
    return ok


def synop_genus() -> bool:
    """The FM-12 decoder reads the 8NhCLCMCH group (Samjiyon 2026-09-20 00Z: 7/8 total cover, altocumulus translucidus
    under cirrus fibratus, no low cloud; Key West's automated report has no genus group), and the live OGIMET feed
    yields genus at hundreds of stations."""
    from weather import synop

    samjiyon = synop.decode("AAXX 20001 47005 32470 71801 10112 20073 38665 4//// 57002 82031 333 20053=")
    key_west = synop.decode("AAXX 20034 72201 32966 00000 10278 20233 30132 40145 50010 90253 555 92003=")
    ok = check("synop decode", samjiyon == (7, 0, 3, 1) and key_west is None, f"samjiyon={samjiyon} key_west={key_west}")
    rare = synop.Report("x", "x", 0.0, 0.0, dt.datetime.now(dt.UTC), 5, 0, 4, 9).rare_genera()
    ok &= check("synop rare genera", rare == [("rare_cloud", "cirrocumulus", 0.8), ("lenticular", "altocumulus lenticularis", 0.9)], str(rare))
    observations = synop.reports(dt.datetime.now(dt.UTC), "/tmp/cloudcatcher-grib")
    return ok & check("synop live feed", len(observations) >= 300, f"{len(observations)} genus reports with coordinates in the last 3 h")


def aurora() -> bool:
    payload, _ = run_events("--when", "2026-09-20T04:40Z", "--aurora")
    ykn = [e for e in payload["events"] if e["type"] == "aurora"
           and distance_km(62.45, -114.37, e["lat"], e["lon"]) <= 60]
    return check("aurora fires near Yellowknife (approximate; OVATION is live-only)",
                 bool(ykn), f"{len(ykn)} events within 60 km")


if __name__ == "__main__":
    results = [regression_lamar(), global_fields(), synop_genus(), replay_mead(), live(), aurora(), sunset_sweep()]
    sys.exit(0 if all(results) else 1)
