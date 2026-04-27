# CRITICAL: gevent monkey patch must happen BEFORE any other imports
# This fixes the "maximum recursion depth exceeded" error with SSL
try:
    from gevent import monkey
    monkey.patch_all()
except ImportError:
    pass

"""
National All-Hazards GIS Monitoring System
Cloud deployment version — no ArcPy required
Runs on Render.com as a Dash web application
Author: Lucas Zukrow | GSP318 | Cal Poly Humboldt
"""

import os
# Load .env for local dev. Silent no-op on Render (no .env file, no dotenv installed is fine).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import json
import csv
import time
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import requests
import folium
from folium.plugins import MiniMap, Fullscreen
import dash
from dash import dcc, html, Input, Output
import plotly.graph_objects as go

# SendGrid — optional; alerting is silently skipped if not installed or not configured
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    _SENDGRID_AVAILABLE = True
except ImportError:
    _SENDGRID_AVAILABLE = False

# Groq — optional; AI situation reports are silently disabled if not installed/configured
try:
    from groq import Groq as _GroqClient
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False

# ─────────────────────────────────────────────
# DATA SOURCE URLs
# ─────────────────────────────────────────────
NWS_URL      = "https://mapservices.weather.noaa.gov/eventdriven/rest/services/WWA/watch_warn_adv/MapServer/0/query?where=1%3D1&outFields=*&f=geojson"
SPC_URL      = "https://www.spc.noaa.gov/products/outlook/day1otlk_cat.nolyr.geojson"
USGS_EQ_URL  = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
NHC_URL      = "https://www.nhc.noaa.gov/CurrentStorms.json"
FIRMS_KEY        = os.environ.get("FIRMS_KEY", "")
# Note: FIRMS URLs are built inside fetch_fires() so FIRMS_KEY is always current
SENDGRID_API_KEY  = os.environ.get("SENDGRID_API_KEY", "")
ALERT_EMAIL       = os.environ.get("ALERT_EMAIL", "")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
AIRNOW_KEY    = os.environ.get("AIRNOW_KEY", "").strip()   # Free key at airnowapi.org

# In-memory subscription store (resets on redeploy; swap for a DB for persistence)
_subscriptions = []  # list of {email, county, lat, lng, radius_miles}
CENSUS_URL    = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/counties/totals/co-est2023-alldata.csv"
COUNTIES_URL  = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"

# State geographic centroids — used to plot FEMA disaster declarations on the map
STATE_CENTROIDS = {
    "AL":[32.81,-86.79],"AK":[61.37,-152.40],"AZ":[33.73,-111.43],"AR":[34.97,-92.37],
    "CA":[36.12,-119.68],"CO":[39.06,-105.31],"CT":[41.60,-72.76],"DE":[39.32,-75.51],
    "FL":[27.77,-81.69],"GA":[33.04,-83.64],"HI":[21.09,-157.50],"ID":[44.24,-114.48],
    "IL":[40.35,-88.99],"IN":[39.85,-86.26],"IA":[42.01,-93.21],"KS":[38.53,-96.73],
    "KY":[37.67,-84.67],"LA":[31.17,-91.87],"ME":[44.69,-69.38],"MD":[39.06,-76.80],
    "MA":[42.23,-71.53],"MI":[43.33,-84.54],"MN":[45.69,-93.90],"MS":[32.74,-89.68],
    "MO":[38.46,-92.29],"MT":[46.92,-110.45],"NE":[41.13,-98.27],"NV":[38.31,-117.06],
    "NH":[43.45,-71.56],"NJ":[40.30,-74.52],"NM":[34.84,-106.25],"NY":[42.17,-74.95],
    "NC":[35.63,-79.81],"ND":[47.53,-99.78],"OH":[40.39,-82.76],"OK":[35.57,-96.93],
    "OR":[44.57,-122.07],"PA":[40.59,-77.21],"RI":[41.68,-71.51],"SC":[33.86,-80.95],
    "SD":[44.30,-99.44],"TN":[35.75,-86.69],"TX":[31.05,-97.56],"UT":[40.15,-111.86],
    "VT":[44.05,-72.71],"VA":[37.77,-78.17],"WA":[47.40,-121.49],"WV":[38.49,-80.95],
    "WI":[44.27,-89.62],"WY":[42.76,-107.30],"PR":[18.22,-66.59],
}

# ─────────────────────────────────────────────
# STARTUP VALIDATION — warn on missing required keys
# ─────────────────────────────────────────────
_missing_keys = []
if not FIRMS_KEY:
    _missing_keys.append("FIRMS_KEY (wildfire data will be unavailable)")
if not os.environ.get("MAPBOX_TOKEN"):
    _missing_keys.append("MAPBOX_TOKEN (/mapbox view will be broken)")
if not AIRNOW_KEY:
    _missing_keys.append("AIRNOW_KEY (air quality layer disabled — free key at airnowapi.org)")
if _missing_keys:
    print("\n⚠  WARNING: Missing environment variables:")
    for k in _missing_keys:
        print(f"   • {k}")
    print("   Set these in your .env file or Render dashboard.\n")

# ─────────────────────────────────────────────
# LOOKUP TABLES
# ─────────────────────────────────────────────
hazard_colors = {
    "TO": "#FF0000", "FF": "#00BFFF", "HU": "#FF6600",
    "TS": "#FF9900", "SV": "#FF6666", "WS": "#AAAAFF",
    "FA": "#0099FF", "FW": "#FF4500", "default": "#FFFF00"
}
spc_colors = {
    "TSTM": "#76FF7A", "MRGL": "#009000", "SLGT": "#FFFF00",
    "ENH":  "#FF9900", "MDT":  "#FF0000", "HIGH": "#FF00FF",
    "default": "#76FF7A"
}
phenom_names = {
    "TO": "Tornado", "SV": "Severe Thunderstorm", "FF": "Flash Flood",
    "FA": "Flood", "HU": "Hurricane", "TS": "Tropical Storm",
    "WS": "Winter Storm", "BZ": "Blizzard", "FW": "Fire Weather",
    "EH": "Excessive Heat", "HW": "High Wind", "CF": "Coastal Flood",
    "SS": "Storm Surge", "MA": "Marine", "DS": "Dust Storm",
    "AV": "Avalanche", "HZ": "Hard Freeze", "FZ": "Freeze",
    "FR": "Frost", "ZR": "Freezing Rain", "EC": "Extreme Cold"
}

# ── Severity classification ──────────────────────────────────────────
# Five tiers used everywhere in the new Command Center UI. Map onto
# the tokens.css palette: --sev-extreme, --sev-severe, --sev-moderate,
# --sev-minor, --sev-info.
PHENOM_SEVERITY = {
    "TO": "extreme", "HU": "extreme", "FF": "extreme", "SS": "extreme",
    "BZ": "severe",  "EH": "severe",  "SV": "severe",  "TS": "severe",
    "WS": "severe",  "FW": "severe",  "HW": "severe",  "EC": "severe",
    "CF": "moderate","FA": "moderate","HZ": "moderate","FZ": "moderate",
    "ZR": "moderate","DS": "moderate","AV": "moderate","MA": "moderate",
    "FR": "minor",
}

def severity_for_phenom(phenom):
    return PHENOM_SEVERITY.get(str(phenom or "").strip().upper(), "minor")

def severity_for_magnitude(mag):
    """USGS earthquake magnitude → severity tier."""
    try:
        m = float(mag or 0)
    except (TypeError, ValueError):
        return "info"
    if m >= 6.0: return "extreme"
    if m >= 5.0: return "severe"
    if m >= 4.0: return "moderate"
    if m >= 3.0: return "minor"
    return "info"

def severity_for_gauge_status(status):
    """NOAA AHPS river-gauge status → severity tier."""
    s = str(status or "").strip().lower()
    return {"major": "extreme", "moderate": "severe",
            "minor": "moderate", "action": "minor"}.get(s, "info")

def severity_for_fire_confidence(conf):
    """FIRMS confidence flag (h/n/l or 0–100) → severity tier."""
    c = str(conf or "").strip().lower()
    if c in ("h", "high"): return "severe"
    if c in ("n", "nominal"): return "moderate"
    if c in ("l", "low"): return "minor"
    try:
        cn = float(c)
        if cn >= 80: return "severe"
        if cn >= 30: return "moderate"
        return "minor"
    except (TypeError, ValueError):
        return "moderate"
state_fips = {
    "01":"Alabama","02":"Alaska","04":"Arizona","05":"Arkansas",
    "06":"California","08":"Colorado","09":"Connecticut","10":"Delaware",
    "11":"District of Columbia","12":"Florida","13":"Georgia","15":"Hawaii",
    "16":"Idaho","17":"Illinois","18":"Indiana","19":"Iowa","20":"Kansas",
    "21":"Kentucky","22":"Louisiana","23":"Maine","24":"Maryland",
    "25":"Massachusetts","26":"Michigan","27":"Minnesota","28":"Mississippi",
    "29":"Missouri","30":"Montana","31":"Nebraska","32":"Nevada",
    "33":"New Hampshire","34":"New Jersey","35":"New Mexico","36":"New York",
    "37":"North Carolina","38":"North Dakota","39":"Ohio","40":"Oklahoma",
    "41":"Oregon","42":"Pennsylvania","44":"Rhode Island","45":"South Carolina",
    "46":"South Dakota","47":"Tennessee","48":"Texas","49":"Utah",
    "50":"Vermont","51":"Virginia","53":"Washington","54":"West Virginia",
    "55":"Wisconsin","56":"Wyoming","72":"Puerto Rico"
}

# ─────────────────────────────────────────────
# CACHE FILE — persists data across restarts
# ─────────────────────────────────────────────
CACHE_FILE = "/tmp/hazard_cache.json"

def save_cache(data):
    """Save state to file so it survives restarts."""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Cache save failed: {e}")

def load_cache():
    """Load cached state from file."""
    try:
        if os.path.exists(CACHE_FILE):
            age = time.time() - os.path.getmtime(CACHE_FILE)
            if age < 3600:  # Use cache if less than 1 hour old
                with open(CACHE_FILE, "r") as f:
                    data = json.load(f)
                print(f"  Loaded cache ({int(age/60)} min old)")
                return data
    except Exception as e:
        print(f"Cache load failed: {e}")
    return None

# ─────────────────────────────────────────────
# GLOBAL STATE — shared between update thread
# and Dash callbacks
# ─────────────────────────────────────────────
state = {
    "last_update":    "Never",
    "warnings":       {"type": "FeatureCollection", "features": []},
    "spc":            {"type": "FeatureCollection", "features": []},
    "earthquakes":    {"type": "FeatureCollection", "features": []},
    "storms":         [],
    "fires":          [],
    "pop_data":       {},
    "counties_geojson": None,
    "summary": {
        "warnings_count": 0,
        "counties_count": 0,
        "total_population": 0,
        "spc_zones": 0,
        "earthquakes": 0,
        "active_storms": 0,
        "wildfires": 0,
        "affected_counties": []
    },
    "map_html": "",
    "updating": False,
    "lightning":        {"type": "FeatureCollection", "features": []},
    "fire_perimeters":  {"type": "FeatureCollection", "features": []},
    "air_quality":      {"type": "FeatureCollection", "features": []},
    "fema_disasters":   {"type": "FeatureCollection", "features": []},
    "river_gauges":     {"type": "FeatureCollection", "features": []},
    "volcanoes":        {"type": "FeatureCollection", "features": []},
    "drought":          {"type": "FeatureCollection", "features": []},
    "shelters":         {"type": "FeatureCollection", "features": []},
    # In-memory only — intentionally not cached so restarts don't suppress alerts
    # for events that are still active when the server comes back up
    "seen_alert_ids": set()
}

# Protects state dict against concurrent reads/writes from the background thread
# and Flask request handlers.
state_lock = threading.RLock()

# Load cache at module level — runs when gunicorn imports app
# This ensures data is available immediately on startup
_startup_cache = load_cache()
if _startup_cache:
    with state_lock:
        state.update(_startup_cache)
    print(f"Startup: loaded cache from {_startup_cache.get('last_update', 'unknown')}")



# ─────────────────────────────────────────────
# DATA DOWNLOAD FUNCTIONS
# ─────────────────────────────────────────────
def fetch_json(url, timeout=20, retries=3):
    """GET url, parse JSON. Retries up to `retries` times with exponential backoff."""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                print(f"  WARNING: {url} failed (attempt {attempt+1}/{retries}), retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"  WARNING: Failed to fetch {url} after {retries} attempts: {e}")
    return None

def fetch_warnings():
    print("Downloading NWS warnings...")
    data = fetch_json(NWS_URL)
    if data:
        print(f"  NWS: {len(data.get('features', []))} warnings")
    return data or {"type": "FeatureCollection", "features": []}

def fetch_spc():
    print("Downloading SPC outlook...")
    data = fetch_json(SPC_URL)
    if data:
        print(f"  SPC: {len(data.get('features', []))} zones")
    return data or {"type": "FeatureCollection", "features": []}

def fetch_earthquakes():
    print("Downloading USGS earthquakes...")
    data = fetch_json(USGS_EQ_URL)
    if data:
        print(f"  USGS: {len(data.get('features', []))} events")
    return data or {"type": "FeatureCollection", "features": []}

def fetch_storms():
    print("Downloading NHC storms...")
    data = fetch_json(NHC_URL)
    if not data:
        return []
    storms = data.get("activeStorms", [])
    print(f"  NHC: {len(storms)} active storms")
    result = []
    for storm in storms:
        wallet = storm.get("wallet", "")
        cone   = fetch_json(f"https://www.nhc.noaa.gov/storm_graphics/api/{wallet}_5day_cone_with_line.json")
        track  = fetch_json(f"https://www.nhc.noaa.gov/storm_graphics/api/{wallet}_5day_pts.json")
        result.append({"name": storm.get("name",""), "cone": cone, "track": track, "info": storm})
    return result

def fetch_fires():
    print("Downloading NASA FIRMS fires...")
    # Combine ALL satellite sources for maximum coverage
    urls = [
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/VIIRS_SNPP_NRT/-125,24,-66,50/2",
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/VIIRS_NOAA20_NRT/-125,24,-66,50/2",
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/MODIS_NRT/-125,24,-66,50/2",
    ]
    all_fires = []
    seen_coords = set()
    for url in urls:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            text = r.text.strip()
            if not text or "error" in text.lower()[:50]:
                continue
            lines = text.split("\n")
            if len(lines) < 2:
                continue
            headers = [h.strip() for h in lines[0].split(",")]
            count = 0
            for line in lines[1:]:
                if not line.strip():
                    continue
                vals = [v.strip() for v in line.split(",")]
                if len(vals) >= len(headers):
                    fire = dict(zip(headers, vals))
                    # Deduplicate by lat/lon rounded to 2 decimals
                    key = (round(float(fire.get("latitude",0)),2),
                           round(float(fire.get("longitude",0)),2))
                    if key not in seen_coords:
                        seen_coords.add(key)
                        all_fires.append(fire)
                        count += 1
            print(f"  FIRMS {url.split('/')[-3]}: +{count} detections")
        except Exception as e:
            print(f"  FIRMS {url.split('/')[-3]} failed: {e}")
            continue
    print(f"  FIRMS total: {len(all_fires)} fire detections")
    return all_fires

def fetch_population():
    print("Loading Census population data...")
    pop = {}
    try:
        r = requests.get(CENSUS_URL, timeout=30)
        r.raise_for_status()
        lines = r.text.split("\n")
        reader = csv.DictReader(lines)
        for row in reader:
            try:
                sf = str(row.get("STATE","")).zfill(2)
                cf = str(row.get("COUNTY","")).zfill(3)
                if cf == "000":
                    continue
                pop[sf+cf] = int(row.get("POPESTIMATE2023", 0))
            except Exception:
                continue
        print(f"  Census: {len(pop)} counties loaded")
    except Exception as e:
        print(f"  WARNING: Census failed: {e}")
    return pop

def point_in_bbox(lon, lat, bounds):
    """Check if a point is inside a bounding box."""
    return (bounds["minlon"] <= lon <= bounds["maxlon"] and
            bounds["minlat"] <= lat <= bounds["maxlat"])

def get_warning_bounds(warnings_geojson):
    """Extract bounding boxes from warning polygons."""
    bounds_list = []
    for feat in warnings_geojson.get("features", []):
        try:
            coords = feat.get("geometry", {}).get("coordinates", [])
            if not coords:
                continue
            all_pts = []
            def flatten(c):
                if isinstance(c[0], (int, float)):
                    all_pts.append(c)
                else:
                    for item in c:
                        flatten(item)
            flatten(coords)
            if all_pts:
                lons = [p[0] for p in all_pts]
                lats = [p[1] for p in all_pts]
                bounds_list.append({
                    "minlon": min(lons), "maxlon": max(lons),
                    "minlat": min(lats), "maxlat": max(lats),
                    "props":  feat.get("properties", {})
                })
        except Exception:
            continue
    return bounds_list

def bboxes_overlap(bbox1, bbox2):
    """Check if two bounding boxes overlap."""
    return not (bbox1["maxlon"] < bbox2["minlon"] or
                bbox1["minlon"] > bbox2["maxlon"] or
                bbox1["maxlat"] < bbox2["minlat"] or
                bbox1["minlat"] > bbox2["maxlat"])

def get_feature_bbox(coords):
    """Get bounding box of a GeoJSON geometry."""
    all_pts = []
    def flatten(c):
        if not c:
            return
        if isinstance(c[0], (int, float)):
            all_pts.append(c)
        else:
            for item in c:
                flatten(item)
    flatten(coords)
    if not all_pts:
        return None
    lons = [p[0] for p in all_pts]
    lats = [p[1] for p in all_pts]
    return {"minlon": min(lons), "maxlon": max(lons),
            "minlat": min(lats), "maxlat": max(lats)}

SIG_RANK = {"W": 3, "A": 2, "Y": 1, "S": 0}   # Warning > Watch > Advisory > Statement


def find_affected_counties(warnings_geojson, pop_data, counties_geojson):
    """Find counties intersecting warning polygons using bbox overlap.

    For counties under multiple overlapping warnings, keep the highest-severity
    warning and count the total number of overlaps so the UI can badge them.
    """
    if not counties_geojson or not warnings_geojson:
        return [], 0

    warning_bounds = get_warning_bounds(warnings_geojson)
    if not warning_bounds:
        return [], 0

    county_hits = {}  # fips -> {"rank", "props", "feat_props", "count"}

    for feat in counties_geojson.get("features", []):
        try:
            fips   = feat.get("id", "")
            if not fips:
                continue
            props  = feat.get("properties", {})
            coords = feat.get("geometry", {}).get("coordinates", [])
            if not coords:
                continue

            county_bbox = get_feature_bbox(coords)
            if not county_bbox:
                continue

            for bounds in warning_bounds:
                if bboxes_overlap(county_bbox, bounds):
                    w_props = bounds["props"]
                    rank    = SIG_RANK.get(str(w_props.get("sig", "")).strip(), 0)
                    entry   = county_hits.get(fips)
                    if entry is None:
                        county_hits[fips] = {
                            "rank": rank, "props": w_props,
                            "feat_props": props, "count": 1
                        }
                    else:
                        entry["count"] += 1
                        if rank > entry["rank"]:
                            entry["rank"]  = rank
                            entry["props"] = w_props
        except Exception:
            continue

    affected = []
    total_pop = 0
    for fips, h in county_hits.items():
        pop = pop_data.get(fips, 0)
        total_pop += pop
        w        = h["props"]
        phenom   = w.get("phenom", "")
        sig      = w.get("sig", "")
        sig_name = {"W":"Warning","A":"Watch","Y":"Advisory","S":"Statement"}.get(str(sig).strip(), str(sig))
        affected.append({
            "county":        h["feat_props"].get("NAME", "Unknown"),
            "state":         state_fips.get(fips[:2], fips[:2]),
            "fips":          fips,
            "population":    pop,
            "phenom":        phenom,
            "sig":           sig_name,
            "severity_rank": h["rank"],
            "warning_count": h["count"],
            "event":         phenom_names.get(str(phenom).strip().upper(), phenom) + " " + sig_name,
        })

    return affected, total_pop

def build_folium_map(warnings, spc, earthquakes, storms, fires):
    """Build the Folium interactive HTML map."""
    m = folium.Map(location=[39.5, -98.35], zoom_start=4,
                   tiles="CartoDB dark_matter", prefer_canvas=True)
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, position="bottomright").add_to(m)

    # NWS Warnings
    if warnings.get("features"):
        wg = folium.FeatureGroup(name=f"NWS Warnings ({len(warnings['features'])})", show=True)
        for feat in warnings["features"]:
            try:
                props  = feat.get("properties", {})
                phenom = props.get("phenom", "") or ""
                color  = hazard_colors.get(str(phenom).strip().upper(), hazard_colors["default"])
                sig    = props.get("sig","")
                event  = phenom_names.get(str(phenom).strip().upper(), phenom)
                sig_name = {"W":"Warning","A":"Watch","Y":"Advisory"}.get(str(sig).strip(), str(sig))
                folium.GeoJson(
                    feat,
                    style_function=lambda f, c=color: {
                        "fillColor": c, "color": "#FFFFFF",
                        "weight": 1, "fillOpacity": 0.5
                    },
                    tooltip=folium.Tooltip(f"{event} {sig_name}"),
                    popup=folium.Popup(
                        f"<div style='font-family:Arial;font-size:13px;min-width:180px;'>"
                        f"<b style='color:{color};'>{event} {sig_name}</b><br>"
                        f"<hr style='margin:4px 0;'>"
                        f"<b>Phenomenon:</b> {phenom}<br>"
                        f"<b>WFO:</b> {props.get('wfo','N/A')}</div>",
                        max_width=220
                    )
                ).add_to(wg)
            except Exception:
                continue
        wg.add_to(m)

    # SPC Outlook
    if spc.get("features"):
        sg = folium.FeatureGroup(name=f"SPC Outlook ({len(spc['features'])} zones)", show=True)
        for feat in spc["features"]:
            try:
                label = feat.get("properties", {}).get("LABEL", "") or ""
                label2 = feat.get("properties", {}).get("LABEL2", "") or ""
                fill  = spc_colors.get(str(label).strip().upper(), spc_colors["default"])
                folium.GeoJson(
                    feat,
                    style_function=lambda f, c=fill: {
                        "fillColor": c, "color": c,
                        "weight": 1.5, "fillOpacity": 0.3, "dashArray": "4 4"
                    },
                    tooltip=folium.Tooltip(f"SPC: {label2 or label}")
                ).add_to(sg)
            except Exception:
                continue
        sg.add_to(m)

    # Earthquakes
    if earthquakes.get("features"):
        eg = folium.FeatureGroup(name=f"Earthquakes M2.5+ ({len(earthquakes['features'])})", show=True)
        for feat in earthquakes["features"]:
            try:
                props  = feat.get("properties", {})
                coords = feat["geometry"]["coordinates"]
                lon, lat = coords[0], coords[1]
                mag   = props.get("mag", 0) or 0
                place = props.get("place", "Unknown")
                color = "#FF0000" if mag >= 5 else "#FF9900" if mag >= 4 else "#FFFF00"
                folium.CircleMarker(
                    location=[lat, lon], radius=max(4, float(mag)*3),
                    color="#FFFFFF", weight=1.5, fill=True,
                    fill_color=color, fill_opacity=0.8,
                    tooltip=folium.Tooltip(f"M{mag} - {place}"),
                    popup=folium.Popup(
                        f"<div style='font-family:Arial;font-size:13px;'>"
                        f"<b>Earthquake M{mag}</b><br>{place}</div>",
                        max_width=200
                    )
                ).add_to(eg)
            except Exception:
                continue
        eg.add_to(m)

    # Wildfires
    if fires:
        fg = folium.FeatureGroup(name=f"Wildfires ({len(fires)})", show=True)
        for fire in fires:
            try:
                lat  = float(fire.get("latitude", 0))
                lon  = float(fire.get("longitude", 0))
                frp  = float(fire.get("frp", 1))
                acq  = fire.get("acq_date", "")
                if lat == 0 and lon == 0:
                    continue
                dot_color = "#FF0000" if frp > 100 else "#FF4500" if frp > 20 else "#FF8C00"
                folium.CircleMarker(
                    location=[lat, lon], radius=4,
                    color="#FFD700", weight=1, fill=True,
                    fill_color=dot_color, fill_opacity=0.75,
                    tooltip=folium.Tooltip(f"Fire FRP: {frp} MW")
                ).add_to(fg)
            except Exception:
                continue
        fg.add_to(m)

    # Hurricanes
    for storm in storms:
        name = storm.get("name", "Storm")
        sg   = folium.FeatureGroup(name=f"Hurricane: {name}", show=True)
        cone = storm.get("cone")
        if cone and cone.get("features"):
            for feat in cone["features"]:
                try:
                    folium.GeoJson(feat, style_function=lambda f: {
                        "fillColor": "#FF6600", "color": "#FF6600",
                        "weight": 2, "fillOpacity": 0.2
                    }).add_to(sg)
                except Exception:
                    continue
        track = storm.get("track")
        if track and track.get("features"):
            for feat in track["features"]:
                try:
                    coords = feat["geometry"]["coordinates"]
                    folium.CircleMarker(
                        location=[coords[1], coords[0]], radius=7,
                        color="#FF0000", fill=True, fill_color="#FF6600",
                        fill_opacity=0.9,
                        tooltip=folium.Tooltip(f"{name} track point")
                    ).add_to(sg)
                except Exception:
                    continue
        sg.add_to(m)

    # Legend
    legend_html = """
    <div style="position:fixed;bottom:20px;left:10px;z-index:1000;
        background:rgba(15,15,15,0.92);border:1px solid #666;border-radius:8px;
        padding:10px 14px;font-family:Arial;font-size:11px;color:white;
        display:flex;gap:16px;max-width:520px;">
        <div>
            <b style="color:#AAD4FF;">&#9889; NWS Warnings</b><br><br>
            <span style="color:#FF0000;">&#9608;&#9608;</span> Tornado Warning<br>
            <span style="color:#FF6600;">&#9608;&#9608;</span> Hurricane Warning<br>
            <span style="color:#FF6666;">&#9608;&#9608;</span> Severe T-Storm<br>
            <span style="color:#00BFFF;">&#9608;&#9608;</span> Flash Flood<br>
            <span style="color:#FFFF00;">&#9608;&#9608;</span> Other<br>
            <br>
            <b style="color:#AAD4FF;">&#9928; SPC Outlook</b><br><br>
            <span style="color:#76FF7A;">&#9608;&#9608;</span> General Thunder<br>
            <span style="color:#FFFF00;">&#9608;&#9608;</span> Slight Risk<br>
            <span style="color:#FF9900;">&#9608;&#9608;</span> Enhanced Risk<br>
            <span style="color:#FF0000;">&#9608;&#9608;</span> Moderate Risk<br>
        </div>
        <div>
            <b style="color:#AAD4FF;">&#128308; Earthquakes</b><br><br>
            <span style="color:#FFFF00;">&#11044;</span> M2.5-3.9<br>
            <span style="color:#FF9900;">&#11044;</span> M4.0-4.9<br>
            <span style="color:#FF0000;">&#11044;</span> M5.0+<br>
            <br>
            <b style="color:#AAD4FF;">&#127755; Hurricanes</b><br><br>
            <span style="color:#FF6600;">&#9608;&#9608;</span> Forecast Cone<br>
            <span style="color:#FF0000;">&#11044;</span> Track Points<br>
            <br>
            <b style="color:#AAD4FF;">&#128293; Wildfires</b><br><br>
            <span style="color:#FF0000;">&#11044;</span> High (&gt;100 MW)<br>
            <span style="color:#FF4500;">&#11044;</span> Medium<br>
            <span style="color:#FF8C00;">&#11044;</span> Low<br>
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    title_html = f"""
    <div style="position:fixed;top:15px;left:50%;transform:translateX(-50%);
        z-index:1000;background:rgba(20,20,20,0.9);border:1px solid #555;
        border-radius:8px;padding:8px 20px;font-family:Arial;color:white;text-align:center;">
        <b style="font-size:16px;">&#127774; National Hazard Monitor</b><br>
        <small style="color:#aaa;">Last updated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</small>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))
    # NEXRAD live radar
    folium.WmsTileLayer(
        url="https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi",
        layers="nexrad-n0r",
        name="NEXRAD Radar (Live)",
        fmt="image/png",
        transparent=True,
        overlay=True,
        control=True,
        show=True,
        attr="Iowa State Mesonet"
    ).add_to(m)

    # GOES infrared satellite
    folium.WmsTileLayer(
        url="https://mesonet.agron.iastate.edu/cgi-bin/wms/goes/conus_ir.cgi",
        layers="goes_conus_ir",
        name="GOES Infrared (Live)",
        fmt="image/png",
        transparent=True,
        overlay=True,
        control=True,
        show=False,
        attr="Iowa State Mesonet"
    ).add_to(m)

    folium.LayerControl(position="topright", collapsed=False).add_to(m)

    return m._repr_html_()

# ─────────────────────────────────────────────
# EMAIL ALERTING (SendGrid)
# Fires on new tornado warnings, hurricane warnings, and M5.0+ earthquakes
# ─────────────────────────────────────────────

def generate_sitrep():
    """Call Groq (llama-3.3-70b-versatile) to write a 3-paragraph emergency situation report.

    Returns (report_text, raw_text_for_clipboard).
    On any failure, raw_text_for_clipboard is None.
    """
    if not GROQ_API_KEY:
        return "API key not configured. Set the GROQ_API_KEY environment variable to enable AI-generated situation reports.", None
    if not _GROQ_AVAILABLE:
        return "The groq package is not installed. Run: pip install groq", None

    s        = state["summary"]
    affected = s.get("affected_counties", [])

    # Top 15 affected areas
    top_areas = "\n".join(
        f"  - {c.get('county','')}, {c.get('state','')} ({c.get('event','')}, sig={c.get('sig','')})"
        for c in affected[:15]
    )

    # Top 5 earthquakes by magnitude
    eq_features = state.get("earthquakes", {}).get("features", [])
    eq_features_sorted = sorted(eq_features, key=lambda f: f.get("properties", {}).get("mag", 0), reverse=True)
    top_eqs = "\n".join(
        f"  - M{f['properties'].get('mag','?')} near {f['properties'].get('place','unknown')}"
        for f in eq_features_sorted[:5]
    )

    # Top 5 flood gauges by severity
    gauge_features = state.get("river_gauges", {}).get("features", [])
    top_gauges = "\n".join(
        f"  - {f['properties'].get('gaugelid','')}: {f['properties'].get('status','')} — {f['properties'].get('location','')}, {f['properties'].get('state','')}"
        for f in gauge_features[:5]
    )

    # Volcano alerts
    vol_features = state.get("volcanoes", {}).get("features", [])
    top_vols = "\n".join(
        f"  - {f['properties'].get('name','')}: alert={f['properties'].get('alertlevel','')}"
        for f in vol_features[:5]
    )

    # AQ summary — count unhealthy stations
    aq_features = state.get("air_quality", {}).get("features", [])
    aq_unhealthy = sum(1 for f in aq_features if (f.get("properties", {}).get("aqi") or 0) > 100)

    context = (
        f"Report time: {state.get('last_update', 'Unknown')} UTC\n"
        f"\n--- ACTIVE HAZARDS ---\n"
        f"NWS warnings/watches/advisories: {s.get('warnings_count', 0)}\n"
        f"Counties under active warnings: {s.get('counties_count', 0)}\n"
        f"Estimated population at risk from warnings: {s.get('total_population', 0):,}\n"
        f"SPC severe weather outlook zones: {s.get('spc_zones', 0)}\n"
        f"Earthquakes M2.5+ (past 24 h): {s.get('earthquakes', 0)}\n"
        f"Active tropical storms / hurricanes: {s.get('active_storms', 0)}\n"
        f"Wildfire satellite detections: {s.get('wildfires', 0)}\n"
        f"River gauges at/above flood stage: {s.get('river_gauges', 0)}\n"
        f"Volcano orange/red alerts: {s.get('volcanoes', 0)}\n"
        f"Drought polygons (D0-D4): {s.get('drought', 0)}\n"
        f"Air quality stations with AQI > 100 (unhealthy): {aq_unhealthy}\n"
        + (f"\nTop NWS-warned areas:\n{top_areas}" if top_areas else "")
        + (f"\nLargest earthquakes:\n{top_eqs}" if top_eqs else "")
        + (f"\nCritical flood gauges:\n{top_gauges}" if top_gauges else "")
        + (f"\nVolcano alerts:\n{top_vols}" if top_vols else "")
    )

    try:
        ai_client = _GroqClient(api_key=GROQ_API_KEY)
        response = ai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=900,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a national emergency management professional writing situation "
                        "reports for emergency operations centers. Be concise and authoritative. "
                        "Use plain language. No markdown formatting."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        "Based on this real-time national hazard data, write a structured briefing "
                        "using exactly this format:\n\n"
                        "SEVERITY: [single integer 1-10, where 10 is catastrophic national emergency]\n\n"
                        "PRIORITY THREATS:\n"
                        "1. [Most urgent threat — specific location, event type, scale]\n"
                        "2. [Second threat]\n"
                        "3. [Third threat — if applicable, else omit]\n\n"
                        "SITUATION: [2-3 sentences summarizing the overall national hazard picture]\n\n"
                        "ACTIONS: [2-3 specific recommended actions for emergency managers]\n\n"
                        f"Data:\n{context}"
                    )
                }
            ]
        )
        if not response.choices:
            return "Error: No response returned by AI model.", None
        text = response.choices[0].message.content
        return text, text
    except Exception as e:
        return f"Error generating report: {e}", None


def _haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km between two points."""
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(min(1, math.sqrt(a)))


def _feature_near(feat, lat, lng, radius_km):
    """Return True if the feature's centroid is within radius_km of (lat, lng)."""
    try:
        geom = feat.get("geometry") or {}
        t    = geom.get("type", "")
        coords = geom.get("coordinates", [])
        if t == "Point":
            return _haversine_km(lat, lng, coords[1], coords[0]) <= radius_km
        ring = coords[0] if t == "Polygon" else (coords[0][0] if t == "MultiPolygon" else [])
        if ring:
            avg_lat = sum(c[1] for c in ring) / len(ring)
            avg_lng = sum(c[0] for c in ring) / len(ring)
            return _haversine_km(lat, lng, avg_lat, avg_lng) <= radius_km
    except Exception:
        pass
    return False


def generate_county_sitrep(lat, lng, radius_miles=50, county_name=""):
    """County-scoped AI briefing — filters all hazard layers to radius_miles around (lat, lng)."""
    if not GROQ_API_KEY:
        return "GROQ_API_KEY not configured.", None
    if not _GROQ_AVAILABLE:
        return "groq package not installed.", None

    radius_km = radius_miles * 1.60934
    loc = county_name or f"area near {lat:.2f}N, {abs(lng):.2f}W"

    w_feats   = [f for f in state.get("warnings",      {}).get("features", []) if _feature_near(f, lat, lng, radius_km)]
    eq_feats  = [f for f in state.get("earthquakes",   {}).get("features", []) if _feature_near(f, lat, lng, radius_km)]
    fire_feats= [f for f in state.get("fires",         {}).get("features", []) if _feature_near(f, lat, lng, radius_km)]
    gauge_feats=[f for f in state.get("river_gauges",  {}).get("features", []) if _feature_near(f, lat, lng, radius_km)]
    perim_feats=[f for f in state.get("fire_perimeters",{}).get("features",[]) if _feature_near(f, lat, lng, radius_km)]

    ctx = (
        f"Location: {loc}\n"
        f"Radius: {radius_miles} miles\n"
        f"Report time: {state.get('last_update','Unknown')} UTC\n\n"
        f"LOCAL HAZARDS:\n"
        f"  NWS warnings/watches/advisories: {len(w_feats)}\n"
        f"  Earthquakes M2.5+: {len(eq_feats)}\n"
        f"  Wildfire detections: {len(fire_feats)}\n"
        f"  Active fire perimeters: {len(perim_feats)}\n"
        f"  Flood gauges at/above stage: {len(gauge_feats)}\n"
    )
    if w_feats:
        ctx += "\nWarnings:\n" + "\n".join(
            f"  - {phenom_names.get(str(f.get('properties',{}).get('phenom','')).upper(), f.get('properties',{}).get('phenom',''))} "
            f"{'Warning' if f.get('properties',{}).get('sig')=='W' else 'Watch' if f.get('properties',{}).get('sig')=='A' else 'Advisory'}"
            for f in w_feats[:8]
        )
    if eq_feats:
        ctx += "\nEarthquakes:\n" + "\n".join(
            f"  - M{f.get('properties',{}).get('mag','?')} — {f.get('properties',{}).get('place','')}"
            for f in sorted(eq_feats, key=lambda x: x.get("properties",{}).get("mag",0), reverse=True)[:5]
        )
    if gauge_feats:
        ctx += "\nFlood gauges:\n" + "\n".join(
            f"  - {f.get('properties',{}).get('location','')} ({f.get('properties',{}).get('status','')})"
            for f in gauge_feats[:5]
        )

    prompt_user = (
        f"Write a 60-second briefing for {loc} emergency management officials using EXACTLY this format:\n\n"
        "SEVERITY: [1-10]\n\n"
        "PRIORITY THREATS:\n"
        "1. [Most urgent local threat — specific and actionable]\n"
        "2. [Second threat — omit line if none]\n\n"
        "SITUATION: [2 sentences on what is happening locally right now]\n\n"
        "ACTIONS: [2 specific recommended actions for local emergency managers]\n\n"
        f"Local hazard data:\n{ctx}"
    )
    try:
        client = _GroqClient(api_key=GROQ_API_KEY)
        resp   = client.chat.completions.create(
            model="llama-3.3-70b-versatile", max_tokens=700,
            messages=[
                {"role": "system", "content": (
                    "You are a county emergency management professional writing a local situation report. "
                    "Be specific to the local area. Plain language only. No markdown."
                )},
                {"role": "user", "content": prompt_user}
            ]
        )
        text = resp.choices[0].message.content
        return text, text
    except Exception as e:
        return f"Error generating county briefing: {e}", None


def generate_briefing_from_client(score, threat_level, threats, county):
    """Generate a SITUATION + ACTIONS briefing using the client-computed threat score and list.
    The AI does not re-derive severity — it uses the score we already calculated."""
    if not GROQ_API_KEY:
        return "GROQ_API_KEY not configured.", None
    if not _GROQ_AVAILABLE:
        return "groq package not installed.", None

    loc = county or "this area"

    # threats can be either:
    #  - list[str] (legacy)
    #  - list[dict] with keys like {label, points, dist, source}
    threat_lines = []
    if isinstance(threats, list):
        for t in threats[:12]:
            if isinstance(t, str):
                threat_lines.append(t.strip())
            elif isinstance(t, dict):
                label  = str(t.get("label", "")).strip()
                src    = str(t.get("source", "")).strip()
                pts    = t.get("points", None)
                dist   = t.get("dist", None)
                parts = []
                if src:
                    parts.append(f"[{src}]")
                if label:
                    parts.append(label)
                if pts is not None:
                    parts.append(f"(+{pts} pts)")
                if dist is not None:
                    try:
                        parts.append(f"~{float(dist):.0f} mi")
                    except Exception:
                        pass
                line = " ".join(parts).strip()
                if line:
                    threat_lines.append(line)
    threat_list = "\n".join(f"  - {t}" for t in threat_lines) if threat_lines else "  - No active immediate threats detected"

    prompt = (
        f"Location: {loc}\n"
        f"Computed threat score: {score}/100 ({threat_level} THREAT)\n"
        f"Active threats in this area:\n{threat_list}\n\n"
        f"Write a 60-second briefing for {loc} emergency management officials.\n"
        "Use EXACTLY this format — do not add a SEVERITY line, the score is already shown:\n\n"
        "SITUATION: [2 sentences on what is happening locally right now and who is affected]\n\n"
        "ACTIONS: [2-3 specific, concrete actions local emergency managers should take now]\n\n"
        "Hard rules:\n"
        "- ONLY reference hazards that appear in the 'Active threats' list.\n"
        "- Do NOT add new hazards, numbers, locations, or claims not present in the list.\n"
        "- If the list shows no active immediate threats, explicitly say that.\n\n"
        "Plain language only. No markdown. No bullet points in ACTIONS — use numbered sentences."
    )
    try:
        client = _GroqClient(api_key=GROQ_API_KEY)
        resp   = client.chat.completions.create(
            model="llama-3.3-70b-versatile", max_tokens=400,
            messages=[
                {"role": "system", "content": (
                    "You are a county emergency manager writing a concise local situation briefing. "
                    "Be direct and actionable. Reference the specific threats listed. No markdown."
                )},
                {"role": "user", "content": prompt}
            ]
        )
        text = resp.choices[0].message.content.strip()
        return text, text
    except Exception as e:
        return f"Error generating briefing: {e}", None


def send_alert_email(subject, body):
    """Send a plain-text alert email via SendGrid. Silently skips if unconfigured."""
    if not _SENDGRID_AVAILABLE or not SENDGRID_API_KEY or not ALERT_EMAIL:
        return
    try:
        message = Mail(
            from_email=ALERT_EMAIL,
            to_emails=ALERT_EMAIL,
            subject=subject,
            plain_text_content=body
        )
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
        print(f"  [ALERT] Email sent: {subject}")
    except Exception as e:
        print(f"  [ALERT] Email send failed: {e}")


def check_and_send_alerts(warnings, earthquakes, storms, affected):
    """
    Compare incoming data against seen_alert_ids and send emails for:
      - New Tornado Warnings  (NWS phenom=TO, sig=W)
      - New Hurricane Warnings (NWS phenom=HU, sig=W)
      - New M5.0+ Earthquakes (USGS)
      - Newly detected NHC tropical storms / hurricanes
    """
    if not _SENDGRID_AVAILABLE or not SENDGRID_API_KEY or not ALERT_EMAIL:
        return

    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # Build a lookup: phenom code → affected counties for this cycle
    counties_by_phenom = {}
    for c in affected:
        p = str(c.get("phenom", "")).strip().upper()
        counties_by_phenom.setdefault(p, []).append(c)

    # ── NWS Tornado and Hurricane Warnings ──
    for feat in warnings.get("features", []):
        props  = feat.get("properties", {})
        phenom = str(props.get("phenom", "")).strip().upper()
        sig    = str(props.get("sig", "")).strip()

        if phenom not in ("TO", "HU") or sig != "W":
            continue

        # Build a stable event ID. NWS MapServer features carry a numeric ObjectID
        # as the GeoJSON feature "id"; fall back to a composite key if absent.
        feat_id = feat.get("id") or feat.get("properties", {}).get("objectid") or ""
        wfo = props.get("wfo", "")
        event_id = f"nws_{phenom}_{sig}_{feat_id or wfo}"

        if event_id in state["seen_alert_ids"]:
            continue
        state["seen_alert_ids"].add(event_id)

        event_name = phenom_names.get(phenom, phenom) + " Warning"
        counties   = counties_by_phenom.get(phenom, [])
        top_counties = counties[:15]
        county_lines = "\n".join(
            f"  - {c['county']}, {c['state']}  (pop: {c['population']:,})"
            for c in top_counties
        )
        if len(counties) > 15:
            county_lines += f"\n  ... and {len(counties) - 15} more"
        total_pop = sum(c.get("population", 0) for c in counties)

        location_hint = wfo if wfo else "Unknown WFO"
        subject = f"HAZARD ALERT: {event_name} - {location_hint}"
        body = (
            f"NATIONAL HAZARD MONITOR ALERT\n"
            f"{'=' * 42}\n\n"
            f"EVENT:            {event_name}\n"
            f"ISSUING OFFICE:   {wfo or 'N/A'}\n"
            f"TIME:             {now_str}\n\n"
            f"AFFECTED COUNTIES ({len(counties)} total):\n"
            f"{county_lines or '  (none matched)'}\n\n"
            f"POPULATION AT RISK: {total_pop:,}\n\n"
            f"--\n"
            f"National Hazard Monitor\n"
        )
        send_alert_email(subject, body)

    # ── USGS Earthquakes M5.0+ ──
    for feat in earthquakes.get("features", []):
        props = feat.get("properties", {})
        mag   = props.get("mag") or 0
        if mag < 5.0:
            continue

        # USGS assigns stable IDs like "us7000k9g1"
        feat_id  = feat.get("id") or ""
        place    = props.get("place", "Unknown location")
        event_id = f"eq_{feat_id}" if feat_id else f"eq_{place}_{mag}"

        if event_id in state["seen_alert_ids"]:
            continue
        state["seen_alert_ids"].add(event_id)

        coords = feat.get("geometry", {}).get("coordinates", [])
        depth = round(coords[2], 1) if len(coords) >= 3 and coords[2] is not None else "?"
        eq_ts = props.get("time", 0)
        if eq_ts:
            eq_time_str = datetime.datetime.utcfromtimestamp(eq_ts / 1000).strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            eq_time_str = now_str

        subject = f"HAZARD ALERT: M{mag:.1f} Earthquake - {place}"
        body = (
            f"NATIONAL HAZARD MONITOR ALERT\n"
            f"{'=' * 42}\n\n"
            f"EVENT:     Magnitude {mag:.1f} Earthquake\n"
            f"LOCATION:  {place}\n"
            f"DEPTH:     {depth} km\n"
            f"TIME:      {eq_time_str}\n"
            f"USGS ID:   {feat_id or 'N/A'}\n"
            f"USGS URL:  https://earthquake.usgs.gov/earthquakes/eventpage/{feat_id}\n\n"
            f"--\n"
            f"National Hazard Monitor\n"
        )
        send_alert_email(subject, body)

    # ── NHC Active Tropical Storms / Hurricanes ──
    for storm in storms:
        info   = storm.get("info", {})
        wallet = info.get("wallet", "") or ""
        name   = storm.get("name", "Unknown Storm")

        if not wallet:
            continue

        event_id = f"nhc_{wallet}"
        if event_id in state["seen_alert_ids"]:
            continue
        state["seen_alert_ids"].add(event_id)

        classification = info.get("classification", "Tropical Cyclone")
        intensity      = info.get("intensity", "?")
        subject = f"HAZARD ALERT: {classification} {name} - Now Active"
        body = (
            f"NATIONAL HAZARD MONITOR ALERT\n"
            f"{'=' * 42}\n\n"
            f"EVENT:          {classification} {name}\n"
            f"MAX WINDS:      {intensity} kt\n"
            f"NHC STORM ID:   {wallet}\n"
            f"DETECTED:       {now_str}\n\n"
            f"This storm is being actively tracked by the National Hurricane Center.\n"
            f"NHC Advisory:   https://www.nhc.noaa.gov/\n\n"
            f"--\n"
            f"National Hazard Monitor\n"
        )
        send_alert_email(subject, body)

    # Prevent unbounded memory growth — discard oldest ~half when over limit
    if len(state["seen_alert_ids"]) > 10000:
        excess = len(state["seen_alert_ids"]) - 5000
        for _ in range(excess):
            state["seen_alert_ids"].pop()


# ─────────────────────────────────────────────
# BACKGROUND UPDATE THREAD
# Runs every 30 minutes automatically
# ─────────────────────────────────────────────
def run_update():
    """Download all data and rebuild map. Runs in background thread."""
    global state
    with state_lock:
        if state["updating"]:
            return
        state["updating"] = True
    print(f"\n{'='*50}")
    print(f"  UPDATE: {datetime.datetime.now()}")
    print(f"{'='*50}")

    try:
        # Fetch all data sources in parallel — cuts update time from ~5-10min to ~30-60s
        _fetch_tasks = {
            'warnings':        fetch_warnings,
            'spc':             fetch_spc,
            'earthquakes':     fetch_earthquakes,
            'storms':          fetch_storms,
            'fires':           fetch_fires,
            'lightning':       fetch_lightning,
            'fire_perimeters': fetch_fire_perimeters,
            'air_quality':     fetch_air_quality,
            'fema_disasters':  fetch_fema_disasters,
            'river_gauges':    fetch_river_gauges,
            'volcanoes':       fetch_volcanoes,
            'drought':         fetch_drought,
            'shelters':        fetch_shelters,
        }
        _empty_fc = {"type": "FeatureCollection", "features": []}
        _results = {}
        with ThreadPoolExecutor(max_workers=13) as executor:
            _futures = {executor.submit(fn): name for name, fn in _fetch_tasks.items()}
            for future in as_completed(_futures):
                name = _futures[future]
                try:
                    _results[name] = future.result()
                except Exception as exc:
                    print(f"  ERROR in {name}: {exc}")
                    _results[name] = dict(_empty_fc)
        warnings        = _results['warnings']
        spc             = _results['spc']
        earthquakes     = _results['earthquakes']
        storms          = _results['storms']
        fires           = _results['fires']
        lightning       = _results['lightning']
        fire_perimeters = _results['fire_perimeters']
        air_quality     = _results['air_quality']
        fema_disasters  = _results['fema_disasters']
        river_gauges    = _results['river_gauges']
        volcanoes       = _results['volcanoes']
        drought         = _results['drought']
        shelters        = _results['shelters']

        # Load population once
        if not state["pop_data"]:
            state["pop_data"] = fetch_population()

        # Load counties GeoJSON once
        if not state["counties_geojson"]:
            print("Loading county boundaries...")
            try:
                r = requests.get(COUNTIES_URL, timeout=30)
                r.raise_for_status()
                state["counties_geojson"] = r.json()
                print(f"  Counties loaded: {len(state['counties_geojson'].get('features',[]))}")
            except Exception as e:
                print(f"  WARNING: Counties failed: {e}")

        # Find affected counties
        affected, total_pop = find_affected_counties(
            warnings, state["pop_data"], state["counties_geojson"]
        )

        # Build Folium map
        print("Building interactive map...")
        map_html = build_folium_map(warnings, spc, earthquakes, storms, fires)

        # Send alerts for new high-priority events
        check_and_send_alerts(warnings, earthquakes, storms, affected)

        # Update global state — lock prevents Flask handlers from reading a
        # partially-updated state dict during the multi-key replacement.
        with state_lock:
            state.update({
                "last_update":     datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "warnings":        warnings,
                "spc":             spc,
                "earthquakes":     earthquakes,
                "storms":          storms,
                "fires":           fires,
                "lightning":       lightning,
                "fire_perimeters": fire_perimeters,
                "air_quality":     air_quality,
                "fema_disasters":  fema_disasters,
                "river_gauges":    river_gauges,
                "volcanoes":       volcanoes,
                "drought":         drought,
                "shelters":        shelters,
                "map_html":        map_html,
                "summary": {
                    "warnings_count":   len(warnings.get("features", [])),
                    "counties_count":   len(affected),
                    "total_population": total_pop,
                    "spc_zones":        len(spc.get("features", [])),
                    "earthquakes":      len(earthquakes.get("features", [])),
                    "active_storms":    len(storms),
                    "wildfires":        len(fires),
                    "river_gauges":     len(river_gauges.get("features", [])),
                    "volcanoes":        len(volcanoes.get("features", [])),
                    "drought":          len(drought.get("features", [])),
                    "shelters":         len(shelters.get("features", [])),
                    "affected_counties": affected
                }
            })
        print(f"  Update complete — {len(affected)} counties affected")
        # Save to cache file so data survives restarts
        save_cache({
            "last_update":     state["last_update"],
            "warnings":        state["warnings"],
            "spc":             state["spc"],
            "earthquakes":     state["earthquakes"],
            "storms":          state["storms"],
            "fires":           state["fires"],
            "lightning":       state["lightning"],
            "fire_perimeters": state["fire_perimeters"],
            "air_quality":     state["air_quality"],
            "fema_disasters":  state["fema_disasters"],
            "river_gauges":    state["river_gauges"],
            "volcanoes":       state["volcanoes"],
            "drought":         state["drought"],
            "shelters":        state["shelters"],
            "summary":         state["summary"],
            "map_html":        state["map_html"]
        })
        print("  Cache saved")

    except Exception as e:
        print(f"  ERROR during update: {e}")
    finally:
        state["updating"] = False

def fetch_lightning():
    """Fetch recent lightning strikes from Iowa State Mesonet."""
    print("Downloading lightning data...")
    try:
        # Iowa State LSR feed filtered to lightning type only (typetext contains LIGHTNING)
        url = "https://mesonet.agron.iastate.edu/geojson/lsr.php?hours=6&wfo=all&ltype=L&ltype=T"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        # Filter to only actual lightning reports
        features = [
            f for f in data.get("features", [])
            if str(f.get("properties", {}).get("typetext", "")).upper() in
               ["LIGHTNING", "HAIL", "TSTM WND GST", "TSTM WND DMG", "FUNNEL CLOUD", "TORNADO"]
        ]
        print(f"  Storm reports: {len(features)} events (last 6hr)")
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        print(f"  Lightning failed: {e}")
        return {"type": "FeatureCollection", "features": []}

def fetch_fire_perimeters():
    """Fetch active wildfire perimeters from NIFC WFIGS via ArcGIS REST.
    Queries both sources and merges results, deduplicating by incident name."""
    print("Downloading wildfire perimeters...")
    urls = [
        # NIFC WFIGS current-year interagency perimeters (primary — live REST endpoint)
        "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_YTD/FeatureServer/0/query?where=1%3D1&outFields=*&geometryPrecision=3&outSR=4326&resultRecordCount=500&f=geojson",
        # NIFC current fires (backup/supplemental)
        "https://services9.arcgis.com/RHVPKKiFTONKtxq3/arcgis/rest/services/USA_Wildfires_v1/FeatureServer/0/query?where=1%3D1&outFields=IncidentName,GISAcres,PercentContained&geometryPrecision=3&outSR=4326&resultRecordCount=300&f=geojson",
    ]
    all_features = []
    seen_names = set()
    for url in urls:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                print(f"  Fire perimeters ArcGIS error: {data['error']}")
                continue
            for feat in data.get("features", []):
                # Skip Point/MultiPoint — those are fire location markers, not perimeters.
                # Only Polygon/MultiPolygon render correctly as fill layers on the map.
                geom_type = (feat.get("geometry") or {}).get("type", "")
                if geom_type not in ("Polygon", "MultiPolygon"):
                    continue
                name = (feat.get("properties") or {}).get("IncidentName", "")
                key = name.strip().upper() if name else None
                if key and key in seen_names:
                    continue
                if key:
                    seen_names.add(key)
                all_features.append(feat)
        except Exception as e:
            print(f"  Fire perimeters failed ({url[:60]}...): {e}")
            continue
    print(f"  Fire perimeters: {len(all_features)} active fire perimeters (merged, polygons only)")
    return {"type": "FeatureCollection", "features": all_features}

def fetch_air_quality():
    """Fetch current AQI readings from AirNow API. Requires AIRNOW_KEY env var (free at airnowapi.org)."""
    if not AIRNOW_KEY:
        return {"type": "FeatureCollection", "features": []}
    print("Downloading AirNow AQI data...")
    try:
        now  = datetime.datetime.utcnow()
        end  = now.strftime("%Y-%m-%dT%H")
        # AirNow reports are 1-2 hours delayed — query a 3-hour window to guarantee data
        start = (now - datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H")
        url = (
            f"https://www.airnowapi.org/aq/data/"
            f"?startDate={start}&endDate={end}"
            f"&parameters=PM25,OZONE&BBOX=-125,24,-66,50"
            f"&dataType=A&format=application/json&verbose=0&monitorType=2"
            f"&API_KEY={AIRNOW_KEY}"
        )
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            print(f"  AirNow HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
        readings = r.json()
        if isinstance(readings, dict) and readings.get("WebServiceError"):
            print(f"  AirNow API error: {readings['WebServiceError']}")
            return {"type": "FeatureCollection", "features": []}
        print(f"  AirNow raw readings: {len(readings) if isinstance(readings, list) else 'not a list'}")
        # Deduplicate by station — keep highest AQI reading per location
        station_best = {}
        for item in readings:
            if not isinstance(item, dict):
                continue
            lat = item.get("Latitude")
            lon = item.get("Longitude")
            if not lat or not lon:
                continue
            key = (round(float(lat), 3), round(float(lon), 3))
            aqi = item.get("AQI", 0) or 0
            if key not in station_best or aqi > station_best[key].get("AQI", 0):
                station_best[key] = item
        features = []
        for (lat, lon), item in station_best.items():
            aqi = item.get("AQI", 0) or 0
            cat_raw = item.get("Category", {})
            cat = cat_raw.get("Name", "Unknown") if isinstance(cat_raw, dict) else "Unknown"
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "aqi":            aqi,
                    "category":       cat,
                    "parameter":      item.get("ParameterName", ""),
                    "reporting_area": item.get("ReportingArea", ""),
                    "state":          item.get("StateCode", ""),
                }
            })
        print(f"  AirNow: {len(features)} monitoring stations")
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        print(f"  AirNow failed: {e}")
        return {"type": "FeatureCollection", "features": []}

def fetch_fema_disasters():
    """Fetch active FEMA disaster declarations from the last 60 days."""
    print("Downloading FEMA disaster declarations...")
    try:
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
        r = requests.get(
            "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries",
            params={
                "$filter": f"declarationDate ge '{cutoff}'",
                "$format": "json",
                "$orderby": "declarationDate desc",
                "$top": "300",
                "$select": "disasterNumber,state,declarationDate,incidentType,declarationTitle,designatedArea",
            },
            timeout=20
        )
        r.raise_for_status()
        data = r.json()
        records = data.get("DisasterDeclarationsSummaries", [])
        # Aggregate by state — count disasters and collect titles
        by_state = {}
        for rec in records:
            st = rec.get("state", "")
            if not st or st not in STATE_CENTROIDS:
                continue
            if st not in by_state:
                by_state[st] = {"count": 0, "types": set(), "latest": ""}
            by_state[st]["count"] += 1
            dtype = rec.get("incidentType", "")
            if dtype:
                by_state[st]["types"].add(dtype)
            if not by_state[st]["latest"]:
                by_state[st]["latest"] = rec.get("declarationTitle", "")
        features = []
        for st, info in by_state.items():
            lat, lon = STATE_CENTROIDS[st]
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "state":   st,
                    "count":   info["count"],
                    "types":   ", ".join(sorted(info["types"])),
                    "latest":  info["latest"],
                }
            })
        print(f"  FEMA: {len(records)} declarations across {len(features)} states")
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        print(f"  FEMA disasters failed: {e}")
        return {"type": "FeatureCollection", "features": []}

def fetch_river_gauges():
    """Fetch NOAA AHPS river gauges currently at or above flood stage."""
    print("Downloading NOAA river flood gauges...")
    try:
        r = requests.get(
            "https://mapservices.weather.noaa.gov/eventdriven/rest/services/water/riv_gauges/MapServer/0/query",
            params={
                "where":  "status IN ('action','minor','moderate','major')",
                "outFields": "gaugelid,status,location,state,url",
                "f": "geojson",
                "resultRecordCount": "2000",
            },
            timeout=20
        )
        r.raise_for_status()
        data = r.json()
        color_map = {
            "action":   "#FFFF00",
            "minor":    "#FFA500",
            "moderate": "#FF4500",
            "major":    "#FF0000",
        }
        features = []
        for feat in data.get("features", []):
            try:
                props = feat.get("properties") or {}
                status = str(props.get("status", "")).lower()
                props["color"] = color_map.get(status, "#AAAAFF")
                props["name"]  = props.get("location", "Unknown Gauge")
                features.append(feat)
            except Exception:
                continue
        print(f"  River gauges: {len(features)} at/above flood/action stage")
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        print(f"  River gauges failed: {e}")
        return {"type": "FeatureCollection", "features": []}

def fetch_volcanoes():
    """Fetch elevated US volcano alerts from USGS HANS (Yellow / Orange / Red).

    The getElevatedVolcanoes endpoint returns one record per elevated volcano
    but no coordinates — we fetch each volcano's notice detail to get lat/lng
    plus the human-readable synopsis. HANS is authoritative for US volcanoes
    (replaces the previous GDACS feed, which never reports VO events).
    """
    print("Downloading USGS HANS volcano alerts...")
    try:
        r = requests.get(
            "https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes",
            timeout=15
        )
        r.raise_for_status()
        elevated = r.json() or []
        color_map = {"YELLOW": "#FFD700", "ORANGE": "#FF8800", "RED": "#FF0000"}
        features = []
        for v in elevated:
            try:
                notice_url = v.get("notice_data")
                if not notice_url:
                    continue
                nr = requests.get(notice_url, timeout=15)
                nr.raise_for_status()
                notice = nr.json() or {}
                sections = notice.get("notice_sections") or []
                if not sections:
                    continue
                # Observatory notices (e.g. AVO) include sections for every elevated
                # volcano in their region — match by vnum instead of picking [0].
                target_vnum = str(v.get("vnum") or "")
                s = next((sec for sec in sections if str(sec.get("vnum") or "") == target_vnum), None) or sections[0]
                lat = s.get("lat")
                lng = s.get("lng")
                if lat is None or lng is None:
                    continue
                color_code = (v.get("color_code") or "").upper()
                alert_level = (v.get("alert_level") or "").upper()
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(lng), float(lat)]},
                    "properties": {
                        "name":     v.get("volcano_name") or s.get("vName") or "Volcano",
                        "alert":    alert_level.lower(),
                        "color":    color_map.get(color_code, "#FF8800"),
                        "region":   s.get("region", ""),
                        "observatory": v.get("obs_fullname", ""),
                        "synopsis": s.get("synopsis", ""),
                        "url":      s.get("vUrl") or v.get("notice_url", ""),
                    }
                })
            except Exception as inner:
                print(f"  Volcano notice fetch failed ({v.get('volcano_name')}): {inner}")
                continue
        print(f"  Volcanoes: {len(features)} elevated (Yellow/Orange/Red) US alerts")
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        print(f"  Volcanoes failed: {e}")
        return {"type": "FeatureCollection", "features": []}

def fetch_drought():
    """Fetch current US Drought Monitor polygons from UNL."""
    print("Downloading drought monitor data...")
    try:
        r = requests.get(
            "https://droughtmonitor.unl.edu/data/json/usdm_current.json",
            timeout=30
        )
        r.raise_for_status()
        data = r.json()
        features = data.get("features", [])
        print(f"  Drought monitor: {len(features)} polygons")
        return data
    except Exception as e:
        print(f"  Drought monitor failed: {e}")
        return {"type": "FeatureCollection", "features": []}

def fetch_shelters():
    """Fetch FEMA National Shelter System open shelters."""
    print("Downloading FEMA open shelters...")
    try:
        url = (
            "https://gis.fema.gov/arcgis/rest/services/NSS/OpenShelters/MapServer/0/query"
            "?where=1%3D1&outFields=shelter_name,address,city,state,pet_accommodations_code,evacuation_capacity,shelter_status"
            "&geometryPrecision=3&outSR=4326&resultRecordCount=2000&f=geojson"
        )
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            print(f"  Shelters ArcGIS error: {data['error']}")
            return {"type": "FeatureCollection", "features": []}
        features = data.get("features", [])
        print(f"  Open shelters: {len(features)}")
        return data
    except Exception as e:
        print(f"  Shelters failed: {e}")
        return {"type": "FeatureCollection", "features": []}

def schedule_updates(interval_minutes=30):
    """Run update on schedule in background thread."""
    def loop():
        # Initial update
        print("Starting initial data update...")
        run_update()
        while True:
            print(f"Sleeping {interval_minutes} minutes until next update...")
            time.sleep(interval_minutes * 60)
            run_update()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print(f"Background update thread started")

# ─────────────────────────────────────────────
# DASH APP
# ─────────────────────────────────────────────
app = dash.Dash(
    __name__,
    title="National Hazard Monitor",
    url_base_pathname="/analytics/",
    external_stylesheets=[
        "https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap"
    ]
)
server = app.server  # Expose Flask server for Render

# Use Flask's before_first_request to start background thread
# This runs once when the first request hits the server
_started = False
_started_lock = threading.Lock()

@server.before_request
def start_background_on_first_request():
    global _started
    with _started_lock:
        if not _started:
            _started = True
            print("First request — starting background update thread...")
            t = threading.Thread(target=lambda: schedule_updates(30), daemon=True)
            t.start()

# ─────────────────────────────────────────────
# FLASK API ENDPOINTS
# Serve hazard data as GeoJSON for Mapbox map
# ─────────────────────────────────────────────
import flask as flask_module

@app.server.route("/api/warnings")
def api_warnings():
    """Returns current NWS warnings as GeoJSON."""
    return flask_module.Response(
        json.dumps(state["warnings"]),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/spc")
def api_spc():
    """Returns SPC convective outlook as GeoJSON."""
    return flask_module.Response(
        json.dumps(state["spc"]),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/earthquakes")
def api_earthquakes():
    """Returns USGS earthquake data as GeoJSON."""
    return flask_module.Response(
        json.dumps(state["earthquakes"]),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/fires")
def api_fires():
    """Returns NASA FIRMS fire data as GeoJSON points."""
    features = []
    for fire in state.get("fires", []):
        try:
            lat = float(fire.get("latitude", 0))
            lon = float(fire.get("longitude", 0))
            frp = float(fire.get("frp", 0))
            if lat == 0 and lon == 0:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "frp":        frp,
                    "acq_date":   fire.get("acq_date", ""),
                    "confidence": fire.get("confidence", "")
                }
            })
        except Exception:
            continue
    return flask_module.Response(
        json.dumps({"type": "FeatureCollection", "features": features}),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/summary")
def api_summary():
    """Returns current hazard summary stats."""
    return flask_module.Response(
        json.dumps({
            "last_update": state["last_update"],
            "summary":     state["summary"]
        }),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/counties")
def api_counties():
    """Returns affected counties as GeoJSON with population data."""
    affected = state["summary"].get("affected_counties", [])
    counties_geojson = state.get("counties_geojson")
    if not counties_geojson or not affected:
        return flask_module.Response(
            json.dumps({"type": "FeatureCollection", "features": []}),
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
    affected_fips = {c["fips"]: c for c in affected}
    features = []
    for feat in counties_geojson.get("features", []):
        fips = feat.get("id", "")
        if fips in affected_fips:
            county_data = affected_fips[fips]
            feat_copy = dict(feat)
            feat_copy["properties"] = {
                "fips":          fips,
                "county":        county_data.get("county", ""),
                "state":         county_data.get("state", ""),
                "population":    county_data.get("population", 0),
                "event":         county_data.get("event", ""),
                "sig":           county_data.get("sig", ""),
                "phenom":        county_data.get("phenom", ""),
                "severity_rank": county_data.get("severity_rank", 0),
                "warning_count": county_data.get("warning_count", 1),
            }
            features.append(feat_copy)
    return flask_module.Response(
        json.dumps({"type": "FeatureCollection", "features": features}),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/infrastructure")
def api_infrastructure():
    """Returns infrastructure near warning areas as GeoJSON."""
    features = []
    warning_bounds = get_warning_bounds(state["warnings"])
    
    if not warning_bounds:
        return flask_module.Response(
            json.dumps({"type": "FeatureCollection", "features": []}),
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    # Build a single expanded bounding box covering all warnings
    all_minlon = min(b["minlon"] for b in warning_bounds) - 1.0
    all_maxlon = max(b["maxlon"] for b in warning_bounds) + 1.0
    all_minlat = min(b["minlat"] for b in warning_bounds) - 1.0
    all_maxlat = max(b["maxlat"] for b in warning_bounds) + 1.0

    infra_types = [
        ("amenity", "hospital",     "hospital",     "#FF0066", "🏥"),
        ("amenity", "fire_station", "fire_station",  "#FF4400", "🚒"),
        ("power",   "plant",        "power_plant",   "#FFD700", "⚡"),
        ("amenity", "school",       "school",        "#00FF88", "🏫"),
    ]

    cache_key = f"{all_minlon:.1f},{all_minlat:.1f},{all_maxlon:.1f},{all_maxlat:.1f}"
    with state_lock:
        if state.get("infra_cache_key") == cache_key and state.get("infra_features"):
            return flask_module.Response(
                json.dumps({"type": "FeatureCollection", "features": state["infra_features"]}),
                mimetype="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

    bbox_str = f"{all_minlat},{all_minlon},{all_maxlat},{all_maxlon}"

    # Public Overpass mirrors — tried in order until one succeeds. The main
    # instance (overpass-api.de) is overloaded during peak hours and returns
    # 504 Gateway Timeout; mirrors distribute the load and keep us up.
    OVERPASS_MIRRORS = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    ]

    for tag_key, tag_val, infra_type, color, icon in infra_types:
        query = f'[out:json][timeout:15];node["{tag_key}"="{tag_val}"]({bbox_str});out body;'
        items = None
        last_err = None
        for mirror in OVERPASS_MIRRORS:
            try:
                r = requests.post(mirror, data={"data": query}, timeout=20)
                r.raise_for_status()
                items = r.json().get("elements", [])
                break  # success — don't try more mirrors
            except Exception as e:
                last_err = e
                continue
        if items is None:
            print(f"  Overpass {infra_type} failed on all mirrors: {last_err}")
            continue
        try:
            for item in items[:500]:
                lat = item.get("lat")
                lon = item.get("lon")
                if not lat or not lon:
                    continue
                at_risk = any(point_in_bbox(lon, lat, b) for b in warning_bounds)
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "type":    infra_type,
                        "name":    item.get("tags", {}).get("name", infra_type.replace("_", " ").title()),
                        "color":   color,
                        "icon":    icon,
                        "at_risk": at_risk
                    }
                })
        except Exception as e:
            print(f"  Overpass {infra_type} parse failed: {e}")
            continue

    with state_lock:
        state["infra_cache_key"] = cache_key
        state["infra_features"]  = features

    return flask_module.Response(
        json.dumps({"type": "FeatureCollection", "features": features}),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )


@app.server.route("/api/storms")
def api_storms():
    """Returns hurricane cones and track points as GeoJSON."""
    features = []
    for storm in state.get("storms", []):
        name = storm.get("name", "Storm")
        cone = storm.get("cone")
        if cone and cone.get("features"):
            for feat in cone["features"]:
                f = dict(feat)
                f["properties"] = {**(feat.get("properties") or {}), "storm_name": name, "layer": "cone"}
                features.append(f)
        track = storm.get("track")
        if track and track.get("features"):
            for i, feat in enumerate(track["features"]):
                coords = feat.get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": coords},
                        "properties": {**(feat.get("properties") or {}),
                                       "storm_name": name, "layer": "track", "seq": i}
                    })
    return flask_module.Response(
        json.dumps({"type": "FeatureCollection", "features": features}),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/lightning")
def api_lightning():
    """Returns recent lightning strikes as GeoJSON."""
    return flask_module.Response(
        json.dumps(state.get("lightning", {"type":"FeatureCollection","features":[]})),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/fire_perimeters")
def api_fire_perimeters():
    """Returns active wildfire perimeters as GeoJSON."""
    return flask_module.Response(
        json.dumps(state.get("fire_perimeters", {"type":"FeatureCollection","features":[]})),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/air_quality")
def api_air_quality():
    """Returns current AQI monitoring station readings as GeoJSON."""
    return flask_module.Response(
        json.dumps(state.get("air_quality", {"type":"FeatureCollection","features":[]})),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/fema_disasters")
def api_fema_disasters():
    """Returns active FEMA disaster declarations as GeoJSON state-level points."""
    return flask_module.Response(
        json.dumps(state.get("fema_disasters", {"type":"FeatureCollection","features":[]})),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/river_gauges")
def api_river_gauges():
    """Returns USGS river gauges at or above flood stage as GeoJSON."""
    return flask_module.Response(
        json.dumps(state.get("river_gauges", {"type":"FeatureCollection","features":[]})),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/volcanoes")
def api_volcanoes():
    """Returns USGS volcano alert levels as GeoJSON."""
    return flask_module.Response(
        json.dumps(state.get("volcanoes", {"type":"FeatureCollection","features":[]})),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/drought")
def api_drought():
    """Returns current US Drought Monitor polygons as GeoJSON."""
    return flask_module.Response(
        json.dumps(state.get("drought", {"type":"FeatureCollection","features":[]})),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/shelters")
def api_shelters():
    """Returns FEMA open emergency shelters as GeoJSON."""
    return flask_module.Response(
        json.dumps(state.get("shelters", {"type":"FeatureCollection","features":[]})),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/api/events")
def api_events():
    """Unified live feed for the Command Center right column.

    Merges the most recent items from warnings / earthquakes / fire
    detections / river gauges into one list, sorts by time descending,
    returns the top 20 with a uniform shape: {source, time, text, sev,
    lng, lat}.
    """
    SIG_NAMES = {"W": "Warning", "A": "Watch", "Y": "Advisory", "S": "Statement"}

    def _coords_from_geom(geom):
        """Best-effort centroid coords for a feature geometry."""
        if not isinstance(geom, dict):
            return None, None
        gtype  = geom.get("type", "")
        coords = geom.get("coordinates")
        if not coords:
            return None, None
        try:
            if gtype == "Point":
                return float(coords[0]), float(coords[1])
            if gtype == "MultiPoint" or gtype == "LineString":
                return float(coords[0][0]), float(coords[0][1])
            if gtype == "Polygon":
                ring = coords[0] or []
                if not ring: return None, None
                xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
                return sum(xs)/len(xs), sum(ys)/len(ys)
            if gtype == "MultiPolygon":
                ring = (coords[0] or [[]])[0] or []
                if not ring: return None, None
                xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
                return sum(xs)/len(xs), sum(ys)/len(ys)
        except (TypeError, ValueError, IndexError):
            return None, None
        return None, None

    # Per-source cap — without this, NOAA's hundreds of river gauges (all
    # stamped with the same "now" since the feed has no per-gauge time) win
    # every sort tie and crowd warnings/quakes/fires off the feed.
    PER_SOURCE_CAP = 5
    events = []

    # NWS warnings — `sent` is ISO 8601 string; `phenom` and `sig` define the event.
    for f in state.get("warnings", {}).get("features", [])[:30]:
        p = f.get("properties") or {}
        phenom = str(p.get("phenom", "")).strip().upper()
        sig    = str(p.get("sig", "")).strip().upper()
        name   = phenom_names.get(phenom, phenom or "Alert")
        text   = (name + " " + SIG_NAMES.get(sig, "")).strip()
        lng, lat = _coords_from_geom(f.get("geometry") or {})
        events.append({
            "source": "NWS",
            "time":   p.get("sent") or p.get("issuance") or "",
            "text":   text,
            "sev":    severity_for_phenom(phenom),
            "lng":    lng, "lat": lat,
        })

    # USGS earthquakes — `time` is ms-since-epoch integer.
    for f in state.get("earthquakes", {}).get("features", [])[:30]:
        p = f.get("properties") or {}
        mag   = p.get("mag")
        place = p.get("place") or "Unknown location"
        try:
            time_iso = datetime.datetime.utcfromtimestamp(
                float(p.get("time", 0)) / 1000.0
            ).strftime("%Y-%m-%dT%H:%M:%SZ") if p.get("time") else ""
        except (TypeError, ValueError):
            time_iso = ""
        try:
            mag_str = "M{:.1f}".format(float(mag))
        except (TypeError, ValueError):
            mag_str = "M?"
        lng, lat = _coords_from_geom(f.get("geometry") or {})
        events.append({
            "source": "USGS",
            "time":   time_iso,
            "text":   f"{mag_str} earthquake — {place}",
            "sev":    severity_for_magnitude(mag),
            "lng":    lng, "lat": lat,
        })

    # NASA FIRMS fire detections — list of dicts with acq_date + acq_time.
    for fire in (state.get("fires") or [])[:30]:
        date = str(fire.get("acq_date", "")).strip()
        tm   = str(fire.get("acq_time", "")).strip().zfill(4)
        time_iso = f"{date}T{tm[:2]}:{tm[2:]}:00Z" if date and len(tm) == 4 else ""
        try:
            lng = float(fire.get("longitude", 0))
            lat = float(fire.get("latitude",  0))
        except (TypeError, ValueError):
            lng, lat = None, None
        events.append({
            "source": "FIRMS",
            "time":   time_iso,
            "text":   f"Fire detection ({fire.get('confidence','?')}) at {lat:.2f}, {lng:.2f}"
                      if lat is not None and lng is not None else "Fire detection",
            "sev":    severity_for_fire_confidence(fire.get("confidence")),
            "lng":    lng, "lat": lat,
        })

    # NOAA river gauges — no per-gauge timestamp; assume "now" so they
    # mix into the recent feed instead of sorting last.
    now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for f in state.get("river_gauges", {}).get("features", [])[:30]:
        p = f.get("properties") or {}
        loc    = p.get("location") or p.get("name") or "Unknown gauge"
        status = (p.get("status") or "").lower() or "alert"
        lng, lat = _coords_from_geom(f.get("geometry") or {})
        events.append({
            "source": "NOAA",
            "time":   now_iso,
            "text":   f"{status.title()} flood stage — {loc}",
            "sev":    severity_for_gauge_status(status),
            "lng":    lng, "lat": lat,
        })

    # Bucket by source, sort each bucket newest-first, take PER_SOURCE_CAP.
    # Then merge and final-sort so the feed reads chronologically but still
    # shows every source that has anything to report.
    buckets = {}
    for e in events:
        buckets.setdefault(e["source"], []).append(e)
    balanced = []
    for src, items in buckets.items():
        items.sort(key=lambda e: e.get("time") or "", reverse=True)
        balanced.extend(items[:PER_SOURCE_CAP])
    balanced.sort(key=lambda e: e.get("time") or "", reverse=True)
    return flask_module.Response(
        json.dumps({"events": balanced[:20]}),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.server.route("/static/nri_counties.json")
def serve_nri_counties():
    """Serves the pre-built FEMA NRI county lookup table."""
    return flask_module.send_from_directory(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'),
        'nri_counties.json',
        mimetype='application/json'
    )

@app.server.route("/api/sitrep", methods=["GET", "POST"])
def api_sitrep():
    """Generate an AI situation report via Groq. Returns JSON {text, raw}.
    POST body: {score, threat_level, threats[], county, lat, lng, radius}
      — uses client-computed score/threats so AI doesn't re-derive severity.
    GET fallback: national sitrep (no location params) or county sitrep (lat/lng params)."""
    if flask_module.request.method == "POST":
        data   = flask_module.request.get_json(force=True, silent=True) or {}
        score  = data.get("score", 0)
        level  = data.get("threat_level", "LOW")
        threats= data.get("threats", [])
        county = data.get("county", "")
        text, raw = generate_briefing_from_client(score, level, threats, county)
    else:
        try:
            lat    = flask_module.request.args.get("lat",    type=float)
            lng    = flask_module.request.args.get("lng",    type=float)
            radius = flask_module.request.args.get("radius", default=50, type=float)
            county = flask_module.request.args.get("county", default="")
        except Exception:
            lat = lng = None
        valid_loc = (
            lat is not None and lng is not None
            and -90  <= lat <= 90
            and -180 <= lng <= 180
            and 1    <= radius <= 500
        )
        if valid_loc:
            text, raw = generate_county_sitrep(lat, lng, radius, county)
        else:
            text, raw = generate_sitrep()
    return flask_module.jsonify({"text": text, "raw": raw or ""})


@app.server.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    """Subscribe an email to county-level hazard alerts."""
    data   = flask_module.request.get_json(force=True, silent=True) or {}
    email  = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return flask_module.jsonify({"ok": False, "error": "Invalid email address"}), 400
    county = data.get("county", "")
    lat    = data.get("lat")
    lng    = data.get("lng")
    radius = float(data.get("radius", 50))
    existing = next((s for s in _subscriptions if s["email"] == email), None)
    if existing:
        existing.update({"county": county, "lat": lat, "lng": lng, "radius": radius})
        msg = f"Updated alert location to {county or 'your area'}."
    else:
        _subscriptions.append({
            "email": email, "county": county,
            "lat": lat, "lng": lng, "radius": radius,
            "created": datetime.datetime.now().isoformat()
        })
        msg = f"Subscribed! You will receive alerts when hazards change for {county or 'your area'}."
    print(f"  [SUBSCRIBE] {email} → {county or 'coords'} ({len(_subscriptions)} total)")
    return flask_module.jsonify({"ok": True, "message": msg})

@app.server.route("/")
@app.server.route("/mapbox")
def mapbox_map():
    """Serves the full Mapbox GL JS map page (template-backed)."""
    response = flask_module.make_response(flask_module.render_template(
        "mapbox.html",
        mapbox_token=os.environ.get("MAPBOX_TOKEN", ""),
    ))
    response.headers["Content-Security-Policy"] = (
        "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:; "
        "script-src * 'unsafe-inline' 'unsafe-eval' blob:; "
        "style-src * 'unsafe-inline'; "
        "img-src * data: blob:; "
        "connect-src *; "
        "worker-src blob: *;"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), microphone=(), camera=()"
    return response


# Serve assets/* from the repo root (used by mapbox.html via url_for).
# Endpoint name 'mapbox_assets' matches the url_for() call in the template.
@app.server.route("/assets/<path:filename>", endpoint="mapbox_assets")
def mapbox_assets(filename):
    return flask_module.send_from_directory(
        os.path.join(os.path.dirname(__file__) or ".", "assets"),
        filename,
        max_age=300,
    )


app.layout = html.Div(
    style={
        "backgroundColor": "#0A0A0A", "minHeight": "100vh",
        "fontFamily": "'Fira Sans', sans-serif", "color": "#FFFFFF"
    },
    children=[
        dcc.Interval(id="refresh", interval=5*60*1000, n_intervals=0),

        # Header
        html.Div(style={
            "backgroundColor": "#111111",
            "borderBottom": "2px solid #7C3AED",
            "padding": "16px 24px", "display": "flex",
            "justifyContent": "space-between", "alignItems": "center"
        }, children=[
            html.Div([
                html.H1("National All-Hazards Monitor",
                    style={
                        "margin": "0", "fontSize": "20px",
                        "fontFamily": "'Fira Code', monospace",
                        "fontWeight": "600",
                        "color": "#A78BFA",
                        "letterSpacing": "0.5px"
                    }),
                html.P([
                    "Real-time hazard tracking | NWS · NHC · SPC · USGS · NASA · Census  ",
                    html.A("Open Mapbox Map →", href="/mapbox", target="_blank",
                           className="mapbox-link")
                ], style={
                    "margin": "6px 0 0 0", "fontSize": "11px",
                    "color": "rgba(255,255,255,0.45)",
                    "display": "flex", "alignItems": "center", "gap": "12px"
                })
            ]),
            # ── Watchzone search (center) ──────────────────────────────────
            html.Div([
                html.Div([
                    dcc.Input(
                        id="watchzone-input",
                        type="text",
                        placeholder="Watch my area...",
                        debounce=True,
                        maxLength=50,
                        className="watchzone-input",
                        value=""
                    ),
                    html.Button(
                        "✕",
                        id="watchzone-clear-btn",
                        className="watchzone-clear-btn",
                        n_clicks=0,
                        style={"display": "none"}
                    )
                ], className="watchzone-search-row"),
                html.Div(
                    id="watchzone-active-label",
                    className="watchzone-active-label"
                )
            ], style={"display": "flex", "flexDirection": "column", "alignItems": "center", "gap": "4px"}),

            html.Div([
                html.P(id="last-updated", style={
                    "margin": "0", "fontSize": "12px",
                    "fontFamily": "'Fira Code', monospace",
                    "color": "rgba(255,255,255,0.55)", "textAlign": "right"
                }),
                html.P("Auto-refreshes every 30 minutes",
                    style={
                        "margin": "4px 0 0 0", "fontSize": "10px",
                        "color": "rgba(255,255,255,0.3)", "textAlign": "right",
                        "letterSpacing": "0.3px"
                    }),
                html.Button(
                    "⚡ Situation Report",
                    id="sitrep-btn",
                    className="sitrep-gen-btn",
                    n_clicks=0
                )
            ], style={"display": "flex", "flexDirection": "column", "alignItems": "flex-end", "gap": "4px"})
        ]),

        # Stat cards
        html.Div(id="stat-cards",
            style={
                "display": "flex", "gap": "10px",
                "padding": "16px 24px", "flexWrap": "wrap"
            }),

        # Map + Charts
        html.Div(style={
            "display": "flex", "gap": "14px",
            "padding": "0 24px 14px 24px"
        }, children=[
            # Map
            html.Div(style={"flex": "2", "minWidth": "0"}, children=[
                html.Div(style={
                    "backgroundColor": "#111111",
                    "border": "1px solid rgba(124,58,237,0.25)",
                    "borderRadius": "12px",
                    "overflow": "hidden"
                }, children=[
                    html.Div("Live Hazard Map", style={
                        "padding": "10px 14px", "fontSize": "12px",
                        "fontFamily": "'Fira Code', monospace", "fontWeight": "600",
                        "color": "#A78BFA", "letterSpacing": "0.5px",
                        "textTransform": "uppercase",
                        "borderBottom": "1px solid rgba(124,58,237,0.25)"
                    }),
                    html.Div(id="watchzone-map-badge", style={"display": "none"}),
                    html.Div(id="map-container", style={
                        "width": "100%", "height": "480px",
                        "backgroundColor": "#0f0f0f", "overflow": "hidden"
                    })
                ])
            ]),
            # Charts
            html.Div(style={
                "flex": "1", "minWidth": "260px",
                "display": "flex", "flexDirection": "column", "gap": "12px"
            }, children=[
                html.Div(style={
                    "backgroundColor": "#111111",
                    "border": "1px solid rgba(124,58,237,0.25)",
                    "borderRadius": "12px", "overflow": "hidden"
                }, children=[
                    html.Div("Active Warnings by Type", style={
                        "padding": "10px 14px", "fontSize": "12px",
                        "fontFamily": "'Fira Code', monospace", "fontWeight": "600",
                        "color": "#A78BFA", "letterSpacing": "0.5px",
                        "textTransform": "uppercase",
                        "borderBottom": "1px solid rgba(124,58,237,0.25)"
                    }),
                    dcc.Graph(id="bar-chart", style={"height": "200px"},
                              config={"displayModeBar": False})
                ]),
                html.Div(style={
                    "backgroundColor": "#111111",
                    "border": "1px solid rgba(124,58,237,0.25)",
                    "borderRadius": "12px", "overflow": "hidden"
                }, children=[
                    html.Div("Alert Level Breakdown", style={
                        "padding": "10px 14px", "fontSize": "12px",
                        "fontFamily": "'Fira Code', monospace", "fontWeight": "600",
                        "color": "#A78BFA", "letterSpacing": "0.5px",
                        "textTransform": "uppercase",
                        "borderBottom": "1px solid rgba(124,58,237,0.25)"
                    }),
                    dcc.Graph(id="donut-chart", style={"height": "200px"},
                              config={"displayModeBar": False})
                ])
            ])
        ]),

        # Counties table
        html.Div(style={"padding": "0 24px 28px 24px"}, children=[
            html.Div(style={
                "backgroundColor": "#111111",
                "border": "1px solid rgba(124,58,237,0.25)",
                "borderRadius": "12px", "overflow": "hidden"
            }, children=[
                html.Div("Affected Counties", style={
                    "padding": "10px 14px", "fontSize": "12px",
                    "fontFamily": "'Fira Code', monospace", "fontWeight": "600",
                    "color": "#A78BFA", "letterSpacing": "0.5px",
                    "textTransform": "uppercase",
                    "borderBottom": "1px solid rgba(124,58,237,0.25)"
                }),
                html.Div(id="counties-table",
                    style={"maxHeight": "300px", "overflowY": "auto"})
            ])
        ]),

        # ── Watchzone persistent store (localStorage) ─────────────────────
        dcc.Store(id="watchzone-store", storage_type="local", data=""),

        # ── AI Situation Report modal ──────────────────────────────────────
        dcc.Store(id="sitrep-store", data=""),
        html.Div(id="sitrep-modal", style={"display": "none"}, children=[
            html.Div(className="sitrep-overlay", children=[
                html.Div(className="sitrep-card", children=[
                    # Header
                    html.Div(className="sitrep-card-header", children=[
                        html.Div([
                            html.Span("⚡", style={"fontSize": "16px"}),
                            html.Span("AI Situation Report", style={
                                "fontFamily": "'Fira Code', monospace",
                                "fontSize": "13px", "fontWeight": "600",
                                "color": "#A78BFA", "marginLeft": "8px",
                                "letterSpacing": "0.5px", "textTransform": "uppercase"
                            })
                        ], style={"display": "flex", "alignItems": "center"}),
                        html.Button("✕", id="sitrep-close", className="sitrep-close-btn", n_clicks=0)
                    ]),
                    # Body
                    html.Div(id="sitrep-body", className="sitrep-card-body"),
                    # Footer
                    html.Div(className="sitrep-card-footer", children=[
                        html.Button(
                            "Copy to Clipboard",
                            id="sitrep-copy-btn",
                            className="sitrep-copy-btn",
                            n_clicks=0
                        ),
                        html.Span(id="sitrep-copy-feedback", style={
                            "fontSize": "11px", "color": "#76FF7A",
                            "fontFamily": "'Fira Code', monospace",
                            "marginLeft": "10px"
                        })
                    ])
                ])
            ])
        ])
    ]
)

@app.callback(
    [Output("last-updated",  "children"),
     Output("stat-cards",    "children"),
     Output("map-container", "children"),
     Output("bar-chart",     "figure"),
     Output("donut-chart",   "figure"),
     Output("counties-table","children")],
    Input("refresh", "n_intervals"),
    Input("watchzone-store", "data")
)
def update_ui(n, watchzone):
    # Trigger update if data is stale or never loaded
    if state["last_update"] == "Never" and not state["updating"]:
        print("Dashboard triggered data update...")
        t = threading.Thread(target=run_update, daemon=True)
        t.start()
    s   = state["summary"]

    # ── Watchzone filter ──────────────────────────────────────────────────
    wz = (watchzone or "").strip().upper()
    all_affected = s.get("affected_counties", [])
    if wz:
        affected = [
            c for c in all_affected
            if wz in (c.get("state") or "").upper()
            or wz in (c.get("county") or "").upper()
        ]
    else:
        affected = all_affected

    filtered_pop   = sum(c.get("population", 0) for c in affected)
    pop_display    = f"{filtered_pop:,}" if filtered_pop else "N/A"
    suffix         = " ★" if wz else ""

    def card(value, label, color="#A78BFA", glow_class="glow-purple"):
        return html.Div(style={
            "backgroundColor": "#111111",
            "borderRadius": "12px",
            "padding": "16px 20px",
            "minWidth": "130px",
            "flex": "1",
            "border": f"1px solid {color}33",
            "transition": "all 200ms ease",
            "cursor": "pointer"
        }, children=[
            html.Div(str(value), style={
                "fontSize": "28px", "fontWeight": "700",
                "color": color, "lineHeight": "1",
                "fontFamily": "'Fira Code', monospace",
                "textShadow": f"0 0 10px {color}80"
            }),
            html.Div(label, style={
                "fontSize": "10px", "color": "rgba(255,255,255,0.45)",
                "marginTop": "6px", "letterSpacing": "0.8px",
                "textTransform": "uppercase", "fontWeight": "500"
            })
        ])

    stat_cards = [
        card(s.get("warnings_count", 0),   "Active Warnings",              "#FF6666"),
        card(len(affected),                 "Affected Counties" + suffix,   "#F97316"),
        card(pop_display,                   "Population at Risk" + suffix,  "#F97316"),
        card(s.get("spc_zones", 0),        "SPC Outlook Zones",            "#76FF7A"),
        card(s.get("earthquakes", 0),      "Earthquakes M2.5+",            "#A78BFA"),
        card(s.get("active_storms", 0),    "Active Hurricanes",            "#F97316"),
        card(s.get("wildfires", 0),        "Fire Detections",              "#FF4500"),
    ]

    # Map — inject yellow highlight layer for watchzone when active
    map_html_source = state.get("map_html", "")
    if not map_html_source:
        map_content = html.P(
            "Map loading... Data update in progress.",
            style={"color": "#666", "padding": "20px", "textAlign": "center"}
        )
    elif wz:
        # Build minimal filtered GeoJSON to embed in the script (cap at 50 features)
        matching_feats = [
            {"type": "Feature",
             "geometry": f.get("geometry"),
             "properties": {"event": (f.get("properties") or {}).get("event", "")}}
            for f in state.get("warnings", {}).get("features", [])
            if f.get("geometry")
            and wz in ((f.get("properties") or {}).get("areaDesc", "")).upper()
        ][:50]
        geojson_str = json.dumps({"type": "FeatureCollection", "features": matching_feats})
        inject = (
            "\n<script>\n"
            "(function(){\n"
            f"  var _g={geojson_str};\n"
            "  var _t=setInterval(function(){\n"
            "    var m=null;\n"
            "    Object.keys(window).forEach(function(k){\n"
            "      if(/^map_/.test(k)&&window[k]&&window[k].addLayer)m=window[k];\n"
            "    });\n"
            "    if(!m)return;\n"
            "    clearInterval(_t);\n"
            "    if(_g.features&&_g.features.length){\n"
            "      L.geoJSON(_g,{style:{color:'#FFD700',weight:3,"
            "fillOpacity:0.15,fillColor:'#FFD700'}}).addTo(m);\n"
            "    }\n"
            "  },250);\n"
            "})();\n"
            "</script>\n"
            "</html>"
        )
        before, sep, _ = map_html_source.rpartition("</html>")
        patched = (before + inject) if sep else map_html_source
        map_content = html.Iframe(srcDoc=patched, style={"width": "100%", "height": "480px", "border": "none"})
    else:
        map_content = html.Iframe(
            srcDoc=map_html_source,
            style={"width": "100%", "height": "480px", "border": "none"}
        )

    # Bar chart
    bar_fig = go.Figure()
    if affected:
        phenom_counts = Counter(c.get("phenom","") for c in affected)
        labels = [phenom_names.get(p, p) for p in phenom_counts]
        colors_list = [hazard_colors.get(p, hazard_colors["default"]) for p in phenom_counts]
        bar_fig.add_trace(go.Bar(
            x=labels, y=list(phenom_counts.values()),
            marker_color=colors_list,
            text=list(phenom_counts.values()),
            textposition="outside",
            textfont=dict(color="white", size=10)
        ))
    bar_fig.update_layout(
        paper_bgcolor="#111111", plot_bgcolor="#111111",
        font=dict(color="rgba(255,255,255,0.7)", size=10, family="Fira Sans"),
        margin=dict(l=10, r=10, t=10, b=40),
        xaxis=dict(tickfont=dict(size=9), gridcolor="rgba(124,58,237,0.15)"),
        yaxis=dict(gridcolor="rgba(124,58,237,0.15)", showticklabels=False),
        showlegend=False
    )

    # Donut chart
    donut_fig = go.Figure()
    if affected:
        sig_counts = {}
        for c in affected:
            sig = c.get("sig", "Unknown")
            sig_counts[sig] = sig_counts.get(sig, 0) + 1
        sig_colors = {"Warning":"#FF0000","Watch":"#FF9900",
                      "Advisory":"#FFFF00","Statement":"#00BFFF"}
        donut_fig.add_trace(go.Pie(
            labels=list(sig_counts.keys()),
            values=list(sig_counts.values()),
            hole=0.5,
            marker_colors=[sig_colors.get(s,"#888") for s in sig_counts],
            textfont=dict(color="white", size=10),
            textposition="inside"
        ))
    donut_fig.update_layout(
        paper_bgcolor="#111111", font=dict(color="rgba(255,255,255,0.7)", size=10, family="Fira Sans"),
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(font=dict(color="rgba(255,255,255,0.65)"), bgcolor="#111111"),
        showlegend=True
    )

    # Counties table
    if not affected:
        no_data_msg = (
            f"No counties matching '{wz}' found"
            if wz else "No active warnings detected"
        )
        counties_html = html.P(no_data_msg,
                               style={
                                   "color": "rgba(255,255,255,0.3)",
                                   "fontSize": "13px",
                                   "padding": "16px",
                                   "fontFamily": "'Fira Code', monospace"
                               })
    else:
        hstyle = {
            "padding": "8px 12px", "fontSize": "10px",
            "fontFamily": "'Fira Code', monospace",
            "color": "rgba(255,255,255,0.45)",
            "textAlign": "left",
            "borderBottom": "1px solid rgba(124,58,237,0.25)",
            "backgroundColor": "#111111",
            "textTransform": "uppercase", "letterSpacing": "0.5px"
        }
        cstyle = {
            "padding": "8px 12px", "fontSize": "12px",
            "fontFamily": "'Fira Sans', sans-serif",
            "borderBottom": "1px solid rgba(255,255,255,0.04)"
        }
        header = html.Tr([
            html.Th(h, style=hstyle)
            for h in ["County", "State", "Population", "Event", "Level"]
        ])
        rows = []
        for i, c in enumerate(affected):
            sig   = c.get("sig", "")
            color = {"Warning": "#FF6666", "Watch": "#F97316", "Advisory": "#FFFF00"}.get(sig, "rgba(255,255,255,0.7)")
            rows.append(html.Tr([
                html.Td(c.get("county",""),                style=cstyle),
                html.Td(c.get("state",""),                 style=cstyle),
                html.Td(f"{c.get('population',0):,}",      style={**cstyle, "fontFamily": "'Fira Code', monospace"}),
                html.Td(c.get("event",""),                 style=cstyle),
                html.Td(sig, style={**cstyle, "color": color, "fontWeight": "700",
                                    "fontFamily": "'Fira Code', monospace",
                                    "textShadow": f"0 0 8px {color}60"})
            ], style={"backgroundColor": "#111111" if i%2==0 else "rgba(124,58,237,0.04)"}))
        counties_html = html.Table(
            [html.Thead(header), html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse"}
        )

    return (
        f"Last update: {state['last_update']}",
        stat_cards,
        map_content,
        bar_fig,
        donut_fig,
        counties_html
    )


@app.callback(
    Output("sitrep-modal",  "style"),
    Output("sitrep-body",   "children"),
    Output("sitrep-store",  "data"),
    Input("sitrep-btn",   "n_clicks"),
    Input("sitrep-close", "n_clicks"),
    prevent_initial_call=True
)
def handle_sitrep_modal(btn_clicks, close_clicks):
    """Show modal with AI situation report, or hide it on close."""
    if dash.ctx.triggered_id == "sitrep-close":
        return {"display": "none"}, dash.no_update, dash.no_update

    report_text, raw_text = generate_sitrep()

    # Split on blank lines to render paragraphs
    paragraphs = [
        html.P(para.strip(), style={
            "margin": "0 0 14px 0", "lineHeight": "1.75",
            "color": "rgba(255,255,255,0.85)", "fontSize": "13px",
            "fontFamily": "'Fira Sans', sans-serif"
        })
        for para in report_text.strip().split("\n\n") if para.strip()
    ]

    modal_visible = {"display": "block"}
    return modal_visible, paragraphs, raw_text or ""


app.clientside_callback(
    """
    function(n_clicks, raw_text) {
        if (!n_clicks || !raw_text) return "";
        try {
            navigator.clipboard.writeText(raw_text);
        } catch (e) {
            // Fallback for non-HTTPS environments
            var ta = document.createElement("textarea");
            ta.value = raw_text;
            ta.style.position = "fixed";
            ta.style.opacity = "0";
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
        }
        return "Copied!";
    }
    """,
    Output("sitrep-copy-feedback", "children"),
    Input("sitrep-copy-btn", "n_clicks"),
    dash.dependencies.State("sitrep-store", "data"),
    prevent_initial_call=True
)


# ── Watchzone callbacks ───────────────────────────────────────────────────

# 1. User types → save to localStorage store
app.clientside_callback(
    "function(v) { return (v !== undefined && v !== null) ? v : ''; }",
    Output("watchzone-store", "data"),
    Input("watchzone-input", "value"),
    prevent_initial_call=True
)

# 2. Page load / store change → restore input value
app.clientside_callback(
    "function(stored) { return stored || ''; }",
    Output("watchzone-input", "value"),
    Input("watchzone-store", "data")
)

# 3. Store change → toggle clear button visibility + active label text
app.clientside_callback(
    """
    function(stored) {
        var active = stored && stored.trim().length > 0;
        var clearStyle = active
            ? {display: 'inline-flex', alignItems: 'center', justifyContent: 'center'}
            : {display: 'none'};
        var label = active ? 'Watching: ' + stored.trim().toUpperCase() : '';
        return [clearStyle, label];
    }
    """,
    [Output("watchzone-clear-btn", "style"),
     Output("watchzone-active-label", "children")],
    Input("watchzone-store", "data")
)

# 4. Clear button → wipe store
app.clientside_callback(
    "function(n) { return n > 0 ? '' : window.dash_clientside.no_update; }",
    Output("watchzone-store", "data", allow_duplicate=True),
    Input("watchzone-clear-btn", "n_clicks"),
    prevent_initial_call=True
)

# 5. Store change → map badge visibility + text
app.clientside_callback(
    """
    function(stored) {
        if (stored && stored.trim()) {
            return [
                'Filtered to: ' + stored.trim().toUpperCase(),
                {display: 'block', background: 'rgba(255,215,0,0.12)',
                 borderBottom: '2px solid #FFD700',
                 padding: '6px 14px', fontSize: '11px',
                 fontFamily: "'Fira Code', monospace",
                 color: '#FFD700', letterSpacing: '0.5px'}
            ];
        }
        return ['', {display: 'none'}];
    }
    """,
    [Output("watchzone-map-badge", "children"),
     Output("watchzone-map-badge", "style")],
    Input("watchzone-store", "data")
)


# ─────────────────────────────────────────────
# START
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*50)
    print("  NATIONAL HAZARD MONITOR")
    print("  Starting background data update...")
    print("="*50)
    # Load cached data immediately so map works before first update
    cached = load_cache()
    if cached:
        state.update(cached)
        print("  Loaded cached data from previous run")
    schedule_updates(interval_minutes=30)
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
