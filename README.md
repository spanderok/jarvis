# Jarvis

A voice assistant for macOS that answers with Claude Code. You say "Jarvis", ask
a question out loud, and he answers out loud - or hands the work over to a Claude
session that can actually do it.

Speech in and speech out are local: no cloud speech service, no audio leaving the
machine. The answer comes from the `claude` CLI you already have.

English out of the box, Russian in the box next to it. Everything he says or
listens for is a config file, so a third language is one more file - see
[Languages](#languages).

## One exchange, end to end

1. The wake engine listens to the microphone at 16 kHz. By default that is Vosk,
   a 39 MB offline recognizer that triggers on the real word "Jarvis" - free, no
   registration, no network.
2. A chime, then he records until you stop talking - or until you press the key,
   which ends the recording immediately.
3. `parakeet-mlx` transcribes locally. A leading wake word is stripped, so
   "Jarvis, what time is it" arrives as "what time is it".
4. `claude -p` answers inside one resumable session, and the reply is spoken by a
   local voice.
5. For a second and a half after the reply he keeps listening without a wake
   word, so a follow-up question needs no ceremony.

## What you need

- macOS on Apple silicon. It uses `hidutil`, `osascript`, `afplay` and the macOS
  permission model, and the transcriber is MLX.
- [`uv`](https://docs.astral.sh/uv/) - every script declares its own
  dependencies in its header, so there is no requirements file and no virtualenv
  to manage.
- The [`claude` CLI](https://claude.com/claude-code), logged in.
- About 800 MB of disk for the models.

### The models

Nothing is bundled - `install.sh` fetches each one from its own home, and
`models/` is in `.gitignore`. All of them are free and need no account.

| Model | For | Size | From |
|---|---|---|---|
| `vosk-model-small-en-us-0.15` | hearing the wake word | 39 MB | [alphacephei.com](https://alphacephei.com/vosk/models) |
| `parakeet-tdt-0.6b-v3` | transcribing the question | 600 MB | [Hugging Face](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3), pulled on first use |
| `en_GB-alan-medium` | his voice | 60 MB | [piper-voices](https://huggingface.co/rhasspy/piper-voices) |
| `campplus.onnx` | telling your voice from a stranger's | 28 MB | [CAM++ ONNX](https://huggingface.co/FunAudioLLM/CosyVoice-300M) |

Which ones depends on the language: `JARVIS_LANG=ru` swaps the first three for
the Russian recognizer and the vosk-tts voice (129 MB), and the transcriber is
shared. The URLs live in `locales/<lang>.toml`, so pointing at a different
model is a config edit rather than a patch.

The speaker model is the only optional one. Skip it with `JARVIS_NO_SPEAKER=1`
and the voice lock stays off, which is the default anyway until you record a
profile.

## Install

```bash
git clone <this repo> ~/.claude/jarvis
cd ~/.claude/jarvis
bash install.sh
```

The installer downloads the models for your language, links the two skills and
three commands into `~/.claude`, and creates `jarvis.env` from the example. Clone
it somewhere else and it symlinks that folder to `~/.claude/jarvis`, because the
scripts and skills refer to that path.

Then grant two permissions in System Settings -> Privacy & Security:

- **Microphone** for the terminal app you start him from. Without it he hears
  nothing.
- **Input Monitoring** for the same app, if you want the hotkeys. Skip it and use
  Shortcuts.app instead - see below.

Start him:

```bash
bash ~/.claude/jarvis/jarvisd.sh
```

Say "Jarvis". `Ctrl+C` stops him.

## The key

Speech recognition guesses; a key press does not. One press is your own hand
saying "switch to me", and it means something different depending on what he is
doing:

| While he is | One press | Two presses |
|---|---|---|
| listening to you | that was the whole question, answer now | forget it, back to the wake word |
| thinking or talking | interrupt, and listen to what I say next | shut up and wait |
| idle | wake up, no wake word needed | nothing |

Three ways to get that key, pick one.

**1. A spare key on your keyboard.** A macro key that sends `End` becomes F18,
and F18 is the Jarvis key. F18 because F13 is Print Screen on a Mac and
applications do react to it - a macro key mapped to F13 once wiped a cell in a
spreadsheet. F16..F20 have no system meaning and nothing binds them.

```bash
bash keymap.sh list        # find your keyboard's VendorID and ProductID
```

Put them into `jarvis.env`:

```sh
JARVIS_KEYMAP_VENDOR=1234
JARVIS_KEYMAP_PRODUCT=567
JARVIS_KEYMAP_SRC=0x4D     # the key you press: End
JARVIS_KEYMAP_DST=0x6D     # what it becomes: F18
```

```bash
bash keymap.sh on          # apply now
bash install.sh keymap     # and re-apply at every login
bash keymap.sh off         # undo
```

The remap is scoped to that one keyboard, so `End` keeps working everywhere else.

**2. Any key you already have.** No remapping needed - just tell the daemon which
key to watch:

```bash
uv run probe_key.py        # press the key, it prints the name to use
```

```sh
JARVIS_TAP_KEYS="<f13>"    # switch to me
JARVIS_DONE_KEYS="<space>" # "answer now", only while he is listening
JARVIS_OFF_KEYS="<esc>"    # shut up and wait
```

`<space>` and `<esc>` are safe defaults: space only acts while he is recording,
escape only while he is busy, so typing in an editor never touches him.

**3. No key at all.** If you would rather not grant Input Monitoring, bind
`jarvis-key.sh` to a keyboard shortcut in Shortcuts.app (Run Shell Script). It
signals the running daemon and needs no permission whatsoever:

```bash
jarvis-key.sh              # one press
jarvis-key.sh double       # two presses
```

The same thing by hand: `kill -USR1 <pid>` and `kill -USR2 <pid>`.

## Settings

Everything personal lives in `jarvis.env` - your name, the keys, timings, which
model answers. `jarvis.env.example` documents every knob with its default; copy,
edit, restart. The code ships unchanged.

The one worth setting first:

```sh
JARVIS_OWNER=Ada           # empty: he says "the owner of this computer"
```

## Two ways to run him

**Standalone daemon** - `jarvisd.sh`. His own Claude session answers, and heavy
work is handed to another window.

**Inside an agent session** - the `/assist` command. The session you are already
working in takes the microphone and starts hearing you: you ask out loud, the
agent does the work in that repository and answers out loud. `/assist-off` gives
the microphone back, `/jarvis-daemon` returns it to the standalone daemon.

There is one microphone, so only one of them can hold it. Both drop in through
the same file lock, and each tells you who it took it from.

## Rooms and actions

He answers most questions himself. The rest either go to a **room** - another
Claude session in a Terminal window - or set off an **action**, a command that
runs here and whose output he reads out.

Say "tell the chief to ship the demo" and the task is typed into the window of
the session named `chief`, where you can watch it work. Say "what's playing" and
nothing leaves the machine: a script runs and he names the track.

Neither is built in. Both are rows in two TOML files:

```toml
# rooms.d/notes.toml
[[room]]
id = "notes"
label = "notes"
session = "notes"
work_dir = "~/notes"
launch = "room.sh notes"
ask = ["ask notes", "ask my notes"]
tell = ["write this down"]
ack_ask = "Asked your notes."
```

`bash room.sh notes` raises it and he can address it. Nothing else was edited -
not the daemon, not the launcher, not his system prompt, which picks up the
room's own `hint` line.

**[docs/EXTENDING.md](docs/EXTENDING.md)** walks one question end to end, gives
the order the routes are tried in, and explains the one field that will steal
your ordinary questions if you set it carelessly.

## Long-term memory

His session forgets everything ten minutes after the last question. Point him at
a vector store and he stops forgetting:

```toml
# config/memory.toml
[memory]
enabled = true
recall = "memory.d/recall.sh"      # the question in, context out
remember = "memory.d/remember.sh"  # the question and the answer in
timeout_s = 3.0
```

`recall` is any command that takes a question as its argument and prints plain
text. That text goes in front of the question before Jarvis is asked:

```
you say         "what did we decide about the deploys"
recall runs     memory.d/recall.sh "what did we decide about the deploys"
it prints       Decided 12 Aug: deploys go out Tuesdays, one person on call.
he answers      "Tuesdays, with one person on call. That was the twelfth."
```

A command and not a library on purpose. Chroma, Qdrant, LanceDB, sqlite-vec or a
grep over a folder of markdown are all a shell script, so none of them becomes a
dependency of this repository - and a fixed route cannot be talked into reading
something it was not pointed at. `memory.d/` ships a grep example that needs
nothing installed, and a Chroma one to copy from.

Off until you switch it on, and it fails open: a store that is down costs you the
context, never the answer. Check yours with
`python3 memory.py "a question you would ask"`.

## Languages

`JARVIS_LANG=en` is the default; `JARVIS_LANG=ru` is the other one that ships.
A language is one file in `locales/`, holding everything he says and listens for:

- the wake word, and the forms a small recognizer mangles it into
- the stop words, the acknowledgements, the line for a stranger
- the persona prompt that gives him his manner
- the models that can handle it - the wake recognizer, the transcriber, the voice

The models come from the locale so that a prompt in one language cannot end up
read by a voice in another. English speaks through
[piper](https://github.com/rhasspy/piper) (63 MB a voice, offline, about 0.3 s to
first sound), Russian through vosk-tts. Any piper voice drops in:

```sh
JARVIS_VOICE=en_US-lessac-medium         # the shipped default is en_GB-alan-medium
JARVIS_EXTRA_VOICES="en_US-ryan-high"    # fetched by install.sh alongside it
```

Adding a third language is copying `locales/en.toml`. The routing vocabulary is
separate, because it belongs to your rooms rather than to the language -
`rooms.d/ru.example.toml` is a complete Russian preset showing how far a drop-in
goes.

## What else is in the box

Optional pieces, each independent - ignore what you do not need.

| Piece | What it does |
|---|---|
| `skills/voice-answer` | lets any Claude session speak a short answer aloud |
| `skills/spotify` | voice control for Spotify; credentials come from Keychain |
| `overlay.sh` | floating badge showing listening / thinking / talking |
| `status.sh` | menu bar indicator |
| `tg_listen.py` | ask him from Telegram, by text or voice message |
| `room.sh` | raise a companion session he can hand work to |
| `enroll_voice.py` | record a voice print so he only answers you |
| `memory.py` | the vector-store hook, and a way to test it |
| `mic_check.py`, `probe_key.py`, `why.sh` | diagnostics when something is off |

## Privacy

Everything stays on the machine, and the repository is built to keep it that way.
`.gitignore` excludes the voice print, the enrolment recordings, the logs (they
hold transcripts of everything said near the microphone), the Telegram chat id
and inbox, and the Spotify tokens. Secrets are read from the macOS Keychain, never
from a file in the repo.

The voice lock is off unless you enable it, and it fails open by design: a
missing model or a broken profile lets everyone through rather than silencing him.

## License

MIT.
