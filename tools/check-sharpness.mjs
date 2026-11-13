/*
 * Are the agents in focus in every chapter of the film?
 *
 * For each chapter it crops the inside of every agent's head out of the 3D stage twice, once with depth of
 * field on and once with it off, and compares edge sharpness (variance of a Laplacian).
 * An agent whose sharpness drops below RATIO of the no-blur picture is out of focus: FAIL.
 *
 *   PLAYWRIGHT_PATH=/path/to/playwright/index.mjs node tools/check-sharpness.mjs            # check
 *   PLAYWRIGHT_PATH=... node tools/check-sharpness.mjs --self-test    # push focus off by 150 mm: must FAIL
 *
 * Exit code: 0 all sharp · 1 something is blurred (or the self-test did not fail) · 2 cannot run
 *
 * Why this exists: on 2026-11-13 the first story cut shipped with blurred agents. The author had
 * looked at every chapter, saw the blur in two of them and read it as "cinematic depth of field".
 */
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const RATIO = 0.8, MIN_R = 14, CHAPTERS = [0, 1, 2, 3, 4, 5, 6, 6.3, 7, 8];
const SELF = process.argv.includes('--self-test');
const page_url = pathToFileURL(join(dirname(fileURLToPath(import.meta.url)), '../docs/index.html')).href + '?shot=1&lang=en';
if (!process.env.PLAYWRIGHT_PATH) { console.error('set PLAYWRIGHT_PATH to playwright/index.mjs'); process.exit(2); }
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_PATH).href);

const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--use-gl=angle', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
await page.goto(page_url, { waitUntil: 'load' });
try { await page.waitForFunction(() => window.__dh && window.__dh.ready, null, { timeout: 90000 }); } catch { console.error('3D stage did not start'); await browser.close(); process.exit(2); }
/* hide the page furniture, and skip the canvas fade-in: measuring a half-transparent first chapter gave numbers that changed from run to run */
await page.addStyleTag({ content: '.panel,.pins,.nav,.rail,.cue,.stage__scrim{display:none!important} #stage-canvas{opacity:1!important;transition:none!important}' });
if (!(await page.evaluate(() => window.__dh.dof(true)))) { console.error('this build has no depth of field pass (low-power mode?)'); await browser.close(); process.exit(2); }
if (SELF) await page.evaluate(() => window.__dh.focusBias(150));

/* variance of a 4-neighbour Laplacian over the luminance of a PNG, computed in the page */
async function sharpness(clip) {
  const b64 = (await page.screenshot({ clip })).toString('base64');
  return page.evaluate(async (b64) => {
    const bmp = await createImageBitmap(await (await fetch('data:image/png;base64,' + b64)).blob());
    const c = new OffscreenCanvas(bmp.width, bmp.height), g = c.getContext('2d'); g.drawImage(bmp, 0, 0);
    const d = g.getImageData(0, 0, bmp.width, bmp.height).data, w = bmp.width, h = bmp.height, L = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) L[i] = 0.2126 * d[i * 4] + 0.7152 * d[i * 4 + 1] + 0.0722 * d[i * 4 + 2];
    let sum = 0, sq = 0, n = 0;
    for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) { const v = 4 * L[y * w + x] - L[y * w + x - 1] - L[y * w + x + 1] - L[(y - 1) * w + x] - L[(y + 1) * w + x]; sum += v; sq += v * v; n++; }
    return sq / n - (sum / n) * (sum / n);
  }, b64);
}

let bad = 0, checked = 0;
for (const p of CHAPTERS) {
  const agents = await page.evaluate((p) => { window.__dh.dof(true); window.__dh.set(p); window.__dh.run(1.2); return window.__dh.agents(); }, p);
  for (const a of agents) {
    if (a.r < MIN_R) continue;
    /* a square inside the head (visor and eyes), so the background behind the agent, which is meant to be soft, is not measured */
    const clip = { x: Math.round(a.x - a.r * 0.62), y: Math.round(a.y - a.r * 0.62), width: Math.round(a.r * 1.24), height: Math.round(a.r * 1.24) };
    if (clip.x < 0 || clip.y < 0 || clip.x + clip.width > 1440 || clip.y + clip.height > 900) continue;
    const on = await sharpness(clip);
    await page.evaluate(() => { window.__dh.dof(false); window.__dh.run(0.04); });
    const off = await sharpness(clip);
    await page.evaluate(() => { window.__dh.dof(true); window.__dh.run(0.04); });
    const ratio = on / off, ok = ratio >= RATIO; checked++; if (!ok) bad++;
    console.log(`${ok ? 'ok  ' : 'FAIL'}  chapter ${String(p).padEnd(4)} ${a.name.padEnd(8)} head ${Math.round(a.r * 2)}px  sharpness ${ratio.toFixed(2)} of the no-blur picture`);
  }
}
await browser.close();
console.log(`${checked} agent views checked, ${bad} blurred (threshold ${RATIO})`);
if (!checked) { console.error('nothing was checked'); process.exit(2); }
if (SELF) { console.log(bad ? 'self-test passed: the deliberate defocus was caught' : 'SELF-TEST FAILED: a deliberate 150 mm defocus was not caught'); process.exit(bad ? 0 : 1); }
process.exit(bad ? 1 : 0);
