# agent-guardrails: the README's look

**Family.** The README is drawn in Glass, the language of the gallowforge profile: real output as frosted-glass windows floating in a dark, softly lit space, with the one line that matters lifted out of its window toward you. aih, agent-guardrails and bwtidy share the renderer, the type and the composition grammar; each has its own accent light.

**The idea.** A warning, never a wall. The hero pipes one risky command into the real hook and lifts its advisory out of the terminal, with `echo $?` printing 0 underneath: the command is flagged and still allowed. It plays as a loop: the command is typed, the reply arrives and the advisory lifts. The loop starts on the finished output and holds it, so a paused image is the whole picture.

**Accent.** Amber, the colour of caution rather than stop, because nothing in this kit blocks: `#ffc370` on dark windows, `#9a5c06` on light pages, and a pool of `#b8740f` light behind the glass. It marks only what the hook flagged and the one line each scene is about. There is no red anywhere.

**Type.** Geist for headings and copy, Geist Mono for every line of output and every file.

**Themes.** Each image has a dark version (on GitHub's `#0d1117`) and a light one (on `#ffffff`), with phone crops; the edges dissolve into the page. Terminals stay dark in both themes; the agent files, `settings.json` and `bin/claude` are dark pages in the dark version and light pages in the light one.

**Truth.** `scripts/readme-visuals.py` runs the hook, every CI step, both test suites and `claude-sessions` (on an invented project) for real and writes their output word for word into `docs/readme/src/tex/`. The agent and source files are shown as they are in the repository. Every number in the copy is counted from the hook, the tests or the agents folder.

**Rebuild.** `python3 scripts/readme-visuals.py`. The renderer is `docs/readme/src/glass/`: `scene.js` (three.js compositor), `grammar.js` (light, glass, type and compositions), `layouts.js` (this README's scenes) and `glass.cjs` (textures, scenes and WebP; the hero loop is 96 frames, 8 seconds, encoded with ffmpeg). `ONLY=hero node docs/readme/src/glass/glass.cjs scenes` renders the hero alone.
