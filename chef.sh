#!/bin/bash
# Raise the orchestrator - the session Jarvis hands work over to.
#
#   bash ~/.claude/jarvis/chef.sh
#
# The name matters: sessions address each other by it (the chef's own CLAUDE.md
# uses SendMessage), and the daemon looks for the window by that name first.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

NAME="${JARVIS_ORCH_NAME:-шеф}"
DIR="${JARVIS_ORCH_DIR:-$HOME/claude-orchestrator}"
# Remote Control: the chef is reachable from the phone and from other machines,
# not only from this Terminal window. JARVIS_ORCH_RC=off leaves it out.
RC="${JARVIS_ORCH_RC:-orchestrator}"

# started from inside another Claude session the child inherits this marker,
# skips the session registry and writes no transcript - then nobody can find it
unset CLAUDE_CODE_CHILD_SESSION

cd "$DIR" || exit 1
# a warm mark on the window, so the chef is not confused with the rocket agent
bash "$HOME/.claude/jarvis/tint.sh" "${JARVIS_ORCH_TINT:-khaki}"
if [ "$RC" = "off" ]; then
  exec claude -n "$NAME" "$@"
fi
exec claude -n "$NAME" --remote-control "$RC" "$@"
