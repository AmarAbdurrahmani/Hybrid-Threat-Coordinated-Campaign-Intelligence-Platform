---
description: Garbage-collect, filter and hand this session's context to a new chat (3 agents)
argument-hint: [next task]
---

Run the context handoff pipeline. Next task: $ARGUMENTS
(If no task was given, use "continue developing and demoing this project".)

Run the three agents strictly in order; each one reads the previous one's file, so wait for each to finish before starting the next:

1. **context-gc** — prompt: "Garbage-collect this project's latest session transcript into .claude/handoff/01-gc.md."
2. **context-filter** — prompt: "Filter .claude/handoff/01-gc.md for the next task: <task>. Write .claude/handoff/02-filtered.md."
3. **context-handoff** — prompt: "Write the handoff brief for the next task: <task> from .claude/handoff/02-filtered.md into .claude/handoff/03-handoff.md."

Then report to the user, briefly: the one-line result from each agent, the path to `.claude/handoff/03-handoff.md`, and the command at the end of that file for starting a new chat with it. Do not paste secret values; the files in `.claude/handoff/` are gitignored and stay local.
