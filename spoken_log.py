"""Every phrase Jarvis says, written down - the corpus for polishing pronunciation.

Until 21.08 nothing recorded what had been spoken. Fixing stress meant digging the
speak.sh calls out of ~/.claude/projects/*/*.jsonl session transcripts, which works
only while those transcripts are still on disk. One line per phrase costs nothing
and makes the next pass a single command:

    python3 ~/.claude/jarvis/spoken_log.py words          words the model has to guess
    python3 ~/.claude/jarvis/spoken_log.py words 7        the same over the last 7 days
    python3 ~/.claude/jarvis/spoken_log.py tail 20        the last 20 phrases

Line format is tab separated: time, who said it, the text as the agent wrote it,
and the text after normalize_for_tts when the two differ. Both halves are needed -
stress lives in the second, the latin word table is driven by the first.
"""
import os
import re
import sys
import time

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spoken.log")
# about 20 000 phrases at 150 characters each; trimmed to half when it grows past
MAX_BYTES = 3_000_000


def record(raw: str, spoken: str, source: str) -> None:
    """Append one phrase. Never raises - a broken log must not cost a spoken answer.

    JARVIS_SPOKEN_SOURCE relabels the rows: readback.py sets it so its own playback
    does not come back as fresh corpus on the next run - on 21.08 one pass tripled
    the log, 182 rows of 244 were the readback repeating the day to itself.
    """
    try:
        source = os.environ.get("JARVIS_SPOKEN_SOURCE", source)
        raw = " ".join(raw.split())
        spoken = " ".join(spoken.split())
        if not raw:
            return
        line = "\t".join([time.strftime("%Y-%m-%d %H:%M:%S"), source, raw,
                          "" if spoken == raw else spoken])
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(LOG) > MAX_BYTES:
            _trim()
    except OSError:
        pass


def _trim() -> None:
    with open(LOG, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    with open(LOG, "w", encoding="utf-8") as f:
        f.writelines(lines[len(lines) // 2:])


# rows written by a readback pass: real speech, but not new material
ECHO_SOURCES = {"readback"}


def read(days: float | None = None,
         skip_echo: bool = True) -> list[tuple[str, str, str, str]]:
    """Rows of (time, source, raw, spoken); spoken falls back to raw when equal."""
    if not os.path.exists(LOG):
        return []
    cutoff = time.time() - days * 86400 if days else 0
    rows = []
    for line in open(LOG, encoding="utf-8", errors="replace"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3:
            continue
        ts, source, raw = parts[0], parts[1], parts[2]
        spoken = parts[3] if len(parts) > 3 and parts[3] else raw
        if skip_echo and source in ECHO_SOURCES:
            continue
        if cutoff:
            try:
                if time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")) < cutoff:
                    continue
            except ValueError:
                pass
        rows.append((ts, source, raw, spoken))
    return rows


def unknown_words(days: float | None = None) -> list[tuple[int, str]]:
    """Words absent from the voice dictionary, most spoken first.

    A word the dictionary does not hold gets its stress guessed from the letters,
    and that guess is what sounds mangled - so this list is the work queue.
    """
    import sqlite3
    from collections import Counter
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "models", "vosk-0.7", "dict.sqlite")
    cur = sqlite3.connect(db).cursor()
    counts: Counter = Counter()
    for _, _, _, spoken in read(days):
        counts.update(re.findall(r"[а-яёА-ЯЁ]+", spoken.lower()))
    missing = []
    for word, n in counts.items():
        if cur.execute("SELECT 1 FROM o WHERE w=?", (word,)).fetchone():
            continue
        if cur.execute("SELECT 1 FROM d WHERE w=?", (word,)).fetchone():
            continue
        missing.append((n, word))
    return sorted(missing, reverse=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "words"
    arg = float(sys.argv[2]) if len(sys.argv) > 2 else None
    if cmd == "tail":
        for ts, src, raw, _ in read()[-int(arg or 20):]:
            print(f"{ts}  {src:6s}  {raw}")
    elif cmd == "words":
        rows = read(arg)
        total = len({w for _, _, _, s in rows for w in re.findall(r"[а-яёА-ЯЁ]+", s.lower())})
        missing = unknown_words(arg)
        span = f"за последние {arg:g} дн." if arg else "за всё время"
        print(f"фраз {span}: {len(rows)}, разных слов {total}, "
              f"нет в словаре {len(missing)}")
        for n, w in missing:
            print(f"  {n}x  {w}")
    else:
        print(__doc__)
