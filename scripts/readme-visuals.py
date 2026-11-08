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

The look is a level crossing: see docs/readme/IDENTITY.md.

    python3 scripts/readme-visuals.py              # everything
    python3 scripts/readme-visuals.py hero rules   # only these scenes
    python3 scripts/readme-visuals.py --profile ../github-profile/docs
                                                   # also the GitHub profile card

Needs python3, bash, sh, util-linux script(1), shellcheck, ImageMagick 7
(`magick`) and Chrome or Chromium (set CHROME to override the binary).
Fonts (Barlow, Barlow Condensed, Red Hat Mono) load from Google Fonts at render time.
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
# 5. The look: a level crossing (see docs/readme/IDENTITY.md)
# --------------------------------------------------------------------------

e = html.escape

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700'
         '&family=Barlow+Condensed:wght@500;600;700;800'
         '&family=Red+Hat+Mono:wght@400;500;600;700&display=block" rel="stylesheet">')

ASPHALT, YELLOW, RED, WHITE, PAPER, INK = "#141413", "#ffc61a", "#e2231a", "#f2f0ea", "#ece6d8", "#191816"

GRAIN = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='220'%3E"
         "%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/%3E"
         "%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .55 0'/%3E%3C/filter%3E"
         "%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")")

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: transparent; }
body { font-family: Barlow, sans-serif; color: var(--white); -webkit-font-smoothing: antialiased; }
:root { --asphalt: #141413; --asphalt-2: #1b1b19; --asphalt-3: #252522; --white: #f2f0ea; --grey: #a5a297; --dim: #75726a;
  --faint: #4a4842; --yellow: #ffc61a; --red: #e2231a; --red-hi: #ff5a48; --paper: #ece6d8; --paper-2: #e2dbca;
  --ink: #191816; --ink-2: #4f4b44; --ink-3: #8a857a; --rule: #d3cab5; }

.field { position: relative; overflow: hidden; isolation: isolate; background: var(--asphalt); }
.field::after { content: ''; position: absolute; inset: 0; z-index: 40; pointer-events: none; opacity: .045;
  background-image: GRAIN; mix-blend-mode: screen; }
.paper { position: relative; overflow: hidden; isolation: isolate; background: var(--paper); color: var(--ink); }
.paper::after { content: ''; position: absolute; inset: 0; z-index: 40; pointer-events: none; opacity: .07;
  background-image: GRAIN; mix-blend-mode: multiply; filter: invert(1); }
.abs { position: absolute; }
.cond { font-family: 'Barlow Condensed', sans-serif; }
.mono, .term { font-family: 'Red Hat Mono', monospace; font-variant-ligatures: none; }
.term { white-space: pre; }
.ln { display: block; }
.label { font: 700 13px/1 'Barlow Condensed', sans-serif; letter-spacing: .18em; text-transform: uppercase; }
svg { display: block; overflow: visible; }

/* shell colouring on asphalt */
.c { color: var(--white); font-weight: 600; }
.f { color: #cfcabd; }
.s { color: #edd08a; }
.o { color: var(--dim); }
.k { color: var(--grey); }
.n { color: var(--yellow); }
.v { color: #ece9e1; }
.mut { color: var(--dim); }
.hit { color: var(--white); font-weight: 700; }
.red { color: var(--red-hi); }
.yel { color: var(--yellow); }
/* on paper */
.paper .c { color: var(--ink); font-weight: 700; }
.paper .f { color: #3d3a34; }
.paper .s { color: #7a5600; }
.paper .o { color: var(--ink-3); }
.paper .mut { color: var(--ink-3); }
.paper .red { color: #c41d14; }
.paper .field { color: #dedad0; }
.paper .field .c { color: var(--white); } .paper .field .f { color: #cfcabd; } .paper .field .s { color: #edd08a; }
.paper .field .o { color: var(--dim); } .paper .field .mut { color: var(--dim); }
.wrap .ln { white-space: pre-wrap; padding-left: 16px; text-indent: -16px; }

/* a reflective sign plate: black face, white keyline, four bolts */
.plate { position: relative; background: #0c0c0b; border-radius: 12px;
  box-shadow: inset 0 0 0 6px #0c0c0b, inset 0 0 0 8.5px var(--white), 0 22px 40px -22px #000; }
.plate.yellow { background: var(--yellow); color: var(--ink);
  box-shadow: inset 0 0 0 5px var(--yellow), inset 0 0 0 7.5px var(--ink), 0 22px 40px -22px #000; }
.plate.white { background: var(--white); color: var(--ink);
  box-shadow: inset 0 0 0 5px var(--white), inset 0 0 0 7.5px var(--ink), 0 22px 40px -22px #000; }
.bolt { position: absolute; width: 9px; height: 9px; border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #e4e1d8, #8a877f 55%, #3a3834); box-shadow: 0 1px 1.5px rgba(0,0,0,.55); }
.bolt::after { content: ''; position: absolute; left: 1.5px; right: 1.5px; top: 3.75px; height: 1.5px;
  background: rgba(20, 20, 18, .7); transform: rotate(var(--r, 20deg)); }
""".replace("GRAIN", GRAIN)

SVG_DEFS = f"""<defs>
<radialGradient id="lensOn" cx="44%" cy="40%" r="62%"><stop offset="0" stop-color="#fff6ea"/><stop offset=".16" stop-color="#ff9a78"/>
<stop offset=".48" stop-color="#f22b1d"/><stop offset="1" stop-color="#8a0b05"/></radialGradient>
<radialGradient id="lensOff" cx="44%" cy="38%" r="64%"><stop offset="0" stop-color="#5e1913"/><stop offset=".65" stop-color="#36100c"/>
<stop offset="1" stop-color="#1e0705"/></radialGradient>
<radialGradient id="amberOff" cx="44%" cy="38%" r="64%"><stop offset="0" stop-color="#5a4210"/><stop offset=".65" stop-color="#35270a"/>
<stop offset="1" stop-color="#1d1505"/></radialGradient>
<radialGradient id="glow"><stop offset="0" stop-color="#ff4130" stop-opacity=".62"/><stop offset=".3" stop-color="#ff2a1a" stop-opacity=".2"/>
<stop offset="1" stop-color="#ff2a1a" stop-opacity="0"/></radialGradient>
<radialGradient id="wash"><stop offset="0" stop-color="#ff2a1a" stop-opacity=".16"/><stop offset="1" stop-color="#ff2a1a" stop-opacity="0"/></radialGradient>
<linearGradient id="steel" x1="0" x2="1"><stop offset="0" stop-color="#2f2e2b"/><stop offset=".3" stop-color="#8f8c84"/>
<stop offset=".5" stop-color="#6c6a63"/><stop offset="1" stop-color="#262523"/></linearGradient>
<linearGradient id="steelH" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8f8c84"/><stop offset=".45" stop-color="#5d5b55"/>
<stop offset="1" stop-color="#262523"/></linearGradient>
<linearGradient id="armShade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".28"/>
<stop offset=".3" stop-color="#fff" stop-opacity="0"/><stop offset=".75" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".38"/></linearGradient>
<linearGradient id="box" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#1d1d1b"/><stop offset=".4" stop-color="#3a3935"/>
<stop offset="1" stop-color="#161615"/></linearGradient>
<pattern id="armStripe" width="60" height="60" patternUnits="userSpaceOnUse" patternTransform="rotate(-42)">
<rect width="30" height="60" fill="{RED}"/><rect x="30" width="30" height="60" fill="{WHITE}"/></pattern>
<pattern id="hazard" width="22" height="22" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
<rect width="11" height="22" fill="{YELLOW}"/><rect x="11" width="11" height="22" fill="#121211"/></pattern>
</defs>"""


def bolts(inset=11, size=9, angles=(18, -34, 62, 7)):
    pos = [f"left:{inset}px;top:{inset}px", f"right:{inset}px;top:{inset}px",
           f"left:{inset}px;bottom:{inset}px", f"right:{inset}px;bottom:{inset}px"]
    return "".join(f'<i class="bolt" style="{p};width:{size}px;height:{size}px;--r:{a}deg"></i>' for p, a in zip(pos, angles))


def svg(w, h, body, style=""):
    return f'<svg class="abs" style="left:0;top:0;{style}" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{SVG_DEFS}{body}</svg>'


# ---- drawn parts -------------------------------------------------------------

def lamp(cx, cy, r, lit=False, amber=False, board=True, hood=True):
    """A crossing lamp seen face on: black target board, fresnel lens, visor."""
    out = []
    if lit:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r * 4.6:.1f}" fill="url(#glow)"/>')
    if board:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r * 1.46:.1f}" fill="#0b0b0a" stroke="{WHITE}" stroke-width="{max(1.2, r * .085):.1f}"/>')
    if hood:
        R, oy = r * 1.2, cy - r * .12
        half = (R * R - (cy - oy) ** 2) ** .5
        out.append(f'<path d="M{cx - half:.1f} {cy:.1f} A{R:.1f} {R:.1f} 0 0 1 {cx + half:.1f} {cy:.1f} Z" fill="#050505"/>')
    lens = "lensOn" if lit else ("amberOff" if amber else "lensOff")
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#{lens})"/>')
    ring = "#ffd9c9" if lit else ("#6e5418" if amber else "#6f2219")
    for k in (.7, .42):
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r * k:.1f}" fill="none" stroke="{ring}" stroke-opacity="{.32 if lit else .55}" '
                   f'stroke-width="{max(.8, r * .04):.1f}"/>')
    if not lit:
        out.append(f'<ellipse cx="{cx - r * .32:.1f}" cy="{cy - r * .36:.1f}" rx="{r * .3:.1f}" ry="{r * .16:.1f}" '
                   f'transform="rotate(-30 {cx - r * .32:.1f} {cy - r * .36:.1f})" fill="#fff" fill-opacity=".14"/>')
    if lit:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r * 1.9:.1f}" fill="url(#glow)" style="mix-blend-mode:screen"/>')
    return "".join(out)


def lamp_pod(cx, cy, r, gap, left_lit=True):
    """Two red lamps on a pill-shaped backboard, the way a crossing shows them."""
    w, h = gap + r * 3.5, r * 3.3
    return (f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{h / 2:.1f}" fill="#0b0b0a" '
            f'stroke="{WHITE}" stroke-width="{r * .1:.1f}"/>'
            + lamp(cx - gap / 2, cy, r, lit=left_lit, board=False)
            + lamp(cx + gap / 2, cy, r, lit=not left_lit, board=False))


def crossbuck(cx, cy, w, h, words=(("BASH", "ONLY"), ("CROSS", "ING")), angle=38):
    """A crossbuck: two boards, each word split around the centre bolt like RAIL ROAD / CROSS ING."""
    out = []
    for (a_word, b_word), a in zip(words, (-angle, angle)):
        t = (f'text-anchor="middle" font-family="Barlow Condensed" font-weight="800" font-size="{h * .6:.1f}" '
             f'letter-spacing="{h * .05:.1f}" fill="{INK}"')
        out.append(f'<g transform="translate({cx} {cy}) rotate({a})">'
                   f'<rect x="{-w / 2}" y="{-h / 2}" width="{w}" height="{h}" rx="{h * .12:.1f}" fill="{WHITE}" stroke="#0b0b0a" stroke-width="1.5"/>'
                   f'<rect x="{-w / 2 + h * .1:.1f}" y="{-h / 2 + h * .1:.1f}" width="{w - h * .2:.1f}" height="{h * .8:.1f}" rx="{h * .07:.1f}" '
                   f'fill="none" stroke="{INK}" stroke-width="{h * .045:.1f}"/>'
                   f'<text x="{-w * .27:.1f}" y="{h * .215:.1f}" {t}>{a_word}</text>'
                   f'<text x="{w * .27:.1f}" y="{h * .215:.1f}" {t}>{b_word}</text></g>')
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{h * .15:.1f}" fill="url(#steel)" stroke="#0b0b0a" stroke-width="1"/>')
    return "".join(out)


def post(cx, y0, y1, w=20, hazard=70):
    return (f'<rect x="{cx - w / 2}" y="{y0}" width="{w}" height="{y1 - y0}" fill="url(#steel)"/>'
            + (f'<rect x="{cx - w / 2 - 3}" y="{y1 - hazard}" width="{w + 6}" height="{hazard}" fill="url(#hazard)"/>'
               f'<rect x="{cx - w / 2 - 3}" y="{y1 - hazard}" width="{w + 6}" height="{hazard}" fill="url(#steel)" opacity=".28"/>'
               if hazard else ""))


def arm(x0, y, length, h, lamp_xs=(), lit_tip=True, counter=60):
    """The barrier arm, lowered: striped boom, shading, lamps along the top, counterweight."""
    out = [f'<rect x="{x0 - counter}" y="{y - h * .2:.1f}" width="{counter}" height="{h * 1.4:.1f}" rx="3" fill="url(#box)" stroke="#0b0b0a"/>']
    for k in range(3):
        out.append(f'<rect x="{x0 - counter + 6 + k * (counter - 12) / 3:.1f}" y="{y - h * .2 + 4:.1f}" width="{(counter - 12) / 3 - 3:.1f}" '
                   f'height="{h * 1.4 - 8:.1f}" rx="1.5" fill="#2b2a27"/>')
    out.append(f'<rect x="{x0}" y="{y}" width="{length}" height="{h}" rx="{h / 2}" fill="url(#armStripe)"/>')
    out.append(f'<rect x="{x0}" y="{y}" width="{length}" height="{h}" rx="{h / 2}" fill="url(#armShade)" stroke="#0b0b0a" stroke-width="1.2"/>')
    r = h * .36
    for i, lx in enumerate(lamp_xs):
        last = i == len(lamp_xs) - 1
        out.append(f'<rect x="{lx - r * 1.25:.1f}" y="{y - r * 2.2:.1f}" width="{r * 2.5:.1f}" height="{r * 2.4:.1f}" rx="{r * .5:.1f}" fill="#0b0b0a"/>')
        out.append(lamp(lx, y - r * 1.0, r * .8, lit=(last and lit_tip), board=False, hood=False))
    return "".join(out)


def housing(cx, y, w, h):
    return (f'<rect x="{cx - w / 2}" y="{y}" width="{w}" height="{h}" rx="5" fill="url(#box)" stroke="#0b0b0a" stroke-width="1.2"/>'
            f'<rect x="{cx - w / 2 + 5}" y="{y + 6}" width="{w - 10}" height="3" rx="1.5" fill="#fff" fill-opacity=".08"/>'
            f'<circle cx="{cx}" cy="{y + h * .34:.1f}" r="{w * .2:.1f}" fill="url(#steel)" stroke="#0b0b0a"/>')


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


def hl_json(text):
    out = []
    for m in JTOKEN.finditer(text):
        string, colon, punct, num, ws = m.groups()
        if string and colon:
            out.append(f'<span class="k">{e(string)}</span><span class="o">{e(colon)}</span>')
        elif string:
            out.append(f'<span class="s">{e(string)}</span>')
        elif punct:
            out.append(f'<span class="o">{e(punct)}</span>')
        elif num:
            out.append(f'<span class="n">{e(num)}</span>')
        else:
            out.append(ws)
    return "".join(out)


def warning_html(ctx):
    """The hook's exact sentence, with the rule names picked out."""
    inner = ctx[len(PREFIX):-len(SUFFIX)]
    return e(PREFIX) + "; ".join(f'<b class="hit">{e(p)}</b>' for p in inner.split("; ")) + e(SUFFIX)


def page(name, body, width):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{name}</title>{FONTS}'
            f'<style>{CSS}</style></head><body style="width:{width}px">{body}</body></html>')


# ---- hero --------------------------------------------------------------------

def passed_strip(items, size=15, gap=34):
    return "".join(f'<span style="display:inline-flex;align-items:baseline;gap:9px;margin-right:{gap}px;white-space:nowrap">'
                   f'<span class="term" style="font-size:{size}px">{hl_shell(r["cmd"])}</span>'
                   f'<span class="mono" style="font-size:{size - 3}px;color:var(--dim)">exit {r["code"]}</span></span>' for r in items)


def tri(size, color=RED):
    return f'<svg width="{size}" height="{size * .92:.1f}" viewBox="0 0 13 12"><path d="M6.5 0 13 12H0z" fill="{color}"/></svg>'


def lane(w, y, h, dash, gap):
    """A painted, dashed lane line along the road strip, so the strip reads as the road."""
    return (f'<path d="M-{dash / 2:.0f} {y} H{w}" stroke="{WHITE}" stroke-opacity=".62" stroke-width="{h}" '
            f'stroke-dasharray="{dash} {gap}"/>')


def dimension(x0, x1, y, h, color=YELLOW, sw=1.5):
    """An engineering-drawing dimension line between two extension lines."""
    return (f'<path d="M{x0} {y - h / 2} v{h} M{x1} {y - h / 2} v{h} M{x0 + 2} {y} H{x1 - 2}" stroke="{color}" stroke-width="{sw}" fill="none"/>'
            f'<path d="M{x0 + 9} {y - 5} l-7 5 7 5 M{x1 - 9} {y - 5} l7 5 -7 5" fill="none" stroke="{color}" stroke-width="{sw}"/>')


def crossing(px, top, ground, cb, pod, house_y, arm_y, arm_len, arm_h, lamps, counter, post_w=20, hazard=70):
    """The whole assembly: crossbuck, lamp pod, LOOK plate (HTML, placed by the caller), mechanism, arm."""
    cb_w, cb_h = cb
    pod_r, pod_gap = pod
    return (post(px, top, ground, w=post_w, hazard=hazard)
            + crossbuck(px, top, cb_w, cb_h)
            + lamp_pod(px, top + cb_w * .62, pod_r, pod_gap)
            + housing(px, house_y, post_w * 2.8, arm_h * 2.7)
            + arm(px + post_w * 1.1, arm_y, arm_len, arm_h, lamp_xs=lamps, counter=counter))


def look_plate(cx, top, w, h, big, small):
    return (f'<div class="plate abs" style="left:{cx - w / 2}px;top:{top}px;width:{w}px;height:{h}px;display:flex;'
            f'flex-direction:column;align-items:center;justify-content:center">{bolts(round(w * .052), round(w * .042))}'
            f'<div class="cond" style="font-weight:800;font-size:{big}px;line-height:1;letter-spacing:.06em">LOOK</div>'
            f'<div style="margin-top:{big * .1:.0f}px;font-weight:600;font-size:{small}px">when lights show</div></div>')


def hero(mobile=False):
    flag, allowed = HERO_FLAGGED[0], HERO_ALLOWED
    if not mobile:
        W, H, px, ground = 1100, 790, 176, 704
        arm_x, arm_len = px + 22, 664
        tip = arm_x + arm_len
        art = svg(W, H,
                  f'<circle cx="122" cy="296" r="340" fill="url(#wash)"/><ellipse cx="{px}" cy="{ground + 30}" rx="260" ry="40" fill="url(#wash)"/>'
                  f'<rect x="0" y="{ground}" width="{W}" height="{H - ground}" fill="#1c1c1a"/>'
                  f'<rect x="0" y="{ground}" width="{W}" height="1.5" fill="#fff" fill-opacity=".07"/>'
                  + lane(W, ground + 17, 5, 38, 26)
                  + crossing(px, 140, ground, (232, 40), (30, 110), 456, 478, arm_len, 34, (420, 640, 846), 74)
                  + dimension(tip + 6, W - 36, 495, 40))
        look = look_plate(px, 348, 172, 86, 34, 14.5)
        title = (f'<div class="abs" style="left:420px;top:96px;width:660px">'
                 f'<div class="cond" style="font-weight:800;font-size:98px;line-height:.9;letter-spacing:-.005em;white-space:nowrap">agent-guardrails</div>'
                 f'<div style="margin-top:26px;font-weight:500;font-size:31px;line-height:1.22;letter-spacing:-.005em;max-width:600px">'
                 f'A level crossing between your coding agent and your machine.</div>'
                 f'<div style="margin-top:18px;font-size:18.5px;line-height:1.5;color:var(--grey);max-width:570px">'
                 f'Most commands cross in silence. A risky one gets the barrier, the lights and a sign in the agent&rsquo;s '
                 f'own context. Nothing is locked.</div></div>')
        gapnote = (f'<div class="abs" style="left:{tip + 8}px;top:530px;width:{W - tip - 44}px">'
                   f'<div class="label yel">Half barrier</div>'
                   f'<div style="margin-top:8px;font-size:14.5px;line-height:1.42;color:#d9d5ca">The gap is the design. '
                   f'The hook warns; your permission rules decide.</div></div>')
        held = (f'<div class="abs" style="left:258px;top:542px;width:580px">'
                f'<div class="label red" style="display:flex;align-items:center;gap:10px">{tri(13)}Held at the barrier</div>'
                f'<div class="term" style="margin-top:12px;font-size:44px;line-height:1;letter-spacing:-.01em">{hl_shell(flag["cmd"])}</div>'
                f'<div class="mono" style="margin-top:15px;font-size:14.5px;line-height:1.55;color:var(--grey)">{warning_html(flag["context"])}</div>'
                f'</div>')
        road = (f'<div class="abs" style="left:56px;top:{ground + 40}px;right:24px;display:flex;align-items:baseline;gap:22px">'
                f'<span class="label" style="color:var(--dim);white-space:nowrap">Crossed in silence</span>'
                f'<span style="white-space:nowrap">{passed_strip(allowed, 13.5, 24)}</span></div>')
        return f'<div class="field" style="width:{W}px;height:{H}px">{art}{look}{title}{gapnote}{held}{road}</div>'
    W, H, px, ground = 540, 1000, 92, 904
    arm_x, arm_len = px + 17.6, 342
    tip = arm_x + arm_len
    art = svg(W, H,
              f'<circle cx="70" cy="520" r="270" fill="url(#wash)"/>'
              f'<rect x="0" y="{ground}" width="{W}" height="{H - ground}" fill="#1c1c1a"/>'
              f'<rect x="0" y="{ground}" width="{W}" height="1.5" fill="#fff" fill-opacity=".07"/>'
              + lane(W, ground + 13, 4, 26, 18)
              + crossing(px, 372, ground, (160, 28), (21, 76), 628, 646, arm_len, 26, (246, 350, 440), 54, post_w=16, hazard=54)
              + dimension(tip + 5, W - 18, 659, 30, sw=1.3))
    look = look_plate(px, 553, 120, 62, 24, 11)
    title = (f'<div class="abs" style="left:36px;top:44px;width:470px">'
             f'<div class="cond" style="font-weight:800;font-size:78px;line-height:.88">agent-<br>guardrails</div>'
             f'<div style="margin-top:22px;font-weight:500;font-size:25px;line-height:1.24">A level crossing between your coding agent and your machine.</div></div>')
    side = (f'<div class="abs" style="left:212px;top:430px;width:296px;font-size:17px;line-height:1.5;color:var(--grey)">'
            f'Most commands cross in silence. A risky one gets the barrier, the lights and a sign in the agent&rsquo;s own context.'
            f'<div style="margin-top:12px;color:var(--white);font-weight:600">Nothing is locked.</div></div>')
    gapnote = (f'<div class="abs" style="left:{tip + 2:.0f}px;top:684px;width:{W - tip - 10:.0f}px">'
               f'<div class="label yel" style="font-size:11px;line-height:1.3">Half<br>barrier</div></div>')
    held = (f'<div class="abs" style="left:146px;top:712px;width:366px">'
            f'<div class="label red" style="display:flex;align-items:center;gap:8px;font-size:12px">{tri(11)}Held at the barrier</div>'
            f'<div class="term" style="margin-top:10px;font-size:30px;line-height:1">{hl_shell(flag["cmd"])}</div>'
            f'<div class="mono" style="margin-top:12px;font-size:12.5px;line-height:1.55;color:var(--grey)">{warning_html(flag["context"])}</div></div>')
    road = (f'<div class="abs" style="left:36px;right:16px;top:{ground + 30}px">'
            f'<div class="label" style="color:var(--dim);font-size:11px">Crossed in silence</div>'
            f'<div style="margin-top:9px;white-space:nowrap">{passed_strip(allowed[:3], 12.5, 16)}</div></div>')
    return f'<div class="field" style="width:{W}px;height:{H}px">{art}{look}{title}{side}{gapnote}{held}{road}</div>'


# ---- numbers: lineside boards -----------------------------------------------

STATS = [
    (str(len(RULES)), "Risk rules", "narrow, high-signal"),
    (str(TESTS), "Tests passing", f"{GUARD_N} hook + {LAUNCH_N} launcher"),
    (str(len([f for f in os.listdir(os.path.join(ROOT, ".claude", "agents")) if f.endswith(".md")])), "Agents", "reviewer and verifier"),
    ("4", "Verdicts", "per checked claim"),  # checked against verifier.md below (VERDICTS)
    (str(SUBCOMMANDS), "Subcommands", "never get the bypass"),
    ("0", "Hard blocks", "the hook only warns"),
]


def track(x0, x1, y, sleeper=7, pitch=22):
    """Plain line in elevation: ballast, sleeper ends and a rail with a head."""
    sl = "".join(f'<rect x="{x:.1f}" y="{y + 7}" width="{sleeper * 2.2:.1f}" height="{sleeper}" rx="1" fill="#2c2a26"/>'
                 for x in [x0 + i * pitch for i in range(int((x1 - x0) / pitch) + 1)])
    return (f'<rect x="{x0}" y="{y + 10}" width="{x1 - x0}" height="16" fill="#1d1c1a"/>{sl}'
            f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="7" fill="url(#steelH)"/>'
            f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="1.5" fill="#d8d5cc" fill-opacity=".7"/>')


def board(cx, top, w, h, ground, num, kind="yellow", num_size=80):
    """A lineside board on two posts."""
    legs = "".join(f'<rect x="{cx + dx - 3}" y="{top + h - 6}" width="6" height="{ground - top - h + 6}" fill="url(#steel)"/>'
                   for dx in (-w * .28, w * .28))
    face = YELLOW if kind == "yellow" else WHITE
    return (legs + f'<rect x="{cx - w / 2}" y="{top}" width="{w}" height="{h}" rx="7" fill="{face}" stroke="#0b0b0a" stroke-width="1"/>'
            f'<rect x="{cx - w / 2 + 5}" y="{top + 5}" width="{w - 10}" height="{h - 10}" rx="4" fill="none" stroke="{INK}" stroke-width="2.5"/>'
            f'<text x="{cx}" y="{top + h / 2 + num_size * .35:.1f}" text-anchor="middle" font-family="Barlow Condensed" font-weight="800" '
            f'font-size="{num_size}" fill="{INK}">{num}</text>'
            + "".join(f'<circle cx="{cx + dx}" cy="{top + 12}" r="2.6" fill="#55524b"/>' for dx in (-w * .28, w * .28)))


def numbers(mobile=False):
    if not mobile:
        W, H, G = 1100, 300, 186
        cols = [92 + i * 183.2 for i in range(6)]
        boards = "".join(board(x, 36, 118, 100, G, n, "white" if n == "0" and i == 5 else "yellow")
                         for i, (x, (n, _, _)) in enumerate(zip(cols, STATS)))
        extra = (f'<rect x="{cols[5] - 50}" y="140" width="100" height="26" rx="4" fill="{WHITE}" stroke="#0b0b0a"/>'
                 f'<rect x="{cols[5] - 47}" y="143" width="94" height="20" rx="2.5" fill="none" stroke="{INK}" stroke-width="1.5"/>'
                 f'<text x="{cols[5]}" y="158" text-anchor="middle" font-family="Barlow Condensed" font-weight="800" font-size="14" '
                 f'letter-spacing="1.4" fill="{INK}">WARNS ONLY</text>')
        art = svg(W, H, track(0, W, G) + boards + extra)
        caps = "".join(f'<div class="abs" style="left:{x - 88:.0f}px;width:176px;top:{G + 42}px;text-align:center">'
                       f'<div class="cond" style="font-weight:700;font-size:21px;letter-spacing:.05em;text-transform:uppercase">{l}</div>'
                       f'<div style="margin-top:4px;font-size:14.5px;color:var(--grey)">{sub}</div></div>'
                       for x, (_, l, sub) in zip(cols, STATS))
        return f'<div class="field" style="width:{W}px;height:{H}px">{art}{caps}</div>'
    W, rowh, top0 = 540, 262, 34
    H = top0 + rowh * 3 + 6
    out, caps = [], []
    for i, (n, l, sub) in enumerate(STATS):
        cx = 140 + (i % 2) * 260
        y = top0 + (i // 2) * rowh
        G = y + 150
        if i % 2 == 0:
            out.append(track(0, W, G, pitch=20))
        out.append(board(cx, y, 112, 96, G, n, "white" if i == 5 else "yellow", 76))
        if i == 5:
            out.append(f'<rect x="{cx - 46}" y="{y + 102}" width="92" height="24" rx="4" fill="{WHITE}" stroke="#0b0b0a"/>'
                       f'<rect x="{cx - 43}" y="{y + 105}" width="86" height="18" rx="2.5" fill="none" stroke="{INK}" stroke-width="1.5"/>'
                       f'<text x="{cx}" y="{y + 119}" text-anchor="middle" font-family="Barlow Condensed" font-weight="800" font-size="13" '
                       f'letter-spacing="1.3" fill="{INK}">WARNS ONLY</text>')
        caps.append(f'<div class="abs" style="left:{cx - 120}px;width:240px;top:{G + 40}px;text-align:center">'
                    f'<div class="cond" style="font-weight:700;font-size:21px;letter-spacing:.05em;text-transform:uppercase">{l}</div>'
                    f'<div style="margin-top:3px;font-size:15px;color:var(--grey)">{sub}</div></div>')
    return f'<div class="field" style="width:{W}px;height:{H}px">{svg(W, H, "".join(out))}{"".join(caps)}</div>'


# ---- rules: the rulebook -----------------------------------------------------

def _doc(x, y, a):
    return (f'<g transform="translate({x} {y}) rotate({a})"><path d="M-7 -9 h9 l5 5 v13 h-14z" fill="{INK}"/>'
            f'<path d="M2 -9 v5 h5" fill="none" stroke="{WHITE}" stroke-width="1.4"/></g>')


PICTOS = [
    # recursive force-delete: files tumbling off a cliff, after the falling-rocks sign
    f'<path d="M73 50 h9 l15 43 h-24z" fill="{INK}"/>'
    + _doc(62, 58, -22) + _doc(50, 71, 28) + _doc(41, 86, -12)
    + f'<path d="M66 69 l4 3 M56 83 l5 2" stroke="{INK}" stroke-width="2" stroke-linecap="round"/>',
    # force-push: history shoved off its line
    f'<path d="M60 92 V64" stroke="{INK}" stroke-width="4"/><circle cx="60" cy="88" r="5.5" fill="{INK}"/>'
    f'<circle cx="60" cy="72" r="5.5" fill="{INK}"/><circle cx="60" cy="54" r="5.5" fill="none" stroke="{INK}" stroke-width="2" stroke-dasharray="3 2.4"/>'
    f'<circle cx="77" cy="50" r="6" fill="{INK}"/><path d="M36 54 h13" stroke="{INK}" stroke-width="5"/>'
    f'<path d="M47 46 l9 8 -9 8z" fill="{INK}"/>',
    # curl | sh: a download falling straight into a prompt
    f'<path d="M47 52 a7 7 0 0 1 8 -8 a9 9 0 0 1 16 2 a6 6 0 0 1 2 12 h-24 a6 6 0 0 1 -2 -6z" fill="{INK}"/>'
    f'<path d="M60 60 v11" stroke="{INK}" stroke-width="4"/><path d="M54 68 l6 7 6 -7z" fill="{INK}"/>'
    f'<rect x="44" y="77" width="32" height="18" rx="2.5" fill="{INK}"/>'
    f'<path d="M49 82 l5 4 -5 4" fill="none" stroke="{WHITE}" stroke-width="2"/><path d="M57 90.5 h8" stroke="{WHITE}" stroke-width="2"/>',
    # world-writable: an open padlock
    f'<rect x="46" y="68" width="28" height="24" rx="3.5" fill="{INK}"/>'
    f'<path d="M52 68 v-10 a8 8 0 0 1 16 0 v2" fill="none" stroke="{INK}" stroke-width="4.5" transform="rotate(-24 68 60) translate(10 -6)"/>'
    f'<circle cx="60" cy="78" r="3" fill="{WHITE}"/><path d="M60 80 v6" stroke="{WHITE}" stroke-width="2.4"/>',
    # credentials: a key
    f'<circle cx="47" cy="74" r="10" fill="{INK}"/><circle cx="44.5" cy="74" r="3.6" fill="{WHITE}"/>'
    f'<path d="M55 74 h29" stroke="{INK}" stroke-width="5"/><path d="M76 74 v8 M82 74 v6" stroke="{INK}" stroke-width="4"/>',
    # raw disk write: a platter and its head
    f'<circle cx="57" cy="76" r="17" fill="{INK}"/><circle cx="57" cy="76" r="4" fill="{WHITE}"/>'
    f'<circle cx="57" cy="76" r="11" fill="none" stroke="{WHITE}" stroke-width=".9" stroke-opacity=".55"/>'
    f'<path d="M83 56 L66 79" stroke="{INK}" stroke-width="4" stroke-linecap="round"/><circle cx="83" cy="56" r="4" fill="{INK}"/>'
    f'<path d="M66 79 l-2 -1.5" stroke="{WHITE}" stroke-width="2"/>',
    # write to a block device: a redirect into a drive
    f'<path d="M37 66 l10 9 -10 9" fill="none" stroke="{INK}" stroke-width="4.5" stroke-linejoin="round"/>'
    f'<rect x="53" y="63" width="30" height="24" rx="2.5" fill="{INK}"/>'
    + "".join(f'<rect x="{56 + i * 6}" y="88" width="3" height="5" fill="{INK}"/>' for i in range(5))
    + f'<rect x="58" y="68" width="10" height="6" rx="1" fill="{WHITE}" fill-opacity=".85"/>',
]
TOKENS = ["rm -rf", "push --force", "curl | sh", "chmod 777", "~/.ssh", "dd of=/dev", "> /dev/nvme"]
assert len(PICTOS) == len(TOKENS) == len(RULES)


def warn_sign(picto, w=118):
    h = w * .9
    return (f'<svg width="{w}" height="{h:.1f}" viewBox="0 0 120 108">'
            f'<path d="M60 9 L113 101 H7 Z" fill="#c8271e" stroke="#c8271e" stroke-width="14" stroke-linejoin="round"/>'
            f'<path d="M60 26 L99 93 H21 Z" fill="{WHITE}" stroke="{WHITE}" stroke-width="3" stroke-linejoin="round"/>'
            f'<g transform="translate(0 4)">{picto}</g></svg>')


def supp_plate(token, size=13.5):
    return (f'<div class="mono" style="display:inline-block;margin-top:8px;padding:4px 10px;border-radius:4px;background:{WHITE};'
            f'box-shadow:inset 0 0 0 1.5px {INK};font-weight:700;font-size:{size}px;color:{INK};white-space:nowrap">{e(token)}</div>')


def rule_cell(i, warning, bad, good):
    return (f'<div style="position:relative">'
            f'<div class="mono" style="font-size:11px;letter-spacing:.14em;color:var(--ink-3)">DIAGRAM {i + 1:02d}</div>'
            f'<div style="margin-top:14px;display:flex;flex-direction:column;align-items:flex-start">{warn_sign(PICTOS[i])}'
            f'{supp_plate(TOKENS[i])}</div>'
            f'<div style="margin-top:16px;font-weight:700;font-size:19px;line-height:1.2;letter-spacing:-.005em;min-height:46px">{e(warning)}</div>'
            f'<div class="term wrap" style="margin-top:10px;font-size:12.5px;line-height:1.65">'
            f'<span class="ln" style="margin-bottom:5px"><span style="display:inline-block;width:16px;text-indent:0">{tri(9, "#c8271e")}</span>{hl_shell(bad)}</span>'
            f'<span class="ln"><span style="display:inline-block;width:16px;text-indent:0;color:var(--ink-3)">&#9675;</span>'
            f'<span style="color:var(--ink-2)">{hl_shell(good)}</span></span></div></div>')


def rules(mobile=False):
    multi_cmd = (f'<span class="ln" style="white-space:pre-wrap"><span class="o">$</span> payload=<span class="s">\'{e(MULTI["payload"])}\'</span></span>'
                 f'<span class="ln"><span class="o">$</span> {hl_shell(chr(34).join(["echo ", "$payload", " | sh .claude/hooks/advisory-guard.sh"]))}</span>'
                 f'<span class="ln" style="white-space:pre-wrap;margin-top:6px">{hl_json(MULTI_STDOUT)}</span>')
    if not mobile:
        W = 1100
        head = (f'<div style="display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:18px;'
                f'border-bottom:5px solid var(--ink);box-shadow:0 5px 0 var(--paper),0 7px 0 var(--ink)">'
                f'<div><div class="cond" style="font-weight:800;font-size:66px;line-height:.9;letter-spacing:.005em">THE RULEBOOK</div>'
                f'<div class="mono" style="margin-top:12px;font-size:14px;color:var(--ink-2)">.claude/hooks/advisory-guard.sh &middot; '
                f'{len(RULES)} signs &middot; case-insensitive, matched anywhere</div></div>'
                f'<div style="text-align:right;font-size:14.5px;line-height:1.6;color:var(--ink-2)">'
                f'<div class="label" style="color:var(--ink-3);font-size:12px;margin-bottom:8px">MIT licence &middot; no charge</div>'
                f'<span style="display:inline-flex;align-items:center;gap:7px">{tri(10, "#c8271e")} flags</span>&ensp;'
                f'<span style="color:var(--ink-3)">&#9675;</span> lets through, the near miss<br>Captions are the hook&rsquo;s exact words.</div></div>')
        cells = [rule_cell(i, w, b, g) for i, (w, b, g) in enumerate(rule_rows)]
        last = (f'<div style="display:flex;flex-direction:column;justify-content:flex-end;padding:20px 22px;background:var(--ink);color:var(--white);'
                f'border-radius:6px">'
                f'<div class="cond" style="font-weight:800;font-size:46px;line-height:.95;text-transform:uppercase">Every match<br>still runs.</div>'
                f'<div style="margin-top:14px;font-size:14.5px;line-height:1.5;color:#cfcbc0">The hook adds a warning to the agent&rsquo;s '
                f'context, exits {MULTI["code"]} and sends no permission decision. Your settings decide what runs.</div></div>')
        grid = (f'<div style="margin-top:38px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));column-gap:30px;row-gap:36px">'
                + "".join(cells) + last + '</div>')
        spec = (f'<div style="margin-top:36px;padding-top:18px;border-top:1.5px solid var(--ink)">'
                f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
                f'<div class="cond" style="font-weight:800;font-size:24px;letter-spacing:.04em">SPECIMEN: THREE SIGNS, ONE WARNING</div>'
                f'<div class="mono" style="font-size:13px;color:var(--ink-2)">{hl_shell(MULTI["cmd"])} &nbsp;&rarr;&nbsp; {len(hits(MULTI))} rules, '
                f'exit {MULTI["code"]}</div></div>'
                f'<div class="term field" style="margin-top:14px;padding:16px 20px;border-radius:6px;font-size:13px;line-height:1.65;color:#dedad0">{multi_cmd}</div></div>')
        return f'<div class="paper" style="width:{W}px;padding:50px 54px 54px">{head}{grid}{spec}</div>'
    W = 540
    head = (f'<div style="padding-bottom:14px;border-bottom:4px solid var(--ink);box-shadow:0 4px 0 var(--paper),0 6px 0 var(--ink)">'
            f'<div class="cond" style="font-weight:800;font-size:52px;line-height:.9">THE RULEBOOK</div>'
            f'<div class="mono" style="margin-top:10px;font-size:12.5px;color:var(--ink-2)">advisory-guard.sh &middot; {len(RULES)} signs &middot; every match still runs</div></div>')
    rows = "".join(
        f'<div style="display:grid;grid-template-columns:96px 1fr;column-gap:18px;align-items:center;padding:16px 0;'
        f'{"border-top:1px solid var(--rule)" if i else ""}">'
        f'<div style="display:flex;flex-direction:column;align-items:center">{warn_sign(PICTOS[i], 88)}{supp_plate(TOKENS[i], 11)}</div>'
        f'<div><div class="mono" style="font-size:10.5px;letter-spacing:.14em;color:var(--ink-3)">DIAGRAM {i + 1:02d}</div>'
        f'<div style="margin-top:5px;font-weight:700;font-size:18px;line-height:1.2">{e(w)}</div>'
        f'<div class="term" style="margin-top:7px;font-size:12px;line-height:1.75">'
        f'<span class="ln"><span style="display:inline-block;width:15px">{tri(8, "#c8271e")}</span>{hl_shell(b)}</span>'
        f'<span class="ln"><span style="display:inline-block;width:15px;color:var(--ink-3)">&#9675;</span><span style="color:var(--ink-2)">{hl_shell(g)}</span></span></div></div></div>'
        for i, (w, b, g) in enumerate(rule_rows))
    return f'<div class="paper" style="width:{W}px;padding:38px 34px 26px">{head}<div style="margin-top:10px">{rows}</div></div>'


# ---- agents: the keepers' cabin ----------------------------------------------

REVIEWER_SPECIMEN = [
    ("prompt", "> Use the reviewer agent on the changes in src/sync/."),
    ("blank", ""),
    ("find", "src/sync/queue.py:88", "retry loop never resets `attempt`, so after the first failure every later item gets zero retries."),
    ("find", "src/sync/queue.py:131", "deletes the local copy before the upload is confirmed; a network error loses the file."),
]
VERIFIER_SPECIMEN = [
    ("prompt", '> Verify: "git push --force-with-lease refuses to push if the remote ref moved."'),
    ("blank", ""),
    ("verdict", "CORRECT.", "git-push(1), --force-with-lease: the push is refused unless the remote ref still matches the expected "
                "value (by default the remote-tracking branch). Source: https://git-scm.com/docs/git-push"),
]
AGENT_TOOLS = {}
for _name in ("reviewer", "verifier"):
    _m = re.search(r"^tools: (.*)$", open(os.path.join(ROOT, ".claude", "agents", f"{_name}.md")).read(), re.M)
    AGENT_TOOLS[_name] = [t.strip() for t in _m.group(1).split(",")]
assert AGENT_TOOLS["reviewer"] == ["Read", "Grep", "Glob", "Bash"], AGENT_TOOLS
assert "WebFetch" in AGENT_TOOLS["verifier"] and "WebSearch" in AGENT_TOOLS["verifier"], AGENT_TOOLS
_VER_SRC = open(os.path.join(ROOT, ".claude", "agents", "verifier.md")).read()
VERDICTS = ["CORRECT", "PARTIALLY CORRECT", "FALSE", "COULD NOT VERIFY"]
assert all(v in _VER_SRC for v in VERDICTS)
_REV_SRC = open(os.path.join(ROOT, ".claude", "agents", "reviewer.md")).read()
CHECKS = ["Correctness", "Safety", "Convention drift", "Simplification"]
assert all(f"**{c}**" in _REV_SRC for c in CHECKS)


def cabin(cx, base, name, w=128, keeper=True):
    """A signal box at night: brick base, lit window band, hipped roof, nameboard."""
    bh, wh, rh = 40, 42, 26
    x0 = cx - w / 2
    y_win = base - bh - wh
    bricks = "".join(f'<path d="M{x0 + 4} {base - bh + 8 + k * 8} H{x0 + w - 4}" stroke="#0b0b0a" stroke-opacity=".35" stroke-width="1"/>'
                     for k in range(4))
    mull = "".join(f'<rect x="{x0 + 6 + i * (w - 12) / 5:.1f}" y="{y_win + 4}" width="2.4" height="{wh - 8}" fill="#2a2213"/>'
                   for i in range(1, 5))
    fig = ""
    if keeper:
        fx = cx + (-w * .18 if name == "REVIEWER" else w * .16)
        fig = (f'<circle cx="{fx:.1f}" cy="{y_win + 17}" r="5.2" fill="#2a2213"/>'
               f'<path d="M{fx - 10:.1f} {y_win + wh - 4} q0 -13 10 -13 q10 0 10 13z" fill="#2a2213"/>')
    return (f'<rect x="{x0 - 30}" y="{y_win - 16}" width="{w + 60}" height="{wh + 60}" fill="url(#lampGlowY)" opacity=".55"/>'
            f'<rect x="{x0 + 4}" y="{base - bh}" width="{w - 8}" height="{bh}" fill="#6e3a2a"/>{bricks}'
            f'<rect x="{x0 + w / 2 - 50}" y="{base - bh + 9}" width="100" height="21" rx="2" fill="#0c0c0b" stroke="{WHITE}" stroke-width="1.3"/>'
            f'<text x="{cx}" y="{base - bh + 24.5}" text-anchor="middle" font-family="Barlow Condensed" font-weight="800" font-size="14.5" '
            f'letter-spacing="1.6" fill="{WHITE}">{name}</text>'
            f'<rect x="{x0}" y="{y_win}" width="{w}" height="{wh}" fill="#ffd257"/>'
            f'<rect x="{x0}" y="{y_win}" width="{w}" height="{wh}" fill="url(#winShade)"/>{fig}{mull}'
            f'<rect x="{x0}" y="{y_win}" width="{w}" height="{wh}" fill="none" stroke="#3a3530" stroke-width="5"/>'
            f'<path d="M{x0 - 10} {y_win} L{x0 + 22} {y_win - rh} H{x0 + w - 22} L{x0 + w + 10} {y_win} Z" fill="#2c2b28" stroke="#0b0b0a"/>'
            f'<path d="M{x0 - 10} {y_win} H{x0 + w + 10}" stroke="#55534d" stroke-width="2"/>')


EXTRA_DEFS = """<defs><linearGradient id="winShade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".25"/>
<stop offset=".5" stop-color="#ffb800" stop-opacity="0"/><stop offset="1" stop-color="#b86a00" stop-opacity=".35"/></linearGradient>
<radialGradient id="lampGlowY"><stop offset="0" stop-color="#ffc61a" stop-opacity=".35"/><stop offset="1" stop-color="#ffc61a" stop-opacity="0"/></radialGradient></defs>"""


def joint(x, y, color):
    return f'<path d="M{x} {y - 7} v14" stroke="{color}" stroke-width="1.6"/>'


def specimen_lines(lines, label_color):
    out = []
    for item in lines:
        if item[0] == "prompt":
            out.append(f'<span class="ln" style="color:var(--grey)">{e(item[1])}</span>')
        elif item[0] == "blank":
            out.append('<span class="ln" style="height:8px"></span>')
        else:
            _, head, rest = item
            out.append(f'<span class="ln" style="margin-bottom:6px"><b style="color:{label_color};font-weight:700">{e(head)}</b> '
                       f'<span style="color:#dedad0">{e(rest)}</span></span>')
    return "".join(out)


def stamp(text, color=RED, size=15, angle=-9):
    return (f'<div class="cond" style="display:inline-block;transform:rotate({angle}deg);padding:3px 10px 2px;border:2.5px solid {color};'
            f'border-radius:4px;color:{color};font-weight:800;font-size:{size}px;letter-spacing:.2em;opacity:.92">{text}</div>')


def duty_card(name, line, rows, tools, specimen):
    tool_txt = " &middot; ".join(e(t) for t in tools)
    return (f'<div class="plate" style="padding:26px 28px 26px">{bolts(12, 8)}'
            f'<div class="cond" style="font-weight:800;font-size:30px;letter-spacing:.06em">{name}</div>'
            f'<div style="margin-top:6px;font-size:16px;line-height:1.45;color:#dedad0">{line}</div>'
            f'<div class="mono" style="margin-top:6px;font-size:11.5px;color:var(--dim)">tools: {tool_txt}</div>'
            f'<div style="margin-top:14px">{rows}</div>'
            f'<div style="position:relative;margin-top:16px;padding:14px 16px 10px;border-radius:6px;background:#1b1b19;'
            f'box-shadow:inset 0 0 0 1px rgba(242,240,234,.08)">'
            f'<div class="label" style="color:var(--dim);font-size:11px;margin-bottom:9px">Report format, as the agent file asks</div>'
            f'<div class="term wrap" style="font-size:12.5px;line-height:1.55">{specimen}</div>'
            f'<div class="abs" style="right:12px;top:-12px">{stamp("SPECIMEN", RED, 13)}</div></div></div>')


def check_rows():
    return ('<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">' + "".join(
        f'<span style="display:inline-flex;align-items:center;gap:8px;height:28px;padding:0 11px 0 4px;border-radius:5px;'
        f'background:#232321;font-size:14px;font-weight:600"><b class="mono" style="display:grid;place-items:center;width:20px;height:20px;'
        f'border-radius:3px;background:{YELLOW};color:{INK};font-size:12px">{i + 1}</b>{c}</span>' for i, c in enumerate(CHECKS))
        + '</div><div class="mono" style="margin-top:10px;font-size:12px;color:var(--grey)">in that order &middot; every finding cites file:line '
          '&middot; nothing to flag: one line</div>')


def verdict_rows():
    tone = {"CORRECT": WHITE, "PARTIALLY CORRECT": YELLOW, "FALSE": "#ff5a48", "COULD NOT VERIFY": "#a5a297"}
    return ('<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">' + "".join(
        f'<span class="cond" style="display:inline-flex;align-items:center;height:28px;padding:0 11px;border-radius:5px;'
        f'box-shadow:inset 0 0 0 1.5px {tone[v]};color:{tone[v]};font-size:15px;font-weight:700;letter-spacing:.08em">{v}</span>' for v in VERDICTS)
        + '</div><div class="mono" style="margin-top:10px;font-size:12px;color:var(--grey)">one per claim &middot; cites the URL, file:line '
          'or command it checked</div>')


def agents(mobile=False):
    rev = duty_card("REVIEWER", "Reads the actual diff once the work is functionally complete. Did not write a line of it.",
                    check_rows(), AGENT_TOOLS["reviewer"], specimen_lines(REVIEWER_SPECIMEN, YELLOW))
    ver = duty_card("VERIFIER", "Checks each claim against docs, <span class=\"mono\" style=\"font-size:14px\">--help</span> and "
                    "source. Repetition is not evidence.", verdict_rows(), AGENT_TOOLS["verifier"],
                    specimen_lines(VERIFIER_SPECIMEN, WHITE))
    if not mobile:
        W, H = 1100, 1062
        y = 352           # main line
        segs = [("SPEC &amp; LIMITS", "templates/CLAUDE.md", 64, 252, WHITE), ("AGENT BUILDS", "the main session", 258, 452, WHITE),
                (None, None, 458, 690, YELLOW), (None, None, 696, 900, WHITE), ("SHIP", "when both are clean", 906, 1036, WHITE)]
        segs_lbl = {906: 920}
        lines = "".join(f'<path d="M{a} {y} H{b}" stroke="{c}" stroke-width="5" stroke-linecap="butt"/>' for _, _, a, b, c in segs)
        joints = "".join(joint(a - 3, y, "#5c5a54") for _, _, a, _, _ in segs[1:])
        loop_y = y + 84
        loop = (f'<path d="M690 {y + 4} L740 {loop_y} M900 {y + 4} L850 {loop_y} M850 {loop_y} H460 L410 {y + 4}" fill="none" '
                f'stroke="{YELLOW}" stroke-width="2.6" stroke-dasharray="9 7"/>'
                + "".join(f'<path d="M{cx + 6} {loop_y - 7} l-8 7 8 7" fill="none" stroke="{YELLOW}" stroke-width="2.8" '
                          f'stroke-linejoin="round"/>' for cx in (560, 650, 790)))
        sigs = "".join(f'<path d="M{x} {y - 6} v-26 h-12" stroke="{WHITE}" stroke-width="2" fill="none"/>'
                       f'<circle cx="{x - 19}" cy="{y - 32}" r="7" fill="url(#lensOn)"/><circle cx="{x - 19}" cy="{y - 32}" r="20" fill="url(#glow)"/>'
                       for x in (684, 894))
        train = (f'<rect x="512" y="{y - 9}" width="118" height="18" rx="3" fill="{YELLOW}"/>'
                 f'<text x="571" y="{y + 4.5}" text-anchor="middle" font-family="Red Hat Mono" font-weight="700" font-size="11.5" fill="{INK}">your change</text>')
        art = svg(W, H, EXTRA_DEFS + f'<rect x="40" y="172" width="{W - 80}" height="330" rx="10" fill="#0e0e0d" stroke="#2c2b28"/>'
                  + cabin(574, y - 26, "REVIEWER") + cabin(798, y - 26, "VERIFIER") + lines + joints + loop + sigs + train
                  + f'<path d="M1036 {y - 14} v28" stroke="{WHITE}" stroke-width="5"/>')
        labels = "".join(f'<div class="abs" style="left:{segs_lbl.get(a, a)}px;width:{b - a}px;top:{y - 62}px">'
                         f'<div class="cond" style="font-weight:700;font-size:16px;letter-spacing:.1em">{t}</div>'
                         f'<div class="mono" style="margin-top:3px;font-size:11.5px;color:var(--grey)">{s_}</div></div>'
                         for t, s_, a, b, _ in segs if t)
        labels += (f'<div class="abs" style="left:478px;width:200px;top:{y + 16}px"><div class="mono" style="font-size:11.5px;color:var(--grey)">reads the diff</div></div>'
                   f'<div class="abs" style="left:736px;width:160px;top:{y + 16}px"><div class="mono" style="font-size:11.5px;color:var(--grey)">checks the claims</div></div>'
                   f'<div class="abs" style="left:330px;width:520px;top:{loop_y + 12}px;text-align:center">'
                   f'<span class="label yel" style="font-size:12.5px">Findings go back to the builder, then get checked again</span></div>'
                   f'<div class="abs label" style="left:64px;top:194px;color:var(--dim);font-size:12px">Keepers&rsquo; panel &middot; .claude/agents/</div>')
        head = (f'<div class="abs" style="left:54px;top:44px;right:54px">'
                f'<div class="cond" style="font-weight:800;font-size:60px;line-height:.92;white-space:nowrap">Nobody marks their own homework.</div>'
                f'<div style="margin-top:12px;font-size:17px;color:var(--grey)">Two keepers, each in a fresh context. Neither one wrote the change it checks.</div></div>')
        cards = f'<div class="abs" style="left:40px;right:40px;top:528px;display:grid;grid-template-columns:1fr 1fr;gap:20px">{rev}{ver}</div>'
        return f'<div class="field" style="width:{W}px;height:{H}px">{art}{labels}{head}{cards}</div>'
    W = 540
    x = 172
    stops = [("SPEC &amp; LIMITS", "templates/CLAUDE.md", 60, 150), ("AGENT BUILDS", "the main session", 156, 250),
             ("REVIEWER", "reads the diff", 256, 430), ("VERIFIER", "checks the claims", 436, 610), ("SHIP", "when both are clean", 616, 690)]
    cols = [WHITE, WHITE, YELLOW, WHITE, WHITE]
    lines = "".join(f'<path d="M{x} {a} V{b}" stroke="{c}" stroke-width="5"/>' for (_, _, a, b), c in zip(stops, cols))
    loop = (f'<path d="M{x + 4} 430 L{x + 60} 470 M{x + 4} 610 L{x + 60} 570 M{x + 60} 570 V250 L{x + 4} 210" fill="none" '
            f'stroke="{YELLOW}" stroke-width="2.4" stroke-dasharray="8 6"/>')
    train = (f'<rect x="{x - 9}" y="300" width="18" height="96" rx="3" fill="{YELLOW}"/>')
    art = svg(W, 720, EXTRA_DEFS + f'<rect x="30" y="34" width="{W - 60}" height="680" rx="10" fill="#0e0e0d" stroke="#2c2b28"/>'
              + cabin(416, 372, "REVIEWER", 120) + cabin(416, 552, "VERIFIER", 120) + lines + loop + train
              + f'<path d="M{x - 14} 690 h28" stroke="{WHITE}" stroke-width="5"/>')
    labels = "".join(f'<div class="abs" style="left:{x - 136}px;width:122px;top:{a + 6}px;text-align:right">'
                     f'<div class="cond" style="font-weight:700;font-size:14px;letter-spacing:.08em;line-height:1.1">{t}</div>'
                     f'<div class="mono" style="margin-top:3px;font-size:10px;color:var(--grey);line-height:1.3">{s_}</div></div>'
                     for t, s_, a, b in stops)
    labels += (f'<div class="abs" style="left:{x + 70}px;top:{372 - 108 - 4}px;width:96px"><span class="label yel" style="font-size:10.5px;line-height:1.3">'
               f'Findings go<br>back, then get<br>checked again</span></div>')
    head = (f'<div style="padding:42px 34px 18px"><div class="cond" style="font-weight:800;font-size:46px;line-height:.92">Nobody marks<br>their own homework.</div>'
            f'<div style="margin-top:12px;font-size:16px;line-height:1.5;color:var(--grey)">Two keepers, each in a fresh context. Neither wrote the change it checks.</div></div>')
    diagram = f'<div style="position:relative;height:740px">{art}{labels}</div>'
    cards = f'<div style="padding:0 20px 24px;display:grid;gap:18px">{rev}{ver}</div>'
    return f'<div class="field" style="width:{W}px">{head}{diagram}{cards}</div>'


# ---- launcher: the TTY-only gate --------------------------------------------

LAUNCH_CASES = []
for _l in LAUNCH_LINES:
    _m = re.match(r"ok\s+(.*?)\s{2,}ARGS:(.*)$", _l)
    if _m:
        LAUNCH_CASES.append((_m.group(1), _m.group(2)))
assert len(LAUNCH_CASES) == LAUNCH_N, LAUNCH_CASES

GATES = [
    ("Terminal on both ends?", "[ -t 0 ] && [ -t 1 ]", "pipes, cron, hooks and CI stop here"),
    ("No print or permission flag?", "-p --print --permission-mode", "your own flag always wins"),
    ("First word not a subcommand?", f"mcp  attach  stop &hellip; {SUBCOMMANDS} in all", "a subcommand stays a subcommand"),
]


def gate_plate(i, q, code, note, w, h=126):
    return (f'<div class="plate yellow" style="width:{w}px;height:{h}px;padding:14px 16px 13px">{bolts(8, 6)}'
            f'<div style="display:flex;gap:10px;align-items:baseline"><span class="cond" style="font-weight:800;font-size:26px;line-height:.9">{i}</span>'
            f'<span class="cond" style="font-weight:800;font-size:18px;line-height:1.05;letter-spacing:.02em;text-transform:uppercase">{q}</span></div>'
            f'<div class="mono" style="margin-top:8px;font-size:11.5px;font-weight:600;white-space:nowrap">{code}</div>'
            f'<div style="margin-top:4px;font-size:12.5px;color:#4a3c08">{note}</div></div>')


def trial_rows(size=13, compact=False):
    rows = []
    for label, args in LAUNCH_CASES:
        bypass = "--dangerously-skip-permissions" in args
        shown = e(args).replace("--dangerously-skip-permissions", '<b class="yel" style="font-weight:700">--dangerously-skip-permissions</b>') or '<span class="mut">(none)</span>'
        tag = (f'<span class="cond" style="font-weight:800;font-size:{size}px;letter-spacing:.12em;color:{INK if bypass else WHITE};'
               f'background:{YELLOW if bypass else "transparent"};box-shadow:inset 0 0 0 1.5px {YELLOW if bypass else "#55534d"};'
               f'padding:2px 8px;border-radius:3px;white-space:nowrap">{"BYPASS" if bypass else "AS TYPED"}</span>')
        if compact:
            rows.append(f'<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 0;border-top:1px solid #2a2a27">'
                        f'<span style="font-size:14px;color:#dedad0">{e(label)}</span>{tag}</div>')
        else:
            rows.append(f'<div style="display:grid;grid-template-columns:28px 250px 1fr 110px;align-items:center;padding:8px 0;border-top:1px solid #2a2a27">'
                        f'<span class="mono" style="font-size:{size}px;color:var(--dim)">ok</span>'
                        f'<span class="mono" style="font-size:{size}px;color:#dedad0">{e(label)}</span>'
                        f'<span class="mono" style="font-size:{size}px;color:var(--grey)">ARGS: {shown}</span>'
                        f'<span style="text-align:right">{tag}</span></div>')
    return "".join(rows)


def launcher(mobile=False):
    if not mobile:
        W, H = 1100, 930
        y, low = 404, 504
        xs = [196, 436, 676]
        main = (f'<path d="M150 {y} H{xs[2] + 26}" stroke="{WHITE}" stroke-width="5"/>'
                f'<path d="M{xs[2] + 26} {y} H1044" stroke="{YELLOW}" stroke-width="5"/>'
                f'<path d="M{xs[0] + 6 + (low - y)} {low} H1044" stroke="{WHITE}" stroke-width="5"/>'
                + "".join(f'<path d="M{x + 6} {y + 2} L{x + 6 + (low - y)} {low}" stroke="{WHITE}" stroke-width="5" fill="none"/>' for x in xs)
                + f'<path d="M1044 {y - 16} v32 M1044 {low - 16} v32" stroke="{WHITE}" stroke-width="5"/>')
        sigs = "".join(f'<rect x="{x - 2.5}" y="{y - 34}" width="5" height="30" fill="url(#steel)"/>'
                       f'<rect x="{x - 11}" y="{y - 74}" width="22" height="42" rx="11" fill="#0b0b0a" stroke="{WHITE}" stroke-width="1.4"/>'
                       + lamp(x, y - 63, 6.5, lit=False, board=False, hood=False) + lamp(x, y - 43, 6.5, lit=False, amber=True, board=False, hood=False)
                       for x in xs)
        nos = "".join(f'<text x="{x + 56}" y="{y + 44}" font-family="Barlow Condensed" font-weight="800" font-size="15" letter-spacing="2" fill="#a5a297">NO</text>'
                      for x in xs)
        yes = "".join(f'<text x="{x + 22}" y="{y - 12}" font-family="Barlow Condensed" font-weight="800" font-size="15" letter-spacing="2" fill="#a5a297">YES</text>'
                      for x in xs)
        sign_post = f'<rect x="965" y="250" width="6" height="{y - 254}" fill="url(#steel)"/>'
        art = svg(W, H, f'<rect x="40" y="168" width="{W - 80}" height="392" rx="10" fill="#0e0e0d" stroke="#2c2b28"/>' + sign_post + main + sigs + nos + yes)
        plates = "".join(f'<div class="abs" style="left:{x - 116}px;top:190px">{gate_plate(i + 1, q, c, n, 232)}</div>'
                         for i, (x, (q, c, n)) in enumerate(zip(xs, GATES)))
        ends = (f'<div class="abs mono" style="left:58px;top:{y - 11}px;font-size:15px"><span class="o">$</span> <span class="c">claude</span></div>'
                f'<div class="abs" style="left:893px;width:150px;top:206px">'
                f'<div class="plate white" style="padding:10px 12px;text-align:center">{bolts(6, 5)}'
                f'<div class="cond" style="font-weight:800;font-size:16px;line-height:1.08;letter-spacing:.06em;color:#b81c14">NO SCRIPTS<br>'
                f'<span style="color:{INK}">beyond this point</span></div></div></div>'
                f'<div class="abs" style="right:70px;top:{y + 16}px;text-align:right"><div class="label yel" style="font-size:14px">Bypass</div>'
                f'<div class="mono" style="margin-top:5px;font-size:11.5px;line-height:1.45;color:#dedad0">exec claude<br>--dangerously-skip-permissions "$@"</div></div>'
                f'<div class="abs" style="right:70px;top:{low + 14}px;text-align:right"><div class="label" style="font-size:14px">As typed, with the normal prompts</div>'
                f'<div class="mono" style="margin-top:5px;font-size:11.5px;color:#dedad0">exec claude "$@"</div></div>')
        head = (f'<div class="abs" style="left:54px;top:44px;right:54px">'
                f'<div class="cond" style="font-weight:800;font-size:60px;line-height:.92;white-space:nowrap">The gate opens only for a person.</div>'
                f'<div style="margin-top:12px;font-size:17px;color:var(--grey)">bin/claude sits ahead of the real one on PATH. Three yeses, or it runs exactly as typed.</div></div>')
        log = (f'<div class="abs" style="left:40px;right:40px;top:596px">'
               f'<div style="display:flex;justify-content:space-between;align-items:baseline;padding:0 0 10px">'
               f'<div class="label" style="color:var(--grey);font-size:13px">Trial runs &middot; tests/test-launcher.sh -v &middot; a stub claude prints the arguments it got</div>'
               f'<div class="mono yel" style="font-size:13px;font-weight:600">launcher: {LAUNCH_N} passed, 0 failed</div></div>'
               f'{trial_rows(13)}</div>')
        return f'<div class="field" style="width:{W}px;height:{H}px">{art}{plates}{ends}{head}{log}</div>'
    W = 540
    x = 46
    blocks = []
    for i, (q, c, n) in enumerate(GATES):
        blocks.append(f'<div style="position:relative;margin-left:{x + 26}px;width:{W - x - 26 - 30}px">{gate_plate(i + 1, q, c, n, W - x - 26 - 30, 118)}'
                      f'<div class="abs cond" style="right:12px;bottom:-11px;padding:3px 9px;border-radius:3px;background:{ASPHALT};'
                      f'box-shadow:inset 0 0 0 1.5px #55534d;font-weight:800;font-size:12px;letter-spacing:.12em">NO: RUNS AS TYPED</div></div>')
        blocks.append(f'<div class="cond" style="height:44px;margin-left:{x + 26}px;display:flex;align-items:center;font-weight:800;'
                      f'font-size:13px;letter-spacing:.16em;color:var(--grey)">YES &darr;</div>')
    total = 3 * 118 + 3 * 44
    rail = svg(W, total + 80, f'<rect x="{x - 2.5}" y="0" width="5" height="{total - 22}" fill="{WHITE}"/>'
               f'<rect x="{x - 2.5}" y="{total - 22}" width="5" height="62" fill="{YELLOW}"/>'
               + "".join(f'<rect x="{x}" y="{59 + k * 162}" width="26" height="4" fill="{WHITE}"/>' for k in range(3)))
    term = (f'<div style="margin-left:{x + 26}px;margin-right:30px;padding:14px 16px;border-radius:6px;background:{YELLOW};color:{INK}">'
            f'<div class="cond" style="font-weight:800;font-size:18px;letter-spacing:.08em">BYPASS</div>'
            f'<div class="mono" style="margin-top:4px;font-size:11.5px;font-weight:600">exec claude --dangerously-skip-permissions "$@"</div></div>')
    head = (f'<div style="padding:42px 34px 26px"><div class="cond" style="font-weight:800;font-size:46px;line-height:.92">The gate opens<br>only for a person.</div>'
            f'<div style="margin-top:12px;font-size:16px;line-height:1.5;color:var(--grey)">Three yeses, or claude runs exactly as typed, with the normal prompts.</div></div>')
    seq = f'<div style="position:relative">{rail}<div style="position:relative">{"".join(blocks)}{term}</div></div>'
    log = (f'<div style="padding:30px 34px 32px"><div class="label" style="color:var(--grey);font-size:12px;margin-bottom:8px">'
           f'Trial runs &middot; launcher: {LAUNCH_N} passed, 0 failed</div>{trial_rows(12, compact=True)}</div>')
    return f'<div class="field" style="width:{W}px">{head}{seq}{log}</div>'


# ---- sessions: the train register --------------------------------------------

def session_rows(out):
    rows = []
    for l in out[1:]:
        m = re.match(r"^\s*(\S+ ago)\s+(\d+K)\s+(.*?)\s+claude --resume (\S+)$", l)
        assert m, l
        rows.append(m.groups())
    return rows


SESS_ALL = session_rows(SESSION_RUNS[0][1])
SESS_FOUND = session_rows(SESSION_RUNS[1][1])
assert len(SESS_FOUND) == 1 and SESS_FOUND[0] in SESS_ALL
FALLBACK_TITLE = next(first for t, first, *_ in FAKE_SESSIONS if t is None)  # listed by its first message


def ink_stamp(lines, color="#c41d14", angle=-7, size=30, sub=13):
    big, small = lines
    return (f'<div style="display:inline-block;transform:rotate({angle}deg);filter:url(#inkf)">'
            f'<div style="padding:8px 16px 9px;border:3.5px solid {color};border-radius:6px;color:{color};text-align:center;'
            f'box-shadow:inset 0 0 0 2.5px var(--paper),inset 0 0 0 4px {color}">'
            f'<div class="cond" style="font-weight:800;font-size:{size}px;line-height:1;letter-spacing:.08em">{big}</div>'
            f'<div class="cond" style="margin-top:3px;font-weight:700;font-size:{sub}px;letter-spacing:.14em;text-transform:uppercase">{small}</div></div></div>')


INK_FILTER = ('<svg width="0" height="0" style="position:absolute"><filter id="inkf" x="-10%" y="-10%" width="120%" height="120%">'
              '<feTurbulence type="fractalNoise" baseFrequency=".75" numOctaves="2" seed="4" result="n"/>'
              '<feColorMatrix in="n" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 -2.2 1.55" result="m"/>'
              '<feComposite in="SourceGraphic" in2="m" operator="in"/></filter></svg>')


def sessions(mobile=False):
    found_title = SESS_FOUND[0][2]
    if not mobile:
        W = 1100
        cols = "100px 74px 1fr 430px"
        head = (f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
                f'<div><div class="cond" style="font-weight:800;font-size:66px;line-height:.9">TRAIN REGISTER</div>'
                f'<div class="mono" style="margin-top:14px;font-size:15px"><span style="color:var(--ink-3)">~/acme-shop</span> '
                f'<span style="color:#c41d14;font-weight:700">$</span> <b>claude-sessions</b></div></div>'
                f'<div style="margin-top:4px">{ink_stamp(("CANNOT GO STALE", "read from disk on every run"))}</div></div>')
        th = "".join(f'<div class="cond" style="padding:0 12px;font-weight:700;font-size:14px;letter-spacing:.14em;color:#c41d14;'
                     f'{"text-align:right" if i < 2 else ""}">{h}</div>' for i, h in enumerate(["LAST USED", "SIZE", "TITLE", "RESUME WITH"]))
        rows = []
        for when, size, title, sid in SESS_ALL:
            mark = title == found_title
            rows.append(f'<div style="display:grid;grid-template-columns:{cols};align-items:center;height:46px;border-bottom:1px solid var(--rule)">'
                        f'<div class="mono" style="padding:0 12px;text-align:right;font-size:13.5px;color:var(--ink-2)">{e(when)}</div>'
                        f'<div class="mono" style="padding:0 12px;text-align:right;font-size:13.5px;color:var(--ink-2)">{e(size)}</div>'
                        f'<div class="mono" style="padding:0 12px;font-size:13.5px;font-weight:600;position:relative">'
                        f'<span style="position:relative">{e(title)}'
                        + (f'<svg class="abs" style="left:-12px;top:-11px" width="{len(title) * 8.15 + 24:.0f}" height="40" viewBox="0 0 {len(title) * 8.15 + 24:.0f} 40">'
                           f'<path d="M8 22 C 6 6, {len(title) * 8.15 + 10:.0f} 2, {len(title) * 8.15 + 18:.0f} 18 S 30 40, 14 30 S 20 8, 40 6" fill="none" stroke="#c41d14" '
                           f'stroke-width="1.8" stroke-linecap="round" opacity=".85"/></svg>' if mark else "")
                        + f'</span></div>'
                        f'<div class="mono" style="padding:0 12px;font-size:12.5px;color:var(--ink-2)"><b style="color:var(--ink)">claude</b> --resume {e(sid)}</div></div>')
        rules_v = "".join(f'<div class="abs" style="top:0;bottom:0;left:{x}px;width:1.5px;background:#c41d14;opacity:.55"></div>' for x in (100, 174, 562))
        table = (f'<div style="position:relative;margin-top:34px;border-top:2px solid #c41d14">{rules_v}'
                 f'<div style="display:grid;grid-template-columns:{cols};align-items:center;height:40px;border-bottom:3px double #c41d14">{th}</div>'
                 + "".join(rows) + '</div>')
        notes = (f'<div style="margin-top:22px;display:grid;grid-template-columns:1fr 1fr;gap:30px">'
                 f'<div style="border-left:2px solid #c41d14;padding-left:14px"><div class="label" style="color:#c41d14;font-size:12px">Find one fast</div>'
                 f'<div class="mono" style="margin-top:7px;font-size:13.5px"><span style="color:#c41d14;font-weight:700">$</span> <b>{e(SESSION_RUNS[1][0])}</b></div>'
                 f'<div class="mono" style="margin-top:4px;font-size:12.5px;color:var(--ink-2)">&rarr; {len(SESS_FOUND)} match: {e(found_title)}, '
                 f'{e(SESS_FOUND[0][0])}</div></div>'
                 f'<div style="border-left:2px solid var(--ink-3);padding-left:14px"><div class="label" style="color:var(--ink-3);font-size:12px">Titles</div>'
                 f'<div style="margin-top:7px;font-size:14.5px;line-height:1.5;color:var(--ink-2)">The latest AI title wins. A session without one, like '
                 f'&ldquo;{e(FALLBACK_TITLE)}&rdquo;, is listed by its first message.</div></div></div>')
        foot = (f'<div style="margin-top:24px;padding-top:12px;border-top:1px solid var(--rule);display:flex;justify-content:space-between;'
                f'font-size:13px;color:var(--ink-3)"><span>The project and its transcripts are invented. The output is the tool&rsquo;s own.</span>'
                f'<span class="mono">bin/claude-sessions</span></div>')
        return f'<div class="paper" style="width:{W}px;padding:50px 54px 34px">{INK_FILTER}{head}{table}{notes}{foot}</div>'
    W = 540
    head = (f'<div class="cond" style="font-weight:800;font-size:52px;line-height:.9">TRAIN REGISTER</div>'
            f'<div class="mono" style="margin-top:12px;font-size:14px"><span style="color:var(--ink-3)">~/acme-shop</span> '
            f'<span style="color:#c41d14;font-weight:700">$</span> <b>claude-sessions</b></div>'
            f'<div class="abs" style="right:26px;top:86px">{ink_stamp(("CANNOT GO STALE", "read from disk"), size=17, sub=9.5, angle=-6)}</div>')
    items = []
    for when, size, title, sid in SESS_ALL:
        items.append(f'<div style="padding:13px 0 12px;border-bottom:1px solid var(--rule)">'
                     f'<div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline">'
                     f'<span class="mono" style="font-size:14px;font-weight:600">{e(title)}</span>'
                     f'<span class="mono" style="font-size:12px;color:var(--ink-3);white-space:nowrap">{e(when)} &middot; {e(size)}</span></div>'
                     f'<div class="mono" style="margin-top:5px;font-size:11px;color:var(--ink-2)"><b style="color:var(--ink)">claude</b> --resume {e(sid)}</div></div>')
    table = (f'<div style="margin-top:34px;border-top:2px solid #c41d14"><div class="cond" style="display:flex;justify-content:space-between;'
             f'padding:8px 0;border-bottom:3px double #c41d14;font-weight:700;font-size:13px;letter-spacing:.14em;color:#c41d14">'
             f'<span>TITLE &middot; RESUME WITH</span><span>LAST USED &middot; SIZE</span></div>{"".join(items)}</div>')
    foot = (f'<div style="margin-top:16px;font-size:13px;line-height:1.5;color:var(--ink-3)">Newest first. Invented project, the tool&rsquo;s own output.</div>')
    return f'<div class="paper" style="width:{W}px;padding:40px 30px 28px">{INK_FILTER}{head}{table}{foot}</div>'


# ---- tests: the inspection certificate ---------------------------------------

def ci_list(size=14, cmd=True):
    out = []
    for name, lines, last, _ in ci_runs:
        res = e(last) if last else "no output, exit 0"
        out.append(f'<div style="display:grid;grid-template-columns:28px 1fr;column-gap:10px;padding:10px 0;border-top:1px solid var(--rule)">'
                   f'<svg width="22" height="22" viewBox="0 0 22 22" style="margin-top:1px"><rect x="1" y="1" width="20" height="20" rx="2" fill="none" '
                   f'stroke="{INK}" stroke-width="1.6"/><path d="M5 11.5 9.2 15.5 17 6.5" fill="none" stroke="#c41d14" stroke-width="2.6" '
                   f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
                   f'<div><div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline">'
                   f'<span style="font-weight:700;font-size:{size + 2}px">{e(name)}</span>'
                   f'<span class="mono" style="font-size:{size - 1}px;font-weight:{700 if last else 500};color:{"var(--ink)" if last else "var(--ink-3)"}">{res}</span></div>'
                   + (f'<div class="mono" style="margin-top:3px;font-size:{size - 3}px;color:var(--ink-3)">{"<br>".join(e(l) for l in lines)}</div>' if cmd else "")
                   + '</div></div>')
    return "".join(out)


def schedule_lines(size=12):
    out = [f'<span class="ln"><span style="color:#c41d14;font-weight:700">$</span> <b>bash tests/test-guard.sh -v</b></span>']
    for l in GUARD_LINES:
        m = re.match(r"^ok\s+(warn|allow)\s+(.*)$", l)
        if not m:
            out.append(f'<span class="ln" style="margin-top:4px;font-weight:700">{e(l)}</span>')
            continue
        kind, rest = m.groups()
        out.append(f'<span class="ln"><span style="color:var(--ink-3)">ok</span>  '
                   f'<span style="color:{"#c41d14" if kind == "warn" else "var(--ink-3)"};font-weight:{700 if kind == "warn" else 500}">{kind:<5}</span>  '
                   f'<span style="color:var(--ink)">{e(rest)}</span></span>')
    return "".join(out)


def certificate(w, big=150, pad=40, cmd=True):
    return (f'<div class="paper" style="width:{w}px;padding:{pad}px;border-radius:3px;'
            f'box-shadow:0 30px 60px -20px rgba(0,0,0,.85),0 8px 18px -8px rgba(0,0,0,.6)">'
            f'<div style="position:relative;padding:28px 30px 26px;border:5px solid var(--ink);box-shadow:inset 0 0 0 4px var(--paper),inset 0 0 0 5.5px var(--ink)">'
            f'<div class="cond" style="font-weight:800;font-size:15px;letter-spacing:.3em;color:var(--ink-3)">AGENT-GUARDRAILS &middot; LEVEL CROSSING</div>'
            f'<div class="cond" style="margin-top:8px;font-weight:800;font-size:44px;line-height:.95">CERTIFICATE<br>OF INSPECTION</div>'
            f'<div style="margin-top:14px;font-size:15.5px;line-height:1.5;color:var(--ink-2)">Inspected on every push and pull request by '
            f'<span class="mono" style="font-size:14px;color:var(--ink)">.github/workflows/ci.yml</span>, and on this run, locally:</div>'
            f'<div style="display:flex;align-items:flex-end;gap:18px;margin-top:10px">'
            f'<div class="cond" style="font-weight:800;font-size:{big}px;line-height:.82;letter-spacing:-.01em">{TESTS}</div>'
            f'<div style="padding-bottom:6px"><div class="cond" style="font-weight:800;font-size:26px;line-height:1">TESTS PASSED</div>'
            f'<div class="mono" style="margin-top:6px;font-size:13.5px;color:var(--ink-2)">{GUARD_N} hook &middot; {LAUNCH_N} launcher &middot; '
            f'<b style="color:#c41d14">0 failed</b></div></div></div>'
            f'<div style="margin-top:20px">{ci_list(14, cmd)}</div>'
            f'<div style="margin-top:22px;display:flex;justify-content:space-between;align-items:flex-end">'
            f'<div><div style="position:relative;width:200px;border-bottom:1.5px solid var(--ink);height:34px">'
            f'<svg class="abs" style="left:14px;top:2px" width="150" height="40" viewBox="0 0 150 40"><path d="M4 30 C 14 6, 22 4, 20 22 S 30 34, 38 14 '
            f'S 46 30, 54 24 S 64 8, 70 18 S 78 30, 90 20 S 112 10, 146 16" fill="none" stroke="{INK}" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round" opacity=".8"/></svg></div>'
            f'<div class="mono" style="margin-top:6px;font-size:11.5px;color:var(--ink-3)">inspector: ci.yml, job &ldquo;check&rdquo;</div></div>'
            f'<div style="margin-right:6px">{ink_stamp(("PASSED", "all five steps"), size=34, sub=11, angle=-11)}</div></div></div></div>')


def tests(mobile=False):
    if not mobile:
        W, H = 1100, 968
        cert = f'<div class="abs" style="left:40px;top:40px;transform:rotate(-1deg);transform-origin:50% 0;z-index:2">{certificate(560)}</div>'
        sched = (f'<div class="abs" style="left:620px;top:66px;transform:rotate(1.2deg);transform-origin:50% 0;z-index:1">'
                 f'<div class="paper" style="width:446px;padding:28px 26px 24px;background:#e4ddcc;border-radius:3px;'
                 f'box-shadow:0 30px 60px -20px rgba(0,0,0,.85),0 8px 18px -8px rgba(0,0,0,.6)">'
                 f'<div style="display:flex;justify-content:space-between;align-items:baseline;padding-bottom:10px;border-bottom:2px solid var(--ink)">'
                 f'<span class="cond" style="font-weight:800;font-size:22px;letter-spacing:.06em">SCHEDULE A</span>'
                 f'<span class="mono" style="font-size:12px;color:var(--ink-3)">every hook case, both sides</span></div>'
                 f'<div class="term" style="margin-top:12px;font-size:11.6px;line-height:1.6">{schedule_lines()}</div></div></div>')
        return f'<div class="field" style="width:{W}px;height:{H}px">{INK_FILTER}{sched}{cert}</div>'
    W = 540
    return f'<div class="field" style="width:{W}px;padding:34px 22px 38px">{INK_FILTER}{certificate(496, 120, 22, cmd=False)}</div>'


# ---- social preview and the GitHub profile card --------------------------------

def held_block(flag, size, label_size, note="warned, never blocked"):
    return (f'<div class="label red" style="display:flex;align-items:center;gap:10px;font-size:{label_size}px">{tri(label_size)}'
            f'Held at the barrier &middot; {note}</div>'
            f'<div class="term" style="margin-top:{size * .28:.0f}px;font-size:{size}px;line-height:1;letter-spacing:-.01em">{hl_shell(flag["cmd"])}</div>')


def social():
    flag = HERO_FLAGGED[0]
    W, H, px, ground = 1280, 640, 196, 586
    arm_len = 820
    tip = px + 22 + arm_len
    art = svg(W, H,
              f'<circle cx="140" cy="250" r="360" fill="url(#wash)"/><ellipse cx="{px}" cy="{ground + 26}" rx="300" ry="44" fill="url(#wash)"/>'
              f'<rect x="0" y="{ground}" width="{W}" height="{H - ground}" fill="#1c1c1a"/>'
              f'<rect x="0" y="{ground}" width="{W}" height="1.5" fill="#fff" fill-opacity=".07"/>'
              + lane(W, ground + 27, 5, 42, 28)
              + crossing(px, 104, ground, (236, 41), (31, 112), 418, 440, arm_len, 36, (480, 760, 1024), 76)
              + dimension(tip + 6, W - 34, 458, 42))
    look = look_plate(px, 316, 172, 86, 34, 14.5)
    title = (f'<div class="abs" style="left:446px;top:70px;width:800px">'
             f'<div class="cond" style="font-weight:800;font-size:110px;line-height:.9;white-space:nowrap">agent-guardrails</div>'
             f'<div style="margin-top:26px;font-weight:500;font-size:38px;line-height:1.2;max-width:760px">'
             f'A level crossing between your coding agent and your machine.</div></div>')
    gap = f'<div class="abs label yel" style="left:{tip + 10}px;top:494px;font-size:15px;line-height:1.3">Half<br>barrier</div>'
    held = f'<div class="abs" style="left:280px;top:500px">{held_block(flag, 40, 15)}</div>'
    return f'<div class="field" style="width:{W}px;height:{H}px">{art}{look}{title}{gap}{held}</div>'


def profile_card(mobile=False):
    flag = HERO_FLAGGED[0]
    if not mobile:
        W, H, px, ground = 880, 500, 128, 500
        arm_len = 546
        tip = px + 18 + arm_len
        art = svg(W, H,
                  f'<circle cx="96" cy="190" r="290" fill="url(#wash)"/>'
                  + crossing(px, 84, ground + 40, (184, 32), (25, 90), 326, 344, arm_len, 30, (330, 520, 680), 62, post_w=17, hazard=0)
                  + dimension(tip + 6, W - 26, 359, 34))
        look = look_plate(px, 258, 140, 62, 25, 11.5)
        title = (f'<div class="abs" style="left:290px;top:52px;width:560px">'
                 f'<div class="cond" style="font-weight:800;font-size:86px;line-height:.9;white-space:nowrap">agent-guardrails</div>'
                 f'<div style="margin-top:20px;font-weight:500;font-size:29px;line-height:1.22">A level crossing between your coding agent and your machine.</div></div>')
        gap = f'<div class="abs label yel" style="left:{tip + 8}px;top:384px;font-size:12.5px;line-height:1.3">Half<br>barrier</div>'
        held = f'<div class="abs" style="left:206px;top:402px">{held_block(flag, 34, 13)}</div>'
        return f'<div class="field" style="width:{W}px;height:{H}px">{art}{look}{title}{gap}{held}</div>'
    W, H, px = 400, 400, 70
    arm_len = 262
    tip = px + 15 + arm_len
    art = svg(W, H,
              f'<circle cx="60" cy="236" r="200" fill="url(#wash)"/>'
              + post(px, 196, H + 20, w=14, hazard=0)
              + lamp_pod(px, 224, 17, 62)
              + housing(px, 268, 40, 58)
              + arm(px + 15, 282, arm_len, 22, lamp_xs=(190, 268, 334), counter=44)
              + dimension(tip + 5, W - 14, 293, 26, sw=1.2))
    title = (f'<div class="abs" style="left:28px;top:30px;width:350px">'
             f'<div class="cond" style="font-weight:800;font-size:58px;line-height:.88">agent-<br>guardrails</div>'
             f'<div style="margin-top:12px;font-weight:500;font-size:19px;line-height:1.25">A level crossing for your coding agent.</div></div>')
    held = (f'<div class="abs" style="left:122px;top:322px"><div class="label red" style="display:flex;align-items:center;gap:7px;font-size:11px">'
            f'{tri(10)}warned, never blocked</div><div class="term" style="margin-top:8px;font-size:22px;line-height:1">{hl_shell(flag["cmd"])}</div></div>')
    return f'<div class="field" style="width:{W}px;height:{H}px">{art}{title}{held}</div>'


SCENES = {
    "hero": (lambda: hero(), 1100),
    "hero-mobile": (lambda: hero(True), 540),
    "numbers": (lambda: numbers(), 1100),
    "numbers-mobile": (lambda: numbers(True), 540),
    "rules": (lambda: rules(), 1100),
    "rules-mobile": (lambda: rules(True), 540),
    "agents": (lambda: agents(), 1100),
    "agents-mobile": (lambda: agents(True), 540),
    "launcher": (lambda: launcher(), 1100),
    "launcher-mobile": (lambda: launcher(True), 540),
    "sessions": (lambda: sessions(), 1100),
    "sessions-mobile": (lambda: sessions(True), 540),
    "tests": (lambda: tests(), 1100),
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


def render(name, html_doc, width, out_path=None, src_path=None, scale=2, height=None, final_size=None):
    """Render one page with headless Chrome and write WebP (or PNG).

    Without a height the page is shot tall and the transparent margin trimmed;
    with one, the window is exactly width x height CSS pixels."""
    src = src_path or os.path.join(SRC, f"{name}.html")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w") as fh:
        fh.write(html_doc)
    out = out_path or os.path.join(OUT, f"{name}.webp")
    tmp = tempfile.mkdtemp()
    try:
        png = os.path.join(tmp, "shot.png")
        p = subprocess.run([chrome(), "--headless=new", "--no-sandbox", "--hide-scrollbars", "--disable-gpu",
                            f"--force-device-scale-factor={scale}", "--default-background-color=00000000",
                            "--virtual-time-budget=8000", f"--window-size={width},{height or 2600}",
                            f"--user-data-dir={tmp}/profile", f"--screenshot={png}", "file://" + src],
                           capture_output=True, text=True)
        if not os.path.exists(png):
            sys.exit(f"render failed for {name}: {p.stderr[-800:]}")
        cmd = ["magick", png] + ([] if height else ["-trim", "+repage"])
        if final_size:
            cmd += ["-resize", final_size + "!"]
        cmd += ["-strip"]
        if out.endswith(".png"):
            cmd += ["-background", ASPHALT, "-flatten", out]
        else:
            cmd += ["-quality", "86", "-define", "webp:method=6", out]
        subprocess.run(cmd, check=True)
        dims = subprocess.run(["magick", "identify", "-format", "%wx%h", out], capture_output=True, text=True).stdout
        print(f"{name:<24} {dims:>10}  {os.path.getsize(out) // 1024:>4} KB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    args = sys.argv[1:]
    profile = None
    if "--profile" in args:
        # Also draw this repo's card for the GitHub profile README: --profile <profile repo>/docs
        i = args.index("--profile")
        profile = args[i + 1]
        del args[i:i + 2]
    only = args
    print(f"hook: {len(RULES)} rules checked against {2 * len(RULE_EXAMPLES)} examples; "
          f"tests: {GUARD_N} + {LAUNCH_N} passed; sessions: {len(SESSION_RUNS[0][1]) - 1} fake transcripts")
    for name, (build, width) in SCENES.items():
        if only and name not in only:
            continue
        if name == "social":
            render(name, page(name, build(), width), width, out_path=os.path.join(OUT, "social.png"),
                   height=640, final_size="1280x640")
        else:
            render(name, page(name, build(), width), width)
    if profile:
        for suffix, (w, h), mobile in (("", (880, 500), False), ("-mobile", (400, 400), True)):
            name = f"agent-guardrails{suffix}"
            render(name, page(name, profile_card(mobile), w), w, scale=1.5, height=h,
                   src_path=os.path.join(profile, "src", "cards", f"{name}.html"),
                   out_path=os.path.join(profile, "cards", f"{name}.webp"))


if __name__ == "__main__":
    main()
