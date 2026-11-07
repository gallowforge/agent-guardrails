#!/usr/bin/env bash
# Checks when bin/claude injects --dangerously-skip-permissions, using a stub
# "real" claude that prints its arguments. TTY cases use util-linux `script`.
# Usage: tests/test-launcher.sh
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
wrapper_dir="$(cd "$here/../bin" && pwd)"
stub_dir="$(mktemp -d)"
trap 'rm -rf "$stub_dir"' EXIT

cat >"$stub_dir/claude" <<'EOF'
#!/bin/sh
echo "ARGS:$*"
EOF
chmod +x "$stub_dir/claude"

export PATH="$wrapper_dir:$stub_dir:$PATH"
pass=0
fail=0

expect() {
  # $1 = label, $2 = expected ARGS line, $3 = actual output
  local got
  got="$(printf '%s\n' "$3" | tr -d '\r' | grep '^ARGS:' | head -n1)"
  if [ "$got" = "$2" ]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    printf 'FAIL  %s\n      expected: %s\n      got:      %s\n' "$1" "$2" "$got"
  fi
}

in_tty() {
  # Run a command line under a pseudo-terminal so stdin and stdout are TTYs.
  script -qec "$1" /dev/null </dev/null
}

expect "non-interactive: no flag"   "ARGS:hello"          "$(claude hello </dev/null)"
expect "print mode: no flag"         "ARGS:-p hi"          "$(claude -p hi </dev/null)"

if command -v script >/dev/null 2>&1 && script -qec true /dev/null </dev/null >/dev/null 2>&1; then
  expect "interactive: flag injected"  "ARGS:--dangerously-skip-permissions hello" "$(in_tty 'claude hello')"
  expect "interactive -p: no flag"     "ARGS:-p hi"                               "$(in_tty 'claude -p hi')"
  expect "explicit mode wins"          "ARGS:--permission-mode plan"              "$(in_tty 'claude --permission-mode plan')"
  expect "subcommand: no flag"         "ARGS:mcp list"                            "$(in_tty 'claude mcp list')"
  expect "bare interactive: flag"      "ARGS:--dangerously-skip-permissions"      "$(in_tty 'claude')"
else
  echo "skip: util-linux script(1) not available, TTY cases not run"
fi

echo "launcher: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
