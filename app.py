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

def find_affected_counties(warnings_geojson, pop_data, counties_geojson):
    """Find counties intersecting warning polygons using bbox overlap."""
    if not counties_geojson or not warnings_geojson:
        return [], 0

    warning_bounds = get_warning_bounds(warnings_geojson)
    if not warning_bounds:
        return [], 0

    affected = []
    total_pop = 0
    seen_fips = set()

    for feat in counties_geojson.get("features", []):
        try:
            fips  = feat.get("id", "")
            if fips in seen_fips:
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
                    state_code = fips[:2]
                    pop = pop_data.get(fips, 0)
                    total_pop += pop
                    seen_fips.add(fips)
                    w_props  = bounds["props"]
                    phenom   = w_props.get("phenom", "")
                    sig      = w_props.get("sig", "")
                    sig_name = {"W":"Warning","A":"Watch","Y":"Advisory","S":"Statement"}.get(str(sig).strip(), str(sig))
                    affected.append({
                        "county":     props.get("NAME", "Unknown"),
                        "state":      state_fips.get(state_code, state_code),
                        "fips":       fips,
                        "population": pop,
                        "phenom":     phenom,
                        "sig":        sig_name,
                        "event":      phenom_names.get(str(phenom).strip().upper(), phenom) + " " + sig_name
                    })
                    break
        except Exception:
            continue

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
        f"  - {f['properties'].get('gaugelid','')}: {f['properties'].get('status','')} — {f['properties'].get('waterbody','')}, {f['properties'].get('state','')}"
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
            f"  - {f.get('properties',{}).get('waterbody','')} ({f.get('properties',{}).get('status','')})"
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
                "$select": "disasterNumber,state,declarationDate,disasterType,declarationTitle,designatedArea",
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
            dtype = rec.get("disasterType", "")
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
    """Fetch elevated volcano alerts from GDACS (Orange/Red level globally)."""
    print("Downloading GDACS volcano alerts...")
    try:
        r = requests.get(
            "https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP?eventlist=VO",
            timeout=20
        )
        r.raise_for_status()
        data = r.json()
        alert_colors = {"orange": "#FF8800", "red": "#FF0000"}
        features = []
        for feat in data.get("features", []):
            try:
                props = feat.get("properties") or {}
                # GDACS API ignores eventlist=VO — must filter by eventtype client-side
                if str(props.get("eventtype", "")).upper() != "VO":
                    continue
                alert = str(props.get("alertlevel", "")).lower()
                # Only show elevated (orange/red) current events to avoid clutter
                if alert not in ("orange", "red"):
                    continue
                if not props.get("iscurrent"):
                    continue
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates")
                if not coords:
                    continue
                # GDACS sometimes returns polygon geometry for uncertainty cones —
                # use the first coordinate pair as the point location
                if geom.get("type") == "Point":
                    lon, lat = coords[0], coords[1]
                elif geom.get("type") == "Polygon":
                    # centroid approximation from first ring's first point
                    ring = coords[0]
                    lon = sum(p[0] for p in ring) / len(ring)
                    lat = sum(p[1] for p in ring) / len(ring)
                else:
                    continue
                name = props.get("eventname") or props.get("name") or "Volcano"
                country = props.get("country", "")
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "name":    name,
                        "country": country,
                        "alert":   alert,
                        "color":   alert_colors.get(alert, "#FF8800"),
                        "score":   props.get("alertscore", ""),
                    }
                })
            except Exception:
                continue
        # Deduplicate by rounded coordinates (GDACS sends same event as multiple polygons)
        seen = set()
        unique = []
        for feat in features:
            coords = feat["geometry"]["coordinates"]
            key = (round(coords[0], 1), round(coords[1], 1))
            if key not in seen:
                seen.add(key)
                unique.append(feat)
        print(f"  Volcanoes: {len(unique)} elevated (Orange/Red) active alerts")
        return {"type": "FeatureCollection", "features": unique}
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
            "?where=1%3D1&outFields=SHELTER_NAME,ADDRESS,CITY,STATE,PET_FRIENDLY,CAPACITY"
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
                "fips":       fips,
                "county":     county_data.get("county", ""),
                "state":      county_data.get("state", ""),
                "population": county_data.get("population", 0),
                "event":      county_data.get("event", ""),
                "sig":        county_data.get("sig", ""),
                "phenom":     county_data.get("phenom", "")
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
    
    for tag_key, tag_val, infra_type, color, icon in infra_types:
        query = f'[out:json][timeout:15];node["{tag_key}"="{tag_val}"]({bbox_str});out body;'
        try:
            r = requests.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query}, timeout=20
            )
            r.raise_for_status()
            items = r.json().get("elements", [])
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
            print(f"  Overpass {infra_type} failed: {e}")
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
        if lat is not None and lng is not None:
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
    """Serves the full Mapbox GL JS map page."""
    MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN", "")
    html = f"""<!DOCTYPE html>
<html class="dark" lang="en">
<head>
    <meta charset="utf-8"/>
    <meta content="width=device-width, initial-scale=1.0" name="viewport"/>
    <title>National All-Hazards Monitor</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet"/>
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
    <script src="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/@turf/turf@6/turf.min.js"></script>
    <link href="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.css" rel="stylesheet">
    <!-- Chart.js removed — hazard overview now uses inline HTML bars -->
    <style>
        /* ── 1. RESET & BASE ─────────────────────────────── */
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Inter', sans-serif; background: #040f1b; overflow: hidden; color: #dde9fb; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}

        /* ── 2. COMPONENT CLASSES ────────────────────────── */
        .material-symbols-outlined {{ font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24; display: inline-block; line-height: 1; vertical-align: middle; }}
        .glass-panel {{ background: rgba(21,39,57,0.82); backdrop-filter: blur(4px); border: 1px solid rgba(88,191,255,0.1); }}
        .corner-bracket {{ position: absolute; width: 8px; height: 8px; border-color: #58bfff; border-style: solid; border-width: 0; }}
        .corner-tl {{ top: -1px; left: -1px; border-top-width: 2px; border-left-width: 2px; }}
        .corner-tr {{ top: -1px; right: -1px; border-top-width: 2px; border-right-width: 2px; }}
        .corner-bl {{ bottom: -1px; left: -1px; border-bottom-width: 2px; border-left-width: 2px; }}
        .corner-br {{ bottom: -1px; right: -1px; border-bottom-width: 2px; border-right-width: 2px; }}
        .live-pulse {{ animation: livePulse 2s infinite; border-radius: 50%; }}
        @keyframes livePulse {{
            0%   {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255,113,108,0.7); }}
            70%  {{ transform: scale(1);    box-shadow: 0 0 0 8px rgba(255,113,108,0); }}
            100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255,113,108,0); }}
        }}
        .nav-btn {{ display: flex; flex-direction: column; align-items: center; gap: 4px; width: 80px; padding: 8px 0; cursor: pointer; border: none; background: transparent; color: #64748b; transition: background 150ms, color 150ms; font-family: 'Inter', sans-serif; }}
        .nav-btn:hover {{ background: rgba(30,41,59,0.5); color: #58bfff; }}
        .nav-btn.active {{ background: rgba(88,191,255,0.1); color: #58bfff; border-left: 4px solid #58bfff; }}
        .nav-btn .nav-label {{ font-size: 9px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; }}
        #sitrep-overlay {{ display: none; position: fixed; inset: 0; z-index: 100; align-items: center; justify-content: center; background: rgba(4,15,27,0.85); backdrop-filter: blur(4px); padding: 16px; overflow-y: auto; }}
        #sitrep-overlay.open {{ display: flex; }}
        #popup {{
            position: absolute; z-index: 20;
            background: linear-gradient(135deg, rgba(0,8,20,0.98) 0%, rgba(0,20,40,0.98) 100%);
            border: 1px solid rgba(88,191,255,0.3); padding: 16px 18px;
            font-size: 12px; min-width: 220px; max-width: 280px;
            backdrop-filter: blur(4px); display: none;
            box-shadow: 0 0 40px rgba(0,0,0,0.6), 0 0 20px rgba(88,191,255,0.1);
            animation: popup-in 0.2s ease-out;
        }}
        @keyframes popup-in {{
            from {{ opacity: 0; transform: scale(0.95) translateY(4px); }}
            to   {{ opacity: 1; transform: scale(1)    translateY(0); }}
        }}
        #popup-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.08); }}
        #popup-title {{ font-size: 13px; font-weight: 700; color: white; margin: 0; }}
        #close-popup {{ cursor: pointer; color: rgba(255,255,255,0.3); font-size: 18px; line-height: 1; transition: color 0.2s; margin-left: 10px; }}
        #close-popup:hover {{ color: white; }}
        .popup-row {{ display: flex; justify-content: space-between; align-items: center; padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }}
        .popup-key {{ color: rgba(255,255,255,0.4); font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }}
        .popup-val {{ color: white; font-weight: 600; font-size: 12px; }}
        .mapboxgl-ctrl-group {{ background: rgba(11,27,42,0.96) !important; border: 1px solid rgba(88,191,255,0.15) !important; border-radius: 0 !important; }}
        .mapboxgl-ctrl-group button {{ background: transparent !important; }}
        .mapboxgl-ctrl-group button span {{ filter: invert(1) brightness(0.7); }}
        .mapboxgl-ctrl-group button:hover span {{ filter: invert(1); }}
        .mapboxgl-ctrl-attrib {{ display: none !important; }}
        #hover-tooltip {{
            position: absolute; z-index: 15; pointer-events: none;
            background: rgba(11,27,42,0.96); border: 1px solid rgba(88,191,255,0.3);
            padding: 7px 12px; font-size: 11px;
            display: none; box-shadow: 0 4px 20px rgba(0,0,0,0.6);
            white-space: nowrap; line-height: 1.6; color: #dde9fb;
        }}
        ::-webkit-scrollbar {{ width: 4px; }}
        ::-webkit-scrollbar-track {{ background: rgba(0,0,0,0.2); }}
        ::-webkit-scrollbar-thumb {{ background: rgba(88,191,255,0.3); border-radius: 2px; }}
        .threat-item {{ padding: 7px 10px; margin: 4px 0; background: rgba(88,191,255,0.05); border-left: 3px solid; font-size: 11px; }}
        .nri-section {{ margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(88,191,255,0.1); }}
        .nri-row {{ display: flex; justify-content: space-between; font-size: 10px; padding: 2px 0; }}
        .nri-label {{ color: rgba(160,172,189,0.8); }}
        .nri-val {{ font-weight: 600; }}
        #address-input:focus {{ border-bottom-color: #58bfff !important; outline: none; }}
        @media (max-width: 1100px) {{
            nav.sidebar {{ width: 200px !important; }}
            #stat-cards-wrap {{ left: 216px !important; }}
            #legend-wrap {{ left: 216px !important; }}
        }}
        @media (max-width: 768px) {{
            nav.sidebar {{ display: none; }}
            #stat-cards-wrap {{ left: 16px !important; width: calc(100vw - 32px) !important; }}
            #address-panel {{ width: calc(100vw - 32px) !important; right: 16px !important; bottom: auto !important; top: 70px !important; }}
            #legend-wrap {{ display: none !important; }}
        }}

        /* ── 3. UTILITY CLASSES (always last — highest cascade priority) ── */
        .fixed{{position:fixed}}.absolute{{position:absolute}}.relative{{position:relative}}
        .inset-0{{inset:0}}.top-0{{top:0}}.left-0{{left:0}}.right-0{{right:0}}.bottom-0{{bottom:0}}
        .top-4{{top:16px}}.bottom-4{{bottom:16px}}.left-4{{left:16px}}.right-4{{right:16px}}
        .left-1\/2{{left:50%}}.-translate-x-1\/2{{transform:translateX(-50%)}}
        .flex{{display:flex}}.grid{{display:grid}}.hidden{{display:none}}.block{{display:block}}.inline-block{{display:inline-block}}
        .flex-col{{flex-direction:column}}.flex-1{{flex:1 1 0%}}.flex-shrink-0{{flex-shrink:0}}
        .items-center{{align-items:center}}.items-baseline{{align-items:baseline}}.items-start{{align-items:flex-start}}.items-end{{align-items:flex-end}}
        .justify-between{{justify-content:space-between}}.justify-center{{justify-content:center}}
        .mt-auto{{margin-top:auto}}
        .grid-cols-2{{grid-template-columns:repeat(2,minmax(0,1fr))}}
        .gap-1{{gap:4px}}.gap-2{{gap:8px}}.gap-3{{gap:12px}}.gap-4{{gap:16px}}.gap-5{{gap:20px}}.gap-6{{gap:24px}}.gap-8{{gap:32px}}
        .h-full{{height:100%}}.w-full{{width:100%}}.w-fit{{width:-webkit-fit-content;width:fit-content}}
        .w-20{{width:80px}}.w-2{{width:8px}}.w-2\.5{{width:10px}}.w-6{{width:24px}}.w-8{{width:32px}}
        .h-2{{height:8px}}.h-2\.5{{height:10px}}.h-6{{height:24px}}.h-8{{height:32px}}
        .min-w-0{{min-width:0}}
        .z-10{{z-index:10}}.z-40{{z-index:40}}.z-50{{z-index:50}}
        .p-3{{padding:12px}}.p-4{{padding:16px}}.p-6{{padding:24px}}.p-8{{padding:32px}}
        .px-4{{padding-left:16px;padding-right:16px}}.px-5{{padding-left:20px;padding-right:20px}}.px-6{{padding-left:24px;padding-right:24px}}
        .py-1\.5{{padding-top:6px;padding-bottom:6px}}.py-2{{padding-top:8px;padding-bottom:8px}}
        .py-2\.5{{padding-top:10px;padding-bottom:10px}}.py-8{{padding-top:32px;padding-bottom:32px}}
        .mt-1{{margin-top:4px}}.mt-2{{margin-top:8px}}.mt-3{{margin-top:12px}}.mt-4{{margin-top:16px}}
        .mb-3{{margin-bottom:12px}}.mb-10{{margin-bottom:40px}}
        .text-xs{{font-size:12px;line-height:16px}}.text-sm{{font-size:14px;line-height:20px}}.text-\[10px\]{{font-size:10px}}
        .font-bold{{font-weight:700}}.font-mono{{font-family:monospace}}
        .uppercase{{text-transform:uppercase}}.tracking-widest{{letter-spacing:.1em}}.text-center{{text-align:center}}.text-right{{text-align:right}}
        .rounded-full{{border-radius:9999px}}
        .overflow-hidden{{overflow:hidden}}.overflow-y-auto{{overflow-y:auto}}
        .cursor-pointer{{cursor:pointer}}.pointer-events-auto{{pointer-events:auto}}.pointer-events-none{{pointer-events:none}}
        .select-none{{user-select:none}}
        .transition-all{{transition:all 150ms cubic-bezier(.4,0,.2,1)}}
        .shadow-2xl{{box-shadow:0 25px 50px -12px rgba(0,0,0,.25)}}
        .backdrop-blur-xl{{backdrop-filter:blur(6px)}}.backdrop-blur-2xl{{backdrop-filter:blur(8px)}}
        .bg-background{{background-color:#040f1b}}.text-on-surface{{color:#dde9fb}}.text-on-primary{{color:#000d18}}
        .text-primary{{color:#58bfff !important}}.text-slate-500{{color:#64748b}}.text-slate-400{{color:#94a3b8}}
        .bg-primary{{background-color:#58bfff}}.bg-error{{background-color:#ff716c}}
        .bg-primary\/5{{background-color:rgba(88,191,255,.05)}}.bg-primary\/10{{background-color:rgba(88,191,255,.1) !important}}.bg-primary\/20{{background-color:rgba(88,191,255,.2)}}
        .bg-surface-variant\/60{{background-color:rgba(21,39,57,.6)}}
        .bg-slate-800\/50{{background-color:rgba(30,41,59,.5)}}.bg-slate-900\/60{{background-color:rgba(15,23,42,.6)}}.bg-slate-950\/80{{background-color:rgba(2,6,23,.8)}}
        .border{{border-width:1px;border-style:solid}}.border-r{{border-right-width:1px;border-right-style:solid}}
        .border-r-2{{border-right-width:2px;border-right-style:solid}}.border-t-2{{border-top-width:2px;border-top-style:solid}}
        .border-b-2{{border-bottom-width:2px;border-bottom-style:solid}}.border-b{{border-bottom-width:1px;border-bottom-style:solid}}
        .border-l-2{{border-left-width:2px !important;border-left-style:solid !important}}.border-l-4{{border-left-width:4px !important;border-left-style:solid !important}}
        .border-primary{{border-color:#58bfff}}.border-primary\/10{{border-color:rgba(88,191,255,.1)}}.border-primary\/20{{border-color:rgba(88,191,255,.2)}}
        .border-primary\/30{{border-color:rgba(88,191,255,.3)}}.border-primary\/40{{border-color:rgba(88,191,255,.4)}}.border-primary\/60{{border-color:rgba(88,191,255,.6)}}
        .border-error\/60{{border-color:rgba(255,113,108,.6)}}.border-tertiary\/60{{border-color:rgba(172,137,255,.6)}}
        .border-orange-500\/60{{border-color:rgba(249,115,22,.6)}}.border-amber-500\/60{{border-color:rgba(245,158,11,.6)}}
        .border-outline-variant\/20{{border-color:rgba(61,73,87,.2)}}
        .hover\:bg-primary\/5:hover{{background-color:rgba(88,191,255,.05)}}
        .hover\:bg-primary-dim:hover{{background-color:#00a8ee}}
        .hover\:bg-slate-800\/50:hover{{background-color:rgba(30,41,59,.5)}}
        .hover\:text-primary:hover{{color:#58bfff}}
        .hover\:border-primary:hover{{border-color:#58bfff}}
        @media(min-width:768px){{.md\:flex-row{{flex-direction:row}}}}
        .glow-purple{{box-shadow:0 0 20px rgba(172,137,255,.3)}}

        /* ── HAZARD ROWS ─────────────────────────────────── */
        .haz-row {{
            display: flex; align-items: center; justify-content: space-between;
            font-size: 11px; color: rgba(255,255,255,0.6); padding: 2px 0;
        }}
        .haz-row .haz-bar-wrap {{
            flex: 1; margin: 0 8px; height: 3px;
            background: rgba(255,255,255,0.07); border-radius: 2px; overflow: hidden;
        }}
        .haz-bar {{
            height: 100%; border-radius: 2px; width: 0%;
            transition: width 0.8s cubic-bezier(.4,0,.2,1);
        }}
        .haz-count {{
            font-family: 'Space Grotesk', sans-serif; font-weight: 700;
            font-size: 12px; min-width: 28px; text-align: right;
        }}

        /* ── SKELETON LOADING ───────────────────────────── */
        @keyframes shimmer {{
            0%   {{ background-position: -200% 0; }}
            100% {{ background-position:  200% 0; }}
        }}
        .skel {{
            background: linear-gradient(90deg,rgba(88,191,255,0.07) 25%,rgba(88,191,255,0.18) 50%,rgba(88,191,255,0.07) 75%);
            background-size: 200% 100%;
            animation: shimmer 1.4s ease-in-out infinite;
            border-radius: 4px;
            color: transparent !important;
            min-width: 40px; display: inline-block; vertical-align: middle;
        }}

        /* ── STAT DELTAS ────────────────────────────────── */
        .stat-delta {{
            font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
            font-family: 'Space Grotesk', sans-serif;
            transition: opacity 0.3s;
        }}
        .stat-delta:empty {{ display: none; }}
        .stat-delta.up   {{ color: #ff716c; }}
        .stat-delta.down {{ color: #00e676; }}

        /* ── LAYER TOGGLE GROUPS ──────────────────────────── */
        .layer-group-header {{
            font-size: 9px; color: #58bfff; font-weight: 700;
            letter-spacing: 2px; text-transform: uppercase;
            padding: 4px 2px 6px; margin-bottom: 4px;
            display: flex; align-items: center; gap: 6px;
            border-bottom: 1px solid rgba(88,191,255,0.12);
            font-family: 'Space Grotesk', sans-serif;
        }}
        .layer-group-header:hover {{ background: rgba(88,191,255,0.06); }}
        .layer-group-list {{ display: flex; flex-direction: column; gap: 1px; }}
        .layer-toggle-count {{
            font-size: 9px; font-weight: 700; letter-spacing: 0.5px;
            margin-left: auto; padding: 1px 5px;
            background: rgba(88,191,255,0.1); border-radius: 8px;
            font-family: 'Space Grotesk', sans-serif;
            flex-shrink: 0;
        }}
        .layer-toggle {{
            background: transparent; border: none;
            padding: 6px 8px; cursor: pointer;
            display: flex; align-items: center; gap: 10px;
            width: 100%; text-align: left;
            font-family: 'Inter', sans-serif;
            border-radius: 3px;
            transition: background 0.15s;
        }}
        .layer-toggle:hover {{ background: rgba(88,191,255,0.08); }}
        .layer-toggle-dot {{
            width: 10px; height: 10px; border-radius: 50%;
            border: 1.5px solid rgba(255,255,255,0.25);
            background: transparent; flex-shrink: 0;
            transition: background 0.15s, border-color 0.15s, box-shadow 0.15s;
        }}
        .layer-toggle:hover .layer-toggle-dot {{ box-shadow: 0 0 6px rgba(88,191,255,0.5); }}
        .layer-toggle-label {{
            font-size: 11px; font-weight: 500;
            color: rgba(255,255,255,0.45);
            transition: color 0.15s; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis;
        }}
        @keyframes layer-dot-spin {{
            0%   {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
        .layer-toggle-dot.loading {{
            background: transparent !important;
            border-color: rgba(88,191,255,0.35) !important;
            border-top-color: #58bfff !important;
            animation: layer-dot-spin 0.7s linear infinite;
        }}
        .layer-toggle-count.empty {{
            background: rgba(255,255,255,0.04);
            color: rgba(255,255,255,0.35) !important;
            font-style: italic;
        }}
        body.light .layer-group-header {{ color: #0080cc; border-color: rgba(88,191,255,0.25); }}
        body.light .layer-toggle-label {{ color: rgba(30,40,55,0.55); }}
        body.light .layer-toggle:hover {{ background: rgba(88,191,255,0.15); }}

        /* ── BASEMAP BUTTONS ─────────────────────────────── */
        .bm-btn {{
            background: rgba(88,191,255,0.06);
            border: 1px solid rgba(88,191,255,0.15);
            color: rgba(255,255,255,0.5);
            font-size: 10px; padding: 5px 4px;
            cursor: pointer; border-radius: 4px;
            font-family: 'Inter', sans-serif;
            transition: all 0.15s; text-align: center;
        }}
        .bm-btn:hover {{ background: rgba(88,191,255,0.15); color: #a8d8ff; }}
        .bm-btn.active {{ background: rgba(88,191,255,0.22); border-color: rgba(88,191,255,0.5); color: #58bfff; }}

        /* ── COUNTY HERO PANEL ───────────────────────────── */
        #county-hero {{
            position: fixed; inset: 0; z-index: 150;
            display: flex; align-items: center; justify-content: center;
            background: rgba(4,15,27,0.93); backdrop-filter: blur(14px);
            transition: opacity 0.35s ease;
        }}
        #county-hero .hero-box {{
            text-align: center; max-width: 500px; padding: 0 28px; width: 100%;
        }}
        #hero-input {{
            width: 100%; background: rgba(255,255,255,0.05);
            border: 1px solid rgba(88,191,255,0.35); border-radius: 6px;
            color: #dde9fb; font-size: 14px; padding: 13px 16px;
            outline: none; font-family: 'Inter', sans-serif;
            transition: border-color 0.2s;
        }}
        #hero-input:focus {{ border-color: #58bfff; }}
        #hero-input::placeholder {{ color: #3d5066; }}
        .hero-search-btn {{
            background: #58bfff; border: none; color: #000d18; font-weight: 700;
            font-size: 11px; letter-spacing: 2px; padding: 13px 24px; cursor: pointer;
            font-family: 'Space Grotesk', sans-serif; text-transform: uppercase;
            border-radius: 6px; white-space: nowrap; transition: background 0.15s;
        }}
        .hero-search-btn:hover {{ background: #7dceff; }}
        .hero-examples span {{
            display: inline-block; margin: 3px 4px;
            background: rgba(88,191,255,0.08); border: 1px solid rgba(88,191,255,0.2);
            color: #58bfff; font-size: 10px; padding: 3px 9px; border-radius: 20px;
            cursor: pointer; transition: background 0.15s;
        }}
        .hero-examples span:hover {{ background: rgba(88,191,255,0.18); }}

        /* ── SEVERITY BAR ───────────────────────────────── */
        #severity-bar {{
            position: fixed; top: 0; left: 0; right: 0; z-index: 200;
            height: 3px; background: rgba(10,20,35,0.6); pointer-events: none;
        }}
        #severity-fill {{
            height: 100%; width: 0%;
            transition: width 2s cubic-bezier(.4,0,.2,1), background-color 1s;
            background: #00CC66;
        }}
        #severity-label {{
            position: fixed; top: 8px; right: 96px; z-index: 201;
            font-size: 8px; font-weight: 700; letter-spacing: 2px;
            color: rgba(255,255,255,0.35); text-transform: uppercase;
            pointer-events: none; font-family: 'Inter', sans-serif;
            transition: color 1s;
        }}
        @keyframes sev-pulse {{
            0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }}
        }}
        .sev-critical {{ animation: sev-pulse 1s ease-in-out infinite; }}

        /* ── LIGHT MODE — panels only, map stays dark ──── */
        body.light {{ background: #040f1b; color: #1a2332; }}
        body.light .glass-panel {{
            background: rgba(228,240,252,0.92);
            border-color: rgba(88,191,255,0.25);
            color: #1a2332;
        }}
        body.light nav.sidebar {{
            background: rgba(228,240,252,0.96) !important;
            border-color: rgba(88,191,255,0.2) !important;
        }}
        body.light header {{
            background: rgba(228,240,252,0.92) !important;
        }}
        body.light .nav-btn {{ color: #3d5066; }}
        body.light .nav-btn:hover {{ background: rgba(88,191,255,0.15); color: #0080cc; }}
        body.light .nav-btn.active {{ color: #0080cc; border-left-color: #0080cc !important; }}
        body.light #popup {{
            background: rgba(228,240,252,0.99) !important;
            border-color: rgba(88,191,255,0.4);
            color: #1a2332;
        }}
        body.light #sitrep-overlay > div {{ background: rgba(228,240,252,0.97) !important; color: #1a2332; }}
        body.light #severity-bar {{ background: rgba(180,200,220,0.6); }}

        /* ── KEYBOARD SHORTCUTS MODAL ───────────────────── */
        #shortcuts-modal {{
            display: none; position: fixed; inset: 0; z-index: 300;
            background: rgba(4,15,27,0.88);
            align-items: center; justify-content: center;
        }}
        #shortcuts-modal.open {{ display: flex; }}
        .shortcut-row {{
            display: flex; align-items: center; justify-content: space-between;
            padding: 9px 0; border-bottom: 1px solid rgba(88,191,255,0.08);
            gap: 32px; font-size: 12px; color: #a0acbd;
        }}
        .shortcut-row:last-child {{ border-bottom: none; }}
        kbd {{
            background: rgba(88,191,255,0.1); border: 1px solid rgba(88,191,255,0.3);
            border-radius: 4px; padding: 2px 9px; font-size: 11px;
            font-family: monospace; color: #58bfff; font-weight: 700;
            min-width: 28px; text-align: center; display: inline-block;
        }}
    </style>
</head>
<body class="bg-background text-on-surface overflow-hidden select-none">

<!-- County Hero Search Panel — front-and-center on load -->
<div id="county-hero">
  <div class="hero-box">
    <div style="font-size:9px;letter-spacing:4px;color:#58bfff;font-weight:700;text-transform:uppercase;margin-bottom:18px;">National All-Hazards Monitor</div>
    <h1 style="font-family:'Space Grotesk',sans-serif;font-size:26px;font-weight:700;color:#dde9fb;line-height:1.3;margin-bottom:10px;">What is threatening your county?</h1>
    <p style="font-size:12px;color:#4a6280;margin-bottom:28px;line-height:1.7;">Real-time hazards, threat score, and a 60-second briefing you can share with your team.</p>
    <div style="display:flex;gap:8px;margin-bottom:14px;">
      <input id="hero-input" type="text" placeholder="Harris County TX  ·  Miami-Dade  ·  Los Angeles CA" autocomplete="off" />
      <button class="hero-search-btn" onclick="heroSearch()">ANALYZE</button>
    </div>
    <div class="hero-examples" style="margin-bottom:20px;">
      <span onclick="heroQuick('Harris County TX')">Harris County TX</span>
      <span onclick="heroQuick('Miami-Dade FL')">Miami-Dade FL</span>
      <span onclick="heroQuick('Los Angeles CA')">Los Angeles CA</span>
      <span onclick="heroQuick('Cook County IL')">Cook County IL</span>
      <span onclick="heroQuick('King County WA')">King County WA</span>
    </div>
    <div onclick="dismissHero()" style="font-size:10px;color:#2d3f50;cursor:pointer;letter-spacing:1px;text-transform:uppercase;">Skip — show full map</div>
  </div>
</div>

<!-- Severity bar — top 3px strip, color reflects current national threat level -->
<div id="severity-bar"><div id="severity-fill"></div></div>
<div id="severity-label">THREAT LEVEL CALCULATING...</div>

<div id="map"></div>

<!-- Viewport corner brackets -->
<div class="fixed top-4 left-4 w-6 h-6 border-t-2 border-l-2 border-primary/40 z-50 pointer-events-none"></div>
<div class="fixed top-4 right-4 w-6 h-6 border-t-2 border-r-2 border-primary/40 z-50 pointer-events-none"></div>
<div class="fixed bottom-4 left-4 w-6 h-6 border-b-2 border-l-2 border-primary/40 z-50 pointer-events-none"></div>
<div class="fixed bottom-4 right-4 w-6 h-6 border-b-2 border-r-2 border-primary/40 z-50 pointer-events-none"></div>

<!-- Layers sidebar -->
<nav class="sidebar fixed left-0 top-0 h-full z-40 flex flex-col" style="background:rgba(2,6,23,0.96);border-right:1px solid rgba(88,191,255,0.1);width:240px;">
    <div style="padding:18px 18px 14px;border-bottom:1px solid rgba(88,191,255,0.1);display:flex;align-items:center;gap:10px;flex-shrink:0;">
        <div style="width:28px;height:28px;background:rgba(88,191,255,0.15);display:flex;align-items:center;justify-content:center;flex-shrink:0;">
            <span class="material-symbols-outlined" style="font-size:18px;color:#58bfff;">layers</span>
        </div>
        <div style="font-size:10px;letter-spacing:2.5px;color:#58bfff;font-weight:700;font-family:'Space Grotesk',sans-serif;">MAP LAYERS</div>
    </div>
    <div id="sidebar-layers-body" style="flex:1 1 auto;overflow-y:auto;overflow-x:hidden;padding:12px 14px 8px;display:flex;flex-direction:column;min-height:0;"></div>
    <div style="padding:10px 14px;border-top:1px solid rgba(88,191,255,0.1);flex-shrink:0;display:flex;gap:6px;">
        <button onclick="locateMe()" title="Near me (N)" style="flex:1;background:rgba(88,191,255,0.08);border:1px solid rgba(88,191,255,0.25);color:#a8d8ff;font-size:9px;font-weight:700;letter-spacing:1.5px;padding:7px 4px;cursor:pointer;font-family:'Inter',sans-serif;text-transform:uppercase;">⌖ Near Me</button>
        <button onclick="document.documentElement.requestFullscreen?.()" title="Fullscreen (F)" style="flex:1;background:transparent;border:1px solid rgba(88,191,255,0.25);color:#64748b;font-size:9px;font-weight:700;letter-spacing:1.5px;padding:7px 4px;cursor:pointer;font-family:'Inter',sans-serif;text-transform:uppercase;">⛶ Full</button>
    </div>
</nav>

<!-- Location prompt banner -->
<div id="location-prompt" style="
    position:fixed; bottom:80px; left:50%; transform:translateX(-50%);
    z-index:50; display:flex; align-items:center; gap:12px;
    background:rgba(4,15,27,0.95); border:1px solid rgba(88,191,255,0.3);
    padding:12px 20px; backdrop-filter:blur(4px);
    box-shadow:0 4px 24px rgba(0,0,0,0.5);
    font-family:'Inter',sans-serif; white-space:nowrap;
">
    <span class="material-symbols-outlined" style="color:#58bfff;font-size:20px;">location_on</span>
    <span style="font-size:12px;color:#dde9fb;">Show active warnings near your location?</span>
    <button onclick="locateMe()" style="
        background:#58bfff; border:none; color:#000d18; font-weight:700;
        font-size:10px; letter-spacing:1px; padding:6px 14px; cursor:pointer;
        font-family:'Space Grotesk',sans-serif; text-transform:uppercase;
    ">ALLOW</button>
    <button onclick="document.getElementById('location-prompt').remove()" style="
        background:transparent; border:none; color:#64748b; cursor:pointer; font-size:18px; padding:0 4px;
    ">&times;</button>
</div>

<!-- Top Header -->
<header class="fixed top-0 left-1/2 -translate-x-1/2 z-50 flex items-center gap-6 bg-slate-900/60 backdrop-blur-xl mt-3 w-fit border border-primary/20 px-6 py-2" style="box-shadow:0 0 15px rgba(88,191,255,0.1);">
    <div class="flex items-center gap-3">
        <div id="live-dot" class="w-2.5 h-2.5 bg-error rounded-full live-pulse"></div>
        <div class="flex flex-col">
            <h1 style="font-family:'Space Grotesk',sans-serif;font-size:13px;font-weight:700;letter-spacing:3px;color:#58bfff;text-transform:uppercase;white-space:nowrap;">National All-Hazards Monitor</h1>
            <p id="update-time" style="font-size:9px;color:#a0acbd;letter-spacing:2px;font-weight:700;text-transform:uppercase;margin-top:2px;white-space:nowrap;">ACQUIRING LIVE DATA...</p>
        </div>
    </div>
    <div style="width:1px;height:24px;background:rgba(61,73,87,0.5);"></div>
    <nav class="flex items-center gap-5">
        <a href="#" onclick="event.preventDefault();showHazardOverview();" style="font-size:10px;font-weight:700;letter-spacing:2px;color:#58bfff;text-transform:uppercase;border-bottom:2px solid #58bfff;padding-bottom:2px;cursor:pointer;">GLOBAL</a>
        <a href="#" style="font-size:10px;font-weight:700;letter-spacing:2px;color:#6a7686;text-transform:uppercase;">REGIONAL</a>
        <a href="/analytics/" style="font-size:10px;font-weight:700;letter-spacing:2px;color:#6a7686;text-transform:uppercase;">ANALYTICS</a>
    </nav>
    <div style="width:1px;height:24px;background:rgba(61,73,87,0.5);"></div>
    <button id="sitrep-btn" onclick="openSitrep()" class="flex items-center gap-2 bg-primary px-4 py-1.5 text-on-primary font-bold text-[10px] tracking-widest uppercase hover:bg-primary-dim transition-all" style="font-family:'Space Grotesk',sans-serif;">
        <span class="material-symbols-outlined" style="font-size:16px;">psychology</span>
        AI SITUATION REPORT
    </button>
    <div style="width:1px;height:24px;background:rgba(61,73,87,0.5);"></div>
    <button id="theme-btn" onclick="toggleTheme()" title="Toggle light / dark (D)" style="background:transparent;border:none;color:#64748b;cursor:pointer;padding:4px;display:flex;align-items:center;" class="hover:text-primary transition-all">
        <span class="material-symbols-outlined" style="font-size:20px;">dark_mode</span>
    </button>
    <button onclick="openShortcuts()" title="Keyboard shortcuts (?)" style="background:transparent;border:none;color:#64748b;cursor:pointer;padding:4px;display:flex;align-items:center;" class="hover:text-primary transition-all">
        <span class="material-symbols-outlined" style="font-size:20px;">keyboard</span>
    </button>
</header>

<!-- Sitrep Modal -->
<div id="sitrep-overlay" onclick="if(event.target===this)closeSitrep()">
    <div class="relative w-full bg-surface-variant/60 backdrop-blur-xl border border-outline-variant/20 shadow-2xl flex flex-col overflow-hidden" style="max-width:56rem;">
        <div class="corner-bracket corner-tl"></div>
        <div class="corner-bracket corner-tr"></div>
        <div class="corner-bracket corner-bl"></div>
        <div class="corner-bracket corner-br"></div>
        <header class="p-6 flex items-center justify-between" style="border-bottom:1px solid rgba(61,73,87,0.2);">
            <div class="flex items-center gap-3">
                <span class="material-symbols-outlined text-primary" style="font-variation-settings:'FILL' 1;font-size:24px;">bolt</span>
                <h1 style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:20px;color:#dde9fb;">AI Situation Report</h1>
            </div>
            <div class="flex items-center gap-4">
                <div class="flex items-center gap-2">
                    <div class="w-2 h-2 bg-primary rounded-full" style="animation:pulse 2s infinite;box-shadow:0 0 8px #58bfff;"></div>
                    <span style="font-size:9px;font-weight:700;letter-spacing:2px;color:#a0acbd;text-transform:uppercase;">LIVE INTELLIGENCE FEED</span>
                </div>
            </div>
        </header>
        <div class="flex flex-col md:flex-row" style="min-height:320px;">
            <div class="flex-1 p-8 flex flex-col gap-8" style="background:rgba(6,20,34,0.3);border-right:1px solid rgba(61,73,87,0.2);">
                <section class="flex items-baseline justify-between">
                    <div>
                        <span style="display:block;font-size:9px;font-weight:700;letter-spacing:2px;color:#a0acbd;text-transform:uppercase;margin-bottom:4px;">NATIONAL THREAT LEVEL</span>
                        <div class="flex items-baseline gap-2">
                            <span id="sitrep-level" style="font-family:'Space Grotesk',sans-serif;font-size:64px;font-weight:700;line-height:1;color:#FF8C00;">—</span>
                            <span style="font-family:'Space Grotesk',sans-serif;font-size:28px;font-weight:700;color:#a0acbd;">/10</span>
                        </div>
                    </div>
                    <div class="text-right">
                        <span style="display:block;font-size:9px;font-weight:700;letter-spacing:2px;color:#a0acbd;text-transform:uppercase;margin-bottom:4px;">STATUS</span>
                        <span id="sitrep-status-badge" style="display:inline-block;padding:4px 12px;border:1px solid rgba(255,113,108,0.3);background:rgba(159,5,25,0.15);color:#ff716c;font-weight:700;font-size:11px;letter-spacing:1px;">ANALYZING...</span>
                    </div>
                </section>
                <section>
                    <span style="display:block;font-size:9px;font-weight:700;letter-spacing:2px;color:#a0acbd;text-transform:uppercase;margin-bottom:10px;">SITUATION SUMMARY</span>
                    <p id="sitrep-summary" style="font-size:14px;line-height:1.7;color:#dde9fb;font-weight:300;">Generating report...</p>
                </section>
                <section>
                    <span style="display:block;font-size:9px;font-weight:700;letter-spacing:2px;color:#a0acbd;text-transform:uppercase;margin-bottom:12px;">PRIORITY THREATS</span>
                    <div id="sitrep-threats" class="flex flex-col gap-3">
                        <p style="color:#a0acbd;font-size:13px;">Analyzing threats...</p>
                    </div>
                </section>
            </div>
            <div style="width:320px;flex-shrink:0;padding:32px;background:rgba(0,0,0,0.15);">
                <span style="display:block;font-size:9px;font-weight:700;letter-spacing:2px;color:#a0acbd;text-transform:uppercase;margin-bottom:20px;">COMMAND ACTIONS</span>
                <div id="sitrep-actions" class="flex flex-col gap-5">
                    <p style="color:#a0acbd;font-size:11px;">Processing...</p>
                </div>
                <div style="margin-top:40px;padding:14px;background:#102131;border:1px solid rgba(61,73,87,0.3);position:relative;">
                    <span style="font-size:9px;font-weight:700;color:#a0acbd;letter-spacing:3px;text-transform:uppercase;">AI LOG_PROCESSOR</span>
                    <div id="sitrep-confidence" style="margin-top:6px;font-family:monospace;font-size:10px;color:rgba(88,191,255,0.8);line-height:1.8;">
                        <p>&gt; AWAITING_DATA...</p>
                    </div>
                </div>
            </div>
        </div>
        <footer style="padding:20px 24px;background:rgba(16,33,49,0.5);border-top:1px solid rgba(61,73,87,0.2);display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;">
            <div class="flex items-center gap-5" style="font-size:9px;font-weight:700;letter-spacing:2px;color:#a0acbd;text-transform:uppercase;">
                <span class="flex items-center gap-1"><span class="material-symbols-outlined" style="font-size:13px;">history</span> REF: SITREP-AI</span>
                <span class="flex items-center gap-1"><span class="material-symbols-outlined" style="font-size:13px;">database</span> SOURCE: GROQ-LLM</span>
            </div>
            <div class="flex gap-3">
                <button onclick="copySitrep()" class="flex items-center gap-2 border border-primary/30 text-primary px-5 py-2 text-xs font-bold tracking-widest uppercase hover:bg-primary/5 hover:border-primary transition-all" style="background:transparent;">
                    <span class="material-symbols-outlined" style="font-size:14px;">content_copy</span>
                    Copy <span id="sitrep-copy-feedback" style="color:#00CC66;font-size:10px;"></span>
                </button>
                <button onclick="closeSitrep()" class="flex items-center gap-2 bg-primary text-on-primary px-6 py-2 text-xs font-bold tracking-widest uppercase hover:bg-primary-dim transition-all">
                    Close
                </button>
            </div>
        </footer>
    </div>
</div>

<!-- Keyboard shortcuts modal -->
<div id="shortcuts-modal" onclick="if(event.target===this)closeShortcuts()">
    <div class="glass-panel" style="min-width:340px;max-width:440px;padding:28px 32px;position:relative;">
        <div class="corner-bracket corner-tl"></div>
        <div class="corner-bracket corner-br"></div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
            <div style="display:flex;align-items:center;gap:10px;">
                <span class="material-symbols-outlined text-primary" style="font-size:20px;">keyboard</span>
                <span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:16px;color:#dde9fb;">Keyboard Shortcuts</span>
            </div>
            <button onclick="closeShortcuts()" style="background:transparent;border:none;color:#64748b;cursor:pointer;font-size:18px;line-height:1;">&times;</button>
        </div>
        <div class="shortcut-row"><span>Toggle layer panel</span><kbd>L</kbd></div>
        <div class="shortcut-row"><span>AI Situation Report</span><kbd>S</kbd></div>
        <div class="shortcut-row"><span>Refresh data now</span><kbd>R</kbd></div>
        <div class="shortcut-row"><span>Fullscreen</span><kbd>F</kbd></div>
        <div class="shortcut-row"><span>Light / dark mode</span><kbd>D</kbd></div>
        <div class="shortcut-row"><span>Threat analysis panel</span><kbd>W</kbd></div>
        <div class="shortcut-row"><span>Near me / locate</span><kbd>N</kbd></div>
        <div class="shortcut-row"><span>Close / dismiss</span><kbd>Esc</kbd></div>
        <div class="shortcut-row"><span>This help screen</span><kbd>?</kbd></div>
    </div>
</div>

<!-- Stat Cards + Hazard Chart (top-left) — hidden by default, shown via GLOBAL nav link -->
<div id="stat-cards-wrap" class="absolute z-10 pointer-events-auto" style="left:256px;top:72px;width:370px;display:none;">
    <div id="stats-collapse-bar" class="glass-panel" style="display:flex;align-items:center;justify-content:space-between;padding:6px 12px;margin-bottom:8px;">
        <span style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#58bfff;font-family:'Space Grotesk',sans-serif;">◈ Hazard Overview</span>
        <span onclick="hideHazardOverview()" style="font-size:14px;color:#a0acbd;cursor:pointer;line-height:1;padding:0 4px;transition:color 0.15s;" onmouseover="this.style.color='#ff716c'" onmouseout="this.style.color='#a0acbd'" title="Close">✕</span>
    </div>
    <div id="stats-body">
    <div class="grid grid-cols-2 gap-3">
        <div class="stat-card glass-panel p-3 relative cursor-pointer transition-all" style="border-left:3px solid rgba(255,113,108,0.7);">
            <span style="font-size:9px;color:#a0acbd;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Active Warnings</span>
            <div class="flex items-baseline gap-2 mt-1">
                <span id="stat-warnings" class="skel" style="font-family:'Space Grotesk',sans-serif;font-size:26px;font-weight:700;color:#dde9fb;">—</span>
                <span id="delta-warnings" class="stat-delta"></span>
            </div>
        </div>
        <div class="stat-card glass-panel p-3 relative cursor-pointer transition-all" style="border-left:3px solid rgba(88,191,255,0.7);">
            <span style="font-size:9px;color:#a0acbd;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Seismic M2.5+</span>
            <div class="flex items-baseline gap-2 mt-1">
                <span id="stat-eq" class="skel" style="font-family:'Space Grotesk',sans-serif;font-size:26px;font-weight:700;color:#dde9fb;">—</span>
                <span id="delta-eq" class="stat-delta"></span>
            </div>
        </div>
        <div class="stat-card glass-panel p-3 relative cursor-pointer transition-all" style="border-left:3px solid rgba(249,115,22,0.7);">
            <span style="font-size:9px;color:#a0acbd;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Thermal Sites</span>
            <div class="flex items-baseline gap-2 mt-1">
                <span id="stat-fires" class="skel" style="font-family:'Space Grotesk',sans-serif;font-size:26px;font-weight:700;color:#dde9fb;">—</span>
                <span id="delta-fires" class="stat-delta"></span>
            </div>
        </div>
        <div class="stat-card glass-panel p-3 relative cursor-pointer transition-all" style="border-left:3px solid rgba(172,137,255,0.7);">
            <span style="font-size:9px;color:#a0acbd;font-weight:700;letter-spacing:2px;text-transform:uppercase;">SPC Outlook</span>
            <div class="flex items-baseline gap-2 mt-1">
                <span id="stat-spc" class="skel" style="font-family:'Space Grotesk',sans-serif;font-size:26px;font-weight:700;color:#dde9fb;">—</span>
                <span id="delta-spc" class="stat-delta"></span>
            </div>
        </div>
        <div class="stat-card glass-panel p-3 relative cursor-pointer transition-all" style="border-left:3px solid rgba(245,158,11,0.7);">
            <span style="font-size:9px;color:#a0acbd;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Counties Alert</span>
            <div class="flex items-baseline gap-2 mt-1">
                <span id="stat-counties" class="skel" style="font-family:'Space Grotesk',sans-serif;font-size:26px;font-weight:700;color:#dde9fb;">—</span>
            </div>
        </div>
        <div class="stat-card glass-panel p-3 relative cursor-pointer transition-all" style="border-left:3px solid rgba(88,191,255,0.3);">
            <span style="font-size:9px;color:#a0acbd;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Population Exp.</span>
            <div class="flex items-baseline gap-2 mt-1">
                <span id="stat-pop" class="skel" style="font-family:'Space Grotesk',sans-serif;font-size:26px;font-weight:700;color:#dde9fb;">—</span>
            </div>
        </div>
    </div>
    <div class="glass-panel mt-3 p-3" style="position:relative;">
        <div class="corner-bracket corner-tl"></div>
        <div class="corner-bracket corner-br"></div>
        <div style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#a0acbd;margin-bottom:10px;">Live Hazard Counts</div>
        <div id="hazard-rows" style="display:flex;flex-direction:column;gap:6px;">
            <div class="haz-row" data-key="warnings_count"  data-color="#FF716C">⚠ Active Warnings</div>
            <div class="haz-row" data-key="earthquakes"     data-color="#58bfff">🔴 Earthquakes M2.5+</div>
            <div class="haz-row" data-key="wildfires"       data-color="#FF8C00">🔥 Fire Detections</div>
            <div class="haz-row" data-key="river_gauges"    data-color="#00BFFF">🌊 Flood Gauges</div>
            <div class="haz-row" data-key="active_storms"   data-color="#FF6600">🌀 Active Storms</div>
            <div class="haz-row" data-key="volcanoes"       data-color="#FF4500">🌋 Volcano Alerts</div>
        </div>
    </div>
    </div>
</div>

<!-- Legend (collapsible, bottom-left) -->
<div id="legend-wrap" class="absolute z-10 pointer-events-auto" style="bottom:24px;left:256px;">
    <div id="legend-toggle" onclick="toggleLegend()" class="glass-panel px-4 py-2 flex items-center justify-between cursor-pointer" style="min-width:140px;gap:16px;">
        <span style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#58bfff;">MAP LEGEND</span>
        <span id="legend-toggle-arrow" style="font-size:10px;display:inline-block;transform:rotate(180deg);transition:transform 0.2s;color:#58bfff;">▲</span>
    </div>
    <div id="legend" class="collapsed glass-panel mt-1 p-4" style="display:none;grid-template-columns:repeat(3,1fr);gap:16px;max-width:500px;">
        <div>
            <div style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#a0acbd;margin-bottom:8px;">NWS Warnings</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:10px;height:7px;background:#FF0000;flex-shrink:0;"></div>Tornado</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:10px;height:7px;background:#FF6600;flex-shrink:0;"></div>Hurricane</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:10px;height:7px;background:#FF6666;flex-shrink:0;"></div>Svr T-Storm</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:10px;height:7px;background:#00BFFF;flex-shrink:0;"></div>Flash Flood</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:8px;"><div style="width:10px;height:7px;background:#AAAAFF;flex-shrink:0;"></div>Winter Storm</div>
            <div style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#a0acbd;margin-bottom:8px;">Earthquakes</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FFFF00;flex-shrink:0;"></div>M2.5–3.9</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF9900;flex-shrink:0;"></div>M4.0–4.9</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF0000;flex-shrink:0;"></div>M5.0+</div>
        </div>
        <div>
            <div style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#a0acbd;margin-bottom:8px;">Wildfires &amp; AQ</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF4500;flex-shrink:0;"></div>FIRMS Detection</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:8px;"><div style="width:10px;height:7px;background:rgba(255,69,0,0.5);border:1px solid #FF4500;flex-shrink:0;"></div>Perimeter</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#00E400;flex-shrink:0;"></div>AQ Good</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FFFF00;flex-shrink:0;"></div>AQ Moderate</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF7E00;flex-shrink:0;"></div>AQ Unhealthy/S</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF0000;flex-shrink:0;"></div>AQ Unhealthy</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;"><div style="width:8px;height:8px;border-radius:50%;background:#8F3F97;flex-shrink:0;"></div>AQ Very Unhealthy</div>
        </div>
        <div>
            <div style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#a0acbd;margin-bottom:8px;">Flood &amp; Other</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FFFF00;flex-shrink:0;"></div>Action Stage</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FFA500;flex-shrink:0;"></div>Minor Flood</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF4500;flex-shrink:0;"></div>Moderate Flood</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:8px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF0000;flex-shrink:0;"></div>Major Flood</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#00FF88;flex-shrink:0;"></div>Open Shelter</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#C084FC;flex-shrink:0;"></div>FEMA Disaster</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;margin-bottom:4px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF0066;flex-shrink:0;"></div>Hospital</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;"><div style="width:8px;height:8px;border-radius:50%;background:#FF8800;flex-shrink:0;"></div>Volcano Alert</div>
        </div>
    </div>
</div>

<!-- Location Threat Analysis Panel (bottom-right) -->
<div id="address-panel" class="glass-panel absolute z-10 pointer-events-auto overflow-hidden" style="bottom:24px;right:24px;width:300px;max-height:calc(100vh - 280px);display:flex;flex-direction:column;">
    <div onclick="toggleAddressPanel()" style="background:rgba(0,0,0,0.3);padding:10px 16px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(61,73,87,0.3);flex-shrink:0;cursor:pointer;" title="Collapse / expand">
        <div style="display:flex;align-items:center;gap:8px;">
            <span class="material-symbols-outlined text-primary" style="font-size:14px;font-variation-settings:'FILL' 1;">security</span>
            <h4 style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#58bfff;">Threat Analysis</h4>
        </div>
        <span id="address-arrow" style="font-size:10px;color:#58bfff;transition:transform 0.2s;transform:rotate(180deg);">▲</span>
    </div>
    <div id="address-body" style="padding:16px;overflow-y:auto;flex:1;min-height:0;display:block;">
        <label style="display:block;font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#a0acbd;margin-bottom:8px;">Search Location</label>
        <div style="position:relative;">
            <input id="address-input" type="text" placeholder="ENTER ADDRESS OR CITY" style="width:100%;background:rgba(0,0,0,0.4);border:none;border-bottom:2px solid rgba(106,118,134,0.4);color:#dde9fb;font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;padding:8px 28px 8px 0;outline:none;transition:border-color 0.2s;font-family:'Inter',sans-serif;">
            <span class="material-symbols-outlined" style="position:absolute;right:0;top:8px;font-size:14px;color:#6a7686;">search</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px;margin-bottom:6px;">
            <label style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#a0acbd;">Analysis Radius</label>
            <span id="buffer-label" style="font-size:10px;font-weight:700;color:#58bfff;">50 miles</span>
        </div>
        <input id="buffer-slider" type="range" min="5" max="200" value="50" step="5" style="width:100%;accent-color:#58bfff;">
        <!-- Score Inputs (Immediate Threat Score) -->
        <div style="margin-top:14px;padding-top:12px;border-top:1px solid rgba(88,191,255,0.12);">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;">
                <div style="font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#a0acbd;">Score Inputs</div>
                <div style="display:flex;gap:6px;">
                    <button type="button" onclick="applyScorePreset('immediate')" style="background:rgba(88,191,255,0.10);border:1px solid rgba(88,191,255,0.25);color:#58bfff;font-size:9px;font-weight:700;letter-spacing:1px;padding:4px 8px;cursor:pointer;">Immediate</button>
                    <button type="button" onclick="applyScorePreset('weather')" style="background:rgba(255,255,255,0.04);border:1px solid rgba(61,73,87,0.35);color:#a0acbd;font-size:9px;font-weight:700;letter-spacing:1px;padding:4px 8px;cursor:pointer;">Weather</button>
                    <button type="button" onclick="applyScorePreset('fire')" style="background:rgba(255,255,255,0.04);border:1px solid rgba(61,73,87,0.35);color:#a0acbd;font-size:9px;font-weight:700;letter-spacing:1px;padding:4px 8px;cursor:pointer;">Fire</button>
                </div>
            </div>
            <div id="score-inputs" style="display:grid;grid-template-columns:1fr 1fr;gap:6px 10px;">
                <label style="display:flex;align-items:center;gap:8px;font-size:10px;color:#c8d8eb;cursor:pointer;">
                    <input type="checkbox" id="si-warnings" checked style="accent-color:#58bfff;"> NWS Warnings
                </label>
                <label style="display:flex;align-items:center;gap:8px;font-size:10px;color:#c8d8eb;cursor:pointer;">
                    <input type="checkbox" id="si-stormreports" checked style="accent-color:#58bfff;"> Storm Reports
                </label>
                <label style="display:flex;align-items:center;gap:8px;font-size:10px;color:#c8d8eb;cursor:pointer;">
                    <input type="checkbox" id="si-earthquakes" checked style="accent-color:#58bfff;"> Earthquakes
                </label>
                <label style="display:flex;align-items:center;gap:8px;font-size:10px;color:#c8d8eb;cursor:pointer;">
                    <input type="checkbox" id="si-firedetections" checked style="accent-color:#58bfff;"> Fire Detections
                </label>
                <label style="display:flex;align-items:center;gap:8px;font-size:10px;color:#c8d8eb;cursor:pointer;">
                    <input type="checkbox" id="si-fireperimeters" checked style="accent-color:#58bfff;"> Fire Perimeters
                </label>
                <label style="display:flex;align-items:center;gap:8px;font-size:10px;color:#c8d8eb;cursor:pointer;">
                    <input type="checkbox" id="si-rivergauges" style="accent-color:#58bfff;"> Flood Gauges
                </label>
                <label style="display:flex;align-items:center;gap:8px;font-size:10px;color:#c8d8eb;cursor:pointer;grid-column:1 / -1;">
                    <input type="checkbox" id="si-hurricanes" style="accent-color:#58bfff;"> Hurricanes / Tropical Systems
                </label>
            </div>
            <div style="margin-top:8px;font-size:9px;color:#6a7686;line-height:1.5;">
                Only selected inputs affect the score and local briefing.
            </div>
        </div>
        <div id="threat-results" style="display:none;margin-top:12px;"></div>
        <button id="search-btn" onclick="searchLocation()" class="flex items-center justify-center gap-2 w-full mt-4 py-2.5 text-xs font-bold tracking-widest uppercase transition-all" style="background:#102131;border:1px solid rgba(61,73,87,0.4);color:#dde9fb;font-family:'Inter',sans-serif;cursor:pointer;letter-spacing:2px;">
            🔍 ANALYZE THREATS
            <span class="material-symbols-outlined" style="font-size:13px;">arrow_forward_ios</span>
        </button>
        <div id="clear-search" onclick="clearSearch()" style="display:none;text-align:center;margin-top:8px;font-size:10px;color:#a0acbd;cursor:pointer;letter-spacing:1px;">✕ Clear search</div>
    </div>
</div>

<!-- Hover tooltip -->
<div id="hover-tooltip"></div>

<!-- Popup -->
<div id="popup">
    <div id="popup-header">
        <h3 id="popup-title">Feature</h3>
        <span id="close-popup" onclick="document.getElementById('popup').style.display='none'">✕</span>
    </div>
    <div id="popup-content"></div>
</div>

<script>
mapboxgl.accessToken = '{MAPBOX_TOKEN}';

const HAZARD_COLORS = {{
    'TO': '#FF0000', 'FF': '#00BFFF', 'HU': '#FF6600',
    'TS': '#FF9900', 'SV': '#FF6666', 'WS': '#AAAAFF',
    'FA': '#0099FF', 'FW': '#FF4500'
}};
const SPC_COLORS = {{
    'TSTM': '#76FF7A', 'MRGL': '#009000', 'SLGT': '#FFFF00',
    'ENH': '#FF9900', 'MDT': '#FF0000', 'HIGH': '#FF00FF'
}};
const PHENOM_NAMES = {{
    'TO':'Tornado','SV':'Severe Thunderstorm','FF':'Flash Flood',
    'FA':'Flood','HU':'Hurricane','TS':'Tropical Storm',
    'WS':'Winter Storm','FW':'Fire Weather','EH':'Excessive Heat',
    'HW':'High Wind','CF':'Coastal Flood','SS':'Storm Surge'
}};

const map = new mapboxgl.Map({{
    container: 'map',
    style: 'mapbox://styles/mapbox/dark-v11',
    center: [-98.35, 39.5],
    zoom: 3.5
}});

map.addControl(new mapboxgl.NavigationControl(), 'top-right');
map.addControl(new mapboxgl.FullscreenControl(), 'top-right');

// ── LEGEND TOGGLE ────────────────────────────────
let _legendOpen = false;
function toggleLegend() {{
    _legendOpen = !_legendOpen;
    const legend = document.getElementById('legend');
    const arrow  = document.getElementById('legend-toggle-arrow');
    legend.style.display = _legendOpen ? 'grid' : 'none';
    arrow.style.transform = _legendOpen ? '' : 'rotate(180deg)';
}}

// ── SIDEBAR HELPERS ───────────────────────────────
function setSidebarActive(btn) {{
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}}
function focusThreatPanel() {{
    const panel = document.getElementById('address-panel');
    panel.style.boxShadow = '0 0 0 2px #58bfff, 0 0 20px rgba(88,191,255,0.3)';
    setTimeout(() => {{ panel.style.boxShadow = ''; }}, 1600);
    document.getElementById('address-input').focus();
}}
function toggleLayerPanel() {{
    // Layers now live in the persistent left sidebar. Flash the sidebar to draw attention.
    const p = document.querySelector('nav.sidebar');
    if (!p) return;
    p.style.boxShadow = '4px 0 24px rgba(88,191,255,0.35)';
    setTimeout(() => {{ p.style.boxShadow = ''; }}, 900);
}}
function showHazardOverview() {{
    const wrap = document.getElementById('stat-cards-wrap');
    if (wrap) wrap.style.display = 'block';
}}
function hideHazardOverview() {{
    const wrap = document.getElementById('stat-cards-wrap');
    if (wrap) wrap.style.display = 'none';
}}
function toggleAddressPanel() {{
    const body = document.getElementById('address-body');
    const arrow = document.getElementById('address-arrow');
    if (!body || !arrow) return;
    const nowOpen = body.style.display === 'none';
    body.style.display = nowOpen ? 'block' : 'none';
    arrow.style.transform = nowOpen ? 'rotate(180deg)' : 'rotate(0deg)';
    try {{ localStorage.setItem('nhm_threat_open', nowOpen ? '1' : '0'); }} catch(e) {{}}
}}
// Restore threat panel collapse state on load
document.addEventListener('DOMContentLoaded', () => {{
    try {{
        if (localStorage.getItem('nhm_threat_open') === '0') toggleAddressPanel();
    }} catch(e) {{}}
}});
function flyToWarnings() {{
    if (_latestWarnings?.features?.length) {{
        const f = _latestWarnings.features[0];
        const coords = f.geometry?.coordinates?.[0]?.[0];
        if (coords) map.flyTo({{center: coords, zoom: 5, duration: 1400}});
    }}
}}
function flyToEarthquakes() {{
    if (_latestEarthquakes?.features?.length) {{
        const biggest = [..._latestEarthquakes.features]
            .sort((a,b) => (b.properties?.mag||0) - (a.properties?.mag||0))[0];
        if (biggest?.geometry?.coordinates) {{
            const [lng,lat] = biggest.geometry.coordinates;
            map.flyTo({{center:[lng,lat], zoom:6, duration:1400}});
        }}
    }}
}}
function flyToFires() {{
    if (_latestFires?.features?.length) {{
        const pts = _latestFires.features.filter(f=>f.geometry?.coordinates);
        if (pts.length) {{
            const lng = pts.reduce((s,p)=>s+p.geometry.coordinates[0],0)/pts.length;
            const lat = pts.reduce((s,p)=>s+p.geometry.coordinates[1],0)/pts.length;
            map.flyTo({{center:[lng,lat], zoom:5, duration:1400}});
        }}
    }}
}}

function showPopup(title, rows, e) {{
    const popup = document.getElementById('popup');
    document.getElementById('popup-title').textContent = title;
    let html = '';
    for (const [k, v] of Object.entries(rows)) {{
        html += `<div class="popup-row"><span class="popup-key">${{k}}</span><span class="popup-val">${{v}}</span></div>`;
    }}
    document.getElementById('popup-content').innerHTML = html;
    popup.style.display = 'block';
    const x = e.point.x + 14;
    const y = e.point.y - 10;
    popup.style.left = Math.min(x, window.innerWidth - 300) + 'px';
    popup.style.top  = Math.max(y, 10) + 'px';
}}

function setupLayers() {{
    // Guard: don't add sources if already added
    if (map.getSource('warnings')) return;

    // 3D terrain removed — was forcing full 3D render mode on every frame

    // ── SPC OUTLOOK ─────────────────────────────────
    map.addSource('spc', {{ type: 'geojson', data: '/api/spc' }});
    map.addLayer({{
        id: 'spc-fill', type: 'fill', source: 'spc',
        layout: {{ visibility: 'none' }},
        paint: {{
            'fill-color': [
                'match', ['get', 'LABEL'],
                'TSTM', '#76FF7A', 'MRGL', '#009000',
                'SLGT', '#FFFF00', 'ENH',  '#FF9900',
                'MDT',  '#FF0000', 'HIGH', '#FF00FF',
                '#76FF7A'
            ],
            'fill-opacity': 0.25
        }}
    }});
    map.addLayer({{
        id: 'spc-outline', type: 'line', source: 'spc',
        layout: {{ visibility: 'none' }},
        paint: {{
            'line-color': ['match', ['get', 'LABEL'],
                'TSTM', '#76FF7A', 'MRGL', '#009000',
                'SLGT', '#FFFF00', 'ENH',  '#FF9900',
                'MDT',  '#FF0000', 'HIGH', '#FF00FF',
                '#76FF7A'
            ],
            'line-width': 1.5,
            'line-dasharray': [4, 4]
        }}
    }});

    // ── NWS WARNINGS ────────────────────────────────
    map.addSource('warnings', {{ type: 'geojson', data: '/api/warnings' }});
    map.addLayer({{
        id: 'warnings-fill', type: 'fill', source: 'warnings',
        paint: {{
            'fill-color': [
                'match', ['get', 'phenom'],
                'TO', '#FF0000', 'FF', '#00BFFF', 'HU', '#FF6600',
                'TS', '#FF9900', 'SV', '#FF6666', 'WS', '#AAAAFF',
                'FA', '#0099FF', 'FW', '#FF4500', '#FFFF00'
            ],
            'fill-opacity': 0.45
        }}
    }});
    map.addLayer({{
        id: 'warnings-outline', type: 'line', source: 'warnings',
        paint: {{
            'line-color': '#FFFFFF',
            'line-width': 1,
            'line-opacity': 0.6
        }}
    }});

    // Warning pulse — slowed to 1500ms so setPaintProperty fires ~6× less often
    let opacity = 0.4;
    let direction = -1;
    setInterval(() => {{
        if (!map.getLayer('warnings-fill')) return;
        if (!_latestWarnings?.features?.length) return;
        opacity += direction * 0.15;
        if (opacity <= 0.2 || opacity >= 0.6) direction *= -1;
        map.setPaintProperty('warnings-fill', 'fill-opacity', opacity);
    }}, 1500);

    // ── EARTHQUAKES ──────────────────────────────────
    map.addSource('earthquakes', {{ type: 'geojson', data: '/api/earthquakes' }});
    map.addLayer({{
        id: 'eq-circles', type: 'circle', source: 'earthquakes',
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': [
                'step', ['get', 'mag'],
                '#FFFF00', 4, '#FF9900', 5, '#FF0000'
            ],
            'circle-radius': [
                'interpolate', ['linear'], ['get', 'mag'],
                2.5, 4, 4, 8, 6, 16, 8, 28
            ],
            'circle-opacity': 0.8,
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 1.5
        }}
    }});

    // ── WILDFIRES (simple points, no clustering or heatmap) ──
    map.addSource('fires', {{ type: 'geojson', data: '/api/fires' }});
    map.addLayer({{
        id: 'fire-points', type: 'circle', source: 'fires',
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': [
                'step', ['get', 'frp'],
                '#FF8C00', 20, '#FF4500', 100, '#FF0000'
            ],
            'circle-radius': 5,
            'circle-stroke-color': '#FFD700',
            'circle-stroke-width': 1,
            'circle-opacity': 0.8
        }}
    }});

    // ── CLICK HANDLERS ───────────────────────────────
    map.on('click', 'warnings-fill', (e) => {{
        const p = e.features[0].properties;
        const phenom = p.phenom || '';
        const sigMap = {{'W':'Warning','A':'Watch','Y':'Advisory','S':'Statement'}};
        const sig = sigMap[p.sig] || p.sig || '';
        const name = (PHENOM_NAMES[phenom] || phenom) + ' ' + sig;
        const rows = {{ 'Issued by': p.wfo || 'N/A' }};
        if (p.expires) {{
            try {{
                const exp = new Date(p.expires);
                if (!isNaN(exp)) rows['Expires'] = exp.toLocaleString([],
                    {{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}});
            }} catch(e) {{}}
        }}
        if (p.event) rows['Event'] = p.event;
        if (p.headline) rows['Headline'] = p.headline.length > 90 ? p.headline.slice(0,90) + '…' : p.headline;
        if (p.areaDesc) rows['Area'] = p.areaDesc.length > 80 ? p.areaDesc.slice(0,80) + '…' : p.areaDesc;
        showPopup('⚠ ' + name, rows, e);
    }});
    map.on('click', 'spc-fill', (e) => {{
        const p = e.features[0].properties;
        const labels = {{'TSTM':'General Thunder','MRGL':'Marginal Risk','SLGT':'Slight Risk','ENH':'Enhanced Risk','MDT':'Moderate Risk','HIGH':'High Risk'}};
        showPopup('⛈ SPC Convective Outlook', {{
            'Risk Level': labels[p.LABEL] || p.LABEL || 'N/A',
            'Label': p.LABEL2 || p.LABEL || 'N/A'
        }}, e);
    }});
    map.on('click', 'eq-circles', (e) => {{
        const p = e.features[0].properties;
        showPopup('🔴 Earthquake M' + p.mag, {{
            'Location': p.place || 'Unknown',
            'Magnitude': p.mag,
            'Depth': (p.depth || 'N/A') + ' km',
            'Time': p.time ? new Date(p.time).toLocaleString() : 'N/A'
        }}, e);
    }});
    map.on('click', 'fire-points', (e) => {{
        const p = e.features[0].properties;
        showPopup('🔥 Wildfire Detection', {{
            'Date': p.acq_date || 'N/A',
            'FRP': (p.frp || 'N/A') + ' MW',
            'Confidence': p.confidence || 'N/A'
        }}, e);
    }});
    // Cursor changes for all clickable layers
    ['warnings-fill','spc-fill','eq-circles','fire-points'].forEach(layer => {{
        map.on('mouseenter', layer, () => map.getCanvas().style.cursor = 'pointer');
        map.on('mouseleave', layer, () => map.getCanvas().style.cursor = '');
    }});

    // ── AFFECTED COUNTIES ────────────────────────────
    map.addSource('counties', {{ type: 'geojson', data: '/api/counties' }});
    map.addLayer({{
        id: 'counties-fill', type: 'fill', source: 'counties',
        layout: {{ visibility: 'none' }},
        paint: {{
            'fill-color': [
                'interpolate', ['linear'],
                ['get', 'population'],
                0,       'rgba(255,100,0,0.1)',
                50000,   'rgba(255,100,0,0.25)',
                200000,  'rgba(255,80,0,0.4)',
                500000,  'rgba(255,50,0,0.55)',
                1000000, 'rgba(255,0,0,0.7)'
            ],
            'fill-opacity': 0.7
        }}
    }});
    map.addLayer({{
        id: 'counties-outline', type: 'line', source: 'counties',
        layout: {{ visibility: 'none' }},
        paint: {{
            'line-color': '#FF6600',
            'line-width': 1.5,
            'line-opacity': 0.8
        }}
    }});
    map.on('click', 'counties-fill', (e) => {{
        const p = e.features[0].properties;
        showPopup('📍 ' + p.county + ', ' + p.state, {{
            'Population': Number(p.population).toLocaleString(),
            'Event': p.event || 'N/A',
            'Alert Level': p.sig || 'N/A',
            'FIPS': p.fips || 'N/A'
        }}, e);
    }});
    map.on('mouseenter', 'counties-fill', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'counties-fill', () => map.getCanvas().style.cursor = '');

    // ── INFRASTRUCTURE ────────────────────────────────
    map.addSource('infrastructure', {{ type: 'geojson', data: '/api/infrastructure' }});

    // At-risk infrastructure — glowing red (off by default)
    map.addLayer({{
        id: 'infra-at-risk', type: 'circle', source: 'infrastructure',
        filter: ['==', ['get', 'at_risk'], true],
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': '#FF0000',
            'circle-radius': 7,
            'circle-stroke-color': '#FF6666',
            'circle-stroke-width': 2,
            'circle-opacity': 0.9
        }}
    }});

    // Normal infrastructure (off by default)
    map.addLayer({{
        id: 'infra-normal', type: 'circle', source: 'infrastructure',
        filter: ['==', ['get', 'at_risk'], false],
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': ['get', 'color'],
            'circle-radius': 4,
            'circle-stroke-color': 'rgba(255,255,255,0.3)',
            'circle-stroke-width': 1,
            'circle-opacity': 0.7
        }}
    }});

    // Infrastructure labels
    map.addLayer({{
        id: 'infra-labels', type: 'symbol', source: 'infrastructure',
        layout: {{
            'text-field': ['get', 'name'],
            'text-size': 9,
            'text-offset': [0, 1.2],
            'text-anchor': 'top',
            'visibility': 'none'
        }},
        paint: {{
            'text-color': 'white',
            'text-halo-color': 'rgba(0,0,0,0.8)',
            'text-halo-width': 1
        }}
    }});

    map.on('click', 'infra-at-risk', (e) => {{
        const p = e.features[0].properties;
        showPopup(p.icon + ' ' + p.name, {{
            'Type': p.type.replace('_', ' ').toUpperCase(),
            'Status': '⚠ AT RISK — inside warning zone',
        }}, e);
    }});
    map.on('click', 'infra-normal', (e) => {{
        const p = e.features[0].properties;
        showPopup(p.icon + ' ' + p.name, {{
            'Type': p.type.replace('_', ' ').toUpperCase(),
            'Status': '✅ Not currently at risk',
        }}, e);
    }});
    ['infra-at-risk', 'infra-normal'].forEach(layer => {{
        map.on('mouseenter', layer, () => map.getCanvas().style.cursor = 'pointer');
        map.on('mouseleave', layer, () => map.getCanvas().style.cursor = '');
    }});
    map.addSource('nexrad', {{
        type: 'raster',
        tiles: ['https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS=nexrad-n0r&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE&HEIGHT=256&WIDTH=256&SRS=EPSG:3857&BBOX={{bbox-epsg-3857}}'],
        tileSize: 256,
        attribution: 'Iowa State Mesonet'
    }});
    map.addLayer({{
        id: 'nexrad-layer',
        type: 'raster',
        source: 'nexrad',
        layout: {{ visibility: 'none' }},
        paint: {{ 'raster-opacity': 0.7 }}
    }});

    // ── GOES INFRARED (Live) ─────────────────────────
    map.addSource('goes-ir', {{
        type: 'raster',
        tiles: ['https://mesonet.agron.iastate.edu/cgi-bin/wms/goes/conus_ir.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS=goes_conus_ir&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE&HEIGHT=256&WIDTH=256&SRS=EPSG:3857&BBOX={{bbox-epsg-3857}}'],
        tileSize: 256,
        attribution: 'Iowa State Mesonet'
    }});
    map.addLayer({{
        id: 'goes-ir-layer',
        type: 'raster',
        source: 'goes-ir',
        paint: {{ 'raster-opacity': 0.6 }},
        layout: {{ 'visibility': 'none' }}
    }});

    // ── LIGHTNING / STORM REPORTS ────────────────────
    map.addSource('lightning', {{ type: 'geojson', data: '/api/lightning' }});
    map.addLayer({{
        id: 'lightning-strikes', type: 'circle', source: 'lightning',
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': '#FFFF00',
            'circle-radius': 5,
            'circle-stroke-color': 'rgba(255,255,255,0.6)',
            'circle-stroke-width': 1,
            'circle-opacity': 0.85
        }}
    }});
    map.on('click', 'lightning-strikes', (e) => {{
        const p = e.features[0].properties;
        showPopup('⚡ Storm Report', {{
            'Type':     p.typetext || 'N/A',
            'Location': p.city    || 'N/A',
            'Time':     p.valid   ? new Date(p.valid).toLocaleString() : 'N/A',
            'Source':   'NWS LSR'
        }}, e);
    }});
    map.on('mouseenter', 'lightning-strikes', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'lightning-strikes', () => map.getCanvas().style.cursor = '');

    // ── FIRE PERIMETERS ──────────────────────────────
    map.addSource('fire_perimeters', {{ type: 'geojson', data: '/api/fire_perimeters' }});
    map.addLayer({{
        id: 'fire-perimeter-fill', type: 'fill', source: 'fire_perimeters',
        layout: {{ visibility: 'none' }},
        paint: {{
            'fill-color': 'rgba(255,69,0,0.25)',
            'fill-outline-color': '#FF4500'
        }}
    }});
    map.addLayer({{
        id: 'fire-perimeter-outline', type: 'line', source: 'fire_perimeters',
        layout: {{ visibility: 'none' }},
        paint: {{
            'line-color': '#FF4500',
            'line-width': 2,
            'line-opacity': 0.9,
            'line-dasharray': [2, 1]
        }}
    }});
    map.on('click', 'fire-perimeter-fill', (e) => {{
        const p = e.features[0].properties;
        showPopup('🔥 ' + (p.IncidentName || 'Active Fire'), {{
            'Acres':     p.GISAcres ? Math.round(p.GISAcres).toLocaleString() : 'N/A',
            'Contained': (p.PercentContained || 0) + '%',
            'Updated':   p.ModifiedOnDateTime_dt || 'N/A'
        }}, e);
    }});
    map.on('mouseenter', 'fire-perimeter-fill', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'fire-perimeter-fill', () => map.getCanvas().style.cursor = '');

    // ── HURRICANES ───────────────────────────────────
    map.addSource('storms', {{ type: 'geojson', data: '/api/storms' }});
    map.addLayer({{
        id: 'storm-cone', type: 'fill', source: 'storms',
        filter: ['==', ['get', 'layer'], 'cone'],
        layout: {{ visibility: 'none' }},
        paint: {{
            'fill-color': '#FF6600',
            'fill-opacity': 0.18
        }}
    }});
    map.addLayer({{
        id: 'storm-cone-outline', type: 'line', source: 'storms',
        filter: ['==', ['get', 'layer'], 'cone'],
        layout: {{ visibility: 'none' }},
        paint: {{ 'line-color': '#FF6600', 'line-width': 2, 'line-opacity': 0.7 }}
    }});
    map.addLayer({{
        id: 'storm-track', type: 'circle', source: 'storms',
        filter: ['==', ['get', 'layer'], 'track'],
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': '#FF6600',
            'circle-radius': 7,
            'circle-stroke-color': '#FFD700',
            'circle-stroke-width': 2,
            'circle-opacity': 0.9
        }}
    }});
    map.on('click', 'storm-track', (e) => {{
        const p = e.features[0].properties;
        showPopup('🌀 ' + (p.storm_name || 'Hurricane'), {{
            'Type': 'Forecast Track Point',
            'Storm': p.storm_name || 'N/A'
        }}, e);
    }});
    map.on('mouseenter', 'storm-track', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'storm-track', () => map.getCanvas().style.cursor = '');

    // Animate hurricane track — slowed to 1000ms, same visual speed via adjusted frequency
    let _stormFrame = 0;
    setInterval(() => {{
        if (!map.getLayer('storm-track')) return;
        if (!_latestStorms?.features?.length) return;
        const pulse = 7 + Math.sin(_stormFrame) * 3;
        map.setPaintProperty('storm-track', 'circle-radius', pulse);
        _stormFrame++;
    }}, 1000);

    // ── DROUGHT MONITOR ──────────────────────────────
    map.addSource('drought', {{ type: 'geojson', data: '/api/drought' }});
    map.addLayer({{
        id: 'drought-fill', type: 'fill', source: 'drought',
        layout: {{ visibility: 'none' }},
        paint: {{
            'fill-color': [
                'step', ['get', 'DM'],
                '#F5DEB3',  // D0 Abnormally Dry
                1, '#FFD700',  // D1 Moderate
                2, '#FF8C00',  // D2 Severe
                3, '#FF2400',  // D3 Extreme
                4, '#8B0000'   // D4 Exceptional
            ],
            'fill-opacity': 0.45
        }}
    }});
    map.on('click', 'drought-fill', (e) => {{
        const dm = e.features[0].properties.DM;
        const labels = ['D0 Abnormally Dry','D1 Moderate Drought','D2 Severe Drought','D3 Extreme Drought','D4 Exceptional Drought'];
        showPopup('🏜 Drought Conditions', {{ 'Severity': labels[dm] || 'D'+dm }}, e);
    }});

    // ── AIR QUALITY (AQI) ────────────────────────────
    map.addSource('air_quality', {{ type: 'geojson', data: '/api/air_quality' }});
    map.addLayer({{
        id: 'aqi-circles', type: 'circle', source: 'air_quality',
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': [
                'step', ['get', 'aqi'],
                '#00E400',   // 0-50 Good
                51,  '#FFFF00',  // 51-100 Moderate
                101, '#FF7E00',  // 101-150 Unhealthy for Sensitive
                151, '#FF0000',  // 151-200 Unhealthy
                201, '#8F3F97',  // 201-300 Very Unhealthy
                301, '#7E0023'   // 301+ Hazardous
            ],
            'circle-radius': 7,
            'circle-opacity': 0.85,
            'circle-stroke-color': 'rgba(0,0,0,0.4)',
            'circle-stroke-width': 1
        }}
    }});
    map.on('click', 'aqi-circles', (e) => {{
        const p = e.features[0].properties;
        showPopup('💨 Air Quality — ' + (p.reporting_area || p.state || ''), {{
            'AQI':       p.aqi,
            'Category':  p.category,
            'Parameter': p.parameter,
            'State':     p.state
        }}, e);
    }});
    map.on('mouseenter', 'aqi-circles', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'aqi-circles', () => map.getCanvas().style.cursor = '');

    // ── RIVER FLOOD GAUGES ───────────────────────────
    map.addSource('river_gauges', {{ type: 'geojson', data: '/api/river_gauges' }});
    map.addLayer({{
        id: 'river-gauges', type: 'circle', source: 'river_gauges',
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': ['get', 'color'],
            'circle-radius': 7,
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 1.5,
            'circle-opacity': 0.9
        }}
    }});
    map.on('click', 'river-gauges', (e) => {{
        const p = e.features[0].properties;
        showPopup('🌊 Flood Gauge — ' + (p.location || p.name || ''), {{
            'Status':   (p.status || '').toUpperCase(),
            'Location': p.location || 'N/A',
            'State':    p.state || 'N/A',
            'Details':  p.url ? '<a href="' + p.url + '" target="_blank" style="color:#00B4FF">NOAA Gauge Page</a>' : 'N/A'
        }}, e);
    }});
    map.on('mouseenter', 'river-gauges', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'river-gauges', () => map.getCanvas().style.cursor = '');

    // ── VOLCANOES ────────────────────────────────────
    map.addSource('volcanoes', {{ type: 'geojson', data: '/api/volcanoes' }});
    map.addLayer({{
        id: 'volcano-circles', type: 'circle', source: 'volcanoes',
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': ['get', 'color'],
            'circle-radius': 9,
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 2,
            'circle-opacity': 0.95
        }}
    }});
    map.on('click', 'volcano-circles', (e) => {{
        const p = e.features[0].properties;
        showPopup('🌋 ' + (p.name || 'Volcano'), {{
            'Alert Level': (p.alert || '').toUpperCase(),
            'Country':     p.country || 'N/A',
            'Source':      'GDACS'
        }}, e);
    }});
    map.on('mouseenter', 'volcano-circles', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'volcano-circles', () => map.getCanvas().style.cursor = '');

    // ── FEMA DISASTER DECLARATIONS ───────────────────
    map.addSource('fema_disasters', {{ type: 'geojson', data: '/api/fema_disasters' }});
    map.addLayer({{
        id: 'fema-disasters', type: 'circle', source: 'fema_disasters',
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': '#C084FC',
            'circle-radius': ['interpolate', ['linear'], ['get', 'count'], 1, 8, 10, 18],
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 1.5,
            'circle-opacity': 0.8
        }}
    }});
    map.on('click', 'fema-disasters', (e) => {{
        const p = e.features[0].properties;
        showPopup('🏛 FEMA Disasters — ' + p.state, {{
            'Active Declarations': p.count,
            'Types':   p.types || 'N/A',
            'Latest':  p.latest || 'N/A'
        }}, e);
    }});
    map.on('mouseenter', 'fema-disasters', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'fema-disasters', () => map.getCanvas().style.cursor = '');

    // ── EMERGENCY SHELTERS ───────────────────────────
    map.addSource('shelters', {{ type: 'geojson', data: '/api/shelters' }});
    map.addLayer({{
        id: 'shelter-circles', type: 'circle', source: 'shelters',
        layout: {{ visibility: 'none' }},
        paint: {{
            'circle-color': '#00FF88',
            'circle-radius': 7,
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 1.5,
            'circle-opacity': 0.9
        }}
    }});
    map.on('click', 'shelter-circles', (e) => {{
        const p = e.features[0].properties;
        showPopup('🏠 Emergency Shelter', {{
            'Name':    p.SHELTER_NAME || 'Open Shelter',
            'Address': (p.ADDRESS || '') + (p.CITY ? ', ' + p.CITY : '') + (p.STATE ? ', ' + p.STATE : ''),
            'Pet Friendly': p.PET_FRIENDLY === 'Yes' ? '✅ Yes' : '❌ No',
            'Capacity': p.CAPACITY || 'N/A'
        }}, e);
    }});
    map.on('mouseenter', 'shelter-circles', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'shelter-circles', () => map.getCanvas().style.cursor = '');

    // ── COUNTY HOVER TOOLTIP ─────────────────────────
    const hoverTooltip = document.createElement('div');
    hoverTooltip.id = 'hover-tooltip';
    document.body.appendChild(hoverTooltip);

    let _tooltipLastFid = null;
    let _tooltipRafPending = false;
    map.on('mousemove', 'counties-fill', (e) => {{
        // RAF throttle — at most one DOM update per rendered frame (~60fps cap)
        if (_tooltipRafPending) return;
        _tooltipRafPending = true;
        const point = e.point;
        const features = e.features;
        requestAnimationFrame(() => {{
            _tooltipRafPending = false;
            if (!features.length) return;
            const p = features[0].properties;
            const fid = p.county + p.state;
            if (fid !== _tooltipLastFid) {{
                _tooltipLastFid = fid;
                const pop = Number(p.population).toLocaleString();
                hoverTooltip.innerHTML = `<span style="color:#FF9600;font-weight:700;">${{p.county}}, ${{p.state}}</span><br><span style="color:rgba(255,255,255,0.5);">Pop: </span><span style="color:#fff;">${{pop}}</span>${{p.event ? ` <span style="color:#FF8888;">· ${{p.event}}</span>` : ''}}`;
            }}
            hoverTooltip.style.display = 'block';
            hoverTooltip.style.left = Math.min(point.x+14, window.innerWidth-280) + 'px';
            hoverTooltip.style.top  = Math.max(point.y-52, 10) + 'px';
        }});
    }});
    map.on('mouseleave', 'counties-fill', () => {{ hoverTooltip.style.display = 'none'; _tooltipLastFid = null; }});

    // ── NEXRAD AUTO-REFRESH (every 60s for latest radar) ─
    setInterval(() => {{
        if (map.getSource('nexrad')) {{
            const t = Date.now();
            map.getSource('nexrad').tiles = [
                `https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS=nexrad-n0r&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE&HEIGHT=256&WIDTH=256&SRS=EPSG:3857&BBOX={{bbox-epsg-3857}}&_t=${{t}}`
            ];
            try {{
                map.style.sourceCaches['nexrad'].clearTiles();
                map.style.sourceCaches['nexrad'].update(map.transform);
            }} catch(e) {{}}
        }}
    }}, 60000);

    // ── SHARED SOURCE CACHE ───────────────────────────────────────────────────────
    // All GeoJSON sources ship hidden (visibility:'none'), and Mapbox defers URL
    // fetches for hidden-only sources. Kick off explicit fetches in parallel so
    // data is already cached by the time the user toggles a layer on.
    window._srcPromises = window._srcPromises || {{}};
    window._srcData = window._srcData || {{}};
    function fetchSource(srcName, force) {{
        if (!force && window._srcPromises[srcName]) return window._srcPromises[srcName];
        const p = fetch('/api/' + srcName + '?t=' + Date.now())
            .then(r => r.json())
            .then(d => {{
                window._srcData[srcName] = d;
                if (map.getSource(srcName) && map.getSource(srcName).setData) {{
                    map.getSource(srcName).setData(d);
                }}
                return d;
            }})
            .catch(() => {{ delete window._srcPromises[srcName]; return null; }});
        window._srcPromises[srcName] = p;
        return p;
    }}
    [
        'warnings','spc','earthquakes','fires','counties','lightning',
        'fire_perimeters','storms','fema_disasters','river_gauges',
        'volcanoes','drought','shelters','air_quality'
    ].forEach(src => fetchSource(src));

    // ── LAYER TOGGLE BUTTONS (rendered into left sidebar) ─────────────────────────
    const sidebarBody = document.getElementById('sidebar-layers-body');
    if (sidebarBody) sidebarBody.innerHTML = '';
    const toggleContainer = sidebarBody || document.body;
    // Backward-compat id so legacy references don't break
    if (sidebarBody) sidebarBody.id = 'sidebar-layers-body';

    // Layer categories with their toggles
    const LAYER_GROUPS = [
        {{ name: 'WEATHER', icon: 'cyclone', toggles: [
            ['⚠ Active Warnings', ['warnings-fill','warnings-outline'], true],
            ['⛈ SPC Outlook', ['spc-fill','spc-outline'], false],
            ['⚡ Storm Reports', 'lightning-strikes', false],
            ['🌀 Hurricanes', ['storm-cone','storm-cone-outline','storm-track'], false],
            ['📡 NEXRAD Radar', 'nexrad-layer', false],
            ['🛰 GOES Infrared', 'goes-ir-layer', false],
        ]}},
        {{ name: 'FIRE & SEISMIC', icon: 'local_fire_department', toggles: [
            ['🔥 Fire Detections', 'fire-points', false],
            ['🔥 Fire Perimeters', ['fire-perimeter-fill','fire-perimeter-outline'], false],
            ['🔴 Earthquakes', 'eq-circles', false],
            ['🌋 Volcanoes', 'volcano-circles', false],
        ]}},
        {{ name: 'WATER & AIR', icon: 'water_drop', toggles: [
            ['🌊 Flood Gauges', 'river-gauges', false],
            ['💨 Air Quality', 'aqi-circles', false],
            ['🏜 Drought', 'drought-fill', false],
        ]}},
        {{ name: 'RESPONSE', icon: 'shield', toggles: [
            ['🗺 Affected Counties', ['counties-fill','counties-outline'], false],
            ['🏥 Hospitals', 'infra-normal', false],
            ['⚠ At-Risk Infra', 'infra-at-risk', false],
            ['🏠 Shelters', 'shelter-circles', false],
            ['🏛 FEMA Disasters', 'fema-disasters', false],
        ]}},
    ];

    function makeToggle(label, layerId, defaultOn) {{
        const btn = document.createElement('button');
        btn.className = 'layer-toggle';
        const ids = Array.isArray(layerId) ? layerId : [layerId];
        let on = defaultOn;
        let count = null;  // null = unknown, number = feature count
        let loading = false;
        const render = () => {{
            let countTxt = '';
            if (loading) {{
                countTxt = `<span class="layer-toggle-count" style="color:rgba(88,191,255,0.6);">···</span>`;
            }} else if (count !== null) {{
                const isEmpty = count === 0;
                const cls = isEmpty ? 'layer-toggle-count empty' : 'layer-toggle-count';
                const txt = isEmpty ? 'empty' : count;
                countTxt = `<span class="${{cls}}" style="color:${{isEmpty ? 'rgba(255,255,255,0.35)' : '#58bfff'}};">${{txt}}</span>`;
            }}
            const dotCls = loading ? 'layer-toggle-dot loading' : 'layer-toggle-dot';
            const dotStyle = loading
                ? ''
                : `style="background:${{on ? '#58bfff' : 'transparent'}};border-color:${{on ? '#58bfff' : 'rgba(255,255,255,0.25)'}};"`;
            btn.innerHTML = `
                <span class="${{dotCls}}" ${{dotStyle}}></span>
                <span class="layer-toggle-label" style="color:${{on ? '#dde9fb' : 'rgba(255,255,255,0.45)'}};">${{label}}</span>
                ${{countTxt}}
            `;
        }};
        render();

        // Seed count from cache/pending promise so the badge populates on page load,
        // even before the user clicks.
        (function seedCount() {{
            let srcName = null;
            for (const id of ids) {{
                const s = map.getLayer(id)?.source;
                if (s && map.getSource(s) && map.getSource(s).setData) {{ srcName = s; break; }}
            }}
            if (!srcName) return;
            if (window._srcData[srcName]) {{
                const d = window._srcData[srcName];
                count = (d && d.features) ? d.features.length : 0;
                render();
            }} else if (window._srcPromises[srcName]) {{
                window._srcPromises[srcName].then(d => {{
                    count = (d && d.features) ? d.features.length : 0;
                    render();
                }});
            }}
        }})();

        btn.onclick = () => {{
            on = !on;
            ids.forEach(id => {{
                if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
            }});
            if (!on) {{ render(); return; }}

            // Determine source to refresh (first one; all ids in a toggle share a source).
            let srcName = null;
            for (const id of ids) {{
                const s = map.getLayer(id)?.source;
                if (s && map.getSource(s) && map.getSource(s).setData) {{ srcName = s; break; }}
            }}
            if (!srcName) {{ render(); return; }}

            // If cached, use immediately; otherwise show spinner until promise resolves.
            if (window._srcData[srcName] && _searchContext === null) {{
                const d = window._srcData[srcName];
                if (map.getSource(srcName)) map.getSource(srcName).setData(d);
                count = (d && d.features) ? d.features.length : 0;
                render();
                return;
            }}
            if (_searchContext !== null) {{ render(); return; }}
            loading = true; render();
            fetchSource(srcName).then(d => {{
                loading = false;
                count = (d && d.features) ? d.features.length : 0;
                render();
            }}).catch(() => {{ loading = false; count = 0; render(); }});
        }};
        return btn;
    }}

    // ── COLLAPSIBLE LAYER GROUPS ─────────────────────────
    const _groupStateKey = 'nhm_group_open';
    let _groupState = {{}};
    try {{ _groupState = JSON.parse(localStorage.getItem(_groupStateKey) || '{{}}'); }} catch(e) {{}}
    const saveGroupState = () => {{
        try {{ localStorage.setItem(_groupStateKey, JSON.stringify(_groupState)); }} catch(e) {{}}
    }};

    LAYER_GROUPS.forEach((group, gi) => {{
        const section = document.createElement('div');
        section.className = 'layer-group';
        if (gi > 0) section.style.marginTop = '8px';

        const isOpen = _groupState[group.name] !== undefined ? _groupState[group.name] : (gi === 0);
        const header = document.createElement('div');
        header.className = 'layer-group-header';
        header.style.cursor = 'pointer';
        const renderHeader = (open) => {{
            header.innerHTML = `
                <span class="material-symbols-outlined" style="font-size:14px;color:#58bfff;">${{group.icon}}</span>
                <span style="flex:1;">${{group.name}}</span>
                <span class="layer-group-arrow" style="font-size:10px;transform:rotate(${{open ? 90 : 0}}deg);transition:transform 0.18s;">▶</span>
            `;
        }};
        renderHeader(isOpen);
        section.appendChild(header);

        const list = document.createElement('div');
        list.className = 'layer-group-list';
        list.style.display = isOpen ? 'flex' : 'none';
        group.toggles.forEach(([label, ids, def]) => list.appendChild(makeToggle(label, ids, def)));
        section.appendChild(list);

        header.onclick = () => {{
            const nowOpen = list.style.display === 'none';
            list.style.display = nowOpen ? 'flex' : 'none';
            renderHeader(nowOpen);
            _groupState[group.name] = nowOpen;
            saveGroupState();
        }};

        toggleContainer.appendChild(section);
    }});

    // ── BASEMAP SWITCHER (collapsible) ───────────────────
    const basemapSection = document.createElement('div');
    basemapSection.className = 'layer-group';
    basemapSection.style.cssText = 'margin-top:12px;border-top:1px solid rgba(88,191,255,0.15);padding-top:10px;';
    const bmOpen = _groupState['BASEMAP'] !== undefined ? _groupState['BASEMAP'] : false;
    const bmHeader = document.createElement('div');
    bmHeader.className = 'layer-group-header';
    bmHeader.style.cursor = 'pointer';
    const renderBmHeader = (open) => {{
        bmHeader.innerHTML = `
            <span class="material-symbols-outlined" style="font-size:14px;color:#58bfff;">map</span>
            <span style="flex:1;">BASEMAP</span>
            <span class="layer-group-arrow" style="font-size:10px;transform:rotate(${{open ? 90 : 0}}deg);transition:transform 0.18s;">▶</span>
        `;
    }};
    renderBmHeader(bmOpen);
    basemapSection.appendChild(bmHeader);
    const bmGrid = document.createElement('div');
    bmGrid.style.cssText = 'display:' + (bmOpen ? 'grid' : 'none') + ';grid-template-columns:1fr 1fr;gap:4px;margin-top:6px;';
    bmGrid.innerHTML = `
        <button class="bm-btn active" data-style="mapbox://styles/mapbox/dark-v11">🌑 Dark</button>
        <button class="bm-btn" data-style="mapbox://styles/mapbox/light-v11">☀️ Light</button>
        <button class="bm-btn" data-style="mapbox://styles/mapbox/satellite-streets-v12">🛰 Satellite</button>
        <button class="bm-btn" data-style="mapbox://styles/mapbox/streets-v12">🗺 Streets</button>
        <button class="bm-btn" data-style="mapbox://styles/mapbox/outdoors-v12">🌲 Outdoors</button>
        <button class="bm-btn" data-style="mapbox://styles/mapbox/navigation-night-v1">🚗 Nav Night</button>
    `;
    basemapSection.appendChild(bmGrid);
    bmHeader.onclick = () => {{
        const nowOpen = bmGrid.style.display === 'none';
        bmGrid.style.display = nowOpen ? 'grid' : 'none';
        renderBmHeader(nowOpen);
        _groupState['BASEMAP'] = nowOpen;
        saveGroupState();
    }};
    bmGrid.querySelectorAll('.bm-btn').forEach(btn => {{
        btn.onclick = () => {{
            const styleUrl = btn.getAttribute('data-style');
            map.setStyle(styleUrl);
            map.once('style.load', setupLayers);
            bmGrid.querySelectorAll('.bm-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        }};
    }});
    toggleContainer.appendChild(basemapSection);

    // ── LOAD STATS WITH RETRY ────────────────────────
    // Global hazard data for stat card fly-to
    let _latestWarnings    = null;
    let _latestEarthquakes = null;
    let _latestFires       = null;
    let _latestStorms      = null;
    let _dataLoaded        = false;
    let _prevSummary       = null;

    function loadData() {{
        fetch('/api/summary').then(r => r.json()).then(data => {{
            const s = data.summary || {{}};
            const hasData = (s.warnings_count > 0 || s.earthquakes > 0 || s.wildfires > 0);

            document.getElementById('stat-warnings').textContent = s.warnings_count || 0;
            document.getElementById('stat-eq').textContent       = s.earthquakes    || 0;
            document.getElementById('stat-fires').textContent    = s.wildfires      || 0;
            document.getElementById('stat-spc').textContent      = s.spc_zones      || 0;

            // ── DELTA INDICATORS ──────────────────────────────
            if (_prevSummary) {{
                const applyDelta = (id, newVal, oldVal) => {{
                    const el = document.getElementById(id);
                    if (!el) return;
                    const d = (newVal || 0) - (oldVal || 0);
                    if (d === 0) {{ el.textContent = ''; el.className = 'stat-delta'; return; }}
                    el.textContent = (d > 0 ? '+' : '') + d;
                    el.className   = 'stat-delta ' + (d > 0 ? 'up' : 'down');
                }};
                applyDelta('delta-warnings', s.warnings_count, _prevSummary.warnings_count);
                applyDelta('delta-eq',       s.earthquakes,    _prevSummary.earthquakes);
                applyDelta('delta-fires',    s.wildfires,      _prevSummary.wildfires);
                applyDelta('delta-spc',      s.spc_zones,      _prevSummary.spc_zones);
            }}
            _prevSummary = Object.assign({{}}, s);

            // Remove skeleton shimmer on first successful data load
            if (!_dataLoaded) {{
                _dataLoaded = true;
                document.querySelectorAll('.skel').forEach(el => el.classList.remove('skel'));
            }}
            updateSeverityBar(s);
            if (document.getElementById('stat-counties')) {{
                document.getElementById('stat-counties').textContent = s.counties_count || 0;
            }}
            if (document.getElementById('stat-pop')) {{
                const pop = s.total_population || 0;
                document.getElementById('stat-pop').textContent = pop > 1000000
                    ? (pop/1000000).toFixed(1) + 'M'
                    : pop > 1000 ? (pop/1000).toFixed(0) + 'K' : pop;
            }}

            if (data.last_update && data.last_update !== 'Never') {{
                document.getElementById('update-time').textContent = 'LAST UPDATED: ' + data.last_update;
                // ── AGE INDICATOR ─────────────────────────────
                const dotEl = document.getElementById('live-dot');
                const parsed = new Date(data.last_update.replace(' ', 'T'));
                if (!isNaN(parsed)) {{
                    const ageMin = (Date.now() - parsed.getTime()) / 60000;
                    const dotColor = ageMin < 15 ? '#00FF88' : ageMin < 40 ? '#FFCC00' : '#FF4400';
                    dotEl.style.setProperty('--dot-color', dotColor);
                    dotEl.style.background = dotColor;
                    dotEl.style.boxShadow = `0 0 8px ${{dotColor}}`;
                }}
            }} else {{
                document.getElementById('update-time').textContent = 'ACQUIRING LIVE DATA...';
            }}

            updateHazardChart(s);

            // Refresh all sources in parallel via shared cache (force=true bypasses dedup).
            // Skip while a buffer search is active — sources hold filtered data and
            // refreshing would overwrite the buffer clip.
            if (map.loaded() && _searchContext === null && typeof fetchSource === 'function') {{
                ['warnings','spc','earthquakes','fires','counties','lightning',
                 'fire_perimeters','storms','fema_disasters','river_gauges',
                 'volcanoes','drought','shelters','air_quality']
                    .forEach(src => fetchSource(src, true));
                // Infrastructure intentionally NOT auto-refreshed — Overpass API
                // takes up to 20s and the data changes rarely. Load once on first click.
            }}

            // If no data yet retry in 10 seconds
            if (!hasData) {{
                console.log('No data yet, retrying in 10s...');
                setTimeout(loadData, 10000);
                return;
            }}

            // ── STAT CARD FLY-TO ─────────────────────────
            // Fetch hazard centroids once data is loaded, wire up click handlers
            if (!_latestWarnings) {{
                fetch('/api/warnings').then(r=>r.json()).then(d=>{{ _latestWarnings = d; wireStatCards(); }}).catch(()=>{{}});
                fetch('/api/earthquakes').then(r=>r.json()).then(d=>{{ _latestEarthquakes = d; wireStatCards(); }}).catch(()=>{{}});
                fetch('/api/fires').then(r=>r.json()).then(d=>{{ _latestFires = d; wireStatCards(); }}).catch(()=>{{}});
                fetch('/api/storms').then(r=>r.json()).then(d=>{{ _latestStorms = d; }}).catch(()=>{{}});
            }}
        }}).catch(err => {{
            console.log('Fetch failed, retrying in 10s...', err);
            setTimeout(loadData, 10000);
        }});
    }}

    function wireStatCards() {{
        // Warnings card → fly to centroid of all warning features
        const warnEl = document.getElementById('stat-warnings').closest('.stat-card');
        if (warnEl && _latestWarnings?.features?.length) {{
            warnEl.onclick = () => {{
                try {{
                    const pts = _latestWarnings.features.flatMap(f => {{
                        const c = f.geometry?.coordinates;
                        if (!c) return [];
                        const flat = [];
                        const walk = a => Array.isArray(a[0]) ? a.forEach(walk) : flat.push(a);
                        walk(c); return flat;
                    }});
                    if (pts.length) {{
                        const lng = pts.reduce((s,p)=>s+p[0],0)/pts.length;
                        const lat = pts.reduce((s,p)=>s+p[1],0)/pts.length;
                        map.flyTo({{center:[lng,lat], zoom:5, duration:1400}});
                    }}
                }} catch(e) {{}}
            }};
        }}

        // Earthquakes card → fly to largest earthquake
        const eqEl = document.getElementById('stat-eq').closest('.stat-card');
        if (eqEl && _latestEarthquakes?.features?.length) {{
            eqEl.onclick = () => {{
                const biggest = _latestEarthquakes.features
                    .filter(f => f.geometry?.coordinates)
                    .sort((a,b) => (b.properties?.mag||0) - (a.properties?.mag||0))[0];
                if (biggest) {{
                    const [lng, lat] = biggest.geometry.coordinates;
                    map.flyTo({{center:[lng,lat], zoom:6, duration:1400}});
                }}
            }};
        }}

        // Fires card → fly to densest fire area
        const fireEl = document.getElementById('stat-fires').closest('.stat-card');
        if (fireEl && _latestFires?.features?.length) {{
            fireEl.onclick = () => {{
                try {{
                    const pts = _latestFires.features.filter(f=>f.geometry?.coordinates);
                    if (pts.length) {{
                        // Find centroid
                        const lng = pts.reduce((s,f)=>s+f.geometry.coordinates[0],0)/pts.length;
                        const lat = pts.reduce((s,f)=>s+f.geometry.coordinates[1],0)/pts.length;
                        map.flyTo({{center:[lng,lat], zoom:5, duration:1400}});
                    }}
                }} catch(e) {{}}
            }};
        }}

        // SPC card → fly to center of CONUS (SPC covers CONUS)
        const spcEl = document.getElementById('stat-spc').closest('.stat-card');
        if (spcEl) {{
            spcEl.onclick = () => map.flyTo({{center:[-98.35,39.5], zoom:4, duration:1400}});
        }}
    }}

    // Load immediately then every 5 minutes
    loadData();
    setInterval(loadData, 10 * 60 * 1000);
}}

// Use exact Mapbox recommended pattern
map.on('load', function() {{
    setupLayers();
}});

// ── LAYER PANEL COLLAPSE (no-op: layers now live in persistent sidebar) ───────
function collapseLayerPanelForSearch() {{}}
function restoreLayerPanelAfterSearch() {{}}

// ── SITREP ────────────────────────────────────────
let _sitrepRaw = '';
function openSitrep() {{
    document.getElementById('sitrep-overlay').classList.add('open');
    // Reset to loading state
    const setEl = (id, html) => {{ const el = document.getElementById(id); if (el) el.innerHTML = html; }};
    setEl('sitrep-level', '—');
    document.getElementById('sitrep-level').style.color = '#FF8C00';
    setEl('sitrep-status-badge', 'ANALYZING...');
    setEl('sitrep-summary', 'Generating report...');
    setEl('sitrep-threats', '<p style="color:#a0acbd;font-size:13px;">Analyzing threats...</p>');
    setEl('sitrep-actions', '<p style="color:#a0acbd;font-size:11px;">Processing...</p>');
    setEl('sitrep-confidence', '<p>&gt; FETCHING_DATA...</p>');
    _sitrepRaw = '';
    fetch('/api/sitrep')
        .then(r => r.json())
        .then(data => {{
            _sitrepRaw = data.raw || data.text || '';
            parseSitrep(_sitrepRaw);
        }})
        .catch(() => {{
            document.getElementById('sitrep-summary').textContent = 'Failed to generate report. Check GROQ_API_KEY.';
            document.getElementById('sitrep-summary').style.color = '#ff716c';
        }});
}}
function parseSitrep(text) {{
    // SEVERITY
    const sevMatch = text.match(/SEVERITY:\s*(\d+)/i);
    const severity = sevMatch ? parseInt(sevMatch[1], 10) : 5;
    const levelEl = document.getElementById('sitrep-level');
    if (levelEl) {{
        levelEl.textContent = isNaN(severity) ? '?' : severity;
        levelEl.style.color = severity >= 8 ? '#FF4444' : severity >= 5 ? '#FF8C00' : '#00CC66';
    }}
    const badge = document.getElementById('sitrep-status-badge');
    if (badge) {{
        const label = severity >= 8 ? 'CRITICAL RISK' : severity >= 6 ? 'ELEVATED RISK' : severity >= 4 ? 'ADVISORY' : 'NORMAL';
        const col   = severity >= 8 ? '#FF4444' : severity >= 6 ? '#FF8C00' : '#00CC66';
        badge.textContent = label;
        badge.style.color = col;
        badge.style.borderColor = col + '55';
        badge.style.background  = col + '18';
    }}
    // PRIORITY THREATS
    const threatsMatch = text.match(/PRIORITY THREATS:\\n([\\s\\S]*?)(?:\\n\\nSITUATION:|$)/i);
    const threatsEl = document.getElementById('sitrep-threats');
    if (threatsEl && threatsMatch) {{
        const items = threatsMatch[1].trim().split('\\n').filter(l => l.trim());
        const icons = ['warning', 'local_fire_department', 'cyclone'];
        const labels = ['CRITICAL', 'HIGH', 'MEDIUM'];
        const colors = ['#58bfff', '#FF8C00', '#ac89ff'];
        threatsEl.innerHTML = items.slice(0, 3).map((line, i) => {{
            const content = line.replace(/^\d+\.\s*/, '').trim();
            const c = colors[i] || colors[2];
            const ic = icons[i] || 'warning';
            return `<div style="display:flex;align-items:flex-start;gap:12px;background:rgba(21,39,57,0.5);padding:10px 12px;border-left:3px solid ${{c}};">
                <span class="material-symbols-outlined" style="color:${{c}};font-size:18px;flex-shrink:0;">${{ic}}</span>
                <div style="flex:1;min-width:0;">
                    <p style="font-size:12px;color:#dde9fb;line-height:1.4;">${{content}}</p>
                </div>
                <span style="font-size:10px;font-weight:700;color:${{c}};flex-shrink:0;">${{labels[i]||''}}</span>
            </div>`;
        }}).join('');
    }}
    // SITUATION
    const sitMatch = text.match(/SITUATION:\\s*([\\s\\S]*?)(?:\\n\\nACTIONS:|$)/i);
    const summaryEl = document.getElementById('sitrep-summary');
    if (summaryEl && sitMatch) summaryEl.textContent = sitMatch[1].trim();
    // ACTIONS
    const actMatch = text.match(/ACTIONS:\\s*([\\s\\S]*)$/i);
    const actionsEl = document.getElementById('sitrep-actions');
    if (actionsEl && actMatch) {{
        const acts = actMatch[1].trim().split('\\n').filter(l => l.trim());
        const codes = ['001-ALPHA', '002-BRAVO', '003-GAMMA', '004-DELTA'];
        actionsEl.innerHTML = acts.slice(0, 4).map((line, i) => {{
            const content = line.replace(/^[-\d.]+\s*/, '').trim();
            const isFirst = i === 0;
            return `<div style="position:relative;padding-left:20px;border-left:1px solid rgba(61,73,87,0.5);">
                <div style="position:absolute;left:-4px;top:2px;width:7px;height:7px;background:${{isFirst ? '#58bfff' : '#6a7686'}};"></div>
                <span style="font-size:9px;font-weight:700;color:${{isFirst ? '#58bfff' : '#a0acbd'}};letter-spacing:1px;">${{codes[i]||''}}</span>
                <p style="font-size:11px;color:#dde9fb;margin-top:3px;line-height:1.4;">${{content}}</p>
            </div>`;
        }}).join('');
    }}
    // AI terminal
    const confEl = document.getElementById('sitrep-confidence');
    if (confEl) confEl.innerHTML = `<p>&gt; ANALYSIS_COMPLETE</p><p>&gt; MODEL: GROQ-LLAMA-3.3</p><p>&gt; THREAT_VECTORS_MAPPED</p>`;
}}
function closeSitrep() {{
    document.getElementById('sitrep-overlay').classList.remove('open');
}}
function copySitrep() {{
    if (!_sitrepRaw) return;
    navigator.clipboard.writeText(_sitrepRaw).then(() => {{
        const fb = document.getElementById('sitrep-copy-feedback');
        if (fb) {{ fb.textContent = ' ✓'; setTimeout(() => {{ fb.textContent = ''; }}, 2000); }}
    }});
}}

// ── HAZARD OVERVIEW ROWS (replaces Chart.js) ──────
function updateHazardChart(summary) {{
    const s = summary || {{}};
    const keys = {{
        warnings_count: s.warnings_count || 0,
        earthquakes:    s.earthquakes    || 0,
        wildfires:      s.wildfires      || 0,
        river_gauges:   s.river_gauges   || 0,
        active_storms:  s.active_storms  || 0,
        volcanoes:      s.volcanoes      || 0,
    }};
    const maxVal = Math.max(1, ...Object.values(keys));
    document.querySelectorAll('.haz-row').forEach(row => {{
        const key   = row.dataset.key;
        const color = row.dataset.color;
        const val   = keys[key] || 0;
        const pct   = Math.round((val / maxVal) * 100);
        // Build inner HTML once if not already built
        if (!row.querySelector('.haz-bar-wrap')) {{
            const label = row.textContent.trim();
            row.innerHTML = `
                <span style="white-space:nowrap;">${{label}}</span>
                <div class="haz-bar-wrap"><div class="haz-bar" style="background:${{color}};"></div></div>
                <span class="haz-count" style="color:${{color}};">0</span>`;
        }}
        row.querySelector('.haz-bar').style.width = pct + '%';
        row.querySelector('.haz-count').textContent = val;
        row.style.opacity = val > 0 ? '1' : '0.35';
    }});
}}

// ── ADDRESS SEARCH & THREAT ANALYSIS ─────────────
const MAPBOX_TOKEN_JS = mapboxgl.accessToken;
let searchMarker = null;
let bufferLayer  = null;

// ── SCORE INPUTS (Immediate Threat Score) ─────────
const SCORE_INPUTS_KEY = 'nhm-score-inputs-v1';
const DEFAULT_SCORE_INPUTS = {{
    warnings: true,
    stormreports: true,
    earthquakes: true,
    firedetections: true,
    fireperimeters: true,
    rivergauges: false,
    hurricanes: false
}};

function _loadScoreInputs() {{
    try {{
        const raw = localStorage.getItem(SCORE_INPUTS_KEY);
        if (!raw) return {{ ...DEFAULT_SCORE_INPUTS }};
        const parsed = JSON.parse(raw);
        return {{ ...DEFAULT_SCORE_INPUTS, ...(parsed || {{}}) }};
    }} catch (e) {{
        return {{ ...DEFAULT_SCORE_INPUTS }};
    }}
}}

function _saveScoreInputs(v) {{
    try {{ localStorage.setItem(SCORE_INPUTS_KEY, JSON.stringify(v)); }} catch (e) {{}}
}}

function readScoreInputsFromUI() {{
    const get = (id, fallback) => {{
        const el = document.getElementById(id);
        return el ? !!el.checked : fallback;
    }};
    return {{
        warnings:       get('si-warnings', true),
        stormreports:   get('si-stormreports', true),
        earthquakes:    get('si-earthquakes', true),
        firedetections: get('si-firedetections', true),
        fireperimeters: get('si-fireperimeters', true),
        rivergauges:    get('si-rivergauges', false),
        hurricanes:     get('si-hurricanes', false),
    }};
}}

function writeScoreInputsToUI(v) {{
    const set = (id, val) => {{ const el = document.getElementById(id); if (el) el.checked = !!val; }};
    set('si-warnings',       v.warnings);
    set('si-stormreports',   v.stormreports);
    set('si-earthquakes',    v.earthquakes);
    set('si-firedetections', v.firedetections);
    set('si-fireperimeters', v.fireperimeters);
    set('si-rivergauges',    v.rivergauges);
    set('si-hurricanes',     v.hurricanes);
}}

function _toast(msg) {{
    const note = document.createElement('div');
    note.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:250;padding:10px 14px;' +
        'background:rgba(4,15,27,0.92);border:1px solid rgba(88,191,255,0.25);' +
        'color:#a8d8ff;font-size:11px;font-family:Inter,sans-serif;letter-spacing:0.6px;';
    note.textContent = msg;
    document.body.appendChild(note);
    setTimeout(() => {{ try {{ note.remove(); }} catch(e) {{}} }}, 1600);
}}

function applyScorePreset(name) {{
    let v = {{ ...DEFAULT_SCORE_INPUTS }};
    if (name === 'weather') {{
        v = {{ ...DEFAULT_SCORE_INPUTS,
            firedetections: false, fireperimeters: false, earthquakes: false,
            rivergauges: false, hurricanes: false
        }};
    }} else if (name === 'fire') {{
        v = {{ ...DEFAULT_SCORE_INPUTS,
            warnings: false, stormreports: false, earthquakes: false,
            rivergauges: false, hurricanes: false
        }};
    }} else {{
        v = {{ ...DEFAULT_SCORE_INPUTS }};
    }}
    writeScoreInputsToUI(v);
    _saveScoreInputs(v);
    _toast('Score preset: ' + (name || 'immediate').toUpperCase());
}}

// Update buffer label and re-run analysis when slider changes
let _sliderDebounce = null;
document.getElementById('buffer-slider').addEventListener('input', function() {{
    document.getElementById('buffer-label').textContent = this.value + ' miles';
    // Re-run analysis only if a search is already active
    if (_searchContext !== null) {{
        clearTimeout(_sliderDebounce);
        _sliderDebounce = setTimeout(() => searchLocation(), 400);
    }}
}});

// Enter key triggers search
document.getElementById('address-input').addEventListener('keypress', function(e) {{
    if (e.key === 'Enter') searchLocation();
}});

// Restore persisted score inputs on load and persist changes
document.addEventListener('DOMContentLoaded', () => {{
    const v = _loadScoreInputs();
    writeScoreInputsToUI(v);
    ['si-warnings','si-stormreports','si-earthquakes','si-firedetections','si-fireperimeters','si-rivergauges','si-hurricanes']
        .forEach(id => {{
            const el = document.getElementById(id);
            if (!el) return;
            el.addEventListener('change', () => _saveScoreInputs(readScoreInputsFromUI()));
        }});
}});

// ── FEMA NRI LOOKUP ──────────────────────────────
// Loaded once on first search, then cached for all subsequent lookups.
let _nriData = null;

async function _loadNRI() {{
    if (_nriData) return _nriData;
    try {{
        const r = await fetch('/static/nri_counties.json');
        if (!r.ok) {{ console.log('NRI load failed:', r.status); return null; }}
        _nriData = await r.json();
        console.log('NRI loaded:', Object.keys(_nriData).length, 'counties');
        return _nriData;
    }} catch(e) {{
        console.log('NRI load error:', e);
        return null;
    }}
}}

async function fetchNRI(stateAbbr, countyName) {{
    if (!stateAbbr) return null;
    const data = await _loadNRI();
    if (!data) return null;

    const norm = s => (s || '').toLowerCase()
        .replace(/ county$/, '').replace(/ parish$/, '')
        .replace(/ borough$/, '').replace(/ census area$/, '').trim();

    const targetState  = stateAbbr.toUpperCase();
    const targetCounty = norm(countyName);

    let match = null;
    // Exact match first
    for (const d of Object.values(data)) {{
        if (d.sa === targetState && norm(d.co) === targetCounty) {{ match = d; break; }}
    }}
    // Partial fallback
    if (!match && targetCounty) {{
        for (const d of Object.values(data)) {{
            if (d.sa === targetState) {{
                const c = norm(d.co);
                if (c.includes(targetCounty) || targetCounty.includes(c)) {{ match = d; break; }}
            }}
        }}
    }}
    if (!match) {{ console.log('NRI: no match for', targetState, targetCounty); return null; }}

    return {{
        COUNTY:    match.co,
        STATE:     match.sa,
        RISK_SCORE: match.rs || 0,
        RISK_RATNG: match.rr || '',
        SOVI_SCORE: match.ss || 0,
        SOVI_RATNG: match.sr || '',
        RESL_SCORE: match.ls || 0,
        RESL_RATNG: match.lr || '',
        EAL_VALT:  match.ev || 0,
        TRND_EALT: match.to || 0,
        WFIR_EALT: match.wf || 0,
        ERQK_EALT: match.eq || 0,
        RFLD_EALT: match.fl || 0,
        HRCN_EALT: match.hu || 0,
        ISTM_EALT: match.is || 0,
        LTNG_EALT: match.lt || 0,
        HAIL_EALT: match.ha || 0,
    }};
}}

function getRatingColor(rating) {{
    const r = (rating || '').toLowerCase();
    if (r.includes('very high'))      return '#FF0000';
    if (r.includes('relatively high')) return '#FF6600';
    if (r.includes('high'))           return '#FF4400';
    if (r.includes('relatively mod')) return '#FFCC00';
    if (r.includes('moderate'))       return '#FFFF00';
    if (r.includes('relatively low')) return '#88FF00';
    if (r.includes('low'))            return '#00FF88';
    return '#888888';
}}

function formatDollars(val) {{
    if (!val || val <= 0) return 'N/A';
    if (val >= 1e9) return '$' + (val/1e9).toFixed(1) + 'B/yr';
    if (val >= 1e6) return '$' + (val/1e6).toFixed(1) + 'M/yr';
    if (val >= 1e3) return '$' + (val/1e3).toFixed(0) + 'K/yr';
    return '$' + Math.round(val);
}}

function buildNRIPanel(nri, countyName) {{
    if (!nri) return '';
    
    const riskColor  = getRatingColor(nri.RISK_RATNG);
    const soviColor  = getRatingColor(nri.SOVI_RATNG);
    const reslColor  = getRatingColor(nri.RESL_RATNG);
    // Resilience is inverse - low resilience = high risk
    const reslRisk   = 100 - (nri.RESL_SCORE || 50);
    
    const hazards = [
        {{ name: 'Tornado',    val: nri.TRND_EALT, color: '#FF0000' }},
        {{ name: 'Wildfire',   val: nri.WFIR_EALT, color: '#FF4500' }},
        {{ name: 'Earthquake', val: nri.ERQK_EALT, color: '#00B4FF' }},
        {{ name: 'Riv. Flood', val: nri.RFLD_EALT, color: '#0088FF' }},
        {{ name: 'Hurricane',  val: nri.HRCN_EALT, color: '#FF6600' }},
        {{ name: 'Ice Storm',  val: nri.ISTM_EALT, color: '#AAAAFF' }},
        {{ name: 'Lightning',  val: nri.LTNG_EALT, color: '#FFFF00' }},
        {{ name: 'Hail',       val: nri.HAIL_EALT, color: '#88FFFF' }},
    ].filter(h => h.val > 0).sort((a,b) => b.val - a.val).slice(0, 6);

    return `
    <div class="nri-section">
        <div class="nri-title">🏛 FEMA National Risk Index — ${{nri.COUNTY || countyName}} Co.</div>
        
        <div class="nri-score-row">
            <div>
                <div class="nri-label">OVERALL RISK</div>
                <div class="nri-bar-wrap" style="width:120px;margin-top:4px">
                    <div class="nri-bar" style="width:${{nri.RISK_SCORE||0}}%;background:${{riskColor}}"></div>
                </div>
            </div>
            <div style="text-align:right">
                <div class="nri-value" style="color:${{riskColor}}">${{(nri.RISK_SCORE||0).toFixed(1)}}</div>
                <div style="font-size:9px;color:${{riskColor}};letter-spacing:1px">${{(nri.RISK_RATNG||'N/A').toUpperCase()}}</div>
            </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin:6px 0">
            <div class="nri-score-row" style="flex-direction:column;align-items:flex-start">
                <div class="nri-label">SOCIAL VULNERABILITY</div>
                <div class="nri-value" style="color:${{soviColor}}">${{(nri.SOVI_SCORE||0).toFixed(1)}} <span style="font-size:9px;opacity:0.7">/100</span></div>
                <div style="font-size:9px;color:${{soviColor}}">${{(nri.SOVI_RATNG||'N/A').toUpperCase()}}</div>
            </div>
            <div class="nri-score-row" style="flex-direction:column;align-items:flex-start">
                <div class="nri-label">COMMUNITY RESILIENCE</div>
                <div class="nri-value" style="color:${{reslColor}}">${{(nri.RESL_SCORE||0).toFixed(1)}} <span style="font-size:9px;opacity:0.7">/100</span></div>
                <div style="font-size:9px;color:${{reslColor}}">${{(nri.RESL_RATNG||'N/A').toUpperCase()}}</div>
            </div>
        </div>

        <div class="nri-score-row">
            <div class="nri-label">EXPECTED ANNUAL LOSS</div>
            <div class="nri-value" style="color:#FFD700">${{formatDollars(nri.EAL_VALT)}}</div>
        </div>

        ${{hazards.length ? `
        <div class="nri-title" style="margin-top:8px">TOP HAZARD LOSSES/YR</div>
        <div class="nri-hazards">
            ${{hazards.map(h => `
                <div class="nri-hazard-item" style="border-color:${{h.color}}">
                    <div class="nri-hazard-name">${{h.name}}</div>
                    <div class="nri-hazard-val" style="color:${{h.color}}">${{formatDollars(h.val)}}</div>
                </div>
            `).join('')}}
        </div>` : ''}}
    </div>`;
}}

async function searchLocation() {{
    const btn = document.getElementById('search-btn');
    btn.textContent = '⏳ ANALYZING...';
    btn.disabled = true;

    let lat, lng, placeName, nriState = '', nriCounty = '';
    const scoreInputs = readScoreInputsFromUI();
    _saveScoreInputs(scoreInputs);

    try {{
        if (_gpsOverride) {{
            // GPS path — skip geocoding, use exact device coordinates
            lat = _gpsOverride.lat;
            lng = _gpsOverride.lng;
            const feat = _gpsOverride.feature;
            _gpsOverride = null;
            placeName = feat?.place_name || 'Your Location';
            for (const ctx of (feat?.context || [])) {{
                if (ctx.id.startsWith('region'))   nriState  = (ctx.short_code || '').replace('US-', '');
                if (ctx.id.startsWith('district')) nriCounty = ctx.text || '';
            }}
        }} else {{
            // Address search path — geocode the input
            const address = document.getElementById('address-input').value.trim();
            if (!address) {{ btn.textContent = '🔍 ANALYZE THREATS'; btn.disabled = false; return; }}
            const geoUrl = 'https://api.mapbox.com/geocoding/v5/mapbox.places/' +
                encodeURIComponent(address) +
                '.json?country=US&limit=1&access_token=' + MAPBOX_TOKEN_JS;
            const geo = await fetch(geoUrl);
            if (!geo.ok) throw new Error('Geocoding failed: ' + geo.status);
            const geoData = await geo.json();
            if (!geoData.features || geoData.features.length === 0) {{
                showResults([{{type:'error', text:'Address not found. Try a different search.'}}]);
                btn.textContent = '🔍 ANALYZE THREATS'; btn.disabled = false;
                return;
            }}
            [lng, lat] = geoData.features[0].center;
            placeName  = geoData.features[0].place_name;
            for (const ctx of (geoData.features[0].context || [])) {{
                if (ctx.id.startsWith('region'))   nriState  = (ctx.short_code || '').replace('US-', '');
                if (ctx.id.startsWith('district')) nriCounty = ctx.text || '';
            }}
        }}

        const radiusMiles = parseFloat(document.getElementById('buffer-slider').value);
        const radiusKm    = radiusMiles * 1.60934;

        // Fly to location
        map.flyTo({{ center: [lng, lat], zoom: 7, duration: 1500 }});

        // Fetch FEMA NRI data in parallel (local lookup, no extra API call)
        const nriPromise = fetchNRI(nriState, nriCounty);

        // Remove old marker and buffer (don't restore data — we're about to set it)
        clearSearch(false, false);

        // Add marker at location
        const el = document.createElement('div');
        el.style.cssText = `
            width: 16px; height: 16px; background: #00B4FF;
            border: 3px solid white; border-radius: 50%;
            box-shadow: 0 0 20px #00B4FF;
        `;
        searchMarker = new mapboxgl.Marker(el).setLngLat([lng, lat]).addTo(map);

        // Create buffer circle using Turf.js
        const point  = turf.point([lng, lat]);
        const buffer = turf.circle(point, radiusKm, {{ steps: 64, units: 'kilometers' }});

        // Add buffer to map
        if (map.getSource('search-buffer')) {{
            map.getSource('search-buffer').setData(buffer);
        }} else {{
            map.addSource('search-buffer', {{ type: 'geojson', data: buffer }});
            map.addLayer({{
                id: 'buffer-fill', type: 'fill', source: 'search-buffer',
                paint: {{ 'fill-color': '#00B4FF', 'fill-opacity': 0.08 }}
            }});
            map.addLayer({{
                id: 'buffer-outline', type: 'line', source: 'search-buffer',
                paint: {{ 'line-color': '#00B4FF', 'line-width': 2, 'line-dasharray': [4,4] }}
            }});
        }}

        // Fetch all hazard data and analyze
        const empty = {{type:'FeatureCollection',features:[]}};
        const safeJson = async (url, transform) => {{
            try {{
                const r = await fetch(url);
                if (!r.ok) {{ console.log('API failed:', url, r.status); return empty; }}
                const d = await r.json();
                return transform ? transform(d) : d;
            }} catch(e) {{ console.log('API error:', url, e.message); return empty; }}
        }};

        const severe = ['TORNADO','HAIL','TSTM WND GST','TSTM WND DMG','FUNNEL CLOUD','LIGHTNING','FLASH FLOOD'];

        const [warnings, earthquakes, fires, lightning, perimeters,
               spcData, droughtData, stormsData, countiesData,
               riverData, volcanoData, femaData, aqiData, shelterData] = await Promise.all([
            safeJson('/api/warnings'),
            safeJson('/api/earthquakes'),
            safeJson('/api/fires'),
            safeJson('https://mesonet.agron.iastate.edu/geojson/lsr.php?hours=6&wfo=all',
                d => ({{type:'FeatureCollection', features:(d.features||[]).filter(f => severe.some(x => (f.properties&&f.properties.typetext||'').toUpperCase().indexOf(x)>=0))}})),
            safeJson('https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_YTD/FeatureServer/0/query?where=1%3D1&outFields=IncidentName,GISAcres,PercentContained&geometryPrecision=3&outSR=4326&resultRecordCount=200&f=geojson'),
            safeJson('/api/spc'),
            safeJson('/api/drought'),
            safeJson('/api/storms'),
            safeJson('/api/counties'),
            safeJson('/api/river_gauges'),
            safeJson('/api/volcanoes'),
            safeJson('/api/fema_disasters'),
            safeJson('/api/air_quality'),
            safeJson('/api/shelters'),
        ]);

        const threats = [];
        const threatObjs = [];
        let totalScore = 0;
        const userPt = turf.point([lng, lat]);

        const addThreat = (obj) => {{
            // obj: {{kind,label,points,dist,color,source,detail}}
            threatObjs.push(obj);
            threats.push({{
                type: 'threat',
                color: obj.color,
                dist:  obj.dist,
                points: obj.points,
                source: obj.source,
                text:  obj.label
            }});
        }};

        // ── NWS WARNINGS ─────────────────────────────
        const warningsInBuffer = (warnings.features || []).filter(f => {{
            try {{
                if (!f.geometry) return false;
                if (f.geometry.type === 'Point') {{
                    return turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer);
                }}
                return turf.booleanIntersects(f, buffer);
            }} catch(e) {{ return false; }}
        }});

        if (scoreInputs.warnings) warningsInBuffer.forEach(f => {{
            const phenom = (f.properties?.phenom || '').toUpperCase();
            const sig    = (f.properties?.sig    || '').toUpperCase();
            let weight   = THREAT_WEIGHTS.other_warning;
            let label    = '⚠ Warning';
            let color    = '#FF8800';

            if (phenom === 'TO' && sig === 'W') {{ weight = THREAT_WEIGHTS.tornado_warning;   label = '🌪 Tornado Warning';             color = '#FF0000'; }}
            else if (phenom === 'HU')           {{ weight = THREAT_WEIGHTS.hurricane_warning;  label = '🌀 Hurricane Warning/Watch';     color = '#FF6600'; }}
            else if (phenom === 'FF')           {{ weight = THREAT_WEIGHTS.flash_flood;        label = '🌊 Flash Flood Warning';         color = '#00BFFF'; }}
            else if (phenom === 'FA')           {{ weight = THREAT_WEIGHTS.flood_warning;      label = '🌊 Flood Warning';               color = '#0099FF'; }}
            else if (phenom === 'SV')           {{ weight = THREAT_WEIGHTS.severe_tstorm;      label = '⛈ Severe Thunderstorm Warning'; color = '#FF6666'; }}
            else if (phenom === 'WS')           {{ weight = THREAT_WEIGHTS.winter_storm;       label = '❄ Winter Storm Warning';        color = '#AAAAFF'; }}
            else if (phenom === 'FW')           {{ weight = THREAT_WEIGHTS.other_warning + 5;  label = '🔥 Fire Weather Warning';        color = '#FF4500'; }}

            // Calculate distance to warning centroid
            let dist = radiusMiles;
            try {{
                const centroid = turf.centroid(f);
                dist = turf.distance(userPt, centroid, {{units: 'miles'}});
            }} catch(e) {{}}

            const decay   = distanceDecay(dist, radiusMiles);
            const pts     = Math.round(weight * decay);
            totalScore   += pts;
            addThreat({{
                kind: 'warning',
                label: `${{label}}`,
                points: pts,
                dist,
                color,
                source: 'NWS',
                detail: phenom + '/' + sig
            }});
        }});

        // ── EARTHQUAKES ───────────────────────────────
        const eqFeats = (earthquakes.features || [])
            .filter(f => f.geometry?.coordinates)
            .map(f => ({{
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }}))
            .filter(f => {{
                try {{ return turf.booleanPointInPolygon(f._pt, buffer); }}
                catch(e) {{ return false; }}
            }});

        if (scoreInputs.earthquakes) eqFeats.forEach(f => {{
            const mag  = parseFloat(f.properties?.mag || 0);
            const dist = turf.distance(userPt, f._pt, {{units: 'miles'}});
            let weight = mag >= 5 ? THREAT_WEIGHTS.earthquake_m5
                       : mag >= 4 ? THREAT_WEIGHTS.earthquake_m4
                       :            THREAT_WEIGHTS.earthquake_m3;
            const decay = distanceDecay(dist, radiusMiles);
            const pts   = Math.round(weight * decay);
            totalScore += pts;
            addThreat({{
                kind: 'earthquake',
                label: `🔴 Earthquake M${{mag.toFixed(1)}} — ${{f.properties?.place || 'Unknown'}}`,
                points: pts,
                dist,
                color: '#00B4FF',
                source: 'USGS',
                detail: 'M' + mag.toFixed(1)
            }});
        }});

        // ── WILDFIRES ─────────────────────────────────
        const fireFeats = (fires.features || [])
            .filter(f => f.geometry?.coordinates)
            .map(f => ({{
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }}))
            .filter(f => {{
                try {{ return turf.booleanPointInPolygon(f._pt, buffer); }}
                catch(e) {{ return false; }}
            }});

        if (scoreInputs.firedetections && fireFeats.length > 0) {{
            let firePtsTotal = 0;
            let closestDist = Infinity;
            fireFeats.forEach(f => {{
                const dist  = turf.distance(userPt, f._pt, {{units:'miles'}});
                const decay = distanceDecay(dist, radiusMiles);
                const pts   = Math.round(THREAT_WEIGHTS.wildfire_near * decay);
                totalScore   += pts;
                firePtsTotal += pts;
                if (dist < closestDist) closestDist = dist;
            }});
            addThreat({{
                kind: 'fire_detection',
                label: `🔥 ${{fireFeats.length}} Fire Detection(s) — closest ${{Math.round(closestDist)}}mi`,
                points: firePtsTotal,
                dist: closestDist,
                color: '#FF5000',
                source: 'NASA FIRMS',
                detail: String(fireFeats.length)
            }});
        }}

        // ── STORM REPORTS ─────────────────────────────
        const stormFeats = (lightning.features || [])
            .filter(f => f.geometry?.coordinates)
            .map(f => ({{
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }}))
            .filter(f => {{
                try {{ return turf.booleanPointInPolygon(f._pt, buffer); }}
                catch(e) {{ return false; }}
            }});

        if (scoreInputs.stormreports && stormFeats.length > 0) {{
            // Time decay — recent reports weighted more
            const now = Date.now();
            let stormPtsTotal = 0;
            stormFeats.forEach(f => {{
                const validTime = new Date(f.properties?.valid || now).getTime();
                const hoursAgo  = (now - validTime) / 3600000;
                const recency   = Math.max(0, 1 - hoursAgo / 6);
                const dist      = turf.distance(userPt, f._pt, {{units:'miles'}});
                const decay     = distanceDecay(dist, radiusMiles);
                const pts       = Math.round(THREAT_WEIGHTS.storm_report * decay * recency);
                totalScore     += pts;
                stormPtsTotal  += pts;
            }});
            const types = [...new Set(stormFeats.map(f => f.properties?.typetext || 'Storm').slice(0,3))];
            const closestStorm = stormFeats.reduce((a,b) =>
                turf.distance(userPt,a._pt,{{units:'miles'}}) <
                turf.distance(userPt,b._pt,{{units:'miles'}}) ? a : b
            );
            const distClosest = turf.distance(userPt, closestStorm._pt, {{units:'miles'}});
            addThreat({{
                kind: 'storm_report',
                label: `⚡ ${{stormFeats.length}} Storm Report(s) — ${{types.join(', ')}}`,
                points: stormPtsTotal,
                dist: distClosest,
                color: '#FFFF00',
                source: 'NWS LSR',
                detail: String(stormFeats.length)
            }});
        }}

        // ── FIRE PERIMETERS ───────────────────────────
        const perimInBuffer = (perimeters.features || []).filter(f => {{
            try {{
                if (!f.geometry) return false;
                return turf.booleanIntersects(f, buffer);
            }} catch(e) {{ return false; }}
        }});

        if (scoreInputs.fireperimeters) perimInBuffer.forEach(f => {{
            const acres = parseFloat(f.properties?.GISAcres || 0);
            const name  = f.properties?.IncidentName || 'Active Fire';
            let weight  = THREAT_WEIGHTS.fire_perimeter;
            // Scale weight by fire size
            if (acres > 100000) weight *= 1.5;
            else if (acres > 10000) weight *= 1.2;

            let dist = radiusMiles / 2;
            try {{
                const centroid = turf.centroid(f);
                dist = turf.distance(userPt, centroid, {{units:'miles'}});
            }} catch(e) {{}}

            const decay = distanceDecay(dist, radiusMiles);
            const pts   = Math.round(weight * decay);
            totalScore += pts;
            addThreat({{
                kind: 'fire_perimeter',
                label: `🔥 ${{name}} — ${{Math.round(acres).toLocaleString()}} acres (${{f.properties?.PercentContained||0}}% contained)`,
                points: pts,
                dist,
                color: '#FF4500',
                source: 'WFIGS',
                detail: Math.round(acres)
            }});
        }});

        // ── RIVER FLOOD GAUGES (optional) ─────────────
        if (scoreInputs.rivergauges) {{
            const gaugesInBuffer = (riverData.features || [])
                .filter(f => f.geometry?.coordinates)
                .map(f => ({{ ...f, _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]]) }}))
                .filter(f => {{
                    try {{ return turf.booleanPointInPolygon(f._pt, buffer); }}
                    catch(e) {{ return false; }}
                }});

            const statusWeight = (status) => {{
                const s = (status || '').toLowerCase();
                if (s === 'major')    return THREAT_WEIGHTS.flood_gauge_major;
                if (s === 'moderate') return THREAT_WEIGHTS.flood_gauge_moderate;
                if (s === 'minor')    return THREAT_WEIGHTS.flood_gauge_minor;
                if (s === 'action')   return THREAT_WEIGHTS.flood_gauge_action;
                return 0;
            }};

            gaugesInBuffer.forEach(f => {{
                const p = f.properties || {{}};
                const w = statusWeight(p.status);
                if (!w) return;
                const dist = turf.distance(userPt, f._pt, {{units:'miles'}});
                const decay = distanceDecay(dist, radiusMiles);
                const pts   = Math.round(w * decay);
                totalScore += pts;
                addThreat({{
                    kind: 'flood_gauge',
                    label: `🌊 Flood Gauge — ${{(p.location||p.name||'Gauge')}} (${{String(p.status||'').toUpperCase()}})`,
                    points: pts,
                    dist,
                    color: '#00BFFF',
                    source: 'NOAA AHPS',
                    detail: p.gaugelid || ''
                }});
            }});
        }}

        // ── HURRICANES / TROPICAL SYSTEMS (optional) ───
        if (scoreInputs.hurricanes) {{
            const stormsInBuffer = (stormsData.features || []).filter(f => {{
                try {{
                    if (!f.geometry) return false;
                    if (f.geometry.type === 'Point') {{
                        return turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer);
                    }}
                    return turf.booleanIntersects(f, buffer);
                }} catch(e) {{ return false; }}
            }});
            if (stormsInBuffer.length) {{
                const coneHits  = stormsInBuffer.filter(f => (f.properties?.layer || '') === 'cone');
                const trackHits = stormsInBuffer.filter(f => (f.properties?.layer || '') === 'track');
                const name = (coneHits[0]?.properties?.storm_name || trackHits[0]?.properties?.storm_name || 'Storm');
                if (coneHits.length) {{
                    let dist = radiusMiles;
                    try {{ const c = turf.centroid(coneHits[0]); dist = turf.distance(userPt, c, {{units:'miles'}}); }} catch(e) {{}}
                    const decay = distanceDecay(dist, radiusMiles);
                    const pts = Math.round(THREAT_WEIGHTS.hurricane_cone * decay);
                    totalScore += pts;
                    addThreat({{
                        kind: 'hurricane_cone',
                        label: `🌀 Forecast Cone intersects area — ${{name}}`,
                        points: pts,
                        dist,
                        color: '#FF6600',
                        source: 'NHC',
                        detail: name
                    }});
                }} else if (trackHits.length) {{
                    let dist = radiusMiles;
                    try {{ const c = turf.centroid(trackHits[0]); dist = turf.distance(userPt, c, {{units:'miles'}}); }} catch(e) {{}}
                    const decay = distanceDecay(dist, radiusMiles);
                    const pts = Math.round(THREAT_WEIGHTS.hurricane_track * decay);
                    totalScore += pts;
                    addThreat({{
                        kind: 'hurricane_track',
                        label: `🌀 Storm track points within area — ${{name}}`,
                        points: pts,
                        dist,
                        color: '#FF6600',
                        source: 'NHC',
                        detail: name
                    }});
                }}
            }}
        }}

        // ── CAP SCORE & SORT ──────────────────────────
        totalScore = Math.min(100, Math.round(totalScore));

        // Sort threats by distance
        threats.sort((a,b) => (a.dist||99) - (b.dist||99));

        // Store search context for county briefing + email alerts
        _searchContext = {{ lat, lng, label: placeName.split(',').slice(0,2).join(','), radius: radiusMiles }};
        dismissHero();  // remove hero panel if still visible

        // ── ACTIVATE SELECTED LAYERS (Immediate Threat Score) ───────────────
        // Only turn on layers the user chose to include in scoring + counties context.
        const _AUTO_ON = [];
        if (scoreInputs.warnings)       _AUTO_ON.push('warnings-fill','warnings-outline');
        if (scoreInputs.earthquakes)    _AUTO_ON.push('eq-circles');
        if (scoreInputs.firedetections) _AUTO_ON.push('fire-points');
        if (scoreInputs.fireperimeters) _AUTO_ON.push('fire-perimeter-fill','fire-perimeter-outline');
        if (scoreInputs.stormreports)   _AUTO_ON.push('lightning-strikes');
        if (scoreInputs.rivergauges)    _AUTO_ON.push('river-gauges');
        if (scoreInputs.hurricanes)     _AUTO_ON.push('storm-cone','storm-cone-outline','storm-track');
        // Always show affected counties in analysis mode (helps interpret exposure)
        _AUTO_ON.push('counties-fill','counties-outline');

        // Turn off most layers to avoid clutter, then enable selected ones.
        const _TOGGLEABLE = [
            'warnings-fill','warnings-outline',
            'spc-fill','spc-outline',
            'eq-circles',
            'fire-points','fire-perimeter-fill','fire-perimeter-outline',
            'lightning-strikes',
            'storm-cone','storm-cone-outline','storm-track',
            'river-gauges','volcano-circles',
            'drought-fill','fema-disasters','aqi-circles',
            'shelter-circles','infra-normal','infra-at-risk',
            'counties-fill','counties-outline',
            'nexrad-layer','goes-ir-layer'
        ];
        _TOGGLEABLE.forEach(id => {{
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none');
        }});
        _AUTO_ON.forEach(id => {{
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'visible');
        }});

        // ── CLIP ALL SOURCES TO THE BUFFER ZONE ─────────────────────────────
        // Polygon features are clipped to the buffer circle via turf.intersect
        // so only the portion inside the circle renders — not the full polygon.
        // Point features are filtered via booleanPointInPolygon.
        // loadData() is blocked from overwriting these while _searchContext is set.

        // Polygon/mixed sources: clip polygons to buffer boundary, filter points in
        const polyFilter = (fc) => {{
            const feats = [];
            for (const f of (fc.features || [])) {{
                try {{
                    if (!f.geometry) continue;
                    const gt = f.geometry.type;
                    if (gt === 'Point') {{
                        if (turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer))
                            feats.push(f);
                    }} else if (gt === 'Polygon' || gt === 'MultiPolygon') {{
                        const clipped = turf.intersect(f, buffer);
                        if (clipped) {{ clipped.properties = f.properties; feats.push(clipped); }}
                    }} else if (turf.booleanIntersects(f, buffer)) {{
                        feats.push(f);  // LineString etc — include if intersects
                    }}
                }} catch(e) {{}}
            }}
            return {{type:'FeatureCollection', features: feats}};
        }};

        // Point-only sources: filter to points inside buffer
        const ptFilter = (fc) => ({{
            type: 'FeatureCollection',
            features: (fc.features || []).filter(f => {{
                try {{
                    if (!f.geometry?.coordinates) return false;
                    return turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer);
                }} catch(e) {{ return false; }}
            }})
        }});

        // Clip only the selected immediate layers (plus counties context)
        if (scoreInputs.warnings && map.getSource('warnings'))
            map.getSource('warnings').setData(polyFilter(warnings));
        if (scoreInputs.fireperimeters && map.getSource('fire_perimeters'))
            map.getSource('fire_perimeters').setData(polyFilter(perimeters));
        if (scoreInputs.hurricanes && map.getSource('storms'))
            map.getSource('storms').setData(polyFilter(stormsData));
        if (map.getSource('counties'))
            map.getSource('counties').setData(polyFilter(countiesData));

        if (scoreInputs.earthquakes && map.getSource('earthquakes'))
            map.getSource('earthquakes').setData(ptFilter(earthquakes));
        if (scoreInputs.firedetections && map.getSource('fires'))
            map.getSource('fires').setData(ptFilter(fires));
        if (scoreInputs.stormreports && map.getSource('lightning'))
            map.getSource('lightning').setData(ptFilter(lightning));
        if (scoreInputs.rivergauges && map.getSource('river_gauges'))
            map.getSource('river_gauges').setData(ptFilter(riverData));

        // Show results
        document.getElementById('clear-search').style.display = 'block';
        const locationLabel = placeName.split(',').slice(0,2).join(',');

        // Wait for NRI data
        const nriData = await nriPromise;
        const nriHtml = buildNRIPanel(nriData, locationLabel.split(',')[0]);

        const threatLevel = getThreatLevel(totalScore).label;

        if (threats.length === 0) {{
            showResults([
                {{ type: 'score', score: 0 }},
                {{ type: 'safe', text: `✅ No active threats detected within ${{radiusMiles}} miles of ${{locationLabel}}` }},
                {{ type: 'nri', html: nriHtml }}
            ]);
        }} else {{
            showResults([
                {{ type: 'header', text: `📍 ${{locationLabel}} · ${{radiusMiles}}mi radius` }},
                {{ type: 'score', score: totalScore }},
                ...threats,
                {{ type: 'nri', html: nriHtml }}
            ]);
        }}

        // Auto-generate county briefing using computed score + structured threats
        generateInlineBriefing(totalScore, threatLevel, threatObjs, locationLabel, scoreInputs);

    }} catch(err) {{
        console.error('Search error:', err);
        showResults([{{type:'error', text:'Error: ' + (err.message || 'Search failed. Check console for details.')}}]);
    }} finally {{
        btn.textContent = '🔍 ANALYZE THREATS';
        btn.disabled = false;
    }}
}}

// ── THREAT SCORING ENGINE ────────────────────────
const THREAT_WEIGHTS = {{
    tornado_warning:    40,
    hurricane_warning:  35,
    fire_perimeter:     35,
    severe_tstorm:      20,
    flash_flood:        18,
    wildfire_near:      15,
    earthquake_m5:      25,
    earthquake_m4:      12,
    earthquake_m3:       5,
    storm_report:        8,
    flood_warning:      15,
    winter_storm:       10,
    other_warning:       8,
    // Optional immediate add-ons
    flood_gauge_action:   6,
    flood_gauge_minor:   10,
    flood_gauge_moderate: 16,
    flood_gauge_major:   22,
    hurricane_cone:      20,
    hurricane_track:     12,
}};

function getThreatLevel(score) {{
    if (score >= 75) return {{ label: 'EXTREME',   color: '#FF0000', bg: 'rgba(255,0,0,0.15)',    emoji: '🚨' }};
    if (score >= 55) return {{ label: 'SEVERE',    color: '#FF4400', bg: 'rgba(255,68,0,0.12)',   emoji: '🔴' }};
    if (score >= 35) return {{ label: 'HIGH',      color: '#FF8800', bg: 'rgba(255,136,0,0.12)',  emoji: '🟠' }};
    if (score >= 15) return {{ label: 'ELEVATED',  color: '#FFCC00', bg: 'rgba(255,204,0,0.12)', emoji: '🟡' }};
    return                  {{ label: 'LOW',       color: '#00FF88', bg: 'rgba(0,255,136,0.08)', emoji: '🟢' }};
}}

function distanceDecay(distMiles, radiusMiles) {{
    // Closer threats weighted more heavily
    // 0 miles = 1.5x, radius miles = 0.5x
    return 1.5 - (distMiles / radiusMiles);
}}

function getProximityLabel(distMiles) {{
    if (distMiles < 10)  return {{ label: 'IMMEDIATE', color: '#FF0000' }};
    if (distMiles < 30)  return {{ label: 'NEAR',      color: '#FF8800' }};
    if (distMiles < 60)  return {{ label: 'MODERATE',  color: '#FFCC00' }};
    return                      {{ label: 'DISTANT',   color: '#888888' }};
}}

function showResults(threats) {{
    collapseLayerPanelForSearch();
    const div = document.getElementById('threat-results');
    div.style.display = 'block';
    div.innerHTML = threats.map(t => {{
        if (t.type === 'safe') {{
            return `<div class="threat-item threat-none">${{t.text}}</div>`;
        }}
        if (t.type === 'header') {{
            return `<div style="font-size:10px;color:rgba(255,255,255,0.5);margin-bottom:6px;letter-spacing:1px">${{t.text}}</div>`;
        }}
        if (t.type === 'score') {{
            const level = getThreatLevel(t.score);
            const pct = Math.min(100, t.score);
            return `
                <div style="background:${{level.bg}};border:1px solid ${{level.color}}40;
                    border-radius:8px;padding:10px;margin:6px 0;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                        <span style="font-size:11px;font-weight:700;color:${{level.color}};letter-spacing:2px;">
                            ${{level.emoji}} ${{level.label}} THREAT
                        </span>
                        <span style="font-size:20px;font-weight:700;color:${{level.color}}">${{Math.round(t.score)}}<span style="font-size:10px;color:rgba(255,255,255,0.4)">/100</span></span>
                    </div>
                    <div style="background:rgba(0,0,0,0.3);border-radius:4px;height:6px;overflow:hidden;">
                        <div style="height:100%;width:${{pct}}%;background:linear-gradient(90deg,${{level.color}}88,${{level.color}});
                            border-radius:4px;transition:width 0.8s ease;"></div>
                    </div>
                </div>`;
        }}
        if (t.type === 'nri') {{
            return t.html || '';
        }}
        if (t.type === 'error') {{
            return `<div class="threat-item" style="color:#FF6666;border-color:#FF6666;">${{t.text}}</div>`;
        }}
        // Threat item with distance and proximity label
        const prox = t.dist !== undefined ? getProximityLabel(t.dist) : null;
        return `<div class="threat-item" style="color:${{t.color}};border-color:${{t.color}}40;
            background:${{t.color}}08;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <span>${{t.text}}</span>
                ${{prox ? `<span style="font-size:9px;color:${{prox.color}};font-weight:700;
                    letter-spacing:1px;margin-left:8px;flex-shrink:0;">${{prox.label}}</span>` : ''}}
            </div>
            <div style="display:flex;justify-content:space-between;gap:10px;margin-top:2px;">
                ${{t.dist !== undefined ? `<div style="font-size:9px;color:rgba(255,255,255,0.3);">
                    ${{Math.round(t.dist)}} miles away</div>` : `<div></div>`}}
                <div style="font-size:9px;color:rgba(255,255,255,0.35);white-space:nowrap;">
                    ${{t.source ? t.source : ''}}${{t.points !== undefined ? ` · +${{t.points}} pts` : ''}}
                </div>
            </div>
        </div>`;
    }}).join('');

    // ── ACTION FOOTER: inline briefing placeholder + email alert signup ──
    div.innerHTML += `
    <div id="inline-briefing"></div>
    <div style="margin-top:10px;border-top:1px solid rgba(88,191,255,0.1);padding-top:10px;">
        <div style="font-size:9px;color:#4a6280;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;">Alert me when threats change</div>
        <div style="display:flex;gap:6px;">
            <input id="alert-email" type="email" placeholder="your@email.gov"
                style="flex:1;background:rgba(0,0,0,0.3);border:1px solid rgba(88,191,255,0.2);
                color:#dde9fb;font-size:10px;padding:8px 10px;outline:none;
                font-family:'Inter',sans-serif;border-radius:4px;" />
            <button onclick="subscribeAlerts()"
                style="background:rgba(88,191,255,0.1);border:1px solid rgba(88,191,255,0.3);
                color:#58bfff;font-size:9px;font-weight:700;letter-spacing:1px;padding:8px 12px;
                cursor:pointer;font-family:'Inter',sans-serif;white-space:nowrap;border-radius:4px;">
                🔔 ALERT ME</button>
        </div>
        <div id="subscribe-status" style="font-size:9px;color:#4a6280;margin-top:5px;text-align:center;min-height:14px;"></div>
    </div>`;
}}

// ── SIDEBAR PRESETS ───────────────────────────────────────
// All layers that presets can show/hide
const _ALL_PRESET_LAYERS = [
    'warnings-fill','warnings-outline',
    'spc-fill','spc-outline',
    'eq-circles',
    'fire-points','fire-perimeter-fill','fire-perimeter-outline',
    'lightning-strikes',
    'storm-cone','storm-cone-outline','storm-track',
    'nexrad-layer','river-gauges','volcano-circles',
    'counties-fill','counties-outline'
];
function _setPresetLayers(show) {{
    _ALL_PRESET_LAYERS.forEach(id => {{
        if (map.getLayer(id)) {{
            map.setLayoutProperty(id, 'visibility',
                show.includes(id) ? 'visible' : 'none');
        }}
    }});
}}
// ATMOS: NWS warnings + SPC outlook + hurricanes + lightning
function presetAtmos() {{
    _setPresetLayers([
        'warnings-fill','warnings-outline',
        'spc-fill','spc-outline',
        'storm-cone','storm-cone-outline','storm-track',
        'lightning-strikes'
    ]);
}}
// SEISMIC: earthquakes only
function presetSeismic() {{
    _setPresetLayers(['eq-circles']);
}}
// THERMAL: fire detections + fire perimeters
function presetThermal() {{
    _setPresetLayers(['fire-points','fire-perimeter-fill','fire-perimeter-outline']);
}}

// ── SEARCH CONTEXT (set after each successful search) ─────
let _searchContext = null;  // {{lat, lng, label, radius}}

// ── HERO PANEL ────────────────────────────────────────────
function heroSearch() {{
    const val = (document.getElementById('hero-input')?.value || '').trim();
    if (!val) return;
    document.getElementById('address-input').value = val;
    dismissHero();
    searchLocation();
}}
function heroQuick(val) {{
    document.getElementById('hero-input').value = val;
    heroSearch();
}}
function dismissHero() {{
    const h = document.getElementById('county-hero');
    if (!h) return;
    h.style.opacity = '0';
    h.style.pointerEvents = 'none';
    setTimeout(() => h.remove(), 380);
}}
// Enter key on hero input
document.addEventListener('DOMContentLoaded', () => {{
    const hi = document.getElementById('hero-input');
    if (hi) hi.addEventListener('keypress', e => {{ if (e.key === 'Enter') heroSearch(); }});
}});

// ── COUNTY BRIEFING + EMAIL ALERTS ───────────────────────
function openCountySitrep() {{
    const btn = document.getElementById('county-sitrep-btn');
    if (btn) {{ btn.textContent = '⏳ GENERATING...'; btn.disabled = true; }}
    const params = _searchContext
        ? '?lat=' + _searchContext.lat + '&lng=' + _searchContext.lng
          + '&radius=' + _searchContext.radius
          + '&county=' + encodeURIComponent(_searchContext.label)
        : '';
    // Reuse the existing sitrep modal
    document.getElementById('sitrep-overlay').classList.add('open');
    const setEl = (id, html) => {{ const el = document.getElementById(id); if (el) el.innerHTML = html; }};
    setEl('sitrep-level', '—');
    document.getElementById('sitrep-level').style.color = '#FF8C00';
    setEl('sitrep-status-badge', 'GENERATING LOCAL BRIEFING...');
    setEl('sitrep-summary', 'Analyzing local hazards...');
    setEl('sitrep-threats', '<p style="color:#a0acbd;font-size:13px;">Filtering threats to your area...</p>');
    setEl('sitrep-actions', '<p style="color:#a0acbd;font-size:11px;">Processing...</p>');
    setEl('sitrep-confidence', '<p>> COUNTY_SCOPE_ACTIVE</p>');
    _sitrepRaw = '';
    fetch('/api/sitrep' + params)
        .then(r => r.json())
        .then(data => {{
            _sitrepRaw = data.raw || data.text || '';
            parseSitrep(_sitrepRaw);
            if (btn) {{ btn.textContent = '📋 GENERATE BRIEFING'; btn.disabled = false; }}
        }})
        .catch(() => {{
            setEl('sitrep-summary', 'Failed to generate briefing.');
            if (btn) {{ btn.textContent = '📋 GENERATE BRIEFING'; btn.disabled = false; }}
        }});
}}
async function generateInlineBriefing(score, threatLevel, threatObjs, county, scoreInputs) {{
    const el = document.getElementById('inline-briefing');
    if (!el) return;
    el.innerHTML = '<div style="color:#4a6280;font-size:10px;letter-spacing:1px;padding:4px 0;">⏳ Generating briefing...</div>';
    try {{
        const r = await fetch('/api/sitrep', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                score,
                threat_level: threatLevel,
                threats: threatObjs || [],
                county,
                score_inputs: scoreInputs || null
            }})
        }});
        const d = await r.json();
        const text = (d.raw || d.text || '').trim();
        if (!text) {{ el.innerHTML = ''; return; }}

        // Parse SITUATION and ACTIONS sections
        const sitMatch = text.match(/SITUATION:\s*([\s\S]*?)(?=ACTIONS:|$)/i);
        const actMatch = text.match(/ACTIONS:\s*([\s\S]*?)$/i);
        const sit = sitMatch ? sitMatch[1].trim() : text;
        const act = actMatch ? actMatch[1].trim() : '';

        el.innerHTML = `
            <div style="border-top:1px solid rgba(88,191,255,0.15);padding-top:10px;margin-top:4px;">
                <div style="font-size:9px;color:#58bfff;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;">
                    📋 LOCAL BRIEFING
                </div>
                <div style="font-size:11px;color:#c8d8eb;line-height:1.6;margin-bottom:8px;">${{sit}}</div>
                ${{act ? `<div style="font-size:9px;color:#a0acbd;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;">ACTIONS</div>
                <div style="font-size:11px;color:#c8d8eb;line-height:1.6;">${{act}}</div>` : ''}}
            </div>`;
    }} catch(e) {{
        el.innerHTML = '';
    }}
}}

async function subscribeAlerts() {{
    const email  = (document.getElementById('alert-email')?.value || '').trim();
    const status = document.getElementById('subscribe-status');
    if (!email || !email.includes('@')) {{
        if (status) {{ status.textContent = 'Please enter a valid email.'; status.style.color = '#ff716c'; }}
        return;
    }}
    if (!_searchContext) {{
        if (status) {{ status.textContent = 'Search a county first.'; status.style.color = '#ff716c'; }}
        return;
    }}
    try {{
        const r = await fetch('/api/subscribe', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                email,
                county: _searchContext.label,
                lat: _searchContext.lat,
                lng: _searchContext.lng,
                radius: _searchContext.radius
            }})
        }});
        const d = await r.json();
        if (status) {{
            status.textContent = d.message || (d.ok ? 'Subscribed!' : (d.error || 'Error.'));
            status.style.color = d.ok ? '#00e676' : '#ff716c';
        }}
    }} catch(e) {{
        if (status) {{ status.textContent = 'Failed. Try again.'; status.style.color = '#ff716c'; }}
    }}
}}

// ── LOCATE ME ─────────────────────────────────────────────
let _userMarker = null;
let _gpsOverride = null;  // set by locateMe() so searchLocation() can skip geocoding
function _showLocNote(msg, isError) {{
    document.querySelectorAll('.loc-note').forEach(n => n.remove());
    const note = document.createElement('div');
    note.className = 'loc-note';
    note.style.cssText = `position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
        z-index:200;padding:12px 20px;font-size:11px;max-width:460px;text-align:center;
        font-family:Inter,sans-serif;line-height:1.6;
        background:${{isError ? 'rgba(255,60,60,0.12)' : 'rgba(88,191,255,0.12)'}};
        border:1px solid ${{isError ? 'rgba(255,80,80,0.4)' : 'rgba(88,191,255,0.3)'}};
        color:${{isError ? '#ffc0c0' : '#a8d8ff'}};`;
    note.innerHTML = msg;
    document.body.appendChild(note);
    setTimeout(() => note.remove(), 8000);
}}

function locateMe() {{
    console.log('[locateMe] called, geolocation:', !!navigator.geolocation);
    if (!navigator.geolocation) {{
        _showLocNote('Geolocation not supported by this browser.', true);
        return;
    }}
    document.getElementById('location-prompt')?.remove();
    _showLocNote('Requesting your location...', false);
    document.getElementById('address-input').value = 'Requesting location...';

    navigator.geolocation.getCurrentPosition(
        (pos) => {{
            console.log('[locateMe] GPS success:', pos.coords.latitude, pos.coords.longitude);
            _showLocNote('Got GPS — loading place name...', false);
            const lat = pos.coords.latitude;
            const lng = pos.coords.longitude;
            document.getElementById('address-input').value = lat.toFixed(4) + ', ' + lng.toFixed(4);

            // Place blue marker immediately
            map.flyTo({{ center: [lng, lat], zoom: 8, duration: 1800 }});
            if (_userMarker) _userMarker.remove();
            const el = document.createElement('div');
            el.style.cssText = 'width:16px;height:16px;border-radius:50%;background:#58bfff;border:3px solid #fff;box-shadow:0 0 0 4px rgba(88,191,255,0.3),0 0 16px rgba(88,191,255,0.6);';
            _userMarker = new mapboxgl.Marker({{ element: el }}).setLngLat([lng, lat]).addTo(map);

            // Reverse-geocode then run threat analysis
            fetch('https://api.mapbox.com/geocoding/v5/mapbox.places/'
                + lng + ',' + lat
                + '.json?types=place,district,region&limit=1&access_token=' + MAPBOX_TOKEN_JS)
            .then(r => r.json())
            .then(rgd => {{
                const feature = rgd.features?.[0] || null;
                _gpsOverride = {{ lat, lng, feature }};
                const label = feature
                    ? feature.place_name.split(',').slice(0,2).join(',')
                    : lat.toFixed(4) + ', ' + lng.toFixed(4);
                document.getElementById('address-input').value = label;
                _showLocNote('📍 ' + label + ' — analyzing threats...', false);
                searchLocation();
            }})
            .catch(() => {{
                // Reverse geocode failed — run analysis with raw coords
                _gpsOverride = {{ lat, lng, feature: null }};
                document.getElementById('address-input').value = lat.toFixed(4) + ', ' + lng.toFixed(4);
                _showLocNote('📍 Location found — analyzing nearby threats...', false);
                searchLocation();
            }});
        }},
        (err) => {{
            console.log('[locateMe] GPS error code:', err.code, err.message);
            document.getElementById('address-input').value = 'Error code: ' + err.code;
            const msgs = {{
                1: 'Location blocked (code 1). Chrome site settings show Allow but macOS may still block it — check System Preferences → Privacy & Security → Location Services → enable Chrome.',
                2: 'Could not determine your location (code 2). Try searching an address manually.',
                3: 'Location request timed out (code 3). Try again.'
            }};
            _showLocNote(msgs[err.code] || 'Location unavailable (code ' + err.code + ').', true);
        }},
        {{ timeout: 15000, enableHighAccuracy: false }}
    );
}}

// ── SEVERITY BAR ──────────────────────────────────────────
function updateSeverityBar(s) {{
    const raw = (s.warnings_count || 0) * 0.18
              + (s.earthquakes    || 0) * 0.05
              + (s.active_storms  || 0) * 4
              + (s.spc_zones      || 0) * 0.03
              + (s.river_gauges   || 0) * 0.12;
    const pct  = Math.min(100, Math.round(raw));
    const fill  = document.getElementById('severity-fill');
    const label = document.getElementById('severity-label');
    if (!fill || !label) return;
    let color, text;
    if      (pct >= 70) {{ color = '#FF2D2D'; text = 'CRITICAL'; }}
    else if (pct >= 45) {{ color = '#FF8C00'; text = 'ELEVATED'; }}
    else if (pct >= 20) {{ color = '#FFCC00'; text = 'ADVISORY'; }}
    else                {{ color = '#00CC66'; text = 'NORMAL';   }}
    fill.style.width = pct + '%';
    fill.style.backgroundColor = color;
    fill.classList.toggle('sev-critical', pct >= 70);
    label.textContent = text + ' · ' + pct + '%';
    label.style.color = pct >= 45 ? color : 'rgba(255,255,255,0.35)';
}}

// ── LIGHT / DARK THEME TOGGLE ─────────────────────────────
function toggleTheme() {{
    const isLight = document.body.classList.toggle('light');
    localStorage.setItem('nhm-theme', isLight ? 'light' : 'dark');
    const icon = document.getElementById('theme-btn')?.querySelector('.material-symbols-outlined');
    if (icon) icon.textContent = isLight ? 'light_mode' : 'dark_mode';
}}
// Apply saved theme on load
(function() {{
    if (localStorage.getItem('nhm-theme') === 'light') {{
        document.body.classList.add('light');
        const icon = document.getElementById('theme-btn')?.querySelector('.material-symbols-outlined');
        if (icon) icon.textContent = 'light_mode';
    }}
}})();

// ── KEYBOARD SHORTCUTS MODAL ──────────────────────────────
function openShortcuts()  {{ document.getElementById('shortcuts-modal').classList.add('open'); }}
function closeShortcuts() {{ document.getElementById('shortcuts-modal').classList.remove('open'); }}

// ── KEYBOARD SHORTCUTS ────────────────────────────────────
document.addEventListener('keydown', function(e) {{
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const key = e.key;
    if      (key === 'l' || key === 'L') {{ toggleLayerPanel(); }}
    else if (key === 's' || key === 'S') {{ if (!document.getElementById('sitrep-overlay').classList.contains('open')) openSitrep(); }}
    else if (key === 'r' || key === 'R') {{ loadData(); }}
    else if (key === 'f' || key === 'F') {{ document.documentElement.requestFullscreen?.(); }}
    else if (key === 'd' || key === 'D') {{ toggleTheme(); }}
    else if (key === 'w' || key === 'W') {{ focusThreatPanel(); }}
    else if (key === 'n' || key === 'N') {{ locateMe(); }}
    else if (key === '?')                {{ openShortcuts(); }}
    else if (key === 'Escape')           {{ closeSitrep(); closeShortcuts(); }}
}});

function clearSearch(resetInput=true, restoreData=true) {{
    restoreLayerPanelAfterSearch();
    if (searchMarker) {{ searchMarker.remove(); searchMarker = null; }}
    if (map.getLayer('buffer-fill'))    map.removeLayer('buffer-fill');
    if (map.getLayer('buffer-outline')) map.removeLayer('buffer-outline');
    if (map.getSource('search-buffer')) map.removeSource('search-buffer');
    document.getElementById('threat-results').style.display = 'none';
    document.getElementById('threat-results').innerHTML = '';
    document.getElementById('clear-search').style.display = 'none';
    if (resetInput) document.getElementById('address-input').value = '';
    if (restoreData) {{
        // Allow loadData() to refresh sources again, then trigger a refresh
        // so all sources return to showing global data.
        _searchContext = null;
        loadData();
    }}
}}

</script>
</body>
</html>"""
    response = flask_module.make_response(html)
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
