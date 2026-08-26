#!/bin/bash
# Start the Jarvis voice daemon in the foreground. Ctrl+C stops it.
# First start asks for microphone permission for your terminal app.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

# Personal settings - hotkeys, owner name, timings - live in one file so the
# code stays as it is shipped. See jarvis.env.example for every knob.
ENV_FILE="${JARVIS_ENV:-$HOME/.claude/jarvis/jarvis.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

# The microphone is single. Starting the assistant (by hand or from Siri) takes it
# from whichever agent had it through /assist - otherwise two processes fight for
# the input and neither hears anything.
for pid in $(pgrep -f "jarvis_daemon.py --listen" 2>/dev/null); do
  kill "$pid" 2>/dev/null
done
rm -f "$HOME/.claude/jarvis/listener.pid" "$HOME/.claude/jarvis/listener.owner"

exec uv run --quiet $HOME/.claude/jarvis/jarvis_daemon.py "$@"
