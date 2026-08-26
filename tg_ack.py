#!/usr/bin/env python3
"""Отметить сообщение в телеграме как выполненное.

    python3 ~/.claude/jarvis/tg_ack.py 1234          -> 👌 сделал
    python3 ~/.claude/jarvis/tg_ack.py 1234 🤔       -> не вышло

Номер сообщения приходит в событии `TGIN#<номер> текст`.
Telegram принимает только эмодзи из своего списка, «✅» в него не входит.
"""
import json
import subprocess
import sys
import urllib.parse
import urllib.request

ALLOWED = {"👌", "👍", "👀", "🤔", "🔥", "🎉"}


def secret(name: str) -> str:
    return subprocess.run(["security", "find-generic-password", "-s", name, "-w"],
                          capture_output=True, text=True).stdout.strip()


def main() -> None:
    if len(sys.argv) < 2:
        print("нужен номер сообщения")
        return
    mid = sys.argv[1]
    emoji = sys.argv[2] if len(sys.argv) > 2 else "👌"
    if emoji not in ALLOWED:
        print(f"такой эмодзи телеграм не примет, можно: {' '.join(sorted(ALLOWED))}")
        return
    token, chat = secret("rocketwatch-telegram-token"), secret("rocketwatch-telegram-chat")
    url = (f"https://api.telegram.org/bot{token}/setMessageReaction?"
           + urllib.parse.urlencode({
               "chat_id": chat, "message_id": mid,
               "reaction": json.dumps([{"type": "emoji", "emoji": emoji}])}))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            ok = json.load(r).get("ok")
        print(f"{emoji} поставлен" if ok else "не поставился")
    except Exception as e:
        print(f"не поставился: {e}")
        return
    close_open(mid, emoji)


def close_open(mid: str, emoji: str) -> None:
    """Закрыть дело в списке «принял, но не сделал».

    Дописываем строку, а не переписываем файл: список читается так, что по
    каждому номеру верной считается последняя запись. Так две отметки подряд не
    портят друг другу файл.
    """
    import os
    import time
    path = os.path.expanduser("~/.claude/jarvis/tg_open.jsonl")
    if emoji not in ("👌", "👍", "🎉", "🔥"):
        return                      # 👀 и 🤔 дело не закрывают
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": int(mid), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "text": "", "done": True}, ensure_ascii=False) + "\n")
    except (OSError, ValueError) as e:
        print(f"в списке дел не отметилось: {e}")


main()
