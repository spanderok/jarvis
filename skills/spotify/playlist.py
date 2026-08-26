#!/usr/bin/env python3
"""Свои плейлисты Spotify: список, создание, добавление треков, запуск.

Работает на доступе от имени пользователя - см. auth.py. Ключа приложения тут
не хватает: он открывает только публичный каталог.

    playlist.py list                        свои плейлисты
    playlist.py create "Имя" [описание]     создать приватный
    playlist.py add "часть имени" uri...    добавить треки
    playlist.py show "часть имени"          что внутри
    playlist.py uri "часть имени"           uri плейлиста, чтобы включить
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from auth import access_token  # noqa: E402

API = "https://api.spotify.com/v1"


def call(method: str, path: str, body=None):
    tok = access_token()
    if not tok:
        print("нет доступа от твоего имени - запусти:\n"
              "  python3 ~/.claude/skills/spotify/auth.py", file=sys.stderr)
        sys.exit(2)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        print(f"Spotify ответил {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)


def me() -> str:
    return call("GET", "/me")["id"]


def mine() -> list:
    """Свои плейлисты - те, где ты владелец, чужие подписки отбрасываем."""
    who = me()
    out, url = [], "/me/playlists?limit=50"
    while url:
        page = call("GET", url)
        for p in page.get("items", []):
            if p and p.get("owner", {}).get("id") == who:
                out.append(p)
        nxt = page.get("next")
        url = nxt[len(API):] if nxt else None
    return out


def find_one(part: str) -> dict:
    part = part.lower().strip()
    hits = [p for p in mine() if part in p["name"].lower()]
    if not hits:
        print(f"плейлиста с «{part}» в имени нет", file=sys.stderr)
        sys.exit(1)
    if len(hits) > 1:
        exact = [p for p in hits if p["name"].lower() == part]
        if len(exact) == 1:
            return exact[0]
        print("подходит несколько, уточни:", file=sys.stderr)
        for p in hits:
            print("  " + p["name"], file=sys.stderr)
        sys.exit(1)
    return hits[0]


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        for p in mine():
            # у некоторых плейлистов Spotify не отдаёт tracks в списке
            total = (p.get("tracks") or {}).get("total", "?")
            print(f"{total:>4}  {p['name']}")

    elif cmd == "create":
        name = sys.argv[2]
        desc = sys.argv[3] if len(sys.argv) > 3 else ""
        # приватный по умолчанию: плейлист, собранный голосом между делом,
        # не должен молча появляться в профиле
        # POST /me/playlists, а не /users/{id}/playlists: старый адрес Spotify
        # закрыл для приложений в режиме разработки в феврале 2026, он отдаёт 403
        p = call("POST", "/me/playlists",
                 {"name": name, "public": False, "description": desc})
        print(f"создан: {p['name']}\n{p['uri']}")

    elif cmd == "add":
        p = find_one(sys.argv[2])
        uris = sys.argv[3:]
        if not uris:
            print("нечего добавлять", file=sys.stderr)
            return 1
        # /items вместо /tracks - там же, миграция февраля 2026
        call("POST", f"/playlists/{p['id']}/items", {"uris": uris})
        print(f"в «{p['name']}» добавлено треков: {len(uris)}")

    elif cmd == "show":
        p = find_one(sys.argv[2])
        page = call("GET", f"/playlists/{p['id']}/items?limit=50")
        print(f"{p['name']}:")
        for it in page.get("items", []):
            # после миграции поле называется item, у старых ответов - track
            t = (it or {}).get("item") or (it or {}).get("track") or {}
            if not t:
                continue
            who = ", ".join(a["name"] for a in t.get("artists", []))
            print(f"  {t.get('name')} - {who}")

    elif cmd == "uri":
        print(find_one(sys.argv[2])["uri"])

    elif cmd == "like":
        ids = [u.rsplit(":", 1)[-1] for u in sys.argv[2:]]
        if not ids:
            print("нечего добавлять", file=sys.stderr)
            return 1
        call("PUT", "/me/tracks?ids=" + ",".join(ids))
        print(f"в любимые добавлено треков: {len(ids)}")

    elif cmd == "unlike":
        # По одному треку за раз и только явным указанием: тем же правом можно
        # обнулить всю библиотеку, поэтому массового снятия здесь нет.
        if len(sys.argv) != 3:
            print("снимаю сердечко только с одного трека за раз", file=sys.stderr)
            return 1
        call("DELETE", "/me/tracks?ids=" + sys.argv[2].rsplit(":", 1)[-1])
        print("сердечко снято")

    elif cmd == "liked":
        ids = [u.rsplit(":", 1)[-1] for u in sys.argv[2:]]
        if not ids:
            page = call("GET", "/me/tracks?limit=20")
            print("последние сохранённые:")
            for it in page.get("items", []):
                t = it.get("track") or {}
                who = ", ".join(a["name"] for a in t.get("artists", []))
                print(f"  {t.get('name')} - {who}")
            return 0
        res = call("GET", "/me/tracks/contains?ids=" + ",".join(ids))
        for u, yes in zip(sys.argv[2:], res):
            print(f"  {'есть' if yes else 'нет'}: {u}")

    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
