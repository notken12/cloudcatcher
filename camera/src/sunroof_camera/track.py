"""Camera track record: what the VLM has said about each camera so far, turned into a ranking
prior (design doc §2 Stage E, §4.3).

Every verdict already lands in `verdicts.jsonl`; this module folds that log into a per
(camera, event type) Beta posterior of "the event was visible when we asked", and exposes a
bounded multiplier for `Catalog.find_cameras` and the in-event best-frame pick. It is a ranker:
a camera with no history is neutral (1.0), one with confirmed positives is boosted, one that was
asked many times and never showed anything is demoted — never excluded.

Why this beats geometry alone: in the 993-event replay two IEM cameras (Big Creek Marina, ISU Ag
Farm) produced 33 of the 40 confirmed sunsets; nothing in their catalog row distinguishes them
from the neighbours that produced none.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

VIS_WEIGHT = {"yes": 1.0, "partial": 0.5}
MIN_MULT, MAX_MULT = 0.75, 1.35
PRIOR_STRENGTH = 4.0  # pseudo-observations behind the type-wide base rate


@dataclass
class _Stat:
    judged: int = 0
    hits: float = 0.0  # yes = 1, partial = 0.5
    q_sum: float = 0.0
    vlm_quality_sum: float = 0.0


@dataclass
class CameraTrack:
    per_cam: dict[tuple[str, str], _Stat] = field(default_factory=lambda: defaultdict(_Stat))
    per_type: dict[str, _Stat] = field(default_factory=lambda: defaultdict(_Stat))

    @classmethod
    def from_log(cls, path: str | Path | None) -> CameraTrack:
        t = cls()
        if not path or not Path(path).exists():
            return t
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not r.get("usable", True) or "camera_id" not in r:
                    continue
                t.record(
                    r["camera_id"],
                    r.get("event_type", ""),
                    r.get("event_visible", "unsure"),
                    q=r.get("q"),
                    vlm_quality=r.get("quality"),
                )
        return t

    def record(
        self,
        camera_id: str,
        event_type: str,
        event_visible: str,
        q: float | None = None,
        vlm_quality: int | None = None,
    ) -> None:
        w = VIS_WEIGHT.get(event_visible, 0.0)
        for s in (self.per_cam[(camera_id, event_type)], self.per_type[event_type]):
            s.judged += 1
            s.hits += w
            if w:
                s.q_sum += q or 0.0
                s.vlm_quality_sum += vlm_quality or 0

    def hit_rate(self, camera_id: str, event_type: str) -> float:
        """Posterior mean P(visible) for this camera, shrunk to the type-wide base rate."""
        base = self.per_type.get(event_type)
        p0 = (base.hits + 0.5) / (base.judged + 1.0) if base else 0.05
        s = self.per_cam.get((camera_id, event_type))
        if s is None:
            return p0
        return (s.hits + PRIOR_STRENGTH * p0) / (s.judged + PRIOR_STRENGTH)

    def multiplier(self, camera_id: str, event_type: str) -> float:
        """Bounded ranking multiplier; 1.0 for an unknown camera."""
        s = self.per_cam.get((camera_id, event_type))
        if s is None or s.judged == 0:
            return 1.0
        base = self.per_type.get(event_type)
        p0 = (base.hits + 0.5) / (base.judged + 1.0) if base else 0.05
        p = self.hit_rate(camera_id, event_type)
        # log-odds gain over the base rate, damped by how much we have actually seen
        gain = math.log((p + 1e-3) / (p0 + 1e-3))
        conf = 1.0 - math.exp(-s.judged / 6.0)
        return float(min(MAX_MULT, max(MIN_MULT, 1.0 + 0.35 * gain * conf)))

    def summary(self, event_type: str | None = None, min_hits: float = 1.0) -> list[dict]:
        """Cameras with confirmed positives, best first — the 'what to demo' list."""
        rows = []
        for (cid, typ), s in self.per_cam.items():
            if event_type and typ != event_type or s.hits < min_hits:
                continue
            n = max(1, round(s.hits))
            rows.append(
                {
                    "camera_id": cid,
                    "event_type": typ,
                    "judged": s.judged,
                    "hits": s.hits,
                    "hit_rate": round(self.hit_rate(cid, typ), 3),
                    "mean_q": round(s.q_sum / n, 3),
                    "mean_vlm_quality": round(s.vlm_quality_sum / n, 2),
                    "multiplier": round(self.multiplier(cid, typ), 3),
                }
            )
        rows.sort(key=lambda r: (-r["hits"], -r["hit_rate"], -r["mean_q"]))
        return rows
