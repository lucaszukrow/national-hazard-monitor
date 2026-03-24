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
import requests
import folium
from folium.plugins import MiniMap, Fullscreen
import dash
from dash import dcc, html, Input, Output
import plotly.graph_objects as go

# ─────────────────────────────────────────────
# DATA SOURCE URLs
# ─────────────────────────────────────────────
NWS_URL      = "https://mapservices.weather.noaa.gov/eventdriven/rest/services/WWA/watch_warn_adv/MapServer/0/query?where=1%3D1&outFields=*&f=geojson"
SPC_URL      = "https://www.spc.noaa.gov/products/outlook/day1otlk_cat.nolyr.geojson"
USGS_EQ_URL  = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
NHC_URL      = "https://www.nhc.noaa.gov/CurrentStorms.json"
FIRMS_KEY    = "9979055d64039403128c5f82c0997133"
FIRMS_URL    = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/VIIRS_SNPP_NRT/-125,24,-66,50/1"
CENSUS_URL   = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/counties/totals/co-est2023-alldata.csv"
COUNTIES_URL = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"

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
    "updating": False
}

# Load cache at module level — runs when gunicorn imports app
# This ensures data is available immediately on startup
_startup_cache = load_cache()
if _startup_cache:
    state.update(_startup_cache)
    print(f"Startup: loaded cache from {_startup_cache.get('last_update', 'unknown')}")

# Start background update thread at module level
# Runs when gunicorn imports the app — not just when run directly
_update_thread = threading.Thread(target=lambda: schedule_updates(30), daemon=True)
_update_thread.start()

# ─────────────────────────────────────────────
# DATA DOWNLOAD FUNCTIONS
# ─────────────────────────────────────────────
def fetch_json(url, timeout=20):
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  WARNING: Failed to fetch {url}: {e}")
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
    try:
        r = requests.get(FIRMS_URL, timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        if len(lines) < 2:
            return []
        headers = [h.strip() for h in lines[0].split(",")]
        fires = []
        for line in lines[1:]:
            if not line.strip():
                continue
            vals = [v.strip() for v in line.split(",")]
            if len(vals) >= len(headers):
                fires.append(dict(zip(headers, vals)))
        print(f"  FIRMS: {len(fires)} fire detections")
        return fires
    except Exception as e:
        print(f"  WARNING: FIRMS failed: {e}")
        return []

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

def find_affected_counties(warnings_geojson, pop_data, counties_geojson):
    """Find counties intersecting warning polygons using bounding box check."""
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
            props = feat.get("properties", {})
            geom  = feat.get("geometry", {})
            coords = geom.get("coordinates", [])
            if not coords:
                continue

            # Get county centroid approx
            all_pts = []
            def flatten(c):
                if isinstance(c[0], (int, float)):
                    all_pts.append(c)
                else:
                    for item in c:
                        flatten(item)
            flatten(coords)
            if not all_pts:
                continue

            clon = sum(p[0] for p in all_pts) / len(all_pts)
            clat = sum(p[1] for p in all_pts) / len(all_pts)

            for bounds in warning_bounds:
                if point_in_bbox(clon, clat, bounds):
                    state_code  = fips[:2]
                    county_code = fips[2:]
                    pop = pop_data.get(fips, 0)
                    total_pop += pop
                    seen_fips.add(fips)
                    w_props = bounds["props"]
                    phenom  = w_props.get("phenom", "")
                    sig     = w_props.get("sig", "")
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
# BACKGROUND UPDATE THREAD
# Runs every 30 minutes automatically
# ─────────────────────────────────────────────
def run_update():
    """Download all data and rebuild map. Runs in background thread."""
    global state
    if state["updating"]:
        return
    state["updating"] = True
    print(f"\n{'='*50}")
    print(f"  UPDATE: {datetime.datetime.now()}")
    print(f"{'='*50}")

    try:
        warnings   = fetch_warnings()
        spc        = fetch_spc()
        earthquakes = fetch_earthquakes()
        storms     = fetch_storms()
        fires      = fetch_fires()

        # Load population once
        if not state["pop_data"]:
            state["pop_data"] = fetch_population()

        # Load counties GeoJSON once
        if not state["counties_geojson"]:
            print("Loading county boundaries...")
            try:
                r = requests.get(COUNTIES_URL, timeout=30)
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

        # Update global state
        state.update({
            "last_update":  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "warnings":     warnings,
            "spc":          spc,
            "earthquakes":  earthquakes,
            "storms":       storms,
            "fires":        fires,
            "map_html":     map_html,
            "summary": {
                "warnings_count":   len(warnings.get("features", [])),
                "counties_count":   len(affected),
                "total_population": total_pop,
                "spc_zones":        len(spc.get("features", [])),
                "earthquakes":      len(earthquakes.get("features", [])),
                "active_storms":    len(storms),
                "wildfires":        len(fires),
                "affected_counties": affected
            }
        })
        print(f"  Update complete — {len(affected)} counties affected")
        # Save to cache file so data survives restarts
        save_cache({
            "last_update": state["last_update"],
            "warnings":    state["warnings"],
            "spc":         state["spc"],
            "earthquakes": state["earthquakes"],
            "fires":       state["fires"],
            "summary":     state["summary"],
            "map_html":    state["map_html"]
        })
        print("  Cache saved")

    except Exception as e:
        print(f"  ERROR during update: {e}")
    finally:
        state["updating"] = False

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
app = dash.Dash(__name__, title="National Hazard Monitor", update_title=None)
server = app.server  # Expose Flask server for Render

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

@app.server.route("/mapbox")
def mapbox_map():
    """Serves the full Mapbox GL JS map page."""
    MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN", "")
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>National Hazard Monitor</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.js"></script>
    <link href="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.css" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #0a0a0a; font-family: Arial, sans-serif; color: white; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}

        #header {{
            position: absolute; top: 16px; left: 50%; transform: translateX(-50%);
            z-index: 10; background: rgba(10,10,10,0.92);
            border: 1px solid #1B4F72; border-radius: 10px;
            padding: 10px 24px; text-align: center;
            backdrop-filter: blur(10px);
        }}
        #header h1 {{ font-size: 18px; color: #AAD4FF; margin: 0; }}
        #header p  {{ font-size: 11px; color: #666; margin: 4px 0 0 0; }}

        #stats {{
            position: absolute; top: 16px; left: 16px; z-index: 10;
            display: flex; flex-direction: column; gap: 6px;
        }}
        .stat-card {{
            background: rgba(10,10,10,0.92); border-radius: 8px;
            padding: 8px 14px; border: 1px solid #333;
            backdrop-filter: blur(10px); min-width: 160px;
        }}
        .stat-value {{ font-size: 22px; font-weight: bold; line-height: 1; }}
        .stat-label {{ font-size: 10px; color: #888; margin-top: 2px; }}

        #legend {{
            position: absolute; bottom: 40px; left: 16px; z-index: 10;
            background: rgba(10,10,10,0.92); border-radius: 8px;
            padding: 12px 16px; border: 1px solid #333;
            backdrop-filter: blur(10px); font-size: 11px;
            display: flex; gap: 20px;
        }}
        .legend-section h4 {{ color: #AAD4FF; margin: 0 0 8px 0; font-size: 12px; }}
        .legend-item {{ display: flex; align-items: center; gap: 6px; margin: 3px 0; }}
        .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
        .legend-box {{ width: 14px; height: 10px; border-radius: 2px; flex-shrink: 0; }}

        #popup {{
            position: absolute; z-index: 20;
            background: rgba(10,10,10,0.95); border: 1px solid #444;
            border-radius: 8px; padding: 12px 16px;
            font-size: 12px; max-width: 240px;
            backdrop-filter: blur(10px); display: none;
        }}
        #popup h3 {{ color: #FF6666; margin: 0 0 8px 0; font-size: 14px; }}
        #popup .row {{ display: flex; justify-content: space-between; margin: 3px 0; }}
        #popup .key {{ color: #888; }}
        #popup .val {{ color: white; font-weight: bold; }}
        #close-popup {{
            position: absolute; top: 6px; right: 10px;
            cursor: pointer; color: #888; font-size: 16px;
        }}

        .mapboxgl-ctrl-group {{ background: rgba(10,10,10,0.92) !important; }}
        .mapboxgl-ctrl-group button {{ background: transparent !important; }}
        .mapboxgl-ctrl-group button span {{ filter: invert(1); }}
    </style>
</head>
<body>

<div id="map"></div>

<div id="header">
    <h1>&#127774; National All-Hazards Monitor</h1>
    <p id="update-time">Loading live data...</p>
</div>

<div id="stats">
    <div class="stat-card">
        <div class="stat-value" id="stat-warnings" style="color:#FF6666">-</div>
        <div class="stat-label">Active Warnings</div>
    </div>
    <div class="stat-card">
        <div class="stat-value" id="stat-eq" style="color:#AAD4FF">-</div>
        <div class="stat-label">Earthquakes M2.5+</div>
    </div>
    <div class="stat-card">
        <div class="stat-value" id="stat-fires" style="color:#FF4500">-</div>
        <div class="stat-label">Fire Detections</div>
    </div>
    <div class="stat-card">
        <div class="stat-value" id="stat-spc" style="color:#76FF7A">-</div>
        <div class="stat-label">SPC Outlook Zones</div>
    </div>
</div>

<div id="legend">
    <div class="legend-section">
        <h4>&#9889; NWS Warnings</h4>
        <div class="legend-item"><div class="legend-box" style="background:#FF0000"></div> Tornado</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF6600"></div> Hurricane</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF6666"></div> Severe T-Storm</div>
        <div class="legend-item"><div class="legend-box" style="background:#00BFFF"></div> Flash Flood</div>
        <div class="legend-item"><div class="legend-box" style="background:#FFFF00"></div> Other</div>
    </div>
    <div class="legend-section">
        <h4>&#9928; SPC Outlook</h4>
        <div class="legend-item"><div class="legend-box" style="background:#76FF7A"></div> General Thunder</div>
        <div class="legend-item"><div class="legend-box" style="background:#FFFF00"></div> Slight Risk</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF9900"></div> Enhanced Risk</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF0000"></div> Moderate Risk</div>
    </div>
    <div class="legend-section">
        <h4>&#128308; Earthquakes</h4>
        <div class="legend-item"><div class="legend-dot" style="background:#FFFF00"></div> M2.5 - 3.9</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FF9900"></div> M4.0 - 4.9</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FF0000"></div> M5.0+</div>
        <h4 style="margin-top:8px">&#128293; Wildfires</h4>
        <div class="legend-item"><div class="legend-dot" style="background:#FF4500"></div> NASA FIRMS</div>
    </div>
</div>

<div id="popup">
    <span id="close-popup" onclick="document.getElementById('popup').style.display='none'">&#x2715;</span>
    <h3 id="popup-title">Feature</h3>
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
    zoom: 3.5,
    projection: 'globe'
}});

map.addControl(new mapboxgl.NavigationControl(), 'top-right');
map.addControl(new mapboxgl.FullscreenControl(), 'top-right');
map.setFog({{
    color: 'rgb(10,10,10)',
    'high-color': 'rgb(20,30,50)',
    'horizon-blend': 0.02
}});

function showPopup(title, rows, e) {{
    const popup = document.getElementById('popup');
    document.getElementById('popup-title').textContent = title;
    let html = '';
    for (const [k, v] of Object.entries(rows)) {{
        html += `<div class="row"><span class="key">${{k}}</span><span class="val">${{v}}</span></div>`;
    }}
    document.getElementById('popup-content').innerHTML = html;
    popup.style.display = 'block';
    popup.style.left = (e.point.x + 10) + 'px';
    popup.style.top  = (e.point.y - 10) + 'px';
}}

map.on('load', () => {{

    // ── SPC OUTLOOK ─────────────────────────────────
    map.addSource('spc', {{ type: 'geojson', data: '/api/spc' }});
    map.addLayer({{
        id: 'spc-fill', type: 'fill', source: 'spc',
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

    // Warning pulse animation
    let opacity = 0.45;
    let direction = -1;
    setInterval(() => {{
        opacity += direction * 0.02;
        if (opacity < 0.25 || opacity > 0.55) direction *= -1;
        if (map.getLayer('warnings-fill')) {{
            map.setPaintProperty('warnings-fill', 'fill-opacity', opacity);
        }}
    }}, 80);

    // ── EARTHQUAKES ──────────────────────────────────
    map.addSource('earthquakes', {{ type: 'geojson', data: '/api/earthquakes' }});
    map.addLayer({{
        id: 'eq-circles', type: 'circle', source: 'earthquakes',
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

    // ── WILDFIRES ────────────────────────────────────
    map.addSource('fires', {{ type: 'geojson', data: '/api/fires',
        cluster: true, clusterMaxZoom: 7, clusterRadius: 40
    }});
    map.addLayer({{
        id: 'fire-clusters', type: 'circle', source: 'fires',
        filter: ['has', 'point_count'],
        paint: {{
            'circle-color': [
                'step', ['get', 'point_count'],
                '#FF8C00', 25, '#FF4500', 100, '#FF0000'
            ],
            'circle-radius': [
                'step', ['get', 'point_count'],
                16, 25, 22, 100, 30
            ],
            'circle-stroke-color': '#FFD700',
            'circle-stroke-width': 2
        }}
    }});
    map.addLayer({{
        id: 'fire-cluster-count', type: 'symbol', source: 'fires',
        filter: ['has', 'point_count'],
        layout: {{
            'text-field': ['get', 'point_count_abbreviated'],
            'text-size': 11, 'text-font': ['DIN Offc Pro Bold', 'Arial Unicode MS Bold']
        }},
        paint: {{ 'text-color': '#fff' }}
    }});
    map.addLayer({{
        id: 'fire-points', type: 'circle', source: 'fires',
        filter: ['!', ['has', 'point_count']],
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
        const sig = {{'W':'Warning','A':'Watch','Y':'Advisory','S':'Statement'}}[p.sig] || p.sig || '';
        const name = (PHENOM_NAMES[phenom] || phenom) + ' ' + sig;
        showPopup('⚠ ' + name, {{
            'Phenomenon': phenom,
            'Significance': sig,
            'WFO': p.wfo || 'N/A',
            'Product': p.prod_type || 'N/A'
        }}, e);
    }});
    map.on('click', 'eq-circles', (e) => {{
        const p = e.features[0].properties;
        showPopup('Earthquake M' + p.mag, {{
            'Location': p.place || 'Unknown',
            'Magnitude': p.mag,
            'Depth': (p.depth || 'N/A') + ' km'
        }}, e);
    }});
    map.on('click', 'fire-points', (e) => {{
        const p = e.features[0].properties;
        showPopup('Wildfire Detection', {{
            'Date': p.acq_date || 'N/A',
            'FRP': (p.frp || 'N/A') + ' MW',
            'Confidence': p.confidence || 'N/A'
        }}, e);
    }});
    map.on('click', 'fire-clusters', (e) => {{
        map.flyTo({{ center: e.lngLat, zoom: map.getZoom() + 2 }});
    }});

    // Cursor changes
    ['warnings-fill','eq-circles','fire-points','fire-clusters'].forEach(layer => {{
        map.on('mouseenter', layer, () => map.getCanvas().style.cursor = 'pointer');
        map.on('mouseleave', layer, () => map.getCanvas().style.cursor = '');
    }});

    // ── LOAD STATS ───────────────────────────────────
    fetch('/api/summary').then(r => r.json()).then(data => {{
        const s = data.summary || {{}};
        document.getElementById('stat-warnings').textContent = s.warnings_count || 0;
        document.getElementById('stat-eq').textContent       = s.earthquakes    || 0;
        document.getElementById('stat-fires').textContent    = s.wildfires      || 0;
        document.getElementById('stat-spc').textContent      = s.spc_zones      || 0;
        document.getElementById('update-time').textContent   = 'Last updated: ' + (data.last_update || 'Loading...');
    }});

    // Auto-refresh stats every 5 min
    setInterval(() => {{
        fetch('/api/summary').then(r => r.json()).then(data => {{
            const s = data.summary || {{}};
            document.getElementById('stat-warnings').textContent = s.warnings_count || 0;
            document.getElementById('stat-eq').textContent       = s.earthquakes    || 0;
            document.getElementById('stat-fires').textContent    = s.wildfires      || 0;
            document.getElementById('stat-spc').textContent      = s.spc_zones      || 0;
            document.getElementById('update-time').textContent   = 'Last updated: ' + (data.last_update || '');
            // Refresh map sources
            ['warnings','spc','earthquakes','fires'].forEach(src => {{
                if (map.getSource(src)) map.getSource(src).setData('/api/' + src + '?t=' + Date.now());
            }});
        }});
    }}, 5 * 60 * 1000);
}});
</script>
</body>
</html>"""
    return html

app.layout = html.Div(
    style={"backgroundColor": "#0a0a0a", "minHeight": "100vh",
           "fontFamily": "Arial, sans-serif", "color": "white"},
    children=[
        dcc.Interval(id="refresh", interval=5*60*1000, n_intervals=0),

        # Header
        html.Div(style={
            "backgroundColor": "#111", "borderBottom": "2px solid #1B4F72",
            "padding": "16px 24px", "display": "flex",
            "justifyContent": "space-between", "alignItems": "center"
        }, children=[
            html.Div([
                html.H1("🌪 National All-Hazards Monitor",
                    style={"margin": "0", "fontSize": "22px", "color": "#AAD4FF"}),
                html.P([
                    "Real-time hazard tracking | NWS · NHC · SPC · USGS · NASA · Census  ",
                    html.A("🗺 Open Mapbox Map →", href="/mapbox", target="_blank",
                           style={"color": "#AAD4FF", "fontSize": "11px", "textDecoration": "none"})
                ], style={"margin": "4px 0 0 0", "fontSize": "11px", "color": "#888"})
            ]),
            html.Div([
                html.P(id="last-updated", style={"margin": "0", "fontSize": "12px",
                       "color": "#aaa", "textAlign": "right"}),
                html.P("Auto-refreshes every 30 minutes",
                    style={"margin": "2px 0 0 0", "fontSize": "10px",
                           "color": "#666", "textAlign": "right"})
            ])
        ]),

        # Stat cards
        html.Div(id="stat-cards",
            style={"display": "flex", "gap": "10px",
                   "padding": "14px 24px", "flexWrap": "wrap"}),

        # Map + Charts
        html.Div(style={"display": "flex", "gap": "14px",
                        "padding": "0 24px 14px 24px"}, children=[
            # Map
            html.Div(style={"flex": "2", "minWidth": "0"}, children=[
                html.Div("Live Hazard Map", style={
                    "backgroundColor": "#1a1a1a", "padding": "8px 12px",
                    "borderRadius": "6px 6px 0 0", "fontSize": "13px",
                    "color": "#AAD4FF", "fontWeight": "bold",
                    "borderBottom": "1px solid #333"
                }),
                html.Div(id="map-container", style={
                    "width": "100%", "height": "480px",
                    "backgroundColor": "#111", "overflow": "hidden",
                    "borderRadius": "0 0 6px 6px"
                })
            ]),
            # Charts
            html.Div(style={"flex": "1", "minWidth": "260px",
                            "display": "flex", "flexDirection": "column",
                            "gap": "12px"}, children=[
                html.Div(style={"backgroundColor": "#1a1a1a",
                                "borderRadius": "6px", "overflow": "hidden"}, children=[
                    html.Div("Active Warnings by Type", style={
                        "padding": "8px 12px", "fontSize": "13px",
                        "color": "#AAD4FF", "fontWeight": "bold",
                        "borderBottom": "1px solid #333"
                    }),
                    dcc.Graph(id="bar-chart", style={"height": "200px"},
                              config={"displayModeBar": False})
                ]),
                html.Div(style={"backgroundColor": "#1a1a1a",
                                "borderRadius": "6px", "overflow": "hidden"}, children=[
                    html.Div("Alert Level Breakdown", style={
                        "padding": "8px 12px", "fontSize": "13px",
                        "color": "#AAD4FF", "fontWeight": "bold",
                        "borderBottom": "1px solid #333"
                    }),
                    dcc.Graph(id="donut-chart", style={"height": "200px"},
                              config={"displayModeBar": False})
                ])
            ])
        ]),

        # Counties table
        html.Div(style={"padding": "0 24px 24px 24px"}, children=[
            html.Div(style={"backgroundColor": "#1a1a1a", "borderRadius": "6px",
                            "overflow": "hidden"}, children=[
                html.Div("Affected Counties", style={
                    "padding": "8px 12px", "fontSize": "13px",
                    "color": "#AAD4FF", "fontWeight": "bold",
                    "borderBottom": "1px solid #333"
                }),
                html.Div(id="counties-table",
                    style={"padding": "12px", "maxHeight": "300px",
                           "overflowY": "auto"})
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
    Input("refresh", "n_intervals")
)
def update_ui(n):
    # Trigger update if data is stale or never loaded
    if state["last_update"] == "Never" and not state["updating"]:
        print("Dashboard triggered data update...")
        t = threading.Thread(target=run_update, daemon=True)
        t.start()
    s   = state["summary"]
    pop = s.get("total_population", 0)

    def card(value, label, color="#AAD4FF", bg="#1B2A3A"):
        return html.Div(style={
            "backgroundColor": bg, "borderRadius": "8px",
            "padding": "14px 18px", "minWidth": "130px", "flex": "1",
            "border": f"1px solid {color}22"
        }, children=[
            html.Div(str(value), style={"fontSize": "28px", "fontWeight": "bold",
                                         "color": color, "lineHeight": "1"}),
            html.Div(label, style={"fontSize": "11px", "color": "#888", "marginTop": "4px"})
        ])

    stat_cards = [
        card(s.get("warnings_count", 0),   "Active Warnings",      "#FF6666", "#2A1B1B"),
        card(s.get("counties_count", 0),   "Affected Counties",    "#FF9900", "#2A1E0A"),
        card(f"{pop:,}" if pop else "N/A", "Population at Risk",   "#FF6600", "#2A1500"),
        card(s.get("spc_zones", 0),        "SPC Outlook Zones",    "#76FF7A", "#0A1A0A"),
        card(s.get("earthquakes", 0),      "Earthquakes M2.5+",    "#AAD4FF", "#1B2A3A"),
        card(s.get("active_storms", 0),    "Active Hurricanes",    "#FF6600", "#2A1500"),
        card(s.get("wildfires", 0),        "Fire Detections",      "#FF4500", "#2A0A00"),
    ]

    # Map
    map_content = html.Iframe(
        srcDoc=state.get("map_html", "<p style='color:white;padding:20px;'>Loading map...</p>"),
        style={"width": "100%", "height": "480px", "border": "none"}
    ) if state.get("map_html") else html.P(
        "Map loading... Data update in progress.",
        style={"color": "#666", "padding": "20px", "textAlign": "center"}
    )

    # Bar chart
    bar_fig = go.Figure()
    affected = s.get("affected_counties", [])
    if affected:
        from collections import Counter
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
        paper_bgcolor="#1a1a1a", plot_bgcolor="#1a1a1a",
        font=dict(color="white", size=10),
        margin=dict(l=10, r=10, t=10, b=40),
        xaxis=dict(tickfont=dict(size=9), gridcolor="#333"),
        yaxis=dict(gridcolor="#333", showticklabels=False),
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
        paper_bgcolor="#1a1a1a", font=dict(color="white", size=10),
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(font=dict(color="white"), bgcolor="#1a1a1a"),
        showlegend=True
    )

    # Counties table
    if not affected:
        counties_html = html.P("No active warnings detected",
                               style={"color": "#666", "fontSize": "13px"})
    else:
        hstyle = {"padding": "6px 10px", "fontSize": "11px", "color": "#888",
                  "textAlign": "left", "borderBottom": "1px solid #333",
                  "backgroundColor": "#111"}
        cstyle = {"padding": "6px 10px", "fontSize": "12px",
                  "borderBottom": "1px solid #1a1a1a"}
        header = html.Tr([
            html.Th(h, style=hstyle)
            for h in ["County", "State", "Population", "Event", "Level"]
        ])
        rows = []
        for i, c in enumerate(affected):
            sig   = c.get("sig", "")
            color = {"Warning":"#FF6666","Watch":"#FF9900","Advisory":"#FFFF00"}.get(sig,"white")
            rows.append(html.Tr([
                html.Td(c.get("county",""),                style=cstyle),
                html.Td(c.get("state",""),                 style=cstyle),
                html.Td(f"{c.get('population',0):,}",      style=cstyle),
                html.Td(c.get("event",""),                 style=cstyle),
                html.Td(sig, style={**cstyle, "color": color, "fontWeight": "bold"})
            ], style={"backgroundColor": "#111" if i%2==0 else "#161616"}))
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
