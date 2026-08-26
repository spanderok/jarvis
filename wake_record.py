# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["sounddevice", "numpy<2"]
# ///
"""Record the mic while music plays, so wake-word misses can be counted.

The daemon saves audio only after the wake word fired, so a call it did not hear
leaves nothing on disk and cannot be measured. This writes one plain wav of the
whole attempt instead, and wake_bench.py counts the calls in it afterwards.

    uv run wake_record.py [seconds]

The listener must be down for the run: if it is up it wakes on the first call it
does hear, pauses the music itself, and the rest of the take is no longer the
thing being measured.
"""
import os
import sys
import time
import wave

import numpy as np
import sounddevice as sd

JARVIS_DIR = os.path.expanduser("~/.claude/jarvis")
OUT_DIR = os.path.join(JARVIS_DIR, "wake-test")
RATE = 16000

secs = 60.0
for a in sys.argv[1:]:
    try:
        secs = float(a)
    except ValueError:
        pass

pid_file = os.path.join(JARVIS_DIR, "listener.pid")
if os.path.exists(pid_file) and "--force" not in sys.argv:
    try:
        pid = int(open(pid_file).read().strip())
        os.kill(pid, 0)
        owner = ""
        owner_file = os.path.join(JARVIS_DIR, "listener.owner")
        if os.path.exists(owner_file):
            owner = open(owner_file).read().strip()
        sys.exit(
            f"A listener is alive: pid {pid}, session {owner!r}.\n"
            "It will wake on the first call it hears and pause the music itself -\n"
            "and the rest of the recording is then no longer what we are measuring.\n\n"
            "Free the microphone:  bash ~/.claude/jarvis/take-mic.sh\n"
            "Or record over it anyway:  uv run wake_record.py --force")
    except (ProcessLookupError, ValueError):
        pass

os.makedirs(OUT_DIR, exist_ok=True)
path = os.path.join(OUT_DIR, time.strftime("%Y%m%d-%H%M%S") + "-music.wav")

print(f"Recording {secs:.0f} seconds into {path}")
print("Leave the music on. Call the wake word with about four seconds between calls.")
for n in (3, 2, 1):
    print(f"  {n}...", flush=True)
    time.sleep(1)
print("RECORDING", flush=True)

frames = []
t0 = time.monotonic()
with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                    blocksize=2000) as stream:
    said = 0
    while time.monotonic() - t0 < secs:
        block, _ = stream.read(2000)
        frames.append(block.copy())
        left = secs - (time.monotonic() - t0)
        if int(left) != said:
            said = int(left)
            if said % 10 == 0 and said:
                print(f"  {said}s left", flush=True)

audio = np.concatenate(frames).astype(np.int16).reshape(-1)
with wave.open(path, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(audio.tobytes())

peak = int(np.abs(audio).max())
print(f"\nDone: {path}")
print(f"  length {len(audio) / RATE:.1f}s, peak {peak} of 32767")
print("Tell me how many times you called - both recognizers get run over it.")
