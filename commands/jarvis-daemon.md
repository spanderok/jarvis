Give the microphone back to the full Jarvis: kill the listener in this session and start the standalone daemon. Works from any session, and needs neither a voice shortcut nor a free microphone.

Spoken phrasings that should also reach this command: "bring the daemon back", "switch to the normal Jarvis", "start the daemon", "give the microphone back to Jarvis".

## Why this exists

There is one microphone. `/assist` takes it into an agent session on purpose, and the way back used to run through a voice shortcut - which is a circle that does not open: bringing the daemon back needs the assistant, and the assistant needs the microphone the agent is holding. Voice cannot break that circle, because the voice is the occupied resource.

Caught on 22.08. Until that day the way back had always been done through the voice shortcut, and nobody had tried it with the microphone already taken.

## What to do

**Step 1. Kill your own listener, if it is in this session.** `TaskStop` on the monitor id that `/assist` started here. Not knowing the id is fine - step 2 finishes the job.

**Step 2. Start the daemon.**

```bash
nohup bash ~/.claude/jarvis/jarvisd.sh > ~/.claude/jarvis/daemon.log 2>&1 &
```

`jarvisd.sh` kills the agent listener itself, by the `jarvis_daemon.py --listen` pattern, and cleans up `listener.pid` and `listener.owner` - none of that needs doing separately. Do not reach for `pkill` by hand: it hits listeners in other sessions too.

In the background through `nohup`, not directly: `jarvisd.sh` ends in an `exec` and holds the terminal, while this command has to return.

**Step 3. Check it came up.** After ten or twelve seconds:

```bash
cd ~/.claude/jarvis && echo "pid $(cat daemon.pid)" && tail -3 daemon.log
```

The log should hold `Jarvis daemon up` and `asr worker ready`. An empty `daemon.pid`, or those lines missing, means showing the owner the tail of the log rather than guessing.

**Step 4. Raise the badge if it is not up.**

```bash
pgrep -f jarvis_overlay.py >/dev/null || nohup bash ~/.claude/jarvis/overlay.sh >/dev/null 2>&1 &
```

**Step 5. Report.** If the request arrived by voice, answer in one spoken sentence and repeat it in text: the daemon is up, it has the microphone, call it by the wake word. The monitor in this session ends by itself after step 2 - that is normal, not a fault.

## The way back

Taking the microphone into an agent session again is `/assist`. It is one-way in the same manner: it kills the daemon and starts a listener.

## Limits

- Works only where the code and the models are installed - `~/.claude/jarvis/` and its `models/` folder do not travel between machines.
- The daemon and a listener never live side by side; a second instance will not start, because there is one microphone.
- The daemon speaks on its own queue and does not watch the agents' speech lock. The badge already accounts for that, and needs no fixing.
