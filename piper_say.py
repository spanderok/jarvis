# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["piper-tts"]
# ///
"""The English voice: piper, offline, one 63 MB file per voice.

    uv run piper_say.py "text"            synthesise and play
    uv run piper_say.py --to out.wav "text"   synthesise only

Which voice comes from the locale (`lang.py get tts_voice`) and the model file
sits in models/piper/. Any voice from https://huggingface.co/rhasspy/piper-voices
drops in - two files, an .onnx and its .onnx.json, named the same.

Phrases are cached by text and voice, so "Listening." is synthesised once and
replayed from disk after that. That matters more than it sounds: the first
sound after a wake word is the thing a person waits for, and 0.3 s of synthesis
is audible where 0.02 s of disk read is not.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys
import wave

JARVIS_DIR = pathlib.Path(__file__).resolve().parent
MODEL_DIR = pathlib.Path(os.environ.get(
    "JARVIS_PIPER_DIR", JARVIS_DIR / "models" / "piper"))
CACHE_DIR = JARVIS_DIR / "cache-piper"

# Longer than a spoken sentence ever is; anything past it is a wedged process,
# and a wedged voice is worse than no voice - he would just go quiet.
SYNTH_TIMEOUT_SEC = float(os.environ.get("JARVIS_PIPER_TIMEOUT", "30"))

# Below this many characters the phrase is worth keeping: the short ones are the
# ones said over and over ("Listening.", "Got it."). Long answers are said once.
CACHE_MAX_CHARS = 120


def voice_name() -> str:
    name = os.environ.get("JARVIS_VOICE", "").strip()
    if name:
        return name
    sys.path.insert(0, str(JARVIS_DIR))
    import lang
    return lang.current().tts_voice


def model_path(voice: str) -> pathlib.Path:
    return MODEL_DIR / f"{voice}.onnx"


def synth(text: str, voice: str, out: pathlib.Path) -> bool:
    """One phrase to one wav. False means the caller should fall back."""
    model = model_path(voice)
    if not model.is_file():
        print(f"piper: no voice at {model}", file=sys.stderr)
        return False
    tmp = out.with_suffix(out.suffix + ".tmp")
    try:
        # -m/-f rather than the long flags: the short ones have been stable
        # across every piper release, the long ones have not.
        subprocess.run(
            [sys.executable, "-m", "piper", "-m", str(model), "-f", str(tmp)],
            input=text.encode(), check=True, capture_output=True,
            timeout=SYNTH_TIMEOUT_SEC)
    except FileNotFoundError:
        print("piper: not installed", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print(f"piper: gave up after {SYNTH_TIMEOUT_SEC:.0f}s", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return False
    except subprocess.CalledProcessError as e:
        print(f"piper: {e.stderr.decode()[:400]}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return False
    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False
    # An empty wav plays as silence rather than as an error, so it is caught
    # here instead of leaving him mute with nothing in the log.
    try:
        with wave.open(str(tmp)) as w:
            if w.getnframes() == 0:
                raise wave.Error("no frames")
    except wave.Error as e:
        print(f"piper: unusable wav ({e})", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(out)
    return True


def main(argv: list[str]) -> int:
    out_path = None
    if len(argv) > 1 and argv[1] == "--to":
        out_path = pathlib.Path(argv[2])
        argv = argv[:1] + argv[3:]

    text = " ".join(argv[1:]).strip() or (
        "" if sys.stdin.isatty() else sys.stdin.read().strip())
    if not text:
        return 0

    voice = voice_name()
    if out_path is not None:
        return 0 if synth(text, voice, out_path) else 1

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.wav"
    keep = len(text) < CACHE_MAX_CHARS

    if not (keep and cached.exists() and cached.stat().st_size > 0):
        target = cached if keep else CACHE_DIR / f"{key}.once.wav"
        if not synth(text, voice, target):
            return 1
        cached = target

    try:
        subprocess.run(["afplay", str(cached)], check=True)
    finally:
        if not keep:
            cached.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
