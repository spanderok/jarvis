"""Stress dictionary for the local vosk voice: base words on disk, personal overrides on top.

Base table `d` holds the 2 million words shipped with the model. Table `o` holds
words the owner corrected by hand; it always wins. Keeping both in sqlite instead of a
python dict is what makes the voice cost 359 MB instead of 966 MB.
"""
import os
import sqlite3
import sys

DB = os.path.expanduser("~/.claude/jarvis/models/vosk-0.7/dict.sqlite")


class StressDict:
    """Reads like a dict, lives on disk. Only `in` and `[]` are used by vosk-tts."""

    def __init__(self, path=DB):
        self.cur = sqlite3.connect(path, check_same_thread=False).cursor()

    def _get(self, word):
        row = self.cur.execute("SELECT p FROM o WHERE w=?", (word,)).fetchone()
        if row is None:
            row = self.cur.execute("SELECT p FROM d WHERE w=?", (word,)).fetchone()
        return row

    def __contains__(self, word):
        return self._get(word) is not None

    def __getitem__(self, word):
        row = self._get(word)
        if row is None:
            raise KeyError(word)
        return row[0]


def build(dictionary_path, db_path=DB):
    """One-off: text dictionary -> sqlite. Keeps the highest-probability variant per word."""
    best = {}
    with open(dictionary_path, encoding="utf-8") as f:
        for line in f:
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                continue
            word, prob, phonemes = parts[0], float(parts[1]), parts[2].strip()
            if best.get(word, (-1.0,))[0] < prob:
                best[word] = (prob, phonemes)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("CREATE TABLE IF NOT EXISTS d(w TEXT PRIMARY KEY, p TEXT) WITHOUT ROWID")
    con.execute("CREATE TABLE IF NOT EXISTS o(w TEXT PRIMARY KEY, p TEXT) WITHOUT ROWID")
    con.executemany("INSERT OR REPLACE INTO d VALUES(?,?)", ((w, v[1]) for w, v in best.items()))
    con.commit()
    con.close()
    return len(best)


def stressed_form(word):
    """"тЕстовый" -> "т+естовый": the capital letter marks the stressed vowel."""
    out = []
    marked = False
    for ch in word:
        if ch.isupper() and ch.lower() in "аеёиоуыэюя" and not marked:
            out.append("+")
            marked = True
        out.append(ch.lower())
    if not marked:
        raise ValueError(f"в слове «{word}» не отмечена ударная гласная заглавной буквой")
    return "".join(out)


def version(db_path=DB):
    """Bumped on every correction: the phrase cache is keyed by it, so a fixed word is re-said."""
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT count(*) FROM o").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def add_override(word_with_capital, db_path=DB):
    """Teach the voice one word.

    "тЕстовый"                 - capital letter marks the stressed vowel
    "слотегратор=слотэгрАтор"  - left side is how it is written, right side how it sounds
                                 (е -> э makes the consonant before it hard)
    """
    from vosk_tts.g2p import convert

    if "=" in word_with_capital:
        written, spoken = word_with_capital.split("=", 1)
    else:
        written = spoken = word_with_capital
    plain = written.lower()
    phonemes = convert(stressed_form(spoken)).strip()
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE IF NOT EXISTS o(w TEXT PRIMARY KEY, p TEXT) WITHOUT ROWID")
    con.execute("INSERT OR REPLACE INTO o VALUES(?,?)", (plain, phonemes))
    con.commit()
    con.close()
    return plain, phonemes


def list_overrides(db_path=DB):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT w, p FROM o ORDER BY w").fetchall()
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        print(f"слов записано: {build(sys.argv[2])}")
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        for w, p in list_overrides():
            print(f"{w}\t{p}")
    elif len(sys.argv) > 1:
        for word in sys.argv[1:]:
            plain, phonemes = add_override(word)
            print(f"{plain}: {phonemes}")
    else:
        print("использование: vosk_dict.py тЕстовый [ещёСлово ...] | list | build <файл>")
