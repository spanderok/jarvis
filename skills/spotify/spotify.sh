#!/bin/bash
# Control Spotify on this Mac. The player through AppleScript, search through
# the public Spotify API (needs keys in the Keychain, see SKILL.md).
#
#   spotify.sh state                  what is playing now
#   spotify.sh play | pause | toggle
#   spotify.sh next | prev
#   spotify.sh vol 60 | vol +10 | vol -10
#   spotify.sh shuffle on|off
#   spotify.sh find "query"           find a track and play it
#   spotify.sh find-album "query"
#   spotify.sh playlist "part of name" play one of your playlists
#   spotify.sh playlists              list your playlists
#   spotify.sh open "query"           find but do not play - show the options
set -uo pipefail

osa() { osascript -e "$1" 2>/dev/null; }
running() { [ "$(osa 'tell application "System Events" to (name of processes) contains "Spotify"')" = "true" ]; }

# Only `play track` steals focus - it raises the Spotify window. Measured 22.08:
# pause, next, volume and reads leave the frontmost app alone, and playing a uri
# pulls Spotify forward from any other app. Bringing the previous app back has to
# wait out Spotify's own activation, otherwise it is overridden - 0.7 s works,
# immediately does not.
FOCUS_BACK_DELAY="${JARVIS_SPOTIFY_FOCUS_DELAY:-0.7}"

front_app() { osa 'tell application "System Events" to name of first process whose frontmost is true'; }

restore_front() {                # restore_front <application name>
  [ -z "${1:-}" ] && return 0
  [ "$1" = "Spotify" ] && return 0
  sleep "$FOCUS_BACK_DELAY"
  osa "tell application \"System Events\" to set frontmost of process \"$1\" to true" >/dev/null
}

need_running() {
  running && return 0
  echo "Spotify is not running" >&2
  exit 1
}

state() {
  running || { echo "Spotify is not running"; return; }
  local st tr ar al vol
  st=$(osa 'tell application "Spotify" to player state as string')
  tr=$(osa 'tell application "Spotify" to name of current track')
  ar=$(osa 'tell application "Spotify" to artist of current track')
  al=$(osa 'tell application "Spotify" to album of current track')
  vol=$(osa 'tell application "Spotify" to sound volume')
  echo "state: $st"
  echo "track: $tr"
  echo "artist: $ar"
  echo "album: $al"
  echo "volume: $vol"
}

# -- app token: client credentials, lives an hour, cached on disk --------------
TOKEN_CACHE="$HOME/.claude/jarvis/spotify_token"

token() {
  if [ -s "$TOKEN_CACHE" ]; then
    local exp
    exp=$(cut -d' ' -f1 < "$TOKEN_CACHE")
    if [ "$(date +%s)" -lt "$((exp - 60))" ]; then
      cut -d' ' -f2 < "$TOKEN_CACHE"; return 0
    fi
  fi
  local id secret resp tok
  id=$(security find-generic-password -s spotify-client-id -w 2>/dev/null)
  secret=$(security find-generic-password -s spotify-client-secret -w 2>/dev/null)
  if [ -z "$id" ] || [ -z "$secret" ]; then
    echo "no keys in the Keychain: spotify-client-id / spotify-client-secret" >&2
    return 1
  fi
  resp=$(curl -s -X POST https://accounts.spotify.com/api/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials&client_id=$id&client_secret=$secret")
  tok=$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)
  [ -z "$tok" ] && { echo "no token came back: $resp" >&2; return 1; }
  echo "$(( $(date +%s) + 3600 )) $tok" > "$TOKEN_CACHE"
  chmod 600 "$TOKEN_CACHE"
  printf '%s' "$tok"
}

search() {                      # search <type> <query> [how many]
  local kind="$1" q="$2" limit="${3:-5}" tok
  tok=$(token) || return 1
  curl -s -G "https://api.spotify.com/v1/search" \
    -H "Authorization: Bearer $tok" \
    --data-urlencode "q=$q" --data-urlencode "type=$kind" \
    --data-urlencode "limit=$limit" --data-urlencode "market=ES"
}

pick() {                        # pick <type> - stdin into "uri<TAB>description"
  python3 "$(dirname "$0")/pick.py" "$1"
}

play_uri() {
  need_running
  local was
  was=$(front_app)
  osa "tell application \"Spotify\" to play track \"$1\""
  restore_front "$was"
}

case "${1:-state}" in
  state)  state ;;
  play)   need_running; was=$(front_app); osa 'tell application "Spotify" to play'; restore_front "$was"; state ;;
  pause)  need_running; osa 'tell application "Spotify" to pause'; echo "paused" ;;
  toggle) need_running; osa 'tell application "Spotify" to playpause'; state ;;
  next)   need_running; osa 'tell application "Spotify" to next track'; sleep 0.6; state ;;
  prev)   need_running; osa 'tell application "Spotify" to previous track'; sleep 0.6; state ;;
  vol)
    need_running
    cur=$(osa 'tell application "Spotify" to sound volume')
    arg="${2:?a value is needed: 60, +10 or -10}"
    case "$arg" in
      +*) new=$(( cur + ${arg#+} )) ;;
      -*) new=$(( cur - ${arg#-} )) ;;
      *)  new=$arg ;;
    esac
    [ "$new" -gt 100 ] && new=100
    [ "$new" -lt 0 ] && new=0
    osa "tell application \"Spotify\" to set sound volume to $new"
    echo "volume: $cur -> $new"
    ;;
  shuffle)
    need_running
    case "${2:-on}" in
      on)  osa 'tell application "Spotify" to set shuffling to true'; echo "shuffle on" ;;
      off) osa 'tell application "Spotify" to set shuffling to false'; echo "shuffle off" ;;
    esac
    ;;
  find|find-album)
    kind=track; [ "$1" = "find-album" ] && kind=album
    q="${2:?a query is needed}"
    res=$(search "$kind" "$q" 5) || exit 1
    line=$(printf '%s' "$res" | pick "$kind" | head -1)
    [ -z "$line" ] && { echo "nothing found for \"$q\""; exit 1; }
    uri=${line%%$'\t'*}; desc=${line#*$'\t'}
    play_uri "$uri"
    echo "playing: $desc"
    ;;
  open)
    q="${2:?a query is needed}"
    res=$(search track "$q" 8) || exit 1
    printf '%s' "$res" | pick track | nl -w2 -s'. ' | cut -c1-100
    ;;
  playlists|playlist)
    echo "playlists need personal authorisation, not an app key - see SKILL.md, \"Your own playlists\"" >&2
    exit 2
    ;;
  *)
    sed -n '2,16p' "$0" | sed 's/^# \?//'
    ;;
esac
