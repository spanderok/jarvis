Attach the microphone to THIS session: you go on working as usual, but you also hear the owner when they call your name, and you answer out loud.

Argument in `$ARGUMENTS`: empty - turn it on, `stop` - release the microphone, `status` - say who is holding it, `kill` - shut the voice up immediately.

## If `$ARGUMENTS` = kill

One command, nothing else:

```bash
bash ~/.claude/skills/voice-answer/speak.sh --stop
```

Works from any session and silences the voice even when another one is talking. Answer in one line, and say nothing out loud.

## If `$ARGUMENTS` = status

Run this and retell it in a line or two. Start nothing, stop nothing:

```bash
python3 ~/.claude/jarvis/agents_status.py | head -1
```

The first line of the output is about the microphone: free, or held by a named session. Works from any session, even when the listener is running in a different one.

The same thing as `stop` has its own command, `/assist-off` - shorter to type and shorter to say.

## If `$ARGUMENTS` = stop

1. Stop the listener with `TaskStop` (the id of the Monitor task you started in this session).
2. Say in one line that the microphone is released, and remind them that the full daemon comes back with `bash ~/.claude/jarvis/jarvisd.sh` or the `/jarvis-daemon` command.
3. Do nothing else.

## With no argument - turn it on

**`/assist` with no argument is always a restart.** Even when the microphone is already here and the listener is alive, still go through every step from the first: kill the old listener and start a new one. Answering "already on" and doing nothing is wrong - the owner types the command again precisely when a change in `jarvis_daemon.py` needs picking up, and every number and key is read once, at start.

**Step 1. Take the microphone.** There is only one, so turning it on here turns it off everywhere else - the standalone daemon, another session's listener, and your own. Do not ask for permission: the owner typed the command in this window on purpose.

```bash
bash ~/.claude/jarvis/take-mic.sh
```

The script answers in one line saying who it took it from: a named session, the daemon, or nobody. Pass that line on, so the owner knows where Jarvis just went quiet.

**Step 2. Kill your own previous monitor.** If a `Monitor` with the listener was already started in this session, stop it with `TaskStop` - otherwise a dead task is left behind that hears nothing.

**Step 3. Start the listener** with the `Monitor` tool, `persistent: true` is required:

```
Monitor({
  command: "bash ~/.claude/jarvis/listen.sh",
  description: "voice: listening for the wake word in this session",
  persistent: true,
})
```

**Step 4. Raise the badge** if it is not up - it is what shows listening / thinking / talking:

```bash
pgrep -f jarvis_overlay.py >/dev/null || nohup bash ~/.claude/jarvis/overlay.sh >/dev/null 2>&1 &
```

The menu bar icon (`status.sh`) is not started any more: its menu is covered by the key, by `/assist-off` and by `/jarvis-daemon`. The files are still there, and `bash ~/.claude/jarvis/status.sh` brings it back by hand.

**Step 5. Report in two lines** - that the microphone is yours, and that they should start with the wake word. Do not speak on this step.

## What to do with the listener's events

Every line from the monitor is an event, not a chat message from the owner.

| Line | What it means | What to do |
|---|---|---|
| `LISTENING: …` | the listener is up | nothing, it is a confirmation |
| `HEARD: <text>` | the owner said this out loud | treat it as an ordinary request |
| `BUSY: … pid N` | somebody took the microphone between steps | wait a couple of seconds, run `take-mic.sh` again and restart the monitor |

Work on a `HEARD` goes the usual way, with all your tools and the project's rules. Four things differ.

0. **Repeat what you heard in the chat first** - before any work, as a quote:

```
You said:
> <the whole transcribed phrase>
```

The owner does not read the monitor's events, so without this it looks as if the agent walked off to think in silence. The quote does two things at once: they see the question arrived, and they see what the transcription made of it - it does get things wrong, and the quote explains at a glance why an answer is about the wrong thing.

1. **The text was transcribed automatically, so it has mistakes in it** - work out what was meant, and do not ask again over small things.

2. **When you are done, tell the badge.** If you answered in text with no voice, run `bash ~/.claude/jarvis/answered.sh`. Otherwise "thinking" hangs there until the timeout: only the start of speech clears it, and there was none. If the answer was spoken, nothing to do - the speech lock clears it by itself.

3. **Speak only in reply to something spoken, and only to what was asked.** Background events - a new message, a failed pipeline, a status change - are never announced out loud until they explicitly ask for that. Answering one question about the chat is not a subscription to every new message in it. A question that arrived as a `HEARD` event gets both voice and text. A question typed into the chat by hand gets text only, never read aloud: they are looking at the screen, and speech there is noise. The one exception is being asked to say it out loud.

   **Voice first, then the text.** The moment the answer is clear, say the short version out loud, and only then write the details into the chat. The owner is listening, not reading: put the long write-up first and they sit in silence waiting for the voice the whole time you are typing. Write the text afterwards as thoroughly as always.

   The voice is the `voice-answer` skill:

```bash
bash ~/.claude/skills/voice-answer/speak.sh "done, tests are green, fixed one snapshot"
```

The rules for the voice live inside that skill: three or four sentences, no markup, no paths, numbers as words.

4. **A phrase may not be addressed to you - forward it.** There is one microphone but any agent can report through it, so the owner's answer always arrives here, even when somebody else asked.

   Before working on a phrase, look at `~/.claude/jarvis/last_speaker`: the first line is the session that spoke last, the second is how many seconds ago. If the name is **not** yours, it was under ten minutes ago, and the phrase sounds like an answer to somebody else's report rather than a task for you - forward it with `SendMessage` to that session by name and tell the owner in one line where it went. Your own name in the file, or a stale timestamp: it is yours, work on it.

5. **A new phrase while you are still working goes in a queue, and answers come in order.** The listener hears the wake word during "thinking" too, so a second phrase arrives as its own event. The order is fixed: quote it into the chat, finish the first answer and speak it, and only then start the second. Do not announce the queue out loud - the quote in the chat already shows the phrase was taken.

   The key is a different matter: it means "be quiet and listen to me", not "join the queue". It cuts the current speech off, the rest of the unfinished answer is not read, and you listen to the new phrase.

6. **Long work gets two spoken lines.** Say at once, in one sentence, that you have started, and say it again when you are done. Ten minutes of silence is not acceptable - they are not looking at the screen.

   The answer is always spoken, however long the work took. After five minutes the listener clears "thinking" from the badge itself - that is an indicator, not a cancellation. Reaching an answer after fifteen minutes still means saying it out loud, or as far as the owner is concerned it simply vanished.

7. **Do not ask questions out loud when the microphone is not yours.** While `/assist` is on here, a spoken conversation makes sense. Once the microphone moves to another session, reporting becomes one-way - see the `voice-answer` skill, the section on one-way reports.

The listener falls silent by itself while you talk: the speech skill holds the lock at `~/.claude/tts-cache/.speak.lock`, and no recording happens then. That is why it never records your own voice.

The indicator is its business too, so never write the state by hand: it sets "listening" while recording, "thinking" from the moment a phrase reaches you, and "talking" while the speech lock is held. Work longer than five minutes and "thinking" clears itself.

## Limits

- Starting the full daemon takes the microphone back: `jarvisd.sh` kills the agent's listener and cleans up its pid file. The monitor in this session then ends on its own - that is normal, not a fault.
- **The monitor died - do not blindly restart it.** Look at `~/.claude/jarvis/listener.owner` first: another session's name there, or `agents_status.py` saying the microphone is busy, means somebody took it on purpose and there is nothing to do but tell the owner where Jarvis is now. Restart it only when the microphone is free and they asked for it.
- **Never `pkill` the listener by hand.** The only legitimate way to take the microphone is `take-mic.sh`: it kills the current holder only, and says who that was. A bare `pkill -f "jarvis_daemon.py --listen"` hits other sessions too - that is exactly how a listener that had just been started in another window got killed on 21.08.
- There is one listener per machine, its pid in `~/.claude/jarvis/listener.pid` and the owning session's name in `~/.claude/jarvis/listener.owner`. A second one refuses to start (`BUSY`), which is why turning it on always begins with `take-mic.sh` - take it, do not stand next to it.
- The owner's name shows up in three places: on the badge next to the state, in the `LISTENING` line at startup, and as the first line of `agents_status.py`. The last one works from any session - that is how to answer "which agent has Jarvis right now".
- Works only where the code and the models are installed: `~/.claude/jarvis/` and its `models/` folder do not travel between machines. On a machine without them, say so plainly and do not try to fix it.
- The terminal may not have microphone permission, and then the listener dies with an access error. Pass the error text on - permissions are granted by hand in System Settings.
