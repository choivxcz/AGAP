'use strict';
const $ = id => document.getElementById(id);
const names = {ElevationMeters:'Elevation',SlopeDegrees:'Slope',DistanceToWaterwayM:'Distance to waterway',LocationType:'Location type',LandCoverType:'Land cover',HistoricalWaterOccurrencePct:'Past surface water',HouseFloodHazard5yr:'NOAH at point · 5-yr',HouseFloodHazard25yr:'NOAH at point · 25-yr',HouseFloodHazard100yr:'NOAH at point · 100-yr',FloodHazard5yr:'NOAH barangay · 5-yr',FloodHazard25yr:'NOAH barangay · 25-yr',FloodHazard100yr:'NOAH barangay · 100-yr',MGBFloodSusceptibility:'MGB susceptibility at point'};
const map = L.map('map').setView([14.265,121.415],12);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
let marker, boundaries, point, current, ready=false, requestId=0, reviewId=0, controller;
const layers=[];
function showFeatures(features={}) {
  $('features').replaceChildren();
  for (const [key,label] of Object.entries(names)) {
    const item=document.createElement('div'); item.className='feature';
    const small=document.createElement('small'); small.textContent=label;
    const strong=document.createElement('strong'); const value=features[key]?.value;
    strong.textContent=value==null?'Unavailable':typeof value==='number'?value.toLocaleString(undefined,{maximumFractionDigits:2}):value;
    if (typeof value==='number') { const unit=document.createElement('span'); unit.className='unit'; unit.textContent=' '+features[key].unit; strong.append(unit); }
    item.title=features[key]?.source||'Choose a location to extract this value'; item.append(small,strong); $('features').append(item);
  }
}
function clearReview(){reviewId++;$('result').hidden=true;$('form-error').textContent='';}
async function api(url,body,signal){
  const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal}:{signal});
  const data=await response.json();
  if(!response.ok){const detail=data.detail;throw new Error(Array.isArray(detail)?detail.map(d=>d.msg).join(' '):detail||'Unable to load data.');}
  return data;
}
async function extract(){
  const id=++requestId; controller?.abort();controller=new AbortController();
  current=null;clearReview();$('assess').disabled=true;showFeatures();$('warnings').replaceChildren();
  if(!point||!ready)return;
  $('location-status').textContent='Extracting map features…';
  try{
    const data=await api('/api/assessment/location',point,controller.signal);
    if(id!==requestId)return;
    current=data;showFeatures(data.features);$('assess').disabled=!data.valid_land;
    $('location-status').textContent=`${data.barangay} · ${data.latitude.toFixed(6)}, ${data.longitude.toFixed(6)}`;
    for(const warning of data.warnings){const li=document.createElement('li');li.textContent=warning;$('warnings').append(li);}
  }catch(error){if(id===requestId&&error.name!=='AbortError')$('location-status').textContent=error.message;}
}
function choose(lat,lng){
  point={latitude:lat,longitude:lng};$('latitude').value=lat.toFixed(6);$('longitude').value=lng.toFixed(6);
  if(marker)marker.setLatLng([lat,lng]);else{marker=L.marker([lat,lng],{draggable:true}).addTo(map);marker.on('dragend',()=>{const p=marker.getLatLng();choose(p.lat,p.lng);});}
  extract();
}
const counts=c=>`Low ${c.Low} · Medium ${c.Medium} · High ${c.High}`;
function showEvents(events){
  const body=$('event-table').querySelector('tbody');body.replaceChildren();
  for(const e of events){
    const tr=document.createElement('tr');
    for(const text of [e.EventDate,e.EventType,`${e.Rainfall24hMm.toFixed(1)} mm`,`${e.Rainfall3DayMm.toFixed(1)} mm`,e.RiskLevel]){const td=document.createElement('td');td.textContent=text;tr.append(td);}
    tr.lastChild.className='risk-'+e.RiskLevel;body.append(tr);
  }
}
map.on('click',e=>choose(e.latlng.lat,e.latlng.lng));
$('coordinates').addEventListener('submit',e=>{e.preventDefault();const lat=Number($('latitude').value),lng=Number($('longitude').value);choose(lat,lng);map.panTo([lat,lng]);});
$('household-form').addEventListener('input',()=>{clearReview();$('assess').disabled=!current?.valid_land;});
$('household-form').addEventListener('submit',async e=>{
  e.preventDefault();clearReview();if(!current?.valid_land)return;
  const payload={...point};
  for(const [key,value] of new FormData(e.target))payload[key]=key==='HousingMaterial'?value:Number(value);
  if(payload.ChildrenCount+payload.ElderCount>payload.HouseMembers){$('form-error').textContent='Children + elders must not exceed household members.';return;}
  if(payload.PWDCount>payload.HouseMembers){$('form-error').textContent='PWD count must not exceed household members.';return;}
  const id=reviewId; const locationId=requestId;$('assess').disabled=true;
  try{
    const data=await api('/api/assessment/prepare',payload);
    if(id!==reviewId||locationId!==requestId)return;
    const p=data.prediction;
    $('result-title').textContent=p.RiskLevel?`${p.RiskLevel} risk in major storms · prototype model`:'Risk model unavailable';
    $('result-message').textContent=p.message;
    $('result-score').textContent=p.RiskLevel?`Across the 15 extreme rain events: ${counts(p.extreme_counts)}. Across all 60 rain events: ${counts(p.all_counts)}.`:'';
    $('result-worst').textContent=!p.worst?'':p.all_counts.Low===p.events.length?`Low in all ${p.events.length} rain events.`:`Highest level reached: ${p.worst.RiskLevel} (${p.worst.EventDate}, ${p.worst.EventType.toLowerCase()} event, ${p.worst.Rainfall24hMm.toFixed(1)} mm in 24 hours).`;
    showEvents(p.events||[]);
    $('result-rules').replaceChildren();
    for(const detail of p.details){const item=document.createElement('li');item.textContent=detail;$('result-rules').append(item);}
    $('result-summary').textContent=`Prepared ${data.barangay} features for a household head aged ${data.household.Age} and ${data.household.HouseMembers} household members. Details were not saved.`;
    $('result').hidden=false;$('result').scrollIntoView({behavior:'smooth',block:'nearest'});
  }catch(error){if(id===reviewId)$('form-error').textContent=error.message;}
  finally{if(id===reviewId)$('assess').disabled=!current?.valid_land;}
});
$('barangay').addEventListener('change',()=>{const index=$('barangay').value;map.fitBounds(index===''?boundaries.getBounds():layers[Number(index)].getBounds(),{padding:[15,15]});});
showFeatures();
Promise.all([api('/api/assessment/config'),api('/api/assessment/barangays')]).then(([config,geo])=>{
  ready=true;$('rain-note').textContent=config.rainfall_note;
  boundaries=L.geoJSON(geo,{style:{color:'#287e6f',weight:1.5,fillColor:'#6bad97',fillOpacity:.12},onEachFeature:(feature,layer)=>{
    const text=document.createElement('span');text.textContent=feature.properties.BRGY_NAME;layer.bindTooltip(text);
    const option=document.createElement('option');option.value=layers.length;option.textContent=feature.properties.BRGY_NAME;$('barangay').append(option);layers.push(layer);
  }}).addTo(map);map.fitBounds(boundaries.getBounds(),{padding:[12,12]});
  $('location-status').textContent='Click within a Santa Cruz barangay to get started.';if(point)extract();
}).catch(error=>{$('location-status').textContent='Could not initialize the assessment: '+error.message;$('rain-note').textContent='Source data unavailable. Reload after checking the server.';});
