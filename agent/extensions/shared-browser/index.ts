import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { Type, type ImageContent, type TextContent } from "@earendil-works/pi-ai";
import { defineTool, truncateHead, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { browserRequest, type BrowserResult } from "./client.mjs";

const RAW_DENIAL = "Raw Chrome MCP calls are blocked by tab ownership. Use the browser tool: open a tab, then call a reviewed tool with your pageId. Use tools/describe to discover its arguments.";
const MODEL_ACTIONS = new Set(["open", "tabs", "call", "tools", "describe", "status"]);
const chromeServer = (name: unknown) => typeof name === "string" && name.replaceAll("_", "-") === "chrome-devtools";
const chromeTool = (name: unknown) => typeof name === "string" && /^(?:mcp__chrome_devtools(?:__|$)|chrome[-_]devtools_)/.test(name);

type ApprovalRequest = {
  serverName: string;
  supportsDenyReason?: boolean;
  claim(handler: () => "deny" | { decision: "deny"; reason: string }): boolean;
};

async function renderResult(result: BrowserResult, owner: string) {
  const content: (TextContent | ImageContent)[] = [];
  if (result.pageId !== undefined) content.push({ type: "text", text: `Owned pageId: ${result.pageId}` });
  const raw = result.mcp?.content || [{ type: "text", text: JSON.stringify(result, null, 2) }];
  const text = raw.filter((block) => block.type === "text").map((block) => block.text || "").join("\n");
  const bounded = truncateHead(text, { maxBytes: 48 * 1024, maxLines: 1800 });
  if (bounded.truncated) {
    const directory = await mkdtemp(path.join(tmpdir(), "pi-owned-browser-"));
    const file = path.join(directory, "result.txt");
    await writeFile(file, text, { mode: 0o600 });
    content.push({ type: "text", text: `${bounded.content}\n[Browser output truncated; full text: ${file}]` });
  } else if (text) {
    content.push({ type: "text", text });
  }
  for (const block of raw) {
    if (block.type === "image" && block.data && block.mimeType) {
      content.push({ type: "image", data: block.data, mimeType: block.mimeType });
    }
  }
  return {
    content,
    details: { owner, pageId: result.pageId, generation: result.generation },
    ...(result.mcp?.isError ? { isError: true } : {}),
  };
}

export default function sharedBrowser(pi: ExtensionAPI) {
  let unsubscribe: (() => void) | undefined;
  pi.on("session_start", () => {
    unsubscribe?.();
    // This broker gate also covers adapter paths that do not emit Pi tool_call.
    unsubscribe = pi.events.on("pi-mcp-adapter:tool-approval-request", (value) => {
      const request = value as ApprovalRequest;
      if (!chromeServer(request.serverName)) return;
      request.claim(() => request.supportsDenyReason ? { decision: "deny", reason: RAW_DENIAL } : "deny");
    });
  });
  pi.on("session_shutdown", () => {
    unsubscribe?.();
    unsubscribe = undefined;
    // The shared browser and its ownership registry outlive any one Pi runtime.
  });
  pi.on("tool_call", (event) => {
    const input = event.input as Record<string, unknown>;
    if (chromeTool(event.toolName) || (event.toolName === "mcp" && input.tool &&
      (chromeServer(input.server) || chromeTool(input.tool)))) {
      return { block: true, reason: RAW_DENIAL };
    }
  });

  pi.registerTool(defineTool({
    name: "browser",
    label: "Owned browser",
    description: "Chrome automation in the shared browser, with enforced per-Pi-session tab ownership. " +
      "open(url) creates your tab and returns pageId; tabs lists only your tabs. " +
      "tools(query?) lists reviewed Chrome tools; describe(tool) returns its input schema. " +
      "call(tool,args) executes an original Chrome tool name with an explicit args.pageId owned by this session. " +
      "status shows your owner identity. Raw chrome-devtools MCP calls are blocked. " +
      "Tabs/logins persist after Pi exits. Calls on foreign tabs and unreviewed browser-wide tools are denied.",
    promptSnippet: "Shared Chrome automation with session-owned tabs",
    promptGuidelines: [
      "Use browser for Chrome automation. Each Pi session and herd leaf opens its own tabs; pass the pageId returned by open/tabs on every call.",
    ],
    parameters: Type.Object({
      action: Type.Union([
        Type.Literal("open"), Type.Literal("tabs"), Type.Literal("call"),
        Type.Literal("tools"), Type.Literal("describe"), Type.Literal("status"),
      ]),
      url: Type.Optional(Type.String({ description: "URL for action=open; defaults to about:blank" })),
      tool: Type.Optional(Type.String({ description: "Original Chrome tool name for call/describe, e.g. take_snapshot" })),
      args: Type.Optional(Type.Record(Type.String(), Type.Unknown(), { description: "Chrome tool arguments; args.pageId is mandatory" })),
      query: Type.Optional(Type.String({ description: "Optional tools-list search" })),
    }, { additionalProperties: false }),
    async execute(_id, params, signal, _onUpdate, ctx) {
      if (!MODEL_ACTIONS.has(params.action)) throw new Error("Unsupported browser action.");
      const owner = ctx.sessionManager.getSessionId();
      // Pass only the supported fields; ownership and admin operations are not model inputs.
      const result = await browserRequest(owner, {
        action: params.action, url: params.url, tool: params.tool, args: params.args, query: params.query,
      }, { signal });
      return renderResult(result, owner);
    },
  }));

  pi.registerCommand("browser", {
    description: "Browser status/tabs, inspect, claim, handoff, or stop the shared browser (human confirmation)",
    async handler(arguments_, ctx) {
      const [action = "status", page, target] = arguments_.trim().split(/\s+/).filter(Boolean);
      const owner = ctx.sessionManager.getSessionId();
      try {
        let result: BrowserResult;
        if (action === "status" || action === "tabs") {
          result = await browserRequest(owner, { action });
        } else if (action === "stop") {
          if (!ctx.hasUI) throw new Error("Stopping the shared browser requires interactive human confirmation.");
          if (!await ctx.ui.confirm("Stop the shared automation browser?",
            "This closes Chrome and every session's tabs. Profile data stays on disk, but tab ownership resets. The next browser call starts it again.")) return;
          result = await browserRequest(owner, { action: "shutdown" });
        } else if (action === "inspect") {
          if (!ctx.hasUI) throw new Error("Inspect requires an interactive human session.");
          result = await browserRequest(owner, { action: "inspect" });
        } else if (action === "handoff" || action === "claim") {
          if (!ctx.hasUI) throw new Error("Ownership changes require an interactive human confirmation.");
          const pageId = Number(page);
          if (!Number.isSafeInteger(pageId) || pageId < 1) throw new Error("Supply a valid pageId.");
          if (action === "handoff" && !target) throw new Error("Usage: /browser handoff <pageId> <target-Pi-session-id>");
          const inspected = await browserRequest(owner, { action: "inspect" });
          const tab = inspected.pages?.find((entry) => entry.id === pageId);
          if (!tab) throw new Error("Tab no longer exists.");
          const destination = action === "claim" ? owner : target;
          if (!await ctx.ui.confirm("Transfer browser tab ownership?",
            `Tab ${pageId}: ${tab.url}\nFrom: ${tab.owner || "unowned"}\nTo: ${destination}\nThe previous owner will lose browser-tool access.`)) return;
          result = await browserRequest(owner, { action, pageId, targetOwner: target, expectedOwner: tab.owner });
        } else {
          throw new Error("Use /browser status | tabs | inspect | claim <pageId> | handoff <pageId> <Pi-session-id> | stop");
        }
        pi.sendMessage({ customType: "owned-browser", content: JSON.stringify(result, null, 2), display: true }, { triggerTurn: false });
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });
}
