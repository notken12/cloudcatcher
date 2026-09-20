"""Web Push (VAPID) for the installed PWA: subscriptions, users + preferences, throttled sends.

    GET  /push/vapid-public-key      applicationServerKey for PushManager.subscribe
    POST /push/subscribe             {subscription, user_id?} -> stored (upsert on endpoint)
    POST /push/unsubscribe           {endpoint}
    POST /users                      {name, email?, likes: [event_type]} -> {id, ...}
    GET  /users/{id}                 profile + likes
    PUT  /users/{id}/prefs           {likes: [event_type]}

The VAPID key pair lives in `--push-key` (PEM, generated on first run) so subscriptions
survive restarts. `SUNROOF_PUBLIC_URL` (e.g. https://sunroof.example) makes notification
images and links absolute; without it notifications carry text only.

Send policy (`notify`): every FOOTAGE_FOUND result is a candidate. A subscription gets it iff
    * its user (if any) has no likes yet, or the event type is one of them, and
    * we have not sent this event_id to that endpoint before, and
    * the last send to that endpoint was >= MIN_GAP_S ago (liked types halve the gap).
Rarer events (higher `rarity`) win when several arrive inside one gap.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from .events_db import RARITY_PRIOR
from .footage import FootageResult
from .query import EventType

log = logging.getLogger(__name__)

MIN_GAP_S = 3 * 3600  # at most one push per subscription per 3 h (1.5 h for liked types)
TTL_S = 20 * 60  # a live-sky ping is stale after ~20 min

EMOJI: dict[str, str] = {
    "sunrise": "🌅",
    "sunset": "🌇",
    "thunderstorm": "⛈️",
    "lightning": "⚡",
    "mammatus": "☁️",
    "lenticular": "🛸",
    "undercast": "🌫️",
    "aurora": "🌌",
    "rainbow": "🌈",
}
HEADLINE: dict[str, str] = {
    "sunrise": "Sunrise glow right now",
    "sunset": "The sky is on fire",
    "thunderstorm": "A storm tower is building",
    "lightning": "Lightning, live",
    "mammatus": "Mammatus pouches overhead",
    "lenticular": "Lenticular clouds hovering",
    "undercast": "A sea of cloud from above",
    "aurora": "Aurora dancing right now",
    "rainbow": "A rainbow just appeared",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    likes TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    endpoint TEXT PRIMARY KEY,
    info TEXT NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    created TEXT NOT NULL,
    last_sent TEXT
);
CREATE TABLE IF NOT EXISTS sends (
    endpoint TEXT NOT NULL,
    event_id TEXT NOT NULL,
    sent TEXT NOT NULL,
    PRIMARY KEY (endpoint, event_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


class PushStore:
    """SQLite behind the push endpoints; a separate file from the events DB (--db)."""

    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # -- users -------------------------------------------------------------------------
    def create_user(self, name: str, email: str | None, likes: list[str]) -> dict[str, Any]:
        uid = uuid.uuid4().hex[:12]
        self.conn.execute(
            "INSERT INTO users (id, name, email, likes, created) VALUES (?,?,?,?,?)",
            (uid, name.strip(), (email or "").strip() or None, json.dumps(likes), _now()),
        )
        self.conn.commit()
        return self.get_user(uid)  # type: ignore[return-value]

    def get_user(self, uid: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return _user(row) if row else None

    def set_likes(self, uid: str, likes: list[str]) -> dict[str, Any] | None:
        cur = self.conn.execute("UPDATE users SET likes=? WHERE id=?", (json.dumps(likes), uid))
        self.conn.commit()
        return self.get_user(uid) if cur.rowcount else None

    # -- subscriptions -----------------------------------------------------------------
    def subscribe(self, info: dict[str, Any], user_id: str | None) -> None:
        if user_id is not None and self.get_user(user_id) is None:
            user_id = None
        self.conn.execute(
            "INSERT INTO subscriptions (endpoint, info, user_id, created) VALUES (?,?,?,?) "
            "ON CONFLICT(endpoint) DO UPDATE SET info=excluded.info, "
            "user_id=COALESCE(excluded.user_id, subscriptions.user_id)",
            (info["endpoint"], json.dumps(info), user_id, _now()),
        )
        self.conn.commit()

    def unsubscribe(self, endpoint: str) -> bool:
        cur = self.conn.execute("DELETE FROM subscriptions WHERE endpoint=?", (endpoint,))
        self.conn.execute("DELETE FROM sends WHERE endpoint=?", (endpoint,))
        self.conn.commit()
        return bool(cur.rowcount)

    def subscriptions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT s.endpoint, s.info, s.last_sent, u.likes FROM subscriptions s "
            "LEFT JOIN users u ON u.id = s.user_id"
        ).fetchall()
        return [
            {
                "endpoint": r["endpoint"],
                "info": json.loads(r["info"]),
                "last_sent": _parse(r["last_sent"]),
                "likes": json.loads(r["likes"]) if r["likes"] else [],
            }
            for r in rows
        ]

    def already_sent(self, endpoint: str, event_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sends WHERE endpoint=? AND event_id=?", (endpoint, event_id)
        ).fetchone()
        return row is not None

    def mark_sent(self, endpoint: str, event_id: str) -> None:
        now = _now()
        self.conn.execute(
            "INSERT OR REPLACE INTO sends (endpoint, event_id, sent) VALUES (?,?,?)",
            (endpoint, event_id, now),
        )
        self.conn.execute("UPDATE subscriptions SET last_sent=? WHERE endpoint=?", (now, endpoint))
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]


def _user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "likes": json.loads(row["likes"]),
        "created": row["created"],
    }


# -- VAPID ---------------------------------------------------------------------------------


def load_vapid(key_file: Path | str) -> Vapid:
    """PEM private key; generated (and saved) on first use."""
    Path(key_file).parent.mkdir(parents=True, exist_ok=True)
    return Vapid.from_file(str(key_file))


def public_key_b64(vapid: Vapid) -> str:
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# -- notifications -------------------------------------------------------------------------


def payload(res: FootageResult, public_url: str | None = None) -> dict[str, Any] | None:
    """Notification JSON for the service worker, or None when there is nothing to show."""
    if res.status != "FOOTAGE_FOUND" or not res.footage:
        return None
    f = res.footage[0]
    t = f.event_type
    where = f.camera.name
    caption = (f.verdict.caption if f.verdict else "").strip().rstrip(".")
    body = f"{where}" + (f" — {caption}" if caption else "")
    base = (public_url or "").rstrip("/")
    out: dict[str, Any] = {
        "title": f"{EMOJI.get(t, '✨')} {HEADLINE.get(t, t.title())}",
        "body": body[:180],
        "tag": res.event_id,
        "url": f"{base}/" if base else "/",
        "type": t,
        "event_id": res.event_id,
    }
    poster = f.media.poster or (f.media.src if f.media.kind == "image" else None)
    if poster and base:
        out["image"] = poster if poster.startswith("http") else base + poster
    return out


def wants(
    sub: dict[str, Any], ev_type: str, event_id: str, now: datetime, store: PushStore
) -> bool:
    likes: list[str] = sub["likes"]
    if likes and ev_type not in likes:
        return False
    if store.already_sent(sub["endpoint"], event_id):
        return False
    gap = MIN_GAP_S / 2 if ev_type in likes else MIN_GAP_S
    last: datetime | None = sub["last_sent"]
    return last is None or (now - last).total_seconds() >= gap


def rarity_of(res: FootageResult) -> float:
    t: EventType | str = res.footage[0].event_type if res.footage else ""
    return RARITY_PRIOR.get(t, 0.0)


class Notifier:
    def __init__(
        self,
        store: PushStore,
        vapid: Vapid,
        public_url: str | None = None,
        contact: str = "mailto:sunroof@example.com",
    ):
        self.store = store
        self.vapid = vapid
        self.public_url = public_url or os.environ.get("SUNROOF_PUBLIC_URL")
        self.contact = contact
        self.sent = 0
        self.failed = 0

    async def notify(self, res: FootageResult) -> int:
        """Fan a new result out to every subscription that wants it. Returns sends."""
        data = payload(res, self.public_url)
        if data is None:
            return 0
        now = datetime.now(timezone.utc)
        targets = [
            s
            for s in self.store.subscriptions()
            if wants(s, data["type"], res.event_id, now, self.store)
        ]
        if not targets:
            return 0
        body = json.dumps(data)
        results = await asyncio.gather(
            *(asyncio.to_thread(self._send, s["info"], body) for s in targets),
            return_exceptions=True,
        )
        n = 0
        for s, r in zip(targets, results):
            if r is True:
                self.store.mark_sent(s["endpoint"], res.event_id)
                n += 1
            elif r == "gone":
                self.store.unsubscribe(s["endpoint"])
        self.sent += n
        log.info(
            "push: event %s (%s) -> %d/%d subscriptions",
            res.event_id,
            data["type"],
            n,
            len(targets),
        )
        return n

    def _send(self, info: dict[str, Any], body: str) -> bool | str:
        try:
            webpush(
                subscription_info=info,
                data=body,
                vapid_private_key=self.vapid,
                vapid_claims={"sub": self.contact},
                ttl=TTL_S,
                timeout=10,
            )
            return True
        except WebPushException as e:  # 404/410 = the browser dropped the subscription
            status = getattr(e.response, "status_code", None)
            self.failed += 1
            if status in (404, 410):
                return "gone"
            log.warning("push failed (%s): %s", status, e)
            return False
        except Exception as e:  # noqa: BLE001 — never let one bad endpoint break the fan-out
            self.failed += 1
            log.warning("push error: %s", e)
            return False
