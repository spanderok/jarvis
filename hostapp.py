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


if __name__ == "__main__":
    print(name())
