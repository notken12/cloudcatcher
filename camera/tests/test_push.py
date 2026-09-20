"""Web Push store, send policy, payload and the /push + /users routes (webpush mocked)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from sunroof_camera import push
from sunroof_camera.footage import CameraInfo, Footage, FootageResult, Media, Verdict

NOW = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)


def sub_info(n: int) -> dict:
    return {"endpoint": f"https://push.example/{n}", "keys": {"p256dh": "x", "auth": "y"}}


def result(
    event_id="e1", ev_type="sunset", status="FOOTAGE_FOUND", caption="Deep reds"
) -> FootageResult:
    f = Footage(
        event_id=event_id,
        event_type=ev_type,
        camera_id="c1",
        rank=1,
        verified=True,
        media=Media(kind="image", src="/proxy/frame/c1", poster="/proxy/frame/c1"),
        verdict=Verdict(
            usable=True, event_visible="yes", confidence=0.9, quality=4, caption=caption
        ),
        why="faces west",
        camera=CameraInfo(
            id="c1",
            name="Loveland Pass",
            lat=39.7,
            lon=-105.9,
            source="manual",
            distance_km=3,
            bearing_to_event=270,
        ),
        fetched_at=NOW,
        hold_until=NOW + timedelta(minutes=20),
    )
    return FootageResult(
        event_id=event_id, status=status, footage=[f] if status == "FOOTAGE_FOUND" else []
    )


def test_payload_text_and_absolute_image():
    p = push.payload(result())
    assert p["title"].endswith("The sky is on fire") and p["body"] == "Loveland Pass — Deep reds"
    assert p["url"] == "/" and "image" not in p and p["tag"] == "e1"
    p2 = push.payload(result(), "https://sunroof.app/")
    assert (
        p2["image"] == "https://sunroof.app/proxy/frame/c1" and p2["url"] == "https://sunroof.app/"
    )
    assert push.payload(result(status="NO_FOOTAGE_FOUND")) is None


def test_store_users_and_subscriptions(tmp_path):
    st = push.PushStore(tmp_path / "p.sqlite")
    u = st.create_user("Sofia", " s@example.com ", ["aurora", "sunset"])
    assert st.get_user(u["id"])["likes"] == ["aurora", "sunset"] and u["email"] == "s@example.com"
    assert st.set_likes(u["id"], ["rainbow"])["likes"] == ["rainbow"]
    assert st.set_likes("nope", []) is None
    st.subscribe(sub_info(1), None)
    st.subscribe(sub_info(1), u["id"])  # re-subscribe attaches the user, no duplicate row
    st.subscribe(sub_info(2), "ghost")  # unknown user -> anonymous
    subs = {s["endpoint"]: s for s in st.subscriptions()}
    assert st.count() == 2 and subs[sub_info(1)["endpoint"]]["likes"] == ["rainbow"]
    assert subs[sub_info(2)["endpoint"]]["likes"] == []
    assert st.unsubscribe(sub_info(2)["endpoint"]) and not st.unsubscribe("x")


def test_wants_policy(tmp_path):
    st = push.PushStore(tmp_path / "p.sqlite")
    anon = {"endpoint": "a", "likes": [], "last_sent": None}
    fan = {"endpoint": "b", "likes": ["aurora"], "last_sent": None}
    assert push.wants(anon, "sunset", "e1", NOW, st)
    assert not push.wants(fan, "sunset", "e1", NOW, st)  # not a liked type
    assert push.wants(fan, "aurora", "e1", NOW, st)
    st.subscribe({"endpoint": "a", "keys": {}}, None)
    st.mark_sent("a", "e1")
    assert not push.wants(anon, "sunset", "e1", NOW, st)  # dedupe per event
    recent = {"endpoint": "a", "likes": [], "last_sent": NOW - timedelta(hours=1)}
    assert not push.wants(recent, "sunset", "e2", NOW, st)  # 3 h gap for anonymous
    liked_recent = {"endpoint": "a", "likes": ["sunset"], "last_sent": NOW - timedelta(hours=2)}
    assert push.wants(liked_recent, "sunset", "e2", NOW, st)  # 1.5 h gap for liked types
    old = {"endpoint": "a", "likes": [], "last_sent": NOW - timedelta(hours=4)}
    assert push.wants(old, "sunset", "e2", NOW, st)


async def test_notifier_fanout_and_gone(tmp_path, monkeypatch):
    calls: list[str] = []

    class Resp:
        status_code = 410

    def fake_webpush(subscription_info, data, **kw):
        calls.append(subscription_info["endpoint"])
        if subscription_info["endpoint"].endswith("/2"):
            raise push.WebPushException("gone", response=Resp())
        return "ok"

    monkeypatch.setattr(push, "webpush", fake_webpush)
    st = push.PushStore(tmp_path / "p.sqlite")
    n = push.Notifier(st, push.load_vapid(tmp_path / "vapid.pem"))
    fan = st.create_user("F", None, ["aurora"])
    st.subscribe(sub_info(1), None)
    st.subscribe(sub_info(2), None)
    st.subscribe(sub_info(3), fan["id"])
    assert await n.notify(result("e1", "sunset")) == 1  # 1 ok, /2 gone (removed), fan skipped
    assert st.count() == 2 and sorted(calls) == [sub_info(1)["endpoint"], sub_info(2)["endpoint"]]
    assert await n.notify(result("e1", "sunset")) == 0  # dedupe
    assert await n.notify(result("e2", "aurora")) == 1  # only the fan (anon throttled)
    assert n.sent == 2 and n.failed == 1


async def test_push_routes(tmp_path, monkeypatch):
    from test_gates_resolve_server import _catalog

    from sunroof_camera.server import create_app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(
        catalog_path=_catalog(tmp_path),
        verdict_log=None,
        push_db=tmp_path / "push.sqlite",
        push_key=tmp_path / "vapid.pem",
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            key = (await c.get("/push/vapid-public-key")).json()["key"]
            assert len(key) == 87 and "=" not in key  # 65 raw bytes, url-safe, unpadded
            r = await c.post("/users", json={"name": "Sofia", "likes": ["aurora", "aurora"]})
            assert r.status_code == 201, r.text
            assert r.json()["likes"] == ["aurora"]
            uid = r.json()["id"]
            assert (await c.post("/users", json={"name": "", "likes": []})).status_code == 422
            assert (
                await c.post("/users", json={"name": "x", "likes": ["comet"]})
            ).status_code == 422
            r = await c.put(f"/users/{uid}/prefs", json={"likes": ["sunset"]})
            assert r.json()["likes"] == ["sunset"]
            assert (await c.get(f"/users/{uid}")).json()["name"] == "Sofia"
            assert (await c.get("/users/nope")).status_code == 404
            r = await c.post("/push/subscribe", json={"subscription": sub_info(1), "user_id": uid})
            assert r.status_code == 201 and r.json()["subscriptions"] == 1
            r = await c.post("/push/subscribe", json={"subscription": {"endpoint": "x"}})
            assert r.status_code == 422
            assert (await c.get("/health")).json()["push"]["subscriptions"] == 1
            r = await c.post("/push/unsubscribe", json={"endpoint": sub_info(1)["endpoint"]})
            assert r.json()["removed"] is True


async def test_push_routes_disabled(tmp_path, monkeypatch):
    from test_gates_resolve_server import _catalog

    from sunroof_camera.server import create_app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(catalog_path=_catalog(tmp_path), verdict_log=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            assert (await c.get("/push/vapid-public-key")).status_code == 503
            assert (await c.get("/health")).json()["push"] is None
