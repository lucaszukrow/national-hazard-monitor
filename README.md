# 🌪 National All-Hazards GIS Monitoring System

A real-time hazard monitoring dashboard that integrates live data from 7 federal agencies into a single unified GIS analysis and visualization platform.

## Live Dashboard
[View Live](https://national-hazard-monitor.onrender.com)

## Data Sources
| Agency | Dataset |
|--------|---------|
| NOAA/NWS | Active watches, warnings, and advisories |
| NOAA/NHC | Hurricane tracks and forecast cones |
| NOAA/SPC | Convective outlook risk zones |
| USGS | Earthquake feed M2.5+ (24 hours) |
| NASA FIRMS | VIIRS satellite wildfire detections |
| US Census | 2023 county population estimates |
| OpenStreetMap | Hospital, school, fire station, power plant locations |

## Features
- Live interactive Folium map with all hazard layers
- Affected county identification with population at risk
- Critical infrastructure impact analysis
- Auto-updates every 30 minutes
- PDF Situation Report generation
- CSV export with full legend

## Tech Stack
- Python (Dash, Plotly, Folium, Requests)
- ArcPy (local version) / Shapely (cloud version)
- Render.com deployment

## Author
Lucas Zukrow | GSP318 GIS Programming | Cal Poly Humboldt
