"""One-shot local voice: text -> speakers, no network, no permanent process.

Usage:
  vosk_say.py "text"              speak it
  vosk_say.py --to out.wav "..."  write the wav instead of playing

Long text is cut into sentences: the first one starts playing while the rest are
still being synthesised, so the wait before the first sound stays about 0.9 s
regardless of how long the report is.
"""
import hashlib
import os
import queue
import re
import subprocess
import sys
import threading
import time

START = time.time()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vosk_dict import StressDict, version as dict_version  # noqa: E402
from vosk_text import normalize_for_tts  # noqa: E402
from spoken_log import record as log_spoken  # noqa: E402
import speak_level  # noqa: E402

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(JARVIS_DIR, "models", "vosk-0.7")
CACHE_DIR = os.path.join(JARVIS_DIR, "cache-vosk")
STOP_FILE = os.path.join(JARVIS_DIR, "speak.stop")
# Touched the moment the first sound goes out. speak.sh looks at it to decide
# whether the network fallback is still needed: without it a crash *after* the
# phrase had played made the fallback voice say everything a second time.
SPOKEN_FILE = os.path.join(JARVIS_DIR, "last_spoken")
SPEAKER = int(os.environ.get("JARVIS_VOSK_SPEAKER", "4"))
# How much the model is allowed to jitter the length of every sound. The model
# ships with 0.8, and that jitter is what ate short words: one-syllable ones drew a
# short duration by luck and smeared. Measured on one phrase said five times over,
# at 0.8 the length wandered by 0.56 s, at 0.2 by 0.09 s. the owner picked the steady
# version by ear on 21.08, then asked twice for more of the life back: 0.4 first,
# 0.5 after hearing a day of speech read out at 0.4.
DURATION_NOISE = float(os.environ.get("JARVIS_DURATION_NOISE", "0.5"))
# Speed of speech, 1.0 is what the model ships with. Never passed until 21.08, so
# the model default stood; the owner asked for ten percent slower after hearing the
# day read back - short one-syllable words were smearing at full speed.
SPEECH_RATE = float(os.environ.get("JARVIS_SPEECH_RATE", "0.9"))
CHUNK_LIMIT = 220  # characters; longer chunks delay the first sound without sounding better
DEBUG = os.environ.get("JARVIS_TTS_DEBUG") == "1"
DICT_VERSION = dict_version()


def tick(stage: str) -> None:
    if DEBUG:
        sys.stderr.write(f"[{time.time() - START:5.2f}s] {stage}\n")
        sys.stderr.flush()


def stopped() -> bool:
    """Did the hotkey ask for silence after this phrase started?"""
    try:
        return os.path.getmtime(STOP_FILE) > START
    except OSError:
        return False


def cache_path(text: str) -> str:
    key = hashlib.sha1(f"{text}|{SPEAKER}|0.7|{DICT_VERSION}|{DURATION_NOISE}|{SPEECH_RATE}".encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.wav")


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for part in parts:
        if len(current) + len(part) + 1 > CHUNK_LIMIT and current:
            chunks.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        chunks.append(current)
    return chunks or [text]


def mark_spoken() -> None:
    try:
        with open(SPOKEN_FILE, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def play_in_background(ready: "queue.Queue") -> threading.Thread:
    """Playback is the safe half to move off the main thread - it is a subprocess.

    Synthesis is not: onnxruntime aborted the whole process with
    "recursive_mutex lock failed" when it ran in a worker thread while the main
    one waited on afplay.
    """
    def loop() -> None:
        while True:
            path = ready.get()
            if path is None or stopped():
                speak_level.clear()
                return
            speak_level.warm_if_cold()
            started = time.time()
            player = subprocess.Popen(["afplay", path],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            # after the sound is already out: the pass over the samples takes
            # about 50 ms and must not be added to the wait for the first word
            speak_level.publish(path, started)
            player.wait()
            speak_level.mark_played()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def main() -> None:
    args = sys.argv[1:]
    out_file = None
    if args and args[0] == "--to":
        out_file = args[1]
        args = args[2:]
    text = " ".join(args).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        return

    raw, text = text, normalize_for_tts(text)
    if not text:
        return
    # written down before anything is synthesised or replayed from cache, so the
    # corpus holds every phrase that reached the speakers, not just the new ones
    log_spoken(raw, text, "say")
    os.makedirs(CACHE_DIR, exist_ok=True)
    chunks = split_sentences(text)
    paths = [cache_path(c) for c in chunks]

    # A fully cached phrase never touches the model: 359 MB stay unallocated.
    if out_file is None and all(os.path.exists(p) for p in paths):
        tick("found in the cache, playing")
        mark_spoken()
        for path in paths:
            if stopped():
                speak_level.clear()
                return
            os.utime(path, None)
            speak_level.warm_if_cold()
            started = time.time()
            player = subprocess.Popen(["afplay", path],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            speak_level.publish(path, started)
            player.wait()
            speak_level.mark_played()
        speak_level.clear()
        return

    from vosk_tts import Model, Synth
    tick("library loaded")
    model = Model(model_path=MODEL_DIR)
    model.dic = StressDict()
    synth = Synth(model)
    tick("model ready")

    if out_file is not None:
        synth.synth(text, out_file, speaker_id=SPEAKER,
                    duration_noise_level=DURATION_NOISE,
                             speech_rate=SPEECH_RATE)
        return

    ready: "queue.Queue" = queue.Queue()
    player = play_in_background(ready)

    for index, (chunk, path) in enumerate(zip(chunks, paths)):
        if stopped():
            break
        if not os.path.exists(path):
            try:
                synth.synth(chunk, path + ".tmp", speaker_id=SPEAKER,
                             duration_noise_level=DURATION_NOISE,
                             speech_rate=SPEECH_RATE)
                os.replace(path + ".tmp", path)
            except Exception as e:
                # never fail silently: a lost phrase looks like Jarvis ignoring you
                sys.stderr.write(f"synthesis failed: {e}\n")
                subprocess.run(["say", "-v", "Yuri", chunk],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                continue
        if index == 0:
            tick("first sound")
            mark_spoken()
        ready.put(path)
    ready.put(None)
    player.join()


main()
