#!/bin/bash
# Raise one room - a Claude session Jarvis can hand work to.
#
#   bash ~/.claude/jarvis/room.sh chief
#   bash ~/.claude/jarvis/room.sh chat
#
# Everything about the room comes out of config/rooms.toml: the session name,
# the folder it starts in, an extra system prompt, the window tint. Adding a
# room is a block in that file, not a copy of this script.
#
# It must be a Terminal.app window. The daemon types into the window and reads
# the answer off the screen, and an IDE terminal pane is invisible to it. The
# session is found by its name, so the folder can be any repository you like.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

JARVIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOM="$1"
shift 2>/dev/null

if [ -z "$ROOM" ]; then
  echo "usage: room.sh <room-id>   (known: $(python3 "$JARVIS_DIR/plugins.py" rooms))" >&2
  exit 2
fi

field() { python3 "$JARVIS_DIR/plugins.py" get "$ROOM" "$1" 2>/dev/null; }

NAME="$(field session)"
if [ -z "$NAME" ] && ! python3 "$JARVIS_DIR/plugins.py" get "$ROOM" id >/dev/null; then
  echo "room.sh: no room called '$ROOM' - known: $(python3 "$JARVIS_DIR/plugins.py" rooms)" >&2
  exit 2
fi
DIR="$(field work_dir)"
ROLE="$(field role_file)"
TINT="$(field tint)"
RC="$(field remote_control)"

# Started from inside another Claude session the child inherits this marker,
# skips the session registry and writes no transcript - and then nobody, the
# daemon included, can find it.
unset CLAUDE_CODE_CHILD_SESSION

cd "${DIR:-$HOME}" || exit 1

# A colour on the window, so two rooms are not mistaken for each other.
[ -n "$TINT" ] && bash "$JARVIS_DIR/tint.sh" "$TINT"

set -- ${NAME:+-n "$NAME"} "$@"
# Remote Control: the room is reachable from a phone or another machine, not
# only from this Terminal window.
[ -n "$RC" ] && set -- "$@" --remote-control "$RC"
if [ -n "$ROLE" ]; then
  # A repository with its own CLAUDE.md would ignore a role file dropped in the
  # folder, so the role is appended to the system prompt instead.
  ROLE_PATH="$ROLE"
  [ -f "$ROLE_PATH" ] || ROLE_PATH="$JARVIS_DIR/$ROLE"
  [ -f "$ROLE_PATH" ] && set -- "$@" --append-system-prompt "$(cat "$ROLE_PATH")"
fi

exec claude "$@"
