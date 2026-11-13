/*
 * Bundle docs/assets/js/scene.js and the vendored three.js into one classic script.
 *
 * Why: a page opened from file:// may not load ES modules, so the 3D stage would
 * never start when someone double-clicks index.html. One plain script has no such limit.
 *
 * Run after every change to scene.js:   npm run build
 */
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { statSync } from 'node:fs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vendor = join(root, 'docs/assets/vendor/three');
const out = join(root, 'docs/assets/js/scene.bundle.js');

await build({
  entryPoints: [join(root, 'docs/assets/js/scene.js')],
  outfile: out,
  bundle: true,
  format: 'iife',
  minify: true,
  target: ['es2020'],
  legalComments: 'inline',
  banner: { js: '/* DeviceHands 3D stage. Built from scene.js by tools/build-scene.mjs. Includes three.js (MIT), see assets/vendor/three/LICENSE. */' },
  plugins: [{
    name: 'vendored-three',
    setup(b) {
      b.onResolve({ filter: /^three$/ }, () => ({ path: join(vendor, 'build/three.module.min.js') }));
      b.onResolve({ filter: /^three\/addons\// }, (a) => ({ path: join(vendor, a.path.replace(/^three\//, '')) }));
    },
  }],
});
console.log('wrote', out, (statSync(out).size / 1024).toFixed(0) + ' KB');
