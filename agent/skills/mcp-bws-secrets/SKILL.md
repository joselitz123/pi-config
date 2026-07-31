---
name: mcp-bws-secrets
description: Diagnose and fix MCP server credential resolution under pi when launched via `bws run` (Bitwarden Secrets Manager). Use when an MCP server (e.g. in ~/.config/mcp/mcp.json) fails with Unauthorized / invalid API key / 401 errors and its env block uses opencode-style {env:VAR} placeholders, or when a secret injected by `bws run` is not reaching the server.
---

# MCP + bws secrets under pi

## When to use

Use this skill when an MCP server (configured in `~/.config/mcp/mcp.json` or another
MCP config) fails to authenticate (`Unauthorized`, `invalid API key`, `401`, etc.)
and **any** of these is true:

- The server is launched under pi via `bws run -- pi` (Bitwarden Secrets Manager).
- Its `env` block uses opencode-style `{env:VAR}` placeholders.
- A secret injected by `bws run` isn't reaching the server.

## Background (root cause)

- `bws run -- <cmd>` injects every accessible Bitwarden secret as an environment
  variable, named after the secret's Bitwarden **key** field (e.g.
  `opencode-tavily_api_key_1`). It does **not** rewrite config files.
- MCP configs commonly map these into a server `env` block with opencode-style
  `{env:VAR}` placeholders. **opencode** resolves those when *it* parses its config.
- **pi** does **not** resolve `{env:VAR}` (nor `$VAR` / `${VAR}` / `!cmd`) inside an
  MCP server's `env` block — it passes the value through **verbatim**. The server
  then receives the literal string and fails authentication.
- Bitwarden "key" names may contain hyphens, so `$VAR` shell expansion won't work;
  the fix uses `printenv <name>`, which accepts any name.

## Fix strategy

Move secret resolution into the server's **`command`** by wrapping it in a shell
that exports each target var from its bws source via `printenv`, then `exec`s the
original command. Drop the now-redundant `env` entries. The child inherits the
bws-injected vars from the parent process, so `printenv` can read them.

This works for **pi and opencode** (both inherit the bws env into spawned servers),
keeps secrets out of plaintext config, requires no Bitwarden changes, and keeps the
shared `mcp.json` valid for both tools.

Result for a `{env:opencode-tavily_api_key_1}` mapping:

```jsonc
// before
"command": "npx",
"args": ["-y", "tavily-mcp@latest"],
"env": { "TAVILY_API_KEY": "{env:opencode-tavily_api_key_1}" }

// after
"command": "sh",
"args": ["-c", "export TAVILY_API_KEY=\"$(printenv opencode-tavily_api_key_1)\"; exec npx -y tavily-mcp@latest"]
// (no env block)
```

## Audit (detect problems)

```bash
./scripts/mcp-secrets.py audit
./scripts/mcp-secrets.py audit -c /path/to/mcp.json
```

Lists each server, any `{env:...}` refs, and whether each source var is currently in
the environment. Exits non-zero if unresolved refs are found.

## Fix (transform one server)

```bash
# auto-detect {env:...} refs for a server:
./scripts/mcp-secrets.py fix tavily
# preview only:
./scripts/mcp-secrets.py fix tavily --dry-run
# explicit mapping (bws var -> env var the server expects):
./scripts/mcp-secrets.py fix tavily --source opencode-tavily_api_key_1 --target TAVILY_API_KEY
```

Backs up the original to `<config>.bak`, rewrites `command`/`args` to a
`sh -c 'export ...; exec ...'` wrapper (with `printenv`), removes the resolved
`env` entries, and re-validates the JSON. Refuses to double-wrap a server that is
already wrapped.

## Verify & activate

- Validate JSON: `python3 -m json.tool <config>`
- Confirm resolution (masked, no secret printed):
  `sh -c 'export K=$(printenv <source>); echo "len=${#K}"'`
- Check the running server actually received it: `pgrep -af <server>` then
  `tr '\0' '\n' < /proc/<pid>/environ | grep <TARGET_VAR>`
- pi caches the MCP config at startup: **restart pi** (`bws run -- pi`) for changes
  to take effect.

## Notes

- One shared `mcp.json` works for both pi and opencode after this transform.
- To wire a bws var to a server env var that has **no** `{env:...}` entry, use
  `--source`/`--target` (the target is removed from `env` if present).
- If a source var is absent from the current env, `audit`/`fix` warn; it must be
  present at launch time (i.e. you actually launched via `bws run`).
- Requires `python3` (stdlib only) and `printenv` (coreutils/procps) on PATH.
