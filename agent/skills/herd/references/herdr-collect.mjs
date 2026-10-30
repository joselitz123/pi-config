#!/usr/bin/env node
/** Herd collection companion. The existing fanout CLI remains the lifecycle owner. */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, constants, fstatSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const fanout = fileURLToPath(new URL("herdr-fanout.sh", import.meta.url));
const MAX_PAGE_BYTES = 96 * 1024;
const MIN_RESERVE_TOKENS = 16384;
const PAGE_OVERHEAD_TOKENS = 4096;
let noticeBudget = MAX_PAGE_BYTES;
const HELP = `Herd wait-and-collect companion (native codemode caller; no lifecycle replacement).

Usage:
  node herdr-collect.mjs --context
  node herdr-collect.mjs --run '["--plan","-"]' --limits '<model JSON>' < plan.json
  node herdr-collect.mjs --run '["--wait","<rundir>"]' --limits '<model JSON>'
  node herdr-collect.mjs --run '["--retry-leaf","<name>","--rundir","<rundir>","--prompt","-"]' --limits '<model JSON>'
  node herdr-collect.mjs --collect <rundir> [--final] --limits '<model JSON>'
  node herdr-collect.mjs --page <opaque-cursor> --limits '<model JSON>'

--run delegates blocking --plan, --wait, or --retry-leaf arguments to herdr-fanout.sh.
Stdin is forwarded for its '-' input. Detached startup/status/close remain on that CLI.
--final authorizes collection of successful results after failure/retry decisions finish.
Partial failures and all retry calls otherwise return only the complete run report.
--limits is {"provider":"...","id":"...","contextWindow":128000}, obtained from the
active codemode model registry, never guessed. Provider/id must match shell metadata.
--context reads the active persistent session projection; unknown usage fails closed.
--page-bytes is 256..${MAX_PAGE_BYTES} (default ${MAX_PAGE_BYTES}), a UTF-8 data-page budget.
Context allowance reserves at least ${MIN_RESERVE_TOKENS} tokens plus ${PAGE_OVERHEAD_TOKENS} per remaining page;
remaining payload is conservatively charged one token per UTF-8 byte.

JSON output (except help): version, state, kind, workspace, rundir, sha256,
total_bytes, offset/end (UTF-8 bytes), chunk_sha256, next_cursor, text.
States: more | complete (all successful results collected) | report_only |
limited (no complete collection fits) | error. Never synthesize from more/limited/error.
Snapshots live under <rundir>/.state/collections/ and cursors are replayable. Session
receipt/duplicate prevention belongs to collect.js, not this stateless CLI.
Exit: 0 all report leaves ok | 1 some report leaf failed (still valid data) |
2 fanout refused | 3 lifecycle, context, or collection error. This companion reports
the overall run status even after a successful single-leaf retry; the original fanout
CLI still reports that retry's own status. Original run files/verdicts are unchanged.
Requires Node and the installed Pi SDK (tested with 1.4.0); PI_SDK_ROOT can locate it.
`;

function digest(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

function readReport(rundir) {
  const root = realpathSync(process.env.HERD_ROOT || path.join(homedir(), ".pi", "herd"));
  const relative = path.relative(root, realpathSync(rundir));
  if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("Collection rundir must lie under the herd root");
  }
  const plan = readJson(path.join(rundir, "plan.json"));
  const report = readJson(path.join(rundir, "report.json"));
  if (path.resolve(report.rundir) !== path.resolve(rundir) || report.rundir !== plan.rundir ||
      report.workspace !== plan.workspace || !Array.isArray(report.leaves) ||
      report.leaves.length !== plan.leaves.length || report.leaves.length < 1 || report.leaves.length > 5 ||
      report.ok !== report.leaves.every(leaf => leaf.ok === true)) {
    throw new Error("Run report does not match the plan");
  }
  const seen = new Set();
  for (const leaf of report.leaves) {
    const assigned = plan.leaves.find(item => item.name === leaf.name);
    if (!/^[a-z][a-z0-9_-]{0,27}$/.test(leaf.name) || seen.has(leaf.name) || !assigned ||
        assigned.path !== path.join(plan.rundir, `${leaf.name}.md`) || leaf.path !== assigned.path ||
        leaf.dest !== assigned.dest || typeof leaf.ok !== "boolean" ||
        (leaf.delivered && (!leaf.ok || !path.isAbsolute(leaf.dest || "")))) {
      throw new Error(`Leaf ${leaf.name} does not match the plan`);
    }
    seen.add(leaf.name);
  }
  return report;
}

function reportText(report) {
  return `# Run report\n\n\`\`\`json\n${JSON.stringify(report, null, 2)}\n\`\`\`\n`;
}

function sdkRoot() {
  let directory = process.env.PI_SDK_ROOT || process.env.PI_PACKAGE_DIR;
  if (!directory) {
    const found = spawnSync("which", ["pi"], { encoding: "utf8" });
    if (found.status !== 0) throw new Error("Cannot locate Pi; set PI_SDK_ROOT to its package directory");
    directory = path.dirname(realpathSync(found.stdout.trim()));
  }
  while (true) {
    try {
      if (readJson(path.join(directory, "package.json")).name === "@earendil-works/pi-coding-agent") return directory;
    } catch { /* Walk from the executable to its package. */ }
    if (directory === path.dirname(directory)) throw new Error("Cannot locate Pi's SDK package");
    directory = path.dirname(directory);
  }
}

async function contextSnapshot() {
  try { return await readContextSnapshot(); }
  catch (error) { error.reason = "context_unknown"; throw error; }
}

async function readContextSnapshot() {
  if (!process.env.PI_SESSION_FILE || !process.env.PI_SESSION_ID || !process.env.PI_PROVIDER || !process.env.PI_MODEL) {
    throw new Error("Context usage is unknown: use a persistent Pi session with shell session metadata");
  }
  const entries = readFileSync(process.env.PI_SESSION_FILE, "utf8").split("\n").filter(line => line.trim()).map(line => JSON.parse(line));
  const header = entries.shift();
  if (header.type !== "session" || header.version !== 3 || header.id !== process.env.PI_SESSION_ID) {
    throw new Error("Context usage is unknown: session metadata does not match the current session");
  }
  const byId = new Map(entries.map(entry => [entry.id, entry]));
  const branch = [];
  const seen = new Set();
  let current = entries.at(-1);
  while (current) {
    if (seen.has(current.id)) throw new Error("Invalid session parent chain");
    seen.add(current.id);
    branch.unshift(current);
    if (!current.parentId) break;
    current = byId.get(current.parentId);
    if (!current) throw new Error("Incomplete session parent chain");
  }
  const root = sdkRoot();
  const sdk = await import(pathToFileURL(path.join(root, "dist/index.js")).href);
  const { estimateProjectedContextTokens } = await import(pathToFileURL(path.join(root, "dist/core/compaction/compaction.js")).href);
  const { parseCodemodeSource } = await import(pathToFileURL(path.join(root, "node_modules/@earendil-works/pi-codemode/dist/source.js")).href);
  const projection = sdk.buildSessionProjection(entries, entries.at(-1)?.id, byId);
  const estimate = estimateProjectedContextTokens(projection, branch);
  if (estimate.lastUsageIndex === null || !Number.isSafeInteger(estimate.tokens)) {
    throw new Error("Context usage is unknown: no current, post-edit assistant usage; retry after a model response");
  }
  const usage = projection.messages[estimate.lastUsageIndex];
  if (usage.provider !== process.env.PI_PROVIDER || usage.model !== process.env.PI_MODEL) {
    throw new Error("Context usage is unknown: last assistant usage belongs to another model");
  }
  const model = { provider: process.env.PI_PROVIDER, id: process.env.PI_MODEL };
  const assistant = branch.findLast(entry => entry.type === "message" && entry.message.role === "assistant")?.message;
  const calls = (assistant?.content || []).filter(block => block.type === "toolCall" && block.name === "codemode");
  // Concurrent codemode calls share this assistant entry: the smallest budget is safe for all.
  const outputTokens = calls.length ? Math.min(...calls.map(call =>
    parseCodemodeSource(call.arguments.code).options.maxOutputTokens ?? 10000)) : null;
  // Read only. Taking project reserves too is conservative when project trust is unknown here.
  const settings = sdk.SettingsManager.create(process.cwd(), process.env.PI_CODING_AGENT_DIR, { projectTrusted: true });
  const errors = settings.drainErrors();
  if (errors.length) throw new Error(`Cannot assess compaction reserve: ${errors[0].error.message}`);
  return { version: 1, state: "context", ...model, session_id: header.id, leaf_id: entries.at(-1)?.id,
    used_tokens: estimate.tokens, reserve_tokens: Math.max(MIN_RESERVE_TOKENS, settings.getCompactionReserveTokens(model)),
    codemode_output_tokens: outputTokens, compaction_id: branch.findLast(entry => entry.type === "compaction")?.id || null };
}

function boundedNotice(text, budget) {
  return Buffer.byteLength(text) <= budget ? text :
    "Collection stopped: diagnostic exceeds the notice budget. No findings were accepted.\n";
}

function controlText(report, message, budget = MAX_PAGE_BYTES) {
  const reportPath = path.join(report.rundir, "report.json");
  return [
    reportText(report) + "\n" + message + "\n",
    `${message}\nThe complete run report is at ${reportPath}; it exceeds this notice's budget.\n`,
    `Collection stopped; complete run report: ${reportPath}\n`,
    "Collection stopped. The complete run report is report.json in the returned rundir. Findings remain on disk.\n",
  ].find(text => Buffer.byteLength(text) <= budget);
}

function collectionError(report, error, budget) {
  return { version: 1, state: "error", reason: error.reason || "collection_failed", workspace: report.workspace,
    rundir: report.rundir, text: controlText(report, `Collection stopped: ${error.message}`, budget) };
}

function exitFor(output, code) {
  return ["limited", "error"].includes(output.state) ? 3 : code;
}

function contextLimit(report, remainingBytes, pageBytes, context, limits) {
  // One UTF-8 byte per token is deliberately more conservative than Pi's chars/4 estimate.
  const required = remainingBytes + Math.ceil(remainingBytes / (pageBytes - 3)) * PAGE_OVERHEAD_TOKENS;
  const available = Math.max(0, limits.contextWindow - context.used_tokens - context.reserve_tokens);
  if (required <= available) return null;
  return { version: 1, state: "limited", reason: "context_limit", workspace: report.workspace, rundir: report.rundir,
    required_tokens: required, available_tokens: available,
    text: controlText(report, `Collection stopped: complete remaining results exceed the conservative context allowance ` +
      `(${required} required, ${available} available). Findings remain on disk; nothing was truncated or summarized.`, pageBytes) };
}

function readResult(source, expected) {
  const fd = openSync(source, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = fstatSync(fd);
    if (!before.isFile() || before.ino !== expected.ino || before.size !== expected.size || before.mtimeMs !== expected.mtimeMs) {
      throw new Error(`Leaf result changed or is not a regular file: ${source}`);
    }
    const bytes = readFileSync(fd);
    const after = fstatSync(fd);
    if (after.size !== before.size || after.mtimeMs !== before.mtimeMs || bytes.length !== before.size) {
      throw new Error(`Leaf result changed while collecting: ${source}`);
    }
    if (!Buffer.from(bytes.toString("utf8")).equals(bytes)) throw new Error(`Leaf result is not valid UTF-8: ${source}`);
    return bytes;
  } finally { closeSync(fd); }
}

function collect(report, includeResults, pageBytes, context, limits) {
  try { return buildCollection(report, includeResults, pageBytes, context, limits); }
  catch (error) { return collectionError(report, error, pageBytes); }
}

function buildCollection(report, includeResults, pageBytes, context, limits) {
  const reportBytes = Buffer.from(reportText(report));
  const files = [];
  let totalBytes = reportBytes.length;
  for (const leaf of includeResults ? report.leaves : []) {
    if (!leaf.ok) continue;
    const source = leaf.delivered ? leaf.dest : leaf.path;
    const stat = lstatSync(source);
    if (!stat.isFile()) throw new Error(`Leaf result must be a regular file: ${source}`);
    const header = Buffer.from(`\n# Leaf result: ${leaf.name}\nSource: ${JSON.stringify(source)}\n\n`);
    files.push({ source, stat, header });
    totalBytes += header.length + stat.size;
  }
  const limited = contextLimit(report, totalBytes, pageBytes, context, limits);
  if (limited) return limited;
  const parts = [reportBytes];
  for (const file of files) parts.push(file.header, readResult(file.source, file.stat));
  const bytes = Buffer.concat(parts);
  const snapshot = {
    version: 1, terminal_state: includeResults ? "complete" : "report_only", kind: includeResults ? "results" : "report",
    workspace: report.workspace, rundir: report.rundir, code: report.ok ? 0 : 1,
    sha256: digest(bytes), total_bytes: bytes.length, report, report_sha256: digest(JSON.stringify(report)),
  };
  const directory = path.join(report.rundir, ".state", "collections", `${snapshot.kind}-${snapshot.sha256}`);
  mkdirSync(directory, { recursive: true });
  for (const [name, content] of [["bundle.md", bytes], ["manifest.json", JSON.stringify(snapshot)]]) {
    try { writeFileSync(path.join(directory, name), content, { flag: "wx", mode: 0o600 }); }
    catch (error) { if (error.code !== "EEXIST") throw error; }
  }
  return page(snapshot, 0, pageBytes, context, limits);
}

function page(snapshot, offset, pageBytes, context, limits) {
  const directory = path.join(snapshot.rundir, ".state", "collections", `${snapshot.kind}-${snapshot.sha256}`);
  const bytes = readFileSync(path.join(directory, "bundle.md"));
  if (bytes.length !== snapshot.total_bytes || digest(bytes) !== snapshot.sha256) {
    throw new Error("Collection snapshot failed its integrity check");
  }
  if (!Number.isSafeInteger(offset) || offset < 0 || offset >= bytes.length ||
      (bytes[offset] & 0xc0) === 0x80) throw new Error("Invalid collection cursor offset");
  const limited = contextLimit(snapshot.report, bytes.length - offset, pageBytes, context, limits);
  if (limited) return limited;
  let end = Math.min(offset + pageBytes, bytes.length);
  while (end < bytes.length && (bytes[end] & 0xc0) === 0x80) end--;
  const chunk = bytes.subarray(offset, end);
  const next = end < bytes.length
    ? Buffer.from(JSON.stringify({ rundir: snapshot.rundir, kind: snapshot.kind, sha256: snapshot.sha256, offset: end })).toString("base64url")
    : null;
  return {
    version: 1, state: next ? "more" : snapshot.terminal_state, kind: snapshot.kind,
    workspace: snapshot.workspace, rundir: snapshot.rundir, sha256: snapshot.sha256,
    total_bytes: bytes.length, offset, end, chunk_sha256: digest(chunk), next_cursor: next, text: chunk.toString("utf8"),
  };
}

function nextPage(cursor, pageBytes, context, limits) {
  if (cursor.length > 8192 || !/^[A-Za-z0-9_-]+$/.test(cursor)) throw new Error("Invalid collection cursor");
  const decoded = JSON.parse(Buffer.from(cursor, "base64url").toString("utf8"));
  if (typeof decoded.rundir !== "string" || !/^[a-f0-9]{64}$/.test(decoded.sha256) ||
      !["report", "results"].includes(decoded.kind)) throw new Error("Invalid collection cursor");
  const report = readReport(decoded.rundir);
  const snapshot = readJson(path.join(decoded.rundir, ".state", "collections", `${decoded.kind}-${decoded.sha256}`, "manifest.json"));
  if (snapshot.version !== 1 || snapshot.workspace !== report.workspace ||
      snapshot.terminal_state !== (decoded.kind === "results" ? "complete" : "report_only") ||
      snapshot.code !== (report.ok ? 0 : 1) || !Number.isSafeInteger(snapshot.total_bytes) || snapshot.total_bytes <= 0) {
    throw Object.assign(new Error("Collection snapshot metadata is invalid"), { reason: "invalid_snapshot" });
  }
  if (snapshot.rundir !== report.rundir || snapshot.sha256 !== decoded.sha256 || snapshot.kind !== decoded.kind ||
      snapshot.report_sha256 !== digest(JSON.stringify(report)) || snapshot.report_sha256 !== digest(JSON.stringify(snapshot.report))) {
    throw Object.assign(new Error("Collection cursor is stale: the run report or snapshot changed"), { reason: "stale_cursor" });
  }
  const output = page(snapshot, decoded.offset, pageBytes, context, limits);
  return { code: exitFor(output, snapshot.code), output };
}

function run(args, pageBytes, context, limits) {
  const input = args.includes("-") ? readFileSync(0, "utf8") : undefined;
  const result = spawnSync(fanout, args, { input, encoding: "utf8", maxBuffer: 2 * 1024 * 1024 });
  if (result.error) throw result.error;
  if (result.status !== 0 && result.status !== 1) {
    return { code: result.status || 3, output: { version: 1, state: "error", reason: "fanout_failed",
      text: result.stderr || `Fanout terminated (${result.signal || result.status})` } };
  }
  const retry = args[0] === "--retry-leaf";
  const report = readReport(retry ? args[args.indexOf("--rundir") + 1] : JSON.parse(result.stdout).rundir);
  const output = collect(report, retry ? false : report.ok, pageBytes, context, limits);
  return { code: exitFor(output, report.ok ? 0 : 1), output };
}

async function main() {
  const { values } = parseArgs({ options: {
    run: { type: "string" }, collect: { type: "string" }, page: { type: "string" }, limits: { type: "string" },
    final: { type: "boolean" }, context: { type: "boolean" }, help: { type: "boolean", short: "h" },
    "page-bytes": { type: "string", default: String(MAX_PAGE_BYTES) },
  } });
  if (values.help) return { code: 0, help: HELP };
  if (values.context) return { code: 0, output: await contextSnapshot() };
  const pageBytes = Number(values["page-bytes"]);
  if (!Number.isSafeInteger(pageBytes) || pageBytes < 256 || pageBytes > MAX_PAGE_BYTES) {
    throw new Error(`--page-bytes must be 256..${MAX_PAGE_BYTES}`);
  }
  noticeBudget = pageBytes;
  const limits = JSON.parse(values.limits || "null");
  if (!limits || !Number.isSafeInteger(limits.contextWindow) || limits.contextWindow <= 0 ||
      limits.provider !== process.env.PI_PROVIDER || limits.id !== process.env.PI_MODEL) {
    throw new Error("--limits must identify the active model and its positive contextWindow");
  }
  if ([values.run, values.collect, values.page].filter(Boolean).length !== 1) {
    throw new Error("Choose --run, --collect, or --page");
  }
  const context = await contextSnapshot();
  if (values.page) return nextPage(values.page, pageBytes, context, limits);
  if (values.collect) {
    const report = readReport(values.collect);
    const output = collect(report, report.ok || values.final, pageBytes, context, limits);
    return { code: exitFor(output, report.ok ? 0 : 1), output };
  }
  const args = JSON.parse(values.run);
  if (!Array.isArray(args) || !["--plan", "--wait", "--retry-leaf"].includes(args[0]) ||
      args.some(arg => typeof arg !== "string") || args.includes("--detach")) {
    throw new Error("--run expects blocking --plan, --wait, or --retry-leaf fanout arguments");
  }
  return run(args, pageBytes, context, limits);
}

try {
  const { code, output, help } = await main();
  if (output && ["error", "limited"].includes(output.state)) output.text = boundedNotice(output.text, noticeBudget);
  process.stdout.write(help || JSON.stringify(output) + "\n");
  process.exitCode = code;
} catch (error) {
  process.stdout.write(JSON.stringify({ version: 1, state: "error", reason: error.reason || "collection_failed",
    text: boundedNotice(error.message, noticeBudget) }) + "\n");
  process.exitCode = 3;
}
