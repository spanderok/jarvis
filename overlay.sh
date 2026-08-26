#!/bin/bash
# Floating "what is Jarvis doing" badge, above all windows. Ctrl+C to stop.
# Dragging on, click-through everywhere else. Both at once is only possible with
# the cursor watch in jarvis_overlay.py: the window catches the mouse while the
# pointer is over the capsule and lets it through anywhere in the shadow margin.
# the owner asked for dragging to work in sleep too, where the capsule is a 52 pt
# circle - so the watch is the thing that makes that circle the only live target.
# JARVIS_OVERLAY_DRAG=0 gives up dragging and removes the watch entirely.
export JARVIS_OVERLAY_DRAG="${JARVIS_OVERLAY_DRAG:-1}"
exec uv run --quiet $HOME/.claude/jarvis/jarvis_overlay.py
