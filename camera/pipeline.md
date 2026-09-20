here will be the pipeline

- `docs/sources.md` — which camera sources exist and what they give us
- `docs/preprocessing-plan.md` — ingest → `cameras.parquet` → `find_cameras()`
- `docs/query-and-routing-plan.md` — per-event query profiles, frame fetch + gating, VLM verdict, routing to the frontend / `NO_FOOTAGE_FOUND` back to the weather backend (diagram: `../docs/diagrams/query-routing-schema.svg`)
