# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["edge-tts>=7"]
# ///
"""Long-lived text-to-speech worker: keeps edge-tts warm, speaks lines in order.

Protocol (line based, UTF-8):
  stdin   "<text>"   speak this, queued after whatever is already queued
          "!STOP"    stop the current sentence and drop the queue
          "!QUIT"    exit
  stdout  "!SPEAKING"  the first audio of a sentence reached the speakers
          "!DONE"      that sentence is finished (spoken, failed or stopped)
          "!IDLE"      nothing left to say
          "!ERR <msg>" synthesis failed for one sentence

Why a worker: starting python + edge-tts per sentence cost about 1.5 s, which
turned every sentence break into an audible stumble.
"""
import asyncio
import os
import subprocess
import sys

import edge_tts

VOICE = os.environ.get("JARVIS_EDGE_VOICE", "ru-RU-the ownerNeural")
RATE = os.environ.get("JARVIS_EDGE_RATE", "+5%")
PITCH = os.environ.get("JARVIS_EDGE_PITCH", "+0Hz")
VOLUME = os.environ.get("JARVIS_EDGE_VOLUME", "+0%")


def emit(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


class Worker:
    def __init__(self):
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.player: subprocess.Popen | None = None
        self.stopping = False

    async def read_stdin(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                await self.queue.put("!QUIT")
                return
            line = line.rstrip("\n")
            if not line:
                continue
            if line == "!STOP":
                self.stopping = True
                self.kill_player()
                while not self.queue.empty():
                    self.queue.get_nowait()
                self.stopping = False
                emit("!IDLE")
                continue
            await self.queue.put(line)

    def kill_player(self) -> None:
        p = self.player
        self.player = None
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    async def speak(self, text: str) -> None:
        self.player = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        started = False
        comm = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH, volume=VOLUME)
        try:
            async for chunk in comm.stream():
                if self.player is None or self.stopping:
                    return
                if chunk["type"] != "audio":
                    continue
                if not started:
                    started = True
                    emit("!SPEAKING")
                self.player.stdin.write(chunk["data"])
                self.player.stdin.flush()
        except (BrokenPipeError, OSError):
            return
        except Exception as e:  # network hiccup on one sentence
            emit(f"!ERR {e}")
            return
        finally:
            p = self.player
            if p is not None and p.stdin:
                try:
                    p.stdin.close()
                except OSError:
                    pass
        p = self.player
        if p is not None:
            # wait for playback to drain without blocking the event loop
            while p.poll() is None:
                if self.player is None or self.stopping:
                    return
                await asyncio.sleep(0.03)
        self.player = None

    async def run(self) -> None:
        asyncio.create_task(self.read_stdin())
        emit("!IDLE")
        while True:
            text = await self.queue.get()
            if text == "!QUIT":
                self.kill_player()
                return
            await self.speak(text)
            emit("!DONE")
            if self.queue.empty():
                emit("!IDLE")


asyncio.run(Worker().run())
