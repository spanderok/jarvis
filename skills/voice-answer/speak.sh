#!/bin/bash
# Speak a short text with the same voice Jarvis uses (local vosk 0.7, speaker 4)
# and optionally send it to Telegram as a voice message.
#
#   bash speak.sh "done, tests are green"              # aloud on this Mac
#   bash speak.sh --telegram "done, tests are green"   # voice message in Telegram
#   bash speak.sh --both "done, tests are green"       # aloud and to Telegram
#   bash speak.sh --file /tmp/answer.ogg "text"        # only build the file
#   bash speak.sh --stop                               # shut up right now
#
# The voice is local, so Telegram messages are built offline too. VOICE_ENGINE=edge
# brings back the old network voice; if the local model is missing, edge is used
# automatically, and if that fails as well, the macOS voice Yuri.
#
# Env: VOICE_ENGINE (default vosk), VOICE (edge only), RATE (edge only), CAPTION.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

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
  # word, including the shell that is running this very check
  # the stop file is what actually ends a long answer: it is said sentence by
  # sentence, so killing one afplay only skipped to the next one
  date +%s > "$HOME/.claude/jarvis/speak.stop" 2>/dev/null
  pkill -x afplay >/dev/null 2>&1
  pkill -x ffplay >/dev/null 2>&1
  rmdir "$HOME/.claude/tts-cache/.speak.lock" 2>/dev/null
  echo "stopped the speech"
  exit 0
fi

TEXT="$*"
[ -z "$TEXT" ] && { echo "nothing to say: no text was passed" >&2; exit 1; }

# On a call the room hears everything the speakers say, so the answer goes to
# Telegram as a voice message instead of into the meeting. the owner gets it on the
# phone; nobody else on the call hears a thing.
if [ "$MODE" = "say" ] || [ "$MODE" = "both" ]; then
  if ! bash "$HOME/.claude/jarvis/call_guard.sh"; then
    MODE="telegram"
    echo "in a call: sending a voice message to Telegram instead of the speakers" >&2
    # the badge waits for the speech lock to drop "thinking"; there will be no lock
    bash "$HOME/.claude/jarvis/answered.sh" 2>/dev/null
  fi
fi

VOICE="${VOICE:-ru-RU-the ownerNeural}"
RATE="${RATE:-+5%}"
CACHE="$HOME/.claude/tts-cache"
mkdir -p "$CACHE"

VOSK_PY="$HOME/.claude/jarvis/venv-vosk/bin/python"
VOSK_SAY="$HOME/.claude/jarvis/vosk_say.py"
ENGINE="${VOICE_ENGINE:-vosk}"
[ "$ENGINE" = "vosk" ] && [ ! -x "$VOSK_PY" ] && ENGINE="edge"

KEY=$(printf '%s|%s|%s|%s' "$TEXT" "$ENGINE" "$VOICE" "$RATE" | shasum | cut -d' ' -f1)
MP3="$CACHE/$KEY.mp3"
WAV="$CACHE/$KEY.wav"
SRC="$MP3"
[ "$ENGINE" = "vosk" ] && SRC="$WAV"

# --- synthesis (cached: the same sentence is never paid for twice) -----------
# For plain speaking the whole file is not needed - play_fast.py does its own
# per-piece synthesis - so this step only runs for Telegram and --file.
if [ "$MODE" = "say" ]; then
  :
elif [ "$ENGINE" = "vosk" ]; then
  if [ ! -s "$WAV" ]; then
    if ! "$VOSK_PY" "$VOSK_SAY" --to "$WAV.tmp" "$TEXT" >/dev/null 2>&1 || [ ! -s "$WAV.tmp" ]; then
      rm -f "$WAV.tmp"
      echo "the local voice would not render, trying the network" >&2
      ENGINE="edge"; SRC="$MP3"
    else
      mv "$WAV.tmp" "$WAV"
    fi
  fi
fi

if [ "$MODE" != "say" ] && [ "$ENGINE" = "edge" ] && [ ! -s "$MP3" ]; then
  if ! uvx edge-tts --voice "$VOICE" --rate "$RATE" --text "$TEXT" \
        --write-media "$MP3.tmp" >/dev/null 2>&1 || [ ! -s "$MP3.tmp" ]; then
    rm -f "$MP3.tmp"
    if [ "$MODE" = "say" ]; then
      say -v Yuri "$TEXT"   # offline fallback, local playback only
      exit $?
    fi
    echo "edge-tts is unavailable, nothing left to build the voice message with" >&2
    exit 1
  fi
  mv "$MP3.tmp" "$MP3"
fi

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

# Speaking starts on a deliberately tiny first piece: measured on 20.08, the
# service returns audio for one short word in 0.43 s but needs 3.36 s for a whole
# sentence. play_fast.py cuts the text and synthesises pieces in parallel.
# the owner has the right of way: if the listener is recording them right now,
# starting to speak would both cut him off and make the listener drop his phrase.
# The state file is written by the listener, "listening" means the mic is open.
wait_his_turn() {
  local state waited=0
  while :; do
    state=$(cat "$HOME/.claude/jarvis/state" 2>/dev/null)
    [ "$state" = "listening" ] || return 0
    sleep 0.3
    waited=$((waited + 1))
    [ $waited -gt 60 ] && return 0   # 18 s is a monologue, not a question - just talk
  done
}

play_fast() {
  wait_his_turn
  lock
  # Local voice: 0.86 s to the first sound on a new phrase, 0.01 s on a repeat.
  if [ "$ENGINE" = "vosk" ]; then
    local started spoken
    started=$(date +%s)
    "$VOSK_PY" "$VOSK_SAY" "$TEXT" && return 0
    # It exited badly - but did it already speak? vosk_say.py stamps last_spoken
    # on the first sound. Falling back after the phrase was heard makes the
    # network voice repeat the whole answer, which is what happened on 21.08.
    spoken=$(stat -f %m "$HOME/.claude/jarvis/last_spoken" 2>/dev/null || echo 0)
    if [ "$spoken" -ge "$started" ]; then
      echo "the local voice played and then exited badly - no need to repeat it" >&2
      return 0
    fi
  fi
  if ! uvx --from edge-tts python "$(dirname "$0")/play_fast.py" "$TEXT"; then
    say -v Yuri "$TEXT"   # last resort: no model, no network
  fi
}

play() { wait_his_turn; lock; afplay "$SRC"; }

# --- ogg/opus: what Telegram shows as a voice message with a waveform --------
to_ogg() {
  local ogg="$1"
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

# Who just talked to the owner. The microphone belongs to one session only, so its
# spoken answer lands there - and without this file that session has no way to
# know the question came from somebody else. It forwards the reply by name.
remember_speaker() {
  local me
  me=$(bash "$HOME/.claude/jarvis/whoami.sh" 2>/dev/null)
  [ -n "$me" ] || return 0
  printf '%s\n%s\n' "$me" "$(date +%s)" > "$HOME/.claude/jarvis/last_speaker"
}

case "$MODE" in
  say)      remember_speaker; play_fast ;;
  telegram) remember_speaker; send_telegram ;;
  both)     remember_speaker; send_telegram; play_fast ;;
  file)     to_ogg "$OUT" && echo "built: $OUT" ;;
esac
