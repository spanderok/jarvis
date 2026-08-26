#!/bin/bash
# Take the microphone over: stop whoever holds it right now - the standalone
# Jarvis daemon or a listener armed by another session - and say who that was.
# The microphone is single, so this is the only honest way to switch owners.
#
#   bash take-mic.sh            # take it quietly, one line of output
#   bash take-mic.sh --dry-run  # only say who is holding it
JARVIS_DIR="$HOME/.claude/jarvis"
owner=$(cat "$JARVIS_DIR/listener.owner" 2>/dev/null)
pids=$(/usr/bin/pgrep -f "jarvis_daemon.py" 2>/dev/null | tr '\n' ' ')

if [ "$1" = "--dry-run" ]; then
  if [ -n "$owner" ]; then echo "the microphone is held by session \"${owner}\""
  elif [ -n "$pids" ]; then echo "the microphone is held by the Jarvis daemon (${pids})"
  else echo "the microphone is free"; fi
  exit 0
fi

for pid in $pids; do kill "$pid" 2>/dev/null; done
/usr/bin/pkill -f asr_worker.py 2>/dev/null
/usr/bin/pkill -f tts_worker.py 2>/dev/null
/usr/bin/pkill -f vosk_worker.py 2>/dev/null
rm -f "$JARVIS_DIR/listener.pid" "$JARVIS_DIR/listener.owner"

if [ -n "$owner" ]; then
  echo "took the microphone from session \"${owner}\""
elif [ -n "$pids" ]; then
  echo "stopped the Jarvis daemon, the microphone is free"
else
  echo "the microphone was already free"
fi
