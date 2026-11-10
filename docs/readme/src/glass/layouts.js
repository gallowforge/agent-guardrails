// agent-guardrails in Glass: every composition of the README. The windows show real hook, test
// and launcher output and the real agent files (see scripts/readme-visuals.py). The accent light
// is amber: a caution, never a stop, because nothing in this kit blocks.
import { lights, term, doc, lift, hero, heroPhone, panel, panelPhone, band, bandPhone, social } from './grammar.js';

const A = { ray: '#e9d6b2', pool: '#b8740f', deep: '#3d2c12', lift: '#d08a22', top: '#0b0c10', haze: '#1e1d21', fog: '#0c0c0f',
  mote: '#fff0d8', day: '#f5f3ef', shade: '#efece7', dayFog: '#f5f3ef' };
const L = lights(A);

export function build(M) {
  const F = M.facts;
  const dk = (n) => (n ? '#0B0E14' : '#F7F7F5');
  const HERO = {
    name: 'agent-guardrails', h: 600, hp: 640, namePhone: '40px',
    line: 'Independent review for coding agents, and a warning before a risky command runs.',
    linePhone: 'Independent review for coding agents,<br>and a warning before a risky command runs.',
    win: (n, ph) => {
      const P = ph
        ? { s: { x: 84, y: 262, w: 140, z: -420, rot: [4, 28, 0] }, d: { x: 298, y: 262, w: 130, z: -420, rot: [4, -28, 0] }, m: { x: 190.5, y: 392, w: 320, z: 0, rot: [3, 0, 0] } }
        : { s: { x: 178, y: 320, w: 210, z: -420, rot: [4, 28, 0] }, d: { x: 668, y: 326, w: 220, z: -420, rot: [4, -28, 0] }, m: { x: 420, y: 372, w: 420, z: 0, rot: [3, 0, 0] } };
      const far = { fog: n ? 0.42 : 0.30, lod: 0.8, defocus: 1.6, breathe: [14, 0.15] };
      return [
        term(M, 'guard-all', P.s, n, A, { ...far }),
        doc(M, 'verifier', P.d, n, A, { ...far, breathe: [14, 0.7] }),
        term(M, 'hook', P.m, n, A, { breathe: [10, 0.4], bezel: 10, glow: 0.75 }),
        lift(M, 2, 'hook', 'key', n, A, { lift: ph ? 60 : 90, breathe: [12, 0.85] }),
      ];
    },
  };
  const HOOK = {
    kick: 'Hook', head: 'A warning, not a wall',
    body: 'Every Bash command passes the hook first. When one of seven narrow rules matches, a warning lands in the agent’s own context. It returns no permission decision, so it never blocks.',
    win: (n, ph) => {
      const P = ph ? { a: { x: 150, y: 360, w: 230, z: -80, rot: [2, -12, 0] }, b: { x: 232, y: 470, w: 270, z: 60, rot: [0, -12, 0] } }
        : { a: { x: 548, y: 196, w: 250, z: -100, rot: [2, -18, 0] }, b: { x: 646, y: 300, w: 320, z: 50, rot: [0, -12, 0] } };
      return [
        doc(M, 'settings', P.a, n, A, { breathe: [8, 0.0] }),
        term(M, 'guard', P.b, n, A, { breathe: [8, 0.45] }),
        lift(M, 1, 'guard', 'key', n, A, { lift: 80, shift: [ph ? 0 : -24, 0] }),
      ];
    },
  };
  const AGENTS = {
    kick: 'Agents', head: 'Nobody reviews their own work', hw: 320,
    body: 'Two subagents check the work in a fresh context. <code>reviewer</code> reads the actual diff and cites file:line; <code>verifier</code> checks claims against primary sources and gives each a verdict.',
    win: (n, ph) => {
      const P = ph ? { a: { x: 140, y: 380, w: 220, z: -90, rot: [2, -12, 0] }, b: { x: 250, y: 440, w: 230, z: 50, rot: [0, -12, 0] } }
        : { a: { x: 534, y: 214, w: 240, z: -120, rot: [2, -18, 0] }, b: { x: 680, y: 236, w: 250, z: 40, rot: [0, -14, 0] } };
      return [
        doc(M, 'reviewer', P.a, n, A, { breathe: [8, 0.0] }),
        doc(M, 'verifier', P.b, n, A, { breathe: [8, 0.45] }),
        lift(M, 1, n ? 'verifier' : 'verifier-light', 'key', n, A, { lift: 90, pad: 14, key: dk(n), keyAmt: n ? 0.7 : 0.8, shift: [ph ? 0 : -40, 0] }),
      ];
    },
  };
  const LAUNCHER = {
    kick: 'Launcher', head: 'Skip prompts only when a person is watching', hw: 320,
    body: '<code>bin/claude</code> skips permission prompts only with a terminal on both ends, no print or permission flag, and no subcommand first. Scripts, cron jobs and CI always get the prompts.',
    win: (n, ph) => {
      const P = ph ? { a: { x: 164, y: 360, w: 250, z: -90, rot: [2, -12, 0] }, b: { x: 214, y: 474, w: 300, z: 60, rot: [0, -10, 0] } }
        : { a: { x: 556, y: 194, w: 290, z: -110, rot: [2, -18, 0] }, b: { x: 640, y: 312, w: 330, z: 50, rot: [0, -12, 0] } };
      return [
        doc(M, 'decide', P.a, n, A, { breathe: [8, 0.0] }),
        term(M, 'launcher', P.b, n, A, { breathe: [8, 0.45] }),
        lift(M, 1, 'launcher', 'key', n, A, { lift: 80, shift: [ph ? 0 : -24, 0] }),
      ];
    },
  };
  const SESSIONS = {
    kick: 'Sessions', head: 'Pick up any session where you left off', cw: 270,
    body: '<code>claude-sessions</code> lists the project’s saved sessions, newest first, with the exact command to resume each. It reads the transcripts every time, so the list cannot go stale.',
    win: (n, ph) => {
      const P = ph ? { a: { x: 190.5, y: 380, w: 360, z: -20, rot: [2, -8, 0] } } : { a: { x: 584, y: 214, w: 500, z: -40, rot: [2, -16, 0] } };
      return [
        term(M, 'sessions', P.a, n, A, { breathe: [8, 0.0] }),
        lift(M, 0, 'sessions', 'key', n, A, { lift: ph ? 60 : 100, grow: ph ? 1.0 : 1.04, shift: [ph ? 0 : -20, 0] }),
      ];
    },
  };
  const FACTS = { glow: A.deep, hi: '#ffc370', hiDay: '#9a5c06', facts: [
    [String(F.rules), 'risk rules, each tested from both sides'],
    [String(F.tests), `tests: ${F.guard} for the hook, ${F.launcher} for the launcher`],
    [String(F.agents), 'review agents, each in a fresh context'],
    ['0', 'commands blocked: the hook only warns', true],
  ] };
  // social preview: the hero's main window and its lifted line at the right, one page behind it
  const socialWin = (n) => { const w = HERO.win(n, false); return [
    { ...w[1], x: 590, y: 150, w: 128, z: -320, rot: [3, -26, 0] },
    { ...w[2], x: 456, y: 162, w: 236, z: 0, rot: [2, -12, 0] },
    { ...w[3], parent: 1, lift: 70, shift: [-10, 0] }]; };
  return {
    hero: hero(HERO, L), 'hero-phone': heroPhone(HERO, L),
    numbers: band(FACTS, L), 'numbers-phone': bandPhone(FACTS, L),
    hook: panel(HOOK, L), 'hook-phone': panelPhone(HOOK, L),
    agents: panel(AGENTS, L), 'agents-phone': panelPhone(AGENTS, L),
    launcher: panel(LAUNCHER, L), 'launcher-phone': panelPhone(LAUNCHER, L),
    sessions: panel(SESSIONS, L), 'sessions-phone': panelPhone(SESSIONS, L),
    social: social({ kick: 'Claude Code · Shell', name: 'agent-guardrails', size: 34,
      line: 'Independent review for coding agents, and a warning before a risky command runs.', win: socialWin }, L),
  };
}
