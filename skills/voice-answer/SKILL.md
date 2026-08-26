---
name: voice-answer
description: Speak a short answer aloud in Jarvis' own voice on this Mac, and/or send it as a Telegram voice message. Use it when the owner asks for a spoken answer, asks you to say something out loud, to read the result back, or to send a voice message - including when the request is phrased as "do X and answer out loud".
---

# voice-answer

Any agent can report a result out loud, in the same voice Jarvis speaks with. It runs on a local model - piper for English, vosk for Russian - with no network, no MCP and no call to the daemon.

## How to call it

```bash
bash ~/.claude/skills/voice-answer/speak.sh "tests are green, fixed one snapshot"
bash ~/.claude/skills/voice-answer/speak.sh --telegram "the demo deploy went through"
bash ~/.claude/skills/voice-answer/speak.sh --both "review is done, three notes"
```

- no flag - out loud on this Mac
- `--telegram` - a voice message to the bot's direct chat, says nothing aloud
- `--both` - both at once
- `--file /tmp/x.ogg` - build the file only, send nothing
- `--stop` - shut the current speech up immediately

**"Be quiet" is carried out by any agent, not only the one talking.** The owner often types in another session's window, and the keys go there. So on "be quiet", "enough", "stop", run this at once:

```bash
bash ~/.claude/skills/voice-answer/speak.sh --stop
```

It kills the player and releases the lock, even when a different session is the one talking.

If the `~/.claude/skills` symlink is not set up, take the path from the repository instead: `~/.claude/jarvis/skills/voice-answer/speak.sh`.

## What is worth speaking

Text for an ear, not for a screen - otherwise it cannot be listened to:

- three or four sentences of ordinary speech
- no markdown, no lists, no paths, no links, no session identifiers
- numbers and ticket ids as words: "ticket fourteen seven oh three", not `ABC-14703`
- people by surname, not by login

**The full answer is still written out in the session as text.** The voice is an addition to the report, never a replacement: the owner reads the detail with their eyes and hears the conclusion.

## A spoken report is one-way

If the microphone is not held by this session, the owner cannot answer out loud - they may not even be looking at this window. So a spoken report ends in a statement, not an invitation to talk:

- ask no questions out loud, and never say "waiting for your answer", "tell me what next", "please confirm"
- do not wait for a reaction: report and end the turn
- if a decision really is needed to carry on, say out loud that the work has stopped and why, and leave the question itself as text in the session, to be read when they get to it

Checking who holds the microphone, when it matters to the wording:

```bash
bash ~/.claude/jarvis/take-mic.sh --dry-run
```

An answer naming a session that is not this one means: speak in statements. If this session holds it, a spoken conversation makes sense - the owner will say the next phrase and it will arrive as an event.

Never speak or send tokens, passwords or the contents of keys - a voice message goes through Telegram's servers.

## When to use it without being reminded

- The owner asked for a spoken answer, or for a voice message.
- They asked at the start of a long task to be told when it is finished - then a spoken line at the end is right.

If they simply handed over a task, stay quiet. Sound nobody asked for is an irritation.

## How it works

One phrase end to end, from the request to the sound:

1. The agent calls `speak.sh` with the text.
2. The script takes a sha1 of the text, the voice and the rate, and looks in `~/.claude/tts-cache`. Repeating the same phrase plays instantly and costs nothing.
3. Not in the cache: the local model synthesises a wav - about 0.3 s for a new phrase with piper, 0.9 s with vosk, 0.01 s for a repeat either way.
4. For Telegram the wav is converted to ogg with the opus codec - in that form the message shows up as a real voice message, with a waveform and a speed button, rather than as a file attachment.
5. Sending is `sendVoice`; the token and chat id come from the Keychain (`jarvis-telegram-token`, `jarvis-telegram-chat`) and never reach the log.
6. Local playback is `afplay` under a directory lock at `~/.claude/tts-cache/.speak.lock`, so two agents finishing at the same moment do not talk over each other.

No network is needed for either the voice or the voice message. If the local model is not in place, the script goes to the network for a Microsoft neural voice (`VOICE_ENGINE=edge` forces that), and with no network at all it falls back to the system voice.

Fixing how a word is pronounced is a one-time thing. For the Russian voice: `~/.claude/jarvis/venv-vosk/bin/python ~/.claude/jarvis/vosk_dict.py тЕстовый` - the capital letter marks the stressed vowel.

## The answer will not come back to you

One session holds the microphone, and the owner's answer lands there whoever was talking. So this skill writes its own session name and the time into `~/.claude/jarvis/last_speaker` on every phrase it speaks.

The session with the microphone reads that file and forwards the answer by name when the phrase was not addressed to it. Nothing is required of you - just remember that a question you asked out loud comes back by a roundabout route, and not instantly.

## In a call: Telegram instead of the speakers

If a video call is open and the sound is going to the built-in speakers, the skill does not speak aloud - the phrase goes to Telegram as a voice message instead. The check lives in `~/.claude/jarvis/call_guard.sh` and takes a third of a second.

It works that way because a mute button cannot be read from outside, and the harm from a voice only exists when the room hears it: on headphones the same call is safe, and the skill speaks as usual.

Turn the check off with `JARVIS_CALL_GUARD=off`.

## Never speak unasked

Speak only about what the owner asked for: the question arrived by voice, or they explicitly asked for it out loud, or it is a report on a task they handed over themselves.

Background events are never announced out loud. A new message, a failed pipeline, a ticket changing status - all of that goes quietly, as text in the session. They are sitting in headphones working, and an unexpected voice throws them off more than it helps.

The one exception is being told something like "tell me out loud about every new message". Then speak exactly what was named, for the rest of the session.

## Limits

- The lock only holds agents. The Jarvis daemon speaks on its own queue and knows nothing about this lock, so it is still possible to talk over it - if it is answering out loud at that moment, waiting is better.
- The `+x` bit does not always survive being synced or copied between machines, so call the script through `bash <path>` rather than directly.
