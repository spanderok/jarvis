#!/bin/bash
# Name of the Claude session this script was started from.
# Walks up the parent chain until a pid appears in ~/.claude/sessions - the same
# trick the listener uses to know whose ears it is. Prints an empty line if the
# caller is not inside a named session.
pid=$$
for _ in $(seq 1 12); do
  pid=$(/bin/ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ')
  [ -z "$pid" ] || [ "$pid" -le 1 ] 2>/dev/null && break
  f="$HOME/.claude/sessions/$pid.json"
  if [ -f "$f" ]; then
    /usr/bin/sed -n 's/.*"name":"\([^"]*\)".*/\1/p' "$f"
    exit 0
  fi
done
echo ""
