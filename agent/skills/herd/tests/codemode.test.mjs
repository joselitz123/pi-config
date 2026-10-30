// Installed Pi sandbox/pipeline plus a scripted loopback provider; no remote or billed model calls.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const references = fileURLToPath(new URL("../references/", import.meta.url));
let packageRoot = process.env.PI_SDK_ROOT || path.dirname(realpathSync(execFileSync("which", ["pi"], { encoding: "utf8" }).trim()));
while (true) {
  try { if (JSON.parse(readFileSync(path.join(packageRoot, "package.json"))).name === "@earendil-works/pi-coding-agent") break; } catch {}
  if (packageRoot === path.dirname(packageRoot)) throw new Error("Set PI_SDK_ROOT to the installed Pi package");
  packageRoot = path.dirname(packageRoot);
}
const sdk = await import(pathToFileURL(path.join(packageRoot, "dist/index.js")).href);
const { createCodemodeTool } = await import(pathToFileURL(path.join(packageRoot, "dist/extensions/codemode/tool.js")).href);
const { runToolCall } = await import(pathToFileURL(path.join(packageRoot, "node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js")).href);

function fixture(t, body, contextWindow = 128000) {
  const root = mkdtempSync(path.join(tmpdir(), "herd-codemode-test-"));
  const spills = new Set();
  t.after(() => {
    for (const file of spills) rmSync(file, { force: true });
    rmSync(root, { recursive: true, force: true });
  });
  const herdRoot = path.join(root, "herd");
  const rundir = path.join(herdRoot, "2026", "10", "fixture");
  const agentDir = path.join(root, "agent");
  mkdirSync(path.join(rundir, ".state"), { recursive: true });
  mkdirSync(agentDir);
  const resultPath = path.join(rundir, "spec.md");
  writeFileSync(resultPath, body);
  const leaf = { name: "spec", path: resultPath, dest: null };
  writeFileSync(path.join(rundir, "plan.json"), JSON.stringify({ workspace: "w-fixture", rundir, leaves: [leaf] }));
  writeFileSync(path.join(rundir, "report.json"), JSON.stringify({ workspace: "w-fixture", rundir, ok: true, closed: true,
    leaves: [{ ...leaf, ok: true, delivered: false, reason: null, attempts: 1 }] }));
  const model = { provider: "fixture", id: "fixture", input: ["text"], contextWindow };
  const sessionFile = path.join(root, "session.jsonl");
  writeFileSync(sessionFile, [
    { type: "session", version: 3, id: "fixture-session", cwd: root },
    { type: "message", id: "s", parentId: null, message: { role: "system", content: "Fixture", timestamp: 0 } },
    { type: "message", id: "a", parentId: "s", message: { role: "assistant", provider: model.provider, model: model.id,
      content: [], stopReason: "toolUse", usage: { input: 900, output: 100, cacheRead: 0, cacheWrite: 0, totalTokens: 1000 }, timestamp: 0 } },
  ].map(entry => JSON.stringify(entry) + "\n").join(""));
  const fakeState = path.join(root, "fake-state.json");
  const scenario = path.join(root, "scenario.json");
  writeFileSync(scenario, JSON.stringify({ leaves: { spec: [[
    { at: 0.01, status: "working" }, { at: 0.05, write: "expected" }, { at: 0.1, status: "idle" },
  ]] } }));
  const branch = [];
  let sequence = 0;
  const ctx = {
    cwd: root, model,
    sessionManager: { getBranch: () => branch, getSessionId: () => "fixture-session", getSessionFile: () => sessionFile },
    modelRegistry: { getModelOfType: () => model },
  };
  const hooks = { spawn: value => value };
  const rawTools = [sdk.createReadTool(root), sdk.createBashTool(root, { spawnHook: value => hooks.spawn({ ...value,
    env: { ...value.env, HERD_ROOT: herdRoot, HERDR_ENV: "1", HERDR_DEPTH: "0", HERD_NUDGE_GRACE_MS: "300",
      HERDR_BIN: fileURLToPath(new URL("fake_herdr.py", import.meta.url)), FAKE_HERDR_STATE: fakeState,
      FAKE_HERDR_SCENARIO: scenario, PI_CODING_AGENT_DIR: agentDir, PI_SDK_ROOT: packageRoot, PI_OFFLINE: "1" },
  }) })];
  const tools = rawTools.map(tool => ({ ...tool, execute: async (id, args, signal, update) => {
    const result = await tool.execute(id, args, signal, update, ctx);
    if (result.details?.fullOutputPath) spills.add(result.details.fullOutputPath);
    return result;
  } }));
  ctx.tools = tools;
  ctx.executeTool = (name, args, options) => runToolCall({ type: "toolCall", id: `nested-${++sequence}`, name, arguments: args },
    { context: { messages: [], tools }, assistantMessage: {}, signal: options.signal });
  const codemode = createCodemodeTool(tools, { models: true,
    appendEntry: (customType, data) => branch.push({ type: "custom", customType, data }) });
  const outer = { ...codemode, execute: (id, args, signal, update) => codemode.execute(id, args, signal, update, ctx) };
  return {
    root, rundir, branch, tools, ctx, hooks, fakeState, sessionFile, spills,
    async call(input, options = {}, finish = "return output;") {
      const code = `// @options: ${JSON.stringify({ max_output_tokens: 30000, ...options })}\n` +
        `const collect = eval(await tools.read({path: ${JSON.stringify(path.join(references, "collect.js"))}}));\n` +
        `const output = await collect(${JSON.stringify({ helper: path.join(references, "herdr-collect.mjs"), ...input })});\n${finish}`;
      const call = { type: "toolCall", id: `outer-${++sequence}`, name: "codemode", arguments: { code } };
      const entries = readFileSync(sessionFile, "utf8").trim().split("\n").map(line => JSON.parse(line));
      entries.at(-1).message.content = [call];
      writeFileSync(sessionFile, entries.map(entry => JSON.stringify(entry) + "\n").join(""));
      const outcome = await runToolCall(call, { context: { messages: [], tools: [outer] }, assistantMessage: {} });
      if (outcome.result.details?.fullOutputPath) spills.add(outcome.result.details.fullOutputPath);
      return outcome;
    },
  };
}

function text(result) {
  return result.content.filter(block => block.type === "text").map(block => block.text).join("");
}

test("codemode returns the full >50 KiB leaf result and its citations in one untruncated response", async (t) => {
  const body = "# Findings\nBEGIN\n" + "x".repeat(40000) + "\nMIDDLE\n" + "x".repeat(40000) +
    "\nSources: [primary](https://example.test/primary)\nEND\n<!-- herd:done -->\n";
  const f = fixture(t, body);
  const outcome = await f.call({ collect: f.rundir });
  assert.equal(outcome.isError, false, text(outcome.result));
  assert.match(text(outcome.result), /Collection complete/);
  assert.ok(text(outcome.result).includes(body), "The exact original Markdown, not a summary, must reach model-facing content");
  assert.equal(outcome.result.details.fullOutputPath, undefined);
  assert.doesNotMatch(text(outcome.result), /Warning: truncated output/);
});

test("successful collection is recorded on the session branch and is not returned twice", async (t) => {
  const f = fixture(t, "# Findings\nA cited result\n<!-- herd:done -->\n");
  const first = await f.call({ collect: f.rundir });
  assert.equal(first.isError, false, text(first.result));
  const repeated = await f.call({ collect: f.rundir });
  assert.equal(repeated.isError, true);
  assert.match(text(repeated.result), /already collected/);
  assert.doesNotMatch(text(repeated.result), /A cited result/);
});

test("a run path alias cannot bypass the receipt and return successful findings twice", async (t) => {
  const body = "# Findings\nRECEIPT-ONCE\n<!-- herd:done -->\n";
  const f = fixture(t, body);
  const first = await f.call({ collect: f.rundir });
  assert.equal(first.isError, false, text(first.result));
  const alias = await f.call({ collect: f.rundir + "/." });
  assert.equal(alias.isError, true);
  assert.match(text(alias.result), /exact.*rundir/);
  assert.doesNotMatch(text(alias.result), /RECEIPT-ONCE/);
});

test("oversized Markdown crosses codemode calls on a lossless, contiguous cursor", async (t) => {
  const body = "# Findings\r\nBEGIN\r\n" + "x".repeat(110000) + "\r\n" + "😀界é".repeat(3000) +
    "\r\nSources: [primary](https://example.test/primary)\r\nEND\r\n<!-- herd:done -->\r\n";
  const f = fixture(t, body, 1000000);
  let outcome = await f.call({ collect: f.rundir });
  const chunks = [];
  for (let count = 0; count < 10; count++) {
    assert.equal(outcome.isError, false, text(outcome.result));
    assert.equal(outcome.result.details.fullOutputPath, undefined);
    const payload = text(outcome.result);
    chunks.push(payload.replace(/^Script completed\nWall time [^\n]+\nOutput:\nCollection [^\n]+\n\n/, ""));
    if (payload.includes("Collection complete")) break;
    assert.match(payload, /Collection more/);
    outcome = await f.call({ next: f.rundir });
  }
  assert.match(text(outcome.result), /Collection complete/);
  assert.equal(chunks.length, 2);
  assert.ok(chunks.join("").includes(body));
});

test("one codemode call performs blocking fanout and returns the run report with findings", async (t) => {
  const f = fixture(t, "unused fixture");
  const plan = { slug: "from-native", close: "on_success", cadence_ms: 50, quiesce_ms: 250,
    inactivity_ms: 1500, cap_ms: 8000,
    leaves: [{ name: "spec", prompt: "Investigate the fixture. Write findings to {{path}}." }] };
  const outcome = await f.call({ run: ["--plan", "-"], input: JSON.stringify(plan) });
  assert.equal(outcome.isError, false, text(outcome.result));
  assert.match(text(outcome.result), /Collection complete/);
  assert.match(text(outcome.result), /"closed": true/);
  assert.match(text(outcome.result), /# spec findings\n\nbody\n\n<!-- herd:done -->/);
});

test("mixed success stays report-only until explicit final collection, including exit code 1", async (t) => {
  const body = "# Findings\nSUCCESS-ONCE\n<!-- herd:done -->\n";
  const f = fixture(t, body);
  const failed = { name: "other", path: path.join(f.rundir, "other.md"), dest: null };
  const planPath = path.join(f.rundir, "plan.json");
  const reportPath = path.join(f.rundir, "report.json");
  const plan = JSON.parse(readFileSync(planPath));
  plan.leaves.push(failed);
  writeFileSync(planPath, JSON.stringify(plan));
  const report = JSON.parse(readFileSync(reportPath));
  report.ok = false;
  report.closed = false;
  report.leaves.push({ ...failed, ok: false, delivered: false, reason: "no_marker", attempts: 3 });
  writeFileSync(reportPath, JSON.stringify(report));
  const first = await f.call({ collect: f.rundir });
  assert.equal(first.isError, false, text(first.result));
  assert.match(text(first.result), /Collection report_only/);
  assert.doesNotMatch(text(first.result), /SUCCESS-ONCE/);
  const final = await f.call({ collect: f.rundir, final: true });
  assert.equal(final.isError, false, text(final.result));
  assert.match(text(final.result), /Collection complete/);
  assert.ok(text(final.result).includes(body));
  assert.match(text(final.result), /"reason": "no_marker"/);
});

test("a smaller actual outer output budget automatically uses pages instead of truncating", async (t) => {
  const body = "# Findings\nBEGIN\n" + "x".repeat(80000) + "\nEND [citation](https://example.test/)\n<!-- herd:done -->\n";
  const f = fixture(t, body);
  let outcome = await f.call({ collect: f.rundir }, { max_output_tokens: 10000 });
  const chunks = [];
  for (let count = 0; count < 10; count++) {
    assert.equal(outcome.isError, false, text(outcome.result));
    assert.equal(outcome.result.details.fullOutputPath, undefined, "The actual codemode budget must be honored");
    const payload = text(outcome.result);
    assert.doesNotMatch(payload, /Warning: truncated output/);
    chunks.push(payload.replace(/^Script completed\nWall time [^\n]+\nOutput:\nCollection [^\n]+\n\n/, ""));
    if (payload.includes("Collection complete")) break;
    assert.match(payload, /Collection more/);
    outcome = await f.call({ next: f.rundir }, { max_output_tokens: 10000 });
  }
  assert.match(text(outcome.result), /Collection complete/);
  assert.equal(chunks.length, 3);
  assert.ok(chunks.join("").includes(body));
});

test("truncated native Bash capture is rejected before any fanout can start", async (t) => {
  const f = fixture(t, "unused");
  f.hooks.spawn = value => ({ ...value, command: `python3 -c 'print("x" * (1024 * 1024 + 64))'` });
  const outcome = await f.call({ run: ["--plan", "-"], input: "{}" });
  assert.equal(outcome.isError, true);
  assert.match(text(outcome.result), /Incomplete nested Bash capture/);
  assert.equal(existsSync(f.fakeState), false);
});

test("unknown native model limits stop before fanout", async (t) => {
  const f = fixture(t, "unused");
  f.ctx.modelRegistry.getModelOfType = () => undefined;
  const outcome = await f.call({ run: ["--plan", "-"], input: "{}" });
  assert.equal(outcome.isError, true);
  assert.match(text(outcome.result), /context window is unknown/);
  assert.equal(existsSync(f.fakeState), false);
});

test("cancellation leaves the saved cursor replayable without losing a page", async (t) => {
  const body = "# Findings\nBEGIN\n" + "x".repeat(110000) + "\nEND [source](https://example.test/)\n<!-- herd:done -->\n";
  const f = fixture(t, body, 1000000);
  const first = await f.call({ collect: f.rundir });
  assert.equal(first.isError, false, text(first.result));
  assert.match(text(first.result), /Collection more/);
  let sawPage = false;
  f.hooks.spawn = value => {
    if (!value.command.includes("--page")) return value;
    sawPage = true;
    return { ...value, command: "sleep 3; " + value.command };
  };
  const cancelled = await f.call({ next: f.rundir }, { timeout_ms: 1500 });
  assert.equal(sawPage, true);
  assert.equal(cancelled.isError, true);
  assert.match(text(cancelled.result), /timed out|aborted/i);
  f.hooks.spawn = value => value;
  const retried = await f.call({ next: f.rundir });
  assert.equal(retried.isError, false, text(retried.result));
  assert.match(text(retried.result), /Collection complete/);
  const chunks = [first, retried].map(outcome => text(outcome.result)
    .replace(/^Script completed\nWall time [^\n]+\nOutput:\nCollection [^\n]+\n\n/, ""));
  assert.ok(chunks.join("").includes(body));
});

test("a script failing after collection commits no receipt and the full result can be retried", async (t) => {
  const body = "# Findings\nFINAL-OUTPUT-RECOVERY [source](https://example.test/)\n<!-- herd:done -->\n";
  const f = fixture(t, body);
  const failed = await f.call({ collect: f.rundir }, {}, 'throw new Error("Synthetic final output failure");');
  assert.equal(failed.isError, true);
  assert.match(text(failed.result), /Synthetic final output failure/);
  assert.doesNotMatch(text(failed.result), /FINAL-OUTPUT-RECOVERY/);
  const retried = await f.call({ collect: f.rundir });
  assert.equal(retried.isError, false, text(retried.result));
  assert.match(text(retried.result), /Collection complete/);
  assert.ok(text(retried.result).includes(body));
});

test("a context limitation returns no findings and does not mark them received", async (t) => {
  const body = "# Findings\nCONTEXT-PRESSURE\n" + "x".repeat(10000) + "\n<!-- herd:done -->\n";
  const f = fixture(t, body, 20000);
  const limited = await f.call({ collect: f.rundir });
  assert.equal(limited.isError, false, text(limited.result));
  assert.match(text(limited.result), /Collection limited \(context_limit\)/);
  assert.doesNotMatch(text(limited.result), /CONTEXT-PRESSURE/);
  f.ctx.model.contextWindow = 128000;
  const retried = await f.call({ collect: f.rundir });
  assert.equal(retried.isError, false, text(retried.result));
  assert.match(text(retried.result), /Collection complete/);
  assert.ok(text(retried.result).includes(body));
});

test("compaction between pages stops collection instead of treating a summary as intact findings", async (t) => {
  const f = fixture(t, "# Findings\n" + "x".repeat(110000) + "\n<!-- herd:done -->\n", 1000000);
  const first = await f.call({ collect: f.rundir });
  assert.equal(first.isError, false, text(first.result));
  assert.match(text(first.result), /Collection more/);
  const entries = readFileSync(f.sessionFile, "utf8").trim().split("\n").map(line => JSON.parse(line));
  const assistant = entries.at(-1).message;
  entries.push({ type: "compaction", id: "compact", parentId: "a", firstKeptEntryId: "compact",
    summary: "A summary is not the complete leaf result", tokensBefore: 1000 });
  entries.push({ type: "message", id: "after", parentId: "compact", message: { ...assistant, content: [] } });
  writeFileSync(f.sessionFile, entries.map(entry => JSON.stringify(entry) + "\n").join(""));
  const next = await f.call({ next: f.rundir });
  assert.equal(next.isError, true);
  assert.match(text(next.result), /compacted between collection pages/);
  assert.doesNotMatch(text(next.result), /Collection complete/);
});

test("herder-only CLI activation and a real SDK session preserve the payload to a loopback provider", async (t) => {
  const body = "# Findings\nBEGIN-SDK\n" + "x".repeat(80000) +
    "\nEND-SDK [primary](https://example.test/primary)\n<!-- herd:done -->\n";
  const f = fixture(t, body);
  const agentDir = path.join(f.root, "sdk-agent");
  mkdirSync(agentDir);
  const saved = new Map(["HERD_ROOT", "PI_CODING_AGENT_DIR", "PI_SDK_ROOT", "PI_OFFLINE"].map(key => [key, process.env[key]]));
  Object.assign(process.env, { HERD_ROOT: path.join(f.root, "herd"), PI_CODING_AGENT_DIR: agentDir,
    PI_SDK_ROOT: packageRoot, PI_OFFLINE: "1" });
  t.after(() => {
    for (const [key, value] of saved) value === undefined ? delete process.env[key] : process.env[key] = value;
  });
  const code = `// @options: {"max_output_tokens":30000}\n` +
    `const collect = eval(await tools.read({path:${JSON.stringify(path.join(references, "collect.js"))}}));\n` +
    `return await collect(${JSON.stringify({ helper: path.join(references, "herdr-collect.mjs"), collect: f.rundir })});`;
  const requests = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    requests.push(JSON.parse(raw));
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    const chunk = (delta, finish = null) => response.write(`data: ${JSON.stringify({
      id: "local-herd-test", object: "chat.completion.chunk", created: 0, model: "payload",
      choices: [{ index: 0, delta, finish_reason: finish }],
    })}\n\n`);
    if (requests.length === 1) {
      chunk({ role: "assistant", tool_calls: [{ index: 0, id: "collect-fixture", type: "function",
        function: { name: "codemode", arguments: JSON.stringify({ code }) } }] });
      chunk({}, "tool_calls");
    } else {
      chunk({ role: "assistant", content: "fixture synthesis placeholder" });
      chunk({}, "stop");
    }
    response.write(`data: ${JSON.stringify({ id: "local-herd-test", object: "chat.completion.chunk", choices: [],
      usage: { prompt_tokens: 1000, completion_tokens: 100, total_tokens: 1100 } })}\n\n`);
    response.end("data: [DONE]\n\n");
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const model = { provider: "herd-payload-test", id: "payload", name: "payload", api: "openai-completions",
    baseUrl: `http://127.0.0.1:${server.address().port}/v1`, reasoning: false, input: ["text"],
    contextWindow: 128000, maxTokens: 1000, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
  const loader = new sdk.DefaultResourceLoader({ cwd: f.root, agentDir, noExtensions: true,
    noSkills: true, noThemes: true, noContextFiles: true, noPromptTemplates: true,
    extensionFactories: [sdk.createCodemodeExtension(), pi => {
      // Record only this session's exact spill paths, including nested Bash captures.
      pi.on("tool_execution_end", event => {
        if (event.result?.details?.fullOutputPath) f.spills.add(event.result.details.fullOutputPath);
      });
      pi.registerProvider(model.provider, {
        api: model.api, baseUrl: model.baseUrl, apiKey: "local-test-only", models: [{ ...model, type: "chat" }],
      });
    }] });
  await loader.reload();
  assert.deepEqual(loader.getExtensions().errors, []);
  const runtime = await sdk.ModelRuntime.create({ authPath: path.join(agentDir, "auth.json"), modelsPath: null,
    refreshOnCreate: false });
  const settings = sdk.SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false },
    cacheWarming: "off" });
  const cli = sdk.parseArgs(["--tools", "+codemode"]);
  const { session } = await sdk.createAgentSession({ cwd: f.root, agentDir, model, modelRuntime: runtime,
    resourceLoader: loader, settingsManager: settings, tools: cli.tools,
    sessionManager: sdk.SessionManager.create(f.root, path.join(f.root, "sessions")) });
  t.after(() => session.dispose());
  await session.bindExtensions({ mode: "json" });
  assert.ok(session.getActiveToolNames().includes("codemode"));
  assert.equal(settings.getSettings().defaultTools, undefined, "Activation must not change default settings");
  await session.prompt("Execute the fixed local collection fixture.");
  assert.equal(requests.length, 2);
  assert.ok(f.spills.size > 0, "The SDK session's nested Bash spill must be owned and cleaned by this fixture");
  const toolResult = session.messages.find(message => message.role === "toolResult" && message.toolName === "codemode");
  assert.ok(toolResult, JSON.stringify(session.messages));
  assert.equal(toolResult.isError, false, text(toolResult));
  assert.ok(text(toolResult).includes(body));
  const providerText = requests[1].messages.map(message => typeof message.content === "string" ? message.content :
    (message.content || []).filter(block => block.type === "text").map(block => block.text).join("")).join("\n");
  assert.ok(providerText.includes(body), "The complete original result must reach the next provider request");
  assert.equal(session.sessionManager.getBranch().some(entry => entry.type === "compaction"), false);
});
