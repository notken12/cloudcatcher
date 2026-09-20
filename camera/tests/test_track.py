import json

from sunroof_camera.footage import Verdict
from sunroof_camera.track import MAX_MULT, MIN_MULT, CameraTrack


def _v(vis, conf=0.9, quality=4):
    return Verdict(
        usable=True, event_visible=vis, event_type_seen="sunset", confidence=conf, quality=quality
    )


def test_unknown_camera_is_neutral():
    t = CameraTrack()
    assert t.multiplier("cam", "sunset") == 1.0
    t.record("other", "sunset", "yes", 0.5, 5)
    assert t.multiplier("cam", "sunset") == 1.0


def test_confirmed_camera_boosted_and_dud_demoted_within_bounds():
    t = CameraTrack()
    for _ in range(20):
        t.record("hero", "sunset", "yes", 0.5, 5)
        t.record("dud", "sunset", "no")
        t.record("bg", "sunset", "no")
    assert MAX_MULT >= t.multiplier("hero", "sunset") > 1.05
    assert MIN_MULT <= t.multiplier("dud", "sunset") < 0.97
    assert t.multiplier("hero", "thunderstorm") == 1.0  # per type


def test_from_log_and_summary(tmp_path):
    p = tmp_path / "verdicts.jsonl"
    rows = [
        {"camera_id": "a", "event_type": "sunset", "q": 0.6, **_v("yes").model_dump()},
        {"camera_id": "a", "event_type": "sunset", "q": 0.4, **_v("partial").model_dump()},
        {"camera_id": "b", "event_type": "sunset", "q": 0.1, **_v("no").model_dump()},
        {"camera_id": "c", "event_type": "sunset", "q": 0.1, "usable": False},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    t = CameraTrack.from_log(p)
    assert t.per_type["sunset"].judged == 3
    s = t.summary("sunset")
    assert [r["camera_id"] for r in s] == ["a"]
    assert s[0]["hits"] == 1.5 and s[0]["judged"] == 2
    assert CameraTrack.from_log(tmp_path / "missing.jsonl").per_type == {}
