"""Acceptance tests through the broker's public socket interface, without Chrome."""
import concurrent.futures
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def request(state, owner, timeout=10, **operation):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(timeout)
        connection.connect(str(Path(state) / "browser.sock"))
        connection.sendall(json.dumps({"owner": owner, **operation}).encode() + b"\n")
        with connection.makefile("rb") as response:
            return json.loads(response.readline())


class SharedBrowserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pi-browser-test-", dir="/tmp")
        self.state = Path(self.directory.name)
        self.log = (self.state / "test.log").open("w+")
        self.command = [sys.executable, str(ROOT / "broker.py"), "--state-dir", str(self.state),
                        "--command-json", json.dumps([sys.executable, str(ROOT / "tests/fake_chrome.py")])]
        self.process = subprocess.Popen(self.command, stdout=self.log, stderr=self.log)
        self.addCleanup(self.cleanup)
        for _ in range(100):
            if self.process.poll() is not None:
                self.log.seek(0)
                self.fail("Broker exited: " + self.log.read())
            try:
                request(self.state, "test", action="status")
                break
            except (OSError, ValueError):
                time.sleep(0.02)
        else:
            self.fail("Broker did not start")

    def cleanup(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.log.close()
        self.directory.cleanup()

    def call(self, owner, **operation):
        response = request(self.state, owner, **operation)
        self.assertTrue(response["ok"], response)
        return response["result"]

    def test_concurrent_sessions_get_distinct_owned_tabs(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            a = pool.submit(self.call, "session-a", action="open", url="https://alpha.invalid")
            b = pool.submit(self.call, "leaf-b", action="open", url="https://beta.invalid")
            a, b = a.result()["pageId"], b.result()["pageId"]
        self.assertNotEqual(a, b)
        denied = request(self.state, "leaf-b", action="call", tool="navigate_page",
                         args={"pageId": a, "url": "https://wrong.invalid"})
        self.assertFalse(denied["ok"], denied)
        self.assertEqual(denied["error"]["code"], "ownership_denied")
        tabs = self.call("session-a", action="tabs")["pages"]
        self.assertEqual([(p["id"], p["url"]) for p in tabs], [(a, "https://alpha.invalid")])

    def test_explicit_handoff_revokes_previous_owner(self):
        page = self.call("session-a", action="open", url="https://alpha.invalid")["pageId"]
        self.call("session-a", action="handoff", pageId=page, targetOwner="leaf-b")
        denied = request(self.state, "session-a", action="call", tool="take_snapshot", args={"pageId": page})
        self.assertEqual(denied["error"]["code"], "ownership_denied")
        granted = self.call("leaf-b", action="call", tool="take_snapshot", args={"pageId": page})
        self.assertIn("alpha.invalid", json.dumps(granted))

    def test_explicit_human_claim_checks_the_owner_has_not_changed(self):
        page = self.call("session-a", action="open", url="https://alpha.invalid")["pageId"]
        inspected = self.call("session-b", action="inspect")["pages"]
        self.assertEqual(next(p for p in inspected if p["id"] == page)["owner"], "session-a")
        self.call("session-a", action="handoff", pageId=page, targetOwner="session-c")
        stale = request(self.state, "session-b", action="claim", pageId=page, expectedOwner="session-a")
        self.assertEqual(stale["error"]["code"], "ownership_changed")
        self.call("session-b", action="claim", pageId=page, expectedOwner="session-c")
        self.assertEqual(self.call("session-b", action="tabs")["pages"][0]["id"], page)

    def test_foreign_reads_missing_ids_and_unreviewed_tools_fail_closed(self):
        a = self.call("session-a", action="open", url="https://alpha.invalid")["pageId"]
        b = self.call("leaf-b", action="open", url="https://beta.invalid")["pageId"]
        for tool, args, expected in (
            ("take_snapshot", {"pageId": a}, "ownership_denied"),
            ("take_snapshot", {}, "page_id_required"),
            ("take_snapshot", {"pageId": True}, "page_id_required"),
            ("resize_page", {"pageId": b}, "unsupported_tool"),
            ("future_tool", {"pageId": b}, "unsupported_tool"),
            ("new_page", {"url": "about:blank"}, "unsupported_tool"),
            ("evaluate_script", {"pageId": b, "serviceWorkerId": "foreign"}, "unsupported_target"),
        ):
            with self.subTest(tool=tool, args=args):
                result = request(self.state, "leaf-b", action="call", tool=tool, args=args)
                self.assertEqual(result["error"]["code"], expected)
        snapshot = self.call("leaf-b", action="call", tool="take_snapshot", args={"pageId": b})
        self.assertIn("beta.invalid", json.dumps(snapshot))
        self.assertNotIn("alpha.invalid", json.dumps(snapshot))

    def test_backend_restart_cannot_transfer_recycled_page_ids(self):
        old = self.call("session-a", action="open", url="https://alpha.invalid")
        lost = request(self.state, "session-a", action="call", tool="evaluate_script",
                       args={"pageId": old["pageId"], "function": "fixture:exit"})
        self.assertEqual(lost["error"]["code"], "backend_lost")
        new = self.call("leaf-b", action="open", url="https://beta.invalid")
        self.assertEqual(old["pageId"], new["pageId"], "Fixture deliberately recycles IDs after a process restart")
        self.assertNotEqual(old["generation"], new["generation"])
        denied = request(self.state, "session-a", action="call", tool="take_snapshot", args={"pageId": old["pageId"]})
        self.assertEqual(denied["error"]["code"], "ownership_denied")

    def test_failed_backend_start_can_be_retried_without_restarting_broker(self):
        self.process.terminate()
        self.process.wait(timeout=5)
        executable = self.state / "upstream"
        self.command[-1] = json.dumps([str(executable)])
        self.process = subprocess.Popen(self.command, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                request(self.state, "test", action="status")
                break
            except OSError:
                time.sleep(0.02)
        for _ in range(2):
            failed = request(self.state, "session-a", action="tools")
            self.assertEqual(failed["error"]["code"], "backend_start_failed")
        executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests/fake_chrome.py"}"\n')
        executable.chmod(0o700)
        self.assertIn("pageId", self.call("session-a", action="open", url="about:blank"))

    def test_explicit_shutdown_releases_broker_and_resets_ownership(self):
        previous = self.call("session-a", action="open", url="https://alpha.invalid")
        self.call("human", action="shutdown")
        self.process.wait(timeout=5)
        self.assertFalse((self.state / "browser.sock").exists())
        self.process = subprocess.Popen(self.command, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                request(self.state, "test", action="status")
                break
            except OSError:
                time.sleep(0.02)
        current = self.call("leaf-b", action="open", url="https://beta.invalid")
        self.assertNotEqual(previous["generation"], current["generation"])
        denied = request(self.state, "session-a", action="call", tool="take_snapshot", args={"pageId": previous["pageId"]})
        self.assertEqual(denied["error"]["code"], "ownership_denied")


if __name__ == "__main__":
    unittest.main()
