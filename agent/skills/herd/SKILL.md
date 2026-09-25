---
name: herd
description: Fan one question out to a herd of leaf pi subagents that graze in parallel inside herdr, then synthesize their findings into one answer. Use when the human asks to herd, fan out, or graze a question across multiple parallel subagents; for ordinary research or a single background leaf, use dev-research instead. Spawns real, costly subagent processes.
---

# Herd

Fan one question out to several leaf pi subagents that **graze** in parallel inside herdr, each writing its findings to a file; then collect and synthesize. You are the only **herder** — leaves graze, they never herd.

It spawns real, costly subagent processes, so run it only when the human actually asked for one — a typed `/skill:herd` or an explicit request to fan work out across parallel subagents. When you do run it, follow every step in order; the plan in step 2 must precede any spawn.

## The shape of a herd

- **Depth 2.** You herd (depth 1); each leaf is depth 2 and cannot herd. Leaves are interactive, full-capability pi — they keep every skill and tool, inherit your model, and stay alive after finishing so the human can `herdr agent attach` to debug one.
- **Findings are files.** Each leaf writes one markdown file under a per-run directory, ending with a `<!-- herd:done -->` marker; you read those files back. Nothing rides over the terminal — the marker is the verdict that the leaf finished, because leaf *status* is unreliable while pi retries an inference timeout.
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

### 3. Open the herd and let it graze
Turn the plan into a JSON plan file: a top-level `slug` (required) plus a `leaves` array — one entry per leaf with `name`, `prompt` (its task — use the leaf task-prompt template in [`references/herdr-commands.md`](references/herdr-commands.md)), and `path` (the **absolute** output file from step 2; it must match the path the prompt tells the leaf to write). Run `~/.pi/agent/skills/herd/references/herdr-fanout.sh --plan <plan>`. The script owns the whole mechanical loop and prints one JSON report to stdout (progress to stderr; full plan + report schema in its `--help`). It polls each leaf — never trusting leaf *status*, which flickers mid-retry — until each result file carries the `<!-- herd:done -->` completion marker and the leaf is quiescent; `ok` in the report already encodes that verdict, so do not judge doneness yourself. Read the report: capture the top-level **`workspace`** id and **`rundir`**, and each leaf's **`ok`** and **`path`**. Exit contract: `0` = every leaf ok; `1` = partial success (some `ok:false` → step 4); `2`/`3` = setup failure (bad/missing plan, rundir collision, workspace failure) — fix and rerun (on a rundir collision, pick a fresh slug like `<slug>-2`).
**Done when** you hold the workspace id, the rundir, and each leaf's `ok`/`path` from the report.

### 4. Retry failures
For each `ok:false` leaf, re-prompt the **same** leaf with the reason via the script's retry mode — `herdr-fanout.sh --retry-leaf <name> --path <path> --prompt "<retry task>"` — so the verdict is the same sentinel + quiescence check as the initial fan-out (never judge by leaf status). Up to **two retries** per leaf.
**Done when** every leaf has a valid result file (carrying the completion marker), or is retired as failed after retries.

### 5. Collect
Read each leaf's result file. Record every failed or timed-out leaf with its reason.
**Done when** every non-failed file is read and every failed leaf is recorded.

### 6. Synthesize
Merge the findings into one answer, crediting which leaf produced what. Name the gaps from failures explicitly.
**Done when** the synthesis accounts for every leaf, success or failure.

### 7. Report
Tell the human the herd is done, the herdr workspace id from the report (so they can inspect or `herdr agent attach`), and the run-directory path. Leave the leaves alive; the human can `herdr workspace close <id>` to clean up when finished.
**Done when** both the workspace name and the directory path have been reported.
