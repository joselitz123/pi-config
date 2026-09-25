---
name: dev-research
description: Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. Use when the user wants a topic researched, docs or API facts gathered, or research delegated to a single background leaf.
---

Delegate the reading to **one background leaf** so you keep working while it reads. pi has no subagent tool — run it through `herdr-fanout.sh --detach`, following [`~/.pi/agent/skills/herd/references/fanout.md`](../herd/references/fanout.md). **Launch only when `HERDR_ENV=1` and `HERDR_DEPTH` is unset or `< 2`; if either fails, do the research in your own context.** First inspect the repository's convention for research notes and choose the final findings-file path. Plan exactly one leaf whose prompt ends with "Write your findings to {{path}}.", with that final findings-file path as its `dest`, and `"close": "on_success"`. Collect the result with `--wait <rundir>`: when the leaf is ok and `delivered`, the findings are at `dest`, ready to read and verify. If delegation is unavailable or the leaf does not produce a successful result, finish the research in your own context.

The leaf's job:

1. Investigate the entire question against **primary sources** — official docs, source code, specs, first-party APIs — not a secondary write-up of them. Follow every claim back to the source that owns it.
2. Write the findings to a single Markdown file, citing each claim's source.
3. Save it to the findings-file path you chose above: a leaf writes to `{{path}}` and the script delivers it to `dest`; in your own context, write it there directly.
