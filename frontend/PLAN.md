# sunroof · frontend plan

Living doc. Every later PR in this repo should point at the section it
implements. Diagram: `../docs/diagrams/frontend-schema.svg`.

Inputs this plan was derived from: `notken12/sunroof` (`camera/README.md`,
`camera/docs/query-and-routing-plan.md` §5 "Routing and the frontend format",
the two camera schema SVGs), the project one-pager (doc-3), the frontend
brief (doc-4: "Web page … minimalist … Apple light theme … iteration 1
broadcast, iteration 2 globe"), and the HackMIT sponsor sheet.

---

## 0. TL;DR

- **Web, not React Native.** Vite + React 19 + TypeScript + Tailwind v4.
  No Next.js, no Expo. Reasons in §1.
- **The frontend renders exactly one object: `Footage`** (the envelope the
  camera backend already specified). Three renderers keyed on `media.kind`
  (`image` / `hls` / `iframe`). Nothing per-source, nothing per-event-type
  beyond a label + colour.
- **Fixture first.** `src/fixtures/feed.json` mirrors `GET /feed`; the app is
  fully demoable with `VITE_API_BASE` unset. Flip one env var to go live.
- **Globe = iteration 2, but the layout is built for it from day one**: the
  broadcast card is a component that already takes a `Footage`, and the app
  state is "selected footage + filter", so the globe just becomes a second
  way to set `selected`. Library: **`cobe`** (5 kB WebGL, dotted-earth look
  that matches the theme) — chosen over `react-globe.gl` (~570 kB gzip +
  texture) after measuring both; see §3 iteration 2.

---

## 1. Stack decision — why not React Native

The brief says **"Web page"**, judges see it on a laptop/projector, and the
three hard requirements all fight RN:

| requirement                                                     | web (React DOM)                                                   | React Native (+ RN-web)                                                                                                    |
| --------------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `hls` renderer for DOT/511 streams                              | `hls.js` on a `<video>`; Safari native                            | `expo-av`/`react-native-video` on device; on web falls back to… hls.js via RN-web `<video>` shim — extra layer for no gain |
| `iframe` renderer (YouTube / Panomax / Roundshot, ToS-required) | `<iframe>`                                                        | `react-native-webview`; on web RN-web has no iframe primitive, you write a platform-split component                        |
| Globe (iteration 2)                                             | `react-globe.gl` / `globe.gl` / CesiumJS — mature, WebGL, drop-in | `expo-gl` + `expo-three`: works but no globe.gl port, no CSS labels/HTML pins, you hand-roll the pin layer                 |
| SSE (`GET /stream`)                                             | `EventSource` built in                                            | polyfill on native                                                                                                         |
| "Apple light theme, Helvetica" polish                           | Tailwind, CSS, real fonts                                         | StyleSheet, no CSS, platform font differences                                                                              |

RN buys us an iOS/Android build we won't ship in a hackathon. If a phone
demo becomes a thing, the web app is responsive and a PWA manifest is a
20-line add; that covers "open it on your phone" without a second codebase.
The Long Lake "push notification → live view" story is a Web Push later, not
an RN app.

**Why Vite over Next.js:** no SSR/route-handlers needed (the camera backend
already owns `/feed`, `/stream`, `/proxy/*`); a static SPA deploys anywhere
(Vercel/Netlify/S3, or served by FastAPI itself as static files), and the
dev loop is faster. If a same-origin proxy is needed in dev, `vite.config.ts`
`server.proxy` → `http://localhost:8000`.

**Kept deliberately small:**

| concern        | choice                                                                                                                     | why                                                                                    |
| -------------- | -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| framework      | React 19 + TS + Vite 6                                                                                                     | team knows React; TS pins the `Footage` contract                                       |
| styling        | Tailwind v4 + a few CSS vars in `index.css`                                                                                | light neutral theme in one place; "change colours of bars/buttons later" = change vars |
| data           | TanStack Query for `/feed` & `/events/{id}/footage`; raw `EventSource` for `/stream` that invalidates queries              | cache/retry/stale for free; SSE stays 15 lines                                         |
| state          | React `useState` lifted to `App` (selected footage, event-type filter). Zustand only if the globe makes prop-drilling ugly | nothing global enough yet                                                              |
| video          | `hls.js` (lazy `import()` so the image-only path pays nothing)                                                             | already the backend's recommendation                                                   |
| globe (iter 2) | `cobe` (lazy chunk) + our own lat/lon→screen projection for DOM pins                                                       | 5 kB, zero deps, dotted light globe fits the design; pins stay real `<button>`s        |
| icons          | `lucide-react`                                                                                                             | thin-line, neutral                                                                     |
| tests          | Vitest + Testing Library on `FootageCard` + renderer switch                                                                | cheap, guards the contract                                                             |
| lint/format    | oxlint (vite template default) + Prettier                                                                                  |                                                                                        |

---

## 2. Contract (read-only; owned by `sunroof/camera`)

Copied into `src/lib/types.ts` verbatim from query-and-routing-plan §5. If
the backend changes it, this file is the diff to review.

```ts
type EventType =
  | 'sunrise'
  | 'sunset'
  | 'thunderstorm'
  | 'lightning'
  | 'mammatus'
  | 'lenticular'
  | 'undercast'
  | 'aurora'
  | 'rainbow'
type MediaKind = 'image' | 'hls' | 'iframe'

interface Footage {
  event_id: string
  camera_id: string
  rank: number
  verified: boolean // false for iframe-only sources
  media: {
    kind: MediaKind
    src: string
    poster?: string
    refresh_s?: number
    expires_at?: string
    width?: number
    height?: number
  }
  verdict?: {
    event_visible: 'yes' | 'partial' | 'no' | 'unsure'
    confidence: number
    quality: number
    caption: string
  }
  why: string
  camera: {
    name: string
    lat: number
    lon: number
    source: string
    page_url?: string
    attribution?: string
    license?: string
  }
  frame_ts: string // UTC ISO
  hold_until: string
  // joined in by /feed from the Event (weather half):
  event: {
    type: EventType
    lat: number
    lon: number
    radius_km: number
    place?: string
    rarity?: number
    severity?: number
  }
}
```

Endpoints (camera backend, FastAPI): `GET /feed` → `Footage[]` ranked;
`GET /events/{id}/footage` → `Footage[]`; `GET /stream` (SSE) →
`{event_id, status}`; `GET /proxy/frame/{camera_id}?t=…`; `GET
/proxy/hls/{camera_id}/…`. Frontend **never** hits a camera host directly.

Two things the frontend needs that the backend plan doesn't spell out — ask
the routing Devin to add them to `/feed` (both trivial joins):

1. `event.type`, `event.place` (reverse-geocoded name or "39.7°N 104.9°W"),
   `event.rarity` on each `Footage` — the card needs them and shouldn't
   fetch `/events/{id}` for each row.
2. `GET /cameras?fields=id,lat,lon,health` (or a static
   `cameras.geojson` dumped from `cameras.parquet` at build time) for the
   globe's "dot per camera" layer. Static file is fine for the demo.

Fog is out of scope (weather side may still emit it; the UI ignores unknown types).
`undercast` is its own chip.

---

## 3. Views

### Iteration 1 — Broadcast (build now)

```
┌───────────────────────────────────────────────────────────────┐
│  sunroof                       [ all sights ▾ ]   ● live 4    │  header, 56px
├───────────────────────────────────────────────────────────────┤
│                                                               │
│              ┌───────────────────────────────┐                │
│              │                               │                │
│              │      <FootageMedia/>          │  16:9, max 1100px
│              │  image | hls | iframe         │                │
│              │                               │                │
│              └───────────────────────────────┘                │
│   MAMMATUS · Denver, CO          frame 05:11 UTC · ~4 min ago │  caption row
│   "Mammatus lobes under a decaying anvil, looking SW"         │
│   Des Moines KCCI tower · Iowa Environmental Mesonet ↗        │  attribution
│                                                               │
│   ○ ○ ● ○      (up to 4 dots = other live pairs, auto-cycle)  │
└───────────────────────────────────────────────────────────────┘
```

- **One hero, centred, neutral background** (`#f5f5f7`-ish, Apple grey).
  Imagery is the colour; chrome is greyscale + one accent per event type
  used only on the small chip.
- **Time honesty** (explicit in the brief): caption shows `frame_ts` as
  "frame 05:11 UTC · 4 min ago"; for `hls`/`iframe` it says "stream ·
  delay ≈ Ns" (N from `Date.now() - frame_ts` of the poster). Never the word
  "live" alone; the header dot says "live" only for the SSE connection state.
- **Image refresh**: `image` renderer swaps `src` to `…?t=<now>` every
  `media.refresh_s` with a cross-fade; failed load keeps the previous frame.
- **Dropdown** (brief: "hardcoded types of events"): a `<select>`-styled
  listbox over the 9 chips + "all". Filters `/feed` client-side; when
  nothing matches show the empty state ("nothing verified for lenticular
  right now — cameras checked: N") using `FootageResult.reason` if the
  backend exposes it.
- **Auto-cycle** through the top 4 every 20 s (pause on hover). Gives the
  "4 live things happening now" feel of the one-pager without a grid
  fighting the hero.
- **Loading / empty state**: a tiny inline SVG weather glyph set (sun,
  cloud, bolt) cross-fading — "something cute, minimalistic". Also used as
  the favicon.
- **Sidebars: no.** The brief asked. A sidebar competes with the hero and
  becomes the globe's home in iteration 2 anyway. Everything secondary
  (evidence, why-this-camera, attribution) lives _under_ the hero and is
  collapsible.

### Iteration 2 — Globe (done)

**Library: `cobe`, not `react-globe.gl`.** Measured on a real build:
`react-globe.gl` adds ~570 kB gzip (three.js + d3) plus a 1–2 MB earth
texture and looks "NASA dark" unless re-skinned; `cobe` is ~6 kB gzip, zero
deps, and its dotted light-grey earth _is_ the Apple-light look we want.
What we lose (zoom, hit-testing on arbitrary globe points, photoreal
texture) we don't need: the globe's job is "all cameras as dots + ≤ 50
clickable event pins + auto-rotate".

**Same page, two layouts — not a separate page.** Header segmented toggle
(`Tv` / `Earth`) backed by a hash route: `#/globe` ↔ globe layout, anything
else ↔ broadcast (`src/lib/view.ts`, `useSyncExternalStore` on
`hashchange`). Filter, selected index and the 20 s auto-cycle survive the
switch because it's the same `App` state; the card is the same
`FootageCard`, docked right on `lg:` and stacked under the globe on phones.

**How it's built** (`src/components/globe/`):

- `Globe.tsx` — lazy-imports `cobe`, one `<canvas>` sized to its square
  wrapper (ResizeObserver → `globe.update({width,height})`). cobe draws the
  earth + every camera as a tiny marker (`useCameras()` → fixture
  `cameras.json` today, `GET /cameras.geojson` when the backend has it).
- Event pins are **DOM `<button>`s**, not cobe markers: one per visible
  `Footage` (≤ `PIN_SLOTS` = 50), thumbnail = `media.poster`, ring colour =
  `--c-{event.type}`. Every frame they're positioned with
  `projection.ts`, which mirrors cobe's shader camera (`phi`, `theta`,
  radius 0.8 + `markerElevation`) so a pin sits exactly on its dot; far-side
  pins fade out and lose pointer events. This avoids cobe v2's CSS-anchor
  positioning (no Firefox) and keeps pins fully styleable/clickable.
- Motion: idle auto-rotate (`IDLE_SPIN`), pointer drag on the canvas, and
  fly-to on select (`phiFacing(lon)` + `shortestTurn`, eased). All mutable
  render state lives in refs; the rAF loop never re-renders React. Loop
  skips frames while `document.hidden`; unmount cancels rAF and calls
  `globe.destroy()`.
- Click pin → `setIndex(...)` in `App`; the docked card switches, the globe
  flies. The `Cycler` keeps advancing, so the globe follows the broadcast.

**Phones.** Layout works at 420 px (pins shrink to 40 px, status label
collapses to the dot). Still a live WebGL loop → noticeable battery; the
default route is broadcast, globe is opt-in via the toggle. **PWA / iOS
install deliberately skipped** for now (decided with the team); when
wanted it's a manifest + service worker on the same build, no new codebase.

### Iteration 3 — Time travel (done, tryout version)

Third header layout (`#/time/<YYYY-MM-DD>T<HH:MM>`): the same globe, but the
pins are archive frames from a fixed set of cameras at a chosen **date +
camera-local time of day** (18:00 everywhere = one sunset ring around the
planet, no black night frames).

**Why hand-picked cameras.** Of ~40 sources only foto-webcam.eu (10 y, 10 min),
PhenoCam (2008→, 30 min) and IEM (2003→, 5 min) expose a frame per timestamp;
the other ~38k cameras are live-only. So `src/fixtures/archive-cams.json` is
~25 cameras chosen for sky-heavy views and uptime, spread as far as the
archives allow (Alps, N. Sea, Iowa, Hawaii, Alaska, Panama, Puerto Rico,
Brazil, Chile, Spain, UK, Sweden, DR Congo, Madagascar, China, Taiwan, NZ).
Each has `since` (first usable year) and `step_min` (cadence to snap to).

**Data path.** Fixture mode: browser loads the strftime template directly
(foto-webcam / IEM permit it; PhenoCam pins render hollow). Live mode: every
frame goes through `camera`'s `GET /proxy/history/{id}?ts=<ISO+offset>&w=<px>`
which resolves the source-specific archive URL (PhenoCam needs a browse-page
lookup) and downscales with Pillow — PhenoCam originals are 300–900 kB, the
`w=240` pin thumbnails ~15 kB. Local time → UTC uses `round(lon/15)`; good to
±1 h, which the UI's 30-min slider tolerates.

**Latency.** 25 parallel requests, progressive: pins pop in as they arrive,
typically 1–3 s for all (measured 0.4–1 s per frame through the proxy). The
hash updates 350 ms after the last slider move so a scrub isn't 25 requests
per pixel; a revisited moment is a browser cache hit.

**Later.** Backend pre-warming for the current moment, a "same UTC instant"
toggle, more archive sources (Digitraffic 24 h, Windy embeds), and the weather
model's events for that date as an overlay when its history exists.

### Iteration 4 — Motion & show (done)

All on top of the existing pieces, no new dependencies:

- **Sunset ring**: Play in the time controls steps the shared local clock
  30 min / 2 s (`stepMoment`, hash keeps updating → still linkable). ±1 step
  is prefetched after each moment lands, so playback is mostly cache hits;
  `camera` keeps a 600-entry LRU of proxied frames so the card and its pin
  share one source fetch.
- **Ambient globe**: `lib/sun.ts` gives the subsolar point; a 2D canvas over
  cobe shades the night hemisphere (`drawNight`, one ellipse-bounded fill per
  twilight step, redrawn inside the existing rAF loop). Time travel has no
  single terminator (every camera is at the same local clock) so the whole
  globe is tinted by `duskiness(minutes)` via cobe's colour params instead.
  Pins pop when their frame arrives; hovering shows place · time.
- **24 h time-lapse** on the archive card: ≤48 frames at w=640 preloaded,
  `<img>` swaps at 6 fps, scrub slider. No video decoding.
- **Story mode** `#/show`: full-bleed footage with a slow Ken Burns drift,
  caption rising in, inverted-colour globe inset flying to the camera, 8 s a
  sight; Esc, click or any printable key exits (not F11/modifiers). Starts itself after 90 s idle on broadcast (booth).
- Keyboard (`←/→`, `1–4`, `space`, `?`), share link, OG/Twitter tags,
  content-shaped skeleton while `/feed` loads.

**Visual direction from here.** The Apple-ish base (grey page, white cards,
one accent per sight) stays because it keeps the footage the loudest thing on
screen, but the show layout is the first step toward a darker, editorial
register: black room, big serif-free headline, hairline progress. Candidates
next: a sky-coloured page gradient driven by the same `duskiness` (dawn →
day → dusk) so the whole site breathes with the time slider; a "tonight in
numbers" strip (cameras watched, sights verified, rarest sight) above the
hero; per-sight colour washes behind the card; a small terminator legend on
the globe. Keep to CSS vars + one canvas — nothing per-frame in React.

### Iteration 5 (only if time)

- Event evidence panel under the hero (radar/satellite tile from the weather
  half, "why it's rare" text) — needs the weather half to expose a tile URL.
- Share link `/e/{event_id}` → deep-links to that footage (React Router,
  one route).
- PWA manifest + Web Push for the Long Lake story (skipped in iteration 2
  on purpose; ~1 h when needed).

---

## 4. Design system (fix once, then only touch vars)

```css
:root {
  --bg: #f5f5f7;
  --surface: #ffffff;
  --ink: #1d1d1f;
  --ink-2: #6e6e73;
  --line: #e5e5ea;
  --radius: 18px;
  --font: -apple-system, 'Helvetica Neue', Helvetica, Inter, Arial, sans-serif;
  /* one muted accent per event type, used only on the chip + globe ring */
  --c-sunrise: #f2a65a;
  --c-sunset: #e07a5f;
  --c-thunderstorm: #5b6c8f;
  --c-lightning: #f4d35e;
  --c-mammatus: #9b8fb8;
  --c-lenticular: #7fb3c8;
  --c-fog: #a3b1b8;
  --c-undercast: #a3b1b8;
  --c-aurora: #6fcf97;
  --c-rainbow: #d98ccf;
}
```

Type scale: 13 / 15 / 17 / 28 (page title). Weights 400/600 only. Shadows:
one soft `0 8px 30px rgb(0 0 0 / .08)` on the hero. No borders on media.

---

## 5. Repo layout

```
frontend/
  PLAN.md                     ← this file
  ../docs/diagrams/frontend-schema.svg
  index.html
  src/
    main.tsx  App.tsx  index.css
    lib/types.ts              ← Footage contract (§2)
    lib/api.ts                ← fetchFeed(), useStream(); fixture fallback
    lib/time.ts               ← relative time, "delay ≈"
    lib/events.ts             ← EVENT_TYPES, labels, colours
    components/
      Header.tsx  EventFilter.tsx
      FootageCard.tsx         ← hero: media + caption + attribution
      media/ImageMedia.tsx  HlsMedia.tsx  IframeMedia.tsx  FootageMedia.tsx
      Cycler.tsx              ← dots + auto-advance
      Loader.tsx              ← weather glyphs
      globe/                  ← iteration 2 (empty for now)
    fixtures/feed.json        ← 4 Footage rows: mammatus Denver, aurora Lapland,
                                undercast Alps, thunderstorm Miami (the 4 replay
                                events the backend plan names)
```

---

## 6. Build order

1. **Scaffold + fixture + `FootageCard` with `image` renderer** — this
   session. Demoable offline.
2. `hls` + `iframe` renderers, refresh loop, time-honesty strings, dropdown
   filter, cycler, loader. — this session if time.
3. `lib/api.ts` against real `/feed` + SSE; `VITE_API_BASE` switch; Vite dev
   proxy. — as soon as the routing Devin has step 2 of _their_ build order
   ("proxy routes + store + SSE").
4. Globe (§3 iteration 2).
5. Time travel (§3 iteration 3) + `/proxy/history` in `camera`.
6. Evidence panel / share link / PWA (PWA deferred).

---

## 7. Open questions for the team

- Backend: add `event.{type,place,rarity}` to `/feed` rows and a
  `cameras.geojson` dump (§2). Both are one-liners on their side; blocking
  for the globe, not for iteration 1.
- Which host serves the SPA in the demo? Proposal: FastAPI mounts
  `frontend/dist` at `/` so there is one origin and zero CORS work.
- Attribution copy for ALERTCalifornia (CC BY-NC-ND) — must be visible on
  the frame, not just in a tooltip. Card already reserves the attribution
  row for this.
