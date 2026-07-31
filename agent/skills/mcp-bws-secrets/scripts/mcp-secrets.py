#!/usr/bin/env python3
"""Audit and fix MCP server credential resolution for pi + `bws run`.

Problem
-------
`bws run -- pi` injects Bitwarden secrets as environment variables, named after
each secret's Bitwarden "key" field (e.g. `opencode-tavily_api_key_1`). MCP configs
commonly map these into a server's `env` block with opencode-style `{env:VAR}`
placeholders:

    "env": { "TAVILY_API_KEY": "{env:opencode-tavily_api_key_1}" }

pi does NOT resolve `{env:VAR}` (nor `$VAR`/`${VAR}`/`!cmd`) inside an MCP server's
`env` block - values are passed through verbatim, so the server receives the literal
string and fails authentication.

Fix
---
Move the resolution into the server's `command` by wrapping it in a shell that
exports each target var from the inherited bws var via `printenv`, then execs the
original command. `printenv` (not `$VAR`) is used so source names with hyphens work.
The resolved `env` entries are removed.

Subcommands
-----------
  audit   report unresolved {env:...} refs and whether source vars are present
  fix     wrap a server's command to resolve bws secrets via printenv
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

DEFAULT_CONFIG = "~/.config/mcp/mcp.json"
ENV_REF_RE = re.compile(r"^\s*\{env:([^}]+)\}\s*$")


def load_config(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def save_config(path: Path, data: dict) -> None:
    with path.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def shell_join(parts):
    try:
        return shlex.join(parts)
    except AttributeError:  # py < 3.8
        return " ".join(shlex.quote(p) for p in parts)


def find_env_refs(server: dict) -> list[tuple[str, str]]:
    """Return [(target_key, source_var)] for {env:VAR} entries in server.env."""
    out = []
    for k, v in (server.get("env") or {}).items():
        if isinstance(v, str):
            m = ENV_REF_RE.match(v)
            if m:
                out.append((k, m.group(1).strip()))
    return out


def is_wrapped(server: dict) -> bool:
    return server.get("command") == "sh" and any(
        a == "-c" for a in (server.get("args") or [])
    )


def cmd_audit(args) -> int:
    path = Path(args.config).expanduser()
    if not path.exists():
        print(f"config not found: {path}", file=sys.stderr)
        return 2
    data = load_config(path)
    servers = data.get("mcpServers", {})
    print(f"# MCP secret audit - {path}")
    if not servers:
        print("(no servers defined)")
        return 0

    issues = 0
    for name, server in servers.items():
        wrapped = is_wrapped(server)
        refs = find_env_refs(server)
        tag = " [already wrapped]" if wrapped else ""
        print(f"\n## {name}{tag}")
        cmd_line = shell_join([server.get("command", "")] + list(server.get("args") or []))
        print(f"  command: {cmd_line}")
        env = server.get("env") or {}
        if env:
            print("  env:")
            for k, v in env.items():
                print(f"    {k} = {v}")
        if refs:
            issues += 1
            print("  unresolved {env:...} refs (pi will NOT resolve these):")
            for target, source in refs:
                status = "present" if source in os.environ else "ABSENT in current env"
                print(f"    {target} <- {source}  [{status}]")
        elif wrapped:
            print("  ok (wrapped command resolves secrets at spawn)")
        elif not env:
            print("  ok (no env block; inherits parent env)")
        else:
            print("  ok (env has no {env:...} placeholders)")

    print()
    if issues:
        print(f"RESULT: {issues} server(s) with unresolved {{env:...}} refs. Run `fix`.")
        return 1
    print("RESULT: no {env:...} issues found.")
    return 0


def cmd_fix(args) -> int:
    path = Path(args.config).expanduser()
    if not path.exists():
        print(f"config not found: {path}", file=sys.stderr)
        return 2
    data = load_config(path)
    servers = data.get("mcpServers", {})
    if args.server not in servers:
        known = ", ".join(servers) or "(none)"
        print(f"server not found: {args.server!r}. Known: {known}", file=sys.stderr)
        return 2

    server = servers[args.server]
    if is_wrapped(server):
        print(f"{args.server!r} is already wrapped; not modifying.", file=sys.stderr)
        return 2

    mappings = find_env_refs(server)  # [(target, source)]
    if args.source and args.target:
        if (args.target, args.source) not in mappings:
            mappings.append((args.target, args.source))
    if not mappings:
        print(
            f"no {{env:...}} refs found for {args.server!r}, and no --source/--target given.",
            file=sys.stderr,
        )
        return 2

    cmd = server.get("command")
    if not cmd:
        print("server has no 'command'", file=sys.stderr)
        return 2
    original = shell_join([cmd] + list(server.get("args") or []))

    exports = []
    warnings = []
    for target, source in mappings:
        if source not in os.environ:
            warnings.append(
                f"WARNING: {source} not in current env "
                "(fix will only work if it is injected at launch via `bws run`)"
            )
        exports.append(f'export {target}="$(printenv {source})"')
    for w in warnings:
        print(w, file=sys.stderr)

    wrapper = "; ".join(exports) + f"; exec {original}"

    new_server = dict(server)
    new_server["command"] = "sh"
    new_server["args"] = ["-c", wrapper]

    env = dict(server.get("env") or {})
    for target, _ in mappings:
        env.pop(target, None)
    if env:
        new_server["env"] = env
    else:
        new_server.pop("env", None)
    servers[args.server] = new_server

    if args.dry_run:
        print(f"# DRY RUN - new {args.server} entry:")
        print(json.dumps({args.server: new_server}, indent=2))
        return 0

    backup = path.with_suffix(path.suffix + ".bak")
    if backup.exists():
        backup = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    backup.write_text(path.read_text())
    save_config(path, data)
    load_config(path)  # re-validate written JSON

    print(f"updated {path}")
    print(f"backup  {backup}")
    print(f"\nnew command for {args.server!r}:")
    print(f"  sh -c '{wrapper}'")
    print("\nRestart pi (`bws run -- pi`) for the change to take effect.")
    return 0


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG, help=f"MCP config (default {DEFAULT_CONFIG})"
    )

    ap = argparse.ArgumentParser(
        description="Audit/fix MCP server secrets for pi + bws run",
        epilog="Put -c/--config after the subcommand, e.g. `mcp-secrets.py audit -c FILE`.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", parents=[common], help="report unresolved {env:...} refs")
    a.set_defaults(func=cmd_audit)

    f = sub.add_parser("fix", parents=[common], help="wrap a server's command to resolve bws secrets")
    f.add_argument("server", help="server name in mcpServers")
    f.add_argument("--source", help="bws source env var (auto-detected from {env:..} if omitted)")
    f.add_argument("--target", help="env var the server expects (auto-detected if omitted)")
    f.add_argument("--dry-run", action="store_true", help="show the result without writing")
    f.set_defaults(func=cmd_fix)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
