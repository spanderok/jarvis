"""Pause the music while Jarvis listens or talks, put it back afterwards.

Only what was actually playing is touched: if Spotify was already paused when the
wake word fired, nothing resumes it later. Apps are asked through AppleScript and
never launched - a stopped Spotify stays stopped.
"""
import os
import subprocess
import threading

APPS = [a.strip() for a in os.environ.get("JARVIS_MEDIA_APPS", "Spotify,Music").split(",") if a.strip()]
_lock = threading.Lock()
_paused: list[str] = []          # apps we paused ourselves, in order


def _osa(script: str) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _running(app: str) -> bool:
    # asking the app itself would launch it; System Events only looks at the list
    return _osa(f'tell application "System Events" to (name of processes) contains "{app}"') == "true"


def _playing(app: str) -> bool:
    return _running(app) and _osa(f'tell application "{app}" to player state as string') == "playing"


def pause(reason: str = "") -> list[str]:
    """Pause whatever is playing. Returns the apps that were actually paused."""
    with _lock:
        if _paused:
            return list(_paused)
        for app in APPS:
            if _playing(app):
                _osa(f'tell application "{app}" to pause')
                _paused.append(app)
        return list(_paused)


def resume() -> list[str]:
    """Start again only what we paused ourselves."""
    with _lock:
        back = list(_paused)
        for app in back:
            _osa(f'tell application "{app}" to play')
        _paused.clear()
        return back


def paused_apps() -> list[str]:
    with _lock:
        return list(_paused)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "resume":
        print("вернул:", ", ".join(resume()) or "нечего")
    else:
        print("поставил на паузу:", ", ".join(pause()) or "ничего не играло")
