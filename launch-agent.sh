#!/bin/zsh
# Siri entry point. Three Terminal windows on purpose:
#   window 1 - the working agent: interactive Claude Code, started by
#              `room.sh chief` in the folder its room names
#   window 2 - a second room, one per narrow job - here the one that reads
#              chats, started by `room.sh chat` in a repository so it sees both
#              the messages and the code. Chat questions go straight there, so a
#              long digest never blocks the working agent or eats its context.
#              Which rooms exist is config/rooms.toml, not this file
#   window 3 - Jarvis, asleep until a key activates him; he types your voice
#              into whichever window the question belongs to
# Terminal is used instead of a background process so that microphone and
# input-monitoring permissions belong to Terminal, not to the uv binary.
# Full paths everywhere: Shortcuts does not run a login shell.

JARVIS_DIR="$HOME/.claude/jarvis"
# Which rooms exist, and what each is called, comes out of config/rooms.toml.
ROOMS="$(python3 "$JARVIS_DIR/plugins.py" rooms 2>/dev/null)"
room_field() { python3 "$JARVIS_DIR/plugins.py" get "$1" "$2" 2>/dev/null; }
UV=$(command -v uv || echo /opt/homebrew/bin/uv)

# --- window handling --------------------------------------------------------
# Terminal opens a default window when it launches, and `do script` always adds
# another one, so an idle window gets reused instead of leaving an empty shell.
# "busy" is useless here: an interactive Claude session waiting for input is not
# busy, and the command would be typed into it as a message. A window counts as
# free only when nothing but a shell runs in it.
#
# USED_WIDS is the fix for the bug that ate the orchestrator: two `do script`
# calls in a row both saw the same window as free, because the first command had
# not spawned its process yet - so the second one landed in the input box of the
# agent that had just started. Now a window is claimed the moment it is used, and
# we wait until its process actually appears.
USED_WIDS=""

terminal_window_ids() {
  /usr/bin/osascript -e 'tell application "Terminal" to get id of every window' 2>/dev/null \
    | /usr/bin/tr -d ' ' | /usr/bin/tr ',' '\n'
}

window_is_free() {
  local procs p
  procs=$(/usr/bin/osascript -e "tell application \"Terminal\" to get processes of selected tab of window id $1" 2>/dev/null | /usr/bin/tr -d ' ')
  [ -n "$procs" ] || return 1
  # zsh does not word-split unquoted expansions, so split on commas explicitly:
  # without (s:,:) the whole list arrived as one word and every window looked busy
  for p in ${(s:,:)procs}; do
    case "$p" in
      login|-zsh|zsh|bash|-bash|sh) ;;
      *) return 1 ;;
    esac
  done
  return 0
}

run_in_terminal() {
  local cmd="$1" wid="" w i=0
  for w in $(terminal_window_ids); do
    case " $USED_WIDS " in (*" $w "*) continue ;; esac
    if window_is_free "$w"; then wid="$w"; break; fi
  done
  /usr/bin/osascript -e 'tell application "Terminal" to activate' >/dev/null 2>&1
  if [ -n "$wid" ]; then
    /usr/bin/osascript -e "tell application \"Terminal\" to do script \"$cmd\" in window id $wid" >/dev/null 2>&1
  else
    wid=$(/usr/bin/osascript \
      -e "tell application \"Terminal\" to do script \"$cmd\"" \
      -e 'tell application "Terminal" to get id of front window' 2>/dev/null | /usr/bin/tail -1)
  fi
  [ -n "$wid" ] && USED_WIDS="$USED_WIDS $wid"
  # wait for the command to become a process, so the next call cannot claim it
  while [ -n "$wid" ] && [ $i -lt 20 ]; do
    window_is_free "$wid" || break
    /bin/sleep 0.25
    i=$((i + 1))
  done
}

# --- who is already up ------------------------------------------------------
# Checked by process cwd, not by ~/.claude/sessions/*.json: a session started
# from inside another Claude Code session inherits CLAUDE_CODE_CHILD_SESSION and
# never registers itself there, so the registry misses exactly these windows.
claude_running_in() {
  local pid cwd
  for pid in $(/usr/bin/pgrep -f "bin/claude" 2>/dev/null); do
    cwd=$(/usr/sbin/lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | /usr/bin/grep '^n' | /usr/bin/cut -c2-)
    [ "$cwd" = "$1" ] && return 0
  done
  return 1
}

# The rocket agent is found by session name, not by folder: it lives inside a
# repository, and other sessions may sit in the same one.
session_named_alive() {
  local f pid
  for f in "$HOME/.claude/sessions"/*.json; do
    [ -f "$f" ] || continue
    /usr/bin/grep -q "\"name\":\"$1\"" "$f" || continue
    pid=$(/usr/bin/sed -n 's/.*"pid":\([0-9]*\).*/\1/p' "$f")
    [ -n "$pid" ] && /bin/kill -0 "$pid" 2>/dev/null && return 0
  done
  return 1
}

# The listener of /assist is the same file with --listen, so `pgrep -f
# jarvis_daemon.py` said "Jarvis is already up" and Siri quietly skipped the
# window - the microphone stayed with the agent session. The pid file is written
# by the assistant daemon only, so it answers the question that was really asked.
assistant_alive() {
  local pid
  pid=$(/bin/cat "$JARVIS_DIR/daemon.pid" 2>/dev/null) || return 1
  [ -n "$pid" ] && /bin/kill -0 "$pid" 2>/dev/null
}

start_jarvis_window() {
  assistant_alive && return 0
  # no JARVIS_ASLEEP: he greets once and listens for the wake word straight away,
  # so nothing has to be pressed after the launch
  run_in_terminal "clear; JARVIS_DEBUG=1 bash $JARVIS_DIR/jarvisd.sh"
  if ! /usr/bin/pgrep -f "jarvis_overlay.py" >/dev/null; then
    # drag is on by default now: the badge can be moved by hand, and the price
    # is that it catches clicks aimed at the window under it
    JARVIS_OVERLAY_DRAG="${JARVIS_OVERLAY_DRAG:-1}" \
      nohup "$UV" run --quiet "$JARVIS_DIR/jarvis_overlay.py" >/dev/null 2>&1 &
  fi
}

# --- raise what is missing --------------------------------------------------
# One window per room, in the order the config lists them. Adding a room adds
# a window here without this file being edited.
started=""
for room in $ROOMS; do
  name="$(room_field "$room" session)"
  label="$(room_field "$room" label)"
  folder="$(room_field "$room" work_dir)"
  if { [ -n "$name" ] && session_named_alive "$name"; } ||
     { [ -n "$folder" ] && claude_running_in "$folder"; }; then
    started="${started:+$started, }$label was already up"
  else
    run_in_terminal "true; bash '$JARVIS_DIR/room.sh' $room"
    started="${started:+$started, }started $label"
  fi
done
[ -n "$started" ] || started="no rooms in the config"
start_jarvis_window
/usr/bin/osascript -e 'tell application "Terminal" to activate' \
  -e "display notification \"$started\" with title \"Jarvis\"" >/dev/null 2>&1
