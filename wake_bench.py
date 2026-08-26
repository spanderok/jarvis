# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["vosk", "openwakeword>=0.6", "onnxruntime", "sounddevice",
#                 "numpy<2", "pvporcupine", "pynput", "webrtcvad",
#                 "kaldi-native-fbank"]
# ///
"""How many calls of "Джарвис" does the wake spotter catch in a recording?

    uv run wake_bench.py                     # everything in wake-test/
    uv run wake_bench.py path/to/one.wav     # one file

Both spotters are run over the same audio: the old one with the whole Russian
dictionary open, and the new one with a grammar of just the name. A fire is
counted as a new call only after a gap - consecutive frames of one "Джарвис"
would otherwise count as several. The gap is 1.0 s: the daemon's own debounce is
0.18 s, and calls in the test are made about four seconds apart.
"""
import glob
import os
import sys
import wave

import numpy as np

import jarvis_daemon as jd

GAP_SEC = 1.0


def calls(path: str, grammar: bool):
    """Returns (distinct calls, frames that fired) for one file."""
    jd.WAKE_GRAMMAR = grammar
    eng = jd.VoskEngine()
    with wave.open(path) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    n = eng.frame_len
    step = n / jd.SAMPLE_RATE
    fires, distinct, last = 0, 0, -99.0
    for i in range(0, len(audio) - n, n):
        if eng.detect(audio[i:i + n]):
            fires += 1
            at = i / jd.SAMPLE_RATE
            if at - last > GAP_SEC:
                distinct += 1
            last = at
    return distinct, fires


args = [a for a in sys.argv[1:] if not a.startswith("-")]
if args:
    files = [f for a in args for f in glob.glob(os.path.expanduser(a))]
else:
    files = sorted(glob.glob(os.path.join(
        os.path.expanduser("~/.claude/jarvis/wake-test"), "*.wav")))

if not files:
    sys.exit("Записей нет. Сначала: uv run ~/.claude/jarvis/wake_record.py 60")

print(f"записей: {len(files)}")
for f in files:
    with wave.open(f) as w:
        secs = w.getnframes() / w.getframerate()
    d_p, n_p = calls(f, False)
    d_g, n_g = calls(f, True)
    print(f"\n{os.path.basename(f)}  ({secs:.1f} с)")
    print(f"  полный словарь : поймал {d_p} зовов (кадров сработало {n_p})")
    print(f"  грамматика     : поймал {d_g} зовов (кадров сработало {n_g})")
