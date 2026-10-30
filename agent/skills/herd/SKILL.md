---
name: herd
description: Fan one question out to a herd of leaf pi subagents that graze in parallel inside herdr, then synthesize their findings into one answer. Use when the human asks to herd, fan out, or graze a question across multiple parallel subagents; for ordinary research or a single background leaf, use dev-research instead. Spawns real, costly subagent processes.
---

# Herd

Fan one question out to several leaf pi subagents that **graze** in parallel inside herdr, each writing its findings to a file; then collect and synthesize. You are the only **herder** — leaves graze, they never herd.

It spawns real, costly subagent processes, so run it only when the human actually asked for one — a typed `/skill:herd` or an explicit request to fan work out across parallel subagents. When you do run it, follow every step in order; the plan in step 2 must precede any spawn.

## The shape of a herd

- **Depth 2.** You herd (depth 1); each leaf is depth 2 and cannot herd. Leaves are interactive, full-capability pi on the settings-default model. By default the workspace closes itself once the run finishes ok; the human can ask to keep the leaves open to inspect them or `herdr agent attach` to one.
- **Findings are files.** `herdr-fanout.sh` assigns each leaf `<rundir>/<name>.md` and decides when the leaf is done; native codemode returns the complete leaf results for synthesis. Load [`references/fanout.md`](references/fanout.md) before step 3 for the lifecycle contract.

## The herd, in order

Each step ends on a check. Start the next only when the check passes.

### 1. Pre-flight
Confirm `HERDR_ENV=1` is set; if not, stop — you are not inside herdr.
Confirm you may herd: `HERDR_DEPTH` is unset or less than 2; if it is 2 or more, stop and say you are a leaf and the herd is capped at depth 2.
Load [`references/collection.md`](references/collection.md) for native-codemode activation, the collection bootstrap, response handling, and limitation recovery. Check that native `codemode` is actually advertised; if absent, stop before spawning and give its herder-only activation instructions.
**Done when** the environment, depth, and native tool exposure are confirmed, or you have stopped and told the human why.

### 2. Plan the herd
Before spawning anything, write the plan: the overall question, split into at most **five** independent leaves, and for each leaf — a `name`, a one-line scope, and the role its finding plays in the synthesis. Infer one short slug for the whole herd from the topic.
**Done when** every leaf has all three — name, scope, synthesis role.

### 3. Open the herd and let it graze
Compose the fan-out plan from step 2 as [`references/fanout.md`](references/fanout.md) describes, with `"close": "on_success"` — the workspace closes itself when every leaf is ok. Only when the human explicitly asks to keep the leaves open for inspection, plan `"close": "never"` instead. Build each leaf's `prompt` from this template:

```
Research: <scope>. Use your tools, including web search, as needed. Write your findings as markdown to {{path}}: a 3–5 line summary first, then detailed findings under subheadings, then a Sources list.
```

Use the collection bootstrap with `run: ["--plan", "-"]` and the plan as `input`. It delegates the unchanged lifecycle to `herdr-fanout.sh`: all-success returns the run report and complete leaf results together; partial failure returns only the run report. Drain any `more` pages with `next` before deciding what to do. On a limitation or error, stop and follow the collection reference's recovery.
**Done when** you hold the complete run report's `workspace`, `rundir`, and every leaf's `ok`, `path`, and `reason`, plus any leaf results already collected.

### 4. Retry failures
Re-prompt each `ok:false` leaf through the collection bootstrap's `run: ["--retry-leaf", ...]`, telling it its `reason` (retry template in `fanout.md`). Up to **two retries** per leaf. Drain paged run reports with `next`; retries remain report-only until your decisions are finished.
**Done when** every leaf is ok, or is retired as failed after two retries.

### 5. Collect
If step 3 already returned `complete`, keep those results. Otherwise use the collection bootstrap with `collect: "<rundir>", final: true`, then `next` until `complete`. Record every failed leaf with its final `reason`. Keep the original pages intact; a limitation, truncation, or intervening compaction is a stop, not permission to synthesize from partial findings.
**Done when** collection is `complete`, every successful leaf result is present unchanged across its pages, and every failed leaf is recorded.

### 6. Synthesize
Merge the findings into one answer, crediting which leaf produced what. Name the gaps from failures explicitly.
**Done when** the synthesis accounts for every leaf, success or failure.

### 7. Report
Tell the human the herd is done, with the herdr workspace id (so they can inspect or `herdr agent attach`) and the run directory. Proactively say whether the workspace is still open: when the human asked to keep the leaves, or a leaf failed under `on_success`, the leaves are alive and `herdr-fanout.sh --close <rundir>` cleans up when the human is finished; when it closed itself, say so.
**Done when** both the workspace id and the run directory have been reported.
