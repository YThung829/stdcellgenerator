(function(){
"use strict";
const PAYLOAD = JSON.parse(document.getElementById("stackdata").textContent);
const D = PAYLOAD.techs;      /* tech -> {layers:[...], ...} */
const META = PAYLOAD.meta;    /* tech -> {obj, specs:[[k,v],...]} — built in layers.py */
const ALPHA = PAYLOAD.alpha;  /* tech -> {gds_key: opacity} — enveloping layers */

const GROUP_ZH = {
  substrate:"基板", implant:"佈植", device:"元件層", bot:"下層元件", top:"上層元件",
  back:"背面元件", backbeol:"背面金屬", mid:"層間繞線", miv:"MIV", mol:"MOL",
  via:"Via", beol:"正面金屬", mask:"遮罩", virtual:"虛擬邊",
};

let tech = "CFET";
let explode = 0.22;
let groupFilter = null;
let hoverKey = null;
const onState = {};   // tech -> {layerKey: bool}

/* ------------------------------------------------------------------ three */
const canvas = document.getElementById("gl");
const renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:false});
renderer.setClearColor(0x060a0e, 1);
const scene = new THREE.Scene();
scene.fog = new THREE.Fog(0x060a0e, 900, 2200);
const camera = new THREE.PerspectiveCamera(38, 1, 1, 4000);

scene.add(new THREE.HemisphereLight(0x9fc6ff, 0x0b1016, 0.95));
const key = new THREE.DirectionalLight(0xffffff, 0.72); key.position.set(220, 420, 300); scene.add(key);
const fill = new THREE.DirectionalLight(0xffc9a8, 0.28); fill.position.set(-300, 120, -220); scene.add(fill);

const root = new THREE.Group(); scene.add(root);

/* simple orbit state */
const orb = {az: -0.72, pol: 1.14, dist: 620, tx:0, ty:0, tz:0, spin:true};
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
if (reduceMotion) orb.spin = false;

function applyCamera(){
  const sp = Math.max(0.12, Math.min(Math.PI - 0.12, orb.pol));
  camera.position.set(
    orb.tx + orb.dist * Math.sin(sp) * Math.sin(orb.az),
    orb.ty + orb.dist * Math.cos(sp),
    orb.tz + orb.dist * Math.sin(sp) * Math.cos(orb.az)
  );
  camera.lookAt(orb.tx, orb.ty, orb.tz);
}

/* ------------------------------------------------------------- scene build */
const meshes = [];

function bbox(poly){
  let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
  for (const p of poly){
    if(p[0]<x0)x0=p[0]; if(p[0]>x1)x1=p[0];
    if(p[1]<y0)y0=p[1]; if(p[1]>y1)y1=p[1];
  }
  return [x0,y0,x1,y1];
}

function darken(hex, f){
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n>>16)&255)*f), g = Math.round(((n>>8)&255)*f), b = Math.round((n&255)*f);
  return (r<<16)|(g<<8)|b;
}

function build(){
  for (const m of meshes){
    root.remove(m);
    m.geometry.dispose();
    if (m.material.dispose) m.material.dispose();
  }
  meshes.length = 0;

  const T = D[tech];
  const st = onState[tech];
  let cx = 0, cy = 0, n = 0, ymin = Infinity, ymax = -Infinity, xr = 0, zr = 0;

  for (const L of T.layers){
    if (!st[L.key] || !L.polys.length) continue;
    if (groupFilter && L.group !== groupFilter) continue;
    const thick = Math.max(2, L.z1 - L.z0);
    const col = parseInt(L.color.slice(1), 16);
    const alpha = (ALPHA[tech] && ALPHA[tech][L.key]) || 1;
    const mat = new THREE.MeshLambertMaterial({
      color: col, transparent: alpha < 1, opacity: alpha, depthWrite: alpha >= 1,
    });
    const emat = new THREE.LineBasicMaterial({color: darken(L.color, 0.45), transparent:true, opacity:0.75});
    for (const poly of L.polys){
      const [x0,y0,x1,y1] = bbox(poly);
      const w = Math.max(0.6, x1-x0), h = Math.max(0.6, y1-y0);
      const geo = new THREE.BoxGeometry(w, thick, h);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.renderOrder = alpha < 1 ? 2 : 0;
      mesh.userData.baseAlpha = alpha;
      mesh.userData.baseY = (L.z0 + L.z1) / 2;
      mesh.userData.layer = L;
      mesh.position.set((x0+x1)/2, mesh.userData.baseY, -(y0+y1)/2);
      root.add(mesh); meshes.push(mesh);

      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), emat);
      edges.userData.baseY = mesh.userData.baseY;
      edges.userData.follow = mesh;
      edges.position.copy(mesh.position);
      root.add(edges); meshes.push(edges);

      cx += (x0+x1)/2; cy += -(y0+y1)/2; n++;
      xr = Math.max(xr, x1); zr = Math.max(zr, y1);
      ymin = Math.min(ymin, L.z0); ymax = Math.max(ymax, L.z1);
    }
  }

  applyExplode();
  fitView();
  applyCamera();
}

/* Frame whatever is currently visible: real world bbox of the exploded meshes,
   then back the camera off far enough that its bounding sphere fits the FOV. */
let userZoom = false;
function fitView(){
  const box = new THREE.Box3();
  let any = false;
  for (const m of meshes){
    if (m.type !== "Mesh") continue;
    m.updateMatrixWorld();
    box.expandByObject(m); any = true;
  }
  if (!any){ orb.tx = 45; orb.ty = 60; orb.tz = -72; orb.dist = 420; return; }
  const c = box.getCenter(new THREE.Vector3());
  const r = box.getSize(new THREE.Vector3()).length() / 2;
  orb.tx = c.x; orb.tz = c.z;
  // The HUD floats over the bottom of the stage. Bias the framing upward by
  // that much so a deep stack's lowest layers (QFET's backside metals) are
  // not parked behind the controls. Proportional, so it holds at any height.
  const stageH = canvas.clientHeight || 1;
  orb.ty = c.y - r * (76 / stageH);
  if (!userZoom){
    const vfov = camera.fov * Math.PI / 180;
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    orb.dist = r / Math.sin(Math.min(vfov, hfov) / 2) * 1.16;
  }
}

function applyExplode(){
  for (const m of meshes){
    m.position.y = m.userData.baseY * (1 + explode * 2.6);
  }
}

/* --------------------------------------------------------------- highlight */
function applyHighlight(){
  for (const m of meshes){
    if (!m.material || !m.userData.layer) continue;
    const isEdge = m.type === "LineSegments";
    if (isEdge) continue;
    const base = m.userData.baseAlpha != null ? m.userData.baseAlpha : 1;
    const hit = !hoverKey || m.userData.layer.key === hoverKey;
    m.material.opacity = hit ? base : 0.12;
    m.material.transparent = !hit || base < 1;
    m.material.depthWrite = hit && base >= 1;
    m.material.needsUpdate = true;
  }
}
/* edges carry the same layer for hover, set after build */
function tagEdges(){
  for (const m of meshes){
    if (m.type === "LineSegments" && m.userData.follow) m.userData.layer = m.userData.follow.userData.layer;
  }
}

/* -------------------------------------------------------------------- rail */
const railEl = document.getElementById("rail");
const chipsEl = document.getElementById("chips");
const specsEl = document.getElementById("specs");

function renderSpecs(){
  specsEl.innerHTML = "";
  for (const [k,v] of META[tech].specs){
    const d = document.createElement("div"); d.className = "spec";
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    d.append(dt, dd); specsEl.append(d);
  }
  document.getElementById("chip-obj").textContent = tech + " " + META[tech].obj;
}

function renderChips(){
  const T = D[tech];
  const seen = [];
  for (const L of T.layers){ if (!seen.includes(L.group)) seen.push(L.group); }
  chipsEl.innerHTML = "";
  const mk = (label, g) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = label;
    b.setAttribute("aria-pressed", String(groupFilter === g));
    b.addEventListener("click", () => {
      groupFilter = (groupFilter === g) ? null : g;
      renderChips(); renderRail(); build(); tagEdges(); applyHighlight();
    });
    return b;
  };
  const all = mk("全部", null);
  all.setAttribute("aria-pressed", String(groupFilter === null));
  chipsEl.append(all);
  for (const g of seen) chipsEl.append(mk(GROUP_ZH[g] || g, g));
}

function renderRail(){
  const T = D[tech], st = onState[tech];
  const rows = T.layers.slice().sort((a,b) => (b.z1 - a.z1) || (b.z0 - a.z0));
  railEl.innerHTML = "";
  for (const L of rows){
    if (groupFilter && L.group !== groupFilter) continue;
    const b = document.createElement("button");
    b.type = "button"; b.className = "lrow";
    b.dataset.on = st[L.key] ? "1" : "0";
    b.dataset.empty = L.polys.length ? "0" : "1";
    b.style.borderLeftColor = st[L.key] ? L.color : "transparent";

    const sw = document.createElement("span");
    sw.className = "sw"; sw.style.background = L.color;

    const mid = document.createElement("span");
    const nm = document.createElement("span"); nm.className = "lname"; nm.textContent = L.name;
    const mt = document.createElement("span"); mt.className = "lmeta";
    mt.textContent = "GDS " + L.key + " · " + (L.polys.length ? L.polys.length + " poly" : "無幾何") + " · " + (GROUP_ZH[L.group] || L.group);
    mid.append(nm, document.createElement("br"), mt);

    const z = document.createElement("span");
    z.className = "lz"; z.textContent = L.z0 + "→" + L.z1;

    b.append(sw, mid, z);
    b.title = L.note;
    b.addEventListener("click", () => {
      st[L.key] = !st[L.key];
      renderRail(); build(); tagEdges(); applyHighlight();
    });
    b.addEventListener("mouseenter", () => { hoverKey = L.key; applyHighlight(); showTipFor(L, null); });
    b.addEventListener("mouseleave", () => { hoverKey = null; applyHighlight(); hideTip(); });
    b.addEventListener("focus", () => { hoverKey = L.key; applyHighlight(); });
    b.addEventListener("blur", () => { hoverKey = null; applyHighlight(); });
    railEl.append(b);
  }
}

/* -------------------------------------------------------------------- tips */
const tip = document.getElementById("tip");
const stage = document.querySelector(".stage");
function showTipFor(L, ev){
  tip.innerHTML = "";
  const a = document.createElement("div"); a.className = "t-n"; a.textContent = L.name;
  const b = document.createElement("div"); b.className = "t-m";
  b.textContent = "GDS " + L.key + "  ·  z " + L.z0 + " → " + L.z1 + " nm  ·  " + L.polys.length + " polygon";
  const c = document.createElement("div"); c.className = "t-d"; c.textContent = L.note;
  tip.append(a, b, c);
  tip.style.opacity = "1";
  if (ev){
    const r = stage.getBoundingClientRect();
    let x = ev.clientX - r.left + 16, y = ev.clientY - r.top + 16;
    if (x + 300 > r.width) x = r.width - 305;
    if (y + 140 > r.height) y = r.height - 145;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  } else {
    tip.style.left = "16px"; tip.style.top = "76px";
  }
}
function hideTip(){ tip.style.opacity = "0"; }

/* ------------------------------------------------------------- interaction */
const ray = new THREE.Raycaster();
const ndc = new THREE.Vector2();
let dragging = null, lastX = 0, lastY = 0, userTouched = false;

canvas.addEventListener("pointerdown", e => {
  dragging = (e.button === 2 || e.shiftKey) ? "pan" : "orbit";
  lastX = e.clientX; lastY = e.clientY;
  userTouched = true; orb.spin = false;
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener("pointerup", e => {
  dragging = null;
  if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
});
canvas.addEventListener("contextmenu", e => e.preventDefault());
canvas.addEventListener("pointermove", e => {
  if (dragging === "orbit"){
    orb.az -= (e.clientX - lastX) * 0.007;
    orb.pol -= (e.clientY - lastY) * 0.007;
    lastX = e.clientX; lastY = e.clientY; applyCamera();
  } else if (dragging === "pan"){
    const s = orb.dist * 0.0016;
    orb.tx -= Math.cos(orb.az) * (e.clientX - lastX) * s;
    orb.tz += Math.sin(orb.az) * (e.clientX - lastX) * s;
    orb.ty += (e.clientY - lastY) * s;
    lastX = e.clientX; lastY = e.clientY; applyCamera();
  } else {
    const r = canvas.getBoundingClientRect();
    ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    ray.setFromCamera(ndc, camera);
    const hits = ray.intersectObjects(meshes.filter(m => m.type === "Mesh"), false);
    if (hits.length){
      const L = hits[0].object.userData.layer;
      if (L){ hoverKey = L.key; applyHighlight(); showTipFor(L, e); }
    } else if (hoverKey){
      hoverKey = null; applyHighlight(); hideTip();
    }
  }
});
canvas.addEventListener("pointerleave", () => { hoverKey = null; applyHighlight(); hideTip(); });
canvas.addEventListener("wheel", e => {
  e.preventDefault(); userTouched = true; userZoom = true; orb.spin = false;
  orb.dist = Math.max(90, Math.min(2600, orb.dist * (1 + Math.sign(e.deltaY) * 0.11)));
  applyCamera();
}, {passive:false});

document.getElementById("ex").addEventListener("input", e => {
  explode = e.target.value / 100;
  document.getElementById("exv").textContent = explode.toFixed(2);
  applyExplode(); fitView(); applyCamera();
});
document.getElementById("btn-reset").addEventListener("click", () => {
  orb.az = -0.72; orb.pol = 1.14; userZoom = false; userTouched = true;
  build(); tagEdges(); applyHighlight();
});
document.getElementById("btn-all").addEventListener("click", () => {
  const st = onState[tech];
  const anyOff = D[tech].layers.some(L => L.polys.length && !st[L.key]);
  for (const L of D[tech].layers) st[L.key] = anyOff ? !!L.polys.length : false;
  renderRail(); build(); tagEdges(); applyHighlight();
});
for (const btn of document.querySelectorAll(".seg button")){
  btn.addEventListener("click", () => {
    tech = btn.dataset.tech;
    for (const b of document.querySelectorAll(".seg button"))
      b.setAttribute("aria-pressed", String(b === btn));
    groupFilter = null; hoverKey = null; hideTip();
    renderSpecs(); renderChips(); renderRail(); build(); tagEdges(); applyHighlight();
  });
}

/* -------------------------------------------------------------------- boot */
for (const t of Object.keys(D)){
  onState[t] = {};
  for (const L of D[t].layers) onState[t][L.key] = L.on && L.polys.length > 0;
}
function resize(){
  const r = stage.getBoundingClientRect();
  const w = Math.max(320, r.width), h = Math.max(360, r.height);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
  if (meshes.length){ fitView(); applyCamera(); }
}
window.addEventListener("resize", resize);

renderSpecs(); renderChips(); renderRail();
resize(); build(); tagEdges(); applyHighlight();

let t0 = performance.now();
(function loop(now){
  requestAnimationFrame(loop);
  if (orb.spin && !userTouched){
    orb.az += (now - t0) * 0.00007;
    applyCamera();
  }
  t0 = now;
  renderer.render(scene, camera);
})(t0);

/* ------------------------------------------- the half-pitch grid diagram */
const fig = document.getElementById("fig-grid");
fig.innerHTML =
'<svg viewBox="0 0 820 220" role="img" aria-label="倍解析度座標系示意：placement layer 的 gate 欄在偶數 index、source/drain 欄在奇數 index，繞線層的 pitch 加倍後只落在偶數格點上">' +
  '<rect x="0" y="0" width="820" height="220" fill="#0d131a"/>' +
  '<text x="14" y="26" fill="#6d8093" font-family="IBM Plex Mono, monospace" font-size="11" letter-spacing="1.4">PLACEMENT LAYER  (PC / BPC / PC1)  pitch 保持原值</text>' +
  '<line x1="60" y1="66" x2="780" y2="66" stroke="#22303d" stroke-width="1"/>' +
  [0,1,2,3,4,5,6,7,8].map(function(i){
    var x = 60 + i*90, even = i%2===0;
    return '<line x1="'+x+'" y1="46" x2="'+x+'" y2="86" stroke="'+(even?'#ef4444':'#f59e0b')+'" stroke-width="'+(even?4:3)+'"/>' +
           '<text x="'+x+'" y="106" fill="'+(even?'#ff9b8a':'#fbbf24')+'" font-family="IBM Plex Mono, monospace" font-size="11" text-anchor="middle">'+i+'</text>' +
           '<text x="'+x+'" y="122" fill="#6d8093" font-family="IBM Plex Mono, monospace" font-size="9.5" text-anchor="middle">'+(even?'gate':'S/D')+'</text>';
  }).join("") +
  '<text x="14" y="164" fill="#6d8093" font-family="IBM Plex Mono, monospace" font-size="11" letter-spacing="1.4">ROUTING LAYER  (M1 / BM1 / H1)  pitch × 2 → 只對得上偶數格點</text>' +
  '<line x1="60" y1="192" x2="780" y2="192" stroke="#22303d" stroke-width="1"/>' +
  [0,2,4,6,8].map(function(i){
    var x = 60 + i*90;
    return '<line x1="'+x+'" y1="176" x2="'+x+'" y2="208" stroke="#8b5cf6" stroke-width="4"/>' +
           '<line x1="'+x+'" y1="86" x2="'+x+'" y2="176" stroke="#8b5cf6" stroke-width="1" stroke-dasharray="3 5" opacity="0.45"/>';
  }).join("") +
  '<text x="795" y="70" fill="#6d8093" font-family="IBM Plex Mono, monospace" font-size="10" text-anchor="end"></text>' +
'</svg>';
const cap = document.createElement("figcaption");
cap.textContent = "倍解析度座標系：placement layer 用原本的 pitch，所以 gate（偶數 index）與 source/drain（奇數 index）都落在整數上；其他所有層的 pitch 乘 2，只會對到偶數格點。虛線就是 via 邊唯一可能存在的位置 —— _build_graph 要求「同一個 (r, c) 在兩層都存在」才連跨層邊。";
fig.append(cap);
})();
