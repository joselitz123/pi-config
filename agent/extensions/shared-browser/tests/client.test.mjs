import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { browserRequest } from "../client.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));

test("concurrent first-use Pi clients share one broker and cannot spoof ownership", async (t) => {
  const stateDir = await mkdtemp("/tmp/pi-browser-client-");
  const options = { stateDir, command: ["python3", path.join(root, "tests/fake_chrome.py")] };
  let pid;
  t.after(async () => {
    if (pid) process.kill(pid, "SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 200));
    await rm(stateDir, { recursive: true, force: true });
  });
  const sessions = ["session-a", "leaf-b", "leaf-c", "session-d", "leaf-e"];
  const opened = await Promise.all(sessions.map((owner) =>
    browserRequest(owner, { action: "open", url: `https://${owner}.invalid`, owner: "forged" }, options),
  ));
  assert.equal(new Set(opened.map((result) => result.pageId)).size, 5);
  const statuses = await Promise.all(sessions.map((owner) => browserRequest(owner, { action: "status" }, options)));
  pid = statuses[0].pid;
  assert.equal(new Set(statuses.map((result) => result.pid)).size, 1);
  assert.deepEqual(statuses.map((result) => result.ownedTabCount), [1, 1, 1, 1, 1]);
  await assert.rejects(browserRequest("leaf-b", {
    action: "call", tool: "close_page", args: { pageId: opened[0].pageId },
  }, options), /not owned/);
});

test("cancelled queued work is skipped, while an in-flight tab remains owned without retry", async (t) => {
  const stateDir = await mkdtemp("/tmp/pi-browser-cancel-");
  const options = { stateDir, command: ["python3", path.join(root, "tests/fake_chrome.py")] };
  const status = await browserRequest("setup", { action: "status" }, options);
  t.after(async () => {
    process.kill(status.pid, "SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 200));
    await rm(stateDir, { recursive: true, force: true });
  });
  await browserRequest("setup", { action: "tools" }, options);
  const blocking = browserRequest("session-a", { action: "open", url: "https://slow.invalid" }, options);
  await new Promise((resolve) => setTimeout(resolve, 30));
  const queuedSignal = new AbortController();
  const queued = browserRequest("leaf-b", { action: "open", url: "about:blank" }, { ...options, signal: queuedSignal.signal });
  setTimeout(() => queuedSignal.abort(), 50);
  await assert.rejects(queued, /cancelled/);
  await blocking;
  assert.deepEqual((await browserRequest("leaf-b", { action: "tabs" }, options)).pages, []);
  const inFlightSignal = new AbortController();
  const inFlight = browserRequest("leaf-c", { action: "open", url: "https://slow.invalid" }, { ...options, signal: inFlightSignal.signal });
  setTimeout(() => inFlightSignal.abort(), 50);
  await assert.rejects(inFlight, /may already have run/);
  const tabs = await browserRequest("leaf-c", { action: "tabs" }, options);
  assert.equal(tabs.pages.length, 1, "No automatic retry or loss of the completed tab's owner");
});
