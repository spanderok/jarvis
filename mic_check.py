# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["sounddevice", "numpy<2"]
# ///
"""Is the microphone actually reaching this terminal?

Run it in the same terminal you start the daemon from, and talk:
  uv run ~/.claude/jarvis/mic_check.py

Numbers that stay at 0 mean the app has no microphone access:
  System Settings -> Privacy & Security -> Microphone -> enable your terminal.
"""
import queue
import sys
import time

import numpy as np
import sounddevice as sd

SECONDS = 10
print("input devices:")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        mark = " <- default" if i == sd.default.device[0] else ""
        print(f"  [{i}] {d['name']} ({d['max_input_channels']} ch){mark}")

# A blocking read waits for sound that a terminal without permission is never
# given, so this used to hang here for ever - printing nothing, in exactly the
# case the script exists to diagnose. The callback form cannot hang: the clock
# runs whether or not any audio arrives.
blocks: "queue.Queue[np.ndarray]" = queue.Queue()
peaks = []
print(f"\nnow talk. Measuring for {SECONDS} seconds:\n", flush=True)
try:
    stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                            blocksize=2000,
                            callback=lambda data, *_: blocks.put(data.copy()))
except Exception as e:                                   # noqa: BLE001
    print(f"could not open the microphone: {e}")
    print("VERDICT: no input device this terminal may use - grant it access in\n"
          "         System Settings -> Privacy & Security -> Microphone,\n"
          "         then restart the terminal")
    sys.exit(1)

with stream:
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        try:
            block = blocks.get(timeout=0.25)
        except queue.Empty:
            continue
        level = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
        peaks.append(level)
        bar = "#" * min(60, int(level / 40))
        print(f"  {level:6.0f} {bar}", flush=True)

top = max(peaks) if peaks else 0
quiet = sum(1 for p in peaks if p < 5)
print(f"\npeak {top:.0f}, {quiet} quiet frames out of {len(peaks)}")
if not peaks:
    print("VERDICT: the stream opened but not one block of sound arrived in\n"
          f"         {SECONDS} seconds - that is what a missing permission looks\n"
          "         like. System Settings -> Privacy & Security -> Microphone,\n"
          "         enable your terminal, then restart it")
elif top < 10:
    print("VERDICT: the microphone does not reach this terminal - grant it access\n"
          "         in System Settings -> Privacy & Security -> Microphone,\n"
          "         then restart the terminal")
elif top < 200:
    print(f"VERDICT: there is sound, but it is quiet. Start the daemon with "
          f"JARVIS_MIN_LEVEL={int(top/3)}")
else:
    print("VERDICT: the microphone is fine")
