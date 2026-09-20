"""Generate the Sunroof architecture diagrams as SVG.

Every diagram is emitted twice from one layout description, on one fixed
1600x1000 canvas:

  <name>.svg           overview  - box titles only
  <name>-detailed.svg  detailed  - titles plus the notes that used to live
                                   in camera/docs/*.svg and frontend/docs

    uv run python docs/diagrams/gen_diagrams.py [out_dir]
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1600, 1200
MARGIN = 28
FONT = "Helvetica,Arial,sans-serif"

STYLES = {
    "box": "fill:#fff;stroke:#333",
    "ext": "fill:#f4f4f4;stroke:#888;stroke-dasharray:5 3",
    "q": "fill:#fdf0ff;stroke:#9254de",
    "db": "fill:#f0fff0;stroke:#389e0d",
    "gate": "fill:#fff7e6;stroke:#d48806",
    "vlm": "fill:#e6f7ff;stroke:#1890ff",
    "route": "fill:#e6fffb;stroke:#13c2c2",
    "fail": "fill:#fff1f0;stroke:#c0392b",
    "later": "fill:#fafafa;stroke:#999;stroke-dasharray:3 3",
    "img": "fill:#f0fff0;stroke:#389e0d",
    "hls": "fill:#fff7e6;stroke:#d48806",
    "ifr": "fill:#f9f0ff;stroke:#9254de",
}
BAND = {  # section background per style
    "q": "#fbf5ff", "gate": "#fffbf2", "vlm": "#f2faff", "route": "#f2fffd",
    "db": "#f6fff6", "ext": "#f7f7f7", "later": "#fafafa", "box": "#f7f7f7",
}


@dataclass
class Box:
    title: str  # short title (overview)
    lines: list[str] = field(default_factory=list)
    style: str = "box"
    span: float = 1.0  # relative width inside the row
    long: str | None = None  # title used in the detailed version

    def heading(self, detailed: bool) -> str:
        return self.long if detailed and self.long else self.title


@dataclass
class Row:
    boxes: list[Box]
    chain: bool = True  # horizontal arrows between neighbours
    link: str | None | bool = None  # arrow from previous row: label / None / False


@dataclass
class Section:
    title: str
    rows: list[Row]
    style: str = "box"
    link: str | None | bool = None  # arrow from previous section
    long: str | None = None

    def heading(self, detailed: bool) -> str:
        return self.long if detailed and self.long else self.title


@dataclass
class Diagram:
    name: str
    title: str
    subtitle: str
    sections: list[Section]


# --------------------------------------------------------------------------- layout

TITLE_H = 60
SEC_HEAD = 30
SEC_PAD = 14
SEC_GAP = 34  # room for the inter-section arrow
ROW_GAP = 34
LINE_H = 14.5
OVERVIEW_BOX_H = 78


def box_lines(b: Box, bw: float, detailed: bool) -> list[tuple[str, str]]:
    if not detailed:
        return []
    out: list[tuple[str, str]] = []
    for ln in b.lines:
        cls = "m" if ln.startswith("`") else "s"
        for wl in wrap(ln.replace("`", ""), bw - 24, 6.5 if cls == "m" else 5.8):
            out.append((cls, wl))
    return out


def box_h(b: Box, bw: float, detailed: bool) -> float:
    n = len(box_lines(b, bw, detailed))
    if n == 0:
        return 44 if detailed else OVERVIEW_BOX_H
    return 34 + LINE_H * n


def row_widths(row: Row, inner_w: float) -> tuple[list[float], float]:
    n = len(row.boxes)
    gap = 30 if row.chain else 18
    total_span = sum(b.span for b in row.boxes)
    avail = inner_w - 2 * SEC_PAD - gap * (n - 1)
    return [avail * b.span / total_span for b in row.boxes], gap


def wrap(s: str, width_px: float, px_per_char: float = 6.1) -> list[str]:
    maxc = max(12, int(width_px / px_per_char))
    out: list[str] = []
    for para in s.split("\n"):
        cur = ""
        for word in para.split(" "):
            if cur and len(cur) + 1 + len(word) > maxc:
                out.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}" if cur else word
        out.append(cur)
    return out


def render(d: Diagram, detailed: bool, gap_k: float = 1.0) -> str:
    parts: list[str] = []
    global SEC_HEAD, SEC_GAP, ROW_GAP
    SEC_HEAD, SEC_GAP, ROW_GAP = (30, 34, 34) if detailed else (44, 56, 52)
    SEC_GAP, ROW_GAP = SEC_GAP * gap_k, ROW_GAP * gap_k
    inner_w = W - 2 * MARGIN
    y = MARGIN + TITLE_H

    def text(x, yy, s, cls="", extra=""):
        parts.append(f'<text x="{x:.1f}" y="{yy:.1f}" class="{cls}" {extra}>{escape(s)}</text>')

    def arrow(x1, y1, x2, y2, label=None, red=False):
        cls = "no" if red else "f"
        parts.append(f'<path class="{cls}" d="M{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f}"/>')
        if label:
            lx, ly = (x1 + x2) / 2 + 6, (y1 + y2) / 2 - 4
            text(lx, ly, label, "lbl")

    prev_sec_bottom = None
    for sec in d.sections:
        # measure
        row_geoms = []
        sec_top = y
        yy = y + SEC_HEAD + SEC_PAD
        for ri, row in enumerate(sec.rows):
            if ri > 0:
                yy += ROW_GAP if row.link is not False else 12
            widths, _ = row_widths(row, inner_w)
            rh = max(box_h(b, bw, detailed) for b, bw in zip(row.boxes, widths))
            row_geoms.append((yy, rh))
            yy += rh
        sec_bottom = yy + SEC_PAD
        band = BAND.get(sec.style, "#f7f7f7")
        parts.append(
            f'<rect x="{MARGIN}" y="{sec_top}" width="{inner_w}" height="{sec_bottom - sec_top:.1f}" '
            f'rx="10" fill="{band}" stroke="{STYLES[sec.style].split("stroke:")[1].split(";")[0]}" stroke-width="1.2"/>'
        )
        text(MARGIN + 14, sec_top + (21 if detailed else 30), sec.heading(detailed), "sec")

        # inter-section arrow
        if prev_sec_bottom is not None and sec.link is not False:
            arrow(W / 2, prev_sec_bottom + 2, W / 2, sec_top - 2, sec.link if isinstance(sec.link, str) else None)

        prev_row_geom = None
        for ri, row in enumerate(sec.rows):
            ry, rh = row_geoms[ri]
            n = len(row.boxes)
            widths, gap = row_widths(row, inner_w)
            x = MARGIN + SEC_PAD
            for bi, b in enumerate(row.boxes):
                bw = widths[bi]
                bh = box_h(b, bw, detailed)
                lines = box_lines(b, bw, detailed)
                parts.append(
                    f'<rect x="{x:.1f}" y="{ry:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="8" '
                    f'style="{STYLES[b.style]};stroke-width:1.4"/>'
                )
                ty = ry + 27 if lines else ry + bh / 2 + (5 if detailed else 7)
                text(x + 12, ty, b.heading(detailed), "t")
                ly = ry + 27 + LINE_H
                for cls, wl in lines:
                    text(x + 12, ly, wl, cls)
                    ly += LINE_H
                if row.chain and bi < n - 1:
                    ay = ry + 22 if detailed else ry + bh / 2
                    arrow(x + bw + 2, ay, x + bw + gap - 2, ay,
                          red=(row.boxes[bi + 1].style == "fail"))
                x += bw + gap
            if prev_row_geom is not None and row.link is not False:
                py, ph = prev_row_geom
                arrow(W / 2, py + ph + 2, W / 2, ry - 2, row.link if isinstance(row.link, str) else None)
            prev_row_geom = (ry, rh)
        prev_sec_bottom = sec_bottom
        y = sec_bottom + SEC_GAP

    content_h = y - SEC_GAP + MARGIN
    scale = min(1.0, (H - MARGIN - TITLE_H) / max(1.0, content_h - MARGIN - TITLE_H))
    global OVERVIEW_BOX_H
    if scale < 1.0 and not detailed and OVERVIEW_BOX_H > 46:
        OVERVIEW_BOX_H -= 8
        try:
            return render(d, detailed, max(0.6, gap_k - 0.1))
        finally:
            OVERVIEW_BOX_H += 8

    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="{FONT}" font-size="12">',
        "<defs>",
        '<marker id="a" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">'
        '<path d="M0,0 L9,4.5 L0,9 z" fill="#444"/></marker>',
        '<marker id="r" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">'
        '<path d="M0,0 L9,4.5 L0,9 z" fill="#c0392b"/></marker>',
        "<style>",
        ".t{font-weight:700;font-size:%spx;fill:#111}" % (13.5 if detailed else 20),
        ".s{font-size:11.5px;fill:#333}",
        ".m{font-family:Menlo,Consolas,monospace;font-size:10.5px;fill:#222}",
        ".lbl{font-size:%spx;fill:#555;font-style:italic}" % (11 if detailed else 14),
        ".sec{font-weight:700;font-size:%spx;fill:#222}" % (15 if detailed else 22),
        ".h1{font-weight:700;font-size:24px;fill:#111}",
        ".h2{font-size:12.5px;fill:#555;font-style:italic}",
        "path.f{stroke:#444;stroke-width:1.5;fill:none;marker-end:url(#a)}",
        "path.no{stroke:#c0392b;stroke-width:1.5;fill:none;marker-end:url(#r);stroke-dasharray:6 3}",
        "</style></defs>",
        f'<rect width="{W}" height="{H}" fill="#fff"/>',
        f'<text x="{MARGIN}" y="{MARGIN + 22}" class="h1">{escape(d.title)}</text>',
        f'<text x="{MARGIN}" y="{MARGIN + 44}" class="h2">{escape(d.subtitle)}</text>',
        f'<g transform="translate({(W - scale * W) / 2:.1f},{MARGIN + TITLE_H}) scale({scale:.4f}) translate(0,{-(MARGIN + TITLE_H)})">',
    ]
    return "\n".join(head + parts + ["</g>", "</svg>"]) + "\n"


# --------------------------------------------------------------------------- content

def preprocessing() -> Diagram:
    return Diagram(
        "preprocessing-schema",
        "Sunroof · camera preprocessing → mini-DB → query",
        "Offline/scheduled jobs build the catalog; the backend calls find_cameras() at event time. Grey = other teams' components.",
        [
            Section("Build the catalog (offline)", [
                Row([
                    Box("Sources", [
                        "Tier 1 · JSON catalog + JPEG: Caltrans, ALERTCalifornia, NY/ON 511, Digitraffic, Iceland, Panomax, foto-webcam, NDBC, PhenoCam, IEM, NPS (~12k views)",
                        "Tier 2 · needs free key: Windy (~70k), CARS states, WSDOT, FAA WeatherCams",
                        "Tier 3 · manual.yaml: YouTube 24/7, Explore.org, all-sky hobby cams",
                    ], "ext"),
                    Box("Adapters", [
                        "`catalog() → RawCamera[]`",
                        "`normalize() → Camera row`",
                        "`fetch_frame(cam, ts?) → Frame`",
                        "Edge cases: gzip/identity headers, placeholder 'cam offline' JPEG → sha1 set, redirect-to-HTML / <3 KB frozen frame, per-host rate limit, signed URLs (Windy), tz-less timestamps → EXIF",
                        "Heading: numeric (Panomax/PhenoCam/IEM/FAA) · text 'N/looking west' · road geometry · PTZ → unknown",
                    ], "gate", long='Adapters (one per source)'),
                    Box("Enrich", [
                        "Coverage = viewing cone: az, hfov, elev_min, elev_max, conf",
                        "8-pt compass from text (±22.5°), road geometry, unknown/PTZ → no bearing filter, score ×0.6",
                        "Static: tz (timezonefinder) · alt_m (SRTM) · license · sky_frac heuristic on one daytime frame",
                    ], "gate", long='Enrich (once per camera)'),
                    Box("Mini-DB", [
                        "`cameras.parquet` (pandas, one row per view)",
                        "`id source name lat lon alt_m tz azimuth_deg hfov_deg elev_min/max heading_conf sky_frac night_ok all_sky ptz image_url stream_url embed_url refresh_s history_kind license last_frame_ts health quality_score`",
                        "`camera_samples.parquet`: camera_id ts url sha1 bytes luminance solar_elev vlm_label",
                    ], "db"),
                ]),
                Row([
                    Box("Scheduled jobs", [
                        "daily: re-pull catalogs, upsert, 3 misses → dead",
                        "10 min: probe frame → last_frame_ts, sha1, luminance → health (re-uses fetch_frame)",
                    ], "gate"),
                    Box("Annular-sector test", [
                        "Feature at altitude h over ground distance d appears at elevation α = atan(h/d) − d/(2·R_earth)",
                        "visible iff |bearing − az| ≤ hfov/2 + δ(conf) and elev_min ≤ α ≤ elev_max ⇒ annulus d_min..d_max",
                        "h per type: anvil 12 km, mammatus 4, aurora 110–250, fog 0.2; sunset/rainbow use sun az",
                    ], "box"),
                ], chain=False, link=False),
            ], "gate", long='1–4 · Build the catalog (offline / scheduled jobs)'),
            Section("Query time (per event)", [
                Row([
                    Box("Event", [
                        "`type lat lon radius_km t_start t_end severity`",
                        "sunrise sunset thunderstorm lightning mammatus lenticular fog/undercast aurora rainbow",
                    ], "ext"),
                    Box("find_cameras()", [
                        "① bbox prefilter → haversine ≤ R_cap(type)",
                        "② annular-sector test: bearing ∈ az ± hfov/2, d_min ≤ d ≤ d_max; per-type rules (sunset: sun_az, aurora: night_ok…)",
                        "③ night gate (astral) · ④ health == live · ⑤ score = geo_fit + freshness + sky_frac + source_prior",
                        "⑥ return top 3k hits with reason string + fetch URL; t in the past → history_template (replay)",
                    ], "q", long='find_cameras(event, k)'),
                    Box("Frame fetch + CV gates", [
                        "JPEG · HLS/YouTube → ffmpeg 1 frame · embed → screenshot",
                        "sha1 dedupe, luminance gate, sky crop; verdict cached per (cam, 10-min bucket)",
                    ], "gate"),
                    Box("VLM gate", [
                        "`usable: bool · sky_visible: 0..1 · event_visible: enum · night: bool`",
                        "verdict → camera_samples → night_ok, quality_score feedback",
                    ], "vlm", long='VLM gate (OpenAI)'),
                    Box("Frontend", [
                        "top-k passing pairs; iframe / <video> / img via proxy (CORS); attribution + 'why'",
                    ], "ext"),
                ]),
            ], "q", link="reads cameras.parquet", long='5 · Query time (backend calls us every ~10 min per event)'),
            Section("Historical / backtest", [
                Row([
                    Box("Archives", [
                        "IEM 2003→ (5-min, heading logged), PhenoCam 2008→ (30-min), foto-webcam (years), Caltrans last-12, FAA/Digitraffic 24 h",
                        "fetch_frame(cam, ts) fills history_template",
                    ], "db"),
                    Box("Replay fixtures", [
                        "Front Range mammatus day · Lapland aurora night · Alpine undercast morning · Miami thunderstorm",
                        "same find_cameras() with t_end in the past → demo never depends on tonight's weather",
                    ], "db"),
                    Box("Sponsor seam", [
                        "pandas/Parquet canonical; Elastic mirror only if the events team adopts it",
                        "OpenAI = VLM gate · Token Company = frame gating/caching metrics · Voloridge = archives → rarity/backtest",
                    ], "ext"),
                ], chain=False),
            ], "db", link=False, long='6 · Historical / backtest (Voloridge angle)'),
        ],
    )


def query_routing() -> Diagram:
    return Diagram(
        "query-routing-schema",
        "Sunroof · event → which camera → is the frame real → what the frontend gets",
        "One call of resolve_footage(event, k=3, deadline 30 s). Solid arrows = success path, red dashed = status back to the weather backend.",
        [
            Section("Inputs", [
                Row([
                    Box("Event (weather backend)", [
                        "`Event{id, type, lat, lon, radius_km, t_start, t_end, severity, rarity, evidence{…}, region?: polygon}`",
                        "every ~10 min per live event · once per replay event",
                    ], "ext"),
                    Box("Mini-DB", [
                        "cameras.parquet · one row per view: lat lon alt_m tz · azimuth hfov elev · sky_frac night_ok all_sky ptz · urls refresh_s · health quality_score",
                        "camera_samples.parquet (sha1, luminance, vlm_label)",
                    ], "db"),
                    Box("EventProfile (per type)", [
                        "geometry h/R_cap · night_policy · require/prefer · media · frames · hold",
                        "thunderstorm annulus 12/150 twilight … · undercast above_layer · lightning annulus 6/60 hls burst · sunset sun_az exact heading … (9 rows)",
                    ], "q", long='EventProfile table · one row per type'),
                ], chain=False),
            ], "ext"),
            Section("① find_cameras() → candidates", [
                Row([
                    Box("bbox → haversine", ["d ≤ R_cap(type) or ≤ radius_km for point events", "90k → ~500 rows"]),
                    Box("geometry test", ["annulus: |b−az| ≤ hfov/2+δ, dmin ≤ d ≤ dmax", "sun_az / antisolar · above_layer: alt_m vs fog_top · region polygon", "ptz/unknown heading: skip test, score ×0.6"], long='geometry test (per profile)'),
                    Box("hard filters", ["health == live · profile.require (elev_min, alt_m, refresh_s, heading…)", "replay: history covers t"]),
                    Box("night gate (astral)", ["sun_el > −6 all cams · −6…−18 night_ok or ×0.5 · < −18 night_ok only", "lightning exempt · aurora < −12"]),
                    Box("score & rank", ["geo_fit + freshness + sky_frac + source_prior + night_mult + profile.prefer − dup_penalty", "top 3k (≤9) with reason string + URLs"]),
                    Box("no cameras", ["drop / don't re-poll · retry after sunrise unless aurora/lightning"], "fail", long='0 rows → NO_CAMERAS_IN_RANGE · CAMERAS_DARK'),
                ]),
            ], "q", long='① find_cameras(event, profile, k) → top 3k candidates · vectorised pandas, ~10 ms'),
            Section("② fetch frames + cheap gates", [
                Row([
                    Box("get a frame", ["jpeg GET image_url?t=… · hls m3u8 → last seg → ffmpeg 1 fr · lightning: 15–20 s burst · embed yt-dlp -g → hls", "page no frame → iframe-only, verified=false"], "gate", long='get a frame (profile.media_pref)'),
                    Box("bytes", ["200 · image/* · ≥3 KB · FFD8…FFD9 · PIL.verify", "placeholder: sha1 ∈ per-source offline set · frozen: pHash ≈ last sample"], "gate"),
                    Box("timestamp", ["source JSON → EXIF → Last-Modified → fetch time; reject if > 2·refresh_s old", "clock skew: bytes changed but ts says stale → trust bytes"], "gate"),
                    Box("pixels", ["mean lum < 12/255 → reject (except aurora, lightning) · std < 4 → uniform · Laplacian var < 15 → penalise", "dedupe across cams: pHash dist ≤ 4"], "gate", long='pixels (256-px sky crop, <5 ms)'),
                    Box("re-rank", ["score · (1 + 0.2·sharpness)", "CLIP pre-gate (optional, CPU): 'offline card / black / road at night' → drop before paying"], "gate", long='re-rank → top ≤ 6'),
                    Box("all frames failed", ["retry in 10 min · 30 s deadline returns partial, verified=false", "rejected frames → camera_samples.vlm_label = 'rejected:<gate>'"], "fail", long='every frame failed → ALL_STALE · TIMEOUT'),
                ]),
            ], "gate", link="≤ 9 candidates", long='② fetch frames + cheap gates (no LLM) · concurrent httpx, 5 s timeout · verdict cached per (camera, 10-min bucket)'),
            Section("③ VLM gate", [
                Row([
                    Box("prompt", ["fixed system msg + target type + one-line definition + sun_az & cam.azimuth (sunset)", "asks what it DOES see, not yes/no → fewer sycophantic positives", "escalate ≤ 1× per event to the large model when unsure"], "vlm"),
                    Box("Verdict", ["`usable: bool · sky_visible: 0..1 · night: bool · event_visible: yes|partial|no|unsure · event_type_seen · confidence 0..1 · quality 1..5 · caption ≤ 12 words`", "verdict → camera_samples → night_ok / quality_score feedback"], "vlm", long='Verdict (pydantic)'),
                    Box("pass / pick order", ["pass iff usable ∧ event_visible ∈ {yes, partial} ∧ event_type_seen == target ∧ conf ≥ 0.5", "order = conf · (0.6 + 0.1·quality) → top k (=3) to routing"], "vlm"),
                    Box("VLM says no", ["labelled negative worth keeping (backtest) · only partial/unsure → retry 10 min"], "fail", long='fresh frames, VLM says no → NO_FOOTAGE_FOUND · EVENT_NOT_VISIBLE'),
                ]),
            ], "vlm", link="≤ 6 frames (lightning: max-projection composite)", long="③ VLM gate · OpenAI mini-tier, structured output, detail:'low', ≤ 6 concurrent calls, p50 2 s"),
            Section("④ route to the frontend", [
                Row([
                    Box("Footage envelope", ["`{event_id, camera_id, rank, verified, media:{kind: image|hls|iframe, src, poster, refresh_s, expires_at}, verdict:{…}, why, camera:{…}, frame_ts, hold_until}`", "image ≈ 90 % of catalog and the only cheaply VLM-verified path"], "route", long='Footage (what the frontend gets)'),
                    Box("proxy", ["fixes CORS (.gov/DOT block browsers) · rewrites HLS segment URIs · Cache-Control = refresh_s", "no-store pass-through: ALERTCA, Windy (signed URL), Panomax iframe"], "route", long='proxy /proxy/frame|hls/{camera_id}'),
                    Box("store + push", ["aiosqlite, key = event_id · GET /feed · GET /events/{id}/footage · GET /stream (SSE)", "hold & re-verify: visible for hold_s (10–30 min); frozen/black → demote + ALL_STALE upstream"], "route"),
                    Box("Frontend", ["image → <img> re-request every refresh_s · hls → hls.js · iframe → embed, verified=false", "time to first verified frame ≈ 4 s p50 (JPEG) · 10–15 s HLS · 30 s cap"], "ext"),
                ]),
            ], "route", link="k verified (camera, frame, verdict)", long='④ route · one universal Footage envelope, three renderers · store + SSE · hold & cheap re-verify'),
            Section("FootageResult → weather backend", [
                Row([Box("FootageResult", ["`{event_id, status, footage[0..k], candidates, checked, vlm_calls, tokens_in, elapsed_ms, retry_after_s, reason}`"], "ext")]),
            ], "ext", link="FOOTAGE_FOUND + counters", long='FootageResult → weather backend (always returned, success or not)'),
        ],
    )


def frontend() -> Diagram:
    return Diagram(
        "frontend-schema",
        "Sunroof · frontend — one object in, three renderers out, a globe on top later",
        "Web SPA (Vite + React 19 + TS + Tailwind). Talks only to the camera backend, never to a camera host directly.",
        [
            Section("Camera backend (FastAPI)", [
                Row([
                    Box("GET /feed", ["ranked Footage[] across all active events · poll 60 s · fixture fallback if VITE_API_BASE unset"], "ext"),
                    Box("GET /events/{id}/footage", ["all candidates for one event (globe drill-down)"], "ext"),
                    Box("GET /stream (SSE)", ["footage.new / replaced / expired → invalidate query keys, never mutate UI"], "ext"),
                    Box("GET /proxy/{camera}/…", ["frames, HLS playlists, posters — same origin, CORS-free, camera URLs hidden"], "ext"),
                    Box("GET /cameras.geojson", ["lat/lon of every catalog camera → globe dots · join event.type/place/rarity into Footage"], "later"),
                ], chain=False),
            ], "ext", long='sunroof/camera backend (FastAPI) — read-only for the frontend'),
            Section("The one object: Footage", [
                Row([
                    Box("Footage", [
                        "`{ event_id, camera_id, rank, verified, media: { kind: 'image'|'hls'|'iframe', src, poster?, refresh_s?, expires_at?, width?, height? }, verdict?: { event_visible, confidence, quality, caption }, why, camera: { name, provider, lat, lon, bearing?, source_url }, frame_ts, hold_until, event: { type, place?, rarity?, lat, lon, t0, t1 } }`",
                        "Card, cycler, globe pin and evidence panel all take the same prop; media.kind is the only switch in the whole UI",
                    ], "vlm"),
                ]),
            ], "vlm", link="JSON", long='The one object: Footage (src/lib/types.ts — mirrors backend, no adapters)'),
            Section("Frontend SPA", [
                Row([
                    Box("api.ts", ["useFeed() → TanStack Query ['feed'] · useStream() → EventSource · mediaUrl() prefixes API_BASE", "no API_BASE ⇒ fixtures/feed.json (offline demo)"]),
                    Box("events.ts · time.ts", ["filter chips: all · sunrise · sunset · storms … (fog + undercast one chip)", "still → 'frame 05:11 UTC · 37 min ago' · stream → 'delay ≈ 30 s'"]),
                ], chain=False),
                Row([Box("App.tsx + Header.tsx", ["filter → visible = feed.filter(type).slice(0,4) → current; header: title · event select · fixture/live/SSE dot"])]),
                Row([
                    Box("Cycler.tsx", ["auto-advance 20 s, pauses on hover, a11y dots"]),
                    Box("FootageCard.tsx", ["hero media · type chip · place · time/delay · VLM caption · 1-line 'what is this' · attribution · verified · why-this-camera"]),
                ], chain=False),
                Row([Box("FootageMedia.tsx (switch on media.kind)", [])]),
                Row([
                    Box("ImageMedia", ["<img> re-fetched every refresh_s, cache-bust ?t=…, keep old frame until new one loads", "IEM, Windy, DOT stills (~90 % of catalog)"], "img", long="'image' → ImageMedia"),
                    Box("HlsMedia", ["<video> native HLS on Safari, lazy hls.js elsewhere; muted, autoplay, poster on fatal error", "DOT / 511 streams, Skyline"], "hls", long="'hls' → HlsMedia"),
                    Box("IframeMedia", ["poster first, click to open the third-party player; allow=autoplay;fullscreen", "Panomax, YouTube (not verifiable → unverified)"], "ifr", long="'iframe' → IframeMedia"),
                ], chain=False),
            ], "box", long='frontend SPA — iteration 1'),
            Section("Later iterations", [
                Row([
                    Box("Globe", ["pointsData = all cameras · ringsData = event centers · htmlElements ≤ 50 thumbnail pins · arcsData camera → event sightline", "click pin → setSelected(footage), card slides in; layout flips 'centered card' → 'globe + card'", "web not RN: hls.js / iframe / EventSource are free in a browser; one origin, one deploy"], "later", long='iteration 2 — the globe (react-globe.gl / three.js)'),
                    Box("Depth (if time)", ["evidence drawer: frame + VLM verdict + gate stats + weather trigger", "rarity badge · share link /e/{event_id} · PWA kiosk manifest · timeline scrubber over hold_until"], "later", long='iteration 3 — depth, if time'),
                ], chain=False),
            ], "later", link=False, long='later iterations — same Footage, new consumers'),
        ],
    )


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    out.mkdir(parents=True, exist_ok=True)
    for d in (preprocessing(), query_routing(), frontend()):
        (out / f"{d.name}.svg").write_text(render(d, detailed=False))
        (out / f"{d.name}-detailed.svg").write_text(render(d, detailed=True))
        print("wrote", d.name)


if __name__ == "__main__":
    main()
