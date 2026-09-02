#!/bin/bash
# Speak a short text with the same voice Jarvis uses, and optionally send it to
# Telegram as a voice message.
#
#   bash speak.sh "done, tests are green"              # aloud on this Mac
#   bash speak.sh --telegram "done, tests are green"   # voice message in Telegram
#   bash speak.sh --both "done, tests are green"       # aloud and to Telegram
#   bash speak.sh --file /tmp/answer.ogg "text"        # only build the file
#   bash speak.sh --stop                               # shut up right now
#
# Which voice is the locale's business: say.sh reads locales/<lang>.toml and
# tries the local voice (piper for English, vosk for Russian), then the network
# one (edge-tts), then the macOS system voice. This script adds what an agent
# needs around that: one voice at a time, the call guard, the Telegram leg, and
# a note of who spoke last.
#
# Env: JARVIS_BACKEND / JARVIS_VOICE / JARVIS_EDGE_VOICE (see say.sh), CAPTION.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

JARVIS="$HOME/.claude/jarvis"
ENV_FILE="${JARVIS_ENV:-$JARVIS/jarvis.env}"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi

MODE="say"
OUT=""
case "$1" in
  --telegram) MODE="telegram"; shift ;;
  --both)     MODE="both";     shift ;;
  --file)     MODE="file"; OUT="$2"; shift 2 ;;
  --stop)     MODE="stop";     shift ;;
esac

# "be quiet" from any session, not only from the one that is talking: the keys
# reach the listener, but the owner is often typing in another agent's window.
if [ "$MODE" = "stop" ]; then
  # -x, not -f: "-f afplay" matches any command line that merely mentions the
  # word, including the shell that is running this very check.
  # The stop file is what actually ends a long answer: it is said sentence by
  # sentence, so killing one afplay only skipped to the next one.
  date +%s > "$JARVIS/speak.stop" 2>/dev/null
  pkill -x afplay >/dev/null 2>&1
  pkill -x ffplay >/dev/null 2>&1
  rmdir "$HOME/.claude/tts-cache/.speak.lock" 2>/dev/null
  echo "stopped the speech"
  exit 0
fi

TEXT="$*"
[ -z "$TEXT" ] && { echo "nothing to say: no text was passed" >&2; exit 1; }

# On a call the room hears everything the speakers say, so the answer goes to
# Telegram as a voice message instead of into the meeting. The owner gets it on
# the phone; nobody else on the call hears a thing.
if [ "$MODE" = "say" ] || [ "$MODE" = "both" ]; then
  if ! bash "$JARVIS/call_guard.sh"; then
    MODE="telegram"
    echo "in a call: sending a voice message to Telegram instead of the speakers" >&2
    # the badge waits for the speech lock to drop "thinking"; there will be no lock
    bash "$JARVIS/answered.sh" 2>/dev/null
  fi
fi

CACHE="$HOME/.claude/tts-cache"
mkdir -p "$CACHE"

# The same sentence is never synthesised twice. The key carries everything that
# changes the sound without a python call to find out what the locale picked.
KEY=$(printf '%s|%s|%s|%s|%s' "$TEXT" "${JARVIS_LANG:-en}" "${JARVIS_BACKEND:-}" \
        "${JARVIS_VOICE:-}" "${JARVIS_EDGE_VOICE:-}" | shasum | cut -d' ' -f1)
SRC="$CACHE/$KEY.wav"

# --- synthesis into a file: only Telegram and --file need it -----------------
# Speaking aloud goes straight through say.sh below, which streams and caches
# on its own; building the whole file first would only delay the first sound.
build_file() {
  [ -s "$SRC" ] && return 0
  if bash "$JARVIS/say.sh" --to "$SRC.tmp" "$TEXT" >/dev/null 2>&1 && [ -s "$SRC.tmp" ]; then
    mv "$SRC.tmp" "$SRC"
    return 0
  fi
  rm -f "$SRC.tmp"
  echo "no voice could render the phrase - local, network or system" >&2
  return 1
}

# --- one voice at a time ----------------------------------------------------
# Two agents finishing at once used to talk over each other. The lock is a
# directory because macOS has no flock binary; anything older than two minutes
# is a leftover from a killed process.
LOCK="$CACHE/.speak.lock"
lock() {
  local waited=0
  while ! mkdir "$LOCK" 2>/dev/null; do
    if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +2 2>/dev/null)" ]; then
      rm -rf "$LOCK"
      continue
    fi
    sleep 0.5
    waited=$((waited + 1))
    [ $waited -gt 120 ] && return 0   # a minute of waiting is enough, just talk
  done
  trap 'rm -rf "$LOCK"' EXIT
}

# The owner has the right of way: if the listener is recording them right now,
# starting to speak would both cut them off and make the listener drop the
# phrase. The state file is written by the listener, "listening" means the mic
# is open.
wait_his_turn() {
  local state waited=0
  while :; do
    state=$(cat "$JARVIS/state" 2>/dev/null)
    [ "$state" = "listening" ] || return 0
    sleep 0.3
    waited=$((waited + 1))
    [ $waited -gt 60 ] && return 0   # 18 s is a monologue, not a question - just talk
  done
}

play() {
  wait_his_turn
  lock
  bash "$JARVIS/say.sh" "$TEXT"
}

# --- ogg/opus: what Telegram shows as a voice message with a waveform --------
to_ogg() {
  local ogg="$1"
  command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is not installed (brew install ffmpeg) - cannot build a voice message" >&2; return 1; }
  ffmpeg -y -v error -i "$SRC" -c:a libopus -b:a 32k -ar 48000 -ac 1 \
         -application voip "$ogg"
}

send_telegram() {
  local tok chat ogg rc
  tok=$(security find-generic-password -s jarvis-telegram-token -w 2>/dev/null)
  chat=$(security find-generic-password -s jarvis-telegram-chat -w 2>/dev/null)
  if [ -z "$tok" ] || [ -z "$chat" ]; then
    echo "no token or chat id in the Keychain (jarvis-telegram-token/-chat)" >&2
    return 1
  fi
  build_file || return 1
  ogg="$CACHE/$KEY.ogg"
  [ -s "$ogg" ] || to_ogg "$ogg" || { echo "ffmpeg did not produce an ogg" >&2; return 1; }
  rc=$(curl -s -o /dev/null -w '%{http_code}' \
        -F "chat_id=$chat" -F "voice=@$ogg" \
        ${CAPTION:+-F "caption=$CAPTION"} \
        "https://api.telegram.org/bot$tok/sendVoice")
  if [ "$rc" != "200" ]; then
    echo "Telegram answered $rc" >&2
    return 1
  fi
  echo "voice message sent to Telegram"
}

# Who just talked to the owner. The microphone belongs to one session only, so
# its spoken answer lands there - and without this file that session has no
# way to know the question came from somebody else. It forwards the reply by
# name.
remember_speaker() {
  local me
  me=$(bash "$JARVIS/whoami.sh" 2>/dev/null)
  [ -n "$me" ] || return 0
  printf '%s\n%s\n' "$me" "$(date +%s)" > "$JARVIS/last_speaker"
}

case "$MODE" in
  say)      remember_speaker; play ;;
  telegram) remember_speaker; send_telegram ;;
  both)     remember_speaker; send_telegram; play ;;
  file)     build_file && to_ogg "$OUT" && echo "built: $OUT" ;;
esac
