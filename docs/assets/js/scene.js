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
  /* near and far matter for more than clipping: the depth-of-field pass reads a packed depth texture,
     and with near = 1 the far half of the scene lost so much depth precision that distant agents were blurred by mistake */
  const camera = new THREE.PerspectiveCamera(30, 1, 24, 2600);

  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(60, 330, -110); key.castShadow = true;
  key.shadow.mapSize.set(LOW ? 2048 : 4096, LOW ? 2048 : 4096);
  Object.assign(key.shadow.camera, { left: -215, right: 215, top: 215, bottom: -215, near: 60, far: 640 });
  key.shadow.bias = -0.0005; key.shadow.normalBias = 0.12;
  scene.add(key, key.target);

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

  /* =====================================================================
   * The story world.
   *   outG  the places people can see: a home desk, a lab in another city, the device on the bench
   *   inG   the world inside the device, where the agents go to look
   * Three agents with bodies: main (largest, at home), local (in the lab PC), onboard (smallest, in the chip).
   * ===================================================================== */
  const outG = new THREE.Group(), inG = new THREE.Group();
  scene.add(outG, inG);
  scene.remove(board); outG.add(board);
  const V = (x, y, z) => new THREE.Vector3(x, y, z);
  const HM = V(430, 50, -560);                 /* home platform, top centre */
  const PC = V(-128, -8, 24);                  /* lab PC, bottom centre */
  const IO = V(0, -4000, 0);                   /* origin of the inside world */
  const CR = IO.clone().add(V(60, 0, 0));      /* where the program counter stopped */
  const TK = CR.clone().add(V(-132, 0, -72));    /* where the toolkit is opened */
  const UP = V(0, 1, 0);
  const mesh = (geo, m, x, y, z, g = outG) => { const o = new THREE.Mesh(geo, m); o.position.set(x, y, z); o.castShadow = o.receiveShadow = g === outG; g.add(o); return o; };
  const rbox = (w, h, d, r, m, x, y, z, g = outG, seg = 3) => mesh(new RoundedBoxGeometry(w, h, d, seg, Math.min(r, w / 2 - 0.01, h / 2 - 0.01, d / 2 - 0.01)), m, x, y, z, g);
  const canvasTex = (w, h, draw, srgb = true) => { const c = document.createElement('canvas'); c.width = w; c.height = h; draw(c.getContext('2d'), w, h); const t = new THREE.CanvasTexture(c); if (srgb) t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t; };
  const MONO = '"IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace';
  const GOLD = '#e3bf7c';

  /* ---------- outside: platforms, lab PC, cables, product line-up, home desk ---------- */
  const platMat = M({ color: 0x1a1b1e, metalness: 1, roughness: 0.46, side: THREE.DoubleSide });
  const alu = M({ color: 0xb9bcc0, metalness: 1, roughness: 0.34 });
  const darkAlu = M({ color: 0x2b2d31, metalness: 1, roughness: 0.4 });
  const glass = new THREE.MeshPhysicalMaterial({ color: 0x050608, roughness: 0.06, metalness: 0.3, clearcoat: 1 });
  const lit = (hex, k) => new THREE.MeshBasicMaterial({ color: new THREE.Color(hex).multiplyScalar(k) });
  function platform(R, c) {
    const pts = [[0.01, 0], [R - 4, 0], [R, -4], [R, -11], [R - 5, -15], [0.01, -15]].map(([x, y]) => new THREE.Vector2(x, y));
    mesh(new THREE.LatheGeometry(pts, 120), platMat, c.x, c.y, c.z);
    mesh(new THREE.TorusGeometry(R - 9, 0.5, 8, 180), mat.gold, c.x, c.y + 0.15, c.z).rotation.x = Math.PI / 2;
  }
  platform(196, V(-20, -8, -34));
  platform(132, HM);
  [[-45, -30], [45, -30], [-45, 30], [45, 30]].forEach(([x, z]) => mesh(new THREE.CylinderGeometry(1.5, 1.5, 6.4, 16), mat.gold, x, -4.8, z));

  /* the lab PC and the two real links to the board: a USB cable and four serial wires */
  rbox(74, 28, 74, 5, darkAlu, PC.x, PC.y + 14, PC.z);
  rbox(46, 1.1, 0.6, 0.2, lit(0xfff1d0, 2.4), PC.x, PC.y + 6, PC.z + 37.2);
  rbox(7.2, 2.3, 6.4, 0.9, mat.steel, -54.6, 1.62, 8);
  rbox(13, 5.2, 8.8, 2, mat.plastic, -64.4, 1.62, 8);
  const tube = (pts, r, m, g = outG) => mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts.map((p) => V(...p))), 64, r, 10), m, 0, 0, 0, g);
  tube([[-70.6, 1.62, 8], [-77, 1.5, 8.3], [-83, 0.6, 11], [-87.5, 0.8, 16], [-91, 1.2, 18]], 1.3, mat.rubber);
  rbox(10.8, 6.2, 2.9, 0.4, mat.plastic, 25.81, 6.3, 30.5);
  [0x1b1b1c, 0xc79a1e, 0x2f6b3c, 0xa5352b].forEach((c, i) => {
    const x = 22 + i * 2.54;
    tube([[x, 9.3, 30.5], [x - 2, 19, 34], [-18, 15, 50 + i * 1.3], [-66, -4.5, 62 + i * 1.6], [-98 + i * 2.2, -1, 66], [PC.x + 14 + i * 2.6, PC.y + 15, PC.z + 37.4]], 0.6, M({ color: c, roughness: 0.6 }));
  });

  /* a line-up of products: the device in trouble can be any of these */
  const DEV = [-112, -56, 0, 56, 112].map((x) => V(x, -8, -130));
  const devLed = [];
  /* the television is the one that dies in this story: a picture while it is fine, a panic screen once it is not */
  const tvOk = canvasTex(640, 360, (c, w, h) => {
    const gr = c.createLinearGradient(0, 0, 0, h); gr.addColorStop(0, '#3b2a14'); gr.addColorStop(0.55, '#a8763a'); gr.addColorStop(1, '#16110b'); c.fillStyle = gr; c.fillRect(0, 0, w, h);
    c.fillStyle = 'rgba(0,0,0,.45)'; c.beginPath(); c.moveTo(0, 260); for (let x = 0; x <= w; x += 16) c.lineTo(x, 250 - Math.sin(x / 70) * 34 - Math.sin(x / 23) * 8); c.lineTo(w, h); c.lineTo(0, h); c.fill();
    c.fillStyle = '#ffe2b0'; c.beginPath(); c.arc(430, 150, 34, 0, 7); c.fill();
  });
  const tvBad = canvasTex(640, 360, (c, w, h) => {
    c.fillStyle = '#120403'; c.fillRect(0, 0, w, h); c.strokeStyle = c.fillStyle = '#ff5a45'; c.lineWidth = 12; c.lineJoin = 'round';
    c.beginPath(); c.moveTo(320, 70); c.lineTo(408, 214); c.lineTo(232, 214); c.closePath(); c.stroke(); c.fillRect(313, 118, 14, 54); c.fillRect(313, 184, 14, 14);
    c.font = `600 34px ${MONO}`; c.textAlign = 'center'; c.fillText('kernel panic', 320, 282); c.font = `500 20px ${MONO}`; c.fillStyle = '#a8483b'; c.fillText('not syncing', 320, 316);
  });
  const tvMat = new THREE.MeshBasicMaterial({ map: tvOk });
  DEV.forEach((d, i) => {
    mesh(new THREE.CylinderGeometry(25, 26, 3, 64), darkAlu, d.x, d.y + 1.5, d.z);
    mesh(new THREE.TorusGeometry(23, 0.35, 8, 96), mat.gold, d.x, d.y + 3.05, d.z).rotation.x = Math.PI / 2;
    const y0 = d.y + 3, led = lit(0x58ff8a, 2.2);
    devLed.push(led);
    if (i === 0) {            /* phone */
      const g = new THREE.Group(); g.position.set(d.x, y0, d.z); g.rotation.x = -0.2; outG.add(g);
      rbox(23, 46, 2.8, 2.6, darkAlu, 0, 23, 0, g);
      mesh(new THREE.PlaneGeometry(21, 44), new THREE.MeshBasicMaterial({ map: canvasTex(256, 512, (c, w, h) => {
        const gr = c.createLinearGradient(0, 0, w, h); gr.addColorStop(0, '#2b2418'); gr.addColorStop(1, '#0c0c0e'); c.fillStyle = gr; c.fillRect(0, 0, w, h);
        c.fillStyle = '#f4f1ea'; c.font = `600 54px ${MONO}`; c.fillText('09:41', 48, 120);
        for (let a = 0; a < 4; a++) for (let b = 0; b < 3; b++) { c.fillStyle = (a + b) % 3 ? 'rgba(255,255,255,.16)' : GOLD; c.beginPath(); c.roundRect(30 + b * 70, 210 + a * 70, 50, 50, 14); c.fill(); }
      }) }), 0, 23, 1.45, g);
      mesh(new THREE.SphereGeometry(0.7, 12, 8), led, 0, 43.4, 1.7, g);
    } else if (i === 1) {     /* smart speaker */
      mesh(new THREE.CylinderGeometry(15, 15.6, 34, 64), M({ color: 0x33363b, roughness: 0.96 }), d.x, y0 + 17, d.z);
      mesh(new THREE.CylinderGeometry(15.1, 15.1, 1.2, 64), glass, d.x, y0 + 34.5, d.z);
      mesh(new THREE.TorusGeometry(11.5, 0.5, 8, 80), led, d.x, y0 + 35.2, d.z).rotation.x = Math.PI / 2;
    } else if (i === 2) {     /* television */
      rbox(62, 36, 1.8, 0.8, darkAlu, d.x, y0 + 25, d.z);
      mesh(new THREE.PlaneGeometry(60, 34), tvMat, d.x, y0 + 25, d.z + 0.95);
      rbox(3, 8, 3, 0.6, darkAlu, d.x, y0 + 4, d.z - 1); rbox(26, 1.2, 12, 0.5, darkAlu, d.x, y0 + 0.6, d.z);
      mesh(new THREE.SphereGeometry(0.75, 12, 8), led, d.x + 28, y0 + 8.6, d.z + 1.1);
    } else if (i === 3) {     /* desktop computer */
      rbox(19, 42, 40, 2.2, alu, d.x, y0 + 21, d.z);
      rbox(1.2, 30, 0.5, 0.2, lit(0xffffff, 0.9), d.x - 5, y0 + 22, d.z + 20.1);
      mesh(new THREE.SphereGeometry(0.8, 12, 8), led, d.x + 5, y0 + 37, d.z + 20.1);
    } else {                  /* rack server */
      for (let k = 0; k < 3; k++) {
        rbox(54, 9.4, 42, 0.8, darkAlu, d.x, y0 + 4.9 + k * 10.2, d.z);
        for (let b = 0; b < 8; b++) rbox(4.6, 6.4, 0.5, 0.3, mat.plastic, d.x - 21 + b * 5.4, y0 + 4.9 + k * 10.2, d.z + 21.1);
        mesh(new THREE.SphereGeometry(0.6, 10, 8), k === 1 ? led : lit(0x58ff8a, 2.2), d.x + 24.5, y0 + 4.9 + k * 10.2, d.z + 21.2);
      }
    }
  });

  /* home: a laptop on a desk, a mug */
  rbox(128, 5, 84, 2.5, alu, HM.x, HM.y + 2.5, HM.z + 6);
  rbox(112, 0.5, 46, 1, M({ color: 0x18191b, roughness: 0.6 }), HM.x, HM.y + 5.05, HM.z - 6);
  rbox(44, 0.4, 24, 1, M({ color: 0xa9acb0, metalness: 1, roughness: 0.28 }), HM.x, HM.y + 5.05, HM.z + 32);
  const lidG = new THREE.Group(); lidG.position.set(HM.x, HM.y + 5, HM.z - 36); lidG.rotation.x = -0.3; outG.add(lidG);
  rbox(128, 84, 3, 2.5, alu, 0, 42, 0, lidG);
  const screenTex = canvasTex(1024, 640, (c, w, h) => {
    c.fillStyle = '#0a0a0b'; c.fillRect(0, 0, w, h);
    c.fillStyle = '#1b1b1d'; c.fillRect(0, 0, w, 54);
    c.fillStyle = GOLD; [0, 1, 2, 3, 4].forEach((i) => c.fillRect(28 + i * 9, 40 - [13, 22, 26, 21, 11][i], 5, [13, 22, 26, 21, 11][i]));
    c.font = `600 24px ${MONO}`; c.fillStyle = '#f4f1ea'; c.fillText('DeviceHands', 86, 36);
    const R = rng(4);
    for (let i = 0; i < 9; i++) { const y = 96 + i * 56, me = i % 3 === 0; c.fillStyle = me ? '#2a2a2d' : 'rgba(227,191,124,.16)'; c.beginPath(); c.roundRect(me ? 520 - R() * 120 : 40, y, 320 + R() * 220, 36, 12); c.fill(); }
    c.strokeStyle = GOLD; c.lineWidth = 4; c.beginPath(); for (let i = 0; i <= 40; i++) { const x = 700 + i * 7, y = 560 - Math.pow(i / 40, 2.4) * 150 - Math.sin(i) * 4; i ? c.lineTo(x, y) : c.moveTo(x, y); } c.stroke();
  });
  /* what the laptop shows at the end: the report */
  const reportTex = canvasTex(1024, 640, (c, w, h) => {
    c.fillStyle = '#0c0c0d'; c.fillRect(0, 0, w, h); c.fillStyle = '#1b1b1d'; c.fillRect(0, 0, w, 54);
    c.fillStyle = GOLD; [0, 1, 2, 3, 4].forEach((i) => c.fillRect(28 + i * 9, 40 - [13, 22, 26, 21, 11][i], 5, [13, 22, 26, 21, 11][i]));
    c.font = `600 24px ${MONO}`; c.fillStyle = '#f4f1ea'; c.fillText('DeviceHands  /  report', 86, 36);
    c.font = `600 40px ${MONO}`; c.fillText('Storage driver used a cleared pointer', 44, 124);
    c.fillStyle = GOLD; c.fillRect(44, 150, 120, 5);
    const R = rng(8);
    ['UART', 'logcat', 'kernel'].forEach((n, r) => { const y = 214 + r * 62; c.font = `500 20px ${MONO}`; c.fillStyle = '#8b877f'; c.fillText(n, 44, y + 7); for (let x = 150; x < 620;) { const ww = 5 + R() * 20, hot = x > 500; c.fillStyle = hot ? (r === 2 ? '#ff6a4d' : GOLD) : 'rgba(255,255,255,.3)'; c.fillRect(x, y - 13, ww, 28); x += ww + (hot ? 5 : 9 + R() * 26); } });
    c.fillStyle = '#ff6a4d'; c.fillRect(622, 180, 4, 200);
    ['panic()', 'storage_wait()', 'blk_request()', 'worker_thread()', 'kthread()'].forEach((n, i) => { c.fillStyle = i ? '#1c1a15' : '#3a0d0a'; c.fillRect(690, 176 + i * 44, 290, 36); c.fillStyle = i ? GOLD : '#ff8a78'; c.font = `500 20px ${MONO}`; c.fillText('#' + i + '  ' + n, 704, 201 + i * 44); });
    c.fillStyle = 'rgba(227,191,124,.14)'; c.beginPath(); c.roundRect(44, 440, 936, 150, 18); c.fill();
    c.fillStyle = '#f4f1ea'; c.font = `500 24px ${MONO}`; c.fillText('cause   pointer cleared 3 s before the crash', 70, 492); c.fillText('next    patch ready, flash and run 20 boots?', 70, 546);
  });
  const screenMat = new THREE.MeshBasicMaterial({ map: screenTex, color: new THREE.Color(1.5, 1.5, 1.5) });
  mesh(new THREE.PlaneGeometry(120, 75), screenMat, 0, 42.5, 1.6, lidG);
  mesh(new THREE.CylinderGeometry(9, 8, 19, 40), M({ color: 0xece8df, roughness: 0.35 }), HM.x - 92, HM.y + 9.5, HM.z + 34);
  mesh(new THREE.TorusGeometry(6, 1.4, 10, 30, Math.PI), M({ color: 0xece8df, roughness: 0.35 }), HM.x - 101, HM.y + 10, HM.z + 34).rotation.z = Math.PI / 2;

  /* ---------- light paths: board traces, the long link home <-> lab ---------- */
  const flowMat = (hex) => new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    uniforms: { uHead: { value: -1 }, uFill: { value: 0 }, uBase: { value: 0 }, uGain: { value: 7 }, uColor: { value: new THREE.Color(hex) } },
    vertexShader: 'attribute float along; varying float vA; varying float vV; void main(){ vA=along; vV=uv.y; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader: `uniform float uHead,uFill,uBase,uGain; uniform vec3 uColor; varying float vA; varying float vV;
      void main(){ float d=vA-uHead; float head=exp(-d*d/0.0009); float tail=smoothstep(-0.4,0.0,d)*step(d,0.0);
        float lat=smoothstep(0.0,0.45,1.0-abs(vV*2.0-1.0)); float fill=step(vA,uFill)*uBase;
        gl_FragColor=vec4(uColor*(head*uGain+tail*1.1+fill)*lat,1.0); }`,
  });
  /* a ribbon lying on a surface along a polyline */
  function ribbon(pts3, width, hex, g) {
    const pos = [], uv = [], along = [], idx = []; let total = 0; const cum = [0];
    for (let i = 1; i < pts3.length; i++) { total += pts3[i].distanceTo(pts3[i - 1]); cum.push(total); }
    pts3.forEach((p, i) => {
      const a = pts3[Math.max(0, i - 1)], b = pts3[Math.min(pts3.length - 1, i + 1)];
      const side = V(b.x - a.x, 0, b.z - a.z).normalize().cross(UP).multiplyScalar(width / 2);
      pos.push(p.x - side.x, p.y, p.z - side.z, p.x + side.x, p.y, p.z + side.z);
      uv.push(0, 0, 0, 1); along.push(cum[i] / total, cum[i] / total);
      if (i) { const q = i * 2; idx.push(q - 2, q - 1, q, q, q - 1, q + 1); }
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geo.setAttribute('along', new THREE.Float32BufferAttribute(along, 1));
    geo.setIndex(idx);
    const m = flowMat(hex), o = new THREE.Mesh(geo, m); g.add(o);
    return { m, total, head: -1 };
  }
  const sample = (curve, n) => Array.from({ length: n + 1 }, (_, i) => curve.getPoint(i / n));
  const routes = ROUTES.filter((r) => r.lanes).map((r) => {
    const set = [];
    for (let k = 0; k < r.lanes; k++) set.push(ribbon(offsetPolyline(r.pts, (k - (r.lanes - 1) / 2) * 0.34).map(([x, z]) => V(x, 0.035, z)), 0.42, 0xffc878, outG));
    return set;
  });
  const linkCurve = new THREE.QuadraticBezierCurve3(V(HM.x, HM.y + 92, HM.z - 44), V((HM.x + PC.x) / 2, 420, (HM.z + PC.z) / 2), V(PC.x, PC.y + 30, PC.z));
  const link = ribbon(sample(linkCurve, 160), 2.2, 0xffc878, outG);
  link.m.uniforms.uBase.value = 0.5; link.m.uniforms.uGain.value = 5;
  const link2 = ribbon(sample(linkCurve, 160).map((p) => p.clone().add(V(0, 0, 0.01))), 2.2, 0xffc878, outG);
  link2.m.uniforms.uGain.value = 5;
  /* the SoC answers with a soft ring when data lands */
  const halo = new THREE.Mesh(new THREE.RingGeometry(7.9, 8.15, 4, 1, Math.PI / 4),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(0xffcf8f).multiplyScalar(2), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
  halo.rotation.x = -Math.PI / 2; halo.position.set(SOC.x, 1.06, SOC.z); halo.scale.setScalar(1.3); outG.add(halo);
  let haloT = 9;

  /* ---------- the agents ---------- */
  const glowTex = canvasTex(128, 128, (c) => { const g = c.createRadialGradient(64, 64, 0, 64, 64, 64); g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.35, 'rgba(255,255,255,.35)'); g.addColorStop(1, 'rgba(255,255,255,0)'); c.fillStyle = g; c.fillRect(0, 0, 128, 128); });
  const ceramic = new THREE.MeshPhysicalMaterial({ color: 0xf1efe9, roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.15 });
  const visorMat = new THREE.MeshPhysicalMaterial({ color: 0x040405, roughness: 0.1, clearcoat: 1 });
  function makeAgent(r, eyeHex, opt = {}) {
    const g = new THREE.Group(), eyeM = lit(eyeHex, 6.5);
    const P = (geo, m, x, y, z, parent = g) => { const o = new THREE.Mesh(geo, m); o.position.set(x, y, z); o.castShadow = true; parent.add(o); return o; };
    P(new THREE.SphereGeometry(r, 48, 32), ceramic, 0, 0, 0);
    P(new THREE.SphereGeometry(r * 1.015, 48, 16, Math.PI / 2 - 1.05, 2.1, Math.PI / 2 - 0.5, 0.74), visorMat, 0, 0, 0);
    const eyes = [-1, 1].map((s) => {
      const ph = Math.PI / 2 + s * 0.4, th = Math.PI / 2 - 0.1, R = r * 1.03;
      const e = P(new THREE.SphereGeometry(r * 0.12, 16, 12), eyeM, -R * Math.cos(ph) * Math.sin(th), R * Math.cos(th), R * Math.sin(ph) * Math.sin(th));
      e.scale.set(1, 1.5, 0.4); return e;
    });
    P(new THREE.CylinderGeometry(r * 0.3, r * 0.36, r * 0.34, 20), visorMat, 0, -r * 1.04, 0);
    P(new THREE.SphereGeometry(r * 0.84, 40, 28), ceramic, 0, -r * 1.95, 0).scale.set(1, 1.12, 0.86);
    P(new THREE.SphereGeometry(r * 0.13, 16, 12), eyeM, 0, -r * 1.66, r * 0.68).scale.set(1, 1, 0.35);
    const hands = [-1, 1].map((s) => {
      const h = new THREE.Group(); h.position.set(s * r * 1.3, -r * 1.8, r * 0.55); h.userData.base = h.position.clone();
      const palm = new THREE.Mesh(new RoundedBoxGeometry(r * 0.44, r * 0.52, r * 0.34, 3, r * 0.14), mat.gold); palm.castShadow = true; h.add(palm);
      const th = new THREE.Mesh(new THREE.SphereGeometry(r * 0.13, 12, 10), mat.gold); th.position.set(-s * r * 0.25, r * 0.1, r * 0.06); h.add(th);
      g.add(h); return h;
    });
    if (opt.halo) P(new THREE.TorusGeometry(r * 0.8, r * 0.035, 8, 72), mat.gold, 0, r * 1.36, 0).rotation.x = Math.PI / 2;
    if (opt.antenna) { P(new THREE.CylinderGeometry(r * 0.035, r * 0.035, r * 0.5, 6), mat.gold, 0, r * 1.2, 0); P(new THREE.SphereGeometry(r * 0.11, 10, 8), eyeM, 0, r * 1.5, 0); }
    const jet = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: eyeHex, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false }));
    jet.scale.setScalar(r * 2.6); jet.position.set(0, -r * 3.05, 0); g.add(jet);
    scene.add(g);
    return { g, eyes, hands, r, blink: 1 + Math.random() * 3, world: 'out', talk: 0, ph: Math.random() * 6 };
  }
  const R_MAIN = 18, R_LOCAL = 12, R_OB = 7;
  const AG = { main: makeAgent(R_MAIN, 0xffc66e, { halo: true }), local: makeAgent(R_LOCAL, 0x7fd0ff), onboard: makeAgent(R_OB, 0x7dffb0, { antenna: true }) };
  const A_MAIN = HM.clone().add(V(90, 66, 36)), A_LOCAL = V(PC.x, 62, PC.z), A_OB = V(SOC.x, 27, SOC.z), LID = V(SOC.x, 4, SOC.z);
  const SKY = CR.clone().add(V(-70, 300, 90));
  const C = (x, y, z) => CR.clone().add(V(x, y, z));
  /* where each agent is at each point of the story: [progress, world, position, scale, arc lift] */
  const TRACK = {
    onboard: [[0, 'out', A_OB, 1], [4.3, 'out', A_OB, 1], [4.48, 'out', LID, 0.05], [4.52, 'in', C(18, 90, 26), 0.05], [5, 'in', C(18, 25, 26), 1], [7.4, 'in', C(18, 25, 26), 1], [7.5, 'in', C(18, 120, 26), 0.05], [7.52, 'out', LID, 0.05], [8, 'out', A_OB, 1]],
    local: [[0, 'out', A_LOCAL, 1], [4.05, 'out', A_LOCAL, 1], [4.3, 'out', V(-58, 30, 12), 1, 14], [4.48, 'out', LID, 0.05], [4.52, 'in', SKY, 0.05], [5, 'in', TK.clone().add(V(54, 52, 40)), 1], [5.55, 'in', TK.clone().add(V(54, 52, 40)), 1], [6, 'in', C(-34, 40, -30), 1, 10], [7.4, 'in', C(-34, 40, -30), 1], [7.5, 'in', SKY, 0.05], [7.52, 'out', LID, 0.05], [8, 'out', A_LOCAL, 1, 30]],
    main: [[0, 'out', A_MAIN, 1], [6.1, 'out', A_MAIN, 1], [6.48, 'out', LID, 0.05, 260], [6.52, 'in', SKY, 0.05], [7, 'in', C(-6, 80, 34), 1], [7.4, 'in', C(-6, 80, 34), 1], [7.5, 'in', SKY, 0.05], [7.52, 'out', V((HM.x + PC.x) / 2, 300, (HM.z + PC.z) / 2), 0.05], [8, 'out', A_MAIN, 1]],
  };
  function place(a, keys, p) {
    let i = 0; while (i < keys.length - 2 && p >= keys[i + 1][0]) i++;
    const k0 = keys[i], k1 = keys[i + 1];
    if (k0[1] !== k1[1]) { const k = p < k1[0] ? k0 : k1; a.g.position.copy(k[2]); a.s = k[3]; a.world = k[1]; return; }
    const f = smooth(k0[0], k1[0], p);
    a.g.position.lerpVectors(k0[2], k1[2], f); a.g.position.y += Math.sin(f * Math.PI) * (k1[4] || 0);
    a.s = lerp(k0[3], k1[3], f); a.world = k0[1];
  }

  /* ---------- inside the device ---------- */
  const cellsTex = canvasTex(256, 256, (c) => {
    c.fillStyle = '#000'; c.fillRect(0, 0, 256, 256); const R = rng(31);
    for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) { const v = R(); if (v < 0.5) continue; const b = v > 0.93 ? 1 : v > 0.78 ? 0.42 : 0.14; c.fillStyle = `rgba(227,191,124,${b})`; c.fillRect(x * 16 + 4, y * 16 + 5, 8, 6); }
  });
  cellsTex.wrapS = cellsTex.wrapT = THREE.RepeatWrapping;
  {
    const R = rng(77), mats = [], m4 = new THREE.Matrix4(), q = new THREE.Quaternion();
    for (let i = -27; i <= 27; i++) for (let j = -27; j <= 27; j++) {
      const x = 60 + i * 15, z = j * 15, dx = x - 60;
      if (Math.abs(z) < 17) continue;                                /* the avenue where code runs */
      if (dx * dx + z * z < 74 * 74) continue;                       /* the plaza round the crash */
      if (Math.hypot(dx + 132, z + 72) < 110) continue;                /* room for the toolkit */
      if (dx > 95 && z > 34 && dx < 300 && z < 230) continue;        /* the empty lot the bad pointer aims at */
      if (R() < 0.07) continue;
      const far = smooth(300, 440, Math.hypot(dx, z)), h = 4 + Math.pow(R(), 2.2) * (15 + far * 150);
      m4.compose(V(IO.x + x, IO.y, IO.z + z), q, V(11 + R() * 2, h, 11 + R() * 2)); mats.push(m4.clone());
    }
    const geo = new THREE.BoxGeometry(1, 1, 1); geo.translate(0, 0.5, 0);
    const im = new THREE.InstancedMesh(geo, M({ color: 0x1c1d21, metalness: 0.75, roughness: 0.42, emissive: 0xe3bf7c, emissiveMap: cellsTex, emissiveIntensity: 1.5 }), mats.length);
    mats.forEach((mx, i) => im.setMatrixAt(i, mx)); inG.add(im);
    const gridTex = canvasTex(128, 128, (c) => { c.fillStyle = '#000'; c.fillRect(0, 0, 128, 128); c.strokeStyle = 'rgba(227,191,124,.5)'; c.lineWidth = 1.5; c.strokeRect(0, 0, 128, 128); });
    gridTex.wrapS = gridTex.wrapT = THREE.RepeatWrapping; gridTex.repeat.set(140, 140);
    const ground = mesh(new THREE.PlaneGeometry(2100, 2100), M({ color: 0x08080a, metalness: 0.6, roughness: 0.5, emissive: 0xe3bf7c, emissiveMap: gridTex, emissiveIntensity: 0.22 }), IO.x + 60, IO.y, IO.z, inG);
    ground.rotation.x = -Math.PI / 2;
  }
  /* instructions as tiles; the program counter walks them and stops on the red one */
  const TILE = (k) => CR.x - (36 - k) * 13;
  {
    const im = new THREE.InstancedMesh(new THREE.BoxGeometry(11, 1, 21), new THREE.MeshBasicMaterial(), 46), m4 = new THREE.Matrix4(), col = new THREE.Color();
    for (let k = 0; k < 46; k++) { m4.makeTranslation(TILE(k), IO.y + 0.6, IO.z); im.setMatrixAt(k, m4); im.setColorAt(k, col.setScalar(k < 36 ? 0.30 : 0.05).multiply(new THREE.Color(k < 36 ? 0xe3bf7c : 0xffffff))); }
    if (im.instanceColor) im.instanceColor.needsUpdate = true; inG.add(im);
  }
  const crashTile = mesh(new THREE.BoxGeometry(11.4, 1.8, 21.4), lit(0xff3b2f, 6), CR.x, CR.y + 1.1, CR.z, inG); crashTile.rotation.z = 0.1;
  const crashRing = mesh(new THREE.RingGeometry(15, 15.8, 64), new THREE.MeshBasicMaterial({ color: new THREE.Color(0xff5a45).multiplyScalar(2.2), transparent: true, opacity: 0.6, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }), CR.x, CR.y + 0.9, CR.z, inG);
  crashRing.rotation.x = -Math.PI / 2;
  const pcMark = mesh(new THREE.ConeGeometry(4.2, 9, 4), lit(0xffd48a, 3), CR.x, CR.y + 15, CR.z, inG); pcMark.rotation.x = Math.PI;
  /* the call stack as a tower of frames */
  const STACK = ['kthread', 'worker_thread', 'blk_request', 'storage_wait', 'panic'];
  const SP = C(38, 0, -38);
  const slabs = STACK.map((name, n) => {
    const front = new THREE.MeshBasicMaterial({ map: canvasTex(512, 128, (c, w, h) => { c.fillStyle = n === 4 ? '#3a0d0a' : '#141310'; c.fillRect(0, 0, w, h); c.strokeStyle = n === 4 ? '#ff5a45' : GOLD; c.lineWidth = 6; c.strokeRect(3, 3, w - 6, h - 6); c.fillStyle = n === 4 ? '#ff8a78' : GOLD; c.font = `500 30px ${MONO}`; c.fillText('#' + (4 - n), 26, 76); c.fillStyle = '#f4f1ea'; c.font = `600 44px ${MONO}`; c.fillText(name + '()', 96, 80); }) });
    const side = M({ color: n === 4 ? 0x3a0d0a : 0x1a1814, metalness: 0.5, roughness: 0.35, emissive: n === 4 ? 0xff3b2f : 0xe3bf7c, emissiveIntensity: 0.12 });
    const s = new THREE.Mesh(new THREE.BoxGeometry(46, 8.4, 24), [side, side, side, side, front, side]);
    s.position.set(SP.x, SP.y + 5 + n * 10, SP.z); inG.add(s); return s;
  });
  /* the pointer that points at nothing */
  const VOID = C(196, 2.5, 128);
  const badPtr = ribbon(sample(new THREE.QuadraticBezierCurve3(V(SP.x + 23, SP.y + 35, SP.z + 4), C(120, 96, 40), VOID), 60), 1.6, 0xff5a45, inG);
  badPtr.m.uniforms.uBase.value = 2.2;
  const voidRing = mesh(new THREE.RingGeometry(9, 10, 48), new THREE.MeshBasicMaterial({ color: new THREE.Color(0xff5a45).multiplyScalar(2), transparent: true, opacity: 0, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }), VOID.x, VOID.y, VOID.z, inG);
  voidRing.rotation.x = -Math.PI / 2;
  /* the chain the main agent finds: an earlier instruction cleared that pointer */
  const SRC = V(TILE(11), IO.y + 2, IO.z);
  const chain = ribbon(sample(new THREE.QuadraticBezierCurve3(SRC, V((SRC.x + SP.x) / 2, IO.y + 150, IO.z - 60), V(SP.x - 23, SP.y + 35, SP.z + 4)), 90), 2.2, 0xffd48a, inG);
  chain.m.uniforms.uBase.value = 2.6;
  const srcTile = mesh(new THREE.BoxGeometry(11.6, 2, 21.6), lit(0xffd48a, 0.2), SRC.x, IO.y + 1.2, IO.z, inG);
  /* the shaft of light the agents arrive through */
  const shaft = mesh(new THREE.CylinderGeometry(7, 20, 620, 40, 1, true), new THREE.MeshBasicMaterial({ color: 0xffd9a0, transparent: true, opacity: 0.03, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }), SKY.x, IO.y + 310, SKY.z, inG);

  /* the DeviceHands toolkit: a case that opens, tools that rise out of it */
  const caseG = new THREE.Group(); caseG.position.copy(TK); caseG.rotation.y = 0.5; inG.add(caseG);
  const caseMat = M({ color: 0x141517, metalness: 1, roughness: 0.38 });
  rbox(50, 12, 32, 2.5, caseMat, 0, 6, 0, caseG);
  rbox(51, 1, 33, 0.4, mat.gold, 0, 12.2, 0, caseG);
  rbox(8, 3, 1, 0.4, mat.gold, 0, 9.5, 16.3, caseG);
  mesh(new THREE.PlaneGeometry(44, 26), lit(0xffd48a, 1.8), 0, 12.9, 0, caseG).rotation.x = -Math.PI / 2;
  const caseLid = new THREE.Group(); caseLid.position.set(0, 12.7, -16); caseG.add(caseLid);
  const lidTop = canvasTex(512, 320, (c, w, h) => { c.fillStyle = '#17181a'; c.fillRect(0, 0, w, h); c.fillStyle = GOLD; [0, 1, 2, 3, 4].forEach((i) => { const hh = [58, 96, 112, 92, 50][i]; c.beginPath(); c.roundRect(84 + i * 34, 170 - hh, 18, hh, 9); c.fill(); }); c.font = `600 44px ${MONO}`; c.fillText('DeviceHands', 84, 236); c.font = `500 22px ${MONO}`; c.fillStyle = '#8b877f'; c.fillText('TOOLKIT', 86, 274); });
  const lidMesh = new THREE.Mesh(new RoundedBoxGeometry(50, 3.4, 32, 3, 1.4), [caseMat, caseMat, new THREE.MeshStandardMaterial({ map: lidTop, metalness: 0.9, roughness: 0.38 }), caseMat, caseMat, caseMat]);
  lidMesh.position.set(0, 1.7, 16); caseLid.add(lidMesh);
  const TOOLS = [['UART LOG', 0, 'log'], ['CRASH LOG', 0, 'warn'], ['TIMELINE', 0, 'lanes'], ['LOG SEARCH', 0, 'find'], ['PC / STACK', 1, 'stack'], ['MEMORY', 1, 'grid'], ['TRACE', 1, 'wave']];
  const tools = TOOLS.map(([name, planned, icon], i) => {
    const tex = canvasTex(256, 256, (c) => {
      c.fillStyle = '#0d0d0e'; c.fillRect(0, 0, 256, 256);
      c.strokeStyle = planned ? 'rgba(227,191,124,.6)' : GOLD; c.lineWidth = 6; if (planned) c.setLineDash([14, 10]); c.beginPath(); c.roundRect(8, 8, 240, 240, 26); c.stroke(); c.setLineDash([]);
      c.strokeStyle = c.fillStyle = planned ? '#bfa878' : GOLD; c.lineWidth = 7; c.lineCap = 'round'; c.lineJoin = 'round';
      const L = (...p) => { c.beginPath(); for (let k = 0; k < p.length; k += 2) (k ? c.lineTo(p[k], p[k + 1]) : c.moveTo(p[k], p[k + 1])); c.stroke(); };
      if (icon === 'log') { L(64, 62, 150, 62); L(64, 92, 192, 92); L(64, 122, 128, 122); c.fillRect(140, 112, 14, 20); }
      if (icon === 'warn') { L(128, 46, 192, 146, 64, 146, 128, 46); L(128, 84, 128, 112); c.beginPath(); c.arc(128, 128, 4.5, 0, 7); c.fill(); }
      if (icon === 'lanes') { [62, 96, 130].forEach((y, r) => { L(56, y, 200, y); [0, 1, 2, 3].forEach((k) => c.fillRect(66 + k * 32 + r * 9, y - 10, 12, 20)); }); }
      if (icon === 'find') { c.beginPath(); c.arc(114, 88, 36, 0, 7); c.stroke(); L(142, 116, 184, 150); }
      if (icon === 'stack') { [0, 1, 2].forEach((k) => c.strokeRect(70, 52 + k * 34, 96, 24)); L(190, 58, 190, 140); L(178, 126, 190, 140, 202, 126); }
      if (icon === 'grid') { for (let a = 0; a < 4; a++) for (let b = 0; b < 3; b++) { c.globalAlpha = (a * 3 + b) % 4 === 0 ? 1 : 0.4; c.fillRect(60 + a * 36, 50 + b * 34, 26, 24); } c.globalAlpha = 1; }
      if (icon === 'wave') { c.beginPath(); for (let x = 0; x <= 150; x += 3) { const y = 98 + Math.sin(x / 13) * 30 * Math.exp(-Math.pow((x - 80) / 46, 2)); x ? c.lineTo(54 + x, y) : c.moveTo(54 + x, y); } c.stroke(); }
      c.font = `600 25px ${MONO}`; c.textAlign = 'center'; c.fillStyle = '#f4f1ea'; c.fillText(name, 128, 198);
      if (planned) { c.font = `500 17px ${MONO}`; c.fillStyle = '#8b877f'; c.fillText('PLANNED', 128, 226); }
    });
    const o = mesh(new THREE.PlaneGeometry(30, 30), new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide }), TK.x, TK.y + 14, TK.z, inG);
    const a = lerp(-1.25, 1.25, i / 6);
    o.userData.to = TK.clone().add(V(Math.sin(a) * 104, 70 + Math.cos(a * 1.25) * 20 + (i % 2) * 9, 6 - Math.cos(a) * 16));
    return o;
  });
  /* what the tools draw: the logs, lined up on one timeline */
  const holoTex = canvasTex(1024, 480, (c, w, h) => {
    c.fillStyle = 'rgba(8,8,9,.92)'; c.fillRect(0, 0, w, h); c.strokeStyle = GOLD; c.lineWidth = 4; c.strokeRect(2, 2, w - 4, h - 4);
    c.font = `600 30px ${MONO}`; c.fillStyle = '#f4f1ea'; c.fillText('timeline', 36, 56); c.fillStyle = GOLD; c.fillText('-3.0 s', 610, 56);
    c.fillStyle = 'rgba(227,191,124,.12)'; c.fillRect(600, 76, 170, 360); c.setLineDash([10, 8]); c.strokeStyle = 'rgba(227,191,124,.7)'; c.lineWidth = 2; c.strokeRect(600, 76, 170, 360); c.setLineDash([]);
    const R = rng(12); c.font = `500 24px ${MONO}`;
    ['UART', 'logcat', 'kernel', 'events'].forEach((n, r) => {
      const y = 126 + r * 86; c.fillStyle = '#8b877f'; c.fillText(n, 36, y + 8); c.fillStyle = 'rgba(255,255,255,.1)'; c.fillRect(150, y, 840, 2);
      for (let x = 156; x < 760;) { const ww = 5 + R() * (r === 3 ? 6 : 26), hot = x > 600; c.fillStyle = hot ? (r === 2 && R() < 0.5 ? '#ff6a4d' : GOLD) : 'rgba(255,255,255,.34)'; c.fillRect(x, y - 16, ww, 34); x += ww + (hot ? 4 + R() * 8 : 8 + R() * (r === 3 ? 90 : 34)); }
    });
    c.fillStyle = '#ff6a4d'; c.fillRect(770, 76, 4, 372); c.font = `600 26px ${MONO}`; c.fillText('panic', 786, 440);
  });
  const holo = mesh(new THREE.PlaneGeometry(150, 70), new THREE.MeshBasicMaterial({ map: holoTex, transparent: true, side: THREE.DoubleSide }), CR.x - 6, CR.y + 74, CR.z - 78, inG);
  holo.rotation.y = -0.12;
  /* log lines streaming out of the city into the picture */
  const SN = LOW ? 48 : 110, spos = new Float32Array(SN * 3), sr = rng(41);
  const streams = Array.from({ length: SN }, () => ({ a: C((sr() - 0.5) * 420, 4 + sr() * 30, (sr() - 0.5) * 300), m: C((sr() - 0.5) * 120, 110 + sr() * 50, -20), o: sr(), s: 0.16 + sr() * 0.16 }));
  const streamPts = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(spos, 3)),
    new THREE.PointsMaterial({ color: new THREE.Color(0xffd48a).multiplyScalar(2), size: 2.2, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true }));
  streamPts.frustumCulled = false; inG.add(streamPts);

  /* dust in the light, outside */
  const DN = LOW ? 80 : 220, dpos = new Float32Array(DN * 3), dr = rng(99);
  for (let i = 0; i < DN; i++) { dpos[i * 3] = (dr() - 0.3) * 900; dpos[i * 3 + 1] = dr() * 300; dpos[i * 3 + 2] = (dr() - 0.75) * 900; }
  const dust = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(dpos, 3)),
    new THREE.PointsMaterial({ color: 0xfff2dc, size: 1.3, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true }));
  outG.add(dust);
  scene.fog = new THREE.FogExp2(0x000000, 0);

  /* ---------- post ---------- */
  const rt = new THREE.WebGLRenderTarget(4, 4, { type: THREE.HalfFloatType, samples: LOW ? 2 : 4 });
  const composer = new EffectComposer(renderer, rt);
  composer.addPass(new RenderPass(scene, camera));
  const bokeh = LOW ? null : new BokehPass(scene, camera, { focus: 100, aperture: 0.0001, maxblur: 0.0042 });
  if (bokeh) {
    /* three's bokeh shader has one focus distance and nothing else; give it a band that stays sharp */
    const mb = bokeh.materialBokeh, src = mb.fragmentShader;
    mb.uniforms.band = { value: 0 };
    mb.fragmentShader = src.replace('uniform float focus;', 'uniform float focus;\n\t\tuniform float band;')
      .replace('float factor = ( focus + viewZ );', 'float factor = ( focus + viewZ ); factor = sign( factor ) * max( abs( factor ) - band, 0.0 );');
    if (mb.fragmentShader === src || !mb.fragmentShader.includes('- band')) console.error('[devicehands] bokeh shader patch did not apply');
    mb.needsUpdate = true;
    composer.addPass(bokeh);
  }
  const bloom = new UnrealBloomPass(new THREE.Vector2(4, 4), 0.3, 0.5, 5.0);
  composer.addPass(bloom);
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.0;
  composer.addPass(new OutputPass());
  const finish = new ShaderPass({
    uniforms: { tDiffuse: { value: null }, uT: { value: 0 }, uVig: { value: 0.55 }, uFlash: { value: 0 } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader: `uniform sampler2D tDiffuse; uniform float uT,uVig,uFlash; varying vec2 vUv;
      float h(vec2 p){ return fract(sin(dot(p,vec2(12.9898,78.233))+uT)*43758.5453); }
      void main(){ vec4 c=texture2D(tDiffuse,vUv); vec2 q=vUv-0.5; float v=smoothstep(0.85,0.2,length(q*vec2(1.0,1.25)));
        c.rgb*=mix(1.0-uVig,1.0,v); c.rgb+=(h(vUv*vec2(1920.0,1080.0))-0.5)*0.022;
        c.rgb=mix(c.rgb,vec3(1.0,0.95,0.84),uFlash); gl_FragColor=c; }`,
  });
  composer.addPass(finish);

  /* ---------- camera: one keyframe per chapter ---------- */
  const NCH = 8;
  const wide = innerWidth / innerHeight > 1.05;
  const K = (world, pos, tgt, o = {}) => ({ world, pos, tgt, fov: 30, ox: wide ? 0.2 : 0, oy: wide ? 0.02 : -0.15, ap: 0.0001, ...o });
  const KF = [
    K('out', V(-345, 258, 610), V(110, 20, -200), { fov: 32, ox: wide ? 0.15 : 0, oy: wide ? 0.02 : 0.17, ap: 0.00002 }),
    K('out', V(-96, 104, 236), V(0, 14, -52), { ap: 0.00012 }),
    K('out', V(-268, 122, 240), V(-96, 46, 20), { ap: 0.00008 }),
    K('out', HM.clone().add(V(-170, 132, 372)), HM.clone().add(V(46, 48, 6)), { ox: wide ? 0.23 : 0, ap: 0.00006, lift: 120 }),
    K('out', V(-360, 300, 600), V(110, 60, -230), { fov: 32, ox: wide ? 0.12 : 0, ap: 0.00003, lift: 60, exit: { pos: V(SOC.x + 20, 44, SOC.z + 40), tgt: V(SOC.x, 1, SOC.z) } }),
    K('in', TK.clone().add(V(-30, 96, 285)), TK.clone().add(V(14, 54, -4)), { ox: wide ? 0.08 : 0, ap: 0.00003, enter: { pos: C(-300, 430, 400), tgt: TK.clone() } }),
    K('in', C(-110, 92, 214), C(20, 40, -30), { ap: 0.000035 }),
    K('in', C(-196, 152, 256), C(-14, 44, -6), { fov: 32, ox: wide ? 0.15 : 0, ap: 0.00002, exit: { pos: C(-240, 520, 380), tgt: C(-20, 0, 20) } }),
    K('out', HM.clone().add(V(-170, 132, 372)), HM.clone().add(V(46, 48, 6)), { ox: wide ? 0.23 : 0, ap: 0.00006, enter: { pos: HM.clone().add(V(-360, 330, 560)), tgt: HM.clone() } }),
  ];
  if (!wide) KF.forEach((k) => { k.pos = k.tgt.clone().add(k.pos.clone().sub(k.tgt).multiplyScalar(1.5)); k.fov = 36; });
  const cur = { pos: V(0, 0, 0), tgt: V(0, 0, 0), fov: 30, ox: 0, oy: 0, ap: 0 };
  let override = null;

  /* things in the page that follow things in the scene: name tags, speech bubbles, labels */
  const ANCHOR = {
    main: () => [AG.main.g.position, AG.main.world, R_MAIN], local: () => [AG.local.g.position, AG.local.world, R_LOCAL], onboard: () => [AG.onboard.g.position, AG.onboard.world, R_OB],
    pc: () => [pcMark.position, 'in', 0], void: () => [VOID, 'in', 0], src: () => [SRC, 'in', 0], stack: () => [slabs[4].position, 'in', 0], kit: () => [TK, 'in', 0],
    home: () => [HM, 'out', 0], lab: () => [PC, 'out', 0], link: () => [linkCurve.getPoint(0.5), 'out', 0],
  };
  DEV.forEach((d, i) => { ANCHOR['dev' + i] = () => [d, 'out', 0]; });
  const pins = [...document.querySelectorAll('[data-anchor]')].map((el) => ({ el, key: el.dataset.anchor, a: +el.dataset.a, b: +el.dataset.b, dy: +(el.dataset.dy || 0), who: el.classList.contains('bub') ? el.dataset.anchor : null }));

  let W = 0, H = 0;
  function resize() {
    const w = stage.clientWidth, h = Math.min(stage.clientHeight, innerHeight);
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

  let tPrev = performance.now(), T0 = 0, beat = 0, turn = 0, focusNow = -1, bandNow = 0, focusBias = 0;
  const tmp = V(0, 0, 0), tmp2 = V(0, 0, 0);
  const fireRoute = (i) => { routes[i].forEach((r) => { r.head = 0; }); };

  function frame(now, draw = true) {
    const dt = Math.min(0.05, (now - tPrev) / 1000); tPrev = now;
    if (!REDUCED || override) T0 += dt;
    resize();
    const p = override ? override.p : clamp(window.__dhP || 0, 0, NCH);

    /* camera */
    const i0 = Math.min(NCH - 1, Math.floor(p)), fr = p - i0, a = KF[i0], b = KF[i0 + 1];
    let world = a.world, flash = 0;
    if (a.world === b.world) {
      const f = smooth(0.2, 0.8, fr);
      cur.pos.lerpVectors(a.pos, b.pos, f); cur.tgt.lerpVectors(a.tgt, b.tgt, f); cur.pos.y += Math.sin(f * Math.PI) * (b.lift || 0);
    } else if (fr < 0.5) {
      const f = smooth(0.16, 0.5, fr); cur.pos.lerpVectors(a.pos, a.exit.pos, f); cur.tgt.lerpVectors(a.tgt, a.exit.tgt, f); flash = smooth(0.36, 0.5, fr);
    } else {
      const f = smooth(0.5, 0.84, fr); cur.pos.lerpVectors(b.enter.pos, b.pos, f); cur.tgt.lerpVectors(b.enter.tgt, b.tgt, f); flash = 1 - smooth(0.5, 0.64, fr); world = b.world;
    }
    const fs = smooth(0.2, 0.8, fr);
    cur.fov = lerp(a.fov, b.fov, fs); cur.ox = lerp(a.ox, b.ox, fs); cur.oy = lerp(a.oy, b.oy, fs); cur.ap = lerp(a.ap, b.ap, fs);
    if (override && override.cam) { cur.pos.copy(override.cam.pos); cur.tgt.copy(override.cam.tgt); cur.fov = override.cam.fov || cur.fov; if (override.cam.world) world = override.cam.world; }
    mouse.sx += (mouse.x - mouse.sx) * 0.04; mouse.sy += (mouse.y - mouse.sy) * 0.04;
    const sway = (REDUCED ? 0 : Math.sin(T0 * 0.16) * 0.045) - mouse.sx * 0.12;
    tmp.copy(cur.pos).sub(cur.tgt).applyAxisAngle(UP, sway); tmp.y *= 1 + mouse.sy * 0.1;
    camera.position.copy(cur.tgt).add(tmp);
    camera.fov = cur.fov; camera.lookAt(cur.tgt);
    camera.setViewOffset(W, H, -cur.ox * W, -cur.oy * H, W, H);
    camera.updateProjectionMatrix();
    if (bokeh) bokeh.uniforms.aperture.value = cur.ap * 0.5;
    outG.visible = world === 'out'; inG.visible = world === 'in';
    /* the shadow-casting light covers whichever place the camera is looking at */
    const atHome = cur.tgt.distanceTo(HM) < cur.tgt.length();
    key.target.position.copy(atHome ? HM : tmp2.set(-20, 0, -34)); key.position.copy(key.target.position).add(tmp2.set(60, 330, -110));
    scene.fog.density = world === 'in' ? 0.0017 : 0;
    finish.uniforms.uFlash.value = flash;
    finish.uniforms.uT.value = REDUCED || SHOT ? 0 : (now % 1000) / 1000;   /* film grain holds still for screenshots and checks */

    /* the agents */
    for (const [name, ag] of Object.entries(AG)) {
      place(ag, TRACK[name], p);
      ag.g.visible = ag.world === world && ag.s > 0.06;
      if (!ag.g.visible) continue;
      ag.g.position.y += Math.sin(T0 * 1.1 + ag.ph) * ag.r * 0.1;
      ag.g.scale.setScalar(ag.s);
      tmp2.set(camera.position.x, ag.g.position.y + (camera.position.y - ag.g.position.y) * 0.35, camera.position.z);
      ag.g.lookAt(tmp2);
      ag.blink -= dt; const bl = ag.blink < 0.12 ? 0.12 : 1; if (ag.blink < 0) ag.blink = 2 + Math.random() * 3.5;
      ag.eyes.forEach((e) => { e.scale.y = 1.5 * bl; });
      ag.hands.forEach((h, k) => {
        const bs = h.userData.base, wave = ag.talk * (k ? 1 : 0.25);
        h.position.set(bs.x, bs.y + Math.sin(T0 * 2 + k * 2 + ag.ph) * ag.r * 0.06 + wave * ag.r * (0.9 + Math.sin(T0 * 7) * 0.16), bs.z + wave * ag.r * 0.3);
      });
    }

    /* depth of field: everything between the nearest and the farthest agent on screen stays sharp;
       only what lies outside that band (the far city, the far platform) is softened */
    if (bokeh) {
      camera.updateMatrixWorld();
      let lo = 1e9, hi = -1e9;
      for (const ag of Object.values(AG)) {
        if (!ag.g.visible || ag.s < 0.5) continue;
        tmp2.copy(ag.g.position).project(camera);
        if (tmp2.z > 1 || Math.abs(tmp2.x) > 1.1 || Math.abs(tmp2.y) > 1.3) continue;
        const depth = -tmp2.copy(ag.g.position).applyMatrix4(camera.matrixWorldInverse).z;
        lo = Math.min(lo, depth - ag.r * 1.8); hi = Math.max(hi, depth + ag.r * 1.8);
      }
      if (hi < lo) { const d = -tmp2.copy(cur.tgt).applyMatrix4(camera.matrixWorldInverse).z; lo = d - 25; hi = d + 25; }
      /* the margin grows with distance because the packed depth texture is a few percent off far away */
      const wantF = (lo + hi) / 2, wantB = (hi - lo) / 2 + 10 + hi * 0.1, k = focusNow < 0 || SHOT ? 1 : 0.1;
      focusNow += (wantF - focusNow) * k; bandNow += (wantB - bandNow) * k;
      bokeh.uniforms.focus.value = focusNow + focusBias; bokeh.uniforms.band.value = bandNow;
    }

    /* outside: the device is in trouble from chapter 1 until the report */
    const fault = p > 0.55 && p < 7.6;
    if (actLed) { actLed.emissive.set(0xff3b2f); actLed.emissiveIntensity = fault && !REDUCED ? 1.6 + Math.sin(T0 * 6) * 1.2 : fault ? 2 : 0; }
    devLed[2].color.set(fault ? 0xff3b2f : 0x58ff8a).multiplyScalar(fault ? 1.8 + Math.sin(T0 * 6) * 1.2 : 2.2);
    if (tvMat.map !== (fault ? tvBad : tvOk)) { tvMat.map = fault ? tvBad : tvOk; tvMat.needsUpdate = true; }
    const done = p > 7.6;
    if (screenMat.map !== (done ? reportTex : screenTex)) { screenMat.map = done ? reportTex : screenTex; screenMat.needsUpdate = true; }
    const working = p > 1.55 && p < 4.4;
    beat += dt;
    if (working && beat > 0.9 && !REDUCED) { beat = 0; fireRoute(turn++ % routes.length); }
    let landed = false;
    routes.forEach((set) => set.forEach((r) => {
      if (r.head >= 0) { r.head += dt * 62 / r.total; if (r.head > 1.5) r.head = -1; else if (r.head > 1 && !r.hit) { r.hit = true; landed = true; } }
      if (r.head < 0.5) r.hit = false;
      r.m.uniforms.uHead.value = r.head < 0 ? -1 : r.head; r.m.uniforms.uBase.value = working ? 0.16 : 0; r.m.uniforms.uFill.value = 1;
    }));
    if (landed) haloT = 0;
    haloT += dt; const ht = haloT / 0.9;
    halo.material.opacity = ht < 1 ? (1 - ht) * (1 - ht) * 0.5 : 0; halo.scale.setScalar(1.3 + ht * 0.18);
    const on = smooth(2.45, 2.9, p) * (1 - smooth(7.9, 8.0, p) * 0);
    link.m.uniforms.uFill.value = on; link.m.uniforms.uHead.value = on > 0.99 ? (T0 * 0.22) % 1.4 - 0.2 : -1;
    link2.m.uniforms.uHead.value = on > 0.99 ? 1.2 - ((T0 * 0.22 + 0.7) % 1.4) : -1;
    dust.rotation.y = T0 * 0.004;

    /* inside */
    if (world === 'in') {
      caseLid.rotation.x = -smooth(4.6, 4.8, p) * 1.95;
      tools.forEach((t, i) => {
        const s = smooth(4.7 + i * 0.03, 4.9 + i * 0.03, p);
        t.position.lerpVectors(tmp.set(TK.x, TK.y + 14, TK.z), t.userData.to, s); t.position.y += Math.sin(T0 * 1.3 + i) * 1.4 * s;
        t.scale.setScalar(Math.max(0.001, s)); t.lookAt(camera.position.x, t.position.y, camera.position.z);
      });
      const k = lerp(20, 36, smooth(5.45, 5.82, p)), hit = p > 5.82; pcMark.visible = p > 5.42;
      pcMark.position.set(TILE(k), IO.y + 15 + (hit ? Math.sin(T0 * 5) * 0.8 : Math.sin(T0 * 12) * 0.5), IO.z);
      pcMark.material.color.set(hit ? 0xff5a45 : 0xffd48a).multiplyScalar(6.5); pcMark.rotation.y = T0 * 1.4;
      crashRing.scale.setScalar(1 + ((T0 * 0.6) % 1) * 1.1); crashRing.material.opacity = 0.6 * (1 - (T0 * 0.6) % 1);
      slabs.forEach((s, n) => { const v = smooth(5.84 + n * 0.03, 5.94 + n * 0.03, p); s.visible = v > 0.01; s.scale.set(1, Math.max(0.01, v), 1); s.position.y = SP.y + 5 + n * 10 * v; });
      const bp = smooth(6.02, 6.28, p);
      badPtr.m.uniforms.uFill.value = bp; badPtr.m.uniforms.uHead.value = bp > 0.99 && !REDUCED ? (T0 * 0.5) % 1.5 : -1;
      voidRing.material.opacity = bp > 0.95 ? 0.75 : 0; voidRing.scale.setScalar(1 + Math.sin(T0 * 3) * 0.08);
      const hv = smooth(5.9, 6.12, p); holo.visible = hv > 0.01; holo.material.opacity = hv; holo.scale.set(1, Math.max(0.01, hv), 1);
      const sv = smooth(5.7, 6.0, p) * (1 - smooth(7.3, 7.5, p)); streamPts.material.opacity = sv * 0.9;
      if (sv > 0.01) {
        streams.forEach((s, i) => { const t = (T0 * s.s + s.o) % 1, u = 1 - t; spos[i * 3] = u * u * s.a.x + 2 * u * t * s.m.x + t * t * holo.position.x + (s.o - 0.5) * 120 * t; spos[i * 3 + 1] = u * u * s.a.y + 2 * u * t * s.m.y + t * t * holo.position.y; spos[i * 3 + 2] = u * u * s.a.z + 2 * u * t * s.m.z + t * t * holo.position.z; });
        streamPts.geometry.attributes.position.needsUpdate = true;
      }
      const ch = smooth(6.72, 7.12, p);
      chain.m.uniforms.uFill.value = ch; chain.m.uniforms.uHead.value = ch > 0.99 && !REDUCED ? (T0 * 0.4) % 1.5 : -1;
      srcTile.material.color.set(0xffd48a).multiplyScalar(0.2 + ch * 2.6);
      shaft.visible = false;
    }

    /* page elements that follow the scene */
    for (const ag of Object.values(AG)) ag.wantTalk = 0;
    for (const pin of pins) {
      const [pos, w, r] = ANCHOR[pin.key]();
      let show = p >= pin.a && p <= pin.b && w === world && flash < 0.2;
      if (show) {
        tmp2.copy(pos); tmp2.y += pin.dy + (r ? r * 1.75 : 0); tmp2.project(camera);
        show = tmp2.z < 1 && Math.abs(tmp2.x) < 1.15 && Math.abs(tmp2.y) < 1.15;
        if (show) { let x = (tmp2.x * 0.5 + 0.5) * W; if (pin.who) { const half = Math.min(158, W * 0.34); x = clamp(x, half + 8, W - half - 8); }
          pin.el.style.transform = `translate3d(${x.toFixed(1)}px,${Math.max(pin.who ? 110 : 0, (-tmp2.y * 0.5 + 0.5) * H).toFixed(1)}px,0)`; }
      }
      pin.el.classList.toggle('is-on', show);
      if (show && pin.who && AG[pin.who]) AG[pin.who].wantTalk = 1;
    }
    for (const ag of Object.values(AG)) ag.talk += (ag.wantTalk - ag.talk) * 0.12;

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
    ready: true, KF, scene, camera, C: (x, y, z) => C(x, y, z).toArray(), HM: HM.toArray(),
    set(p, cam) { override = p == null ? null : { p, cam: cam && { pos: V(...cam.pos), tgt: V(...cam.tgt), fov: cam.fov, world: cam.world } }; kick(); },
    /* for tools/check-sharpness.mjs: switch depth of field, push the focus off on purpose, list the agents on screen */
    dof(on) { if (bokeh) bokeh.enabled = on; return !!bokeh; },
    focusBias(mm) { focusBias = mm; },
    dofState() { return { focus: focusNow, band: bandNow, aperture: bokeh ? bokeh.uniforms.aperture.value : 0, maxblur: bokeh ? bokeh.uniforms.maxblur.value : 0 }; },
    agents() {
      const out = [], v = new THREE.Vector3(), e = new THREE.Vector3();
      for (const [name, ag] of Object.entries(AG)) {
        if (!ag.g.visible || ag.s < 0.9) continue;
        v.copy(ag.g.position).project(camera); if (v.z > 1) continue;
        e.copy(ag.g.position).addScaledVector(camera.up, ag.r).project(camera);
        out.push({ name, x: (v.x * 0.5 + 0.5) * W, y: (-v.y * 0.5 + 0.5) * H, r: Math.abs(e.y - v.y) * 0.5 * H });
      }
      return out;
    },
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
