#!/bin/bash
# May Jarvis speak out loud right now?
#   exit 0 - yes
#   exit 1 - no, and the reason is printed
#
# The rule is not "is the microphone muted" - that state lives inside the Meet
# page and is not readable from outside. What matters is whether his voice can
# reach other people: a call plus the built-in speakers means the room mic picks
# him up. With headphones the same call is harmless.
#
# JARVIS_CALL_GUARD=off turns the whole check off.
CACHE="/tmp/jarvis-output-device"
CACHE_SEC=60

[ "$JARVIS_CALL_GUARD" = "off" ] && exit 0

in_call() {
  local urls
  urls=$(osascript -e 'tell application "Google Chrome" to get URL of tabs of windows' 2>/dev/null)
  case "$urls" in
    *meet.google.com/[a-z][a-z][a-z]-*) return 0 ;;
  esac
  /usr/bin/pgrep -x "zoom.us" >/dev/null && return 0
  return 1
}

output_device() {
  # system_profiler takes about a second, so the answer is cached: the output
  # device changes when headphones go in, not between two phrases
  if [ -f "$CACHE" ] && [ -z "$(find "$CACHE" -mmin +1 2>/dev/null)" ]; then
    cat "$CACHE"; return
  fi
  local name
  name=$(system_profiler SPAudioDataType 2>/dev/null | awk '
    /^        [[:alpha:]].*:$/ { name=$0 }
    /Default Output Device: Yes/ { print name; exit }')
  printf '%s' "$name" > "$CACHE"
  printf '%s' "$name"
}

if in_call; then
  dev=$(output_device)
  case "$dev" in
    *Speakers*|*Speaker*)
      echo "a call is running and the sound goes to the speakers ($(echo "$dev" | tr -d ' :')) - staying quiet" >&2
      exit 1 ;;
    *)
      exit 0 ;;   # headphones: nobody but the owner hears it
  esac
fi
exit 0
