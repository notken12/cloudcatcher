"""VLM gate: "is the target event type visible in this frame?" (plan §5).

OpenAI mini-tier vision model with a structured (pydantic) answer. Frames are downscaled to
≤768 px before upload and sent with `detail: low` — one call is ~100 input tokens of image.
Without `OPENAI_API_KEY` the gate is skipped and footage goes out with `verified=False`.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from .footage import Verdict

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("SUNROOF_VLM_MODEL", "gpt-4o-mini")
ESCALATION_MODEL = os.environ.get("SUNROOF_VLM_MODEL_LARGE", "gpt-4o")
EVENT_TYPES = (
    "sunrise sunset thunderstorm lightning mammatus lenticular fog undercast aurora rainbow"
).split()

SYSTEM = (
    "You check public webcam frames for a weather-watching app. Answer strictly about what is "
    "visible in this single frame. Be conservative: say the event is visible only if a "
    "meteorologist would agree from this image alone. A plain or overcast sky is not an event."
)


class _Answer(BaseModel):
    usable: bool = Field(
        description="frame shows a real outdoor scene (not a colour card, error page, black frame)"
    )
    sky_visible: float = Field(ge=0, le=1, description="fraction of the frame that is sky")
    night: bool
    event_visible: Literal["yes", "partial", "no", "unsure"]
    event_type_seen: str = Field(description="one of the event types, 'none' or 'other'")
    confidence: float = Field(ge=0, le=1)
    quality: int = Field(ge=1, le=5, description="how good this frame would look on screen")
    caption: str = Field(description="<= 12 words")
    burned_in_time: str | None = Field(
        description="timestamp text burned into the image, verbatim, else null"
    )


def available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _prep(content: bytes, max_side: int = 768) -> str:
    img = Image.open(io.BytesIO(content)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def _prompt(event_type: str, definition: str, context: str) -> str:
    return (
        f"Target event: {event_type} — {definition}\n"
        f"Camera context: {context}\n"
        f"Is the target event visible? Also report which event type (of {', '.join(EVENT_TYPES)}, "
        "none, other) best matches what you see, and read any burned-in timestamp."
    )


async def judge(
    content: bytes,
    event_type: str,
    definition: str,
    context: str = "",
    model: str | None = None,
    allow_escalation: bool = True,
) -> Verdict | None:
    """One structured verdict for one frame, or None if the VLM is unavailable / errored."""
    if not available():
        return None
    from openai import AsyncOpenAI  # imported lazily so the package works without the SDK

    client = AsyncOpenAI()
    model = model or DEFAULT_MODEL
    b64 = _prep(content)
    try:
        resp = await client.beta.chat.completions.parse(
            model=model,
            temperature=0,
            max_tokens=200,
            messages=[
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _prompt(event_type, definition, context)},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
                        },
                    ],
                },
            ],
            response_format=_Answer,
        )
    except Exception as e:  # noqa: BLE001 - network / API errors are all "no verdict"
        log.warning("vlm call failed: %s", e)
        return None
    ans = resp.choices[0].message.parsed
    if ans is None:
        return None
    v = Verdict(**ans.model_dump(), model=model)
    hard = event_type in ("mammatus", "lenticular", "aurora")
    if (
        allow_escalation
        and model != ESCALATION_MODEL
        and (v.event_visible == "unsure" or (hard and v.confidence < 0.6))
    ):
        return await judge(content, event_type, definition, context, ESCALATION_MODEL, False)
    return v


def skipped_verdict(reason: str) -> Verdict:
    return Verdict(
        usable=True, event_visible="unsure", caption=reason, confidence=0.0, quality=3, model=None
    )


def log_verdict(path: str, camera_id: str, event_type: str, v: Verdict, sha1: str) -> None:
    """Append-only JSONL used later to learn night_ok / quality_score per camera."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "camera_id": camera_id,
        "event_type": event_type,
        "sha1": sha1,
        **v.model_dump(),
    }
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
