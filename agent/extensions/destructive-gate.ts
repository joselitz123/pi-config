/**
 * Destructive Gate Extension (global)
 *
 * Policy (posture: minimalist, git as the safety net):
 *   - bash:  ask before commands matching a catastrophic pattern list below
 *   - write/edit: ask before any path inside .git/ (protect the safety net itself)
 *   - read / MCP tools / user_bash: untouched
 *
 * Behavior:
 *   - Interactive (TUI/RPC, ctx.hasUI): confirm yes/no, EVERY time (nothing remembered)
 *   - No-UI (print `-p`, `--mode json`): block with a reason (can't ask -> don't proceed)
 *   - Deny: return { block: true, reason } so the agent is told why
 *
 * Deliberately NOT gated: plain `rm file` (no -r), `mv`, `>` truncation, installs,
 * `curl|sh`, writes to normal project files (git-recoverable or out of scope).
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Each rule: a regex tested against the full bash command string, plus a label
// shown to the user in the confirm prompt.
const BASH_RULES: { pattern: RegExp; label: string }[] = [
  // --- Destructive git (the hole in the git safety net) ---
  { label: "git reset --hard", pattern: /\bgit\s+reset\b[^|;&\n]*--hard\b/i },
  { label: "git clean --force", pattern: /\bgit\s+clean\b[^|;&\n]*(-[A-Za-z]*f[A-Za-z]*|--force\b)/i },
  { label: "git checkout/restore . (discard working tree)", pattern: /\bgit\s+(checkout|restore)\s+(-[A-Za-z]+\s+)*(--\s+)?\./i },
  { label: "git push --force", pattern: /\bgit\s+push\b[^|;&\n]*(--force|\s-f(?=\s|$))/i },
  // case-sensitive on purpose: -D force-deletes, -d only deletes merged branches
  { label: "git branch --delete --force (-D)", pattern: /\bgit\s+branch\b[^|;&\n]*\s-[A-Za-z]*D[A-Za-z]*(?=\s|$)/ },
  { label: "git stash drop/clear", pattern: /\bgit\s+stash\s+(drop|clear)\b/i },

  // --- Catastrophic system ops ---
  { label: "rm recursive (rm -r/-R/-rf/...)", pattern: /\brm\b[^|;&\n]*(-[A-Za-z]*[rR][A-Za-z]*|--recursive\b)/i },
  { label: "privilege escalation (sudo/su/doas)", pattern: /\b(sudo|su|doas)\b/i },
  { label: "filesystem format (mkfs/fdisk/parted)", pattern: /\b(mkfs[\w.]*|fdisk|parted)\b/i },
  { label: "raw dd to a device", pattern: /\bdd\b[^|;&\n]*\bof\s*=\s*\/dev\//i },
  { label: "redirect to raw disk (>/dev/sd*)", pattern: />+\s*\/dev\/(sd|nvme|hd|vd|disk|loop)/i },
  { label: "fork bomb", pattern: /:\s*\(\s*\)\s*\{/i },
  { label: "chmod -R 777", pattern: /\bchmod\b[^|;&\n]*-R\b[^|;&\n]*\b777\b/i },
  { label: "chown -R", pattern: /\bchown\b[^|;&\n]*-R\b/i },
];

// Matches `.git`, `.git/`, `.git/config`, `foo/.git/refs`, etc.
// Does NOT match bare repos named like `repo.git` (no leading path separator).
const GIT_PATH = /(^|\/)\.git(\/|$)/;

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    // --- bash: catastrophic command patterns ---
    if (event.toolName === "bash") {
      const command = ((event.input?.command as string | undefined) ?? "").trim();
      if (!command) return;

      const hit = BASH_RULES.find((r) => r.pattern.test(command));
      if (!hit) return;

      if (!ctx.hasUI) {
        return {
          block: true,
          reason: `Destructive command blocked (no UI to confirm) — ${hit.label}\n> ${command}`,
        };
      }

      const allow = await ctx.ui.confirm(
        "⚠️  Destructive command",
        `Matched rule: ${hit.label}\n\n${command}\n\nAllow this command?`,
      );
      if (!allow) {
        return { block: true, reason: `Blocked by user — ${hit.label}\n> ${command}` };
      }
      return;
    }

    // --- write / edit: protect .git/ ---
    if (event.toolName === "write" || event.toolName === "edit") {
      const path = ((event.input?.path as string | undefined) ?? "").trim();
      if (!path || !GIT_PATH.test(path)) return;

      if (!ctx.hasUI) {
        return {
          block: true,
          reason: `Write inside .git blocked (no UI to confirm) — ${path}`,
        };
      }

      const allow = await ctx.ui.confirm(
        "⚠️  Write inside .git",
        `Modifying git internals can corrupt your repository (your safety net).\n\n${path}\n\nAllow?`,
      );
      if (!allow) {
        return { block: true, reason: `Blocked by user — write to .git refused: ${path}` };
      }
      return;
    }
  });
}
