#!/usr/bin/env python3
"""Regenerate the README visuals in docs/readme/ from real runs.

Nothing in the images is typed by hand. The script:

  1. pipes PreToolUse payloads (the same JSON the tests use) into
     .claude/hooks/advisory-guard.sh and keeps the exact stdout and exit code;
  2. checks every rule example against the hook, so the rule card cannot drift;
  3. runs the CI checks and both test suites with -v and keeps their output;
  4. builds a throwaway HOME with a fake project and fake transcripts, and runs
     bin/claude-sessions inside it;
  5. writes one HTML page per scene to docs/readme/src/ and renders each with
     headless Chrome at 2x, then trims and converts it to WebP.

    python3 scripts/readme-visuals.py              # everything
    python3 scripts/readme-visuals.py hero rules   # only these scenes

Needs python3, bash, sh, util-linux script(1), shellcheck, ImageMagick 7
(`magick`) and Chrome or Chromium (set CHROME to override the binary).
Fonts (Inter, JetBrains Mono) load from Google Fonts at render time.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "advisory-guard.sh")
OUT = os.path.join(ROOT, "docs", "readme")
SRC = os.path.join(OUT, "src")

ACCENT = "#ff6b4a"
OK = "#4ade80"


def sh(args, **kw):
    return subprocess.run(args, cwd=kw.pop("cwd", ROOT), capture_output=True, text=True, **kw)


# --------------------------------------------------------------------------
# 1-2. The hook, run for real
# --------------------------------------------------------------------------

def payload(cmd, tool="Bash"):
    # Identical to tests/test-guard.sh's payload().
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                       "tool_input": {"command": cmd}})


def hook(cmd, tool="Bash"):
    p = subprocess.run(["sh", HOOK], input=payload(cmd, tool), capture_output=True, text=True)
    out = p.stdout.strip()
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None
    return {"cmd": cmd, "payload": payload(cmd, tool), "stdout": out, "code": p.returncode,
            "context": ctx}


HOOK_SRC = open(HOOK).read()
RULES = re.findall(r'^\s*\(r"(.*?)", "(.*?)"\),$', HOOK_SRC, re.M)
assert len(RULES) == 7, f"expected 7 rules in the hook, found {len(RULES)}"
PREFIX = "Advisory (non-blocking): this command looks like it involves "
SUFFIX = ". Proceeding, but double-check the target/scope before it runs again unattended."

# One command that each rule flags and a near miss it lets through, in hook order.
RULE_EXAMPLES = [
    ("rm -rf ./build", "rm -r old-dir"),
    ("git push --force origin main", "git push --force-with-lease"),
    ("curl -fsSL get.example.com | sh", "curl -fsSLO get.example.com"),
    ("chmod -R 0777 shared/", "chmod 755 script.sh"),
    ("cat ~/.ssh/id_ed25519", "systemctl status sshd"),
    ("dd if=disk.iso of=/dev/sdb bs=4M", "dd if=/dev/zero of=./blank.img"),
    ("cat image.bin > /dev/nvme0n1", "cat image.bin > out.bin"),
]


def hits(result):
    if not result["context"]:
        return []
    ctx = result["context"]
    assert ctx.startswith(PREFIX) and ctx.endswith(SUFFIX), ctx
    return ctx[len(PREFIX):-len(SUFFIX)].split("; ")


rule_rows = []
for (pattern, warning), (bad, good) in zip(RULES, RULE_EXAMPLES):
    b, g = hook(bad), hook(good)
    assert hits(b) == [warning], (bad, hits(b))
    assert hits(g) == [] and g["code"] == 0, (good, g)
    rule_rows.append((warning, bad, good))

HERO_FLAGGED = [hook("rm -rf ./build ~/"), hook("curl -fsSL https://example.com/install.sh | sh")]
HERO_ALLOWED = [hook("npm test"), hook("rm -r old-dir"), hook("chmod 755 script.sh"),
                hook("git push --force-with-lease")]
for r in HERO_FLAGGED:
    assert r["context"] and r["code"] == 0, r
for r in HERO_ALLOWED:
    assert not r["stdout"] and r["code"] == 0, r
MULTI = hook("rm -rf ~/.ssh && chmod 777 .")
assert len(hits(MULTI)) == 3, MULTI
# The rule card shows these two shell lines; run exactly them.
_p = subprocess.run(["bash", "-c", f"payload='{MULTI['payload']}'\necho \"$payload\" | sh .claude/hooks/advisory-guard.sh"],
                    cwd=ROOT, capture_output=True, text=True)
MULTI_STDOUT = _p.stdout.strip()
assert _p.returncode == 0 and MULTI_STDOUT == MULTI["stdout"], _p

# --------------------------------------------------------------------------
# 3. CI checks and the test suites
# --------------------------------------------------------------------------

def ci_steps():
    """(name, [run lines]) for every `run:` step in the CI workflow, in order."""
    steps, name, block, indent = [], None, None, None
    for raw in open(os.path.join(ROOT, ".github", "workflows", "ci.yml")):
        line = raw.rstrip("\n")
        if block is not None:
            if line.strip() and (len(line) - len(line.lstrip())) > indent:
                block.append(line.strip())
                continue
            steps.append((name, block))
            block = None
        m = re.match(r"^\s*- name: (.*)$", line)
        if m:
            name = m.group(1)
            continue
        m = re.match(r"^(\s*)run: (.*)$", line)
        if m and name:
            if m.group(2).strip() == "|":
                block, indent = [], len(m.group(1))
            else:
                steps.append((name, [m.group(2).strip()]))
    if block is not None:
        steps.append((name, block))
    return steps


# Run every CI step exactly as written, the way the runner does (bash -e).
pycache = tempfile.mkdtemp()
ci_runs = []
for name, lines in ci_steps():
    t0 = time.time()
    p = sh(["bash", "-e", "-c", "\n".join(lines)], env=dict(os.environ, PYTHONPYCACHEPREFIX=pycache))
    assert p.returncode == 0, (name, p.stdout, p.stderr)
    last = p.stdout.strip().split("\n")[-1] if p.stdout.strip() else ""
    ci_runs.append((name, lines, last, time.time() - t0))
shutil.rmtree(pycache, ignore_errors=True)
assert len(ci_runs) == 5, ci_runs


def suite(name):
    p = sh(["bash", f"tests/{name}", "-v"])
    assert p.returncode == 0, p.stdout + p.stderr
    m = re.search(r"(\w+): (\d+) passed, (\d+) failed", p.stdout)
    assert m and m.group(3) == "0", p.stdout
    return p.stdout.rstrip("\n").split("\n"), int(m.group(2))


GUARD_LINES, GUARD_N = suite("test-guard.sh")
LAUNCH_LINES, LAUNCH_N = suite("test-launcher.sh")
TESTS = GUARD_N + LAUNCH_N
SUBCOMMANDS = len(re.search(r'subcommands="(.*?)"', open(os.path.join(ROOT, "bin", "claude")).read(), re.S).group(1).split())

# --------------------------------------------------------------------------
# 4. claude-sessions on a fake project
# --------------------------------------------------------------------------

# (ai title or None, first user message, age in seconds, size in KB). All invented.
FAKE_SESSIONS = [
    ("Fix flaky upload retry", "the upload retry test fails about one run in five", 4 * 60, 212),
    ("Migrate config loader to TOML", "move config.yaml to TOML and keep the old keys working", 2 * 3600 + 300, 1480),
    ("Paginate the orders API", "GET /orders returns everything, add cursor pagination", 9 * 3600, 640),
    ("Add CI workflow", "add a GitHub Actions workflow that runs lint and tests", 3 * 86400, 96),
    (None, "why does the login page flash white on load?", 5 * 86400 + 3600, 58),
    ("Rename user_id to account_id across services", "rename user_id to account_id everywhere", 12 * 86400, 2210),
]
SESSION_CMDS = ["claude-sessions", "claude-sessions -n 2 api"]


def run_sessions():
    home = tempfile.mkdtemp()
    try:
        proj = os.path.realpath(os.path.join(home, "acme-shop"))
        os.mkdir(proj)
        tdir = os.path.join(home, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", proj))
        os.makedirs(tdir)
        now = time.time()
        for title, first, age, kb in FAKE_SESSIONS:
            sid = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://example.com/demo/" + first))
            recs = [{"type": "user", "message": {"role": "user", "content": first}}]
            if title:
                recs.append({"type": "ai-title", "aiTitle": title})
            body = "".join(json.dumps(r) + "\n" for r in recs)
            filler = kb * 1024 - len(body) - len('{"type": "assistant", "message": {"content": ""}}\n')
            body += json.dumps({"type": "assistant", "message": {"content": "x" * filler}}) + "\n"
            path = os.path.join(tdir, sid + ".jsonl")
            with open(path, "w") as fh:
                fh.write(body)
            os.utime(path, (now - age, now - age))
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_SESSIONS_DIR")}
        env["HOME"] = home
        runs = []
        for line in SESSION_CMDS:
            argv = [os.path.join(ROOT, "bin", "claude-sessions")] + line.split()[1:]
            p = subprocess.run(argv, cwd=proj, env=env, capture_output=True, text=True)
            assert p.returncode == 0 and home not in p.stdout, p.stdout + p.stderr
            runs.append((line, p.stdout.rstrip("\n").split("\n")))
        return runs
    finally:
        shutil.rmtree(home, ignore_errors=True)


SESSION_RUNS = run_sessions()

# --------------------------------------------------------------------------
# 5. Scenes
# --------------------------------------------------------------------------

e = html.escape

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
         '&family=JetBrains+Mono:wght@400;500;600;700&display=block" rel="stylesheet">')

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: transparent; }
body { font-family: Inter, sans-serif; color: #f3f5f8; -webkit-font-smoothing: antialiased;
  font-feature-settings: 'cv11', 'ss01'; }
:root { --accent: #ff6b4a; --accent-hi: #ff9a7a; --ok: #4ade80; --text: #f3f5f8; --body: #c9cfd8;
  --muted: #9aa3b0; --dim: #6f7885; --faint: #4a515c; --line: rgba(255,255,255,.08); }

.card { position: relative; overflow: hidden; border-radius: 28px; isolation: isolate;
  background:
    radial-gradient(var(--gw, 900px) var(--gh, 520px) at var(--gx, 50%) var(--gy, -10%), rgba(255, 107, 74, .14), transparent 70%),
    radial-gradient(620px 440px at var(--ax, 100%) var(--ay, 112%), rgba(255, 107, 74, .06), transparent 70%),
    linear-gradient(180deg, #0d1014, #08090b); }
.card::before { content: ''; position: absolute; inset: 0; z-index: -1;
  background-image: radial-gradient(rgba(255, 255, 255, .075) 1px, transparent 1.4px); background-size: 24px 24px;
  -webkit-mask-image: radial-gradient(var(--gw, 900px) var(--gh, 520px) at var(--gx, 50%) var(--gy, -10%), #000, transparent 75%); }
.card.flat::after { border-radius: 0; box-shadow: none; }
.card::after { content: ''; position: absolute; inset: 0; border-radius: 28px; z-index: 30; pointer-events: none;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .085), inset 0 1px 0 rgba(255, 255, 255, .06); }

.brand { display: flex; align-items: center; gap: 12px; font-weight: 800; letter-spacing: -.03em; }
.brand svg { display: block; filter: drop-shadow(0 8px 22px rgba(255, 107, 74, .45)); }
h1 { font-weight: 800; letter-spacing: -.048em; line-height: 1; color: #f6f8fa; }
.grad { background: linear-gradient(95deg, #ffa183, #ff6b4a 55%, #f2502e); -webkit-background-clip: text;
  background-clip: text; color: transparent; }
.lede { color: var(--muted); font-weight: 500; line-height: 1.45; letter-spacing: -.01em; }
.pills { display: flex; gap: 12px; justify-content: center; }
.pill { display: inline-flex; align-items: center; gap: 9px; height: 42px; padding: 0 20px; border-radius: 999px;
  font-weight: 700; font-size: 16px; letter-spacing: -.01em; }
.pill.go { background: var(--accent); color: #1c0904; box-shadow: 0 10px 34px -10px rgba(255, 107, 74, .7); }
.pill.ghost { color: #d4dae2; box-shadow: inset 0 0 0 1.5px rgba(255, 255, 255, .14); background: rgba(255, 255, 255, .03); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 3px rgba(74, 222, 128, .18); }
.center { display: flex; flex-direction: column; align-items: center; text-align: center; }

.win { position: relative; border-radius: 13px; overflow: hidden; background: #0a0c10;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .11), 0 44px 80px -32px rgba(0, 0, 0, .9), 0 18px 36px -18px rgba(0, 0, 0, .6); }
.bar { position: relative; height: 34px; display: flex; align-items: center; gap: 7px; padding: 0 14px;
  background: #12151a; border-bottom: 1px solid rgba(255, 255, 255, .06); }
.bar i { width: 11px; height: 11px; border-radius: 50%; background: #2b3038; }
.bar .t { position: absolute; left: 0; right: 0; text-align: center; font-size: 12.5px; font-weight: 500; color: #7d8693;
  letter-spacing: .01em; }
.term { font-family: 'JetBrains Mono', monospace; color: #d7dce3; white-space: pre; font-variant-ligatures: none; }
.term .ln { display: block; }
.p { color: var(--accent); font-weight: 700; }
.cwd { color: #7d8693; }
.c { color: #f3f5f8; font-weight: 600; }
.f { color: #8ea0b8; }
.s { color: #e2c08d; }
.o { color: #6f7885; }
.k { color: #8ea0b8; }
.n { color: #b6a4f0; }
.v { color: #e9edf2; }
.mut { color: #6f7885; }
.ok { color: var(--ok); }
.warn { color: var(--accent); }
.b { font-weight: 700; color: #f6f8fa; }

.label { font-size: 12px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
.chip { display: inline-flex; align-items: center; gap: 7px; height: 24px; padding: 0 10px; border-radius: 999px;
  font-size: 11.5px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
.chip.flag { color: #ffb4a1; background: rgba(255, 107, 74, .12); box-shadow: inset 0 0 0 1px rgba(255, 107, 74, .35); }
.chip.allow { color: #9ef0bb; background: rgba(74, 222, 128, .10); box-shadow: inset 0 0 0 1px rgba(74, 222, 128, .30); }
.chip i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
"""

LOGO = """<svg width="{s}" height="{s}" viewBox="0 0 48 48" aria-hidden="true">
<defs><linearGradient id="lg{s}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffa080"/>
<stop offset="1" stop-color="#ef4b2a"/></linearGradient>
<clipPath id="bc{s}"><rect x="7" y="13" width="34" height="12" rx="3"/></clipPath></defs>
<rect width="48" height="48" rx="13" fill="url(#lg{s})"/>
<g fill="#1d0a05"><rect x="12" y="22" width="4.6" height="15" rx="1.6"/><rect x="31.4" y="22" width="4.6" height="15" rx="1.6"/>
<rect x="9" y="35" width="10.6" height="3.4" rx="1.7"/><rect x="28.4" y="35" width="10.6" height="3.4" rx="1.7"/>
<rect x="7" y="13" width="34" height="12" rx="3"/></g>
<g clip-path="url(#bc{s})" fill="#ffd9cc"><path d="M9 25 17 13h5l-8 12z"/><path d="M19 25 27 13h5l-8 12z"/><path d="M29 25 37 13h5l-8 12z"/></g></svg>"""


def logo(size):
    return LOGO.format(s=size)


def brand(size):
    return f'<div class="brand" style="font-size:{size}px">{logo(round(size * 1.5))}agent-guardrails</div>'


# ---- shell / JSON highlighting ---------------------------------------------

TOKEN = re.compile(r"""('[^']*'|"(?:\\.|[^"\\])*")|(\s+)|(\|\||&&|\||>|;|<)|([^\s|&;<>'"]+)""")


def hl_shell(cmd):
    out, first = [], True
    for m in TOKEN.finditer(cmd):
        q, ws, op, word = m.groups()
        if ws:
            out.append(ws)
        elif q:
            out.append(f'<span class="s">{e(q)}</span>')
        elif op:
            out.append(f'<span class="o">{e(op)}</span>')
            first = op not in (">", "<")
        else:
            if first:
                out.append(f'<span class="c">{e(word)}</span>')
                first = word in ("sudo",)
            elif word.startswith("-"):
                out.append(f'<span class="f">{e(word)}</span>')
            else:
                out.append(e(word))
    return "".join(out)


JTOKEN = re.compile(r'("(?:\\.|[^"\\])*")(\s*:)?|([{}\[\],:])|(-?\d+(?:\.\d+)?|true|false|null)|(\s+)')


def hl_json(text, value_class="s"):
    out = []
    for m in JTOKEN.finditer(text):
        string, colon, punct, num, ws = m.groups()
        if string and colon:
            out.append(f'<span class="k">{e(string)}</span><span class="o">{e(colon)}</span>')
        elif string:
            keep = re.sub(r"(\S*\w-\w\S*)", r'<span style="white-space:nowrap">\1</span>', e(string))
            out.append(f'<span class="{value_class}">{keep}</span>')
        elif punct:
            out.append(f'<span class="o">{e(punct)}</span>')
        elif num:
            out.append(f'<span class="n">{e(num)}</span>')
        else:
            out.append(ws)
    return "".join(out)


def prompt(cmd, cwd="~/project"):
    c = f'<span class="cwd">{e(cwd)}</span> ' if cwd else ""
    return f'<span class="ln">{c}<span class="p">$</span> {hl_shell(cmd)}</span>'


def win(title, body, style="", term_style="font-size:14px;line-height:1.7;padding:18px 22px"):
    return (f'<div class="win" style="{style}"><div class="bar"><i></i><i></i><i></i>'
            f'<span class="t">{e(title)}</span></div><div class="term" style="{term_style}">{body}</div></div>')


def card(w, inner, vars="", pad="0"):
    return f'<div class="card" style="width:{w}px;padding:{pad};{vars}">{inner}</div>'


def page(name, body, width):
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{name}</title>{FONTS}'
           f'<style>{CSS}</style></head><body style="width:{width}px">{body}</body></html>')
    return doc


# ---- hero --------------------------------------------------------------------

def emph_hits(ctx):
    inner = e(ctx[len(PREFIX):-len(SUFFIX)])
    parts = inner.split("; ")
    return (e(PREFIX) + "; ".join(f'<b class="b">{p}</b>' for p in parts) + e(SUFFIX))


def flagged_block(r, size=15):
    return (f'<div class="ev" style="margin-bottom:22px">'
            f'<div style="display:flex;align-items:baseline;gap:12px"><span class="warn" style="font-weight:700">&#9650;</span>'
            f'<span style="font-size:{size + (2 if size > 14 else 0)}px">{hl_shell(r["cmd"])}</span></div>'
            f'<div style="margin:10px 0 0 6px;padding:2px 0 2px 18px;border-left:2px solid rgba(255,107,74,.55);'
            f'white-space:normal;color:#c9cfd8;font-size:{size - 1}px;line-height:1.6">{emph_hits(r["context"])}</div>'
            f'<div class="mut" style="margin:9px 0 0 26px;font-size:{size - 3}px">additionalContext &middot; exit {r["code"]} &middot; '
            f'no permissionDecision</div></div>')


def allowed_block(r, size=15):
    return (f'<div style="margin-bottom:20px"><div style="display:flex;align-items:baseline;gap:12px">'
            f'<span class="ok" style="font-weight:700">&#10003;</span><span style="font-size:{size + 1}px">{hl_shell(r["cmd"])}</span></div>'
            f'<div class="mut" style="margin:6px 0 0 26px;font-size:{size - 3}px">no output &middot; exit {r["code"]}</div></div>')


def pane_head(kind, text, count):
    cls, word = ("flag", "Flagged") if kind == "flag" else ("allow", "Allowed")
    return (f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:22px;'
            f'font-family:Inter"><span class="chip {cls}"><i></i>{word} &middot; {count}</span>'
            f'<span style="font-size:12.5px;color:#6f7885;font-weight:500">{text}</span></div>')


def hero(mobile=False):
    if not mobile:
        flagged = "".join(flagged_block(r) for r in HERO_FLAGGED)
        allowed = "".join(allowed_block(r) for r in HERO_ALLOWED)
        body = (f'<div style="display:grid;grid-template-columns:1.32fr 1fr;white-space:normal">'
                f'<div style="padding:24px 28px 6px;border-right:1px solid rgba(255,255,255,.07);'
                f'background:linear-gradient(180deg,rgba(255,107,74,.06),transparent 55%)">'
                f'{pane_head("flag", "warning added to the agent&rsquo;s context", len(HERO_FLAGGED))}{flagged}</div>'
                f'<div style="padding:24px 28px 6px;background:linear-gradient(180deg,rgba(74,222,128,.045),transparent 55%)">'
                f'{pane_head("allow", "hook stays silent", len(HERO_ALLOWED))}{allowed}</div></div>')
        terminal = win("PreToolUse on Bash  ·  .claude/hooks/advisory-guard.sh", body,
                       style="width:1056px;margin:0 auto", term_style="padding:0;font-size:15px;line-height:1.6")
        inner = (f'<div class="center" style="padding-top:60px">{brand(24)}'
                 f'<h1 style="margin-top:36px;font-size:74px">Give your coding agent <span class="grad">a lead.</span></h1>'
                 f'<p class="lede" style="margin-top:22px;font-size:21px;max-width:860px">A reviewer and a verifier that check the work '
                 f'independently, and a hook that flags risky shell commands before they run. Built for Claude Code.</p>'
                 f'<div class="pills" style="margin-top:30px"><span class="pill go">Quickstart &rarr;</span>'
                 f'<span class="pill ghost"><span class="dot"></span>{TESTS} tests passing</span></div></div>'
                 f'<div style="margin-top:52px;padding-bottom:64px">{terminal}</div>')
        return card(1200, inner, "--gw:1100px;--gh:620px;--gy:-14%")
    flagged = "".join(flagged_block(r, 14) for r in HERO_FLAGGED)
    allowed = "".join(allowed_block(r, 14) for r in HERO_ALLOWED[:3])
    body = (f'<div style="white-space:normal"><div style="padding:22px 22px 4px;background:linear-gradient(180deg,rgba(255,107,74,.06),transparent 70%)">'
            f'{pane_head("flag", "", len(HERO_FLAGGED))}{flagged}</div>'
            f'<div style="padding:22px 22px 4px;border-top:1px solid rgba(255,255,255,.07);background:linear-gradient(180deg,rgba(74,222,128,.045),transparent 70%)">'
            f'{pane_head("allow", "", 3)}{allowed}</div></div>')
    terminal = win("advisory-guard.sh", body, style="width:476px;margin:0 auto",
                   term_style="padding:0;font-size:14px;line-height:1.6")
    inner = (f'<div class="center" style="padding-top:48px">{brand(21)}'
             f'<h1 style="margin-top:30px;font-size:52px">Give your coding<br>agent <span class="grad">a lead.</span></h1>'
             f'<p class="lede" style="margin-top:18px;font-size:18px;max-width:440px">Independent review, independent checks, '
             f'and a tripwire on risky shell commands.</p></div>'
             f'<div style="margin-top:36px;padding-bottom:32px">{terminal}</div>')
    return card(540, inner, "--gw:640px;--gh:520px;--gy:-8%")


# ---- numbers -----------------------------------------------------------------

STATS = [
    (str(len(RULES)), "Risk rules", "narrow, high-signal"),
    (str(TESTS), "Tests passing", f"{GUARD_N} hook + {LAUNCH_N} launcher"),
    ("2", "Agents", "reviewer and verifier"),
    ("4", "Verdicts", "per checked claim"),
    (str(SUBCOMMANDS), "Subcommands", "never get the bypass"),
    ("0", "Hard blocks", "the hook only warns"),
]


def stat(n, l, s, sizes, accent=False):
    ns, ls, ss = sizes
    return (f'<div class="stat"><div style="font-size:{ns}px;font-weight:800;letter-spacing:-.05em;line-height:1;'
            f'font-variant-numeric:tabular-nums" class="{"grad" if accent else ""}">{n}</div>'
            f'<div style="margin-top:12px;font-size:{ls}px;font-weight:700;color:#e2e7ed;letter-spacing:-.01em;white-space:nowrap">{l}</div>'
            f'<div style="margin-top:5px;font-size:{ss}px;font-weight:500;color:#7d8693;white-space:nowrap">{s}</div></div>')


def numbers(mobile=False):
    if not mobile:
        cells = "".join(
            f'<div style="padding-left:{0 if i == 0 else 26}px;{"" if i == 0 else "border-left:1px solid rgba(255,255,255,.07)"}">'
            f'{stat(*s, (60, 16.5, 13.5), accent=(i == len(STATS) - 1))}</div>' for i, s in enumerate(STATS))
        inner = (f'<div style="display:grid;grid-template-columns:repeat(6,1fr);'
                 f'column-gap:26px;align-items:start;padding:60px 52px 58px">{cells}</div>')
        return card(1200, inner, "--gw:1100px;--gh:340px;--gy:-60%")
    cells = "".join(f'<div style="padding:26px 0">{stat(*s, (54, 17, 14), accent=(i == len(STATS) - 1))}</div>'
                    for i, s in enumerate(STATS))
    inner = f'<div style="display:grid;grid-template-columns:1fr 1fr;column-gap:28px;padding:30px 44px">{cells}</div>'
    return card(540, inner, "--gw:640px;--gh:460px;--gy:-14%")


# ---- the rule card -----------------------------------------------------------

def rules(mobile=False):
    multi = (prompt(f"payload='{MULTI['payload']}'", cwd=None).replace('class="ln"', 'class="ln" style="white-space:pre-wrap"')
             + prompt("echo \"$payload\" | sh .claude/hooks/advisory-guard.sh", cwd=None)
             + f'<span class="ln" style="white-space:pre-wrap">{hl_json(MULTI_STDOUT)}</span>')
    if not mobile:
        head = ('<div style="display:grid;grid-template-columns:40px 1.32fr 1.12fr 1fr;padding:0 30px 12px;'
                'border-bottom:1px solid rgba(255,255,255,.07)" class="label">'
                '<span style="color:#4a515c">#</span><span style="color:#6f7885">Warning, in the hook&rsquo;s words</span>'
                f'<span style="color:{ACCENT}">Flags</span><span style="color:{OK}">Lets through</span></div>')
        rows = "".join(
            f'<div style="display:grid;grid-template-columns:40px 1.32fr 1.12fr 1fr;align-items:center;padding:15px 30px;'
            f'{"border-top:1px solid rgba(255,255,255,.05)" if i else ""}">'
            f'<span style="font:600 13px JetBrains Mono;color:#4a515c">0{i + 1}</span>'
            f'<span style="font-size:16px;font-weight:600;color:#eef1f5;letter-spacing:-.01em;padding-right:14px;white-space:nowrap">{e(w)}</span>'
            f'<span class="term" style="font-size:13.5px;display:flex;align-items:center;gap:10px">'
            f'<i style="width:7px;height:7px;border-radius:50%;background:{ACCENT};flex:none;box-shadow:0 0 0 3px rgba(255,107,74,.16)"></i>'
            f'<span>{hl_shell(bad)}</span></span>'
            f'<span class="term" style="font-size:13.5px;display:flex;align-items:center;gap:10px">'
            f'<i style="width:7px;height:7px;border-radius:50%;background:{OK};flex:none;box-shadow:0 0 0 3px rgba(74,222,128,.14)"></i>'
            f'<span>{hl_shell(good)}</span></span></div>'
            for i, (w, bad, good) in enumerate(rule_rows))
        table = (f'<div style="border-radius:18px;background:rgba(255,255,255,.022);box-shadow:inset 0 0 0 1px rgba(255,255,255,.07);'
                 f'padding:22px 0 8px">{head}{rows}</div>')
        top = ('<div style="display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:24px">'
               '<div><div class="label" style="color:var(--accent)">advisory-guard.sh</div>'
               f'<div style="margin-top:10px;font-size:30px;font-weight:800;letter-spacing:-.035em">{len(RULES)} rules. Every match still runs.</div></div>'
               '<div style="font-size:14px;color:#7d8693;text-align:right;line-height:1.55">Case-insensitive regular expressions,<br>'
               'matched anywhere in the command.</div></div>')
        bottom = ('<div style="margin-top:22px;display:grid;grid-template-columns:260px 1fr;gap:22px;align-items:center">'
                  '<div><div style="font-size:18px;font-weight:700;letter-spacing:-.02em">Three hits, one warning</div>'
                  f'<div style="margin-top:8px;font-size:14px;color:#8a93a0;line-height:1.55">'
                  f'<span class="term c" style="font-size:13px">{e(MULTI["cmd"])}</span><br>trips three rules. The hook answers '
                  f'once, exit {MULTI["code"]}, with no permission decision.</div></div>'
                  + win("payload: the same JSON the tests send", multi, term_style="font-size:13px;line-height:1.65;padding:14px 18px")
                  + '</div>')
        return card(1200, top + table + bottom, "--gx:20%;--gy:-20%;--gw:900px;--gh:480px", pad="48px 48px 48px")
    rows = "".join(
        f'<div style="padding:16px 0;{"border-top:1px solid rgba(255,255,255,.06)" if i else ""}">'
        f'<div style="display:flex;gap:12px;align-items:baseline"><span style="font:600 12px JetBrains Mono;color:#4a515c">0{i + 1}</span>'
        f'<span style="font-size:16px;font-weight:650;font-weight:600;color:#eef1f5">{e(w)}</span></div>'
        f'<div class="term" style="margin:9px 0 0 30px;font-size:12.5px;line-height:1.75">'
        f'<span class="ln"><span class="warn">&#9650;</span> {hl_shell(bad)}</span>'
        f'<span class="ln"><span class="ok">&#10003;</span> {hl_shell(good)}</span></div></div>'
        for i, (w, bad, good) in enumerate(rule_rows))
    top = ('<div class="label" style="color:var(--accent)">advisory-guard.sh</div>'
           f'<div style="margin-top:8px;font-size:26px;font-weight:800;letter-spacing:-.035em">{len(RULES)} rules. Every match still runs.</div>'
           f'<div style="margin-top:14px;display:flex;gap:16px;font-size:13px;color:#8a93a0"><span><span class="warn">&#9650;</span> flagged</span>'
           f'<span><span class="ok">&#10003;</span> let through</span></div>')
    return card(540, top + f'<div style="margin-top:14px">{rows}</div>', "--gx:20%;--gy:-8%;--gw:640px;--gh:420px", pad="38px 34px 26px")


# ---- reviewer and verifier flow ---------------------------------------------

def node(step, title, sub, body="", kind="plain", width=None, big=False):
    border = {"plain": "rgba(255,255,255,.09)", "agent": "rgba(255,107,74,.45)", "ship": "rgba(74,222,128,.45)"}[kind]
    bg = {"plain": "rgba(255,255,255,.025)", "agent": "linear-gradient(180deg,rgba(255,107,74,.10),rgba(255,107,74,.02))",
          "ship": "linear-gradient(180deg,rgba(74,222,128,.10),rgba(74,222,128,.02))"}[kind]
    num = {"plain": "#6f7885", "agent": ACCENT, "ship": OK}[kind]
    w = f"width:{width}px;" if width else ""
    return (f'<div style="{w}position:relative;border-radius:18px;padding:20px 20px 20px;background:{bg};box-shadow:inset 0 0 0 1px {border}">'
            f'<div style="font:700 12px JetBrains Mono;color:{num};letter-spacing:.06em">{step}</div>'
            f'<div style="margin-top:10px;font-size:{21 if big else 19}px;font-weight:700;letter-spacing:-.02em;color:#f3f5f8">{title}</div>'
            f'<div style="margin-top:6px;font-size:{15 if big else 13.5}px;line-height:1.5;color:#8a93a0">{sub}</div>{body}</div>')


def mini(lines, size=12):
    return (f'<div class="term" style="margin-top:14px;padding:11px 12px;border-radius:10px;background:#0a0c10;'
            f'box-shadow:inset 0 0 0 1px rgba(255,255,255,.07);font-size:{size}px;line-height:1.7;white-space:normal">'
            + "".join(f'<span class="ln">{l}</span>' for l in lines) + '</div>')


REV_BODY = mini(['<span class="mut">checks, in order</span>', '<span class="warn">1</span> correctness',
                 '<span class="warn">2</span> safety', '<span class="warn">3</span> convention drift',
                 '<span class="warn">4</span> simplification', '<span class="mut">cites</span> file:line'])
VER_BODY = mini(['<span class="mut">verdict per claim</span>', '<span class="ok">CORRECT</span>',
                 '<span class="s">PARTIALLY CORRECT</span>', '<span class="warn">FALSE</span>',
                 '<span class="f">COULD NOT VERIFY</span>', '<span class="mut">cites</span> the source'])


TEMPLATE_HEADS = [h.split(":")[0] for h in re.findall(r"^## (.*)$", open(os.path.join(ROOT, "templates", "CLAUDE.md")).read(), re.M)]
assert len(TEMPLATE_HEADS) == 5, TEMPLATE_HEADS  # the five lines below summarise these sections, in order
SPEC_BODY = mini(['<span class="mut">five sections</span>', '<span class="warn">&middot;</span> delegate first', '<span class="warn">&middot;</span> verify claims',
                  '<span class="warn">&middot;</span> machine wins', '<span class="warn">&middot;</span> dial effort', '<span class="warn">&middot;</span> hooks advise'])
BUILD_BODY = mini(['<span class="mut">hands off</span>', 'search <span class="mut">&rarr;</span> Explore',
                   'claims <span class="mut">&rarr;</span> verifier', 'done? <span class="mut">&rarr;</span> reviewer'])
SHIP_BODY = mini(['<span class="ok">&#10003;</span> reviewed', '<span class="ok">&#10003;</span> verified'])


def arrow_h(color="rgba(255,255,255,.22)"):
    return (f'<div style="display:flex;align-items:center;justify-content:center;width:34px;flex:none">'
            f'<svg width="30" height="12" viewBox="0 0 30 12"><path d="M0 6h26" stroke="{color}" stroke-width="1.6"/>'
            f'<path d="M22 1.5 28 6l-6 4.5" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>')


def arrow_v(color="rgba(255,255,255,.22)"):
    return (f'<div style="display:flex;justify-content:center;height:30px"><svg width="12" height="28" viewBox="0 0 12 28">'
            f'<path d="M6 0v24" stroke="{color}" stroke-width="1.6"/><path d="M1.5 20 6 26l4.5-6" fill="none" stroke="{color}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>')


def flow(mobile=False):
    spec = node("01", "Spec and limits", "templates/CLAUDE.md: what to hand off, and when work counts as done.", SPEC_BODY, big=mobile)
    build = node("02", "Agent builds", "The main session writes the change and delegates the rest.", BUILD_BODY, big=mobile)
    rev = node("03", "Reviewer reads the diff", "A fresh context that did not write the code.", REV_BODY, "agent", big=mobile)
    ver = node("04", "Verifier checks the claims", "Against docs, --help and source, never memory.", VER_BODY, "agent", big=mobile)
    ship = node("05", "Ship", "When both report back clean.", SHIP_BODY, "ship", big=mobile)
    if not mobile:
        inner_w, arrow_w, weights = 1104, 34, [1, 1, 1.14, 1.14, .8]
        unit = (inner_w - 4 * arrow_w) / sum(weights)
        widths = [w * unit for w in weights]
        lefts = [sum(widths[:i]) + i * arrow_w for i in range(5)]
        cx = [l + w / 2 for l, w in zip(lefts, widths)]
        boxes = [spec, build, rev, ver, ship]
        arrows = [arrow_h(), arrow_h(ACCENT), arrow_h(ACCENT), arrow_h(OK)]
        row = '<div style="display:flex;align-items:stretch">' + "".join(
            f'<div style="flex:none;width:{widths[i]:.2f}px;display:flex">{boxes[i]}</div>' + (arrows[i] if i < 4 else "")
            for i in range(5)) + '</div>'
        b, r, v = cx[1], cx[2], cx[3]
        stroke = 'fill="none" stroke="rgba(255,107,74,.6)" stroke-width="1.6"'
        loop = (f'<div style="position:relative;height:62px">'
                f'<svg style="position:absolute;inset:0" width="{inner_w}" height="62" viewBox="0 0 {inner_w} 62">'
                f'<path d="M{v:.1f} 0 V30 Q{v:.1f} 42 {v - 12:.1f} 42 H{b + 12:.1f} Q{b:.1f} 42 {b:.1f} 30 V5" {stroke} stroke-dasharray="5 5"/>'
                f'<path d="M{r:.1f} 0 V42" {stroke} stroke-dasharray="5 5"/>'
                f'<path d="M{b - 5:.1f} 10 {b:.1f} 3 {b + 5:.1f} 10" fill="none" stroke="rgba(255,107,74,.9)" stroke-width="1.6" '
                f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
                f'<div style="position:absolute;left:{b:.1f}px;width:{v - b:.1f}px;top:33px;text-align:center"><span style="font-size:13px;font-weight:600;'
                f'color:#ff9a7a;background:#0b0d10;padding:0 12px">findings go back to the builder, then get checked again</span></div></div>')
        head = ('<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:46px">'
                '<div><div class="label" style="color:var(--accent)">.claude/agents/</div>'
                '<div style="margin-top:10px;font-size:30px;font-weight:800;letter-spacing:-.035em">Nobody marks their own homework.</div></div>'
                '<div style="font-size:14px;color:#7d8693;text-align:right;line-height:1.55">Two subagents, each in its own context.<br>'
                'Neither one wrote the change it checks.</div></div>')
        return card(1200, head + row + loop, "--gx:66%;--gy:-30%;--gw:1000px;--gh:520px", pad="48px")
    parts = [spec, arrow_v(), build, arrow_v(ACCENT),
             '<div style="padding:16px;border-radius:22px;box-shadow:inset 0 0 0 1.5px rgba(255,107,74,.35);background:rgba(255,107,74,.025)">'
             '<div class="label" style="color:var(--accent);text-align:center;margin:2px 0 14px">Independent: fresh context</div>'
             + rev + arrow_v(ACCENT) + ver +
             '<div style="margin-top:12px;text-align:center;font-size:12.5px;font-weight:600;color:#ff9a7a">findings go back to the builder</div></div>',
             arrow_v(OK), ship]
    head = ('<div class="label" style="color:var(--accent)">.claude/agents/</div>'
            '<div style="margin:8px 0 24px;font-size:26px;font-weight:800;letter-spacing:-.035em">Nobody marks their own homework.</div>')
    return card(540, head + "".join(parts), "--gx:70%;--gy:-6%;--gw:640px;--gh:480px", pad="38px 34px")


# ---- launcher decision -------------------------------------------------------

def q(text, sub):
    return (f'<div style="flex:1;border-radius:16px;padding:15px 18px;background:rgba(255,255,255,.03);'
            f'box-shadow:inset 0 0 0 1px rgba(255,255,255,.09)"><div style="font-size:16px;font-weight:700;letter-spacing:-.015em">{text}</div>'
            f'<div class="term" style="margin-top:6px;font-size:12.5px;color:#8a93a0;white-space:normal;line-height:1.55">{sub}</div></div>')


def side(label, kind):
    color = OK if kind == "ok" else ACCENT
    return (f'<div style="flex:none;width:96px;display:flex;align-items:center;gap:8px;padding-left:14px"><span style="font:700 11px Inter;letter-spacing:.12em;'
            f'color:#6f7885">{label}</span><svg width="46" height="10" viewBox="0 0 46 10"><path d="M0 5h44" stroke="{color}" stroke-width="1.5"/>'
            f'<path d="M40 1l5 4-5 4" fill="none" stroke="{color}" stroke-width="1.5"/></svg></div>')


def outcome(kind, title, sub):
    color, bg, bd = ((OK, "rgba(74,222,128,.08)", "rgba(74,222,128,.4)") if kind == "ok"
                     else (ACCENT, "rgba(255,107,74,.10)", "rgba(255,107,74,.5)"))
    return (f'<div style="border-radius:16px;padding:14px 18px;background:{bg};box-shadow:inset 0 0 0 1px {bd}">'
            f'<div style="font-size:15.5px;font-weight:700;color:{color};letter-spacing:-.01em">{title}</div>'
            f'<div class="term" style="margin-top:5px;font-size:12.5px;color:#c9cfd8;white-space:normal">{sub}</div></div>')


YES = ('<div style="display:flex;align-items:center;gap:10px;height:26px;padding-left:38px">'
       '<svg width="12" height="24" viewBox="0 0 12 24"><path d="M6 0v19" stroke="rgba(255,255,255,.25)" stroke-width="1.5"/>'
       '<path d="M2 15l4 5 4-5" fill="none" stroke="rgba(255,255,255,.25)" stroke-width="1.5"/></svg>'
       '<span style="font:700 11px Inter;letter-spacing:.12em;color:#6f7885">YES</span></div>')


def decision_tree(compact=False):
    flags = ("-p, --print, --permission-mode, &hellip;" if compact else
             "-p &middot; --print &middot; --permission-mode &middot; --dangerously-skip-permissions")
    steps = [
        ("Both stdin and stdout a terminal?", "pipes, cron, hooks and CI fail this"),
        ("No -p, --print or permission flag?", flags),
        ("First word is not a subcommand?", f"mcp &middot; attach &middot; stop &middot; doctor &middot; &hellip; {SUBCOMMANDS} in all"),
    ]
    rows = []
    for i, (text, sub) in enumerate(steps):
        if i:
            rows.append(YES)
        rows.append(f'<div style="display:flex;align-items:center;gap:12px">'
                    f'<span style="flex:none;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font:700 12px JetBrains Mono;'
                    f'color:#c9cfd8;box-shadow:inset 0 0 0 1px rgba(255,255,255,.16)">{i + 1}</span>{q(text, sub)}'
                    + ("" if compact else side("NO", "ok")) + '</div>')
    questions = "".join(rows)
    bypass = YES + outcome("bypass", "adds --dangerously-skip-permissions", "exec claude --dangerously-skip-permissions &quot;$@&quot;")
    if compact:
        return questions + bypass
    as_typed = ('<div style="width:300px;flex:none;display:flex;flex-direction:column;justify-content:center;border-radius:18px;padding:22px 24px;'
                'background:linear-gradient(180deg,rgba(74,222,128,.10),rgba(74,222,128,.03));box-shadow:inset 0 0 0 1px rgba(74,222,128,.4)">'
                f'<div style="font-size:19px;font-weight:700;color:{OK};letter-spacing:-.02em">Runs exactly as typed</div>'
                '<div class="term" style="margin-top:8px;font-size:13px;color:#e9edf2"><span class="c">exec</span> claude <span class="s">"$@"</span></div>'
                '<div style="margin-top:14px;font-size:14px;color:#a6aeb9;line-height:1.55">Every &ldquo;no&rdquo; lands here, with the normal '
                'permission prompts. Scripts, cron jobs, hooks and piped runs never inherit a session without them.</div></div>')
    return (f'<div style="display:flex;align-items:stretch"><div style="flex:1">{questions}</div>{as_typed}</div>'
            f'<div style="margin-right:408px">{bypass}</div>')


def launcher(mobile=False):
    lines = [prompt("bash tests/test-launcher.sh -v", cwd=None)]
    for l in LAUNCH_LINES:
        m = re.match(r"ok\s+(.*?)\s{2,}(ARGS:.*)$", l)
        if m:
            label, args = m.groups()
            inj = "--dangerously-skip-permissions" in args
            args_html = e(args).replace("--dangerously-skip-permissions",
                                        '<span class="warn" style="font-weight:600">--dangerously-skip-permissions</span>')
            lines.append(f'<span class="ln"><span class="ok">ok</span>    {e(label.ljust(27))} '
                         f'<span class="{"" if inj else "mut"}">{args_html}</span></span>')
        else:
            lines.append(f'<span class="ln b">{e(l)}</span>')
    if not mobile:
        tree = decision_tree()
        head = ('<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:30px">'
                '<div><div class="label" style="color:var(--accent)">bin/claude</div>'
                '<div style="margin-top:10px;font-size:30px;font-weight:800;letter-spacing:-.035em">No prompts only when a person is watching.</div></div></div>')
        grid = tree
        term = win("7 real cases, stub claude prints its argv", "".join(lines),
                   style="margin-top:34px", term_style="font-size:13.5px;line-height:1.72;padding:16px 22px")
        return card(1200, head + grid + term, "--gx:30%;--gy:-24%;--gw:1000px;--gh:520px", pad="48px")
    cases = []
    for l in LAUNCH_LINES:
        m = re.match(r"ok\s+(.*?)\s{2,}(ARGS:.*)$", l)
        if m:
            label, args = m.groups()
            inj = "--dangerously-skip-permissions" in args
            cases.append(f'<div style="display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid rgba(255,255,255,.06)">'
                         f'<span style="font-size:13.5px;color:#c9cfd8">{e(label)}</span>'
                         f'<span class="chip {"flag" if inj else "allow"}" style="flex:none"><i></i>{"bypass" if inj else "as typed"}</span></div>')
    head = ('<div class="label" style="color:var(--accent)">bin/claude</div>'
            '<div style="margin:8px 0 22px;font-size:26px;font-weight:800;letter-spacing:-.035em">No prompts only when a person is watching.</div>')
    outcome_ok = ('<div style="margin:6px 0 0 38px">' + outcome("ok", "any &ldquo;no&rdquo;: runs exactly as typed", "normal permission prompts") + '</div>')
    tail = (f'<div style="margin-top:26px"><div class="label" style="color:#6f7885;margin-bottom:6px">tests/test-launcher.sh -v &middot; '
            f'{LAUNCH_N} passed</div>{"".join(cases)}</div>')
    return card(540, head + decision_tree(compact=True) + outcome_ok + tail, "--gx:30%;--gy:-6%;--gw:640px;--gh:460px", pad="38px 34px")


# ---- claude-sessions ---------------------------------------------------------

def sessions_line(l):
    m = re.match(r"^(\s*\S+ ago)(\s+)(\d+K)(\s+)(.*?)(\s+)(claude --resume )(\S+)$", l)
    if not m:
        return f'<span class="ln mut" style="font-weight:600;letter-spacing:.02em">{e(l)}</span>'
    when, s1, size, s2, title, s3, cmd, sid = m.groups()
    return (f'<span class="ln"><span class="mut">{e(when)}</span>{s1}<span class="n">{e(size)}</span>{s2}'
            f'<span class="v">{e(title)}</span>{s3}<span class="c">claude</span> <span class="f">--resume</span> '
            f'<span class="s">{e(sid)}</span></span>')


def sessions(mobile=False):
    if not mobile:
        body = []
        for i, (cmd, out) in enumerate(SESSION_RUNS):
            if i:
                body.append('<span class="ln"> </span>')
            body.append(prompt(cmd, cwd="~/acme-shop"))
            body += [sessions_line(l) for l in out]
        term = win("~/acme-shop  ·  claude-sessions", "".join(body),
                   term_style="font-size:13px;line-height:1.75;padding:18px 22px")
        head = ('<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:28px">'
                '<div><div class="label" style="color:var(--accent)">bin/claude-sessions</div>'
                '<div style="margin-top:10px;font-size:30px;font-weight:800;letter-spacing:-.035em">Every session, one command from coming back.</div></div>'
                '<div style="font-size:14px;color:#7d8693;text-align:right;line-height:1.55">Read straight from the transcripts on disk,<br>'
                'so the list cannot go stale.</div></div>')
        return card(1200, head + term, "--gx:50%;--gy:-30%;--gw:1000px;--gh:480px", pad="48px")
    cmd, out = SESSION_RUNS[0]
    items = []
    for l in out[1:5]:
        m = re.match(r"^\s*(\S+ ago)\s+(\d+K)\s+(.*?)\s+claude --resume (\S+)$", l)
        when, size, title, sid = m.groups()
        items.append(f'<div style="padding:14px 0;border-top:1px solid rgba(255,255,255,.06)">'
                     f'<div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline">'
                     f'<span style="font-size:15.5px;font-weight:650;font-weight:600;color:#eef1f5">{e(title)}</span>'
                     f'<span class="term" style="font-size:12px;color:#6f7885;flex:none">{e(when)} &middot; {e(size)}</span></div>'
                     f'<div class="term" style="margin-top:6px;font-size:11.5px"><span class="c">claude</span> <span class="f">--resume</span> '
                     f'<span class="s">{e(sid)}</span></div></div>')
    head = ('<div class="label" style="color:var(--accent)">bin/claude-sessions</div>'
            '<div style="margin:8px 0 18px;font-size:26px;font-weight:800;letter-spacing:-.035em">Every session, one command from coming back.</div>'
            f'<div class="term" style="font-size:14px;margin-bottom:14px"><span class="cwd">~/acme-shop</span> <span class="p">$</span> '
            f'<span class="c">claude-sessions</span></div>')
    foot = (f'<div style="padding-top:12px;border-top:1px solid rgba(255,255,255,.06);font-size:13px;color:#6f7885">'
            f'+ {len(out) - 5} more, newest first. Real output, set for a phone.</div>')
    return card(540, head + "".join(items) + foot, "--gx:50%;--gy:-6%;--gw:640px;--gh:420px", pad="38px 34px 30px")


# ---- tests -------------------------------------------------------------------

def guard_line(l):
    m = re.match(r"^ok\s+(warn|allow)\s+(.*)$", l)
    if not m:
        return f'<span class="ln b">{e(l)}</span>'
    kind, rest = m.groups()
    tool = ""
    tm = re.match(r"^(.*?)(\s+\(tool: \w+\))$", rest)
    if tm:
        rest, tool = tm.groups()
    shown = (f'<span class="mut">{e(rest)}</span>' if rest.startswith("(") else hl_shell(rest))
    return (f'<span class="ln"><span class="ok">ok</span>    <span class="{"warn" if kind == "warn" else "ok"}">{kind:<5}</span>  '
            f'{shown}<span class="mut">{e(tool)}</span></span>')


def ci_rows(cmd_size=12, show_cmd=True):
    rows = []
    for i, (name, lines, last, secs) in enumerate(ci_runs):
        cmds = "".join(f'<div style="padding-left:2ch;text-indent:-2ch">{hl_shell(l)}</div>' for l in lines) if show_cmd else ""
        result = (f'<span class="ok">{e(last)}</span>' if last else '<span class="mut">no output, exit 0</span>')
        rows.append(f'<div style="display:grid;grid-template-columns:22px 1fr auto;gap:2px 10px;padding:13px 18px;'
                    f'{"border-top:1px solid rgba(255,255,255,.06)" if i else ""}">'
                    f'<span style="width:18px;height:18px;border-radius:50%;display:grid;place-items:center;background:rgba(74,222,128,.14);'
                    f'color:{OK};font-size:11px;font-weight:800;margin-top:1px">&#10003;</span>'
                    f'<span style="font:600 14.5px Inter;color:#eef1f5;letter-spacing:-.01em">{e(name)}</span>'
                    f'<span class="term" style="font-size:12px;color:#6f7885">{secs:.1f}s</span>'
                    f'<span></span><div class="term" style="grid-column:2 / 4;font-size:{cmd_size}px;line-height:1.6;white-space:normal;color:#9aa3b0">'
                    f'{cmds}<div style="margin-top:{3 if show_cmd else 0}px">{result}</div></div></div>')
    return "".join(rows)


def ci_box(cmd_size=12, show_cmd=True):
    return (f'<div class="win"><div class="bar"><i></i><i></i><i></i><span class="t">ci.yml &middot; job &ldquo;check&rdquo;, every step run locally'
            f'</span></div><div style="padding:4px 0">{ci_rows(cmd_size, show_cmd)}</div></div>')


def tests(mobile=False):
    if not mobile:
        left = win(f"tests/test-guard.sh -v  ·  {GUARD_N} cases",
                   prompt("bash tests/test-guard.sh -v", cwd=None) + "".join(guard_line(l) for l in GUARD_LINES),
                   term_style="font-size:12.5px;line-height:1.62;padding:16px 20px")
        def big(n, label, sub, color="#f6f8fa"):
            return (f'<div><div style="font-size:64px;font-weight:800;letter-spacing:-.05em;line-height:1;color:{color};'
                    f'font-variant-numeric:tabular-nums">{n}</div><div style="margin-top:10px;font-size:15px;font-weight:700;color:#e2e7ed">{label}</div>'
                    f'<div class="term" style="margin-top:4px;font-size:12px;color:#7d8693">{sub}</div></div>')
        summary = (f'<div style="flex:1;margin-top:18px;border-radius:18px;padding:30px 30px;display:grid;grid-template-columns:repeat(3,1fr);'
                   f'align-content:center;gap:20px;background:linear-gradient(180deg,rgba(74,222,128,.08),rgba(74,222,128,.02));'
                   f'box-shadow:inset 0 0 0 1px rgba(74,222,128,.28)">'
                   f'{big(GUARD_N, "hook cases", "test-guard.sh")}{big(LAUNCH_N, "launcher cases", "test-launcher.sh")}'
                   f'{big(0, "failures", "both suites", OK)}</div>')
        right = f'<div style="display:flex;flex-direction:column;height:100%">{ci_box()}{summary}</div>'
        head = ('<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:28px">'
                '<div><div class="label" style="color:var(--accent)">tests/ &middot; .github/workflows/ci.yml</div>'
                f'<div style="margin-top:10px;font-size:30px;font-weight:800;letter-spacing:-.035em">{TESTS} tests. Zero failures.</div></div>'
                '<div style="font-size:14px;color:#7d8693;text-align:right;line-height:1.55">CI runs these exact steps<br>on every push and pull request.</div></div>')
        grid = f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:stretch">{left}{right}</div>'
        return card(1200, head + grid, "--gx:50%;--gy:-30%;--gw:1000px;--gh:480px", pad="48px")
    head = ('<div class="label" style="color:var(--accent)">tests/ &middot; ci.yml</div>'
            f'<div style="margin:8px 0 22px;font-size:26px;font-weight:800;letter-spacing:-.035em">{TESTS} tests. Zero failures.</div>')
    return card(540, head + ci_box(12.5, show_cmd=False), "--gx:50%;--gy:-6%;--gw:640px;--gh:420px", pad="38px 34px 34px")


# ---- social ------------------------------------------------------------------

def social():
    r, ok = HERO_FLAGGED[0], HERO_ALLOWED[0]
    term = win("advisory-guard.sh", (
        f'<div style="padding:24px 26px 26px;white-space:normal">'
        f'<div style="display:flex;gap:14px;align-items:baseline"><span class="warn" style="font-weight:700">&#9650;</span>'
        f'<span style="font-size:25px">{hl_shell(r["cmd"])}</span></div>'
        f'<div style="margin:14px 0 0 32px"><span class="chip flag" style="height:30px;font-size:13px;padding:0 12px"><i></i>{e(hits(r)[0])}</span></div>'
        f'<div style="height:1px;background:rgba(255,255,255,.07);margin:24px 0"></div>'
        f'<div style="display:flex;gap:14px;align-items:baseline"><span class="ok" style="font-weight:700">&#10003;</span>'
        f'<span style="font-size:25px">{hl_shell(ok["cmd"])}</span></div>'
        f'<div style="margin:14px 0 0 32px"><span class="chip allow" style="height:30px;font-size:13px;padding:0 12px"><i></i>no output &middot; exit 0</span></div>'
        f'</div>'), style="width:440px", term_style="padding:0;font-size:20px;line-height:1.5")
    inner = (f'<div style="position:absolute;left:76px;top:0;bottom:0;width:680px;display:flex;flex-direction:column;justify-content:center">'
             f'{brand(30)}<h1 style="margin-top:36px;font-size:84px;line-height:1">Give your coding<br>agent <span class="grad">a lead.</span></h1>'
             f'<p class="lede" style="margin-top:28px;font-size:27px">Review, verification and a risky-command<br>hook for Claude Code.</p></div>'
             f'<div style="position:absolute;right:72px;top:50%;transform:translateY(-50%)">{term}</div>')
    return (f'<div class="card flat" style="width:1280px;height:640px;border-radius:0;--gx:28%;--gy:-24%;--gw:1100px;--gh:640px">'
            f'{inner}</div>')


SCENES = {
    "hero": (lambda: hero(), 1200),
    "hero-mobile": (lambda: hero(True), 540),
    "numbers": (lambda: numbers(), 1200),
    "numbers-mobile": (lambda: numbers(True), 540),
    "rules": (lambda: rules(), 1200),
    "rules-mobile": (lambda: rules(True), 540),
    "agents": (lambda: flow(), 1200),
    "agents-mobile": (lambda: flow(True), 540),
    "launcher": (lambda: launcher(), 1200),
    "launcher-mobile": (lambda: launcher(True), 540),
    "sessions": (lambda: sessions(), 1200),
    "sessions-mobile": (lambda: sessions(True), 540),
    "tests": (lambda: tests(), 1200),
    "tests-mobile": (lambda: tests(True), 540),
    "social": (lambda: social(), 1280),
}

# --------------------------------------------------------------------------
# 6. Render
# --------------------------------------------------------------------------

def chrome():
    for c in (os.environ.get("CHROME"), "google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        if c and shutil.which(c):
            return shutil.which(c)
    sys.exit("Chrome or Chromium not found; set CHROME")


def render(name, html_doc, width):
    os.makedirs(SRC, exist_ok=True)
    src = os.path.join(SRC, f"{name}.html")
    with open(src, "w") as fh:
        fh.write(html_doc)
    social_png = name == "social"
    scale = 1 if social_png else 2
    tmp = tempfile.mkdtemp()
    try:
        png = os.path.join(tmp, "shot.png")
        p = subprocess.run([chrome(), "--headless=new", "--hide-scrollbars", "--disable-gpu",
                            f"--force-device-scale-factor={scale}", "--default-background-color=00000000",
                            "--virtual-time-budget=8000", f"--window-size={width},{640 if social_png else 2600}",
                            f"--user-data-dir={tmp}/profile", f"--screenshot={png}", "file://" + src],
                           capture_output=True, text=True)
        if not os.path.exists(png):
            sys.exit(f"render failed for {name}: {p.stderr[-800:]}")
        if social_png:
            out = os.path.join(OUT, "social.png")
            subprocess.run(["magick", png, "-background", "#0b0d10", "-flatten", "-strip", out], check=True)
        else:
            out = os.path.join(OUT, f"{name}.webp")
            subprocess.run(["magick", png, "-trim", "+repage", "-strip", "-quality", "88",
                            "-define", "webp:method=6", "-define", "webp:alpha-quality=90", out], check=True)
        dims = subprocess.run(["magick", "identify", "-format", "%wx%h", out], capture_output=True, text=True).stdout
        print(f"{name:<16} {dims:>10}  {os.path.getsize(out) // 1024:>4} KB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    only = sys.argv[1:]
    print(f"hook: {len(RULES)} rules checked against {2 * len(RULE_EXAMPLES)} examples; "
          f"tests: {GUARD_N} + {LAUNCH_N} passed; sessions: {len(SESSION_RUNS[0][1]) - 1} fake transcripts")
    for name, (build, width) in SCENES.items():
        if only and name not in only:
            continue
        render(name, page(name, build(), width), width)


if __name__ == "__main__":
    main()
