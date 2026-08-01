---
name: dev-research
description: Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. Use when the user wants a topic researched, docs or API facts gathered, or reading legwork delegated to a background agent.
---

Delegate the reading to **background leaf sub-agents** so you keep working while they read. pi has no subagent tool — fan out via the **herdr** leaf mechanism the `herd` skill uses. **Spawn only when `HERDR_ENV=1` and `HERDR_DEPTH` is unset or `< 2`; if either fails, do the research in your own context.** Write a JSON plan — a fresh `slug` plus a `leaves` array of one entry per leaf (`name`, `prompt`, `path`) — and run `~/.pi/agent/skills/herd/references/herdr-fanout.sh --plan <plan>`. Each leaf's `path` is **absolute** and must match the write target named in its `prompt`. The script owns the whole mechanical loop and prints a JSON report (exit `0` iff every leaf is ok; `1` = partial success — read the report's per-leaf `ok`/`path`, not `$?`; full schema in `--help`). Read each `ok:true` leaf's **findings file**; any `ok:false` leaf gets a tailored retry. Retries and workspace cleanup live in `~/.pi/agent/skills/herd/references/herdr-commands.md`.

**Leaf count is adaptive.** A single-threaded question with one source family → one leaf. A question that decomposes into independent sub-questions → one leaf per sub-question, up to five (herd's cap); each investigates its slice and writes its own cited file. More than five → take the five strongest, or tell the user it's herd-scale. Then synthesize the per-leaf files into the single findings file below.

Each leaf's job:

1. Investigate its slice against **primary sources** — official docs, source code, specs, first-party APIs — not a secondary write-up of them. Follow every claim back to the source that owns it.
2. Write its findings to a Markdown file, citing each claim's source.
3. Save it where the repo already keeps such notes; match the existing convention, and if there is none, put it somewhere sensible and say where.

After the leaves finish, merge their per-leaf files into one cited findings file — dedupe the claims, keep a single sources list.
