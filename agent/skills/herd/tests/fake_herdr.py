#!/usr/bin/env python3
"""Fake `herdr` for the herdr-fanout tests.

State lives in $FAKE_HERDR_STATE (JSON); behaviour comes from $FAKE_HERDR_SCENARIO (JSON):
  {
    "integration": "pi: current (v9) (...)",   # `integration status` pi line
    "hook": true,                               # screen_detection_skipped for started leaves
    "taken_names": ["spec"],                    # names already held by live agents elsewhere
    "leaves": {"<leaf>": [[<actions for dispatch 1>], [<dispatch 2>], ...]}
  }
Actions run relative to the time of the leaf's Nth prompt (the leaf is recovered from the
result path the prompt names):
  {"at": 0.1, "status": "working"|"idle"|"blocked"}
  {"at": 0.2, "write": "expected"|"nomarker"|"misplaced"}
  {"at": 0.0, "screen": "tick"|"static"}
"""
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

MARKER = "<!-- herd:done -->"
STATE = Path(os.environ["FAKE_HERDR_STATE"])
SCENARIO = json.loads(Path(os.environ["FAKE_HERDR_SCENARIO"]).read_text())
PATH_RES = [re.compile(r"Herd result file: `([^`]+)`"), re.compile(r"reads exactly one file: `([^`]+)`")]


def out(obj, code=0):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.exit(code)


def err(code, message):
    out({"error": {"code": code, "message": message}}, 1)


def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    agents = {}
    for i, n in enumerate(SCENARIO.get("taken_names", [])):
        agents["w0:p%d" % i] = {"name": n, "workspace": "w0", "status": "idle", "seq": 1, "leaf": None,
                                "prompts": [], "applied": [], "screen": "static", "hook": True}
    return {"ws": 0, "panes": {}, "agents": agents, "closed": [], "calls": []}


def advance(st):
    now = time.time()
    for pane, a in st["agents"].items():
        if not a["prompts"] or not a["leaf"]:
            continue
        k = len(a["prompts"]) - 1
        script = SCENARIO.get("leaves", {}).get(a["leaf"], [])
        actions = script[k] if k < len(script) else []
        elapsed = now - a["prompts"][k]
        for i, act in enumerate(actions):
            key = "%d:%d" % (k, i)
            if key in a["applied"] or act.get("at", 0) > elapsed:
                continue
            a["applied"].append(key)
            if "status" in act and act["status"] != a["status"]:
                a["status"] = act["status"]
                a["seq"] += 1
            if "screen" in act:
                a["screen"] = act["screen"]
            if "write" in act:
                path = Path(a["path"])
                kind = act["write"]
                if kind == "expected":
                    path.write_text("# %s findings\n\nbody\n\n%s\n" % (a["leaf"], MARKER))
                elif kind == "nomarker":
                    path.write_text("# %s findings\n\nbody\n" % a["leaf"])
                elif kind == "misplaced":
                    (path.parent / ("%s-findings.md" % a["leaf"])).write_text("# misplaced\n\n%s\n" % MARKER)


def main(argv):
    lock = open(str(STATE) + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    st = load()
    st["calls"].append(argv[:2])
    advance(st)
    try:
        return dispatch(st, argv)
    finally:
        STATE.write_text(json.dumps(st))


def dispatch(st, argv):
    cmd = tuple(argv[:2])
    if cmd == ("integration", "status"):
        sys.stdout.write(SCENARIO.get("integration", "pi: current (v9) (/x/herdr-agent-state.ts)") + "\n")
        sys.stdout.write("omp: not installed (/x)\n")
        return 0
    if cmd == ("workspace", "create"):
        st["ws"] += 1
        w = "w%d" % st["ws"]
        pane = "%s:p1" % w
        st["panes"][pane] = w
        out({"result": {"workspace": {"workspace_id": w}, "root_pane": {"pane_id": pane}}})
    if cmd == ("pane", "split"):
        w = st["panes"][argv[2]]
        pane = "%s:p%d" % (w, sum(1 for p in st["panes"] if p.startswith(w + ":")) + 1)
        st["panes"][pane] = w
        out({"result": {"pane": {"pane_id": pane}}})
    if cmd == ("agent", "start"):
        name, pane = argv[2], argv[argv.index("--pane") + 1]
        if any(a["name"] == name for a in st["agents"].values()):
            err("agent_name_taken", "agent name %s is already used" % name)
        hook = SCENARIO.get("hook", True)
        st["agents"][pane] = {"name": name, "workspace": st["panes"][pane], "status": "idle", "seq": 1,
                              "leaf": None, "path": None, "prompts": [], "applied": [], "screen": "static",
                              "hook": hook}
        if SCENARIO.get("start_timeout"):
            err("timeout", "timed out waiting for agent startup")   # yet the agent did come up
        out({"result": {"agent": {"name": name, "pane_id": pane, "agent_status": "idle",
                                  "state_change_seq": 1, "screen_detection_skipped": hook}}})
    if cmd == ("agent", "list"):
        agents = [{"name": a["name"], "pane_id": p, "agent_status": a["status"],
                   "state_change_seq": a["seq"], "screen_detection_skipped": a["hook"],
                   "agent_session": {"source": "herdr:pi", "value": "/nonexistent/session.jsonl"}}
                  for p, a in st["agents"].items()]
        out({"result": {"agents": agents}})
    if cmd == ("agent", "read"):
        a = st["agents"].get(argv[2])
        if a is None:
            err("agent_not_found", argv[2])
        sys.stdout.write("screen %s\n" % (time.time() if a["screen"] == "tick" else "static"))
        return 0
    if cmd == ("agent", "prompt"):
        pane, text = argv[2], argv[3]
        a = st["agents"].get(pane)
        if a is None:
            err("agent_not_found", pane)
        for rx in PATH_RES:
            m = rx.search(text)
            if m:
                a["path"] = m.group(1)
                a["leaf"] = Path(m.group(1)).stem
                break
        a["prompts"].append(time.time())
        a.setdefault("texts", []).append(text)
        out({"result": {"type": "prompt_submitted"}})
    if cmd == ("workspace", "close"):
        w = argv[2]
        st["closed"].append(w)
        st["agents"] = {p: a for p, a in st["agents"].items() if a["workspace"] != w}
        out({"result": {"type": "ok"}})
    err("unsupported", " ".join(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
