#!/usr/bin/env python3
"""Opt-in real-Chrome acceptance test; --baseline demonstrates the old collision.

Uses a temporary automation profile, never an existing browser or website login.
CHROME_MCP_COMMAND may contain a JSON argv to an already installed MCP executable.
"""

import argparse
import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


class StdioMcp:
    """Independent test driver for the public MCP stdio protocol."""

    def __init__(self, command):
        self.stderr = tempfile.TemporaryFile(mode="w+")
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, text=True, start_new_session=True,
        )
        self.messages = queue.Queue()
        self.sequence = 0
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "pi-browser-acceptance", "version": "1"},
        })
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        finally:
            self.messages.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params, timeout=40):
        self.sequence += 1
        request_id = self.sequence
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if message is None:
                self.stderr.seek(0)
                raise RuntimeError("MCP exited: " + self.stderr.read()[-2000:])
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
        self.process.stdout.close()
        self.stderr.close()


def chrome_command(profile):
    command = json.loads(os.environ.get(
        "CHROME_MCP_COMMAND", '["npx", "--yes", "chrome-devtools-mcp@1.11.0"]',
    ))
    return command + [
        "--headless", f"--user-data-dir={profile}", "--page-id-routing",
        "--no-usage-statistics", "--no-performance-crux", "--experimental-structured-content",
    ]


def baseline():
    clients = []
    with tempfile.TemporaryDirectory(prefix="pi-browser-baseline-") as directory:
        try:
            command = chrome_command(Path(directory) / "profile")
            clients.append(StdioMcp(command))
            first = clients[0].call("new_page", {"url": "about:blank"})
            assert not first.get("isError"), first
            print("Session A: opened a tab", flush=True)
            clients.append(StdioMcp(command))
            second = clients[1].call("new_page", {"url": "about:blank"})
            if second.get("isError"):
                message = "\n".join(block.get("text", "") for block in second.get("content", []))
                raise AssertionError("Session B cannot use the same browser profile: " + message)
            print("Session B: opened a tab", flush=True)
        finally:
            for client in reversed(clients):
                client.close()


def guarded():
    from test_broker import request, ROOT

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            title = self.path.strip("/") or "home"
            body = f"<title>{title}</title><h1>{title}</h1><button>Click me</button>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    site = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{site.server_port}"
    with tempfile.TemporaryDirectory(prefix="pi-browser-live-", dir="/tmp") as directory:
        state = Path(directory)
        log = (state / "smoke.log").open("w+")
        process = subprocess.Popen([
            sys.executable, str(ROOT / "broker.py"), "--state-dir", directory,
            "--command-json", json.dumps(chrome_command(state / "profile")),
        ], stdout=log, stderr=log)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                try:
                    request(state, "session-a", action="status")
                    break
                except OSError:
                    time.sleep(0.03)

            def call(owner, **operation):
                response = request(state, owner, timeout=120, **operation)
                assert response["ok"], response
                result = response["result"]
                assert not result.get("mcp", {}).get("isError"), result
                return result

            with concurrent.futures.ThreadPoolExecutor() as pool:
                a = pool.submit(call, "session-a", action="open", url=base + "/alpha")
                b = pool.submit(call, "leaf-b", action="open", url=base + "/beta")
                a, b = a.result()["pageId"], b.result()["pageId"]
            assert a != b
            print(f"PASS: concurrent callers share Chrome with distinct tabs ({a}, {b})", flush=True)
            call("session-a", action="call", tool="evaluate_script", args={
                "pageId": a, "function": "() => { document.cookie = 'pi_shared=ok; path=/'; return document.title; }",
            })
            cookie = call("leaf-b", action="call", tool="evaluate_script", args={"pageId": b, "function": "() => document.cookie"})
            assert "pi_shared=ok" in json.dumps(cookie), cookie
            print("PASS: tabs share website login/cookie context", flush=True)
            call("session-a", action="call", tool="navigate_page", args={"pageId": a, "url": base + "/alpha-next"})
            own = call("leaf-b", action="call", tool="take_snapshot", args={"pageId": b})
            assert "beta" in json.dumps(own) and "alpha-next" not in json.dumps(own), own
            print("PASS: one caller's navigation does not change the other's tab or listing", flush=True)
            for tool in ("take_snapshot", "navigate_page", "close_page"):
                denied = request(state, "leaf-b", action="call", tool=tool, args={"pageId": a, "url": base + "/wrong"})
                assert not denied["ok"] and denied["error"]["code"] == "ownership_denied", denied
            unchanged = call("session-a", action="call", tool="take_snapshot", args={"pageId": a})
            assert "alpha-next" in json.dumps(unchanged), unchanged
            print("PASS: foreign-tab read/navigation/close rejected without changing the owner tab", flush=True)
            image = call("session-a", action="call", tool="take_screenshot", args={"pageId": a})
            assert any(block.get("type") == "image" for block in image["mcp"]["content"])
            print("PASS: screenshot image blocks pass through the guard", flush=True)
        finally:
            process.terminate()
            process.wait(timeout=10)
            log.close()
            site.shutdown()
            site.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true")
    options = parser.parse_args()
    baseline() if options.baseline else guarded()
