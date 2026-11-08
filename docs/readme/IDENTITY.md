# Identity: the level crossing

**One idea.** agent-guardrails is a level crossing between a coding agent and your machine. Most traffic crosses in silence. A risky command meets a lowered barrier, flashing lights and a sign, and the sign is the hook's real warning. It is a half barrier on purpose: the hook warns and never blocks, and the drawings say so.

**Motif.** Railway and road signage, drawn face on and in signal-box diagrams: the crossbuck (it reads BASH ONLY / CROSSING, because the hook matches the Bash tool), twin red lamps, a red and white striped arm, a LOOK when lights show plate where the real one says STOP, lineside number boards, warning triangles with supplementary plates, a keepers' track panel with two lit signal boxes, a junction for the launcher, a train register for sessions and an inspection certificate for the tests. Hazard stripes appear once, at the foot of the post.

**Type.** Barlow and Barlow Condensed: a slightly rounded grotesk drawn from California highway signs, whose condensed width plays the role DIN 1451 Engschrift plays on European signs. One family in two widths gives sign legends and body text. Red Hat Mono is used for every command and line of output; its open, round shapes sit with Barlow, so a command reads as part of the sign.

**Palette.** 60 / 30 / 10.

| Role | Colour | Hex |
| --- | --- | --- |
| Field | Asphalt | `#141413` |
| Support | Safety yellow | `#ffc61a` |
| Support | Reflective white | `#f2f0ea` |
| Accent, spent only on danger: lit lamps, the arm, flagged commands, stamps | Signal red | `#e2231a` |
| Printed matter (rulebook, register, certificate) | Paper / ink | `#ece6d8` / `#191816` |

**Voice.** Stern, with dry humour. Short declaratives in sign grammar ("Every match still runs.", "Nobody marks their own homework.", "The gate opens only for a person."). Jokes come from accuracy, never from exaggeration: the half-barrier dimension line, NO SCRIPTS beyond this point, a WARNS ONLY plate under the zero, SPECIMEN stamped on the agents' report formats.

**Signature visual.** The barrier arm lowered across `rm -rf ./build ~/`, with the hook's exact `additionalContext` underneath, the near misses (`rm -r old-dir`, `chmod 755 script.sh`) crossing in silence at exit 0, and a yellow dimension line measuring the gap the agent can still drive through.

**Truth.** Every command, warning, count, test line and session row is produced by `scripts/readme-visuals.py` from real runs. The report formats in the keepers' image are marked SPECIMEN because they come from the agent files, not from a run.
