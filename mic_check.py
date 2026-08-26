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
import time

import numpy as np
import sounddevice as sd

SECONDS = 10
print("input devices:")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        mark = " <- default" if i == sd.default.device[0] else ""
        print(f"  [{i}] {d['name']} ({d['max_input_channels']} ch){mark}")

peaks = []
print(f"\nтеперь говори. Замер {SECONDS} секунд:\n")
with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=2000) as st:
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        block, overflowed = st.read(2000)
        level = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
        peaks.append(level)
        bar = "#" * min(60, int(level / 40))
        print(f"  {level:6.0f} {bar}", flush=True)

top = max(peaks) if peaks else 0
quiet = sum(1 for p in peaks if p < 5)
print(f"\nпик {top:.0f}, тихих кадров {quiet} из {len(peaks)}")
if top < 10:
    print("ВЕРДИКТ: микрофон не доходит до этого терминала - дай ему доступ "
          "в Настройки -> Конфиденциальность -> Микрофон и перезапусти терминал")
elif top < 200:
    print(f"ВЕРДИКТ: звук есть, но тихий. Запускай демон с JARVIS_MIN_LEVEL={int(top/3)}")
else:
    print("ВЕРДИКТ: микрофон в порядке")
