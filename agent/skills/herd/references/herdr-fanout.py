#!/usr/bin/env python3
"""herdr-fanout — fan leaf pi agents out over herdr and decide when each is done.

Usage:
  herdr-fanout.sh --plan <file|->                 fan out, block, print the report
  herdr-fanout.sh --plan <file|-> --detach        fan out, print {workspace, rundir}, monitor in background
  herdr-fanout.sh --status <rundir>               non-blocking progress snapshot
  herdr-fanout.sh --wait <rundir>                 block until a detached run finishes, print the report
  herdr-fanout.sh --retry-leaf <name> --rundir <rundir> --prompt <text|->
                                                  re-prompt one finished, ok:false leaf; print its entry
  herdr-fanout.sh --close <rundir>                close the run's herdr workspace

Plan (JSON, from a file or stdin with `--plan -`):
  {
    "slug": "dpp53-review",          required; sanitised to [a-z0-9-]
    "close": "never",                optional: never | on_success | always (default never)
    "cadence_ms": 2000,              optional poll interval
    "quiesce_ms": 15000,             optional settle window before a verdict
    "inactivity_ms": 300000,         optional: fail a working leaf whose terminal, session,
                                     result file and state all stay unchanged this long
    "cap_ms": 1800000,               optional overall cap per dispatch
    "leaf_depth": 2,                 optional HERDR_DEPTH given to leaves
    "leaves": [
      {"name": "spec", "prompt": "Review ... Write your findings to {{path}}.",
       "dest": "/abs/final/location.md"}          dest optional
    ]
  }

Paths are owned by this script:
  rundir      ~/.pi/herd/<UTC YYYY>/<MM>/<slug>; an existing rundir gets -2, -3, ...
  leaf path   <rundir>/<name>.md; `{{path}}` in a prompt expands to it, and the appended
              footer names it again together with the `<!-- herd:done -->` marker rule.
  dest        after a leaf is ok, its file is copied here with the marker line removed.
Leaf names: 1-28 chars, lowercase letter first, then [a-z0-9_-]. The herdr agent name is the
leaf name, or <name>-2, -3, ... when a live agent already holds it.

Refused before anything spawns (exit 2): missing/invalid fields, a leaf `path` field
(removed — use {{path}} and dest), `timeout_ms` (replaced by inactivity_ms and cap_ms),
a prompt naming anything under ~/.pi/herd/, more than 5 leaves, HERDR_ENV!=1,
HERDR_DEPTH>=2, or the herdr pi hook integration missing.

Verdict per leaf. The herdr pi hook reports `idle` only after pi settles, so auto-retries
stay `working`. A leaf is ok when its hook status is idle/done, its state has been quiet
for quiesce_ms, and its file carries the marker. When it settles quietly without that,
the script sends it ONE nudge per dispatch naming the exact path (and any unassigned .md
files found in the rundir); settling again without a valid file fails it. A retry first
moves the leaf's previous file to .state/attempts/, so its verdict starts fresh. Reasons:
  no_file | no_marker | misplaced:<files> | inactive | blocked | cap | agent_gone |
  hook_lost | dispatch_failed:<code> | dest_failed:<error>

Report (stdout, also <rundir>/report.json):
  {"workspace":"w3","rundir":"...","ok":false,"closed":false,
   "leaves":[{"name":"spec","agent":"spec","pane":"w3:p1","status":"idle","ok":true,
              "reason":null,"path":"<rundir>/spec.md","bytes":14302,"dest":null,
              "delivered":false,"nudged":false,"attempts":1}]}
Retry prints that run's single leaf entry. <rundir> also keeps plan.json (resolved plan),
events.jsonl (one event per line) and status.json (latest snapshot).

Exit: 0 every leaf ok | 1 some leaf ok:false | 2 refused, nothing spawned |
      3 herdr/setup failure or a dead background monitor.
Progress goes to stderr; stdout carries JSON only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info < (3, 9):
    sys.stderr.write("herdr-fanout: python 3.9+ required\n")
    sys.exit(2)

MARKER = "<!-- herd:done -->"
PLACEHOLDER = "{{path}}"
MAX_LEAVES = 5
SETTLED = ("idle", "done")
LEAF_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,27}$")
AGENT_NAME_MAX = 32
DEFAULTS = {
    "cadence_ms": 2000,
    "quiesce_ms": 15000,
    "inactivity_ms": 300000,
    "cap_ms": 1800000,
    "leaf_depth": 2,
    "close": "never",
}
CLOSE_POLICIES = ("never", "on_success", "always")
NUDGE_GRACE_MS = int(os.environ.get("HERD_NUDGE_GRACE_MS") or 30000)  # min wait for a leaf to react to a nudge
MISSING_POLLS = 3          # consecutive polls without the leaf's agent -> agent_gone
START_TIMEOUT_MS = 60000


class Refused(Exception):
    """Refused before anything spawned -> exit 2."""


class HerdrError(Exception):
    """herdr call failed -> exit 3."""


# --------------------------------------------------------------------------- utils

def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def herd_root() -> Path:
    return Path(os.environ.get("HERD_ROOT") or Path.home() / ".pi" / "herd")


def write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text())


def emit(data) -> None:
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


def file_sig(path) -> tuple:
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except (OSError, TypeError):
        return (None, None)


def file_check(path: Path) -> str:
    """ok | no_file | no_marker"""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "no_file"
    if not text.strip():
        return "no_file"
    return "ok" if MARKER in text else "no_marker"


def file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def sanitize_slug(raw: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", str(raw).lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:80].strip("-")


class Events:
    def __init__(self, rundir: Path):
        self.path = rundir / "events.jsonl"

    def __call__(self, event: str, **fields) -> None:
        rec = {"ts": now_iso(), "event": event}
        rec.update(fields)
        with self.path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")


# --------------------------------------------------------------------------- herdr

class Herdr:
    def __init__(self):
        self.bin = os.environ.get("HERDR_BIN") or shutil.which("herdr")
        if not self.bin:
            raise Refused("herdr not found on PATH")

    def raw(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run([self.bin, *args], capture_output=True, text=True, timeout=timeout)

    def call(self, *args: str, timeout: float = 120) -> dict:
        """Run a JSON-returning herdr command. Returns the parsed object (may carry .error)."""
        try:
            cp = self.raw(*args, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error": {"code": "timeout", "message": "herdr %s timed out" % args[0]}}
        out = cp.stdout.strip() or cp.stderr.strip()
        try:
            data = json.loads(out)
        except ValueError:
            data = {"error": {"code": "unparsable", "message": out[:500]}}
        if cp.returncode != 0 and "error" not in data:
            data["error"] = {"code": "exit_%d" % cp.returncode, "message": out[:500]}
        return data

    def pi_hook_installed(self) -> tuple:
        try:
            cp = self.raw("integration", "status", timeout=30)
        except subprocess.TimeoutExpired:
            return (False, "herdr integration status timed out")
        for line in cp.stdout.splitlines():
            if line.strip().startswith("pi:"):
                return ("not installed" not in line, line.strip())
        return (False, "no pi line in `herdr integration status`")

    def agents_by_pane(self) -> dict | None:
        data = self.call("agent", "list", timeout=30)
        if "error" in data:
            return None
        return {a.get("pane_id"): a for a in data.get("result", {}).get("agents", []) if a.get("pane_id")}

    def agent_names(self) -> set:
        agents = self.agents_by_pane() or {}
        return {a.get("name") for a in agents.values() if a.get("name")}

    def screen(self, pane: str) -> str:
        try:
            cp = self.raw("agent", "read", pane, "--source", "recent", "--lines", "60", timeout=30)
        except subprocess.TimeoutExpired:
            return ""
        return cp.stdout if cp.returncode == 0 else ""

    def prompt(self, pane: str, text: str) -> str | None:
        """Dispatch without waiting. Returns None on success, else an error code."""
        for attempt in range(2):
            data = self.call("agent", "prompt", pane, text, timeout=60)
            if "error" not in data:
                return None
            code = data["error"].get("code", "error")
            if attempt == 0:
                time.sleep(2)
        return code

    def close(self, workspace: str) -> bool:
        return "error" not in self.call("workspace", "close", workspace, timeout=60)


def normalize_screen(text: str) -> str:
    """Strip spinner glyphs and digits so an animating spinner or ticking timer is not activity."""
    text = re.sub("[\u2800-\u28ff]", "", text)
    return re.sub(r"\d+", "#", text)


# --------------------------------------------------------------------------- plan

def load_plan_source(src: str) -> dict:
    try:
        raw = sys.stdin.read() if src == "-" else Path(src).read_text()
    except OSError as exc:
        raise Refused("plan not readable: %s" % exc)
    try:
        plan = json.loads(raw)
    except ValueError as exc:
        raise Refused("plan is not valid JSON: %s" % exc)
    if not isinstance(plan, dict):
        raise Refused("plan must be a JSON object")
    return plan


def herd_path_mentions(text: str) -> list:
    home = str(Path.home())
    roots = {str(herd_root()), home + "/.pi/herd", "~/.pi/herd", "$HOME/.pi/herd", "${HOME}/.pi/herd"}
    return sorted(r for r in roots if r + "/" in text or text.rstrip().endswith(r))


def validate_prompt(name: str, prompt) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise Refused('leaf "%s": missing "prompt"' % name)
    hits = herd_path_mentions(prompt)
    if hits:
        raise Refused(
            'leaf "%s": prompt names a path under %s — the script assigns each leaf its file; '
            "write {{path}} where the prompt names the output file" % (name, hits[0]))
    return prompt


def validate_plan(plan: dict) -> dict:
    if "timeout_ms" in plan:
        raise Refused('"timeout_ms" was replaced by "inactivity_ms" and "cap_ms"')
    slug = sanitize_slug(plan.get("slug") or "")
    if not slug:
        raise Refused('plan needs a "slug" with at least one [a-z0-9] character')
    settings = {}
    for key, default in DEFAULTS.items():
        val = plan.get(key, default)
        if key == "close":
            if val not in CLOSE_POLICIES:
                raise Refused('"close" must be one of %s' % ", ".join(CLOSE_POLICIES))
        elif key == "leaf_depth":
            if not isinstance(val, int) or isinstance(val, bool) or val < 2:
                raise Refused('"leaf_depth" must be an integer >= 2 so leaves cannot herd')
        elif not isinstance(val, int) or isinstance(val, bool) or val < 1:
            raise Refused('"%s" must be a positive integer' % key)
        settings[key] = val
    leaves = plan.get("leaves")
    if not isinstance(leaves, list) or not leaves:
        raise Refused('plan needs a non-empty "leaves" array')
    if len(leaves) > MAX_LEAVES:
        raise Refused("plan has %d leaves; the herd cap is %d" % (len(leaves), MAX_LEAVES))
    seen, out = set(), []
    for i, leaf in enumerate(leaves):
        if not isinstance(leaf, dict):
            raise Refused("leaf[%d] must be an object" % i)
        name = leaf.get("name")
        if not isinstance(name, str) or not LEAF_NAME_RE.match(name):
            raise Refused('leaf[%d] name %r: use 1-28 chars, a lowercase letter first, then [a-z0-9_-]' % (i, name))
        if name in seen:
            raise Refused('duplicate leaf name "%s"' % name)
        seen.add(name)
        if "path" in leaf:
            raise Refused(
                'leaf "%s": "path" was removed — the script assigns <rundir>/%s.md; write {{path}} in the '
                'prompt where the output file goes, and use "dest" for a final location' % (name, name))
        prompt = validate_prompt(name, leaf.get("prompt"))
        dest = leaf.get("dest")
        if dest is not None:
            if not isinstance(dest, str) or not dest.strip():
                raise Refused('leaf "%s": "dest" must be an absolute path' % name)
            dest_p = Path(os.path.expanduser(dest))
            if not dest_p.is_absolute():
                raise Refused('leaf "%s": "dest" must be absolute, got %s' % (name, dest))
            if str(dest_p.resolve()).startswith(str(herd_root().resolve()) + os.sep):
                raise Refused('leaf "%s": "dest" must lie outside ~/.pi/herd — the leaf file already lives in '
                              'the rundir; omit "dest" to keep only that copy' % name)
            dest = str(dest_p)
        out.append({"name": name, "prompt": prompt, "dest": dest})
    return {"slug": slug, "settings": settings, "leaves": out}


def allocate_rundir(slug: str) -> Path:
    base = herd_root() / datetime.now(timezone.utc).strftime("%Y/%m")
    base.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        cand = base / (slug if n == 1 else "%s-%d" % (slug, n))
        try:
            cand.mkdir()
            return cand
        except FileExistsError:
            n += 1


def footer(path: Path) -> str:
    return (
        "\n\n---\nHerd result file: `%s`\n"
        "Write your complete findings as markdown to exactly that path — it is the only file the herd reads. "
        "When the file is complete, add one final line containing exactly `%s`, then stop." % (path, MARKER))


def render(prompt: str, path: Path) -> str:
    return prompt.replace(PLACEHOLDER, str(path)) + footer(path)


def nudge_text(path: Path, check: str, candidates: list) -> str:
    lines = ["Your herd result is not complete yet. The herd reads exactly one file: `%s`." % path]
    if candidates:
        lines.append("These files in the run directory are not assigned to any leaf: %s. "
                     "If one is yours, move its content to `%s`." % (", ".join("`%s`" % c for c in candidates), path))
    elif check == "no_marker":
        lines.append("The file exists but does not end with the completion marker.")
    lines.append("Make sure `%s` holds your complete findings and ends with a final line containing exactly `%s`, "
                 "then stop." % (path, MARKER))
    return " ".join(lines)


# --------------------------------------------------------------------------- run state

class Run:
    """A rundir with its resolved plan."""

    def __init__(self, rundir: Path):
        self.dir = rundir
        self.plan_path = rundir / "plan.json"
        self.plan = read_json(self.plan_path)
        self.events = Events(rundir)

    @property
    def settings(self) -> dict:
        return self.plan["settings"]

    def leaf(self, name: str) -> dict:
        for leaf in self.plan["leaves"]:
            if leaf["name"] == name:
                return leaf
        raise Refused('no leaf "%s" in %s' % (name, self.plan_path))

    def save_plan(self) -> None:
        write_json(self.plan_path, self.plan)

    @property
    def report_path(self) -> Path:
        return self.dir / "report.json"

    @property
    def pid_path(self) -> Path:
        return self.dir / ".state" / "monitor.pid"


def open_run(rundir: str) -> Run:
    p = Path(os.path.expanduser(rundir))
    if not (p / "plan.json").is_file():
        raise Refused("not a herd rundir (no plan.json): %s" % p)
    return Run(p)


# --------------------------------------------------------------------------- monitor

class LeafWatch:
    def __init__(self, leaf: dict, settings: dict, rundir: Path, events: Events, all_paths: set):
        self.leaf = leaf
        self.s = settings
        self.rundir = rundir
        self.events = events
        self.all_paths = all_paths
        self.name = leaf["name"]
        self.pane = leaf["pane"]
        self.path = Path(leaf["path"])
        self.start = time.monotonic()
        self.last_activity = self.start
        self.fingerprint = None
        self.settle_seq = None
        self.settle_since = None
        self.nudged = False
        self.nudge_at = None
        self.missing = 0
        self.hookless = 0
        self.status = "unknown"
        self.phase = "working"
        self.done = False
        self.ok = False
        self.reason = None
        if leaf.get("dispatch_error"):
            self.finish(False, "dispatch_failed:%s" % leaf["dispatch_error"])

    def finish(self, ok: bool, reason: str | None) -> None:
        self.done, self.ok, self.reason = True, ok, reason
        self.phase = "ok" if ok else "failed"
        self.events("verdict", leaf=self.name, ok=ok, reason=reason, status=self.status)
        log("  %s: %s%s" % (self.name, "ok" if ok else "failed", "" if ok else " (%s)" % reason))

    def candidates(self) -> list:
        out = []
        for p in sorted(self.rundir.rglob("*.md")):
            if ".state" in p.relative_to(self.rundir).parts or str(p) in self.all_paths:
                continue
            out.append(str(p))
        return out

    def tick(self, herdr: Herdr, agents: dict | None) -> None:
        if self.done:
            return
        now = time.monotonic()
        ms = lambda t: (now - t) * 1000.0  # noqa: E731
        if agents is None:            # agent list failed this poll; try again next tick
            return
        a = agents.get(self.pane)
        if a is None:
            self.missing += 1
            if self.missing >= MISSING_POLLS:
                self.finish(False, "agent_gone")
            return
        self.missing = 0
        if a.get("screen_detection_skipped") is not True:
            self.hookless += 1
            if self.hookless >= MISSING_POLLS:
                self.finish(False, "hook_lost")
                return
        else:
            self.hookless = 0
        status = a.get("agent_status") or "unknown"
        if status != self.status:
            self.events("status", leaf=self.name, status=status, seq=a.get("state_change_seq"))
        self.status = status
        seq = a.get("state_change_seq")
        settled = status in SETTLED
        session = (a.get("agent_session") or {}).get("value")
        screen = "" if settled else hashlib.sha1(normalize_screen(herdr.screen(self.pane)).encode()).hexdigest()
        fp = (seq, file_sig(session), file_sig(self.path), screen)
        if fp != self.fingerprint:
            self.fingerprint = fp
            self.last_activity = now

        if ms(self.start) >= self.s["cap_ms"]:
            self.finish(False, "cap")
            return

        if not settled:
            self.settle_seq = None
            self.phase = "nudged" if self.nudged else "working"
            if ms(self.last_activity) >= self.s["inactivity_ms"]:
                self.finish(False, "blocked" if status == "blocked" else "inactive")
            return

        if seq != self.settle_seq or self.settle_since is None:
            self.settle_seq, self.settle_since = seq, now
        self.phase = "settling"
        if ms(self.settle_since) < self.s["quiesce_ms"]:
            return
        if self.nudged and ms(self.nudge_at) < max(self.s["quiesce_ms"], NUDGE_GRACE_MS):
            return
        check = file_check(self.path)
        if check == "ok":
            self.finish(True, None)
            return
        cands = self.candidates()
        if not self.nudged:
            self.nudged, self.nudge_at = True, now
            self.settle_seq = None
            self.phase = "nudged"
            err = herdr.prompt(self.pane, nudge_text(self.path, check, cands))
            self.events("nudge", leaf=self.name, check=check, candidates=cands, error=err)
            log("  %s: settled without a valid file (%s) — nudged" % (self.name, check))
            return
        self.finish(False, "misplaced:%s" % ",".join(cands) if cands else check)

    def snapshot(self) -> dict:
        return {"name": self.name, "phase": self.phase, "status": self.status, "reason": self.reason,
                "nudged": self.nudged}


def deliver(leaf: dict, events: Events) -> str | None:
    """Copy a leaf's file to its dest with marker lines removed. Returns an error string or None."""
    dest = leaf.get("dest")
    if not dest:
        return None
    try:
        text = Path(leaf["path"]).read_text()
        body = "\n".join(l for l in text.splitlines() if l.strip() != MARKER).rstrip() + "\n"
        d = Path(dest)
        existed = d.exists()
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(body)
        events("delivered", leaf=leaf["name"], dest=dest, overwrote=existed)
        return None
    except OSError as exc:
        events("deliver_failed", leaf=leaf["name"], dest=dest, error=str(exc))
        return str(exc)


def leaf_entry(leaf: dict, w: LeafWatch) -> dict:
    return {"name": leaf["name"], "agent": leaf["agent"], "pane": leaf["pane"], "status": w.status,
            "ok": w.ok, "reason": w.reason, "path": leaf["path"], "bytes": file_bytes(Path(leaf["path"])),
            "dest": leaf.get("dest"), "delivered": bool(w.ok and leaf.get("dest")),
            "nudged": w.nudged, "attempts": leaf.get("attempts", 1)}


def watch(herdr: Herdr, run: Run, names: list) -> dict:
    """Poll the named leaves to a verdict. Returns {name: LeafWatch}."""
    s = run.settings
    all_paths = {l["path"] for l in run.plan["leaves"]}
    watches = {n: LeafWatch(run.leaf(n), s, run.dir, run.events, all_paths) for n in names}
    cadence = s["cadence_ms"] / 1000.0
    log("polling %d leaf(s) (poll=%dms quiesce=%dms inactivity=%dms cap=%dms)…"
        % (len(names), s["cadence_ms"], s["quiesce_ms"], s["inactivity_ms"], s["cap_ms"]))
    while True:
        pending = [w for w in watches.values() if not w.done]
        if not pending:
            break
        agents = herdr.agents_by_pane()
        for w in pending:
            w.tick(herdr, agents)
        write_json(run.dir / "status.json", {"state": "running", "updated_at": now_iso(),
                                             "leaves": [w.snapshot() for w in watches.values()]})
        if any(not w.done for w in watches.values()):
            time.sleep(cadence)
    for n, w in watches.items():
        if w.ok:
            err = deliver(run.leaf(n), run.events)
            if err:
                w.ok, w.reason = False, "dest_failed:%s" % err
    return watches


def apply_close(herdr: Herdr, run: Run, report: dict) -> None:
    policy = run.settings["close"]
    if report.get("closed"):
        return
    if policy == "always" or (policy == "on_success" and report["ok"]):
        report["closed"] = herdr.close(run.plan["workspace"])
        run.events("closed", workspace=run.plan["workspace"], ok=report["closed"], policy=policy)


def finish_fanout(herdr: Herdr, run: Run) -> dict:
    names = [l["name"] for l in run.plan["leaves"]]
    watches = watch(herdr, run, names)
    leaves = [leaf_entry(run.leaf(n), watches[n]) for n in names]
    report = {"workspace": run.plan["workspace"], "rundir": str(run.dir),
              "ok": all(l["ok"] for l in leaves), "closed": False, "leaves": leaves}
    apply_close(herdr, run, report)
    write_json(run.report_path, report)
    write_json(run.dir / "status.json", {"state": "finished", "updated_at": now_iso(),
                                         "leaves": [w.snapshot() for w in watches.values()]})
    run.events("finished", ok=report["ok"], closed=report["closed"])
    return report


# --------------------------------------------------------------------------- fanout

def preflight(herdr: Herdr) -> None:
    if os.environ.get("HERDR_ENV") != "1":
        raise Refused("HERDR_ENV is not 1 — not inside herdr")
    depth = os.environ.get("HERDR_DEPTH") or "0"
    try:
        if int(depth) >= 2:
            raise Refused("HERDR_DEPTH=%s — leaves cannot herd (cap is depth 2)" % depth)
    except ValueError:
        raise Refused("HERDR_DEPTH=%r is not an integer" % depth)
    ok, line = herdr.pi_hook_installed()
    if not ok:
        raise Refused("herdr pi hook integration is not installed (%s) — run: herdr integration install pi" % line)


def pick_agent_name(base: str, taken: set) -> str:
    n = 1
    while True:
        suffix = "" if n == 1 else "-%d" % n
        cand = base[: AGENT_NAME_MAX - len(suffix)] + suffix
        if cand not in taken:
            return cand
        n += 1


def start_leaf(herdr: Herdr, leaf: dict, pane: str, taken: set) -> dict:
    for _ in range(12):
        name = pick_agent_name(leaf["name"], taken)
        data = herdr.call("agent", "start", name, "--kind", "pi", "--pane", pane,
                          "--timeout", str(START_TIMEOUT_MS), timeout=START_TIMEOUT_MS / 1000 + 30)
        err = data.get("error")
        if not err:
            taken.add(name)
            return data["result"]["agent"]
        code = err.get("code")
        if code == "agent_name_taken":
            taken.add(name)
            continue
        if code in ("agent_pane_busy", "agent_pane_unavailable"):
            time.sleep(1)
            continue
        if code == "timeout":
            # pi can start slower than the readiness window; accept it if it came up anyway.
            deadline = time.monotonic() + START_TIMEOUT_MS / 1000
            while time.monotonic() < deadline:
                a = (herdr.agents_by_pane() or {}).get(pane) or {}
                if a.get("name") == name:
                    taken.add(name)
                    return a
                time.sleep(2)
        raise HerdrError("agent start %s on %s failed: %s" % (name, pane, err))
    raise HerdrError("agent start for %s on %s kept failing" % (leaf["name"], pane))


def await_hook(herdr: Herdr, pane: str, agent: dict) -> bool:
    if agent.get("screen_detection_skipped") is True:
        return True
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        a = (herdr.agents_by_pane() or {}).get(pane) or {}
        if a.get("screen_detection_skipped") is True:
            return True
        time.sleep(1)
    return False


def spawn(herdr: Herdr, resolved: dict) -> Run:
    rundir = allocate_rundir(resolved["slug"])
    (rundir / ".state").mkdir()
    s = resolved["settings"]
    leaves = []
    for leaf in resolved["leaves"]:
        path = rundir / ("%s.md" % leaf["name"])
        leaves.append({"name": leaf["name"], "path": str(path), "dest": leaf["dest"],
                       "task": leaf["prompt"], "prompt": render(leaf["prompt"], path), "attempts": 1})
    plan = {"slug": rundir.name, "rundir": str(rundir), "created_at": now_iso(), "cwd": os.getcwd(),
            "settings": s, "workspace": None, "leaves": leaves}
    write_json(rundir / "plan.json", plan)
    run = Run(rundir)
    run.events("rundir", rundir=str(rundir), requested_slug=resolved["slug"])
    log("herd %s: %d leaf(s) in %s" % (rundir.name, len(leaves), rundir))

    ws = herdr.call("workspace", "create", "--cwd", os.getcwd(), "--label", rundir.name,
                    "--env", "HERDR_DEPTH=%d" % s["leaf_depth"], "--no-focus")
    if "error" in ws:
        raise HerdrError("workspace create failed: %s" % ws["error"])
    try:
        wid = ws["result"]["workspace"]["workspace_id"]
        root = ws["result"]["root_pane"]["pane_id"]
    except (KeyError, TypeError):
        raise HerdrError("could not parse workspace create response: %s" % json.dumps(ws)[:500])
    run.plan["workspace"] = wid
    run.save_plan()
    run.events("workspace", workspace=wid, root_pane=root)
    log("  workspace %s" % wid)

    try:
        taken = herdr.agent_names()
        for i, leaf in enumerate(run.plan["leaves"]):
            if i == 0:
                pane = root
            else:
                sp = herdr.call("pane", "split", root, "--direction", "right",
                                "--env", "HERDR_DEPTH=%d" % s["leaf_depth"], "--no-focus")
                pane = (sp.get("result") or {}).get("pane", {}).get("pane_id")
                if not pane:
                    raise HerdrError("pane split failed: %s" % json.dumps(sp)[:500])
            agent = start_leaf(herdr, leaf, pane, taken)
            leaf["pane"], leaf["agent"] = pane, agent.get("name")
            run.save_plan()
            run.events("spawned", leaf=leaf["name"], agent=leaf["agent"], pane=pane)
            if not await_hook(herdr, pane, agent):
                raise HerdrError("leaf %s started without the herdr pi hook reporting — reinstall: "
                              "herdr integration install pi" % leaf["name"])
            log("  %s -> agent %s on %s" % (leaf["name"], leaf["agent"], pane))
    except (HerdrError, Refused):
        herdr.close(wid)
        run.events("setup_failed", workspace=wid)
        raise

    log("dispatching prompts…")
    for leaf in run.plan["leaves"]:
        leaf["dispatched_at"] = now_iso()
        err = herdr.prompt(leaf["pane"], leaf["prompt"])
        if err:
            leaf["dispatch_error"] = err
            log("  warn: dispatch to %s failed: %s" % (leaf["name"], err))
        run.events("dispatched", leaf=leaf["name"], error=err)
    run.save_plan()
    return run


def cmd_fanout(herdr: Herdr, plan_src: str, detach: bool) -> int:
    resolved = validate_plan(load_plan_source(plan_src))
    preflight(herdr)
    run = spawn(herdr, resolved)
    if detach:
        logf = (run.dir / ".state" / "monitor.log").open("a")
        proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--monitor", str(run.dir)],
                                stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, start_new_session=True,
                                env=os.environ.copy())
        run.pid_path.write_text(str(proc.pid))
        run.events("detached", pid=proc.pid)
        emit({"workspace": run.plan["workspace"], "rundir": str(run.dir), "detached": True,
              "leaves": [{"name": l["name"], "agent": l["agent"], "path": l["path"], "dest": l.get("dest")}
                         for l in run.plan["leaves"]]})
        return 0
    run.pid_path.write_text(str(os.getpid()))
    try:
        report = finish_fanout(herdr, run)
    finally:
        try:
            run.pid_path.unlink()
        except OSError:
            pass
    emit(report)
    return 0 if report["ok"] else 1


def cmd_monitor(herdr: Herdr, rundir: str) -> int:
    run = open_run(rundir)
    try:
        finish_fanout(herdr, run)
    finally:
        try:
            run.pid_path.unlink()
        except OSError:
            pass
    return 0


# --------------------------------------------------------------------------- status / wait / close

def monitor_alive(run: Run) -> bool:
    try:
        pid = int(run.pid_path.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_status(rundir: str) -> int:
    run = open_run(rundir)
    if run.report_path.exists():
        emit(dict(state="finished", **read_json(run.report_path)))
        return 0
    snap = read_json(run.dir / "status.json") if (run.dir / "status.json").exists() else {"leaves": []}
    state = "running" if monitor_alive(run) else "dead"
    emit({"state": state, "workspace": run.plan.get("workspace"), "rundir": str(run.dir),
          "updated_at": snap.get("updated_at"), "leaves": snap.get("leaves", [])})
    return 0


def cmd_wait(rundir: str) -> int:
    run = open_run(rundir)
    cadence = run.settings["cadence_ms"] / 1000.0
    while not run.report_path.exists():
        if not monitor_alive(run) and not run.report_path.exists():
            time.sleep(0.5)
            if not run.report_path.exists():
                log("herdr-fanout: background monitor for %s is not running and left no report "
                    "(see .state/monitor.log)" % run.dir)
                return 3
        time.sleep(cadence)
    report = read_json(run.report_path)
    emit(report)
    return 0 if report["ok"] else 1


def cmd_close(herdr: Herdr, rundir: str) -> int:
    run = open_run(rundir)
    wid = run.plan.get("workspace")
    if not wid:
        raise Refused("run has no workspace recorded")
    ok = herdr.close(wid)
    run.events("closed", workspace=wid, ok=ok, policy="manual")
    if run.report_path.exists():
        report = read_json(run.report_path)
        report["closed"] = report.get("closed") or ok
        write_json(run.report_path, report)
    emit({"workspace": wid, "closed": ok})
    return 0 if ok else 3


# --------------------------------------------------------------------------- retry

def cmd_retry(herdr: Herdr, name: str, rundir: str, prompt_src: str, overrides: dict) -> int:
    run = open_run(rundir)
    leaf = run.leaf(name)
    if not run.report_path.exists():
        raise Refused("run %s is still in progress — wait for its report (--wait/--status)" % run.dir)
    report = read_json(run.report_path)
    if report.get("closed"):
        raise Refused("workspace %s is closed; start a new herd instead" % report.get("workspace"))
    entry = next((l for l in report["leaves"] if l["name"] == name), None)
    if entry and entry.get("ok"):
        raise Refused('leaf "%s" is already ok' % name)
    agent = (herdr.agents_by_pane() or {}).get(leaf["pane"])
    if agent is None:
        raise HerdrError("leaf %s (pane %s) no longer hosts an agent" % (name, leaf["pane"]))
    status = agent.get("agent_status")
    if status not in SETTLED:
        raise Refused('leaf "%s" is %s (hook-reported) — it is still working; let it settle before retrying'
                      % (name, status))
    text = sys.stdin.read() if prompt_src == "-" else prompt_src
    validate_prompt(name, text)
    path = Path(leaf["path"])
    leaf["attempts"] = leaf.get("attempts", 1) + 1
    if path.exists():
        archive = run.dir / ".state" / "attempts" / ("%s.%d.md" % (name, leaf["attempts"] - 1))
        archive.parent.mkdir(parents=True, exist_ok=True)
        path.replace(archive)
        run.events("archived", leaf=name, to=str(archive))
    leaf["prompt"] = render(text, path)
    leaf.pop("dispatch_error", None)
    for key, val in overrides.items():
        if val is not None:
            run.settings[key] = val
    run.save_plan()
    err = herdr.prompt(leaf["pane"], leaf["prompt"])
    run.events("retry_dispatched", leaf=name, attempt=leaf["attempts"], error=err)
    if err:
        leaf["dispatch_error"] = err
    log("retrying %s (attempt %d)…" % (name, leaf["attempts"]))
    w = watch(herdr, run, [name])[name]
    new = leaf_entry(leaf, w)
    report["leaves"] = [new if l["name"] == name else l for l in report["leaves"]]
    report["ok"] = all(l["ok"] for l in report["leaves"])
    apply_close(herdr, run, report)
    write_json(run.report_path, report)
    emit(new)
    return 0 if new["ok"] else 1


# --------------------------------------------------------------------------- main

def parse(argv: list) -> dict:
    opts = {"mode": None, "detach": False, "overrides": {}}
    value_flags = {"--plan": "fanout", "--status": "status", "--wait": "wait", "--close": "close",
                   "--retry-leaf": "retry", "--monitor": "monitor"}
    int_flags = {"--cadence-ms": "cadence_ms", "--quiesce-ms": "quiesce_ms",
                 "--inactivity-ms": "inactivity_ms", "--cap-ms": "cap_ms"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            opts["mode"] = "help"
            return opts
        if a == "--detach":
            opts["detach"] = True
            i += 1
            continue
        if i + 1 >= len(argv):
            raise Refused("%s needs a value" % a)
        v = argv[i + 1]
        if a in value_flags:
            if opts["mode"]:
                raise Refused("choose one of --plan, --status, --wait, --close, --retry-leaf")
            opts["mode"], opts["arg"] = value_flags[a], v
        elif a == "--rundir":
            opts["rundir"] = v
        elif a == "--prompt":
            opts["prompt"] = v
        elif a == "--path":
            raise Refused("--path was removed — retry with --rundir <rundir>; the leaf's file is known")
        elif a == "--timeout-ms":
            raise Refused("--timeout-ms was replaced by --inactivity-ms and --cap-ms")
        elif a in int_flags:
            try:
                opts["overrides"][int_flags[a]] = int(v)
            except ValueError:
                raise Refused("%s needs an integer" % a)
        else:
            raise Refused("unknown argument: %s" % a)
        i += 2
    return opts


def main(argv: list) -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        opts = parse(argv)
        mode = opts["mode"]
        if mode in (None, "help"):
            sys.stdout.write(__doc__)
            return 0 if mode == "help" else 2
        if mode == "status":
            return cmd_status(opts["arg"])
        if mode == "wait":
            return cmd_wait(opts["arg"])
        herdr = Herdr()
        if mode == "fanout":
            return cmd_fanout(herdr, opts["arg"], opts["detach"])
        if mode == "monitor":
            return cmd_monitor(herdr, opts["arg"])
        if mode == "close":
            return cmd_close(herdr, opts["arg"])
        if mode == "retry":
            if not opts.get("rundir") or "prompt" not in opts:
                raise Refused("--retry-leaf needs --rundir <rundir> and --prompt <text|->")
            return cmd_retry(herdr, opts["arg"], opts["rundir"], opts["prompt"], opts["overrides"])
        raise Refused("unknown mode")
    except Refused as exc:
        log("herdr-fanout: refused: %s" % exc)
        return 2
    except HerdrError as exc:
        log("herdr-fanout: herdr failure: %s" % exc)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
