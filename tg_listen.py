# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["numpy<2", "onnxruntime", "kaldi-native-fbank"]
# ///
"""Watch a Telegram bot, and hand this session only what is addressed to it.

One message, end to end:

  1. The owner writes to the bot from their phone: "chief, how is the deploy".
  2. The poll picks it up and checks chat_id - other people's messages are
     not ours.
  3. The first word is a session name, so the rest - "how is the deploy" - is
     for the session running under that name.
  4. If that name is ours, the line is printed as `TGIN#<id> how is the deploy`
     and becomes a monitor event in this session.
  5. If it is somebody else's, it goes into their inbox file and waits there.
     A message with no name at the front goes to the default room's inbox.

Voice messages take the same path plus two things: the voice is checked
against the profile (`voiceprint.py`), and only then turned into text.

Exactly one process may poll the bot - Telegram answers the second one with
`409 Conflict` and cuts the first one off. So polling is taken under a lock:
failing to take it means somebody else is polling, and we simply read our own
inbox, where they will put whatever is addressed to us.

    JARVIS_TG_NAME=chief   the name this session answers to
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

LOCK = os.path.join(JARVIS_DIR, "tg.lock")
OFFSET = os.path.join(JARVIS_DIR, "tg_offset")
INBOX = os.path.join(JARVIS_DIR, "tg_inbox")
# The "accepted but not done" list. The tg_offset file answers a different
# question - which message the poll has read; it knows nothing about whether
# the work got done. A line is closed with a 👌 through tg_ack.py.
OPEN = os.path.join(JARVIS_DIR, "tg_open.jsonl")
DOWNLOADS = os.path.join(JARVIS_DIR, "tg_in")
ME = os.environ.get("JARVIS_TG_NAME", "").strip()
# How long one wait lasts. Telegram honestly holds the connection for as long
# as asked - measured 22.08: asked for 5 seconds, came back in 5.4; asked for
# 10, came back in 10.4. It costs a hundredth of a second of CPU per request,
# so the window is kept short: any hiccup between two waits costs at most ten
# seconds rather than thirty.
POLL_SEC = int(os.environ.get("JARVIS_TG_POLL", "10"))


def _fallback_name() -> str:
    """Whose inbox an unaddressed message goes into: the default room."""
    try:
        import plugins

        cfg = plugins.load()
        room = cfg.room(cfg.default_room) or (cfg.rooms[0] if cfg.rooms else None)
        return room.id if room else "inbox"
    except Exception:
        return "inbox"


def _stranger_line() -> str:
    try:
        import lang

        return lang.current().stranger_line
    except Exception:
        return "Sorry, I am only allowed to talk to the owner of this computer."


STRANGER = _stranger_line()


def secret(name: str) -> str:
    out = subprocess.run(["security", "find-generic-password", "-s", name, "-w"],
                         capture_output=True, text=True)
    return out.stdout.strip()


TOKEN = secret("jarvis-telegram-token")
CHAT = secret("jarvis-telegram-chat")
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
    # Telegram only accepts emoji from its own list, and "✅" is not on it.
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
        log(f"the offset would not save: {e}")


def to_wav(src: str) -> str | None:
    dst = src.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        src, dst], check=True, capture_output=True)
        return dst
    except Exception as e:
        log(f"transcoding failed: {e}")
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
        log(f"the download failed: {e}")
        return None


_asr = None


def transcribe(wav: str) -> str | None:
    """One long-lived parakeet, shared by every voice message - as the daemon does."""
    global _asr
    if _asr is None or _asr.poll() is not None:
        try:
            _asr = subprocess.Popen(
                ["uv", "run", "--quiet", os.path.join(JARVIS_DIR, "asr_worker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                start_new_session=True)
            if _asr.stdout.readline().strip() != "!READY":
                log("the transcriber would not start")
                _asr = None
                return None
        except OSError as e:
            log(f"the transcriber failed to launch: {e}")
            _asr = None
            return None
    try:
        _asr.stdin.write(wav + "\n")
        _asr.stdin.flush()
        line = _asr.stdout.readline().strip()
    except OSError as e:
        log(f"the transcriber fell over: {e}")
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
        # the same caution as on the microphone: could not check, so let it through
        log(f"the voice check did not run: {e}")
        return True, f"the check did not work: {e}"


def known_names() -> set[str]:
    """Every name a message can be addressed to: the rooms, plus our own."""
    names = {ME} if ME else set()
    try:
        import plugins

        for room in plugins.load().rooms:
            names.add(room.id.lower())
            if room.env_session():
                names.add(room.env_session().lower())
            for word in room.bare:
                names.add(word.lower())
    except Exception:
        pass
    return {n for n in names if n}


def route(text: str) -> tuple[str, str]:
    """("name", "the rest") when the message opens with a known name.

    Only the very first word counts, and only as a whole word. A message that
    merely mentions a room in passing is not addressed to it - the same rule
    the voice side uses for a bare name.
    """
    head, _, rest = text.strip().partition(" ")
    bare = head.strip(",.:;!?-").lower()
    if bare and rest.strip() and bare in known_names():
        return bare, rest.strip()
    return "", text


def deliver(who: str, rest: str, whole: str) -> None:
    """Leave a message in a session's inbox file, so nothing is lost."""
    try:
        os.makedirs(INBOX, exist_ok=True)
        with open(os.path.join(INBOX, f"{who}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), "text": rest, "whole": whole},
                               ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"could not write to the inbox of {who!r}: {e}")


def handle(text: str, message_id: int) -> None:
    """Who this was said to - and whether to print an event into the session."""
    who, rest = route(text)
    if who and who == ME:
        deliver(who, rest, text)          # into the inbox too, so nothing is lost
        # The message number travels in the event: "done" is set by the agent,
        # once the work is actually finished, through tg_ack.py. Setting it here
        # would be a lie - at this moment the task has only been accepted.
        note_open(message_id, rest)
        emit(f"TGIN#{message_id} {rest}")
        return
    if who:
        deliver(who, rest, text)
        log(f"that is for {who}, not for me: {rest[:60]}")
        return
    # Unaddressed. If nobody else is polling and no name was given, it is
    # meant for whoever is listening - which is us.
    if not ME:
        note_open(message_id, text)
        emit(f"TGIN#{message_id} {text}")
        return
    spare = _fallback_name()
    deliver(spare, text, text)
    log(f"no name at the front - left it for {spare!r}: {text[:60]}")


def note_open(message_id: int, text: str) -> None:
    try:
        with open(OPEN, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": message_id, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "text": text, "done": False}, ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"the open item would not save: {e}")


def pending() -> list:
    """What was accepted and has not been marked done yet."""
    seen = {}
    try:
        for line in open(OPEN, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            seen[r["id"]] = r          # the last entry for a number is the true one
    except (OSError, ValueError):
        return []
    return [r for r in seen.values() if not r.get("done")]


def one_update(u: dict) -> None:
    m = u.get("message") or u.get("edited_message") or {}
    # Anybody who knows the bot's name can write to it - that is how Telegram
    # works, not a hole in the setup. Two fields are checked at once, the chat
    # and the author. In a direct chat both equal the owner's user id, and for
    # a stranger neither matches.
    if str(m.get("chat", {}).get("id")) != str(CHAT):
        who = m.get("from", {})
        log(f"someone else's chat {m.get('chat', {}).get('id')} "
            f"from @{who.get('username') or who.get('id')} - skipping")
        return
    if str(m.get("from", {}).get("id")) != str(CHAT):
        log(f"someone else writing in my chat: {m.get('from', {})} - skipping")
        return
    mid = m.get("message_id")
    # The eyes go on immediately, before any parsing. They mean "seen", not
    # "done": transcribing a voice message takes seconds, and without this mark
    # the phone stays silent for exactly that long.
    lag = time.time() - m.get("date", time.time())
    t0 = time.monotonic()
    react(mid, "👀")
    log(f"message {mid}: arrived in {lag:.1f}s, eyes set in "
        f"{time.monotonic() - t0:.1f}s")
    if "text" in m:
        handle(m["text"], mid)
        return
    if "voice" in m or "audio" in m:
        v = m.get("voice") or m["audio"]
        src = download(v["file_id"], ".ogg")
        wav = to_wav(src) if src else None
        if not wav:
            send("I could not open that voice message, please write it as text")
            return
        ok, why = voice_is_his(wav)
        if not ok:
            log(f"not his voice, in Telegram: {why}")
            react(mid, "🤔")
            send(STRANGER)
            return
        text = transcribe(wav)
        if not text:
            react(mid, "🤔")
            send("I could not transcribe that, please write it as text")
            return
        log(f"voice message: {why}")
        handle(text, mid)
        return
    log(f"nothing to parse: {sorted(k for k in m if k not in ('chat', 'from', 'date', 'message_id'))}")


def poll_forever() -> None:
    emit(f"TGWATCH: polling the bot, passing on only what is addressed to {ME or 'this session'!r}")
    # What is left over from the session's previous life. A session restarts more
    # often than mail arrives, and without this line anything accepted but not
    # done simply sinks.
    left = pending()
    if left:
        emit("TGWATCH: still open from last time: "
             + "; ".join(f"#{r['id']} {r['text'][:60]}" for r in left[-5:]))
    while True:
        off = read_offset()
        t_poll = time.monotonic()
        d = api("getUpdates", offset=off or "", timeout=POLL_SEC,
                allowed_updates=json.dumps(["message", "edited_message"]))
        if d and d.get("ok") and d.get("result"):
            log(f"the poll returned {len(d['result'])} after "
                f"{time.monotonic() - t_poll:.1f}s of waiting")
        if not d:
            time.sleep(3)
            continue
        if not d.get("ok"):
            # 409 means somebody else took over the poll - wait and try again
            log(f"Telegram answered: {d.get('error_code')} {d.get('description', '')}")
            time.sleep(5)
            continue
        for u in d["result"]:
            write_offset(u["update_id"] + 1)
            try:
                one_update(u)
            except Exception as e:
                log(f"the message would not parse: {e}")


def follow_inbox() -> None:
    """Somebody else is polling, so we just wait for the inbox to be filled."""
    path = os.path.join(INBOX, f"{ME}.jsonl")
    os.makedirs(INBOX, exist_ok=True)
    emit(f"TGWATCH: another process polls the bot, reading the {ME!r} inbox")
    pos = os.path.getsize(path) if os.path.exists(path) else 0
    while True:
        try:
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if size < pos:
                pos = 0                            # the file was truncated or replaced
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
            log(f"the inbox would not read: {e}")
        time.sleep(2)


def main() -> None:
    if not TOKEN or not CHAT:
        emit("TGWATCH: no token or chat id in the Keychain - the watch did not start")
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
