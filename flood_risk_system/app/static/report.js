'use strict';
const $ = id => document.getElementById(id);
const STATUS_TEXT = {new: 'Received, waiting for MDRRMO verification', verified: 'Verified by MDRRMO',
  dispatched: 'Responders dispatched', resolved: 'Resolved', dismissed: 'Closed (not actionable / duplicate)'};
let point = null, marker = null, boundary = null;

const map = L.map('rmap').setView([14.275, 121.415], 13);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap contributors'}).addTo(map);

function setPoint(lat, lng, how) {
  point = {latitude: lat, longitude: lng};
  if (marker) marker.setLatLng([lat, lng]);
  else { marker = L.marker([lat, lng], {draggable: true}).addTo(map); marker.on('dragend', () => { const p = marker.getLatLng(); setPoint(p.lat, p.lng, 'adjusted'); }); }
  $('loc-text').textContent = `${how}: ${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}
map.on('click', e => setPoint(e.latlng.lat, e.latlng.lng, 'Pinned'));

$('gps').addEventListener('click', () => {
  if (!navigator.geolocation) { $('loc-text').textContent = 'This browser cannot share location. Tap the map instead.'; return; }
  $('loc-text').textContent = 'Getting location…';
  navigator.geolocation.getCurrentPosition(pos => {
    setPoint(pos.coords.latitude, pos.coords.longitude, `GPS (±${Math.round(pos.coords.accuracy)} m)`);
    map.setView([pos.coords.latitude, pos.coords.longitude], 17);
  }, err => { $('loc-text').textContent = 'Location unavailable (' + err.message + '). Tap the map instead.'; },
  {enableHighAccuracy: true, timeout: 15000, maximumAge: 30000});
});

function choices(container, name, options, required) {
  for (const [value, text] of Object.entries(options)) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'radio'; input.name = name; input.value = value; input.required = required;
    label.append(input, document.createTextNode(text));
    container.append(label);
  }
}

async function api(url, body) {
  const res = await fetch(url, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const d = data.detail; throw new Error(Array.isArray(d) ? d.map(x => x.msg.replace('Value error, ', '')).join(' ') : d || 'Something went wrong. Please try again.'); }
  return data;
}

$('report').addEventListener('submit', async e => {
  e.preventDefault(); $('err').textContent = '';
  const type = document.querySelector('input[name=type]:checked')?.value;
  const depth = document.querySelector('input[name=depth]:checked')?.value || null;
  if (!type) { $('err').textContent = 'Choose what is happening.'; return; }
  if (!point) { $('err').textContent = 'Set the location: tap the map or use your current location.'; return; }
  const people = $('people').value === '' ? null : Number($('people').value);
  const body = {type, ...point, water_depth: depth, people_affected: people,
    description: $('desc').value || null, reporter_name: $('name').value || null, reporter_contact: $('contact').value || null};
  $('submit').disabled = true; $('submit').textContent = 'Sending…';
  try {
    const r = await api('/api/public/reports', body);
    $('ref').textContent = r.ref_code; $('done-msg').textContent = `${r.barangay} · ${r.message}`;
    try { localStorage.setItem('lastReport', r.ref_code); } catch (_) { /* storage blocked */ }
    $('lookup').value = r.ref_code;
    $('report').classList.add('hidden'); $('done').classList.remove('hidden'); window.scrollTo({top: 0, behavior: 'smooth'});
  } catch (error) { $('err').textContent = error.message; }
  finally { $('submit').disabled = false; $('submit').textContent = 'Send report'; }
});

$('again').addEventListener('click', () => {
  $('report').reset(); $('done').classList.add('hidden'); $('report').classList.remove('hidden');
});

$('check').addEventListener('click', async () => {
  const ref = $('lookup').value.trim(); $('status').replaceChildren();
  if (!ref) return;
  try {
    const r = await api('/api/public/reports/' + encodeURIComponent(ref));
    const head = document.createElement('p');
    const b = document.createElement('b'); b.textContent = STATUS_TEXT[r.status] || r.status;
    head.append(`${r.ref_code} · ${r.type} · ${r.barangay}: `, b);
    const ul = document.createElement('ul');
    for (const h of r.history) { const li = document.createElement('li'); li.textContent = `${new Date(h.at).toLocaleString()} · ${STATUS_TEXT[h.status] || h.status}`; ul.append(li); }
    $('status').append(head, ul);
  } catch (error) { $('status').textContent = error.message; }
});

try { const last = localStorage.getItem('lastReport'); if (last) $('lookup').value = last; } catch (_) { /* storage blocked */ }

api('/api/public/report-options').then(o => {
  choices($('types'), 'type', o.types, true);
  choices($('depths'), 'depth', o.depths, false);
}).catch(() => { $('err').textContent = 'Cannot reach the server. Call the MDRRMO hotline instead.'; });

api('/api/assessment/barangays').then(geo => {
  boundary = L.geoJSON(geo, {style: {color: '#0f5c63', weight: 1, fillOpacity: 0.04}, interactive: false}).addTo(map);
  if (!point) map.fitBounds(boundary.getBounds());
}).catch(() => {});
