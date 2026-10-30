"""Deterministic collection tests: real commands, fake herdr, no model calls.

Run: python3 -m unittest discover -s agent/skills/herd/tests -p test_collect.py -v
"""
import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path

from test_fanout import FAST, MARKER, W, I, FanoutCase, at, finishes

COLLECT = Path(__file__).resolve().parent.parent / "references" / "herdr-collect.mjs"
LIMITS = {"provider": "fixture", "id": "fixture", "contextWindow": 128000}


class CollectionCase(FanoutCase):
    def setUp(self):
        super().setUp()
        self.agent_dir = self.root / "agent"
        self.agent_dir.mkdir()
        self.session = self.root / "session.jsonl"
        entries = [
            {"type": "session", "version": 3, "id": "fixture-session", "cwd": str(self.root)},
            {"type": "message", "id": "s", "parentId": None,
             "message": {"role": "system", "content": "Fixture system prompt", "timestamp": 0}},
            {"type": "message", "id": "a", "parentId": "s",
             "message": {"role": "assistant", "content": [], "provider": "fixture", "model": "fixture",
                         "stopReason": "toolUse", "usage": {"input": 900, "output": 100, "cacheRead": 0,
                                                            "cacheWrite": 0, "totalTokens": 1000},
                         "timestamp": 0}},
        ]
        self.session.write_text("".join(json.dumps(e) + "\n" for e in entries))

    def env(self):
        env = super().env()
        env.update(PI_SESSION_FILE=str(self.session), PI_SESSION_ID="fixture-session", PI_PROVIDER="fixture",
                   PI_MODEL="fixture", PI_CODING_AGENT_DIR=str(self.agent_dir), PI_OFFLINE="1")
        return env

    def collect(self, *args, stdin=None, limits=None, timeout=60):
        cp = subprocess.run(["node", str(COLLECT), *args, "--limits", json.dumps(limits or LIMITS)],
                            input=stdin, capture_output=True, text=True, env=self.env(), cwd=self.root,
                            timeout=timeout)
        out = cp.stdout.strip()
        return cp.returncode, (json.loads(out) if out else None), cp.stderr

    def collect_plan(self, leaves, extra=None, *flags):
        plan = dict(FAST, slug="t-run", leaves=leaves)
        plan.update(extra or {})
        return self.collect("--run", json.dumps(["--plan", "-"]), *flags, stdin=json.dumps(plan))


class CombinedCollection(CollectionCase):
    def test_help_describes_the_additive_collection_interface(self):
        cp = subprocess.run(["node", str(COLLECT), "--help"], capture_output=True, text=True, env=self.env())
        self.assertEqual(cp.returncode, 0, cp.stderr)
        for option in ("--run", "--collect", "--page", "--final", "--context", "--limits", "--page-bytes"):
            self.assertIn(option, cp.stdout)
        self.assertIn("98304", cp.stdout)
        self.assertEqual(self.fake()["ws"], 0)

    def test_all_success_returns_run_report_and_complete_leaf_results_after_close(self):
        self.scenario({"leaves": {"spec": [finishes()], "standards": [finishes()]}})
        code, page, err = self.collect_plan([self.leaf("spec"), self.leaf("standards")],
                                            {"close": "on_success"})
        self.assertEqual(code, 0, err)
        self.assertEqual(page["state"], "complete")
        self.assertEqual(page["kind"], "results")
        self.assertIsNone(page["next_cursor"])
        self.assertIn('# Run report\n', page["text"])
        self.assertIn('"closed": true', page["text"])
        for name in ("spec", "standards"):
            self.assertIn("# Leaf result: " + name + "\n", page["text"])
            self.assertIn("# %s findings\n\nbody\n\n%s\n" % (name, MARKER), page["text"])
        self.assertEqual(self.fake()["closed"], [page["workspace"]])

    def test_partial_failure_and_retry_return_only_run_reports_until_final_collection(self):
        idle_quick = [at(0.05, **W), at(0.2, **I)]
        self.scenario({"leaves": {"spec": [finishes()],
                                  "other": [finishes("nomarker"), idle_quick, finishes()]}})
        code, first, err = self.collect_plan([self.leaf("spec"), self.leaf("other")],
                                             {"close": "on_success"})
        self.assertEqual(code, 1, err)
        self.assertEqual(first["state"], "report_only")
        self.assertIn('"reason": "no_marker"', first["text"])
        self.assertNotIn("# Leaf result:", first["text"])
        args = ["--retry-leaf", "other", "--rundir", first["rundir"], "--prompt", "-"]
        code, retried, err = self.collect("--run", json.dumps(args), stdin="Fix the marker in {{path}}.")
        self.assertEqual(code, 0, err)
        self.assertEqual(retried["state"], "report_only")
        self.assertNotIn("# Leaf result:", retried["text"])
        code, final, err = self.collect("--collect", first["rundir"])
        self.assertEqual(code, 0, err)
        self.assertEqual(final["state"], "complete")
        self.assertEqual(final["text"].count("# Leaf result:"), 2)
        self.assertIn('"attempts": 2', final["text"])
        self.assertIn('"closed": true', final["text"])

    def test_successful_retry_still_reports_partial_run_failure_consistently_across_pages(self):
        idle_quick = [at(0.05, **W), at(0.2, **I)]
        self.scenario({"leaves": {
            "spec": [finishes("nomarker"), idle_quick, finishes()],
            "other": [finishes("nomarker"), idle_quick],
        }})
        code, first, err = self.collect_plan([self.leaf("spec"), self.leaf("other")])
        self.assertEqual(code, 1, err)
        args = ["--retry-leaf", "spec", "--rundir", first["rundir"], "--prompt", "-"]
        code, retried, err = self.collect("--run", json.dumps(args), "--page-bytes", "256",
                                          stdin="Fix the marker in {{path}}.")
        self.assertEqual(code, 1, err)
        self.assertEqual(retried["kind"], "report")
        self.assertEqual(retried["state"], "more")
        chunks = [retried["text"]]
        while retried["next_cursor"]:
            code, retried, err = self.collect("--page", retried["next_cursor"], "--page-bytes", "256")
            self.assertEqual(code, 1, err)
            chunks.append(retried["text"])
        self.assertEqual(retried["state"], "report_only")
        self.assertIn('"name": "spec"', "".join(chunks))
        self.assertIn('"ok": false', "".join(chunks))
        self.assertNotIn("# Leaf result:", "".join(chunks))

    def test_wait_collects_a_detached_run_without_changing_its_close_policy(self):
        self.scenario({"leaves": {"spec": [[at(0.05, **W), at(0.6, write="expected"), at(0.7, **I)]]}})
        code, started, err = self.fanout([self.leaf("spec")], {"close": "on_success"}, "--detach")
        self.assertEqual(code, 0, err)
        code, out, err = self.collect("--run", json.dumps(["--wait", started["rundir"]]))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["state"], "complete")
        self.assertIn('"closed": true', out["text"])
        self.assertIn("# spec findings\n\nbody\n\n" + MARKER + "\n", out["text"])

    def test_dead_monitor_wait_failure_is_not_mistaken_for_a_complete_collection(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")], {"close": "on_success"})
        (Path(report["rundir"]) / "report.json").unlink()
        code, out, err = self.collect("--run", json.dumps(["--wait", report["rundir"]]))
        self.assertEqual(code, 3, err)
        self.assertEqual(out["state"], "error")
        self.assertEqual(out["reason"], "fanout_failed")
        self.assertIn("not running and left no report", out["text"])
        self.assertNotIn("# Leaf result:", out["text"])

    def test_explicit_final_collection_includes_successes_and_final_failure_reasons(self):
        self.scenario({"leaves": {"spec": [finishes()], "other": [[at(0.05, status="blocked")]]}})
        code, first, err = self.collect_plan([self.leaf("spec"), self.leaf("other")],
                                             {"close": "always", "inactivity_ms": 500})
        self.assertEqual(code, 1, err)
        self.assertEqual(first["state"], "report_only")
        code, final, err = self.collect("--collect", first["rundir"], "--final")
        self.assertEqual(code, 1, err)
        self.assertEqual(final["state"], "complete")
        self.assertIn('"reason": "blocked"', final["text"])
        self.assertEqual(final["text"].count("# Leaf result:"), 1)
        self.assertIn("# Leaf result: spec\n", final["text"])
        self.assertIn('"closed": true', final["text"])


class LosslessPages(CollectionCase):
    def test_delivered_unicode_and_long_lines_are_paged_without_changing_a_byte(self):
        dest = self.root / "repo" / "findings.md"
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec", dest=str(dest))], {"close": "on_success"})
        fixture = ("# Delivered result\r\n\r\nBEGIN\r\n" + "x" * 12000 + "\r\n" + "😀界é" * 6000 +
                   "\r\nMIDDLE\r\nSources: [primary](https://example.test/primary?q=é)\r\nEND\r\n")
        dest.write_bytes(fixture.encode("utf8"))
        Path(report["leaves"][0]["path"]).write_text("WRONG RAW COPY\n" + MARKER)
        args = ["--collect", report["rundir"], "--page-bytes", "8192"]
        pieces = []
        expected_offset = 0
        bundle_hash = None
        while True:
            code, page, err = self.collect(*args, limits=dict(LIMITS, contextWindow=1000000))
            self.assertEqual(code, 0, err)
            self.assertEqual(page["offset"], expected_offset)
            raw = page["text"].encode("utf8")
            self.assertLessEqual(len(raw), 8192)
            self.assertEqual(page["chunk_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(page["end"], expected_offset + len(raw))
            pieces.append(page["text"])
            expected_offset = page["end"]
            if bundle_hash is None:
                bundle_hash = page["sha256"]
            self.assertEqual(page["sha256"], bundle_hash)
            if page["next_cursor"] is None:
                self.assertEqual(page["state"], "complete")
                self.assertEqual(expected_offset, page["total_bytes"])
                break
            self.assertEqual(page["state"], "more")
            args = ["--page", page["next_cursor"], "--page-bytes", "8192"]
        bundle = "".join(pieces)
        self.assertIn(fixture, bundle)
        self.assertNotIn("WRONG RAW COPY", bundle)
        self.assertIn(str(dest), bundle)
        self.assertEqual(hashlib.sha256(bundle.encode("utf8")).hexdigest(), bundle_hash)


class CollectionIntegrity(CollectionCase):
    def test_precollection_and_fanout_error_notices_are_bounded_too(self):
        self.herd.mkdir()
        missing = str(self.root / ("missing-" * 80))
        refused = ["--plan", "-", "--" + "unknown" * 100, "value"]
        for args, code in ((("--collect", missing), 3), (("--run", json.dumps(refused)), 2)):
            with self.subTest(args=args[0]):
                actual, out, err = self.collect(*args, "--page-bytes", "256", stdin="{}")
                self.assertEqual(actual, code, err)
                self.assertEqual(out["state"], "error")
                self.assertLessEqual(len(out["text"].encode("utf8")), 256)
                self.assertEqual(self.fake()["ws"], 0)

    def test_empty_run_metadata_cannot_claim_a_complete_collection(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")])
        rundir = Path(report["rundir"])
        plan = json.loads((rundir / "plan.json").read_text())
        plan["leaves"] = report["leaves"] = []
        (rundir / "plan.json").write_text(json.dumps(plan))
        (rundir / "report.json").write_text(json.dumps(report))
        code, out, err = self.collect("--collect", str(rundir))
        self.assertEqual(code, 3, err)
        self.assertEqual(out["state"], "error")
        self.assertIn("does not match the plan", out["text"])

    def test_collection_refuses_a_report_path_that_was_not_assigned_by_the_plan(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")])
        outside = self.root / "unassigned.md"
        outside.write_text("UNASSIGNED-CONTENT-MUST-NOT-BE-READ")
        report["leaves"][0]["path"] = str(outside)
        (Path(report["rundir"]) / "report.json").write_text(json.dumps(report))
        code, out, err = self.collect("--collect", report["rundir"])
        self.assertEqual(code, 3, err)
        self.assertEqual(out["state"], "error")
        self.assertIn("does not match the plan", out["text"])
        self.assertNotIn("UNASSIGNED-CONTENT", out["text"])

    def test_cursor_refuses_a_run_report_changed_after_the_snapshot(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")])
        code, first, err = self.collect("--collect", report["rundir"], "--page-bytes", "256")
        self.assertEqual(code, 0, err)
        self.assertEqual(first["state"], "more")
        self.run_script("--close", report["rundir"])
        code, out, err = self.collect("--page", first["next_cursor"], "--page-bytes", "256")
        self.assertEqual(code, 3, err)
        self.assertEqual(out["state"], "error")
        self.assertEqual(out["reason"], "stale_cursor")
        self.assertNotIn("# Leaf result:", out["text"])

    def test_cursor_refuses_inconsistent_snapshot_metadata_instead_of_mislabeling_a_page(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")])
        code, first, err = self.collect("--collect", report["rundir"], "--page-bytes", "256")
        self.assertEqual(code, 0, err)
        manifest = (Path(report["rundir"]) / ".state" / "collections" /
                    ("results-" + first["sha256"]) / "manifest.json")
        original = json.loads(manifest.read_text())
        for field, value in (("workspace", "wrong-workspace"), ("terminal_state", "report_only"),
                             ("code", 1), ("version", 99), ("total_bytes", str(first["total_bytes"]))):
            with self.subTest(field=field):
                manifest.write_text(json.dumps(dict(original, **{field: value})))
                code, out, err = self.collect("--page", first["next_cursor"], "--page-bytes", "256")
                self.assertEqual(code, 3, err)
                self.assertEqual(out["state"], "error")
                self.assertEqual(out["reason"], "invalid_snapshot")
                self.assertNotIn("# Leaf result:", out["text"])
        manifest.write_text(json.dumps(original))
        code, replayed, err = self.collect("--page", first["next_cursor"], "--page-bytes", "256")
        self.assertEqual(code, 0, err)
        self.assertEqual(replayed["offset"], first["end"])

    def test_an_unreadable_successful_result_keeps_the_run_report_without_a_partial_bundle(self):
        self.scenario({"leaves": {"spec": [finishes()], "other": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec"), self.leaf("other")])
        Path(report["leaves"][1]["path"]).write_bytes(b"invalid UTF-8: \xff")
        code, out, err = self.collect("--collect", report["rundir"])
        self.assertEqual(code, 3, err)
        self.assertEqual(out["state"], "error")
        self.assertEqual(out["workspace"], report["workspace"])
        self.assertEqual(out["rundir"], report["rundir"])
        self.assertIn("# Run report", out["text"])
        self.assertIn("not valid UTF-8", out["text"])
        self.assertNotIn("# Leaf result:", out["text"])
        self.assertNotIn("# spec findings", out["text"])

    def test_collection_refuses_symlinked_leaf_results_without_reading_the_target(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")])
        outside = self.root / "unassigned.md"
        outside.write_text("SYMLINK-TARGET-MUST-NOT-BE-READ")
        result = Path(report["leaves"][0]["path"])
        result.unlink()
        result.symlink_to(outside)
        code, out, err = self.collect("--collect", report["rundir"])
        self.assertEqual(code, 3, err)
        self.assertEqual(out["state"], "error")
        self.assertIn("regular file", out["text"])
        self.assertNotIn("SYMLINK-TARGET", out["text"])


class ContextPressure(CollectionCase):
    def test_control_notices_obey_the_page_budget_without_returning_partial_findings(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")])
        Path(report["leaves"][0]["path"]).write_bytes(b"FINDINGS-MUST-STAY-ON-DISK\xff")
        for window, state in ((18000, "limited"), (128000, "error")):
            with self.subTest(state=state):
                code, out, err = self.collect("--collect", report["rundir"], "--page-bytes", "256",
                                              limits=dict(LIMITS, contextWindow=window))
                self.assertEqual(code, 3, err)
                self.assertEqual(out["state"], state)
                self.assertLessEqual(len(out["text"].encode("utf8")), 256)
                self.assertEqual(out["workspace"], report["workspace"])
                self.assertEqual(out["rundir"], report["rundir"])
                self.assertNotIn("FINDINGS-MUST-STAY-ON-DISK", out["text"])
                self.assertNotIn("# Leaf result:", out["text"])

    def test_context_is_rechecked_between_pages_and_a_refused_cursor_can_be_replayed(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")])
        result = Path(report["leaves"][0]["path"])
        result.write_text("PAGE-PRESSURE\n" + "x" * 20000 + "\n" + MARKER + "\n")
        code, first, err = self.collect("--collect", report["rundir"], "--page-bytes", "8192")
        self.assertEqual(code, 0, err)
        self.assertEqual(first["state"], "more")
        original = self.session.read_text()
        entries = [json.loads(line) for line in original.splitlines()]
        entries[-1]["message"]["usage"].update(totalTokens=120000, input=119900)
        self.session.write_text("".join(json.dumps(e) + "\n" for e in entries))
        code, limited, err = self.collect("--page", first["next_cursor"], "--page-bytes", "8192")
        self.assertEqual(code, 3, err)
        self.assertEqual(limited["state"], "limited")
        self.assertNotIn("PAGE-PRESSURE", limited["text"])
        self.session.write_text(original)
        code, replayed, err = self.collect("--page", first["next_cursor"], "--page-bytes", "8192")
        self.assertEqual(code, 0, err)
        self.assertEqual(replayed["offset"], first["end"])
        self.assertEqual(replayed["sha256"], first["sha256"])

    def test_context_probe_uses_active_branch_and_model_specific_compaction_reserve(self):
        entries = [json.loads(line) for line in self.session.read_text().splitlines()]
        entries += [
            {"type": "message", "id": "abandoned", "parentId": "a",
             "message": {"role": "toolResult", "content": [{"type": "text", "text": "x" * 400000}],
                         "timestamp": 0}},
            dict(entries[-1], id="active", parentId="s"),
            {"type": "message", "id": "tail", "parentId": "active",
             "message": {"role": "toolResult", "content": [{"type": "text", "text": "tail"}],
                         "timestamp": 0}},
        ]
        self.session.write_text("".join(json.dumps(e) + "\n" for e in entries))
        (self.agent_dir / "settings.json").write_text(json.dumps({
            "compaction": {"modelOverrides": {"fixture/fixture": {"reserveTokens": 20000}}}}))
        code, probe, err = self.collect("--context")
        self.assertEqual(code, 0, err)
        self.assertEqual(probe["state"], "context")
        self.assertEqual(probe["used_tokens"], 1001)
        self.assertEqual(probe["reserve_tokens"], 20000)
        self.assertEqual(probe["session_id"], "fixture-session")
        self.assertEqual(probe["leaf_id"], "tail")
        self.assertEqual(self.fake()["ws"], 0)

    def test_unknown_post_compaction_context_refuses_before_spawning(self):
        entries = [json.loads(line) for line in self.session.read_text().splitlines()]
        entries.append({"type": "compaction", "id": "c", "parentId": "a", "summary": "summary",
                        "firstKeptEntryId": "c", "tokensBefore": 1000})
        self.session.write_text("".join(json.dumps(e) + "\n" for e in entries))
        code, out, err = self.collect_plan([self.leaf("spec")])
        self.assertEqual(code, 3, err)
        self.assertEqual(out["state"], "error")
        self.assertEqual(out["reason"], "context_unknown")
        self.assertIn("post-edit assistant usage", out["text"])
        self.assertEqual(self.fake()["ws"], 0)

    def test_collection_stops_before_emitting_any_findings_when_the_whole_bundle_cannot_fit(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, report, _ = self.fanout([self.leaf("spec")])
        result = Path(report["leaves"][0]["path"])
        fixture = "BEGIN-FINDINGS\n" + "x" * 100000 + "\nEND-FINDINGS\n" + MARKER + "\n"
        result.write_text(fixture)
        code, out, err = self.collect("--collect", report["rundir"],
                                      limits=dict(LIMITS, contextWindow=32768))
        self.assertEqual(code, 3, err)
        self.assertEqual(out["state"], "limited")
        self.assertEqual(out["reason"], "context_limit")
        self.assertGreater(out["required_tokens"], out["available_tokens"])
        self.assertIn("# Run report", out["text"])
        self.assertNotIn("BEGIN-FINDINGS", out["text"])
        self.assertNotIn("# Leaf result:", out["text"])
        self.assertEqual(result.read_text(), fixture)


if __name__ == "__main__":
    unittest.main()
