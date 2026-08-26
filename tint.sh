#!/bin/bash
# Mark the Terminal window this script runs in, so the chef window is not mixed
# up with the other ones. Terminal exposes no window border to colour, so the
# mark is the background: the default profile, a couple of shades lighter.
#
#   bash tint.sh          # khaki, #1D1F18 - the default mark
#   bash tint.sh subtle   # #1B1D18, barely off the default background
#   bash tint.sh olive    # #1F2117, more yellow in it
#   bash tint.sh default  # back to the profile, no mark
#
# Three shades were tried and rejected on the way: lighter grey (#262626) made
# white text read worse, warm brown (#201D1A) was not wanted, and a clean green
# (#1A1F1B) came out too vivid. Khaki keeps green the highest channel but pulls
# red up close to it and drops blue, which is what makes it look muted.
#
# Default profile background is #171717 (5866 of 65535 in Terminal's 16-bit
# scale). Everything else - text, bold, cursor, selection - comes from the
# profile, so resetting to it first keeps the window ordinary in every other way.
# The window is found by the tty this shell runs on, no window id needed.

PROFILE="${JARVIS_TERM_PROFILE:-Basic}"

case "${1:-khaki}" in
  khaki)   BG="7453,7967,6168" ;;  # #1D1F18
  subtle)  BG="6939,7453,6168" ;;  # #1B1D18
  olive)   BG="7967,8481,5911" ;;  # #1F2117
  default) BG=""               ;;
  *) echo "unknown palette: $1 (khaki, subtle, olive, default)" >&2; exit 1 ;;
esac

TTY=$(tty 2>/dev/null)
[ -z "$TTY" ] && exit 0

/usr/bin/osascript <<APPLESCRIPT >/dev/null 2>&1
tell application "Terminal"
  repeat with w in windows
    try
      if tty of tab 1 of w is "$TTY" then
        set t to tab 1 of w
        set current settings of t to settings set "$PROFILE"
        if "$BG" is not "" then set background color of t to {$BG}
        exit repeat
      end if
    end try
  end repeat
end tell
APPLESCRIPT
