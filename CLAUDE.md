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

### Environment Variables

Required:
- `FIRMS_KEY` — NASA FIRMS API key for wildfire data
- `MAPBOX_TOKEN` — Mapbox GL JS access token
- `PORT` — set automatically by Render

Optional (features degrade silently if missing):
- `AIRNOW_KEY` — EPA AirNow API key for air quality layer (free at airnowapi.org)
- `SENDGRID_API_KEY` — SendGrid key for email alerts
- `ALERT_EMAIL` — destination address for alert emails
- `GROQ_API_KEY` — Groq key for AI situation reports (llama-3.3-70b-versatile)

Missing required keys are logged as warnings at startup. Missing optional keys silently disable the relevant feature.

---

## Architecture

Everything lives in a single file: `app.py` (~4400 lines). There are no modules, no tests, no build step.

### Two map views

| Route | Stack | Purpose |
|-------|-------|---------|
| `/` | Dash + Folium | Legacy dashboard with charts, stat cards, county table, embedded Folium map |
| `/mapbox` | Mapbox GL JS (served as raw HTML from Flask) | Flagship full-screen map with glassmorphism dark UI — **this is the primary view** |

The Mapbox page now includes everything: stat cards, AI situation report, hazard overview chart, layer toggles, and the location threat analysis panel.

The Mapbox page is a large f-string returned by the `mapbox_map()` Flask route. All its CSS, HTML, and JavaScript live inside that string. Because it is an f-string, literal `{` and `}` must be written as `{{` and `}}` throughout the JS/CSS.

### Data flow

1. **Background thread** (`schedule_updates`, every 30 min) calls all `fetch_*` functions and stores results in the global `state` dict.
2. **Cache** (`/tmp/hazard_cache.json`) is written after each update and loaded at startup, so the app serves data immediately on first request.
3. **Flask API endpoints** read directly from `state` and return GeoJSON or JSON.
4. **Mapbox JS** in the browser calls these endpoints to populate map layers. `loadData()` (called every 5 min) refreshes all sources via `setData()`.
5. **Dash callback** (`update_ui`) fires on a `dcc.Interval` and re-renders the Folium map, charts, and table from `state`.

### Flask API endpoints

| Endpoint | Source | Returns |
|----------|--------|---------|
| `/api/warnings` | NWS | Active watches/warnings/advisories (GeoJSON) |
| `/api/spc` | NOAA SPC | Convective outlook zones (GeoJSON) |
| `/api/earthquakes` | USGS | M2.5+ earthquakes past 24h (GeoJSON) |
| `/api/fires` | NASA FIRMS | Satellite fire detections (GeoJSON points) |
| `/api/fire_perimeters` | NIFC/ArcGIS | Active fire perimeter polygons (GeoJSON) |
| `/api/lightning` | Iowa State Mesonet | Storm reports past 6h (GeoJSON) |
| `/api/storms` | NHC | Hurricane cones and track points (GeoJSON) |
| `/api/counties` | Plotly/Census | Affected county polygons with population (GeoJSON) |
| `/api/infrastructure` | OpenStreetMap Overpass | Hospitals, fire stations, power plants, schools near warnings (GeoJSON) |
| `/api/summary` | state dict | Hazard counts and last update time (JSON) — includes `river_gauges`, `volcanoes`, `drought` counts |
| `/api/air_quality` | EPA AirNow | AQI monitoring station readings (GeoJSON) |
| `/api/fema_disasters` | FEMA OpenFEMA | Active disaster declarations last 60 days, state-level (GeoJSON) |
| `/api/river_gauges` | NOAA AHPS ArcGIS | River gauges at/above flood stage (GeoJSON) |
| `/api/volcanoes` | GDACS | Volcano Orange/Red alert events (GeoJSON) |
| `/api/drought` | UNL Drought Monitor | Drought severity polygons D0–D4 (GeoJSON) |
| `/api/shelters` | FEMA NSS ArcGIS | Open emergency shelters (GeoJSON) |
| `/api/sitrep` | Groq llama-3.3-70b | AI situation report as JSON `{text, raw}` |

### Key globals

- `state` — shared dict between the update thread and all request handlers. Contains raw GeoJSON for each data source, the rendered Folium map HTML, and the summary stats.
- `state_lock` — `threading.RLock()` that must wrap all multi-key writes to `state` and the infrastructure cache reads/writes. Single-key reads are GIL-safe and do not need the lock.
- `_started` / `_started_lock` — guards the background thread so it only starts once.
- `STATE_CENTROIDS` — dict of state abbreviation → [lat, lon] used to plot FEMA disaster declarations.

### Thread safety

`state_lock = threading.RLock()` protects against race conditions between the background update thread and Flask request handlers. Rules:
- **Always lock** `state.update({...})` calls in `run_update()`.
- **Always lock** reads and writes to `state["infra_cache_key"]` and `state["infra_features"]` in `api_infrastructure`.
- **Single-key reads** (`state["warnings"]`) in API endpoints are GIL-safe — no lock needed.

### Static assets

- `static/nri_counties.json` — pre-built FEMA National Risk Index lookup table (county → risk scores). Served at `/static/nri_counties.json` and loaded client-side for the Location Threat Analysis panel. **Do not regenerate this file lightly** — it is 1.3 MB.
- `nri_counties.json` at repo root is the source file used to build the static copy.

### Threat scoring (Location Threat Analysis)

The search panel computes two independent scores shown together:

1. **Real-time threat score (0–100)** — computed client-side in `runSearch()` using `THREAT_WEIGHTS` constants and a distance-decay function. Responds to currently active hazards within the search radius. This is the primary score.
2. **FEMA NRI panel** — loaded from `static/nri_counties.json`. Shows long-term baseline risk (expected annual loss) for the searched county. This is static/historical context — it does NOT update based on active threats. Updated by FEMA annually.

Keep both. They answer different questions: "what's happening right now" vs "what's this area's historical risk profile."

### Infrastructure API note

`/api/infrastructure` calls the Overpass API (OpenStreetMap) at request time. It has an in-memory cache keyed by the bounding box of current warnings. This endpoint can be slow (up to 20s) on the first call after warnings change. Timeout is 20s.

### Fire perimeter note

The NIFC/ArcGIS endpoints return a mix of Polygon features (actual mapped perimeters) and Point features (incident location markers). Only `Polygon` and `MultiPolygon` geometry types are passed through to the API — Points are filtered out because they cannot render as fill layers and appear as invisible dots.

The primary NIFC endpoint (`WFIGS_Interagency_Perimeters_YTD`) returns 400 errors in the off-season (January–April). This is expected — the service is in maintenance and will return data when fire season starts.

### Data source URLs (confirmed working as of April 2026)

Several URLs were replaced after the originals went dead:
- **River gauges**: `mapservices.weather.noaa.gov/eventdriven/rest/services/water/riv_gauges/MapServer/0/query` (replaced dead waterwatch.usgs.gov)
- **Volcanoes**: `gdacs.org/gdacsapi/api/events/geteventlist/MAP?eventlist=VO` (USGS has no public JSON API — all URLs return HTML)
- **Drought**: `droughtmonitor.unl.edu/data/json/usdm_current.json` (replaced broken ArcGIS URL)
- **FEMA Disasters**: OData filter uses `params={}` dict with single-quoted date value `declarationDate ge '2026-02-05'`

### AirNow gotcha

`AIRNOW_KEY` must be `.strip()`-ped when reading from env — Render stores it with a trailing `\n` which becomes `%0A` in the URL and causes 401s. The fix is already in place at the env var assignment line.

AirNow sometimes returns `"Category": 2` (integer) instead of `"Category": {"Name": "Good", "Number": 2}`. The parsing code guards against this with `isinstance(cat_raw, dict)`.

### Email alerts note

`send_alert_email()` uses `ALERT_EMAIL` as both the `from_email` and `to_emails`. Do not change `from_email` to a hardcoded domain unless that domain is verified in SendGrid — SendGrid returns 403 for unverified senders.

### Mapbox page UI components

The Mapbox page (`mapbox_map()` f-string) includes:
- **Stat cards** (top-left) — 6 cards updated by `loadData()` every 5 min
- **Hazard overview chart** (top-left, next to stat cards) — Chart.js bar chart showing counts from `/api/summary`
- **Layer toggles** (right side, scrollable) — `makeToggle(label, layerId, defaultOn)` where `layerId` can be a string OR array of strings. Toggle panel has `max-height: calc(100vh - 280px)` to prevent overlap with the search panel.
- **Location Threat Analysis** (bottom-right) — address search with radius slider, real-time threat score, FEMA NRI panel
- **AI Situation Report** — button in header calls `/api/sitrep`, displays modal with copy-to-clipboard
- **Legend** (bottom-left) — collapsible

### Dependency notes

- `gevent` monkey-patch must happen before all other imports (first lines of `app.py`) to fix SSL recursion issues with gunicorn gevent workers.
- `folium` is only used for the Dash layout map, not the Mapbox page.
- `groq` is pinned to `>=0.4.0,<2.0.0` — do not unpin.
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
- Confirm all new env vars are documented in this file and `.env.example`
- Run `python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"` to catch syntax errors before committing

### 5. Adding a New Data Source

Pattern for every new data source (all must degrade silently if the API is down):

1. Write `fetch_X()` — returns GeoJSON FeatureCollection, prints count, catches all exceptions
2. Add `"X": {"type": "FeatureCollection", "features": []}` to `state` dict initializer
3. Call `fetch_X()` in `run_update()` and include in `state.update({...})` (inside `state_lock`)
4. Include in `save_cache({...})` dict
5. Add `@app.server.route("/api/X")` Flask endpoint
6. Add Mapbox source + layer(s) in the f-string (remember `{{` `}}` escaping)
7. Add source name to the `loadData()` refresh array
8. Add toggle button via `makeToggle()`
9. Document the endpoint in the API table above

### 6. Demand Elegance

Prefer the smallest change that achieves the goal:
- CSS-only solutions over Python layout changes when possible
- Clientside callbacks over server round-trips for pure UI state
- No new files, abstractions, or helpers unless clearly necessary
- No backwards-compatibility shims — just change the code

### 7. Autonomous Bug Fixing

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
