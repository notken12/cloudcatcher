# sunroof · frontend

Something beautiful is happening in the sky right now. Here's a live view.

Web SPA (Vite + React 19 + TypeScript + Tailwind v4) that renders ranked `Footage`
from the [camera backend](../camera).
Design + reasoning live in [PLAN.md](./PLAN.md); architecture in [docs/diagrams/frontend-schema.svg](../docs/diagrams/frontend-schema.svg) (`-detailed.svg` for the annotated version).

```sh
pnpm install
pnpm dev            # fixture mode — no backend needed
VITE_API_BASE=http://localhost:8000 pnpm dev   # live against the camera backend
VITE_API_BASE=/ pnpm build                     # same-origin build for `sunroof-camera serve --frontend frontend/dist`
pnpm test && pnpm lint && pnpm build
```

Four layouts, toggled in the header or keys `1–4`: broadcast (default), globe (`#/globe`),
time travel (`#/time`, e.g. `#/time/2023-06-15T18:00`) and story mode (`#/show`, full
screen, 8 s a sight, any key exits; starts itself after 90 s idle on broadcast).
`←/→` cycle sights, `?` lists shortcuts.
The globe needs `GET /cameras.geojson` from the backend for the camera dots; in
fixture mode it uses `src/fixtures/cameras.json` (synthetic points, not real cameras).

Time travel shows the ~25 hand-picked archive cameras in `src/fixtures/archive-cams.json`
at a chosen date + camera-local time of day. Without a backend only the 8 with a
direct URL template (foto-webcam.eu, IEM) load; with `VITE_API_BASE` all of them
go through `GET /proxy/history/{camera_id}?ts=…&w=…` (PhenoCam needs it).

In time travel, Play advances the local clock 30 min every 2 s (every camera passes
through dusk together), `share` copies the link, and `24 h` on the card plays that
camera's whole day (≤48 frames, preloaded).
