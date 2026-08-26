#!/usr/bin/env python3
"""What every live Claude session on this machine is doing right now.

Claude Code keeps a registry in ~/.claude/sessions/<pid>.json: name, cwd,
busy/idle and the moment that status last changed. Reading it beats asking the
orchestrator - the answer costs no tokens, needs no window and works the same
for sessions started from WebStorm, iTerm or Terminal.

The last thing a session said comes from its transcript,
~/.claude/projects/<slug>/<sessionId>.jsonl, tail only.

Usage: agents_status.py [name-or-word ...]
Without arguments - every session. With arguments - only those whose name,
folder or last line matches, so "review" picks out the session doing one.
"""

import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
SESSIONS = os.path.join(HOME, ".claude", "sessions")
PROJECTS = os.path.join(HOME, ".claude", "projects")
# folders whose session speaks for a known role - the owner calls them by these names
# A room started by room.sh carries its own name, so its session name is
# already the spoken word - only a window raised by hand in a known folder
# needs mapping.
# Add your own here, or leave it empty and start rooms with room.sh.
ROLES: dict[str, str] = {}
# Jarvis himself: reporting his own session as an agent would be noise
SELF_DIRS = {os.path.join(HOME, ".claude", "jarvis", "session")}
# Big enough to hold a whole working session: a subagent spawned an hour ago is
# only visible if its Agent call is still inside the slice we read.
TAIL_BYTES = 600_000


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def slug(cwd: str) -> str:
    """Transcript folder name: every non-alphanumeric character becomes a dash."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def tail_events(sid: str, cwd: str) -> list[dict]:
    """Parsed tail of that session's transcript, oldest first."""
    path = os.path.join(PROJECTS, slug(cwd), f"{sid}.jsonl")
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                f.readline()  # drop the half line the seek landed in
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def subagents(events: list[dict]) -> list[dict]:
    """Subagents this session spawned - the "review agent" the owner asks about.

    A subagent has no registry entry of its own: the only trace is the Agent
    tool call in the parent transcript. It is still running while no tool_result
    carries its id back.
    """
    calls: dict[str, dict] = {}
    done: set[str] = set()
    for ev in events:
        content = ev.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use" and part.get("name") in ("Agent", "Task"):
                inp = part.get("input") or {}
                calls[part.get("id")] = {
                    "type": str(inp.get("subagent_type") or "agent"),
                    "what": " ".join(str(inp.get("description") or "").split())[:120],
                }
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in calls:
                done.add(part["tool_use_id"])
    out = []
    for tid, call in calls.items():
        call["running"] = tid not in done
        out.append(call)
    running = [c for c in out if c["running"]]
    return running + [c for c in out if not c["running"]][-2:]


def last_lines(events: list[dict], limit: int = 2) -> list[str]:
    """The last things the assistant said in that session, oldest first."""
    out: list[str] = []
    for ev in reversed(events):
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            said = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and part.get("text"):
                    said.append(part["text"])
                elif part.get("type") == "tool_use":
                    # a tool call is often the only sign of what it is busy with
                    said.append(f"[{part.get('name', 'tool')}]")
            text = " ".join(said)
        else:
            text = ""
        text = " ".join(text.split())
        if not text:
            continue
        out.append(text[:400])
        if len(out) >= limit:
            break
    return list(reversed(out))


def ago(ms: int) -> str:
    if not ms:
        return "at some unknown time"
    mins = int((time.time() - ms / 1000) / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    return f"{mins // 60} h {mins % 60} min ago"


def unregistered() -> list[dict]:
    """Live claude processes the registry does not know about.

    A session started from inside another Claude Code session inherits
    CLAUDE_CODE_CHILD_SESSION and never writes ~/.claude/sessions/<pid>.json.
    Without this pass such a window is simply invisible - and that window is
    often the orchestrator, the one thing worth reporting.
    """
    import subprocess
    try:
        pids = subprocess.run(["pgrep", "-f", "bin/claude"],
                              capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for pid in pids:
        try:
            lsof = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                                  capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        cwd = next((l[1:] for l in lsof.split("\n") if l.startswith("n")), "")
        if not cwd or cwd in SELF_DIRS:
            continue
        folder = os.path.basename(cwd.rstrip("/")) or cwd
        out.append({
            "pid": int(pid),
            "name": f"session in {folder}",
            "cwd": cwd,
            "folder": folder,
            "role": ROLES.get(folder),
            "status": "alive",
            "since": 0,
            "started": 0,
            # no session id, so there is no transcript to read
            "said": ["not in the session registry, so what it is doing is not visible"],
            "subagents": [],
        })
    return out


def collect() -> list[dict]:
    out = []
    try:
        names = os.listdir(SESSIONS)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS, fn)) as f:
                s = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        pid, cwd, sid = s.get("pid"), s.get("cwd", ""), s.get("sessionId", "")
        if not pid or not alive(int(pid)) or cwd in SELF_DIRS:
            continue
        events = tail_events(sid, cwd)
        out.append({
            "pid": pid,
            "name": s.get("name") or f"session {pid}",
            "cwd": cwd,
            "folder": os.path.basename(cwd.rstrip("/")) or cwd,
            "role": ROLES.get(os.path.basename(cwd.rstrip("/"))),
            "status": s.get("status", "?"),
            "since": s.get("statusUpdatedAt") or s.get("updatedAt") or 0,
            "started": s.get("startedAt") or 0,
            "said": last_lines(events),
            "subagents": subagents(events),
        })
    known = {x["pid"] for x in out}
    out += [x for x in unregistered() if x["pid"] not in known]
    out.sort(key=lambda x: (x["status"] != "busy", -(x["since"] or 0)))
    return out


def matches(sess: dict, words: list[str]) -> bool:
    """Does this session look like the one the question is about?

    Compared by word stems, not by substring: the spoken word comes in any case
    ("what is the chief up to"), while the registry holds the plain name.
    """
    if not words:
        return True
    hay = re.findall(r"[\w-]+", " ".join(
        [sess["name"], sess["folder"], sess["role"] or "",
         " ".join(sess["said"]),
         " ".join(f"{a['type']} {a['what']}" for a in sess["subagents"])]).lower())
    for w in (x.lower() for x in words):
        for h in hay:
            if h == w or h.startswith(w[:4]) or w.startswith(h[:4]):
                return True
    return False


def microphone_line() -> str:
    """Who holds the microphone - the answer to "which agent has Jarvis right now".

    The listener writes its pid and the name of the session that armed it, so the
    question is answerable without looking at the screen badge.
    """
    jarvis = os.path.join(HOME, ".claude", "jarvis")
    try:
        pid = int(open(os.path.join(jarvis, "listener.pid")).read().strip())
    except (OSError, ValueError):
        return "The microphone is free: no session has Jarvis switched on."
    if not alive(pid):
        return "The microphone is free: a listener was on record, but its process is dead."
    try:
        owner = open(os.path.join(jarvis, "listener.owner")).read().strip()
    except OSError:
        owner = ""
    who = f"session {owner!r}" if owner else f"process {pid}"
    return f"The microphone is held by {who}, and Jarvis is switched on there."


def main() -> None:
    words = [w for w in sys.argv[1:] if w.strip()]
    print(microphone_line())
    sessions = collect()
    picked = [s for s in sessions if matches(s, words)]
    if not sessions:
        print("No live Claude sessions.")
        return
    if not picked:
        print(f"Nothing matched the question. Live sessions: {len(sessions)} - "
              + ", ".join(f"{s['role'] or s['name']} ({s['status']})" for s in sessions))
        return
    for s in picked:
        who = s["role"] or s["name"]
        state = {"busy": "working", "idle": "idle"}.get(s["status"], s["status"])
        head = f"{who} (dir {s['folder']}): {state}"
        if s["since"]:
            head += f", in that state since {ago(s['since'])}"
        print(head)
        for a in s["subagents"]:
            state = "working" if a["running"] else "finished"
            what = f" «{a['what']}»" if a["what"] else ""
            print(f"  subagent {a['type']}{what}: {state}")
        for line in s["said"]:
            print(f"  last: {line}")
    # a word that matched nothing is an answer too: "I see no review agent"
    missing = [w for w in words if not any(matches(x, [w]) for x in sessions)]
    if missing:
        print("nothing matched these words: " + ", ".join(missing))
    skipped = len(sessions) - len(picked)
    if skipped > 0:
        print(f"Other sessions ({skipped}): "
              + ", ".join(f"{s['role'] or s['name']} - {s['status']}"
                          for s in sessions if s not in picked))


if __name__ == "__main__":
    main()
