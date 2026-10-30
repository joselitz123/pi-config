import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { promisify } from "node:util";
import { mkdir, mkdtemp, rm, stat, symlink, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { readFileSync, realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import { browserRequest } from "../client.mjs";
import { toolModel } from "./fake-model.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
let sdkRoot = process.env.PI_SDK_ROOT || path.dirname(realpathSync(execFileSync("which", ["pi"], { encoding: "utf8" }).trim()));
while (true) {
  try { if (JSON.parse(readFileSync(path.join(sdkRoot, "package.json"))).name === "@earendil-works/pi-coding-agent") break; } catch {}
  if (sdkRoot === path.dirname(sdkRoot)) throw new Error("Set PI_SDK_ROOT to your Pi installation");
  sdkRoot = path.dirname(sdkRoot);
}
const sdkEntry = path.join(sdkRoot, "dist/index.js");
const sdk = await import(pathToFileURL(sdkEntry).href);
const offlineModel = {
  id: "offline", name: "offline", api: "openai-completions", provider: "offline",
  baseUrl: "http://127.0.0.1:1", reasoning: false, input: ["text"],
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 128000, maxTokens: 1000,
};

async function piSession(directory, additional = [], model = offlineModel) {
  const events = sdk.createEventBus();
  const loader = new sdk.DefaultResourceLoader({
    cwd: directory, agentDir: directory, eventBus: events,
    noExtensions: true, additionalExtensionPaths: [path.join(root, "index.ts"), ...additional],
    noSkills: true, noThemes: true, noContextFiles: true, noPromptTemplates: true,
    extensionFactories: model.provider === "browser-test" ? [(pi) => pi.registerProvider("browser-test", {
      baseUrl: model.baseUrl, api: "openai-completions", apiKey: "local-test-only", models: [{ ...model, type: "chat" }],
    })] : [],
  });
  await loader.reload();
  assert.deepEqual(loader.getExtensions().errors, []);
  const modelRuntime = await sdk.ModelRuntime.create({ authPath: path.join(directory, "auth.json"), modelsPath: path.join(directory, "models.json") });
  await modelRuntime.setRuntimeApiKey(model.provider, "local-test-only");
  const { session } = await sdk.createAgentSession({
    cwd: directory, agentDir: directory, resourceLoader: loader, model, modelRuntime,
    sessionManager: sdk.SessionManager.inMemory(directory),
    settingsManager: sdk.SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } }),
  });
  await session.bindExtensions({ mode: "json" });
  const browser = session.agent.state.tools.find((tool) => tool.name === "browser");
  assert.ok(browser, "Pi must register the guarded browser tool");
  return { session, events, browser };
}

test("Pi discovers the global extension without extra CLI flags or herd changes", async (t) => {
  const directory = await mkdtemp("/tmp/pi-browser-discovery-");
  t.after(() => rm(directory, { recursive: true, force: true }));
  await mkdir(path.join(directory, "extensions"));
  await symlink(root, path.join(directory, "extensions/shared-browser"), "dir");
  const loader = new sdk.DefaultResourceLoader({
    cwd: directory, agentDir: directory, noSkills: true, noThemes: true, noContextFiles: true, noPromptTemplates: true,
  });
  await loader.reload();
  assert.deepEqual(loader.getExtensions().errors, []);
  assert.ok(loader.getExtensions().extensions.some(extension => extension.tools.has("browser")));
});

test("real Pi sessions use their own runtime identities through the browser tool", async (t) => {
  const directory = await mkdtemp("/tmp/pi-browser-sdk-");
  const stateDir = path.join(directory, "state");
  const old = process.env.PI_SHARED_BROWSER_STATE_DIR;
  process.env.PI_SHARED_BROWSER_STATE_DIR = stateDir;
  const status = await browserRequest("setup", { action: "status" }, {
    stateDir, command: ["python3", path.join(root, "tests/fake_chrome.py")],
  });
  const sessions = [];
  const model = await toolModel();
  t.after(async () => {
    for (const { session } of sessions) {
      await session.extensionRunner.emit({ type: "session_shutdown", reason: "quit" });
      session.dispose();
    }
    process.kill(status.pid, "SIGTERM");
    await model.close();
    old === undefined ? delete process.env.PI_SHARED_BROWSER_STATE_DIR : process.env.PI_SHARED_BROWSER_STATE_DIR = old;
    await new Promise((resolve) => setTimeout(resolve, 200));
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  });
  sessions.push(await piSession(directory, [], model.model), await piSession(directory));
  const [a, b] = await Promise.all(sessions.map(({ browser }, i) => browser.execute(`open-${i}`, {
    action: "open", url: `https://session-${i}.invalid`, owner: "forged",
  })));
  assert.notEqual(a.details.owner, b.details.owner);
  assert.notEqual(a.details.pageId, b.details.pageId);
  await assert.rejects(sessions[1].browser.execute("foreign", {
    action: "call", tool: "take_snapshot", args: { pageId: a.details.pageId },
  }), /not owned/);
  const ownCall = await model.call(sessions[0].session, "browser", {
    action: "call", tool: "take_snapshot", args: { pageId: a.details.pageId },
  });
  assert.equal(ownCall.isError, false);
  assert.match(JSON.stringify(ownCall.content), /session-0.invalid/);
  const admin = await model.call(sessions[0].session, "browser", { action: "claim", pageId: b.details.pageId });
  assert.equal(admin.isError, true, "Human-only actions cannot be invoked through the browser tool");
  for (const origin of ["proxy", "direct", "script", "resource", "iframe"]) {
    let handler;
    sessions[0].events.emit("pi-mcp-adapter:tool-approval-request", {
      serverName: "chrome-devtools", originalToolName: "navigate_page", args: { pageId: a.details.pageId },
      origin, supportsDenyReason: true, claim: (candidate) => { handler = candidate; return true; },
    });
    assert.equal((await handler()).decision, "deny", origin);
  }
});

test("installed MCP adapter rejects raw proxy, direct, and runtime/script bypasses", async (t) => {
  const directory = await mkdtemp("/tmp/pi-browser-adapter-");
  const adapterRoot = process.env.PI_BROWSER_ADAPTER_ROOT || path.join(homedir(), ".pi/agent/npm/node_modules/pi-mcp-adapter");
  const fixture = path.join(directory, "adapter.ts");
  const server = { command: "python3", args: [path.join(root, "tests/fake_chrome.py")], lifecycle: "lazy-keep-alive", approveTools: false };
  await writeFile(fixture, `import { createMcpAdapter } from ${JSON.stringify(path.join(adapterRoot, "index.ts"))};\nexport default createMcpAdapter({ config: ${JSON.stringify({ mcpServers: { "chrome-devtools": server, other: server } })} });\n`);
  const model = await toolModel();
  const { session, events } = await piSession(directory, [fixture], model.model);
  t.after(async () => {
    await session.extensionRunner.emit({ type: "session_shutdown", reason: "quit" });
    session.dispose();
    await model.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  });
  const callRuntime = async (server) => {
    const request = { version: 1, server, tool: "new_page", args: { url: "about:blank" } };
    events.emit("pi-mcp-adapter:runtime-tool-call:v1", request);
    assert.ok(request.result, "Adapter must handle its documented runtime-call event");
    return await request.result;
  };
  const denied = await callRuntime("chrome-devtools");
  assert.equal(denied.ok, false);
  assert.match(denied.error.message, /approval_denied/);
  const allowed = await callRuntime("other");
  assert.equal(allowed.ok, true, "Other MCP servers must be unaffected");
  const proxyResult = await model.call(session, "mcp", { server: "chrome-devtools", tool: "new_page", args: { url: "about:blank" } });
  assert.match(JSON.stringify(proxyResult), /Raw Chrome MCP calls are blocked/);
  const rawName = session.getAllTools().find((tool) => /chrome_devtools.*new_page/.test(tool.name))?.name;
  assert.ok(rawName, "Adapter must register the raw deferred tool for the direct-path test");
  session.setActiveToolsByName([...session.getActiveToolNames(), rawName]);
  const directResult = await model.call(session, rawName, { url: "about:blank" });
  assert.match(JSON.stringify(directResult), /Raw Chrome MCP calls are blocked/);
});

test("independent Pi processes share Chrome and retain separate ownership after exiting", async (t) => {
  const directory = await mkdtemp("/tmp/pi-browser-process-");
  const stateDir = path.join(directory, "state");
  const command = process.env.PI_BROWSER_TEST_REAL_CHROME === "1"
    ? [...JSON.parse(process.env.CHROME_MCP_COMMAND || '["npx","--yes","chrome-devtools-mcp@1.11.0"]'),
      "--headless", "--page-id-routing", "--experimental-structured-content", "--no-category-extensions",
      "--no-usage-statistics", "--no-performance-crux", `--user-data-dir=${path.join(stateDir, "profile")}`]
    : ["python3", path.join(root, "tests/fake_chrome.py")];
  const status = await browserRequest("setup", { action: "status" }, { stateDir, command });
  t.after(async () => {
    process.kill(status.pid, "SIGTERM");
    // Wait for this test's broker, including its Chrome child, to release the profile.
    for (let i = 0; i < 100; i++) {
      try { await stat(path.join(stateDir, "browser.sock")); }
      catch { break; }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  });
  const source = `
    import * as sdk from ${JSON.stringify(pathToFileURL(sdkEntry).href)};
    const directory = process.env.CHILD_AGENT_DIR;
    const loader = new sdk.DefaultResourceLoader({cwd: directory, agentDir: directory, noExtensions: true,
      additionalExtensionPaths: [${JSON.stringify(path.join(root, "index.ts"))}], noSkills: true,
      noThemes: true, noContextFiles: true, noPromptTemplates: true});
    await loader.reload();
    if (loader.getExtensions().errors.length) throw Error(JSON.stringify(loader.getExtensions().errors));
    const {session} = await sdk.createAgentSession({cwd: directory, agentDir: directory, resourceLoader: loader,
      model: ${JSON.stringify(offlineModel)}, sessionManager: sdk.SessionManager.inMemory(directory),
      settingsManager: sdk.SettingsManager.inMemory({compaction: {enabled: false}})});
    await session.bindExtensions({mode: "json"});
    try {
      const browser = session.agent.state.tools.find(t => t.name === "browser");
      const result = await browser.execute("open", {action: "open", url: "about:blank"});
      console.log(JSON.stringify(result.details));
    } finally {
      await session.extensionRunner.emit({type: "session_shutdown", reason: "quit"});
      session.dispose();
    }
  `;
  const children = await Promise.all([0, 1].map(() => promisify(execFile)(process.execPath, ["--input-type=module", "-e", source], {
    env: { ...process.env, CHILD_AGENT_DIR: directory, PI_SHARED_BROWSER_STATE_DIR: stateDir }, timeout: 45_000,
  })));
  const [a, b] = children.map(({ stdout }) => JSON.parse(stdout.trim().split("\n").at(-1)));
  assert.notEqual(a.owner, b.owner);
  assert.notEqual(a.pageId, b.pageId);
  for (const result of [a, b]) {
    const tabs = await browserRequest(result.owner, { action: "tabs" }, { stateDir });
    assert.deepEqual(tabs.pages.map(p => p.id), [result.pageId]);
  }
  await assert.rejects(browserRequest(b.owner, { action: "call", tool: "close_page", args: { pageId: a.pageId } }, { stateDir }), /not owned/);
});
