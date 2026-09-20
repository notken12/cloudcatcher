"""Stage-A deterministic quality: features on synthetic skies, Q ranking, and the per-type
acceptance rules applied to VLM verdicts (offline; VLM is monkeypatched)."""

from __future__ import annotations

import io
from dataclasses import replace
from datetime import timedelta

import httpx
import numpy as np
import pytest
from PIL import Image
from test_gates_resolve_server import NOW, _catalog, frame, jpeg

from sunroof_camera import quality, resolve, vlm
from sunroof_camera.footage import Verdict, WeatherEvent
from sunroof_camera.profiles import profile
from sunroof_camera.query import Catalog


def _img(top: tuple[int, int, int], bottom: tuple[int, int, int], noise=6, seed=0) -> Image.Image:
    rng = np.random.default_rng(seed)
    h, w = 480, 640
    a = np.zeros((h, w, 3))
    a[: int(h * 0.6)] = top
    a[int(h * 0.6) :] = bottom
    a += rng.normal(0, noise, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8"))


def _bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


BLUE_SKY = _img((120, 170, 230), (60, 90, 40))
SUNSET = _img((235, 120, 40), (30, 25, 30))
GREY_WALL = _img((150, 150, 150), (115, 115, 115), noise=3)
NIGHT = _img((5, 5, 8), (3, 3, 4), noise=2)


def test_features_separate_obvious_skies():
    sky, sun, wall, night = (quality.extract(i) for i in (BLUE_SKY, SUNSET, GREY_WALL, NIGHT))
    assert sky.sky_share > 0.8 and sun.sky_share < 0.2
    assert sun.warm_share > 0.8 and sky.warm_share < 0.05
    assert sun.colourfulness > sky.colourfulness > wall.colourfulness
    assert wall.texture < 0.15 and night.mean_lum < 12
    for f in (sky, sun, wall, night):
        assert all(0 <= v <= 1 for k, v in f.as_dict().items() if k != "mean_lum")


def test_q_uses_type_weights():
    sun, sky = quality.extract(SUNSET), quality.extract(BLUE_SKY)
    assert quality.score(sun, profile("sunset").q) > quality.score(sky, profile("sunset").q)
    assert quality.score(sky, profile("thunderstorm").q) > quality.score(
        quality.extract(NIGHT), profile("thunderstorm").q
    )
    assert quality.score(sun, {}) == 0.5


def test_vlm_rules():
    ok = Verdict(usable=True, event_visible="yes", event_type_seen="lightning", confidence=0.9)
    assert resolve._vlm_reject(ok, "lightning", profile("lightning")) is None
    partial = ok.model_copy(update={"event_visible": "partial"})
    assert "partial" in resolve._vlm_reject(partial, "lightning", profile("lightning"))
    assert resolve._vlm_reject(partial, "thunderstorm", profile("thunderstorm")) == "saw lightning"
    night = Verdict(usable=True, event_visible="yes", event_type_seen="thunderstorm", night=True)
    night = night.model_copy(update={"confidence": 0.9})
    assert "night" in resolve._vlm_reject(night, "thunderstorm", profile("thunderstorm"))


@pytest.fixture
def fake_vlm(monkeypatch):
    verdicts: dict[str, Verdict] = {}

    class B:
        parallel = 8
        min_budget_s = 0

    async def fake_judge(content, ev_type, definition, ctx):
        cam = ctx.split(";")[0]
        return verdicts.get(cam)

    def fake_backend():
        return B()

    fake_backend.cache_clear = lambda: None  # conftest resets the real lru_cache on teardown
    monkeypatch.setattr(vlm, "backend", fake_backend)
    monkeypatch.setattr(vlm, "judge", fake_judge)
    monkeypatch.setattr(resolve, "_now", lambda: NOW)
    return verdicts


STORM = WeatherEvent(id="e1", type="thunderstorm", lat=39.7, lon=-104.9, radius_km=15, t_start=NOW)


async def test_resolve_q_ranks_passing_frames_and_logs(tmp_path, fake_vlm, monkeypatch):
    frames = {"east1": _bytes(GREY_WALL), "east2": _bytes(BLUE_SKY)}

    async def fake_fetch(http, cam, ts=None):
        return frame(frames.get(cam.id, jpeg()), ts=NOW - timedelta(minutes=1), cam=cam.id)

    monkeypatch.setattr(resolve, "fetch_frame", fake_fetch)
    yes = Verdict(usable=True, event_visible="yes", event_type_seen="thunderstorm", confidence=0.8)
    fake_vlm["east1"] = yes
    fake_vlm["east2"] = yes
    cat = Catalog.load(_catalog(tmp_path))
    log = tmp_path / "v.jsonl"
    async with httpx.AsyncClient() as http:
        res = await resolve.resolve_footage(
            STORM, cat, http, resolve.FrameCache(), k=2, verdict_log=str(log)
        )
    assert res.status == "FOOTAGE_FOUND" and res.footage[0].verified, res.rejected
    assert len(res.footage) == 2, res.rejected
    assert res.footage[0].camera_id == "east2"  # same confidence, higher Q wins
    assert res.footage[0].quality > res.footage[1].quality
    assert res.footage[0].features and "sky_share" in res.footage[0].features
    assert '"features": {' in log.read_text() and '"q": ' in log.read_text()


async def test_resolve_night_rule_and_low_quality(tmp_path, fake_vlm, monkeypatch):
    frames = {"east1": jpeg(seed=5), "east2": _bytes(GREY_WALL)}

    async def fake_fetch(http, cam, ts=None):
        return frame(frames.get(cam.id, jpeg()), ts=NOW - timedelta(minutes=1), cam=cam.id)

    monkeypatch.setattr(resolve, "fetch_frame", fake_fetch)
    fake_vlm["east1"] = Verdict(
        usable=True, event_visible="yes", event_type_seen="thunderstorm", confidence=0.9, night=True
    )
    fake_vlm["east2"] = Verdict(
        usable=True, event_visible="yes", event_type_seen="thunderstorm", confidence=0.9
    )
    cat = Catalog.load(_catalog(tmp_path))
    monkeypatch.setattr(resolve, "profile", lambda t: replace(profile(t), min_q=0.99))
    async with httpx.AsyncClient() as http:
        res = await resolve.resolve_footage(STORM, cat, http, resolve.FrameCache(), k=2)
    assert res.status == "LOW_QUALITY"
    by = {r.camera_id: r for r in res.rejected}
    assert by["east1"].stage == "vlm" and "night" in by["east1"].reason
    assert by["east2"].stage == "quality"
