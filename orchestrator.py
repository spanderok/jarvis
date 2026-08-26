# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = []
# ///
"""Talk to the interactive orchestrator session instead of a private one.

Voice text is typed into the Terminal window that runs `claude` in
~/claude-orchestrator, exactly as if the owner typed it - so the whole dialogue
stays visible in that window and there is only one Claude with one context.
The reply is read back off the pane so it can be spoken.

Standalone check:
  uv run ~/.claude/jarvis/orchestrator.py "скажи одно слово: тест"
"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pane_filter import speakable

ORCH_DIR = os.environ.get("JARVIS_ORCH_DIR", os.path.expanduser("~/claude-orchestrator"))
SETTLE_SEC = 1.5     # output is finished once nothing changes for this long
MAX_WAIT_SEC = 300
# lines the TUI draws around the conversation


def osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def find_window() -> str:
    """Terminal window id whose shell runs claude in the orchestrator dir."""
    ids = osa('tell application "Terminal" to get id of every window')
    for wid in [w.strip() for w in ids.split(",") if w.strip()]:
        tty = osa(f'tell application "Terminal" to get tty of tab 1 of window id {wid}')
        if not tty:
            continue
        dev = tty.replace("/dev/", "")
        ps = subprocess.run(["ps", "-t", dev, "-o", "pid=,command="],
                            capture_output=True, text=True).stdout
        for line in ps.strip().split("\n"):
            if "claude" not in line:
                continue
            pid = line.split()[0]
            lsof = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                                  capture_output=True, text=True).stdout
            if any(l[1:] == ORCH_DIR for l in lsof.split("\n") if l.startswith("n")):
                return wid
    return ""


def pane(wid: str) -> str:
    return osa(f'tell application "Terminal" to get history of tab 1 of window id {wid}')


def send(wid: str, text: str) -> None:
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    osa(f'tell application "Terminal" to do script "{safe}" in window id {wid}')


def ask(text: str, wid: str = "") -> tuple[str, str]:
    """Send text, wait for the answer to settle, return (reply, window id)."""
    wid = wid or find_window()
    if not wid:
        return "", ""
    before = pane(wid)
    send(wid, text)
    last, stable_since = "", 0.0
    deadline = time.monotonic() + MAX_WAIT_SEC
    while time.monotonic() < deadline:
        time.sleep(0.4)
        now = pane(wid)
        if now == last:
            if stable_since and time.monotonic() - stable_since >= SETTLE_SEC:
                break
        else:
            last, stable_since = now, time.monotonic()
    # everything the pane gained after our own prompt line
    tail = last[len(before):] if last.startswith(before) else last
    idx = tail.rfind(text)
    if idx >= 0:
        tail = tail[idx + len(text):]
    return speakable(tail), wid


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "скажи одно слово: тест"
    t0 = time.monotonic()
    reply, wid = ask(q)
    print(f"window={wid or 'НЕ НАЙДЕНО'}  {time.monotonic() - t0:.1f}s")
    print(f"reply={reply!r}")
