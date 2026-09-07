/* ---------------------------------------------------------------------------
   Process migration: FinFET -> CFET, animated as a fold.

   The two endpoints are real generated layouts. What is interpolated between
   them is the motion, not the geometry -- every box starts on a real FinFET
   shape and lands on a real CFET shape, and migrate.py decided the pairing at
   build time so it is inspectable rather than guessed here.

   The fold is exact, not a flourish: rotating FinFET's PMOS band 180 degrees
   about the line y=72 lands it at y[19,65], which is precisely where the NMOS
   band sits. Phase 1 performs that rotation; phase 2 settles every box into
   its true CFET position, which is where the stretch to full cell height and
   the taller BEOL show up.
--------------------------------------------------------------------------- */
(function(){
"use strict";
/* app.js keeps its payload inside its own IIFE, so read the embedded JSON
   again here rather than reaching into a scope that is not shared. */
const MIGS = JSON.parse(
  document.getElementById("stackdata").textContent).migrations || {};
const NAMES = Object.keys(MIGS);
const host = document.getElementById("mig");
if (!host || !NAMES.length) { if (host) host.remove(); return; }

const MIG = MIGS[NAMES[0]];
/* Same per-layer opacity the main viewer uses: the gate stack runs through
   both tiers, so leaving it opaque hides exactly the thing the fold is meant
   to reveal. */
const ALPHA = JSON.parse(
  document.getElementById("stackdata").textContent).alpha || {};
const AA = ALPHA[MIG.from] || {}, AB = ALPHA[MIG.to] || {};
function alphaOf(p, t){
  const a = AA[p.ka] != null ? AA[p.ka] : 1;
  const b = AB[p.kb] != null ? AB[p.kb] : 1;
  return a + (b - a) * t;
}
const FOLD_END = 0.5;                 // t at which the rotation completes

/* ---- scene ---- */
const canvas = document.getElementById("mig-gl");
const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
renderer.setClearColor(0x060a0e, 1);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 1, 4000);
scene.add(new THREE.HemisphereLight(0x9fc6ff, 0x0b1016, 0.95));
const k1 = new THREE.DirectionalLight(0xffffff, 0.72); k1.position.set(220,420,300); scene.add(k1);
const k2 = new THREE.DirectionalLight(0xffc9a8, 0.28); k2.position.set(-300,120,-220); scene.add(k2);
const root = new THREE.Group(); scene.add(root);

/* Fold axis in scene coordinates. The viewer maps gds x -> scene x,
   stack z -> scene y, gds y -> scene -z, so the axis runs along scene X. */
const AX_Y = MIG.fold.at_z;
const AX_Z = -MIG.fold.at_y;

function hex(c){ return parseInt(c.slice(1), 16); }

/* Each pair becomes one mesh that lives for the whole animation; "in" and
   "out" boxes simply fade, so nothing pops. */
const items = [];
for (const p of MIG.pairs){
  const src = p.a, dst = p.b;
  const base = src || dst;
  const geo = new THREE.BoxGeometry(1, 1, 1);   // unit box, scaled per frame
  const mat = new THREE.MeshLambertMaterial({
    color: hex(p.b ? p.cb : p.ca), transparent:true, opacity:1, depthWrite:false,
  });
  const mesh = new THREE.Mesh(geo, mat);
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(geo),
    new THREE.LineBasicMaterial({color: 0x0b1016, transparent:true, opacity:0.55}));
  mesh.add(edges);
  root.add(mesh);
  items.push({p, mesh, mat, src, dst, base});
}

/* box [x0,y0,x1,y1,z0,z1] -> scene centre + size */
function decompose(b){
  return {
    cx: (b[0]+b[2])/2, cy: (b[4]+b[5])/2, cz: -(b[1]+b[3])/2,
    sx: Math.max(0.6, b[2]-b[0]),
    sy: Math.max(0.6, b[5]-b[4]),
    sz: Math.max(0.6, b[3]-b[1]),
  };
}
function lerp(a,b,t){ return a + (b-a)*t; }
function ease(t){ return t<0.5 ? 4*t*t*t : 1-Math.pow(-2*t+2,3)/2; }

function apply(t){
  const foldT = ease(Math.min(1, t / FOLD_END));
  const settleT = ease(Math.max(0, (t - FOLD_END) / (1 - FOLD_END)));
  const theta = Math.PI * foldT;
  const cos = Math.cos(theta), sin = Math.sin(theta);

  for (const it of items){
    const {p, mesh, mat} = it;
    const A = it.src ? decompose(it.src) : null;
    const B = it.dst ? decompose(it.dst) : null;

    // Phase 1 state: P-role boxes rotate about the fold axis; everything else
    // holds its FinFET position.
    let s;
    if (A){
      if (p.r === "P"){
        const dy = A.cy - AX_Y, dz = A.cz - AX_Z;
        s = {cx: A.cx, cy: AX_Y + dy*cos - dz*sin, cz: AX_Z + dy*sin + dz*cos,
             sx: A.sx, sy: A.sy, sz: A.sz, rot: theta};
      } else {
        s = {...A, rot: 0};
      }
    } else {
      s = {...B, rot: 0};                       // an "in" box waits at its target
    }

    // Phase 2: settle into the real CFET box.
    let f = s;
    if (B && A){
      f = {cx: lerp(s.cx, B.cx, settleT), cy: lerp(s.cy, B.cy, settleT),
           cz: lerp(s.cz, B.cz, settleT), sx: lerp(s.sx, B.sx, settleT),
           sy: lerp(s.sy, B.sy, settleT), sz: lerp(s.sz, B.sz, settleT),
           rot: s.rot};
    }

    mesh.position.set(f.cx, f.cy, f.cz);
    mesh.scale.set(f.sx, f.sy, f.sz);
    mesh.rotation.x = f.rot;

    // Colour crosses over with the settle, so a split layer visibly becomes
    // its new identity rather than teleporting.
    if (A && B && p.ca !== p.cb){
      mat.color.set(hex(p.ca)).lerp(new THREE.Color(hex(p.cb)), settleT);
    }
    const base = alphaOf(p, settleT);
    mat.opacity = p.m === "out" ? base * (1 - foldT)
                : p.m === "in"  ? base * settleT
                : base;
    mat.depthWrite = base >= 1;
    mesh.renderOrder = base < 1 ? 2 : 0;
    mat.visible = mat.opacity > 0.02;
  }
}

/* ---- camera ---- */
const orb = {az:-0.72, pol:1.14, dist:600, tx:45, ty:80, tz:-72};
function applyCamera(){
  const sp = Math.max(0.12, Math.min(Math.PI-0.12, orb.pol));
  camera.position.set(
    orb.tx + orb.dist*Math.sin(sp)*Math.sin(orb.az),
    orb.ty + orb.dist*Math.cos(sp),
    orb.tz + orb.dist*Math.sin(sp)*Math.cos(orb.az));
  camera.lookAt(orb.tx, orb.ty, orb.tz);
}
function fit(){
  // Frame the union of BOTH endpoints, so the camera never moves during the
  // animation -- the layout should appear to change, not the viewpoint.
  const box = new THREE.Box3();
  for (const it of items){
    for (const b of [it.src, it.dst]){
      if (!b) continue;
      const d = decompose(b);
      box.expandByPoint(new THREE.Vector3(d.cx-d.sx/2, d.cy-d.sy/2, d.cz-d.sz/2));
      box.expandByPoint(new THREE.Vector3(d.cx+d.sx/2, d.cy+d.sy/2, d.cz+d.sz/2));
    }
  }
  const c = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const stageH = canvas.clientHeight || 1;
  // Fit per axis rather than to the bounding SPHERE. This stack is tall and
  // thin, and the sphere's radius is set by the diagonal, which backs the
  // camera off far enough to leave the cell floating in a mostly empty frame.
  const vfov = camera.fov*Math.PI/180;
  const hfov = 2*Math.atan(Math.tan(vfov/2)*camera.aspect);
  const halfDepth = Math.max(size.x, size.z)/2;
  const distV = (size.y/2)   / Math.tan(vfov/2);
  const distH = halfDepth    / Math.tan(hfov/2);
  orb.tx = c.x; orb.tz = c.z;
  orb.dist = Math.max(distV, distH)*1.12 + halfDepth;
  // Leave room for the HUD that floats over the bottom of the stage.
  orb.ty = c.y - (size.y/2)*(72/stageH);
}

let drag=null, lx=0, ly=0;
canvas.addEventListener("pointerdown", e=>{drag=(e.button===2||e.shiftKey)?"pan":"orbit"; lx=e.clientX; ly=e.clientY; canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener("pointerup", e=>{drag=null; if(canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);});
canvas.addEventListener("contextmenu", e=>e.preventDefault());
canvas.addEventListener("pointermove", e=>{
  if(drag==="orbit"){ orb.az-=(e.clientX-lx)*0.007; orb.pol-=(e.clientY-ly)*0.007; lx=e.clientX; ly=e.clientY; applyCamera(); }
  else if(drag==="pan"){ const sc=orb.dist*0.0016;
    orb.tx-=Math.cos(orb.az)*(e.clientX-lx)*sc; orb.tz+=Math.sin(orb.az)*(e.clientX-lx)*sc;
    orb.ty+=(e.clientY-ly)*sc; lx=e.clientX; ly=e.clientY; applyCamera(); }
});
canvas.addEventListener("wheel", e=>{e.preventDefault();
  orb.dist=Math.max(90,Math.min(2600,orb.dist*(1+Math.sign(e.deltaY)*0.11))); applyCamera();
}, {passive:false});

/* ---- controls ---- */
const scrub = document.getElementById("mig-t");
const phase = document.getElementById("mig-phase");
const btn   = document.getElementById("mig-play");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let t = 0, playing = false, last = performance.now();

const PHASES = [
  [0.00, "FinFET — P 與 N 是同一平面上的兩條 row band"],
  [0.02, "折疊中 — PMOS band 繞 y=72 轉 180°"],
  [0.50, "折疊完成 — PMOS band 已經翻到 NMOS band 正上方"],
  [0.53, "落位 — 每個 box 移到真實的 CFET 位置，順便拉伸成整個 cell 高度"],
  [0.99, "CFET — P 與 N 變成 z 軸上的兩個 tier，x/y 完全重疊"],
];
function setT(v){
  t = Math.max(0, Math.min(1, v));
  scrub.value = Math.round(t*1000);
  let label = PHASES[0][1];
  for (const [at, s] of PHASES) if (t >= at) label = s;
  phase.textContent = label;
  apply(t);
}
scrub.addEventListener("input", () => { playing = false; btn.textContent = "播放"; setT(scrub.value/1000); });
btn.addEventListener("click", () => {
  playing = !playing;
  if (playing && t >= 1) setT(0);
  btn.textContent = playing ? "暫停" : "播放";
});
for (const b of host.querySelectorAll("[data-goto]")){
  b.addEventListener("click", () => { playing = false; btn.textContent = "播放"; setT(parseFloat(b.dataset.goto)); });
}

/* ---- the IR, rendered from the payload so it cannot drift ---- */
const KIND_ZH = {split_by_tier:"依 tier 拆分", reinterpreted:"沿用但語意改變", unchanged:"不變"};
const KIND_CLS = {split_by_tier:"k-split", reinterpreted:"k-reint", unchanged:"k-same"};
const tb = document.getElementById("mig-map");
if (tb){
  for (const m of MIG.masks){
    const tr = document.createElement("tr");
    const kind = document.createElement("td");
    const tag = document.createElement("span");
    tag.className = "kind " + (KIND_CLS[m.kind] || "");
    tag.textContent = KIND_ZH[m.kind] || m.kind;
    kind.append(tag);
    const from = document.createElement("td");
    from.innerHTML = `<span class="mono">${m.from.key}</span> ${m.from.name}`;
    const to = document.createElement("td");
    to.innerHTML = m.to.map(x => `<span class="mono">${x.key}</span> ${x.name}`).join("<br>");
    const note = document.createElement("td"); note.textContent = m.note;
    tr.append(kind, from, to, note); tb.append(tr);
  }
}
const ml = document.getElementById("mig-model");
if (ml){
  for (const a of MIG.model.added){
    const li = document.createElement("li");
    li.innerHTML = `<span class="mono">${a.name}</span> <span class="tag">${a.kind}</span>` +
                   (a.draws_geometry ? "" : ` <span class="tag warn">無幾何</span>`) +
                   `<br><span class="d">${a.note}</span>`;
    ml.append(li);
  }
}
const iv = document.getElementById("mig-inv");
if (iv){
  for (const i of MIG.invariants){
    const li = document.createElement("li");
    li.innerHTML = `<b>${i.what}</b> <span class="mono">${i.value}</span><br><span class="d">${i.why}</span>`;
    iv.append(li);
  }
}

/* ---- boot ---- */
function resize(){
  const r = canvas.parentElement.getBoundingClientRect();
  const w = Math.max(320, r.width), h = Math.max(340, r.height);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(w, h, false);
  camera.aspect = w/h; camera.updateProjectionMatrix();
  fit(); applyCamera();
}
window.addEventListener("resize", resize);
resize(); setT(0);

(function loop(now){
  requestAnimationFrame(loop);
  if (playing){
    const dt = (now - last) / 1000;
    setT(t + dt / 6);                    // a full pass takes ~6 s
    if (t >= 1){ playing = false; btn.textContent = "重播"; }
  }
  last = now;
  renderer.render(scene, camera);
})(last);

if (!reduceMotion){
  // Start on the FinFET side and play once, so the page's first impression is
  // the transformation itself rather than a still frame someone has to find a
  // button for.
  setTimeout(() => { if (t === 0){ playing = true; btn.textContent = "暫停"; } }, 900);
}
})();
