# Camera match & filter — design record

Living document. Records *why* the matching / filtering pipeline is shaped the way it is, what is
implemented today, the staged plan for "is this frame worth showing", and the sources we lean on.
Everything under **Sources** was checked to exist (DOI / publisher page) at the time of writing;
accuracy numbers quoted are the papers' own, on *their* datasets — not ours.

Companion docs: `query-and-routing-plan.md` (original plan), `../../docs/diagrams/query-routing-schema.svg`,
`README.md` (ops + API).

---

## 1. Decision layers today (what is implemented)

```
weather event ─► ① catalog match (query.py)      ms       geometry + priors → score, top-N
              ─► ② fetch + gates (gates.py)      1–3 s    bytes / timestamp / pixel stats
              ─► ③ VLM yes-no (vlm.py)           ~1.5 s   "is <type> visible?" + type seen
              ─► ④ route (resolve.py)            —        rank → Footage / status
```

### ① Catalog match — `Catalog.find_cameras()`

Per-type `EventProfile` (`profiles.py`) drives one generic query:

| step | rule | why |
|---|---|---|
| annular sector | `d_min ≤ dist ≤ d_max` around the event; per type (storm 20–150 km, rainbow ≤ 15 km, aurora up to 300 km …) | phenomena have very different visual ranges: a cumulonimbus top is visible from 100+ km, a rainbow only from where the geometry works |
| viewing cone | bearing to event within `azimuth ± hfov/2 (+ slack by heading_conf)` unless `all_sky`/`ptz` | a camera pointing away sees nothing; slack absorbs unknown headings instead of dropping cameras |
| elevation | event altitude band vs camera `elev_min/max`; undercast requires camera *above* cloud top | undercast is the only "look down" type |
| night capability | sun elevation (astral) vs `night_ok`; night-only types (aurora) need dark; daytime types skip night cameras | avoids paying fetch/VLM cost for black frames |
| health / freshness | skip `dead`, skip `last_frame_ts` older than `DEAD_AFTER` (24 h) | the health probe (health.py) is the source of truth; unprobed-but-stale is treated as dead |
| score | distance-fit × cone-centredness × heading_conf × sky_frac × quality_score × source prior | one scalar so later stages can cut top-N deterministically |

Design choice: **one engine, one table** rather than per-type code paths. Adding a type = adding a
profile row. The score is *relative* (ranking) — never interpreted as a probability.

Match runs in the reanalysis cron (`match.py`), not at query time: `GET /events` is a pure read of
`event_cameras` / `event_footage` (see README, PR #26).

### ② Gates — `gates.check_frame()`

Deterministic, ~1 ms/frame (PIL + numpy on a 256×256 grey copy). Order is cheapest-first, every
reject carries a human-readable reason that is logged and shown on the sandbox page.

| check | rule | source / rationale |
|---|---|---|
| bytes | ≥ `MIN_BYTES`, JPEG/PNG magic, decodable, ≥ 160×120 | catches HTML error pages, truncated CDN responses |
| placeholder | sha1 in `PLACEHOLDER_SHA1`; health probe also flags sha1s shared by several cameras | FAA "camera unavailable" cards |
| frozen | identical sha1 to previous fetch → reject; pHash Hamming ≤ 2 → note | stuck encoders show a valid but stale picture |
| timestamp | API ts → EXIF → fetch time (flagged); reject if age > 2×cadence or ≤ last ts | FAA gives `imageDatetime`; freshness before any ML |
| dark | mean luminance < 12 → night; reject unless profile allows night frames | mean grey is a crude but monotonic exposure proxy |
| uniform | std < 4 → colour card / fog-on-lens / white-out | |
| blown-out | mean > 240 | from ken/data-validation PoC |
| soft | Laplacian variance < 15 → ×0.7 score, not a reject | Laplacian variance is the standard focus measure (Pertuz 2013) but threshold is scene-dependent |

Design choice: gates *reject* only on things we are sure about (bytes, freshness, extreme exposure)
and *down-weight* on things that are scene-dependent (sharpness). This keeps recall high and lets
the VLM / later scorers do the fine judgement.

### ③ VLM — `vlm.judge()`

One structured call per gate-passed frame (top 2k), parallel, `detail: low`, ~3.3k prompt tokens,
≈ $0.0005/frame on gpt-4o-mini. Output: `usable, sky_visible, night, event_visible
(yes|partial|no|unsure), event_type_seen, confidence, quality (1–5), caption, burned_in_time`.

Design choices:
- **yes/no on the type, not open captioning** — VLMs are reliable at coarse recognition and at
  reading burned-in text; we saw ~0.8–0.9 confidence "no, night scene" on every night frame with
  correct timestamp OCR. They are *not* reliable on numeric self-ratings (quality 3 vs 5 for equally
  black frames) or on fine cloud taxonomy (see §3).
- **provider-agnostic** — OpenAI chat format; Groq / Ollama / any compatible server via env vars.
- **degrade, don't fail** — if every call times out we serve gate-passed frames as `verified:false`.

### ④ Route — `resolve.py`

Passing = `usable ∧ event_visible ∈ {yes, partial} ∧ event_type_seen == type ∧ confidence ≥
profile.min_conf`. Rank by `confidence × (0.6 + 0.1·quality)`; top-k become `Footage`; statuses
`FOOTAGE_FOUND | EVENT_NOT_VISIBLE | ALL_STALE | CAMERAS_DARK | NO_FOOTAGE_FOUND | TIMEOUT`.

Known weakness (this doc's reason to exist): `quality` is the VLM's gut feel with no rubric.

---

## 2. Staged plan — "is this frame worth showing?"

Constraints agreed with the team:
- latency budget ≈ unchanged (≤ ~5 s to first frame, VLM calls already parallel);
- dependency weight matters (today: numpy, pandas, pyarrow, PIL, httpx, fastapi, astral, openai;
  no torch) — a possible later iOS port means anything we add should have a Core ML / ONNX path
  or be trivially re-implementable;
- prefer deterministic, explainable, fast metrics; add learned models only where the deterministic
  ones are demonstrably insufficient; avoid over-engineering.

### Stage A — deterministic quality score (no new deps, ~1–2 ms/frame)

Replace VLM `quality` with a computed `Q ∈ [0,1]` from features already cheap on the 256² grey +
a 128² colour copy:

| feature | computation | used for | trust |
|---|---|---|---|
| sky share | fraction of upper-half pixels with HSV S<0.25 & V>0.5 (grey/white) or blue hue band; cf. TSI red/blue-ratio sky-cloud separation (ARM TSI handbook) | all types; also a "no sky at all" reject | medium — fails on snow, water, white walls; good enough to *rank*, not to *reject* below ~0.1 |
| colourfulness | Hasler–Süsstrunk metric `M = σ_rgyb + 0.3·μ_rgyb` | sunrise/sunset/rainbow/aurora prominence | high as a metric (correlates ρ≈0.95 with human colourfulness ratings in the paper); *relevance* to "pretty sunset" is an assumption we should calibrate |
| warm-hue mass | fraction of sky pixels with hue in 0–40° & S>0.35 | sunrise/sunset ("a plain bright sky does not count") | medium |
| sharpness | Laplacian variance (have) | all | medium — scene-dependent; Pertuz et al. 2013 rank Laplacian-based operators among the best simple focus measures, but absolute thresholds don't transfer across cameras → normalise per camera using the health log |
| exposure | mean / clipped-pixel fraction (have) | all | high for extremes only |
| sky texture | Laplacian variance restricted to sky mask; Haralick-style contrast on sky | thunderstorm/mammatus/lenticular prominence (Heinle et al. 2010 use exactly such spectral + textural features to classify sky images at ~97 % on 7 classes, on *whole-sky* imagers) | medium |
| obstruction | fraction of frame = near-black or straight-edge blobs in the sky mask | masts, roofs | low — heuristic; keep as a small penalty |
| haze | dark-channel prior mean (He et al. 2009) — min over RGB in local patches, high ⇒ haze/fog/white-out | reject "milky" frames for all types except undercast (where a bright uniform lower band is the *signal*) | medium; cheap (one min-filter) |

`Q = w·features` with per-type weights in `EventProfile`; log every feature to `verdicts.jsonl`.
Also add per-type hard rules: `night:true` for daytime types → reject even under `--ignore-night`;
lightning/rainbow require `event_visible == yes` (not partial) and age < 1.5×cadence.

Cost: zero new dependencies; iOS-portable (Accelerate/vImage). Expected effect: consistent
tie-breaking among VLM-passed frames; ~10–20 % fewer VLM calls from the no-sky / haze rejects.

### Stage B — embedding pre-rank (one new dep, ~50–150 ms/frame CPU)

CLIP-family image embeddings (Radford et al. 2021) scored against per-type text prompts, both
positive ("towering dark storm cloud with rain shaft") and negative ("grey overcast", "night",
"rain drops on the lens", "camera pointed at a building"). Use as:
1. **pre-rank** — VLM sees only the top 1–2 per event ⇒ 3–5× fewer VLM calls, latency down;
2. **cheap prominence** — cosine margin positive–negative as an extra `Q` term.

Dependency choice: ship an **ONNX** export of a small model (e.g. MobileCLIP-S0, Apple 2024, ~11 M
image params, Core ML weights published — the same weights would serve an iOS port) run with
`onnxruntime` (~30 MB wheel). Avoid torch in the service. Zero-shot accuracy on our specific
classes is unknown; **B is gated on Stage A + a day of daylight `verdicts.jsonl` to measure
agreement with the VLM before it is allowed to reject anything.** Initially rank-only.

### Stage C — per-type learned heads (no new deps beyond B)

Logistic regression / small MLP on the CLIP embedding per event type, trained from our own
VLM-labelled log (teacher–student). Trains in seconds, ~0 latency. Purpose: (i) turn Stage B into a
calibrated probability, (ii) for easy types (thunderstorm, undercast, night) drop the VLM call
entirely when the head is confident. Public datasets to pre-train / sanity-check:

| type | dataset | notes |
|---|---|---|
| cloud genera (Cb, Cu, St, Ci …, incl. some mammatus/lenticular-adjacent) | CCSN (Zhang et al. 2018, 2 543 images, 11 classes, CloudNet ~88 %); SWIMCAT (Dev et al. 2015, whole-sky patches); TJNU GCD (Liu et al. 2020, 19 000 images, 7 classes); DeepSky (2023, all-sky) | all are *sky-imager / hand-held* pictures, not webcam scenes — domain shift is real; treat as pre-training only |
| aurora | OATH (Clausen & Nickisch 2018, 5 824 all-sky images, 6 classes, ~82 % with pretrained features + ridge classifier); Kvammen et al. 2020 (7 subclasses, ResNet-50 92 % average precision on clean, cloud-free night images) | all-sky, not horizon webcams; but "is there aurora at all" transfers better than fine morphology |
| fog / low visibility | RMI webcam fog detection (Belgian met office, ML on webcam images); Meteorological Visibility on Webcam Images (IJCTE 2017); VisNet (Palvanov & Cho 2019) | webcam domain — directly relevant to haze/white-out rejection and to undercast vs fog |
| generic outdoor webcam attributes | Transient Attributes (Laffont et al. 2014: 40 attributes incl. sunny/cloudy/fog/storm/sunrise-sunset, 8 571 webcam frames from AMOS); AMOS (Jacobs et al. 2007, millions of webcam frames) | closest domain to ours; the attribute regressors are exactly "how much sunset is in this frame" |
| rainbow | Workman, Mihail & Jacobs 2014 ("A Pot of Gold") — rainbow *detection* in consumer + webcam images, released dataset | the only rainbow-specific vision paper we found; also gives sun-position geometry we can cross-check |
| lightning | Vision-based lightning in surveillance video (2016, frame-difference + brightness); LD-Net (2024, distilled detector); few-shot frame-difference + triplet net (2026) | video-frame methods; for stills we can only do brightness-flash on consecutive FAA frames (10-min cadence → rarely catches a bolt) |

### Stage D — best-of-N pick (optional; +1.5–3 s; skip unless A–C prove insufficient)

Tile the finalists, one VLM call, "which would you put on a weather site". Only worth it if the
computed `Q` still disagrees with humans; adds real latency, so default off.

### Stage E — camera-level learning (background, no latency)

`verdicts.jsonl` + `health_log.parquet` → per-camera `quality_score`, `night_usable_frac`,
sharpness baseline, typical sky share. Feeds back into ① so bad cameras stop being fetched at all.
Already sketched in `health.py`; needs a nightly job.

### What we deliberately do **not** plan

- NIMA / MUSIQ / BRISQUE-style aesthetic or no-reference IQA models: BRISQUE (Mittal et al.
  2012) targets compression / noise distortions on natural photos, NIMA (Talebi & Milanfar 2018)
  and MUSIQ (Ke et al. 2021) target AVA-style aesthetics. Webcam frames are all "low quality" by
  those standards; the differences we care about (is the storm dramatic) are not what they
  measure, and they add torch-scale dependencies.
- Full sky segmentation networks (SkyFinder-trained U-Nets etc.) — heuristic sky share plus the
  catalog's `sky_frac` is sufficient for *ranking*; a fixed camera's sky region barely changes, so
  it can be estimated once per camera offline (Stage E) instead of per frame.
- Training our own cloud-genus CNN — dataset domain shift (sky imagers vs webcams) and the ambiguous
  taxonomy (mammatus/lenticular are sub-features, not genera) make this expensive for little gain
  over CLIP + VLM.

---

## 3. How accurate are the CV metrics — should we trust them?

Short answer: trust them as **rankers and as rejecters of extremes**, not as detectors.

| metric | what it measures well | where it lies |
|---|---|---|
| mean / std luminance | night, white-out, colour cards | dusk with city lights; snow scenes look "blown out"; fog looks "uniform" (fine, we don't want fog anyway) |
| Laplacian variance | relative sharpness for the *same* camera over time | across cameras it mostly measures scene texture; a sharp picture of a flat overcast scores low → per-camera normalisation (Stage E) |
| pHash | frozen / near-duplicate frames | small pans on PTZ cameras defeat it (fine, PTZ is already handled in ①) |
| Hasler–Süsstrunk colourfulness | perceptual colourfulness (paper reports strong correlation with human judgements) | "colourful" ≠ "sunset": autumn trees, red roofs, an FAA overlay. Restrict to the sky mask |
| HSV sky mask | blue/grey sky in daylight | snow, water, white buildings, night sky; overcast vs white wall indistinguishable |
| dark-channel haze | haze / fog / milky lens | bright uniform undercast top *is* a high dark-channel value → per-type exception |
| frame-difference flash | lightning in video | useless at 10-min stills; our lightning path stays VLM-first |

Rule of thumb adopted: a deterministic metric may **reject** only when its failure modes cannot
produce a good frame (bytes, freshness, mean<12 for a daytime type, std<4), may **down-weight**
otherwise, and every reject writes its reason so we can audit it in the sandbox log. "The more the
better" holds for ranking features (they are ~free); it does not hold for reject thresholds, where
each one costs recall.

---

## 4. Latency & dependency budget per stage

| stage | added latency / event | added deps | iOS path |
|---|---|---|---|
| A | ~2 ms | none | trivial (vImage / Accelerate) |
| B | 50–150 ms (CPU, 2–10 frames) — and *removes* 2–4 VLM calls | onnxruntime (~30 MB) + ~40 MB weights | Core ML MobileCLIP weights published by Apple |
| C | ~0 | none extra | tiny weights |
| D | +1.5–3 s | none | server-side only |
| E | 0 (offline) | none | n/a |

Current end-to-end (measured tonight, US night, 9 candidates): fetch+gates 1–2 s, 6 parallel
gpt-4o-mini calls, total 3.3 s.

### 4.1 Gate-only vs gate+VLM — daylight run, 2026-09-20 ~09 UTC (12.5k-camera catalog)

Same events, same catalog, same frame cache, `k=5`, `SUNROOF_VLM_BACKEND=off` then `openai`
(gpt-4o-mini, `detail:low`). Content heuristics were advisory only (this PR).

| set | events | with cameras in range | gate-only status | VLM status | VLM calls | rejects by gates | median Δt |
|---|---|---|---|---|---|---|---|
| 10 synthetic (Taipei, HK, Alps, Nordics, NZ…) | 10 | 7 (3 `CAMERAS_DARK`, local dusk) | 7 × `FOOTAGE_FOUND` (5 frames each) | 7 × `EVENT_NOT_VISIBLE` | 70 | 3 (all `stale`) | 1.3 s → 2.7 s |
| real `weather/events.py` output (needs_daylight types) | 25 | 5 (lenticular, CZ/SK/AT) | 5 × `FOOTAGE_FOUND` | 5 × `EVENT_NOT_VISIBLE` | 41 | 2 (both `stale`) | 0.4 s → 1.7 s |

All 92 verdicts were `event_visible: no`, confidence 0.8–0.95; spot-checking the contact sheets
agrees (ordinary blue / partly-cloudy sky, no lens-shaped or storm cloud). Q sat at 0.86–0.92 for
every served frame — it separated nothing.

What this says:

- **The VLM's gain is precision, not recall.** Gate-only shows 5 pleasant-but-irrelevant sky
  frames per event ("not stunning"); the VLM turns them into an honest `EVENT_NOT_VISIBLE`. It never
  had a real event to confirm in this run, so the recall side is still unmeasured.
- **Not one frame was lost to a content heuristic**: the only gate rejects were stale timestamps.
  So the "images aren't stunning" problem is not the CV layer being too strict; it is upstream:
  20/25 real events had **no camera in range** (SK/PL mountains, Turkey, Malaysia, W. Africa), and
  the 5 that did are SYNOP station reports with a 50 km radius, whose nearest cameras look at a
  different piece of sky.
- **Panoramas are wasted on `detail:low`**: the panomax `recent_small.jpg` we ingest is 1807×150
  (12:1); after the model's 512 px downscale the sky is ~20 px tall. If lenticular/mammatus recall
  matters, fetch the larger rendition and tile / crop the sky band before the VLM call (Stage
  D-style, one extra call) — a cheaper win than any new CV feature.
- Verdict on "more fast CV filtering?": **no, not as filters.** Evidence for adding Stage B
  (MobileCLIP pre-rank) is only cost: ~10 VLM calls/event ≈ $0.005 — fine at demo scale. Revisit if
  events × cameras grows 10×. Stage A stays as a tie-breaker among *confirmed* frames.

Raw logs: `verdicts.jsonl` rows carry `q`, `features`, `confidence`, `caption` per call.

### 4.2 CV pre-gate vs VLM-only — historical replay, 993 events / 2021–2026 (decides the pipeline)

§4.1 could not measure recall (no positives). This run replays *historical* events through the
real pipeline (`find_cameras` at the event time → archive frame → sanity gates → Q features → VLM
on **every** gate-passing frame), then evaluates each candidate CV pre-gate offline against the
logged verdicts: "calls it would have skipped" vs "frames the VLM said `yes` in that it would have
skipped". Same frames on both sides, so it is an exact shadow comparison.

**Sample.** 993 events (two seeds, ~500 each), 2021–2026, all months (Jun–Aug heaviest, as storms
are), lat 26.5–50.3 / lon −123 to −70, 268 distinct 1° cells. Types: thunderstorm 593 (MRMS
ProbSevere objects), sunset 147 / sunrise 132 (solar crossings at camera sites), aurora 80 (hand-picked Kp≥7 storm
nights). No mammatus / lenticular / undercast / rainbow: no historical *event* source exists for
them, so the cloud-type rule below is transferred from thunderstorm. Frames: PhenoCam archive
(705 judged frames, US-wide, 30-min cadence, `night_ok=False`) and IEM Iowa webcams (137, 5-min
cadence, `night_ok=True`). Panomax/FAA have no usable archive → they are absent here.

**Outcome.** 952 events ran; 967 VLM calls (gpt-4o-mini, `detail:low`, ≈$0.5 total); 842 unique
frame×type judgements. Statuses: `EVENT_NOT_VISIBLE` 550, `CAMERAS_DARK` 280, `FOOTAGE_FOUND` 49,
`NO_FOOTAGE_FOUND` 45, `ALL_STALE` 28. Resolver latency on events that reached the VLM: median
2.3 s, p90 5.0 s. Feature extraction: 4.7 ms/frame. Positives are rare: 50 `yes` + 9 `partial`
frames — sunset 40, sunrise 8, thunderstorm 2 (+7 partial), aurora **0** (Iowa night frames at
Kp≥7 nights showed nothing the VLM accepted; the aurora rows below are therefore not evidence either way).

| rule (skip VLM when …) | type | best zero-`yes`-loss threshold | calls skipped | what the lost frames look like |
|---|---|---|---|---|
| `sky_share < thr` | thunderstorm (538 fr, 2 yes / 9 partial) | 0.20 (0.30 loses 1 partial) | **35 %** | — |
| `sky_share < thr` | sunset (162 fr, 40 yes) | 0.05 | 7 % | at 0.10: 3 real sunsets lost — **sun glare reads as non-sky** |
| `sky_share < thr` | sunrise (72 fr, 8 yes) | 0.10 | 24 % | at 0.20: 2 lost |
| `warm_share < thr` | sunset | **none** — every threshold loses `yes` frames (3 lost at 0.005) | — | hazy pink/white sunsets have almost no "warm" pixels |
| `warm_share < thr` | sunrise | 0.02 | 60 % | 1 lost at 0.05 |
| `colourfulness < thr` | sunset / sunrise | 0.20 / 0.40 | 10 % / 76 % | — |
| `texture < thr` (cloud texture) | thunderstorm | 0.30 | 3 % | useless: PhenoCam sky bands are smooth |
| `Q < thr` | sunset / sunrise / thunderstorm | 0.30 / 0.30 / 0.50 | 19 % / 35 % / 3 % | Q≥0.4 loses 16 of 40 sunsets |
| **rank-then-cut: top-N by Q per event** | all (599 events with frames) | N = 2 | **15 %**, every event with a `yes` keeps one | N = 1 loses 11 of 43 positive events |

Q as a ranker: AUC(yes vs no) 0.92 sunrise, 0.67 sunset, 0.49 thunderstorm — it orders sun
events well and storms not at all (storm `yes` frames had Q 0.73 vs 0.72 for `no`).

**Reading it honestly.** Every *content* threshold that saves a lot of calls on the colour types
loses real events, and the losses are exactly the "ugly but real" frames the policy in §4.1 worried
about (blown-out sun, haze). The one rule that is both safe and worthwhile is *no sky in view → no
cloud event*, for cloud types only (35 % of storm calls, 0 `yes` lost, 0 partial lost at 0.20; we
ship 0.15 for margin). The largest type-agnostic saving comes from not judging every frame: the
pipeline already ranks by gate-adjusted score × Q, and the VLM only needs the top few. The
thunderstorm positive base (2 `yes`) is thin; the rule is kept because its failure mode is
physically implausible, not because n=2 proves it.

**Pipeline shipped (this commit):**

1. **Night skip** — daytime types (everything but aurora / lightning) are not resolved at all when
   the sun is below −6° at the event (sunrise/sunset: −12°, afterglow window) → `CAMERAS_DARK`
   with `note`, no fetch, no VLM. 280/952 replay events were night rows for daytime types.
2. **CV pre-gate, cloud types only** — `EventProfile.min_sky_share = 0.15` for thunderstorm /
   mammatus / lenticular; frames below it are `Rejected(stage="cv")` and never reach the VLM. Off
   for every colour type, aurora, undercast, rainbow (the data says it would cost real events).
3. **Rank-then-cut** — `EventProfile.vlm_top_n = 3` (env `SUNROOF_VLM_TOP_N`; ≤0 restores judging
   2k frames): only the 3 best gate-passing frames per event are judged. Replay: N=2 already kept
   every positive event; N=3 is the margin. With the live 12.5k-camera catalog (~9 candidates/event
   in §4.1) this is a ~3× cut in VLM calls; on the archive replay (1–2 candidates/event) it is 6 %.
4. Everything else stays advisory: `Q` orders, `min_q = 0`, VLM is the content judge.

Not changed by the data: warm-hue / colourfulness / texture / Q thresholds — rejected as gates.
`FootageResult.cv_skipped` counts frames removed by 2+3 so the saving stays measurable in
production logs.

Artifacts (not committed): `bench/report.md`, per-type contact sheets (`sunset_yes.jpg`,
`thunderstorm_partial.jpg`, `lost_sunset_sky_share_0.1.jpg`, …), `results.jsonl`, `verdicts.jsonl`.

### 4.3 Event → camera: the pick is deterministic, and it now learns per camera

The choice among suitable cameras/frames was three ranked stages already (catalog score → gate-
adjusted score → VLM verdict × Q). The replay exposed what geometry cannot see: **which cameras
actually deliver**. Of 40 confirmed sunsets, 33 came from two IEM cameras (`iem:KCCI-034` Big
Creek Marina 21/40 asked, `iem:KCCI-027` ISU Ag Farm 13/19), while `phenocam:arsbrooks10` — same
type, same geometry class — was asked 33 times for 2 hits. Nothing in the catalog row
(`sky_frac`, heading, source prior) separates them.

So the second commit adds a **camera track record** (`track.py`, Stage E made concrete):

* every VLM verdict is folded into a per-(camera, type) Beta posterior of *P(event visible when
  asked)* — `yes` = 1, `partial` = ½ — shrunk towards the type-wide base rate with 4 pseudo-obs.
  It is rebuilt from `verdicts.jsonl` at startup (`Catalog.load(..., verdict_log=)`) and updated
  in memory after every event, so the live server and the cron `match` step learn continuously.
* `Catalog.find_cameras` multiplies the catalog score by a bounded factor
  `clip(1 + 0.35·log(p/p₀)·(1−e^{−n/6}), 0.75, 1.35)` and appends `track x1.23` to `reason`.
  An unknown camera is exactly neutral; nothing is ever *excluded* by history (a dud can still
  win when it is the only camera in range, and one confirmed frame lifts it again).
* the in-event best-frame pick is `pick_score` (resolve.py):
  `confidence × (yes 1.0 | partial 0.7) × (0.5+0.5·Q) × (0.8+0.05·vlm_quality) × (0.9+0.1·catalog) × track`.
  Presence dominates; Q (replay-calibrated) and the VLM's 1-5 decide the look; geometry/freshness
  and track record break ties. This replaces the old `confidence × (0.5+0.5·Q)`.
* `sunroof-camera track [--type]` prints the shortlist of cameras with confirmed sightings.

Night: daytime types are skipped *before* any fetch when the sun is below −6° at the event
(−12° for sunrise/sunset, to keep the afterglow); aurora and lightning are exempt (§4.2, commit 1).

**Demo shortlist from the replay** (what the VLM accepted, ordered by hits then Q; frames in the
`sunset_yes` / `thunderstorm_yes` / `sunrise_yes` contact sheets):

| camera | type | asked → yes | mean Q | note |
|---|---|---|---|---|
| `iem:KCCI-034` Big Creek Marina, Polk City IA (az 268°) | sunset | 40 → 21 | 0.40 | sun setting over the lake, boats in the foreground — the most photogenic and most reliable view in the whole sample; live in the IEM KCCI network today |
| `iem:KCCI-027` ISU Ag Farm, Ames IA (az 282°) | sunset | 19 → 13 | 0.51 | highest hit rate; open horizon, strong colour, lens flare on clear evenings |
| `phenocam:harvardhemlock2` Harvard Forest MA (az 225°) | sunset | 2 → 2 | 0.52 | fisheye over the canopy, warm rim light |
| `phenocam:uiefsorghum` U. Illinois Energy Farm | thunderstorm | 5 → 1 | 0.80 | rain shaft + dark base over the field, best Q of any positive |
| `phenocam:NEON.D10.STER.DP1.00033` Sterling CO tower | thunderstorm | 12 → 1 | 0.66 | shelf cloud across the plains |
| `phenocam:archboldavir` / `archboldpnot` Archbold FL | sunrise | 1 → 1 each | 0.56–0.64 | pastel Florida sunrises; the site has four near-identical cams, dedupe keeps one |
| `iem:KCCI-008` | sunrise | 1 → 1 | 0.46 | Iowa TV cam, warm horizon |

Aurora: 80 events on Kp≥7 nights, no accepted frame — the archive cameras face the wrong way or
are not `night_ok`; for an aurora demo use the `night_ok` all-sky/Nordic rows in the README table
(`manual:irf-kiruna-allsky`, `no_vegvesen:2000065_1`, `iceland:*`) rather than anything measured here.

Caveat: `data/cameras.parquet` shipped without the `iem`/`phenocam` shards; run
`sunroof-camera refresh --source iem --source phenocam` once (≈1 min) so these cameras exist live.

---

## 5. Sources

Vision-language / embeddings
- Radford et al., *Learning Transferable Visual Models From Natural Language Supervision* (CLIP), ICML 2021. https://proceedings.mlr.press/v139/radford21a/radford21a.pdf
- Zhai et al., *Sigmoid Loss for Language Image Pre-Training* (SigLIP), ICCV 2023. https://openaccess.thecvf.com/content/ICCV2023/papers/Zhai_Sigmoid_Loss_for_Language_Image_Pre-Training_ICCV_2023_paper.pdf
- Apple, *MobileCLIP* (CVPR 2024) code + Core ML weights. https://github.com/apple/ml-mobileclip , https://huggingface.co/apple/coreml-mobileclip

Image statistics / quality
- Hasler & Süsstrunk, *Measuring Colourfulness in Natural Images*, SPIE HVEI 2003. https://infoscience.epfl.ch/record/33994
- Pertuz, Puig & García, *Analysis of focus measure operators for shape-from-focus*, Pattern Recognition 46 (2013). doi:10.1016/j.patcog.2012.11.011
- He, Sun & Tang, *Single Image Haze Removal Using Dark Channel Prior*, CVPR 2009. https://people.csail.mit.edu/kaiming/publications/cvpr09.pdf
- Mittal, Moorthy & Bovik, *No-Reference Image Quality Assessment in the Spatial Domain* (BRISQUE), IEEE TIP 2012. https://live.ece.utexas.edu/publications/2012/TIP%20BRISQUE.pdf
- Talebi & Milanfar, *NIMA: Neural Image Assessment*, IEEE TIP 2018. https://research.google/pubs/nima-neural-image-assessment/
- Ke et al., *MUSIQ: Multi-scale Image Quality Transformer*, ICCV 2021. doi:10.1109/ICCV48922.2021.00510
- ARM, *Total Sky Imager (TSI) Handbook* (red/blue ratio sky–cloud separation). https://www.arm.gov/publications/tech_reports/handbooks/tsi_handbook.pdf

Cloud classification datasets / methods
- Heinle, Macke & Srivastav, *Automatic cloud classification of whole sky images*, AMT 3, 2010. doi:10.5194/amt-3-557-2010
- Dev, Lee & Winkler, *Categorization of cloud image patches using an improved texton-based approach* (SWIMCAT), ICIP 2015. doi:10.1109/ICIP.2015.7350833
- Zhang et al., *CloudNet: Ground-Based Cloud Classification With Deep CNN* (CCSN dataset), GRL 2018. doi:10.1029/2018GL077787
- Liu et al., *Ground-Based Cloud Classification Using Task-Based Graph Convolutional Network* (TJNU GCD), GRL 2020. doi:10.1029/2020GL087338 ; dataset https://github.com/shuangliutjnu/TJNU-Ground-based-Cloud-Dataset
- *DeepSky dataset: a new benchmark for ground-based cloud classification using all-sky images*, Zenodo 2023. doi:10.5281/zenodo.8208505
- *Deriving WMO Cloud Classes From Ground-Based RGB Pictures With a Residual Neural Network Ensemble*, Earth and Space Science 2025. doi:10.1029/2024EA004112

Aurora
- Clausen & Nickisch, *Automatic Classification of Auroral Images From the Oslo Auroral THEMIS (OATH) Data Set Using Machine Learning*, JGR Space Physics 2018. doi:10.1029/2018JA025274
- Kvammen et al., *Auroral Image Classification With Deep Neural Networks*, JGR Space Physics 2020. doi:10.1029/2020JA027808
- *Transfer Learning Aurora Image Classification and Magnetic Disturbance Evaluation*, JGR Space Physics 2021. doi:10.1029/2021JA029683
- *Automatic Detection and Classification of Aurora in THEMIS All-Sky Images*, JGR Machine Learning and Computation 2024. doi:10.1029/2024JH000292

Rainbow
- Workman, Mihail & Jacobs, *A Pot of Gold: Rainbows as a Calibration Cue*, ECCV 2014. https://cs.valdosta.edu/~rpmihail/d.php?file=pubs%2Frainbow.pdf

Lightning (video)
- *A vision based method for detecting lightning in surveillance videos*, ICETT 2016. doi:10.1109/ICETT.2016.7873685
- *LD-Net: A novel one-stage knowledge distillation algorithm for lightning detection network*, Meteorological Applications 2024. doi:10.1002/met.2171
- *A Few-Shot Optical Classification Approach for Meteorological Lightning Monitoring: Leveraging Frame Difference and Triplet Network*, Remote Sensing 18(3), 2026. doi:10.3390/rs18030386

Fog / visibility from webcams
- Royal Meteorological Institute of Belgium, *Automated fog detection on the RMI webcam images using machine learning techniques*. https://radli.meteo.be/uploads/media/63ff730c19070/fogdetectionwithml.pdf
- *Meteorological Visibility Evaluation on Webcam Weather Image Using Deep Learning Features*, IJCTE 9(1), 2017. doi:10.7763/IJCTE.2017.V9.1186
- Palvanov & Cho, *VisNet: Deep Convolutional Neural Networks for Forecasting Atmospheric Visibility*, Sensors 19(6), 2019. https://www.mdpi.com/1424-8220/19/6/1343

Webcam scene datasets
- Jacobs, Roman & Pless, *Consistent Temporal Variations in Many Outdoor Scenes* (AMOS), CVPR 2007. doi:10.1109/CVPR.2007.383258 ; https://mvrl.cse.wustl.edu/datasets/amos/
- Laffont, Ren, Tao, Qian & Hays, *Transient Attributes for High-Level Understanding and Editing of Outdoor Scenes*, SIGGRAPH 2014. http://transattr.cs.brown.edu/
- Mihail et al., *SkyFinder* sky-segmentation dataset (2016). https://zenodo.org/records/5884485

Internal
- PR #3 plan, PR #6 service, PR #18/#19 health & FAA fetch, PR #26 SQLite + cron match — see README for the current CLI/API.
