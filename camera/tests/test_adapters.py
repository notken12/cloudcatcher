"""Adapter parsing against captured endpoint shapes (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pandas as pd

from sunroof_camera.ingest.build import dedupe_views, merge_shards
from sunroof_camera.ingest.sources import (
    caltrans,
    cars_gql,
    cars_list,
    digitraffic,
    drivebc,
    fotowebcam,
    hongkong,
    nzta,
    panomax,
    singapore,
    taiwan,
    tfl,
    tripcheck,
    vegvesen,
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


def test_hongkong_xml():
    xml = """<?xml version="1.0"?><image-list>
    <image><key>H421F</key><description>Aberdeen Tunnel [H421F]</description>
      <latitude>22.24986</latitude><longitude>114.17557</longitude>
      <url>https://tdcctv.data.one.gov.hk/H421F.JPG</url></image>
    <image><key>X</key><url>u</url></image></image-list>"""
    cams = _run(hongkong.HongKongTDAdapter(), {"Traffic_Camera_Locations_En.xml": xml})
    assert [c.id for c in cams] == ["hk_td:H421F"]
    assert cams[0].tz == "Asia/Hong_Kong" and cams[0].image_url.endswith("H421F.JPG")


def test_singapore_unions_recent_batches():
    def batch(*ids):
        return {
            "items": [
                {
                    "cameras": [
                        {
                            "camera_id": i,
                            "image": f"https://images.data.gov.sg/x/{i}.jpg",
                            "location": {"latitude": 1.3, "longitude": 103.8},
                        }
                        for i in ids
                    ]
                }
            ]
        }

    calls = iter([batch("2701"), batch("2701", "4703"), batch(), batch(), batch()])

    def handler(req: httpx.Request) -> httpx.Response:
        assert "date_time=" in str(req.url)
        return httpx.Response(200, json=next(calls))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await singapore.SingaporeLTAAdapter().catalog(http)

    cams = asyncio.run(go())
    assert sorted(c.id for c in cams) == ["sg_lta:2701", "sg_lta:4703"]
    assert cams[0].image_url is None and cams[0].history_kind == "api"


def test_cars_gql_active_filter_strips_query_and_finds_hls():
    feats = [
        {
            "bbox": [-93.1, 44.9, -93.1, 44.9],
            "title": "I-94 @ Hwy 280",
            "uri": "camera/1",
            "__typename": "Camera",
            "active": True,
            "views": [
                {
                    "uri": "camera/1/10",
                    "category": "VIDEO",
                    "url": "https://x/1.jpg?1700000000",
                    "title": "I-94 WB @ Hwy 280",
                    "sources": [{"type": "application/x-mpegURL", "src": "https://x/1.m3u8"}],
                },
                {"uri": "camera/1/11", "category": "IMAGE", "url": None, "title": "dead"},
                {"uri": "camera/1/12", "category": "IMAGE", "url": "https://x/icon.svg"},
            ],
        },
        {
            "bbox": [-93.2, 44.8, -93.2, 44.8],
            "title": "inactive",
            "uri": "camera/2",
            "__typename": "Camera",
            "active": False,
            "views": [{"uri": "camera/2/1", "category": "IMAGE", "url": "https://x/2.jpg"}],
        },
    ]
    payload = {"data": {"mapFeaturesQuery": {"mapFeatures": feats, "error": None}}}
    cams = _run(cars_gql.CARS_GQL_ADAPTERS[0](), {"/api/graphql": payload})
    assert [c.id for c in cams] == ["cars_mn:1:10"]
    c = cams[0]
    assert c.image_url == "https://x/1.jpg" and c.stream_url == "https://x/1.m3u8"
    assert c.azimuth_deg == 270 and c.heading_conf == "text" and c.refresh_s == 120
    assert (c.lat, c.lon) == (44.9, -93.1)


def test_taiwan_mjpeg_vs_snapshot_and_first_frame():
    hw = {
        "CCTVs": [
            {
                "CCTVID": "A",
                "PositionLat": 24.1,
                "PositionLon": 120.6,
                "RoadName": "台1線",
                "RoadDirection": "N",
                "VideoImageURL": "https://cctv.thb.gov.tw/A.jpg",
                "VideoStreamURL": "https://cctv.thb.gov.tw/A.m3u8",
            },
            {"CCTVID": "nocoords", "VideoImageURL": "https://x/n.jpg"},
        ]
    }
    fw = {
        "CCTVs": [
            {
                "CCTVID": "B",
                "PositionLat": 25.0,
                "PositionLon": 121.5,
                "RoadName": "國道1號",
                "VideoStreamURL": "https://cctvn.freeway.gov.tw/abs2mjpg/bmjpg?camera=B",
            },
            {
                "CCTVID": "C",
                "PositionLat": 25.0,
                "PositionLon": 121.5,
                "VideoStreamURL": "https://x/C.m3u8",
            },
        ]
    }
    cams = _run(taiwan.TaiwanTDXAdapter(), {"CCTV/Highway": hw, "CCTV/Freeway": fw})
    assert [c.id for c in cams] == ["tw_tdx:A", "tw_tdx:B"]
    assert cams[0].image_url.endswith("A.jpg") and cams[0].stream_url is None
    assert cams[0].azimuth_deg == 0
    assert cams[1].image_url.startswith("https://cctvn.freeway.gov.tw") and cams[1].stream_url
    buf = b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8abc\xff\xd9\r\n--boundary"
    assert taiwan.first_mjpeg_frame(buf) == b"\xff\xd8abc\xff\xd9"
    assert taiwan.first_mjpeg_frame(b"\xff\xd8partial") is None


def test_tfl_available_filter_and_view_heading():
    def place(pid, avail, view):
        return {
            "id": f"JamCams_{pid}",
            "commonName": f"cam {pid}",
            "lat": 51.5,
            "lon": -0.1,
            "additionalProperties": [
                {"key": "available", "value": avail},
                {"key": "imageUrl", "value": f"https://s3/{pid}.jpg"},
                {"key": "videoUrl", "value": f"https://s3/{pid}.mp4"},
                {"key": "view", "value": view},
            ],
        }

    cams = _run(
        tfl.TfLJamCamAdapter(),
        {
            "Place/Type/JamCam": [
                place("00001.1", "true", "North East"),
                place("00001.2", "false", "West"),
            ]
        },
    )
    assert [c.id for c in cams] == ["tfl:00001.1"]
    assert cams[0].azimuth_deg == 45 and cams[0].stream_url.endswith(".mp4")
    assert cams[0].tz == "Europe/London"


def test_vegvesen_status_altitude_and_naming():
    payload = {
        "measurementSites": [
            {
                "id": "2000065",
                "name": "Aisaroaivi",
                "location": {
                    "geometry": {"type": "Point", "coordinates": [24.1, 70.28]},
                    "heightAboveSeaLevel": 237.7,
                    "road": {"number": "E6"},
                },
                "cameras": [
                    {
                        "id": "2000065_1",
                        "orientationDescription": "Skaidi",
                        "stillImageUrl": "https://kamera.atlas.vegvesen.no/api/images/2000065_1",
                        "videoUrl": "https://kamera.vegvesen.no/public/2000065_1/manifest.m3u8",
                        "status": "OK",
                    },
                    {
                        "id": "2000065_2",
                        "stillImageUrl": "https://kamera.atlas.vegvesen.no/api/images/2000065_2",
                        "status": "OUT_OF_SERVICE",
                    },
                ],
            },
            {"id": "x", "location": {}, "cameras": []},
        ]
    }
    cams = _run(vegvesen.VegvesenAdapter(), {"measurement-sites": payload})
    assert [c.id for c in cams] == ["no_vegvesen:2000065_1"]
    c = cams[0]
    assert (
        c.name == "E6 Aisaroaivi (Skaidi)" and c.alt_m == 237.7 and c.stream_url.endswith(".m3u8")
    )
    assert (c.lat, c.lon) == (70.28, 24.1) and c.heading_conf == "unknown"
