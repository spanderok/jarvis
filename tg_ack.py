#!/usr/bin/env python3
"""Mark a Telegram message as dealt with.

    python3 ~/.claude/jarvis/tg_ack.py 1234          -> 👌 done
    python3 ~/.claude/jarvis/tg_ack.py 1234 🤔       -> did not work out

The message number arrives in the `TGIN#<number> text` event.
Telegram only accepts emoji from its own list, and "✅" is not on it.
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
        print("a message number is needed")
        return
    mid = sys.argv[1]
    emoji = sys.argv[2] if len(sys.argv) > 2 else "👌"
    if emoji not in ALLOWED:
        print(f"Telegram will not take that emoji, allowed: {' '.join(sorted(ALLOWED))}")
        return
    token, chat = secret("jarvis-telegram-token"), secret("jarvis-telegram-chat")
    url = (f"https://api.telegram.org/bot{token}/setMessageReaction?"
           + urllib.parse.urlencode({
               "chat_id": chat, "message_id": mid,
               "reaction": json.dumps([{"type": "emoji", "emoji": emoji}])}))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            ok = json.load(r).get("ok")
        print(f"{emoji} set" if ok else "it did not go through")
    except Exception as e:
        print(f"it did not go through: {e}")
        return
    close_open(mid, emoji)


def close_open(mid: str, emoji: str) -> None:
    """Close an item on the "accepted but not done" list.

    A line is appended rather than the file rewritten: the list is read so that
    the last entry for a number is the true one. Two marks in a row then cannot
    corrupt each other.
    """
    import os
    import time
    path = os.path.expanduser("~/.claude/jarvis/tg_open.jsonl")
    if emoji not in ("👌", "👍", "🎉", "🔥"):
        return                      # 👀 and 🤔 do not close anything
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": int(mid), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "text": "", "done": True}, ensure_ascii=False) + "\n")
    except (OSError, ValueError) as e:
        print(f"could not mark it on the list: {e}")


main()
