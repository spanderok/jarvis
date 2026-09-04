"""Which application a script is really running inside.

Every macOS permission - the microphone, Input Monitoring - is granted to an
application, never to a script: the terminal, or the IDE whose terminal panel is
being used. Telling somebody to "grant your terminal access" leaves them
guessing which one that is, and guessing wrong is silent - the permission list
happily holds an app that never asks for anything.

So the app is found rather than described: walk up the process chain and keep
the outermost .app bundle on the way to launchd.
"""
from __future__ import annotations

import os
import re
import subprocess


def name(pid: int | None = None) -> str:
    """The app to grant permissions to, e.g. "Terminal", "iTerm2", "WebStorm".

    The answer is remembered in the environment, because the process chain is
    not always there to be walked: a daemon started with nohup is re-parented to
    launchd the moment its shell exits, and after that the walk finds nothing.
    Whoever looked first passes the answer on to everything they start.
    """
    remembered = os.environ.get("JARVIS_HOST_APP", "").strip()
    if remembered:
        return remembered
    pid = pid or os.getpid()
    found = ""
    for _ in range(20):                      # a chain is a dozen deep at most
        if not pid or pid <= 1:
            break
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True,
                                 timeout=3).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            break
        if not out:
            break
        ppid, _, comm = out.partition(" ")
        match = re.search(r"/([^/]+)\.app/", comm)
        if match:
            found = match.group(1)           # keep going: the outer one wins
        try:
            pid = int(ppid.strip())
        except ValueError:
            break
    if found:
        os.environ["JARVIS_HOST_APP"] = found
    return found or "your terminal"


def can_watch_keys() -> str:
    """"granted", "MISSING" or "unknown" - may this app see key presses?

    Asked of the Input Monitoring toggle itself, because the obvious source
    lies: pynput's Listener.IS_TRUSTED is a class attribute that stays False
    until a listener has actually started, so reading it beforehand always
    answers "no permission" however the settings look. IOHIDCheckAccess is the
    toggle, and it answers before anything is started.
    """
    try:
        import ctypes
        import ctypes.util
        path = (ctypes.util.find_library("IOKit")
                or "/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit = ctypes.CDLL(path)
        iokit.IOHIDCheckAccess.restype = ctypes.c_int
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
        # kIOHIDRequestTypeListenEvent = 1
        state = iokit.IOHIDCheckAccess(1)
    except Exception:
        return "unknown"
    # 0 granted, 1 denied, 2 nobody has asked yet - only a denial is a no
    return {0: "granted", 1: "MISSING"}.get(state, "unknown")


if __name__ == "__main__":
    print(name())
    print(can_watch_keys())
