# pi-config

My [pi](https://github.com/earendil-works/pi-coding-agent) configuration — portable, shareable config only. No secrets, no machine state.

## What's here

- `agent/APPEND_SYSTEM.md` — appended to the system prompt.
- `agent/settings.json` — pi settings (provider, model, theme, packages).
- `agent/extensions/` — custom extensions (e.g. `destructive-gate.ts`).
- `agent/skills/` — custom skills (`grill-me`, `herd`, `mcp-bws-secrets`, `prd`, `teach`, `writing-great-skills`).
- `mcp/mcp.json` — MCP server config (installed via symlink, see below).

## What's intentionally NOT here

- `agent/auth.json` — **secret**. Holds API keys. pi regenerates it when you authenticate, so it is gitignored and never committed.
- `agent/sessions/`, `agent/bin/`, `agent/*.cache.json`, `agent/npm/`, `agent/models-store.json` — machine state / caches / downloaded tooling, all gitignored.

## Install

This repo is meant to live at `~/.pi`:

```sh
git clone git@github.com:joselitz123/pi-config.git ~/.pi
```

Then authenticate pi so it regenerates `agent/auth.json` (follow pi's auth flow).

### MCP config

`mcp/mcp.json` is the real file; the live location is a symlink to it. To wire it up on a new machine:

```sh
mkdir -p ~/.config/mcp
ln -s "$HOME/.pi/mcp/mcp.json" "$HOME/.config/mcp/mcp.json"
```

## Security

This repo uses a **default-deny `.gitignore`**: everything is ignored unless explicitly whitelisted, so secrets and state cannot be committed by accident. Still — never paste a real key into a tracked file.

## License

MIT — see [LICENSE](LICENSE).
