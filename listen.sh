#!/bin/bash
# Start the listener half of Jarvis: it hears the wake word and prints events on
# stdout for the Monitor tool of the agent session that owns the microphone.
# Used by the /assist command; jarvisd.sh is the standalone daemon instead.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

ENV_FILE="${JARVIS_ENV:-$HOME/.claude/jarvis/jarvis.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

exec uv run --quiet "$HOME/.claude/jarvis/jarvis_daemon.py" --listen "$@"
