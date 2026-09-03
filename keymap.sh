#!/bin/bash
# Turn a spare key on your keyboard into the Jarvis key, without any extra app.
#
#   keymap.sh list      show connected keyboards with their VendorID/ProductID
#   keymap.sh on        apply the remap (default)
#   keymap.sh off       undo it
#
# Configure it in ~/.claude/jarvis/jarvis.env (see jarvis.env.example):
#   JARVIS_KEYMAP_VENDOR   VendorID of the keyboard, from `keymap.sh list`
#   JARVIS_KEYMAP_PRODUCT  ProductID of the keyboard, e.g. 419
#   JARVIS_KEYMAP_SRC      HID usage of the key you press, default 0x4D (End)
#   JARVIS_KEYMAP_DST      HID usage it becomes, default 0x6D (F18)
#
# Why F18 and not F13: F13 is Print Screen on a Mac and apps do react to it -
# a macro key mapped to F13 once wiped a cell in Google Sheets. F16..F20 have no
# system meaning and no application binds them by default.
#
# HID usages for the usual suspects (full list: HID Usage Tables, keyboard page):
#   End 0x4D   PageUp 0x4B   PageDown 0x4E   Insert 0x49   F13 0x68   F18 0x6D
#   F19 0x6E   F20 0x6F      RightOption 0xE6
#
# Whatever key you end up with, tell the daemon its pynput name via
# JARVIS_TAP_KEYS (F18 is "<f18>"). `uv run probe_key.py` prints the name of any
# key you press.
set -u
ENV_FILE="${JARVIS_ENV:-$HOME/.claude/jarvis/jarvis.env}"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

SRC="${JARVIS_KEYMAP_SRC:-0x4D}"
DST="${JARVIS_KEYMAP_DST:-0x6D}"
VENDOR="${JARVIS_KEYMAP_VENDOR:-}"
PRODUCT="${JARVIS_KEYMAP_PRODUCT:-}"

if [ "${1:-on}" = "list" ]; then
  # hidutil prints the ids in hex and repeats a device once per service; the
  # matching dictionary wants them in decimal, so print both.
  printf '%-8s %-8s %-8s %-8s %s\n' VENDOR PRODUCT '(hex' 'hex)' DEVICE
  hidutil list | awk 'NF > 6 && $1 ~ /^0x/ {
      name = ""
      for (i = 9; i <= NF; i++) name = name (i > 9 ? " " : "") $i
      gsub(/ *\(null\) *[0-9]* *$/, "", name)
      key = $1 "|" $2
      if (!(key in seen) && name != "" && name != "(null)") { seen[key] = 1; print $1, $2, name }
    }' | while read -r v p name; do
      printf '%-8d %-8d %-8s %-8s %s\n' "$((v))" "$((p))" "$v" "$p" "$name"
    done
  echo
  echo "Put the two decimal numbers of your keyboard into $ENV_FILE:"
  echo "  JARVIS_KEYMAP_VENDOR=<VENDOR>"
  echo "  JARVIS_KEYMAP_PRODUCT=<PRODUCT>"
  exit 0
fi

# Scoping the remap to one keyboard keeps End working everywhere else, so a
# keyboard to point at is required rather than optional: without one there is
# nothing to remap and the script says so instead of pretending.
if [ -n "$VENDOR" ] && [ -n "$PRODUCT" ]; then
  MATCH="{\"VendorID\":$((VENDOR)),\"ProductID\":$((PRODUCT))}"
else
  MATCH=""
fi

if [ "${1:-on}" = "off" ]; then
  # Undoing is safe to aim at everything: an empty mapping is the default state.
  [ -z "$MATCH" ] && MATCH='{}'
  hidutil property --matching "$MATCH" --set '{"UserKeyMapping":[]}' >/dev/null
  echo "keymap off (the key works as itself again)"
  exit 0
fi

# No keyboard to point at, so there is nothing to remap. Saying so on stdout
# rather than stderr matters: the daemon copies this line into listener.log,
# and it used to log "keymap on" for a remap that had never happened.
if [ -z "$MATCH" ]; then
  echo "keymap skipped: JARVIS_KEYMAP_VENDOR/PRODUCT are not set in $ENV_FILE."
  echo "  Run 'bash keymap.sh list' to find your keyboard, or use a key you"
  echo "  already have - JARVIS_TAP_KEYS in jarvis.env, see 'The key' in README."
  exit 0
fi

# hidutil wants the HID page in the number: keyboard page 0x07 shifted up,
# plus the usage of the key itself.
src_full=$(printf '0x%X' $(( 0x700000000 + SRC )))
dst_full=$(printf '0x%X' $(( 0x700000000 + DST )))
dst_dec=$(( 0x700000000 + DST ))

hidutil property --matching "$MATCH" \
  --set "{\"UserKeyMapping\":[{\"HIDKeyboardModifierMappingSrc\":$src_full,\"HIDKeyboardModifierMappingDst\":$dst_full}]}" >/dev/null

# hidutil exits 0 even when it matched no device, so the only honest report is
# reading the property back off the keyboard - it prints the mapping in decimal,
# whatever base it was given.
if hidutil property --matching "$MATCH" --get "UserKeyMapping" 2>/dev/null \
     | grep -q "$dst_dec"; then
  echo "keymap on ($SRC -> $DST -> Jarvis)"
else
  echo "keymap failed: no keyboard with VendorID $VENDOR / ProductID $PRODUCT is"
  echo "  connected, the key was not remapped. 'bash keymap.sh list' shows what is here."
  exit 1
fi
