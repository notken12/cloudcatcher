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
