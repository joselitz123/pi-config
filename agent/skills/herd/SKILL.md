---
name: herd
description: Spawn and supervise a herd of leaf pi subagents through herdr to research a question in parallel, then synthesize their findings into one answer.
disable-model-invocation: true
---

# Herd

Fan one question out to several leaf pi subagents that **graze** in parallel inside herdr, each writing its findings to a file; then collect and synthesize. You are the only **herder** — leaves graze, they never herd.

This runs only because the human typed `/skill:herd`. It spawns real, costly subagent processes, so follow every step in order; the plan in step 2 must precede any spawn.

## The shape of a herd

- **Depth 2.** You herd (depth 1); each leaf is depth 2 and cannot herd. Leaves are interactive, full-capability pi — they keep every skill and tool, inherit your model, and stay alive after finishing so the human can `herdr agent attach` to debug one.
- **Findings are files.** Each leaf writes one markdown file under a per-run directory; you read those files back. Nothing rides over the terminal.
- **Secrets live at the server.** herdr must have been launched under `bws run` so leaves inherit credentials. You cannot fully verify this up front — if leaves later think but cannot web-search (tavily 401s), the server was not bws-launched; tell the human.

## The herd, in order

Each step ends on a check. Start the next only when the check passes.

### 1. Pre-flight
Confirm `HERDR_ENV=1` is set; if not, stop — you are not inside herdr.
Confirm you may herd: `HERDR_DEPTH` is unset or less than 2; if it is 2 or more, stop and say you are a leaf and the herd is capped at depth 2.
**Done when** both are confirmed, or you have stopped and told the human why.

### 2. Plan the herd
Before spawning anything, write the plan: the overall question, split into at most **five** independent leaves, and for each leaf — a one-line scope, the output file path it will write, and the role its finding plays in the synthesis. Infer one short, filesystem-safe slug for the whole herd from the topic.
**Done when** every leaf has all three — scope, output path, synthesis role.

### 3. Open the herd
Create the run directory `~/.pi/herd/yyyy/mm/<slug>/` (suffix the slug if the name is already taken this month). Create a herdr workspace named `<slug>`, and start one interactive `pi` leaf per plan entry, each in its own pane with `HERDR_DEPTH=2`. A new pane is not instantly ready to host an agent — start each leaf with a readiness retry, not a blind sleep.
The exact commands and the leaf task-prompt are in [`references/herdr-commands.md`](references/herdr-commands.md) — read it before creating anything.
**Done when** the directory and workspace exist, and every leaf's `pi` is started and detected by herdr.

### 4. Let the herd graze
Prompt each leaf with its task (`herdr agent prompt <leaf> "<task>" --wait --until idle --timeout 600000`), then judge success by **the result file**, not by the wait's return — `agent_prompt_stalled` is often a false alarm. A leaf succeeded only if its result file exists and is valid markdown; corroborate with the leaf's `state_change_seq` (it advanced = the leaf did work). Retry only on a missing or invalid file: re-prompt the **same** leaf with the reason, up to **two retries**.
**Done when** every leaf has a valid result file, or is retired as failed after retries.

### 5. Collect
Read each leaf's result file. Record every failed or timed-out leaf with its reason.
**Done when** every non-failed file is read and every failed leaf is recorded.

### 6. Synthesize
Merge the findings into one answer, crediting which leaf produced what. Name the gaps from failures explicitly.
**Done when** the synthesis accounts for every leaf, success or failure.

### 7. Report
Tell the human the herd is done, the herdr workspace name (so they can inspect or `attach`), and the run-directory path. Leave the leaves alive.
**Done when** both the workspace name and the directory path have been reported.
