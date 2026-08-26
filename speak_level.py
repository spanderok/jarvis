"""Envelope of the wav Jarvis is about to say, published for the overlay.

The badge cannot hear the speakers, and capturing system output would need a
loopback device. It does not have to: whoever plays the phrase already holds its
samples, so the envelope is computed once from the file and handed over with a
start time. The overlay then just looks up "where are we now".

One line: start time (unix seconds), values per second, then signed peaks in
-1..1. Cleared when playback ends, so a stale envelope never animates silence.
"""
import os
import time
import wave

LEVEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speak_level")
RATE = 32                # values per second, same as the listening wave
SCALE = 20000.0          # int16 peak that fills the slot; tts output is loud
MAX_SECONDS = 120        # a longer phrase is truncated rather than published whole


def publish(path: str, start: float | None = None) -> None:
    """Compute the envelope of one wav and write it. Never raises.

    Call it AFTER playback has started and pass the moment it started: the pass
    over the samples takes about 50 ms on an eight-second phrase, and that must
    not be added to the wait before the first sound.
    """
    try:
        import numpy as np
        with wave.open(path) as w:
            sr = w.getframerate()
            frames = w.readframes(min(w.getnframes(), sr * MAX_SECONDS))
        data = np.frombuffer(frames, dtype=np.int16)
        if not len(data):
            return
        step = max(1, sr // RATE)
        usable = (len(data) // step) * step
        if not usable:
            return
        blocks = data[:usable].reshape(-1, step)
        lo = blocks.min(axis=1).astype("int32")
        hi = blocks.max(axis=1).astype("int32")
        # signed peak: keeps the envelope and makes the trace cross the axis
        peak = np.where(hi >= -lo, hi, lo) / SCALE
        peak = np.clip(peak, -1.0, 1.0)
        with open(LEVEL_FILE, "w") as f:
            f.write(f"{start if start is not None else time.time():.3f} {RATE} "
                    + " ".join(f"{v:.3f}" for v in peak))
    except Exception:            # noqa: BLE001 - a badge must never cost a phrase
        pass


def clear() -> None:
    try:
        with open(LEVEL_FILE, "w") as f:
            f.write("0 32")
    except OSError:
        pass


WARMUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "warmup.wav")


# When the output device has been idle this long, treat it as cold and wake it
# before the phrase. eqMac (the default device, a virtual one from Bitgapp) and
# the Bluetooth headset behind it both swallow the beginning of whatever starts
# playing after a pause - the owner hears the first consonant vanish. The synthesised
# wavs carry only about 97 ms of their own leading silence, measured over the last
# eight in the cache, and that is not enough.
#
# Padding the files was the other option and was dropped: the worker gets a long
# answer as several chunks, so every chunk would gain 250 ms and the answer would
# fall apart into pieces - the very seam that was closed earlier tonight. Waking
# the device costs the pause only when there actually was one.
COLD_AFTER = float(os.environ.get("JARVIS_OUTPUT_COLD_AFTER", "2.0"))
WARMUP_MS = 400
# On disk, not in a variable: voice-answer starts a fresh vosk_say.py process for
# every phrase, so an in-memory timestamp is always zero and the warm-up fired
# every single time - measured 22.08, two phrases in a row both paid the 400 ms.
PLAYED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_played")


def mark_played() -> None:
    try:
        with open(PLAYED_FILE, "w") as f:
            f.write(f"{time.time():.3f}")
    except OSError:
        pass


def _played_ago() -> float:
    try:
        with open(PLAYED_FILE) as f:
            return time.time() - float(f.read().strip())
    except (OSError, ValueError):
        return 1e9


def warm_if_cold() -> None:
    """Wake the output chain if nothing has played for a while. Never raises.

    Synchronous on purpose: the point is to have the device already running when
    the phrase starts, and that means waiting out the warm-up first. It costs
    400 ms, and only after a silence - inside one answer the chunks stay glued.
    """
    try:
        import subprocess
        if _played_ago() < COLD_AFTER:
            return
        subprocess.run(["afplay", WARMUP],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mark_played()
    except Exception:            # noqa: BLE001 - a warm-up must never cost a phrase
        pass
