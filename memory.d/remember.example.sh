#!/bin/bash
# A remember command: one exchange appended to a dated file.
#
#   memory.d/remember.example.sh "the question" "the answer"
#
# Runs detached, after Jarvis has already spoken, so it can take its time - but
# it has no way to report a problem either. Nothing waits for it and nothing
# reads its output.
#
# Replace the body with an insert into your store. Whether an exchange is worth
# keeping at all is your call: most spoken questions are "what time is it".
set -u

NOTES="${JARVIS_NOTES:-$HOME/notes}"
Q="${1:-}"; A="${2:-}"
[ -z "$Q" ] || [ -z "$A" ] && exit 0
mkdir -p "$NOTES/jarvis" || exit 0

printf '\n## %s\n\n**Q:** %s\n\n**A:** %s\n' \
  "$(date '+%Y-%m-%d %H:%M')" "$Q" "$A" \
  >> "$NOTES/jarvis/$(date '+%Y-%m').md"
