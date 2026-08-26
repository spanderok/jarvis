# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["numpy<2", "onnxruntime", "kaldi-native-fbank"]
# ///
"""Слушать телеграм-бота и отдавать этой сессии только то, что адресовано ей.

Одно сообщение, от начала до конца:

  1. Хозяин пишет боту с телефона: «Джарвис, что там по деплою».
  2. Опрос забирает его, проверяет chat_id - чужие сообщения не наши.
  3. tg_route.py видит имя в начале и говорит: это «доктору», остаток -
     «что там по деплою».
  4. Строка печатается как `TGIN что там по деплою` и становится событием
     монитора в сессии.
  5. Без имени в начале сообщение сюда не попадает: оно ложится в ящик
     рокет-агента и ждёт его.

Голосовые проходят тот же путь плюс две вещи: голос сверяется с профилем
(`voiceprint.py`), и только потом речь превращается в текст.

Бота может опрашивать ровно один процесс - Телеграм отвечает второму
`409 Conflict` и обрывает первого. Поэтому опрос берётся под замком: не
удалось взять - значит опрашивает rocket-watch, и мы просто читаем свой ящик,
куда он положит адресованное нам.
"""

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import wave

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, JARVIS_DIR)
sys.path.insert(0, os.path.expanduser("~/.claude/rocket-watch"))

WATCH_DIR = os.path.expanduser("~/.claude/rocket-watch")
LOCK = os.path.join(WATCH_DIR, "tg.lock")
OFFSET = os.path.join(WATCH_DIR, "tg_offset")
INBOX = os.path.join(JARVIS_DIR, "tg_inbox")
# Список «принял, но не сделал». Смещение tg_offset отвечает на другой
# вопрос - какое сообщение прочитано опросом; сделана ли по нему работа,
# оно не знает. Строка закрывается отметкой 👌 через tg_ack.py.
OPEN = os.path.join(JARVIS_DIR, "tg_open.jsonl")
DOWNLOADS = os.path.join(JARVIS_DIR, "tg_in")
ME = os.environ.get("JARVIS_TG_NAME", "доктор")
# Длина одного ожидания. Телеграм честно держит столько, сколько попросили -
# замер 22.08: попросил 5 секунд, вернулось за 5.4, попросил 10 - за 10.4.
# Стоит это одну сотую секунды процессорного времени на запрос, поэтому
# окно короткое: любая заминка между двумя ожиданиями стоит не больше десяти
# секунд, а не тридцати.
POLL_SEC = int(os.environ.get("JARVIS_TG_POLL", "10"))
STRANGER = ("Извините, мне разрешено разговаривать только с хозяином.")


def secret(name: str) -> str:
    out = subprocess.run(["security", "find-generic-password", "-s", name, "-w"],
                         capture_output=True, text=True)
    return out.stdout.strip()


TOKEN = secret("rocketwatch-telegram-token")
CHAT = secret("rocketwatch-telegram-chat")
API = f"https://api.telegram.org/bot{TOKEN}"


def emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    sys.stderr.write(f"{time.strftime('%d.%m %H:%M:%S')} {msg}\n")
    sys.stderr.flush()


def api(method: str, **params):
    url = f"{API}/{method}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=POLL_SEC + 15) as r:
            return json.load(r)
    except Exception as e:
        log(f"{method}: {e}")
        return None


def send(text: str) -> None:
    api("sendMessage", chat_id=CHAT, text=text)


def react(message_id: int, emoji: str) -> None:
    # Telegram принимает только эмодзи из своего списка, «✅» в него не входит.
    api("setMessageReaction", chat_id=CHAT, message_id=message_id,
        reaction=json.dumps([{"type": "emoji", "emoji": emoji}]))


def read_offset() -> int:
    try:
        return int(open(OFFSET).read().strip())
    except (OSError, ValueError):
        return 0


def write_offset(value: int) -> None:
    try:
        with open(OFFSET, "w") as f:
            f.write(str(value))
    except OSError as e:
        log(f"смещение не записалось: {e}")


def to_wav(src: str) -> str | None:
    dst = src.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        src, dst], check=True, capture_output=True)
        return dst
    except Exception as e:
        log(f"перекодировать не вышло: {e}")
        return None


def download(file_id: str, suffix: str) -> str | None:
    info = api("getFile", file_id=file_id)
    if not info or not info.get("ok"):
        return None
    path = info["result"]["file_path"]
    os.makedirs(DOWNLOADS, exist_ok=True)
    dst = os.path.join(DOWNLOADS, f"{file_id[:20]}{suffix}")
    try:
        urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{TOKEN}/{path}", dst)
        return dst
    except Exception as e:
        log(f"скачать не вышло: {e}")
        return None


_asr = None


def transcribe(wav: str) -> str | None:
    """Одна долгоживущая копия parakeet на все голосовые - как у демона."""
    global _asr
    if _asr is None or _asr.poll() is not None:
        try:
            _asr = subprocess.Popen(
                ["uv", "run", "--quiet", os.path.join(JARVIS_DIR, "asr_worker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                start_new_session=True)
            if _asr.stdout.readline().strip() != "!READY":
                log("распознавание не поднялось")
                _asr = None
                return None
        except OSError as e:
            log(f"распознавание не запустилось: {e}")
            _asr = None
            return None
    try:
        _asr.stdin.write(wav + "\n")
        _asr.stdin.flush()
        line = _asr.stdout.readline().strip()
    except OSError as e:
        log(f"распознавание отвалилось: {e}")
        _asr = None
        return None
    return line[5:].strip() if line.startswith("TEXT ") else None


def voice_is_his(wav: str) -> tuple[bool, str]:
    try:
        import numpy as np
        import voiceprint
        with wave.open(wav) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        ok, _, why = voiceprint.check(audio)
        return ok, why
    except Exception as e:
        # та же осторожность, что и на микрофоне: не смогли проверить - пускаем
        log(f"голос не проверился: {e}")
        return True, f"проверка не сработала: {e}"


def handle(text: str, message_id: int) -> None:
    """Кому это сказано - и печатать ли событие в сессию."""
    from tg_route import route, deliver
    who, rest = route(text)
    if who == ME:
        deliver(who, rest, text)          # в ящик тоже, чтобы не потерялось
        # Номер сообщения едет в событии: «сделал» ставит агент, когда работа
        # действительно закончена, скриптом tg_ack.py. Ставить его здесь было бы
        # враньём - в этот момент задача только принята.
        note_open(message_id, rest)
        emit(f"TGIN#{message_id} {rest}")
        return
    if who:
        deliver(who, rest, text)
        log(f"это {who}, не мне: {rest[:60]}")
        return
    deliver("рокет", text, text)
    log(f"без имени в начале - оставил рокету: {text[:60]}")


def note_open(message_id: int, text: str) -> None:
    try:
        with open(OPEN, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": message_id, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "text": text, "done": False}, ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"открытое дело не записалось: {e}")


def pending() -> list:
    """Что принято и до сих пор не закрыто отметкой."""
    seen = {}
    try:
        for line in open(OPEN, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            seen[r["id"]] = r          # последняя запись по номеру - она и верна
    except (OSError, ValueError):
        return []
    return [r for r in seen.values() if not r.get("done")]


def one_update(u: dict) -> None:
    m = u.get("message") or u.get("edited_message") or {}
    # Боту может написать кто угодно, кто знает его имя - это свойство телеграма,
    # а не дырка в настройке. Отсекаем по двум полям сразу: чат и автор. В личной
    # переписке оба равны его user id, у постороннего не совпадёт ни одно.
    if str(m.get("chat", {}).get("id")) != str(CHAT):
        who = m.get("from", {})
        log(f"чужой чат {m.get('chat', {}).get('id')} "
            f"от @{who.get('username') or who.get('id')} - пропускаю")
        return
    if str(m.get("from", {}).get("id")) != str(CHAT):
        log(f"чужой автор в моём чате: {m.get('from', {})} - пропускаю")
        return
    mid = m.get("message_id")
    # Глаза - сразу и на всё, до разбора. Они значат «увидел», а не «сделал»:
    # на голосовом расшифровка занимает секунды, и без этой отметки телефон
    # молчит ровно столько же.
    lag = time.time() - m.get("date", time.time())
    t0 = time.monotonic()
    react(mid, "👀")
    log(f"сообщение {mid}: дошло за {lag:.1f}с, глаза поставились за "
        f"{time.monotonic() - t0:.1f}с")
    if "text" in m:
        handle(m["text"], mid)
        return
    if "voice" in m or "audio" in m:
        v = m.get("voice") or m["audio"]
        src = download(v["file_id"], ".ogg")
        wav = to_wav(src) if src else None
        if not wav:
            send("не смог открыть голосовое, напиши текстом")
            return
        ok, why = voice_is_his(wav)
        if not ok:
            log(f"чужой голос в телеграме: {why}")
            react(mid, "🤔")
            send(STRANGER)
            return
        text = transcribe(wav)
        if not text:
            react(mid, "🤔")
            send("расшифровать не вышло, напиши текстом")
            return
        log(f"голосовое: {why}")
        handle(text, mid)
        return
    log(f"нечего разбирать: {sorted(k for k in m if k not in ('chat', 'from', 'date', 'message_id'))}")


def poll_forever() -> None:
    emit(f"TGWATCH: опрашиваю бота, отдаю сюда только адресованное «{ME}»")
    # Что осталось с прошлой жизни сессии. Сессия перезапускается чаще, чем
    # приходит почта, и без этой строки принятое, но не сделанное просто тонет.
    left = pending()
    if left:
        emit("TGWATCH: не закрыто с прошлого раза: "
             + "; ".join(f"#{r['id']} {r['text'][:60]}" for r in left[-5:]))
    while True:
        off = read_offset()
        t_poll = time.monotonic()
        d = api("getUpdates", offset=off or "", timeout=POLL_SEC,
                allowed_updates=json.dumps(["message", "edited_message"]))
        if d and d.get("ok") and d.get("result"):
            log(f"опрос вернул {len(d['result'])} шт. через "
                f"{time.monotonic() - t_poll:.1f}с ожидания")
        if not d:
            time.sleep(3)
            continue
        if not d.get("ok"):
            # 409 значит, что кто-то ещё взял опрос - подождать и попробовать снова
            log(f"телеграм ответил: {d.get('error_code')} {d.get('description', '')}")
            time.sleep(5)
            continue
        for u in d["result"]:
            write_offset(u["update_id"] + 1)
            try:
                one_update(u)
            except Exception as e:
                log(f"сообщение не разобралось: {e}")


def follow_inbox() -> None:
    """Опрашивает кто-то другой - значит просто ждём, что положат в ящик."""
    path = os.path.join(INBOX, f"{ME}.jsonl")
    os.makedirs(INBOX, exist_ok=True)
    emit(f"TGWATCH: бота опрашивает другой процесс, читаю ящик «{ME}»")
    pos = os.path.getsize(path) if os.path.exists(path) else 0
    while True:
        try:
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if size < pos:
                pos = 0                            # файл обрезали или заменили
            if size > pos:
                with open(path, encoding="utf-8") as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            emit("TGIN " + json.loads(line)["text"])
                        except Exception:
                            emit("TGIN " + line)
                    pos = f.tell()
        except OSError as e:
            log(f"ящик не читается: {e}")
        time.sleep(2)


def main() -> None:
    if not TOKEN or not CHAT:
        emit("TGWATCH: нет токена или chat id в Keychain - слежка не поднялась")
        return
    os.makedirs(WATCH_DIR, exist_ok=True)
    fh = open(LOCK, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        follow_inbox()
        return
    fh.write(str(os.getpid()))
    fh.flush()
    poll_forever()


main()
