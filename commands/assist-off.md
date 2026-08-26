Turn Jarvis off in THIS session: release the microphone and stop listening. The session itself carries on as usual.

Do exactly this, and start nothing back up:

1. **Stop the listener.** With `TaskStop` first - the monitor that `/assist` started in this session. If the task id is unknown (the session was restarted, or somebody else started the monitor), kill it by pid file:

```bash
pid=$(cat ~/.claude/jarvis/listener.pid 2>/dev/null)
[ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "listener $pid stopped" || echo "no listener running"
```

2. **Check the microphone was released:**

```bash
python3 ~/.claude/jarvis/agents_status.py | head -1
```

The answer should begin with the microphone being free.

3. **Report in one line.** Say nothing out loud - the voice was just switched off.

## What this does not switch off

- The badge and the menu bar icon stay alive; they simply show that there is no listener. Leave them running.
- The `voice-answer` skill goes on working: an answer can be spoken with no microphone at all.
- The other way to release the microphone is `/assist stop`, which is the same thing. `assist-off` exists as its own command because it is easier to say and to type.

## If another session holds the microphone

The first line of `agents_status.py` names it. It has to be switched off there - one session cannot stop another one's monitor. Taking the microphone for yourself, rather than switching it off entirely, is what `/assist` does: it kills the other listener with `take-mic.sh` first, then starts its own.
