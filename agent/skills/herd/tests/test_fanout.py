"""Deterministic tests for herdr-fanout against a fake `herdr`.

Run:  python3 -m unittest discover -s ~/.pi/agent/skills/herd/tests -v
Live: HERD_LIVE_SMOKE=1 python3 -m unittest test_live_smoke   (spawns one real pi leaf)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "references" / "herdr-fanout.sh"
FAKE = HERE / "fake_herdr.py"
MARKER = "<!-- herd:done -->"
FAST = {"cadence_ms": 50, "quiesce_ms": 250, "inactivity_ms": 1500, "cap_ms": 8000}

W, I = {"status": "working"}, {"status": "idle"}


def at(t, **kw):
    return dict(kw, at=t)


def finishes(write="expected"):
    """One dispatch: work, write, settle."""
    return [at(0.05, **W), at(0.2, write=write), at(0.35, **I)]


class FanoutCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.herd = self.root / "herd"
        self.state = self.root / "fake-state.json"
        self.scenario_path = self.root / "scenario.json"
        self.scenario({})

    def tearDown(self):
        self.tmp.cleanup()

    def scenario(self, sc):
        self.scenario_path.write_text(json.dumps(sc))

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HERDR_", "HERD_"))}
        env.update(HERDR_ENV="1", HERDR_BIN=str(FAKE), HERD_ROOT=str(self.herd), HERD_NUDGE_GRACE_MS="300",
                   FAKE_HERDR_STATE=str(self.state), FAKE_HERDR_SCENARIO=str(self.scenario_path))
        return env

    def run_script(self, *args, stdin=None, timeout=60):
        cp = subprocess.run([str(SCRIPT), *args], input=stdin, capture_output=True, text=True,
                            env=self.env(), timeout=timeout)
        out = cp.stdout.strip()
        return cp.returncode, (json.loads(out) if out else None), cp.stderr

    def fanout(self, leaves, extra=None, *flags):
        plan = dict(FAST, slug="t-run", leaves=leaves)
        plan.update(extra or {})
        return self.run_script("--plan", "-", *flags, stdin=json.dumps(plan))

    def fake(self):
        return json.loads(self.state.read_text()) if self.state.exists() else {"ws": 0, "closed": []}

    def events(self, rundir):
        return [json.loads(l) for l in (Path(rundir) / "events.jsonl").read_text().splitlines()]

    def leaf(self, name, prompt="Investigate the thing. Write findings to {{path}}.", **kw):
        return dict(kw, name=name, prompt=prompt)


class HappyPath(FanoutCase):
    def test_two_leaves_ok_and_archived(self):
        self.scenario({"leaves": {"spec": [finishes()], "standards": [finishes()]}})
        code, rep, err = self.fanout([self.leaf("spec"), self.leaf("standards")])
        self.assertEqual(code, 0, err)
        self.assertTrue(rep["ok"])
        month = datetime.now(timezone.utc).strftime("%Y/%m")
        self.assertEqual(rep["rundir"], str(self.herd / month / "t-run"))
        for l in rep["leaves"]:
            self.assertTrue(l["ok"])
            self.assertIsNone(l["reason"])
            self.assertFalse(l["nudged"])
            self.assertEqual(l["path"], str(Path(rep["rundir"]) / ("%s.md" % l["name"])))
        rd = Path(rep["rundir"])
        self.assertEqual(json.loads((rd / "report.json").read_text()), rep)
        plan = json.loads((rd / "plan.json").read_text())
        spec = plan["leaves"][0]
        self.assertIn(spec["path"], spec["prompt"])          # {{path}} expanded
        self.assertNotIn("{{path}}", spec["prompt"])
        self.assertIn("Herd result file: `%s`" % spec["path"], spec["prompt"])
        kinds = [e["event"] for e in self.events(rd)]
        for k in ("rundir", "workspace", "spawned", "dispatched", "verdict", "finished"):
            self.assertIn(k, kinds)
        self.assertFalse(rep["closed"])
        self.assertEqual(self.fake()["closed"], [])


class Paths(FanoutCase):
    def test_slug_collision_gets_suffix(self):
        month = datetime.now(timezone.utc).strftime("%Y/%m")
        (self.herd / month / "t-run").mkdir(parents=True)
        self.scenario({"leaves": {"spec": [finishes()]}})
        code, rep, err = self.fanout([self.leaf("spec")])
        self.assertEqual(code, 0, err)
        self.assertTrue(rep["rundir"].endswith("/t-run-2"))

    def test_slug_is_sanitised(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        code, rep, err = self.fanout([self.leaf("spec")], {"slug": "DPP 53 / Review!"})
        self.assertEqual(code, 0, err)
        self.assertTrue(rep["rundir"].endswith("/dpp-53-review"))

    def test_dest_delivery_strips_marker(self):
        dest = self.root / "repo" / "docs" / "note.md"
        self.scenario({"leaves": {"research": [finishes()]}})
        code, rep, err = self.fanout([self.leaf("research", dest=str(dest))])
        self.assertEqual(code, 0, err)
        leaf = rep["leaves"][0]
        self.assertTrue(leaf["delivered"])
        self.assertEqual(leaf["dest"], str(dest))
        body = dest.read_text()
        self.assertNotIn(MARKER, body)
        self.assertIn("research findings", body)
        self.assertIn(MARKER, Path(leaf["path"]).read_text())

    def test_slow_start_that_came_up_is_accepted(self):
        self.scenario({"start_timeout": True, "leaves": {"spec": [finishes()]}})
        code, rep, err = self.fanout([self.leaf("spec")])
        self.assertEqual(code, 0, err)
        self.assertEqual(rep["leaves"][0]["agent"], "spec")

    def test_taken_agent_name_gets_suffix(self):
        self.scenario({"taken_names": ["spec"], "leaves": {"spec": [finishes()]}})
        code, rep, err = self.fanout([self.leaf("spec")])
        self.assertEqual(code, 0, err)
        self.assertEqual(rep["leaves"][0]["agent"], "spec-2")


class Nudge(FanoutCase):
    def test_misplaced_then_fixed_by_nudge(self):
        self.scenario({"leaves": {"spec": [finishes("misplaced"), finishes("expected")]}})
        code, rep, err = self.fanout([self.leaf("spec")])
        self.assertEqual(code, 0, err)
        leaf = rep["leaves"][0]
        self.assertTrue(leaf["ok"])
        self.assertTrue(leaf["nudged"])
        nudge = [e for e in self.events(rep["rundir"]) if e["event"] == "nudge"]
        self.assertEqual(len(nudge), 1)
        self.assertTrue(nudge[0]["candidates"][0].endswith("spec-findings.md"))
        texts = self.fake()["agents"][leaf["pane"]]["texts"]
        self.assertIn("reads exactly one file: `%s`" % leaf["path"], texts[1])
        self.assertIn("spec-findings.md", texts[1])

    def test_misplaced_and_nudge_ignored_fails_with_reason(self):
        self.scenario({"leaves": {"spec": [finishes("misplaced"), [at(0.05, **W), at(0.2, **I)]]}})
        code, rep, err = self.fanout([self.leaf("spec")])
        self.assertEqual(code, 1, err)
        leaf = rep["leaves"][0]
        self.assertFalse(leaf["ok"])
        self.assertTrue(leaf["reason"].startswith("misplaced:"))
        self.assertTrue(leaf["reason"].endswith("spec-findings.md"))
        self.assertEqual(len(self.fake()["agents"][leaf["pane"]]["texts"]), 2)   # exactly one nudge

    def test_no_marker_reason(self):
        self.scenario({"leaves": {"spec": [finishes("nomarker"), [at(0.05, **W), at(0.2, **I)]]}})
        code, rep, _ = self.fanout([self.leaf("spec")])
        self.assertEqual(code, 1)
        self.assertEqual(rep["leaves"][0]["reason"], "no_marker")

    def test_hook_reported_working_through_retries_gets_no_nudge(self):
        # pi auto-retrying: the hook keeps `working` for well past the quiesce window.
        self.scenario({"leaves": {"spec": [[at(0.05, **W), at(0.05, screen="tick"),
                                            at(1.2, write="expected"), at(1.3, **I)]]}})
        code, rep, err = self.fanout([self.leaf("spec")])
        self.assertEqual(code, 0, err)
        self.assertFalse(rep["leaves"][0]["nudged"])
        self.assertFalse([e for e in self.events(rep["rundir"]) if e["event"] == "nudge"])


class Timers(FanoutCase):
    def test_inactivity_timeout(self):
        self.scenario({"leaves": {"spec": [[at(0.05, **W)]]}})
        code, rep, _ = self.fanout([self.leaf("spec")], {"inactivity_ms": 600})
        self.assertEqual(code, 1)
        self.assertEqual(rep["leaves"][0]["reason"], "inactive")

    def test_blocked_reason(self):
        self.scenario({"leaves": {"spec": [[at(0.05, status="blocked")]]}})
        code, rep, _ = self.fanout([self.leaf("spec")], {"inactivity_ms": 600})
        self.assertEqual(code, 1)
        self.assertEqual(rep["leaves"][0]["reason"], "blocked")

    def test_overall_cap_even_while_active(self):
        self.scenario({"leaves": {"spec": [[at(0.05, **W), at(0.05, screen="tick")]]}})
        code, rep, _ = self.fanout([self.leaf("spec")], {"cap_ms": 900})
        self.assertEqual(code, 1)
        self.assertEqual(rep["leaves"][0]["reason"], "cap")


class Refusals(FanoutCase):
    def assertRefused(self, leaves, extra=None, needle=""):
        code, rep, err = self.fanout(leaves, extra)
        self.assertEqual(code, 2, err)
        self.assertIsNone(rep)
        self.assertIn(needle, err)
        self.assertEqual(self.fake()["ws"], 0)      # nothing spawned

    def test_legacy_path_field(self):
        self.assertRefused([dict(self.leaf("spec"), path="/abs/spec.md")], needle='"path" was removed')

    def test_prompt_naming_herd_dir(self):
        self.assertRefused([self.leaf("spec", prompt="Write to ~/.pi/herd/2026/03/x/spec.md")],
                           needle="names a path under")
        home = str(Path.home())
        self.assertRefused([self.leaf("spec", prompt="Write to %s/.pi/herd/2026-09/spec.md" % home)],
                           needle="names a path under")

    def test_repo_paths_in_prompt_are_fine(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        code, _, err = self.fanout([self.leaf("spec", prompt="Read docs/contracts/CONTRACT-MAP.md and "
                                              "/Users/x/repo/README.md. Write to {{path}}.")])
        self.assertEqual(code, 0, err)

    def test_timeout_ms_replaced(self):
        self.assertRefused([self.leaf("spec")], {"timeout_ms": 1000}, needle="inactivity_ms")

    def test_bad_leaf_name(self):
        self.assertRefused([self.leaf("01-map")], needle="lowercase letter first")

    def test_too_many_leaves(self):
        self.assertRefused([self.leaf("l%d" % i) for i in range(6)], needle="cap is 5")

    def test_leaf_depth_below_two(self):
        self.assertRefused([self.leaf("spec")], {"leaf_depth": 1}, needle="leaves cannot herd")

    def test_hook_integration_missing(self):
        self.scenario({"integration": "pi: not installed (/x/herdr-agent-state.ts)"})
        self.assertRefused([self.leaf("spec")], needle="herdr integration install pi")

    def test_depth_guard(self):
        env = self.env()
        env["HERDR_DEPTH"] = "2"
        plan = dict(FAST, slug="t", leaves=[self.leaf("spec")])
        cp = subprocess.run([str(SCRIPT), "--plan", "-"], input=json.dumps(plan), capture_output=True,
                            text=True, env=env, timeout=30)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("leaves cannot herd", cp.stderr)

    def test_removed_retry_flags(self):
        code, _, err = self.run_script("--retry-leaf", "spec", "--path", "/x.md", "--prompt", "p")
        self.assertEqual(code, 2)
        self.assertIn("--path was removed", err)


class Close(FanoutCase):
    def test_on_success_closes(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        code, rep, err = self.fanout([self.leaf("spec")], {"close": "on_success"})
        self.assertEqual(code, 0, err)
        self.assertTrue(rep["closed"])
        self.assertEqual(self.fake()["closed"], [rep["workspace"]])

    def test_on_success_keeps_failed_run_open(self):
        self.scenario({"leaves": {"spec": [[at(0.05, **W)]]}})
        code, rep, _ = self.fanout([self.leaf("spec")], {"close": "on_success", "inactivity_ms": 500})
        self.assertEqual(code, 1)
        self.assertFalse(rep["closed"])
        self.assertEqual(self.fake()["closed"], [])

    def test_manual_close(self):
        self.scenario({"leaves": {"spec": [finishes()]}})
        _, rep, _ = self.fanout([self.leaf("spec")])
        code, out, err = self.run_script("--close", rep["rundir"])
        self.assertEqual(code, 0, err)
        self.assertTrue(out["closed"])
        self.assertTrue(json.loads((Path(rep["rundir"]) / "report.json").read_text())["closed"])


class Detach(FanoutCase):
    def test_detach_status_wait(self):
        self.scenario({"leaves": {"spec": [[at(0.05, **W), at(0.05, screen="tick"),
                                            at(1.0, write="expected"), at(1.1, **I)]]}})
        t0 = time.time()
        code, out, err = self.fanout([self.leaf("spec")], None, "--detach")
        self.assertEqual(code, 0, err)
        self.assertTrue(out["detached"])
        self.assertLess(time.time() - t0, 5)
        time.sleep(0.4)
        code, st, _ = self.run_script("--status", out["rundir"])
        self.assertEqual(code, 0)
        self.assertEqual(st["state"], "running")
        self.assertEqual(st["leaves"][0]["name"], "spec")
        code, rep, err = self.run_script("--wait", out["rundir"])
        self.assertEqual(code, 0, err)
        self.assertTrue(rep["ok"])
        code, st, _ = self.run_script("--status", out["rundir"])
        self.assertEqual(st["state"], "finished")


class Retry(FanoutCase):
    def test_retry_refused_while_hook_says_working(self):
        self.scenario({"leaves": {"spec": [[at(0.05, **W)]]}})
        _, rep, _ = self.fanout([self.leaf("spec")], {"inactivity_ms": 500})
        self.assertEqual(rep["leaves"][0]["reason"], "inactive")
        code, _, err = self.run_script("--retry-leaf", "spec", "--rundir", rep["rundir"], "--prompt", "again {{path}}")
        self.assertEqual(code, 2)
        self.assertIn("still working", err)

    def test_retry_archives_and_succeeds(self):
        idle_quick = [at(0.05, **W), at(0.2, **I)]
        self.scenario({"leaves": {"spec": [finishes("nomarker"), idle_quick, finishes("expected")]}})
        _, rep, _ = self.fanout([self.leaf("spec")])
        self.assertEqual(rep["leaves"][0]["reason"], "no_marker")
        code, entry, err = self.run_script("--retry-leaf", "spec", "--rundir", rep["rundir"],
                                           "--prompt", "Previous attempt had no marker. Redo it into {{path}}.")
        self.assertEqual(code, 0, err)
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["attempts"], 2)
        rd = Path(rep["rundir"])
        self.assertTrue((rd / ".state" / "attempts" / "spec.1.md").exists())
        self.assertTrue(json.loads((rd / "report.json").read_text())["ok"])

    def test_retry_refuses_stale_herd_path(self):
        self.scenario({"leaves": {"spec": [finishes("nomarker"), [at(0.05, **W), at(0.2, **I)]]}})
        _, rep, _ = self.fanout([self.leaf("spec")])
        code, _, err = self.run_script("--retry-leaf", "spec", "--rundir", rep["rundir"],
                                       "--prompt", "Write to ~/.pi/herd/2026/03/spec.md")
        self.assertEqual(code, 2)
        self.assertIn("names a path under", err)


if __name__ == "__main__":
    unittest.main()
