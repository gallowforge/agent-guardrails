// The shared Glass grammar of the gallowforge READMEs: the light, the glass and the type, and the
// four compositions every README uses (hero, numbers band, feature panel, social preview).
// Positions are the apparent centre in CSS px (y down); sizes are apparent CSS px; z is depth
// (+ toward the viewer; 1 unit = 1 px at z = 0). Each repository passes its own accent light.

export const merge = (a, b) => { const o = { ...a }; for (const k in b) o[k] = (b[k] && typeof b[k] === 'object' && !Array.isArray(b[k])) ? merge(a[k] || {}, b[k]) : b[k]; return o; };

const NIGHT = {
  page: '#0d1117',
  env: { top: '#0a0d15', bot: '#06080c', haze: '#18202f', hazeStr: 0.10, ray: '#a0b6dc',
    shafts: [[0.15, 0.060, 0.06, 0.16], [0.31, 0.024, 0.03, 0.10], [0.66, 0.030, -0.03, 0.11], [0.85, 0.070, -0.06, 0.15], [0.50, 0.20, 0.0, 0.05]],
    glows: [[0.5, 1.15, 0.65, 0.10, '#8ea6d8'], [0.5, -0.15, 0.6, 0.05, '#4a5f86']] },
  glass: { tint: [0.062, 0.068, 0.082], transmit: 0.80, fog: '#0b0e15', light: [-700, 800, 900], rim: 0.9, spec: 0.40, glow: 0.62, shadow: 0.30, levels: 5, caustic: 0 },
  motes: { n: 70, color: '#d4e2ff', amt: 0.50 },
  bloom: 0.40, bloomThresh: 0.55,
};
const DAY = {
  page: '#ffffff',
  env: { top: '#eef1f5', bot: '#f6f6f4', haze: '#ffffff', hazeStr: 0.04, shadeCol: '#eceff4', sun: [0.42, 0, -1.15, 1.0], dapple: 0.6, sunStr: 0.075, sunCol: '#fff4e2',
    shadow: 0.55, caustic: 0.10, shadowDir: [0.05, -0.075], glows: [] },
  glass: { tint: [0.075, 0.078, 0.084], transmit: 0.88, fog: '#eef1f5', light: [-700, 800, 900], rim: 0.95, spec: 0.30, glow: 0.0, shadow: 0.12, levels: 5, caustic: 0 },
  motes: { n: 40, color: '#fffaf0', amt: 0.45 },
  bloom: 0.0, bloomThresh: 0.95,
};

// The accent light: shafts tinted toward it, one pool of it behind the glass, nothing louder.
// a = { ray, pool, deep, top, haze, day, dayFog }
export function lights(a, shafts) {
  return {
    night: merge(NIGHT, { env: { top: a.top || '#0a0c13', bot: '#05070a', haze: a.haze || '#1a1e2c', ray: a.ray,
      shafts: shafts || [[0.48, 0.05, 0.05, 0.10], [0.66, 0.025, 0.02, 0.07], [0.86, 0.07, -0.04, 0.10], [0.70, 0.22, 0.0, 0.04]],
      glows: [[0.70, 0.52, 0.42, 0.09, a.pool], [0.72, 1.1, 0.6, 0.08, '#8ea6d8']] }, glass: { fog: a.fog || '#0b0d12' }, motes: { color: a.mote || '#dfe4ff' } }),
    day: merge(DAY, { env: { top: a.day || '#eff1f5', bot: '#f8f8f7', shadeCol: a.shade || '#ebeef3', sunCol: '#fff4e2' }, glass: { fog: a.dayFog || '#eff1f5' } }),
  };
}

const C = {
  kick: { night: { color: 'rgba(205,214,228,0.56)' }, day: { color: '#7b8291' } },
  name: { night: { color: '#f5f7fb' }, day: { color: '#0b0d11' } },
  head: { night: { color: '#f5f7fb' }, day: { color: '#0b0d11' } },
  line: { night: { color: 'rgba(222,229,240,0.74)' }, day: { color: '#4a5161' } },
  body: { night: { color: 'rgba(222,229,240,0.70)' }, day: { color: '#4a5161' } },
  fact: { night: { color: '#eef2f8' }, day: { color: '#10131a' } },
  fine: { night: { color: 'rgba(205,214,228,0.50)' }, day: { color: '#868c98' } },
  cmd: { night: { color: 'rgba(214,222,236,0.62)' }, day: { color: '#6a7180' } },
  copy: { night: { '--c-kick': 'rgba(205,214,228,0.56)', '--c-head': '#f5f7fb', '--c-body': 'rgba(222,229,240,0.70)' },
    day: { '--c-kick': '#7b8291', '--c-head': '#0b0d11', '--c-body': '#4a5161' } },
  rule: { night: { background: 'rgba(200,215,235,0.16)' }, day: { background: 'rgba(20,30,50,0.12)' } },
};
export const T = (cls, html, x, y, style = {}, align) => ({ cls, html, x, y, align, style, ...C[cls] });

// ---------- windows ----------
// A terminal stays dark in both themes; a document page has a dark and a light texture.
export const term = (M, name, pos, n, a, o = {}) => ({ tex: name + '.png', crop: [0, 0, M[name].w, M[name].h], key: '#0A0D12', keyAmt: n ? 0.6 : 0.12,
  bezel: 9, glowColor: a.deep, ...pos, ...o });
export const doc = (M, name, pos, n, a, o = {}) => {
  const t = n ? name : name + '-light';
  return { tex: t + '.png', crop: [0, 0, M[t].w, M[t].h], key: n ? '#0B0E14' : '#F7F7F5', keyAmt: n ? 0.55 : 0.72, bezel: 10, glowColor: a.deep, ...pos, ...o };
};
// The key line, lifted out of its window toward the viewer.
export const lift = (M, parent, name, key, n, a, o = {}) => {
  const r = M[name].lift[key]; if (!r) throw new Error(`no data-lift="${key}" in ${name}`);
  const p = o.pad ?? 18;
  const src = [r[0] - p * 1.6, r[1] - p, r[2] + p * 3.2, r[3] + p * 2];
  return { parent, src, lift: 80, breathe: [10, 0.25], bezel: 8, radius: 12, key: o.key || '#0A0D12', keyAmt: n ? 0.72 : 0.25,
    shadowOff: n ? [0, -14, 36] : [10, -20, 24], shadow: n ? 0.45 : 0.22, glow: 0.32, glowColor: a.lift || a.deep, ...o };
};

// ---------- compositions ----------

// Hero: the name and one line centred on top, the real output floating below.
export const hero = (o, L) => ({
  css: [840, o.h || 540], fade: [40, 40, 30, 46], cam: [11, 4], motion: 0.75, pool: o.pool || [0.5, 0.44, 0.62, 0.62],
  night: L.night, day: L.day, windows: (n) => o.win(n, false), anim: o.anim,
  type: [
    T('name', o.name, 420, 38, { fontSize: '64px' }, 'center'),
    T('line', o.line, 420, 118, { fontSize: '17px' }, 'center'),
  ],
});
export const heroPhone = (o, L) => ({
  css: [381, o.hp || 640], dpr: 1080 / 381, fade: [16, 16, 24, 30], cam: [7, 3], motion: 0.75, pool: o.poolPhone || [0.5, 0.40, 0.75, 0.55],
  night: L.night, day: L.day, windows: (n) => o.win(n, true), anim: o.animPhone,
  type: [
    T('name', o.name, 190.5, 30, { fontSize: o.namePhone || '52px' }, 'center'),
    T('line', o.linePhone || o.line, 190.5, 96, { fontSize: '15px', textAlign: 'center', lineHeight: '1.45' }, 'center'),
  ],
});

// Feature panel: copy on the page colour at the left, the glass at the right (as on the profile).
const copyHTML = (o, head) => `<div class="k">${o.kick}</div><div class="h">${head}</div><div class="b">${o.body}</div>`;
export const panel = (o, L) => ({
  css: [840, o.h || 440], fade: [36, 36, 30, 36], cam: [12, 5], pool: o.pool || [0.69, 0.52, 0.37, 0.66],
  night: L.night, day: L.day, windows: (n) => o.win(n, false),
  type: [T('copy', copyHTML(o, o.head), 2, (o.h || 440) / 2 + (o.dy || -8), { width: (o.cw || 296) + 'px', transform: 'translateY(-50%)',
    '--ks': '10.5px', '--hs': (o.hs || 34) + 'px', '--bs': '15.5px', '--gap1': '16px', '--gap2': '18px' })],
});
export const panelPhone = (o, L) => ({
  css: [381, o.hp || 572], dpr: 1080 / 381, fade: [18, 18, 24, 28], cam: [8, 4], pool: o.poolPhone || [0.5, 0.40, 0.70, 0.40],
  night: L.night, day: L.day, windows: (n) => o.win(n, true).map(w => w.parent !== undefined ? w : { ...w, y: w.y + (o.dyp ?? -30) }),
  type: [T('copy', copyHTML(o, o.headPhone || o.head), 4, 24, { width: '360px', '--ks': '9.5px', '--hs': '30px', '--bs': '14.5px', '--gap1': '12px', '--gap2': '12px' })],
});

// Numbers: true facts typeset on one band of glass. One fact may carry the accent (a third element `true`).
const accent = (t, on, o) => on ? { ...t, night: { ...t.night, '--acc': o.hi }, day: { ...t.day, '--acc': o.hiDay } } : t;
export const band = (o, L) => {
  const W = 780, n = o.facts.length, col = W / n;
  return {
    css: [840, 200], fade: [30, 30, 24, 28], cam: [0, 0], motion: 0.4, pool: [0.5, 0.5, 0.62, 0.9], phase: 0,
    night: L.night, day: L.day,
    windows: (nt) => [{ x: 420, y: 100, w: W, crop: [0, 0, W, 112], z: 0, rot: [0, 0, 0], bar: false, bezel: 0, radius: 26, bevel: 14,
      breathe: [0, 0], glow: nt ? 0.35 : 0, glowColor: o.glow, shadow: nt ? 0.3 : 0.14 }],
    type: [
      ...o.facts.flatMap((f, i) => [
        accent(T('fact', `<b style="font-size:38px;color:var(--acc,inherit)">${f[0]}</b><span style="font-size:12.5px;margin-top:9px;opacity:.66">${f[1]}</span>`, 30 + 34 + i * col, 62,
          { width: (col - 44) + 'px', whiteSpace: 'normal' }), f[2], o),
        ...(i ? [T('rule', '', 30 + i * col, 70, { width: '1px', height: '60px' })] : []),
      ]),
    ],
  };
};
export const bandPhone = (o, L) => ({
  css: [381, 300], dpr: 1080 / 381, fade: [14, 14, 20, 22], cam: [0, 0], motion: 0.4, pool: [0.5, 0.5, 0.9, 0.9], phase: 0,
  night: L.night, day: L.day,
  windows: (nt) => [{ x: 190.5, y: 150, w: 352, crop: [0, 0, 352, 252], z: 0, rot: [0, 0, 0], bar: false, bezel: 0, radius: 26, bevel: 14,
    breathe: [0, 0], glow: nt ? 0.35 : 0, glowColor: o.glow, shadow: nt ? 0.3 : 0.14 }],
  type: [
    ...o.facts.flatMap((f, i) => [accent(T('fact', `<b style="font-size:32px;color:var(--acc,inherit)">${f[0]}</b><span style="font-size:12px;margin-top:8px;opacity:.66">${f[1]}</span>`,
      14 + 26 + (i % 2) * 176, 24 + 30 + Math.floor(i / 2) * 122, { width: '138px', whiteSpace: 'normal' }), f[2], o)]),
    T('rule', '', 14 + 176, 54, { width: '1px', height: '196px' }),
    T('rule', '', 40, 150, { width: '300px' }),
  ],
});

// Social preview, 1280 x 640: the copy at the left, the hero's glass at the right.
export const social = (o, L) => ({
  css: [640, 320], dpr: 2, fade: [26, 26, 22, 26], cam: [0, 0], motion: 0, pool: [0.70, 0.52, 0.40, 0.75], themes: ['dark'], out: 'social.png',
  night: L.night, day: L.day, windows: o.win,
  type: [
    T('kick', o.kick, 40, 104, { fontSize: '10px' }),
    T('name', o.name, 38, 122, { fontSize: (o.size || 46) + 'px' }),
    T('line', o.line, 40, 122 + (o.size || 46) + 14, { fontSize: '15.5px', whiteSpace: 'normal', width: '236px' }),
  ],
});
