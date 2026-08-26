#!/usr/bin/env python3
"""Разовая авторизация Spotify от имени хозяина - для своих плейлистов.

Ключ приложения (client credentials) открывает только публичный каталог. Всё
личное - свои плейлисты, создание, добавление треков - требует доступа от имени
пользователя. Это одноразовая процедура: открывается браузер, хозяин нажимает
«Agree», и на диск ложится долгоживущий refresh-токен.

    python3 auth.py            один раз, чтобы получить доступ
    python3 auth.py --check    жив ли доступ

Токен лежит в ~/.claude/jarvis/spotify_user_token с правами 600. Логин и пароль
скрипт не видит - их вводит сам Spotify в браузере.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

PORT = 8888
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
TOKEN_FILE = os.path.expanduser("~/.claude/jarvis/spotify_user_token")
# Ровно то, о чём хозяин просил, и ничего сверх.
#
# Плейлисты - читать свои, писать в приватные и публичные.
# История - последние 50 воспроизведений и топы за месяц, полгода, всё время;
# добавлено 22.08 под разбор вкусов. Всё только на чтение.
#
# Чего тут намеренно нет: user-read-email, user-read-private (личные данные
# аккаунта), streaming и user-modify-playback-state (плеером управляет
# AppleScript, права не нужны).
#
# user-library-modify добавлено 23.08 по прямой просьбе хозяина - без него
# нельзя поставить сердечко голосом. Тем же правом можно снять сердечки со всей
# библиотеки, поэтому снятие в playlist.py сделано отдельной командой unlike и
# работает по одному треку: массового удаления в коде нет вообще.
SCOPES = " ".join([
    "playlist-read-private",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-read-recently-played",
    "user-top-read",
    "user-library-read",     # сохранённое сердечком, добавлено 22.08 по просьбе
    "user-library-modify",   # ставить и снимать сердечко, 23.08
])


def keychain(name: str) -> str:
    try:
        return subprocess.run(["security", "find-generic-password", "-s", name, "-w"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def post_token(payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def save(tokens: dict) -> None:
    tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f)
    os.chmod(TOKEN_FILE, 0o600)


def load() -> dict:
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def access_token() -> str:
    """Живой токен доступа: из файла, обновлённый по refresh, или пусто."""
    saved = load()
    if not saved.get("refresh_token"):
        return ""
    if saved.get("access_token") and time.time() < saved.get("expires_at", 0) - 60:
        return saved["access_token"]
    cid, secret = keychain("spotify-client-id"), keychain("spotify-client-secret")
    if not cid or not secret:
        return ""
    try:
        fresh = post_token({"grant_type": "refresh_token",
                            "refresh_token": saved["refresh_token"],
                            "client_id": cid, "client_secret": secret})
    except Exception:                    # noqa: BLE001 - истёкший или отозванный доступ
        return ""
    # Spotify не всегда присылает новый refresh - старый остаётся рабочим
    fresh.setdefault("refresh_token", saved["refresh_token"])
    save(fresh)
    return fresh.get("access_token", "")


class Catcher(http.server.BaseHTTPRequestHandler):
    code = None
    state = None

    def do_GET(self):                    # noqa: N802 - имя задаёт http.server
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        Catcher.code = (params.get("code") or [None])[0]
        Catcher.state = (params.get("state") or [None])[0]
        body = ("<h2>Готово, можно закрывать вкладку.</h2>"
                if Catcher.code else
                f"<h2>Отказано: {params.get('error', ['неизвестно'])[0]}</h2>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):        # тишина в консоли
        pass


def authorize() -> int:
    cid, secret = keychain("spotify-client-id"), keychain("spotify-client-secret")
    if not cid or not secret:
        print("нет ключей в Keychain: spotify-client-id / spotify-client-secret")
        return 1

    state = secrets.token_urlsafe(16)
    # PKCE не обязателен при наличии секрета, но verifier дешёв и лишним не будет
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT,
        "scope": SCOPES, "state": state, "show_dialog": "false",
    })

    server = http.server.HTTPServer(("127.0.0.1", PORT), Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"открываю браузер, нажми Agree. Если не открылось - вот адрес:\n{url}\n")
    subprocess.run(["open", url], check=False)

    deadline = time.time() + 180
    while Catcher.code is None and time.time() < deadline:
        time.sleep(0.2)
    server.shutdown()

    if Catcher.code is None:
        print("не дождался ответа за три минуты")
        return 1
    if Catcher.state != state:
        print("состояние запроса не совпало - авторизация отклонена")
        return 1

    tokens = post_token({"grant_type": "authorization_code", "code": Catcher.code,
                         "redirect_uri": REDIRECT, "client_id": cid,
                         "client_secret": secret})
    if "refresh_token" not in tokens:
        print("Spotify не выдал refresh-токен:", tokens)
        return 1
    save(tokens)
    print(f"доступ получен, токен в {TOKEN_FILE}")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        tok = access_token()
        print("доступ есть" if tok else "доступа нет, запусти без --check")
        sys.exit(0 if tok else 1)
    if "--token" in sys.argv:            # для spotify.sh
        tok = access_token()
        print(tok)
        sys.exit(0 if tok else 1)
    sys.exit(authorize())
