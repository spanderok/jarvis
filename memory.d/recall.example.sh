#!/bin/bash
# A recall command, as small as one can be: grep over a folder of notes.
#
#   memory.d/recall.example.sh "what did we decide about the deploys"
#
# Copy this to memory.d/recall.sh, point NOTES at your own folder, and switch
# `enabled` on in config/memory.toml. No vector database involved - use this to
# see the wiring work before you put a real store behind it.
#
# The contract is the whole interface:
#   argument 1  the question, exactly as transcribed
#   stdout      the context, plain text, no markup - it goes into a prompt
#   exit 0      even when nothing was found; print nothing instead
set -u

NOTES="${JARVIS_NOTES:-$HOME/notes}"
LINES="${JARVIS_NOTES_LINES:-12}"

Q="${1:-}"
[ -z "$Q" ] && exit 0
[ -d "$NOTES" ] || exit 0

# Words worth searching for: four letters or more, at most three of them.
# Everything shorter is grammar, and grammar matches every note there is.
read -r -a WORDS <<< "$(printf '%s' "$Q" | tr '[:upper:]' '[:lower:]' \
  | tr -cs '[:alnum:]' ' ')"
PICKED=()
for w in "${WORDS[@]}"; do
  [ "${#w}" -ge 4 ] && PICKED+=("$w")
  [ "${#PICKED[@]}" -ge 3 ] && break
done
[ "${#PICKED[@]}" -eq 0 ] && exit 0

PATTERN=$(printf '%s\|' "${PICKED[@]}"); PATTERN="${PATTERN%\\|}"

grep -rih --include='*.md' --include='*.txt' -- "$PATTERN" "$NOTES" 2>/dev/null \
  | sed 's/^[[:space:]#>*-]*//' \
  | grep -v '^$' \
  | head -n "$LINES"
