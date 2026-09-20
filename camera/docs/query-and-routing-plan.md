# Camera query → fetch → verify → route (plan for the "after preprocessing" half)

Diagram: `query-routing-schema.svg` (same style as `preprocessing-schema.svg`).

Scope: everything that happens **after** `cameras.parquet` / `camera_samples.parquet`
exist (see `preprocessing-plan.md`, `preprocessing-schema.svg`). Input is an
`Event` from the weather backend; output is either a `Footage` envelope the
frontend can render as-is, or a status such as `NO_FOOTAGE_FOUND` back to the
weather backend. Written to be built in one session against the 10-row
`manual.yaml` fixture first, then scaled.

```
Event ──► profile(type) ──► find_cameras(event, k) ──► fetch top 3k frames
      ──► cheap gates (bytes/pixels) ──► VLM gate (top ≤6) ──► route
                                                           ├─ FOOTAGE_FOUND  → store → frontend (SSE)
                                                           └─ NO_FOOTAGE_FOUND{reason} → weather backend
```

Single entry point (the orchestrator calls this every ~10 min per live event,
and once per replay event):

```python
async def resolve_footage(event: Event, k: int = 3, deadline_s: float = 30) -> FootageResult
```

## 1. Contract with the weather backend

```python
class Event(BaseModel):                      # produced by the weather half, unchanged
    id: str
    type: Literal["sunrise","sunset","thunderstorm","lightning","mammatus",
                  "lenticular","fog","undercast","aurora","rainbow"]
    lat: float; lon: float; radius_km: float
    t_start: datetime; t_end: datetime       # UTC
    severity: float | None = None            # 0..1
    rarity: float | None = None
    evidence: dict = {}                      # e.g. {"ceiling_m": 900, "anvil_top_km": 12, "kp": 6}
    # Optional geometry for non-point events; if absent we use (lat, lon, radius_km)
    region: GeoJSONPolygon | None = None     # aurora oval segment, fog deck, storm cell polygon

class FootageResult(BaseModel):
    event_id: str
    status: Literal["FOOTAGE_FOUND","NO_FOOTAGE_FOUND","NO_CAMERAS_IN_RANGE",
                    "CAMERAS_DARK","ALL_STALE","EVENT_NOT_VISIBLE","TIMEOUT"]
    footage: list[Footage] = []              # 0..k, ranked; empty unless FOOTAGE_FOUND
    checked: int                             # cameras whose frame we actually fetched
    candidates: int                          # cameras find_cameras returned
    vlm_calls: int; tokens_in: int           # Token Company instrumentation
    elapsed_ms: int
    retry_after_s: int | None = None         # hint for the weather backend
    reason: str                              # human readable, logged + shown in debug UI
```

`fog` and `undercast` arrive as two types even though the UI shows one
"fog/undercast" chip: the camera rule is opposite (inside vs. above the layer),
so the weather side should emit whichever its ceiling/model supports, or both.

The non-`FOOTAGE_FOUND` statuses are all "no footage" to the frontend but tell
the weather backend *why*, so it can decide to retry (`ALL_STALE`,
`TIMEOUT`, `EVENT_NOT_VISIBLE` → retry in 10 min), skip (`CAMERAS_DARK` →
retry after sunrise unless type ∈ {aurora, lightning}), or drop
(`NO_CAMERAS_IN_RANGE` → not worth polling again for this event).

## 2. Per-event query schema

One `EventProfile` per type, stored as a Python dict / YAML, consumed by
`find_cameras()` and the gating stages. This is the "mini database query" — it
is a parametrised filter over the flat `cameras` table, not a different table
per type. Everything the preprocessing plan §3 calls "per-type rule" lives here
so the query engine stays generic.

```python
class EventProfile(BaseModel):
    feature_alt_km: float | None      # h for the annular-sector test; None = use sun geometry
    r_cap_km: float                   # hard radius; also the bbox prefilter
    geometry: Literal["annulus","sun_az","antisolar","inside_layer","above_layer","region"]
    sun_elev_range: tuple[float,float] | None      # required solar elevation at cam, deg
    night_policy: Literal["day_only","twilight_ok","night_ok_required","any"]
    require: dict                     # hard column predicates, e.g. {"elev_min_deg <=": 2}
    prefer: dict[str,float]           # soft bonuses added to score
    min_refresh_s: int | None         # reject cams slower than this (lightning)
    media_pref: list[str]             # ordering of source_kind to fetch: ["hls","jpeg","embed"]
    frames_per_cam: int               # 1 for static sky, 3–5 burst for lightning
    vlm_question: str                 # the enum member the VLM must confirm
    hold_s: int                       # how long a verified pair stays on the frontend
    reverify_s: int                   # how often to re-run cheap gates while holding
```

| type | geometry / h | R_cap | hard filters (`require`) | soft (`prefer`) | night_policy | media, frames | hold / reverify |
|---|---|---|---|---|---|---|---|
| **thunderstorm** | annulus, h = `evidence.anvil_top_km` or 12 | 150 km | `health==live`; elev_min ≤ 10 | 30–100 km distance +0.3; `sky_frac` ≥ 0.4 +0.2; ALERTCA/IEM/NDBC source prior | twilight_ok | jpeg > hls, 1 frame | 20 min / 5 min |
| **mammatus** | annulus, h = 4 | 40 km | `health==live`; sun_elev > -3 | `elev_max ≥ 40` +0.3 (near-overhead); IEM/foto-webcam prior; low sun (< 15°) +0.2 (side-lit) | day_only | jpeg, 1 frame | 20 min / 5 min |
| **lenticular** | annulus, h = `evidence.cloud_base_km` or 6 | 80 km | `health==live` | bearing toward `evidence.ridge_bearing` ±30° +0.3; Panomax/foto-webcam/Roundshot prior +0.2 | day_only | jpeg, 1 frame | 30 min / 10 min |
| **fog** | inside_layer: `alt_m < evidence.fog_top_m`; distance only | 10 km | `health==live`; `alt_m < fog_top_m` | `sky_frac` low is fine; DOT cams OK | twilight_ok | jpeg > hls, 1 frame | 30 min / 10 min |
| **undercast** | above_layer: `alt_m > fog_top_m` and `elev_min < 0` | 30 km | `health==live`; `alt_m > fog_top_m + 100` | Panomax/ALERTCA mountaintop prior +0.3; `hfov ≥ 180` +0.2 | twilight_ok | jpeg, 1 frame | 30 min / 10 min |
| **sunset / sunrise** | sun_az: `sun_az(cam, t_mid) ∈ az ± (hfov/2 + δ)` | `radius_km` (≤ 100) | `heading_conf ∈ {catalog,text}` (unknown-heading cams are useless here); `elev_min ≤ 2`; `sky_frac ≥ 0.3` | horizon over water/plain (`over_water`) +0.3; foto-webcam/Panomax prior +0.3; `severity` from weather side already encodes "will it be pretty" | any (it *is* twilight) | jpeg, 1 frame; **re-fetch every 2–3 min** inside the window | until sun_elev < -8 / 3 min |
| **rainbow** | antisolar: `(sun_az+180) ∈ az ± (hfov/2+42)`, sun_elev ∈ (0,42) | 5 km | `health==live`; `refresh_s ≤ 300` | wide hfov +0.3; `over_water` +0.1 | day_only | jpeg, 1 frame, re-fetch every refresh_s | 10 min / 2 min |
| **lightning** | annulus, h = 6 | 60 km | `refresh_s ≤ 60` **or** `stream_url`; `health==live` | hls +0.5; ALERTCA IR +0.2; all_sky +0.3 | **any** (best at night) | **hls first**, 3–5 frames over 10–20 s → max-projection composite | 15 min / 2 min |
| **aurora** | region: any point of `event.region` (or circle) inside annular sector, h = 110–250 | 600 km | `night_ok`; sun_elev < -12; `health==live` | `all_sky` +0.5; hfov ≥ 180 +0.2; moon-phase penalty −0.2·illum | night_ok_required | jpeg (long exposure) > hls, 1 frame | 30 min / 10 min |

Notes that fall out of the table:

- `find_cameras` stays one function: bbox → haversine → geometry test chosen by
  `profile.geometry` → `require` predicates → `astral` gate by `night_policy` →
  score = preprocessing §5 formula + `prefer` bonuses → top `3k`. All
  vectorised over the frame; the per-type branching is data, not code.
- The weather side's `evidence` dict is the only place type-specific numbers
  come from (fog top, anvil top, ridge bearing, Kp). Every field has a default
  so a bare `{type, lat, lon, radius_km}` still works.
- Point events (rainbow, fog) use `radius_km` as R_cap; areal events (aurora,
  storm) use the type's R_cap and ignore `radius_km` unless it is larger.
- Replay (`t_end` in the past): same profiles; `require` gains
  `history_kind != 'none' and history_depth_days ≥ age`; media becomes
  `history_template` only (no HLS).

## 3. Fetch + cheap gates (before any VLM)

Fetch top `3k` (≤ 9 for k = 3) concurrently with `httpx.AsyncClient`, 5 s
per-request timeout, per-host semaphore from the ingest layer. Order of media
per camera follows `profile.media_pref`; the first that yields a frame wins.

| source_kind | how we get a frame | ~cost |
|---|---|---|
| `jpeg` | `GET image_url` (cache-bust `?t=`), conditional `If-None-Match`/`If-Modified-Since` when the host supports it | 0.2–2 s |
| `hls` | `GET playlist.m3u8` → newest segment → `ffmpeg -i <seg> -frames:v 1 -q:v 3 out.jpg` (or `ffmpeg -i <m3u8> -t 15 -vf fps=1/3` for the lightning burst) | 2–5 s (burst 15–20 s) |
| `embed` (YouTube) | `yt-dlp -g` → HLS URL → as above; cache the resolved URL for 1 h | +1–3 s first time |
| `page` / iframe-only (Panomax page, Roundshot) | **no VLM**: display via iframe, verify only via the source's own recent-image JSON timestamp; mark `verified=false` in the envelope | n/a |

Gates, in order, each rejects before the next runs (record which gate fired in
`camera_samples.vlm_label` as `rejected:<gate>` so the health job learns):

1. **Bytes.** status 200, `content-type` startswith `image/` (redirect-to-HTML
   is the #1 failure), size ≥ 3 KB, JPEG magic `FF D8 … FF D9` present
   (truncated uploads are common on DOT cams), `Pillow.Image.verify()`.
2. **Placeholder.** sha1 ∈ per-source placeholder set ("camera unavailable"
   cards). Build the set during preprocessing; add to it whenever the VLM says
   `usable=false, reason="placeholder"`.
3. **Frozen.** sha1 == last sample's sha1 and `now − last_frame_ts > 3·refresh_s`
   → camera is stuck; mark `stale`. Use pHash (`imagehash`) distance ≤ 2 as
   "effectively identical" so re-encoded but identical frames still count.
4. **Timestamp.** Prefer source JSON timestamp → EXIF `DateTimeOriginal` →
   `Last-Modified` header → fetch time. Reject if older than `2·refresh_s`
   (live) — this is the same rule as `health`, re-evaluated on the actual
   bytes. Burned-in timestamps (FAA, IEM) are *not* OCR'd; the VLM prompt asks
   for it as a free field instead.
5. **Pixels** (numpy on a 256-px downscale, < 5 ms): mean luminance of the
   *sky crop* (top `sky_frac`-ish fraction; full frame for all-sky) — reject
   < 12/255 unless type ∈ {aurora, lightning}; std-dev < 4 → uniform grey/black
   → reject; Laplacian variance < 15 → defocused/rain-on-lens → penalise, not
   reject (fog legitimately looks like this — skip this check for fog/undercast).
6. **Dedupe across cameras.** pHash distance ≤ 4 between two candidates (Windy
   re-listing a Panomax cam) → keep the higher-scored row.

Survivors are re-ranked with `score · (1 + 0.2·sharpness_norm)` and the top
**≤ 6** go to the VLM (fewer if the event is cheap-to-confirm; see §4).
Cache the whole gate verdict per `(camera_id, 10-min bucket)` so ten events in
the same storm don't refetch the same frame.

## 4. VLM gate

### What we ask

One call per frame, structured output (OpenAI Structured Outputs with a
pydantic schema, or `response_format=json_schema` equivalent elsewhere):

```python
class Verdict(BaseModel):
    usable: bool                     # not black/placeholder/HTML/obstructed lens
    sky_visible: float               # 0..1
    night: bool
    event_visible: Literal["yes","partial","no","unsure"]
    event_type_seen: Literal[<the 10 types>, "none", "other"]   # asked open, compared to target
    confidence: float                # 0..1
    quality: int                     # 1..5 aesthetic/legibility, used only for ordering
    caption: str                     # ≤ 12 words, shown under the frame
    burned_in_time: str | None       # if a timestamp is printed on the frame
```

Prompt is one fixed system message + the target type + a one-line definition
of that type ("mammatus: pouch-like lobes hanging from the underside of a
cloud base") + the image at `detail: "low"`. Asking the model to name what it
*does* see (`event_type_seen`) rather than a yes/no on the target cuts
sycophantic false positives noticeably in our experience and costs no extra
tokens.

For **lightning** we do not send video. We send the max-projection composite of
the 3–5 burst frames (a flash anywhere in 15 s survives the max) plus the
median frame; question is "is there a lightning channel or flash-lit cloud in
the first image that is absent in the second?". For **sunrise/sunset** we add
`sun_az` and `cam.azimuth` to the prompt so the model can say "sun is out of
frame to the left" (→ `partial`).

### Candidate models (as of the plan date; re-check pricing on the day)

| model | why / why not |
|---|---|
| **OpenAI `gpt-4o-mini` / current "mini" vision tier** | Default. Structured outputs, `detail:"low"` costs a fixed small token budget per image (~85 tokens on 4o-family), ~1–3 s latency, sponsor credits. Good at "is there a storm/fog/sunset"; weak at *mammatus vs. ordinary cumulus base* and *lenticular vs. stratus*. |
| OpenAI full-size vision model | Escalation only: when mini says `unsure` or type ∈ {mammatus, lenticular, aurora} and confidence < 0.6. ≈10× cost; we budget ≤ 1 escalation per event. |
| Google Gemini Flash | Comparable price/quality, accepts real video (would let us send the lightning burst as a clip). Only worth it if OpenAI credits run out; don't run two vendors in the demo. |
| Anthropic Claude (Haiku/Sonnet) | Fine quality, no sponsor tie-in here; skip. |
| Open-weights (Qwen2.5-VL-7B, Moondream 2, SmolVLM) via vLLM/Ollama | No API cost, but needs a GPU box; 7B-class models are unreliable on the rare cloud types. Keep as a fallback demo story, not the path. |
| **`open_clip` zero-shot (ViT-B/32, CPU)** | Not a replacement — a **pre-gate**: 10 ms/frame, prompts "a photo of a black screen / a webcam offline card / a road at night / a sky with clouds / sunset". Drops the obviously-useless 40–60 % of frames before any paid call. This is the Token Company story: log `frames_fetched`, `frames_to_vlm`, `tokens_in`. |

Decision: **OpenAI mini-tier with structured outputs, `detail:"low"`, CLIP
pre-gate, escalate to the large model at most once per event.** Per event that
is ≤ 6 small calls ≈ 1–2 k input tokens total; at 10 events / 10 min that is
< 1 M tokens/day even without caching.

### "Pick the highest quality from the start" — verdict

Do **not** build a separate quality-selection stage now. Reasons:

- The `quality` integer in the same `Verdict` is free; ordering by
  `confidence·(0.6 + 0.1·quality)` gives 90 % of the benefit.
- The cheap signals that predict quality (sharpness, sky_frac, source prior,
  resolution from `Content-Length`) are already in the score before the VLM.
- A real "prettiest frame" pass needs several frames per camera and a
  comparative prompt — that's 3–5× the tokens and only matters for
  sunrise/sunset, which we deprioritise anyway (§7).

Add it later as a **post-hoc** job on `camera_samples` (rank the frames we
already paid to label) that feeds `quality_score` on the camera row, so the
pre-VLM ranking gets better over time without more live calls.

## 5. Routing and the frontend format

### One universal envelope, three renderers

Case-by-case handling on the frontend is a trap: every source becomes a
special component. Instead the backend normalises into a single `Footage`
object and the frontend has exactly three renderers keyed on `media.kind`.

```jsonc
{
  "event_id": "evt_20260920_denver_mammatus",
  "camera_id": "iem:KCCI-014",
  "rank": 1,
  "verified": true,                       // false for iframe-only sources we could not VLM-check
  "media": {
    "kind": "image",                      // "image" | "hls" | "iframe"
    "src": "/proxy/frame/iem:KCCI-014?t=1758348000",   // always our proxy unless embed_allowed && CORS ok
    "poster": "/proxy/frame/iem:KCCI-014?t=1758348000", // for hls/iframe: last verified still
    "refresh_s": 60,                      // frontend re-requests src with new ?t every refresh_s (image only)
    "expires_at": "2026-09-20T05:22:00Z", // Windy signed URLs / our hold window
    "width": 1280, "height": 720
  },
  "verdict": { "event_visible": "yes", "confidence": 0.82, "quality": 4,
               "caption": "Mammatus lobes under a decaying anvil, looking SW" },
  "why": "18 km NE of the storm core, camera faces 225°, cloud base at ~35° elevation",
  "camera": { "name": "Des Moines KCCI tower", "lat": 41.6, "lon": -93.6,
              "source": "iem", "page_url": "https://…", "attribution": "Iowa Environmental Mesonet",
              "license": "public" },
  "frame_ts": "2026-09-20T05:11:30Z",
  "hold_until": "2026-09-20T05:31:30Z"
}
```

- `image` → `<img src>` re-requested every `refresh_s` (the proxy adds
  `Cache-Control: max-age=refresh_s`). This is the path for ≥ 90 % of the
  catalog and the *only* path that gets a VLM `verified: true` cheaply.
- `hls` → `hls.js` (`<video>` on Safari). Always via proxy (DOT hosts have no
  CORS). Poster = the frame we verified, so the screen is never black while the
  player buffers.
- `iframe` → YouTube embed / Panomax / Roundshot player. `verified` is
  `false` unless we screenshot it with Playwright (5–8 s, only do it for the
  hero slot). ToS-required for Panomax/Windy display.

Bytes policy is enforced at the proxy, not in the frontend: `.gov`/CC-BY
sources are cached; ALERTCalifornia (BY-NC-ND) and Windy are passed through
with `no-store`, and Windy URLs are re-resolved when `expires_at` is near.

### Delivery

- Orchestrator writes `FootageResult` to a small store (SQLite via
  `aiosqlite`, or Redis if the frontend team already runs one) keyed by
  `event_id`; `GET /events/{id}/footage` returns the current list; `GET
  /feed` returns the top-N pairs across events for the homepage; `GET
  /stream` is Server-Sent Events pushing `{event_id, status}` whenever a result
  changes. SSE over WebSockets: one direction, works through every proxy,
  reconnects for free with `EventSource`.
- **Hold and re-verify.** A verified pair stays visible for `profile.hold_s`
  (10–30 min; the doc's "hardcode 10 min" is the default). Every
  `reverify_s` the cheap gates (§3, no VLM) re-run on the new frame; if the
  frame goes black/frozen/stale the pair is demoted and the weather backend
  gets `ALL_STALE` so it can re-query. The VLM re-runs only when the
  weather backend re-emits the event (~10 min), i.e. never more than once
  per (camera, event, 10-min bucket).
- Frontend never talks to cameras directly; it only reads the store and the
  proxy, so replay mode and live mode are indistinguishable to it.

### Failure return to the weather backend

`resolve_footage` always returns; the orchestrator POSTs the `FootageResult`
(or calls back into the weather module) whether it is `FOOTAGE_FOUND` or not.
`NO_FOOTAGE_FOUND` is reserved for "cameras existed, frames were fresh, VLM
said no" — the interesting negative that Voloridge/backtest wants logged. The
other statuses distinguish infrastructure failures so we don't pollute the
labelled negatives.

## 6. Time-to-first-frame estimate

Assumes `find_cameras` is the ~ms vectorised pandas path and the orchestrator
runs next to the store. Numbers are per event, wall clock, with everything
parallel that can be.

| stage | p50 | p95 | notes |
|---|---|---|---|
| `find_cameras` | 10 ms | 50 ms | bbox + haversine + astral on ≤ 90k rows |
| fetch 9 frames concurrently (JPEG) | 1.2 s | 4 s | slowest host dominates; 5 s timeout caps it |
| + HLS frame grab (if any candidate is `hls`) | +2.5 s | +5 s | ffmpeg on one segment |
| + YouTube resolve (first time only) | +2 s | +4 s | `yt-dlp -g`, cached 1 h |
| cheap gates + CLIP pre-gate | 60 ms | 200 ms | CPU |
| VLM, ≤ 6 calls concurrent | 2 s | 5 s | mini tier, `detail:low` |
| escalation call (≤ 1) | +3 s | +8 s | only on `unsure` |
| write store + SSE push | 20 ms | 100 ms | |
| **backend total, JPEG-only event** | **≈ 3.5 s** | **≈ 10 s** | |
| **backend total, with HLS/YouTube candidates** | ≈ 6–8 s | ≈ 18 s | |
| frontend: `<img>` render | 0.3 s | 1 s | via proxy, already cached |
| frontend: hls.js first frame | 2–4 s | 8 s | poster shows instantly |
| frontend: YouTube iframe | 3–6 s | 10 s | |

So: **~4 s from event arrival to a verified still on screen for the common
case; ~10–15 s when the winner is a live video stream; 30 s is the hard
deadline** after which `resolve_footage` returns `TIMEOUT` with whatever
partial evidence it has (a still that passed cheap gates but not the VLM is
still returned as `verified: false` so the screen isn't empty). The
lightning burst adds a deliberate 15–20 s and is the one type that cannot
hit the 4 s figure.

Where the time actually goes is DNS + TLS to a dozen different hobby/DOT
hosts, not compute — keep one `httpx.AsyncClient` alive across events
(connection pooling) and pre-warm the top 50 healthiest hosts at startup.

## 7. Edge cases and prioritisation by feasibility

Ordered from "will work at the demo" to "needs an extra layer". Build and
polish in this order; the UI can show all nine chips, but the hero slots
should be drawn from tiers A–B.

**Tier A — static, daytime, big targets: thunderstorm, fog, undercast, lenticular.**
A single fresh JPEG is enough; the VLM is reliable on "storm cloud / fog /
looking down on a cloud deck"; the annulus geometry gives many candidates
(storm: 150 km radius). Undercast is the demo's best visual and is common
every autumn morning in the Alps and coastal California. Edge cases: fog
frames look like camera failure (skip the sharpness/std-dev gates); DOT
cams show storm *rain* not storm *cloud* when inside the cell — the annulus
already excludes d < d_min, but if the weather side sends the cell centre
with a large `radius_km` the geometry needs the cell **polygon** to work
well, so ask for `region` on convective events.

**Tier B — static but needs the right camera: mammatus, aurora.**
Mammatus is rare, short (30–90 min), and needs near-overhead views (IEM,
all-sky, foto-webcam looking up a valley); expect few candidates and a
noisier VLM (lobes vs. ordinary ragged cumulus). Aurora: essentially only
`night_ok`/all-sky cams work (UAF, IRF Kiruna, Digitraffic Lapland,
Iceland), so the candidate set is tiny but very high-yield when Kp is high;
the VLM is good at aurora. Edge cases: moonlit clouds and city glow read as
aurora to a weak model → escalate on confidence < 0.7; camera clocks on
hobby all-sky cams drift; midnight sun kills the entire northern set from
May to August (handled by the astral gate, but the UI should say "aurora
season only").

**Tier C — motion or timing dependent: lightning, rainbow.**
Lightning needs `refresh_s ≤ 60` or HLS plus the burst/max-projection trick;
a single JPEG catches a flash by luck. Only ~10 % of the catalog qualifies
(DOT HLS, ALERTCalifornia 2-min sweeps, YouTube streams). Rainbow is
geometrically strict (antisolar, sun_elev < 42°), lasts minutes, needs rain
on one side and sun on the other — the candidate radius is 5 km, so the
answer is usually `NO_CAMERAS_IN_RANGE`; treat it as a delightful accident
rather than a promise.

**Tier D — needs an extra processing layer: sunrise, sunset.**
The user's suspicion is correct. Everything else asks "is X present?";
sunsets ask "is this a *good* one?", which is (a) subjective, (b) time-boxed
to a ~30 min window that moves west at 15°/hour, and (c) totally dependent
on heading — a camera 20° off the sun azimuth shows a grey sky next to a
spectacular one. Required extras: (1) `heading_conf ∈ {catalog,text}` as a
hard filter, dropping most DOT/PTZ cams; (2) the weather side's quality
prediction (mid/high cloud + clear path toward the sun) as `severity`,
otherwise we'd broadcast every ordinary sunset and the feed becomes
uninteresting; (3) re-fetch every 2–3 min because the best colour is often
after the sun is down (sun_elev −2 … −6°) and the first frame in the window
is rarely the best; (4) the quality/aesthetic ranking that §4 deliberately
defers. It happens every day, so rarity is low unless the weather side
scores it. Recommendation: implement sunset/sunrise last, gated on
`severity ≥ 0.7`, and only from the hand-curated Panomax/foto-webcam/NDBC set
where heading is exact. Sunrise is the same as sunset with the added problem
that few people are watching; treat it as sunset's replay fixture.

Cross-cutting edge cases:

- **PTZ cameras** (ALERTCalifornia, many DOT): stored heading is meaningless;
  they pass geometry with `×0.6` and the VLM decides. For sunset they are
  excluded; for storms they are often the *best* candidates because operators
  point them at weather.
- **Clock skew**: some sources' timestamps are local-without-tz or simply
  wrong; when the bytes are fresh (sha1 changed vs. last sample) but the
  timestamp says stale, trust the bytes and flag `ts_source='inferred'`.
- **Event straddles day/night** (a storm at dusk): the astral gate uses
  `t_mid`; if `t_start`..`t_end` crosses −6°, run the gate at both ends and
  union the candidates.
- **Multiple events, same camera**: the same frame may verify a thunderstorm
  and a mammatus event; the verdict cache is keyed by frame, and
  `event_type_seen` lets one call answer both.
- **Source outage** (whole catalog host down): the health job marks rows
  stale within 10 min; `resolve_footage` returns `ALL_STALE` rather than
  timing out nine times.
- **Rights**: `Footage.media.src` for ALERTCalifornia and Windy is pass-through
  with `no-store`; Panomax is iframe-only for display, JPEG for the VLM only.

## 8. Libraries and tooling

Already chosen upstream and reused as-is: `uv`, `ruff`, `ty`, `httpx`,
`pydantic`, `pandas`/Parquet, `astral`, `pillow`, `numpy`, `ffmpeg`,
`yt-dlp`, `playwright` (screenshots of iframe-only hero cams only).

Added for this half:

- `imagehash` (pHash) for frozen/duplicate detection — tolerant to JPEG
  re-encoding where sha1 is not.
- `opencv-python-headless` only for `cv2.Laplacian` sharpness and the
  lightning max-projection; if we want to avoid the 60 MB wheel, both are
  three lines of numpy.
- `open_clip_torch` + CPU torch for the zero-shot pre-gate. Optional; ship
  behind a flag so the demo box without torch still works (it just pays for
  more VLM calls).
- `openai` SDK with `client.beta.chat.completions.parse(..., response_format=Verdict)`
  for structured outputs; `tenacity` for retries with jitter.
- `fastapi` + `sse-starlette` for `/feed`, `/events/{id}/footage`, `/stream`,
  and the `/proxy/frame/{camera_id}` and `/proxy/hls/{camera_id}/…` routes
  (`StreamingResponse`, rewriting segment URIs in the playlist).
- Store: `aiosqlite` (one file, survives restarts, trivially inspectable at
  3 a.m.). Redis only if the frontend already needs it.
- Frontend side (informational, for the other team): `hls.js`, plain `<img>`
  with `?t=` cache-bust, standard YouTube iframe with `autoplay=1&mute=1`.

Explicitly not used: Elasticsearch for this path (the preprocessing plan's
verdict stands; the events team may use it for their streams), a message
queue (one asyncio process with a scheduler is enough for the hackathon),
video-native VLM calls (too slow/expensive; the burst composite is the
cheaper equivalent), and OCR of burned-in timestamps (the VLM returns it as
a free field when present).

## 9. Build order

1. `EventProfile` table + `resolve_footage()` skeleton against the 10-row
   fixture, returning `FootageResult` with the cheap gates only
   (`verified: false`) → frontend can render `image` envelopes in hour 1.
2. Proxy routes + store + SSE; frontend switches from fixture JSON to `/feed`.
3. VLM gate with structured outputs + verdict cache + token counters.
4. HLS/YouTube frame grab + `hls` renderer; lightning burst.
5. CLIP pre-gate, escalation rule, replay fixtures for the four demo events
   (Front Range mammatus, Lapland aurora, Alpine undercast, Miami storm).
6. Sunset/sunrise profile last, restricted to the curated exact-heading set.
