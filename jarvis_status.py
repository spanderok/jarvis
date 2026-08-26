# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["rumps"]
# ///
"""Menu bar indicator for the Jarvis daemon.

Shows what Jarvis is doing right now, reading the state file the daemon writes:
  😴 waiting for the wake word     🎤 listening to you
  🤔 thinking (ASR or Claude)      🗣️ speaking
  ⛔ daemon is not running

The menu has Start / Stop / Interrupt, so the daemon can be driven by mouse too.
Run it in your own terminal (it needs a GUI session):
  uv run ~/.claude/jarvis/jarvis_status.py
"""
import os
import pathlib
import subprocess

import rumps

JARVIS_DIR = pathlib.Path(os.path.expanduser("~/.claude/jarvis"))
STATE_FILE = JARVIS_DIR / "state"
DAEMON = str(JARVIS_DIR / "jarvisd.sh")
LOG = JARVIS_DIR / "daemon.log"

TITLES = {
    "idle": "😴",
    "listening": "🎤",
    "thinking": "🤔",
    "speaking": "🗣️",
    "off": "⛔",
}
LABELS = {
    "idle": "waiting for the wake word",
    "listening": "listening to you",
    "thinking": "thinking",
    "speaking": "talking",
    "off": "not running",
}


def daemon_pid() -> str:
    """Pid of a live Jarvis - the assistant or an agent's listener.

    Read from pid files, not from `pgrep -f jarvis_daemon.py`: that pattern also
    matches the checking command itself and the kill loop of /assist-off, and the
    indicator flashed a stale state because of it.
    """
    for name in ("daemon.pid", "listener.pid"):
        try:
            pid = int((JARVIS_DIR / name).read_text().strip())
        except (OSError, ValueError):
            continue
        try:
            os.kill(pid, 0)
            return str(pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            return str(pid)
    return ""


class JarvisStatus(rumps.App):
    def __init__(self):
        super().__init__("⛔", quit_button=None)
        self.status_item = rumps.MenuItem("...")
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Switch to me", callback=self.on_tap),
            rumps.MenuItem("Stand down (reset)", callback=self.on_cancel),
            None,
            rumps.MenuItem("Start the daemon", callback=self.on_start),
            rumps.MenuItem("Stop the daemon", callback=self.on_stop),
            rumps.MenuItem("Open the log", callback=self.on_log),
            None,
            rumps.MenuItem("Quit (indicator only)", callback=rumps.quit_application),
        ]

    @rumps.timer(0.4)
    def refresh(self, _) -> None:
        pid = daemon_pid()
        if not pid:
            state = "off"
        else:
            try:
                state = STATE_FILE.read_text().strip() or "idle"
            except OSError:
                state = "idle"
            if state not in TITLES:
                state = "idle"
        self.title = TITLES[state]
        suffix = f" (pid {pid})" if pid else ""
        self.status_item.title = f"Jarvis: {LABELS[state]}{suffix}"

    def on_tap(self, _) -> None:
        self._signal("-USR1")  # one press: wake, or interrupt and listen

    def on_cancel(self, _) -> None:
        self._signal("-USR2")  # two presses: drop everything

    def on_start(self, _) -> None:
        if daemon_pid():
            return
        subprocess.Popen(["bash", DAEMON], start_new_session=True,
                         stdout=open(LOG, "w"), stderr=subprocess.STDOUT)

    def on_stop(self, _) -> None:
        subprocess.run(["pkill", "-f", "jarvis_daemon.py"], capture_output=True)

    def on_log(self, _) -> None:
        subprocess.Popen(["open", "-a", "Console", str(LOG)])

    @staticmethod
    def _signal(sig: str) -> None:
        pid = daemon_pid()
        if pid:
            subprocess.run(["kill", sig, pid], capture_output=True)


JarvisStatus().run()
