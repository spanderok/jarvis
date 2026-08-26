#!/usr/bin/env python3
"""One-time Spotify authorisation as the owner - for their own playlists.

An app key (client credentials) only opens the public catalogue. Everything
personal - your own playlists, creating them, adding tracks - needs access
granted as the user. It is a one-time procedure: a browser opens, the owner
presses Agree, and a long-lived refresh token lands on disk.

    python3 auth.py            once, to get access
    python3 auth.py --check    is the access still alive

The token lives in ~/.claude/jarvis/spotify_user_token, mode 600. The script
never sees the login or the password - those go into Spotify's own page.
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
# Exactly what was asked for, and nothing beyond it.
#
# Playlists - read your own, write to private and public ones.
# History - the last 50 plays and the top items over a month, six months and all
# time; added for taste analysis. Read-only, all of it.
#
# Deliberately absent: user-read-email and user-read-private (personal account
# data), streaming and user-modify-playback-state (the player is driven by
# AppleScript, so no scope is needed).
#
# user-library-modify is here on an explicit request - without it a heart cannot
# be added by voice. The same scope can also strip the hearts off an entire
# library, which is why removal in playlist.py is its own `unlike` command and
# works one track at a time: there is no bulk removal anywhere in the code.
SCOPES = " ".join([
    "playlist-read-private",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-read-recently-played",
    "user-top-read",
    "user-library-read",     # what has been hearted
    "user-library-modify",   # adding and removing a heart
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
    """A live access token: from the file, refreshed, or empty."""
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
    except Exception:                    # noqa: BLE001 - expired or revoked access
        return ""
    # Spotify does not always send a new refresh token - the old one keeps working
    fresh.setdefault("refresh_token", saved["refresh_token"])
    save(fresh)
    return fresh.get("access_token", "")


class Catcher(http.server.BaseHTTPRequestHandler):
    code = None
    state = None

    def do_GET(self):                    # noqa: N802 - the name is http.server's
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        Catcher.code = (params.get("code") or [None])[0]
        Catcher.state = (params.get("state") or [None])[0]
        body = ("<h2>Done, you can close this tab.</h2>"
                if Catcher.code else
                f"<h2>Refused: {params.get('error', ['unknown'])[0]}</h2>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):        # keep the console quiet
        pass


def authorize() -> int:
    cid, secret = keychain("spotify-client-id"), keychain("spotify-client-secret")
    if not cid or not secret:
        print("no keys in the Keychain: spotify-client-id / spotify-client-secret")
        return 1

    state = secrets.token_urlsafe(16)
    # PKCE is not required when there is a secret, but a verifier is cheap
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT,
        "scope": SCOPES, "state": state, "show_dialog": "false",
    })

    server = http.server.HTTPServer(("127.0.0.1", PORT), Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"opening the browser, press Agree. If it did not open, here is the address:\n{url}\n")
    subprocess.run(["open", url], check=False)

    deadline = time.time() + 180
    while Catcher.code is None and time.time() < deadline:
        time.sleep(0.2)
    server.shutdown()

    if Catcher.code is None:
        print("no answer came back within three minutes")
        return 1
    if Catcher.state != state:
        print("the request state did not match - authorisation refused")
        return 1

    tokens = post_token({"grant_type": "authorization_code", "code": Catcher.code,
                         "redirect_uri": REDIRECT, "client_id": cid,
                         "client_secret": secret})
    if "refresh_token" not in tokens:
        print("Spotify did not return a refresh token:", tokens)
        return 1
    save(tokens)
    print(f"access granted, token in {TOKEN_FILE}")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        tok = access_token()
        print("access is there" if tok else "no access, run it without --check")
        sys.exit(0 if tok else 1)
    if "--token" in sys.argv:            # for spotify.sh
        tok = access_token()
        print(tok)
        sys.exit(0 if tok else 1)
    sys.exit(authorize())
