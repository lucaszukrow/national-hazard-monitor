# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
pip install -r requirements.txt
python3 app.py          # runs on http://localhost:8050
```

The app also exposes a Mapbox map at `/mapbox` (the flagship UI).

On Render.com, the server is started via the Procfile:
```
gunicorn app:server --worker-class gevent --workers 1 --timeout 120
```

Required environment variables:
- `FIRMS_KEY` — NASA FIRMS API key for wildfire data
- `MAPBOX_TOKEN` — Mapbox GL JS access token
- `PORT` — set automatically by Render

## Architecture

Everything lives in a single file: `app.py` (~2700 lines). There are no modules, no tests, no build step.

### Two map views

| Route | Stack | Purpose |
|-------|-------|---------|
| `/` | Dash + Folium | Dashboard with charts, stat cards, county table, embedded Folium map |
| `/mapbox` | Mapbox GL JS (served as raw HTML from Flask) | Flagship full-screen map with glassmorphism dark UI |

The Mapbox page is a large f-string returned by the `mapbox_map()` Flask route. All its CSS, HTML, and JavaScript live inside that string. Because it is an f-string, literal `{` and `}` must be written as `{{` and `}}` throughout the JS/CSS.

### Data flow

1. **Background thread** (`schedule_updates`, every 30 min) calls all `fetch_*` functions and stores results in the global `state` dict.
2. **Cache** (`/tmp/hazard_cache.json`) is written after each update and loaded at startup, so the app serves data immediately on first request.
3. **Flask API endpoints** (`/api/warnings`, `/api/spc`, `/api/earthquakes`, `/api/fires`, `/api/fire_perimeters`, `/api/lightning`, `/api/storms`, `/api/counties`, `/api/infrastructure`, `/api/summary`) read directly from `state` and return GeoJSON or JSON.
4. **Mapbox JS** in the browser calls these endpoints to populate map layers. `loadData()` (called every 5 min) refreshes all sources via `setData()`.
5. **Dash callback** (`update_ui`) fires on a `dcc.Interval` and re-renders the Folium map, charts, and table from `state`.

### Key globals

- `state` — shared dict between the update thread and all request handlers. Contains raw GeoJSON for each data source, the rendered Folium map HTML, and the summary stats.
- `_started` / `_started_lock` — guards the background thread so it only starts once.
- `FIRMS_KEY`, `MAPBOX_TOKEN` — read from env at module load.

### Static assets

- `static/nri_counties.json` — pre-built FEMA National Risk Index lookup table (county → risk scores). Served at `/static/nri_counties.json` and loaded client-side for the Location Threat Analysis panel. **Do not regenerate this file lightly** — it is 1.3 MB.
- `nri_counties.json` at repo root is the source file used to build the static copy.

### Infrastructure API note

`/api/infrastructure` calls the Overpass API (OpenStreetMap) at request time. It has an in-memory cache keyed by the bounding box of current warnings. This endpoint can be slow (~15s) on the first call after warnings change.

### Dependency notes

- `gevent` monkey-patch must happen before all other imports (first lines of `app.py`) to fix SSL recursion issues with gunicorn gevent workers.
- `folium` is only used for the Dash layout map, not the Mapbox page.
- No pandas usage despite it being in `requirements.txt`.

---

## Development Strategy

Follow this strategy for every change to `app.py` or any other project file.

### 1. Plan Mode Default

Before writing any code, enter Plan Mode to think through the full approach:
- Identify every file and code section that will be affected
- Anticipate side effects and edge cases (circular callbacks, f-string escaping, Dash component namespaces)
- Confirm the plan looks right before writing a single line

### 2. Subagent Strategy

Use subagents for research and exploration tasks to protect the main context window:
- Spawn an Explore subagent to read and understand large files before editing
- Use subagents in parallel when multiple independent investigations are needed
- Never duplicate work a subagent is already doing

### 3. Self-Improvement Loop

After completing a task, review the result critically:
- Re-read every changed file section to catch regressions, typos, or missed edge cases
- If something looks off, fix it immediately rather than leaving it for the user to find
- Ask: "Is this the simplest correct solution, or did I over-engineer it?"

### 4. Verification Before Done

Before declaring a task complete:
- Mentally trace the execution path end-to-end (data fetch → state → API → JS → UI)
- Check that every new Dash component uses the correct namespace (`dcc.` vs `html.`)
- Check that new f-string content escapes `{` and `}` as `{{` and `}}` inside Mapbox page strings
- Confirm all new env vars are documented in `.env.example`

### 5. Demand Elegance

Prefer the smallest change that achieves the goal:
- CSS-only solutions over Python layout changes when possible
- Clientside callbacks over server round-trips for pure UI state
- No new files, abstractions, or helpers unless clearly necessary
- No backwards-compatibility shims — just change the code

### 6. Autonomous Bug Fixing

When a deploy or runtime error occurs:
- Read the full error message and traceback before touching any code
- Identify the root cause (don't guess — find the exact line)
- Fix only what is broken; don't refactor surrounding code
- Commit the fix with a clear message describing the cause

### Task Management

For every non-trivial change, follow this workflow:

1. **Plan First** — use Plan Mode to map out affected files and steps
2. **Verify Plan** — confirm approach handles edge cases before coding
3. **Track Progress** — use TaskCreate to break work into discrete steps; mark each done immediately
4. **Explain Changes** — write commit messages that explain *why*, not just *what*
5. **Document Results** — update CLAUDE.md if new patterns or gotchas are discovered
6. **Capture Lessons** — save non-obvious insights to memory so future sessions benefit

### Core Principles

- **Simplicity First** — the right solution is the smallest one that works correctly
- **No Laziness** — read the code before touching it; never guess at structure
- **Minimal Impact** — change only what the task requires; leave everything else untouched
