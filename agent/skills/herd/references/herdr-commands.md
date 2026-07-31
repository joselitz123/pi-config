# Herdr commands for a herd

Reference for [`herd`](../SKILL.md). Run these during step 3 (open the herd) and step 4 (let it graze).

herdr returns JSON for create/split/start commands — **capture IDs from the response; never predict them.** These field paths are verified against live responses:

| command | field path |
|---|---|
| `workspace create` | `.result.root_pane.pane_id` |
| `pane split` | `.result.pane.pane_id` |
| `agent list` | `.result.agents[]` |
| `agent get <name>` | `.result.status`, `.result.state_change_seq` |

Run `herdr <command> --help` for any flag you are unsure of.

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

## Prompt a leaf, then judge by the file

`--wait`'s return is **not** the verdict — `agent_prompt_stalled` is often a false alarm. The leaf succeeded iff its result file exists and is non-trivial; corroborate with `state_change_seq`, which should advance.

```bash
leaf_ok () { local p="$1"; [ -s "$p" ] && [ "$(wc -c < "$p")" -gt 100 ]; }

seq_before=$(herdr agent get leaf-1 | jq -r '.result.state_change_seq')
herdr agent prompt leaf-1 "$TASK" --wait --until idle --timeout 600000 || true
seq_after=$(herdr agent get leaf-1 | jq -r '.result.state_change_seq')

if leaf_ok "$PATH1"; then
  echo "leaf-1 ok (file written; seq $seq_before->$seq_after)"
else
  echo "leaf-1 missing/invalid file -> retry"
fi
```

If `--wait` reports `agent_prompt_stalled` but the file is valid and `seq_after` advanced, it is a false alarm — treat it as success and do **not** re-prompt.

### Leaf task-prompt template

Fill `<scope>` and `<path>` from the plan entry:

```
Research: <scope>. Use your tools, including web search, as needed. Write your findings as markdown to <path>: a 3–5 line summary first, then detailed findings under subheadings, then a Sources list. Write only to that file. Stop when you are done.
```

## Retry a failed leaf

Re-prompt the **same** leaf — it is interactive and remembers its prior attempt — then judge by the file again. Up to two retries.

```bash
herdr agent prompt leaf-1 "$RETRY_TASK" --wait --until idle --timeout 600000 || true
# then re-check: leaf_ok "$PATH1"
```

Retry template (fill `<reason>`, `<scope>`, `<path>`):

```
Your previous attempt did not produce a result: <reason>. Research <scope> again and write your findings to <path>. If a tool fails (for example web search returns 401), say so in the file and continue with what you can.
```

## Read a result / inspect the herd

```bash
# in pi, just read the path the leaf wrote to
herdr agent read leaf-1                                      # a leaf's live terminal (handy when one is stuck)
herdr agent list      | jq -r '.result.agents[] | "\(.name)\t\(.status)"'
herdr agent get leaf-1 | jq '{status: .result.status, seq: .result.state_change_seq}'
```

## Notes

- `agent start` needs the pane at an interactive shell prompt — that is exactly what `start_leaf`'s retry loop waits out. There is no separate "wait for ready" command.
- Leaves are interactive and stay alive after their task; the human can `herdr agent attach leaf-1` to debug a worker in place.
- herdr must be running under `bws run` for leaves to inherit credentials (see the skill's *shape* section). The telltale of a non-bws server is leaves that call the model fine but 401 on tavily.
