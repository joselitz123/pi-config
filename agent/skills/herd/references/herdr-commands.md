# Herdr commands for a herd

Reference for [`herd`](../SKILL.md). Run these during step 3 (open the herd) and step 4 (let it graze).

herdr returns JSON for create/split/start commands — **capture IDs from the response; never predict them.** These field paths are verified against live responses:

| command | field path |
|---|---|
| `workspace create` | `.result.workspace.workspace_id`, `.result.root_pane.pane_id` |
| `pane split` | `.result.pane.pane_id` |
| `agent list` | `.result.agents[]` — each has `.name`, `.agent_status`, `.state_change_seq` (filter by name to read one leaf) |
| `agent get <target>` | returns **null** detection fields in practice — use `agent list` for `.agent_status`/`.state_change_seq` |

Run `herdr <command> --help` for any flag you are unsure of.

## Automated fan-out (preferred for the full loop)

For a standard fan-out — create workspace, split+start N leaves, dispatch all, **poll each leaf until its result file carries the completion marker**, classify, report — run **[`herdr-fanout.sh`](./herdr-fanout.sh)** with a JSON plan. It owns that whole mechanical loop, including the robust verdict (sentinel + quiescence — it does **not** use `agent wait`); you compose the plan, aggregate the result files, retry failed leaves (same script, `--retry-leaf` mode), and close the workspace. The sections below are the underlying **primitives** — needed for manual control, debugging a stuck leaf, or the non-herdr fallback — and to understand what the script does under the hood.

## Waiting on completion: poll + sentinel (the verdict)

`agent_status` is **not** a reliable completion signal. While pi auto-retries an inference timeout (on by default), a leaf flickers between `working` and `idle`, and herdr's terminal-derived `idle` cannot tell pi's `agent_end` (premature — a retry may follow) from `agent_settled` (truly done). So `herdr-fanout.sh` does **not** use `agent wait`, and never treats a status reading as the verdict. It polls `agent list` on a cadence and treats the **result file** as the verdict:

- Each leaf writes a trailing **`<!-- herd:done -->`** marker as its very last action (the script appends this instruction to every dispatched prompt).
- A leaf is **done** iff: the marker is present **and** `agent_status ∈ {idle, done}` **and** `state_change_seq` is unchanged for the quiescence window (default 15 s).
- Missing, empty, partial, and "not-finished-yet" files all lack the marker → all "not done" → keep polling. Overall timeout (default 600 s) → **failed**.

`agent wait` and `agent prompt --wait` still exist as primitives, but **do not use either as the verdict**. (`prompt --wait` also has a stall gotcha: it requires an observed state change within 5000 ms or returns `agent_prompt_stalled`, a frequent false alarm.) Dispatch with plain `agent prompt` (non-blocking) and let the poll loop decide doneness.

Tuning — all optional, in the plan JSON or via retry flags: `cadence_ms` (default 2000), `quiesce_ms` (default 15000), `timeout_ms` (default 600000).

## Set up

- `SLUG` — the herd slug, from the plan.
- `LEAF_COUNT` — number of leaves (≤ 5).

```bash
RUNDIR="$HOME/.pi/herd/$(date -u +%Y/%m)/$SLUG"
# if $RUNDIR already exists, pick a fresh slug (e.g. "$SLUG-2") until unique this month, then redo RUNDIR
mkdir -p "$RUNDIR"
```

## Start a leaf, with a readiness retry

A new pane is not instantly at its shell prompt — `agent start` fails fast with *"not an available shell"* until it is. So retry the **start** (never the split) until it takes:

```bash
start_leaf () {  # $1 = leaf name, $2 = pane id
  local name="$1" pane="$2" n=0
  until herdr agent start "$name" --kind pi --pane "$pane"; do
    n=$((n+1)); [ "$n" -ge 20 ] && { echo "gave up starting $name on $pane" >&2; return 1; }
    sleep 1
  done
}
```

## Create the workspace and leaf 1

A workspace ships with one tab and a root pane; use that root pane for leaf 1. `--env HERDR_DEPTH=2` makes the leaf a depth-2 agent that cannot herd.

```bash
WS=$(herdr workspace create --cwd "$PWD" --label "$SLUG" --env HERDR_DEPTH=2 --no-focus)
P1=$(printf '%s\n' "$WS" | jq -r '.result.root_pane.pane_id')
start_leaf leaf-1 "$P1"
```

## Create the remaining leaves (splits)

`pane split` creates the pane the instant it succeeds — capture the id immediately. If `start_leaf` then fails, it retries the start on that same id. **Do not split again on a failed start** — that orphans a pane with no agent.

```bash
i=2
while [ "$i" -le "$LEAF_COUNT" ]; do
  SP=$(herdr pane split "$P1" --direction right --env HERDR_DEPTH=2 --no-focus)
  PN=$(printf '%s\n' "$SP" | jq -r '.result.pane.pane_id')
  start_leaf "leaf-$i" "$PN"
  i=$((i+1))
done
```

## Prompt a leaf, then judge by the sentinel

Prefer `herdr-fanout.sh` for both the initial fan-out and retries — it dispatches non-blocking and polls for the `<!-- herd:done -->` sentinel + quiescence, so "done" is decided once, consistently. The verdict is **never** a status reading: a leaf succeeded iff its result file contains the sentinel.

```bash
leaf_done () { local p="$1"; [ -f "$p" ] && grep -qF '<!-- herd:done -->' "$p"; }

herdr agent prompt leaf-1 "$TASK"   # non-blocking; the script appends the marker instruction
# then poll agent list + leaf_done "$PATH1" until: sentinel present AND
# state_change_seq unchanged for the quiescence window, OR the timeout.
# (That loop is exactly what herdr-fanout.sh / its --retry-leaf mode does.)
```

If you dispatch manually, do **not** trust `agent prompt --wait`'s return — `agent_prompt_stalled` is a frequent false alarm. Judge solely by `leaf_done` plus a quiescence window on `state_change_seq`.

### Leaf task-prompt template

Fill `<scope>` and `<path>` from the plan entry:

```
Research: <scope>. Use your tools, including web search, as needed. Write your findings as markdown to <path>: a 3–5 line summary first, then detailed findings under subheadings, then a Sources list. Write only to that file. Stop when you are done.
```

`herdr-fanout.sh` appends the `<!-- herd:done -->` completion-marker instruction to every prompt for you (initial dispatch and retries), so you do **not** put it in the prompt by hand. If you dispatch a leaf manually with the primitives, add the instruction yourself: tell the leaf to write exactly `<!-- herd:done -->` as the final line when it is finished.

## Retry a failed leaf

Re-prompt the **same** leaf — it is interactive and remembers its prior attempt — through the fan-out script's retry mode, so the verdict is identical to the initial fan-out (sentinel + quiescence, not `prompt --wait`). Up to two retries.

```bash
herdr-fanout.sh --retry-leaf leaf-1 --path "$PATH1" --prompt "$RETRY_TASK"
# prints {"name":...,"status":...,"ok":...,"path":...,"bytes":...}; exit 0 iff ok
# optional: --timeout-ms / --cadence-ms / --quiesce-ms
```

Retry template (fill `<reason>`, `<scope>`, `<path>`):

```
Your previous attempt did not produce a result: <reason>. Research <scope> again and write your findings to <path>. If a tool fails (for example web search returns 401), say so in the file and continue with what you can.
```

## Clean up

Leaves stay alive after their task — the human can `herdr agent attach <name>` to inspect or debug one in place. When a run is finished, close its workspace to free the panes and agents:

```bash
herdr workspace close <id>     # <id> is the report's `workspace` field (e.g. wA)
```

Decide per skill: an interactive, human-gated run (like `herd`) may leave leaves alive for inspection and close later; an autonomous fan-out closes once it has finished aggregating.

## Read a result / inspect the herd

```bash
# in pi, just read the path the leaf wrote to
herdr agent read leaf-1                                      # a leaf's live terminal (handy when one is stuck)
herdr agent list | jq -r '.result.agents[] | "\(.name)\t\(.agent_status)"'
herdr agent list | jq '.result.agents[] | select(.name=="leaf-1") | {status: .agent_status, seq: .state_change_seq}'
```

## Notes

- `agent start` needs the pane at an interactive shell prompt — that is exactly what `start_leaf`'s retry loop waits out. There is no separate "wait for ready" command.
- **Doneness is the sentinel, not the status.** `agent_status` flickers during pi's inference-retry cycle, so `herdr-fanout.sh` polls and requires `<!-- herd:done -->` + a quiescence window. Never treat a single `idle` as completion.
- Leaves are interactive and stay alive after their task; the human can `herdr agent attach leaf-1` to debug a worker in place.
- herdr must be running under `bws run` for leaves to inherit credentials (see the skill's *shape* section). The telltale of a non-bws server is leaves that call the model fine but 401 on tavily.
