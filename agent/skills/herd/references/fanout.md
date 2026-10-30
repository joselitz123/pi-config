# Fan out with herdr-fanout

The contract for every skill that delegates work to leaf pi agents through `~/.pi/agent/skills/herd/references/herdr-fanout.sh`. The plan schema, report fields, failure reasons, and exit codes live in `herdr-fanout.sh --help`; run it when you need a field this page does not name.

**Herd collection:** when called by the `herd` skill, use [`collection.md`](collection.md) for its required native-codemode wait-and-collect path. Other callers, including `dev-research` and `dev-code-review`, retain the command contract below; this shared reference does not require codemode.

## Before the plan

- Fan out only when `HERDR_ENV=1` and `HERDR_DEPTH` is unset or below 2. Otherwise follow your calling skill's in-context fallback. The script enforces the same guard.
- The herdr pi hook integration must be installed. It is what makes leaf status trustworthy: the hook reports `idle` only after pi settles, so pi's inference auto-retries stay `working`. When it is missing, the script refuses with `herdr integration install pi`; relay that to the human.

## Compose the plan

The script owns every path under `~/.pi/herd/`: the run directory, each leaf's result file, collision suffixes, and the date partition. Your plan names the work, never those paths.

- **`slug`** — a short topic slug. The script sanitises it and allocates `~/.pi/herd/<UTC YYYY/MM>/<slug>`, adding `-2`, `-3`, … when it exists, so any slug is safe to reuse.
- **`leaves`** — at most five, each with:
  - `name` — lowercase letter first, then `[a-z0-9_-]`, up to 28 chars. The leaf's result file is `<rundir>/<name>.md`.
  - `prompt` — the task. Write `{{path}}` wherever the prompt names the output file; the script expands it and appends a footer that names the exact file and the `<!-- herd:done -->` marker rule.
  - `dest` — optional absolute path outside `~/.pi/herd/`. Once the leaf is ok, the script copies its file there with the marker removed. Use it for deliverables that belong in a repo.
- **`close`** — `never` keeps the workspace for the human to inspect; `on_success` closes it when every leaf is ok and keeps it open for debugging otherwise; `always` closes it regardless.

Pass the plan on stdin, so no plan file is needed:

```bash
~/.pi/agent/skills/herd/references/herdr-fanout.sh --plan - <<'JSON'
{"slug": "dpp53-review", "close": "on_success", "leaves": [
  {"name": "spec", "prompt": "Review … Write your report to {{path}}."}
]}
JSON
```

## Run it

- **Blocking** (default) — prints the report once every leaf has a verdict. A run can last up to `cap_ms` (30 min by default).
- **Background** — add `--detach`. It prints `{workspace, rundir}` right away; keep working, check `--status <rundir>` whenever you like, and call `--wait <rundir>` for the report.

The script is the leaves' only voice while they graze: it dispatches each prompt, sends at most one corrective nudge per dispatch, and decides doneness from hook status, quiet time, and the marker. Wait for its report, and reach a leaf only through `--retry-leaf` — a direct `herdr agent prompt` lands on a leaf that may be mid-retry and duplicates its work.

## Read the report

- Exit `0` means every leaf is ok, `1` means some leaf is `ok:false` (partial success is normal), `2` means refused before anything spawned (fix the plan or setup and rerun), and `3` means a herdr or setup failure.
- Per leaf, `ok` is the whole verdict. Read `path` for an ok leaf (or `dest` when `delivered` is true). For an `ok:false` leaf, `reason` says why: `no_file`, `no_marker`, `misplaced:<files>`, `inactive`, `blocked`, `cap`, and so on.
- `<rundir>/events.jsonl` is the run's history (spawns, status changes, nudges, verdicts), and `plan.json` holds each leaf's exact dispatched prompt.

## Retry an ok:false leaf

Re-prompt the same leaf — it remembers its attempt — with a task tailored to its `reason`, up to two retries per leaf:

```bash
herdr-fanout.sh --retry-leaf <name> --rundir <rundir> --prompt - <<'TASK'
Your previous attempt ended with `<reason>`. <What to fix.> Write your findings to {{path}}.
If a tool fails (for example web search returns 401), say so in the file and continue with what you can.
TASK
```

The script moves the previous file into `.state/attempts/`, judges the retry exactly like the fan-out, delivers `dest`, updates `report.json`, and prints the leaf's new entry. It refuses while the hook still reports the leaf `working` (let it settle), while the run is still in progress, or once the workspace is closed.

## Close and inspect

- `--close <rundir>` closes the workspace when the plan's `close` policy left it open.
- To look at a stuck leaf: `herdr agent read <agent>` shows its terminal, `herdr agent attach <agent>` takes it over, and `herdr agent explain <pane> --json` shows whether its status comes from the hook (`screen_detection_skipped: true`).

## Leaf environment

- **Model**: leaves start plain `pi`, so they run the default model in `~/.pi/agent/settings.json` (`defaultProvider`, `defaultModel`, `defaultThinkingLevel`), not the herder's model.
- **Credentials**: MCP servers resolve their secrets with `~/.local/bin/pi-bws-secret` when they start, independent of how herdr was launched. When leaves hit 401s, follow the `mcp-credential-recovery` skill.
