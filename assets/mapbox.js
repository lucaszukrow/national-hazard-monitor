mapboxgl.accessToken = window.MAPBOX_TOKEN || '';

// ── Command Center compat shim ──────────────────────────────────────
// The legacy /mapbox JS still references DOM ids from the pre-redesign
// shell (stat-warnings, county-hero, top-impacted-list, sitrep-overlay,
// hazard-overview chart, etc.). The new Command Center template doesn't
// render any of those. Rather than null-crash on every loadData tick,
// missing-id lookups return a proxy that silently swallows reads/writes
// and chained method calls. Real elements still work normally.
(function installDeadElementShim() {
    const _origGetById = document.getElementById.bind(document);
    const noop = () => DEAD;
    const DEAD = new Proxy(function () {}, {
        get(_, prop) {
            if (prop === Symbol.toPrimitive || prop === 'toString') return () => '';
            if (prop === 'length') return 0;
            if (prop === 'forEach' || prop === 'add' || prop === 'remove'
                || prop === 'addEventListener' || prop === 'removeEventListener'
                || prop === 'setProperty' || prop === 'appendChild' || prop === 'closest'
                || prop === 'querySelector' || prop === 'querySelectorAll'
                || prop === 'contains' || prop === 'matches' || prop === 'focus' || prop === 'blur'
                || prop === 'click') return noop;
            return DEAD;
        },
        set() { return true; },
        apply() { return DEAD; },
    });
    document.getElementById = function (id) {
        return _origGetById(id) || DEAD;
    };
})();

const HAZARD_COLORS = {
    'TO': '#FF0000', 'FF': '#00BFFF', 'HU': '#FF6600',
    'TS': '#FF9900', 'SV': '#FF6666', 'WS': '#AAAAFF',
    'FA': '#0099FF', 'FW': '#FF4500'
};
const SPC_COLORS = {
    'TSTM': '#76FF7A', 'MRGL': '#009000', 'SLGT': '#FFFF00',
    'ENH': '#FF9900', 'MDT': '#FF0000', 'HIGH': '#FF00FF'
};
const PHENOM_NAMES = {
    'TO':'Tornado','SV':'Severe Thunderstorm','FF':'Flash Flood',
    'FA':'Flood','HU':'Hurricane','TS':'Tropical Storm',
    'WS':'Winter Storm','FW':'Fire Weather','EH':'Excessive Heat',
    'HW':'High Wind','CF':'Coastal Flood','SS':'Storm Surge'
};

const map = new mapboxgl.Map({
    container: 'map',
    style: 'mapbox://styles/mapbox/dark-v11',
    center: [-98.35, 39.5],
    zoom: 3.5
});

map.addControl(new mapboxgl.NavigationControl(), 'top-right');
map.addControl(new mapboxgl.FullscreenControl(), 'top-right');

// ── LEGEND TOGGLE ────────────────────────────────
let _legendOpen = false;
function toggleLegend() {
    _legendOpen = !_legendOpen;
    const legend = document.getElementById('legend');
    const arrow  = document.getElementById('legend-toggle-arrow');
    legend.style.display = _legendOpen ? 'grid' : 'none';
    arrow.style.transform = _legendOpen ? '' : 'rotate(180deg)';
}

// ── SIDEBAR HELPERS ───────────────────────────────
function setSidebarActive(btn) {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}
function focusThreatPanel() {
    const panel = document.getElementById('address-panel');
    panel.style.boxShadow = '0 0 0 2px #58bfff, 0 0 20px rgba(88,191,255,0.3)';
    setTimeout(() => { panel.style.boxShadow = ''; }, 1600);
    document.getElementById('address-input').focus();
}
function toggleLayerPanel() {
    // Layers now live in the persistent left sidebar. On mobile, open the drawer instead.
    if (window.matchMedia('(max-width: 900px)').matches) {
        openMobileLayers();
        return;
    }
    const p = document.querySelector('nav.sidebar');
    if (!p) return;
    p.style.boxShadow = '4px 0 24px rgba(88,191,255,0.35)';
    setTimeout(() => { p.style.boxShadow = ''; }, 900);
}

// ── MOBILE PANEL CONTROLS ───────────────────────────────────
const _isMobile = () => window.matchMedia('(max-width: 900px)').matches;
const _isCoarse = () => window.matchMedia('(pointer: coarse)').matches;
function openMobileLayers() {
    const sidebar = document.querySelector('nav.sidebar');
    const scrim = document.getElementById('mobile-scrim');
    if (sidebar) sidebar.classList.add('mobile-open');
    if (scrim) scrim.classList.add('show');
    document.body.style.overflow = 'hidden';
}
function openMobileLegend() {
    // Reuse the legend panel — on mobile we pop it up centered.
    const wrap = document.getElementById('legend-wrap');
    const legend = document.getElementById('legend');
    if (!wrap || !legend) return;
    wrap.style.display = 'block';
    wrap.style.position = 'fixed';
    wrap.style.left = 'calc(12px + var(--safe-left))';
    wrap.style.right = 'calc(12px + var(--safe-right))';
    wrap.style.bottom = 'calc(140px + var(--safe-bottom))';
    wrap.style.top = 'auto';
    wrap.style.zIndex = '58';
    wrap.style.maxHeight = '55vh';
    wrap.style.overflowY = 'auto';
    legend.style.display = 'grid';
    legend.style.gridTemplateColumns = '1fr 1fr';
    legend.classList.remove('collapsed');
    const scrim = document.getElementById('mobile-scrim');
    if (scrim) scrim.classList.add('show');
}
function closeMobileLegend() {
    const wrap = document.getElementById('legend-wrap');
    if (!wrap) return;
    wrap.style.display = '';
    wrap.style.position = '';
    wrap.style.left = '';
    wrap.style.right = '';
    wrap.style.bottom = '';
    wrap.style.top = '';
    wrap.style.zIndex = '';
    wrap.style.maxHeight = '';
    wrap.style.overflowY = '';
}
function closeAllMobilePanels() {
    const sidebar = document.querySelector('nav.sidebar');
    const scrim = document.getElementById('mobile-scrim');
    if (sidebar) sidebar.classList.remove('mobile-open');
    if (scrim) scrim.classList.remove('show');
    closeMobileLegend();
    closeOverflowMenu();
    document.body.style.overflow = '';
}
function toggleOverflowMenu(e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById('mobile-overflow-menu');
    if (!menu) return;
    menu.classList.toggle('open');
}
function closeOverflowMenu() {
    const menu = document.getElementById('mobile-overflow-menu');
    if (menu) menu.classList.remove('open');
}
document.addEventListener('click', (e) => {
    const menu = document.getElementById('mobile-overflow-menu');
    const btn = document.getElementById('mobile-overflow-btn');
    if (!menu || !menu.classList.contains('open')) return;
    if (menu.contains(e.target) || (btn && btn.contains(e.target))) return;
    closeOverflowMenu();
});
// ── Address-panel drag-to-expand on mobile ─────────────────
(function setupAddressDrag() {
    const handle = document.getElementById('address-drag-handle');
    const panel = document.getElementById('address-panel');
    const body  = document.getElementById('address-body');
    if (!handle || !panel || !body) return;
    let startY = 0, startH = 0, dragging = false;
    const getH = () => parseFloat(getComputedStyle(panel).maxHeight) || panel.offsetHeight;
    handle.addEventListener('pointerdown', (e) => {
        if (!_isMobile()) return;
        dragging = true; startY = e.clientY; startH = panel.offsetHeight;
        handle.setPointerCapture?.(e.pointerId);
        panel.style.transition = 'none';
    });
    handle.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        const dy = startY - e.clientY;
        const newH = Math.min(window.innerHeight * 0.92, Math.max(80, startH + dy));
        panel.style.maxHeight = newH + 'px';
    });
    const stop = (e) => {
        if (!dragging) return;
        dragging = false;
        panel.style.transition = '';
        // Snap: if collapsed below 120px, collapse body; else show
        if (panel.offsetHeight < 120) {
            if (body.style.display !== 'none') toggleAddressPanel();
            panel.style.maxHeight = '';
        }
    };
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
})();
// ── Tap-based popup close on coarse pointers ───────────────
(function wireMapTouch() {
    if (!_isCoarse()) return;
    // Tapping the map outside a feature closes open popups.
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof map === 'undefined' || !map.on) return;
        map.on('click', (e) => {
            // If the click wasn't on an interactive layer, close the popup.
            const popup = document.getElementById('popup');
            const features = map.queryRenderedFeatures(e.point);
            if (!features.length && popup) popup.style.display = 'none';
        });
    });
})();
// ── Escape closes mobile panels ────────────────────────────
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAllMobilePanels();
});
function showHazardOverview() {
    const wrap = document.getElementById('stat-cards-wrap');
    if (wrap) wrap.style.display = 'block';
}
function hideHazardOverview() {
    const wrap = document.getElementById('stat-cards-wrap');
    if (wrap) wrap.style.display = 'none';
}
function toggleAddressPanel() {
    const body = document.getElementById('address-body');
    const arrow = document.getElementById('address-arrow');
    if (!body || !arrow) return;
    const nowOpen = body.style.display === 'none';
    body.style.display = nowOpen ? 'block' : 'none';
    arrow.style.transform = nowOpen ? 'rotate(180deg)' : 'rotate(0deg)';
    try { localStorage.setItem('nhm_threat_open', nowOpen ? '1' : '0'); } catch(e) {}
}
// Restore threat panel collapse state on load
document.addEventListener('DOMContentLoaded', () => {
    try {
        if (localStorage.getItem('nhm_threat_open') === '0') toggleAddressPanel();
    } catch(e) {}
});
function flyToWarnings() {
    if (_latestWarnings?.features?.length) {
        const f = _latestWarnings.features[0];
        const coords = f.geometry?.coordinates?.[0]?.[0];
        if (coords) map.flyTo({center: coords, zoom: 5, duration: 1400});
    }
}
function flyToEarthquakes() {
    if (_latestEarthquakes?.features?.length) {
        const biggest = [..._latestEarthquakes.features]
            .sort((a,b) => (b.properties?.mag||0) - (a.properties?.mag||0))[0];
        if (biggest?.geometry?.coordinates) {
            const [lng,lat] = biggest.geometry.coordinates;
            map.flyTo({center:[lng,lat], zoom:6, duration:1400});
        }
    }
}
function flyToFires() {
    if (_latestFires?.features?.length) {
        const pts = _latestFires.features.filter(f=>f.geometry?.coordinates);
        if (pts.length) {
            const lng = pts.reduce((s,p)=>s+p.geometry.coordinates[0],0)/pts.length;
            const lat = pts.reduce((s,p)=>s+p.geometry.coordinates[1],0)/pts.length;
            map.flyTo({center:[lng,lat], zoom:5, duration:1400});
        }
    }
}

function showPopup(title, rows, e) {
    const popup = document.getElementById('popup');
    document.getElementById('popup-title').textContent = title;
    let html = '';
    for (const [k, v] of Object.entries(rows)) {
        html += `<div class="popup-row"><span class="popup-key">${k}</span><span class="popup-val">${v}</span></div>`;
    }
    document.getElementById('popup-content').innerHTML = html;
    popup.style.display = 'block';
    // On phones (≤640px) CSS pins the popup to the bottom as a sheet — don't set coords.
    if (window.matchMedia('(max-width: 640px)').matches) {
        popup.style.left = '';
        popup.style.top = '';
        return;
    }
    const x = e.point.x + 14;
    const y = e.point.y - 10;
    popup.style.left = Math.min(x, window.innerWidth - 300) + 'px';
    popup.style.top  = Math.max(y, 10) + 'px';
}

function setupLayers() {
    // Guard: don't add sources if already added
    if (map.getSource('warnings')) return;

    // 3D terrain removed — was forcing full 3D render mode on every frame

    // ── SPC OUTLOOK ─────────────────────────────────
    map.addSource('spc', { type: 'geojson', data: '/api/spc' });
    map.addLayer({
        id: 'spc-fill', type: 'fill', source: 'spc',
        layout: { visibility: 'none' },
        paint: {
            'fill-color': [
                'match', ['get', 'LABEL'],
                'TSTM', '#76FF7A', 'MRGL', '#009000',
                'SLGT', '#FFFF00', 'ENH',  '#FF9900',
                'MDT',  '#FF0000', 'HIGH', '#FF00FF',
                '#76FF7A'
            ],
            'fill-opacity': 0.25
        }
    });
    map.addLayer({
        id: 'spc-outline', type: 'line', source: 'spc',
        layout: { visibility: 'none' },
        paint: {
            'line-color': ['match', ['get', 'LABEL'],
                'TSTM', '#76FF7A', 'MRGL', '#009000',
                'SLGT', '#FFFF00', 'ENH',  '#FF9900',
                'MDT',  '#FF0000', 'HIGH', '#FF00FF',
                '#76FF7A'
            ],
            'line-width': 1.5,
            'line-dasharray': [4, 4]
        }
    });

    // ── NWS WARNINGS ────────────────────────────────
    map.addSource('warnings', { type: 'geojson', data: '/api/warnings' });
    map.addLayer({
        id: 'warnings-fill', type: 'fill', source: 'warnings',
        paint: {
            'fill-color': [
                'match', ['get', 'phenom'],
                'TO', '#FF0000', 'FF', '#00BFFF', 'HU', '#FF6600',
                'TS', '#FF9900', 'SV', '#FF6666', 'WS', '#AAAAFF',
                'FA', '#0099FF', 'FW', '#FF4500', '#FFFF00'
            ],
            'fill-opacity': 0.45
        }
    });
    map.addLayer({
        id: 'warnings-outline', type: 'line', source: 'warnings',
        paint: {
            'line-color': '#FFFFFF',
            'line-width': 1,
            'line-opacity': 0.6
        }
    });

    // Warning pulse — slowed to 1500ms so setPaintProperty fires ~6× less often
    let opacity = 0.4;
    let direction = -1;
    setInterval(() => {
        if (!map.getLayer('warnings-fill')) return;
        if (!_latestWarnings?.features?.length) return;
        opacity += direction * 0.15;
        if (opacity <= 0.2 || opacity >= 0.6) direction *= -1;
        map.setPaintProperty('warnings-fill', 'fill-opacity', opacity);
    }, 1500);

    // ── EARTHQUAKES ──────────────────────────────────
    map.addSource('earthquakes', { type: 'geojson', data: '/api/earthquakes' });
    map.addLayer({
        id: 'eq-circles', type: 'circle', source: 'earthquakes',
        layout: { visibility: 'none' },
        paint: {
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
        }
    });

    // ── WILDFIRES (simple points, no clustering or heatmap) ──
    map.addSource('fires', { type: 'geojson', data: '/api/fires' });
    map.addLayer({
        id: 'fire-points', type: 'circle', source: 'fires',
        layout: { visibility: 'none' },
        paint: {
            'circle-color': [
                'step', ['get', 'frp'],
                '#FF8C00', 20, '#FF4500', 100, '#FF0000'
            ],
            'circle-radius': 5,
            'circle-stroke-color': '#FFD700',
            'circle-stroke-width': 1,
            'circle-opacity': 0.8
        }
    });

    // ── CLICK HANDLERS ───────────────────────────────
    map.on('click', 'warnings-fill', (e) => {
        const p = e.features[0].properties;
        const phenom = p.phenom || '';
        const sigMap = {'W':'Warning','A':'Watch','Y':'Advisory','S':'Statement'};
        const sig = sigMap[p.sig] || p.sig || '';
        const name = (PHENOM_NAMES[phenom] || phenom) + ' ' + sig;
        const rows = { 'Issued by': p.wfo || 'N/A' };
        if (p.expires) {
            try {
                const exp = new Date(p.expires);
                if (!isNaN(exp)) rows['Expires'] = exp.toLocaleString([],
                    {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
            } catch(e) {}
        }
        if (p.event) rows['Event'] = p.event;
        if (p.headline) rows['Headline'] = p.headline.length > 90 ? p.headline.slice(0,90) + '…' : p.headline;
        if (p.areaDesc) rows['Area'] = p.areaDesc.length > 80 ? p.areaDesc.slice(0,80) + '…' : p.areaDesc;
        showPopup('⚠ ' + name, rows, e);
    });
    map.on('click', 'spc-fill', (e) => {
        const p = e.features[0].properties;
        const labels = {'TSTM':'General Thunder','MRGL':'Marginal Risk','SLGT':'Slight Risk','ENH':'Enhanced Risk','MDT':'Moderate Risk','HIGH':'High Risk'};
        showPopup('⛈ SPC Convective Outlook', {
            'Risk Level': labels[p.LABEL] || p.LABEL || 'N/A',
            'Label': p.LABEL2 || p.LABEL || 'N/A'
        }, e);
    });
    map.on('click', 'eq-circles', (e) => {
        const p = e.features[0].properties;
        showPopup('🔴 Earthquake M' + p.mag, {
            'Location': p.place || 'Unknown',
            'Magnitude': p.mag,
            'Depth': (p.depth || 'N/A') + ' km',
            'Time': p.time ? new Date(p.time).toLocaleString() : 'N/A'
        }, e);
    });
    map.on('click', 'fire-points', (e) => {
        const p = e.features[0].properties;
        showPopup('🔥 Wildfire Detection', {
            'Date': p.acq_date || 'N/A',
            'FRP': (p.frp || 'N/A') + ' MW',
            'Confidence': p.confidence || 'N/A'
        }, e);
    });
    // Cursor changes for all clickable layers
    ['warnings-fill','spc-fill','eq-circles','fire-points'].forEach(layer => {
        map.on('mouseenter', layer, () => map.getCanvas().style.cursor = 'pointer');
        map.on('mouseleave', layer, () => map.getCanvas().style.cursor = '');
    });

    // ── AFFECTED COUNTIES ────────────────────────────
    // Two-dimensional encoding: severity_rank -> color, population -> opacity
    map.addSource('counties', { type: 'geojson', data: '/api/counties' });
    map.addLayer({
        id: 'counties-fill', type: 'fill', source: 'counties',
        layout: { visibility: 'none' },
        paint: {
            'fill-color': [
                'match', ['get', 'severity_rank'],
                3, '#FF2222',   // Warning  — red
                2, '#FF8800',   // Watch    — orange
                1, '#FFCC00',   // Advisory — yellow
                0, '#888888',   // Statement/unknown — gray
                   '#888888'
            ],
            'fill-opacity': [
                'interpolate', ['linear'],
                ['get', 'population'],
                0,       0.25,
                50000,   0.40,
                500000,  0.60,
                2000000, 0.80
            ]
        }
    });
    map.addLayer({
        id: 'counties-outline', type: 'line', source: 'counties',
        layout: { visibility: 'none' },
        paint: {
            'line-color': '#FF6600',
            'line-width': 1.5,
            'line-opacity': 0.8
        }
    });
    map.on('click', 'counties-fill', (e) => {
        const p = e.features[0].properties;
        showPopup('📍 ' + p.county + ', ' + p.state, {
            'Population':      Number(p.population).toLocaleString(),
            'Highest alert':   p.event || 'N/A',
            'Level':           p.sig || 'N/A',
            'Active warnings': p.warning_count || 1,
            'FIPS':            p.fips || 'N/A'
        }, e);
    });
    map.on('mouseenter', 'counties-fill', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'counties-fill', () => map.getCanvas().style.cursor = '');

    // ── INFRASTRUCTURE ────────────────────────────────
    map.addSource('infrastructure', { type: 'geojson', data: '/api/infrastructure' });

    // At-risk infrastructure — glowing red (off by default)
    map.addLayer({
        id: 'infra-at-risk', type: 'circle', source: 'infrastructure',
        filter: ['==', ['get', 'at_risk'], true],
        layout: { visibility: 'none' },
        paint: {
            'circle-color': '#FF0000',
            'circle-radius': 7,
            'circle-stroke-color': '#FF6666',
            'circle-stroke-width': 2,
            'circle-opacity': 0.9
        }
    });

    // Normal infrastructure (off by default)
    map.addLayer({
        id: 'infra-normal', type: 'circle', source: 'infrastructure',
        filter: ['==', ['get', 'at_risk'], false],
        layout: { visibility: 'none' },
        paint: {
            'circle-color': ['get', 'color'],
            'circle-radius': 4,
            'circle-stroke-color': 'rgba(255,255,255,0.3)',
            'circle-stroke-width': 1,
            'circle-opacity': 0.7
        }
    });

    // Infrastructure labels
    map.addLayer({
        id: 'infra-labels', type: 'symbol', source: 'infrastructure',
        layout: {
            'text-field': ['get', 'name'],
            'text-size': 9,
            'text-offset': [0, 1.2],
            'text-anchor': 'top',
            'visibility': 'none'
        },
        paint: {
            'text-color': 'white',
            'text-halo-color': 'rgba(0,0,0,0.8)',
            'text-halo-width': 1
        }
    });

    map.on('click', 'infra-at-risk', (e) => {
        const p = e.features[0].properties;
        showPopup(p.icon + ' ' + p.name, {
            'Type': p.type.replace('_', ' ').toUpperCase(),
            'Status': '⚠ AT RISK — inside warning zone',
        }, e);
    });
    map.on('click', 'infra-normal', (e) => {
        const p = e.features[0].properties;
        showPopup(p.icon + ' ' + p.name, {
            'Type': p.type.replace('_', ' ').toUpperCase(),
            'Status': '✅ Not currently at risk',
        }, e);
    });
    ['infra-at-risk', 'infra-normal'].forEach(layer => {
        map.on('mouseenter', layer, () => map.getCanvas().style.cursor = 'pointer');
        map.on('mouseleave', layer, () => map.getCanvas().style.cursor = '');
    });
    map.addSource('nexrad', {
        type: 'raster',
        tiles: ['https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS=nexrad-n0r&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE&HEIGHT=256&WIDTH=256&SRS=EPSG:3857&BBOX={bbox-epsg-3857}'],
        tileSize: 256,
        attribution: 'Iowa State Mesonet'
    });
    map.addLayer({
        id: 'nexrad-layer',
        type: 'raster',
        source: 'nexrad',
        layout: { visibility: 'none' },
        paint: { 'raster-opacity': 0.7 }
    });

    // ── GOES INFRARED (Live) ─────────────────────────
    map.addSource('goes-ir', {
        type: 'raster',
        tiles: ['https://mesonet.agron.iastate.edu/cgi-bin/wms/goes/conus_ir.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS=goes_conus_ir&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE&HEIGHT=256&WIDTH=256&SRS=EPSG:3857&BBOX={bbox-epsg-3857}'],
        tileSize: 256,
        attribution: 'Iowa State Mesonet'
    });
    map.addLayer({
        id: 'goes-ir-layer',
        type: 'raster',
        source: 'goes-ir',
        paint: { 'raster-opacity': 0.6 },
        layout: { 'visibility': 'none' }
    });

    // ── LIGHTNING / STORM REPORTS ────────────────────
    map.addSource('lightning', { type: 'geojson', data: '/api/lightning' });
    map.addLayer({
        id: 'lightning-strikes', type: 'circle', source: 'lightning',
        layout: { visibility: 'none' },
        paint: {
            'circle-color': '#FFFF00',
            'circle-radius': 5,
            'circle-stroke-color': 'rgba(255,255,255,0.6)',
            'circle-stroke-width': 1,
            'circle-opacity': 0.85
        }
    });
    map.on('click', 'lightning-strikes', (e) => {
        const p = e.features[0].properties;
        showPopup('⚡ Storm Report', {
            'Type':     p.typetext || 'N/A',
            'Location': p.city    || 'N/A',
            'Time':     p.valid   ? new Date(p.valid).toLocaleString() : 'N/A',
            'Source':   'NWS LSR'
        }, e);
    });
    map.on('mouseenter', 'lightning-strikes', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'lightning-strikes', () => map.getCanvas().style.cursor = '');

    // ── FIRE PERIMETERS ──────────────────────────────
    map.addSource('fire_perimeters', { type: 'geojson', data: '/api/fire_perimeters' });
    map.addLayer({
        id: 'fire-perimeter-fill', type: 'fill', source: 'fire_perimeters',
        layout: { visibility: 'none' },
        paint: {
            'fill-color': 'rgba(255,69,0,0.25)',
            'fill-outline-color': '#FF4500'
        }
    });
    map.addLayer({
        id: 'fire-perimeter-outline', type: 'line', source: 'fire_perimeters',
        layout: { visibility: 'none' },
        paint: {
            'line-color': '#FF4500',
            'line-width': 2,
            'line-opacity': 0.9,
            'line-dasharray': [2, 1]
        }
    });
    map.on('click', 'fire-perimeter-fill', (e) => {
        const p = e.features[0].properties;
        showPopup('🔥 ' + (p.IncidentName || 'Active Fire'), {
            'Acres':     p.GISAcres ? Math.round(p.GISAcres).toLocaleString() : 'N/A',
            'Contained': (p.PercentContained || 0) + '%',
            'Updated':   p.ModifiedOnDateTime_dt || 'N/A'
        }, e);
    });
    map.on('mouseenter', 'fire-perimeter-fill', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'fire-perimeter-fill', () => map.getCanvas().style.cursor = '');

    // ── HURRICANES ───────────────────────────────────
    map.addSource('storms', { type: 'geojson', data: '/api/storms' });
    map.addLayer({
        id: 'storm-cone', type: 'fill', source: 'storms',
        filter: ['==', ['get', 'layer'], 'cone'],
        layout: { visibility: 'none' },
        paint: {
            'fill-color': '#FF6600',
            'fill-opacity': 0.18
        }
    });
    map.addLayer({
        id: 'storm-cone-outline', type: 'line', source: 'storms',
        filter: ['==', ['get', 'layer'], 'cone'],
        layout: { visibility: 'none' },
        paint: { 'line-color': '#FF6600', 'line-width': 2, 'line-opacity': 0.7 }
    });
    map.addLayer({
        id: 'storm-track', type: 'circle', source: 'storms',
        filter: ['==', ['get', 'layer'], 'track'],
        layout: { visibility: 'none' },
        paint: {
            'circle-color': '#FF6600',
            'circle-radius': 7,
            'circle-stroke-color': '#FFD700',
            'circle-stroke-width': 2,
            'circle-opacity': 0.9
        }
    });
    map.on('click', 'storm-track', (e) => {
        const p = e.features[0].properties;
        showPopup('🌀 ' + (p.storm_name || 'Hurricane'), {
            'Type': 'Forecast Track Point',
            'Storm': p.storm_name || 'N/A'
        }, e);
    });
    map.on('mouseenter', 'storm-track', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'storm-track', () => map.getCanvas().style.cursor = '');

    // Animate hurricane track — slowed to 1000ms, same visual speed via adjusted frequency
    let _stormFrame = 0;
    setInterval(() => {
        if (!map.getLayer('storm-track')) return;
        if (!_latestStorms?.features?.length) return;
        const pulse = 7 + Math.sin(_stormFrame) * 3;
        map.setPaintProperty('storm-track', 'circle-radius', pulse);
        _stormFrame++;
    }, 1000);

    // ── DROUGHT MONITOR ──────────────────────────────
    map.addSource('drought', { type: 'geojson', data: '/api/drought' });
    map.addLayer({
        id: 'drought-fill', type: 'fill', source: 'drought',
        layout: { visibility: 'none' },
        paint: {
            'fill-color': [
                'step', ['get', 'DM'],
                '#F5DEB3',  // D0 Abnormally Dry
                1, '#FFD700',  // D1 Moderate
                2, '#FF8C00',  // D2 Severe
                3, '#FF2400',  // D3 Extreme
                4, '#8B0000'   // D4 Exceptional
            ],
            'fill-opacity': 0.45
        }
    });
    map.on('click', 'drought-fill', (e) => {
        const dm = e.features[0].properties.DM;
        const labels = ['D0 Abnormally Dry','D1 Moderate Drought','D2 Severe Drought','D3 Extreme Drought','D4 Exceptional Drought'];
        showPopup('🏜 Drought Conditions', { 'Severity': labels[dm] || 'D'+dm }, e);
    });

    // ── AIR QUALITY (AQI) ────────────────────────────
    map.addSource('air_quality', { type: 'geojson', data: '/api/air_quality' });
    map.addLayer({
        id: 'aqi-circles', type: 'circle', source: 'air_quality',
        layout: { visibility: 'none' },
        paint: {
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
        }
    });
    map.on('click', 'aqi-circles', (e) => {
        const p = e.features[0].properties;
        showPopup('💨 Air Quality — ' + (p.reporting_area || p.state || ''), {
            'AQI':       p.aqi,
            'Category':  p.category,
            'Parameter': p.parameter,
            'State':     p.state
        }, e);
    });
    map.on('mouseenter', 'aqi-circles', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'aqi-circles', () => map.getCanvas().style.cursor = '');

    // ── RIVER FLOOD GAUGES ───────────────────────────
    map.addSource('river_gauges', { type: 'geojson', data: '/api/river_gauges' });
    map.addLayer({
        id: 'river-gauges', type: 'circle', source: 'river_gauges',
        layout: { visibility: 'none' },
        paint: {
            'circle-color': ['get', 'color'],
            'circle-radius': 7,
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 1.5,
            'circle-opacity': 0.9
        }
    });
    map.on('click', 'river-gauges', (e) => {
        const p = e.features[0].properties;
        showPopup('🌊 Flood Gauge — ' + (p.location || p.name || ''), {
            'Status':   (p.status || '').toUpperCase(),
            'Location': p.location || 'N/A',
            'State':    p.state || 'N/A',
            'Details':  p.url ? '<a href="' + p.url + '" target="_blank" style="color:#00B4FF">NOAA Gauge Page</a>' : 'N/A'
        }, e);
    });
    map.on('mouseenter', 'river-gauges', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'river-gauges', () => map.getCanvas().style.cursor = '');

    // ── VOLCANOES ────────────────────────────────────
    map.addSource('volcanoes', { type: 'geojson', data: '/api/volcanoes' });
    map.addLayer({
        id: 'volcano-circles', type: 'circle', source: 'volcanoes',
        layout: { visibility: 'none' },
        paint: {
            'circle-color': ['get', 'color'],
            'circle-radius': 9,
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 2,
            'circle-opacity': 0.95
        }
    });
    map.on('click', 'volcano-circles', (e) => {
        const p = e.features[0].properties;
        showPopup('🌋 ' + (p.name || 'Volcano'), {
            'Alert Level': (p.alert || '').toUpperCase(),
            'Country':     p.country || 'N/A',
            'Source':      'GDACS'
        }, e);
    });
    map.on('mouseenter', 'volcano-circles', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'volcano-circles', () => map.getCanvas().style.cursor = '');

    // ── FEMA DISASTER DECLARATIONS ───────────────────
    map.addSource('fema_disasters', { type: 'geojson', data: '/api/fema_disasters' });
    map.addLayer({
        id: 'fema-disasters', type: 'circle', source: 'fema_disasters',
        layout: { visibility: 'none' },
        paint: {
            'circle-color': '#C084FC',
            'circle-radius': ['interpolate', ['linear'], ['get', 'count'], 1, 8, 10, 18],
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 1.5,
            'circle-opacity': 0.8
        }
    });
    map.on('click', 'fema-disasters', (e) => {
        const p = e.features[0].properties;
        showPopup('🏛 FEMA Disasters — ' + p.state, {
            'Active Declarations': p.count,
            'Types':   p.types || 'N/A',
            'Latest':  p.latest || 'N/A'
        }, e);
    });
    map.on('mouseenter', 'fema-disasters', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'fema-disasters', () => map.getCanvas().style.cursor = '');

    // ── EMERGENCY SHELTERS ───────────────────────────
    map.addSource('shelters', { type: 'geojson', data: '/api/shelters' });
    map.addLayer({
        id: 'shelter-circles', type: 'circle', source: 'shelters',
        layout: { visibility: 'none' },
        paint: {
            'circle-color': '#00FF88',
            'circle-radius': 7,
            'circle-stroke-color': '#FFFFFF',
            'circle-stroke-width': 1.5,
            'circle-opacity': 0.9
        }
    });
    map.on('click', 'shelter-circles', (e) => {
        const p = e.features[0].properties;
        const petCode = (p.pet_accommodations_code || '').toUpperCase();
        const petOk   = petCode && petCode !== 'NONE' && petCode !== 'UNK';
        showPopup('🏠 Emergency Shelter', {
            'Name':    p.shelter_name || 'Open Shelter',
            'Address': (p.address || '') + (p.city ? ', ' + p.city : '') + (p.state ? ', ' + p.state : ''),
            'Status':  p.shelter_status || 'OPEN',
            'Pet Friendly': petOk ? '✅ Yes' : '❌ No',
            'Capacity': p.evacuation_capacity || 'N/A'
        }, e);
    });
    map.on('mouseenter', 'shelter-circles', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'shelter-circles', () => map.getCanvas().style.cursor = '');

    // ── COUNTY HOVER TOOLTIP ─────────────────────────
    const hoverTooltip = document.createElement('div');
    hoverTooltip.id = 'hover-tooltip';
    document.body.appendChild(hoverTooltip);

    let _tooltipLastFid = null;
    let _tooltipRafPending = false;
    map.on('mousemove', 'counties-fill', (e) => {
        // RAF throttle — at most one DOM update per rendered frame (~60fps cap)
        if (_tooltipRafPending) return;
        _tooltipRafPending = true;
        const point = e.point;
        const features = e.features;
        requestAnimationFrame(() => {
            _tooltipRafPending = false;
            if (!features.length) return;
            const p = features[0].properties;
            const fid = p.county + p.state;
            if (fid !== _tooltipLastFid) {
                _tooltipLastFid = fid;
                const pop = Number(p.population).toLocaleString();
                hoverTooltip.innerHTML = `<span style="color:#FF9600;font-weight:700;">${p.county}, ${p.state}</span><br><span style="color:rgba(255,255,255,0.5);">Pop: </span><span style="color:#fff;">${pop}</span>${p.event ? ` <span style="color:#FF8888;">· ${p.event}</span>` : ''}`;
            }
            hoverTooltip.style.display = 'block';
            hoverTooltip.style.left = Math.min(point.x+14, window.innerWidth-280) + 'px';
            hoverTooltip.style.top  = Math.max(point.y-52, 10) + 'px';
        });
    });
    map.on('mouseleave', 'counties-fill', () => { hoverTooltip.style.display = 'none'; _tooltipLastFid = null; });

    // ── NEXRAD AUTO-REFRESH (every 60s for latest radar) ─
    setInterval(() => {
        if (map.getSource('nexrad')) {
            const t = Date.now();
            map.getSource('nexrad').tiles = [
                `https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS=nexrad-n0r&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE&HEIGHT=256&WIDTH=256&SRS=EPSG:3857&BBOX={bbox-epsg-3857}&_t=${t}`
            ];
            try {
                map.style.sourceCaches['nexrad'].clearTiles();
                map.style.sourceCaches['nexrad'].update(map.transform);
            } catch(e) {}
        }
    }, 60000);

    // ── SHARED SOURCE CACHE ───────────────────────────────────────────────────────
    // All GeoJSON sources ship hidden (visibility:'none'), and Mapbox defers URL
    // fetches for hidden-only sources. Kick off explicit fetches in parallel so
    // data is already cached by the time the user toggles a layer on.
    window._srcPromises = window._srcPromises || {};
    window._srcData = window._srcData || {};
    function fetchSource(srcName, force) {
        if (!force && window._srcPromises[srcName]) return window._srcPromises[srcName];
        const p = fetch('/api/' + srcName + '?t=' + Date.now())
            .then(r => r.json())
            .then(d => {
                window._srcData[srcName] = d;
                if (map.getSource(srcName) && map.getSource(srcName).setData) {
                    map.getSource(srcName).setData(d);
                }
                // Notify the layer panel (or anything else) that fresh data
                // for this source landed — used to refresh the count badges.
                try {
                    document.dispatchEvent(new CustomEvent('hazardSource', {
                        detail: { srcName, data: d }
                    }));
                } catch (e) { /* IE etc. — not a concern here */ }
                return d;
            })
            .catch(() => { delete window._srcPromises[srcName]; return null; });
        window._srcPromises[srcName] = p;
        return p;
    }
    [
        'warnings','spc','earthquakes','fires','counties','lightning',
        'fire_perimeters','storms','fema_disasters','river_gauges',
        'volcanoes','drought','shelters','air_quality'
    ].forEach(src => fetchSource(src));
    // Populate Top Impacted panel once counties data lands on first load
    fetchSource('counties').then(d => { try { renderTopImpacted(d); } catch(e) {} });

    // ── LAYER TOGGLE BUTTONS (rendered into left sidebar) ─────────────────────────
    const sidebarBody = document.getElementById('sidebar-layers-body');
    if (sidebarBody) sidebarBody.innerHTML = '';
    const toggleContainer = sidebarBody || document.body;
    // Backward-compat id so legacy references don't break
    if (sidebarBody) sidebarBody.id = 'sidebar-layers-body';

    // Layer categories with their toggles
    const LAYER_GROUPS = [
        { name: 'WEATHER', icon: 'cyclone', toggles: [
            ['⚠ Active Warnings', ['warnings-fill','warnings-outline'], true],
            ['⛈ SPC Outlook', ['spc-fill','spc-outline'], false],
            ['⚡ Storm Reports', 'lightning-strikes', false],
            ['🌀 Hurricanes', ['storm-cone','storm-cone-outline','storm-track'], false],
            ['📡 NEXRAD Radar', 'nexrad-layer', false],
            ['🛰 GOES Infrared', 'goes-ir-layer', false],
        ]},
        { name: 'FIRE & SEISMIC', icon: 'local_fire_department', toggles: [
            ['🔥 Fire Detections', 'fire-points', false],
            ['🔥 Fire Perimeters', ['fire-perimeter-fill','fire-perimeter-outline'], false],
            ['🔴 Earthquakes', 'eq-circles', false],
            ['🌋 Volcanoes', 'volcano-circles', false],
        ]},
        { name: 'WATER & AIR', icon: 'water_drop', toggles: [
            ['🌊 Flood Gauges', 'river-gauges', false],
            ['💨 Air Quality', 'aqi-circles', false],
            ['🏜 Drought', 'drought-fill', false],
        ]},
        { name: 'RESPONSE', icon: 'shield', toggles: [
            ['🗺 Affected Counties', ['counties-fill','counties-outline'], false],
            ['🏥 Hospitals', 'infra-normal', false],
            ['⚠ At-Risk Infra', 'infra-at-risk', false],
            ['🏠 Shelters', 'shelter-circles', false],
            ['🏛 FEMA Disasters', 'fema-disasters', false],
        ]},
    ];

    // Inline legends keyed by primary layer id. Only layers with meaningful color
    // vocabularies have entries — others show the toggle without a caret.
    const LAYER_LEGENDS = {
        'warnings-fill': [
            {c:'#FF0000', l:'Tornado'}, {c:'#FF9900', l:'Severe T-Storm'},
            {c:'#00BFFF', l:'Flash Flood'}, {c:'#FF6600', l:'Hurricane'},
            {c:'#AAAAFF', l:'Winter'}, {c:'#FF4500', l:'Fire Weather'},
            {c:'#FFFF00', l:'Other'}
        ],
        'spc-fill': [
            {c:'#FF00FF', l:'High'}, {c:'#FF0000', l:'Moderate'},
            {c:'#FF9900', l:'Enhanced'}, {c:'#FFFF00', l:'Slight'},
            {c:'#009000', l:'Marginal'}, {c:'#76FF7A', l:'General TS'}
        ],
        'fire-points': [
            {c:'#FF0000', l:'Intense (FRP >100)'},
            {c:'#FF4500', l:'Active (20-100)'},
            {c:'#FF8C00', l:'Detection (<20)'}
        ],
        'eq-circles': [
            {c:'#FF0000', l:'M5+'}, {c:'#FF9900', l:'M4-5'}, {c:'#FFFF00', l:'M2.5-4'}
        ],
        'aqi-circles': [
            {c:'#00E400', l:'Good'}, {c:'#FFFF00', l:'Moderate'},
            {c:'#FF7E00', l:'Unhealthy (Sens.)'}, {c:'#FF0000', l:'Unhealthy'},
            {c:'#8F3F97', l:'Very Unhealthy'}, {c:'#7E0023', l:'Hazardous'}
        ],
        'drought-fill': [
            {c:'#F5DEB3', l:'D0 Abnormal'}, {c:'#FFD700', l:'D1 Moderate'},
            {c:'#FF8C00', l:'D2 Severe'}, {c:'#FF2400', l:'D3 Extreme'},
            {c:'#8B0000', l:'D4 Exceptional'}
        ],
        'river-gauges': [
            {c:'#FF4444', l:'Major Flood'}, {c:'#FF8800', l:'Moderate'},
            {c:'#FFCC00', l:'Minor'}, {c:'#58BFFF', l:'Action'}
        ],
        'infra-normal': [
            {c:'#58BFFF', l:'Hospitals / Fire / Schools'}
        ],
        'infra-at-risk': [
            {c:'#FF4444', l:'Inside warning area'}
        ]
    };

    // Per-layer legend open/closed state, persisted.
    let _legendState = {};
    try { _legendState = JSON.parse(localStorage.getItem('nhm_legend_open') || '{}'); } catch(e) {}
    const saveLegendState = () => {
        try { localStorage.setItem('nhm_legend_open', JSON.stringify(_legendState)); } catch(e) {}
    };

    function makeToggle(label, layerId, defaultOn) {
        const wrap = document.createElement('div');
        wrap.className = 'layer-toggle-wrap';
        const btn = document.createElement('button');
        btn.className = 'layer-toggle';
        const ids = Array.isArray(layerId) ? layerId : [layerId];
        const primaryId = ids[0];
        const legend = LAYER_LEGENDS[primaryId] || null;
        let legendOpen = !!_legendState[primaryId];

        const legendRow = document.createElement('div');
        legendRow.className = 'layer-legend-row' + (legendOpen ? ' open' : '');
        if (legend) {
            legendRow.innerHTML = legend.map(e =>
                `<span class="layer-legend-item"><span class="layer-legend-swatch" style="background:${e.c};"></span>${e.l}</span>`
            ).join('');
        }

        let on = defaultOn;
        let count = null;  // null = unknown, number = feature count
        let loading = false;
        const render = () => {
            let countTxt = '';
            if (loading) {
                countTxt = `<span class="layer-toggle-count" style="color:rgba(88,191,255,0.6);">···</span>`;
            } else if (count !== null) {
                const isEmpty = count === 0;
                const cls = isEmpty ? 'layer-toggle-count empty' : 'layer-toggle-count';
                const txt = isEmpty ? 'empty' : count;
                countTxt = `<span class="${cls}" style="color:${isEmpty ? 'rgba(255,255,255,0.35)' : '#58bfff'};">${txt}</span>`;
            }
            const dotCls = loading ? 'layer-toggle-dot loading' : 'layer-toggle-dot';
            const dotStyle = loading
                ? ''
                : `style="background:${on ? '#58bfff' : 'transparent'};border-color:${on ? '#58bfff' : 'rgba(255,255,255,0.25)'};"`;
            const caretHtml = legend
                ? `<span class="layer-legend-caret${legendOpen ? ' open' : ''}" data-role="legend-caret" title="Toggle legend">▾</span>`
                : '';
            btn.innerHTML = `
                <span class="${dotCls}" ${dotStyle}></span>
                <span class="layer-toggle-label" style="color:${on ? '#dde9fb' : 'rgba(255,255,255,0.45)'};">${label}</span>
                ${countTxt}
                ${caretHtml}
            `;
        };
        render();

        // Seed count from cache/pending promise so the badge populates on page load,
        // even before the user clicks.
        (function seedCount() {
            let srcName = null;
            for (const id of ids) {
                const s = map.getLayer(id)?.source;
                if (s && map.getSource(s) && map.getSource(s).setData) { srcName = s; break; }
            }
            if (!srcName) return;
            if (window._srcData[srcName]) {
                const d = window._srcData[srcName];
                count = (d && d.features) ? d.features.length : 0;
                render();
            } else if (window._srcPromises[srcName]) {
                window._srcPromises[srcName].then(d => {
                    count = (d && d.features) ? d.features.length : 0;
                    render();
                });
            }
        })();

        btn.onclick = (e) => {
            // Caret click toggles the inline legend row, not the layer itself.
            if (e.target && e.target.dataset && e.target.dataset.role === 'legend-caret') {
                e.stopPropagation();
                legendOpen = !legendOpen;
                _legendState[primaryId] = legendOpen;
                saveLegendState();
                legendRow.classList.toggle('open', legendOpen);
                render();
                return;
            }

            on = !on;
            ids.forEach(id => {
                if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
            });
            if (!on) { render(); return; }

            // Determine source to refresh (first one; all ids in a toggle share a source).
            let srcName = null;
            for (const id of ids) {
                const s = map.getLayer(id)?.source;
                if (s && map.getSource(s) && map.getSource(s).setData) { srcName = s; break; }
            }
            if (!srcName) { render(); return; }

            // If cached, use immediately; otherwise show spinner until promise resolves.
            if (window._srcData[srcName] && _searchContext === null) {
                const d = window._srcData[srcName];
                if (map.getSource(srcName)) map.getSource(srcName).setData(d);
                count = (d && d.features) ? d.features.length : 0;
                render();
                return;
            }
            if (_searchContext !== null) { render(); return; }
            loading = true; render();
            fetchSource(srcName).then(d => {
                loading = false;
                count = (d && d.features) ? d.features.length : 0;
                render();
            }).catch(() => { loading = false; count = 0; render(); });
        };
        wrap.appendChild(btn);
        if (legend) wrap.appendChild(legendRow);
        return wrap;
    }

    // ── COLLAPSIBLE LAYER GROUPS ─────────────────────────
    const _groupStateKey = 'nhm_group_open';
    let _groupState = {};
    try { _groupState = JSON.parse(localStorage.getItem(_groupStateKey) || '{}'); } catch(e) {}
    const saveGroupState = () => {
        try { localStorage.setItem(_groupStateKey, JSON.stringify(_groupState)); } catch(e) {}
    };

    LAYER_GROUPS.forEach((group, gi) => {
        const section = document.createElement('div');
        section.className = 'layer-group';
        if (gi > 0) section.style.marginTop = '8px';

        const isOpen = _groupState[group.name] !== undefined ? _groupState[group.name] : (gi === 0);
        const header = document.createElement('div');
        header.className = 'layer-group-header';
        header.style.cursor = 'pointer';
        const renderHeader = (open) => {
            header.innerHTML = `
                <span class="material-symbols-outlined" style="font-size:14px;color:#58bfff;">${group.icon}</span>
                <span style="flex:1;">${group.name}</span>
                <span class="layer-group-arrow" style="font-size:10px;transform:rotate(${open ? 90 : 0}deg);transition:transform 0.18s;">▶</span>
            `;
        };
        renderHeader(isOpen);
        section.appendChild(header);

        const list = document.createElement('div');
        list.className = 'layer-group-list';
        list.style.display = isOpen ? 'flex' : 'none';
        group.toggles.forEach(([label, ids, def]) => list.appendChild(makeToggle(label, ids, def)));
        section.appendChild(list);

        header.onclick = () => {
            const nowOpen = list.style.display === 'none';
            list.style.display = nowOpen ? 'flex' : 'none';
            renderHeader(nowOpen);
            _groupState[group.name] = nowOpen;
            saveGroupState();
        };

        toggleContainer.appendChild(section);
    });

    // ── BASEMAP SWITCHER (collapsible) ───────────────────
    const basemapSection = document.createElement('div');
    basemapSection.className = 'layer-group';
    basemapSection.style.cssText = 'margin-top:12px;border-top:1px solid rgba(88,191,255,0.15);padding-top:10px;';
    const bmOpen = _groupState['BASEMAP'] !== undefined ? _groupState['BASEMAP'] : false;
    const bmHeader = document.createElement('div');
    bmHeader.className = 'layer-group-header';
    bmHeader.style.cursor = 'pointer';
    const renderBmHeader = (open) => {
        bmHeader.innerHTML = `
            <span class="material-symbols-outlined" style="font-size:14px;color:#58bfff;">map</span>
            <span style="flex:1;">BASEMAP</span>
            <span class="layer-group-arrow" style="font-size:10px;transform:rotate(${open ? 90 : 0}deg);transition:transform 0.18s;">▶</span>
        `;
    };
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
    bmHeader.onclick = () => {
        const nowOpen = bmGrid.style.display === 'none';
        bmGrid.style.display = nowOpen ? 'grid' : 'none';
        renderBmHeader(nowOpen);
        _groupState['BASEMAP'] = nowOpen;
        saveGroupState();
    };
    bmGrid.querySelectorAll('.bm-btn').forEach(btn => {
        btn.onclick = () => {
            const styleUrl = btn.getAttribute('data-style');
            map.setStyle(styleUrl);
            map.once('style.load', setupLayers);
            bmGrid.querySelectorAll('.bm-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        };
    });
    toggleContainer.appendChild(basemapSection);

    // ── LOAD STATS WITH RETRY ────────────────────────
    // Global hazard data for stat card fly-to
    let _latestWarnings    = null;
    let _latestEarthquakes = null;
    let _latestFires       = null;
    let _latestStorms      = null;
    let _dataLoaded        = false;
    let _prevSummary       = null;

    // ── 3D Superman flyover ──────────────────────────────────────────────
    // Terrain + 3D buildings + sky/fog are set up lazily on first flyover so
    // the default 2D view isn't paying the per-frame 3D render cost.
    let _threeDReady = false;
    function ensureThreeD() {
        if (_threeDReady) return;
        _threeDReady = true;
        try {
            if (!map.getSource('mapbox-dem')) {
                map.addSource('mapbox-dem', {
                    type: 'raster-dem',
                    url: 'mapbox://mapbox.mapbox-terrain-dem-v1',
                    tileSize: 512, maxzoom: 14
                });
            }
            map.setTerrain({ source: 'mapbox-dem', exaggeration: 1.3 });
            map.setFog({
                'range': [0.8, 8],
                'color': '#0b1b2a',
                'horizon-blend': 0.2,
                'space-color': '#030712',
                'star-intensity': 0.5
            });
            if (!map.getLayer('sky')) {
                map.addLayer({
                    id: 'sky', type: 'sky',
                    paint: { 'sky-type': 'atmosphere', 'sky-atmosphere-sun-intensity': 12 }
                });
            }
            if (!map.getLayer('buildings-3d')) {
                // Insert 3D buildings beneath label symbols so street names still read.
                const layers = map.getStyle().layers || [];
                const labelLayer = layers.find(l => l.type === 'symbol' && l.layout && l.layout['text-field']);
                map.addLayer({
                    id: 'buildings-3d', type: 'fill-extrusion',
                    source: 'composite', 'source-layer': 'building',
                    minzoom: 13,
                    filter: ['==', ['get', 'extrude'], 'true'],
                    paint: {
                        'fill-extrusion-color': [
                            'interpolate', ['linear'], ['get', 'height'],
                            0, '#243447', 50, '#38546f', 150, '#5eb3ff'
                        ],
                        'fill-extrusion-height': ['get', 'height'],
                        'fill-extrusion-base':   ['get', 'min_height'],
                        'fill-extrusion-opacity': 0.85
                    }
                }, labelLayer ? labelLayer.id : undefined);
            }
        } catch(err) { console.warn('3D setup failed:', err); }
    }

    function dismissThreatCard() {
        const host = document.getElementById('threat-card-host');
        if (host) host.innerHTML = '';
    }

    function showThreatCard(props) {
        const host = document.getElementById('threat-card-host');
        if (!host) return;
        const rank = Number(props.severity_rank || 0);
        const pop  = Number(props.population || 0).toLocaleString();
        const ev   = String(props.event || 'ACTIVE ALERT').toUpperCase();
        const lvl  = props.sig || 'N/A';
        const wct  = Number(props.warning_count || 1);
        const fips = props.fips || 'N/A';
        const title = (props.county || 'Unknown') + (props.state ? ', ' + props.state : '');
        const multiRow = wct > 1
            ? `<div class="tc-row"><span class="tc-label">Active Warnings</span><span class="tc-value hot">${wct}</span></div>` : '';
        host.innerHTML = `
            <div class="threat-card sev-${rank}" style="position:relative;">
                <button class="tc-close" aria-label="Close">×</button>
                <div class="tc-banner">◉ ${ev}</div>
                <div class="tc-body">
                    <div class="tc-title">${title}</div>
                    <div class="tc-sub">Population ${pop}</div>
                    <div class="tc-row"><span class="tc-label">Alert Level</span><span class="tc-value">${lvl}</span></div>
                    ${multiRow}
                    <div class="tc-row"><span class="tc-label">FIPS</span><span class="tc-value">${fips}</span></div>
                </div>
                <div class="tc-actions">
                    <button class="tc-btn" data-action="detail">County Detail</button>
                    <button class="tc-btn" data-action="nws">NWS Alert</button>
                    <button class="tc-btn" data-action="ai">AI Brief</button>
                </div>
            </div>`;
        host.querySelector('.tc-close').addEventListener('click', dismissThreatCard);
        host.querySelectorAll('.tc-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const a = btn.getAttribute('data-action');
                if (a === 'nws') {
                    window.open('https://alerts.weather.gov/search?q=' + encodeURIComponent((props.county||'') + ' ' + (props.state||'')), '_blank');
                } else if (a === 'detail') {
                    const slug = String((props.county||'') + 'county' + (props.state||'')).toLowerCase().replace(/\\s+/g,'');
                    window.open('https://www.census.gov/quickfacts/fact/table/' + encodeURIComponent(slug), '_blank');
                } else if (a === 'ai') {
                    if (typeof generateSitrep === 'function') generateSitrep();
                    else if (typeof window.generateSitrep === 'function') window.generateSitrep();
                    else alert('AI briefing is available from the header button.');
                }
            });
        });
    }

    // Two-stage cinematic flyover. Stage 1 glides in on a curve; stage 2 swoops
    // down to street level with pitch/bearing so 3D buildings become visible.
    function supermanFlyTo(feat, propsOverride) {
        if (!feat) return;
        ensureThreeD();
        let center;
        try { center = turf.centroid(feat).geometry.coordinates; }
        catch(e) { return; }
        // Reveal the affected-counties layer so the target county stays highlighted.
        if (map.getLayer('counties-fill') && map.getLayoutProperty('counties-fill','visibility') !== 'visible') {
            map.setLayoutProperty('counties-fill',   'visibility', 'visible');
            map.setLayoutProperty('counties-outline','visibility', 'visible');
        }
        dismissThreatCard();
        // Stage 1 — approach
        map.flyTo({
            center, zoom: 9.8, pitch: 55, bearing: -18,
            speed: 0.9, curve: 1.6, essential: true
        });
        // Stage 2 — swoop to street level
        setTimeout(() => {
            map.easeTo({ center, zoom: 12.5, pitch: 68, bearing: -22, duration: 4200 });
        }, 1600);
        // Reveal the threat card shortly before the swoop settles
        const props = propsOverride || feat.properties || {};
        setTimeout(() => showThreatCard(props), 5400);
    }

    // Render the "Top Impacted" sidebar panel from /api/counties GeoJSON.
    // Sort by (severity_rank DESC, population DESC) so all Warnings rank above
    // all Watches regardless of population, biggest population wins within tier.
    function renderTopImpacted(geojson) {
        const el = document.getElementById('top-impacted-list');
        if (!el) return;
        const feats = (geojson && geojson.features) || [];
        if (!feats.length) {
            el.innerHTML = '<div style="color:#64748b;font-size:10px;letter-spacing:1px;">NO ACTIVE COUNTIES</div>';
            return;
        }
        const sorted = feats.slice().sort((a, b) => {
            const pa = a.properties || {}, pb = b.properties || {};
            return (pb.severity_rank - pa.severity_rank) || (pb.population - pa.population);
        }).slice(0, 5);
        const sigColor = { 3: '#FF2222', 2: '#FF8800', 1: '#FFCC00', 0: '#888888' };
        el.innerHTML = sorted.map(f => {
            const p = f.properties || {};
            const popStr = Number(p.population || 0).toLocaleString();
            const badge  = (p.warning_count > 1)
                ? `<span style="margin-left:6px;font-size:9px;padding:1px 5px;background:rgba(255,136,0,0.2);border:1px solid rgba(255,136,0,0.4);color:#FF8800;border-radius:2px;">×${p.warning_count}</span>`
                : '';
            const dot = sigColor[p.severity_rank] || '#888';
            return `<div class="impact-row" data-fips="${p.fips}" style="cursor:pointer;padding:6px 8px;border:1px solid rgba(88,191,255,0.08);background:rgba(88,191,255,0.03);transition:background 120ms ease;">
                <div style="display:flex;align-items:center;gap:6px;">
                    <span style="display:inline-block;width:6px;height:6px;background:${dot};flex-shrink:0;"></span>
                    <span style="font-size:11px;color:#dde9fb;font-weight:600;">${p.county}, ${p.state}</span>
                    ${badge}
                </div>
                <div style="font-size:9px;color:#a0acbd;letter-spacing:0.5px;margin-top:3px;margin-left:12px;">${(p.event || 'N/A').toUpperCase()} · ${popStr}</div>
            </div>`;
        }).join('');
        // Wire click-to-fly
        el.querySelectorAll('.impact-row').forEach(row => {
            row.addEventListener('mouseenter', () => row.style.background = 'rgba(88,191,255,0.1)');
            row.addEventListener('mouseleave', () => row.style.background = 'rgba(88,191,255,0.03)');
            row.addEventListener('click', () => {
                const fips = row.getAttribute('data-fips');
                const feat = (window._srcData.counties?.features || []).find(f => (f.properties || {}).fips === fips);
                if (!feat) return;
                // Cinematic 3D flyover + glass threat-card popup
                try { supermanFlyTo(feat, feat.properties || {}); } catch(e) { console.warn('supermanFlyTo:', e); }
            });
        });
    }

    // ── Layer panel ──────────────────────────────────────────────────
    // Grouped toggle list rendered into #layer-list. Each item maps a
    // user-facing label to one or more real Mapbox layer IDs (verified
    // by grepping the addLayer calls above). setLayer(key, on) flips
    // visibility and updates every counter (panel head + rail badge).
    // (Named CC_* to avoid colliding with legacy CC_LAYER_GROUPS in the
    //  buildSidebar() block higher up — that code is dead in the new
    //  shell but its declarations are still in scope.)
    const CC_LAYER_GROUPS = [
        { group: 'ATMOSPHERIC', items: [
            { key: 'nws',       label: 'NWS Warnings',     layerIds: ['warnings-fill','warnings-outline'], defaultOn: true  },
            { key: 'spc',       label: 'SPC Outlook',      layerIds: ['spc-fill','spc-outline'],           defaultOn: false },
            { key: 'nhc',       label: 'Hurricane Track',  layerIds: ['storm-cone','storm-cone-outline','storm-track'], defaultOn: false },
            { key: 'lightning', label: 'Lightning',        layerIds: ['lightning-strikes'],                defaultOn: false },
            { key: 'nexrad',    label: 'Radar (NEXRAD)',   layerIds: ['nexrad-layer'],                     defaultOn: false },
            { key: 'goes',      label: 'Satellite IR',     layerIds: ['goes-ir-layer'],                    defaultOn: false },
        ]},
        { group: 'GEOLOGICAL', items: [
            { key: 'usgs',      label: 'Earthquakes',      layerIds: ['eq-circles'],                       defaultOn: false },
            { key: 'volcanoes', label: 'Volcanoes',        layerIds: ['volcano-circles'],                  defaultOn: false },
        ]},
        { group: 'WILDFIRE', items: [
            { key: 'firms',     label: 'Fire Detections',  layerIds: ['fire-points'],                      defaultOn: false },
            { key: 'perim',     label: 'Fire Perimeters',  layerIds: ['fire-perimeter-fill','fire-perimeter-outline'], defaultOn: false },
        ]},
        { group: 'HYDROLOGICAL', items: [
            { key: 'river',     label: 'River Gauges',     layerIds: ['river-gauges'],                     defaultOn: false },
            { key: 'drought',   label: 'Drought',          layerIds: ['drought-fill'],                     defaultOn: false },
        ]},
        { group: 'RESPONSE', items: [
            { key: 'counties',  label: 'Affected Counties', layerIds: ['counties-fill','counties-outline'], defaultOn: false },
            { key: 'fema',      label: 'FEMA Disasters',   layerIds: ['fema-disasters'],                   defaultOn: false },
            { key: 'shelters',  label: 'Open Shelters',    layerIds: ['shelter-circles'],                  defaultOn: false },
            { key: 'infra',     label: 'Infrastructure',   layerIds: ['infra-at-risk','infra-normal','infra-labels'], defaultOn: false },
        ]},
        { group: 'ENVIRONMENTAL', items: [
            { key: 'airnow',    label: 'Air Quality',      layerIds: ['aqi-circles'],                      defaultOn: false },
        ]},
    ];
    const CC_ALL_ITEMS = CC_LAYER_GROUPS.flatMap(g => g.items);

    function _setLayer(key, on) {
        const item = CC_ALL_ITEMS.find(i => i.key === key);
        if (!item) return;
        for (const id of item.layerIds) {
            if (!map.getLayer(id)) continue;
            map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
        }
        // Refresh panel UI for this row + the counters.
        const row = document.querySelector(`.layer-row[data-key="${key}"]`);
        if (row && typeof row.classList !== 'undefined') {
            row.classList.toggle('on', !!on);
            const t = row.querySelector('.layer-toggle');
            if (t && typeof t.classList !== 'undefined') t.classList.toggle('on', !!on);
        }
        _refreshLayerCounts();
    }

    function _refreshLayerCounts() {
        let on = 0;
        for (const item of CC_ALL_ITEMS) {
            const id = item.layerIds.find(x => map.getLayer(x));
            if (id && map.getLayoutProperty(id, 'visibility') !== 'none') on++;
        }
        const total = CC_ALL_ITEMS.length;
        const onEl    = document.getElementById('layer-on-count');
        const totalEl = document.getElementById('layer-total-count');
        const railEl  = document.getElementById('layer-count-val');
        if (onEl    && typeof onEl.textContent    !== 'undefined') onEl.textContent    = on;
        if (totalEl && typeof totalEl.textContent !== 'undefined') totalEl.textContent = total;
        if (railEl  && typeof railEl.textContent  !== 'undefined') railEl.textContent  = on;
    }

    function _renderLayerPanel() {
        const host = document.getElementById('layer-list');
        if (!host || typeof host.innerHTML === 'undefined') return;
        host.innerHTML = CC_LAYER_GROUPS.map(g => `
            <div class="layer-group">${g.group}</div>
            ${g.items.map(it => `
                <div class="layer-row${it.defaultOn ? ' on' : ''}" data-key="${it.key}">
                    <span class="layer-toggle${it.defaultOn ? ' on' : ''}"><span class="dot"></span></span>
                    <span class="layer-label">${it.label}</span>
                    <span class="layer-count" data-count-for="${it.key}"></span>
                </div>
            `).join('')}
        `).join('');
        // Wire clicks
        host.querySelectorAll('.layer-row').forEach(row => {
            row.addEventListener('click', () => {
                const key = row.getAttribute('data-key');
                const item = CC_ALL_ITEMS.find(i => i.key === key);
                if (!item) return;
                const id = item.layerIds.find(x => map.getLayer(x));
                const cur = id ? map.getLayoutProperty(id, 'visibility') : 'none';
                const turningOn = cur === 'none';
                _setLayer(key, turningOn);
                // Lazy-fetch sources that aren't part of the auto-refresh loop.
                // Infrastructure hits Overpass and can take 15-20s, so we only
                // load it the first time the user actually toggles it on.
                if (turningOn && key === 'infra' && !window._srcData['infrastructure']) {
                    fetchSource('infrastructure', true);
                }
            });
        });
        _refreshLayerCounts();
    }

    // /api source name → layer-panel key (count-badge update routing)
    const SOURCE_TO_KEY = {
        warnings:        'nws',
        spc:             'spc',
        earthquakes:     'usgs',
        fires:           'firms',
        counties:        'counties',
        lightning:       'lightning',
        fire_perimeters: 'perim',
        storms:          'nhc',
        fema_disasters:  'fema',
        river_gauges:    'river',
        volcanoes:       'volcanoes',
        drought:         'drought',
        shelters:        'shelters',
        air_quality:     'airnow',
        infrastructure:  'infra',
    };

    document.addEventListener('hazardSource', (e) => {
        const detail = e && e.detail; if (!detail) return;
        const key = SOURCE_TO_KEY[detail.srcName];
        if (!key) return;
        const span = document.querySelector(`[data-count-for="${key}"]`);
        if (!span || typeof span.textContent === 'undefined') return;
        const d = detail.data;
        // Most sources are GeoJSON FeatureCollections; storms is a list.
        const n = (d && Array.isArray(d.features)) ? d.features.length
                : Array.isArray(d)                  ? d.length
                : 0;
        span.textContent = n ? `n=${n}` : '';
    });

    // Render the panel after layers are in place. Call at the end of
    // setupLayers so getLayer() resolves; map.on('idle') will keep the
    // rail badge fresh when visibilities change from elsewhere.
    _renderLayerPanel();

    // ── Command Center bindings ───────────────────────────────────────
    // Populates: KPI strip ([data-kpi]), priority queue (#queue-list),
    // live feed (#feed-list), ops clock + sync subline, and refreshes
    // every Mapbox source via the existing fetchSource() pipeline.

    const SEV_FOR_RANK = { 3: 'extreme', 2: 'severe', 1: 'moderate', 0: 'info' };

    function _fmtPop(n) {
        if (!n) return '0';
        if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
        if (n >= 1e3) return (n / 1e3).toFixed(0) + 'K';
        return String(n);
    }

    function _setKpi(kpi, value, opts) {
        const tile = document.querySelector(`.kpi[data-kpi="${kpi}"]`);
        if (!tile || tile === undefined) return;
        const val = tile.querySelector('.kpi-val');
        const dlt = tile.querySelector('.kpi-delta');
        if (val) val.textContent = value;
        if (dlt && opts && typeof opts.delta === 'number') {
            const d = opts.delta;
            if (d === 0) { dlt.textContent = ''; dlt.className = 'kpi-delta mono tnum'; }
            else {
                dlt.textContent = (d > 0 ? '+' : '') + d;
                dlt.className   = 'kpi-delta mono tnum ' + (d > 0 ? 'up' : 'down');
            }
        }
    }

    function _renderQueue(affected) {
        const host = document.getElementById('queue-list');
        if (!host || host === undefined || !host.innerHTML) {
            // host may be the dead-element proxy; bail if so.
            if (!host || typeof host.innerHTML === 'undefined') return;
        }
        const meta = document.getElementById('queue-meta');
        if (!Array.isArray(affected) || !affected.length) {
            host.innerHTML = '<div class="empty-state">No affected counties.</div>';
            if (meta) meta.textContent = '0 events';
            return;
        }
        const sorted = affected.slice().sort((a, b) =>
            (b.severity_rank - a.severity_rank) || (b.population - a.population)
        ).slice(0, 12);
        if (meta) meta.textContent = `${affected.length} events · click to fly`;
        host.innerHTML = sorted.map(r => {
            const sev = SEV_FOR_RANK[r.severity_rank] || 'info';
            const evt = String(r.event || 'Active alert');
            const area = `${r.county || '?'}, ${r.state || ''}`.trim();
            const pop = _fmtPop(r.population || 0);
            const wct = (r.warning_count > 1) ? ` · ×${r.warning_count}` : '';
            return `<div class="queue-row" data-fips="${r.fips || ''}">
                <div class="queue-bar sev-${sev}"></div>
                <div class="queue-main">
                    <div class="queue-name">${evt}</div>
                    <div class="queue-meta">${area}${wct}</div>
                </div>
                <div class="queue-pop">
                    <div class="queue-pop-val">${pop}</div>
                    <div class="queue-pop-lbl">POP</div>
                </div>
            </div>`;
        }).join('');
        // Click → flyTo county centroid (uses /api/counties cached data).
        host.querySelectorAll('.queue-row').forEach(row => {
            row.addEventListener('click', () => {
                const fips = row.getAttribute('data-fips');
                const feats = (window._srcData && window._srcData.counties && window._srcData.counties.features) || [];
                const feat = feats.find(f => (f.properties || {}).fips === fips);
                host.querySelectorAll('.queue-row.active').forEach(r => r.classList.remove('active'));
                row.classList.add('active');
                if (!feat) return;
                try {
                    const c = turf.centroid(feat).geometry.coordinates;
                    map.flyTo({ center: c, zoom: 7, speed: 1.2 });
                } catch (e) { /* swallow */ }
            });
        });
    }

    function _renderFeed(events) {
        const host = document.getElementById('feed-list');
        if (!host || typeof host.innerHTML === 'undefined') return;
        if (!Array.isArray(events) || !events.length) {
            host.innerHTML = '<div class="empty-state">No recent events.</div>';
            return;
        }
        host.innerHTML = events.slice(0, 8).map(e => {
            const t = (e.time || '').slice(11, 19) || '—';
            const src = (e.source || '?').toUpperCase();
            const sev = e.sev || 'info';
            return `<div class="feed-row">
                <span class="feed-time">${t}</span>
                <span class="feed-src sev-${sev}">${src}</span>
                <span class="feed-msg">${(e.text || '').replace(/</g, '&lt;')}</span>
            </div>`;
        }).join('');
    }

    function _renderClock(lastUpdate) {
        const clock = document.getElementById('ops-clock');
        const sub   = document.getElementById('ops-clock-sub');
        const dot   = document.getElementById('status-dot');
        const status = document.getElementById('rail-status');
        const now = new Date();
        if (clock) clock.textContent = now.toTimeString().slice(0, 8);
        if (sub) {
            if (lastUpdate && lastUpdate !== 'Never') {
                const parsed = new Date(String(lastUpdate).replace(' ', 'T'));
                const ageMin = isNaN(parsed) ? null : Math.floor((Date.now() - parsed.getTime()) / 60000);
                const next   = ageMin === null ? '—' : Math.max(0, 30 - ageMin) + 'm';
                sub.textContent = `Last sync ${ageMin === null ? '—' : ageMin + 'm ago'} · Next in ${next}`;
                if (dot && dot.style && status) {
                    if (ageMin === null || ageMin > 60) {
                        dot.classList.add('alert'); status.textContent = 'STALE';
                    } else {
                        dot.classList.remove('alert');
                        status.textContent = ageMin > 30 ? 'DELAYED' : 'NOMINAL';
                    }
                }
            } else {
                sub.textContent = 'Acquiring live data…';
            }
        }
    }

    // (_prevSummary + _dataLoaded already declared higher in this scope)
    // Source list refreshed every loadData tick (Infrastructure stays out —
    // it's lazy on first toggle because Overpass is slow).
    const REFRESH_SOURCES = ['warnings','spc','earthquakes','fires','counties',
        'lightning','fire_perimeters','storms','fema_disasters','river_gauges',
        'volcanoes','drought','shelters','air_quality'];

    function _refreshAllSources() {
        if (typeof fetchSource !== 'function') return;
        if (_searchContext !== null) return;  // buffer search active — don't clobber filtered data
        REFRESH_SOURCES.forEach(src => fetchSource(src, true));
    }

    function loadData() {
        fetch('/api/summary').then(r => r.json()).then(data => {
            const s = data.summary || {};
            const prev = _prevSummary || {};
            const delta = (a, b) => (a || 0) - (b || 0);

            _setKpi('warnings',   s.warnings_count   || 0, { delta: delta(s.warnings_count,   prev.warnings_count) });
            _setKpi('severe',     s.spc_zones        || 0, { delta: delta(s.spc_zones,        prev.spc_zones) });
            _setKpi('hurricanes', s.active_storms    || 0);
            _setKpi('quakes',     s.earthquakes      || 0, { delta: delta(s.earthquakes,      prev.earthquakes) });
            _setKpi('fires',      s.wildfires        || 0, { delta: delta(s.wildfires,        prev.wildfires) });
            _setKpi('gauges',     s.river_gauges     || 0);
            _setKpi('population', _fmtPop(s.total_population || 0));
            _setKpi('shelters',   s.shelters         || 0);

            _prevSummary = Object.assign({}, s);
            _renderClock(data.last_update);
            _renderQueue(s.affected_counties || []);

            // Always refresh sources — drop the map.loaded guard. fetchSource
            // is safe to call before sources are added (setData skips when the
            // source isn't present); waiting for map.loaded was making us miss
            // the post-cold-start window.
            _refreshAllSources();

            // Stale-data catch-up: if /api/summary says we have N affected
            // counties but the cached counties source has fewer, re-fetch
            // counties on the next tick. (The 5-min interval is too long to
            // wait when the user just hard-refreshed during run_update.)
            const want = (s.affected_counties || []).length;
            const have = window._srcData?.counties?.features?.length ?? 0;
            if (want > 0 && have < want) {
                setTimeout(() => fetchSource('counties', true), 1500);
            }

            // Live feed from /api/events.
            fetch('/api/events').then(r => r.json()).then(d => {
                _renderFeed((d && d.events) || []);
            }).catch(() => { /* silent */ });

            // First-load follow-up: hit loadData again at 30s and 60s so a
            // user who hard-refreshed during the server's first run_update
            // doesn't sit on stale empty sources for 5 minutes.
            const hasAny = (s.warnings_count > 0 || s.earthquakes > 0 || s.wildfires > 0
                            || s.river_gauges > 0 || s.spc_zones > 0);
            if (!_dataLoaded) {
                _dataLoaded = true;
                setTimeout(loadData, 30000);
                setTimeout(loadData, 60000);
            }
            if (!hasAny) {
                // Cold start with literally nothing — short retry.
                setTimeout(loadData, 10000);
            }
        }).catch(err => {
            console.warn('loadData failed, retry in 10s:', err);
            setTimeout(loadData, 10000);
        });
    }

    // Ops-clock tick (independent of API refresh — keeps the seconds moving).
    setInterval(() => _renderClock(_prevSummary && _prevSummary._last_update), 1000);

    // Layer-count badge is kept in sync inside _refreshLayerCounts() —
    // counting only our user-facing CC_LAYER_GROUPS items, not Mapbox
    // basemap layers (water, roads, building, etc).
    map.on('idle', _refreshLayerCounts);

    // Initial load + 5-minute refresh.
    loadData();
    setInterval(loadData, 5 * 60 * 1000);
}

// Use exact Mapbox recommended pattern
map.on('load', function() {
    setupLayers();
    // First-time visitor tour — 1.2s delay so layers have a chance to paint first.
    setTimeout(() => startOnboarding(false), 1200);
});

// ── ONBOARDING COACH MARKS ───────────────────────────────────────────────────
const ONBOARD_STEPS = [
    {
        target: '#sidebar-layers-body',
        title: 'Toggle hazard layers',
        text: 'Turn weather, fire, seismic, and response layers on or off. The count badge shows how many features each layer has right now, and the small caret reveals its color legend.',
        placement: 'right'
    },
    {
        target: '#address-panel',
        title: 'Score any location',
        text: "Type an address and set a radius. You'll get a real-time threat score based on active hazards nearby, plus long-term FEMA National Risk Index context.",
        placement: 'left'
    },
    {
        target: '#nav-global',
        title: 'See the big picture',
        text: 'Open the hazard overview at any time — national counts, an at-a-glance chart, and the latest update timestamp.',
        placement: 'bottom'
    }
];

let _onboardIdx = 0;
function startOnboarding(force) {
    if (!force) {
        try { if (localStorage.getItem('nhm_onboarded') === 'true') return; } catch(e) {}
    }
    _onboardIdx = 0;
    const overlay = document.getElementById('coach-overlay');
    if (!overlay) return;
    overlay.style.display = 'block';
    renderOnboardStep();
}

function renderOnboardStep() {
    const overlay = document.getElementById('coach-overlay');
    const spot    = document.getElementById('coach-spot');
    const bubble  = document.getElementById('coach-bubble');
    if (!overlay || !spot || !bubble) return;
    const step = ONBOARD_STEPS[_onboardIdx];
    const tgt = document.querySelector(step.target);
    if (!tgt) {
        _onboardIdx++;
        if (_onboardIdx < ONBOARD_STEPS.length) renderOnboardStep();
        else endOnboarding();
        return;
    }
    const r = tgt.getBoundingClientRect();
    spot.style.left   = (r.left - 6) + 'px';
    spot.style.top    = (r.top - 6) + 'px';
    spot.style.width  = (r.width + 12) + 'px';
    spot.style.height = (r.height + 12) + 'px';

    const isLast = _onboardIdx === ONBOARD_STEPS.length - 1;
    bubble.innerHTML = `
        <div style="font-size:9px;letter-spacing:2px;color:#58bfff;font-weight:700;margin-bottom:6px;font-family:'Space Grotesk',sans-serif;">STEP ${_onboardIdx + 1} / ${ONBOARD_STEPS.length}</div>
        <div style="font-size:15px;font-weight:700;color:#fff;margin-bottom:8px;letter-spacing:0.3px;">${step.title}</div>
        <div style="font-size:12px;color:rgba(255,255,255,0.75);line-height:1.55;margin-bottom:14px;">${step.text}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
            <button class="coach-btn skip" onclick="endOnboarding()">Skip tour</button>
            <button class="coach-btn primary" onclick="nextOnboarding()">${isLast ? 'Got it' : 'Next →'}</button>
        </div>
    `;

    // Measure bubble off-screen, then place near target within viewport.
    bubble.style.left = '-9999px';
    bubble.style.right = '';
    bubble.style.top = '0';
    requestAnimationFrame(() => {
        const b = bubble.getBoundingClientRect();
        const margin = 18;
        let top, left;
        if (step.placement === 'right') {
            left = r.right + margin;
            top  = r.top + (r.height/2) - (b.height/2);
        } else if (step.placement === 'left') {
            left = r.left - b.width - margin;
            top  = r.top + (r.height/2) - (b.height/2);
        } else if (step.placement === 'bottom') {
            left = r.left + (r.width/2) - (b.width/2);
            top  = r.bottom + margin;
        } else {
            left = r.left + (r.width/2) - (b.width/2);
            top  = r.top - b.height - margin;
        }
        left = Math.max(12, Math.min(left, window.innerWidth  - b.width  - 12));
        top  = Math.max(12, Math.min(top,  window.innerHeight - b.height - 12));
        bubble.style.left = left + 'px';
        bubble.style.top  = top  + 'px';
    });
}

function nextOnboarding() {
    _onboardIdx++;
    if (_onboardIdx >= ONBOARD_STEPS.length) endOnboarding();
    else renderOnboardStep();
}

function endOnboarding() {
    const overlay = document.getElementById('coach-overlay');
    if (overlay) overlay.style.display = 'none';
    try { localStorage.setItem('nhm_onboarded', 'true'); } catch(e) {}
}

// ESC closes the tour
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const o = document.getElementById('coach-overlay');
        if (o && o.style.display === 'block') endOnboarding();
    }
});

// ── LAYER PANEL COLLAPSE (no-op: layers now live in persistent sidebar) ───────
function collapseLayerPanelForSearch() {}
function restoreLayerPanelAfterSearch() {}

// ── SITREP ────────────────────────────────────────
let _sitrepRaw = '';
function openSitrep() {
    document.getElementById('sitrep-overlay').classList.add('open');
    // Reset to loading state
    const setEl = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
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
        .then(data => {
            _sitrepRaw = data.raw || data.text || '';
            parseSitrep(_sitrepRaw);
        })
        .catch(() => {
            document.getElementById('sitrep-summary').textContent = 'Failed to generate report. Check GROQ_API_KEY.';
            document.getElementById('sitrep-summary').style.color = '#ff716c';
        });
}
function parseSitrep(text) {
    // SEVERITY
    const sevMatch = text.match(/SEVERITY:\s*(\d+)/i);
    const severity = sevMatch ? parseInt(sevMatch[1], 10) : 5;
    const levelEl = document.getElementById('sitrep-level');
    if (levelEl) {
        levelEl.textContent = isNaN(severity) ? '?' : severity;
        levelEl.style.color = severity >= 8 ? '#FF4444' : severity >= 5 ? '#FF8C00' : '#00CC66';
    }
    const badge = document.getElementById('sitrep-status-badge');
    if (badge) {
        const label = severity >= 8 ? 'CRITICAL RISK' : severity >= 6 ? 'ELEVATED RISK' : severity >= 4 ? 'ADVISORY' : 'NORMAL';
        const col   = severity >= 8 ? '#FF4444' : severity >= 6 ? '#FF8C00' : '#00CC66';
        badge.textContent = label;
        badge.style.color = col;
        badge.style.borderColor = col + '55';
        badge.style.background  = col + '18';
    }
    // PRIORITY THREATS
    const threatsMatch = text.match(/PRIORITY THREATS:\\n([\\s\\S]*?)(?:\\n\\nSITUATION:|$)/i);
    const threatsEl = document.getElementById('sitrep-threats');
    if (threatsEl && threatsMatch) {
        const items = threatsMatch[1].trim().split('\\n').filter(l => l.trim());
        const icons = ['warning', 'local_fire_department', 'cyclone'];
        const labels = ['CRITICAL', 'HIGH', 'MEDIUM'];
        const colors = ['#58bfff', '#FF8C00', '#ac89ff'];
        threatsEl.innerHTML = items.slice(0, 3).map((line, i) => {
            const content = line.replace(/^\d+\.\s*/, '').trim();
            const c = colors[i] || colors[2];
            const ic = icons[i] || 'warning';
            return `<div style="display:flex;align-items:flex-start;gap:12px;background:rgba(21,39,57,0.5);padding:10px 12px;border-left:3px solid ${c};">
                <span class="material-symbols-outlined" style="color:${c};font-size:18px;flex-shrink:0;">${ic}</span>
                <div style="flex:1;min-width:0;">
                    <p style="font-size:12px;color:#dde9fb;line-height:1.4;">${content}</p>
                </div>
                <span style="font-size:10px;font-weight:700;color:${c};flex-shrink:0;">${labels[i]||''}</span>
            </div>`;
        }).join('');
    }
    // SITUATION
    const sitMatch = text.match(/SITUATION:\\s*([\\s\\S]*?)(?:\\n\\nACTIONS:|$)/i);
    const summaryEl = document.getElementById('sitrep-summary');
    if (summaryEl && sitMatch) summaryEl.textContent = sitMatch[1].trim();
    // ACTIONS
    const actMatch = text.match(/ACTIONS:\\s*([\\s\\S]*)$/i);
    const actionsEl = document.getElementById('sitrep-actions');
    if (actionsEl && actMatch) {
        const acts = actMatch[1].trim().split('\\n').filter(l => l.trim());
        const codes = ['001-ALPHA', '002-BRAVO', '003-GAMMA', '004-DELTA'];
        actionsEl.innerHTML = acts.slice(0, 4).map((line, i) => {
            const content = line.replace(/^[-\d.]+\s*/, '').trim();
            const isFirst = i === 0;
            return `<div style="position:relative;padding-left:20px;border-left:1px solid rgba(61,73,87,0.5);">
                <div style="position:absolute;left:-4px;top:2px;width:7px;height:7px;background:${isFirst ? '#58bfff' : '#6a7686'};"></div>
                <span style="font-size:9px;font-weight:700;color:${isFirst ? '#58bfff' : '#a0acbd'};letter-spacing:1px;">${codes[i]||''}</span>
                <p style="font-size:11px;color:#dde9fb;margin-top:3px;line-height:1.4;">${content}</p>
            </div>`;
        }).join('');
    }
    // AI terminal
    const confEl = document.getElementById('sitrep-confidence');
    if (confEl) confEl.innerHTML = `<p>&gt; ANALYSIS_COMPLETE</p><p>&gt; MODEL: GROQ-LLAMA-3.3</p><p>&gt; THREAT_VECTORS_MAPPED</p>`;
}
function closeSitrep() {
    document.getElementById('sitrep-overlay').classList.remove('open');
}
function copySitrep() {
    if (!_sitrepRaw) return;
    navigator.clipboard.writeText(_sitrepRaw).then(() => {
        const fb = document.getElementById('sitrep-copy-feedback');
        if (fb) { fb.textContent = ' ✓'; setTimeout(() => { fb.textContent = ''; }, 2000); }
    });
}

// ── HAZARD OVERVIEW ROWS (replaces Chart.js) ──────
function updateHazardChart(summary) {
    const s = summary || {};
    const keys = {
        warnings_count: s.warnings_count || 0,
        earthquakes:    s.earthquakes    || 0,
        wildfires:      s.wildfires      || 0,
        river_gauges:   s.river_gauges   || 0,
        active_storms:  s.active_storms  || 0,
        volcanoes:      s.volcanoes      || 0,
    };
    const maxVal = Math.max(1, ...Object.values(keys));
    document.querySelectorAll('.haz-row').forEach(row => {
        const key   = row.dataset.key;
        const color = row.dataset.color;
        const val   = keys[key] || 0;
        const pct   = Math.round((val / maxVal) * 100);
        // Build inner HTML once if not already built
        if (!row.querySelector('.haz-bar-wrap')) {
            const label = row.textContent.trim();
            row.innerHTML = `
                <span style="white-space:nowrap;">${label}</span>
                <div class="haz-bar-wrap"><div class="haz-bar" style="background:${color};"></div></div>
                <span class="haz-count" style="color:${color};">0</span>`;
        }
        row.querySelector('.haz-bar').style.width = pct + '%';
        row.querySelector('.haz-count').textContent = val;
        row.style.opacity = val > 0 ? '1' : '0.35';
    });
}

// ── ADDRESS SEARCH & THREAT ANALYSIS ─────────────
const MAPBOX_TOKEN_JS = mapboxgl.accessToken;
let searchMarker = null;
let bufferLayer  = null;

// ── SCORE INPUTS (Immediate Threat Score) ─────────
const SCORE_INPUTS_KEY = 'nhm-score-inputs-v1';
const DEFAULT_SCORE_INPUTS = {
    warnings: true,
    stormreports: true,
    earthquakes: true,
    firedetections: true,
    fireperimeters: true,
    rivergauges: false,
    hurricanes: false
};

function _loadScoreInputs() {
    try {
        const raw = localStorage.getItem(SCORE_INPUTS_KEY);
        if (!raw) return { ...DEFAULT_SCORE_INPUTS };
        const parsed = JSON.parse(raw);
        return { ...DEFAULT_SCORE_INPUTS, ...(parsed || {}) };
    } catch (e) {
        return { ...DEFAULT_SCORE_INPUTS };
    }
}

function _saveScoreInputs(v) {
    try { localStorage.setItem(SCORE_INPUTS_KEY, JSON.stringify(v)); } catch (e) {}
}

function readScoreInputsFromUI() {
    const get = (id, fallback) => {
        const el = document.getElementById(id);
        return el ? !!el.checked : fallback;
    };
    return {
        warnings:       get('si-warnings', true),
        stormreports:   get('si-stormreports', true),
        earthquakes:    get('si-earthquakes', true),
        firedetections: get('si-firedetections', true),
        fireperimeters: get('si-fireperimeters', true),
        rivergauges:    get('si-rivergauges', false),
        hurricanes:     get('si-hurricanes', false),
    };
}

function writeScoreInputsToUI(v) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
    set('si-warnings',       v.warnings);
    set('si-stormreports',   v.stormreports);
    set('si-earthquakes',    v.earthquakes);
    set('si-firedetections', v.firedetections);
    set('si-fireperimeters', v.fireperimeters);
    set('si-rivergauges',    v.rivergauges);
    set('si-hurricanes',     v.hurricanes);
}

function _toast(msg) {
    const note = document.createElement('div');
    note.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:250;padding:10px 14px;' +
        'background:rgba(4,15,27,0.92);border:1px solid rgba(88,191,255,0.25);' +
        'color:#a8d8ff;font-size:11px;font-family:Inter,sans-serif;letter-spacing:0.6px;';
    note.textContent = msg;
    document.body.appendChild(note);
    setTimeout(() => { try { note.remove(); } catch(e) {} }, 1600);
}

function applyScorePreset(name) {
    let v = { ...DEFAULT_SCORE_INPUTS };
    if (name === 'weather') {
        v = { ...DEFAULT_SCORE_INPUTS,
            firedetections: false, fireperimeters: false, earthquakes: false,
            rivergauges: false, hurricanes: false
        };
    } else if (name === 'fire') {
        v = { ...DEFAULT_SCORE_INPUTS,
            warnings: false, stormreports: false, earthquakes: false,
            rivergauges: false, hurricanes: false
        };
    } else {
        v = { ...DEFAULT_SCORE_INPUTS };
    }
    writeScoreInputsToUI(v);
    _saveScoreInputs(v);
    _toast('Score preset: ' + (name || 'immediate').toUpperCase());
}

// Update buffer label and re-run analysis when slider changes
let _sliderDebounce = null;
document.getElementById('buffer-slider').addEventListener('input', function() {
    document.getElementById('buffer-label').textContent = this.value + ' miles';
    // Re-run analysis only if a search is already active
    if (_searchContext !== null) {
        clearTimeout(_sliderDebounce);
        _sliderDebounce = setTimeout(() => searchLocation(), 400);
    }
});

// Enter key triggers search
document.getElementById('address-input').addEventListener('keypress', function(e) {
    if (e.key === 'Enter') searchLocation();
});

// Restore persisted score inputs on load and persist changes
document.addEventListener('DOMContentLoaded', () => {
    const v = _loadScoreInputs();
    writeScoreInputsToUI(v);
    ['si-warnings','si-stormreports','si-earthquakes','si-firedetections','si-fireperimeters','si-rivergauges','si-hurricanes']
        .forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            el.addEventListener('change', () => _saveScoreInputs(readScoreInputsFromUI()));
        });
});

// ── FEMA NRI LOOKUP ──────────────────────────────
// Loaded once on first search, then cached for all subsequent lookups.
let _nriData = null;

async function _loadNRI() {
    if (_nriData) return _nriData;
    try {
        const r = await fetch('/static/nri_counties.json');
        if (!r.ok) { console.log('NRI load failed:', r.status); return null; }
        _nriData = await r.json();
        console.log('NRI loaded:', Object.keys(_nriData).length, 'counties');
        return _nriData;
    } catch(e) {
        console.log('NRI load error:', e);
        return null;
    }
}

async function fetchNRI(stateAbbr, countyName) {
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
    for (const d of Object.values(data)) {
        if (d.sa === targetState && norm(d.co) === targetCounty) { match = d; break; }
    }
    // Partial fallback
    if (!match && targetCounty) {
        for (const d of Object.values(data)) {
            if (d.sa === targetState) {
                const c = norm(d.co);
                if (c.includes(targetCounty) || targetCounty.includes(c)) { match = d; break; }
            }
        }
    }
    if (!match) { console.log('NRI: no match for', targetState, targetCounty); return null; }

    return {
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
    };
}

function getRatingColor(rating) {
    const r = (rating || '').toLowerCase();
    if (r.includes('very high'))      return '#FF0000';
    if (r.includes('relatively high')) return '#FF6600';
    if (r.includes('high'))           return '#FF4400';
    if (r.includes('relatively mod')) return '#FFCC00';
    if (r.includes('moderate'))       return '#FFFF00';
    if (r.includes('relatively low')) return '#88FF00';
    if (r.includes('low'))            return '#00FF88';
    return '#888888';
}

function formatDollars(val) {
    if (!val || val <= 0) return 'N/A';
    if (val >= 1e9) return '$' + (val/1e9).toFixed(1) + 'B/yr';
    if (val >= 1e6) return '$' + (val/1e6).toFixed(1) + 'M/yr';
    if (val >= 1e3) return '$' + (val/1e3).toFixed(0) + 'K/yr';
    return '$' + Math.round(val);
}

function buildNRIPanel(nri, countyName) {
    if (!nri) return '';
    
    const riskColor  = getRatingColor(nri.RISK_RATNG);
    const soviColor  = getRatingColor(nri.SOVI_RATNG);
    const reslColor  = getRatingColor(nri.RESL_RATNG);
    // Resilience is inverse - low resilience = high risk
    const reslRisk   = 100 - (nri.RESL_SCORE || 50);
    
    const hazards = [
        { name: 'Tornado',    val: nri.TRND_EALT, color: '#FF0000' },
        { name: 'Wildfire',   val: nri.WFIR_EALT, color: '#FF4500' },
        { name: 'Earthquake', val: nri.ERQK_EALT, color: '#00B4FF' },
        { name: 'Riv. Flood', val: nri.RFLD_EALT, color: '#0088FF' },
        { name: 'Hurricane',  val: nri.HRCN_EALT, color: '#FF6600' },
        { name: 'Ice Storm',  val: nri.ISTM_EALT, color: '#AAAAFF' },
        { name: 'Lightning',  val: nri.LTNG_EALT, color: '#FFFF00' },
        { name: 'Hail',       val: nri.HAIL_EALT, color: '#88FFFF' },
    ].filter(h => h.val > 0).sort((a,b) => b.val - a.val).slice(0, 6);

    return `
    <div class="nri-section">
        <div class="nri-title">🏛 FEMA National Risk Index — ${nri.COUNTY || countyName} Co.</div>
        
        <div class="nri-score-row">
            <div>
                <div class="nri-label">OVERALL RISK</div>
                <div class="nri-bar-wrap" style="width:120px;margin-top:4px">
                    <div class="nri-bar" style="width:${nri.RISK_SCORE||0}%;background:${riskColor}"></div>
                </div>
            </div>
            <div style="text-align:right">
                <div class="nri-value" style="color:${riskColor}">${(nri.RISK_SCORE||0).toFixed(1)}</div>
                <div style="font-size:9px;color:${riskColor};letter-spacing:1px">${(nri.RISK_RATNG||'N/A').toUpperCase()}</div>
            </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin:6px 0">
            <div class="nri-score-row" style="flex-direction:column;align-items:flex-start">
                <div class="nri-label">SOCIAL VULNERABILITY</div>
                <div class="nri-value" style="color:${soviColor}">${(nri.SOVI_SCORE||0).toFixed(1)} <span style="font-size:9px;opacity:0.7">/100</span></div>
                <div style="font-size:9px;color:${soviColor}">${(nri.SOVI_RATNG||'N/A').toUpperCase()}</div>
            </div>
            <div class="nri-score-row" style="flex-direction:column;align-items:flex-start">
                <div class="nri-label">COMMUNITY RESILIENCE</div>
                <div class="nri-value" style="color:${reslColor}">${(nri.RESL_SCORE||0).toFixed(1)} <span style="font-size:9px;opacity:0.7">/100</span></div>
                <div style="font-size:9px;color:${reslColor}">${(nri.RESL_RATNG||'N/A').toUpperCase()}</div>
            </div>
        </div>

        <div class="nri-score-row">
            <div class="nri-label">EXPECTED ANNUAL LOSS</div>
            <div class="nri-value" style="color:#FFD700">${formatDollars(nri.EAL_VALT)}</div>
        </div>

        ${hazards.length ? `
        <div class="nri-title" style="margin-top:8px">TOP HAZARD LOSSES/YR</div>
        <div class="nri-hazards">
            ${hazards.map(h => `
                <div class="nri-hazard-item" style="border-color:${h.color}">
                    <div class="nri-hazard-name">${h.name}</div>
                    <div class="nri-hazard-val" style="color:${h.color}">${formatDollars(h.val)}</div>
                </div>
            `).join('')}
        </div>` : ''}
    </div>`;
}

async function searchLocation() {
    const btn = document.getElementById('search-btn');
    btn.textContent = '⏳ ANALYZING...';
    btn.disabled = true;

    let lat, lng, placeName, nriState = '', nriCounty = '';
    const scoreInputs = readScoreInputsFromUI();
    _saveScoreInputs(scoreInputs);

    try {
        if (_gpsOverride) {
            // GPS path — skip geocoding, use exact device coordinates
            lat = _gpsOverride.lat;
            lng = _gpsOverride.lng;
            const feat = _gpsOverride.feature;
            _gpsOverride = null;
            placeName = feat?.place_name || 'Your Location';
            for (const ctx of (feat?.context || [])) {
                if (ctx.id.startsWith('region'))   nriState  = (ctx.short_code || '').replace('US-', '');
                if (ctx.id.startsWith('district')) nriCounty = ctx.text || '';
            }
        } else {
            // Address search path — geocode the input
            const address = document.getElementById('address-input').value.trim();
            if (!address) { btn.textContent = '🔍 ANALYZE THREATS'; btn.disabled = false; return; }
            const geoUrl = 'https://api.mapbox.com/geocoding/v5/mapbox.places/' +
                encodeURIComponent(address) +
                '.json?country=US&limit=1&access_token=' + MAPBOX_TOKEN_JS;
            const geo = await fetch(geoUrl);
            if (!geo.ok) throw new Error('Geocoding failed: ' + geo.status);
            const geoData = await geo.json();
            if (!geoData.features || geoData.features.length === 0) {
                showResults([{type:'error', text:'Address not found. Try a different search.'}]);
                btn.textContent = '🔍 ANALYZE THREATS'; btn.disabled = false;
                return;
            }
            [lng, lat] = geoData.features[0].center;
            placeName  = geoData.features[0].place_name;
            for (const ctx of (geoData.features[0].context || [])) {
                if (ctx.id.startsWith('region'))   nriState  = (ctx.short_code || '').replace('US-', '');
                if (ctx.id.startsWith('district')) nriCounty = ctx.text || '';
            }
        }

        const radiusMiles = parseFloat(document.getElementById('buffer-slider').value);
        const radiusKm    = radiusMiles * 1.60934;

        // Fly to location
        map.flyTo({ center: [lng, lat], zoom: 7, duration: 1500 });

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
        const buffer = turf.circle(point, radiusKm, { steps: 64, units: 'kilometers' });

        // Add buffer to map
        if (map.getSource('search-buffer')) {
            map.getSource('search-buffer').setData(buffer);
        } else {
            map.addSource('search-buffer', { type: 'geojson', data: buffer });
            map.addLayer({
                id: 'buffer-fill', type: 'fill', source: 'search-buffer',
                paint: { 'fill-color': '#00B4FF', 'fill-opacity': 0.08 }
            });
            map.addLayer({
                id: 'buffer-outline', type: 'line', source: 'search-buffer',
                paint: { 'line-color': '#00B4FF', 'line-width': 2, 'line-dasharray': [4,4] }
            });
        }

        // Fetch all hazard data and analyze
        const empty = {type:'FeatureCollection',features:[]};
        const safeJson = async (url, transform) => {
            try {
                const r = await fetch(url);
                if (!r.ok) { console.log('API failed:', url, r.status); return empty; }
                const d = await r.json();
                return transform ? transform(d) : d;
            } catch(e) { console.log('API error:', url, e.message); return empty; }
        };

        const severe = ['TORNADO','HAIL','TSTM WND GST','TSTM WND DMG','FUNNEL CLOUD','LIGHTNING','FLASH FLOOD'];

        const [warnings, earthquakes, fires, lightning, perimeters,
               spcData, droughtData, stormsData, countiesData,
               riverData, volcanoData, femaData, aqiData, shelterData] = await Promise.all([
            safeJson('/api/warnings'),
            safeJson('/api/earthquakes'),
            safeJson('/api/fires'),
            safeJson('https://mesonet.agron.iastate.edu/geojson/lsr.php?hours=6&wfo=all',
                d => ({type:'FeatureCollection', features:(d.features||[]).filter(f => severe.some(x => (f.properties&&f.properties.typetext||'').toUpperCase().indexOf(x)>=0))})),
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

        const addThreat = (obj) => {
            // obj: {kind,label,points,dist,color,source,detail}
            threatObjs.push(obj);
            threats.push({
                type: 'threat',
                color: obj.color,
                dist:  obj.dist,
                points: obj.points,
                source: obj.source,
                text:  obj.label
            });
        };

        // ── NWS WARNINGS ─────────────────────────────
        const warningsInBuffer = (warnings.features || []).filter(f => {
            try {
                if (!f.geometry) return false;
                if (f.geometry.type === 'Point') {
                    return turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer);
                }
                return turf.booleanIntersects(f, buffer);
            } catch(e) { return false; }
        });

        if (scoreInputs.warnings) warningsInBuffer.forEach(f => {
            const phenom = (f.properties?.phenom || '').toUpperCase();
            const sig    = (f.properties?.sig    || '').toUpperCase();
            let weight   = THREAT_WEIGHTS.other_warning;
            let label    = '⚠ Warning';
            let color    = '#FF8800';

            if (phenom === 'TO' && sig === 'W') { weight = THREAT_WEIGHTS.tornado_warning;   label = '🌪 Tornado Warning';             color = '#FF0000'; }
            else if (phenom === 'HU')           { weight = THREAT_WEIGHTS.hurricane_warning;  label = '🌀 Hurricane Warning/Watch';     color = '#FF6600'; }
            else if (phenom === 'FF')           { weight = THREAT_WEIGHTS.flash_flood;        label = '🌊 Flash Flood Warning';         color = '#00BFFF'; }
            else if (phenom === 'FA')           { weight = THREAT_WEIGHTS.flood_warning;      label = '🌊 Flood Warning';               color = '#0099FF'; }
            else if (phenom === 'SV')           { weight = THREAT_WEIGHTS.severe_tstorm;      label = '⛈ Severe Thunderstorm Warning'; color = '#FF6666'; }
            else if (phenom === 'WS')           { weight = THREAT_WEIGHTS.winter_storm;       label = '❄ Winter Storm Warning';        color = '#AAAAFF'; }
            else if (phenom === 'FW')           { weight = THREAT_WEIGHTS.other_warning + 5;  label = '🔥 Fire Weather Warning';        color = '#FF4500'; }

            // Calculate distance to warning centroid
            let dist = radiusMiles;
            try {
                const centroid = turf.centroid(f);
                dist = turf.distance(userPt, centroid, {units: 'miles'});
            } catch(e) {}

            const decay   = distanceDecay(dist, radiusMiles);
            const pts     = Math.round(weight * decay);
            totalScore   += pts;
            addThreat({
                kind: 'warning',
                label: `${label}`,
                points: pts,
                dist,
                color,
                source: 'NWS',
                detail: phenom + '/' + sig
            });
        });

        // ── EARTHQUAKES ───────────────────────────────
        const eqFeats = (earthquakes.features || [])
            .filter(f => f.geometry?.coordinates)
            .map(f => ({
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }))
            .filter(f => {
                try { return turf.booleanPointInPolygon(f._pt, buffer); }
                catch(e) { return false; }
            });

        if (scoreInputs.earthquakes) eqFeats.forEach(f => {
            const mag  = parseFloat(f.properties?.mag || 0);
            const dist = turf.distance(userPt, f._pt, {units: 'miles'});
            let weight = mag >= 5 ? THREAT_WEIGHTS.earthquake_m5
                       : mag >= 4 ? THREAT_WEIGHTS.earthquake_m4
                       :            THREAT_WEIGHTS.earthquake_m3;
            const decay = distanceDecay(dist, radiusMiles);
            const pts   = Math.round(weight * decay);
            totalScore += pts;
            addThreat({
                kind: 'earthquake',
                label: `🔴 Earthquake M${mag.toFixed(1)} — ${f.properties?.place || 'Unknown'}`,
                points: pts,
                dist,
                color: '#00B4FF',
                source: 'USGS',
                detail: 'M' + mag.toFixed(1)
            });
        });

        // ── WILDFIRES ─────────────────────────────────
        const fireFeats = (fires.features || [])
            .filter(f => f.geometry?.coordinates)
            .map(f => ({
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }))
            .filter(f => {
                try { return turf.booleanPointInPolygon(f._pt, buffer); }
                catch(e) { return false; }
            });

        if (scoreInputs.firedetections && fireFeats.length > 0) {
            let firePtsTotal = 0;
            let closestDist = Infinity;
            fireFeats.forEach(f => {
                const dist  = turf.distance(userPt, f._pt, {units:'miles'});
                const decay = distanceDecay(dist, radiusMiles);
                const pts   = Math.round(THREAT_WEIGHTS.wildfire_near * decay);
                totalScore   += pts;
                firePtsTotal += pts;
                if (dist < closestDist) closestDist = dist;
            });
            addThreat({
                kind: 'fire_detection',
                label: `🔥 ${fireFeats.length} Fire Detection(s) — closest ${Math.round(closestDist)}mi`,
                points: firePtsTotal,
                dist: closestDist,
                color: '#FF5000',
                source: 'NASA FIRMS',
                detail: String(fireFeats.length)
            });
        }

        // ── STORM REPORTS ─────────────────────────────
        const stormFeats = (lightning.features || [])
            .filter(f => f.geometry?.coordinates)
            .map(f => ({
                ...f,
                _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]])
            }))
            .filter(f => {
                try { return turf.booleanPointInPolygon(f._pt, buffer); }
                catch(e) { return false; }
            });

        if (scoreInputs.stormreports && stormFeats.length > 0) {
            // Time decay — recent reports weighted more
            const now = Date.now();
            let stormPtsTotal = 0;
            stormFeats.forEach(f => {
                const validTime = new Date(f.properties?.valid || now).getTime();
                const hoursAgo  = (now - validTime) / 3600000;
                const recency   = Math.max(0, 1 - hoursAgo / 6);
                const dist      = turf.distance(userPt, f._pt, {units:'miles'});
                const decay     = distanceDecay(dist, radiusMiles);
                const pts       = Math.round(THREAT_WEIGHTS.storm_report * decay * recency);
                totalScore     += pts;
                stormPtsTotal  += pts;
            });
            const types = [...new Set(stormFeats.map(f => f.properties?.typetext || 'Storm').slice(0,3))];
            const closestStorm = stormFeats.reduce((a,b) =>
                turf.distance(userPt,a._pt,{units:'miles'}) <
                turf.distance(userPt,b._pt,{units:'miles'}) ? a : b
            );
            const distClosest = turf.distance(userPt, closestStorm._pt, {units:'miles'});
            addThreat({
                kind: 'storm_report',
                label: `⚡ ${stormFeats.length} Storm Report(s) — ${types.join(', ')}`,
                points: stormPtsTotal,
                dist: distClosest,
                color: '#FFFF00',
                source: 'NWS LSR',
                detail: String(stormFeats.length)
            });
        }

        // ── FIRE PERIMETERS ───────────────────────────
        const perimInBuffer = (perimeters.features || []).filter(f => {
            try {
                if (!f.geometry) return false;
                return turf.booleanIntersects(f, buffer);
            } catch(e) { return false; }
        });

        if (scoreInputs.fireperimeters) perimInBuffer.forEach(f => {
            const acres = parseFloat(f.properties?.GISAcres || 0);
            const name  = f.properties?.IncidentName || 'Active Fire';
            let weight  = THREAT_WEIGHTS.fire_perimeter;
            // Scale weight by fire size
            if (acres > 100000) weight *= 1.5;
            else if (acres > 10000) weight *= 1.2;

            let dist = radiusMiles / 2;
            try {
                const centroid = turf.centroid(f);
                dist = turf.distance(userPt, centroid, {units:'miles'});
            } catch(e) {}

            const decay = distanceDecay(dist, radiusMiles);
            const pts   = Math.round(weight * decay);
            totalScore += pts;
            addThreat({
                kind: 'fire_perimeter',
                label: `🔥 ${name} — ${Math.round(acres).toLocaleString()} acres (${f.properties?.PercentContained||0}% contained)`,
                points: pts,
                dist,
                color: '#FF4500',
                source: 'WFIGS',
                detail: Math.round(acres)
            });
        });

        // ── RIVER FLOOD GAUGES (optional) ─────────────
        if (scoreInputs.rivergauges) {
            const gaugesInBuffer = (riverData.features || [])
                .filter(f => f.geometry?.coordinates)
                .map(f => ({ ...f, _pt: turf.point([f.geometry.coordinates[0], f.geometry.coordinates[1]]) }))
                .filter(f => {
                    try { return turf.booleanPointInPolygon(f._pt, buffer); }
                    catch(e) { return false; }
                });

            const statusWeight = (status) => {
                const s = (status || '').toLowerCase();
                if (s === 'major')    return THREAT_WEIGHTS.flood_gauge_major;
                if (s === 'moderate') return THREAT_WEIGHTS.flood_gauge_moderate;
                if (s === 'minor')    return THREAT_WEIGHTS.flood_gauge_minor;
                if (s === 'action')   return THREAT_WEIGHTS.flood_gauge_action;
                return 0;
            };

            gaugesInBuffer.forEach(f => {
                const p = f.properties || {};
                const w = statusWeight(p.status);
                if (!w) return;
                const dist = turf.distance(userPt, f._pt, {units:'miles'});
                const decay = distanceDecay(dist, radiusMiles);
                const pts   = Math.round(w * decay);
                totalScore += pts;
                addThreat({
                    kind: 'flood_gauge',
                    label: `🌊 Flood Gauge — ${(p.location||p.name||'Gauge')} (${String(p.status||'').toUpperCase()})`,
                    points: pts,
                    dist,
                    color: '#00BFFF',
                    source: 'NOAA AHPS',
                    detail: p.gaugelid || ''
                });
            });
        }

        // ── HURRICANES / TROPICAL SYSTEMS (optional) ───
        if (scoreInputs.hurricanes) {
            const stormsInBuffer = (stormsData.features || []).filter(f => {
                try {
                    if (!f.geometry) return false;
                    if (f.geometry.type === 'Point') {
                        return turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer);
                    }
                    return turf.booleanIntersects(f, buffer);
                } catch(e) { return false; }
            });
            if (stormsInBuffer.length) {
                const coneHits  = stormsInBuffer.filter(f => (f.properties?.layer || '') === 'cone');
                const trackHits = stormsInBuffer.filter(f => (f.properties?.layer || '') === 'track');
                const name = (coneHits[0]?.properties?.storm_name || trackHits[0]?.properties?.storm_name || 'Storm');
                if (coneHits.length) {
                    let dist = radiusMiles;
                    try { const c = turf.centroid(coneHits[0]); dist = turf.distance(userPt, c, {units:'miles'}); } catch(e) {}
                    const decay = distanceDecay(dist, radiusMiles);
                    const pts = Math.round(THREAT_WEIGHTS.hurricane_cone * decay);
                    totalScore += pts;
                    addThreat({
                        kind: 'hurricane_cone',
                        label: `🌀 Forecast Cone intersects area — ${name}`,
                        points: pts,
                        dist,
                        color: '#FF6600',
                        source: 'NHC',
                        detail: name
                    });
                } else if (trackHits.length) {
                    let dist = radiusMiles;
                    try { const c = turf.centroid(trackHits[0]); dist = turf.distance(userPt, c, {units:'miles'}); } catch(e) {}
                    const decay = distanceDecay(dist, radiusMiles);
                    const pts = Math.round(THREAT_WEIGHTS.hurricane_track * decay);
                    totalScore += pts;
                    addThreat({
                        kind: 'hurricane_track',
                        label: `🌀 Storm track points within area — ${name}`,
                        points: pts,
                        dist,
                        color: '#FF6600',
                        source: 'NHC',
                        detail: name
                    });
                }
            }
        }

        // ── CAP SCORE & SORT ──────────────────────────
        totalScore = Math.min(100, Math.round(totalScore));

        // Sort threats by distance
        threats.sort((a,b) => (a.dist||99) - (b.dist||99));

        // Store search context for county briefing + email alerts
        _searchContext = { lat, lng, label: placeName.split(',').slice(0,2).join(','), radius: radiusMiles };
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
        _TOGGLEABLE.forEach(id => {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none');
        });
        _AUTO_ON.forEach(id => {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'visible');
        });

        // ── CLIP ALL SOURCES TO THE BUFFER ZONE ─────────────────────────────
        // Polygon features are clipped to the buffer circle via turf.intersect
        // so only the portion inside the circle renders — not the full polygon.
        // Point features are filtered via booleanPointInPolygon.
        // loadData() is blocked from overwriting these while _searchContext is set.

        // Polygon/mixed sources: clip polygons to buffer boundary, filter points in
        const polyFilter = (fc) => {
            const feats = [];
            for (const f of (fc.features || [])) {
                try {
                    if (!f.geometry) continue;
                    const gt = f.geometry.type;
                    if (gt === 'Point') {
                        if (turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer))
                            feats.push(f);
                    } else if (gt === 'Polygon' || gt === 'MultiPolygon') {
                        const clipped = turf.intersect(f, buffer);
                        if (clipped) { clipped.properties = f.properties; feats.push(clipped); }
                    } else if (turf.booleanIntersects(f, buffer)) {
                        feats.push(f);  // LineString etc — include if intersects
                    }
                } catch(e) {}
            }
            return {type:'FeatureCollection', features: feats};
        };

        // Point-only sources: filter to points inside buffer
        const ptFilter = (fc) => ({
            type: 'FeatureCollection',
            features: (fc.features || []).filter(f => {
                try {
                    if (!f.geometry?.coordinates) return false;
                    return turf.booleanPointInPolygon(turf.point(f.geometry.coordinates), buffer);
                } catch(e) { return false; }
            })
        });

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

        if (threats.length === 0) {
            showResults([
                { type: 'score', score: 0 },
                { type: 'safe', text: `✅ No active threats detected within ${radiusMiles} miles of ${locationLabel}` },
                { type: 'nri', html: nriHtml }
            ]);
        } else {
            showResults([
                { type: 'header', text: `📍 ${locationLabel} · ${radiusMiles}mi radius` },
                { type: 'score', score: totalScore },
                ...threats,
                { type: 'nri', html: nriHtml }
            ]);
        }

        // Auto-generate county briefing using computed score + structured threats
        generateInlineBriefing(totalScore, threatLevel, threatObjs, locationLabel, scoreInputs);

    } catch(err) {
        console.error('Search error:', err);
        showResults([{type:'error', text:'Error: ' + (err.message || 'Search failed. Check console for details.')}]);
    } finally {
        btn.textContent = '🔍 ANALYZE THREATS';
        btn.disabled = false;
    }
}

// ── THREAT SCORING ENGINE ────────────────────────
const THREAT_WEIGHTS = {
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
};

function getThreatLevel(score) {
    if (score >= 75) return { label: 'EXTREME',   color: '#FF0000', bg: 'rgba(255,0,0,0.15)',    emoji: '🚨' };
    if (score >= 55) return { label: 'SEVERE',    color: '#FF4400', bg: 'rgba(255,68,0,0.12)',   emoji: '🔴' };
    if (score >= 35) return { label: 'HIGH',      color: '#FF8800', bg: 'rgba(255,136,0,0.12)',  emoji: '🟠' };
    if (score >= 15) return { label: 'ELEVATED',  color: '#FFCC00', bg: 'rgba(255,204,0,0.12)', emoji: '🟡' };
    return                  { label: 'LOW',       color: '#00FF88', bg: 'rgba(0,255,136,0.08)', emoji: '🟢' };
}

function distanceDecay(distMiles, radiusMiles) {
    // Closer threats weighted more heavily
    // 0 miles = 1.5x, radius miles = 0.5x
    return Math.max(0, 1.5 - (distMiles / radiusMiles));
}

function getProximityLabel(distMiles) {
    if (distMiles < 10)  return { label: 'IMMEDIATE', color: '#FF0000' };
    if (distMiles < 30)  return { label: 'NEAR',      color: '#FF8800' };
    if (distMiles < 60)  return { label: 'MODERATE',  color: '#FFCC00' };
    return                      { label: 'DISTANT',   color: '#888888' };
}

function showResults(threats) {
    collapseLayerPanelForSearch();
    const div = document.getElementById('threat-results');
    div.style.display = 'block';
    div.innerHTML = threats.map(t => {
        if (t.type === 'safe') {
            return `<div class="threat-item threat-none">${t.text}</div>`;
        }
        if (t.type === 'header') {
            return `<div style="font-size:10px;color:rgba(255,255,255,0.5);margin-bottom:6px;letter-spacing:1px">${t.text}</div>`;
        }
        if (t.type === 'score') {
            const level = getThreatLevel(t.score);
            const pct = Math.min(100, t.score);
            return `
                <div style="background:${level.bg};border:1px solid ${level.color}40;
                    border-radius:8px;padding:10px;margin:6px 0;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                        <span style="font-size:11px;font-weight:700;color:${level.color};letter-spacing:2px;">
                            ${level.emoji} ${level.label} THREAT
                        </span>
                        <span style="font-size:20px;font-weight:700;color:${level.color}">${Math.round(t.score)}<span style="font-size:10px;color:rgba(255,255,255,0.4)">/100</span></span>
                    </div>
                    <div style="background:rgba(0,0,0,0.3);border-radius:4px;height:6px;overflow:hidden;">
                        <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,${level.color}88,${level.color});
                            border-radius:4px;transition:width 0.8s ease;"></div>
                    </div>
                </div>`;
        }
        if (t.type === 'nri') {
            return t.html || '';
        }
        if (t.type === 'error') {
            return `<div class="threat-item" style="color:#FF6666;border-color:#FF6666;">${t.text}</div>`;
        }
        // Threat item with distance and proximity label
        const prox = t.dist !== undefined ? getProximityLabel(t.dist) : null;
        return `<div class="threat-item" style="color:${t.color};border-color:${t.color}40;
            background:${t.color}08;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <span>${t.text}</span>
                ${prox ? `<span style="font-size:9px;color:${prox.color};font-weight:700;
                    letter-spacing:1px;margin-left:8px;flex-shrink:0;">${prox.label}</span>` : ''}
            </div>
            <div style="display:flex;justify-content:space-between;gap:10px;margin-top:2px;">
                ${t.dist !== undefined ? `<div style="font-size:9px;color:rgba(255,255,255,0.3);">
                    ${Math.round(t.dist)} miles away</div>` : `<div></div>`}
                <div style="font-size:9px;color:rgba(255,255,255,0.35);white-space:nowrap;">
                    ${t.source ? t.source : ''}${t.points !== undefined ? ` · +${t.points} pts` : ''}
                </div>
            </div>
        </div>`;
    }).join('');

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
}

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
function _setPresetLayers(show) {
    _ALL_PRESET_LAYERS.forEach(id => {
        if (map.getLayer(id)) {
            map.setLayoutProperty(id, 'visibility',
                show.includes(id) ? 'visible' : 'none');
        }
    });
}
// ATMOS: NWS warnings + SPC outlook + hurricanes + lightning
function presetAtmos() {
    _setPresetLayers([
        'warnings-fill','warnings-outline',
        'spc-fill','spc-outline',
        'storm-cone','storm-cone-outline','storm-track',
        'lightning-strikes'
    ]);
}
// SEISMIC: earthquakes only
function presetSeismic() {
    _setPresetLayers(['eq-circles']);
}
// THERMAL: fire detections + fire perimeters
function presetThermal() {
    _setPresetLayers(['fire-points','fire-perimeter-fill','fire-perimeter-outline']);
}

// ── SEARCH CONTEXT (set after each successful search) ─────
let _searchContext = null;  // {lat, lng, label, radius}

// ── HERO PANEL ────────────────────────────────────────────
function heroSearch() {
    const val = (document.getElementById('hero-input')?.value || '').trim();
    if (!val) return;
    document.getElementById('address-input').value = val;
    dismissHero();
    searchLocation();
}
function heroQuick(val) {
    document.getElementById('hero-input').value = val;
    heroSearch();
}
function dismissHero() {
    const h = document.getElementById('county-hero');
    if (!h) return;
    h.style.opacity = '0';
    h.style.pointerEvents = 'none';
    setTimeout(() => h.remove(), 380);
}
// Enter key on hero input
document.addEventListener('DOMContentLoaded', () => {
    const hi = document.getElementById('hero-input');
    if (hi) hi.addEventListener('keypress', e => { if (e.key === 'Enter') heroSearch(); });
});

// ── COUNTY BRIEFING + EMAIL ALERTS ───────────────────────
function openCountySitrep() {
    const btn = document.getElementById('county-sitrep-btn');
    if (btn) { btn.textContent = '⏳ GENERATING...'; btn.disabled = true; }
    const params = _searchContext
        ? '?lat=' + _searchContext.lat + '&lng=' + _searchContext.lng
          + '&radius=' + _searchContext.radius
          + '&county=' + encodeURIComponent(_searchContext.label)
        : '';
    // Reuse the existing sitrep modal
    document.getElementById('sitrep-overlay').classList.add('open');
    const setEl = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
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
        .then(data => {
            _sitrepRaw = data.raw || data.text || '';
            parseSitrep(_sitrepRaw);
            if (btn) { btn.textContent = '📋 GENERATE BRIEFING'; btn.disabled = false; }
        })
        .catch(() => {
            setEl('sitrep-summary', 'Failed to generate briefing.');
            if (btn) { btn.textContent = '📋 GENERATE BRIEFING'; btn.disabled = false; }
        });
}
async function generateInlineBriefing(score, threatLevel, threatObjs, county, scoreInputs) {
    const el = document.getElementById('inline-briefing');
    if (!el) return;
    el.innerHTML = '<div style="color:#4a6280;font-size:10px;letter-spacing:1px;padding:4px 0;">⏳ Generating briefing...</div>';
    try {
        const r = await fetch('/api/sitrep', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                score,
                threat_level: threatLevel,
                threats: threatObjs || [],
                county,
                score_inputs: scoreInputs || null
            })
        });
        const d = await r.json();
        const text = (d.raw || d.text || '').trim();
        if (!text) { el.innerHTML = ''; return; }

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
                <div style="font-size:11px;color:#c8d8eb;line-height:1.6;margin-bottom:8px;">${sit}</div>
                ${act ? `<div style="font-size:9px;color:#a0acbd;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;">ACTIONS</div>
                <div style="font-size:11px;color:#c8d8eb;line-height:1.6;">${act}</div>` : ''}
            </div>`;
    } catch(e) {
        el.innerHTML = '';
    }
}

async function subscribeAlerts() {
    const email  = (document.getElementById('alert-email')?.value || '').trim();
    const status = document.getElementById('subscribe-status');
    if (!email || !email.includes('@')) {
        if (status) { status.textContent = 'Please enter a valid email.'; status.style.color = '#ff716c'; }
        return;
    }
    if (!_searchContext) {
        if (status) { status.textContent = 'Search a county first.'; status.style.color = '#ff716c'; }
        return;
    }
    try {
        const r = await fetch('/api/subscribe', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                email,
                county: _searchContext.label,
                lat: _searchContext.lat,
                lng: _searchContext.lng,
                radius: _searchContext.radius
            })
        });
        const d = await r.json();
        if (status) {
            status.textContent = d.message || (d.ok ? 'Subscribed!' : (d.error || 'Error.'));
            status.style.color = d.ok ? '#00e676' : '#ff716c';
        }
    } catch(e) {
        if (status) { status.textContent = 'Failed. Try again.'; status.style.color = '#ff716c'; }
    }
}

// ── LOCATE ME ─────────────────────────────────────────────
let _userMarker = null;
let _gpsOverride = null;  // set by locateMe() so searchLocation() can skip geocoding
function _showLocNote(msg, isError) {
    document.querySelectorAll('.loc-note').forEach(n => n.remove());
    const note = document.createElement('div');
    note.className = 'loc-note';
    note.style.cssText = `position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
        z-index:200;padding:12px 20px;font-size:11px;max-width:460px;text-align:center;
        font-family:Inter,sans-serif;line-height:1.6;
        background:${isError ? 'rgba(255,60,60,0.12)' : 'rgba(88,191,255,0.12)'};
        border:1px solid ${isError ? 'rgba(255,80,80,0.4)' : 'rgba(88,191,255,0.3)'};
        color:${isError ? '#ffc0c0' : '#a8d8ff'};`;
    note.innerHTML = msg;
    document.body.appendChild(note);
    setTimeout(() => note.remove(), 8000);
}

function locateMe() {
    console.log('[locateMe] called, geolocation:', !!navigator.geolocation);
    if (!navigator.geolocation) {
        _showLocNote('Geolocation not supported by this browser.', true);
        return;
    }
    document.getElementById('location-prompt')?.remove();
    _showLocNote('Requesting your location...', false);
    document.getElementById('address-input').value = 'Requesting location...';

    navigator.geolocation.getCurrentPosition(
        (pos) => {
            console.log('[locateMe] GPS success:', pos.coords.latitude, pos.coords.longitude);
            _showLocNote('Got GPS — loading place name...', false);
            const lat = pos.coords.latitude;
            const lng = pos.coords.longitude;
            document.getElementById('address-input').value = lat.toFixed(4) + ', ' + lng.toFixed(4);

            // Place blue marker immediately
            map.flyTo({ center: [lng, lat], zoom: 8, duration: 1800 });
            if (_userMarker) _userMarker.remove();
            const el = document.createElement('div');
            el.style.cssText = 'width:16px;height:16px;border-radius:50%;background:#58bfff;border:3px solid #fff;box-shadow:0 0 0 4px rgba(88,191,255,0.3),0 0 16px rgba(88,191,255,0.6);';
            _userMarker = new mapboxgl.Marker({ element: el }).setLngLat([lng, lat]).addTo(map);

            // Reverse-geocode then run threat analysis
            fetch('https://api.mapbox.com/geocoding/v5/mapbox.places/'
                + lng + ',' + lat
                + '.json?types=place,district,region&limit=1&access_token=' + MAPBOX_TOKEN_JS)
            .then(r => r.json())
            .then(rgd => {
                const feature = rgd.features?.[0] || null;
                _gpsOverride = { lat, lng, feature };
                const label = feature
                    ? feature.place_name.split(',').slice(0,2).join(',')
                    : lat.toFixed(4) + ', ' + lng.toFixed(4);
                document.getElementById('address-input').value = label;
                _showLocNote('📍 ' + label + ' — analyzing threats...', false);
                searchLocation();
            })
            .catch(() => {
                // Reverse geocode failed — run analysis with raw coords
                _gpsOverride = { lat, lng, feature: null };
                document.getElementById('address-input').value = lat.toFixed(4) + ', ' + lng.toFixed(4);
                _showLocNote('📍 Location found — analyzing nearby threats...', false);
                searchLocation();
            });
        },
        (err) => {
            console.log('[locateMe] GPS error code:', err.code, err.message);
            document.getElementById('address-input').value = 'Error code: ' + err.code;
            const msgs = {
                1: 'Location blocked (code 1). Chrome site settings show Allow but macOS may still block it — check System Preferences → Privacy & Security → Location Services → enable Chrome.',
                2: 'Could not determine your location (code 2). Try searching an address manually.',
                3: 'Location request timed out (code 3). Try again.'
            };
            _showLocNote(msgs[err.code] || 'Location unavailable (code ' + err.code + ').', true);
        },
        { timeout: 15000, enableHighAccuracy: false }
    );
}

// ── SEVERITY BAR ──────────────────────────────────────────
function updateSeverityBar(s) {
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
    if      (pct >= 70) { color = '#FF2D2D'; text = 'CRITICAL'; }
    else if (pct >= 45) { color = '#FF8C00'; text = 'ELEVATED'; }
    else if (pct >= 20) { color = '#FFCC00'; text = 'ADVISORY'; }
    else                { color = '#00CC66'; text = 'NORMAL';   }
    fill.style.width = pct + '%';
    fill.style.backgroundColor = color;
    fill.classList.toggle('sev-critical', pct >= 70);
    label.textContent = text + ' · ' + pct + '%';
    label.style.color = pct >= 45 ? color : 'rgba(255,255,255,0.35)';
}

// ── LIGHT / DARK THEME TOGGLE ─────────────────────────────
function toggleTheme() {
    const isLight = document.body.classList.toggle('light');
    localStorage.setItem('nhm-theme', isLight ? 'light' : 'dark');
    const icon = document.getElementById('theme-btn')?.querySelector('.material-symbols-outlined');
    if (icon) icon.textContent = isLight ? 'light_mode' : 'dark_mode';
}
// Apply saved theme on load
(function() {
    if (localStorage.getItem('nhm-theme') === 'light') {
        document.body.classList.add('light');
        const icon = document.getElementById('theme-btn')?.querySelector('.material-symbols-outlined');
        if (icon) icon.textContent = 'light_mode';
    }
})();

// ── KEYBOARD SHORTCUTS MODAL ──────────────────────────────
function openShortcuts()  { document.getElementById('shortcuts-modal').classList.add('open'); }
function closeShortcuts() { document.getElementById('shortcuts-modal').classList.remove('open'); }

// ── KEYBOARD SHORTCUTS ────────────────────────────────────
document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const key = e.key;
    if      (key === 'l' || key === 'L') { toggleLayerPanel(); }
    else if (key === 's' || key === 'S') { if (!document.getElementById('sitrep-overlay').classList.contains('open')) openSitrep(); }
    else if (key === 'r' || key === 'R') { loadData(); }
    else if (key === 'f' || key === 'F') { document.documentElement.requestFullscreen?.(); }
    else if (key === 'd' || key === 'D') { toggleTheme(); }
    else if (key === 'w' || key === 'W') { focusThreatPanel(); }
    else if (key === 'n' || key === 'N') { locateMe(); }
    else if (key === '?')                { openShortcuts(); }
    else if (key === 'Escape')           { closeSitrep(); closeShortcuts(); }
});

function clearSearch(resetInput=true, restoreData=true) {
    restoreLayerPanelAfterSearch();
    if (searchMarker) { searchMarker.remove(); searchMarker = null; }
    if (map.getLayer('buffer-fill'))    map.removeLayer('buffer-fill');
    if (map.getLayer('buffer-outline')) map.removeLayer('buffer-outline');
    if (map.getSource('search-buffer')) map.removeSource('search-buffer');
    document.getElementById('threat-results').style.display = 'none';
    document.getElementById('threat-results').innerHTML = '';
    document.getElementById('clear-search').style.display = 'none';
    if (resetInput) document.getElementById('address-input').value = '';
    if (restoreData) {
        // Allow loadData() to refresh sources again, then trigger a refresh
        // so all sources return to showing global data.
        _searchContext = null;
        loadData();
    }
}

