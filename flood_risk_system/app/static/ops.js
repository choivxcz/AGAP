'use strict';
/* MDRRMO operations console. All user-supplied text is inserted with textContent (see h()), never innerHTML. */
const $ = id => document.getElementById(id);

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'style') el.style.cssText = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const tip = text => h('span', {text});   // tooltips as text nodes: Leaflet would parse strings as HTML
const badge = (cls, text) => h('span', {class: `badge ${cls}`, text});
const fmt = n => n == null ? '–' : Number(n).toLocaleString(undefined, {maximumFractionDigits: 1});
const km = m => m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
const when = iso => iso ? new Date(iso).toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : '';
const EVAC_TEXT = {at_home: 'At home', advised: 'Advised to evacuate', evacuated: 'Evacuated', rescued: 'Rescued', unaccounted: 'Unaccounted'};
const FAC_TEXT = {evacuation_center: 'Evacuation centre', rescue_base: 'Rescue base', hospital: 'Hospital'};
const RISK_COLOR = {High: '#c0392b', Medium: '#e67e22', Low: '#2e8b57'};
const PRIO_COLOR = {critical: '#8e1b1b', high: '#d35400', medium: '#c9a227', low: '#7f8c8d'};
const ROUTE_COLOR = {None: '#1a9e5f', Low: '#f1c40f', Medium: '#e67e22', High: '#c0392b', impassable: '#c0392b', blocked: '#111'};

const S = {meta: null, scenario: null, residents: [], incidents: [], facilities: [], pick: null,
           routeDest: null, routeOrigin: null, knownIncidents: null, selectedResident: null, selectedIncident: null};

/* ------------------------------------------------------------ API with optional PIN */
let PIN = '';
try { PIN = sessionStorage.getItem('opsPin') || ''; } catch (_) { /* storage blocked */ }
let pinWaiters = null;
function askPin() {
  if (!pinWaiters) { pinWaiters = []; $('pin-dialog').showModal(); $('pin').focus(); }
  return new Promise(resolve => pinWaiters.push(resolve));
}
$('pin-form').addEventListener('submit', async e => {
  e.preventDefault();
  const res = await fetch('/api/ops/meta', {headers: {'X-Ops-Pin': $('pin').value}});
  if (res.status === 401) { $('pin-err').textContent = 'Wrong PIN.'; return; }
  PIN = $('pin').value;
  try { sessionStorage.setItem('opsPin', PIN); } catch (_) { /* storage blocked */ }
  $('pin-dialog').close(); pinWaiters.forEach(r => r()); pinWaiters = null;
});

async function api(url, {method = 'GET', body, raw = false} = {}) {
  for (;;) {
    const res = await fetch(url, {method, headers: {'Content-Type': 'application/json', 'X-Ops-Pin': PIN},
                                  body: body === undefined ? undefined : JSON.stringify(body)});
    if (res.status === 401) { await askPin(); continue; }
    if (raw) { if (!res.ok) throw new Error('Request failed'); return res; }
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(Array.isArray(d) ? d.map(x => `${x.loc?.slice(-1)[0] ?? ''} ${x.msg}`.replace('Value error, ', '').trim()).join('; ') : d || `Request failed (${res.status})`);
    }
    return data;
  }
}

/* ------------------------------------------------------------ map */
const map = L.map('map', {preferCanvas: true, zoomControl: true}).setView([14.275, 121.415], 13);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap contributors · Flood: Project NOAH'}).addTo(map);
map.createPane('hazard').style.zIndex = 350;
map.createPane('routes').style.zIndex = 450;
const layers = {hazard: L.layerGroup().addTo(map), res: L.layerGroup().addTo(map), inc: L.layerGroup().addTo(map),
                fac: L.layerGroup().addTo(map), route: L.layerGroup().addTo(map), pick: L.layerGroup().addTo(map)};
for (const [box, key] of [['ly-hazard', 'hazard'], ['ly-res', 'res'], ['ly-inc', 'inc'], ['ly-fac', 'fac']])
  $(box).addEventListener('change', e => e.target.checked ? layers[key].addTo(map) : map.removeLayer(layers[key]));

const HAZARD_STYLE = {Low: {fillColor: '#9ecae1', fillOpacity: .35}, Medium: {fillColor: '#3182bd', fillOpacity: .45}, High: {fillColor: '#08306b', fillOpacity: .55}};
let hazardShown = null;
async function loadHazard(rp) {
  if (hazardShown === rp) return;
  hazardShown = rp;
  const geo = await api(`/api/ops/hazard/${rp}.geojson`);
  layers.hazard.clearLayers();
  L.geoJSON(geo, {pane: 'hazard', interactive: false,
    style: f => ({stroke: false, ...(HAZARD_STYLE[f.properties.HazardClass] || {})})}).addTo(layers.hazard);
}

function startPick(text, cb) {
  S.pick = cb; $('pick-banner').textContent = text + ' (Esc to cancel)'; $('pick-banner').classList.remove('hidden');
  map.getContainer().style.cursor = 'crosshair';
}
function endPick() { S.pick = null; $('pick-banner').classList.add('hidden'); map.getContainer().style.cursor = ''; }
map.on('click', e => {
  if (!S.pick) return;
  const cb = S.pick; endPick();
  layers.pick.clearLayers(); L.circleMarker(e.latlng, {radius: 7, color: '#0f5c63', weight: 3, fillOpacity: .2}).addTo(layers.pick);
  cb(e.latlng.lat, e.latlng.lng);
});
document.addEventListener('keydown', e => { if (e.key === 'Escape' && S.pick) endPick(); });

/* ------------------------------------------------------------ tabs */
function showTab(name) {
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-body').forEach(s => s.classList.toggle('hidden', s.id !== 'tab-' + name));
}
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

/* ------------------------------------------------------------ scenario */
function renderScenario(s) {
  S.scenario = s;
  $('sc-status').className = `badge status-${s.status}`; $('sc-status').textContent = s.status_label;
  $('sc-text').textContent = `${s.label} · ${fmt(s.Rainfall24hMm)} mm/24 h · ${fmt(s.EffectiveRainMm)} mm effective · NOAH ${s.hazard_map} map`;
  const g = $('guidance'); g.replaceChildren(h('b', {text: `${s.status_label}: `}), s.guidance, h('br'), h('span', {class: 'muted', text: s.hazard_map_note}));
  g.className = 'notice' + (['severe', 'extreme'].includes(s.status) ? ' bad' : s.status === 'normal' ? ' good' : '');
  loadHazard(s.hazard_map).catch(() => {});
}
$('sc-change').addEventListener('click', () => {
  const s = S.scenario || {};
  $('sc-24').value = s.Rainfall24hMm ?? 0; $('sc-3').value = s.Rainfall3DayMm ?? 0; $('sc-7').value = s.Rainfall7DayMm ?? 0;
  $('sc-label').value = s.source === 'default' ? '' : s.label || ''; $('sc-err').textContent = ''; $('sc-note').textContent = '';
  $('sc-form').dataset.source = 'manual';
  $('sc-dialog').showModal();
});
$('sc-cancel').addEventListener('click', () => $('sc-dialog').close());
['sc-24', 'sc-3', 'sc-7'].forEach(id => $(id).addEventListener('input', () => { $('sc-form').dataset.source = 'manual'; }));
$('sc-events').addEventListener('change', e => {
  const ev = S.events?.[e.target.value]; if (!ev) return;
  $('sc-24').value = ev.Rainfall24hMm; $('sc-3').value = ev.Rainfall3DayMm; $('sc-7').value = ev.Rainfall7DayMm;
  $('sc-label').value = `Past event ${ev.EventDate} (${ev.EventType})`; $('sc-form').dataset.source = 'historical';
  $('sc-note').textContent = 'NASA POWER rain of a past event. Use it for drills and planning.';
});
$('sc-forecast').addEventListener('click', async () => {
  $('sc-err').textContent = ''; $('sc-note').textContent = 'Loading forecast…';
  try {
    const f = await api('/api/ops/scenario/forecast');
    $('sc-24').value = f.scenario.Rainfall24hMm; $('sc-3').value = f.scenario.Rainfall3DayMm; $('sc-7').value = f.scenario.Rainfall7DayMm;
    $('sc-label').value = f.scenario.label; $('sc-form').dataset.source = 'forecast';
    $('sc-note').textContent = `Next days (mm): ${f.candidates.map(c => `${c.date.slice(5)} ${c.Rainfall24hMm}`).join(' · ')}. ${f.note}`;
  } catch (err) { $('sc-note').textContent = ''; $('sc-err').textContent = err.message; }
});
$('sc-apply').addEventListener('click', async () => {
  $('sc-err').textContent = ''; $('sc-apply').disabled = true;
  try {
    const s = await api('/api/ops/scenario', {method: 'PUT', body: {Rainfall24hMm: Number($('sc-24').value), Rainfall3DayMm: Number($('sc-3').value),
      Rainfall7DayMm: Number($('sc-7').value), source: $('sc-form').dataset.source || 'manual', label: $('sc-label').value || 'Manual entry'}});
    $('sc-dialog').close(); renderScenario(s); await refreshAll();
  } catch (err) { $('sc-err').textContent = err.message; } finally { $('sc-apply').disabled = false; }
});

/* ------------------------------------------------------------ overview */
async function loadSummary() {
  const s = await api('/api/ops/summary');
  renderScenario(s.scenario);
  const r = s.residents, inc = s.incidents, f = s.facilities;
  const kpi = (label, value, alert) => h('div', {class: 'kpi' + (alert ? ' alert' : '')}, h('small', {text: label}), h('b', {text: typeof value === 'string' ? value : fmt(value)}));
  $('kpis').replaceChildren(
    kpi('High-risk households', r.by_risk.High || 0, r.by_risk.High),
    kpi('High-risk, not yet evacuated', r.high_risk_not_evacuated, r.high_risk_not_evacuated),
    kpi('People in High-risk households', r.high_risk_people),
    kpi('…of whom children, elders, PWD', r.high_risk_vulnerable),
    kpi('Open incident reports', inc.open, inc.by_priority.critical),
    kpi('Critical reports', inc.by_priority.critical || 0, inc.by_priority.critical),
    kpi('Evacuees / centre capacity', `${fmt(f.occupancy)} / ${f.capacity ? fmt(f.capacity) : '?'}`),
    kpi('Registered households', r.total));
  $('brgy-table').replaceChildren(...s.barangays.map(b => h('tr', {},
    h('td', {text: b.barangay || '–'}), h('td', {class: 'num', text: b.residents}),
    h('td', {class: 'num', text: b.High, style: b.High ? 'color:#c0392b;font-weight:700' : ''}), h('td', {class: 'num', text: b.Medium}),
    h('td', {class: 'num', text: b.evacuated_people}), h('td', {class: 'num', text: b.open_incidents}))));
  const top = S.residents.filter(x => !['evacuated', 'rescued'].includes(x.evac_status)).slice(0, 8);
  $('top-residents').replaceChildren(...(top.length ? top.map(residentItem) : [h('p', {class: 'small-text muted', text: 'No registered households yet. Register them under Residents, or run scripts/seed_demo_residents.py for demo data.'})]));
  const n = inc.open; $('inc-count').textContent = n; $('inc-count').classList.toggle('hidden', !n);
}

/* ------------------------------------------------------------ residents */
function residentItem(r) {
  return h('div', {class: 'item' + (S.selectedResident === r.resident_id ? ' selected' : ''), onclick: () => selectResident(r.resident_id)},
    h('span', {class: 'name', text: `${r.full_name}`}), badge(`risk-${r.current_risk}`, r.current_risk || 'n/a'),
    h('span', {class: 'meta', text: `${r.ref_code} · ${r.barangay || '?'} · ${r.house_members} members` +
      `${r.pwd_count ? ` · ${r.pwd_count} PWD` : ''}${r.elder_count ? ` · ${r.elder_count} elder` : ''}${r.children_count ? ` · ${r.children_count} child` : ''}` +
      ` · ${EVAC_TEXT[r.evac_status]} · priority ${fmt(r.priority_score)}`}));
}
async function loadResidents() {
  S.residents = await api('/api/ops/residents?compact=true');
  const brgys = [...new Set(S.residents.map(r => r.barangay).filter(Boolean))].sort();
  const sel = $('res-brgy'), cur = sel.value;
  sel.replaceChildren(h('option', {value: '', text: 'All barangays'}), ...brgys.map(b => h('option', {value: b, text: b})));
  sel.value = brgys.includes(cur) ? cur : '';
  renderResidentList(); drawResidents();
}
function filteredResidents() {
  const q = $('res-q').value.trim().toLowerCase(), risk = $('res-risk').value, b = $('res-brgy').value;
  return S.residents.filter(r => (!risk || r.current_risk === risk) && (!b || r.barangay === b) &&
    (!q || [r.full_name, r.ref_code, r.address_text, r.contact_number].some(s => s && s.toLowerCase().includes(q))));
}
function renderResidentList() {
  const rows = filteredResidents();
  $('res-count').textContent = `${rows.length} household(s), sorted by evacuation priority${rows.length > 300 ? ' (first 300 shown)' : ''}`;
  $('res-list').replaceChildren(...rows.slice(0, 300).map(residentItem));
}
['res-q', 'res-risk', 'res-brgy'].forEach(id => $(id).addEventListener('input', renderResidentList));
function drawResidents() {
  layers.res.clearLayers();
  for (const r of S.residents) {
    const done = ['evacuated', 'rescued'].includes(r.evac_status);
    L.circleMarker([r.latitude, r.longitude], {radius: r.current_risk === 'High' ? 6 : 4.5, weight: done ? 2 : 1,
      color: done ? '#1e8449' : '#fff', fillColor: RISK_COLOR[r.current_risk] || '#888', fillOpacity: done ? .35 : .9})
      .bindTooltip(tip(`${r.full_name} · ${r.current_risk || '?'} · ${EVAC_TEXT[r.evac_status]}`))
      .on('click', () => selectResident(r.resident_id)).addTo(layers.res);
  }
}
async function selectResident(id) {
  S.selectedResident = id; showTab('residents');
  const r = await api(`/api/ops/residents/${id}`);
  map.setView([r.latitude, r.longitude], Math.max(map.getZoom(), 16));
  const base = r.baseline || {};
  const probs = r.current_probs ? `High ${Math.round(r.current_probs.High * 100)}% · Medium ${Math.round(r.current_probs.Medium * 100)}% · Low ${Math.round(r.current_probs.Low * 100)}%` : '';
  const f = r.features || {};
  const fv = k => f[k]?.value ?? '–';
  const centres = S.facilities.filter(x => x.type === 'evacuation_center' && x.is_active);
  const statusSel = h('select', {style: 'width:auto'}, ...Object.entries(EVAC_TEXT).map(([k, v]) => h('option', {value: k, text: v, selected: k === r.evac_status})));
  const centreSel = h('select', {style: 'width:auto;max-width:200px'}, h('option', {value: '', text: '(centre)'}),
    ...centres.map(c => h('option', {value: c.facility_id, text: c.name, selected: c.facility_id === r.evac_facility_id})));
  const err = h('p', {class: 'error'});
  $('res-detail').replaceChildren(h('div', {class: 'detail'},
    h('div', {class: 'row'}, h('h4', {text: r.full_name}), h('span', {class: 'spacer'}), badge(`risk-${r.current_risk}`, `${r.current_risk || '?'} now`)),
    h('dl', {},
      h('dt', {text: 'Ref / contact'}), h('dd', {text: `${r.ref_code} · ${r.contact_number || 'no number'}`}),
      h('dt', {text: 'Address'}), h('dd', {text: `${r.address_text || '–'}, ${r.barangay || ''}`}),
      h('dt', {text: 'Household'}), h('dd', {text: `${r.house_members} members (head ${r.age} y): ${r.children_count} children, ${r.elder_count} elders, ${r.pwd_count} PWD · ${r.housing_material}`}),
      r.special_needs ? [h('dt', {text: 'Special needs'}), h('dd', {text: r.special_needs, style: 'color:#a93226;font-weight:600'})] : null,
      h('dt', {text: 'Now (scenario)'}), h('dd', {text: `${r.current_risk || '?'} · ${probs}`}),
      h('dt', {text: 'Major storms'}), h('dd', {text: base.extreme_counts ? `${r.baseline_risk} (15 extreme past events: High ${base.extreme_counts.High}, Medium ${base.extreme_counts.Medium}, Low ${base.extreme_counts.Low})` : r.baseline_risk || '–'}),
      h('dt', {text: 'Location data'}), h('dd', {text: `Elev ${fmt(fv('ElevationMeters'))} m · ${fmt(fv('DistanceToWaterwayM'))} m to waterway · NOAH 100-yr ${fv('HouseFloodHazard100yr')} · MGB ${fv('MGBFloodSusceptibility')}`}),
      h('dt', {text: 'Priority score'}), h('dd', {text: fmt(r.priority_score)})),
    h('div', {class: 'row'}, statusSel, centreSel, h('button', {class: 'small', text: 'Update status', onclick: async () => {
      try { await api(`/api/ops/residents/${id}/status`, {method: 'POST', body: {evac_status: statusSel.value, evac_facility_id: centreSel.value ? Number(centreSel.value) : null}}); await refreshAll(); selectResident(id); }
      catch (e) { err.textContent = e.message; } }})),
    h('div', {class: 'row', style: 'margin-top:8px'},
      h('button', {class: 'small', text: 'Rescue route', onclick: () => routeTo({resident_id: id}, `${r.ref_code} ${r.full_name}`, true)}),
      h('button', {class: 'ghost small', text: 'Evacuation route', onclick: () => { setRouteDest({resident_id: id}, `${r.ref_code} ${r.full_name}`); showTab('routing'); evacRoute(); }}),
      h('button', {class: 'ghost small', text: 'Log incident', onclick: () => openIncidentForm(r.latitude, r.longitude, id)}),
      h('button', {class: 'ghost small', text: 'Edit', onclick: () => openResidentForm(r)}),
      h('button', {class: 'danger small', text: 'Delete', onclick: async () => {
        if (!confirm(`Delete ${r.full_name} (${r.ref_code}) from the registry?`)) return;
        await api(`/api/ops/residents/${id}`, {method: 'DELETE'}); $('res-detail').replaceChildren(); await refreshAll(); }})),
    r.incidents?.length ? h('div', {class: 'log'}, ...r.incidents.map(i => h('div', {text: `${i.ref_code} ${S.meta.incident_types[i.type]} · ${i.status}`}))) : null,
    err));
  renderResidentList();
}
let resPoint = null, resEditing = null;
function openResidentForm(r = null) {
  const form = $('res-form'); form.reset(); resEditing = r?.resident_id ?? null; resPoint = null; $('res-err').textContent = '';
  $('res-form-title').textContent = r ? `Edit ${r.ref_code}` : 'Register household';
  if (r) {
    for (const el of form.elements) if (el.name && r[el.name] != null) el.value = r[el.name];
    resPoint = {latitude: r.latitude, longitude: r.longitude};
  }
  $('res-loc').textContent = resPoint ? `${resPoint.latitude.toFixed(5)}, ${resPoint.longitude.toFixed(5)}` : 'No location';
  form.classList.remove('hidden'); showTab('residents'); form.scrollIntoView({block: 'nearest'});
}
$('res-add').addEventListener('click', () => openResidentForm());
$('res-cancel').addEventListener('click', () => $('res-form').classList.add('hidden'));
$('res-pick').addEventListener('click', () => startPick('Click the household location', (lat, lng) => {
  resPoint = {latitude: lat, longitude: lng}; $('res-loc').textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`; }));
$('res-form').addEventListener('submit', async e => {
  e.preventDefault(); $('res-err').textContent = '';
  if (!resPoint) { $('res-err').textContent = 'Pick the household location on the map.'; return; }
  const body = {...resPoint, location_source: 'map_click'};
  for (const el of e.target.elements) {
    if (!el.name) continue;
    body[el.name] = el.type === 'number' ? (el.value === '' ? null : Number(el.value)) : (el.value.trim() || null);
  }
  const btn = e.submitter; btn.disabled = true;
  try {
    const r = await api(resEditing ? `/api/ops/residents/${resEditing}` : '/api/ops/residents', {method: resEditing ? 'PUT' : 'POST', body});
    $('res-form').classList.add('hidden'); layers.pick.clearLayers(); await refreshAll(); selectResident(r.resident_id);
  } catch (err) { $('res-err').textContent = err.message; } finally { btn.disabled = false; }
});
$('res-export').addEventListener('click', async () => {
  const p = new URLSearchParams(); if ($('res-brgy').value) p.set('barangay', $('res-brgy').value); if ($('res-risk').value) p.set('risk', $('res-risk').value);
  const res = await api('/api/ops/residents/export.csv?' + p, {raw: true});
  const a = h('a', {href: URL.createObjectURL(await res.blob()), download: `evacuation_priority_${new Date().toISOString().slice(0, 10)}.csv`});
  document.body.append(a); a.click(); a.remove();
});

/* ------------------------------------------------------------ incidents */
function incidentItem(i) {
  return h('div', {class: 'item' + (S.selectedIncident === i.incident_id ? ' selected' : ''), onclick: () => selectIncident(i.incident_id)},
    h('span', {class: 'name', text: `${S.meta.incident_types[i.type] || i.type}`}),
    h('span', {}, badge(`prio-${i.priority}`, i.priority), ' ', badge(`st-${i.status}`, i.status)),
    h('span', {class: 'meta', text: `${i.ref_code} · ${i.barangay || '?'} · ${when(i.created_at)}` +
      `${i.water_depth ? ` · water ${i.water_depth.replace('_', ' ')}` : ''}${i.people_affected ? ` · ${i.people_affected} people` : ''} · ${i.source}` +
      `${i.assigned_team ? ` · ${i.assigned_team}` : ''}`}));
}
async function loadIncidents() {
  const all = await api('/api/ops/incidents');
  const fresh = S.knownIncidents ? all.filter(i => !S.knownIncidents.has(i.incident_id)) : [];
  S.knownIncidents = new Set(all.map(i => i.incident_id));
  S.incidents = all;
  const urgent = fresh.filter(i => ['critical', 'high'].includes(i.priority));
  if (urgent.length) toast(`New ${urgent[0].priority} report: ${S.meta.incident_types[urgent[0].type]} in ${urgent[0].barangay} (${urgent[0].ref_code})` + (urgent.length > 1 ? ` and ${urgent.length - 1} more` : ''), () => selectIncident(urgent[0].incident_id));
  renderIncidentList(); drawIncidents();
}
function renderIncidentList() {
  const f = $('inc-filter').value;
  const rows = S.incidents.filter(i => f === 'all' || (f === 'open' ? ['new', 'verified', 'dispatched'].includes(i.status) : i.status === f));
  $('inc-list').replaceChildren(...(rows.length ? rows.map(incidentItem) : [h('p', {class: 'small-text muted', text: 'No reports in this view.'})]));
}
$('inc-filter').addEventListener('input', renderIncidentList);
function drawIncidents() {
  layers.inc.clearLayers();
  for (const i of S.incidents) {
    if (['resolved', 'dismissed'].includes(i.status)) continue;
    const blocked = i.type === 'road_blocked';
    L.circleMarker([i.latitude, i.longitude], {radius: 9, weight: 3, color: blocked ? '#111' : '#fff', dashArray: i.status === 'new' ? '3 3' : null,
      fillColor: PRIO_COLOR[i.priority], fillOpacity: .95})
      .bindTooltip(tip(`${i.ref_code} · ${S.meta.incident_types[i.type]} · ${i.priority} · ${i.status}`))
      .on('click', () => selectIncident(i.incident_id)).addTo(layers.inc);
  }
}
async function selectIncident(id) {
  S.selectedIncident = id; showTab('incidents');
  const i = await api(`/api/ops/incidents/${id}`);
  map.setView([i.latitude, i.longitude], Math.max(map.getZoom(), 16));
  const team = h('input', {placeholder: 'Team (e.g. WASAR 1)', value: i.assigned_team || '', style: 'width:150px'});
  const note = h('input', {placeholder: 'Note for the log', style: 'flex:1;width:auto'});
  const err = h('p', {class: 'error'});
  const act = (label, status, cls = 'small') => h('button', {class: cls, text: label, disabled: i.status === status, onclick: async () => {
    try { await api(`/api/ops/incidents/${id}`, {method: 'PATCH', body: {status, assigned_team: team.value || null, note: note.value || null}}); await refreshAll(); selectIncident(id); }
    catch (e) { err.textContent = e.message; } }});
  $('inc-detail').replaceChildren(h('div', {class: 'detail'},
    h('div', {class: 'row'}, h('h4', {text: `${S.meta.incident_types[i.type]} · ${i.ref_code}`}), h('span', {class: 'spacer'}), badge(`prio-${i.priority}`, i.priority), badge(`st-${i.status}`, i.status)),
    h('dl', {},
      h('dt', {text: 'Where'}), h('dd', {text: `${i.barangay} · ${i.latitude.toFixed(5)}, ${i.longitude.toFixed(5)}`}),
      h('dt', {text: 'Water'}), h('dd', {text: i.water_depth ? S.meta.depths[i.water_depth] : 'not stated'}),
      h('dt', {text: 'People'}), h('dd', {text: i.people_affected ?? 'not stated'}),
      h('dt', {text: 'Reporter'}), h('dd', {text: `${i.reporter_name || 'anonymous'} · ${i.reporter_contact || 'no number'} · via ${i.source}`}),
      h('dt', {text: 'Details'}), h('dd', {text: i.description || '–'}),
      h('dt', {text: 'Team'}), h('dd', {text: i.assigned_team || '–'})),
    i.status === 'new' && i.source === 'public' ? h('p', {class: 'notice', text: 'Unverified public report: call the reporter or check on the ground, then Verify. It does not affect routing until verified.'}) : null,
    h('div', {class: 'row'}, team, note),
    h('div', {class: 'row', style: 'margin-top:6px'}, act('Verify', 'verified'), act('Dispatch', 'dispatched'), act('Resolve', 'resolved', 'ghost small'), act('Dismiss', 'dismissed', 'ghost small'),
      h('button', {class: 'ghost small', text: 'Add note', onclick: async () => { if (!note.value) return; await api(`/api/ops/incidents/${id}`, {method: 'PATCH', body: {note: note.value}}); selectIncident(id); }})),
    h('div', {class: 'row', style: 'margin-top:6px'}, h('button', {class: 'small', text: 'Plan rescue route', onclick: () => routeTo({incident_id: id}, `${i.ref_code} ${S.meta.incident_types[i.type]}`, true)}),
      h('button', {class: 'ghost small', text: 'Evacuation route from here', onclick: () => { setRouteDest({incident_id: id}, i.ref_code); showTab('routing'); evacRoute(); }})),
    err,
    h('div', {class: 'log'}, ...i.log.map(l => h('div', {text: `${when(l.at)} · ${l.action}${l.note ? ' · ' + l.note : ''}`})))));
  renderIncidentList();
}
let incPoint = null, incResident = null;
function openIncidentForm(lat = null, lng = null, residentId = null) {
  $('inc-form').reset(); $('inc-err').textContent = ''; incResident = residentId;
  incPoint = lat == null ? null : {latitude: lat, longitude: lng};
  $('inc-loc').textContent = incPoint ? `${lat.toFixed(5)}, ${lng.toFixed(5)}${residentId ? ' (resident)' : ''}` : 'No location';
  $('inc-form').classList.remove('hidden'); showTab('incidents');
}
$('inc-add').addEventListener('click', () => openIncidentForm());
$('inc-cancel').addEventListener('click', () => $('inc-form').classList.add('hidden'));
$('inc-pick').addEventListener('click', () => startPick('Click the incident location', (lat, lng) => { incPoint = {latitude: lat, longitude: lng}; $('inc-loc').textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`; }));
$('inc-form').addEventListener('submit', async e => {
  e.preventDefault(); $('inc-err').textContent = '';
  if (!incPoint) { $('inc-err').textContent = 'Pick the location on the map.'; return; }
  const fd = new FormData(e.target);
  const body = {...incPoint, type: fd.get('type'), water_depth: fd.get('water_depth') || null,
    people_affected: fd.get('people_affected') ? Number(fd.get('people_affected')) : null, description: fd.get('description') || null, resident_id: incResident};
  try { const i = await api('/api/ops/incidents', {method: 'POST', body}); $('inc-form').classList.add('hidden'); layers.pick.clearLayers(); await refreshAll(); selectIncident(i.incident_id); }
  catch (err) { $('inc-err').textContent = err.message; }
});

/* ------------------------------------------------------------ facilities */
const FAC_ICON = {evacuation_center: ['E', '#1e8449'], rescue_base: ['B', '#0f5c63'], hospital: ['H', '#c0392b']};
async function loadFacilities() {
  S.facilities = await api('/api/ops/facilities');
  renderFacilities(); drawFacilities(); fillOriginSelect();
}
function drawFacilities() {
  layers.fac.clearLayers();
  for (const f of S.facilities) {
    const [letter, color] = FAC_ICON[f.type];
    const icon = L.divIcon({className: '', iconSize: [20, 20], html: `<div style="width:20px;height:20px;border-radius:5px;background:${f.is_active ? color : '#aaa'};color:#fff;font:700 11px/20px sans-serif;text-align:center;border:2px solid #fff;box-shadow:0 1px 4px #0006">${letter}</div>`});
    L.marker([f.latitude, f.longitude], {icon, opacity: f.is_active ? 1 : .6}).bindTooltip(tip(`${FAC_TEXT[f.type]}: ${f.name}${f.is_active ? '' : ' (inactive)'}`)).addTo(layers.fac);
  }
}
function renderFacilities() {
  const type = $('fac-filter').value;
  const rows = S.facilities.filter(f => f.type === type);
  $('fac-list').replaceChildren(...rows.map(f => {
    const hz = f.hazard?.noah?.[S.scenario?.hazard_map || '100yr'] || '?';
    const cap = h('input', {type: 'number', min: 0, value: f.capacity ?? '', placeholder: 'capacity', style: 'width:90px'});
    return h('div', {class: 'item', style: 'cursor:default'},
      h('span', {class: 'name', text: f.name, style: 'cursor:pointer', onclick: () => map.setView([f.latitude, f.longitude], 17)}),
      h('span', {}, f.is_active ? badge('risk-Low', 'active') : badge('risk-null', 'inactive')),
      h('span', {class: 'meta', text: `${f.barangay || '?'} · NOAH ${S.scenario?.hazard_map || ''} at site: ${hz} · MGB ${f.hazard?.mgb || '?'}` +
        `${f.type === 'evacuation_center' ? ` · ${f.occupancy} / ${f.capacity ?? '?'} people` : ''}${f.notes ? ' · ' + f.notes : ''}`}),
      h('span', {class: 'row', style: 'grid-column:1/-1'}, f.type === 'evacuation_center' ? cap : null,
        f.type === 'evacuation_center' ? h('button', {class: 'ghost small', text: 'Save capacity', onclick: async () => { await api(`/api/ops/facilities/${f.facility_id}`, {method: 'PATCH', body: {capacity: cap.value === '' ? null : Number(cap.value)}}); loadFacilities(); }}) : null,
        h('button', {class: 'ghost small', text: f.is_active ? 'Deactivate' : 'Activate', onclick: async () => { await api(`/api/ops/facilities/${f.facility_id}`, {method: 'PATCH', body: {is_active: !f.is_active}}); loadFacilities(); }})));
  }));
}
$('fac-filter').addEventListener('input', renderFacilities);
let facPoint = null;
$('fac-add').addEventListener('click', () => { $('fac-form').reset(); facPoint = null; $('fac-loc').textContent = 'No location'; $('fac-form').classList.remove('hidden'); });
$('fac-cancel').addEventListener('click', () => $('fac-form').classList.add('hidden'));
$('fac-pick').addEventListener('click', () => startPick('Click the facility location', (lat, lng) => { facPoint = {latitude: lat, longitude: lng}; $('fac-loc').textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`; }));
$('fac-form').addEventListener('submit', async e => {
  e.preventDefault(); $('fac-err').textContent = '';
  if (!facPoint) { $('fac-err').textContent = 'Pick the location on the map.'; return; }
  const fd = new FormData(e.target);
  try { await api('/api/ops/facilities', {method: 'POST', body: {...facPoint, name: fd.get('name'), type: fd.get('type'), capacity: fd.get('capacity') ? Number(fd.get('capacity')) : null}});
    $('fac-form').classList.add('hidden'); layers.pick.clearLayers(); $('fac-filter').value = fd.get('type'); loadFacilities(); }
  catch (err) { $('fac-err').textContent = err.message; }
});

/* ------------------------------------------------------------ routing */
function fillOriginSelect() {
  const sel = $('rt-origin'), cur = sel.value;
  const bases = S.facilities.filter(f => f.is_active && f.type !== 'evacuation_center');
  sel.replaceChildren(h('option', {value: 'nearest_base', text: 'Nearest rescue base (automatic)'}),
    ...bases.map(f => h('option', {value: 'f' + f.facility_id, text: `${FAC_TEXT[f.type]}: ${f.name}`})), h('option', {value: 'point', text: 'Point on map…'}));
  sel.value = [...sel.options].some(o => o.value === cur) ? cur : 'nearest_base';
}
$('rt-origin').addEventListener('input', () => $('rt-origin-pick').classList.toggle('hidden', $('rt-origin').value !== 'point'));
$('rt-pick-o').addEventListener('click', () => startPick('Click where the team starts', (lat, lng) => { S.routeOrigin = {latitude: lat, longitude: lng}; $('rt-o-text').textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`; }));
function setRouteDest(place, label) { S.routeDest = place; $('rt-d-text').textContent = label; }
$('rt-pick-d').addEventListener('click', () => startPick('Click the destination', (lat, lng) => setRouteDest({latitude: lat, longitude: lng}, `Point ${lat.toFixed(5)}, ${lng.toFixed(5)}`)));
$('rt-vehicle').addEventListener('input', () => { $('rt-vnote').textContent = S.meta.vehicles[$('rt-vehicle').value].note; });
$('rt-clear').addEventListener('click', () => { layers.route.clearLayers(); $('rt-result').replaceChildren(); });
function originPayload() {
  const v = $('rt-origin').value;
  if (v === 'nearest_base') return 'nearest_base';
  if (v === 'point') { if (!S.routeOrigin) throw new Error('Pick the start point on the map.'); return S.routeOrigin; }
  return {facility_id: Number(v.slice(1))};
}
function routeTo(place, label, go) { setRouteDest(place, label); showTab('routing'); if (go) findRoute(); }
async function findRoute() {
  $('rt-err').textContent = '';
  if (!S.routeDest) { $('rt-err').textContent = 'Choose a destination: a resident, an incident or a point on the map.'; return; }
  $('rt-go').disabled = true; $('rt-result').replaceChildren(h('p', {class: 'small-text muted', text: 'Computing safest route…'}));
  try { drawRoute(await api('/api/ops/route', {method: 'POST', body: {origin: originPayload(), destination: S.routeDest, vehicle: $('rt-vehicle').value}}), false); }
  catch (e) { $('rt-err').textContent = e.message; $('rt-result').replaceChildren(); } finally { $('rt-go').disabled = false; }
}
async function evacRoute() {
  $('rt-err').textContent = '';
  if (!S.routeDest) { $('rt-err').textContent = 'Choose the household/point to evacuate (Destination).'; return; }
  $('rt-result').replaceChildren(h('p', {class: 'small-text muted', text: 'Finding the safest reachable evacuation centre…'}));
  try { drawRoute(await api('/api/ops/evacuation-route', {method: 'POST', body: {origin: S.routeDest, vehicle: $('rt-vehicle').value}}), true); }
  catch (e) { $('rt-err').textContent = e.message; $('rt-result').replaceChildren(); }
}
$('rt-go').addEventListener('click', findRoute);
$('rt-evac').addEventListener('click', evacRoute);

function routeTradeoff(c) {
  // State the trade honestly: the safest route may accept more shallow water to avoid deep water.
  const gains = [], costs = [];
  if (c.deep_water_avoided_m > 0) gains.push(`${km(c.deep_water_avoided_m)} of water too deep for this vehicle`);
  if (c.flood_exposure_avoided_m > 0) gains.push(`${km(c.flood_exposure_avoided_m)} of flooded road`);
  if (c.flood_exposure_avoided_m < 0) costs.push(`${km(-c.flood_exposure_avoided_m)} more shallower flood water`);
  if (c.extra_distance_m > 0) costs.push(`${km(c.extra_distance_m)} extra distance`);
  if (!gains.length) return 'The shortest route is already the safest for this scenario.';
  return `Compared with the shortest route it avoids ${gains.join(' and ')}` + (costs.length ? `, at the cost of ${costs.join(' and ')}.` : '.');
}

function drawRoute(r, evac) {
  layers.route.clearLayers();
  const line = segs => segs.flatMap(s => s.coordinates).map(([x, y]) => [y, x]);
  L.polyline(line(r.shortest.segments), {pane: 'routes', color: '#666', weight: 4, opacity: .7, dashArray: '6 8'}).bindTooltip('Shortest route (ignores flooding)').addTo(layers.route);
  for (const s of r.safest.segments) {
    const pts = s.coordinates.map(([x, y]) => [y, x]);
    L.polyline(pts, {pane: 'routes', color: '#fff', weight: 10, opacity: .9}).addTo(layers.route);
    L.polyline(pts, {pane: 'routes', color: ROUTE_COLOR[s.status], weight: 6, dashArray: ['impassable', 'blocked'].includes(s.status) ? '4 6' : null})
      .bindTooltip(s.status === 'None' ? 'Dry' : s.status === 'impassable' ? 'Too deep for this vehicle: boat / water rescue' : s.status === 'blocked' ? 'Reported blocked' : `${s.status} flood water`).addTo(layers.route);
  }
  const pin = (p, color, label) => L.circleMarker([p.latitude, p.longitude], {pane: 'routes', radius: 8, color: '#fff', weight: 3, fillColor: color, fillOpacity: 1}).bindTooltip(tip(label)).addTo(layers.route);
  pin(r.origin, '#0f5c63', 'Start: ' + r.origin.name);
  pin(r.destination, evac ? '#1e8449' : '#8e1b1b', (evac ? 'Evacuation centre: ' : 'Destination: ') + r.destination.name);
  const sp = r.safest.staging_point;
  if (sp) L.circleMarker([sp.latitude, sp.longitude], {pane: 'routes', radius: 9, color: '#c0392b', weight: 3, fillColor: '#fff', fillOpacity: 1}).bindTooltip(`Staging point: vehicle stops here. ${sp.remaining_m} m by boat / wading`).addTo(layers.route);
  map.fitBounds(L.featureGroup(layers.route.getLayers()).getBounds(), {padding: [40, 40]});

  const d = r.safest.length_by_depth_m, total = Math.max(1, r.safest.length_m);
  const bar = h('div', {class: 'depthbar'}, ...['None', 'Low', 'Medium', 'High'].map(k => h('i', {style: `width:${100 * d[k] / total}%;background:${ROUTE_COLOR[k]}`, title: `${k}: ${d[k]} m`})));
  const c = r.comparison;
  const row = (label, a, b) => [h('span', {class: 'muted', text: label}), h('b', {text: a}), h('span', {text: b})];
  $('rt-result').replaceChildren(h('div', {class: 'route-card'},
    h('h4', {text: `${r.origin.auto_selected ? 'Nearest safe base: ' : ''}${r.origin.name} → ${r.destination.name}`}),
    h('div', {class: 'small-text muted', text: `${r.vehicle.label} · ${r.scenario.label} · NOAH ${r.scenario.hazard_map} map${r.conditions_used.length ? ` · ${r.conditions_used.length} field report(s) applied` : ''}`}),
    bar,
    h('div', {class: 'cmp'}, h('span'), h('b', {text: 'Safest (shown)'}), h('span', {class: 'muted', text: 'Shortest'}),
      row('Distance', km(r.safest.length_m), km(r.shortest.length_m)),
      row('Est. time', `${fmt(r.safest.eta_min)} min`, `${fmt(r.shortest.eta_min)} min`),
      row('In flood water', km(r.safest.flooded_m), km(r.shortest.flooded_m)),
      row('Too deep / blocked', km(r.safest.impassable_m), km(r.shortest.impassable_m))),
    h('p', {class: 'small-text', text: routeTradeoff(c)}),
    ...r.warnings.map(w => h('div', {class: 'notice bad', text: w})),
    r.safest.via.length ? h('div', {class: 'via', text: 'Via: ' + r.safest.via.map(v => `${v.name} (${km(v.length_m)})`).join(' → ')}) : null,
    r.alternatives?.length ? h('div', {class: 'via', text: 'Next options: ' + r.alternatives.map(a => a.name).join(' · ')}) : null));
}

/* ------------------------------------------------------------ misc */
let toastTimer;
function toast(text, onclick) {
  document.querySelector('.toast')?.remove();
  const t = h('div', {class: 'toast', text, role: 'alert', onclick: () => { t.remove(); onclick?.(); }, style: 'cursor:pointer'});
  document.body.append(t); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.remove(), 15000);
}
async function refreshAll() {
  await Promise.all([loadResidents(), loadIncidents(), loadFacilities()]);
  await loadSummary();
}

(async function init() {
  S.meta = await api('/api/ops/meta');
  $('inc-type').replaceChildren(...Object.entries(S.meta.incident_types).map(([k, v]) => h('option', {value: k, text: v})));
  $('inc-depth').append(...Object.entries(S.meta.depths).map(([k, v]) => h('option', {value: k, text: v})));
  $('rt-vehicle').replaceChildren(...Object.entries(S.meta.vehicles).map(([k, v]) => h('option', {value: k, text: v.label, selected: k === 'high_clearance'})));
  $('rt-vnote').textContent = S.meta.vehicles.high_clearance.note;
  api('/api/assessment/barangays').then(geo => {
    const b = L.geoJSON(geo, {interactive: false, style: {color: '#0f5c63', weight: 1.2, fill: false, dashArray: '4 3'},
      onEachFeature: (f, l) => l.bindTooltip(tip(f.properties.BRGY_NAME), {permanent: true, direction: 'center', className: 'brgy'})}).addTo(map);
    map.fitBounds(b.getBounds());
  }).catch(() => {});
  api('/api/ops/scenario/events').then(ev => {
    S.events = ev;
    $('sc-events').append(...ev.map((e, i) => h('option', {value: i, text: `${e.EventDate} · ${e.EventType} · ${e.Rainfall24hMm} mm`})));
  }).catch(() => {});
  await refreshAll();
  setInterval(() => loadIncidents().then(loadSummary).catch(() => {}), 20000);
})().catch(e => { $('sc-text').textContent = 'Could not load: ' + e.message; });
