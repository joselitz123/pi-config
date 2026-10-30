import { spawn } from "node:child_process";
import { chmod, lstat, mkdir, open } from "node:fs/promises";
import net from "node:net";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const defaultStateDir = () => process.env.PI_SHARED_BROWSER_STATE_DIR || path.join(homedir(), ".cache/pi-shared-browser");
const brokerPath = fileURLToPath(new URL("./broker.py", import.meta.url));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class BrowserRequestError extends Error {
  constructor(code, message, sent = false) {
    super(message);
    this.name = "BrowserRequestError";
    this.code = code;
    this.sent = sent;
  }
}

function exchange(stateDir, operation, signal) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(path.join(stateDir, "browser.sock"));
    const chunks = [];
    let size = 0;
    let sent = false;
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      socket.destroy();
      error ? reject(error) : resolve(value);
    };
    const abort = () => finish(new BrowserRequestError("aborted", sent
      ? "Browser call cancelled; it may already have run. Inspect your tabs before retrying."
      : "Browser call cancelled before dispatch.", sent));
    signal?.addEventListener("abort", abort, { once: true });
    socket.setTimeout(180_000, () => finish(new BrowserRequestError("timeout",
      "Browser call timed out; it may have run. Inspect your tabs before retrying.", sent)));
    socket.on("connect", () => {
      if (signal?.aborted) return abort();
      sent = true;
      socket.write(JSON.stringify(operation) + "\n");
    });
    socket.on("error", (error) => finish(new BrowserRequestError(error.code || "connection_failed", error.message, sent)));
    socket.on("end", () => finish(new BrowserRequestError("disconnected", "Browser broker disconnected; inspect tabs before retrying.", sent)));
    socket.on("data", (chunk) => {
      size += chunk.length;
      if (size > 32 * 1024 * 1024) return finish(new BrowserRequestError("oversized_response", "Browser response exceeded 32 MiB. Use a filePath or a narrower query.", sent));
      chunks.push(chunk);
      if (!chunk.includes(10)) return;
      try {
        const response = JSON.parse(Buffer.concat(chunks).toString("utf8").split("\n", 1)[0]);
        if (!response.ok) return finish(new BrowserRequestError(response.error.code, response.error.message, sent));
        finish(undefined, response.result);
      } catch {
        finish(new BrowserRequestError("invalid_response", "Browser broker returned an invalid response.", sent));
      }
    });
  });
}

async function startBroker(stateDir, command) {
  await mkdir(stateDir, { recursive: true, mode: 0o700 });
  const metadata = await lstat(stateDir);
  if (!metadata.isDirectory() || (process.getuid && metadata.uid !== process.getuid())) {
    throw new Error("Browser state must be a directory owned by the current user.");
  }
  await chmod(stateDir, 0o700);
  const log = await open(path.join(stateDir, "broker.log"), "a", 0o600);
  try {
    const args = [brokerPath, "--state-dir", stateDir];
    if (command) args.push("--command-json", JSON.stringify(command));
    const child = spawn(process.env.PI_SHARED_BROWSER_PYTHON || "python3", args, {
      detached: true, stdio: ["ignore", log.fd, log.fd], cwd: stateDir,
    });
    await new Promise((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
    child.unref();
  } finally {
    await log.close();
  }
}

/** Caller identity is supplied by the Pi runtime, never by model arguments.
 * Only a connection failure BEFORE sending may trigger startup and one retry.
 */
export async function browserRequest(owner, operation, options = {}) {
  const stateDir = options.stateDir || defaultStateDir();
  const payload = { ...operation, owner }; // Runtime identity wins over any extra input field.
  try {
    return await exchange(stateDir, payload, options.signal);
  } catch (error) {
    if (error.sent || !["ENOENT", "ECONNREFUSED"].includes(error.code)) throw error;
  }
  options.signal?.throwIfAborted();
  await startBroker(stateDir, options.command);
  for (let attempt = 0; attempt < 100; attempt++) {
    options.signal?.throwIfAborted();
    try {
      const status = await exchange(stateDir, { owner, action: "status" }, options.signal);
      if (status.version !== 1) throw new Error("Incompatible shared browser broker; restart it before continuing.");
      return await exchange(stateDir, payload, options.signal);
    } catch (error) {
      if (error.sent || !["ENOENT", "ECONNREFUSED"].includes(error.code)) throw error;
    }
    await sleep(50);
  }
  throw new Error(`Shared browser broker did not start. Check ${path.join(stateDir, "broker.log")}.`);
}
