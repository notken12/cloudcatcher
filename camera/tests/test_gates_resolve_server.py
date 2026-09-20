"""Gates, FAA adapter mapping, resolver statuses and the FastAPI surface — all offline
(camera fetches are monkeypatched with synthetic JPEGs)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from sunroof_camera import gates, resolve
from sunroof_camera.footage import WeatherEvent
from sunroof_camera.ingest.base import Frame
from sunroof_camera.ingest.sources.faa import camera_row
from sunroof_camera.query import Catalog
from sunroof_camera.schema import Camera, to_frame, write_parquet

NOW = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)  # daytime over Colorado


def jpeg(w=640, h=480, mean=120, noise=40, seed=0, gradient=0.0) -> bytes:
    rng = np.random.default_rng(seed)
    ramp = np.linspace(-gradient, gradient, w)[None, :, None]
    a = np.clip(rng.normal(mean, noise, (h, w, 3)) + ramp, 0, 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def frame(content: bytes, ts: datetime | None = NOW - timedelta(minutes=2), cam="c") -> Frame:
    return Frame(
        camera_id=cam, ts=ts, url="http://x/y.jpg", content=content, content_type="image/jpeg"
    )


# --- gates --------------------------------------------------------------------


def test_gate_passes_normal_frame():
    g = gates.check_frame(frame(jpeg()), refresh_s=600, now=NOW)
    assert g.ok and g.ts_source == "source_api" and g.age_s == pytest.approx(120)
    assert g.phash is not None and g.width == 640


def test_gate_rejects_bytes_level_problems():
    assert "too small" in gates.check_frame(frame(b"x" * 100), 600, now=NOW).reason
    html = b"<html>" + b" " * 5000
    assert "not an image" in gates.check_frame(frame(html), 600, now=NOW).reason
    assert (
        "frozen"
        in gates.check_frame(frame(jpeg()), 600, now=NOW, last_sha1=frame(jpeg()).sha1).reason
    )


def test_gate_rejects_pixel_level_problems():
    assert (
        "blown-out"
        in gates.check_frame(frame(jpeg(mean=246, noise=2, gradient=12)), 600, now=NOW).reason
    )
    assert "uniform" in gates.check_frame(frame(jpeg(mean=128, noise=0.5)), 600, now=NOW).reason
    dark = gates.check_frame(frame(jpeg(mean=6, noise=3, gradient=6)), 600, now=NOW)
    assert not dark.ok and dark.night
    assert gates.check_frame(
        frame(jpeg(mean=6, noise=3, gradient=6)), 600, now=NOW, allow_night=True
    ).ok


def test_gate_freshness():
    old = frame(jpeg(), ts=NOW - timedelta(hours=3))
    assert "stale" in gates.check_frame(old, 600, now=NOW).reason
    no_ts = gates.check_frame(frame(jpeg(), ts=None), 600, now=NOW)
    assert no_ts.ok and no_ts.ts_source == "fetch_time"  # never rejected for staleness, but flagged
    assert any("fetch time" in n for n in no_ts.notes)


def test_phash_similarity():
    a = gates.check_frame(frame(jpeg(seed=1)), 600, now=NOW).phash
    b = gates.check_frame(frame(jpeg(seed=1)), 600, now=NOW).phash
    c = gates.check_frame(frame(jpeg(seed=2, mean=60)), 600, now=NOW).phash
    assert a is not None and b is not None and c is not None
    assert gates.hamming(a, b) == 0
    assert gates.hamming(a, c) > 4


# --- FAA adapter mapping -------------------------------------------------------


def test_faa_camera_row():
    site = {
        "siteId": 168,
        "siteName": "Noatak",
        "latitude": 67.57,
        "longitude": -162.97,
        "elevation": 56,
        "timeZone": "America/Anchorage",
        "cameras": [],
    }
    cam = {
        "cameraId": 10523,
        "cameraDirection": "West",
        "cameraBearing": 285,
        "cameraLastSuccess": "2026-09-20T05:32:11.980Z",
        "cameraMaintenance": False,
    }
    c = camera_row(site, cam)
    assert c.id == "faa:10523" and c.source == "faa"
    assert c.azimuth_deg == 285 and c.heading_conf == "catalog"
    assert c.alt_m == pytest.approx(17.07, abs=0.1)
    assert c.tz == "America/Anchorage" and c.refresh_s == 600
    assert c.image_url.endswith("/cameras/10523/images/last/1")
    assert c.last_frame_ts is not None and c.last_frame_ts.tzinfo is not None


# --- resolver + server ------------------------------------------------------------


def _catalog(tmp_path: Path) -> Path:
    def cam(id, lat, lon, az, alt=1600.0, night_ok=False):
        return Camera(
            id=id,
            source="manual",
            source_kind="jpeg",
            name=id,
            lat=lat,
            lon=lon,
            alt_m=alt,
            tz="America/Denver",
            azimuth_deg=az,
            hfov_deg=60,
            elev_min_deg=-3,
            elev_max_deg=25,
            heading_conf="catalog",
            sky_frac=0.5,
            night_ok=night_ok,
            image_url=f"http://x/{id}.jpg",
            refresh_s=300,
            health="live",
            last_frame_ts=NOW,
        )

    # storm at (39.7, -104.9): cams 60 km west looking east (in the anvil annulus), one looking away
    cams = [
        cam("east1", 39.7, -105.6, 90),
        cam("east2", 39.75, -105.6, 85),
        cam("away", 39.7, -105.6, 270),
    ]
    p = tmp_path / "cameras.parquet"
    write_parquet(to_frame(cams), p)
    return p


STORM = WeatherEvent(id="e1", type="thunderstorm", lat=39.7, lon=-104.9, radius_km=15, t_start=NOW)


@pytest.fixture
def patched_fetch(monkeypatch):
    calls: dict[str, bytes] = {}

    async def fake_fetch(http, cam, ts=None):
        content = calls.get(cam.id, jpeg(seed=hash(cam.id) % 1000))
        return frame(content, ts=NOW - timedelta(minutes=1), cam=cam.id)

    monkeypatch.setattr(resolve, "fetch_frame", fake_fetch)
    monkeypatch.setattr(resolve, "_now", lambda: NOW)
    return calls


async def test_resolve_found_without_vlm(tmp_path, patched_fetch, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cat = Catalog.load(_catalog(tmp_path))
    async with httpx.AsyncClient() as http:
        res = await resolve.resolve_footage(STORM, cat, http, resolve.FrameCache(), k=2)
    assert res.status == "FOOTAGE_FOUND"
    assert res.vlm_calls == 0 and not res.footage[0].verified
    assert {f.camera_id for f in res.footage} == {"east1", "east2"}
    f = res.footage[0]
    assert f.media.kind == "image" and f.media.src == "/proxy/frame/" + f.camera_id
    assert f.ts_source == "source_api" and f.camera.tz == "America/Denver"


async def test_resolve_all_stale_and_frozen(tmp_path, patched_fetch):
    patched_fetch["east1"] = jpeg(mean=246, noise=2, gradient=12)  # blown out
    patched_fetch["east2"] = jpeg(mean=128, noise=0.2)  # uniform
    cat = Catalog.load(_catalog(tmp_path))
    async with httpx.AsyncClient() as http:
        res = await resolve.resolve_footage(STORM, cat, http, resolve.FrameCache(), k=2)
    assert res.status == "NO_FOOTAGE_FOUND"
    assert {r.stage for r in res.rejected} == {"gate"}
    assert res.retry_after_s


async def test_resolve_no_cameras_and_dark(tmp_path, patched_fetch):
    cat = Catalog.load(_catalog(tmp_path))
    async with httpx.AsyncClient() as http:
        far = STORM.model_copy(update={"id": "e2", "lat": 25.0, "lon": -80.0})
        assert (
            await resolve.resolve_footage(far, cat, http, resolve.FrameCache())
        ).status == "NO_CAMERAS_IN_RANGE"
        night = STORM.model_copy(update={"id": "e3", "t_start": NOW + timedelta(hours=10)})
        r = await resolve.resolve_footage(night, cat, http, resolve.FrameCache())
        assert r.status == "CAMERAS_DARK" and r.retry_after_s == 3600


async def test_server_routes(tmp_path, patched_fetch, monkeypatch):
    from sunroof_camera.server import create_app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(catalog_path=_catalog(tmp_path), verdict_log=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            assert (await c.get("/health")).json()["cameras"] == 3
            page = await c.get("/")
            assert page.status_code == 200 and "EventSource('/stream')" in page.text
            r = await c.post("/events", json=STORM.model_dump(mode="json"))
            assert r.status_code == 200 and r.json()["status"] == "FOOTAGE_FOUND"
            cam_id = r.json()["footage"][0]["camera_id"]
            fr = await c.get(f"/proxy/frame/{cam_id}")
            assert fr.status_code == 200 and fr.headers["content-type"] == "image/jpeg"
            assert fr.content[:2] == b"\xff\xd8" and "X-Frame-Ts" in fr.headers
            assert (await c.get("/feed")).json()[0]["event_id"] == "e1"
            assert (await c.get("/events/e1/footage")).status_code == 200
            assert (await c.get("/events/nope/footage")).status_code == 404
            assert (await c.get("/proxy/frame/nope")).status_code == 404
