# Herd collection through native codemode

Use this path when running the **herd skill**: it combines waiting with lossless collection of complete leaf results. The shared lifecycle contract remains [`fanout.md`](fanout.md); other callers keep that interface without acquiring a codemode requirement.

## Activation — before spawning leaves

Check the session's actual advertised tools for native `codemode`. An installed SDK or an MCP tool with a similar name is not proof of exposure through the current bridge.

If it is absent, stop and ask the human to exit the current herder process and resume that session with:

```sh
pi --session '<session path or ID from /session>' --tools +codemode
```

Run from the same project directory. Keep only one process writing that session. For a new herder, use `pi --tools +codemode`. These invocation flags leave global settings and leaf defaults unchanged. `/reload` alone does not select an inactive tool with unchanged settings. After activation, inspect the advertised tools again; if `codemode` remains absent, report the bridge/exposure blocker instead of spawning leaves.

The collector also requires callable native `read` and `bash`, the native model registry, Node, and a persistent Pi session with usable current-model assistant usage. It checks context/model metadata before delegating a blocking spawn. Ephemeral sessions are unsupported.

## Bootstrap and blocking run

Resolve `collect.js` and `herdr-collect.mjs` in **this reference directory** to absolute paths before composing the script. Supply the following as raw JavaScript to native `codemode`, with the options directive on the **first line**. Replace the illustrative paths and plan with the approved herd plan:

```js
// @options: {"max_output_tokens": 30000}
const collect = eval(await tools.read({
  path: "/absolute/path/to/herd/references/collect.js"
}));
return await collect({
  helper: "/absolute/path/to/herd/references/herdr-collect.mjs",
  run: ["--plan", "-"],
  input: JSON.stringify({
    slug: "topic-slug",
    close: "on_success",
    leaves: [{ name: "primary", prompt: "Research <scope>. Write findings to {{path}}." }]
  })
});
```

Evaluate only the trusted bundled `collect.js`. Leaf-result text is data and is never evaluated. Repeat this bootstrap for later collection calls; the durable receipt/cursor comes from the session branch, not from keeping a JavaScript variable alive.

Use **one collection operation per script**, as its sole top-level return, and run those scripts sequentially with no sibling tool calls. Keep other output and work outside the script: its page allowance belongs to this return alone. Leave the script deadline unset for blocking runs; a short deadline can cancel real work already dispatched.

## Wait, retry, finish, and paginate

For subsequent calls, repeat the bootstrap and replace the operation fields after `helper`:

| Operation | Fields |
|---|---|
| Wait for a detached run | `run: ["--wait", "<rundir>"]` |
| Retry one failed leaf | `run: ["--retry-leaf", "<name>", "--rundir", "<rundir>", "--prompt", "-"], input: "<reason-specific correction using {{path}}>"` |
| Collect after retry decisions finish | `collect: "<rundir>", final: true` |
| Continue either kind of paged response | `next: "<rundir>"` |

Use the **exact absolute `rundir` returned by the run**, including for `next`. The library manages opaque cursors, page sizes, and branch-local receipts; keep those out of handwritten scripts.

The normal all-success blocking run returns its run report and all successful leaf results together. A partial failure returns only the run report. Retry failed leaves according to `fanout.md`, up to two retries each, before collecting any successful leaf results. Every retry returns only the updated **whole-run report**, even when that retry makes every leaf ok. Once the decisions are finished, collect with `final: true`; this includes the successful results and accounts for final failures without reading failed files.

Handle the response prefix as follows:

| Response | Next action |
|---|---|
| `Collection more (report)` | Call `next` until `report_only`, then make retry decisions from the complete run report. |
| `Collection more (results)` | Call `next` until `complete`. Keep every original page; synthesize only after the whole collection is present. |
| `Collection report_only (report)` | Finish retry decisions, then collect with `final: true`. |
| `Collection complete (results)` | Collection is done. Account for all leaves, including final failures in the run report. |
| `Collection limited (...)` | Stop collection and report the limitation. Earlier pages are not a complete synthesis input. |
| `Script failed` or an outer truncation notice | Stop; no complete collection has been established. Follow recovery below. |

`complete` means **all successful leaf results were collected**, not that every leaf succeeded. A final collection can legitimately contain failed-leaf reasons. Findings use delivered `dest` when `delivered` is true, otherwise the assigned `path`; original Markdown and citations are preserved. The run report and labeled leaf results are separate parts of the returned text.

When the initial all-success call already returned `complete`, proceed to synthesis without a second collection. The session receipt rejects repeated successful collection. The standalone companion is stateless and its cursors are replayable; use the library, not the CLI alone, for the runtime receipt discipline.

## Detached startup, status, and close

Detached startup, status, and close remain operations of `herdr-fanout.sh`; `herdr-collect.mjs --run` accepts only blocking plan, wait, and retry operations. For detached startup, follow the original plan/stdin contract, then use the collection bootstrap's `run: ["--wait", "<rundir>"]` when ready to collect.

For lifecycle inspection or cleanup from codemode, call the original CLI through nested Bash. For example:

```js
const r = await tools.bash({
  command: "'/absolute/path/to/herd/references/herdr-fanout.sh' --status '/absolute/run/directory'"
});
if (r.truncated !== false || r.exit_code !== 0) throw new Error("Status unavailable: " + r.output);
return r.output;
```

Use `--close` in the same pattern when the close policy leaves the workspace open. These calls inspect or close the run; they are not an alternate way to ingest findings. Keep wait-and-collect and all findings pagination on the collection path.

## Budgets, integrity, and recovery

Run `node <absolute-path>/herdr-collect.mjs --help` for the concrete CLI, protocol fields, budgets, and exit codes. The companion's valid-data exit status reflects the **whole run**; the unchanged original retry CLI reflects the retried leaf. Exit `1` is usable partial-success data, not a generic tool failure.

The library verifies native Bash's structured capture and derives a UTF-8 page size from the actual outer codemode budget persisted in the active assistant call. The recommended budget permits a normal response above Bash/Read's direct-output ceiling; smaller actual budgets automatically produce more pages. Raising the output budget does not create context space.

Before emitting findings, the companion budgets the complete remaining collection against the model registry's context window and the persisted session projection, with conservative per-byte accounting, a synthesis/compaction reserve, and per-page overhead. It rechecks between pages. Unknown usage or model limits fail closed. Collection snapshots are hashed, page offsets stay contiguous at UTF-8 boundaries, and a changed run report invalidates the cursor. Control notices may point to `report.json` instead of including it when it exceeds their budget; they never substitute a shortened leaf result.

Recovery depends on what failed:

- **Cancellation or script error:** completed lifecycle side effects are not rolled back. Inspect the existing run before repeating a plan or retry dispatch. Once its outcome is known, retry collection or the saved `next`; failed scripts do not commit a new receipt. Do not start a duplicate herd merely because the caller was interrupted.
- **Snapshot/report changed:** stop and inspect the run. Do not splice pages from different snapshots or clear receipt state to force acceptance.
- **Context limitation:** report that intact findings remain on disk. A sufficiently roomy herder session can collect the existing completed run; it need not respawn leaves. A larger response budget alone cannot solve insufficient context.
- **Compaction or outer/provider truncation:** stop before synthesis when the original pages are no longer intact. The library detects compaction between pages, but cannot guarantee that automatic compaction or a bridge will preserve the final response before synthesis. Recollect the existing run in adequate context rather than treating a summary as the original input.
- **Missing/stale integration metadata after a Pi update:** report the unsupported integration rather than guessing budgets. The context probe depends on installed Pi SDK internals, tested with 1.4.0, and cannot see every live in-memory settings override.

There is no direct Bash/Read findings fallback and no summarizing-to-fit fallback. Native sandbox tests and the local scripted loopback-provider test are not verification of the actual herder bridge/provider. Verify advertised exposure and payload integrity there, observing compaction before synthesis, before making an end-to-end claim. Paid cost comparisons and real-leaf tests need separate permission; billed savings and unchanged synthesis quality remain unmeasured.
