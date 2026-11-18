/* Santa Cruz household flood-risk location map.
 *
 * Two map engines, one shared state:
 *  - 2D: Leaflet with a real-map look from the LOCAL vector data (barangay
 *    land fill + dashed boundaries + permanent barangay name labels, OSM-style
 *    roads/water/buildings). Always works offline.
 *  - 3D: MapLibre GL (vendored locally) with tilted exploration, 3D extruded
 *    buildings and the same local vector data. The online basemaps (OSM
 *    streets / satellite) are optional in both modes and their failure is
 *    handled without breaking the app.
 *
 * The system never invents GIS values: the only synthetic numbers on screen
 * are the illustrative 3-D building heights, which are labelled as such.
 */
"use strict";

const $ = (id) => document.getElementById(id);

/* ----------------------------------------------------------- shared state */

let cfg = null;               // /api/config payload
let mode = "2d";              // "2d" | "3d"
let selected = null;          // {latitude, longitude}
let selectedBuilding = null;  // GeoJSON feature shared by both map views
let studyBounds = null;        // verified Santa Cruz municipal bounds
let lastExtraction = null;    // extraction result for `selected`
let currentHousehold = null;  // household object with location + latest_extraction
let showLabels = true;        // barangay name labels
let basemapStyle = "offline"; // "offline" | "streets" | "satellite"

/* Cached JSON (the 8 MB buildings payload is shared by both engines). */
const _jsonCache = new Map();
function fetchJSONCached(url) {
  if (!_jsonCache.has(url)) {
    _jsonCache.set(url,
      fetch(url).then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
        .catch((err) => { _jsonCache.delete(url); throw err; }));
  }

  function buildingCenter(feature) {
    const c = featureCentroid(feature.geometry);
    return c ? { latitude: c[0], longitude: c[1] } : null;
  }

  function showBuilding(feature) {
    selectedBuilding = feature;
    const props = feature.properties || {};
    const center = buildingCenter(feature);
    $("building-panel").hidden = false;
    $("bldg-title").textContent = props.name || "Mapped building footprint";
    const rows = [
      ["Source", props.source || "OpenStreetMap"],
      ["Building type", props.building || props.kind || "—"],
      ["OSM ID", props.osm_id || "—"],
      ["Height", props.height ? `${props.height} m (source tagged)` :
        props["building:levels"] ? `${props["building:levels"]} levels (estimated 3 m/level)` : "Not available"],
    ];
    if (center) rows.push(["Footprint center", `${center.latitude.toFixed(6)}, ${center.longitude.toFixed(6)}`]);
    $("bldg-attrs").innerHTML = rows.map(([k, v]) =>
      `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(v)}</td></tr>`).join("");
    $("bldg-note").textContent = center
      ? "A footprint is not proof of a household location. Use its center only as a starting point, then confirm or correct the household point."
      : "This footprint has no usable center; select a location manually.";
    if (buildingLayer && map.hasLayer(buildingLayer)) buildingLayer.setStyle(buildingStyle);
    $("btn-use-building").hidden = false;
  }

  function showPointOfInterest(feature) {
    selectedBuilding = null;
    const props = feature.properties || {};
    $("building-panel").hidden = false;
    $("bldg-title").textContent = props.name || "Named landmark";
    $("bldg-attrs").innerHTML = [
      ["Type", props.kind || "point of interest"],
      ["Source", "OpenStreetMap"],
      ["OSM ID", props.osm_id || "—"],
    ].map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(v)}</td></tr>`).join("");
    $("bldg-note").textContent = "This is a mapped point of interest. Its presence and name come from OpenStreetMap; it is not a household or building verification.";
    $("btn-use-building").hidden = true;
  }
  return _jsonCache.get(url);
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    const body = await res.json().catch(() => null);
    if (body) detail = typeof body.detail === "object" ? JSON.stringify(body.detail) : (body.detail || detail);
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  return res.json();
}

function setStatus(el, cls, text) {
  el.className = "status " + cls;
  el.textContent = text;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    c === "&" ? "\x26amp;" : c === "<" ? "\x26lt;" : c === ">" ? "\x26gt;"
      : c === '"' ? "\x26quot;" : "\x26#39;");
}

/* --------------------------------------------------- geometry (centroids) */

function ringArea(ring) {
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  return Math.abs(a / 2);
}

function ringCentroid(ring) { // -> [lat, lng]
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const x1 = ring[i][0], y1 = ring[i][1], x2 = ring[i + 1][0], y2 = ring[i + 1][1];
    const f = x1 * y2 - x2 * y1;
    a += f; cx += (x1 + x2) * f; cy += (y1 + y2) * f;
  }
  if (Math.abs(a) < 1e-14) {
    let sx = 0, sy = 0;
    for (const p of ring) { sx += p[0]; sy += p[1]; }
    return [sy / ring.length, sx / ring.length];
  }
  a *= 0.5;
  return [cy / (6 * a), cx / (6 * a)];
}

function featureCentroid(geom) { // -> [lat, lng] of the largest polygon part
  const polys = geom.type === "Polygon" ? [geom.coordinates]
    : geom.type === "MultiPolygon" ? geom.coordinates : null;
  if (!polys) return null;
  let best = null, bestA = -1;
  for (const poly of polys) {
    const A = ringArea(poly[0]);
    if (A > bestA) { bestA = A; best = poly[0]; }
  }
  return best ? ringCentroid(best) : null;
}

function featureLineMidpoint(geom) { // -> [lat, lng] at the midpoint of the longest line part
  const lines = geom.type === "LineString" ? [geom.coordinates]
    : geom.type === "MultiLineString" ? geom.coordinates : [];
  let best = null;
  let bestLength = -1;
  for (const line of lines) {
    if (!line || line.length < 2) continue;
    let length = 0;
    for (let i = 1; i < line.length; i++) {
      const dx = line[i][0] - line[i - 1][0];
      const dy = line[i][1] - line[i - 1][1];
      length += Math.hypot(dx, dy);
    }
    if (length > bestLength) {
      bestLength = length;
      best = line;
    }
  }
  if (!best) return null;
  let midpointDistance = bestLength / 2;
  for (let i = 1; i < best.length; i++) {
    const x1 = best[i - 1][0], y1 = best[i - 1][1];
    const x2 = best[i][0], y2 = best[i][1];
    const segmentLength = Math.hypot(x2 - x1, y2 - y1);
    if (midpointDistance <= segmentLength) {
      const fraction = segmentLength ? midpointDistance / segmentLength : 0;
      return [y1 + (y2 - y1) * fraction, x1 + (x2 - x1) * fraction];
    }
    midpointDistance -= segmentLength;
  }
  const last = best[best.length - 1];
  return [last[1], last[0]];
}

function lineLength(geom) {
  const lines = geom.type === "LineString" ? [geom.coordinates]
    : geom.type === "MultiLineString" ? geom.coordinates : [];
  return Math.max(0, ...lines.map((line) => line.reduce((total, point, i) => {
    if (!i) return total;
    return total + Math.hypot(point[0] - line[i - 1][0], point[1] - line[i - 1][1]);
  }, 0)));
}

/* Deterministic, illustrative building height (m) from the OSM way id.
 * Display-only: OSM has no height tags for most footprints here. */
function illustrativeHeight(osmId) {
  const s = String(osmId ?? "");
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  let m = 2.6 + (h % 60) / 10;          // 2.6 – 8.5 m
  if (h % 17 === 0) m += 4;             // occasional taller structure
  return Math.round(m * 10) / 10;
}

/* ============================================================ 2D: Leaflet */

const map = L.map("map", { preferCanvas: true, zoomControl: true });
map.setView([14.2733, 121.4153], 13);

let barangayLayer = null, barangayBorderLayer = null, municipalityBorderLayer = null;
let waterLayer = null, waterCasingLayer = null;
let roadLayer = null, roadCasingLayer = null;
let buildingLayer = null, supplementaryBuildingLayer = null, poiLayer = null;
let onlineBase = null, onlineBaseKind = null, onlineBaseFailed = false;
let marker = null;             // Leaflet selection marker
let barangayLabelMarkers = []; // Leaflet div markers
let buildingLabelMarkers = []; // Named building labels
let streetLabelMarkers = [];   // Named street labels
let labelsPane = null;

function makePane(name, z) {
  const p = map.createPane(name);
  p.style.zIndex = z;
  return p;
}

function addGeoJSONLayer(url, styleFn, paneName, interactive = true) {
  let pane = map.getPane(paneName);
  if (!pane) {
    pane = map.createPane(paneName);
    pane.style.zIndex = PANE_Z[paneName] ?? 400;
  }
  return fetchJSONCached(url)
    .then((fc) => L.geoJSON(fc, { style: styleFn, pane, interactive }))
    .catch((err) => {
      console.warn(`layer ${url} unavailable:`, err);
      return null; // local layer missing -> map still works
    });
}

const PANE_Z = { land: 340, osmCasing: 380, water: 390, osmRoads: 400, buildingsPane: 410, boundary: 450, labels: 650 };

/* OSM-Carto-inspired road styling (fill over casing), width scales with zoom. */
const ROAD_STYLE = {
  motorway: { w: 6, c: "#c24d80", f: "#e892a2" },
  motorway_link: { w: 3.4, c: "#c24d80", f: "#e892a2" },
  trunk: { w: 5.4, c: "#b06a5a", f: "#f9b29c" },
  trunk_link: { w: 3.2, c: "#b06a5a", f: "#f9b29c" },
  primary: { w: 5, c: "#a08300", f: "#fcd6a4" },
  primary_link: { w: 3, c: "#a08300", f: "#fcd6a4" },
  secondary: { w: 4.4, c: "#878757", f: "#f7fabf" },
  secondary_link: { w: 2.8, c: "#878757", f: "#f7fabf" },
  tertiary: { w: 3.8, c: "#93a055", f: "#ffffff" },
  tertiary_link: { w: 2.4, c: "#93a055", f: "#ffffff" },
  unclassified: { w: 3, c: "#c9c3b4", f: "#ffffff" },
  residential: { w: 3, c: "#c9c3b4", f: "#ffffff" },
  living_street: { w: 2.6, c: "#c9c3b4", f: "#f4f2ee" },
  service: { w: 1.8, c: "#d4cec2", f: "#ffffff" },
  pedestrian: { w: 2, c: "#d4cec2", f: "#e8e4da", dash: "2,3" },
  track: { w: 2.2, c: "#c3a375", f: "#dfc497", dash: "5,4" },
};

function zoomScale() {
  const z = map.getZoom();
  return z <= 13 ? 0.55 : z <= 15 ? 0.8 : z <= 17 ? 1.15 : 1.5;
}

function roadFillStyle(f) {
  const s = ROAD_STYLE[f.properties.kind] || ROAD_STYLE.residential;
  const w = (s.w * zoomScale());
  return { color: s.f, weight: Math.max(1.2, w), opacity: 0.95,
           dashArray: s.dash || null, lineCap: "round", lineJoin: "round" };
}

function roadCasingStyle(f) {
  const s = ROAD_STYLE[f.properties.kind] || ROAD_STYLE.residential;
  const w = (s.w * zoomScale()) + 1.8;
  return { color: s.c, weight: Math.max(2, w), opacity: 0.9,
           lineCap: "round", lineJoin: "round" };
}

const WATER_WIDTH = { river: 4.2, canal: 2.6, stream: 2.1, ditch: 1.5, drain: 1.5 };

function waterStyle(f) {
  const w = (WATER_WIDTH[f.properties.kind] || 1.8) * zoomScale();
  return { color: "#4d94d0", weight: Math.max(1.2, w), opacity: 0.95, lineCap: "round" };
}

function waterCasingStyle(f) {
  const w = (WATER_WIDTH[f.properties.kind] || 1.8) * zoomScale() + 2.2;
  return { color: "#a9c9e8", weight: Math.max(2.4, w), opacity: 0.65, lineCap: "round" };
}

function buildingStyle() {
  const z = map.getZoom();
  const id = arguments[0]?.properties?.osm_id;
  const selectedId = selectedBuilding?.properties?.osm_id;
  const selectedFootprint = id != null && selectedId != null && String(id) === String(selectedId);
  return { color: "#b9b0a2", weight: 0.7,
           fillColor: selectedFootprint ? "#2f80d0" : "#e6e0d6",
           fillOpacity: selectedFootprint ? 0.95 : z >= 14 ? 0.9 : 0.55, fill: true };
}

function supplementaryBuildingStyle() {
  const z = map.getZoom();
  return { color: "#8d78a8", weight: 0.6, fillColor: "#d9c9e8",
           fillOpacity: z >= 14 ? 0.65 : 0.35, fill: true };
}

function wireBuildingClicks(layer) {
  if (!layer) return;
  layer.eachLayer((featureLayer) => {
    const props = featureLayer.feature.properties || {};
    if (props.name) {
      const barangay = props.barangay_name ? `<br><span>Barangay: ${escapeHtml(props.barangay_name)}</span>` : "";
      featureLayer.bindTooltip(
        `<strong>${escapeHtml(props.name)}</strong><br><span>${escapeHtml(props.kind || "mapped building")} · OpenStreetMap</span>${barangay}`,
        { sticky: true, className: "poi-tip", direction: "top" });
    }
    featureLayer.on("click", (e) => {
      L.DomEvent.stopPropagation(e);
      showBuilding(featureLayer.feature);
      const center = buildingCenter(featureLayer.feature);
      if (center) selectPoint(center.latitude, center.longitude);
    });
  });
}

function roadTooltip(feature) {
  const p = feature.properties || {};
  const name = p.name || "Unnamed mapped road";
  const kind = p.kind || p.highway || "road";
  const barangay = p.barangay_name ? `<br><span>Barangay: ${escapeHtml(p.barangay_name)}</span>` : "";
  const note = p.name ? "" : "<br><span>No source street name is available</span>";
  return `<strong>${escapeHtml(name)}</strong><br><span>${escapeHtml(kind)} · OpenStreetMap</span>${barangay}${note}`;
}

function poiTooltip(feature) {
  const p = feature.properties || {};
  const barangay = p.barangay_name ? `<br><span>Barangay: ${escapeHtml(p.barangay_name)}</span>` : "";
  return `<strong>${escapeHtml(p.name || "Named landmark")}</strong><br>` +
    `<span>${escapeHtml(p.kind || "point of interest")} · OpenStreetMap</span>${barangay}`;
}

function wireRoadHover(layer) {
  if (!layer) return;
  layer.eachLayer((featureLayer) => featureLayer.bindTooltip(
    roadTooltip(featureLayer.feature),
    { sticky: true, className: "street-tip", direction: "top" }));
}

function barangayFillStyle() {
  return { stroke: false, fillColor: "#f4efe4",
           fillOpacity: basemapStyle === "offline" ? 0.5 : 0.1, fill: true };
}

function barangayBorderStyle() {
  const color = arguments[0]?.properties?.color || "#4d7898";
  return { color, weight: 4.5, opacity: 1, dashArray: "7,4", lineCap: "round",
    lineJoin: "round", fill: false };
}

function municipalityBorderStyle() {
  return { color: "#12355b", weight: 4, opacity: 1, fill: false };
}

function applyMunicipalityBounds(fc) {
  const coordinates = [];
  const visit = (value) => {
    if (typeof value[0] === "number") coordinates.push(value);
    else value.forEach(visit);
  };
  fc.features.forEach((feature) => visit(feature.geometry.coordinates));
  if (!coordinates.length) return;
  const bounds = L.latLngBounds(
    coordinates.map(([lng, lat]) => [lat, lng])
  );
  studyBounds = bounds;
  map.setMaxBounds(bounds.pad(0.015));
  map.options.maxBoundsViscosity = 1;
  map.fitBounds(bounds, { padding: [20, 20] });
  if (map3d) map3d.setMaxBounds([[bounds.getWest(), bounds.getSouth()],
    [bounds.getEast(), bounds.getNorth()]]);
}

function initPanes() {
  for (const [name, z] of Object.entries(PANE_Z)) makePane(name, z);
  labelsPane = map.getPane("labels");
  map.attributionControl.setPrefix(false);
  map.attributionControl.addAttribution(
    'Vector data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors');
}

async function initLayers() {
  const municipalityLayerPromise = addGeoJSONLayer("/api/municipality",
    municipalityBorderStyle, "boundary", false)
    .then((l) => { if (l) l.addTo(map); return l; });
  const barangayLayerPromise = addGeoJSONLayer("/api/barangays", barangayFillStyle, "land")
    .then((l) => {
      if (l) {
        l.bindTooltip((layer) => layer.feature.properties.name,
                      { sticky: true, className: "brgy-label", direction: "top" });
        l.addTo(map);
      }
      return l;
    });

  const barangayBorderLayerPromise = addGeoJSONLayer("/api/barangays", barangayBorderStyle, "boundary", false)
    .then((l) => { if (l) l.addTo(map); return l; });

  const waterCasingLayerPromise = addGeoJSONLayer("/api/osm/waterways", waterCasingStyle, "osmCasing", false);
  const waterLayerPromise = addGeoJSONLayer("/api/osm/waterways", waterStyle, "water");

  const roadCasingLayerPromise = addGeoJSONLayer("/api/osm/roads", roadCasingStyle, "osmCasing", false);
  const roadLayerPromise = addGeoJSONLayer("/api/osm/roads", roadFillStyle, "osmRoads")
    .then((l) => { wireRoadHover(l); return l; });

  const buildingLayerPromise = addGeoJSONLayer("/api/osm/buildings", buildingStyle, "buildingsPane", true)
    .then((l) => {
      wireBuildingClicks(l);
      return l;
    });
  const supplementaryBuildingLayerPromise = addGeoJSONLayer("/api/buildings/overture", supplementaryBuildingStyle,
    "buildingsPane", true).then((l) => { wireBuildingClicks(l); return l; });
  const poiLayerPromise = fetchJSONCached("/api/osm/pois").then((fc) => L.geoJSON(fc, {
    pane: "labels",
    pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
      radius: 5, color: "#7a3e91", weight: 1.5, fillColor: "#d9a7ed", fillOpacity: 0.9,
    }),
    onEachFeature: (feature, layer) => {
      layer.bindTooltip(poiTooltip(feature), { sticky: true, className: "poi-tip", direction: "top" });
      layer.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        const p = feature.properties || {};
        showPointOfInterest(feature);
        selectPoint(e.latlng.lat, e.latlng.lng);
      });
    },
  })).catch((err) => { console.warn("POI layer unavailable:", err); return null; });

  [municipalityBorderLayer, barangayLayer, barangayBorderLayer, waterCasingLayer, waterLayer,
   roadCasingLayer, roadLayer, buildingLayer, supplementaryBuildingLayer, poiLayer] =
    await Promise.all([municipalityLayerPromise, barangayLayerPromise, barangayBorderLayerPromise,
      waterCasingLayerPromise, waterLayerPromise, roadCasingLayerPromise, roadLayerPromise,
      buildingLayerPromise, supplementaryBuildingLayerPromise, poiLayerPromise]);
  fetchJSONCached("/api/municipality").then(applyMunicipalityBounds)
    .catch((err) => console.warn("municipality bounds unavailable:", err));
  if (municipalityBorderLayer) municipalityBorderLayer.bringToFront();
  sync2dFeatureLayers();
  /* zoom-dependent line widths + label visibility */
  map.on("zoomend", () => {
    const restyle = (layer, fn) => { if (layer && map.hasLayer(layer)) layer.setStyle(fn); };
    restyle(roadLayer, roadFillStyle); restyle(roadCasingLayer, roadCasingStyle);
    restyle(waterLayer, waterStyle); restyle(waterCasingLayer, waterCasingStyle);
    restyle(buildingLayer, buildingStyle);
    restyle(supplementaryBuildingLayer, supplementaryBuildingStyle);
    updateLabelVisibility2d();
    updatePoiVisibility2d();
  });
}

/* Permanent barangay name labels at polygon centroids. */
function addBarangayLabels2d() {
  fetchJSONCached("/api/barangays").then((fc) => {
    barangayLabelMarkers = fc.features
      .map((f) => ({ name: f.properties.name, c: featureCentroid(f.geometry) }))
      .filter((x) => x.c)
      .map(({ name, c }) => L.marker(c, {
        interactive: false, pane: "labels",
        icon: L.divIcon({
          className: "brgy-label-wrap",
          html: `<span class="brgy-label-text">${escapeHtml(name)}</span>`,
          iconSize: [0, 0],
        }),
      }));
    updateLabelVisibility2d();
  }).catch((err) => console.warn("barangay labels unavailable:", err));
}

function updateLabelVisibility2d() {
  if (!labelsPane) return;
  const on = showLabels && map.getZoom() >= 12;
  labelsPane.classList.toggle("labels-off", !on);
  barangayLabelMarkers.forEach((m) => { if (on) { if (!map.hasLayer(m)) m.addTo(map); } });
  const streetLabelsOn = $("lyr-street-labels")?.checked && $("lyr-roads")?.checked;
  const zoom = map.getZoom();
  const showAllStreetLabels = zoom >= 14;
  const majorRoadKinds = new Set(["motorway", "trunk", "primary", "secondary", "tertiary"]);
  streetLabelMarkers.forEach((m) => {
    const visible = streetLabelsOn && zoom >= 12 &&
      (showAllStreetLabels || majorRoadKinds.has(m.options.roadKind));
    if (visible) { if (!map.hasLayer(m)) m.addTo(map); }
    else if (map.hasLayer(m)) map.removeLayer(m);
  });
  const buildingsOn = $("lyr-buildings")?.checked && map.getZoom() >= 16;
  buildingLabelMarkers.forEach((m) => {
    if (buildingsOn) { if (!map.hasLayer(m)) m.addTo(map); }
    else if (map.hasLayer(m)) map.removeLayer(m);
  });
}

function addStreetLabels2d(roadData = null) {
  const dataPromise = roadData ? Promise.resolve(roadData) : fetchJSONCached("/api/osm/roads");
  dataPromise.then((fc) => {
    const namedRoads = new Map();
    for (const feature of fc.features || []) {
      const name = feature.properties?.name;
      const midpoint = featureLineMidpoint(feature.geometry);
      if (!name || !midpoint) continue;
      const barangay = feature.properties?.barangay_name || "";
      const key = `${name}|${barangay}`;
      const existing = namedRoads.get(key);
      if (!existing || lineLength(feature.geometry) > existing.length) {
        namedRoads.set(key, { feature, midpoint, length: lineLength(feature.geometry) });
      }
    }
    streetLabelMarkers = [...namedRoads.values()]
      .map((f) => {
        return L.marker(f.midpoint, {
          interactive: false,
          pane: "labels",
          roadKind: f.feature.properties.kind || "road",
          icon: L.divIcon({
            className: "street-label-wrap",
            html: `<span class="street-label-text">${escapeHtml(f.feature.properties.name)}</span>`,
            iconSize: [0, 0],
          }),
        });
      })
      .filter(Boolean);
    updateLabelVisibility2d();
  }).catch((err) => console.warn("street labels unavailable:", err));
}

function addBuildingLabels2d() {
  Promise.all([
    fetchJSONCached("/api/osm/buildings"),
    fetchJSONCached("/api/buildings/overture").catch(() => ({ features: [] })),
  ]).then(([osm, overture]) => {
    const features = [...(osm.features || []), ...(overture.features || [])];
    buildingLabelMarkers = features
      .filter((f) => f.properties?.name && f.geometry)
      .map((f) => ({ name: f.properties.name, c: featureCentroid(f.geometry), source: f.properties.source || "OpenStreetMap" }))
      .filter((x) => x.c)
      .map(({ name, c, source }) => L.marker(c, {
        interactive: false, pane: "labels",
        icon: L.divIcon({
          className: "building-label-wrap",
          html: `<span class="building-label-text">${escapeHtml(name)}</span>`,
          iconSize: [0, 0],
        }),
        title: `${name} (${source})`,
      }));
    updateLabelVisibility2d();
  }).catch((err) => console.warn("building labels unavailable:", err));
}

function updatePoiVisibility2d() {
  if (!poiLayer || basemapStyle !== "offline") return;
  const visible = $("lyr-pois")?.checked && map.getZoom() >= 14;
  if (visible && !map.hasLayer(poiLayer)) poiLayer.addTo(map);
  if (!visible && map.hasLayer(poiLayer)) map.removeLayer(poiLayer);
}

/* ------------------------------------------------------------ 2D basemap */

function basemapAttribution() {
  return basemapStyle === "satellite"
    ? "Imagery &copy; Esri, Maxar, Earthstar Geographics"
    : "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors";
}

function basemapTileUrl() {
  return basemapStyle === "satellite" ? cfg.satellite_tile_url : cfg.online_tile_url;
}

function applyBasemap2d() {
  if (basemapStyle === "offline") {
    if (onlineBase) { map.removeLayer(onlineBase); onlineBase = null; onlineBaseKind = null; }
  } else {
    if (onlineBase && onlineBaseKind !== basemapStyle) {
      map.removeLayer(onlineBase); onlineBase = null;
    }
    if (!onlineBase) {
      onlineBase = L.tileLayer(basemapTileUrl(), { maxZoom: 19, attribution: basemapAttribution() });
      onlineBaseKind = basemapStyle;
      onlineBase.on("tileerror", () => {
        if (!onlineBaseFailed) {
          onlineBaseFailed = true;
          $("basemap-note").textContent =
            "Online basemap tiles are unreachable (offline or blocked). Local layers remain usable.";
        }
      });
    }
    onlineBaseFailed = false;
    onlineBase.addTo(map);
    onlineBase.bringToBack();
  }
  if (barangayLayer && map.hasLayer(barangayLayer)) barangayLayer.setStyle(barangayFillStyle());
  sync2dFeatureLayers();
}

/* Local street-level features duplicate an online basemap; hide them there. */
function sync2dFeatureLayers() {
  const online = basemapStyle !== "offline";
  const apply = (layer, wanted) => {
    if (!layer) return;
    const want = !online && wanted;
    if (want && !map.hasLayer(layer)) layer.addTo(map);
    if (!want && map.hasLayer(layer)) map.removeLayer(layer);
  };
  apply(waterCasingLayer, $("lyr-waterways").checked);
  apply(waterLayer, $("lyr-waterways").checked);
  apply(roadCasingLayer, $("lyr-roads").checked);
  apply(roadLayer, $("lyr-roads").checked);
  apply(buildingLayer, $("lyr-buildings").checked);
  apply(supplementaryBuildingLayer, $("lyr-buildings").checked);
  apply(poiLayer, $("lyr-pois").checked && map.getZoom() >= 14);
}

function setBasemap(style) {
  basemapStyle = style;
  $("basemap-note").textContent =
    basemapStyle === "offline"
      ? cfg.note
      : basemapStyle === "streets" ? "OSM street tiles — needs internet."
      : "Esri World Imagery — needs internet.";
  applyBasemap2d();
  if (map3d) {
    map3d.setStyle(buildStyle3d());
    map3d.once("load", () => {
      hydrateBuildings3d();
      if (!$("lyr-terrain").checked) map3d.setTerrain(null);
    });
  }
}

/* ======================================================== 3D: MapLibre GL */

let map3d = null;                 // lazy: created on first switch to 3D
let marker3d = null;              // MapLibre selection marker
let barangayLabels3d = [];        // MapLibre DOM markers
let buildings3dData = null;       // FeatureCollection with height_m added

async function getBuildings3d() {
  if (!buildings3dData) {
    const fc = await fetchJSONCached("/api/osm/buildings");
    buildings3dData = {
      type: "FeatureCollection",
      features: fc.features.map((f) => ({
        ...f,
        properties: { ...f.properties, height_m: illustrativeHeight(f.properties.osm_id) },
      })),
    };
  }
  return buildings3dData;
}

function selectionFC3d() {
  return selected
    ? { type: "FeatureCollection",
        features: [{ type: "Feature",
                     properties: {},
                     geometry: { type: "Point", coordinates: [selected.longitude, selected.latitude] } }] }
    : { type: "FeatureCollection", features: [] };
}

function buildStyle3d() {
  const offline = basemapStyle === "offline";
  const sources = {
    barangays: { type: "geojson", data: "/api/barangays" },
    municipality: { type: "geojson", data: "/api/municipality" },
    buildings: { type: "geojson", data: "/api/osm/buildings" },
    roads: { type: "geojson", data: "/api/osm/roads" },
    waterways: { type: "geojson", data: "/api/osm/waterways" },
    pois: { type: "geojson", data: "/api/osm/pois" },
    selection: { type: "geojson", data: selectionFC3d() },
    terrain: { type: "raster-dem", tiles: ["/api/tiles/dem/{z}/{x}/{y}.png"],
               encoding: "terrarium", tileSize: 256, maxzoom: 15 },
  };
  const layers = [
    { id: "bg", type: "background",
      paint: { "background-color": offline ? "#e9ebe4" : "#0b0f14" } },
  ];
  if (!offline) {
    sources["base-raster"] = {
      type: "raster", tiles: [basemapTileUrl()], tileSize: 256, maxzoom: 19,
      attribution: basemapStyle === "satellite"
        ? "Imagery &copy; Esri, Maxar, Earthstar Geographics"
        : "&copy; OpenStreetMap contributors",
    };
    layers.push({ id: "base", type: "raster", source: "base-raster" });
  }
  layers.push(
    { id: "barangay-fill", type: "fill", source: "barangays",
      paint: { "fill-color": "#f4efe4", "fill-opacity": offline ? 0.45 : 0.12 } },
    { id: "barangay-outline", type: "line", source: "barangays",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["coalesce", ["get", "color"], "#8d99ae"],
        "line-width": 4, "line-opacity": 1, "line-dasharray": [5, 3] } },
    { id: "municipality-outline", type: "line", source: "municipality",
      paint: { "line-color": "#12355b", "line-width": 6, "line-opacity": 1 } },
    { id: "barangay-label", type: "symbol", source: "barangays", minzoom: 9,
      layout: { "text-field": ["get", "name"], "text-size": 12,
                "text-allow-overlap": false, "text-ignore-placement": false },
      paint: { "text-color": "#34495e", "text-halo-color": "#ffffff",
               "text-halo-width": 2 } },
    { id: "waterway-casing", type: "line", source: "waterways", minzoom: 10,
      layout: { "line-cap": "round" },
      paint: { "line-color": "#8fb8de", "line-opacity": 0.7,
               "line-width": ["interpolate", ["linear"], ["zoom"], 11,
                 ["match", ["get", "kind"], "river", 5.5, 3.2], 16,
                 ["match", ["get", "kind"], "river", 9, 5]] } },
    { id: "waterway-line", type: "line", source: "waterways", minzoom: 10,
      layout: { "line-cap": "round" },
      paint: { "line-color": "#3d7fc2",
               "line-width": ["interpolate", ["linear"], ["zoom"], 11,
                 ["match", ["get", "kind"], "river", 3.2, 1.6], 16,
                 ["match", ["get", "kind"], "river", 6, 3]] } },
    { id: "road-casing", type: "line", source: "roads", minzoom: 11,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#cfc8ba",
               "line-width": ["interpolate", ["linear"], ["zoom"], 13,
                 ["match", ["get", "kind"], ["motorway", "trunk", "primary"], 3,
                   ["secondary", "tertiary"], 2.2, 1.2],
                 16, ["match", ["get", "kind"], ["motorway", "trunk", "primary"], 9,
                   ["secondary", "tertiary"], 6.5, 4],
                 19, ["match", ["get", "kind"], ["motorway", "trunk", "primary"], 22,
                   ["secondary", "tertiary"], 16, 9]] } },
    { id: "road-line", type: "line", source: "roads", minzoom: 11,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["match", ["get", "kind"],
                 "motorway", "#e892a2", "trunk", "#f9b29c", "primary", "#fcd6a4",
                 "secondary", "#f7fabf", "track", "#dfc497", "#ffffff"],
               "line-width": ["interpolate", ["linear"], ["zoom"], 13,
                 ["match", ["get", "kind"], ["motorway", "trunk", "primary"], 1.8,
                   ["secondary", "tertiary"], 1.4, 1],
                 16, ["match", ["get", "kind"], ["motorway", "trunk", "primary"], 6,
                   ["secondary", "tertiary"], 4.6, 3],
                 19, ["match", ["get", "kind"], ["motorway", "trunk", "primary"], 15,
                   ["secondary", "tertiary"], 10, 6.5]] } },
    { id: "poi-point", type: "circle", source: "pois", minzoom: 15,
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 3.5, 17, 6],
               "circle-color": "#7a3e91", "circle-stroke-color": "#ffffff",
               "circle-stroke-width": 1.2, "circle-opacity": 0.9 } },
    { id: "terrain-hillshade", type: "hillshade", source: "terrain",
      paint: { "hillshade-shadow-color": "#44515b", "hillshade-highlight-color": "#ffffff",
               "hillshade-exaggeration": 0.35 } },
    { id: "building-fill", type: "fill", source: "buildings", minzoom: 13,
      paint: { "fill-color": "#e6e0d6", "fill-opacity": 0.85, "fill-outline-color": "#b9b0a2" } },
    { id: "building-extrusion", type: "fill-extrusion", source: "buildings", minzoom: 14.5,
      paint: { "fill-extrusion-color": ["interpolate", ["linear"],
        ["coalesce", ["get", "height_m"], 3],
        2.5, "#d8d1c4", 8.5, "#b0a493"],
      "fill-extrusion-height": ["coalesce", ["get", "height_m"], 3],
               "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.92 } },
    { id: "building-label", type: "symbol", source: "buildings", minzoom: 16,
      layout: { "text-field": ["coalesce", ["get", "name"], ""],
                "text-size": ["interpolate", ["linear"], ["zoom"], 16, 10, 18, 13],
                "text-offset": [0, 0.6], "text-anchor": "top",
                "text-allow-overlap": false, "text-ignore-placement": false },
      paint: { "text-color": "#684d35", "text-halo-color": "#ffffff",
               "text-halo-width": 1.5 } },
    { id: "poi-label", type: "symbol", source: "pois", minzoom: 16,
      layout: { "text-field": ["coalesce", ["get", "name"], ""],
                "text-size": 11, "text-offset": [0, 1.1],
                "text-anchor": "top", "text-allow-overlap": false },
      paint: { "text-color": "#633375", "text-halo-color": "#ffffff",
               "text-halo-width": 1.5 } },
    { id: "road-label", type: "symbol", source: "roads", minzoom: 14,
      layout: { "text-field": ["coalesce", ["get", "name"], ""],
                "text-size": ["interpolate", ["linear"], ["zoom"], 14, 10, 18, 13],
                "symbol-placement": "line", "text-allow-overlap": false,
                "text-ignore-placement": false },
      paint: { "text-color": "#4b5863", "text-halo-color": "#ffffff",
               "text-halo-width": 1.5 } },
    { id: "sel-glow", type: "circle", source: "selection",
      paint: { "circle-radius": 16, "circle-color": "#0b6bcb",
               "circle-opacity": 0.18, "circle-blur": 1 } },
    { id: "sel-dot", type: "circle", source: "selection",
      paint: { "circle-radius": 6.5, "circle-color": "#0b6bcb",
               "circle-stroke-width": 2.5, "circle-stroke-color": "#ffffff" } },
  );
  return { version: 8, sources, layers, terrain: { source: "terrain", exaggeration: terrainExaggeration() } };
}

function terrainExaggeration() {
  return Number($("exaggeration-select")?.value || 1);
}

async function hydrateBuildings3d() {
  const source = map3d.getSource("buildings");
  if (!source) return;
  const fc = await getBuildings3d();
  source.setData(fc);
}

function setLayerVisibility3d(id, visible) {
  if (!map3d.getLayer(id)) return;
  const want = visible ? "visible" : "none";
  if (map3d.getLayoutProperty(id, "visibility") !== want) {
    map3d.setLayoutProperty(id, "visibility", want);
  }
}

function apply3dToggles() {
  if (!map3d || !map3d.isStyleLoaded()) return;
  const vis = (id, on) => setLayerVisibility3d(id, on);
  vis("waterway-casing", $("lyr-waterways").checked);
  vis("waterway-line", $("lyr-waterways").checked);
  vis("road-casing", $("lyr-roads").checked);
  vis("road-line", $("lyr-roads").checked);
  vis("road-label", $("lyr-street-labels").checked && $("lyr-roads").checked);
  vis("poi-point", $("lyr-pois").checked);
  vis("poi-label", $("lyr-pois").checked);
  vis("terrain-hillshade", $("lyr-hillshade").checked);
  vis("building-fill", $("lyr-buildings").checked);
  vis("building-label", $("lyr-buildings").checked);
  vis("building-extrusion", $("lyr-buildings").checked);
  vis("barangay-fill", $("lyr-barangay").checked);
  vis("barangay-outline", $("lyr-barangay").checked);
  vis("municipality-outline", $("lyr-barangay").checked);
  vis("barangay-label", $("lyr-labels").checked && $("lyr-barangay").checked);
  barangayLabels3d.forEach((m) => {
    if (showLabels) { if (!m._added) { m.addTo(map3d); m._added = true; } }
    else if (m._added) { m.remove(); m._added = false; }
  });
}

function addBarangayLabels3d() {
  // Barangay names use the symbol layer so MapLibre declutters them and keeps
  // them visible at municipality-wide zoom levels.
  barangayLabels3d = [];
  return Promise.resolve();
}

let hoverPending = null, hoverLast = 0;

function wireHover3d() {
  const canvas = map3d.getCanvas();
  const render = () => {
    if (!hoverPending) return;
    const e = hoverPending;
    const layers = ["road-line", "road-label", "poi-point", "poi-label",
      "waterway-line", "building-fill", "building-label", "building-extrusion"]
      .filter((id) => map3d.getLayer(id));
    const feats = layers.length ? map3d.queryRenderedFeatures(e.point, { layers }) : [];
    const named = feats.find((f) => f.properties && (f.properties.name || f.layer.id.startsWith("road-")));
    const el = $("hover3d");
    if (named) {
      const p = named.properties || {};
      const featureName = p.name || `Unnamed mapped road (${p.kind || "road"})`;
      el.textContent = p.barangay_name ? `${featureName} · ${p.barangay_name}` : featureName;
      el.hidden = false;
      el.style.left = Math.min(e.point.x + 14, map3d.getCanvas().clientWidth - 160) + "px";
      el.style.top = (e.point.y + 12) + "px";
    } else {
      el.hidden = true;
    }
  };
  map3d.on("mousemove", (e) => {
    hoverPending = e;
    const now = Date.now();
    if (now - hoverLast > 90) { hoverLast = now; render(); }
  });
  canvas.addEventListener("mouseleave", () => { $("hover3d").hidden = true; });
}

function updateMarker3d() {
  if (!map3d) return;
  if (!selected) {
    if (marker3d) { marker3d.remove(); marker3d = null; }
  } else if (!marker3d) {
    marker3d = new maplibregl.Marker({ draggable: true, color: "#0b6bcb" })
      .setLngLat([selected.longitude, selected.latitude]).addTo(map3d);
    marker3d.on("dragend", () => {
      const p = marker3d.getLngLat();
      selectPoint(p.lat, p.lng);
    });
  } else {
    marker3d.setLngLat([selected.longitude, selected.latitude]);
  }
  const src = map3d.getSource("selection");
  if (src) src.setData(selectionFC3d());
}

function flyTo3d(lat, lng) {
  if (!map3d) return;
  map3d.flyTo({
    center: [lng, lat],
    zoom: Math.max(map3d.getZoom(), 16.8),
    pitch: 55, bearing: map3d.getBearing(),
    duration: 1200, essential: true,
  });
}

async function ensureMap3d() {
  if (map3d) return map3d;
  const c = map.getCenter();
  map3d = new maplibregl.Map({
    container: "map3d",
    style: buildStyle3d(),
    center: [c.lng, c.lat],
    zoom: map.getZoom() + 1, // MapLibre 512px tiles run one zoom ahead of Leaflet
    pitch: 0, bearing: 0,
    maxZoom: 19.5,
    attributionControl: { compact: true },
  });
  if (studyBounds) {
    map3d.setMaxBounds([[studyBounds.getWest(), studyBounds.getSouth()],
      [studyBounds.getEast(), studyBounds.getNorth()]]);
  }
  map3d.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
  map3d.addControl(new maplibregl.ScaleControl({ maxWidth: 150, unit: "metric" }), "bottom-left");
  map3d.on("click", (e) => {
    const buildings = map3d.getLayer("building-fill") ? map3d.queryRenderedFeatures(e.point, {
      layers: ["building-fill", "building-extrusion"],
    }) : [];
    const pois = map3d.getLayer("poi-point") ? map3d.queryRenderedFeatures(e.point, {
      layers: ["poi-point", "poi-label"],
    }) : [];
    if (buildings.length) showBuilding(buildings[0]);
    else if (pois.length) showPointOfInterest(pois[0]);
    selectPoint(e.lngLat.lat, e.lngLat.lng);
  });
  map3d.on("error", (e) => {
    if (basemapStyle !== "offline" && !onlineBaseFailed) {
      onlineBaseFailed = true;
      $("basemap-note").textContent =
        "Online basemap tiles are unreachable (offline or blocked). Local layers remain usable.";
    } else if (e && e.error) {
      console.warn("3D map resource error:", e.error.message || e.error);
    }
  });
  map3d.on("styledata", apply3dToggles);
  await new Promise((resolve) => map3d.once("load", resolve));
  await hydrateBuildings3d();
  if (!$("lyr-terrain").checked) map3d.setTerrain(null);
  await addBarangayLabels3d();
  wireHover3d();
  updateMarker3d();
  apply3dToggles();
  return map3d;
}

/* ------------------------------------------------------- 2D/3D mode switch */

async function setMode(m) {
  if (m === mode) return;
  mode = m;
  $("mode-2d").classList.toggle("active", m === "2d");
  $("mode-3d").classList.toggle("active", m === "3d");
  $("map").classList.toggle("hidden", m !== "2d");
  $("map3d").classList.toggle("hidden", m !== "3d");
  $("hint3d").hidden = m !== "3d";
  if (m === "3d") {
    try {
      await ensureMap3d();
      map3d.resize();
      if (selected) flyTo3d(selected.latitude, selected.longitude);
      else map3d.easeTo({ pitch: 55, bearing: -15, duration: 900 });
    } catch (err) {
      console.warn("3D unavailable:", err);
      mode = "2d";
      $("map").classList.remove("hidden");
      $("map3d").classList.add("hidden");
      $("hint3d").hidden = true;
      $("mode-3d").classList.remove("active");
      $("mode-2d").classList.add("active");
      setStatus($("sel-status"), "error",
        `3-D view could not start (WebGL may be disabled). 2-D map still works. (${err.message})`);
    }
  } else {
    if (map3d) {
      const c = map3d.getCenter();
      map.setView([c.lat, c.lng], Math.max(2, map3d.getZoom() - 1));
    }
    map.invalidateSize();
    updateLabelVisibility2d();
  }
}

/* ------------------------------------------------------------- selection */

function setMarker(lat, lng) {
  if (marker) marker.setLatLng([lat, lng]);
  else marker = L.marker([lat, lng], { draggable: true }).addTo(map);
  marker.on("dragend", () => {
    const p = marker.getLatLng();
    selectPoint(p.lat, p.lng);
  });
}

async function selectPoint(lat, lng, { pan = false } = {}) {
  selected = { latitude: lat, longitude: lng };
  setMarker(lat, lng);
  updateMarker3d();
  if (pan) {
    if (mode === "3d" && map3d) flyTo3d(lat, lng);
    else map.panTo([lat, lng]);
  }
  $("lat").value = lat.toFixed(6);
  $("lon").value = lng.toFixed(6);
  setStatus($("sel-status"), "info", `Selected: ${lat.toFixed(6)}, ${lng.toFixed(6)} (lat, lon — WGS 84). Press “Extract GIS features”.`);
  $("barangay-line").textContent = "";
  $("features-panel").hidden = true;
  lastExtraction = null;

  // Resolve barangay immediately for feedback.
  try {
    const res = await api("/api/extract", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latitude: lat, longitude: lng }),
    });
    lastExtraction = res;
    const b = res.barangay ? res.barangay.name : "not within Santa Cruz barangay boundaries";
    const inside = res.inside_gis_coverage === "inside";
    $("barangay-line").textContent = `Barangay: ${b}`;
    if (currentHousehold) {
      try {
        const updated = await api(`/api/households/${currentHousehold.household_id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ barangay_name: res.barangay ? res.barangay.name : null }),
        });
        showHouseholdDetail({ ...currentHousehold, ...updated });
      } catch (err) {
        setStatus($("household-status"), "error",
          `Barangay detected as ${b}, but it could not be recorded: ${err.message}`);
      }
    }
    if (!inside) {
      setStatus($("sel-status"), "error",
        res.inside_gis_coverage === "outside"
          ? "Selected point is OUTSIDE the available GIS coverage (outside all Santa Cruz barangay polygons). Features cannot be extracted."
          : "Barangay boundary data unavailable; coverage cannot be confirmed.");
    }
    renderFeatures(res);
  } catch (err) {
    setStatus($("sel-status"), "error", `Location rejected: ${err.message}`);
  }
}

const FEATURE_LABELS = {
  ElevationMeters: "Elevation",
  SlopeDegrees: "Slope",
  DistanceToWaterwayM: "Distance to mapped waterway",
  LandCoverCode: "Land-cover code",
  LandCoverClass: "Land-cover class",
  HistoricalWaterOccurrencePct: "Historical surface-water occurrence (1984–2024)",
};

function fmt(v, digits) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(digits === undefined ? 2 : digits);
  return String(v);
}

function renderFeatures(result) {
  $("features-panel").hidden = false;
  const tbody = $("features-table").querySelector("tbody");
  tbody.innerHTML = "";
  const statuses = Object.values(result.features);
  const nOk = statuses.filter((f) => f.status === "ok").length;
  const nTot = statuses.length;

  if (result.extraction_status === "ok") {
    setStatus($("features-status"), "ok", `Extraction successful — ${nOk}/${nTot} features valid.`);
  } else if (result.extraction_status === "partial") {
    setStatus($("features-status"), "partial", `Partial data — ${nOk}/${nTot} features valid; the rest are missing (see below).`);
  } else {
    setStatus($("features-status"), "error", "Extraction failed — no feature could be read at this point.");
  }

  for (const [name, f] of Object.entries(result.features)) {
    const tr = document.createElement("tr");
    const value = f.status === "ok" ? fmt(f.value, 2) : "—";
    const reason = f.status !== "ok" && f.reason ? ` (${f.reason})` : "";
    tr.innerHTML = `<td>${FEATURE_LABELS[name] || name}</td>
      <td class="num">${value}</td><td>${f.unit}</td>
      <td><span class="tag ${f.status}">${f.status}${reason}</span></td>`;
    tbody.appendChild(tr);
  }

  const notes = (result.notes || []).concat(
    statuses.filter((f) => f.note).map((f) => `${FEATURE_LABELS[f.feature] || f.feature}: ${f.note}`));
  $("features-notes").textContent = notes.length ? notes.join("\n") : "";

  const sources = [...new Set(statuses.filter((f) => f.source_raster).map((f) => `${FEATURE_LABELS[f.feature] || f.feature} ← ${f.source_raster}`))];
  $("provenance").textContent = sources.length ? "Source layers:\n" + sources.join("\n") : "";
}

/* ----------------------------------------------------------- households */

async function refreshHouseholds(selectId) {
  const households = await api("/api/households");
  const sel = $("household-select");
  sel.innerHTML = '<option value="">— select household —</option>' +
    households.map((h) => {
      const loc = h.latitude != null ? ` (${Number(h.latitude).toFixed(5)}, ${Number(h.longitude).toFixed(5)})` : "";
      const verified = h.verification_status === "verified" ? " ✓" : "";
      return `<option value="${h.household_id}">${h.code || "HH-" + h.household_id}${verified}${loc}</option>`;
    }).join("");
  if (selectId) sel.value = String(selectId);
}

function showHouseholdDetail(h) {
  currentHousehold = h;
  const rows = [];
  rows.push(["Code", h.code || "—"], ["Barangay (recorded)", h.barangay_name || "—"]);
  rows.push(["Household size", h.household_size ?? "—"], ["Children", h.children_count ?? "—"]);
  rows.push(["Elderly", h.elderly_count ?? "—"], ["PWD members", h.pwd_count ?? "—"]);
  rows.push(["House material", h.house_material || "—"]);
  if (h.location) {
    rows.push(["Latitude", h.location.latitude], ["Longitude", h.location.longitude]);
    rows.push(["Coordinate source", h.location.coordinate_source]);
    rows.push(["Verification", h.location.verification_status]);
    rows.push(["Captured at", h.location.captured_at]);
  } else {
    rows.push(["Location", "none yet"]);
  }
  const e = h.latest_extraction;
  if (e) {
    rows.push(["Last extraction", `${e.extraction_status} at ${e.created_at}`]);
    rows.push(["Elevation", e.elevation_m != null ? e.elevation_m.toFixed(2) + " m" : "—"]);
    rows.push(["Slope", e.slope_degrees != null ? e.slope_degrees.toFixed(2) + "°" : "—"]);
    rows.push(["Distance to waterway", e.distance_waterway_m != null ? e.distance_waterway_m.toFixed(1) + " m" : "—"]);
    rows.push(["Land cover", e.land_cover_class ? `${e.land_cover_class} (${e.land_cover_code})` : "—"]);
    rows.push(["Water occurrence", e.water_occurrence_pct != null ? e.water_occurrence_pct.toFixed(1) + " %" : "—"]);
  } else {
    rows.push(["Last extraction", "none yet"]);
  }
  $("household-detail").innerHTML = "<table>" +
    rows.map(([k, v]) => `<tr><td>${k}</td><td>${v ?? "—"}</td></tr>`).join("") + "</table>";

  const hasLoc = !!h.location;
  $("btn-extract-household").disabled = !hasLoc;
  $("btn-verify").disabled = !hasLoc || h.location.verification_status === "verified";
}

async function onHouseholdChange() {
  const id = $("household-select").value;
  if (!id) { currentHousehold = null; $("household-detail").innerHTML = ""; return; }
  try {
    const h = await api(`/api/households/${id}`);
    showHouseholdDetail(h);
    setStatus($("household-status"), "info", `Loaded household ${h.code || h.household_id}.`);
    if (h.location) {
      await selectPoint(h.location.latitude, h.location.longitude, { pan: true });
    }
  } catch (err) {
    setStatus($("household-status"), "error", err.message);
  }
}

async function saveLocationToHousehold() {
  if (!selected) { setStatus($("household-status"), "error", "Select a location on the map first."); return; }
  const id = $("household-select").value;
  if (!id) { setStatus($("household-status"), "error", "Select or create a household first."); return; }
  const body = {
    latitude: selected.latitude, longitude: selected.longitude,
    coordinate_source: "map_click", confirm_overwrite: true,
  };
  // Ask for explicit confirmation when replacing a verified location.
  try {
    const h = await api(`/api/households/${id}`);
    if (h.location && h.location.verification_status === "verified") {
      const ok = confirm("This household's location is VERIFIED. Replacing it will retire the verified point "
        + "(it stays in history). Overwrite with the selected point?");
      if (!ok) return;
    }
    const loc = await api(`/api/households/${id}/location`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const barangay = loc.barangay_name || "outside Santa Cruz barangay boundaries";
    setStatus($("household-status"),
      "ok", `Location saved in ${barangay} (source: ${loc.coordinate_source}, ${loc.verification_status}). Now extract features.`);
    await refreshHouseholds(id);
    await onHouseholdChange();
  } catch (err) {
    setStatus($("household-status"), "error", err.message);
  }
}

async function extractForHousehold() {
  const id = $("household-select").value;
  if (!id) return;
  setStatus($("household-status"), "loading", "Extracting GIS features…");
  try {
    const saved = await api(`/api/households/${id}/extract`, { method: "POST" });
    renderFeatures(saved.result);
    setStatus($("household-status"),
      saved.extraction_status === "ok" ? "ok" : saved.extraction_status === "partial" ? "partial" : "error",
      `Extraction ${saved.extraction_status} (saved as run #${saved.extraction_id}).`);
    await refreshHouseholds(id);
    await onHouseholdChange();
  } catch (err) {
    setStatus($("household-status"), "error", err.message);
  }
}

async function verifyLocation() {
  const id = $("household-select").value;
  if (!id) return;
  const ok = confirm("Confirm that the household's location has been independently verified in the field?\n"
    + "Verified locations require explicit confirmation before being replaced.");
  if (!ok) return;
  try {
    const loc = await api(`/api/households/${id}/verify`, { method: "POST" });
    setStatus($("household-status"), "ok", `Location marked ${loc.verification_status}.`);
    await refreshHouseholds(id);
    await onHouseholdChange();
  } catch (err) {
    setStatus($("household-status"), "error", err.message);
  }
}

function openHouseholdDialog() {
  const dlg = $("dlg-household");
  dlg.showModal();
}

async function submitHouseholdDialog(e) {
  e.preventDefault();
  const f = e.target;
  const num = (name) => f[name].value === "" ? null : Number(f[name].value);
  const body = {
    code: f.code.value || null, label: f.label.value || null,
    household_size: num("household_size"), children_count: num("children_count"),
    elderly_count: num("elderly_count"), pwd_count: num("pwd_count"),
    house_material: f.house_material.value || null, notes: f.notes.value || null,
  };
  try {
    const h = await api("/api/households", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    $("dlg-household").close();
    await refreshHouseholds(h.household_id);
    await onHouseholdChange();
    setStatus($("household-status"), "ok", `Household ${h.code || h.household_id} created.`);
  } catch (err) {
    alert("Could not create household: " + err.message);
  }
}

/* ---------------------------------------------------------------- init */

function initToggles() {
  $("lyr-waterways").addEventListener("change", () => { sync2dFeatureLayers(); apply3dToggles(); });
  $("lyr-roads").addEventListener("change", () => { sync2dFeatureLayers(); apply3dToggles(); });
  $("lyr-street-labels").addEventListener("change", () => {
    updateLabelVisibility2d();
    apply3dToggles();
  });
  $("lyr-pois").addEventListener("change", () => { sync2dFeatureLayers(); apply3dToggles(); });
  $("lyr-buildings").addEventListener("change", () => {
    sync2dFeatureLayers();
    updateLabelVisibility2d();
    apply3dToggles();
  });
  $("lyr-terrain").addEventListener("change", () => {
    if (!map3d || !map3d.isStyleLoaded()) return;
    map3d.setTerrain($("lyr-terrain").checked
      ? { source: "terrain", exaggeration: terrainExaggeration() } : null);
  });
  $("lyr-hillshade").addEventListener("change", () => apply3dToggles());
  $("exaggeration-select").addEventListener("change", () => {
    if (map3d && map3d.isStyleLoaded() && $("lyr-terrain").checked) {
      map3d.setTerrain({ source: "terrain", exaggeration: terrainExaggeration() });
    }
  });
  $("lyr-barangay").addEventListener("change", () => {
    if ($("lyr-barangay").checked) {
      if (barangayLayer && !map.hasLayer(barangayLayer)) barangayLayer.addTo(map);
      if (barangayBorderLayer && !map.hasLayer(barangayBorderLayer)) barangayBorderLayer.addTo(map);
    } else {
      if (barangayLayer) map.removeLayer(barangayLayer);
      if (barangayBorderLayer) map.removeLayer(barangayBorderLayer);
    }
    apply3dToggles();
  });
  $("lyr-labels").addEventListener("change", () => {
    showLabels = $("lyr-labels").checked;
    updateLabelVisibility2d();
    apply3dToggles();
  });
  $("basemap-select").addEventListener("change", (e) => setBasemap(e.target.value));
  $("btn-use-building").addEventListener("click", () => {
    const center = selectedBuilding && buildingCenter(selectedBuilding);
    if (center) selectPoint(center.latitude, center.longitude, { pan: true });
  });
  $("btn-dismiss-building").addEventListener("click", () => {
    selectedBuilding = null;
    $("building-panel").hidden = true;
    if (buildingLayer && map.hasLayer(buildingLayer)) buildingLayer.setStyle(buildingStyle);
  });
  $("btn-reset-cam").addEventListener("click", () => {
    if (mode === "3d" && map3d) {
      map3d.easeTo({ pitch: 0, bearing: 0, duration: 600 });
    } else {
      map.setView([14.2733, 121.4153], 13);
    }
  });
}

function initModeSwitch() {
  $("mode-2d").addEventListener("click", () => setMode("2d"));
  $("mode-3d").addEventListener("click", () => setMode("3d"));
}

async function init() {
  initPanes();
  initToggles();

  // Health / coverage banner.
  try {
    const h = await api("/api/health");
    const brgys = h.barangays_with_polygons;
    $("health").textContent =
      `GIS: ${h.municipal_layers.length} municipal layers, ${brgys} barangay polygons, root ${h.gis_root_exists ? "found" : "MISSING"}` +
      (h.warnings.length ? ` — ${h.warnings.length} warning(s)` : "");
    if (!h.gis_root_exists || h.municipal_layers.length === 0) {
      setStatus($("sel-status"), "error", "GIS raster data not found on the server. Extraction is unavailable; check FLR_GIS_OUTPUT_DIR.");
    }
    if (h.warnings.length) console.warn("GIS warnings:", h.warnings);
  } catch (err) {
    $("health").textContent = "server health check failed";
  }

  try {
    cfg = await api("/api/config");
    $("basemap-note").textContent = cfg.note;
  } catch (err) {
    cfg = { online_tile_url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            satellite_tile_url: "", note: "server config unavailable" };
    $("basemap-note").textContent = cfg.note;
  }

  // The controls are intentionally initialized before this large preload.
  await initLayers();
  addBarangayLabels2d();
  addStreetLabels2d();
  addBuildingLabels2d();

  map.on("click", (e) => selectPoint(e.latlng.lat, e.latlng.lng));

  $("btn-goto").addEventListener("click", () => {
    const lat = parseFloat($("lat").value), lon = parseFloat($("lon").value);
    if (Number.isNaN(lat) || Number.isNaN(lon)) {
      setStatus($("sel-status"), "error", "Enter numeric latitude and longitude (latitude first, WGS 84).");
      return;
    }
    selectPoint(lat, lon, { pan: true });
  });

  $("btn-extract").addEventListener("click", async () => {
    if (!selected) { setStatus($("sel-status"), "error", "Select a location first."); return; }
    setStatus($("sel-status"), "loading", "Extracting…");
    await selectPoint(selected.latitude, selected.longitude);
  });

  $("household-select").addEventListener("change", onHouseholdChange);
  $("btn-new-household").addEventListener("click", openHouseholdDialog);
  $("form-household").addEventListener("submit", submitHouseholdDialog);
  $("btn-save-location").addEventListener("click", saveLocationToHousehold);
  $("btn-extract-household").addEventListener("click", extractForHousehold);
  $("btn-verify").addEventListener("click", verifyLocation);

  await refreshHouseholds();
}

// Keep the mode controls responsive even if a later data preload is slow.
initModeSwitch();
init();
