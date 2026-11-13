/*
 * DeviceHands hero film.
 *
 * One board, five test probes. The probes are the "hand": they drum on five
 * contact points and each touch sends a pulse along a trace to the SoC.
 * Scroll position (window.__dhP, 0..6, written by site.js) moves the camera
 * from the wide shot through five macro shots to a top-down finale.
 *
 * Units are millimetres. The board is 100 x 70 x 1.6, top face at y = 0.
 */
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';

const stage = document.getElementById('stage');
const canvas = document.getElementById('stage-canvas');
const Q = new URLSearchParams(location.search);
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const SMALL = Math.min(innerWidth, innerHeight) < 700;
const SHOT = Q.has('shot');   /* screenshot mode: no free-running loop, frames are stepped by hand */
const LOW = SMALL || (navigator.deviceMemory && navigator.deviceMemory <= 4) || Q.has('low');

/* ---------- small helpers ---------- */
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const lerp = (a, b, t) => a + (b - a) * t;

/* Offset a polyline sideways by d with mitred corners. pts: [[x,z],...] */
function offsetPolyline(pts, d) {
  const n = pts.length, out = [];
  const segN = [];
  for (let i = 0; i < n - 1; i++) {
    const dx = pts[i + 1][0] - pts[i][0], dz = pts[i + 1][1] - pts[i][1];
    const l = Math.hypot(dx, dz) || 1;
    segN.push([-dz / l, dx / l]);
  }
  for (let i = 0; i < n; i++) {
    const a = segN[Math.max(0, i - 1)], b = segN[Math.min(n - 2, i)];
    let mx = a[0] + b[0], mz = a[1] + b[1];
    const ml = Math.hypot(mx, mz) || 1; mx /= ml; mz /= ml;
    const k = d / Math.max(0.35, mx * a[0] + mz * a[1]);
    out.push([pts[i][0] + mx * k, pts[i][1] + mz * k]);
  }
  return out;
}

/* ---------- layout: every part on the board, one list ---------- */
const SOC = { x: -6, z: -6, s: 19 };
const TOUCH = [
  { id: 'uart',  p: [24.54, 8.6, 30.5] },   // top of the TX pin
  { id: 'usb',   p: [-36.5, 0.03, 3.2] },   // D+ test pad
  { id: 'flash', p: [21.2, 0.03, -3.6] },   // pad beside the storage chip
  { id: 'time',  p: [-17.2, 0.03, 15.6] },  // pad beside the crystal
  { id: 'soc',   p: [-10.4, 2.12, -2.2] },  // on the SoC lid
];
/* signal routes: contact point -> SoC edge, 45 degree bends only */
const ROUTES = [
  { lanes: 2, pts: [[24.54, 28.4], [24.54, 20], [14.54, 10], [5, 10], [-1.5, 3.5]] },
  { lanes: 2, pts: [[-36.5, 3.2], [-30, 3.2], [-24, -2.8], [-15.5, -2.8]] },
  { lanes: 4, pts: [[21.2, -3.6], [16, -3.6], [11.4, -8.2], [3.5, -8.2]] },
  { lanes: 1, pts: [[-17.2, 15.6], [-12, 15.6], [-8, 11.6], [-8, 3.5]] },
  { lanes: 0, pts: [] },
];

function buildParts() {
  const R = rng(20261113);
  const parts = [];
  const add = (o) => (parts.push(o), o);
  add({ t: 'soc', x: SOC.x, z: SOC.z });
  add({ t: 'chip', x: 15, z: -22, w: 14, d: 10, h: 0.9, ref: 'U2', mark: ['K4 LP4X', '2GB  2611'] });
  add({ t: 'chip', x: 30, z: -11, w: 11.5, d: 13, h: 1.0, ref: 'U3', mark: ['eMMC 5.1', '32GB', 'A1 2611'] });
  add({ t: 'chip', x: -33, z: -21, w: 6, d: 6, h: 0.85, ref: 'U4', mark: ['PMIC', '809'] });
  add({ t: 'chip', x: 8, z: 22, w: 5, d: 4, h: 0.8, ref: 'U6', mark: ['LDO'] });
  add({ t: 'chip', x: -24, z: 24.5, w: 4, d: 4, h: 0.8, ref: 'U7', mark: ['RTC'] });
  add({ t: 'shield', x: 34, z: 13, w: 17, d: 13, ref: 'U5' });
  add({ t: 'usbc', x: -46.4, z: 8 });
  add({ t: 'header', x: 22, z: 30.5, n: 4, ref: 'J3', labels: ['GND', 'TX', 'RX', '3V3'] });
  add({ t: 'fpc', x: 46.2, z: -12, ref: 'J2' });
  add({ t: 'button', x: -39, z: 28.6, ref: 'SW1', label: 'RESET' });
  add({ t: 'button', x: -30.5, z: 28.6, ref: 'SW2', label: 'BOOT' });
  add({ t: 'led', x: -16, z: 30.6, lit: true, ref: 'D1', label: 'PWR' });
  add({ t: 'led', x: -11.5, z: 30.6, lit: false, ref: 'D2', label: 'ACT', act: true });
  add({ t: 'xtal', x: -22.4, z: 12, ref: 'Y1' });
  add({ t: 'xtal', x: 26, z: 25, ref: 'Y2' });
  [[-41, -13], [-41, -21], [-33, -29.5], [-24.5, -29.5]].forEach(([x, z], i) => add({ t: 'ind', x, z, ref: 'L' + (i + 1) }));
  add({ t: 'tant', x: -22, z: -22, ref: 'C1' });
  add({ t: 'tant', x: -1, z: 28.5, ref: 'C2', rot: 1 });
  add({ t: 'tant', x: 44, z: 27, ref: 'C3' });
  TOUCH.slice(1, 4).forEach((c, i) => add({ t: 'tp', x: c.p[0], z: c.p[2], label: ['D+', 'TP7', 'TP12'][i] }));
  add({ t: 'tp', x: -36.5, z: 0.4, label: 'D-', quiet: true });
  [[-45, -30], [45, -30], [-45, 30], [45, 30]].forEach(([x, z]) => add({ t: 'hole', x, z }));
  [[-40, -4], [40, -30], [6, 31]].forEach(([x, z]) => add({ t: 'fid', x, z }));

  /* passives: a ring round the SoC, then clusters near the other chips */
  const passive = (x, z, rot, big, res) => add({ t: 'mlcc', x, z, rot, big, res });
  const h = SOC.s / 2 + 1.6;
  for (let i = -4; i <= 4; i++) {
    if (R() < 0.12) continue;
    passive(SOC.x + i * 2.05, SOC.z - h, 1, false, R() < 0.25);
    if (i < 1 || i > 3) passive(SOC.x + i * 2.05, SOC.z + h, 1, false, R() < 0.25);
    if (Math.abs(i) !== 1) passive(SOC.x - h, SOC.z + i * 2.05, 0, false, R() < 0.25);
    if (i > -1 && i !== 2) passive(SOC.x + h, SOC.z + i * 2.05, 0, false, R() < 0.25);
  }
  const cluster = (cx, cz, nx, nz, px, pz, rot, big, skip = 0.15) => {
    for (let i = 0; i < nx; i++) for (let j = 0; j < nz; j++) {
      if (R() < skip) continue;
      passive(cx + i * px, cz + j * pz, rot, big, R() < 0.3);
    }
  };
  cluster(-38, -26.5, 1, 4, 0, 2.4, 0, true, 0);     // PMIC output caps
  cluster(-29, -15.5, 4, 1, 2.3, 0, 1, true, 0);
  cluster(-28.3, -24.5, 1, 3, 0, 1.9, 0, false, 0);
  cluster(5.5, -29.8, 9, 1, 2.1, 0, 1, false);         // DRAM decoupling
  cluster(24.4, -25, 1, 3, 0, 2, 0, false, 0);
  cluster(22.5, -19.8, 4, 1, 2.1, 0, 1, false);        // storage
  cluster(38.2, -16, 1, 5, 0, 2.1, 0, false);
  cluster(22, 4.2, 6, 1, 2.2, 0, 1, false);            // radio
  cluster(23.2, 8.6, 1, 4, 0, 2.2, 0, false);
  cluster(-44, 16.5, 3, 2, 2.3, 2.1, 1, false);        // USB
  cluster(-30.5, 8, 2, 3, 2.3, 2.1, 0, false, 0.25);
  cluster(13.5, 17.5, 3, 2, 2.2, 2.4, 1, true, 0.2);
  cluster(-8, 21.5, 3, 3, 2.3, 2.1, 0, false, 0.3);
  cluster(33, 26.5, 3, 1, 2.3, 0, 1, false, 0);
  cluster(-27, 17.2, 2, 1, 2.2, 0, 1, false, 0);        // crystal load caps
  cluster(20.2, 25.4, 1, 2, 0, 2.2, 0, false, 0);
  cluster(-20, 29.4, 2, 1, 6.6, 0, 0, false, 0);        // LED resistors
  return parts;
}

/* ---------- board textures: colour, bump, roughness+metalness ---------- */
function paintBoard(S, parts) {
  const W = 100 * S, H = 70 * S;
  const mk = () => { const c = document.createElement('canvas'); c.width = W; c.height = H; return c; };
  const cC = mk(), cB = mk(), cO = mk();
  const C = cC.getContext('2d'), B = cB.getContext('2d'), O = cO.getContext('2d');
  const orm = (r, m) => `rgb(0,${Math.round(r * 255)},${Math.round(m * 255)})`;
  const STY = {
    mask:  ['#07090b', '#808080', orm(0.44, 0)],
    trace: ['#0f1317', '#a2a2a2', orm(0.38, 0)],
    pour:  ['#0a0d10', '#8c8c8c', orm(0.42, 0)],
    gold:  ['#d2ad66', '#5c5c5c', orm(0.30, 1)],
    hole:  ['#020202', '#202020', orm(0.9, 0)],
    silk:  ['#cbc8bf', '#b2b2b2', orm(0.88, 0)],
    dim:   ['#8f8d86', '#a4a4a4', orm(0.85, 0)],
  };
  const each = (sty, fn) => [C, B, O].forEach((ctx, i) => {
    ctx.save(); ctx.translate(50 * S, 35 * S); ctx.scale(S, S);
    ctx.fillStyle = ctx.strokeStyle = STY[sty][i]; fn(ctx); ctx.restore();
  });
  const poly = (ctx, pts) => { ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); };
  const stroke = (sty, pts, w) => each(sty, (ctx) => { ctx.lineWidth = w; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; poly(ctx, pts); ctx.stroke(); });
  const rect = (sty, x, z, w, d, r = 0) => each(sty, (ctx) => { ctx.beginPath(); r ? ctx.roundRect(x - w / 2, z - d / 2, w, d, r) : ctx.rect(x - w / 2, z - d / 2, w, d); ctx.fill(); });
  const frame = (sty, x, z, w, d, lw = 0.15, r = 0) => each(sty, (ctx) => { ctx.lineWidth = lw; ctx.beginPath(); ctx.roundRect(x - w / 2, z - d / 2, w, d, r); ctx.stroke(); });
  const disc = (sty, x, z, r) => each(sty, (ctx) => { ctx.beginPath(); ctx.arc(x, z, r, 0, 7); ctx.fill(); });
  const ring = (sty, x, z, r, lw) => each(sty, (ctx) => { ctx.lineWidth = lw; ctx.beginPath(); ctx.arc(x, z, r, 0, 7); ctx.stroke(); });
  const text = (sty, s, x, z, h, o = {}) => each(sty, (ctx) => {
    ctx.font = `${o.w || 500} ${h}px "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace`;
    ctx.textAlign = o.align || 'center'; ctx.textBaseline = 'middle';
    if (o.rot) { ctx.translate(x, z); ctx.rotate(o.rot); ctx.fillText(s, 0, 0); } else ctx.fillText(s, x, z);
  });
  const via = (x, z, r = 0.3) => { disc('gold', x, z, r); disc('hole', x, z, r * 0.45); };

  [C, B, O].forEach((ctx, i) => { ctx.fillStyle = STY.mask[i]; ctx.fillRect(0, 0, W, H); });

  /* occupancy grid so random traces never cross */
  const G = 2, GW = 100 * G, GH = 70 * G, occ = new Uint8Array(GW * GH);
  const cell = (x, z) => { const i = Math.floor((x + 50) * G), j = Math.floor((z + 35) * G); return i < 0 || j < 0 || i >= GW || j >= GH ? -1 : j * GW + i; };
  const walk = (pts, half, fn) => {
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, az] = pts[i], [bx, bz] = pts[i + 1], l = Math.hypot(bx - ax, bz - az), n = Math.ceil(l / 0.25);
      const nx = -(bz - az) / (l || 1), nz = (bx - ax) / (l || 1);
      for (let k = 0; k <= n; k++) for (let o = -half; o <= half; o += 0.25) {
        if (fn(ax + (bx - ax) * k / n + nx * o, az + (bz - az) * k / n + nz * o) === false) return false;
      }
    }
    return true;
  };
  const bundle = (pts, lanes, pitch, w) => {
    for (let k = 0; k < lanes; k++) stroke('trace', offsetPolyline(pts, (k - (lanes - 1) / 2) * pitch), w);
    walk(pts, lanes * pitch / 2 + 0.5, (x, z) => { const c = cell(x, z); if (c >= 0) occ[c] = 1; });
  };

  /* reserve the keep-outs under big parts so traces dive under them, not through pads */
  ROUTES.forEach((r) => {
    if (!r.lanes) return;
    bundle(r.pts, r.lanes, 0.34, 0.15);
  });

  const R = rng(7);
  const DIR = [[1, 0], [0.7071, 0.7071], [0, 1], [-0.7071, 0.7071], [-1, 0], [-0.7071, -0.7071], [0, -1], [0.7071, -0.7071]];
  let placed = 0;
  for (let tries = 0; tries < 2600 && placed < 150; tries++) {
    const lanes = R() < 0.45 ? 1 : 1 + Math.floor(R() * 7);
    let x = -46 + R() * 92, z = -32 + R() * 64, d = Math.floor(R() * 8);
    if (d % 2 && R() < 0.7) d = (d + 1) % 8;
    const pts = [[x, z]], turn = R() < 0.5 ? 1 : -1, segs = 2 + Math.floor(R() * 3);
    for (let s = 0; s < segs; s++) {
      const len = s % 2 ? 2 + R() * 9 : 4 + R() * 20;
      x += DIR[d][0] * len; z += DIR[d][1] * len; pts.push([x, z]);
      d = (d + turn + 8) % 8;
    }
    if (pts.some(([px, pz]) => Math.abs(px) > 47.5 || Math.abs(pz) > 32.8)) continue;
    const half = lanes * 0.34 / 2 + 0.55;
    if (!walk(pts, half, (px, pz) => { const c = cell(px, pz); return !(c < 0 || occ[c]); })) continue;
    bundle(pts, lanes, 0.34, 0.15);
    const a = pts[0], b = pts[pts.length - 1];
    for (let k = 0; k < lanes; k++) {
      const o = offsetPolyline(pts, (k - (lanes - 1) / 2) * 0.34);
      if (lanes < 3 || k % 2 === 0) { via(o[0][0], o[0][1], 0.27); via(o[o.length - 1][0], o[o.length - 1][1], 0.27); }
    }
    void a; void b; placed++;
  }
  /* stitching vias along the edge and a few loose ones */
  for (let i = 0; i < 46; i++) { via(-46 + i * 2, -33.6, 0.22); via(-46 + i * 2, 33.6, 0.22); }
  for (let i = 0; i < 31; i++) { via(-48.6, -30 + i * 2, 0.22); via(48.6, -30 + i * 2, 0.22); }

  /* footprints */
  for (const p of parts) {
    const { x, z } = p;
    if (p.t === 'mlcc') {
      const L = p.big ? 1.6 : 1.0, Wd = p.big ? 0.8 : 0.5, e = L * 0.36;
      const pad = (dx, dz) => rect('gold', x + dx, z + dz, p.rot ? Wd + 0.16 : e, p.rot ? e : Wd + 0.16, 0.05);
      if (p.rot) { pad(0, -L / 2 + 0.05); pad(0, L / 2 - 0.05); } else { pad(-L / 2 + 0.05, 0); pad(L / 2 - 0.05, 0); }
    } else if (p.t === 'soc') {
      rect('pour', x, z, 22, 22, 0.6);
      frame('silk', x, z, 20.4, 20.4, 0.14, 0.3);
      disc('silk', x - 11.2, z - 11.2, 0.38);
      text('silk', 'U1', x + 8.6, z + 12.1, 1.15);
    } else if (p.t === 'chip') {
      const n = Math.floor(p.w / 0.8), m = Math.floor(p.d / 0.8);
      if (p.w <= 7) {
        for (let i = 0; i < n; i++) { rect('gold', x - p.w / 2 + 0.6 + i * (p.w - 1.2) / (n - 1), z - p.d / 2 - 0.1, 0.3, 0.8); rect('gold', x - p.w / 2 + 0.6 + i * (p.w - 1.2) / (n - 1), z + p.d / 2 + 0.1, 0.3, 0.8); }
        for (let i = 0; i < m; i++) { rect('gold', x - p.w / 2 - 0.1, z - p.d / 2 + 0.6 + i * (p.d - 1.2) / (m - 1), 0.8, 0.3); rect('gold', x + p.w / 2 + 0.1, z - p.d / 2 + 0.6 + i * (p.d - 1.2) / (m - 1), 0.8, 0.3); }
      }
      frame('silk', x, z, p.w + 1.3, p.d + 1.3, 0.13);
      disc('silk', x - p.w / 2 - 1.05, z - p.d / 2 - 0.3, 0.3);
      text('silk', p.ref, x - p.w / 2 + 0.9, z + p.d / 2 + 1.55, 1.05);
    } else if (p.t === 'shield') {
      frame('gold', x, z, p.w + 0.5, p.d + 0.5, 0.5, 0.4);
      text('silk', p.ref + '  WLAN/BT', x - 1.5, z + p.d / 2 + 1.5, 1.05);
    } else if (p.t === 'usbc') {
      for (let i = 0; i < 12; i++) rect('gold', x + 4.3, z - 2.75 + i * 0.5, 1.1, 0.28);
      [[-1.5, -5.1], [-1.5, 5.1], [2.6, -5.1], [2.6, 5.1]].forEach(([dx, dz]) => { rect('gold', x + dx, z + dz, 2.2, 1.3, 0.5); });
      text('silk', 'J1', x + 5.2, z - 6.6, 1.1);
      text('silk', 'USB', x + 9.5, z + 6.9, 1.25, { w: 600 });
    } else if (p.t === 'header') {
      frame('silk', x + 3.81, z, 10.7, 2.9, 0.16);
      for (let i = 0; i < p.n; i++) {
        const px = x + i * 2.54;
        i ? disc('gold', px, z, 0.95) : rect('gold', px, z, 1.9, 1.9, 0.1);
        disc('hole', px, z, 0.5);
        text('silk', p.labels[i], px, z - 2.75, 0.98, { w: 600 });
      }
      text('silk', p.ref, x - 3.4, z + 0.1, 1.1);
      text('silk', 'UART', x + 14.4, z + 0.1, 1.5, { w: 600 });
      text('dim', '115200 8N1', x + 13.9, z - 2.3, 0.8, { align: 'left' });
    } else if (p.t === 'fpc') {
      for (let i = 0; i < 24; i++) rect('gold', x - 3.1, z - 5.75 + i * 0.5, 1.3, 0.28);
      text('silk', p.ref, x - 3.4, z - 8.6, 1.05);
      text('silk', 'DSI', x - 5.2, z + 5.2, 1.05, { rot: -Math.PI / 2 });
    } else if (p.t === 'button') {
      [[-2.9, -1.6], [2.9, -1.6], [-2.9, 1.6], [2.9, 1.6]].forEach(([dx, dz]) => rect('gold', x + dx, z + dz, 1.2, 0.9));
      text('silk', p.label, x, z - 3.7, 1.05, { w: 600 });
    } else if (p.t === 'led') {
      rect('gold', x - 0.75, z, 0.5, 0.9); rect('gold', x + 0.75, z, 0.5, 0.9);
      text('silk', p.label, x, z - 1.75, 0.85);
    } else if (p.t === 'xtal') {
      rect('gold', x - 1.2, z, 1.0, 2.9); rect('gold', x + 1.2, z, 1.0, 2.9);
      text('silk', p.ref, x, z - 2.5, 0.95);
    } else if (p.t === 'ind') {
      rect('gold', x - 1.75, z, 1.3, 4.2); rect('gold', x + 1.75, z, 1.3, 4.2);
      text('silk', p.ref, x, z + 3.1, 0.95);
    } else if (p.t === 'tant') {
      const a = p.rot ? [0, 1.5] : [1.5, 0];
      rect('gold', x - a[0], z - a[1], p.rot ? 3.0 : 1.3, p.rot ? 1.3 : 3.0); rect('gold', x + a[0], z + a[1], p.rot ? 3.0 : 1.3, p.rot ? 1.3 : 3.0);
      text('silk', '+', x - (p.rot ? 2.4 : 2.9), z - (p.rot ? 2.4 : 0), 1.3);
    } else if (p.t === 'tp') {
      disc('gold', x, z, 0.85);
      if (!p.quiet) ring('silk', x, z, 1.35, 0.12);
      text('silk', p.label, x + (p.label.length > 2 ? 0 : 0), z + 2.45, 0.9, { w: 600 });
    } else if (p.t === 'hole') {
      disc('gold', x, z, 3.0);
      for (let k = 0; k < 8; k++) disc('hole', x + Math.cos(k * 0.785) * 2.35, z + Math.sin(k * 0.785) * 2.35, 0.22);
    } else if (p.t === 'fid') {
      disc('pour', x, z, 1.2); disc('gold', x, z, 0.5);
    }
  }

  /* silkscreen identity block */
  const hx = 3.5, hz = 14.4;
  [0, 1, 2, 3, 4].forEach((i) => rect('silk', hx + i * 1.05, hz - [0.7, 1.25, 1.5, 1.2, 0.55][i] / 2 + 0.9, 0.5, [1.9, 3.0, 3.5, 2.9, 1.6][i], 0.25));
  text('silk', 'DeviceHands', hx + 6.2, hz + 0.35, 2.0, { align: 'left', w: 600 });
  text('dim', 'DEMO BOARD  REV A  2026-11', hx + 6.3, hz + 2.3, 0.82, { align: 'left' });
  text('dim', 'hands for your AI', -38, 33.0 - 0.9, 0.8, { align: 'left' });
  text('dim', '94V-0  4L  ENIG', 30, 32.1, 0.8, { align: 'left' });

  const tex = (c, srgb) => {
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    t.anisotropy = 16; t.repeat.set(1 / 100, 1 / 70); t.offset.set(0.5, 0.5);
    t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping; t.generateMipmaps = true;
    return t;
  };
  return { map: tex(cC, true), bump: tex(cB, false), orm: tex(cO, false) };
}

/* text on chips and lids: returns colour + roughness maps */
function etchTexture(draw, size = 512, base = '#ffffff', rough = [0.3, 0.62]) {
  const mk = () => { const c = document.createElement('canvas'); c.width = c.height = size; return c; };
  const a = mk(), b = mk();
  const A = a.getContext('2d'), Bc = b.getContext('2d');
  A.fillStyle = base; A.fillRect(0, 0, size, size);
  Bc.fillStyle = `rgb(0,${Math.round(rough[0] * 255)},255)`; Bc.fillRect(0, 0, size, size);
  draw(A, size, false); Bc.fillStyle = Bc.strokeStyle = `rgb(0,${Math.round(rough[1] * 255)},255)`; draw(Bc, size, true);
  const ta = new THREE.CanvasTexture(a); ta.colorSpace = THREE.SRGBColorSpace; ta.anisotropy = 8;
  const tb = new THREE.CanvasTexture(b); tb.anisotropy = 8;
  return [ta, tb];
}

/* ---------- studio: soft boxes baked into an environment map ---------- */
function makeStudio(renderer) {
  const s = new THREE.Scene();
  s.background = new THREE.Color(0x010101);
  const box = (w, h, pos, k, col = 0xffffff) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(col).multiplyScalar(k), side: THREE.DoubleSide }));
    m.position.set(...pos); m.lookAt(0, 0, 0); s.add(m);
  };
  box(9, 6, [0.5, 7, -2.5], 6.2);                 // overhead key, a little behind
  box(1.3, 10, [-8, 3, 1.5], 13, 0xf2f6ff);       // cool strip, left rim
  box(1.1, 8, [8, 2.4, -3], 9, 0xffdfba);         // warm strip, right rim
  box(7, 1.6, [0, 1.2, 9], 0.55);                  // low front fill
  box(3, 3, [-3, 5, 6], 1.0, 0xfff1e0);           // small kicker
  const pm = new THREE.PMREMGenerator(renderer);
  const rt = pm.fromScene(s, 0.03);
  pm.dispose();
  return rt.texture;
}

/* ---------- build ---------- */
async function build() {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'high-performance' });
  renderer.setClearColor(0x000000, 1);
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  try { await Promise.race([document.fonts.load('500 16px "IBM Plex Mono"').then(() => document.fonts.load('600 16px "IBM Plex Mono"')), new Promise((r) => setTimeout(r, 1500))]); } catch { /* fall back to system mono */ }

  const scene = new THREE.Scene();
  scene.environment = makeStudio(renderer);
  scene.environmentIntensity = 1.0;
  const camera = new THREE.PerspectiveCamera(30, 1, 1, 1500);

  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(40, 150, -50); key.castShadow = true;
  key.shadow.mapSize.set(LOW ? 1024 : 2048, LOW ? 1024 : 2048);
  Object.assign(key.shadow.camera, { left: -70, right: 70, top: 70, bottom: -70, near: 20, far: 320 });
  key.shadow.bias = -0.0004; key.shadow.normalBias = 0.05;
  scene.add(key);

  const M = (o) => new THREE.MeshStandardMaterial(o);
  const mat = {
    gold: M({ color: 0xdcb46a, metalness: 1, roughness: 0.26 }),
    steel: M({ color: 0xc4c7cb, metalness: 1, roughness: 0.3 }),
    nickel: M({ color: 0xb7b9bb, metalness: 1, roughness: 0.2 }),
    tin: M({ color: 0xc9cccd, metalness: 1, roughness: 0.48 }),
    cap: M({ color: 0xb2946c, roughness: 0.62 }),
    res: M({ color: 0x0c0c0d, roughness: 0.7 }),
    plastic: M({ color: 0x0a0a0b, roughness: 0.48 }),
    ferrite: M({ color: 0x28292b, roughness: 0.92 }),
    tant: M({ color: 0xd29a17, roughness: 0.55 }),
    tantBand: M({ color: 0x6b3d10, roughness: 0.6 }),
    beige: M({ color: 0xc9c0aa, roughness: 0.6 }),
    anod: M({ color: 0x2a2c30, metalness: 1, roughness: 0.36 }),
    rubber: M({ color: 0x070707, roughness: 0.85 }),
    ceramic: M({ color: 0xe9e6de, roughness: 0.35 }),
    substrate: M({ color: 0x0d1a1c, roughness: 0.55 }),
    edge: M({ color: 0x6f6540, roughness: 0.8 }),
    ledBody: M({ color: 0xe6e3d8, roughness: 0.4 }),
  };

  const parts = buildParts();
  const board = new THREE.Group();
  scene.add(board);

  /* board body with real mounting holes */
  const T = paintBoard(LOW ? 20 : 34, parts);
  const shape = new THREE.Shape();
  const bw = 50, bh = 35, br = 3;
  shape.moveTo(-bw + br, -bh); shape.lineTo(bw - br, -bh); shape.absarc(bw - br, -bh + br, br, -Math.PI / 2, 0);
  shape.lineTo(bw, bh - br); shape.absarc(bw - br, bh - br, br, 0, Math.PI / 2);
  shape.lineTo(-bw + br, bh); shape.absarc(-bw + br, bh - br, br, Math.PI / 2, Math.PI);
  shape.lineTo(-bw, -bh + br); shape.absarc(-bw + br, -bh + br, br, Math.PI, Math.PI * 1.5);
  parts.filter((p) => p.t === 'hole').forEach((p) => { const h = new THREE.Path(); h.absarc(p.x, -p.z, 1.6, 0, Math.PI * 2, true); shape.holes.push(h); });
  const bodyGeo = new THREE.ExtrudeGeometry(shape, { depth: 1.6, bevelEnabled: false, curveSegments: 24 });
  const maskMat = new THREE.MeshPhysicalMaterial({
    map: T.map, bumpMap: T.bump, bumpScale: 0.9, roughnessMap: T.orm, metalnessMap: T.orm,
    roughness: 1, metalness: 1, clearcoat: 0.2, clearcoatRoughness: 0.42,
  });
  const body = new THREE.Mesh(bodyGeo, [maskMat, mat.edge]);
  body.rotation.x = -Math.PI / 2; body.position.y = -1.6; body.receiveShadow = true; body.castShadow = true;
  board.add(body);

  const rb = (w, h, d, r, m, x, y, z, seg = 3) => {
    const o = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, seg, Math.min(r, w / 2 - 0.01, h / 2 - 0.01, d / 2 - 0.01)), m);
    o.position.set(x, y, z); o.castShadow = true; o.receiveShadow = true; board.add(o); return o;
  };
  const cyl = (rt, rbm, h, m, x, y, z, seg = 28) => {
    const o = new THREE.Mesh(new THREE.CylinderGeometry(rt, rbm, h, seg), m);
    o.position.set(x, y, z); o.castShadow = true; board.add(o); return o;
  };
  const mono = (ctx, s, x, y, px, w = 500) => { ctx.font = `${w} ${px}px "IBM Plex Mono", ui-monospace, Menlo, monospace`; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(s, x, y); };
  const topMats = (side, top) => [side, side, top, side, side, side];

  let actLed = null;
  const inst = { cap: [], res: [], tin: [] };
  for (const p of parts) {
    const { x, z } = p;
    if (p.t === 'soc') {
      rb(SOC.s, 1.0, SOC.s, 0.25, mat.substrate, x, 0.5, z, 2);
      const [lm, lr] = etchTexture((c, s, r) => {
        if (!r) c.fillStyle = '#7d8084';
        const u = s / 100;
        [0, 1, 2, 3, 4].forEach((i) => { const h = [13, 20, 23, 19, 11][i] * u; c.beginPath(); c.roundRect((14 + i * 6.4) * u, (40 - h + [0, 0, 0, 0, 0][i]) * u / u + 0, 3.2 * u, h, 1.6 * u); c.fill(); });
        mono(c, 'DeviceHands', 14 * u, 52 * u, 9.2 * u, 600);
        mono(c, 'DH-A1  ARM64', 14 * u, 64 * u, 5.6 * u);
        mono(c, '2611  TW  K7Q93', 14 * u, 72.5 * u, 5.6 * u);
        const q = rng(5);
        for (let i = 0; i < 10; i++) for (let j = 0; j < 10; j++) if (i === 0 || j === 9 || q() < 0.5) c.fillRect((70 + i * 1.7) * u, (62 + j * 1.7) * u, 1.75 * u, 1.75 * u);
        c.beginPath(); c.arc(86 * u, 15 * u, 2.2 * u, 0, 7); c.fill();
      }, 1024, '#ffffff', [0.24, 0.58]);
      const lidTop = M({ color: 0xc2c4c6, metalness: 1, roughness: 1, map: lm, roughnessMap: lr, metalnessMap: lr });
      const lid = rb(14.6, 1.1, 14.6, 0.55, mat.nickel, x, 1.0 + 0.55, z, 4);
      lid.material = topMats(mat.nickel, lidTop);
      /* decoupling caps on the package substrate */
      for (let i = -3; i <= 3; i++) {
        parts.push({ t: 'mlcc', x: x + i * 2.1, z: z - 8.55, rot: 0, y: 1.0, tiny: true });
        parts.push({ t: 'mlcc', x: x + i * 2.1, z: z + 8.55, rot: 0, y: 1.0, tiny: true });
      }
    } else if (p.t === 'chip') {
      const [cm, cr] = etchTexture((c, s, r) => {
        if (!r) c.fillStyle = '#9a9a98';
        const asp = p.w / p.d, lines = p.mark, px = s * Math.min(0.16, 0.9 / Math.max(...lines.map((l) => l.length)) * 1.5);
        c.save(); c.translate(s / 2, s / 2); c.scale(1 / Math.max(asp, 1), Math.min(asp, 1) * (asp < 1 ? 1 : asp) / (asp < 1 ? 1 : asp));
        if (asp < 1) c.scale(1 / asp, 1);
        c.textAlign = 'center';
        lines.forEach((l, i) => { c.font = `500 ${px}px "IBM Plex Mono", ui-monospace, monospace`; c.textBaseline = 'middle'; c.fillText(l, 0, (i - (lines.length - 1) / 2) * px * 1.35); });
        c.restore();
        c.beginPath(); c.arc(s * 0.1, s * 0.1, s * 0.035, 0, 7); c.fill();
      }, 512, '#151516', [0.72, 0.45]);
      const top = M({ color: 0xffffff, roughness: 1, metalness: 0, map: cm, roughnessMap: cr });
      const ch = rb(p.w, p.h, p.d, 0.12, mat.res, x, p.h / 2 + 0.06, z, 2);
      const side = M({ color: 0x131314, roughness: 0.72 });
      ch.material = topMats(side, top);
    } else if (p.t === 'shield') {
      const [sm, sr] = etchTexture((c, s, r) => {
        if (!r) c.fillStyle = '#8a8d90';
        mono(c, 'WLAN / BT', s * 0.1, s * 0.3, s * 0.085, 600);
        mono(c, 'FCC ID  DH-2611', s * 0.1, s * 0.45, s * 0.05);
        if (!r) c.fillStyle = '#3a3b3d';
        for (let i = 0; i < 6; i++) for (let j = 0; j < 2; j++) { c.beginPath(); c.arc(s * (0.14 + i * 0.145), s * (0.68 + j * 0.14), s * 0.018, 0, 7); c.fill(); }
      }, 512, '#ffffff', [0.34, 0.6]);
      const top = M({ color: 0xbfc2c5, metalness: 1, roughness: 1, map: sm, roughnessMap: sr, metalnessMap: sr });
      const sh = rb(p.w, 2.3, p.d, 0.5, mat.steel, x, 1.15, z, 3);
      sh.material = topMats(mat.steel, top);
    } else if (p.t === 'usbc') {
      rb(8.4, 3.2, 8.94, 1.55, mat.steel, x, 1.62, z, 5);
      rb(0.5, 2.3, 7.9, 1.1, mat.plastic, x - 4.02, 1.62, z, 4);
      rb(1.4, 0.62, 6.6, 0.25, M({ color: 0x1b1b1d, roughness: 0.5 }), x - 3.9, 1.62, z, 2);
      for (let k = 0; k < 10; k++) rb(0.9, 0.05, 0.28, 0.02, mat.gold, x - 4.05, 1.95, z - 2.48 + k * 0.55, 1);
    } else if (p.t === 'header') {
      rb(p.n * 2.54, 2.5, 2.5, 0.25, mat.plastic, x + (p.n - 1) * 1.27, 1.25, z, 2);
      for (let i = 0; i < p.n; i++) rb(0.64, 8.2, 0.64, 0.14, mat.gold, x + i * 2.54, 4.5, z, 2);
    } else if (p.t === 'fpc') {
      rb(3.6, 1.5, 14.6, 0.2, mat.beige, x - 0.2, 0.75, z, 2);
      rb(1.3, 1.62, 14.6, 0.2, mat.plastic, x + 1.6, 0.81, z, 2);
    } else if (p.t === 'button') {
      rb(4.4, 1.5, 3.4, 0.3, mat.steel, x, 0.75, z, 2);
      cyl(0.95, 0.95, 0.8, mat.plastic, x, 1.8, z);
    } else if (p.t === 'led') {
      const m = p.lit ? M({ color: 0x9dffb0, roughness: 0.4, emissive: 0x37ff6a, emissiveIntensity: 1.6 })
        : p.act ? M({ color: 0xe6e3d8, roughness: 0.4, emissive: 0xffb84a, emissiveIntensity: 0 }) : mat.ledBody;
      rb(1.6, 0.6, 0.8, 0.1, m, x, 0.3, z, 2);
      if (p.act) actLed = m;
    } else if (p.t === 'xtal') {
      rb(3.2, 0.8, 2.5, 0.25, mat.nickel, x, 0.42, z, 3);
    } else if (p.t === 'ind') {
      rb(4, 2.1, 4, 0.55, mat.ferrite, x, 1.05, z, 3);
    } else if (p.t === 'tant') {
      const r = p.rot;
      rb(r ? 2.8 : 3.5, 1.9, r ? 3.5 : 2.8, 0.2, mat.tant, x, 0.95, z, 2);
      rb(r ? 2.84 : 0.7, 1.94, r ? 0.7 : 2.84, 0.2, mat.tantBand, x - (r ? 0 : 1.2), 0.95, z - (r ? 1.2 : 0), 2);
    }
  }
  for (const p of parts) {
    if (p.t !== 'mlcc') continue;
    const L = p.tiny ? 0.6 : p.big ? 1.6 : 1.0, Wd = p.tiny ? 0.3 : p.big ? 0.8 : 0.5, y = (p.y || 0) + Wd / 2 + 0.03;
    const m = new THREE.Matrix4(), q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), p.rot ? Math.PI / 2 : 0);
    const put = (arr, dx, sx, k) => {
      const v = new THREE.Vector3(dx, 0, 0).applyQuaternion(q);
      m.compose(new THREE.Vector3(p.x + v.x, y, p.z + v.z), q, new THREE.Vector3(sx, Wd * k, Wd * k));
      arr.push(m.clone());
    };
    put(p.res ? inst.res : inst.cap, 0, L * 0.56, p.res ? 0.82 : 1);
    put(inst.tin, -L * 0.39, L * 0.24, 1.04); put(inst.tin, L * 0.39, L * 0.24, 1.04);
  }
  const unit = new RoundedBoxGeometry(1, 1, 1, 2, 0.12);
  for (const [k, m] of [['cap', mat.cap], ['res', mat.res], ['tin', mat.tin]]) {
    const im = new THREE.InstancedMesh(unit, m, inst[k].length);
    inst[k].forEach((mx, i) => im.setMatrixAt(i, mx));
    im.castShadow = true; im.receiveShadow = true; board.add(im);
  }

  /* ---------- the hand: five probes ---------- */
  const WRIST = new THREE.Vector3(18, 300, 60);
  const UP = new THREE.Vector3(0, 1, 0);
  const probes = TOUCH.map((c, i) => {
    const g = new THREE.Group(), tip = new THREE.Group();
    const target = new THREE.Vector3(...c.p);
    const dir = WRIST.clone().sub(target).normalize();
    const part = (geo, m, y, parent = g) => { const o = new THREE.Mesh(geo, m); o.position.y = y; o.castShadow = true; parent.add(o); return o; };
    const cone = part(new THREE.ConeGeometry(0.34, 1.1, 28), mat.gold, 0.55, tip); cone.rotation.x = Math.PI;
    part(new THREE.CylinderGeometry(0.34, 0.34, 6.5, 28), mat.gold, 1.1 + 3.25, tip);
    g.add(tip);
    part(new THREE.CylinderGeometry(0.62, 0.62, 11, 32), mat.nickel, 6 + 5.5);
    part(new THREE.CylinderGeometry(0.9, 0.9, 1.1, 32), mat.gold, 17.5);
    part(new THREE.CylinderGeometry(1.55, 0.9, 5, 32), mat.anod, 20.5);
    part(new THREE.CylinderGeometry(1.55, 1.55, 44, 32), mat.anod, 45);
    part(new THREE.CylinderGeometry(1.6, 1.6, 0.9, 32), mat.ceramic, 27.2);
    for (let k = 0; k <= i; k++) part(new THREE.CylinderGeometry(1.6, 1.6, 0.28, 32), mat.gold, 30 + k * 0.75);
    part(new THREE.CylinderGeometry(1.25, 1.55, 2.5, 32), mat.anod, 68.2);
    part(new THREE.CylinderGeometry(0.6, 0.6, 320, 16), mat.rubber, 229);
    g.quaternion.setFromUnitVectors(UP, dir);
    scene.add(g);
    /* touch ripple */
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.74, 0.82, 64),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(0xffd9a0).multiplyScalar(2.2), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2; ring.position.copy(target).y += 0.06; scene.add(ring);
    return { g, tip, target, dir, lift: 30, want: 30, vel: 0, down: false, ring, ringT: 9, label: null };
  });

  /* ---------- signal pulses along the routes ---------- */
  const pulseMat = () => new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uHead: { value: -1 }, uLive: { value: 0 }, uColor: { value: new THREE.Color(1.0, 0.78, 0.46) } },
    vertexShader: 'attribute float along; varying float vA; varying float vV; void main(){ vA=along; vV=uv.y; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader: `uniform float uHead,uLive; uniform vec3 uColor; varying float vA; varying float vV;
      void main(){ float d=vA-uHead; float head=exp(-d*d/0.0009); float tail=smoothstep(-0.42,0.0,d)*step(d,0.0);
        float lat=smoothstep(0.0,0.45,1.0-abs(vV*2.0-1.0));
        float a=(head*7.0+tail*1.1+uLive*0.16)*lat; gl_FragColor=vec4(uColor*a,1.0); }`,
  });
  const routes = ROUTES.map((r) => {
    if (!r.lanes) return null;
    const m = pulseMat(), pos = [], uv = [], along = [], idx = [];
    let total = 0; const cum = [0];
    for (let i = 1; i < r.pts.length; i++) { total += Math.hypot(r.pts[i][0] - r.pts[i - 1][0], r.pts[i][1] - r.pts[i - 1][1]); cum.push(total); }
    for (let k = 0; k < r.lanes; k++) {
      const off = (k - (r.lanes - 1) / 2) * 0.34;
      const a = offsetPolyline(r.pts, off - 0.2), b = offsetPolyline(r.pts, off + 0.2), base = pos.length / 3;
      for (let i = 0; i < r.pts.length; i++) {
        pos.push(a[i][0], 0.035, a[i][1], b[i][0], 0.035, b[i][1]);
        uv.push(0, 0, 0, 1); along.push(cum[i] / total, cum[i] / total);
        if (i) { const q = base + i * 2; idx.push(q - 2, q - 1, q, q, q - 1, q + 1); }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setAttribute('along', new THREE.Float32BufferAttribute(along, 1));
    g.setIndex(idx);
    const mesh = new THREE.Mesh(g, m); mesh.material.side = THREE.DoubleSide; scene.add(mesh);
    return { m, total, head: -1 };
  });
  /* the SoC answers: a soft ring on the lid when data lands */
  const halo = new THREE.Mesh(new THREE.RingGeometry(7.9, 8.15, 4, 1, Math.PI / 4),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(0xffcf8f).multiplyScalar(2.0), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
  halo.rotation.x = -Math.PI / 2; halo.position.set(SOC.x, 1.06, SOC.z); halo.scale.setScalar(1.3); scene.add(halo);
  let haloT = 9;

  /* dust in the light */
  const DN = LOW ? 60 : 160, dpos = new Float32Array(DN * 3), dr = rng(99);
  for (let i = 0; i < DN; i++) { dpos[i * 3] = (dr() - 0.5) * 220; dpos[i * 3 + 1] = dr() * 110 + 4; dpos[i * 3 + 2] = (dr() - 0.5) * 200; }
  const dust = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(dpos, 3)),
    new THREE.PointsMaterial({ color: 0xfff2dc, size: 0.5, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true }));
  scene.add(dust);

  /* ---------- post ---------- */
  const rt = new THREE.WebGLRenderTarget(4, 4, { type: THREE.HalfFloatType, samples: LOW ? 2 : 4 });
  const composer = new EffectComposer(renderer, rt);
  composer.addPass(new RenderPass(scene, camera));
  const bokeh = LOW ? null : new BokehPass(scene, camera, { focus: 100, aperture: 0.0002, maxblur: 0.007 });
  if (bokeh) composer.addPass(bokeh);
  const bloom = new UnrealBloomPass(new THREE.Vector2(4, 4), 0.3, 0.5, 2.6);
  composer.addPass(bloom);
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.0;
  composer.addPass(new OutputPass());
  const finish = new ShaderPass({
    uniforms: { tDiffuse: { value: null }, uT: { value: 0 }, uVig: { value: 0.55 } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader: `uniform sampler2D tDiffuse; uniform float uT,uVig; varying vec2 vUv;
      float h(vec2 p){ return fract(sin(dot(p,vec2(12.9898,78.233))+uT)*43758.5453); }
      void main(){ vec4 c=texture2D(tDiffuse,vUv); vec2 q=vUv-0.5; float v=smoothstep(0.85,0.2,length(q*vec2(1.0,1.25)));
        c.rgb*=mix(1.0-uVig,1.0,v); c.rgb+=(h(vUv*vec2(1920.0,1080.0))-0.5)*0.022; gl_FragColor=c; }`,
  });
  composer.addPass(finish);

  /* ---------- camera keyframes: wide, five macros, finale ---------- */
  const wide = innerWidth / innerHeight > 1.05;
  const KF = wide ? [
    { pos: [-56, 74, 170], tgt: [2, 7, 2], fov: 30, ox: 0.17, oy: 0.05, ap: 0.00010 },
    { pos: [62, 20, 76], tgt: [27, 5.2, 28.5], fov: 28, ox: 0.23, oy: 0.03, ap: 0.00042 },
    { pos: [-84, 22, 44], tgt: [-39, 1.6, 5.5], fov: 28, ox: 0.23, oy: 0.02, ap: 0.00042 },
    { pos: [62, 30, 36], tgt: [25, 0.8, -8], fov: 28, ox: 0.23, oy: 0.02, ap: 0.00040 },
    { pos: [-56, 24, 56], tgt: [-19.5, 0.8, 14], fov: 28, ox: 0.23, oy: 0.02, ap: 0.00042 },
    { pos: [-42, 34, 44], tgt: [-7, 1.6, -5], fov: 30, ox: 0.23, oy: 0.02, ap: 0.00036 },
    { pos: [66, 88, 160], tgt: [0, 2, 4], fov: 30, ox: 0, oy: 0.13, ap: 0.00008 },
  ] : [
    { pos: [-70, 120, 250], tgt: [0, 6, 0], fov: 34, ox: 0, oy: 0.12, ap: 0.0001 },
    { pos: [62, 24, 96], tgt: [27, 5.2, 28.5], fov: 34, ox: 0, oy: -0.16, ap: 0.0004 },
    { pos: [-98, 26, 54], tgt: [-39, 1.6, 5.5], fov: 34, ox: 0, oy: -0.16, ap: 0.0004 },
    { pos: [76, 36, 52], tgt: [25, 0.8, -8], fov: 34, ox: 0, oy: -0.16, ap: 0.0004 },
    { pos: [-70, 30, 76], tgt: [-19.5, 0.8, 14], fov: 34, ox: 0, oy: -0.16, ap: 0.0004 },
    { pos: [-54, 44, 60], tgt: [-7, 1.6, -5], fov: 34, ox: 0, oy: -0.16, ap: 0.0004 },
    { pos: [90, 250, 250], tgt: [0, 0, 0], fov: 34, ox: 0, oy: 0.1, ap: 0.00004 },
  ];
  const V = (a) => new THREE.Vector3(...a);
  KF.forEach((k) => { k.pos = V(k.pos); k.tgt = V(k.tgt); });
  const cur = { pos: new THREE.Vector3(), tgt: new THREE.Vector3(), fov: 30, ox: 0, oy: 0, ap: 0 };
  let override = null;

  /* labels that follow the probes */
  const labels = [...document.querySelectorAll('[data-probe]')];
  labels.forEach((el) => { probes[+el.dataset.probe].label = el; });

  let W = 0, H = 0;
  function resize() {
    const w = stage.clientWidth, h = stage.clientHeight;
    if (w === W && h === H) return;
    W = w; H = h;
    const dpr = Math.min(devicePixelRatio || 1, LOW ? 1.5 : 2);
    renderer.setPixelRatio(dpr); renderer.setSize(w, h, false);
    composer.setPixelRatio(dpr); composer.setSize(w, h);
    camera.aspect = w / h;
    if (bokeh) bokeh.uniforms.aspect.value = w / h;
  }

  const mouse = { x: 0, y: 0, sx: 0, sy: 0 };
  addEventListener('pointermove', (e) => { mouse.x = e.clientX / innerWidth - 0.5; mouse.y = e.clientY / innerHeight - 0.5; }, { passive: true });

  const fire = (i) => { const r = routes[i]; if (r) r.head = 0; else haloT = 0; probes[i].ringT = 0; };
  let tPrev = performance.now(), T0 = 0, drum = 0, drumNext = 1.2, beat = [0, 0, 0, 0, 0];
  const tmp = new THREE.Vector3(), tmp2 = new THREE.Vector3();

  function frame(now, draw = true) {
    const dt = Math.min(0.05, (now - tPrev) / 1000); tPrev = now;
    if (!REDUCED || override) T0 += dt;
    resize();
    const p = override ? override.p : clamp(window.__dhP || 0, 0, 6);

    /* camera */
    const i0 = Math.min(5, Math.floor(p)), f = smooth(0.2, 0.8, p - i0), a = KF[i0], b = KF[i0 + 1];
    cur.pos.lerpVectors(a.pos, b.pos, f); cur.tgt.lerpVectors(a.tgt, b.tgt, f);
    /* lift the camera in an arc between macro shots so it never ploughs through parts */
    if (i0 >= 1 && i0 <= 4) cur.pos.y += Math.sin(f * Math.PI) * 26;
    cur.fov = lerp(a.fov, b.fov, f); cur.ox = lerp(a.ox, b.ox, f); cur.oy = lerp(a.oy, b.oy, f); cur.ap = lerp(a.ap, b.ap, f);
    if (override && override.cam) { cur.pos.copy(override.cam.pos); cur.tgt.copy(override.cam.tgt); cur.fov = override.cam.fov || cur.fov; }
    const hero = 1 - smooth(0.0, 0.7, p), fin = smooth(5.3, 6, p);
    mouse.sx += (mouse.x - mouse.sx) * 0.04; mouse.sy += (mouse.y - mouse.sy) * 0.04;
    const sway = (REDUCED ? 0 : Math.sin(T0 * 0.17) * 0.1 * hero + Math.sin(T0 * 0.11) * 0.05 * fin) - mouse.sx * (0.1 + hero * 0.12);
    tmp.copy(cur.pos).sub(cur.tgt).applyAxisAngle(UP, sway); tmp.y *= 1 + mouse.sy * 0.12;
    camera.position.copy(cur.tgt).add(tmp);
    camera.fov = cur.fov; camera.lookAt(cur.tgt);
    camera.setViewOffset(W, H, -cur.ox * W, -cur.oy * H, W, H);
    camera.updateProjectionMatrix();
    if (bokeh) { bokeh.uniforms.focus.value = camera.position.distanceTo(cur.tgt); bokeh.uniforms.aperture.value = cur.ap; }

    /* the hand */
    const chapter = Math.round(p);
    if (chapter === 0) {
      drum += dt;
      if (drum > drumNext && !REDUCED) { const k = beat.idx = ((beat.idx ?? -1) + 1) % 5; beat[k] = 0.42; drum = 0; drumNext = k === 4 ? 2.1 : 0.34; }
    }
    probes.forEach((pr, i) => {
      let want;
      if (chapter === 0) { beat[i] = Math.max(0, beat[i] - dt); want = beat[i] > 0 || REDUCED ? 0 : 13 + Math.sin(T0 * 1.3 + i * 1.7) * 1.2; }
      else if (chapter === 6) want = 0;
      else want = chapter - 1 === i ? 0 : 30;
      /* spring toward the wanted lift */
      const k = want < pr.lift ? 190 : 60, c = want < pr.lift ? 22 : 13;
      pr.vel += ((want - pr.lift) * k - pr.vel * c) * dt; pr.lift += pr.vel * dt;
      if (pr.lift < 0) { pr.lift = 0; pr.vel = 0; }
      const touching = pr.lift < 0.08;
      if (touching && !pr.down) fire(i);
      pr.down = touching;
      pr.g.position.copy(pr.target).addScaledVector(pr.dir, pr.lift + (touching ? -0.5 : 0));
      pr.tip.position.y = touching ? 0.5 : 0;
      /* repeat pulses while held */
      if (touching && chapter > 0 && !REDUCED) { pr.hold = (pr.hold || 0) + dt; if (pr.hold > 1.5 + i * 0.13) { pr.hold = 0; fire(i); } } else pr.hold = 0;
      pr.ringT += dt; const rt2 = pr.ringT / 0.8;
      pr.ring.material.opacity = rt2 < 1 ? (1 - rt2) * (1 - rt2) * 0.55 : 0; pr.ring.scale.setScalar(1 + rt2 * 1.9);
      if (routes[i]) routes[i].m.uniforms.uLive.value += ((touching ? 1 : 0) - routes[i].m.uniforms.uLive.value) * 0.08;
    });
    let landed = false;
    routes.forEach((r) => {
      if (!r) return;
      if (r.head >= 0) { r.head += dt * 62 / r.total; if (r.head > 1.5) { r.head = -1; } else if (r.head > 1 && !r.hit) { r.hit = true; landed = true; } }
      if (r.head < 0.5) r.hit = false;
      r.m.uniforms.uHead.value = r.head < 0 ? -1 : r.head;
    });
    if (landed) haloT = 0;
    haloT += dt; const ht = haloT / 0.9;
    halo.material.opacity = ht < 1 ? (1 - ht) * (1 - ht) * 0.5 : 0; halo.scale.setScalar(1.3 + ht * 0.18);
    if (actLed) actLed.emissiveIntensity = ht < 1 ? (1 - ht) * 2.4 : 0;
    dust.rotation.y = T0 * 0.008;
    finish.uniforms.uT.value = REDUCED ? 0 : (now % 1000) / 1000;

    /* labels */
    probes.forEach((pr, i) => {
      if (!pr.label) return;
      const show = Math.max(!SMALL && hero > 0.75 ? 1 : 0, fin > 0.9 ? 1 : 0);
      tmp2.copy(pr.target).addScaledVector(pr.dir, pr.lift + 19); tmp2.project(camera);
      const x = (tmp2.x * 0.5 + 0.5) * W, y = (-tmp2.y * 0.5 + 0.5) * H;
      pr.label.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0)`;
      pr.label.style.opacity = show && tmp2.z < 1 ? 1 : 0;
      pr.label.classList.toggle('is-down', pr.down);
    });

    if (draw) composer.render();
  }

  let visible = true, raf = 0;
  const loop = (now) => { raf = 0; if (visible && !document.hidden) { frame(now); if (!SHOT) raf = requestAnimationFrame(loop); } };
  const kick = () => { if (!raf) { tPrev = performance.now(); raf = requestAnimationFrame(loop); } };
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) kick(); }, { rootMargin: '200px' }).observe(document.getElementById('film'));
  document.addEventListener('visibilitychange', kick);
  addEventListener('resize', kick);
  kick();
  stage.classList.add('is-live');

  /* hooks for screenshots and tuning */
  window.__dh = {
    ready: true, KF, probes, scene, camera,
    set(p, cam) { override = p == null ? null : { p, cam: cam && { pos: V(cam.pos), tgt: V(cam.tgt), fov: cam.fov } }; kick(); },
    /* advance the film by s seconds without waiting for real time */
    run(s) { const n = Math.ceil(s / 0.033); let t = performance.now(); for (let i = 0; i < n; i++) { tPrev = t; t += 33; frame(t, i === n - 1); } },
  };
}

function supported() {
  try { const c = document.createElement('canvas'); return !!c.getContext('webgl2'); } catch { return false; }
}
if (supported() && !Q.has('nogl')) {
  build().catch((e) => { console.error('[devicehands] 3D stage failed:', e); stage.classList.add('is-fallback'); });
} else {
  stage.classList.add('is-fallback');
}
