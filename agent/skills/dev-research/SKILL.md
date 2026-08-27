---
name: dev-research
description: Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. Use when the user wants a topic researched, docs or API facts gathered, or research delegated to a single background leaf.
---

Delegate the reading to **one background leaf** so you keep working while it reads. pi has no subagent tool — use the **herdr** leaf mechanism the `herd` skill uses. **Launch only when `HERDR_ENV=1` and `HERDR_DEPTH` is unset or `< 2`; if either fails, do the research in your own context.** First inspect the repository's convention for research notes and choose the final findings-file path. Write a JSON plan with a fresh `slug` and exactly one `leaves` entry (`name`, `prompt`, `path`), then run `~/.pi/agent/skills/herd/references/herdr-fanout.sh --plan <plan>`. The leaf's `path` is **absolute**, must match the final write target named in its `prompt`, and must be the final findings file rather than an intermediate result. Use the script's JSON report to verify completion, read the successful leaf's findings file, and verify that the file exists and contains the completion marker. If delegation is unavailable or the leaf does not produce a successful result, finish the research in your own context. After verification, close the autonomous herdr workspace using the workspace id from the report.

The leaf's job:

1. Investigate the entire question against **primary sources** — official docs, source code, specs, first-party APIs — not a secondary write-up of them. Follow every claim back to the source that owns it.
2. Write the findings to a single Markdown file, citing each claim's source.
3. Save it where the repo already keeps such notes; match the existing convention, and if there is none, put it somewhere sensible and say where.
