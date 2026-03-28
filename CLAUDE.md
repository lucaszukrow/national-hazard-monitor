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
