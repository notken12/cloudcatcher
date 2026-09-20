from datetime import datetime, timezone

import numpy as np
import pytest

from sunroof_camera import geometry as g
from sunroof_camera import solar
from sunroof_camera.query import Catalog, Event
from sunroof_camera.schema import Camera, to_frame


def test_haversine_bearing():
    # Boston -> New York
    d = g.haversine_km(42.36, -71.06, 40.71, -74.01)
    assert 295 < d < 310
    assert 225 < g.bearing_deg(42.36, -71.06, 40.71, -74.01) < 240


def test_annulus_road_cam_anvil():
    d_min, d_max = g.annulus_km(12.0, -3.0, 20.0, 150.0)
    assert d_min == pytest.approx(33.0, abs=1)
    assert d_max == 150.0  # elev_min <= 0 -> curvature horizon, capped


def test_annulus_allsky():
    d_min, d_max = g.annulus_km(12.0, 0.0, 90.0, 150.0)
    assert d_min == 0.0


def test_apparent_elevation_curvature():
    flat = np.degrees(np.arctan(12 / 150))
    assert g.apparent_elevation_deg(12.0, 150.0) == pytest.approx(flat - 0.67, abs=0.05)


def test_bearing_ok_tolerance():
    ok = g.bearing_ok(
        bearing=np.array([100.0, 100.0, 100.0, 100.0]),
        azimuth=np.array([60.0, 60.0, np.nan, 60.0]),
        hfov_deg=np.array([60.0, 60.0, 60.0, 60.0]),
        heading_conf=np.array(["catalog", "text", "unknown", "ptz"]),
        d_km=np.full(4, 50.0),
        event_radius_km=0.0,
    )
    assert list(ok) == [False, True, True, True]


def test_sun_position_boston_noon():
    el, az = solar.sun_position_deg(
        42.36, -71.06, datetime(2026, 6, 21, 16, 45, tzinfo=timezone.utc)
    )
    assert 70 < el < 72
    assert 170 < az < 190


def _cams():
    return to_frame(
        [
            Camera(
                id="a",
                source="manual",
                source_kind="jpeg",
                name="road east",
                lat=40.0,
                lon=-105.0,
                azimuth_deg=90,
                hfov_deg=60,
                elev_min_deg=-3,
                elev_max_deg=20,
                heading_conf="catalog",
                refresh_s=60,
            ),
            Camera(
                id="b",
                source="manual",
                source_kind="jpeg",
                name="allsky",
                lat=40.0,
                lon=-105.0,
                azimuth_deg=None,
                hfov_deg=360,
                elev_min_deg=0,
                elev_max_deg=90,
                heading_conf="catalog",
                all_sky=True,
                night_ok=True,
            ),
            Camera(
                id="c",
                source="manual",
                source_kind="jpeg",
                name="road west",
                lat=40.5,
                lon=-105.0,
                azimuth_deg=270,
                hfov_deg=60,
                elev_min_deg=-3,
                elev_max_deg=20,
                heading_conf="catalog",
            ),
        ]
    )


def test_thunderstorm_overhead_excludes_horizon_cam():
    cat = Catalog(_cams())
    noon = datetime(2026, 7, 1, 19, 0, tzinfo=timezone.utc)
    res = cat.find_cameras(
        Event(type="thunderstorm", lat=40.0, lon=-105.0, radius_km=5, t=noon), k=10
    )
    assert list(res["id"]) == ["b"]  # only the all-sky cam sees an anvil overhead


def test_thunderstorm_east_60km():
    cat = Catalog(_cams())
    noon = datetime(2026, 7, 1, 19, 0, tzinfo=timezone.utc)
    res = cat.find_cameras(
        Event(type="thunderstorm", lat=40.0, lon=-104.3, radius_km=5, t=noon), k=10
    )
    assert "a" in set(res["id"]) and "c" not in set(res["id"])


def test_aurora_requires_night():
    cat = Catalog(_cams())
    noon = datetime(2026, 7, 1, 19, 0, tzinfo=timezone.utc)
    midnight = datetime(2026, 12, 1, 7, 0, tzinfo=timezone.utc)
    assert cat.find_cameras(Event(type="aurora", lat=44.0, lon=-105.0, radius_km=200, t=noon)).empty
    assert list(
        cat.find_cameras(Event(type="aurora", lat=44.0, lon=-105.0, radius_km=200, t=midnight))[
            "id"
        ]
    ) == ["b"]


def test_ignore_night_skips_solar_gate():
    cat = Catalog(_cams())
    midnight = datetime(2026, 12, 1, 7, 0, tzinfo=timezone.utc)
    ev = Event(type="thunderstorm", lat=40.0, lon=-104.3, radius_km=5, t=midnight)
    assert "a" not in set(cat.find_cameras(ev, k=10)["id"])  # not night_ok
    assert "a" in set(cat.find_cameras(ev.model_copy(update={"ignore_night": True}), k=10)["id"])
