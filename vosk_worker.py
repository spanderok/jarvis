"""Long-lived text-to-speech worker on the local vosk voice.

Same line protocol as tts_worker.py, so the daemon can use either one:
  stdin   "<text>"   speak this, queued after whatever is already queued
          "!STOP"    stop the current sentence and drop the queue
          "!QUIT"    exit
  stdout  "!SPEAKING"  the first audio of a sentence reached the speakers
          "!DONE"      that sentence is finished (spoken, failed or stopped)
          "!IDLE"      nothing left to say
          "!ERR <msg>" synthesis failed for one sentence

Runs fully offline. The model stays loaded between sentences (0.13 s to first sound
instead of 0.66 s), and the whole process exits after JARVIS_VOSK_IDLE seconds of
silence so the 380 MB go back to the system - the daemon restarts it on the next
phrase. Dropping just the model inside a living process gives back only 45 MB,
because onnxruntime keeps its arena.
"""
import hashlib
import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vosk_dict import StressDict, version as dict_version  # noqa: E402
from vosk_text import normalize_for_tts  # noqa: E402
from spoken_log import record as log_spoken  # noqa: E402
import speak_level  # noqa: E402

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(JARVIS_DIR, "models", "vosk-0.7")
CACHE_DIR = os.path.join(JARVIS_DIR, "cache-vosk")
SPEAKER = int(os.environ.get("JARVIS_VOSK_SPEAKER", "4"))
# How much the model is allowed to jitter the length of every sound. The model
# ships with 0.8, and that jitter is what ate short words: "игр" and "таба" drew a
# short duration by luck and smeared. Measured on one phrase said five times over,
# at 0.8 the length wandered by 0.56 s, at 0.2 by 0.09 s. the owner picked the steady
# version by ear on 21.08, then asked twice for more of the life back: 0.4 first,
# 0.5 after hearing a day of speech read out at 0.4.
DURATION_NOISE = float(os.environ.get("JARVIS_DURATION_NOISE", "0.5"))
# Speed of speech, 1.0 is what the model ships with. Never passed until 21.08, so
# the model default stood; the owner asked for ten percent slower after hearing the
# day read back - short words like "игр" and "таба" were smearing at full speed.
SPEECH_RATE = float(os.environ.get("JARVIS_SPEECH_RATE", "0.9"))
IDLE_UNLOAD = float(os.environ.get("JARVIS_VOSK_IDLE", "180"))
STOP_FILE = os.path.join(JARVIS_DIR, "speak.stop")
CACHE_LIMIT = 400  # phrases kept on disk; a wav of one sentence is about 300 KB


def emit(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


class Voice:
    """The model, loaded on the first phrase and kept while the talking continues."""

    def __init__(self):
        self.synth = None
        self.lock = threading.Lock()
        self.last_used = time.monotonic()

    def _load(self):
        from vosk_tts import Model, Synth
        model = Model(model_path=MODEL_DIR)
        model.dic = StressDict()
        return Synth(model)

    def to_file(self, text: str, path: str) -> None:
        with self.lock:
            if self.synth is None:
                self.synth = self._load()
            self.last_used = time.monotonic()
            self.synth.synth(text, path, speaker_id=SPEAKER,
                             duration_noise_level=DURATION_NOISE,
                             speech_rate=SPEECH_RATE)

    def idle_for(self) -> float:
        return time.monotonic() - self.last_used


DICT_VERSION = dict_version()


def cache_path(text: str) -> str:
    key = hashlib.sha1(f"{text}|{SPEAKER}|0.7|{DICT_VERSION}|{DURATION_NOISE}|{SPEECH_RATE}".encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.wav")


def trim_cache() -> None:
    files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".wav")]
    if len(files) <= CACHE_LIMIT:
        return
    files.sort(key=os.path.getatime)
    for path in files[:len(files) - CACHE_LIMIT]:
        try:
            os.unlink(path)
        except OSError:
            pass


class Worker:
    """Synthesis runs ahead of playback, so consecutive chunks join without a gap.

    Until 21.08 one loop did both in turn: synthesise, play to the end, synthesise
    the next. A fresh 220-character chunk takes about a second to synthesise, and
    that second was pure silence in the middle of a sentence - the owner heard the
    answer arrive "in pieces". Now the main thread synthesises the next chunk while
    a background thread plays the current one.

    Which thread does what is not a free choice: onnxruntime aborts the process
    with "recursive_mutex lock failed" if synthesis runs off the main thread, so
    playback is the half that moves - the same arrangement vosk_say.py already uses.
    """

    def __init__(self):
        self.queue: queue.Queue[tuple[str, float] | None] = queue.Queue()
        self.ready: queue.Queue[tuple[str, float] | None] = queue.Queue()
        self.voice = Voice()
        self.player: subprocess.Popen | None = None
        self.stopping = False
        os.makedirs(CACHE_DIR, exist_ok=True)

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
            # the stamp is taken when the phrase is queued, not when it is spoken:
            # with synthesis running ahead the two moments are seconds apart, and
            # the stop file has to be judged against the earlier one
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
        """The hotkey wrote the stop file after this phrase was queued."""
        try:
            return os.path.getmtime(STOP_FILE) > queued_at
        except OSError:
            return False

    # --- synthesis: main thread --------------------------------------------

    def synth(self, text: str) -> str | None:
        """Text -> a wav on disk. Returns None if the phrase was spoken already."""
        raw, text = text, normalize_for_tts(text)
        if not text:
            return None
        log_spoken(raw, text, "jarvis")
        path = cache_path(text)
        if os.path.exists(path):
            os.utime(path, None)
            return path
        try:
            self.voice.to_file(text, path + ".tmp")
            os.replace(path + ".tmp", path)
            trim_cache()
            return path
        except Exception as e:
            # say it with the system voice rather than swallow the phrase
            emit(f"!ERR {e}")
            subprocess.run(["say", "-v", "Yuri", text],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None

    # --- playback: background thread ---------------------------------------

    def player_loop(self) -> None:
        while True:
            item = self.ready.get()
            if item is None:
                self.kill_player()
                return
            path, queued_at = item
            if self.stopping or self.stopped(queued_at):
                emit("!DONE")
                self.maybe_idle()
                continue
            speak_level.warm_if_cold()
            started = time.time()
            self.player = subprocess.Popen(["afplay", path],
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
            emit("!SPEAKING")
            # the badge draws the phrase it cannot hear; computed after the sound
            # is already out, so it costs the listener nothing
            speak_level.publish(path, started)
            while self.player is not None and self.player.poll() is None:
                if self.stopped(queued_at):
                    self.kill_player()
                    break
                time.sleep(0.03)
            self.player = None
            speak_level.mark_played()
            speak_level.clear()
            emit("!DONE")
            self.maybe_idle()

    def maybe_idle(self) -> None:
        if self.queue.empty() and self.ready.empty():
            emit("!IDLE")

    def housekeeping(self) -> None:
        """Quit when the room has been quiet for a while; the daemon starts us again."""
        while True:
            time.sleep(2)
            if IDLE_UNLOAD <= 0 or self.player is not None:
                continue
            if (self.queue.empty() and self.ready.empty()
                    and self.voice.idle_for() > IDLE_UNLOAD):
                os._exit(0)

    def run(self) -> None:
        threading.Thread(target=self.read_stdin, daemon=True).start()
        threading.Thread(target=self.player_loop, daemon=True).start()
        threading.Thread(target=self.housekeeping, daemon=True).start()
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
            path = self.synth(text)
            if path is None:
                emit("!DONE")
                self.maybe_idle()
                continue
            self.ready.put((path, queued_at))


Worker().run()
