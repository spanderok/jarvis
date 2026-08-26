#!/bin/bash
# Key helper for the Jarvis daemon - works without any macOS permission.
# Bind it to a keyboard shortcut via Shortcuts.app (Run Shell Script).
#   jarvis-key.sh          one press: switch to me (end my sentence, interrupt
#                          him, or wake him) - he listens right after
#   jarvis-key.sh double   two presses: drop everything, back to the wake word
PID=$(pgrep -f "jarvis_daemon.py" | tail -1)
if [ -z "$PID" ]; then
  echo "jarvis daemon is not running" >&2
  exit 1
fi
case "${1:-tap}" in
  double|x2) kill -USR2 "$PID" ;;
  *)         kill -USR1 "$PID" ;;
esac
