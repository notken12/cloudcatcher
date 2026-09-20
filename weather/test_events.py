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
    from weather.goes_cloud import load_field
    from weather.hrrr import CloudGrid, HrrrCloudField, download_subset
    from weather.sunset_rays import score_site as rays_score
    from weather.sunset_quality import score_site as quality_score

    path = download_subset("hrrr.20260920/conus/hrrr.t00z.wrfsfcf01.grib2", "/tmp/hrrr")
    grid = CloudGrid(path)
    old = rays_score(grid, 38.077, -102.696, 271.8)
    new = quality_score(HrrrCloudField(grid), 38.077, -102.696, 271.8)
    goes = load_field(dt.datetime(2026, 9, 20, 0, 48, tzinfo=dt.UTC), "noaa-goes19", grid)
    if goes is None:
        return check("regression Lamar score_site", False, "no GOES-19 scan before 2026-09-20T00:48Z")
    satellite = quality_score(goes, 38.077, -102.696, 271.8)
    ok = abs(old["quality"] - 0.418) < 0.01 and old["depression_deg"] == 1 and old["horizon_block"] == 0.0
    ok &= check("classifier on HRRR scores Lamar high", new["quality"] >= 0.1, f"quality={new['quality']}")
    ok &= check("classifier on GOES-19 scores Lamar high", satellite["quality"] >= 0.04 and satellite["site_hcc"] >= 90,
                f"quality={satellite['quality']} hcc={satellite['site_hcc']} scan={goes.scanned_at:%H:%M}Z")
    return check("regression Lamar score_site", ok, f"quality={old['quality']} depression={old['depression_deg']}")


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
EVENT_TYPES = ("thunderstorm", "sunset", "aurora")  # camera-side EventType names


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


def aurora() -> bool:
    payload, _ = run_events("--when", "2026-09-20T04:40Z", "--aurora")
    ykn = [e for e in payload["events"] if e["type"] == "aurora"
           and distance_km(62.45, -114.37, e["lat"], e["lon"]) <= 60]
    return check("aurora fires near Yellowknife (approximate; OVATION is live-only)",
                 bool(ykn), f"{len(ykn)} events within 60 km")


if __name__ == "__main__":
    results = [regression_lamar(), replay_mead(), live(), aurora(), sunset_sweep()]
    sys.exit(0 if all(results) else 1)
