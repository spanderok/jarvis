"""Speak a text with the first sound as early as possible.

Time to first sound barely depends on the length of the text - it is a fixed cost
on the service side - but it collapses on a very short piece: measured on
20.08 with ru-RU-the ownerNeural, "Готово." started in 0.43 s while a 22-character
sentence took 3.36 s. So the text is cut into pieces, the first one deliberately
tiny, and every piece is synthesised while the previous one is still playing.

Run through uvx so edge_tts is available:
  uvx --from edge-tts python play_fast.py "текст"
"""
import asyncio
import hashlib
import os
import pathlib
import subprocess
import sys

import time

import edge_tts

STARTED = time.monotonic()

VOICE = os.environ.get("VOICE", "ru-RU-the ownerNeural")
RATE = os.environ.get("RATE", "+5%")
CACHE = pathlib.Path(os.path.expanduser("~/.claude/tts-cache"))
# The first piece only has to buy time: one short word is enough, and the shorter
# it is the sooner the voice starts. The rest is spoken in natural-sized pieces.
# Deliberately one word: 24 characters looked small but still cost 4.2 s to the
# first sound, because the service charges almost the same for 7 and 69
# characters - the cliff is between "one word" and "a phrase". Measured 20.08:
# "Готово." 0.43-0.80 s, 22 characters 3.36 s. The daemon uses 6 for the same
# reason.
FIRST_MAX = int(os.environ.get("JARVIS_FIRST_CHUNK", "10"))
NEXT_MAX = int(os.environ.get("JARVIS_NEXT_CHUNK", "220"))
# The service degrades: on 20.08 it answered "No audio was received" and then
# retried internally for 35 s. Waiting that long in silence is worse than a
# plainer voice, so a piece is given six seconds and then the caller falls back
# to the system voice. Six is about four times the worst healthy synthesis (1.5 s
# for a tiny piece) - long enough not to trip on a slow day.
SYNTH_TIMEOUT = float(os.environ.get("JARVIS_SYNTH_TIMEOUT", "6"))
BREAKS = ".!?…;:,"


def split_pieces(text: str) -> list[str]:
    """First piece short, the rest whole sentences up to NEXT_MAX characters."""
    text = " ".join(text.split())
    if len(text) <= FIRST_MAX:
        return [text]
    cut = -1
    for i, ch in enumerate(text[:FIRST_MAX]):
        if ch in BREAKS:
            cut = i + 1
    if cut < 4:  # no punctuation early enough - break on a word boundary
        cut = text.rfind(" ", 0, FIRST_MAX)
    if cut < 4:
        cut = FIRST_MAX
    pieces, rest = [text[:cut].strip()], text[cut:].strip()
    while rest:
        if len(rest) <= NEXT_MAX:
            pieces.append(rest)
            break
        cut = -1
        for i, ch in enumerate(rest[:NEXT_MAX]):
            if ch in BREAKS:
                cut = i + 1
        if cut < 20:
            cut = rest.rfind(" ", 0, NEXT_MAX)
        if cut < 20:
            cut = NEXT_MAX
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [p for p in pieces if p]


def cache_path(piece: str) -> pathlib.Path:
    key = hashlib.sha1(f"{piece}|{VOICE}|{RATE}".encode()).hexdigest()
    return CACHE / f"{key}.mp3"


def stamp(label: str) -> None:
    if os.environ.get("JARVIS_TTS_TIMING") == "1":
        print(f"  {label}: {time.monotonic() - STARTED:.2f} с", file=sys.stderr)


async def synth(piece: str) -> pathlib.Path:
    """One piece to a cached file; a piece said before costs nothing."""
    path = cache_path(piece)
    if path.exists() and path.stat().st_size > 0:
        return path
    tmp = path.with_suffix(".mp3.part")
    data = bytearray()
    comm = edge_tts.Communicate(piece, VOICE, rate=RATE)
    stamp(f"соединение открываю ({len(piece)} знаков)")
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            if not data:
                stamp("первые байты от сервиса")
            data += chunk["data"]
    if not data:
        raise RuntimeError("no audio")
    tmp.write_bytes(bytes(data))
    tmp.replace(path)
    return path


async def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        print("нечего говорить", file=sys.stderr)
        return 1
    started = STARTED
    stamp("питон запущен, модуль импортирован")
    CACHE.mkdir(parents=True, exist_ok=True)
    pieces = split_pieces(text)
    # One synthesis in flight at a time, the next one prepared while the current
    # piece plays. Firing them all at once was measured on 20.08 as a mistake:
    # the service answers concurrent connections with retries and a 69-character
    # phrase took 35 s instead of 5.
    # One player for the whole answer: a separate afplay per piece cost about a
    # second of startup each and left audible gaps. mp3 frames concatenate, so
    # the pieces are written into one ffplay as they become ready.
    player = await asyncio.create_subprocess_exec(
        "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-i", "pipe:0",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    def start(piece: str):
        return asyncio.create_task(asyncio.wait_for(synth(piece), SYNTH_TIMEOUT))

    ahead = start(pieces[0])
    spoken = 0      # если ни один кусок не прозвучал, вызвавший должен это узнать
    missed: list[str] = []  # что сервис не осилил - дочитаем системным голосом
    for i in range(len(pieces)):
        try:
            path = await ahead
        except asyncio.TimeoutError:
            print(f"кусок не успел за {SYNTH_TIMEOUT:.0f} с", file=sys.stderr)
            missed.append(pieces[i])
            path = None
        except Exception as e:  # a failed piece must not swallow the rest
            print(f"кусок не озвучился: {e}", file=sys.stderr)
            missed.append(pieces[i])
            path = None
        ahead = start(pieces[i + 1]) if i + 1 < len(pieces) else None
        if path is None:
            continue
        try:
            player.stdin.write(path.read_bytes())
            await player.stdin.drain()
            spoken += 1
            if i == 0 and os.environ.get("JARVIS_TTS_TIMING") == "1":
                print(f"первый кусок ушёл в звук через {time.monotonic() - started:.2f} с",
                      file=sys.stderr)
        except (BrokenPipeError, ConnectionResetError):
            break  # его заглушили клавишей или командой стоп
    if ahead is not None:
        ahead.cancel()
    try:
        player.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    await player.wait()
    # Часть фразы могла не озвучиться - дочитываем её системным голосом здесь же.
    # Иначе получалось хуже тишины: начало сказано, а хвост пропал незаметно.
    if missed and spoken:
        tail = " ".join(missed)
        print(f"дочитываю системным голосом: {tail[:60]!r}", file=sys.stderr)
        proc = await asyncio.create_subprocess_exec(
            "say", "-v", "Yuri", tail,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
    # Ненулевой код - сигнал вызвавшему сказать всё то же самое системным голосом.
    # Без него сбой сервиса ("No audio was received") заканчивался тишиной.
    return 0 if spoken else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
