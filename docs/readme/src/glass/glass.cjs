// Renders the README images: real output pages -> textures -> Glass scenes -> WebP.
//
//   node docs/readme/src/glass/glass.cjs            textures, then every scene in both themes
//   node docs/readme/src/glass/glass.cjs scenes     scenes only (textures from the last run)
//   ONLY=hero,search node .../glass.cjs             just these scenes
//
// 1. Textures. Every page in docs/readme/src/tex/ holds real output, written there by the capture
//    script. Each is screenshotted at 2x into docs/readme/.build/shots/, and the box of every
//    element marked data-lift="key" is recorded in shots/manifest.json, so a scene can lift that
//    exact line out of its window. For a terminal page the box of every line and of every typed
//    character of a command is recorded too, so the hero can replay the run from that one texture.
// 2. Scenes. layouts.js places the textures as frosted-glass windows (scene.js, three.js + WebGL).
//    Each layout renders dark (on #0d1117) and light (on #ffffff). A layout with `anim` renders a
//    seamless loop instead of a still: the hero, where the glass holds still and the terminal
//    replays its run (grammar.js, replay). ImageMagick writes the stills and ffmpeg the loop
//    (encodeLoop, below) into docs/readme/.
//
// Needs node with `playwright` and `three` (0.170) resolvable through NODE_PATH, plus ImageMagick
// and ffmpeg. Nothing is installed into the repository.
const path = require('path'), fs = require('fs'), { execFileSync } = require('child_process');
const { pathToFileURL } = require('url');
const { chromium } = require('playwright');

const HERE = __dirname;
const README = path.resolve(HERE, '..', '..');
const TEX = path.join(README, 'src', 'tex');
const BUILD = path.join(README, '.build');
const SHOTS = path.join(BUILD, 'shots');
const THREE = path.dirname(require.resolve('three'));          // .../three/build
const TEXDPR = 2;
const MIME = { '.js': 'text/javascript', '.html': 'text/html', '.png': 'image/png', '.json': 'application/json', '.css': 'text/css' };
const LAUNCH = { args: ['--use-angle=vulkan', '--enable-features=Vulkan', '--ignore-gpu-blocklist', '--enable-gpu'] };

async function textures(browser) {
  fs.rmSync(SHOTS, { recursive: true, force: true }); fs.mkdirSync(SHOTS, { recursive: true });
  const manifest = {};
  for (const f of fs.readdirSync(TEX).filter(f => f.endsWith('.html')).sort()) {
    const name = f.replace(/\.html$/, '');
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 2400 }, deviceScaleFactor: TEXDPR });
    const page = await ctx.newPage();
    await page.goto(pathToFileURL(path.join(TEX, f)).href, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    const info = await page.evaluate(() => {
      const b = document.body.getBoundingClientRect(), lift = {};
      for (const el of document.querySelectorAll('[data-lift]')) {
        const rg = document.createRange(); rg.selectNodeContents(el);   // the text itself, not the full-width line
        const r = rg.getBoundingClientRect();
        lift[el.dataset.lift] = [r.left, r.top, r.width, r.height];
      }
      // a terminal: where each line sits, and each character a command types (x0, x1, row top, row bottom)
      const lines = [];
      if (document.body.classList.contains('term')) {
        const lh = parseFloat(getComputedStyle(document.body).lineHeight);
        let last = null;
        for (const el of document.querySelectorAll('.l')) {
          const r = el.getBoundingClientRect(), p = el.querySelector(':scope > .p');
          const line = { y: [r.top, r.bottom], gap: last !== null && r.top - last > 4, blank: !el.textContent.trim() };
          last = r.bottom;
          if (p) {
            line.chars = [];
            const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            for (let nd = tw.nextNode(); nd; nd = tw.nextNode()) {
              if (p.contains(nd)) continue;
              for (let i = 0; i < nd.data.length; i++) {
                if (!line.chars.length && !nd.data[i].trim()) continue;        // the space after the prompt
                const rg = document.createRange(); rg.setStart(nd, i); rg.setEnd(nd, i + 1);
                const c = rg.getBoundingClientRect(), row = Math.max(0, Math.floor((c.top + c.height / 2 - r.top) / lh));
                line.chars.push([c.left, c.right, r.top + row * lh, r.top + (row + 1) * lh]);
              }
            }
          }
          lines.push(line);
        }
      }
      return { w: b.width, h: b.height, lift, lines };
    });
    await page.screenshot({ path: path.join(SHOTS, name + '.png'), clip: { x: 0, y: 0, width: info.w, height: info.h } });
    const k = TEXDPR, px = (a) => a.map(v => Math.round(v * k));
    manifest[name] = { w: Math.round(info.w * k), h: Math.round(info.h * k), lift: Object.fromEntries(Object.entries(info.lift).map(([n, r]) => [n, px(r)])) };
    if (info.lines.length) manifest[name].lines = info.lines.map(l => ({ ...l, y: px(l.y), ...(l.chars ? { chars: l.chars.map(px) } : {}) }));
    await ctx.close();
  }
  const facts = path.join(TEX, 'facts.json');       // numbers measured by the capture, for the copy
  if (fs.existsSync(facts)) manifest.facts = JSON.parse(fs.readFileSync(facts, 'utf8'));
  fs.writeFileSync(path.join(SHOTS, 'manifest.json'), JSON.stringify(manifest, null, 1));
  console.log('textures:', Object.keys(manifest).length);
}

// A loop as an animated WebP. The first frame is encoded whole, as a still would be. Every later
// frame carries only the blocks that differ from what the viewer already has (ffmpeg's conditional
// replenishment; libwebp_anim compares each frame with the one before it instead, so a slow fade
// never arrives and leaves a ghost). Those later frames get a flat quantiser (-preset icon), four
// points higher: the default spends the fewest bits on the busiest blocks of a frame, and in a
// frame that is mostly empty the busiest blocks are the ones being sent, which then land as patches
// on smooth glass. ffmpeg keeps one setting for a whole file, so the frames are encoded with each
// and the first frame of one file is set in front of the later frames of the other.
function encodeLoop(dir, fps, q, out) {
  const enc = (more, to) => execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(fps), '-i', path.join(dir, 'f%04d.png'),
    '-pix_fmt', 'yuva420p', '-c:v', 'libwebp', '-lossless', '0', '-compression_level', '6', '-cr_threshold', '64', '-cr_size', '8',
    '-g', '100000', '-loop', '0', ...more, to]);
  const first = path.join(dir, 'first.webp'), rest = path.join(dir, 'rest.webp');
  enc(['-quality', String(q), '-frames:v', '2'], first);
  enc(['-quality', String(Math.min(100, q + 4)), '-preset', 'icon'], rest);
  const chunks = (f) => {                               // the RIFF chunks after the 12 byte file header
    const d = fs.readFileSync(f), c = [];
    for (let p = 12; p < d.length;) { const n = 8 + d.readUInt32LE(p + 4), e = p + n + (n & 1); c.push(d.subarray(p, e)); p = e; }
    return c;
  };
  const frame = (c) => c.toString('latin1', 0, 4) === 'ANMF';
  const A = chunks(first), B = chunks(rest);
  const body = Buffer.concat([...B.filter(c => !frame(c)), A.find(frame), ...B.filter(frame).slice(1)]);
  const head = Buffer.alloc(12); head.write('RIFF', 0, 'latin1'); head.writeUInt32LE(4 + body.length, 4); head.write('WEBP', 8, 'latin1');
  fs.writeFileSync(out, Buffer.concat([head, body]));
  fs.rmSync(first); fs.rmSync(rest);
}

async function scene(browser, layout, theme, L, frames, dir) {
  const ctx = await browser.newContext({ viewport: { width: L.css[0], height: L.css[1] }, deviceScaleFactor: L.dpr || 2 });
  const page = await ctx.newPage();
  page.on('console', m => { if (m.type() === 'error') console.log('[page]', m.text().slice(0, 2000)); });
  page.on('pageerror', e => console.log('[pageerror]', e.message));
  await page.route('http://glass.local/**', r => {
    const u = decodeURIComponent(new URL(r.request().url()).pathname);
    const [, root, rest] = u.match(/^\/([^/]+)\/(.*)$/) || [];
    const base = { glass: HERE, shots: SHOTS, three: THREE }[root];
    const f = base && path.join(base, rest);
    if (f && fs.existsSync(f)) r.fulfill({ path: f, contentType: MIME[path.extname(f)] || 'application/octet-stream' });
    else { console.log('404', u); r.fulfill({ status: 404, body: '' }); }
  });
  await page.goto(`http://glass.local/glass/scene.html?layout=${layout}&theme=${theme}&ss=2`);
  await page.waitForFunction(() => window.__ready !== undefined, null, { timeout: 30000 });
  await page.evaluate(() => window.__ready);
  await page.waitForTimeout(250);
  const shots = [];
  for (let i = 0; i < frames; i++) {
    await page.evaluate(p => window.renderAt(p), frames === 1 ? (L.phase || 0) : i / frames);
    const out = path.join(dir, frames === 1 ? 'still.png' : `f${String(i).padStart(4, '0')}.png`);
    await page.screenshot({ path: out }); shots.push(out);
  }
  await ctx.close();
  return shots;
}

(async () => {
  const what = process.argv[2] || 'all';
  const only = process.env.ONLY ? process.env.ONLY.split(',') : null;
  const browser = await chromium.launch(LAUNCH);
  if (what === 'all' || what === 'textures') await textures(browser);
  if (what === 'all' || what === 'scenes') {
    const M = JSON.parse(fs.readFileSync(path.join(SHOTS, 'manifest.json'), 'utf8'));
    const { build } = await import(pathToFileURL(path.join(HERE, 'layouts.js')).href);
    const LAYOUTS = build(M);
    for (const [layout, L] of Object.entries(LAYOUTS)) {
      const base = layout.replace(/-phone$/, ''), phone = layout.endsWith('-phone');
      if (only && !only.includes(base)) continue;
      for (const theme of L.themes || ['dark', 'light']) {
        const dir = path.join(BUILD, 'out', `${layout}-${theme}`); fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
        const name = `${base}-${theme}${phone ? '-phone' : ''}`;
        const t0 = Date.now();
        if (L.out) {                                    // the social preview: one PNG
          const [png] = await scene(browser, layout, theme, L, 1, dir);
          execFileSync('magick', [png, '-strip', '-define', 'png:compression-level=9', path.join(README, L.out)]);
          console.log(L.out, ((Date.now() - t0) / 1000).toFixed(1) + 's'); continue;
        }
        if (L.anim) {
          await scene(browser, layout, theme, L, L.anim.frames, dir);
          const out = path.join(README, name + '.webp');
          const q = [].concat(L.anim.q);
          encodeLoop(dir, L.anim.fps, q[theme === 'dark' ? 0 : q.length - 1], out);
          console.log(name + '.webp', (fs.statSync(out).size / 1e6).toFixed(2) + ' MB', ((Date.now() - t0) / 1000).toFixed(1) + 's');
          continue;
        }
        const [png] = await scene(browser, layout, theme, L, 1, dir);
        const out = path.join(README, name + '.webp');
        execFileSync('magick', [png, '-strip', '-quality', String(L.q || 88), '-define', 'webp:method=6', out]);
        console.log(name + '.webp', Math.round(fs.statSync(out).size / 1e3) + ' kB');
      }
    }
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
