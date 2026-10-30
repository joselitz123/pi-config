# Shared automation browser

One persistent automation browser for Pi sessions and herd leaves, with enforced **agent-owned tabs**. Website logins are shared; tab targeting is not. This is a tool-routing guard, not a sandbox against Bash, arbitrary same-origin JavaScript, or other processes running as your OS user.

## Enable

Requires Chrome, Node/npm, and Python **3.11+** on macOS/Linux. Tested with Pi 1.4.0 and pi-mcp-adapter 7.0.0. No extra npm package, launchd job, or rmcp-mux installation is needed for the guard itself; the broker starts the pinned Chrome MCP package through npx.

1. Keep this directory under `~/.pi/agent/extensions/`; Pi discovers `index.ts` automatically, including in plain `pi` processes started by herd.
2. Merge this Pi-only override into `~/.pi/agent/mcp-adapter.json`, preserving other entries:

   ```json
   { "mcpServers": { "chrome-devtools": { "disabled": true } } }
   ```

   This prevents the old per-Pi launcher from starting. The extension also rejects raw Chrome tool calls through Pi hooks and the adapter's approval broker, including runtime/script calls that skip normal tool hooks. Other MCP servers are unaffected. The shared `~/.config/mcp/mcp.json` remains available to other MCP hosts.
3. Run `/reload` in **every existing Pi session**. New sessions and newly spawned herd leaves load the extension automatically. Old processes remain unguarded until reloaded; do not mix old and new browser access.

The first `browser` operation that needs Chrome opens a separate automation profile. Sign into websites there once; it is not your everyday Chrome profile and does not inherit that profile's logins.

## Agent interface

Use the `browser` tool instead of the raw Chrome MCP tools:

```json
{ "action": "open", "url": "https://example.com" }
```

Keep the returned `pageId`. Discover an operation and its schema:

```json
{ "action": "tools", "query": "snapshot" }
{ "action": "describe", "tool": "take_snapshot" }
```

Then use the original Chrome tool name, supplying your owned page ID:

```json
{ "action": "call", "tool": "take_snapshot", "args": { "pageId": 2 } }
{ "action": "call", "tool": "navigate_page", "args": { "pageId": 2, "url": "https://example.org" } }
{ "action": "call", "tool": "close_page", "args": { "pageId": 2 } }
```

`2` is illustrative: use the actual ID from `open` or `tabs`. `tabs` lists only the current session's tabs; `status` reports its owner identity. Raw `new_page`, `list_pages`, and `select_page` are replaced by `open`, `tabs`, and explicit IDs.

Reads and writes to foreign/unowned tabs are rejected before forwarding. Creating tabs and assigning ownership is serialized; URL matching or a global “selected tab” never establishes ownership. Browser-wide and unreviewed operations are denied, even if they accept a page ID. The live `tools` result is the authoritative supported set.

Chrome's file/network restrictions still apply. The upstream working directory is the private browser state directory, not the first caller's project. Screenshot image blocks pass through; oversized text spills to a private temporary file named in the result.

## Human commands

| Command | Effect |
| --- | --- |
| `/browser status` | Your session identity, broker PID/generation, and owned-tab count; does not launch Chrome |
| `/browser tabs` | Your live owned tabs |
| `/browser inspect` | Interactively inspect tab IDs, URLs, and owners across the shared browser |
| `/browser handoff <pageId> <target-session-id>` | Confirm transfer of one of your tabs; the old owner immediately loses access |
| `/browser claim <pageId>` | Confirm taking a tab, including an unowned popup or a retained leaf tab; refuses if ownership changed during confirmation |
| `/browser stop` | Confirm closing the shared browser for **all sessions** and clearing tab ownership; profile data remains on disk |

Ownership changes and stopping are human-confirmed slash commands, not model-callable `browser` actions. Unexpected popups and manually opened tabs remain unowned until explicitly claimed. A herder has no automatic access to its leaves' tabs.

## Lifetime and failures

- The broker starts lazily and outlives individual Pi processes. Exiting a session or closing a herd workspace preserves its tabs. Clean up completed work with `close_page`, or use an explicit human claim for retained leaf tabs.
- Resuming the same Pi session retains its ownership while the broker/upstream instance lives. A new/forked session has a different identity.
- Individual Chrome commands are serialized, matching the upstream's shared mutable context; independent workflows can interleave safely on distinct tabs. This does not promise simultaneous execution of long-running commands.
- A browser/upstream restart invalidates stale ownership. Recycled numeric page IDs cannot inherit an old owner's rights. Open new tabs, or explicitly inspect/claim surviving tabs.
- Cancellation/disconnection after dispatch does not automatically retry a mutation. It may have completed: check `tabs` before repeating it. Queued work whose caller already disconnected is skipped before dispatch.
- A backend failure can affect all callers of this shared browser. Private profile data is persistent; live tab ownership is deliberately not restored blindly across backend restarts.

State lives at `~/.cache/pi-shared-browser/`: `profile/`, `browser.sock`, `broker.lock`, and `broker.log`. The directory is mode `0700`, the socket/log are private, and an OS file lock prevents concurrent first-use launches from creating multiple brokers. Browser content is not logged by the guard; upstream stderr is suppressed. See a startup error by running the pinned command from `broker.py` directly if needed.

Optional host environment overrides: `PI_SHARED_BROWSER_STATE_DIR` and `PI_SHARED_BROWSER_PYTHON`. All participating Pi processes must use the same state directory. These are host settings, not model arguments.

For an update, stop the broker explicitly and reload participating Pi sessions. The Chrome MCP version and structured-output flags in `broker.py` are intentional: review its schemas/result shape and rerun acceptance tests before upgrading. No files in an installed Pi or Chrome package are patched.

## Verification

From `~/.pi`, run the fast acceptance suite (external Chrome is replaced by a protocol fixture; Pi/adapter tests use the installed packages and a local deterministic model endpoint, not a paid AI service):

```sh
python3 -m unittest discover -s agent/extensions/shared-browser/tests -p 'test_broker.py' -v
node --test agent/extensions/shared-browser/tests/*.test.mjs
```

Real-Chrome checks use temporary profiles and localhost pages, never existing browser sessions:

```sh
python3 agent/extensions/shared-browser/tests/live_smoke.py
PI_BROWSER_TEST_REAL_CHROME=1 node --test --test-name-pattern='independent Pi' agent/extensions/shared-browser/tests/pi.test.mjs
```

`live_smoke.py --baseline` intentionally exits nonzero, reproducing the old two-launcher profile collision. `CHROME_MCP_COMMAND` can supply a JSON argv to an already installed Chrome MCP executable for offline tests. `PI_SDK_ROOT` and `PI_BROWSER_ADAPTER_ROOT` select nonstandard installed package locations for the Pi integration tests.

Architecture rationale: [shared browser ownership ADR](../../../docs/adr/0001-shared-browser-ownership.md).
