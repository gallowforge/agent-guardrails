# Delegation and verification rules

Drop this into a project's `CLAUDE.md` (or `~/.claude/CLAUDE.md`) next to the
`reviewer` and `verifier` agents. It tells the main session when to hand work
off instead of doing everything in one context.

## Delegate by default

- File discovery, codebase and dependency mapping, "where is X": delegate to
  the built-in `Explore` agent. Summaries come back, not raw file dumps. Keep
  main-thread context for decisions and synthesis.
- A specific, falsifiable claim (a library or API's current behavior, a
  changelog fact, a tool's exact syntax): delegate to the `verifier` agent
  instead of asserting from training memory. This is not optional for
  anything time-sensitive or version-specific. Training data goes stale and
  secondhand summaries repeat each other's mistakes. For Claude Code's own
  features, use `claude-code-guide`.
- Independent review before calling something done: delegate to the
  `reviewer` agent, which runs in a fresh context that did not write the code.
  Do not self-review as the final check on anything nontrivial.
- Genuinely independent units of work: parallelize across subagents rather
  than serializing. Do not parallelize work with shared-state dependencies
  just to look fast.

## Verification discipline

Confident summaries of documentation are a common source of invented
features, wrong flag names and nonexistent paths. A summarizing pass over docs
can itself introduce an error while "correcting" another one. Cross-reference
primary sources (official docs, `--help`, source, changelog), not other
summaries, before stating anything as fact.

## Stored notes go stale: check the machine, not the note

Memory files and notes written by earlier or parallel sessions contradict each
other in practice. Before acting on a stored note about system state, verify
it against the live system, and prefer a functional test over a status line
(a service can log a scary error at every start while working fine; a
round-trip through it is the real answer). When a note turns out to be wrong,
correct it in the same session instead of leaving the contradiction for the
next one.

## Effort and model dialing

Drop effort or model explicitly for mechanical, low-stakes subagent work (for
example `Explore` fanning out over many files) rather than running every
delegated task at maximum cost.

## Hooks

`.claude/hooks/advisory-guard.sh` is wired as a `PreToolUse` hook on `Bash`.
It is advisory only: it never denies or blocks, it appends a warning to
context through `hookSpecificOutput.additionalContext` when a command looks
risky. If a hard-block tier is ever wanted, add it as a separate hook rather
than making this one destructive.
