---
name: sunroof-fixture-testing
description: Browser testing of Sunroof broadcast, globe, and archive layouts without a backend.
---

# Fixture setup
- Use the canonical repository's `frontend` directory.
- Install with `pnpm install --frozen-lockfile` if needed.
- Run `pnpm dev -- --port 5199` with `VITE_API_BASE` unset. Before reusing a
  server, verify its process working directory and environment.
- No backend/catalog is required for fixture mode. Run `pnpm test` separately.

## Devin Secrets Needed
None for fixture mode. Public archive domains need internet access.

# Runtime checks
- Tv uses no hash, Earth uses `#/globe`, History uses `#/time` or
  `#/time/YYYY-MM-DDTHH:MM`.
- Time mode uses eight directly loadable cameras; remaining catalog cameras
  require the backend. Do not claim their coverage.
- Known historical example: `#/time/2019-08-01T18:00`.
- Foto-webcam URLs should contain `/2019/08/01/1800_la.jpg`.
- Iowa UTC-keyed URLs for that moment use `201908020000.jpg`.
- A camera's `since` year gates availability; Husum before2020 should render
  `archive starts in 2020`.
- Real missing images may return404 or422. Assert striped `.pin-empty`,
  no broken thumbnail, and informative card text; do not mock a failed frame.
- Check native date keyboard entry as well as calendar/range controls.
  Test both out-of-range intermediate years and typing a complete valid year:
  rejecting transient edits can inadvertently prevent completing the year.
- Verify non-first footage survives Tv/Earth toggles with All sights;
  a single-result filter can conceal selection-reset regressions.
- Single-result views intentionally have no cycler dots.
- Time cycling is8s; footage cycling is20s. Hover pauses cycling.

# Measurements and visual evidence
- Instrument input/hash events for debounce timing (~350ms).
- Measure pin bounding-box center against canvas center, not transform anchor.
- Use screenshot evidence for visible state and DOM for URL/timing/geometry.
- Maximize Chrome. Use DevTools responsive emulation for420px rather than
  changing the desktop resolution, which can disrupt screenshot coordinates.
- Native date inputs have separate month/day/year segments; Tab through them
  if coordinate focus is ambiguous. Emulated touch clicks may not focus ranges
  for subsequent keyboard actions.
- Distinguish React errors from external media404/422/502 and software-WebGL
  notices. HLS fallback is not proof of successful playback.
