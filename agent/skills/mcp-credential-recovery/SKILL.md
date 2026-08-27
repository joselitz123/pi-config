---
name: mcp-credential-recovery
description: Diagnose and repair MCP credential failures in Pi on macOS.
disable-model-invocation: true
---

# MCP credential recovery

Use this skill only when the user explicitly invokes it. It diagnoses the credential **seam** first, then repairs the active MCP setup and proves the repair with a live MCP connection.

The first backend is Bitwarden Secrets Manager with macOS Keychain storage. Keep the workflow backend-shaped so another credential source can replace it without changing the diagnosis steps.

## Operating contract

- Diagnose before editing.
- Repair only the active winning MCP definition; leave lower-precedence imports untouched.
- Keep secret values out of output, files, command arguments, transcripts, and diffs.
- Show presence and lengths only when validating credentials.
- Treat Pi, herdr, MCP child processes, and the remote server as separate seams.
- Prefer a small local resolver over copying credentials into MCP configuration.
- Use the existing macOS Keychain when process-environment inheritance is unreliable.
- A repair is incomplete until the MCP connection and a safe credential-dependent tool call succeed.

## Phase 1 — Probe the setup

### 1. Inventory the active configuration

Inspect the MCP adapter's discovery rules and enumerate every definition of the target server:

- `~/.config/mcp/mcp.json`
- `~/.pi/agent/mcp.json`
- project `.mcp.json`
- project Pi MCP overrides
- imported host configurations

Identify the winning definition and its source path. Do not edit until this is known.

Record, without values:

- server name;
- source path and precedence;
- command versus URL transport;
- credential-bearing fields;
- lifecycle and timeout settings;
- whether the server is cached, lazy, connected, or failed.

**Completion criterion:** every definition of the target server is accounted for and one active winner is identified.

### 2. Establish a tight red probe

Run the smallest probe that reproduces the failure:

1. validate the MCP config syntax;
2. validate the credential map contains the expected non-empty IDs;
3. validate the credential resolver without printing its output;
4. connect the target MCP server;
5. run `tools/list`;
6. if possible, run one clearly read-only tool selected from metadata.

Use lengths or boolean status instead of values. Capture the exact red error category, such as:

- missing credential source;
- invalid BWS token;
- missing decryption key;
- resolver command failure;
- missing header;
- HTTP 401/403;
- HTTP 404;
- invalid MCP transport;
- server process exit;
- tool-level authentication error.

**Completion criterion:** one repeatable command or interaction reproduces the user's failure and identifies the first failing seam.

### 3. Trace process inheritance

Check the environment at the relevant process boundary, never assuming that a variable visible to a Pi bash tool is visible to the MCP adapter process.

Check names and lengths only:

```bash
for name in BWS_ACCESS_TOKEN PI_BWS_ACCESS_TOKEN; do
  value=$(printenv "$name" 2>/dev/null || true)
  printf '%s present=%s length=%s\n' \
    "$name" \
    "$([ -n "$value" ] && echo yes || echo no)" \
    "${#value}"
done
```

Remember: `bws run` injects managed secrets but removes `BWS_ACCESS_TOKEN` from the child environment. If the resolver must call `bws` from inside Pi or herdr, use one of these deliberate strategies:

- launch Pi/herdr directly with `BWS_ACCESS_TOKEN` exported;
- carry the token under a different explicitly chosen variable;
- prefer macOS Keychain so the resolver does not depend on process inheritance.

Do not infer success from the launcher shell alone. Test the exact resolver command in the process context that will run it.

## Phase 2 — Repair the credential seam

### 4. Prefer macOS Keychain for durable resolution

When the process environment is unreliable, use the macOS Keychain item:

```text
account: $USER
service: pi-bws-access-token
```

The resolver should use this order:

1. `BWS_ACCESS_TOKEN`, when explicitly present;
2. `security find-generic-password -a "$USER" -s pi-bws-access-token -w`;
3. a redacted failure explaining which source is missing.

A human must enter or rotate the BWS token. Never ask the user to paste it into chat or write it into a tracked file.

Validate the complete token before storing it:

```bash
id=$(jq -r '.<alias>' ~/.config/pi/bws-secret-ids.json)
BWS_ACCESS_TOKEN="$BWS_ACCESS_TOKEN" bws secret get "$id" --output none
```

The Bitwarden token must include its decryption key. If BWS reports `Doesn't contain a decryption key`, stop and request a newly generated complete machine-account token.

### 5. Resolve secrets at the MCP seam

For stdio servers:

- resolve the current credential when the MCP child starts;
- export it only to that child;
- keep raw values out of `mcp.json`.

For HTTP servers:

- use native headers when the adapter's transport is compatible;
- use a command-backed header resolver when headers must be obtained outside the process environment;
- preserve required prefixes, such as `Basic `, separately from the raw stored secret.

Keep one resolver interface per backend, for example:

```text
resolve-secret <alias> → secret value on stdout
```

or, for multiple HTTP headers:

```text
resolve-headers <server> → JSON object of headers on stdout
```

Diagnostics go to stderr and contain no secret values.

**Completion criterion:** the exact resolver command succeeds from the MCP execution context and the target credential-dependent process receives the expected header or environment variable.

## Phase 3 — Repair transport compatibility when proven

### 6. Probe HTTP MCP servers directly before changing transport

For an HTTP server, send the smallest raw JSON-RPC sequence:

1. `initialize`;
2. `notifications/initialized`;
3. `tools/list`;
4. one safe read-only `tools/call`, if available.

Use the server's real endpoint and resolved headers. Record status codes and session-header presence, never credential values.

Interpret the sequence:

- `initialize` 200 + `tools/list` 200: transport works;
- exact 400 `Invalid HTTP request received.` only for `notifications/initialized`, followed by successful `tools/list`: known server quirk;
- 401/403: credential or required-header failure;
- 404: wrong endpoint or transport path;
- HTML or unrelated JSON: endpoint/proxy mismatch.

Use native Pi HTTP transport whenever the direct probe matches it.

### 7. Use a bridge only for a proven incompatibility

If the direct probe succeeds only by tolerating a narrow non-fatal response that Pi treats as fatal, create a local stdio bridge for that server. The bridge must:

- speak newline-delimited JSON-RPC on stdio;
- send direct HTTP JSON-RPC requests upstream;
- preserve the MCP session ID;
- forward `initialize`, `tools/list`, and `tools/call` responses;
- ignore only the exact proven non-fatal response;
- return all other HTTP failures as MCP errors;
- resolve headers through the backend resolver at bridge startup or request time, according to the chosen lifecycle;
- log only redacted diagnostics.

Do not install a bridge merely because a credential failed. Prove the transport defect first.

## Phase 4 — Apply and verify

### 8. Edit only the active definition

Before writing:

- preserve unrelated servers;
- preserve lower-precedence/imported files;
- avoid raw secret literals;
- validate the intended command, URL, headers, resolver, and lifecycle fields in memory.

Write atomically, validate JSON immediately, and verify that the resulting definition is still the active winner.

### 9. Reconnect and verify

Reload or restart only what is necessary for the active MCP configuration to be read. Prefer reconnecting the affected MCP server over restarting Pi or herdr.

Verify in order:

1. resolver succeeds without displaying values;
2. MCP `initialize` succeeds;
3. `tools/list` succeeds;
4. a clearly read-only tool call succeeds when one is available;
5. the reported failure no longer occurs.

Safe automatic tool selection may use metadata names and descriptions containing `list`, `get`, `search`, `describe`, `status`, or equivalent read-only language. If no tool is clearly safe, report that connection and discovery passed but tool execution was not verified.

## Completion report

End with a concise report containing:

- first failing seam;
- root cause;
- files or Keychain service changed;
- whether any raw secret was written (must be no);
- resolver validation result;
- MCP connection result;
- safe tool-call result or why it was not attempted;
- remaining limitation, if any.

The skill is complete only when every modified MCP definition is accounted for, the active definition is validated, the resolver works in the MCP execution context, the target server initializes, `tools/list` succeeds, and a safe credential-dependent tool call succeeds when available.
