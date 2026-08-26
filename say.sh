#!/bin/bash
# Jarvis voice output.
# Backends (env JARVIS_BACKEND):
#   vosk  (default) - local vosk 0.7 speaker 4, offline, 0.9 s to first sound,
#                     phrases cached in ~/.claude/jarvis/cache-vosk/ and replay in 0.2 s
#   edge            - Microsoft ru-RU-the ownerNeural, needs internet;
#                     short phrases cached in ~/.claude/jarvis/cache/ and replay instantly
#   yuri            - macOS `say -v Yuri`, instant, offline, plainer voice
#   silero          - local Silero v4_ru, offline neural voice
# On vosk failure falls back to edge, and on edge failure to Yuri.
# Stress of a mispronounced word: venv-vosk/bin/python vosk_dict.py тЕстовый
# Usage: say.sh "текст"  |  echo "текст" | say.sh
# Env: JARVIS_EDGE_VOICE, JARVIS_EDGE_RATE (default +5%), JARVIS_EDGE_PITCH,
#      JARVIS_EDGE_VOLUME, JARVIS_YURI_RATE (words per minute)
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

# --to FILE synthesises without playing, so the next sentence can be prepared
# while the current one is still being spoken
OUT=""
if [ "$1" = "--to" ]; then
  OUT="$2"; shift 2
fi

TEXT="$*"
if [ -z "$TEXT" ] && [ ! -t 0 ]; then
  TEXT=$(cat)
fi
[ -z "$TEXT" ] && exit 0

if [ -n "$OUT" ]; then
  if [ "${JARVIS_BACKEND:-vosk}" = "vosk" ] && [ -x "$HOME/.claude/jarvis/venv-vosk/bin/python" ] \
     && "$HOME/.claude/jarvis/venv-vosk/bin/python" "$HOME/.claude/jarvis/vosk_say.py" --to "$OUT" "$TEXT" \
     && [ -s "$OUT" ]; then
    exit 0
  fi
  if uvx edge-tts \
      --voice "${JARVIS_EDGE_VOICE:-ru-RU-the ownerNeural}" \
      --rate "${JARVIS_EDGE_RATE:-+5%}" \
      --pitch "${JARVIS_EDGE_PITCH:-+0Hz}" \
      --volume "${JARVIS_EDGE_VOLUME:-+0%}" \
      --text "$TEXT" --write-media "$OUT" 2>/dev/null && [ -s "$OUT" ]; then
    exit 0
  fi
  # offline fallback: render Yuri into the same file
  say -v Yuri ${JARVIS_YURI_RATE:+-r "$JARVIS_YURI_RATE"} -o "$OUT" --data-format=LEF32@22050 "$TEXT"
  exit $?
fi

BACKEND="${JARVIS_BACKEND:-vosk}"

VOSK_PY="$HOME/.claude/jarvis/venv-vosk/bin/python"
if [ "$BACKEND" = "vosk" ]; then
  if [ -x "$VOSK_PY" ] && "$VOSK_PY" "$HOME/.claude/jarvis/vosk_say.py" "$TEXT"; then
    exit 0
  fi
  BACKEND="edge"   # model missing or broken: fall through to the network voice
fi

if [ "$BACKEND" = "yuri" ]; then
  exec say -v Yuri ${JARVIS_YURI_RATE:+-r "$JARVIS_YURI_RATE"} "$TEXT"
fi

if [ "$BACKEND" = "silero" ]; then
  exec uv run --quiet $HOME/.claude/jarvis/speak.py "$TEXT"
fi

EDGE_VOICE="${JARVIS_EDGE_VOICE:-ru-RU-the ownerNeural}"
EDGE_RATE="${JARVIS_EDGE_RATE:-+5%}"

CACHE_DIR="$HOME/.claude/jarvis/cache"
mkdir -p "$CACHE_DIR"
KEY=$(printf '%s|%s|%s' "$TEXT" "$EDGE_VOICE" "$EDGE_RATE" | shasum | cut -d' ' -f1)
CACHED="$CACHE_DIR/$KEY.mp3"

# Cached phrase: instant local playback, no network.
if [ -s "$CACHED" ]; then
  exec afplay "$CACHED"
fi

# Short phrase (< 120 chars): synth to file, play, keep in cache for next time.
if [ ${#TEXT} -lt 120 ]; then
  if uvx edge-tts \
      --voice "$EDGE_VOICE" \
      --rate "$EDGE_RATE" \
      --pitch "${JARVIS_EDGE_PITCH:-+0Hz}" \
      --volume "${JARVIS_EDGE_VOLUME:-+0%}" \
      --text "$TEXT" --write-media "$CACHED.tmp" 2>/dev/null && [ -s "$CACHED.tmp" ]; then
    mv "$CACHED.tmp" "$CACHED"
    exec afplay "$CACHED"
  fi
  rm -f "$CACHED.tmp"
else
  # Long text: stream, start speaking on first received chunk.
  if uv run --quiet $HOME/.claude/jarvis/speak_stream.py "$TEXT"; then
    exit 0
  fi
fi

# Offline fallback: instant local Yuri.
exec say -v Yuri ${JARVIS_YURI_RATE:+-r "$JARVIS_YURI_RATE"} "$TEXT"
