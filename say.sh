#!/bin/bash
# Jarvis voice output.
#
# Which voice speaks is the locale's business, not this script's: locales/en.toml
# says piper, locales/ru.toml says vosk. JARVIS_BACKEND overrides for one run.
#
#   piper   local, one 60 MB file per voice, 51 languages available
#   vosk    local vosk-tts 0.7 speaker 4, Russian, 0.9 s to first sound
#   edge    Microsoft neural voices, needs internet, cached per phrase
#   system  macOS `say -v <name>`, instant, offline, plainly a robot
#
# The chain falls one step at a time: the local voice, then the network one,
# then the system one. A missing model must never leave him mute.
#
# Stress of a mispronounced Russian word: venv-vosk/bin/python vosk_dict.py тЕстовый
# Usage: say.sh "text"  |  echo "text" | say.sh
# Env: JARVIS_BACKEND, JARVIS_VOICE, JARVIS_EDGE_VOICE, JARVIS_EDGE_RATE
#      (default +5%), JARVIS_EDGE_PITCH, JARVIS_EDGE_VOLUME, JARVIS_SAY_RATE
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

JARVIS_HOME="$HOME/.claude/jarvis"

# One python call for all four, rather than four - this runs on every phrase.
read -r BACKEND VOICE EDGE_VOICE SYSTEM_VOICE <<EOF
$(python3 "$JARVIS_HOME/lang.py" get tts_backend 2>/dev/null) \
$(python3 "$JARVIS_HOME/lang.py" get tts_voice 2>/dev/null) \
$(python3 "$JARVIS_HOME/lang.py" get edge_voice 2>/dev/null) \
$(python3 "$JARVIS_HOME/lang.py" get system_voice 2>/dev/null)
EOF

# If the locale would not load, still say something rather than nothing.
BACKEND="${JARVIS_BACKEND:-${BACKEND:-system}}"
VOICE="${JARVIS_VOICE:-$VOICE}"
EDGE_VOICE="${JARVIS_EDGE_VOICE:-$EDGE_VOICE}"
SYSTEM_VOICE="${SYSTEM_VOICE:-Daniel}"
EDGE_RATE="${JARVIS_EDGE_RATE:-+5%}"

# --to FILE synthesises without playing, so the next sentence can be prepared
# while the current one is still being spoken.
OUT=""
if [ "$1" = "--to" ]; then
  OUT="$2"; shift 2
fi

TEXT="$*"
if [ -z "$TEXT" ] && [ ! -t 0 ]; then
  TEXT=$(cat)
fi
[ -z "$TEXT" ] && exit 0

say_system() {
  local dest="$1"
  if [ -n "$dest" ]; then
    say -v "$SYSTEM_VOICE" ${JARVIS_SAY_RATE:+-r "$JARVIS_SAY_RATE"} \
        -o "$dest" --data-format=LEF32@22050 "$TEXT"
  else
    exec say -v "$SYSTEM_VOICE" ${JARVIS_SAY_RATE:+-r "$JARVIS_SAY_RATE"} "$TEXT"
  fi
}

say_edge() {
  local dest="$1"
  uvx edge-tts \
      --voice "$EDGE_VOICE" \
      --rate "$EDGE_RATE" \
      --pitch "${JARVIS_EDGE_PITCH:-+0Hz}" \
      --volume "${JARVIS_EDGE_VOLUME:-+0%}" \
      --text "$TEXT" --write-media "$dest" 2>/dev/null && [ -s "$dest" ]
}

# --- synthesise into a file, do not play ------------------------------------
if [ -n "$OUT" ]; then
  case "$BACKEND" in
    piper)
      JARVIS_VOICE="$VOICE" uv run --quiet "$JARVIS_HOME/piper_say.py" \
        --to "$OUT" "$TEXT" && [ -s "$OUT" ] && exit 0 ;;
    vosk)
      [ -x "$JARVIS_HOME/venv-vosk/bin/python" ] \
        && "$JARVIS_HOME/venv-vosk/bin/python" "$JARVIS_HOME/vosk_say.py" \
             --to "$OUT" "$TEXT" && [ -s "$OUT" ] && exit 0 ;;
  esac
  say_edge "$OUT" && exit 0
  say_system "$OUT"
  exit $?
fi

# --- speak ------------------------------------------------------------------
case "$BACKEND" in
  piper)
    JARVIS_VOICE="$VOICE" uv run --quiet "$JARVIS_HOME/piper_say.py" "$TEXT" && exit 0
    BACKEND="edge" ;;   # voice file missing or piper broken
  vosk)
    if [ -x "$JARVIS_HOME/venv-vosk/bin/python" ] \
       && "$JARVIS_HOME/venv-vosk/bin/python" "$JARVIS_HOME/vosk_say.py" "$TEXT"; then
      exit 0
    fi
    BACKEND="edge" ;;
  system)
    say_system "" ;;
esac

if [ "$BACKEND" = "system" ]; then
  say_system ""
fi

if [ "$BACKEND" = "silero" ]; then
  exec uv run --quiet "$JARVIS_HOME/speak.py" "$TEXT"
fi

# --- edge, with a cache for the phrases he repeats ---------------------------
CACHE_DIR="$JARVIS_HOME/cache"
mkdir -p "$CACHE_DIR"
KEY=$(printf '%s|%s|%s' "$TEXT" "$EDGE_VOICE" "$EDGE_RATE" | shasum | cut -d' ' -f1)
CACHED="$CACHE_DIR/$KEY.mp3"

if [ -s "$CACHED" ]; then
  exec afplay "$CACHED"
fi

if [ ${#TEXT} -lt 120 ]; then
  if say_edge "$CACHED.tmp"; then
    mv "$CACHED.tmp" "$CACHED"
    exec afplay "$CACHED"
  fi
  rm -f "$CACHED.tmp"
else
  # Long text: stream, start speaking on the first chunk received.
  if uv run --quiet "$JARVIS_HOME/speak_stream.py" "$TEXT"; then
    exit 0
  fi
fi

say_system ""
