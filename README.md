# Jarvis

A voice assistant for macOS that answers with Claude Code. You say "Джарвис",
ask a question out loud, and he answers out loud - or hands the work over to a
Claude session that can actually do it.

Speech in and speech out are local: no cloud speech service, no audio leaving the
machine. The answer comes from the `claude` CLI you already have.

He speaks Russian. The wake word is the Russian word "Джарвис", the recognition
model is Russian, the voice is Russian. Nothing stops you from swapping the model
and prompts for another language, but out of the box it is Russian only.

## One exchange, end to end

1. The wake engine listens to the microphone at 16 kHz. By default that is Vosk,
   a 46 MB offline Russian recognizer that triggers on the real word "Джарвис" -
   free, no registration, no network.
2. A chime, then he records until you stop talking - or until you press the key,
   which ends the recording immediately.
3. `parakeet-mlx` transcribes locally. A leading wake word is stripped, so
   "Джарвис, сколько времени" arrives as "сколько времени".
4. `claude -p` answers inside one resumable session, and the reply is spoken by a
   local Vosk voice.
5. For a second and a half after the reply he keeps listening without a wake
   word, so a follow-up question needs no ceremony.

## What you need

- macOS on Apple silicon. It uses `hidutil`, `osascript`, `afplay` and the macOS
  permission model, and the transcriber is MLX.
- [`uv`](https://docs.astral.sh/uv/) - every script declares its own
  dependencies in its header, so there is no requirements file and no virtualenv
  to manage.
- The [`claude` CLI](https://claude.com/claude-code), logged in.
- About 400 MB of disk for the voice models.

## Install

```bash
git clone <this repo> ~/.claude/jarvis
cd ~/.claude/jarvis
bash install.sh
```

The installer downloads the models, links the two skills and three commands into
`~/.claude`, and creates `jarvis.env` from the example. Clone it somewhere else
and it symlinks that folder to `~/.claude/jarvis`, because the scripts and skills
refer to that path.

Then grant two permissions in System Settings -> Privacy & Security:

- **Microphone** for the terminal app you start him from. Without it he hears
  nothing.
- **Input Monitoring** for the same app, if you want the hotkeys. Skip it and use
  Shortcuts.app instead - see below.

Start him:

```bash
bash ~/.claude/jarvis/jarvisd.sh
```

Say "Джарвис". `Ctrl+C` stops him.

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

**1. A spare key on your keyboard (what I use).** A macro key that sends `End`
becomes F18, and F18 is the Jarvis key. F18 because F13 is Print Screen on a Mac
and applications do react to it - a macro key mapped to F13 once wiped a cell in
Google Sheets. F16..F20 have no system meaning and nothing binds them.

```bash
bash keymap.sh list        # find your keyboard's VendorID and ProductID
```

Put them into `jarvis.env`:

```sh
JARVIS_KEYMAP_VENDOR=13364
JARVIS_KEYMAP_PRODUCT=419
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
JARVIS_OWNER=Дмитрий       # empty: he says "the owner of this computer"
```

## Two ways to run him

**Standalone daemon** - `jarvisd.sh`. His own Claude session answers, and heavy
work is handed to another window.

**Inside an agent session** - the `/assist` command. The session you are already
working in takes the microphone and starts hearing you: you ask out loud, the
agent does the work in that repository and answers out loud. `/assist-off` gives
the microphone back, `/jarvis-daemon` returns it to the standalone daemon.

There is one microphone, so only one of them can hold it. Both dropped in
through the same file lock, and each tells you who it took it from.

## What else is in the box

Optional pieces, each independent - ignore what you do not need.

| Piece | What it does |
|---|---|
| `skills/voice-answer` | lets any Claude session speak a short answer aloud |
| `skills/spotify` | voice control for Spotify; credentials come from Keychain |
| `overlay.sh` | floating badge showing listening / thinking / talking |
| `status.sh` | menu bar indicator |
| `tg_listen.py` | ask him from Telegram, by text or voice message |
| `chef.sh`, `rocket.sh` | raise companion sessions he can hand work to |
| `enroll_voice.py` | record a voice print so he only answers you |
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
