<picture>
  <source media="(max-width: 700px)" srcset="docs/readme/hero-mobile.webp" />
  <img src="docs/readme/hero.webp" width="100%" alt="agent-guardrails: give your coding agent a lead. A terminal shows the PreToolUse hook flagging rm -rf ./build ~/ and curl piped into sh with its real warning text, while npm test, rm -r old-dir, chmod 755 and git push --force-with-lease pass with no output and exit 0." />
</picture>

<p align="center">
  <a href="#quickstart"><b>Quickstart</b></a>
  &nbsp;·&nbsp;
  <a href="#catch-the-risky-command-before-it-runs">The rules</a>
  &nbsp;·&nbsp;
  <a href="#under-the-hood">How it works</a>
  &nbsp;·&nbsp;
  <a href="#quality">Tests</a>
  &nbsp;·&nbsp;
  <a href="https://gallowforge.github.io">More projects</a>
</p>

<p align="center">
  <a href="https://github.com/gallowforge/agent-guardrails/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/gallowforge/agent-guardrails/actions/workflows/ci.yml/badge.svg" /></a>
  <img alt="36 tests passing" src="https://img.shields.io/badge/tests-36_passing-4ade80?labelColor=0f1217" />
  <img alt="Hook: advisory, never blocks" src="https://img.shields.io/badge/hook-advisory%2C_never_blocks-ff6b4a?labelColor=0f1217" />
  <img alt="Needs sh, bash and python3" src="https://img.shields.io/badge/needs-sh_%C2%B7_bash_%C2%B7_python3-ff6b4a?labelColor=0f1217" />
  <img alt="MIT licence" src="https://img.shields.io/badge/licence-MIT-9aa1ad?labelColor=0f1217" />
</p>

<br />

A coding agent will run `rm -rf` on the wrong directory, push over a shared branch, and then tell you the work is done and correct, in the same confident voice. Permission prompts on every command wear you down until you approve everything. Turning them off everywhere leaves nothing between the agent and your files.

**agent-guardrails treats the agent like a capable engineer who still needs a lead: a written spec and limits, a second opinion from something that did not write the code, and a tripwire on the commands that hurt.** It is a handful of plain files that Claude Code already understands: two subagents, one hook, one launcher and one session lister. No service, no daemon, nothing to install beyond `sh`, `bash` and `python3`.

<picture>
  <source media="(max-width: 700px)" srcset="docs/readme/numbers-mobile.webp" />
  <img src="docs/readme/numbers.webp" width="100%" alt="7 risk rules, 36 tests passing (29 hook and 7 launcher), 2 agents, 4 verdicts per checked claim, 21 subcommands that never get the bypass flag, and 0 hard blocks: the hook only warns." />
</picture>

<br />

## Catch the risky command before it runs

`advisory-guard.sh` is a `PreToolUse` hook on the `Bash` tool. Claude Code hands it every shell command before it runs. When a command matches one of seven rules, the warning lands in the agent's own context, so it double-checks the target before going ahead.

- **A tripwire, not a gate.** The hook returns `additionalContext` and deliberately no `permissionDecision`. Returning `allow` would skip the normal prompt for exactly the riskiest commands, so it leaves your permission flow untouched.
- **Narrow on purpose.** `--force-with-lease` passes, `--force` is flagged. Downloading a script passes, piping it into `sh` is flagged. Every rule is tested from both sides.
- **One command, one warning.** `rm -rf ~/.ssh && chmod 777 .` trips three rules and gets a single message that names all three.
- **Fails open and quiet.** Malformed input, other tools, empty input and a missing `python3` all exit 0 with no output, so a problem in the hook never stalls your work.

<picture>
  <source media="(max-width: 700px)" srcset="docs/readme/rules-mobile.webp" />
  <img src="docs/readme/rules.webp" width="100%" alt="The seven rules as a table: each warning in the hook's own words, a command it flags and a near miss it lets through. Below, a terminal pipes a three-hit payload into the hook and shows its real JSON reply." />
</picture>

<details>
<summary><b>All seven rules, with their patterns</b></summary>
<br />

Case-insensitive regular expressions, matched anywhere in the command. The warning column is the hook's exact wording.

| Warning | Pattern | Flags | Lets through |
| --- | --- | --- | --- |
| recursive force-delete | `rm -` with both `r` and `f` flags, in either order | `rm -rf x`, `rm -fr x`, `rm -Rf x` | `rm -r x`, `rm x` |
| force-push (rewrites remote history) | `git push … --force` (not `--force-with-lease`), `git push … -f` | `git push --force`, `git push origin main -f` | `git push --force-with-lease` |
| piping a remote download straight into a shell | `curl` or `wget` piped to `sh` or `bash`, with or without `sudo` | `curl -fsSL url \| sh`, `wget -qO- url \| sudo bash` | `curl -O url` |
| world-writable permissions | `chmod` with a mode ending in `777` | `chmod 777 x`, `chmod -R 0777 x` | `chmod 755 x` |
| touching a credentials/key path | `.ssh`, `.aws/credentials`, `.credentials.json`, `id_rsa`, `id_ed25519` | `cat ~/.ssh/id_ed25519`, `ls ~/.ssh` | `systemctl status sshd` |
| raw disk write | `mkfs.*`, `dd … of=/dev/…` | `mkfs.ext4 /dev/sdb1`, `dd if=x of=/dev/sdb` | `dd of=./disk.img` |
| writing directly to a block device | a redirect into `/dev/sd*` or `/dev/nvme*` | `cat x > /dev/nvme0n1` | `cat x > out.bin` |

Try it by hand with the same payload shape the tests send:

```console
$ echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf build && chmod 777 ."}}' \
    | sh .claude/hooks/advisory-guard.sh
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "Advisory (non-blocking): this command looks like it involves recursive force-delete; world-writable permissions. Proceeding, but double-check the target/scope before it runs again unattended."}}
```

It is a tripwire, not a sandbox. Variables, `eval` and scripts get around it easily; it is there to catch accidents. If you want hard blocking, add a separate hook that returns `permissionDecision: "deny"` and keep this one advisory.

</details>

## Have someone else check the work

The session that wrote a change shares every assumption baked into it, so it is the worst judge of whether the change is right. Two subagents do the checking instead, each in its own context.

- **`reviewer` reads the actual diff,** not a description of it, after the work is functionally complete. It checks correctness on real inputs, then safety (anything destructive or out of scope), then convention drift, then simplification. Every finding cites `file:line`, and if there is nothing worth flagging it says so in one line instead of inventing nitpicks.
- **`verifier` checks claims against primary sources.** Give it one or a few concrete claims, such as a flag's behaviour or a changelog fact, and it returns `CORRECT`, `PARTIALLY CORRECT`, `FALSE` or `COULD NOT VERIFY` for each, with the URL, file and line, or command it checked. Official documentation outranks secondary sources; repetition is not evidence.
- **The spec and the limits are written down.** `templates/CLAUDE.md` tells the main session what to hand off (search to `Explore`, factual claims to `verifier`, the final check to `reviewer`), to trust the live machine over stale notes, and to dial effort down for mechanical work.

<picture>
  <source media="(max-width: 700px)" srcset="docs/readme/agents-mobile.webp" />
  <img src="docs/readme/agents.webp" width="100%" alt="A five-step flow: spec and limits, the agent builds, the reviewer reads the diff, the verifier checks the claims, ship. The reviewer and verifier are highlighted as independent, and a dashed line sends findings back to the builder." />
</picture>

<details>
<summary><b>What their answers look like</b></summary>
<br />

Illustrations of the format each agent is told to use:

```text
> Use the reviewer agent on the changes in src/sync/.

src/sync/queue.py:88  retry loop never resets `attempt`, so after the first
failure every later item gets zero retries.
src/sync/queue.py:131 deletes the local copy before the upload is confirmed;
a network error loses the file.
```

```text
> Verify: "git push --force-with-lease refuses to push if the remote ref moved."

CORRECT. git-push(1), --force-with-lease: the push is refused unless the
remote ref still matches the expected value (by default the remote-tracking
branch). Source: https://git-scm.com/docs/git-push
```

</details>

## Skip the prompts only when a person is watching

`bin/claude` sits ahead of the real `claude` on your `PATH`. It adds `--dangerously-skip-permissions` only when all three checks pass. Anything else runs exactly as typed, with the normal prompts.

- **A person at the keyboard.** Both stdin and stdout must be a terminal, so scripts, cron jobs, hooks, CI and piped runs never inherit a session without prompts.
- **Your flags win.** `-p`, `--print`, `--permission-mode` or your own `--dangerously-skip-permissions` turn the injection off.
- **Subcommands stay subcommands.** Claude Code only parses a word as a subcommand when it comes before any root flag. Injecting the flag first would turn `claude attach <id>` into a new session with "attach" as the prompt, so the 21 known subcommands are left alone.

<picture>
  <source media="(max-width: 700px)" srcset="docs/readme/launcher-mobile.webp" />
  <img src="docs/readme/launcher.webp" width="100%" alt="The launcher's decision: three questions (terminal on both ends, no print or permission flag, first word not a subcommand). Any no runs claude exactly as typed; three yeses add --dangerously-skip-permissions. Below, the real output of the seven launcher tests." />
</picture>

```console
$ claude                         # interactive: runs claude --dangerously-skip-permissions
$ claude -p "summarize this"     # print mode: unchanged
$ echo hi | claude               # piped stdin: unchanged
$ claude --permission-mode plan  # explicit mode wins: unchanged
$ claude mcp list                # subcommand: unchanged
```

Skipping prompts is a real trade-off: nothing stops a destructive command except your own review and the advisory hook, which only warns. Use it on machines where you accept that.

## Pick up any session where you left off

`bin/claude-sessions` lists the current project's saved sessions, newest first, each with the exact command to resume it.

- **It cannot go stale.** It reads the transcripts on disk every time instead of keeping its own list.
- **Real titles.** The session's latest AI title, or its first message when there is none.
- **Find one fast.** `claude-sessions upload` filters by title; `-n 5` keeps the five most recent.
- **Follows your setup.** It honours `CLAUDE_CONFIG_DIR`, and `CLAUDE_SESSIONS_DIR` points it at a transcript folder directly.

<picture>
  <source media="(max-width: 700px)" srcset="docs/readme/sessions-mobile.webp" />
  <img src="docs/readme/sessions.webp" width="100%" alt="A terminal in ~/acme-shop runs claude-sessions and lists six sessions with their age, size, title and claude --resume command, then filters to the one whose title mentions the API." />
</picture>

The project and its transcripts in this image are invented; the output is the tool's own.

## Why it is different

|                         | agent-guardrails                                          | The usual way                          |
| ----------------------- | --------------------------------------------------------- | -------------------------------------- |
| Who checks the work     | A reviewer in a fresh context that did not write it       | The session that wrote it              |
| Facts about tools       | Checked against docs, `--help` and source, with a citation | Recalled from training data            |
| Risky shell commands    | Flagged in the agent's context before they run            | Noticed afterwards, if at all          |
| Permission prompts      | Skipped only in interactive terminal sessions             | On for everything, or off for everything |
| When the hook breaks    | Exits 0 silently and stays out of the way                 | A broken gate can stall every command  |

## Quickstart

Requirements: Claude Code, `sh`, `bash` and `python3`. Clone the kit next to your project, then copy what you want.

```sh
git clone https://github.com/gallowforge/agent-guardrails
cd your-project
mkdir -p .claude
cp -r ../agent-guardrails/.claude/agents ../agent-guardrails/.claude/hooks .claude/
cp ../agent-guardrails/.claude/settings.json .claude/settings.json   # or merge its "hooks" block into yours
cat ../agent-guardrails/templates/CLAUDE.md >> CLAUDE.md
```

To use it in every project, copy `agents/` into `~/.claude/agents/` instead and point the hook command at an absolute path in `~/.claude/settings.json`.

The launcher and the session lister are optional:

```sh
mkdir -p "$HOME/.local/bin/claude-wrap"
cp bin/claude bin/claude-sessions "$HOME/.local/bin/claude-wrap/"
export PATH="$HOME/.local/bin/claude-wrap:$PATH"   # ahead of the real claude
```

From the kit's folder, check that everything works:

```sh
cd ../agent-guardrails
bash tests/test-guard.sh      # guard: 29 passed, 0 failed
bash tests/test-launcher.sh   # launcher: 7 passed, 0 failed
```

<details>
<summary><b>What is in the box</b></summary>
<br />

| Path | What it is |
| --- | --- |
| `.claude/agents/reviewer.md` | Subagent: independent review of a finished diff |
| `.claude/agents/verifier.md` | Subagent: fact-checks specific claims against primary sources |
| `.claude/hooks/advisory-guard.sh` | `PreToolUse` hook on `Bash`: warns on risky commands, never blocks |
| `.claude/settings.json` | Wires the hook into Claude Code, with a 5-second timeout |
| `bin/claude` | Launcher: adds `--dangerously-skip-permissions` only for interactive terminal sessions |
| `bin/claude-sessions` | Lists saved sessions for the current project with the exact resume command |
| `templates/CLAUDE.md` | Delegation and verification rules for the main session |
| `tests/` | Hook and launcher tests; `-v` lists every case |
| `scripts/readme-visuals.py` | Rebuilds the images in this README from real runs |

</details>

## Under the hood

| Question | Method |
| --- | --- |
| How does the hook see the command? | Claude Code sends the `PreToolUse` event as JSON on stdin. The hook saves it to a `mktemp` file (removed by a `trap`), and `python3` reads `tool_name` and `tool_input.command`. |
| How are commands matched? | Seven case-insensitive regular expressions with `re.search`, anywhere in the command. Every hit goes into one warning. |
| What does it return? | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "…"}}` and exit 0. `additionalContext` must sit inside `hookSpecificOutput`; at the root Claude Code silently drops it. |
| Why no `permissionDecision`? | `allow` would skip the prompt for the riskiest commands and `deny` would turn a tripwire into a gate. Leaving it out keeps the normal flow. |
| Why `sh` that calls `python3`? | JSON parsing and a lookahead (`--force` but not `--force-with-lease`) without depending on `jq`. The `sh` wrapper lets it exit 0 quietly when `python3` is missing. |
| How does the launcher find the real `claude`? | It removes its own directory from `PATH`, resolves `claude` with `command -v`, and `exec`s it. If none is found it exits 127. |
| How does it decide? | `[ -t 0 ] && [ -t 1 ]`, then a scan of every argument for the print and permission flags, then `$1` against the 21 subcommands. |
| Where do sessions live? | `$CLAUDE_CONFIG_DIR/projects/<current path with every non-alphanumeric character as ->/*.jsonl`, with `~/.claude` as the default. |
| How is a session titled? | The last `ai-title` record wins; otherwise the first user message; trimmed to 58 characters. |
| What keeps the agents independent? | Each runs in its own context with its own tool list: `reviewer` gets Read, Grep, Glob and Bash; `verifier` adds WebFetch and WebSearch. |

## Quality

| Check | What it covers |
| --- | --- |
| 29 hook tests | Both sides of every rule, three hits in one command, a non-`Bash` tool, malformed and empty input. Every warning is also checked for shape: inside `hookSpecificOutput`, prefixed `Advisory (non-blocking)`, with no `permissionDecision`. |
| 7 launcher tests | A stub `claude` prints the arguments it received. Cases run with and without a pseudo-terminal (util-linux `script`): plain, print mode, explicit permission mode, a subcommand. |
| ShellCheck | `--severity=error` on the hook, the launcher and both test scripts. |
| Syntax | `sh -n`, `bash -n` and `python3 -m py_compile`. |
| CI | All of the above on every push and pull request. |
| README images | `scripts/readme-visuals.py` re-runs the hook, the tests and `claude-sessions`, and stops if any example disagrees with the hook's real output. |

<picture>
  <source media="(max-width: 700px)" srcset="docs/readme/tests-mobile.webp" />
  <img src="docs/readme/tests.webp" width="100%" alt="The real output of tests/test-guard.sh -v with all 29 cases, beside every CI step run locally (shell syntax, ShellCheck, Python syntax, guard tests, launcher tests) and a summary: 29 hook cases, 7 launcher cases, 0 failures." />
</picture>

## FAQ

<details>
<summary><b>Does the hook ever block a command?</b></summary>
<br />
No. It only adds a warning to the agent's context and always exits 0. Your normal permission settings decide what runs.
</details>

<details>
<summary><b>Can the agent get around it?</b></summary>
<br />
Yes, easily: a variable, <code>eval</code> or a script hides the pattern. It is a tripwire for accidents, not a sandbox. For hard limits, add a separate hook that returns <code>permissionDecision: "deny"</code>, or run the agent in a container.
</details>

<details>
<summary><b>Why a separate reviewer instead of asking the same session to double-check?</b></summary>
<br />
The session that wrote the change reads it through the same assumptions that produced it. A fresh context sees only the diff, which is what catches the bugs the author is blind to.
</details>

<details>
<summary><b>Is skipping permission prompts safe?</b></summary>
<br />
It is a trade-off you choose per machine. The launcher limits it to sessions with a person at the terminal, and the hook still warns, but nothing stops a destructive command you approve by not looking. Leave <code>bin/</code> off your <code>PATH</code> if you want prompts everywhere.
</details>

<details>
<summary><b>Does it work with other coding agents?</b></summary>
<br />
The files use Claude Code's formats: subagent Markdown, the <code>PreToolUse</code> JSON protocol and <code>settings.json</code>. The ideas carry anywhere: a written spec and limits, review by something that did not write the code, claims checked against sources, and advisory tripwires. The hook's logic is seven regular expressions and easy to port.
</details>

<details>
<summary><b>What happens when Claude Code adds a subcommand?</b></summary>
<br />
The launcher would inject the flag ahead of it. The list sits at the top of <code>bin/claude</code>, and the header comment has the one-line command that regenerates it from <code>claude --help</code>.
</details>

## Contributing

Issues and pull requests are welcome. Run `bash tests/test-guard.sh`, `bash tests/test-launcher.sh` and ShellCheck before opening one; CI runs the same checks. A new rule needs a test on each side: one command it flags and one near miss it lets through. Keep the hook advisory. After a change that affects the images, run `python3 scripts/readme-visuals.py` (it needs Chrome or Chromium and ImageMagick).

## Licence

[MIT](LICENSE).
