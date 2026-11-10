#!/usr/bin/env python3
"""Regenerate the README images in docs/readme/ from real runs.

Nothing in the images is typed by hand. The script:

  1. pipes PreToolUse payloads (the same JSON the tests use) into
     .claude/hooks/advisory-guard.sh and keeps the exact stdout and exit code,
     and checks every rule example against the hook, so the rule table cannot drift;
  2. runs the CI checks and both test suites with -v and keeps their output;
  3. builds a throwaway HOME with a fake project and fake transcripts, and runs
     bin/claude-sessions inside it;
  4. writes that output, and the real agent, settings and launcher files, as
     terminal and document pages in docs/readme/src/tex/;
  5. renders them as Glass scenes (docs/readme/src/glass/glass.cjs): frosted-glass
     windows in a dark and a light version, for desktop and phone, as WebP. The hero
     is a loop in which the command is typed and the hook's reply arrives; every other
     image is a still.

    python3 scripts/readme-visuals.py           # everything
    python3 scripts/readme-visuals.py --pages   # steps 1 to 4 only

Needs python3, bash, sh, util-linux script(1), shellcheck, node with `playwright`
and `three` (0.170) on NODE_PATH, ImageMagick, ffmpeg, and network access for the fonts.
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
TEX = os.path.join(ROOT, "docs", "readme", "src", "tex")
ACCENT = "--hi:#ffc370;--hi-day:#9a5c06;--ok:#ffc370;--warn:#ffc370"
e = html.escape


def sh(args, **kw):
    return subprocess.run(args, cwd=kw.pop("cwd", ROOT), capture_output=True, text=True, **kw)


# --------------------------------------------------------------------------
# 1. The hook, run for real
# --------------------------------------------------------------------------

def payload(cmd, tool="Bash"):
    # Identical to tests/test-guard.sh's payload().
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": cmd}})


def hook(cmd, tool="Bash"):
    p = subprocess.run(["sh", HOOK], input=payload(cmd, tool), capture_output=True, text=True)
    out = p.stdout.strip()
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None
    assert "permissionDecision" not in out and p.returncode == 0, (cmd, out, p.returncode)
    return {"cmd": cmd, "stdout": out, "code": p.returncode, "context": ctx}


HOOK_SRC = open(HOOK).read()
RULES = re.findall(r'^\s*\(r"(.*?)", "(.*?)"\),$', HOOK_SRC, re.M)
assert len(RULES) == 7, f"expected 7 rules in the hook, found {len(RULES)}"
PREFIX = "Advisory (non-blocking): this command looks like it involves "
SUFFIX = ". Proceeding, but double-check the target/scope before it runs again unattended."

# One command that each rule flags and a near miss it lets through, in hook order.
# The README's rule table quotes these.
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


for (pattern, warning), (bad, good) in zip(RULES, RULE_EXAMPLES):
    assert hits(hook(bad)) == [warning], (bad, warning)
    assert hits(hook(good)) == [], good

# The hero: one payload that trips three rules, run in a shell exactly as shown.
MULTI_CMD = "rm -rf ~/.ssh && chmod 777 ."
assert len(hits(hook(MULTI_CMD))) == 3
HERO_SHELL = [f"payload='{payload(MULTI_CMD)}'", 'echo "$payload" | sh .claude/hooks/advisory-guard.sh', "echo $?"]
_p = sh(["bash", "-c", HERO_SHELL[0] + "\n" + HERO_SHELL[1] + "\n" + HERO_SHELL[2]])
HERO_OUT = _p.stdout.rstrip("\n").split("\n")
assert _p.returncode == 0 and len(HERO_OUT) == 2 and HERO_OUT[1] == "0", HERO_OUT
assert HERO_OUT[0] == hook(MULTI_CMD)["stdout"]

# --------------------------------------------------------------------------
# 2. CI checks and the test suites
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
CI = []
for name, lines in ci_steps():
    p = sh(["bash", "-e", "-c", "\n".join(lines)], env=dict(os.environ, PYTHONPYCACHEPREFIX=pycache))
    assert p.returncode == 0, (name, p.stdout, p.stderr)
    CI.append(name)
shutil.rmtree(pycache, ignore_errors=True)


def suite(name, pipe=""):
    p = sh(["bash", "-c", f"bash tests/{name} -v{pipe}"])
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.rstrip("\n").split("\n")


GUARD = suite("test-guard.sh")
LAUNCH = suite("test-launcher.sh")
GUARD_N = int(re.search(r"guard: (\d+) passed, 0 failed", GUARD[-1]).group(1))
LAUNCH_N = int(re.search(r"launcher: (\d+) passed, 0 failed", LAUNCH[-1]).group(1))
GUARD_HEAD = suite("test-guard.sh", " | head -n 9")
assert GUARD_HEAD == GUARD[:9]
SUBCOMMANDS = len(re.search(r'subcommands="(.*?)"', open(os.path.join(ROOT, "bin", "claude")).read(), re.S).group(1).split())
AGENTS = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, ".claude", "agents")) if f.endswith(".md"))

# --------------------------------------------------------------------------
# 3. claude-sessions on a fake project
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


SESSIONS = run_sessions()

# --------------------------------------------------------------------------
# 4. Pages: terminals (dark in both themes) and documents (dark and light)
# --------------------------------------------------------------------------

def page(name, width, body, kind="term", theme="", size=None):
    style = f"width:{width}px;{ACCENT}" + (f";font-size:{size}px" if size else "")
    with open(os.path.join(TEX, name + ".html"), "w") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="tex.css">'
                f'<body class="{kind} {theme}" style="{style}">\n{body}\n</body>\n')


def prompt(cmd, cls="c"):
    return f'<div class="l"><span class="p">$</span> <span class="{cls}">{e(cmd)}</span></div>'


def test_line(line, lift=False):
    """`ok    warn   rm -rf x`: the verdict word in colour, the command in white."""
    m = re.match(r"^(ok|FAIL)(\s+)(warn|allow)(\s+)(.*)$", line) or re.match(r"^(ok|FAIL)(\s+)(.{27})(\s*)(ARGS:.*)$", line)
    if not m:
        return f'<div class="l">{e(line)}</div>'
    word = m.group(3)
    cls = "w" if word == "warn" else "d"
    inner = f'<span class="ok">{m.group(1)}</span>{m.group(2)}<span class="{cls}">{e(word)}</span>{m.group(4)}<span class="t">{e(m.group(5))}</span>'
    if lift:
        return f'<div class="l lifted"><span data-lift="key">{inner}</span></div>'
    return f'<div class="l">{inner}</div>'


def summary(line, lift=False):
    t = re.sub(r"(\d+ passed, 0 failed)", r'<span class="ok">\1</span>', e(line))
    return f'<div class="l{" lifted" if lift else ""}">' + (f'<span data-lift="key">{t}</span>' if lift else t) + "</div>"


def inline(s):
    s = e(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)


def agent_doc(name, theme):
    """An agent file as a page: its front matter as properties, then its instructions."""
    src = open(os.path.join(ROOT, ".claude", "agents", name + ".md")).read().split("\n")
    end = src.index("---", 1)
    props = dict(l.split(": ", 1) for l in src[1:end])
    rows = "".join(f'<span class="k">{k}</span><span>{e(props[k])}</span>' for k in ("name", "tools", "model"))
    out = [f'<div class="kick">.claude/agents/{name}.md</div>', f'<div class="props">{rows}</div>']
    paras, cur = [], []
    for l in src[end + 1:]:
        if not l.strip():
            if cur:
                paras.append(cur); cur = []
        else:
            cur.append(l)
    if cur:
        paras.append(cur)
    split = []   # a list that follows its lead-in line without a blank line is still a list
    for para in paras:
        k = next((i for i, l in enumerate(para) if re.match(r"\d+\. ", l)), None)
        split += [para[:k], para[k:]] if k else [para]
    for para in split:
        if re.match(r"\d+\. ", para[0]):
            items, item = [], None
            for l in para:
                if re.match(r"\d+\. ", l):
                    item = [l.split(". ", 1)[1]]; items.append(item)
                else:
                    item.append(l.strip())
            lis = []
            for it in items:
                text = " ".join(it)
                lift = ' data-lift="key"' if "CORRECT / PARTIALLY CORRECT" in text else ""
                lis.append(f"<li><span{lift}>{inline(text)}</span></li>")
            out.append("<ol>" + "".join(lis) + "</ol>")
        else:
            out.append(f"<p>{inline(' '.join(l.strip() for l in para))}</p>")
    page(name if theme == "dark" else name + "-light", 540, "\n".join(out), "doc", theme)


def code_doc(name, path, first, last, theme, width=560):
    """Lines first..last of a real file, with their real line numbers."""
    src = open(os.path.join(ROOT, path)).read().split("\n")
    out = [f'<div class="file">{e(path)}</div>']
    for i in range(first, last + 1):
        l = src[i - 1]
        t = e(l)
        if l.lstrip().startswith("#"):
            t = f'<span class="cm">{t}</span>'
        else:
            t = re.sub(r"(&quot;[^&]*?&quot;)", r'<span class="s">\1</span>', t)
            t = re.sub(r"\b(if|then|fi|for|in|do|done|case|esac|exec|break)\b", r'<span class="kw">\1</span>', t)
        out.append(f'<span class="ln"><span class="n">{i}</span>{t or " "}</span>')
    page(name if theme == "dark" else name + "-light", width, "\n".join(out), "doc code", theme)


def pages():
    os.makedirs(TEX, exist_ok=True)
    for f in os.listdir(TEX):
        if f.endswith(".html"):
            os.remove(os.path.join(TEX, f))
    # hero: three rules in one command, one advisory, exit 0
    out = HERO_OUT[0]
    pre, ctx = out.split('"additionalContext": "', 1)
    warn, rest = ctx.split(". Proceeding", 1)
    body = [prompt(HERO_SHELL[0], "d"), prompt(HERO_SHELL[1]), '<div class="gap"></div>',
            f'<div class="l lifted"><span class="d">{e(pre)}"additionalContext": "</span><span data-lift="key"><span class="w">{e(warn)}.</span></span>'
            f'<span class="d"> Proceeding{e(rest)}</span></div>',
            prompt(HERO_SHELL[2]), f'<div class="l"><span class="ok">{e(HERO_OUT[1])}</span></div>']
    page("hook", 620, "\n".join(body))
    # the rules from both sides
    lines = [prompt("bash tests/test-guard.sh -v | head -n 9")]
    lines += [test_line(l, lift="--force-with-lease" in l) for l in GUARD_HEAD]
    page("guard", 560, "\n".join(lines))
    lines = [prompt("bash tests/test-guard.sh -v")] + [test_line(l) for l in GUARD[:-1]] + [summary(GUARD[-1])]
    page("guard-all", 560, "\n".join(lines))
    # the launcher's seven cases
    lines = [prompt("bash tests/test-launcher.sh -v")]
    lines += [test_line(l, lift="flag injected" in l) for l in LAUNCH[:-1]] + [summary(LAUNCH[-1])]
    page("launcher", 700, "\n".join(lines))
    # claude-sessions
    body = []
    for i, (cmd, out) in enumerate(SESSIONS):
        if i:
            body.append('<div class="gap"></div>')
        body.append(prompt(cmd))
        body.append(f'<div class="l"><span class="d">{e(out[0])}</span></div>')
        for j, row in enumerate(out[1:]):
            m = re.match(r"^(.*?)(claude --resume \S+)$", row)
            inner = f'<span class="t">{e(m.group(1))}</span><span class="hi">{e(m.group(2))}</span>'
            if i == 1 and j == 0:
                body.append(f'<div class="l lifted"><span data-lift="key">{inner}</span></div>')
            else:
                body.append(f'<div class="l">{inner}</div>')
    page("sessions", 1060, "\n".join(body), size=12.4)
    for theme in ("dark", "light"):
        agent_doc("reviewer", theme)
        agent_doc("verifier", theme)
        code_doc("settings", ".claude/settings.json", 1, 17, theme, 780)
        src = open(os.path.join(ROOT, "bin", "claude")).read().split("\n")
        first = src.index("inject=1") + 1
        code_doc("decide", "bin/claude", first, first + 18, theme, 640)
    json.dump({"rules": len(RULES), "tests": GUARD_N + LAUNCH_N, "guard": GUARD_N, "launcher": LAUNCH_N,
               "agents": len(AGENTS), "subcommands": SUBCOMMANDS, "ci": len(CI)}, open(os.path.join(TEX, "facts.json"), "w"), indent=1)
    print(f"pages -> docs/readme/src/tex ({GUARD_N} hook tests, {LAUNCH_N} launcher tests, {len(RULES)} rules, {len(CI)} CI steps)")


if __name__ == "__main__":
    pages()
    if "--pages" not in sys.argv:
        subprocess.run(["node", os.path.join(ROOT, "docs", "readme", "src", "glass", "glass.cjs")], check=True)
