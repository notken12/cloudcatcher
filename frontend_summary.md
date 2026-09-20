# sunroof · frontend — work summary

What has been built in `frontend/` (three PRs on hackathon day), the libraries it uses, and why
each design choice was made. The living design doc is `frontend/PLAN.md` (every PR points at the
section it implements; diagram `frontend/docs/frontend-schema.svg`); `frontend/README.md` has the
run commands. This file is the map.

```
frontend/  (~930 lines TS/TSX, Vite + React 19 + TypeScript + Tailwind v4)
  src/App.tsx                 app state: selected footage, event-type filter, layout
  src/lib/types.ts            the Footage contract (copied verbatim from camera/docs)
  src/lib/api.ts              fixture ⇄ live switch, /feed, /cameras.geojson, SSE /stream
  src/lib/view.ts             hash routes: #/ (broadcast) · #/globe · #/time/<YYYY-MM-DDTHH:MM>
  src/lib/archive.ts          25 hand-picked archive cameras + history URL builder
  src/lib/events.ts, time.ts  event labels/colours, "3 min ago" / delay strings
  src/components/FootageCard, Header, Cycler, Loader
  src/components/media/       ImageMedia · HlsMedia · IframeMedia · FootageMedia (switch)
  src/components/globe/       Globe.tsx (cobe canvas + DOM pins), projection.ts
  src/components/time/        ArchiveCard, TimeControls
  src/fixtures/               feed.json · cameras.json · archive-cams.json (offline demo data)
```

---

## 1. What it does

A minimalist, Apple-light-theme web page that shows *live footage of rare sky events happening
right now*, with three layouts behind one header toggle (Tv / Earth / Clock):

| layout | route | what you see |
|---|---|---|
| **Broadcast** | `#/` | one hero `FootageCard` (image / HLS / iframe), event label + place + "captured N s ago", VLM caption, camera attribution; event-type filter chips; auto-cycles every 20 s (pauses on hover) |
| **Globe** | `#/globe` | dotted WebGL earth with every catalogued camera as a dot and each live footage item as a clickable thumbnail pin; clicking flies to it and shows the card |
| **Time travel** | `#/time/2025-09-20T18:00` | pick a date + camera-local time (defaults to a year ago, an hour before sunset) and see what 25 archive-capable cameras looked like then |

Two data modes, switched by one env var: `VITE_API_BASE` unset → **fixture mode** (fully offline,
`src/fixtures/*.json`); set to the camera service (`http://localhost:8000`) → live `/feed`,
`/stream`, `/cameras.geojson`, `/proxy/frame`, `/proxy/history`.

---

## 2. Libraries and why

| library | used for | why this one |
|---|---|---|
| **React 19 + TypeScript** | UI; TS pins the `Footage` contract in `src/lib/types.ts` | team knows React; the contract file *is* the diff to review when the backend changes |
| **Vite 6** | dev server, build, lazy chunks | no SSR or route handlers are needed — the camera FastAPI already owns `/feed`, `/stream`, `/proxy/*` — so a static SPA (Vercel/Netlify/S3, or served by FastAPI itself) beats Next.js; faster dev loop |
| **Tailwind CSS v4** (`@tailwindcss/vite`) + a few CSS variables in `index.css` | the light neutral theme in one place | "change the colour of the bar" is one variable; no component library to fight |
| **TanStack Query v5** | `/feed` (stale 30 s, refetch 60 s live), `/cameras.geojson` (stale ∞) | cache/retry/stale handling for free; the SSE handler just invalidates the `feed` query |
| **`EventSource`** (browser built-in) | `GET /stream` — any message means some event's footage changed → refetch | SSE stays ~15 lines; no socket library |
| **hls.js** (lazy `import()`) | `media.kind === 'hls'` DOT/511/coastal streams on a `<video>`; Safari uses native HLS | the backend's recommended renderer; lazy so the image-only path pays nothing (179 kB gzip chunk loads only when a stream is shown vs 86 kB main bundle) |
| **cobe v2** (lazy chunk) | the globe canvas: dotted earth, camera dots as markers, rotation | ~5 kB gzip, zero deps, dotted-light look that matches the theme almost exactly. **Chosen over `react-globe.gl`** (~570 kB gzip + a texture) after measuring both builds; we only need dots, pins, rotate, click, fly-to — not zoom/terrain/hit-testing |
| own `projection.ts` | lat/lon → screen for **DOM pins** over the canvas | cobe has no hit-testing or labels, so pins are real HTML buttons (accessible, clickable, styled with CSS) positioned with the same rotation math cobe uses; hidden when on the far side |
| **lucide-react** | icons (Tv / Earth / Clock toggle, etc.) | thin-line, neutral |
| **Vitest + Testing Library + jsdom** | `FootageCard` + renderer switch, `events`, `view` (hash parsing), `archive` (URL building), globe `projection` | cheap unit tests that guard the contract and the pure helpers |
| **oxlint + Prettier** | `pnpm lint` | Vite template default; fast |

Deliberately **not** used: React Native / Expo (see §3), Next.js, a state library (Zustand only if
the globe made prop-drilling ugly — it didn't), a component kit, `react-globe.gl`/CesiumJS.

---

## 3. Design decisions and why

**Web, not React Native (PLAN §1).** The brief said "web page"; judges see it on a laptop or
projector; and the three hard requirements all fight RN: HLS (`hls.js` on `<video>` vs
`react-native-video` + RN-web fallbacks), `iframe` embeds required by some sources' ToS (no RN-web
iframe primitive), a WebGL globe (no globe.gl port), SSE (`EventSource` is built in), and
Apple-light polish (real CSS/fonts). A phone demo is covered by the responsive layout; a PWA
manifest + Web Push is the planned iOS path (separate session), not an RN app.

**The frontend renders exactly one object: `Footage`.** The camera backend resolves sources,
proxies frames, runs the VLM, and emits `{event, camera, media:{kind, src, refresh_s, poster},
verdict, frame_ts, ts_source, hold_until}`. The frontend has three renderers keyed on
`media.kind` and nothing per-source or per-event-type beyond a label and a colour
(`lib/events.ts`). Media URLs are always the backend's proxy (`mediaUrl()` prefixes relative
`/proxy/...` with `VITE_API_BASE`), so the browser never touches a camera host (CORS, hot-linking,
attribution all handled server-side).

**Fixture first.** `feed.json` mirrors `GET /feed`, `cameras.json` is a synthetic point cloud,
`archive-cams.json` are real archive cameras with direct URL templates. This let the frontend be
built and browser-tested (all three layouts, desktop + 420 px mobile, via the testing agent)
while the camera catalog and service were still being built in parallel sessions, and makes the
demo immune to camera hosts being down. Flipping one env var goes live.

**App state is "selected footage + filter + layout", lifted to `App`.** The broadcast card was
built as a component that takes a `Footage`; the globe is just a second way to set `selected`,
and time travel swaps the card for `ArchiveCard`. Layout is a hash route (`lib/view.ts`,
`useSyncExternalStore` on `hashchange`) so views and time-travel moments are shareable URLs
without a router dependency.

**Globe as a mode, not a separate page**, measured first: the user asked "how much overhead?"
before iteration 2; the answer (86 kB gzip main today; cobe adds ~5 kB as a lazy chunk;
`react-globe.gl` would add ~570 kB) drove the library choice. Implementation details that
mattered: mutable render state (rotation, drag, fly-to target) lives in refs updated in cobe's
`onRender`, React state only for selection; `ResizeObserver` for canvas sizing; animation pauses
when the tab is hidden; pointer drag; auto-rotation resumes after ~4 s idle. One bug found
in browser testing (pin depth/visibility on the far side) was fixed in the same PR.

**Time travel (PLAN + PR #31) built on what the sources actually allow.** Most live cameras
serve only the current frame, so the view uses a hand-picked set of 25 verified archive cameras
(foto-webcam.eu strftime templates, PhenoCam, IEM) rather than pretending every camera has
history. Time is *camera-local* (date + minutes since midnight), defaulting to an hour before
sunset a year ago — the most photogenic moment — and the globe from iteration 2 is reused for
picking. In fixture mode only cameras with direct templates are shown; in live mode all 25 go
through the backend's `/proxy/history/{id}?ts=&w=` (Pillow-downscaled, browse-page lookup for
sources without templates).

**Design system (PLAN §4):** light neutral palette, one accent per event type, Helvetica/system
font stack, 16:9 hero card with soft shadow, generous whitespace; controls are plain buttons and
chips — no modal, no sidebar, so a projector audience reads it at a glance.

---

## 4. Timeline

| PR | what | decision |
|---|---|---|
| #8 | iteration 1: plan (`PLAN.md`), broadcast view, three renderers, fixture mode, tests, CI-green build; browser-tested in fixture mode | web over RN; Vite over Next; universal `Footage`; fixture first |
| #25 | iteration 2: cobe globe, camera dots, thumbnail pins, header Tv/Earth toggle, `#/globe` | cobe over react-globe.gl after measuring; DOM pins over canvas |
| #31 | iteration 3: time travel (`#/time/...`), year/date/local-time controls, 25 archive cams, backend `/proxy/history` | archive set hand-picked; camera-local time; reuse globe |
| #20 (shared) | `fog` removed from `EVENT_TYPES` and fixtures | user decision |
| #33 | repo skill `.agents/skills/frontend-fixture-testing` for repeatable browser tests | |

Iteration 1 initially targeted a separate `notken12/frontend` repo that Devin could not see; it
landed as `sunroof/frontend` instead, which also keeps the contract file next to its source of truth.

---

## 5. Commands and checks

```sh
cd frontend
pnpm install
pnpm dev                                   # fixture mode → http://localhost:5173 (#/globe, #/time)
VITE_API_BASE=http://localhost:8000 pnpm dev   # live, with `uv run sunroof-camera serve` running
pnpm test && pnpm lint && pnpm typecheck && pnpm build
```

There is no frontend CI workflow yet (only `camera.yml`); checks are run locally / by the testing
agent using the `frontend-fixture-testing` skill.

---

## 6. Known gaps / proposed next steps

- Backend integration is verified against the contract and fixtures, not yet against a long live
  run with the VLM on. The frontend only reads `/feed` (+ SSE invalidation); per-event
  `/events/{id}/footage` is not used.
- `hold_until` / `expires_at` are not enforced (no automatic hide of expired footage).
- No frontend CI; no PWA manifest yet (planned as the iOS/push path).
- Reviewed improvement list (routing session, 2026-09-20): richer event detail panel, time-lapse
  of recent frames per camera, globe day/night terminator and event heat, keyboard navigation,
  skeleton loading, and a "why this camera" panel surfacing the backend's `why`/verdict fields —
  none require a backend contract change.
