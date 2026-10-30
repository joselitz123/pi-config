# Herd output-limit workarounds

Research and implementation rechecked against the installed **Pi 1.4.0** on **2026-10-30**. Scope: reduce billed orchestration cost while preserving complete leaf results and existing synthesis inputs. Codemode is required for the first-version herd wait-and-collect flow; no custom herd extension or global Bash/Read override is planned. Activation is scoped to the herder session only (`pi --tools +codemode`); global settings and leaf configuration stay unchanged. **Implementation and runtime documentation are complete; 64 deterministic tests pass. Herder-only activation, actual-session native codemode exposure, and single-response/paged synthetic payload integrity are verified for the resumed session.**

## Summary

- The normal **50 KiB / 2,000-line** limit belongs to the built-in Bash and Read tools, not to every Pi tool result. Their documented settings and call schemas expose no switch to raise it. [S1–S3]
- **Codemode is a supported workaround:** nested Bash calls expose up to 1 MiB of structured output, and codemode has a separate, explicitly adjustable final-output budget. Both limits must be checked. [S2, S4–S5]
- A **dedicated herd wait-and-collect tool** can read successful leaf results and return them without applying Bash's cap. The inspected local tool pipeline does not impose that cap again; provider, outer-harness, context-window, and compaction constraints still apply. [S6–S7, S10]
- Initial parent-side synthetic probes preserved an 80,043-byte payload through the local pipeline without model calls (§4). Subsequent actual-session checks preserved an 80,312-byte leaf result in one response and a 114,326-byte Unicode/CRLF result across two responses, with no compaction observed before the verification summary (implementation status below).
- Codemode is the lowest-implementation-effort experiment; a narrow herd-specific tool is the cleaner durable integration. Neither reduces the tokens in the findings themselves, and billed savings remain unmeasured.

## 1. Which limit are we trying to avoid?

Bash and Read import `DEFAULT_MAX_LINES = 2000` and `DEFAULT_MAX_BYTES = 50 * 1024`. Bash creates its output accumulator with those defaults; Read applies `truncateHead()` after selecting the requested lines. Increasing Read's `limit` therefore does not raise its byte or line ceiling. Increasing a subprocess's `maxBuffer` or a call's timeout does not change this presentation limit either. The installed settings reference and Bash/Read call schemas expose no output-limit override. [S1–S3, S11]

The layers must be kept separate:

| Layer | Installed behavior |
|---|---|
| Direct Bash model-facing `content` | Last 2,000 lines / 50 KiB, with a pointer to saved full output when truncated. [S1–S2] |
| Bash programmatic `structuredContent.output` | Up to **1 MiB** of captured stdout/stderr. Beyond that it keeps a head and tail, sets `truncated`, and provides `full_output_path`. [S2, S4] |
| Read programmatic output | The same selected/truncated text as its model-facing output, not an unrestricted file read. [S3] |
| Codemode final output | Default **10,000 estimated tokens**, implemented as **40,000 JavaScript string characters**; adjustable per call with `max_output_tokens`. This is not a tokenizer-accurate count. [S5] |
| Custom tool result | The inspected tool wrapper/core pipeline forwards the returned `content` without reapplying the built-ins' 50 KiB rule. This is a local implementation finding, not a promise of unlimited provider input. [S6–S7] |

Codemode additionally has a 16,777,216-character output/data safety ceiling, a 100,000-output-call ceiling, and a 256 MB VM memory limit. Those are not suitable targets for ordinary herd results; the model's available context may be much smaller. [S5]

## 2. Practical alternatives

### A. Codemode: a supported existing orchestration layer

A codemode script can wait for a run, perform dependent tool calls, and return findings once. Nested calls go through validation, tool hooks, and permission checks, but their results do not independently enter model context; only the outer script output does. Consequently, chaining calls in code can eliminate the model turn that otherwise decides to read the files. [S5–S6]

Two caveats are essential:

1. Raising codemode's budget does **not** undo truncation that already happened in a nested tool. Read remains bounded. A collector must paginate Read correctly, or use a deterministic helper that assembles full files and inspect Bash's structured `truncated` flag. Bash's structured path is complete only up to its separate 1 MiB ceiling. [S2–S5]
2. Codemode's default final budget is only about 40,000 characters, so using defaults can truncate a bundle even when nested Bash captured it completely. Set a deliberate, context-safe budget and detect overflow. [S5]

For example, this is a **synthetic limit probe**, not a herd implementation:

```js
// @options: {"max_output_tokens": 30000}
const r = await tools.bash({
  command: "python3 -c 'print(\"x\" * 80000)'"
});
if (r.exit_code !== 0 || r.truncated) throw new Error("Incomplete output");
return r.output;
```

The option permits roughly 120,000 characters; it does not guarantee that the current model has room for that output. A real collection script must handle partial-success exit codes and failure metadata rather than treating every nonzero exit as a generic failure. [S5, S12]

Codemode is shipped but inactive by default. It can be enabled with `"defaultTools": ["+codemode"]` or `pi --tools +codemode`. At initial research, the repository settings did not explicitly enable it and this conversation did not advertise it. The user subsequently resumed the herder with the invocation flag; native exposure is now confirmed in this session. No global setting was changed during research or implementation. [S5, S9, S13, S17]

**Assessment:** smallest new-code experiment; introduces another output budget and per-run script-writing overhead. It is not automatically cheaper than a dedicated tool.

### B. A dedicated herd wait-and-collect tool

A small extension registered with `pi.registerTool()` can invoke the existing fanout/wait operation, read the successful leaf result files, and return the run report plus complete, labeled findings as text content. One text block per leaf is permitted. The inspected `wrapToolDefinition`, `runToolCall`, and final tool-message construction do not impose the built-in 50 KiB ceiling on this content. [S6–S7]

Prefer existing nested tool execution where appropriate to preserve the tool-call authorization pipeline. Importantly, `ctx.executeTool()` preserves the **inner tool's own** truncation; it is not itself an unrestricted file reader. A tool using direct filesystem access must explicitly own and validate its allowed result paths. [S3, S6]

The custom tool should use a deliberate aggregate/context budget and an explicit, lossless fallback rather than silently cutting text or attempting unlimited output. Collect from `dest` when `delivered` is true, otherwise `path`, matching the current contract. Keep the existing run report, files, verdicts, retry limits, destination delivery, and close policy authoritative. Avoid returning already-delivered successful findings again after every retry. [S6, S10, S12]

**Assessment:** cleaner long-term interface and shorter repeated tool arguments, at the price of an extension, tests, and its tool-declaration overhead. End-to-end compatibility and billed savings still need validation.

### C. A scoped `tool_result` hook

An extension can replace the result of a specifically identified fanout/wait Bash invocation with the report plus file contents. `tool_result` transformations occur after the built-in tool executes; the inspected runner does not reapply the Bash output limit afterward. Replacing `content` without also replacing `structuredContent` drops the latter to prevent inconsistent programmatic results. [S6–S8]

Scope this to a positively identified collection operation, not merely every command containing the script name: help, close, status, and retry calls have different result shapes. An implicit hook also needs skill instructions that prevent redundant reads. [S8, S12]

**Assessment:** viable but less explicit than a named tool. It risks command-matching brittleness and surprising unrelated flows.

### D. Override Bash/Read globally

Pi's checked example demonstrates registering a tool with a built-in name to replace it. A replacement could choose a higher cap. Merely wrapping and returning the original tool's already-truncated content will not restore it. [S3, S6, S14]

**Assessment:** technically possible, but unnecessarily broad for this herd-specific goal. It changes behavior and context exposure for other workflows; editing the installed package constants would additionally create an update-sensitive local fork.

### E. Keep full-file reads, but batch or paginate them

Pi can execute multiple calls from one assistant message in parallel. Five file-read calls can therefore share **one model continuation turn**; they do not necessarily cost five model turns. Likewise, pagination costs depend on how pages are batched, not simply how many files are large. [S6–S7]

Direct batched reads retain the report-to-read decision turn. Putting pagination inside codemode or a custom tool can remove model-mediated pagination turns, subject to the outer tool's own budget. [S3, S5–S7]

**Assessment:** safest no-extension baseline/fallback, but not a way to make one ordinary Bash response exceed its cap.

## 3. Things that do not solve the same problem

- **Streaming updates:** `onUpdate` produces execution-update events; the finalized result, not a concatenation of streamed fragments, becomes the tool-result message. Streaming is useful for progress, not an accumulating context channel. [S6–S7]
- **Multiple text blocks:** useful for attribution in a custom tool, but splitting an ordinary shell command's output into sections does not avoid truncation applied before Bash returns. [S2, S7]
- **`details` or structured output alone:** these are for UI/programmatic consumers; they do not automatically become the model-facing findings. Codemode must explicitly return the desired structured data as its own content. [S5–S7]
- **File paths:** preserve durable access but do not load file contents into the model. The current tool-result contract carries text and images, not an automatically ingested Markdown-file attachment. [S7]
- **Compression/base64:** this Pi path does not transparently decode compressed findings into model context. Decoding them programmatically before returning Markdown restores the original content size; asking the model to decode an alternate representation does not meet the unchanged-input/quality objective. [S5–S7]
- **One-line JSON or removing whitespace:** may avoid the line ceiling or reduce packaging, but the byte ceiling remains. Do not remove findings or citations merely to fit it. [S1–S3]

## 4. Local verification

The parent ran `/tmp/pi-herd-output-probe.UzIfnf/probe.mjs` using the installed `createBashTool`, `createCodemodeTool`, `wrapToolDefinition`, and core `runToolCall`. A fixed **80,043-byte ASCII payload** had beginning, middle, and end sentinels. Assertions compared full strings, not just displayed lengths.

| Path | Observed outcome |
|---|---|
| Direct Bash model-facing text | Truncated; only 40,159 bytes including its notice were returned because truncation also respects line boundaries. |
| Bash structured output | All 80,043 payload bytes; `truncated: false`. |
| Synthetic custom tool through local `runToolCall` | Exact payload preserved. |
| Codemode default budget | Truncated; middle sentinel absent. |
| Codemode with `max_output_tokens: 30000` | Exact full payload preserved after its script header. |

These probes made **zero model calls**. They verify the installed local pipeline, not the current conversation bridge, a live provider request, synthesis quality, or billed savings. The background research leaf itself was a real model-backed agent. Test spill files were cleaned up; the temporary probe script is not a durable repository artifact.

## 5. Approved direction and validation

**Agreed first-version direction:** use codemode to combine waiting and lossless collection. Keep custom herd extensions and global Bash/Read overrides out of scope. Preserve the original Markdown and existing failure semantics. The later decisions below settle herder-only activation; concrete budgets are now implemented as recorded in the implementation status. No billed-cost reduction has been measured.

**Agreed partial-failure behavior:** preserve retry-before-collection. If any leaf fails, return only the run report so the herder can make its existing retry decisions. Once those decisions are finished, collect the complete successful leaf results once and account for final failures. An all-success run returns the run report and complete leaf results together.

**Superseding decision — codemode only:** require codemode for the herd wait-and-collect flow and remove the earlier Bash/Read fallback. Successful collection still requires complete leaf results, never a partial bundle or summary. Herder-only activation is agreed; concrete output/context budgets are implemented, and native exposure has been verified for the resumed session as recorded below. This decision concerns the herd workflow, not disabling ordinary Pi tools globally.

**Agreed oversized-result behavior:** when complete results exceed a single response's budget but fit the model's remaining context, collect lossless pages through additional codemode calls. This remains the same codemode-only approach, not a direct Bash/Read fallback. If the complete findings cannot fit the remaining context, report the limitation rather than truncate or summarize. Normal runs use one combined response; oversized runs may require more model round trips.

For the simple all-success blocking case, ignoring unrelated planning calls:

```text
Current:  model → Bash/wait → model → batched reads → model/synthesis
Combined: model → wait-and-collect → model/synthesis
```

That is **three versus two model requests**, not five reads versus one request. Detached startup, status checks, retries, pagination, and limitation recovery add different turns; do not extend this count to every run. Blocking `--plan` and detached `--wait` already perform polling internally without a model request per tick. [S6–S7, S12]

### Implementation approval checkpoint

The user confirmed the complete plan and authorized implementation in the next session. In addition to the agreed behavior above:

- Check codemode availability before spawning leaves; if unavailable, stop and provide herder-only activation instructions.
- Update herd instructions, collection support, and tests while preserving the shared fanout command contract for other skills.
- Start with deterministic tests. Do not launch real agents or paid cost-comparison calls as part of implementation without separate authorization.

### Validation plan

Before claiming savings:

1. Reuse fixed leaf-result fixtures in both paths. Assert that every successful result and source citation reaches the model-facing payload unchanged; preserve all failure reasons and attribution.
2. Exercise mixed success, retries, delivered `dest`, closed workspaces, oversized files, long lines, Unicode, cancellation, dead monitors, and context pressure. Keep retry and close semantics unchanged. [S12]
3. Test through the actual outer harness/provider, not only direct SDK calls. Record whether compaction happens before synthesis; the documented compaction process can summarize prior tool content and adds model work. [S10]
4. Compare total usage and actual provider pricing with the same model, reasoning level, artifacts, and cache conditions. Include any new tool-description/script overhead. Full findings still consume input tokens; leaf work is unchanged, so possible savings are from orchestration, not content elimination. [S6, S11–S12]

There is **no measured billed-cost saving yet**. Also, leaves use the configured startup-default model; the herder can be on a different session-selected model. Do not assume their token prices are identical. [S12]

## Implementation status and verification

Implementation and documentation are integrated:

- `agent/skills/herd/references/herdr-collect.mjs` delegates lifecycle operations to the unchanged fanout CLI, assembles complete successful leaf results, and provides hashed UTF-8 snapshot pages. Its `--help` is authoritative for the protocol and concrete budgets. The companion consistently reports whole-run success/failure, including after a successful retry while another leaf remains failed; the original retry CLI keeps its single-leaf exit semantics.
- `agent/skills/herd/references/collect.js` is trusted bundled code evaluated inside native codemode. It checks structured nested output, derives page size from the persisted active codemode call's actual output budget, and keeps small branch-local receipts/cursors. Findings remain in files, not in the store. Calls use the exact returned run directory; path aliases cannot bypass receipts.
- `agent/skills/herd/SKILL.md` now uses this path for wait/retry/collection. `references/collection.md` documents activation, bootstrap, response handling, budgets, and recovery. The scoped pointer in `references/fanout.md` preserves the old command contract for `dev-research` and `dev-code-review`; those consumers were inspected and remain unchanged.
- Review fixes have regression coverage for whole-run retry status, snapshot metadata validation, empty-run refusal, bounded control/error notices, and receipt bypass via path aliases. Script failure after collection remains replayable without committing a receipt. The SDK test tracks and cleans only its own exact spill paths, including nested Bash output; it performs no global temp-file sweep.

The library recommends an outer `max_output_tokens: 30000`. The helper caps UTF-8 data pages at 96 KiB; the library reduces that for smaller actual outer budgets. Context accounting charges one token per remaining UTF-8 byte, reserves at least 16,384 tokens (or a larger configured compaction reserve), and allows 4,096 tokens per remaining page. These are conservative allowances, not tokenizer-accurate proof that the live provider has room. [S15–S16]

The context probe uses the persisted v3 session's active-branch projection and installed estimator. Missing/stale usage fails closed; ephemeral sessions are unsupported. It depends on installed SDK internals and cannot observe every in-memory settings override. Compaction is detected between pages, but preservation after the final response and before synthesis is not guaranteed. The original `CONTEXT.md` vocabulary changes were preserved; no implementation details or new ADR were added there.

Checks rerun after implementation review and documentation integration, and again after actual-session activation:

| Check | Result |
|---|---|
| `python3 -m unittest discover -s agent/skills/herd/tests -p test_fanout.py -v` | **30 passed** |
| `python3 -m unittest discover -s agent/skills/herd/tests -p test_collect.py -v` | **20 passed** |
| `node --test agent/skills/herd/tests/codemode.test.mjs` | **14 passed** |
| `node --check` on both runtime JavaScript files | Passed |
| `git diff --check` | Passed for tracked changes |

See the test assertions for the verified scenarios and exact payload comparisons. All **64 tests are deterministic**: fixture files, fake herdr, the installed native sandbox/core pipeline, and a real SDK session backed by a **scripted loopback HTTP server**, not an AI model. The latter preserves the full >50 KiB result into the next local provider request, with automatic compaction deliberately disabled to isolate payload preservation. It is not a general no-compaction guarantee or a check of the actual remote provider/bridge.

Before activation, a development-only `--context` invocation succeeded but found no active codemode budget. That was session-usage-probe evidence only. The activation and actual-session checks below supersede that exposure blocker.

### Actual-session verification after herder-only activation

On **2026-10-30**, the user resumed the same persistent herder session with codemode enabled. Native `codemode` was then advertised and executed successfully. Native model metadata identified `github-copilot/gpt-6.1-sol` with a 1,050,000-token context window; the preflight recovered the actual codemode output budget from the persisted assistant call. Global settings, leaf defaults, and installed Pi code remained unchanged. [S17]

Two synthetic, already-finished runs exercised the documented bootstrap using the actual native tool, not a local SDK replacement. Their workspace identifiers were fictional; no agents or workspaces were created. The first call used `run: ["--wait", "<rundir>"]`; the oversized case used `collect`, followed by the saved `next`. Each collection call used `max_output_tokens: 30000` as its sole output.

| Actual-session case | Original leaf-result bytes | Complete bundle bytes | Returned UTF-8 byte ranges | Tool-result entry IDs |
|---|---:|---:|---|---|
| Single response | 80,312 | 80,932 | `0..80932` (`complete`) | `a6182cbb` |
| Unicode/CRLF pagination | 114,326 | 114,943 | `0..98304` (`more`), `98304..114943` (`complete`) | `207f8407`, `4f5d00ec` |

The native model-facing outputs contained the beginning/middle/end sentinels and original citation. An assertion-based audit reconstructed the **active session projection**, matched all three successful tool results to their persisted calls and real-provider continuation responses, and compared the joined pages byte-for-byte against the original fixture files and saved bundles. It verified contiguous offsets, terminal completion, source and bundle SHA-256 values, and absence of outer truncation notices or output-spill markers. The original source SHA-256 values were recorded before collection:

- Single-response source: `c07b44803768bc2c2f137c784c3126538e733f4b003a50bd4d28690a04585976`; bundle: `2ec1e19b64ba45b6b7c2cc45865aecafab681e31ce73cb018ea660e415b70c8c`.
- Paged source: `495353497eb8b1cf894f85d9046d3b63535ac957175c2f8174cc334554244db4`; bundle: `2f4fc6e39c40493d6c08318d1a4dac60725559deb0ec7f759f56d233fb86d872`.

**Compaction observation:** zero compaction or context-edit entries appeared from the first successful payload through the post-collection audit, before the verification summary. The context probe still reported `compaction_id: null`, 246,434 used tokens, and a 16,384-token reserve. Automatic-compaction settings were not changed for this actual-session check. This is an observation for these fixtures, not a guarantee for future runs or every live settings override.

The first wait attempt failed because the synthetic plan omitted the lifecycle CLI's required `settings` field. Direct CLI reproduction identified `KeyError: 'settings'`; correcting only the synthetic metadata made the same native bootstrap pass. No runtime implementation change was needed, and no receipt had been committed by the failed script. After verification, the dedicated synthetic run directory and two nested Bash spill files were removed. Spill ownership was established by the unique probe nonce and exact chunk comparisons; unrelated files were left untouched.

**Evidence scope:** native exposure and model-facing payload integrity are now verified through this resumed conversation, with subsequent responses from its active provider. No independent raw HTTP request capture was made, and this does not establish a universal provider/bridge guarantee. The 64 deterministic tests remain separate evidence from these actual-session probes. No real-leaf implementation smoke test, paid cost comparison, billed saving, or synthesis-quality measurement has occurred.

## Further validation requiring separate permission

The approved implementation, documentation, and actual-session synthetic checks are complete for this checkout/session. Before claiming billed savings or unchanged synthesis quality, obtain authorization for the remaining cost/quality comparisons in §5's validation plan, controlling model, reasoning level, artifacts, and cache conditions. A real-leaf smoke test also needs separate permission. Native-tool exposure and context preservation must still be checked in other herder sessions or after integration changes.

## Sources

Paths below are first-party installed sources, version 1.4.0. Let **P** = `/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent`. Upstream `main` links are navigational; the installed version is the evidence for this report.

- **S1:** `P/dist/core/tools/truncate.js:10-11,44-46,123-126`; [upstream utility](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/truncate.ts).
- **S2:** `P/dist/core/tools/bash.js:14,28-31,166-175,233-251,285-306`; `P/dist/core/tools/bash.d.ts:70-80`; [upstream Bash](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/bash.ts).
- **S3:** `P/dist/core/tools/read.js:10-14,35-38,124-164,178`; [upstream Read](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/read.ts).
- **S4:** `P/dist/core/tools/output-accumulator.js`, `readFullOutput()` at lines 111-149.
- **S5:** `P/docs/codemode.md` (Scripts, Call tools, Limits); `P/dist/extensions/codemode/execute.js:248-250,389-425,564-581`; [codemode docs](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/codemode.md).
- **S6:** `P/docs/extensions.md` (Tools, events, tool exposure); `P/dist/core/extensions/types.d.ts:262-280,985-1004,1110-1119`; [extension docs](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md).
- **S7:** `P/dist/core/tools/tool-definition-wrapper.js:1-14`; `P/node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js:547-553,576-640,662-674`; sibling `types.d.ts:260-270,367-405`.
- **S8:** `P/dist/core/extensions/runner.js:900-955` (`emitToolResult`).
- **S9:** `P/docs/cli.md` (Enable codemode); `P/docs/settings.md` (Tools).
- **S10:** `P/docs/compaction.md` (When It Triggers, How It Works, Message Serialization); [compaction docs](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md).
- **S11:** `P/docs/settings.md` (full installed settings reference, including cache warming).
- **S12:** `/Users/joselitophala/.pi/agent/skills/herd/SKILL.md`; `references/fanout.md`; `references/herdr-fanout.py` (`cmd_fanout`, `cmd_wait`, `cmd_retry`, `watch`, `deliver`, `apply_close`).
- **S13:** `/Users/joselitophala/.pi/agent/settings.json`, inspected only for configured tool/resource availability.
- **S14:** `P/examples/extensions/tool-override.ts`; `P/examples/extensions/truncated-tool.ts`. Both implement their own truncation, reinforcing that custom-tool output budgeting is the author's responsibility.
- **S15:** `P/docs/environment-variables.md` (shell session metadata); `P/docs/session-format.md` (v3 tree, projection, context edits); `P/dist/core/session-manager.js` (`buildSessionProjection`); `P/dist/core/compaction/compaction.js` (`estimateProjectedContextTokens`); `P/dist/core/agent-session.js` (`getContextUsage`).
- **S16:** `P/node_modules/@earendil-works/pi-codemode/dist/source.js` (`parseCodemodeSource`); `P/dist/extensions/codemode/execute.js` (branch-local store commits, output-budget enforcement); repository `agent/skills/herd/tests/test_collect.py` and `codemode.test.mjs` (deterministic CLI and model-facing integration evidence).
- **S17:** Actual persistent session `/Users/joselitophala/.pi/agent/sessions/--Users-joselitophala-.pi--/2026-10-30T09-17-55-948Z_01a18c19-fd6c-7662-88ad-c527c008f119.jsonl`: tool-result entries `a6182cbb`, `207f8407`, `4f5d00ec`; subsequent provider assistant entries `7eb7ad51`, `97b78359`, `aad7fc17`; native preflight, assertion audit, and positively scoped artifact-cleanup calls on that branch. The synthetic directory was `/Users/joselitophala/.pi/herd/native-collection-validation-uh81sm_z` and has been removed.

Research delivery: workspace `w3R`, run directory `/Users/joselitophala/.pi/herd/2026/10/herd-output-limit-workarounds`; the single leaf succeeded, delivery completed, and the workspace closed automatically. This findings file is inside the project at `docs/research/herd-output-limit-workarounds.md` and is explicitly included with the implementation. The repository's default-deny `.gitignore` policy is unchanged.
