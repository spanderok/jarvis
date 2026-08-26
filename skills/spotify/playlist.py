#!/usr/bin/env python3
"""Your own Spotify playlists: list, create, add tracks, play.

Runs on access granted as the user - see auth.py. An app key is not enough
here: it only opens the public catalogue.

    playlist.py list                        your playlists
    playlist.py create "Name" [description] create a private one
    playlist.py add "part of name" uri...   add tracks
    playlist.py show "part of name"         what is inside
    playlist.py uri "part of name"          the playlist uri, to play it
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
        print("no access granted as you - run:\n"
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
        print(f"Spotify answered {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)


def me() -> str:
    return call("GET", "/me")["id"]


def mine() -> list:
    """Your own playlists - the ones you own; followed ones are dropped."""
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
        print(f"no playlist with {part!r} in its name", file=sys.stderr)
        sys.exit(1)
    if len(hits) > 1:
        exact = [p for p in hits if p["name"].lower() == part]
        if len(exact) == 1:
            return exact[0]
        print("several match, be more specific:", file=sys.stderr)
        for p in hits:
            print("  " + p["name"], file=sys.stderr)
        sys.exit(1)
    return hits[0]


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        for p in mine():
            # for some playlists Spotify does not return tracks in the listing
            total = (p.get("tracks") or {}).get("total", "?")
            print(f"{total:>4}  {p['name']}")

    elif cmd == "create":
        name = sys.argv[2]
        desc = sys.argv[3] if len(sys.argv) > 3 else ""
        # private by default: a playlist put together by voice in passing
        # should not quietly show up on a public profile
        # POST /me/playlists, not /users/{id}/playlists: Spotify closed the old
        # address to apps in development mode in February 2026, and it gives 403
        p = call("POST", "/me/playlists",
                 {"name": name, "public": False, "description": desc})
        print(f"created: {p['name']}\n{p['uri']}")

    elif cmd == "add":
        p = find_one(sys.argv[2])
        uris = sys.argv[3:]
        if not uris:
            print("nothing to add", file=sys.stderr)
            return 1
        # /items rather than /tracks - same migration, February 2026
        call("POST", f"/playlists/{p['id']}/items", {"uris": uris})
        print(f"added {len(uris)} tracks to {p['name']!r}")

    elif cmd == "show":
        p = find_one(sys.argv[2])
        page = call("GET", f"/playlists/{p['id']}/items?limit=50")
        print(f"{p['name']}:")
        for it in page.get("items", []):
            # after the migration the field is called item; older responses had track
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
            print("nothing to add", file=sys.stderr)
            return 1
        call("PUT", "/me/tracks?ids=" + ",".join(ids))
        print(f"hearted {len(ids)} tracks")

    elif cmd == "unlike":
        # One track at a time, named explicitly: the same scope can empty the
        # whole library, so there is no bulk removal here.
        if len(sys.argv) != 3:
            print("a heart is removed from one track at a time", file=sys.stderr)
            return 1
        call("DELETE", "/me/tracks?ids=" + sys.argv[2].rsplit(":", 1)[-1])
        print("heart removed")

    elif cmd == "liked":
        ids = [u.rsplit(":", 1)[-1] for u in sys.argv[2:]]
        if not ids:
            page = call("GET", "/me/tracks?limit=20")
            print("most recently saved:")
            for it in page.get("items", []):
                t = it.get("track") or {}
                who = ", ".join(a["name"] for a in t.get("artists", []))
                print(f"  {t.get('name')} - {who}")
            return 0
        res = call("GET", "/me/tracks/contains?ids=" + ",".join(ids))
        for u, yes in zip(sys.argv[2:], res):
            print(f"  {'yes' if yes else 'no'}: {u}")

    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
