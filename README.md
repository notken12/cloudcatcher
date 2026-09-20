# skylight

Live footage of rare sky events, worldwide (HackMIT). The weather side detects events
(storms, aurora, sunsets, …) from live data; the camera side finds public webcams that can see
them, fetches a frame, checks it (deterministic gates + VLM) and streams the result to a web
frontend.

```
weather/  ──events.json──►  camera/  ──/feed, SSE, /proxy──►  frontend/
detect events                match cameras · fetch · gate · VLM   broadcast · globe · time travel
```

## Layout

| dir | what | docs |
|---|---|---|
| `weather/` | data clients (MRMS/ProbSevere, GLM, GOES/Himawari cloud products, HRRR/GFS, NWS, SPC, SYNOP, SWPC aurora, ERA5) and the worldwide event detector `weather.events` | `weather/README.md`, `validation/REPORT.md` |
| `camera/` | camera catalog (42k cameras, 53 keyless sources → `cameras.parquet`), sky-coverage query, health probe, FastAPI service, VLM gate, SQLite event store, cron scheduler | `camera/README.md`, **`camera_summary.md`** |
| `frontend/` | Vite + React SPA: broadcast card, cobe globe, time-travel archive view; fixture mode needs no backend | `frontend/README.md`, **`frontend_summary.md`** |
| `camera_ken/` | earlier camera clients (FAA, Windy, PhenoCam) and image heuristics (sky fraction, colour index) | `camera_ken/README.md` |
| `common/` | shared geo helpers | |
| `validation/` | data-source validation run, sample events, evaluation scripts | `validation/REPORT.md` |
| `docs/diagrams/` | architecture diagrams (preprocessing, query/routing, frontend; overview + detailed) | |

`camera_summary.md` / `frontend_summary.md` explain the libraries used and why each design
choice was made.

## Run it

```sh
# weather + camera_ken (repo root)
uv sync                       # add --extra sky for the SegFormer sky-fraction filter (torch)
uv run python -m weather.events --out out/events.json --sunset --aurora

# camera service (own uv project)
cd camera && uv sync --extra dev
uv run sunroof-camera refresh                 # build cameras.parquet
uv run sunroof-camera health --sample 2000    # mark live/stale/dead
uv run sunroof-camera serve --fake-events     # API + sandbox page on :8080
uv run sunroof-camera cron --db data/events.db  # weather every 30 min, footage every 5 min

# frontend
cd frontend && pnpm install
pnpm dev                                      # fixture mode, http://localhost:5173 (#/globe, #/time)
VITE_API_BASE=http://localhost:8080 pnpm dev  # live against the camera service
VITE_API_BASE=/ pnpm build                    # same-origin build for `sunroof-camera serve --frontend frontend/dist`
```

Keys (all optional, in `camera/.env` — see `camera/.env.example`): `OPENAI_API_KEY` (VLM;
`GROQ_API_KEY` or `SUNROOF_VLM_BACKEND=off` are alternatives), `WINDY_API_KEY` (global filler
cameras). Everything else is keyless.

Checks: `cd camera && uv run ruff check src tests && uv run pytest -q`;
`cd frontend && pnpm test && pnpm lint && pnpm typecheck && pnpm build`.
