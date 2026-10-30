"""MCP fixture at the external Chrome seam; mirrors 1.11's page-list shape."""
import json
import os
import sys
import time

pages = {1: {"id": 1, "url": "about:blank", "title": ""}}
selected = 1


def schema(name, scoped=False):
    return {
        "name": name, "description": f"Fixture {name}",
        "inputSchema": {"type": "object", "properties": {"pageId": {"type": "number"}} if scoped else {},
                        "required": ["pageId"] if scoped else []},
    }


def result(message=""):
    entries = [dict(p, selected=p["id"] == selected) for p in pages.values()]
    lines = [message, "## Pages"] + [
        f'{p["id"]}: {p["url"]}' + (" [selected]" if p["selected"] else "") for p in entries
    ]
    return {"content": [{"type": "text", "text": "\n".join(lines)}], "structuredContent": {"pages": entries}}


for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method, params = request["method"], request.get("params", {})
    if method == "initialize":
        answer = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fixture", "version": "1.11.0"}}
    elif method == "tools/list":
        answer = {"tools": [schema("new_page"), schema("list_pages"), schema("select_page"),
                            *[schema(n, True) for n in ("navigate_page", "take_snapshot", "close_page", "evaluate_script", "resize_page", "future_tool")]]}
    elif method == "tools/call":
        name, args = params["name"], params.get("arguments", {})
        if name == "new_page":
            selected = max(pages, default=0) + 1
            pages[selected] = {"id": selected, "url": args["url"], "title": ""}
            answer = result()
        elif name == "evaluate_script" and args.get("function") == "fixture:exit":
            os._exit(7)
        elif name == "list_pages":
            answer = result()
        elif name == "navigate_page":
            if args["url"].startswith("https://slow.invalid"):
                time.sleep(0.25)
            pages[args["pageId"]]["url"] = args["url"]
            answer = result("Navigated")
        elif name == "take_snapshot":
            answer = result("Snapshot of " + pages[args["pageId"]]["url"])
        elif name == "close_page":
            pages.pop(args["pageId"])
            answer = result("Closed")
        else:
            answer = result()
    else:
        answer = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": answer}), flush=True)
