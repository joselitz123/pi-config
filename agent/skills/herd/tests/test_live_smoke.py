"""Opt-in live smoke: one real pi leaf through real herdr. Costs one small inference run.

Run inside herdr:  HERD_LIVE_SMOKE=1 python3 -m unittest -v test_live_smoke
Checks what the fake cannot: hook-reported status on a real leaf, `agent start --timeout`
readiness, the rendered footer being followed, and close=always.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "references" / "herdr-fanout.sh"


@unittest.skipUnless(os.environ.get("HERD_LIVE_SMOKE") == "1" and os.environ.get("HERDR_ENV") == "1",
                     "set HERD_LIVE_SMOKE=1 inside herdr to spawn a real leaf")
class LiveSmoke(unittest.TestCase):
    def test_one_leaf_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HERD_ROOT=str(Path(tmp) / "herd"))
            env.pop("HERDR_DEPTH", None)
            dest = Path(tmp) / "dest" / "smoke.md"
            plan = {"slug": "herd-live-smoke", "close": "always", "cadence_ms": 1000, "quiesce_ms": 8000,
                    "inactivity_ms": 180000, "cap_ms": 600000,
                    "leaves": [{"name": "smoke", "dest": str(dest),
                                "prompt": "Write a markdown file to {{path}} with a heading `# smoke` and one "
                                          "line saying hello. Use no other tools."}]}
            cp = subprocess.run([str(SCRIPT), "--plan", "-"], input=json.dumps(plan), capture_output=True,
                                text=True, env=env, timeout=900)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            rep = json.loads(cp.stdout)
            leaf = rep["leaves"][0]
            self.assertTrue(leaf["ok"], leaf)
            self.assertTrue(rep["closed"])
            self.assertTrue(leaf["delivered"])
            self.assertIn("smoke", dest.read_text())
            self.assertNotIn("<!-- herd:done -->", dest.read_text())
            events = [json.loads(l) for l in (Path(rep["rundir"]) / "events.jsonl").read_text().splitlines()]
            self.assertIn("spawned", [e["event"] for e in events])


if __name__ == "__main__":
    unittest.main()
