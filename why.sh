#!/bin/bash
# What happened to the last takes: the trace, newest at the bottom.
#   why.sh        - last 60 lines
#   why.sh 200    - last 200 lines
#   why.sh levels - the same, with per-frame level lines kept
LOG="$HOME/.claude/jarvis/listener.log"
[ -f "$LOG" ] || { echo "лога ещё нет: слушатель не перезапускался после правки"; exit 1; }
if [ "$1" = "levels" ]; then
  tail -80 "$LOG"
else
  grep -v "уровни по кадрам" "$LOG" | tail -"${1:-60}"
fi
