#!/usr/bin/env python3
"""One local Chrome MCP process, with session-owned tabs (macOS/Linux).

The private Unix socket is a coordination interface, not a sandbox against
other processes running as the same OS user. No website data is logged here.
"""

import argparse
import asyncio
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import uuid

VERSION = 1
MAX_MESSAGE = 32 * 1024 * 1024
# Reviewed page-targeted operations. New or browser-wide tools fail closed.
PAGE_TOOLS = frozenset({
    "click", "click_at", "drag", "fill", "fill_form", "hover", "press_key",
    "type_text", "upload_file", "navigate_page", "handle_dialog", "close_page",
    "take_snapshot", "take_screenshot", "evaluate_script", "wait_for",
    "list_network_requests", "get_network_request", "list_console_messages",
    "get_console_message", "get_css_styles",
})


class BrowserError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ChromeMcp:
    def __init__(self, command, directory):
        self.command = command
        self.directory = directory
        self.process = None
        self.sequence = 0

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.directory, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_MESSAGE, start_new_session=True,
            env={**os.environ, "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1"},
        )
        initialized = await self.rpc("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "pi-shared-browser", "version": str(VERSION)},
        })
        if initialized.get("serverInfo", {}).get("version") != "1.11.0":
            raise BrowserError("incompatible_backend", "This guard is reviewed for Chrome DevTools MCP 1.11.0.")
        await self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = []
        cursor = None
        while True:
            page = await self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(page.get("tools", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return {tool["name"]: tool for tool in tools}

    async def send(self, value):
        self.process.stdin.write(json.dumps(value).encode() + b"\n")
        await self.process.stdin.drain()

    async def rpc(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        try:
            async with asyncio.timeout(120):
                await self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        raise BrowserError("backend_lost", "Chrome MCP disconnected; the call's outcome may be unknown. Inspect your tabs before retrying.")
                    message = json.loads(line)
                    if "method" in message:
                        if "id" in message:
                            result = {"roots": []} if message["method"] == "roots/list" else None
                            response = {"jsonrpc": "2.0", "id": message["id"]}
                            response.update({"result": result} if result is not None else {
                                "error": {"code": -32601, "message": "Unsupported client method"},
                            })
                            await self.send(response)
                        continue
                    if message.get("id") != request_id:
                        continue
                    if "error" in message:
                        raise BrowserError("backend_error", str(message["error"].get("message", "MCP request failed")))
                    return message["result"]
        except TimeoutError as error:
            raise BrowserError("backend_timeout", "Chrome MCP timed out; the call may have run. Inspect your tabs before retrying.") from error
        except (BrokenPipeError, ConnectionError, ValueError, KeyError) as error:
            raise BrowserError("backend_lost", "Chrome MCP disconnected or returned an invalid response. Inspect your tabs before retrying.") from error

    async def call(self, name, args):
        return await self.rpc("tools/call", {"name": name, "arguments": args})

    async def close(self):
        if not self.process:
            return
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 3)
        except TimeoutError:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()


class SharedBrowser:
    def __init__(self, command, directory):
        self.command, self.directory = command, directory
        self.lock = asyncio.Lock()
        self.backend = None
        self.tools = {}
        self.owners = {}
        self.generation = str(uuid.uuid4())

    async def ensure_backend(self):
        if self.backend and self.backend.process and self.backend.process.returncode is None:
            return
        await self.reset()
        self.backend = ChromeMcp(self.command, self.directory)
        try:
            self.tools = await self.backend.start()
        except BrowserError:
            await self.reset()
            raise
        except OSError as error:
            await self.reset()
            raise BrowserError("backend_start_failed", "Could not start Chrome MCP. Ensure Node/npm are available; the next browser call can retry startup.") from error

    async def reset(self):
        if self.backend:
            await self.backend.close()
        self.backend = None
        self.tools = {}
        self.owners.clear()
        self.generation = str(uuid.uuid4())

    async def pages(self):
        result = await self.backend.call("list_pages", {})
        if result.get("isError"):
            raise BrowserError("backend_error", self.error_text(result))
        data = result.get("structuredContent", {})
        if data.get("reconnected"):
            self.owners.clear()
            self.generation = str(uuid.uuid4())
        pages = data.get("pages")
        if not isinstance(pages, list) or any(type(p.get("id")) is not int for p in pages):
            raise BrowserError("incompatible_backend", "Chrome returned an unrecognized page list; ownership was not assumed.")
        live = {p["id"] for p in pages}
        self.owners = {page: owner for page, owner in self.owners.items() if page in live}
        return pages

    @staticmethod
    def error_text(result):
        return "\n".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")

    def owned_pages(self, owner, pages):
        return [dict(page, selected=False) for page in pages if self.owners.get(page["id"]) == owner]

    def filtered_result(self, owner, result):
        result = copy.deepcopy(result)
        data = result.get("structuredContent", {})
        for key in ("pages", "openedPages", "extensionPages"):
            if key in data:
                data[key] = self.owned_pages(owner, data[key])
        data.pop("extensionServiceWorkers", None)
        for block in result.get("content", []):
            if block.get("type") != "text":
                continue
            # Chrome appends browser-wide listings to many otherwise page-scoped results.
            block["text"] = re.sub(
                r"^## (?:Pages|Opened pages|Extension Pages|Extension Service Workers)\n.*?(?=^## |\Z)",
                "", block["text"], flags=re.MULTILINE | re.DOTALL,
            ).strip()
        return result

    async def request(self, operation, disconnected=lambda: False):
        owner = operation.get("owner")
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owner):
            raise BrowserError("invalid_request", "A valid Pi session identity is required.")
        async with self.lock:
            if disconnected():
                raise BrowserError("cancelled", "Caller disconnected before dispatch.")
            action = operation.get("action")
            if action == "shutdown":
                return {"stopped": True, "message": "Shared browser stopping; tab ownership is reset, profile data stays on disk."}
            if action == "status":
                return {"version": VERSION, "pid": os.getpid(), "generation": self.generation,
                        "owner": owner, "running": bool(self.backend and self.backend.process.returncode is None),
                        "ownedTabCount": sum(o == owner for o in self.owners.values())}
            try:
                await self.ensure_backend()
                if action == "tools":
                    query = operation.get("query") or ""
                    if not isinstance(query, str):
                        raise BrowserError("invalid_request", "query must be a string.")
                    return {"tools": [{"name": name, "description": tool.get("description", "").split("\n")[0],
                                       "parameters": list(tool.get("inputSchema", {}).get("properties", {}))}
                                      for name, tool in self.tools.items()
                                      if name in PAGE_TOOLS and query.lower() in (name + " " + tool.get("description", "")).lower()]}
                if action == "describe":
                    name = operation.get("tool")
                    if name not in PAGE_TOOLS or name not in self.tools:
                        raise BrowserError("unsupported_tool", "That tool is not enabled by the ownership guard.")
                    return self.tools[name]
                pages = await self.pages()
                if action == "tabs":
                    return {"pages": self.owned_pages(owner, pages), "generation": self.generation}
                if action == "inspect":
                    return {"pages": [dict(p, owner=self.owners.get(p["id"])) for p in pages], "generation": self.generation}
                if action == "claim":
                    page_id = operation.get("pageId")
                    if type(page_id) is not int or page_id not in {p["id"] for p in pages}:
                        raise BrowserError("invalid_request", "Tab no longer exists.")
                    if "expectedOwner" not in operation or self.owners.get(page_id) != operation["expectedOwner"]:
                        raise BrowserError("ownership_changed", "Ownership changed while awaiting confirmation. Inspect and confirm again.")
                    self.owners[page_id] = owner
                    return {"pageId": page_id, "owner": owner, "generation": self.generation}
                if action == "handoff":
                    page_id, target = operation.get("pageId"), operation.get("targetOwner")
                    if type(page_id) is not int or self.owners.get(page_id) != owner:
                        raise BrowserError("ownership_denied", "Only the current owner can hand off this tab.")
                    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", target):
                        raise BrowserError("invalid_request", "A valid target Pi session identity is required.")
                    self.owners[page_id] = target
                    return {"pageId": page_id, "owner": target, "generation": self.generation}
                if action == "open":
                    url = operation.get("url", "about:blank")
                    if not isinstance(url, str):
                        raise BrowserError("invalid_request", "url must be a string.")
                    created = await self.backend.call("new_page", {"url": "about:blank", "background": True})
                    if created.get("isError"):
                        raise BrowserError("backend_error", self.error_text(created))
                    candidates = [p for p in created.get("structuredContent", {}).get("pages", [])
                                  if p.get("selected") and p.get("id") not in {p["id"] for p in pages}]
                    if len(candidates) != 1 or type(candidates[0].get("id")) is not int:
                        raise BrowserError("unclaimed_tab", "The new tab could not be identified safely. No ownership was assigned.")
                    page_id = candidates[0]["id"]
                    self.owners[page_id] = owner
                    response = created if url == "about:blank" else await self.backend.call("navigate_page", {"pageId": page_id, "url": url})
                    return {"pageId": page_id, "generation": self.generation, "mcp": self.filtered_result(owner, response)}
                if action != "call":
                    raise BrowserError("invalid_request", "Unknown browser action.")
                name, args = operation.get("tool"), operation.get("args", {})
                if name not in PAGE_TOOLS or name not in self.tools:
                    raise BrowserError("unsupported_tool", "Tool not enabled. Use open/tabs for tab management; browser-wide and unreviewed tools are blocked.")
                if not isinstance(args, dict) or type(args.get("pageId")) is not int or args["pageId"] < 1:
                    raise BrowserError("page_id_required", "Supply an explicit numeric pageId from your owned tabs.")
                page_id = args["pageId"]
                if self.owners.get(page_id) != owner:
                    raise BrowserError("ownership_denied", "That tab is not owned by this Pi session. Open your own tab or request an explicit handoff.")
                schema = self.tools[name].get("inputSchema", {})
                if "pageId" not in schema.get("required", []):
                    raise BrowserError("incompatible_backend", "The tool no longer requires pageId; dispatch was refused.")
                if "serviceWorkerId" in args or "isolatedContext" in args:
                    raise BrowserError("unsupported_target", "Only the owned page may be targeted.")
                response = await self.backend.call(name, args)
                if name == "close_page" and not response.get("isError"):
                    await self.pages()
                return {"pageId": page_id, "generation": self.generation, "mcp": self.filtered_result(owner, response)}
            except BrowserError as error:
                if error.code in {"backend_lost", "backend_timeout", "incompatible_backend"}:
                    await self.reset()
                raise


async def serve(arguments):
    directory = Path(arguments.state_dir).expanduser()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise RuntimeError("Browser state must be a directory owned by the current user.")
    directory.chmod(0o700)
    with (directory / "broker.lock").open("a+") as lock:
        os.chmod(directory / "broker.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # A concurrent first-use launcher already owns the broker.
        socket_path = directory / "browser.sock"
        if socket_path.exists():
            if not stat.S_ISSOCK(socket_path.lstat().st_mode):
                raise RuntimeError("Refusing to replace a non-socket browser.sock.")
            socket_path.unlink()
        command = json.loads(arguments.command_json) if arguments.command_json else [
            "npx", "--yes", "chrome-devtools-mcp@1.11.0", "--page-id-routing",
            "--no-usage-statistics", "--no-performance-crux", "--no-category-extensions",
            "--experimental-structured-content",
            f"--user-data-dir={directory / 'profile'}",
        ]
        browser = SharedBrowser(command, str(directory))
        stopped = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)

        async def handle(reader, writer):
            try:
                async with asyncio.timeout(10):
                    line = await reader.readline()
                operation = json.loads(line)
                result = await browser.request(operation, reader.at_eof)
                response = {"ok": True, "result": result}
            except BrowserError as error:
                response = {"ok": False, "error": {"code": error.code, "message": str(error)}}
            except (ValueError, TypeError, AttributeError, TimeoutError):
                response = {"ok": False, "error": {"code": "invalid_request", "message": "Invalid browser request."}}
            except Exception:
                response = {"ok": False, "error": {"code": "broker_error", "message": "Browser broker failed. The call may have run; inspect your tabs before retrying."}}
            try:
                writer.write(json.dumps(response).encode() + b"\n")
                await writer.drain()
                if response["ok"] and operation.get("action") == "shutdown":
                    stopped.set()
            except (BrokenPipeError, ConnectionError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionError):
                    pass

        server = await asyncio.start_unix_server(handle, path=str(socket_path), limit=MAX_MESSAGE)
        socket_path.chmod(0o600)
        try:
            async with server:
                await stopped.wait()
        finally:
            await browser.reset()
            socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=str(Path.home() / ".cache/pi-shared-browser"))
    parser.add_argument("--command-json", help="Trusted upstream argv override, primarily for acceptance tests")
    asyncio.run(serve(parser.parse_args()))
