"""SYNOP surface reports (WMO FM-12) from OGIMET: the only free worldwide observation of cloud *genus*.

Manned stations report, in section 1's 8NhCLCMCH group, the kind of low, middle and high cloud the observer sees
(WMO code tables 0513, 0515, 0509) plus total cover in oktas; automated stations leave the group out. Around
2026-09-20 00-03Z, 9,244 stations reported worldwide and 2,752 carried genus: 62 in Canada, 26 in Japan, 9 in
France, 1 in the US. One whole-world 3-hour request is 3.5 MB; the client asks OGIMET once per hour.

Coordinates come from NOAA's ISD station history (the first five digits of its USAF id are the WMO index).
"""
import csv
import datetime as dt
import os
import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import requests

from common.geo import distance_km

OGIMET = "https://www.ogimet.com/cgi-bin/getsynop"
ISD_HISTORY = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
USER_AGENT = "cloudcatcher (github.com/notken12/sunroof; kenzhou084@gmail.com)"
WINDOW = dt.timedelta(hours=3)
SECTION_MARKER = re.compile(r"^(222\d\d|333|444|555)$")

LOW = {1: "cumulus humilis", 2: "cumulus mediocris or congestus", 3: "cumulonimbus calvus", 4: "stratocumulus from cumulus",
       5: "stratocumulus", 6: "stratus", 7: "fractus of bad weather", 8: "cumulus and stratocumulus", 9: "cumulonimbus capillatus"}
MIDDLE = {1: "altostratus translucidus", 2: "altostratus opacus or nimbostratus", 3: "altocumulus translucidus",
          4: "altocumulus lenticularis", 5: "altocumulus invading", 6: "altocumulus from cumulus", 7: "altocumulus with altostratus",
          8: "altocumulus castellanus", 9: "chaotic sky"}
HIGH = {1: "cirrus fibratus", 2: "cirrus spissatus", 3: "cirrus from cumulonimbus", 4: "cirrus uncinus invading",
        5: "cirrostratus invading below 45 deg", 6: "cirrostratus invading above 45 deg", 7: "cirrostratus covering the sky",
        8: "cirrostratus not covering the sky", 9: "cirrocumulus"}

# Sunset prior per genus, whitepaper-style post-processing from physics rather than data: thin or broken middle and high
# cloud catches the under-horizon light, opaque layers and low stratus block it. Unvalidated: no colour index exists
# anywhere genus is reported (the US test frames have none), so the factor is bounded to stay a nudge.
FAVOURABLE = {"low": set(), "middle": {3, 4, 5, 6, 8}, "high": {1, 2, 3, 4, 5, 8, 9}}
UNFAVOURABLE = {"low": {5, 6, 7}, "middle": {2, 7}, "high": {7}}
FAVOURABLE_FACTOR = 1.2
UNFAVOURABLE_FACTOR = 0.7
FACTOR_BOUNDS = (0.5, 1.4)

# Genera worth a camera on their own, with how much of a show they are (the event severity). Shares are of the
# world's genus reports for that level, 2026-09-20 00-03Z: the rarer, the higher. Lenticularis is the camera side's
# own `lenticular` event type; the rest go out as `rare_cloud` with the genus named in evidence.
RARE = {
    ("middle", 9): ("chaotic sky", 1.0),                        # 0.1 %
    ("high", 3): ("cirrus from cumulonimbus", 0.9),             # 0.9 %: anvil remnants
    ("high", 9): ("cirrocumulus", 0.8),                          # 1.2 %: mackerel sky
    ("middle", 8): ("altocumulus castellanus", 0.8),            # 1.5 %: turrets
    ("high", 7): ("cirrostratus covering the sky", 0.6),        # 1.8 %: halo weather
    ("high", 4): ("cirrus uncinus", 0.6),                        # 2.1 %: mares' tails
}
LENTICULAR = ("middle", 4)
LENTICULAR_SEVERITY = 0.9


@dataclass(frozen=True)
class Report:
    station: str
    name: str
    lat: float
    lon: float
    observed_at: dt.datetime
    total_oktas: int | None
    low: int | None
    middle: int | None
    high: int | None

    def genus_factor(self) -> float:
        factor = 1.0
        for level, code in (("low", self.low), ("middle", self.middle), ("high", self.high)):
            if code in FAVOURABLE[level]:
                factor *= FAVOURABLE_FACTOR
            if code in UNFAVOURABLE[level]:
                factor *= UNFAVOURABLE_FACTOR
        return float(np.clip(factor, *FACTOR_BOUNDS))

    def rare_genera(self) -> list[tuple[str, str, float]]:
        """(event type, genus, severity) for each reported genus that is a sight in itself."""
        codes = [(level, code) for level, code in (("low", self.low), ("middle", self.middle), ("high", self.high)) if code is not None]
        found: list[tuple[str, str, float]] = [("rare_cloud", *RARE[key]) for key in codes if key in RARE]
        if LENTICULAR in codes:
            found.append(("lenticular", MIDDLE[LENTICULAR[1]], LENTICULAR_SEVERITY))
        return found

    def describe(self) -> dict:
        return {
            "station": self.station, "name": self.name, "observed_at": self.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_oktas": self.total_oktas,
            "low": LOW.get(self.low or 0), "middle": MIDDLE.get(self.middle or 0), "high": HIGH.get(self.high or 0),
            "genus_factor": round(self.genus_factor(), 2),
        }


def digit(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def decode(message: str) -> tuple[int | None, int | None, int | None, int | None] | None:
    """(total oktas, CL, CM, CH) from an AAXX message's section 1; None when the message has no cloud-genus group."""
    groups = message.split("=")[0].split()
    if len(groups) < 5 or groups[0] != "AAXX":
        return None
    section = []
    for group in groups[3:]:
        if SECTION_MARKER.match(group):
            break
        section.append(group)
    if len(section) < 2 or len(section[1]) != 5:
        return None
    genus = next((g for g in section[2:] if len(g) == 5 and g[0] == "8"), None)
    if genus is None:
        return None
    return digit(section[1][0]), digit(genus[2]), digit(genus[3]), digit(genus[4])


@lru_cache(maxsize=1)
def stations(cache_dir: str) -> dict[str, tuple[float, float, str]]:
    """WMO index -> (lat, lon, name) from the ISD history, newest record per index; downloaded once into cache_dir."""
    path = os.path.join(cache_dir, "isd-history.csv")
    if not os.path.exists(path):
        os.makedirs(cache_dir, exist_ok=True)
        response = requests.get(ISD_HISTORY, headers={"User-Agent": USER_AGENT}, timeout=120)
        response.raise_for_status()
        with open(path, "wb") as f:
            f.write(response.content)
    ended: dict[str, str] = {}
    out: dict[str, tuple[float, float, str]] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            index = row["USAF"][:5]
            if not index.isdigit() or index == "99999" or not row["LAT"] or not row["LON"] or row["LAT"] == "+00.000":
                continue
            if row["END"] > ended.get(index, ""):
                ended[index] = row["END"]
                out[index] = (float(row["LAT"]), float(row["LON"]), row["STATION NAME"])
    return out


@lru_cache(maxsize=4)
def fetch(begin: dt.datetime, end: dt.datetime) -> str:
    response = requests.get(OGIMET, params={"begin": begin.strftime("%Y%m%d%H%M"), "end": end.strftime("%Y%m%d%H%M")},
                            headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    return response.text


def reports(when: dt.datetime, cache_dir: str) -> list[Report]:
    """Genus-bearing reports observed in the WINDOW ending at `when` (rounded down to the hour, so a looping run asks
    OGIMET once per hour), at stations with known coordinates."""
    end = when.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)
    coordinates = stations(cache_dir)
    out = []
    for line in fetch(end - WINDOW, end).splitlines():
        parts = line.split(",", 6)
        if len(parts) < 7 or parts[0] not in coordinates:
            continue
        decoded = decode(parts[6])
        if decoded is None or decoded[1:] == (None, None, None):
            continue
        lat, lon, name = coordinates[parts[0]]
        observed = dt.datetime(int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), tzinfo=dt.UTC)
        out.append(Report(parts[0], name, lat, lon, observed, *decoded))
    return out


def nearest(observations: list[Report], lat: float, lon: float, target: dt.datetime, max_km: float, max_age: dt.timedelta) -> Report | None:
    """The closest report within max_km observed no more than max_age before `target`."""
    recent = [r for r in observations if dt.timedelta(0) <= target - r.observed_at <= max_age]
    if not recent:
        return None
    distances = distance_km(lat, lon, np.array([r.lat for r in recent]), np.array([r.lon for r in recent]))
    best = int(np.argmin(distances))
    return recent[best] if distances[best] <= max_km else None


if __name__ == "__main__":
    now = dt.datetime.now(dt.UTC)
    observations = reports(now, "/tmp/cloudcatcher-grib")
    print(len(observations), "genus reports in the last 3 h; nearest to Tokyo:",
          nearest(observations, 35.68, 139.69, now, 150, WINDOW))
