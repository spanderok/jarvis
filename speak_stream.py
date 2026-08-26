# /// script
# requires-python = ">=3.10"
# dependencies = ["edge-tts>=7"]
# ///
"""Streaming edge-tts playback: start playing audio while it is still downloading.

Usage: uv run speak_stream.py "текст"  (or text via stdin)
Env:   JARVIS_EDGE_VOICE, JARVIS_EDGE_RATE, JARVIS_EDGE_PITCH, JARVIS_EDGE_VOLUME
       JARVIS_DEBUG=1 prints time to first audio chunk
"""
import asyncio
import os
import subprocess
import sys
import time

import edge_tts

VOICE = os.environ.get("JARVIS_EDGE_VOICE", "ru-RU-the ownerNeural")
RATE = os.environ.get("JARVIS_EDGE_RATE", "+5%")
PITCH = os.environ.get("JARVIS_EDGE_PITCH", "+0Hz")
VOLUME = os.environ.get("JARVIS_EDGE_VOLUME", "+0%")
DEBUG = os.environ.get("JARVIS_DEBUG") == "1"


async def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        return 0

    t0 = time.monotonic()
    player = subprocess.Popen(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-"],
        stdin=subprocess.PIPE,
    )
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH, volume=VOLUME)
    got_audio = False
    try:
        async for chunk in comm.stream():
            if chunk["type"] != "audio":
                continue
            if not got_audio:
                got_audio = True
                if DEBUG:
                    print(f"first audio chunk: {time.monotonic() - t0:.2f}s", file=sys.stderr)
            player.stdin.write(chunk["data"])
            player.stdin.flush()
    except Exception as e:
        print(f"edge-tts stream failed: {e}", file=sys.stderr)
    finally:
        if player.stdin:
            player.stdin.close()
        player.wait()
    return 0 if got_audio else 1


sys.exit(asyncio.run(main()))
