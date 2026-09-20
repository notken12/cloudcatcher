"""Adapter parsing against captured endpoint shapes (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pandas as pd

from sunroof_camera.ingest.build import dedupe_views, merge_shards
from sunroof_camera.ingest.sources import (
    caltrans,
    cars_list,
    digitraffic,
    drivebc,
    fotowebcam,
    nzta,
    panomax,
    tripcheck,
)
from sunroof_camera.schema import Camera, to_frame, write_parquet


def _client(payloads: dict[str, object]) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        for frag, body in payloads.items():
            if frag in str(req.url):
                if isinstance(body, str):
                    return httpx.Response(200, text=body)
                return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(adapter, payloads):
    async def go():
        async with _client(payloads) as http:
            return await adapter.catalog(http)

    return asyncio.run(go())


def test_panomax_centre_azimuth_and_night():
    cams = _run(
        panomax.PanomaxAdapter(),
        {
            "maps/panomaxweb": {
                "instances": {
                    "7": {
                        "id": 7,
                        "name": "Kitzsteinhorn",
                        "cam": {
                            "id": 7,
                            "latitude": 47.07,
                            "longitude": 12.75,
                            "zeroDirection": 211.5,
                            "viewAngle": 235,
                            "nightVision": True,
                        },
                    }
                }
            }
        },
    )
    assert len(cams) == 1
    c = cams[0]
    assert c.azimuth_deg == (211.5 + 117.5) % 360
    assert c.hfov_deg == 235 and c.night_ok and c.heading_conf == "catalog"


def test_digitraffic_skips_pavement_preset_and_keeps_others():
    feat = {
        "geometry": {"coordinates": [23.99, 60.05, 0.0]},
        "properties": {
            "id": "C01503",
            "name": "kt51_Inkoo",
            "collectionStatus": "GATHERING",
            "presets": [
                {"id": "C0150301", "inCollection": True},
                {"id": "C0150302", "inCollection": True},
                {"id": "C0150309", "inCollection": True},
            ],
        },
    }
    cams = _run(digitraffic.DigitrafficAdapter(), {"weathercam/v1/stations": {"features": [feat]}})
    assert [c.id for c in cams] == ["digitraffic:C0150301", "digitraffic:C0150302"]
    assert cams[0].image_url == "https://weathercam.digitraffic.fi/C0150301.jpg"
    # two presets at one site with unknown heading must survive dedupe
    df = dedupe_views(to_frame(cams))
    assert len(df) == 2


def test_fotowebcam_metadata_blob():
    blob = {
        "cams": [
            {
                "id": "moesern",
                "title": "Seefeld - Blick nach Westen",
                "offline": False,
                "hidden": False,
                "latitude": 47.31,
                "longitude": 11.14,
                "elevation": 1206,
                "direction": 255,
                "sector": 63,
                "captureInterval": 600,
            },
            {"id": "dead", "offline": True, "latitude": 1, "longitude": 1},
        ]
    }
    html = f"<script>var metadata= new Object({json.dumps(blob)});</script>"
    cams = _run(fotowebcam.FotoWebcamAdapter(), {"foto-webcam.eu": html})
    assert len(cams) == 1
    c = cams[0]
    assert c.azimuth_deg == 255 and c.hfov_deg == 63 and c.alt_m == 1206
    assert c.history_kind == "url_template" and "%Y/%m/%d/%H%M_la.jpg" in c.history_template


def test_caltrans_previous_template_and_bad_elevation():
    assert (
        caltrans._previous_template("https://x/image/hwy5/hwy5.jpg")
        == "https://x/image/hwy5/previous/hwy5-{n}.jpg"
    )
    assert caltrans._float("Not Reported") is None and caltrans._float("22") == 22.0


def test_merge_prefers_priority_source(tmp_path):
    (tmp_path / "shards").mkdir()
    mk = lambda src, cid: dict(  # noqa: E731
        id=cid, source=src, source_kind="jpeg", name=cid, lat=47.0, lon=12.0, azimuth_deg=90.0
    )
    write_parquet(to_frame([Camera(**mk("windy", "windy:1"))]), tmp_path / "shards" / "w.parquet")
    write_parquet(
        to_frame([Camera(**mk("panomax", "panomax:1"))]), tmp_path / "shards" / "p.parquet"
    )
    df = merge_shards(tmp_path)
    assert list(df["source"].astype(str)) == ["panomax"]
    assert isinstance(df, pd.DataFrame)


def test_nzta_xml_direction_and_offline_filter():
    xml = """<?xml version="1.0"?><response>
    <camera><description>South along SH1</description><direction>Southbound</direction><id>714</id>
      <imageUrl>/camera/714.jpg</imageUrl><latitude>-43.9</latitude><longitude>171.7</longitude>
      <name>SH1 Tinwald</name><offline>false</offline><underMaintenance>false</underMaintenance>
      <viewUrl>/camera/view/714</viewUrl></camera>
    <camera><description>x</description><direction>Northbound</direction><id>1</id><imageUrl>/camera/1.jpg</imageUrl>
      <latitude>-41</latitude><longitude>174</longitude><name>dead</name><offline>true</offline>
      <underMaintenance>false</underMaintenance><viewUrl>/camera/view/1</viewUrl></camera>
    </response>"""
    cams = _run(nzta.NZTAAdapter(), {"cameras/all": xml})
    assert [c.id for c in cams] == ["nzta:714"]
    assert cams[0].azimuth_deg == 180 and cams[0].heading_conf == "text"
    assert cams[0].image_url == "https://trafficnz.info/camera/714.jpg"


def test_tripcheck_filename_heading_and_bad_coords():
    assert tripcheck.heading_from_filename("AstoriaUS101MeglerBrNB_pid392.jpg") == 0
    assert tripcheck.heading_from_filename("FooSW_pid1.jpg") == 225
    assert tripcheck.heading_from_filename("Foo_pid1.jpg") is None
    feats = {
        "features": [
            {
                "attributes": {
                    "cameraId": 1,
                    "filename": "aNB_pid1.jpg",
                    "latitude": 45.0,
                    "longitude": -122.0,
                    "title": "a",
                }
            },
            {
                "attributes": {
                    "cameraId": 2,
                    "filename": "b_pid2.jpg",
                    "latitude": 45.0,
                    "longitude": 226.0,
                    "title": "b",
                }
            },
        ]
    }
    cams = _run(tripcheck.TripCheckAdapter(), {"cctvinventory": feats})
    assert [c.id for c in cams] == ["tripcheck:1"]


def test_drivebc_orientation_and_off_filter():
    cam = {
        "id": 2,
        "isOn": True,
        "shouldAppear": True,
        "camName": "Coquihalla - N",
        "orientation": "N",
        "location": {"latitude": 49.6, "longitude": -121.16, "elevation": 980},
        "links": {
            "imageDisplay": "https://images.drivebc.ca/bchighwaycam/pub/cameras/2.jpg",
            "replayTheDay": "r",
        },
    }
    off = {**cam, "id": 3, "isOn": False}
    cams = _run(drivebc.DriveBCAdapter(), {"api/v1/webcams": {"webcams": [cam, off]}})
    assert len(cams) == 1 and cams[0].azimuth_deg == 0 and cams[0].alt_m == 980
    assert cams[0].heading_conf == "catalog"


def test_cars_list_paging_wkt_and_filters():
    def site(i, direction, desc, **im):
        return {
            "id": i,
            "direction": direction,
            "location": f"I-95 @ MM {i}",
            "latLng": {"geography": {"wellKnownText": "POINT (-81.1 26.2)"}},
            "images": [
                {
                    "id": i * 10,
                    "description": desc,
                    "imageUrl": f"/map/Cctv/{i * 10}",
                    "disabled": False,
                    "blocked": False,
                    **im,
                }
            ],
        }

    page0 = {"recordsTotal": 101, "data": [site(1, "Northbound", "")] * 100}
    page1 = {
        "recordsTotal": 101,
        "data": [site(2, "Unknown", "Looking SW"), site(3, "Unknown", "", disabled=True)],
    }
    adapter = cars_list.CARS_LIST_ADAPTERS[0]()
    cams = _run(adapter, {"%22start%22%3A0%2C": page0, "%22start%22%3A100%2C": page1})
    assert len(cams) == 101
    assert cams[0].lat == 26.2 and cams[0].lon == -81.1 and cams[0].azimuth_deg == 0
    assert cams[-1].id.endswith(":2:20") and cams[-1].azimuth_deg == 225
    assert cams[-1].image_url == f"https://{adapter.host}/map/Cctv/20"
    assert cars_list.parse_wkt_point("junk") is None
