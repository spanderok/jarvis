#!/bin/bash
# Start the Jarvis menu bar indicator (needs to run from your own GUI session).
exec uv run --quiet $HOME/.claude/jarvis/jarvis_status.py
