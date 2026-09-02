# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["piper-tts", "numpy<2", "tomli; python_version < '3.11'"]
# ///
"""Long-lived text-to-speech worker on the local piper voice.

Same line protocol as tts_worker.py and vosk_worker.py, so the daemon can use
any of the three:
  stdin   "<text>"   speak this, queued after whatever is already queued
          "!STOP"    stop the current sentence and drop the queue
          "!QUIT"    exit
  stdout  "!SPEAKING"  the first audio of a sentence reached the speakers
          "!DONE"      that sentence is finished (spoken, failed or stopped)
          "!IDLE"      nothing left to say
          "!ERR <msg>" synthesis failed for one sentence

Runs fully offline. Synthesis is piper_say.synth - one subprocess per phrase,
about 0.3 s on Apple silicon - and it runs ahead of playback, so the next
sentence is ready on disk while the current one is still being heard. Short
phrases are cached by piper_say, so "Listening." costs a disk read.

Which voice: JARVIS_VOICE, else the locale's tts_voice. The model file has to
be in models/piper/, which is where install.sh puts it.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import piper_say  # noqa: E402
from spoken_log import record as log_spoken  # noqa: E402

try:
    import speak_level  # noqa: E402  (the badge animation; optional)
except Exception:  # numpy missing, or the file gone - the voice still works
    speak_level = None

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
STOP_FILE = os.path.join(JARVIS_DIR, "speak.stop")


def emit(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def system_voice() -> str:
    """The macOS voice for the last resort, from the locale."""
    try:
        import lang
        return lang.current().system_voice or "Daniel"
    except Exception:
        return "Daniel"


class Worker:
    """Synthesis runs ahead of playback, so consecutive chunks join without a gap."""

    def __init__(self) -> None:
        self.queue: queue.Queue[tuple[str, float] | None] = queue.Queue()
        self.ready: queue.Queue[tuple[str, float, bool] | None] = queue.Queue()
        self.player: subprocess.Popen | None = None
        self.stopping = False
        self.voice = piper_say.voice_name()
        piper_say.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # --- stdin ------------------------------------------------------------

    def read_stdin(self) -> None:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line:
                continue
            if line == "!STOP":
                self.stopping = True
                self.kill_player()
                self.drain()
                self.stopping = False
                emit("!IDLE")
                continue
            if line == "!QUIT":
                break
            # stamped when queued, not when spoken: synthesis runs ahead, and
            # the stop file has to be judged against the earlier moment
            self.queue.put((line, time.time()))
        self.queue.put(None)

    def drain(self) -> None:
        for q in (self.queue, self.ready):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    def kill_player(self) -> None:
        p, self.player = self.player, None
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def stopped(self, queued_at: float) -> bool:
        """The key wrote the stop file after this phrase was queued."""
        try:
            return os.path.getmtime(STOP_FILE) > queued_at
        except OSError:
            return False

    # --- synthesis: main thread --------------------------------------------

    def synth(self, text: str) -> tuple[str, bool] | None:
        """Text -> a wav on disk. Returns (path, keep) or None if nothing to play."""
        text = " ".join(text.split())
        if not text:
            return None
        log_spoken(text, text, "jarvis")
        keep = len(text) < piper_say.CACHE_MAX_CHARS
        import hashlib
        key = hashlib.sha1(f"{self.voice}|{text}".encode()).hexdigest()
        path = piper_say.CACHE_DIR / (f"{key}.wav" if keep else f"{key}.once.wav")
        if keep and path.exists() and path.stat().st_size > 0:
            os.utime(path, None)
            return str(path), keep
        if piper_say.synth(text, self.voice, path):
            return str(path), keep
        # say it with the system voice rather than swallow the phrase
        emit("!ERR piper could not render the phrase, used the system voice")
        subprocess.run(["say", "-v", system_voice(), text],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None

    # --- playback: background thread ---------------------------------------

    def player_loop(self) -> None:
        while True:
            item = self.ready.get()
            if item is None:
                self.kill_player()
                return
            path, queued_at, keep = item
            if self.stopping or self.stopped(queued_at):
                emit("!DONE")
                self.maybe_idle()
                continue
            if speak_level:
                speak_level.warm_if_cold()
            started = time.time()
            self.player = subprocess.Popen(["afplay", path],
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
            emit("!SPEAKING")
            if speak_level:
                speak_level.publish(path, started)
            while self.player is not None and self.player.poll() is None:
                if self.stopped(queued_at):
                    self.kill_player()
                    break
                time.sleep(0.03)
            self.player = None
            if speak_level:
                speak_level.mark_played()
                speak_level.clear()
            if not keep:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            emit("!DONE")
            self.maybe_idle()

    def maybe_idle(self) -> None:
        if self.queue.empty() and self.ready.empty():
            emit("!IDLE")

    def run(self) -> None:
        threading.Thread(target=self.read_stdin, daemon=True).start()
        threading.Thread(target=self.player_loop, daemon=True).start()
        emit("!IDLE")
        while True:
            item = self.queue.get()
            if item is None:
                self.ready.put(None)
                self.kill_player()
                return
            text, queued_at = item
            if self.stopping or self.stopped(queued_at):
                emit("!DONE")
                self.maybe_idle()
                continue
            got = self.synth(text)
            if got is None:
                emit("!DONE")
                self.maybe_idle()
                continue
            path, keep = got
            self.ready.put((path, queued_at, keep))


if __name__ == "__main__":
    Worker().run()
