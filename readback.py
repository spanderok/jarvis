"""Read a day of phrases back aloud through the streaming path, to check it by ear.

Not the same as speaking each phrase with speak.sh: that hands the whole text over
at once and never touches the chunking. Here the text is fed in a character at a
time, the way Claude writes it, and cut by the same rule the daemon uses - so what
you hear is the real thing, chunk boundaries and all.

  python3 readback.py                # сегодняшний день
  python3 readback.py --day 2026-08-20
  python3 readback.py --from 12      # начать с двенадцатой фразы
  touch ~/.claude/jarvis/readback.stop   # прервать

The listener is silenced for the whole run by holding the same speak lock the
voice-answer skill uses, otherwise it records the speakers and "hears" nonsense.
"""
import os
import queue
import subprocess
import sys
import threading
import time

JARVIS = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(JARVIS, "spoken.log")
STOP = os.path.join(JARVIS, "readback.stop")
LOCK = os.path.expanduser("~/.claude/tts-cache/.speak.lock")
WORKER = os.path.join(JARVIS, "vosk_worker.py")
VOSK_PY = os.path.join(JARVIS, "venv-vosk", "bin", "python")

FIRST_CHUNK = int(os.environ.get("JARVIS_FIRST_CHUNK", "60"))
NEXT_CHUNK = int(os.environ.get("JARVIS_NEXT_CHUNK", "220"))
MIN_LEN = 60          # проверочные однострочники короче этого не читаем
# Claude пишет быстрее, чем голос говорит (17.3 знака в секунду по замеру дня),
# иначе очередь никогда не набирается и склейку кусков не услышать. 45 - моя
# оценка скорости печати, подбиралась на слух.
STREAM_CPS = float(os.environ.get("READBACK_CPS", "45"))


def split_speakable(buf: str, min_chars: int) -> tuple[str, str]:
    """Ровно то же правило, что в jarvis_daemon.py - иначе проверка ничего не проверяет."""
    if len(buf) < min_chars:
        return "", buf
    cut = -1
    for i in range(len(buf) - 1, -1, -1):
        if buf[i] in ".!?…\n" and i + 1 >= min_chars:
            cut = i
            break
    if cut < 0:
        return "", buf
    return buf[:cut + 1].strip(), buf[cut + 1:]


def hold_lock() -> None:
    """Keep the listener deaf. speak.sh breaks a lock older than two minutes and
    removes it on exit, so the lock can vanish under us - on 21.08 that killed a
    readback at phrase 60. Refresh it if it is there, create it if it is not."""
    try:
        os.utime(LOCK, None)
    except OSError:
        try:
            os.makedirs(LOCK)
        except OSError:
            pass


def phrases(day: str) -> list[str]:
    out, seen = [], set()
    for line in open(LOG, encoding="utf-8", errors="replace"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3 or not parts[0].startswith(day):
            continue
        text = parts[2]
        if parts[1] == "readback" or len(text) < MIN_LEN or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def main() -> int:
    args = sys.argv[1:]
    day = args[args.index("--day") + 1] if "--day" in args else time.strftime("%Y-%m-%d")
    start = int(args[args.index("--from") + 1]) if "--from" in args else 1
    items = phrases(day)
    if not items:
        print(f"за {day} читать нечего")
        return 1
    print(f"фраз за {day}: {len(items)}, начинаю с {start}")

    if os.path.exists(STOP):
        os.unlink(STOP)
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    try:
        os.mkdir(LOCK)          # слушатель молчит, пока замок стоит
    except FileExistsError:
        pass

    env = dict(os.environ, JARVIS_SPOKEN_SOURCE="readback")
    proc = subprocess.Popen([VOSK_PY, WORKER], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1, cwd=JARVIS, env=env)
    events: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=lambda: [events.put(l.strip()) for l in proc.stdout],
                     daemon=True).start()

    def wait_idle(limit: float = 300.0) -> None:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            try:
                if events.get(timeout=0.5) == "!IDLE":
                    return
            except queue.Empty:
                pass

    wait_idle(30)
    try:
        for index, text in enumerate(items, 1):
            if index < start:
                continue
            if os.path.exists(STOP):
                print(f"остановлено перед фразой {index}")
                break
            print(f"\n--- {index}/{len(items)}  {text}", flush=True)
            proc.stdin.write(f"фраза {index}.\n")
            proc.stdin.flush()
            buf, spoken, marks = "", False, []
            for ch in text:                    # так текст приходит от Клода
                buf += ch
                time.sleep(1.0 / STREAM_CPS)
                chunk, buf = split_speakable(buf, FIRST_CHUNK if not spoken else NEXT_CHUNK)
                if chunk:
                    spoken = True
                    marks.append(len(chunk))
                    proc.stdin.write(chunk + "\n")
                    proc.stdin.flush()
            if buf.strip():
                marks.append(len(buf.strip()))
                proc.stdin.write(buf.strip() + "\n")
                proc.stdin.flush()
            print(f"    куски: {marks}", flush=True)
            wait_idle()
            hold_lock()                        # замок мог состариться или его снесли
            time.sleep(0.8)
        else:
            print(f"\nпрочитано всё: {len(items)}")
    finally:
        try:
            proc.stdin.write("!STOP\n")
            proc.stdin.flush()
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            os.rmdir(LOCK)
        except OSError:
            pass
    return 0


sys.exit(main())
