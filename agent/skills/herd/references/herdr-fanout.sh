#!/usr/bin/env bash
#
# herdr-fanout.sh — automated leaf fan-out / retry over herdr.
#
# Owns the MECHANICAL loop only. Two modes:
#   fanout (default): create workspace -> split+start N leaves -> dispatch all
#                     (non-blocking) -> POLL each leaf until its result file
#                     carries the completion sentinel AND the leaf is quiescent
#                     -> report.
#   retry:            re-prompt one existing leaf -> POLL it with the SAME
#                     verdict -> single-leaf report. Use for step-4 retries so
#                     "done" means the same thing as in fanout.
#
# Why polling + sentinel (not `agent wait`, not a byte count): a leaf's
# `agent_status` is unreliable while pi auto-retries an inference timeout — it
# flickers between working and idle, and herdr's terminal-derived `idle`
# conflates pi's `agent_end` (premature; a retry may follow) with
# `agent_settled` (truly done). So we never treat a status reading as the
# verdict. The verdict is the result FILE: each leaf writes a trailing
# `<!-- herd:done -->` marker as its very last action. A leaf is "done" only
# when that marker is present AND the leaf is settled (status idle/done) AND
# `state_change_seq` has not changed for the quiescence window. Missing,
# empty, partial, and "not-finished-yet" files all lack the marker and so all
# collapse to "not done". Bounded by an overall timeout (-> failed).
#
# Does NOT own (the calling skill's job): the pre-flight guard, plan
# composition, aggregation, cleanup. See herdr-commands.md.
#
# Usage:
#   herdr-fanout.sh --plan plan.json
#   herdr-fanout.sh --retry-leaf <name> --path <abs> --prompt <text>
#   herdr-fanout.sh --help
#
# Plan schema (JSON, --plan <path>):
#   {
#     "slug": "dev-research-test",        # -> rundir ~/.pi/herd/YYYY/MM/<slug>
#     "leaf_depth": 2,                    # optional, default 2
#     "cadence_ms": 2000,                 # optional, poll interval, default 2000
#     "quiesce_ms": 15000,                # optional, settle window, default 15000
#     "timeout_ms": 600000,               # optional, per-leaf timeout, default 600000
#     "leaves": [
#       {"name":"leaf-1","prompt":"...write to /abs/rs-1.md","path":"/abs/rs-1.md"},
#       {"name":"leaf-2","prompt":"...","path":"/abs/rs-2.md"}
#     ]
#   }
#
# Retry mode args:
#   --retry-leaf <name>   the existing leaf to re-prompt
#   --path <abs>          the leaf's result file (watched for the sentinel)
#   --prompt <text>       the retry task (the script appends the sentinel footer)
#   --timeout-ms / --cadence-ms / --quiesce-ms   optional overrides
#
# Contract: each leaf's `prompt` MUST tell the leaf where to write, and the
# plan's `path` MUST match it. `path` is absolute. The script appends a
# completion-marker instruction to every dispatched prompt (initial and retry),
# so the leaf is told to write `<!-- herd:done -->` last regardless of what the
# plan prompt says. Slug collision is refused (deterministic; pick a fresh slug).
#
# Output: stdout = JSON report ONLY (schema below); human progress -> stderr.
# exit 0 iff the leaf/leaves are ok, else 1 (partial success is normal).
#
#   fanout: {"workspace":"wA","rundir":"...","ok":false,
#            "leaves":[{"name":"leaf-1","status":"idle","ok":true,"path":"...","bytes":14302},
#                      {"name":"leaf-2","status":"idle","ok":false,"path":"...","bytes":0}]}
#   retry:  {"name":"leaf-2","status":"idle","ok":true,"path":"...","bytes":9012}
#
# `ok` encodes the full verdict: status idle/done AND the sentinel present.

set -uo pipefail

usage() {
  sed -n '2,/^set -uo pipefail/p' "$0" 2>/dev/null \
    | sed '/^set -uo pipefail/d' | sed 's/^# \?//' | sed -n '/^Usage:/,$p' || true
  cat <<'EOF'

Run from a skill that has already done its pre-flight guard (HERDR_ENV=1 and
HERDR_DEPTH<2). This script is mechanism, not policy.
EOF
}

# --- constants ---
SENTINEL_MARKER='<!-- herd:done -->'
SENTINEL_FOOTER=$'\n\nWhen you are completely finished, write your full findings to your file, then add one final line containing exactly `<!-- herd:done -->` as the very last line of the file. Write that marker only once, at the very end, after all your content, then stop.'

# --- args ---
MODE="fanout"
PLAN=""
RETRY_LEAF=""
RETRY_PATH=""
RETRY_PROMPT=""
RETRY_TIMEOUT=""
RETRY_CADENCE=""
RETRY_QUIESCE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --plan)       PLAN="${2:-}"; MODE="fanout"; shift 2 ;;
    --retry-leaf) RETRY_LEAF="${2:-}"; MODE="retry"; shift 2 ;;
    --path)       RETRY_PATH="${2:-}"; shift 2 ;;
    --prompt)     RETRY_PROMPT="${2:-}"; shift 2 ;;
    --timeout-ms) RETRY_TIMEOUT="${2:-}"; shift 2 ;;
    --cadence-ms) RETRY_CADENCE="${2:-}"; shift 2 ;;
    --quiesce-ms) RETRY_QUIESCE="${2:-}"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v jq    >/dev/null || { echo "herdr-fanout: jq required" >&2; exit 2; }
command -v herdr >/dev/null || { echo "herdr-fanout: herdr required" >&2; exit 2; }

# --- helpers ---
log() { printf '%s\n' "$*" >&2; }     # progress -> stderr (stdout stays pure JSON)
file_bytes() { local p="$1"; if [ -s "$p" ]; then wc -c < "$p" | tr -d ' '; else echo 0; fi; }
# file_done: the verdict. Sentinel present in a real file. Missing/empty/partial -> false.
file_done() { local p="$1"; [ -f "$p" ] && grep -qF "$SENTINEL_MARKER" "$p"; }

# agent_state <name> -> echoes "<status> <seq>" via `agent list` (filtered by name).
agent_state() {
  local raw s seq
  raw=$(herdr agent list 2>/dev/null | jq -c --arg n "$1" '.result.agents[] | select(.name==$n)' 2>/dev/null || true)
  s=$(printf '%s' "$raw" | jq -r '.agent_status // "unknown"' 2>/dev/null); [ -z "$s" ] && s=unknown
  seq=$(printf '%s' "$raw" | jq -r '.state_change_seq // 0' 2>/dev/null); [ -z "$seq" ] && seq=0
  printf '%s %s' "$s" "$seq"
}
agent_status() { local s="" x=""; read -r s x < <(agent_state "$1"); printf '%s' "$s"; }

# Retry the START (never the split) until the pane is at a shell prompt.
start_leaf() {
  local name="$1" pane="$2" n=0
  until herdr agent start "$name" --kind pi --pane "$pane" >/dev/null 2>&1; do
    n=$((n+1)); [ "$n" -ge 20 ] && { log "  gave up starting $name on $pane"; return 1; }
    sleep 1
  done
}

# wait_for_leaf <name> <path> <timeout_ms> -> echoes final agent_status.
# Polls every CADENCE_MS. Done = sentinel present AND status idle/done AND
# state_change_seq unchanged for QUIESCE_MS. No `agent wait`; no status-only
# verdict. Bounded by timeout -> returns whatever status it last saw (failed).
wait_for_leaf() {
  local name="$1" path="$2" timeout_ms="$3"
  local cadence_s=$(( CADENCE_MS / 1000 )); [ "$cadence_s" -lt 1 ] && cadence_s=1
  local quiesce_s=$(( QUIESCE_MS / 1000 )); [ "$quiesce_s" -lt 1 ] && quiesce_s=1
  local deadline=$(( $(date +%s) + timeout_ms / 1000 + 1 ))
  local now status="" seq="" tracked_seq="" settle_at=0
  while :; do
    now=$(date +%s); [ "$now" -ge "$deadline" ] && break
    read -r status seq < <(agent_state "$name")
    if file_done "$path" && { [ "$status" = idle ] || [ "$status" = done ]; }; then
      # conditions for done hold; measure how long the seq has been frozen
      if [ "$seq" != "$tracked_seq" ]; then
        tracked_seq="$seq"; settle_at=$(( now + quiesce_s ))   # (re)start clock on any seq change
      fi
      [ "$now" -ge "$settle_at" ] && break                      # held quiescent past the window -> done
    else
      tracked_seq=""                                            # not done yet; reset the clock
    fi
    sleep "$cadence_s"
  done
  agent_status "$name"
}

# ============================================================================
# retry mode
# ============================================================================
run_retry() {
  [ -n "$RETRY_LEAF" ]   || { echo "herdr-fanout: --retry-leaf needs a name" >&2; exit 2; }
  [ -n "$RETRY_PATH" ]   || { echo "herdr-fanout: --retry-leaf needs --path <abs>" >&2; exit 2; }
  [ -n "$RETRY_PROMPT" ] || { echo "herdr-fanout: --retry-leaf needs --prompt <text>" >&2; exit 2; }
  CADENCE_MS="${RETRY_CADENCE:-2000}"
  QUIESCE_MS="${RETRY_QUIESCE:-15000}"
  local timeout_ms="${RETRY_TIMEOUT:-600000}"
  log "retrying $RETRY_LEAF (path=$RETRY_PATH, poll=${CADENCE_MS}ms, quiesce=${QUIESCE_MS}ms, timeout=${timeout_ms}ms)…"
  herdr agent prompt "$RETRY_LEAF" "$(printf '%s%s' "$RETRY_PROMPT" "$SENTINEL_FOOTER")" >/dev/null 2>&1 \
    || log "  warn: retry prompt dispatch returned nonzero for $RETRY_LEAF"
  local st bytes ok=false
  st=$(wait_for_leaf "$RETRY_LEAF" "$RETRY_PATH" "$timeout_ms")
  bytes=$(file_bytes "$RETRY_PATH")
  { { [ "$st" = idle ] || [ "$st" = done ]; } && file_done "$RETRY_PATH"; } && ok=true
  log "  $RETRY_LEAF: $st  ok=$ok  bytes=$bytes"
  jq -n -c --arg n "$RETRY_LEAF" --arg s "$st" --argjson ok "$ok" --arg p "$RETRY_PATH" --argjson b "$bytes" \
    '{name:$n,status:$s,ok:$ok,path:$p,bytes:$b}'
  [ "$ok" = true ]
}

# ============================================================================
# fanout mode
# ============================================================================
run_fanout() {
  [ -n "$PLAN" ] || { echo "herdr-fanout: fanout mode needs --plan <path>" >&2; exit 2; }
  [ -f "$PLAN" ] || { echo "herdr-fanout: plan not found: $PLAN" >&2; exit 2; }

  # --- plan ---
  SLUG=$(jq -r '.slug // empty'            "$PLAN")
  LEAF_DEPTH=$(jq -r '.leaf_depth // 2'      "$PLAN")
  CADENCE_MS=$(jq -r '.cadence_ms // 2000'   "$PLAN")
  QUIESCE_MS=$(jq -r '.quiesce_ms // 15000'  "$PLAN")
  TIMEOUT_MS=$(jq -r '.timeout_ms // 600000' "$PLAN")
  LEAF_COUNT=$(jq '.leaves | length'         "$PLAN")
  [ -n "$SLUG" ]              || { echo "herdr-fanout: plan missing .slug" >&2; exit 2; }
  [ "$LEAF_COUNT" -ge 1 ] 2>/dev/null || { echo "herdr-fanout: plan needs >=1 leaf" >&2; exit 2; }
  [ "$LEAF_COUNT" -le 5 ] 2>/dev/null || { echo "herdr-fanout: plan has >5 leaves (herdr cap)" >&2; exit 2; }

  # --- rundir (refuse on collision) ---
  RUNDIR="$HOME/.pi/herd/$(date -u +%Y/%m)/$SLUG"
  if [ -e "$RUNDIR" ]; then
    echo "herdr-fanout: rundir already exists: $RUNDIR — pick a fresh slug" >&2; exit 2
  fi
  mkdir -p "$RUNDIR/.state"

  # --- leaf fields into arrays ---
  declare -a NAMES
  declare -A PROMPT PATH_
  last=""
  i=0
  while [ "$i" -lt "$LEAF_COUNT" ]; do
    n=$(jq -r ".leaves[$i].name"  "$PLAN")
    p=$(jq -r ".leaves[$i].path"  "$PLAN")
    m=$(jq -r ".leaves[$i].prompt" "$PLAN")
    [ -n "$n" ] || { echo "herdr-fanout: leaf[$i] missing .name" >&2; exit 2; }
    [ -n "$p" ] || { echo "herdr-fanout: leaf[$i] missing .path" >&2; exit 2; }
    [ -n "$m" ] || { echo "herdr-fanout: leaf[$i] missing .prompt" >&2; exit 2; }
    case "$last" in *",$n,"*) echo "herdr-fanout: duplicate leaf name: $n" >&2; exit 2 ;; esac
    last="$last,$n,"
    NAMES[$i]="$n"; PROMPT["$n"]="$m"; PATH_["$n"]="$p"
    i=$((i+1))
  done

  # --- create workspace + leaf 1 on the root pane ---
  log "creating workspace '$SLUG' (depth=$LEAF_DEPTH, $LEAF_COUNT leaves, poll=${CADENCE_MS}ms, quiesce=${QUIESCE_MS}ms)…"
  WS_RAW=$(herdr workspace create --cwd "$PWD" --label "$SLUG" \
    --env "HERDR_DEPTH=$LEAF_DEPTH" --no-focus)
  WS_ID=$(printf '%s'     "$WS_RAW" | jq -r '.result.workspace.workspace_id // empty')
  ROOT_PANE=$(printf '%s' "$WS_RAW" | jq -r '.result.root_pane.pane_id // empty')
  [ -n "$WS_ID" ] && [ -n "$ROOT_PANE" ] || {
    echo "herdr-fanout: could not parse workspace create response" >&2; printf '%s\n' "$WS_RAW" >&2; exit 3; }
  log "  workspace $WS_ID; root pane $ROOT_PANE"

  if ! start_leaf "${NAMES[0]}" "$ROOT_PANE"; then
    echo "herdr-fanout: failed to start ${NAMES[0]}" >&2; exit 3
  fi

  # --- split + start leaves 2..N ---
  i=1
  while [ "$i" -lt "$LEAF_COUNT" ]; do
    SP=$(herdr pane split "$ROOT_PANE" --direction right --env "HERDR_DEPTH=$LEAF_DEPTH" --no-focus)
    PN=$(printf '%s' "$SP" | jq -r '.result.pane.pane_id // empty')
    if [ -z "$PN" ]; then
      echo "herdr-fanout: could not parse pane split response" >&2; printf '%s\n' "$SP" >&2; exit 3
    fi
    start_leaf "${NAMES[$i]}" "$PN" || { echo "herdr-fanout: failed to start ${NAMES[$i]}" >&2; exit 3; }
    i=$((i+1))
  done

  # --- dispatch all prompts (non-blocking); append the sentinel footer ---
  log "dispatching prompts (non-blocking)…"
  i=0
  while [ "$i" -lt "$LEAF_COUNT" ]; do
    n="${NAMES[$i]}"
    herdr agent prompt "$n" "$(printf '%s%s' "${PROMPT[$n]}" "$SENTINEL_FOOTER")" >/dev/null 2>&1 \
      || log "  warn: prompt dispatch returned nonzero for $n"
    i=$((i+1))
  done

  # --- poll each leaf until done/failed, in parallel ---
  log "polling leaves until result files carry the completion marker…"
  i=0
  while [ "$i" -lt "$LEAF_COUNT" ]; do
    n="${NAMES[$i]}"
    ( status_of_leaf=$(wait_for_leaf "$n" "${PATH_[$n]}" "$TIMEOUT_MS"); printf '%s' "$status_of_leaf" > "$RUNDIR/.state/$n" ) &
    i=$((i+1))
  done
  wait
  log "all leaves resolved; classifying…"

  # --- classify + build report ---
  LEAVES_JSON="[]"
  ALL_OK=true
  i=0
  while [ "$i" -lt "$LEAF_COUNT" ]; do
    n="${NAMES[$i]}"; p="${PATH_[$n]}"
    st=$(cat "$RUNDIR/.state/$n" 2>/dev/null || echo unknown)
    [ -z "$st" ] && st=unknown
    bytes=$(file_bytes "$p")
    ok=false
    if { [ "$st" = idle ] || [ "$st" = done ]; } && file_done "$p"; then ok=true; fi
    [ "$ok" = true ] || ALL_OK=false
    LEAVES_JSON=$(printf '%s' "$LEAVES_JSON" | jq -c \
      --arg n "$n" --arg s "$st" --argjson ok "$ok" --arg p "$p" --argjson b "$bytes" \
      '. + [{name:$n,status:$s,ok:$ok,path:$p,bytes:$b}]')
    log "  $n: $st  ok=$ok  bytes=$bytes"
    i=$((i+1))
  done

  jq -n -c \
    --arg ws "$WS_ID" --arg rd "$RUNDIR" --argjson ok "$ALL_OK" --argjson leaves "$LEAVES_JSON" \
    '{workspace:$ws, rundir:$rd, ok:$ok, leaves:$leaves}'

  [ "$ALL_OK" = true ]
}

case "$MODE" in
  retry)  run_retry ;;
  fanout) run_fanout ;;
  *) echo "herdr-fanout: unknown mode" >&2; exit 2 ;;
esac
