# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy", "soundfile"]
# ///
"""Silero TTS wrapper: speak Russian text aloud on macOS.

Usage:
  uv run speak.py "текст"          # or pipe text via stdin
  JARVIS_VOICE=eugene uv run speak.py "текст"

First run downloads the v4_ru model (~60 MB) into ~/.claude/jarvis/models/.
"""
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf
import torch

MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
MODEL_PATH = os.path.expanduser("~/.claude/jarvis/models/v4_ru.pt")
SPEAKER = os.environ.get("JARVIS_VOICE", "aidar")
RATE = os.environ.get("JARVIS_RATE", "1.0")  # afplay time-stretch, pitch preserved
SAMPLE_RATE = 48000
CHUNK_LIMIT = 400  # apply_tts degrades on very long input, split by sentences


def split_text(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    chunks: list[str] = []
    current = ""
    for s in sentences:
        if len(current) + len(s) + 1 > CHUNK_LIMIT and current:
            chunks.append(current)
            current = s
        else:
            current = f"{current} {s}".strip()
    if current:
        chunks.append(current)
    return chunks


def main() -> None:
    text = " ".join(sys.argv[1:]).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        sys.exit(0)

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    if not os.path.exists(MODEL_PATH):
        print("Downloading Silero v4_ru model...", file=sys.stderr)
        torch.hub.download_url_to_file(MODEL_URL, MODEL_PATH)

    torch.set_num_threads(4)
    model = torch.package.PackageImporter(MODEL_PATH).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))

    parts = []
    for chunk in split_text(text):
        audio = model.apply_tts(text=chunk, speaker=SPEAKER, sample_rate=SAMPLE_RATE)
        parts.append(audio.numpy())
    combined = np.concatenate(parts)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, combined, SAMPLE_RATE)
        wav_path = f.name
    try:
        subprocess.run(["afplay", "-q", "1", "-r", RATE, wav_path], check=False)
    finally:
        os.unlink(wav_path)


main()
