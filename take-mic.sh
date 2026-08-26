#!/bin/bash
# Take the microphone over: stop whoever holds it right now - the standalone
# Jarvis daemon or a listener armed by another session - and say who that was.
# The microphone is single, so this is the only honest way to switch owners.
#
#   bash take-mic.sh            # забрать молча, вывод одной строкой
#   bash take-mic.sh --dry-run  # только сказать, кто держит
JARVIS_DIR="$HOME/.claude/jarvis"
owner=$(cat "$JARVIS_DIR/listener.owner" 2>/dev/null)
pids=$(/usr/bin/pgrep -f "jarvis_daemon.py" 2>/dev/null | tr '\n' ' ')

if [ "$1" = "--dry-run" ]; then
  if [ -n "$owner" ]; then echo "микрофон держит сессия «${owner}»"
  elif [ -n "$pids" ]; then echo "микрофон держит Джарвис-демон (${pids})"
  else echo "микрофон свободен"; fi
  exit 0
fi

for pid in $pids; do kill "$pid" 2>/dev/null; done
/usr/bin/pkill -f asr_worker.py 2>/dev/null
/usr/bin/pkill -f tts_worker.py 2>/dev/null
/usr/bin/pkill -f vosk_worker.py 2>/dev/null
rm -f "$JARVIS_DIR/listener.pid" "$JARVIS_DIR/listener.owner"

if [ -n "$owner" ]; then
  echo "микрофон забран у сессии «${owner}»"
elif [ -n "$pids" ]; then
  echo "остановлен Джарвис-демон, микрофон свободен"
else
  echo "микрофон был свободен"
fi
