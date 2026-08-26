# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["sounddevice", "numpy<2", "onnxruntime", "kaldi-native-fbank"]
# ///
"""Record the owner's voice print, in every way they actually sound.

    uv run ~/.claude/jarvis/enroll_voice.py            record the whole profile
    uv run ~/.claude/jarvis/enroll_voice.py --add шёпот   one more condition
    uv run ~/.claude/jarvis/enroll_voice.py --check    say something, get a score
    uv run ~/.claude/jarvis/enroll_voice.py --report    what the profile holds
    uv run ~/.claude/jarvis/enroll_voice.py --rebuild  redo the maths, keep the takes
    uv run ~/.claude/jarvis/enroll_voice.py --forgive  listen to what it refused

Why several takes instead of one: the microphone hears a tired voice at arm's
length differently from a fresh one leaning in. A single average of all of them
sits between the two and matches neither. So each take is kept whole, and a
phrase is his if it matches any one of them.

The threshold is not guessed - it is measured. Two crowds are compared:
  his takes against each other  -> how low his own voice can score
  other voices against his takes -> how high a stranger gets
Other voices are free and already on the disk: Jarvis' own synthesized speech
in cache-vosk (the voice most likely to trigger the microphone, since it plays
through the speakers) plus the two Russian macOS voices, Milena and Yuri.
"""

import glob
import json
import os
import random
import subprocess
import sys
import tempfile
import wave

import numpy as np
import sounddevice as sd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voiceprint as speaker  # noqa: E402

RATE = speaker.SAMPLE_RATE
TAKE_SEC = 6.0
CACHE = os.path.join(speaker.JARVIS_DIR, "cache-vosk")
COHORT_DIR = os.path.join(speaker.JARVIS_DIR, "cohort")
REJECTED_DIR = os.path.join(speaker.JARVIS_DIR, "rejected")

# The phrase does not matter to the model - it compares voices, not words - but
# a real command is easier to say naturally than a test sentence.
CONDITIONS = [
    ("обычно", "Обычным голосом, как всегда сидишь у мака.",
     "Джарвис, что там по задачам на сегодня, посмотри пожалуйста"),
    ("близко", "Ближе к микрофону, чуть тише обычного.",
     "Джарвис, поставь что-нибудь бодрое из моих плейлистов"),
    ("далеко", "Отойди на два-три метра и скажи громче.",
     "Джарвис, сделай погромче и переключи трек, этот не нравится"),
    ("шум", "Включи музыку или вентилятор, говори поверх шума.",
     "Джарвис, добавь этот трек в плейлист и скажи что получилось"),
    ("устал", "Тихо, устало, чуть хрипло - как поздно вечером.",
     "Джарвис, что там с почтой накопилось, я уже никакой"),
    ("быстро", "Быстро и небрежно, проглатывая окончания.",
     "Джарвис, скинь мне в телеграм результат когда закончишь"),
]


def rec_take(secs: float = TAKE_SEC) -> np.ndarray:
    print(f"    пишу {secs:.0f} секунд... говори", flush=True)
    audio = sd.rec(int(secs * RATE), samplerate=RATE, channels=1, dtype="int16")
    sd.wait()
    # An extra Enter pressed while the microphone was open would otherwise be
    # eaten by the next question, and the answer typed after it would land in the
    # shell instead. That silently dropped every other take on 22.08.
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass
    return audio[:, 0].copy()


def loudness(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))


def read_wav_16k(path: str) -> np.ndarray | None:
    """Any wav -> 16 kHz mono int16, through afconvert so no resampler of ours."""
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        path, tmp], check=True, capture_output=True)
        with wave.open(tmp) as w:
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return data if len(data) >= RATE else None
    except Exception:
        return None
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def system_voice(voice: str, text: str) -> np.ndarray | None:
    aiff = tempfile.mktemp(suffix=".aiff")
    try:
        subprocess.run(["say", "-v", voice, "-o", aiff, text],
                       check=True, capture_output=True)
        return read_wav_16k(aiff)
    except Exception:
        return None
    finally:
        if os.path.exists(aiff):
            os.unlink(aiff)


def other_voices(limit: int = 24) -> list[tuple[str, np.ndarray]]:
    """Everyone who is not the owner, gathered from what the disk already has.

    Three sources, and the folder matters most. cohort/ holds the voices that
    turned out to be dangerous when they were tried against the profile: the
    four other speakers of the same TTS model Jarvis speaks with, and a dozen
    male system voices. One of them, vosk3, was passing as the owner until it was
    put here - a voice inside the cohort scores high against its own siblings,
    and that is what sinks it.

    Drop any wav in ~/.claude/jarvis/cohort/ to teach the lock a new stranger -
    a colleague on a call, a family member. Nothing else needs to change.
    """
    out: list[tuple[str, np.ndarray]] = []
    for path in sorted(glob.glob(os.path.join(COHORT_DIR, "*.wav"))):
        a = read_wav_16k(path)
        if a is not None and len(a) / RATE >= 1.0:
            out.append((os.path.basename(path).rsplit("_", 1)[0], a))
    files = sorted(glob.glob(os.path.join(CACHE, "*.wav")))
    random.Random(4).shuffle(files)
    tts = 0
    for path in files:
        if tts >= limit:
            break
        a = read_wav_16k(path)
        if a is not None and len(a) / RATE >= 1.5:
            out.append(("джарвис-tts", a))
            tts += 1
    for voice in ("Milena", "Yuri"):
        for text in ("Джарвис, поставь что-нибудь бодрое из моих плейлистов",
                     "Джарвис, что там с почтой и добавь этот трек"):
            a = system_voice(voice, text)
            if a is not None:
                out.append((voice.lower(), a))
    return out


def pick_threshold(mine: np.ndarray, theirs: list[tuple[str, np.ndarray]]):
    """Where to draw the two lines, measured on the takes just recorded.

    floor  - how much a phrase must look like him at all.
    margin - by how much "looks like him" must beat "looks like the cohort".

    Both come from leave-one-out: every take is scored against the *other*
    takes, which is exactly what will happen to a new phrase later. Same for the
    cohort, so a stranger's second sentence is judged the way his first was.
    """
    n = len(mine)
    C = np.stack([v for _, v in theirs]) if theirs else np.zeros((0, mine.shape[1]),
                                                                dtype=np.float32)
    self_scores, self_gaps = [], []
    for i in range(n):
        rest = np.delete(mine, i, axis=0)
        me = float(np.max(rest @ mine[i])) if len(rest) else 1.0
        them = float(np.max(C @ mine[i])) if len(C) else 0.0
        self_scores.append(me)
        self_gaps.append(me - them)
    worst_self = min(self_scores)
    worst_gap = min(self_gaps)

    best_gap, who = -1.0, "-"
    for j in range(len(C)):
        me = float(np.max(mine @ C[j]))
        rest = np.delete(C, j, axis=0)
        them = float(np.max(rest @ C[j])) if len(rest) else 0.0
        if me - them > best_gap:
            best_gap, who = me - them, theirs[j][0]

    # Same 40% rule for both lines: stand 40% of the way from the strangers up
    # to his own worst take. Not the midpoint, because failing to recognise him
    # costs more than letting one stranger phrase through.
    # 0.20 под худшим тейком, не 0.10. Замер 22.08: тейки при записи узнавались
    # на 0.66, а живая фраза с того же места - на 0.55, и запас в 0.10 отбил его
    # собственный голос. Отрыв от чужих при этом остаётся вторым условием, так
    # что низкий порог никого лишнего не пускает.
    floor = max(0.45, min(worst_self - 0.20, 0.60))
    margin = best_gap + 0.40 * (worst_gap - best_gap)
    return floor, margin, worst_self, worst_gap, best_gap, who


def save(vecs, labels, cohort, floor: float, margin: float, stats: dict) -> None:
    with open(speaker.PROFILE, "w", encoding="utf-8") as fh:
        json.dump({"version": 2,
                   "samples": [np.asarray(v).tolist() for v in vecs],
                   "labels": labels,
                   "cohort": [np.asarray(v).tolist() for _, v in cohort],
                   "cohort_labels": [n for n, _ in cohort],
                   "floor": round(floor, 4), "margin": round(margin, 4),
                   "stats": stats}, fh, ensure_ascii=False)
    os.chmod(speaker.PROFILE, 0o600)


def load() -> dict:
    with open(speaker.PROFILE, encoding="utf-8") as fh:
        return json.load(fh)


def warn_listener() -> None:
    try:
        out = subprocess.run(["pgrep", "-f", "jarvis_daemon.py"],
                             capture_output=True, text=True).stdout.strip()
    except Exception:
        return
    if out:
        print("!! Джарвис сейчас слушает микрофон - он услышит эту запись и "
              "начнёт отвечать.\n   Останови его (Ctrl+C в его окне или "
              "/jarvis-daemon stop) и запусти меня снова.\n")
        if input("   Всё равно продолжить? [y/N] ").strip().lower() != "y":
            sys.exit(1)


def enroll(only: str | None = None) -> None:
    warn_listener()
    conds = CONDITIONS if only is None else [(only, "Своя условие - говори как хочешь.",
                                              "Джарвис, проверка голоса, раз два три")]
    vecs: list[np.ndarray] = []
    labels: list[str] = []
    if only is not None and os.path.exists(speaker.PROFILE):
        old = load()
        vecs = [np.asarray(v, dtype=np.float32) for v in old["samples"]]
        labels = list(old["labels"])

    print(f"Запишем {len(conds)} тейков по {TAKE_SEC:.0f} секунд. "
          "Фраза на экране - подсказка, можно говорить своими словами.\n")
    for key, how, phrase in conds:
        while True:
            print(f"[{key}] {how}")
            print(f'    фраза: "{phrase}"')
            input("    Enter - и говори: ")
            audio = rec_take()
            lvl = loudness(audio)
            print(f"    записано, громкость {lvl:.0f}")
            if lvl < 150:
                print("    это почти тишина. Перепишем.")
                continue
            try:
                vec = speaker.embed(audio)
            except Exception as e:
                print(f"    не смог посчитать отпечаток: {e}. Перепишем.")
                continue
            if vecs:
                near = float(np.max(np.stack(vecs) @ vec))
                print(f"    похоже на прошлые тейки: {near:.2f}")
            again = input("    оставляем? [Y/n] ").strip().lower()
            if again in ("", "y", "д", "da"):
                vecs.append(vec)
                labels.append(key)
                break
            print("    переписываем.\n")
        print()

    print("Считаю, где провести черту. Собираю чужие голоса с диска...")
    theirs = other_voices()
    tvecs = []
    for label, audio in theirs:
        try:
            tvecs.append((label, speaker.embed(audio)))
        except Exception:
            pass
    finish(vecs, labels, tvecs)


def finish(vecs, labels, tvecs) -> None:
    """Measure the two lines, save the profile, say what it is worth."""
    mine = np.stack(vecs)
    floor, margin, worst_self, worst_gap, best_gap, who = pick_threshold(mine, tvecs)
    stats = {"worst_self": round(worst_self, 4), "worst_gap": round(worst_gap, 4),
             "best_other_gap": round(best_gap, 4), "other_top": who,
             "others": len(tvecs)}
    save(vecs, labels, tvecs, floor, margin, stats)

    print(f"\nТейков в профиле: {len(vecs)} ({', '.join(labels)})")
    print(f"Чужих голосов для сверки: {len(tvecs)}")
    print(f"Твой худший тейк узнаётся на: {worst_self:.2f} (порог {floor:.2f})")
    print(f"Твой худший отрыв от чужих: {worst_gap:+.2f}")
    print(f"Лучший отрыв у чужого: {best_gap:+.2f} ({who})")
    print(f"Черта по отрыву: {margin:+.2f}")
    room = worst_gap - best_gap
    if room < 0.15:
        print("!! Зазор маленький. Перезапиши тейки, где голос звучал неестественно.")
    else:
        print(f"Зазор между тобой и чужими: {room:.2f} - этого хватает.")
    print(f"\nПрофиль лежит в {speaker.PROFILE}")


def rebuild() -> None:
    """Recompute the lines from the takes already in the profile.

    The takes are the expensive part - they need his voice and his time. The
    cohort and the two numbers are cheap, so a better rule does not cost a new
    recording session.
    """
    data = load()
    vecs = [np.asarray(v, dtype=np.float32) for v in data["samples"]]
    vecs = [v / np.linalg.norm(v) for v in vecs]
    print("Пересобираю чужие голоса с диска...")
    tvecs = []
    for label, audio in other_voices():
        try:
            tvecs.append((label, speaker.embed(audio)))
        except Exception:
            pass
    finish(vecs, list(data["labels"]), tvecs)


def forgive() -> None:
    """Listen to the phrases Jarvis refused, and keep the ones that were him.

    This is where a wrong refusal turns into something useful: the phrase that
    was nearly rejected is exactly the take the profile was missing.
    """
    files = sorted(glob.glob(os.path.join(REJECTED_DIR, "*.wav")))
    if not files:
        print("отбитых фраз нет - либо всё узнаётся, либо демон ещё не запускался")
        return
    print(f"Отбитых фраз: {len(files)}. Слушаем по одной.\n")
    data = load()
    vecs = [np.asarray(v, dtype=np.float32) for v in data["samples"]]
    vecs = [v / np.linalg.norm(v) for v in vecs]
    labels = list(data["labels"])
    added, done = 0, []
    for path in files:
        stamp = os.path.basename(path)[:-4]
        print(f"[{stamp}]")
        subprocess.run(["afplay", path], capture_output=True)
        ans = input("    это был ты? [y - добавить / n - выбросить / s - оставить как есть / q] ")
        ans = ans.strip().lower()
        if ans == "q":
            break
        if ans == "s":
            continue
        if ans == "y":
            audio = read_wav_16k(path)
            if audio is None:
                print("    файл не читается, пропускаю")
                continue
            try:
                vecs.append(speaker.embed(audio))
            except Exception as e:
                print(f"    отпечаток не посчитался: {e}")
                continue
            labels.append(input("    как назовём тейк? ").strip() or "прощённая")
            added += 1
        done.append(path)
    for path in done:
        os.unlink(path)
    if not added:
        print("\nничего не добавлено")
        return
    print(f"\nДобавлено тейков: {added}. Пересчитываю черту...")
    tvecs = []
    for label, a in other_voices():
        try:
            tvecs.append((label, speaker.embed(a)))
        except Exception:
            pass
    finish(vecs, labels, tvecs)


def check() -> None:
    if not os.path.exists(speaker.PROFILE):
        print("профиля ещё нет, сначала запиши его без флагов")
        return
    warn_listener()
    print("Скажи что-нибудь, как сказал бы Джарвису.")
    input("Enter - и говори: ")
    audio = rec_take(5.0)
    ok, score, why = speaker.check(audio)
    print(f"\n{'ПУСКАЮ' if ok else 'НЕ ПУСКАЮ'}: {why}")
    # A live phrase is worth more than a seventh prompted take: it was said the
    # way he really says things, from wherever he really sits. Keeping the ones
    # that scored low is what widens the profile - a phrase that already passed
    # comfortably teaches it nothing.
    if input("\nОставить эту фразу в профиле? [y/N] ").strip().lower() != "y":
        return
    data = load()
    vecs = [np.asarray(v, dtype=np.float32) for v in data["samples"]]
    vecs = [v / np.linalg.norm(v) for v in vecs]
    try:
        vecs.append(speaker.embed(audio))
    except Exception as e:
        print(f"не смог посчитать отпечаток: {e}")
        return
    labels = list(data["labels"]) + [input("Как назовём тейк? ").strip() or "живая"]
    print("Пересчитываю черту...")
    tvecs = []
    for label, a in other_voices():
        try:
            tvecs.append((label, speaker.embed(a)))
        except Exception:
            pass
    finish(vecs, labels, tvecs)


def report() -> None:
    if not os.path.exists(speaker.PROFILE):
        print("профиля ещё нет")
        return
    data = load()
    st = data.get("stats", {})
    print(f"тейков: {len(data['samples'])} - {', '.join(data['labels'])}")
    print(f"чужих голосов: {len(data.get('cohort', []))}")
    print(f"порог узнавания: {data['floor']}, черта по отрыву: {data['margin']}")
    print(f"худший свой тейк: {st.get('worst_self')}, его худший отрыв: "
          f"{st.get('worst_gap')}, лучший отрыв чужого: {st.get('best_other_gap')} "
          f"({st.get('other_top')})")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--check":
        check()
    elif arg == "--report":
        report()
    elif arg == "--forgive":
        forgive()
    elif arg == "--rebuild":
        rebuild()
    elif arg == "--add":
        enroll(only=sys.argv[2] if len(sys.argv) > 2 else "ещё")
    else:
        enroll()
