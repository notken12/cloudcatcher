# sunroof · frontend

Something beautiful is happening in the sky right now. Here's a live view.

Web SPA (Vite + React 19 + TypeScript + Tailwind v4) that renders ranked `Footage`
from the [camera backend](../camera).
Design + reasoning live in [PLAN.md](./PLAN.md); architecture in [docs/frontend-schema.svg](./docs/frontend-schema.svg).

```sh
pnpm install
pnpm dev            # fixture mode — no backend needed
VITE_API_BASE=http://localhost:8000 pnpm dev   # live against the camera backend
pnpm test && pnpm lint && pnpm build
```

Three layouts, toggled in the header: broadcast (default), globe (`#/globe`) and
time travel (`#/time`, e.g. `#/time/2023-06-15T18:00`).
The globe needs `GET /cameras.geojson` from the backend for the camera dots; in
fixture mode it uses `src/fixtures/cameras.json` (synthetic points, not real cameras).

Time travel shows the ~25 hand-picked archive cameras in `src/fixtures/archive-cams.json`
at a chosen date + camera-local time of day. Without a backend only the 8 with a
direct URL template (foto-webcam.eu, IEM) load; with `VITE_API_BASE` all of them
go through `GET /proxy/history/{camera_id}?ts=…&w=…` (PhenoCam needs it).

## iOS app (PWA) + notifications

The same build is an installable web app: `public/manifest.webmanifest`, Apple meta
tags in `index.html`, and a push-only service worker `public/sw.js`. On iPhone
(iOS 16.4+): open the site in Safari → Share → **Add to Home Screen** → launch from
the icon. Only then does iOS allow Web Push, so everything personal is gated on
that installed ("standalone") mode and the anonymous desktop/projector view is
unchanged:

- **You** (`#/join`) — name, optional email, and the sights you love. Fixture mode
  keeps the account on the device (`localStorage`); live mode `POST /users`.
- **Bell** — asks for notification permission and `POST /push/subscribe`s the
  device (tied to the user if registered).
- Liked sights come first in the feed and linger 1.5× longer in the broadcast.

To see this on a desktop browser append `?personal` to the URL (or run with
`VITE_PERSONAL=1`). Push needs the backend started with its VAPID key
(`sunroof-camera serve …`, see [camera/README](../camera/README.md)) and, for
real phones, a public **https** origin (a Cloudflare/ngrok tunnel is enough).
