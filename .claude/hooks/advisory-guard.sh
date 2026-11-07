#!/bin/sh
# Advisory-only PreToolUse guard for Bash commands.
# Never blocks or denies. It only attaches a warning to Claude's context when
# a command looks risky. If you want a hard-block tier, add a separate hook;
# do not turn this one destructive.

hook_input_file="$(mktemp "${TMPDIR:-/tmp}/claude-advisory-hook.XXXXXX")" || exit 0
trap 'rm -f "$hook_input_file"' EXIT HUP INT TERM
cat >"$hook_input_file" 2>/dev/null || exit 0

command -v python3 >/dev/null 2>&1 || exit 0

HOOK_INPUT_FILE="$hook_input_file" python3 - <<'PY'
import json
import os
import re
import sys

path = os.environ.get("HOOK_INPUT_FILE")
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

if data.get("tool_name") != "Bash":
    sys.exit(0)

cmd = (data.get("tool_input") or {}).get("command", "") or ""

# (pattern, warning) — deliberately narrow and high-signal, not a full
# security scanner. Advisory only: every match still allows the command.
CHECKS = [
    (r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f|rm\s+-[a-zA-Z]*f[a-zA-Z]*r", "recursive force-delete"),
    (r"git\s+push\s+.*--force(?!-with-lease)\b|git\s+push\s+.*\s-f\b", "force-push (rewrites remote history)"),
    (r"\bcurl\b.*\|\s*(sudo\s+)?(sh|bash)\b|\bwget\b.*\|\s*(sudo\s+)?(sh|bash)\b", "piping a remote download straight into a shell"),
    (r"chmod\s+[0-7]*777|chmod\s+-R\s+[0-7]*777", "world-writable permissions"),
    (r"\.ssh\b|\.aws/credentials|\.credentials\.json|id_rsa|id_ed25519", "touching a credentials/key path"),
    (r"\bmkfs\.|dd\s+.*of=/dev/", "raw disk write"),
    (r">\s*/dev/sd[a-z]|>\s*/dev/nvme", "writing directly to a block device"),
]

hits = [msg for pattern, msg in CHECKS if re.search(pattern, cmd, re.IGNORECASE)]
if not hits:
    sys.exit(0)

warning = "Advisory (non-blocking): this command looks like it involves " + "; ".join(hits) + ". Proceeding, but double-check the target/scope before it runs again unattended."

# No permissionDecision on purpose. Returning "allow" here would bypass the
# normal permission prompt for exactly the riskiest commands — an advisory
# hook must not weaken permissions. Omitting the decision leaves the normal
# permission flow intact and only attaches context.
# additionalContext must live INSIDE hookSpecificOutput; at the root it is
# silently dropped as an unrecognized key.
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": warning,
    },
}))
PY
exit 0
