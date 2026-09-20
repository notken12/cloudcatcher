"""Per-event-type knobs for the fetch → gate → VLM → route stages (plan §2).

Geometry / night rules live in `query.PARAMS` (find_cameras); this table holds what happens
*after* the candidate list exists: how many to fetch, how fresh a frame must be, whether the
VLM should run at all, how long a verified frame is held, and what to tell the VLM to look for.
"""

from __future__ import annotations

from dataclasses import dataclass

from .query import EventType


@dataclass(frozen=True)
class EventProfile:
    type: EventType
    fetch_mult: int = 3  # fetch k * fetch_mult candidates
    max_age_mult: float = 2.0  # frame older than this * refresh_s => stale
    max_age_s: int = 1800  # absolute cap on staleness
    frames: int = 1  # frames per camera (lightning: burst)
    hold_s: int = 1200  # how long the frontend may show it before re-verify
    retry_after_s: int = 600  # what we suggest to the weather backend on failure
    min_conf: float = 0.5
    allow_night_frames: bool = False  # keep dark frames for the VLM
    vlm_definition: str = ""  # one-line definition appended to the prompt
    feasibility: int = 3  # 1 = hardest (sunrise/sunset) .. 5 = easiest


PROFILES: dict[str, EventProfile] = {
    "thunderstorm": EventProfile(
        "thunderstorm",
        hold_s=1200,
        vlm_definition="a cumulonimbus / storm cloud: towering dark convective cloud, anvil, "
        "rain shafts or a shelf cloud; not just overcast.",
        feasibility=5,
    ),
    "lightning": EventProfile(
        "lightning",
        frames=3,
        max_age_mult=3.0,
        hold_s=900,
        retry_after_s=300,
        allow_night_frames=True,
        vlm_definition="a visible lightning bolt or a cloud lit from inside by a flash.",
        feasibility=3,
    ),
    "mammatus": EventProfile(
        "mammatus",
        hold_s=900,
        vlm_definition="mammatus: pouch-like bulging lobes hanging from the underside of a cloud.",
        feasibility=4,
    ),
    "lenticular": EventProfile(
        "lenticular",
        hold_s=1800,
        vlm_definition="lenticular cloud: smooth lens / stacked-plate shaped stationary cloud, "
        "usually near mountains.",
        feasibility=4,
    ),
    "undercast": EventProfile(
        "undercast",
        hold_s=1800,
        vlm_definition="undercast / sea of clouds: the camera is above a cloud layer and looks "
        "down on its top with clear sky above.",
        feasibility=5,
    ),
    "aurora": EventProfile(
        "aurora",
        max_age_mult=3.0,
        hold_s=900,
        retry_after_s=300,
        allow_night_frames=True,
        min_conf=0.6,
        vlm_definition="aurora: green / red / purple glowing arcs, curtains or rays in a night sky.",
        feasibility=3,
    ),
    "rainbow": EventProfile(
        "rainbow",
        hold_s=600,
        retry_after_s=300,
        min_conf=0.6,
        vlm_definition="a rainbow: a coloured circular arc in the sky opposite the sun.",
        feasibility=2,
    ),
    "sunrise": EventProfile(
        "sunrise",
        max_age_mult=1.5,
        hold_s=300,
        retry_after_s=180,
        allow_night_frames=True,
        min_conf=0.6,
        vlm_definition="a colourful sunrise: warm orange / pink / red light on the horizon or lit "
        "cloud undersides; a plain bright sky does not count.",
        feasibility=1,
    ),
    "sunset": EventProfile(
        "sunset",
        max_age_mult=1.5,
        hold_s=300,
        retry_after_s=180,
        allow_night_frames=True,
        min_conf=0.6,
        vlm_definition="a colourful sunset: warm orange / pink / red light on the horizon or lit "
        "cloud undersides; a plain bright sky does not count.",
        feasibility=1,
    ),
}


def profile(t: str) -> EventProfile:
    return PROFILES[t]
