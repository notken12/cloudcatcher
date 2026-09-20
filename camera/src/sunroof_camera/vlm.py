"""VLM gate: "is the target event type visible in this frame?" (plan §5).

Any OpenAI-chat-compatible vision endpoint, structured (pydantic) answer. Backend is chosen
by env:

    OPENAI_API_KEY                 -> api.openai.com, gpt-4o-mini (detail=low, ~$0.0001/frame)
    GROQ_API_KEY                   -> api.groq.com (free tier, ~1 s/frame), qwen/qwen3.8-27b
    SUNROOF_VLM_BASE_URL           -> e.g. http://127.0.0.1:11434/v1 (Ollama), vLLM, OpenRouter
    SUNROOF_VLM_MODEL[_LARGE]      -> model ids; _LARGE enables escalation on unsure verdicts
    SUNROOF_VLM_API_KEY            -> key for the custom base_url (Ollama ignores it)
    SUNROOF_VLM_BACKEND            -> openai | groq | ollama | custom | off; default = first key
                                      found in the order above (off disables the gate: CI, offline)

If neither key nor base_url is set but Ollama answers on localhost:11434, it's used automatically.
Frames are downscaled to ≤768 px before upload (~100 image tokens on OpenAI). Without any
backend the gate is skipped and footage goes out with `verified=False`.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

import httpx
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from .footage import Verdict

log = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/v1"
OLLAMA_MODEL = "qwen2.5vl:3b"
GROQ_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "qwen/qwen3.8-27b"
OPENAI_MODEL = "gpt-4o-mini"
# USD per 1M tokens (input, output) for the cost estimate in /health; approximate list prices
OPENAI_PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


@dataclass(frozen=True)
class Backend:
    base_url: str | None  # None -> api.openai.com
    api_key: str
    model: str
    model_large: str
    native_schema: bool  # server enforces json_schema (OpenAI); else json_object + validate
    parallel: int = 8  # concurrent calls; a CPU Ollama serialises anyway, so 1 there
    min_budget_s: float = 0  # floor for the VLM wait (local CPU models need ~30-45 s/frame)

    @property
    def name(self) -> str:
        return "openai" if self.base_url is None else self.base_url


@lru_cache(maxsize=1)
def backend() -> Backend | None:
    env = os.environ.get
    choice = env("SUNROOF_VLM_BACKEND", "").lower().replace("auto", "")
    if choice == "off":
        return None
    if choice not in ("", "openai", "groq", "ollama", "custom"):
        raise ValueError(f"SUNROOF_VLM_BACKEND={choice!r}: use openai|groq|ollama|custom|off")
    if choice == "openai" or (
        choice == "" and env("OPENAI_API_KEY") and not env("SUNROOF_VLM_BASE_URL")
    ):
        if not env("OPENAI_API_KEY"):
            raise ValueError("SUNROOF_VLM_BACKEND=openai but OPENAI_API_KEY is not set")
        model = env("SUNROOF_VLM_MODEL", OPENAI_MODEL)
        return Backend(
            None,
            env("OPENAI_API_KEY", ""),
            model,
            env("SUNROOF_VLM_MODEL_LARGE", model),  # no gpt-4o escalation unless asked (cost)
            native_schema=True,
            parallel=int(env("SUNROOF_VLM_PARALLEL", "8")),
        )
    url = env("SUNROOF_VLM_BASE_URL") if choice in ("", "custom") else None
    if choice == "custom" and not url:
        raise ValueError("SUNROOF_VLM_BACKEND=custom but SUNROOF_VLM_BASE_URL is not set")
    default_model, default_parallel = OLLAMA_MODEL, "4"
    if choice == "groq" or (not url and choice == "" and env("GROQ_API_KEY")):
        if not env("GROQ_API_KEY"):
            raise ValueError("SUNROOF_VLM_BACKEND=groq but GROQ_API_KEY is not set")
        url, default_model, default_parallel = GROQ_URL, GROQ_MODEL, "2"  # free tier 429s above ~2
    if choice == "ollama":
        url = OLLAMA_URL
    if not url:
        try:  # zero-config local fallback
            httpx.get(OLLAMA_URL.removesuffix("/v1") + "/api/tags", timeout=0.5).raise_for_status()
            url = OLLAMA_URL
        except httpx.HTTPError:
            return None
    model = env("SUNROOF_VLM_MODEL", default_model)
    local = "127.0.0.1" in url or "localhost" in url
    if local:
        default_parallel = "1"
    return Backend(
        url,
        env("SUNROOF_VLM_API_KEY") or env("GROQ_API_KEY") or env("OPENAI_API_KEY") or "local",
        model,
        env("SUNROOF_VLM_MODEL_LARGE", model),
        native_schema=False,
        parallel=int(env("SUNROOF_VLM_PARALLEL", default_parallel)),
        min_budget_s=float(env("SUNROOF_VLM_BUDGET_S", "150" if local else "0")),
    )


EVENT_TYPES = (
    "sunrise sunset thunderstorm lightning mammatus lenticular undercast aurora rainbow"
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
        default=None, description="timestamp text burned into the image, verbatim, else null"
    )


def available() -> bool:
    return backend() is not None


def describe() -> str:
    b = backend()
    return f"{b.name} / {b.model}" if b else "none"


@dataclass
class Usage:
    calls: int = 0
    failed: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    est_usd: float = 0.0

    def add(self, model: str, prompt: int, completion: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        price = OPENAI_PRICES.get(model)
        if price:
            self.est_usd += (prompt * price[0] + completion * price[1]) / 1e6

    def as_dict(self) -> dict:
        return {**self.__dict__, "est_usd": round(self.est_usd, 4)}


USAGE = Usage()  # process-wide tally, reported by /health


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


def _schema_hint(event_type: str) -> str:
    # a compact example beats the full JSON schema for small open models (fewer tokens, fewer
    # missing fields)
    example = {
        "usable": True,
        "sky_visible": 0.5,
        "night": False,
        "event_visible": "yes|partial|no|unsure",
        "event_type_seen": f"{event_type}|none|other",
        "confidence": 0.0,
        "quality": 3,
        "caption": "<= 12 words",
        "burned_in_time": None,
    }
    return (
        "Reply with only a JSON object with exactly these keys (quality 1-5, confidence 0-1):\n"
        + json.dumps(example, separators=(",", ":"))
    )


def _messages(b: Backend, event_type: str, definition: str, context: str, b64: str) -> list:
    text = _prompt(event_type, definition, context)
    if not b.native_schema:
        text += "\n\n" + _schema_hint(event_type)
    image = {"url": f"data:image/jpeg;base64,{b64}"}
    if b.base_url is None:
        image["detail"] = "low"
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": image},
            ],
        },
    ]


def _parse_loose(text: str) -> _Answer | None:
    """Small open models sometimes wrap JSON in ```fences or prose; dig it out and validate."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return _Answer.model_validate_json(text[start : end + 1])
    except ValidationError as e:
        err = e.errors()[0]
        log.warning(
            "vlm answer failed validation at %s: %s | %s", err["loc"], err["msg"], text[:300]
        )
        return None


def _client(b: Backend):
    from openai import AsyncOpenAI  # imported lazily so the package works without the SDK

    return AsyncOpenAI(base_url=b.base_url, api_key=b.api_key, timeout=max(120, b.min_budget_s))


async def judge(
    content: bytes,
    event_type: str,
    definition: str,
    context: str = "",
    model: str | None = None,
    allow_escalation: bool = True,
) -> Verdict | None:
    """One structured verdict for one frame, or None if the VLM is unavailable / errored."""
    b = backend()
    if b is None:
        return None
    client = _client(b)
    model = model or b.model
    b64 = _prep(content, 768 if b.native_schema else 512)
    messages = _messages(b, event_type, definition, context, b64)
    try:
        if b.native_schema:
            resp = await client.chat.completions.parse(
                model=model,
                temperature=0,
                max_tokens=200,
                messages=messages,
                response_format=_Answer,
            )
            ans = resp.choices[0].message.parsed
        else:
            resp = await client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=300,
                messages=messages,
                response_format={"type": "json_object"},
            )
            ans = _parse_loose(resp.choices[0].message.content or "")
    except Exception as e:  # noqa: BLE001 - network / API errors are all "no verdict"
        USAGE.failed += 1
        log.warning("vlm call failed (%s): %s", b.name, e)
        return None
    if resp.usage is not None:
        USAGE.add(model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
        log.info(
            "vlm %s: %d+%d tokens (session total %d calls, ~$%.4f)",
            model,
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
            USAGE.calls,
            USAGE.est_usd,
        )
    if ans is None:
        return None
    v = Verdict(**ans.model_dump(), model=model)
    hard = event_type in ("mammatus", "lenticular", "aurora")
    if (
        allow_escalation
        and model != b.model_large
        and (v.event_visible == "unsure" or (hard and v.confidence < 0.6))
    ):
        return await judge(content, event_type, definition, context, b.model_large, False)
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
