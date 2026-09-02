# Rooms, actions, memory and languages

Four things about Jarvis are config files rather than code. This page walks
each one, starting with the two that decide where a phrase goes.

A **room** is a Claude session in a Terminal window that he can hand work to.
An **action** is something answered here on the machine: a command runs and he
says what came out.

Neither is built in. The chief you get out of the box is a block in
`config/rooms.toml`, and deleting that block deletes the chief.

## One question, end to end

You say **"tell the chief to ship the demo to test four"**.

1. The wake engine hears the name, records the sentence, and `parakeet-mlx`
   transcribes it. What reaches the router is
   `tell the chief to ship the demo to test four` - lower case, no punctuation.

2. **Explicit addressing first.** `config/rooms.toml` gives the chief a list of
   openings:

   ```toml
   tell = ["tell the chief", "pass this to the chief", "have the chief"]
   ```

   The sentence starts with `tell the chief`, so the room is `chief`, the task
   is `to ship the demo to test four`, and the kind is `tell` - hand it over, do
   not wait. Nothing else is consulted. An order always outranks a guess.

3. **The window.** `room.sh chief` started that session as `claude -n chief`, so
   Jarvis finds the window by the name `chief` and types the task into it,
   exactly as if you had typed it. The whole dialogue stays visible there.

4. **The acknowledgement** is the room's own line, not a string in the code:

   ```toml
   ack_tell = "Passed it to the chief."
   ```

   He says it and is free for the next question. The chief works in its own
   window on its own time.

Change `label` and those openings and he addresses the room in your words.
Rename the id and `room.sh`, `launch-agent.sh` and `--selfcheck` follow, because
none of them has the name written down.

## The order things are tried

Top to bottom, first match wins:

1. **An order** - one of a room's `ask` or `tell` openings. Beats everything.
2. **An action** - a pattern in `config/actions.toml`, in file order.
3. **A room's `mention_patterns`** - a word that sends the phrase there from
   anywhere in the sentence.
4. **A bare name** - `chief, ...` at the very start. Deliberately the weakest.
5. **Nothing matched** - Jarvis answers it himself.

## Adding a room

Copy `rooms.d/my-room.example.toml` to `rooms.d/notes.toml`:

```toml
[[room]]
id = "notes"
label = "notes"
session = "notes"
work_dir = "~/notes"
launch = "room.sh notes"
ask = ["ask notes", "ask my notes"]
tell = ["put this in my notes", "write this down"]
ack_ask = "Asked your notes."
ack_tell = "Written down."
```

`bash room.sh notes` raises it. `jarvis_daemon.py --selfcheck` now lists it and
says whether its window was found. Nothing else was edited.

Files in `rooms.d/` are merged over `config/rooms.toml` by id, so the same
mechanism changes a shipped room without touching a file `git pull` overwrites:

```toml
[[room]]
id = "chief"
session = "boss"        # only this field; the rest stay as they were
```

and switches one off:

```toml
[[room]]
id = "chief"
enabled = false
```

## About `mention_patterns`

This is the one field that can ruin your day, so it is off by default.

It routes on a word appearing anywhere in the sentence, with no "tell" in
front. Convenient, and it steals ordinary questions the moment the word is
something people say by accident.

An early version of the chat room routed on `unread`, `chat summary` and
`who messaged me`. Then "what did Petrov write" - a question about a commit -
went to the chat agent. The fix was to route on the messenger's own name
instead, which nobody says unless they mean it.

**Name the thing, never the topic.** `slack`, `telegram`, `obsidian` are names.
`chat`, `notes`, `mail` are topics, and they will fire on questions that had
nothing to do with the room.

Write down what must *not* route there, next to the room:

```toml
[[room.example]]
phrase = "what did Petrov write"
route = ""
```

`jarvis_daemon.py --selfcheck` runs every example in the config and prints
`routing: 13/13 ok`. The negative ones are the ones that catch the mistake.

## Adding an action

An action never reaches a room. It runs a command and Jarvis speaks the result:

```toml
[[action]]
id = "timer"
patterns = ['^(?:set a )?timer (?:for )?(?P<minutes>\d+)']
run = "actions.d/timer.sh {minutes}"
speak = "stdout"
```

Named groups in the pattern become `{placeholders}` in `run`. Everything else
about the command line is fixed - which is the point. A route that cannot be
rewritten by what was said cannot be talked into running something else, and
that is why music is an action here rather than a `Bash` tool call in the
daemon's own session.

Three ways to speak:

- `speak = "stdout"` - say the output as it stands. `formatter` names a
  function in `formatters.py` when the output has a shape of its own.
- `speak = "retell"` - hand the output to Jarvis inside `prompt` and let him
  answer in his own voice. For commands that print facts, not sentences.
- `speak = "none"` - silence.

`[action.words]` passes content words from the phrase itself as arguments, so a
script can know what was asked about without learning the language. Matching is
exact per word form: in an inflected language, list every case, or the ones you
missed slip through a list that only spells out the nominative.

## Long-term memory

His own session forgets everything ten minutes after the last question. A
vector store is how he gets at anything older - past decisions, notes, a wiki,
a whole vault. `config/memory.toml` points at two commands:

```toml
[memory]
enabled = true
recall = "memory.d/recall.sh"        # question in, context out
remember = "memory.d/remember.sh"    # question and answer in
timeout_s = 3.0
```

`recall` gets the question as its one argument and prints plain text. That text
goes in front of the question before Jarvis is asked. `remember` gets the
question and the answer, runs detached after he has spoken, and its output is
ignored.

A command and not a Python API on purpose: Chroma, Qdrant, LanceDB, sqlite-vec
or a grep over a folder of markdown are all a shell script, and none of them
becomes a dependency of this repository. `memory.d/` ships a working grep
example and a Chroma one.

Recall sits between the question and the first spoken word, so `timeout_s` is a
budget rather than a suggestion - three seconds of silence after a question
already reads as "he did not hear me". Everything about it fails open: a store
that is down, slow or shouting on stderr costs you the context, never the
answer.

```bash
uv run memory.py "a question you would ask"   # what comes back, and how fast
```

## Another language

`JARVIS_LANG` picks a file in `locales/`. English and Russian ship; adding one
is copying a file. It holds everything he says or listens for - the wake word
and the ways a recognizer mangles it, the stop words, the persona prompt, the
lines he says about rooms - plus the models that can handle that language, so a
prompt in one language cannot end up read by a voice in another.

The routing vocabulary is separate, because it belongs to your rooms rather than
to the language. `rooms.d/ru.example.toml` and `actions.d/ru.example.toml` are
complete Russian presets: copy them without the `.example`, and only the wording
changes - the launcher, the tint and the commands stay as they were.

```bash
uv run lang.py                    # what loaded, and the prompt it builds
uv run lang.py list               # the languages present
```

## Checking your work

```bash
uv run plugins.py                    # what loaded, and the routing examples
uv run plugins.py rooms              # the ids, in order
uv run plugins.py get chief session  # one field, the way room.sh reads it
uv run --with pytest pytest tests/ -q # the whole suite, config included
uv run jarvis_daemon.py --selfcheck   # plus which windows are actually up
```

A config with a broken regex, an id that does not exist, a `speak` mode that is
not a mode, or a locale missing half its sentences stops the daemon at start
with the file name in the message. It is refused rather than half-loaded on
purpose: a question that quietly stops reaching the room it was meant for is
worse than one that was never asked - nobody notices for days.
