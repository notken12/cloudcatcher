from datetime import datetime, timedelta, timezone

import pandas as pd

from sunroof_camera import health
from sunroof_camera.schema import Camera, to_frame

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _catalog(*ids: str) -> pd.DataFrame:
    return to_frame(
        [
            Camera(id=i, source="t", source_kind="jpeg", name=i, lat=0, lon=0, refresh_s=600)
            for i in ids
        ]
    )


def _log(rows):
    df = pd.DataFrame(
        [
            dict(
                id=i,
                source="t",
                ts=NOW - timedelta(minutes=m),
                ok=ok,
                reason="ok" if ok else "no frame",
                sha1=None,
                bytes=0,
                age_s=age,
                mean_lum=lum,
                sharpness=100.0,
                solar_elev=el,
                night=lum is not None and lum < 12,
            )
            for i, m, ok, age, lum, el in rows
        ],
        columns=list(health.LOG_COLUMNS),
    )
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def test_apply_log_health_tiers():
    cat = _catalog("live", "stale_old", "stale_fail", "dead_streak", "dead_silent", "never", "unv")
    hlog = _log(
        [
            ("live", 1, True, 60.0, 100.0, 30.0),
            ("stale_old", 1, True, 3 * 3600.0, 100.0, 30.0),
            ("stale_fail", 30, True, 60.0, 100.0, 30.0),
            ("stale_fail", 1, False, None, None, 30.0),
            ("dead_streak", 3, False, None, None, 30.0),
            ("dead_streak", 2, False, None, None, 30.0),
            ("dead_streak", 1, False, None, None, 30.0),
            ("dead_silent", 30 * 60, True, 60.0, 100.0, 30.0),
            ("dead_silent", 1, False, None, None, 30.0),
            ("unv", 1, False, None, None, 30.0),
        ]
    )
    out = health.apply_log(cat, hlog, NOW).set_index("id")
    assert out.loc["live", "health"] == "live"
    assert out.loc["stale_old", "health"] == "stale"
    assert out.loc["stale_fail", "health"] == "stale"
    assert out.loc["stale_fail", "fail_streak"] == 1
    assert out.loc["dead_streak", "health"] == "dead"
    assert out.loc["dead_silent", "health"] == "dead"
    assert out.loc["never", "health"] == "unverified"
    assert out.loc["unv", "health"] == "unverified"
    assert out.loc["live", "last_frame_ts"] == pd.Timestamp(NOW) - pd.Timedelta(minutes=2)


def test_night_usable_frac_from_dark_samples():
    cat = _catalog("n")
    hlog = _log(
        [
            ("n", 40, True, 60.0, 80.0, -20.0),
            ("n", 30, True, 60.0, 3.0, -20.0),
            ("n", 20, True, 60.0, 90.0, -20.0),
            ("n", 10, True, 60.0, 90.0, 30.0),  # daytime: ignored
            ("n", 1, False, None, None, -20.0),
        ]
    )
    out = health.apply_log(cat, hlog, NOW).set_index("id")
    assert abs(out.loc["n", "night_usable_frac"] - 0.5) < 1e-9


def test_placeholder_detection_marks_shared_bytes():
    def s(i, sha):
        return health.Sample(i, "t", NOW, True, "ok", sha, 5000, 0.0, 100.0, 50.0, 30.0, False)

    samples = [s("a", "x"), s("b", "x"), s("c", "x"), s("d", "y")]
    bad = health._mark_placeholders(samples)
    assert bad == {"x"}
    assert [x.ok for x in samples] == [False, False, False, True]
