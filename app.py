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
FIRMS_URL    = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/VIIRS_SNPP_NRT/-125,24,-66,50/2"
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
    "updating": False,
    "lightning": {"type": "FeatureCollection", "features": []},
    "fire_perimeters": {"type": "FeatureCollection", "features": []}
}

# Load cache at module level — runs when gunicorn imports app
# This ensures data is available immediately on startup
_startup_cache = load_cache()
if _startup_cache:
    state.update(_startup_cache)
    print(f"Startup: loaded cache from {_startup_cache.get('last_update', 'unknown')}")



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
        storms         = fetch_storms()
        fires          = fetch_fires()
        lightning      = fetch_lightning()
        fire_perimeters = fetch_fire_perimeters()

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
            "lightning":    lightning,
            "fire_perimeters": fire_perimeters,
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
            "last_update":     state["last_update"],
            "warnings":        state["warnings"],
            "spc":             state["spc"],
            "earthquakes":     state["earthquakes"],
            "fires":           state["fires"],
            "lightning":       state["lightning"],
            "fire_perimeters": state["fire_perimeters"],
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
    """Fetch active wildfire perimeters from public sources."""
    print("Downloading wildfire perimeters...")
    
    # Try multiple public endpoints
    urls = [
        # NIFC open data - public GeoJSON endpoint (no auth required)
        "https://opendata.arcgis.com/datasets/nifc::wfigs-current-interagency-fire-perimeters.geojson",
        # NIFC historical open data
        "https://opendata.arcgis.com/datasets/5da472c6d27b4b67970acc7b5044c862_0.geojson",
        # NASA FIRMS fire areas (alternative)
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/VIIRS_SNPP_NRT/-125,24,-66,50/7",
    ]
    
    for url in urls:
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 403:
                print(f"  Fire perimeters: 403 forbidden - {url[:60]}")
                continue
            r.raise_for_status()
            
            # Handle CSV response from FIRMS
            if 'csv' in url:
                lines = r.text.strip().split("\n")
                if len(lines) < 2:
                    continue
                headers = [h.strip() for h in lines[0].split(",")]
                features = []
                for line in lines[1:100]:  # Limit to 100 largest
                    if not line.strip():
                        continue
                    vals = [v.strip() for v in line.split(",")]
                    if len(vals) >= len(headers):
                        row = dict(zip(headers, vals))
                        try:
                            lat = float(row.get("latitude", 0))
                            lon = float(row.get("longitude", 0))
                            frp = float(row.get("frp", 0))
                            if lat and lon and frp > 50:  # Only high-intensity fires
                                features.append({
                                    "type": "Feature",
                                    "geometry": {
                                        "type": "Polygon",
                                        "coordinates": [[
                                            [lon-0.05, lat-0.05],
                                            [lon+0.05, lat-0.05],
                                            [lon+0.05, lat+0.05],
                                            [lon-0.05, lat+0.05],
                                            [lon-0.05, lat-0.05]
                                        ]]
                                    },
                                    "properties": {
                                        "IncidentName": f"Active Fire (FRP: {frp} MW)",
                                        "GISAcres": frp * 10,
                                        "PercentContained": 0,
                                        "ModifiedOnDateTime_dt": row.get("acq_date", "")
                                    }
                                })
                        except Exception:
                            continue
                if features:
                    print(f"  Fire perimeters (FIRMS high-intensity): {len(features)} fires")
                    return {"type": "FeatureCollection", "features": features}
                continue

            data = r.json()
            if "message" in data and "permission" in str(data.get("message","")).lower():
                print(f"  Fire perimeters: permission denied")
                continue
            features = data.get("features", [])
            if features:
                print(f"  Fire perimeters: {len(features)} active fires")
                return data
        except Exception as e:
            print(f"  Fire perimeters failed: {e}")
            continue
    
    print("  Fire perimeters: no data available")
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
app = dash.Dash(__name__, title="National Hazard Monitor", update_title=None)
server = app.server  # Expose Flask server for Render

# Use Flask's before_first_request to start background thread
# This runs once when the first request hits the server
_started = False

@server.before_request
def start_background_on_first_request():
    global _started
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
                data={"data": query}, timeout=15
            )
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

    state["infra_cache_key"] = cache_key
    state["infra_features"]  = features

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
    <script src="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/@turf/turf@6/turf.min.js"></script>
    <link href="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #000; font-family: 'Inter', Arial, sans-serif; color: white; overflow: hidden; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}

        /* ── SCAN LINE OVERLAY ── */
        body::after {{
            content: '';
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px);
            pointer-events: none; z-index: 5;
        }}

        /* ── HEADER ── */
        #header {{
            position: absolute; top: 20px; left: 50%; transform: translateX(-50%);
            z-index: 10;
            background: linear-gradient(135deg, rgba(0,8,20,0.95) 0%, rgba(0,20,40,0.95) 100%);
            border: 1px solid rgba(0,180,255,0.3);
            border-radius: 12px;
            padding: 12px 28px;
            text-align: center;
            backdrop-filter: blur(20px);
            box-shadow: 0 0 30px rgba(0,180,255,0.15), inset 0 1px 0 rgba(255,255,255,0.05);
        }}
        #header::before {{
            content: '';
            position: absolute; top: -1px; left: 20%; right: 20%; height: 1px;
            background: linear-gradient(90deg, transparent, rgba(0,180,255,0.8), transparent);
        }}
        #header h1 {{
            font-size: 17px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase;
            background: linear-gradient(135deg, #AAD4FF, #00B4FF);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            margin: 0;
        }}
        #header p {{ font-size: 10px; color: rgba(100,180,255,0.6); margin: 4px 0 0 0; letter-spacing: 1px; }}

        /* ── LIVE INDICATOR ── */
        #live-dot {{
            display: inline-block; width: 8px; height: 8px; background: #00FF88;
            border-radius: 50%; margin-right: 6px;
            box-shadow: 0 0 8px #00FF88;
            animation: pulse-dot 1.5s ease-in-out infinite;
        }}
        @keyframes pulse-dot {{
            0%, 100% {{ opacity: 1; box-shadow: 0 0 8px #00FF88; }}
            50% {{ opacity: 0.4; box-shadow: 0 0 2px #00FF88; }}
        }}

        /* ── STAT CARDS ── */
        #stats {{
            position: absolute; top: 20px; left: 16px; z-index: 10;
            display: flex; flex-direction: column; gap: 8px;
        }}
        .stat-card {{
            background: linear-gradient(135deg, rgba(0,8,20,0.92) 0%, rgba(0,15,35,0.92) 100%);
            border-radius: 10px; padding: 10px 16px;
            border: 1px solid rgba(255,255,255,0.07);
            backdrop-filter: blur(20px); min-width: 170px;
            position: relative; overflow: hidden;
            transition: border-color 0.3s, box-shadow 0.3s;
        }}
        .stat-card:hover {{
            border-color: rgba(0,180,255,0.4);
            box-shadow: 0 0 20px rgba(0,180,255,0.1);
        }}
        .stat-card::before {{
            content: ''; position: absolute;
            top: 0; left: 0; right: 0; height: 1px;
            background: linear-gradient(90deg, transparent, var(--accent, rgba(0,180,255,0.5)), transparent);
        }}
        .stat-card::after {{
            content: ''; position: absolute;
            top: 0; left: 0; bottom: 0; width: 2px;
            background: var(--accent, rgba(0,180,255,0.5));
            border-radius: 2px 0 0 2px;
        }}
        .stat-value {{
            font-size: 26px; font-weight: 700; line-height: 1;
            font-variant-numeric: tabular-nums;
        }}
        .stat-label {{ font-size: 9px; color: rgba(255,255,255,0.4); margin-top: 3px; letter-spacing: 1.5px; text-transform: uppercase; }}
        .stat-icon {{ position: absolute; right: 12px; top: 50%; transform: translateY(-50%); font-size: 18px; opacity: 0.2; }}

        /* ── LEGEND ── */
        #legend {{
            position: absolute; bottom: 30px; left: 16px; z-index: 10;
            background: linear-gradient(135deg, rgba(0,8,20,0.92) 0%, rgba(0,15,35,0.92) 100%);
            border-radius: 10px; padding: 14px 18px;
            border: 1px solid rgba(255,255,255,0.07);
            backdrop-filter: blur(20px); font-size: 11px;
            display: flex; gap: 20px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.4);
        }}
        .legend-title {{
            font-size: 9px; color: rgba(0,180,255,0.8); font-weight: 600;
            letter-spacing: 2px; text-transform: uppercase; margin-bottom: 8px;
            border-bottom: 1px solid rgba(0,180,255,0.2); padding-bottom: 4px;
        }}
        .legend-item {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; color: rgba(255,255,255,0.7); }}
        .legend-dot {{
            width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
            box-shadow: 0 0 6px currentColor;
        }}
        .legend-box {{
            width: 14px; height: 10px; border-radius: 2px; flex-shrink: 0;
            box-shadow: 0 0 6px currentColor;
        }}

        /* ── POPUP ── */
        #popup {{
            position: absolute; z-index: 20;
            background: linear-gradient(135deg, rgba(0,8,20,0.98) 0%, rgba(0,20,40,0.98) 100%);
            border: 1px solid rgba(0,180,255,0.3);
            border-radius: 12px; padding: 16px 18px;
            font-size: 12px; min-width: 220px; max-width: 280px;
            backdrop-filter: blur(20px); display: none;
            box-shadow: 0 0 40px rgba(0,0,0,0.6), 0 0 20px rgba(0,180,255,0.1);
            animation: popup-in 0.2s ease-out;
        }}
        @keyframes popup-in {{
            from {{ opacity: 0; transform: scale(0.95) translateY(4px); }}
            to {{ opacity: 1; transform: scale(1) translateY(0); }}
        }}
        #popup-header {{
            display: flex; justify-content: space-between; align-items: flex-start;
            margin-bottom: 12px; padding-bottom: 10px;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }}
        #popup-title {{
            font-size: 13px; font-weight: 700; color: white; margin: 0;
            letter-spacing: 0.5px;
        }}
        #close-popup {{
            cursor: pointer; color: rgba(255,255,255,0.3); font-size: 18px; line-height: 1;
            transition: color 0.2s; margin-left: 10px; flex-shrink: 0;
        }}
        #close-popup:hover {{ color: white; }}
        .popup-row {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
        }}
        .popup-key {{ color: rgba(255,255,255,0.4); font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }}
        .popup-val {{ color: white; font-weight: 600; font-size: 12px; }}

        /* ── MAPBOX CONTROLS ── */
        .mapboxgl-ctrl-group {{
            background: linear-gradient(135deg, rgba(0,8,20,0.92), rgba(0,15,35,0.92)) !important;
            border: 1px solid rgba(255,255,255,0.07) !important;
            border-radius: 8px !important;
            backdrop-filter: blur(20px) !important;
        }}
        .mapboxgl-ctrl-group button {{ background: transparent !important; }}
        .mapboxgl-ctrl-group button span {{ filter: invert(1) brightness(0.7); }}
        .mapboxgl-ctrl-group button:hover span {{ filter: invert(1); }}
        .mapboxgl-ctrl-attrib {{ display: none !important; }}

        /* ── ADDRESS SEARCH ── */
        #address-panel {{
            position: absolute; bottom: 30px; right: 16px; z-index: 10;
            background: linear-gradient(135deg, rgba(0,8,20,0.96) 0%, rgba(0,20,40,0.96) 100%);
            border: 1px solid rgba(0,180,255,0.3); border-radius: 12px;
            padding: 16px; width: 300px;
            backdrop-filter: blur(20px);
            box-shadow: 0 0 30px rgba(0,0,0,0.5);
        }}
        #address-panel h4 {{
            font-size: 10px; color: rgba(0,180,255,0.8); font-weight: 600;
            letter-spacing: 2px; text-transform: uppercase;
            margin-bottom: 10px; border-bottom: 1px solid rgba(0,180,255,0.2);
            padding-bottom: 6px;
        }}
        #address-input {{
            width: 100%; padding: 8px 10px; border-radius: 6px;
            background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
            color: white; font-size: 12px; font-family: 'Inter', Arial, sans-serif;
            outline: none; transition: border-color 0.2s;
        }}
        #address-input:focus {{ border-color: rgba(0,180,255,0.5); }}
        #address-input::placeholder {{ color: rgba(255,255,255,0.3); }}
        #buffer-row {{
            display: flex; gap: 8px; margin-top: 8px; align-items: center;
        }}
        #buffer-slider {{
            flex: 1; accent-color: #00B4FF;
        }}
        #buffer-label {{
            font-size: 11px; color: rgba(0,180,255,0.8);
            min-width: 55px; text-align: right;
        }}
        #search-btn {{
            width: 100%; margin-top: 10px; padding: 8px;
            background: linear-gradient(135deg, rgba(0,100,200,0.6), rgba(0,50,150,0.6));
            border: 1px solid rgba(0,180,255,0.4); border-radius: 6px;
            color: white; font-size: 11px; font-weight: 600;
            letter-spacing: 1px; text-transform: uppercase;
            cursor: pointer; transition: all 0.2s;
        }}
        #search-btn:hover {{ background: linear-gradient(135deg, rgba(0,120,220,0.8), rgba(0,70,180,0.8)); }}
        #threat-results {{
            margin-top: 12px; max-height: 200px; overflow-y: auto;
            display: none;
        }}
        .threat-item {{
            padding: 7px 10px; margin: 4px 0; border-radius: 6px;
            font-size: 11px; border-left: 3px solid;
            background: rgba(255,255,255,0.03);
            transition: background 0.2s;
        }}
        .threat-item:hover {{ background: rgba(255,255,255,0.06); }}
        .threat-none {{
            color: rgba(0,255,100,0.8); border-color: #00FF64;
            text-align: center; padding: 10px;
        }}
        #clear-search {{
            font-size: 10px; color: rgba(255,255,255,0.3); cursor: pointer;
            text-align: center; margin-top: 8px; display: none;
        }}
        #clear-search:hover {{ color: white; }}

        /* ── CORNER DECORATIONS ── */
        .corner {{
            position: absolute; width: 20px; height: 20px; z-index: 6;
            border-color: rgba(0,180,255,0.4);
            border-style: solid;
        }}
        .corner-tl {{ top: 10px; left: 10px; border-width: 2px 0 0 2px; }}
        .corner-tr {{ top: 10px; right: 10px; border-width: 2px 2px 0 0; }}
        .corner-bl {{ bottom: 10px; left: 10px; border-width: 0 0 2px 2px; }}
        .corner-br {{ bottom: 10px; right: 10px; border-width: 0 2px 2px 0; }}

        /* ── SCROLLBAR ── */
        ::-webkit-scrollbar {{ width: 4px; }}
        ::-webkit-scrollbar-track {{ background: rgba(0,0,0,0.2); }}
        ::-webkit-scrollbar-thumb {{ background: rgba(0,180,255,0.3); border-radius: 2px; }}
    </style>
</head>
<body>

<div id="map"></div>

<!-- Corner decorations -->
<div class="corner corner-tl"></div>
<div class="corner corner-tr"></div>
<div class="corner corner-bl"></div>
<div class="corner corner-br"></div>

<!-- Header -->
<div id="header">
    <h1><span id="live-dot"></span>National All-Hazards Monitor</h1>
    <p id="update-time">ACQUIRING LIVE DATA...</p>
</div>

<!-- Stat Cards -->
<div id="stats">
    <div class="stat-card" style="--accent: rgba(255,80,80,0.6)">
        <div class="stat-value" id="stat-warnings" style="color:#FF5050">—</div>
        <div class="stat-label">Active Warnings</div>
        <div class="stat-icon">⚠</div>
    </div>
    <div class="stat-card" style="--accent: rgba(0,180,255,0.6)">
        <div class="stat-value" id="stat-eq" style="color:#00B4FF">—</div>
        <div class="stat-label">Earthquakes M2.5+</div>
        <div class="stat-icon">🔴</div>
    </div>
    <div class="stat-card" style="--accent: rgba(255,80,0,0.6)">
        <div class="stat-value" id="stat-fires" style="color:#FF5000">—</div>
        <div class="stat-label">Fire Detections</div>
        <div class="stat-icon">🔥</div>
    </div>
    <div class="stat-card" style="--accent: rgba(0,255,120,0.6)">
        <div class="stat-value" id="stat-spc" style="color:#00FF78">—</div>
        <div class="stat-label">SPC Outlook Zones</div>
        <div class="stat-icon">⛈</div>
    </div>
    <div class="stat-card" style="--accent: rgba(255,150,0,0.6)">
        <div class="stat-value" id="stat-counties" style="color:#FF9600">—</div>
        <div class="stat-label">Affected Counties</div>
        <div class="stat-icon">📍</div>
    </div>
    <div class="stat-card" style="--accent: rgba(200,100,255,0.6)">
        <div class="stat-value" id="stat-pop" style="color:#CC64FF">—</div>
        <div class="stat-label">Population at Risk</div>
        <div class="stat-icon">👥</div>
    </div>
</div>

<!-- Legend -->
<div id="legend">
    <div class="legend-section">
        <div class="legend-title">⚡ NWS Warnings</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF0000;color:#FF0000"></div>Tornado</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF6600;color:#FF6600"></div>Hurricane</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF6666;color:#FF6666"></div>Severe T-Storm</div>
        <div class="legend-item"><div class="legend-box" style="background:#00BFFF;color:#00BFFF"></div>Flash Flood</div>
        <div class="legend-item"><div class="legend-box" style="background:#FFFF00;color:#FFFF00"></div>Other</div>
    </div>
    <div class="legend-section">
        <div class="legend-title">⛈ SPC Outlook</div>
        <div class="legend-item"><div class="legend-box" style="background:#76FF7A;color:#76FF7A"></div>General Thunder</div>
        <div class="legend-item"><div class="legend-box" style="background:#FFFF00;color:#FFFF00"></div>Slight Risk</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF9900;color:#FF9900"></div>Enhanced Risk</div>
        <div class="legend-item"><div class="legend-box" style="background:#FF0000;color:#FF0000"></div>Moderate Risk</div>
    </div>
    <div class="legend-section">
        <div class="legend-title">🔴 Earthquakes</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FFFF00;color:#FFFF00"></div>M2.5 – 3.9</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FF9900;color:#FF9900"></div>M4.0 – 4.9</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FF0000;color:#FF0000"></div>M5.0+</div>
        <div class="legend-title" style="margin-top:10px">🔥 Wildfires</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FF4500;color:#FF4500"></div>NASA FIRMS</div>
        <div class="legend-item"><div class="legend-box" style="background:rgba(255,69,0,0.5);color:#FF4500"></div>Fire Perimeter</div>
        <div class="legend-title" style="margin-top:10px">⚡ Storm Reports</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FFFF00;color:#FFFF00"></div>NWS LSR (last 6hr)</div>
    </div>
    <div class="legend-section">
        <div class="legend-title">🏗 Infrastructure</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FF0066;color:#FF0066"></div>Hospital</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FF4400;color:#FF4400"></div>Fire Station</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FFD700;color:#FFD700"></div>Power Plant</div>
        <div class="legend-item"><div class="legend-dot" style="background:#00FF88;color:#00FF88"></div>School</div>
        <div class="legend-item"><div class="legend-dot" style="background:#FF0000;color:#FF0000"></div>⚠ At Risk</div>
        <div class="legend-title" style="margin-top:10px">📍 Counties</div>
        <div class="legend-item"><div class="legend-box" style="background:rgba(255,100,0,0.4);color:#FF6400"></div>Low Pop</div>
        <div class="legend-item"><div class="legend-box" style="background:rgba(255,0,0,0.6);color:#FF0000"></div>High Pop</div>
    </div>
</div>

<!-- Address Search Panel -->
<div id="address-panel">
    <h4>📍 Location Threat Analysis</h4>
    <input id="address-input" type="text" placeholder="Enter address or city...">
    <div id="buffer-row">
        <span style="font-size:10px;color:rgba(255,255,255,0.4);">RADIUS</span>
        <input id="buffer-slider" type="range" min="5" max="200" value="50" step="5">
        <span id="buffer-label">50 miles</span>
    </div>
    <button id="search-btn" onclick="searchLocation()">🔍 ANALYZE THREATS</button>
    <div id="threat-results"></div>
    <div id="clear-search" onclick="clearSearch()">✕ Clear search</div>
</div>

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
        const sigMap = {{'W':'Warning','A':'Watch','Y':'Advisory','S':'Statement'}};
        const sig = sigMap[p.sig] || p.sig || '';
        const name = (PHENOM_NAMES[phenom] || phenom) + ' ' + sig;
        showPopup('⚠ ' + name, {{
            'Phenomenon': phenom,
            'Significance': sig,
            'WFO': p.wfo || 'N/A',
            'Product': p.prod_type || 'N/A'
        }}, e);
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
    map.on('click', 'fire-clusters', (e) => {{
        map.flyTo({{ center: e.lngLat, zoom: map.getZoom() + 2 }});
    }});

    // Cursor changes for all clickable layers
    ['warnings-fill','spc-fill','eq-circles','fire-points','fire-clusters'].forEach(layer => {{
        map.on('mouseenter', layer, () => map.getCanvas().style.cursor = 'pointer');
        map.on('mouseleave', layer, () => map.getCanvas().style.cursor = '');
    }});

    // ── AFFECTED COUNTIES ────────────────────────────
    map.addSource('counties', {{ type: 'geojson', data: '/api/counties' }});
    map.addLayer({{
        id: 'counties-fill', type: 'fill', source: 'counties',
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

    // At-risk infrastructure — glowing red
    map.addLayer({{
        id: 'infra-at-risk', type: 'circle', source: 'infrastructure',
        filter: ['==', ['get', 'at_risk'], true],
        paint: {{
            'circle-color': '#FF0000',
            'circle-radius': 7,
            'circle-stroke-color': '#FF6666',
            'circle-stroke-width': 2,
            'circle-opacity': 0.9
        }}
    }});

    // Normal infrastructure
    map.addLayer({{
        id: 'infra-normal', type: 'circle', source: 'infrastructure',
        filter: ['==', ['get', 'at_risk'], false],
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

    // ── LAYER TOGGLE BUTTONS ─────────────────────────
    // Add toggle buttons for radar and satellite
    const toggleContainer = document.createElement('div');
    toggleContainer.style.cssText = 'position:absolute;top:200px;right:16px;z-index:10;display:flex;flex-direction:column;gap:6px;';

    function makeToggle(label, layerId, defaultOn) {{
        const btn = document.createElement('button');
        btn.textContent = (defaultOn ? '✅ ' : '⬜ ') + label;
        btn.style.cssText = 'background:rgba(10,10,10,0.9);color:white;border:1px solid #444;border-radius:6px;padding:6px 10px;cursor:pointer;font-size:11px;text-align:left;';
        let on = defaultOn;
        btn.onclick = () => {{
            on = !on;
            map.setLayoutProperty(layerId, 'visibility', on ? 'visible' : 'none');
            btn.textContent = (on ? '✅ ' : '⬜ ') + label;
        }};
        return btn;
    }}

    toggleContainer.appendChild(makeToggle('🔥 Fire Perimeters', 'fire-perimeter-fill', true));
    toggleContainer.appendChild(makeToggle('⚡ Storm Reports', 'lightning-strikes', true));
    toggleContainer.appendChild(makeToggle('NEXRAD Radar', 'nexrad-layer', true));
    toggleContainer.appendChild(makeToggle('GOES Infrared', 'goes-ir-layer', false));
    toggleContainer.appendChild(makeToggle('Affected Counties', 'counties-fill', true));
    toggleContainer.appendChild(makeToggle('🏥 Hospitals', 'infra-normal', true));
    toggleContainer.appendChild(makeToggle('⚠ At-Risk Infra', 'infra-at-risk', true));
    toggleContainer.appendChild(makeToggle('🏷 Infra Labels', 'infra-labels', false));
    document.body.appendChild(toggleContainer);


    // ── LOAD STATS WITH RETRY ────────────────────────
    function loadData() {{
        fetch('/api/summary').then(r => r.json()).then(data => {{
            const s = data.summary || {{}};
            const hasData = (s.warnings_count > 0 || s.earthquakes > 0 || s.wildfires > 0);
            
            document.getElementById('stat-warnings').textContent = s.warnings_count || 0;
            document.getElementById('stat-eq').textContent       = s.earthquakes    || 0;
            document.getElementById('stat-fires').textContent    = s.wildfires      || 0;
            document.getElementById('stat-spc').textContent      = s.spc_zones      || 0;
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
            }} else {{
                document.getElementById('update-time').textContent = 'ACQUIRING LIVE DATA...';
            }}

            // Refresh all map sources with fresh data
            if (map.loaded()) {{
                ['warnings','spc','earthquakes','fires','counties','infrastructure','lightning','fire-perimeters'].forEach(src => {{
                    if (map.getSource(src)) {{
                        map.getSource(src).setData('/api/' + src + '?t=' + Date.now());
                    }}
                }});
            }}

            // If no data yet retry in 10 seconds
            if (!hasData) {{
                console.log('No data yet, retrying in 10s...');
                setTimeout(loadData, 10000);
            }}
        }}).catch(err => {{
            console.log('Fetch failed, retrying in 10s...', err);
            setTimeout(loadData, 10000);
        }});
    }}

    // Load immediately then every 5 minutes
    loadData();
    setInterval(loadData, 5 * 60 * 1000);
}}

// Use exact Mapbox recommended pattern
map.on('load', function() {{
    setupLayers();
}});

// ── ADDRESS SEARCH & THREAT ANALYSIS ─────────────
const MAPBOX_TOKEN_JS = mapboxgl.accessToken;
let searchMarker = null;
let bufferLayer  = null;

// Update buffer label
document.getElementById('buffer-slider').addEventListener('input', function() {{
    document.getElementById('buffer-label').textContent = this.value + ' miles';
}});

// Enter key triggers search
document.getElementById('address-input').addEventListener('keypress', function(e) {{
    if (e.key === 'Enter') searchLocation();
}});

async function searchLocation() {{
    const address = document.getElementById('address-input').value.trim();
    if (!address) return;

    const btn = document.getElementById('search-btn');
    btn.textContent = '⏳ ANALYZING...';
    btn.disabled = true;

    try {{
        // Geocode the address using Mapbox - reuse the same token already set
        const geoUrl = 'https://api.mapbox.com/geocoding/v5/mapbox.places/' +
            encodeURIComponent(address) +
            '.json?country=US&limit=1&access_token=' + MAPBOX_TOKEN_JS;
        const geo = await fetch(geoUrl);
        if (!geo.ok) throw new Error('Geocoding failed: ' + geo.status);
        const geoData = await geo.json();

        if (!geoData.features || geoData.features.length === 0) {{
            showResults([{{type:'error', text:'Address not found. Try a different search.'}}]);
            return;
        }}

        const [lng, lat] = geoData.features[0].center;
        const placeName  = geoData.features[0].place_name;
        const radiusMiles = parseFloat(document.getElementById('buffer-slider').value);
        const radiusKm    = radiusMiles * 1.60934;

        // Fly to location
        map.flyTo({{ center: [lng, lat], zoom: 7, duration: 1500 }});

        // Remove old marker and buffer
        clearSearch(false);

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

        const [warnings, earthquakes, fires, lightning, perimeters] = await Promise.all([
            safeJson('/api/warnings'),
            safeJson('/api/earthquakes'),
            safeJson('/api/fires'),
            safeJson('https://mesonet.agron.iastate.edu/geojson/lsr.php?hours=6&wfo=all',
                d => ({{type:'FeatureCollection', features:(d.features||[]).filter(f => severe.some(x => (f.properties&&f.properties.typetext||'').toUpperCase().indexOf(x)>=0))}})),
            safeJson('https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_YTD/FeatureServer/0/query?where=1%3D1&outFields=IncidentName,GISAcres,PercentContained&geometryPrecision=3&outSR=4326&resultRecordCount=200&f=geojson'),
        ]);

        const threats = [];
        let totalScore = 0;
        const userPt = turf.point([lng, lat]);

        // ── NWS WARNINGS ─────────────────────────────
        const warningsInBuffer = warnings.features.filter(f => {{
            try {{
                if (!f.geometry) return false;
                if (f.geometry.type === 'Point') {{
                    return turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer);
                }}
                return turf.booleanIntersects(f, buffer);
            }} catch(e) {{ return false; }}
        }});

        warningsInBuffer.forEach(f => {{
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

            threats.push({{
                type: 'threat', color, dist,
                text: `${{label}} (+${{pts}} pts)`
            }});
        }});

        // ── EARTHQUAKES ───────────────────────────────
        const eqFeats = earthquakes.features
            .filter(f => f.geometry?.coordinates)
            .map(f => ({{
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }}))
            .filter(f => {{
                try {{ return turf.booleanPointInPolygon(f._pt, buffer); }}
                catch(e) {{ return false; }}
            }});

        eqFeats.forEach(f => {{
            const mag  = parseFloat(f.properties?.mag || 0);
            const dist = turf.distance(userPt, f._pt, {{units: 'miles'}});
            let weight = mag >= 5 ? THREAT_WEIGHTS.earthquake_m5
                       : mag >= 4 ? THREAT_WEIGHTS.earthquake_m4
                       :            THREAT_WEIGHTS.earthquake_m3;
            const decay = distanceDecay(dist, radiusMiles);
            const pts   = Math.round(weight * decay);
            totalScore += pts;
            threats.push({{
                type: 'threat', color: '#00B4FF', dist,
                text: `🔴 Earthquake M${{mag.toFixed(1)}} — ${{f.properties?.place || 'Unknown'}} (+${{pts}} pts)`
            }});
        }});

        // ── WILDFIRES ─────────────────────────────────
        const fireFeats = fires.features
            .filter(f => f.geometry?.coordinates)
            .map(f => ({{
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }}))
            .filter(f => {{
                try {{ return turf.booleanPointInPolygon(f._pt, buffer); }}
                catch(e) {{ return false; }}
            }});

        if (fireFeats.length > 0) {{
            // Find closest fire
            const closest = fireFeats.reduce((a, b) => {{
                const da = turf.distance(userPt, a._pt, {{units:'miles'}});
                const db = turf.distance(userPt, b._pt, {{units:'miles'}});
                return da < db ? a : b;
            }});
            const dist  = turf.distance(userPt, closest._pt, {{units:'miles'}});
            const decay = distanceDecay(dist, radiusMiles);
            const pts   = Math.round(THREAT_WEIGHTS.wildfire_near * decay);
            totalScore += pts;
            threats.push({{
                type: 'threat', color: '#FF5000', dist,
                text: `🔥 ${{fireFeats.length}} Wildfire Detection(s) — closest ${{Math.round(dist)}}mi (+${{pts}} pts)`
            }});
        }}

        // ── STORM REPORTS ─────────────────────────────
        const stormFeats = lightning.features
            .filter(f => f.geometry?.coordinates)
            .map(f => ({{
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }}))
            .filter(f => {{
                try {{ return turf.booleanPointInPolygon(f._pt, buffer); }}
                catch(e) {{ return false; }}
            }});

        if (stormFeats.length > 0) {{
            // Time decay — recent reports weighted more
            const now = Date.now();
            stormFeats.forEach(f => {{
                const validTime = new Date(f.properties?.valid || now).getTime();
                const hoursAgo  = (now - validTime) / 3600000;
                const recency   = Math.max(0, 1 - hoursAgo / 6);
                const dist      = turf.distance(userPt, f._pt, {{units:'miles'}});
                const decay     = distanceDecay(dist, radiusMiles);
                const pts       = Math.round(THREAT_WEIGHTS.storm_report * decay * recency);
                totalScore     += pts;
            }});
            const types = [...new Set(stormFeats.map(f => f.properties?.typetext || 'Storm').slice(0,3))];
            threats.push({{
                type: 'threat', color: '#FFFF00',
                dist: turf.distance(userPt,
                    stormFeats.reduce((a,b) =>
                        turf.distance(userPt,a._pt,{{units:'miles'}}) <
                        turf.distance(userPt,b._pt,{{units:'miles'}}) ? a : b
                    )._pt, {{units:'miles'}}),
                text: `⚡ ${{stormFeats.length}} Storm Report(s) — ${{types.join(', ')}}`
            }});
        }}

        // ── FIRE PERIMETERS ───────────────────────────
        const perimInBuffer = perimeters.features.filter(f => {{
            try {{
                if (!f.geometry) return false;
                return turf.booleanIntersects(f, buffer);
            }} catch(e) {{ return false; }}
        }});

        perimInBuffer.forEach(f => {{
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
            threats.push({{
                type: 'threat', color: '#FF4500', dist,
                text: `🔥 ${{name}} — ${{Math.round(acres).toLocaleString()}} acres (${{f.properties?.PercentContained||0}}% contained) (+${{pts}} pts)`
            }});
        }});

        // ── CAP SCORE & SORT ──────────────────────────
        totalScore = Math.min(100, Math.round(totalScore));

        // Sort threats by distance
        threats.sort((a,b) => (a.dist||99) - (b.dist||99));

        // Show results
        document.getElementById('clear-search').style.display = 'block';
        const locationLabel = placeName.split(',').slice(0,2).join(',');

        if (threats.length === 0) {{
            showResults([
                {{ type: 'score', score: 0 }},
                {{ type: 'safe', text: `✅ No active threats detected within ${{radiusMiles}} miles of ${{locationLabel}}` }}
            ]);
        }} else {{
            showResults([
                {{ type: 'header', text: `📍 ${{locationLabel}} · ${{radiusMiles}}mi radius` }},
                {{ type: 'score', score: totalScore }},
                ...threats
            ]);
        }}

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
            ${{t.dist !== undefined ? `<div style="font-size:9px;color:rgba(255,255,255,0.3);margin-top:2px;">
                ${{Math.round(t.dist)}} miles away</div>` : ''}}
        </div>`;
    }}).join('');
}}

function clearSearch(resetInput=true) {{
    if (searchMarker) {{ searchMarker.remove(); searchMarker = null; }}
    if (map.getLayer('buffer-fill'))    map.removeLayer('buffer-fill');
    if (map.getLayer('buffer-outline')) map.removeLayer('buffer-outline');
    if (map.getSource('search-buffer')) map.removeSource('search-buffer');
    document.getElementById('threat-results').style.display = 'none';
    document.getElementById('threat-results').innerHTML = '';
    document.getElementById('clear-search').style.display = 'none';
    if (resetInput) document.getElementById('address-input').value = '';
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
    return response

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
