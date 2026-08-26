"""Из ответа поиска Spotify - строки «uri<TAB>название - исполнитель».

Отдельным файлом, а не heredoc внутри spotify.sh: там `python3 - <<PY` забирает
stdin под сам код, и до json.load доходит уже прочитанный скрипт, а не данные.
Поймано 22.08 - поиск падал с трейсбеком, хотя запрос к API отвечал правильно.
"""
import json
import sys

kind = sys.argv[1]
try:
    data = json.load(sys.stdin)
except (ValueError, OSError):
    sys.exit(1)
for item in (data.get(kind + "s", {}).get("items") or []):
    if not item:
        continue
    who = ", ".join(a["name"] for a in item.get("artists", []) if a)
    print(f"{item['uri']}\t{item['name']}" + (f" - {who}" if who else ""))
