#!/bin/bash
# Raise the chat agent - the session Jarvis sends chat questions to.
#
#   bash ~/.claude/jarvis/rocket.sh
#
# Starts in JARVIS_ROCKET_REPO so the agent can read the code it is asked about;
# defaults to the home directory. It must be a Terminal.app window: the
# daemon types into the window and reads the answer off the screen, and a WebStorm
# terminal pane is invisible to it. The daemon finds the session by its name.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

NAME="${JARVIS_ROCKET_NAME:-рокет}"
REPO="${JARVIS_ROCKET_REPO:-$HOME}"
ROLE="$HOME/.claude/jarvis/rocket-role.md"

# started from inside another Claude session the child inherits this marker,
# skips the session registry and writes no transcript - then nobody can find it
unset CLAUDE_CODE_CHILD_SESSION

cd "$REPO" || exit 1
exec claude -n "$NAME" --append-system-prompt "$(cat "$ROLE")" "$@"
