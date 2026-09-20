"""Adapter parsing against captured endpoint shapes (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pandas as pd

from sunroof_camera.ingest.build import dedupe_views, merge_shards
from sunroof_camera.ingest.sources import (
    algo,
    andes_volcano,
    austin,
    caltrans,
    cars_gql,
    cars_list,
    deldot,
    digitraffic,
    drivebc,
    fotowebcam,
    hongkong,
    nsw_maritime,
    nzta,
    panomax,
    qld,
    seattle,
    singapore,
    taiwan,
    tfl,
    travelmidwest,
    tripcheck,
    vegvesen,
)
from sunroof_camera.schema import Camera, to_frame, upsert, write_parquet


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


def test_nsw_maritime_resolves_hls_from_widget():
    def feat(fid, loc, widget):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [149.92, -36.89]},
            "properties": {"WEB_CAMERA_ID": fid, "LOCATION": loc, "LIVE_FEED": widget},
        }

    cams = _run(
        nsw_maritime.NSWMaritimeAdapter(),
        {
            "maritime_web_camera.geojson": {
                "features": [
                    feat(1, "Merimbula", "https://widget.example/video/aaa"),
                    feat(2, "Nowhere", "https://widget.example/video/bbb"),
                ]
            },
            "video/aaa": '<script>src: "https://cdn.example/cw/merimbula.stream/playlist.m3u8"</script>',
            "video/bbb": "<html>no stream here</html>",
        },
    )
    assert [c.id for c in cams] == ["au_nsw_maritime:1"]
    assert cams[0].source_kind == "hls"
    assert cams[0].stream_url == "https://cdn.example/cw/merimbula.stream/playlist.m3u8"
    assert cams[0].over_water and cams[0].lat == -36.89


def test_qld_geojson_direction_and_skips():
    def feat(fid, img, direction):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [153.01, -27.56]},
            "properties": {
                "id": fid,
                "description": f"cam {fid}",
                "direction": direction,
                "image_url": img,
            },
        }

    cams = _run(
        qld.QLDTrafficAdapter(),
        {
            "webcameras.geojson": {
                "features": [
                    feat(1, "https://cameras/a.jpg", "NorthEast"),
                    feat(2, None, "South"),
                    {"type": "Feature", "geometry": {}, "properties": {"id": 3, "image_url": "x"}},
                ]
            }
        },
    )
    assert [c.id for c in cams] == ["au_qld:1"]
    assert cams[0].azimuth_deg == 45 and cams[0].lat == -27.56 and cams[0].lon == 153.01
    assert cams[0].tz == "Australia/Brisbane"


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


def test_deldot_hls_enabled_filter():
    def cam(cid, enabled=True, status="Active", urls=True):
        return {
            "id": cid,
            "title": f"DE 1 @ {cid}",
            "lat": 38.99,
            "lon": -75.44,
            "enabled": enabled,
            "status": status,
            "urls": {"m3u8s": f"https://video.deldot.gov:443/live/{cid}.stream/playlist.m3u8"}
            if urls
            else {},
        }

    cams = _run(
        deldot.DelDOTAdapter(),
        {
            "videocamera.json": {
                "videoCameras": [
                    cam("KCAM001"),
                    cam("KCAM002", enabled=False),
                    cam("KCAM003", status="Inactive"),
                    cam("KCAM004", urls=False),
                ]
            }
        },
    )
    assert [c.id for c in cams] == ["deldot:KCAM001"]
    assert cams[0].source_kind == "hls" and cams[0].stream_url.endswith(
        "KCAM001.stream/playlist.m3u8"
    )
    assert cams[0].image_url is None and cams[0].heading_conf == "unknown"


def test_algo_public_only_and_direction():
    def cam(cid, direction, access="Public"):
        return {
            "id": cid,
            "accessLevel": access,
            "location": {
                "latitude": 30.5,
                "longitude": -88.2,
                "direction": direction,
                "displayRouteDesignator": "I-10",
                "displayCrossStreet": "McDonald Rd",
                "city": "Mobile",
            },
            "snapshotImageUrl": f"https://api.algotraffic.com/v4/Cameras/{cid}/snapshot.jpg",
            "playbackUrls": {"hls": f"https://cdn/{cid}/playlist.m3u8"},
            "permLink": f"https://www.algotraffic.com?cameraId={cid}",
        }

    cams = _run(
        algo.AlgoTrafficAdapter(),
        {"v4.0/Cameras": [cam(1, "East"), cam(2, "Any"), cam(3, "North", access="ALDOT")]},
    )
    assert [c.id for c in cams] == ["al_algo:1", "al_algo:2"]
    assert cams[0].azimuth_deg == 90 and cams[0].heading_conf == "text"
    assert cams[0].name == "I-10 @ McDonald Rd (Mobile)"
    assert cams[0].stream_url == "https://cdn/1/playlist.m3u8"
    assert cams[1].azimuth_deg is None and cams[1].heading_conf == "unknown"


def test_seattle_image_host_per_type_and_dedupe():
    payload = {
        "Features": [
            {
                "PointCoordinate": [47.52, -122.39],
                "Cameras": [
                    {
                        "Id": "CMR-0112",
                        "Description": "Fauntleroy",
                        "ImageUrl": "f.jpg",
                        "Type": "sdot",
                    },
                    {
                        "Id": "SR99Raye",
                        "Description": "SR-99",
                        "ImageUrl": "099vc03415.jpg",
                        "Type": "wsdot",
                    },
                    {"Id": "X1", "Description": "other", "ImageUrl": "x.jpg", "Type": "port"},
                ],
            },
            {
                "PointCoordinate": [47.6, -122.3],
                "Cameras": [
                    {"Id": "CMR-0112", "Description": "dup", "ImageUrl": "f.jpg", "Type": "sdot"}
                ],
            },
        ]
    }
    cams = _run(seattle.SeattleTravelersAdapter(), {"Travelers/api": payload})
    assert [c.id for c in cams] == ["seattle:sdot:CMR-0112", "seattle:wsdot:SR99Raye"]
    assert cams[0].image_url == "https://www.seattle.gov/trafficcams/images/f.jpg"
    assert cams[1].image_url == "https://images.wsdot.wa.gov/nw/099vc03415.jpg"
    assert cams[0].lat == 47.52 and cams[0].lon == -122.39


def test_austin_status_filter():
    def row(cid, status="TURNED_ON", img=True):
        return {
            "camera_id": cid,
            "location_name": f"cam {cid}",
            "camera_status": status,
            "screenshot_address": f"https://cctv.austinmobility.io/image/{cid}.jpg"
            if img
            else None,
            "location": {"type": "Point", "coordinates": [-97.69, 30.35]},
        }

    cams = _run(
        austin.AustinCCTVAdapter(),
        {"b4k4-adkb.json": [row("1"), row("2", status="TURNED_OFF"), row("3", img=False)]},
    )
    assert [c.id for c in cams] == ["austin:1"]
    assert (
        cams[0].lat == 30.35
        and cams[0].lon == -97.69
        and cams[0].license.startswith("Public Domain")
    )


def test_travelmidwest_one_row_per_view():
    payload = {
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-89.0, 40.5]},
                "properties": {
                    "id": "IL-IDOTD4-5003",
                    "description": "I-55 at I-39",
                    "urls": [
                        {"direction": "E", "url": "https://cctv/e.jpg"},
                        {"direction": "NONE", "url": "https://cctv/x.jpg"},
                        {"direction": "W", "url": None},
                    ],
                },
            }
        ]
    }
    cams = _run(travelmidwest.TravelMidwestAdapter(), {"cameras.json": payload})
    assert [c.id for c in cams] == [
        "travelmidwest:IL-IDOTD4-5003:E",
        "travelmidwest:IL-IDOTD4-5003:NONE",
    ]
    assert cams[0].azimuth_deg == 90 and cams[0].name == "I-55 at I-39 — E"
    assert cams[1].azimuth_deg is None and cams[1].name == "I-55 at I-39"


def test_upsert_keeps_probed_health_across_category_sets():
    mk = lambda cid, health: Camera(  # noqa: E731
        id=cid, source="deldot", source_kind="hls", name=cid, lat=39.0, lon=-75.0, health=health
    )
    base = to_frame([mk("deldot:a", "live"), mk("deldot:b", "dead")])
    new = to_frame([mk("deldot:a", "unverified"), mk("deldot:c", "unverified")])
    out = upsert(base, new).set_index("id")["health"].astype(str)
    assert out.to_dict() == {"deldot:b": "dead", "deldot:a": "live", "deldot:c": "unverified"}


def test_igp_peru_sector_and_site_placement():
    payload = {
        "data": [
            {
                "name": "Sabancaya",
                "slug": "sabancaya",
                "latitud": -15.7876,
                "longitud": -71.8558,
                "elevation": 5960,
                "camera": [
                    {
                        "id": 1,
                        "title": "Sector Noreste",
                        "link": "https://ide.igp.gob.pe/ltImages/Sabancaya.jpg",
                        "status": True,
                    },
                    {
                        "id": 4,
                        "title": "Quebrada Huayray (Pinchollo)",
                        "link": "https://ide.igp.gob.pe/ltImages/Sabancaya05.jpg",
                        "status": True,
                    },
                    {
                        "id": 7,
                        "title": "Off",
                        "link": "https://ide.igp.gob.pe/ltImages/x.jpg",
                        "status": False,
                    },
                    {"id": 8, "title": "No link", "link": None, "status": True},
                ],
            },
            {"name": "NoCoords", "slug": "n", "latitud": None, "longitud": None, "camera": []},
        ]
    }
    cams = _run(andes_volcano.IGPPeruAdapter(), {"api/volcanoes": payload})
    assert [c.id for c in cams] == ["pe_igp:1", "pe_igp:4"]
    ne = cams[0]
    # NE-sector site is ~7 km NE of the summit and looks back SW at it
    assert ne.lat > -15.7876 and ne.lon > -71.8558 and ne.azimuth_deg == 225
    assert ne.heading_conf == "inferred" and ne.tz == "America/Lima" and ne.night_ok is False
    assert cams[1].name.startswith("Sabancaya — ") and cams[1].image_url.endswith("Sabancaya05.jpg")


def test_sector_bearing_es_multiword_first():
    assert andes_volcano.sector_bearing_es("Sector Noreste") == 45
    assert andes_volcano.sector_bearing_es("Sector Sur") == 180
    assert andes_volcano.sector_bearing_es("Sector Sudoeste") == 225
    assert andes_volcano.sector_bearing_es("Pinchollo") is None
    lat, lon = andes_volcano.offset_km(0.0, 0.0, 90.0, 111.19)
    assert abs(lat) < 1e-6 and abs(lon - 1.0) < 1e-3


def test_igepn_parses_single_quoted_calls_ir_and_skips_fallback():
    html = """
    setImageWithFallback('rumVIS','https://www.igepn.edu.ec/images/portal/camaras/RUMVIS_HD.webp',
        'https://www.igepn.edu.ec/images/portal/camaras/loading.png', 640, 480);
    setImageWithFallback("rumIR", "https://www.igepn.edu.ec/images/portal/camaras/RUMIR_HD.webp");
    setImageWithFallback('rumVIS','https://www.igepn.edu.ec/images/portal/camaras/dup.webp');
    setImageWithFallback('tambo','https://www.igepn.edu.ec/images/portal/camaras/TAMBO.webp');
    """
    cams = _run(andes_volcano.IGEPNAdapter(), {"cotopaxi-camaras": html})
    by = {c.id: c for c in cams}
    assert set(by) == {"ec_igepn:rumVIS", "ec_igepn:rumIR", "ec_igepn:tambo"}
    vis, ir, tambo = by["ec_igepn:rumVIS"], by["ec_igepn:rumIR"], by["ec_igepn:tambo"]
    assert vis.image_url.endswith("RUMVIS_HD.webp") and vis.night_ok is False
    assert ir.night_ok is True and (vis.lat, vis.lon) == (ir.lat, ir.lon)
    assert vis.heading_conf == "inferred" and 100 < vis.azimuth_deg < 180  # site NW of summit
    assert tambo.heading_conf == "unknown" and (tambo.lat, tambo.lon) == (-0.677, -78.436)
    # co-located VIS/IR twins survive view dedupe
    assert len(dedupe_views(to_frame(cams))) == 3


def test_sgc_colombia_picks_full_res_and_skips_thumbs_and_dead_mirror():
    galeras = """
    <img src="https://amenazas.sgc.gov.co/ovspa/camaras/img-mini/barranco000.jpg">
    <img src="https://amenazas.sgc.gov.co/webcam/pasto/cumbal000.jpg">
    <a href="https://amenazas.sgc.gov.co/ovspa/camaras/barranco000.jpg">
    <a href="http://amenazas.sgc.gov.co/ovspa/camaras/galeras-consaca.jpg">
    <a href="https://amenazas.sgc.gov.co/ovspa/camaras/barranco000.jpg">
    """
    purace = """
    <a href="https://amenazas.sgc.gov.co/popayan/webcams/Mina2/imagen_web.jpg">
    <a href="https://amenazas.sgc.gov.co/popayan/webcams/Mina2_IR/imagen_web.jpg">
    """
    cams = _run(
        andes_volcano.SGCColombiaAdapter(), {"VolcanGaleras": galeras, "VolcanPurace": purace}
    )
    by = {c.id: c for c in cams}
    assert set(by) == {
        "co_sgc:barranco",
        "co_sgc:galeras-consaca",
        "co_sgc:mina2",
        "co_sgc:mina2_ir",
    }
    assert by["co_sgc:galeras-consaca"].image_url.startswith("https://")
    assert by["co_sgc:barranco"].name == "Galeras — Barranco Alto"
    assert by["co_sgc:mina2_ir"].night_ok and by["co_sgc:mina2_ir"].name == "Puracé — Mina2 (IR)"
    assert by["co_sgc:mina2"].heading_conf == "unknown"
    assert (
        andes_volcano.sgc_cam_key("https://x/ovspa/camaras/azufral-lag-hd000.jpg")
        == "azufral-lag-hd"
    )


def test_archive_camera_from_id_and_time():
    from datetime import datetime, timedelta, timezone

    from sunroof_camera.archive import archive_time, camera_from_id
    from sunroof_camera.ingest.sources.phenocam import closest_frame_path

    cam = camera_from_id("fotowebcam:zugspitze")
    assert cam is not None and cam.history_kind == "url_template"
    assert camera_from_id("caltrans:d3-123") is None
    assert camera_from_id("phenocam") is None

    t = datetime(2023, 6, 15, 18, 0, tzinfo=timezone(timedelta(hours=-6)))
    assert archive_time("iem", t) == datetime(2023, 6, 16, 0, 0)  # archive keyed in UTC
    assert archive_time("fotowebcam", t) == datetime(2023, 6, 15, 18, 0)  # wall clock

    html = (
        '<a href="/data/archive/harvard/2023/06/harvard_2023_06_15_112906.jpg">'
        '<img src="/data/archive/harvard/2023/06/harvard_2023_06_15_165906.jpg">'
    )
    assert closest_frame_path(html, datetime(2023, 6, 15, 17, 30)).endswith("165906.jpg")
    assert closest_frame_path("<html/>", datetime(2023, 6, 15)) is None


def test_downscale_jpeg():
    import io

    from PIL import Image

    from sunroof_camera.server import downscale_jpeg

    buf = io.BytesIO()
    Image.new("RGB", (1200, 675), "skyblue").save(buf, "JPEG")
    out, ctype = downscale_jpeg(buf.getvalue(), 480)
    assert ctype == "image/jpeg" and Image.open(io.BytesIO(out)).size == (480, 270)
    small, _ = downscale_jpeg(buf.getvalue(), 2000)
    assert small == buf.getvalue()
    assert downscale_jpeg(b"not an image", 480) == (b"not an image", "image/jpeg")


def test_history_cache_lru():
    from sunroof_camera.server import HistoryCache

    c = HistoryCache(capacity=2)
    c.put(("a", "t", None), (b"1", "image/jpeg", "u"))
    c.put(("b", "t", None), (b"2", "image/jpeg", "u"))
    assert c.get(("a", "t", None)) == (b"1", "image/jpeg", "u")  # touch a → b is oldest
    c.put(("c", "t", 240), (b"3", "image/jpeg", "u"))
    assert c.get(("b", "t", None)) is None
    assert c.get(("a", "t", None)) is not None
    assert c.get(("c", "t", None)) is None  # width is part of the key
