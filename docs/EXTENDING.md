# Rooms and actions

Two things happen to a phrase Jarvis hears. Either he answers it himself, or it
goes somewhere. Where it can go, and what it can set off, is two TOML files -
no Python, no fork.

A **room** is a Claude session in a Terminal window that he can hand work to.
A **action** is something answered here on the machine: a command runs and he
says what came out.

Neither is built in. The chief you get out of the box is a block in
`config/rooms.toml`, and deleting that block deletes the chief.

## One question, end to end

You say **"передай шефу выкати демо на тест-четыре"**.

1. The wake engine hears "Джарвис", records the sentence, and
   `parakeet-mlx` transcribes it. What reaches the router is
   `передай шефу выкати демо на тест четыре` - lower case, no punctuation.

2. **Explicit addressing first.** `config/rooms.toml` gives the chief a list of
   openings:

   ```toml
   tell = ["передай шефу", "скажи шефу", "поручи шефу", "пусть шеф"]
   ```

   The sentence starts with `передай шефу`, so the room is `chief`, the task is
   `выкати демо на тест четыре`, and the kind is `tell` - hand it over, do not
   wait. Nothing else is consulted. An order always outranks a guess.

3. **The window.** `room.sh chief` started that session as `claude -n шеф`, so
   Jarvis finds the window by the name `шеф` and types the task into it, exactly
   as if you had typed it. The whole dialogue stays visible in that window.

4. **The acknowledgement** is the room's own line, not a string in the code:

   ```toml
   ack_tell = "Передал шефу."
   ```

   He says it and is free for the next question. The chief works in its own
   window on its own time.

Change `label` and those four words and he addresses the room in yours. Rename
the id and `room.sh`, `launch-agent.sh` and `--selfcheck` follow, because none
of them has the name written down.

## The order things are tried

Top to bottom, first match wins:

1. **An order** - one of a room's `ask` or `tell` openings. Beats everything.
2. **An action** - a pattern in `config/actions.toml`, in file order.
3. **A room's `mention_patterns`** - a word that sends the phrase there from
   anywhere in the sentence.
4. **A bare name** - `шеф, ...` at the very start. Deliberately the weakest.
5. **Nothing matched** - Jarvis answers it himself.

## Adding a room

Copy `rooms.d/my-room.example.toml` to `rooms.d/notes.toml`:

```toml
[[room]]
id = "notes"
label = "заметки"
session = "заметки"
work_dir = "~/notes"
launch = "room.sh notes"
ask = ["спроси заметки", "спроси у заметок"]
tell = ["запиши в заметки"]
ack_ask = "Спросил заметки."
ack_tell = "Записал."
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

It routes on a word appearing anywhere in the sentence, with no "передай" in
front. Convenient, and it steals ordinary questions the moment the word is
something people say by accident.

An early version of the chat room routed on `непрочитанное`, `сводка по чатам`
and `кто мне писал`. Then "что написал Петров" - a question about a commit -
went to the chat agent. The fix was to route on the messenger's own name
instead, which nobody says unless they mean it.

**Name the thing, never the topic.** `слак`, `телеграм`, `обсидиан` are names.
`чат`, `заметки`, `почта` are topics, and they will fire on questions that had
nothing to do with the room.

Write down what must *not* route there, next to the room:

```toml
[[room.example]]
phrase = "что написал Петров"
route = ""
```

`jarvis_daemon.py --selfcheck` runs every example in the config and prints
`routing: 13/13 ok`. The negative ones are the ones that catch the mistake.

## Adding an action

An action never reaches a room. It runs a command and Jarvis speaks the result:

```toml
[[action]]
id = "timer"
patterns = ['^(?:поставь )?таймер (?:на )?(?P<minutes>\d+)']
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
exact: in Russian, list every case form, or `агентом` slips through a list that
only says `агент`.

## Checking your work

```bash
python3 plugins.py                    # what loaded, and the routing examples
python3 plugins.py rooms              # the ids, in order
python3 plugins.py get chief session  # one field, the way room.sh reads it
uv run --with pytest pytest tests/ -q # 43 tests, config included
uv run jarvis_daemon.py --selfcheck   # plus which windows are actually up
```

A config with a broken regex, an id that does not exist or a `speak` mode that
is not a mode stops the daemon at start with the file name in the message. It
is refused rather than half-loaded on purpose: a question that quietly stops
reaching the room it was meant for is worse than one that was never asked -
nobody notices for days.
