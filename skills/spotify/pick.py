"""A Spotify search response turned into "uri<TAB>title - artist" lines.

A separate file rather than a heredoc inside spotify.sh: there `python3 - <<PY`
takes stdin for the code itself, so json.load gets the already-read script
instead of the data. Caught on 22.08 - search died with a traceback while the
API request was answering perfectly well.
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
