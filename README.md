# agent-guardrails

A small safety and quality kit for Claude Code: two independent-check subagents, an advisory shell-command hook, a TTY-only permission launcher, and a session lister.

## Why

A coding agent works best when it is treated like a capable engineer who still needs a lead: a clear spec, explicit limits, and someone other than the author checking the result. This kit encodes those three things as files Claude Code already understands.

- **Spec and limits**: delegation rules in `CLAUDE.md` say what to hand off and when.
- **Independent verification**: a `reviewer` agent reviews work in a fresh context that did not write it, and a `verifier` agent checks factual claims against primary sources instead of model memory.
- **Guardrails without friction**: a `PreToolUse` hook flags risky shell commands in the agent's own context, without blocking anything.

## Contents

| Path | What it is |
| --- | --- |
| `.claude/agents/reviewer.md` | Subagent: independent review of a finished diff |
| `.claude/agents/verifier.md` | Subagent: fact-checks specific claims against primary sources |
| `.claude/hooks/advisory-guard.sh` | `PreToolUse` hook on `Bash`: warns on risky commands, never blocks |
| `.claude/settings.json` | Wires the hook into Claude Code |
| `bin/claude` | Launcher: adds `--dangerously-skip-permissions` only for interactive TTY sessions |
| `bin/claude-sessions` | Lists saved sessions for the current project with the exact resume command |
| `templates/CLAUDE.md` | Generic delegation and verification rules |
| `tests/` | Hook and launcher tests |

## Install

Requirements: Claude Code, `sh`, `bash`, `python3`.

Per project:

```sh
git clone https://github.com/gallowforge/agent-guardrails
cd your-project
mkdir -p .claude
cp -r ../agent-guardrails/.claude/agents ../agent-guardrails/.claude/hooks .claude/
# Merge the "hooks" block into .claude/settings.json if you already have one:
cp ../agent-guardrails/.claude/settings.json .claude/settings.json
cat ../agent-guardrails/templates/CLAUDE.md >> CLAUDE.md
```

For every project, copy `agents/` into `~/.claude/agents/` instead and point the hook command at an absolute path in `~/.claude/settings.json`.

The launcher and the session lister are optional:

```sh
mkdir -p "$HOME/.local/bin/claude-wrap"
cp bin/claude bin/claude-sessions "$HOME/.local/bin/claude-wrap/"
# Put the wrapper directory ahead of the real claude on PATH:
export PATH="$HOME/.local/bin/claude-wrap:$PATH"
```

## Components

### reviewer

Runs after implementation is functionally complete. It reads the actual diff, not a description of it, and checks in order: correctness on real inputs, safety (anything destructive or out of scope), convention drift, then simplification. Every finding cites `file:line`; if there is nothing worth flagging it says so in one line instead of inventing nitpicks.

```text
> Use the reviewer agent on the changes in src/sync/.

src/sync/queue.py:88  retry loop never resets `attempt`, so after the first
failure every later item gets zero retries.
src/sync/queue.py:131 deletes the local copy before the upload is confirmed;
a network error loses the file.
```

### verifier

Takes one or a short list of concrete claims and returns a verdict per claim: `CORRECT`, `PARTIALLY CORRECT`, `FALSE`, or `COULD NOT VERIFY`, each with the source it checked (URL, file and line, or command). Official documentation outranks secondary sources; repetition across low-quality sources is not evidence.

```text
> Verify: "git push --force-with-lease refuses to push if the remote ref moved."

CORRECT. git-push(1), --force-with-lease: the push is refused unless the
remote ref still matches the expected value (by default the remote-tracking
branch). Source: https://git-scm.com/docs/git-push
```

### advisory-guard.sh

A `PreToolUse` hook on the `Bash` tool. When a command matches a risky pattern it returns `hookSpecificOutput.additionalContext`, so the warning lands in Claude's context before the command runs. It deliberately returns no `permissionDecision`: returning `allow` would skip the normal permission prompt for exactly the riskiest commands, so the hook leaves the permission flow untouched and only adds context. Malformed input, other tools, and a missing `python3` all exit 0 silently.

```sh
$ echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf build && chmod 777 ."}}' \
    | sh .claude/hooks/advisory-guard.sh
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "Advisory (non-blocking): this command looks like it involves recursive force-delete; world-writable permissions. Proceeding, but double-check the target/scope before it runs again unattended."}}
```

Rules (case-insensitive regular expressions, matched anywhere in the command):

| Warning | Pattern | Matches | Does not match |
| --- | --- | --- | --- |
| recursive force-delete | `rm -` with both `r` and `f` flags | `rm -rf x`, `rm -fr x`, `rm -Rf x` | `rm -r x`, `rm x` |
| force-push | `git push ... --force` (not `--force-with-lease`), `git push ... -f` | `git push --force`, `git push origin main -f` | `git push --force-with-lease` |
| remote download piped into a shell | `curl` or `wget` piped to `sh`/`bash`, with or without `sudo` | `curl -fsSL url \| sh` | `curl -O url` |
| world-writable permissions | `chmod` with mode ending in `777` | `chmod 777 x`, `chmod -R 0777 x` | `chmod 755 x` |
| credentials or key path | `.ssh`, `.aws/credentials`, `.credentials.json`, `id_rsa`, `id_ed25519` | `cat ~/.ssh/id_ed25519`, `ls ~/.ssh` | `systemctl status sshd` |
| raw disk write | `mkfs.*`, `dd ... of=/dev/...` | `mkfs.ext4 /dev/sdb1`, `dd if=x of=/dev/sdb` | `dd of=./disk.img` |
| writing directly to a block device | redirect into `/dev/sd*` or `/dev/nvme*` | `cat x > /dev/nvme0n1` | `cat x > out.bin` |

It is a tripwire, not a sandbox. It is easy to evade on purpose (variables, `eval`, scripts) and is meant to catch accidents. If you need hard blocking, add a separate hook that returns `permissionDecision: "deny"`; keep this one advisory.

### bin/claude

A wrapper that sits ahead of the real `claude` on `PATH`. It adds `--dangerously-skip-permissions` only when all of these hold:

- stdin and stdout are both a terminal;
- no `-p`/`--print`, `--permission-mode`, or `--dangerously-skip-permissions` was passed;
- the first argument is not a CLI subcommand (`mcp`, `attach`, `stop`, ...), because a root flag placed before a subcommand stops it from being parsed as one.

Scripts, cron jobs, hooks and piped runs therefore never inherit a no-prompt session.

```sh
$ claude                         # interactive: runs claude --dangerously-skip-permissions
$ claude -p "summarize this"     # print mode: unchanged
$ echo hi | claude               # piped stdin: unchanged
$ claude --permission-mode plan  # explicit mode wins: unchanged
$ claude mcp list                # subcommand: unchanged
```

Skipping permission prompts is a real trade-off: nothing stops a destructive command except your own review and the advisory hook above, which only warns. Use it on machines where you accept that.

### bin/claude-sessions

Lists the current project's saved sessions, newest first, by reading the transcripts on disk, so the list cannot go stale. It honours `CLAUDE_CONFIG_DIR`, and `CLAUDE_SESSIONS_DIR` overrides the transcript directory directly.

```text
$ claude-sessions -n 3
LAST USED    SIZE  TITLE                                                       RESUME WITH
   4m ago     212K  Fix flaky upload retry                                      claude --resume 3f2a9c1e-...
   2h ago    1480K  Migrate config loader to TOML                               claude --resume 9b07d4aa-...
   3d ago      96K  Add CI workflow                                             claude --resume c41e0b52-...

$ claude-sessions upload        # filter by title
```

## Tests

```sh
bash tests/test-guard.sh      # 29 cases: each rule's warn and allow paths, other tools, bad input
bash tests/test-launcher.sh   # flag injection with and without a TTY (uses util-linux script)
```

CI runs `sh -n`/`bash -n`, `shellcheck --severity=error`, `python3 -m py_compile`, and both test scripts on every push.

## License

MIT
