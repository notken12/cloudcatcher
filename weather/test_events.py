"""Acceptance checks for weather/events.py. Run from the repo root: uv run python -m weather.test_events

1. Regression: Lamar CO on the 2026-09-20 00Z f01 subset (a verified 5/5 sunset frame) ->
   sunset_rays.score_site quality 0.418; the new sunset_quality classifier should also score it high.
2. Replay: --when 2024-04-26T20:30Z emits a storm <= 15 km from Mead NE (41.1447, -96.4616)
   with MESH 1.30 and FLASH_RATE 38 (EF4 day; the 2024 file has no ProbSevere/COMPREF keys).
3. Live: a run with no --when completes and events.json validates; ProbSevere age <= 5 min.
4. Aurora (approximate): --aurora fires at Yellowknife. OVATION is live-only, so the ~26%
   probability expected on 2026-09-20T04:40Z drifts; the check only requires fires=True.
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


def run_events(*args: str, timeout: int = 180) -> dict:
    out = tempfile.mktemp(suffix=".json")
    started = dt.datetime.now(dt.UTC)
    subprocess.run([sys.executable, "-m", "weather.events", "--out", out, *args],
                   cwd=REPO_ROOT, check=True, timeout=timeout)
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
    return json.load(open(out)), elapsed


def regression_lamar() -> bool:
    import pygrib  # noqa: F401  (pygrib prints harmless ECCODES warnings)
    from weather.hrrr import CloudGrid, download_subset
    from weather.sunset_rays import score_site as rays_score
    from weather.sunset_quality import score_site as quality_score

    path = download_subset("hrrr.20260920/conus/hrrr.t00z.wrfsfcf01.grib2", "/tmp/hrrr")
    grid = CloudGrid(path)
    old = rays_score(grid, 38.077, -102.696, 271.8)
    new = quality_score(grid, 38.077, -102.696, 271.8)
    ok = abs(old["quality"] - 0.418) < 0.01 and old["depression_deg"] == 1 and old["horizon_block"] == 0.0
    ok &= check("new classifier also scores Lamar high", new["quality"] >= 0.25, f"quality={new['quality']}")
    return check("regression Lamar score_site", ok, f"quality={old['quality']} depression={old['depression_deg']}")


def replay_mead() -> bool:
    payload, _ = run_events("--when", "2024-04-26T20:30Z")
    storms = [e for e in payload["events"] if e["type"] == "storm"]
    if not storms:
        return check("replay Mead storm", False, "no storm events")
    best = min(storms, key=lambda e: distance_km(41.1447, -96.4616, e["lat"], e["lon"]))
    km = distance_km(41.1447, -96.4616, best["lat"], best["lon"])
    ev = best["evidence"]
    ok = km <= 15 and ev["MESH"] == 1.30 and ev["FLASH_RATE"] == 38.0
    return check("replay Mead storm", ok, f"km={km:.1f} MESH={ev['MESH']} FLASH={ev['FLASH_RATE']}")


REQUIRED_KEYS = {"id", "type", "lat", "lon", "radius_km", "t_start", "t_end", "score",
                 "needs_daylight", "needs_night_capable_camera", "look_bearing_hint", "evidence"}


def live() -> bool:
    payload, elapsed = run_events()
    valid = all(REQUIRED_KEYS <= set(e) and e["type"] in ("storm", "sunset", "aurora")
                and 0 <= e["score"] <= 1 for e in payload["events"])
    age = payload["sources"]["probsevere"]["probsevere_age_s"]
    ok = check("live schema", valid, f"{len(payload['events'])} events")
    ok &= check("live runtime < 90 s", elapsed < 90, f"{elapsed:.0f}s")
    ok &= check("probsevere age <= 5 min", age is not None and age <= 300, f"age={age}s")
    return ok


def aurora() -> bool:
    payload, _ = run_events("--when", "2026-09-20T04:40Z", "--aurora")
    ykn = [e for e in payload["events"] if e["type"] == "aurora"
           and distance_km(62.45, -114.37, e["lat"], e["lon"]) <= 60]
    return check("aurora fires near Yellowknife (approximate; OVATION is live-only)",
                 bool(ykn), f"{len(ykn)} events within 60 km")


if __name__ == "__main__":
    results = [regression_lamar(), replay_mead(), live(), aurora()]
    sys.exit(0 if all(results) else 1)
