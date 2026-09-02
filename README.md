# Jarvis

**A voice for Claude Code.** Say "Jarvis" anywhere in the room and your Mac
answers out loud - and the agent you already work with does the work.
macOS on Apple silicon, everything spoken stays on the machine, MIT.

![macOS, Apple silicon](https://img.shields.io/badge/macOS-Apple%20silicon-111111)
![runs on Claude Code](https://img.shields.io/badge/runs%20on-Claude%20Code-d97757)
![speech stays local](https://img.shields.io/badge/speech-100%25%20on%20device-2ea44f)
![costs nothing extra](https://img.shields.io/badge/extra%20cost-%240-2ea44f)
![license MIT](https://img.shields.io/badge/license-MIT-3b82f6)

> **Free. No account, no API key, no second subscription.** The only thing you
> pay for is the Claude subscription you already have. Every speech model here
> is free and runs on your Mac - hearing, transcribing and talking cost no
> tokens at all. A spoken question costs exactly what the same question typed
> into Claude Code would, and not one token more.

| asleep | listening | thinking | speaking |
|---|---|---|---|
| ![The badge folded into a small ring while nothing is happening](docs/img/badge-asleep.png) | ![The badge showing LISTENING with the live waveform of your voice](docs/img/badge-listening.png) | ![The badge unfolded: THINKING, the name of the session that holds the microphone, a small swarm of dots at work](docs/img/badge-thinking.png) | ![The badge showing SPEAKING with the waveform of the phrase being said](docs/img/badge-speaking.png) |

Ask what time the build finished and he tells you. Say "tell the agent to fix
the failing test" and the words land in a live Claude Code session that goes
and does it, then reports back, out loud, while you are still making coffee.

```bash
git clone https://github.com/spanderok/jarvis ~/.claude/jarvis
cd ~/.claude/jarvis && bash install.sh     # models, skills, commands - about ten minutes
# System Settings -> Privacy & Security -> Microphone -> your terminal
bash jarvisd.sh                            # then say "Jarvis"
```

That is the whole install; the [step by step](#install) below has the details
and the things that go wrong.

## Why you would want him

**He is ears and a mouth for the agent you already have.** Jarvis has no brain
of his own. Type `/assist` in any Claude Code session and that session hears
you and talks back - with every tool, MCP server and project rule it already
has. Ask "is the pipeline green" and the agent that can read your CI answers.

**Nothing you say leaves the machine.** Wake word, speech to text, text to
speech - all local, on Apple silicon. The only thing that goes out is the text
of your question, to the same `claude` CLI you already use.

**And it costs almost nothing to keep on.** Listening takes about 2 % of one
core and 120 MB. The big models wake for half a second per phrase -
[measured](#what-it-costs-your-mac).

**"Tell me out loud when you are done."** Hand an agent a long task, add that
sentence, walk away. Twenty minutes later a voice tells you how it went. This
one habit is why people keep him on -
[more of them](#in-a-working-day).

**He can know your voice - if you want.** Off by default. Six minutes of
enrolment and he answers you and only you; a colleague leaning into your
microphone gets a polite refusal.

**One key beats every recognizer.** A spare key means "switch to me": it ends
your sentence, interrupts him mid-word, or wakes him with no wake word at all.

**You teach him new tricks in a config file, not in Python.** "What's playing"
running a script, "tell the reviewer" typing into a second session, memory of
last month's decisions from your own notes - each is a few lines in a settings
file. English and Russian in the box; a third language is one more text file.

*The badge above (`overlay.sh`) floats over every window and shows what he is
doing and which session holds the microphone - here, one named `agent`. Drag it
wherever it bothers you least; it remembers the spot.*

## Contents

- [One exchange, end to end](#one-exchange-end-to-end)
- [Install](#install) - [before you start](#before-you-start), [four steps](#step-1---get-the-code), [check](#check-that-everything-is-in-place), [if something is off](#if-something-is-off), [Russian](#russian), [updating](#updating), [uninstall](#uninstall)
- [Your agent's ears and mouth](#your-agents-ears-and-mouth)
- [In a working day](#in-a-working-day)
- [What it costs your Mac](#what-it-costs-your-mac)
- [The voice lock - he answers only you](#the-voice-lock---he-answers-only-you)
- [The key](#the-key)
- [Rooms and actions](#rooms-and-actions)
- [Long-term memory](#long-term-memory)
- [Languages](#languages) - [when he says a word wrong](#when-he-says-a-word-wrong)
- [What else is in the box](#what-else-is-in-the-box)
- [Privacy](#privacy), [Disclaimer](#disclaimer), [Feedback](#feedback), [License](#license)

## One exchange, end to end

1. The wake engine listens to the microphone at 16 kHz. By default that is Vosk,
   a 40 MB offline recognizer that triggers on the real word "Jarvis" - free, no
   registration, no network.
2. A chime, then he records until you stop talking - or until you press the key,
   which ends the recording immediately.
3. `parakeet-mlx` transcribes locally. A leading wake word is stripped, so
   "Jarvis, what time is it" arrives as "what time is it".
4. `claude -p` answers inside one resumable session, and the reply is spoken by a
   local voice - piper for English, vosk-tts for Russian.
5. For a second and a half after the reply he keeps listening without a wake
   word, so a follow-up question needs no ceremony.

![The pipeline: microphone, wake word, recorder, speech to text, Claude, voice and speakers all inside the Mac; only the question and the answer, as text, cross to the Anthropic API](docs/img/exchange.svg)

*The dashed box is your Mac. Two amber arrows are the only thing that leaves
it: the question as text, and the answer as text.*

## Install

Fifteen minutes, most of it downloads. Four steps, then a check.

### Before you start

You need a Mac with Apple silicon (M1 or later) running a recent macOS. Intel
Macs are out: the transcriber runs on MLX, which is Apple silicon only.

Three tools have to be on the machine already. Check each one in a terminal;
every command should print a version, not "command not found".

```bash
brew --version      # Homebrew            - https://brew.sh
uv --version        # uv, a Python runner - brew install uv
claude --version    # Claude Code CLI     - https://claude.com/claude-code
```

`claude` has to be logged in as well: run `claude` once by hand, and if it asks
you to sign in, do that first.

Two more things are optional:

- `ffmpeg` (`brew install ffmpeg`) - only for two extras: the network voice that
  fills in when a local model is missing, and Telegram voice messages. Without it
  the offline voices work as usual.
- Python. You do not install one. Every script here declares its own
  dependencies in a header, `uv` reads that header and fetches a Python 3.12 plus
  the packages on the first run. The Python that ships with macOS (3.9) is never
  used.

Expect about 2.6 GB of disk for the models - 2.3 GB of that is the transcriber,
fetched on the first question - and an internet connection for the one-time
downloads.

### Step 1 - get the code

```bash
git clone https://github.com/spanderok/jarvis ~/.claude/jarvis
cd ~/.claude/jarvis
```

That exact folder matters: the scripts, the skills and the Claude commands all
refer to `~/.claude/jarvis`. If you would rather keep the repository somewhere
else, clone it there and run the installer from there - it puts a symlink at
`~/.claude/jarvis` pointing to your folder, and everything works the same.

### Step 2 - run the installer

```bash
bash install.sh
```

It is safe to re-run: every step skips what is already in place. In order, it

1. checks that this is a Mac and that `uv` is there, and warns if `claude` is not
   on PATH;
2. reads the language from `jarvis.env` (English if the file does not exist yet)
   and loads the locale - on the very first `uv` call this pulls a Python and
   takes about a minute;
3. links `~/.claude/jarvis` if the repo lives elsewhere;
4. links the two skills (`voice-answer`, `spotify`) into `~/.claude/skills` and
   the three commands (`/assist`, `/assist-off`, `/jarvis-daemon`) into
   `~/.claude/commands`, so any Claude Code session can use them;
5. creates `jarvis.env` from `jarvis.env.example` - your personal settings file;
6. downloads the models for your language into `models/` (see the table below);
7. installs the login item for the key remap, but only if you configured one -
   see [The key](#the-key).

It ends with a "next" block telling you which permissions to grant. A line that
starts with `warning:` is not fatal - read it, it says what will be missing.

**The models it fetches for English:**

| Model | For | Size | From |
|---|---|---|---|
| `vosk-model-small-en-us-0.15` | hearing the wake word | 41 MB | [alphacephei.com](https://alphacephei.com/vosk/models) |
| `en_GB-alan-medium` | his voice (piper) | 63 MB | [piper-voices](https://huggingface.co/rhasspy/piper-voices) |
| `campplus.onnx` | telling your voice from a stranger's | 28 MB | [CAM++ ONNX](https://huggingface.co/FunAudioLLM/CosyVoice-300M) |
| `parakeet-tdt-0.6b-v3` | transcribing the question | 2.3 GB | [Hugging Face](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3) - **on the first question**, not by the installer |

Nothing is bundled and `models/` is in `.gitignore`. All of them are free and
need no account. The speaker model is downloaded but **not used** until you
record a voice profile - see [The voice lock](#the-voice-lock---he-answers-only-you).
Skip even the download with `JARVIS_NO_SPEAKER=1 bash install.sh`.

### Step 3 - two permissions in System Settings

Both are granted to the **terminal app you start Jarvis from** - Terminal.app,
iTerm2, Warp, whichever one it is. Not to `uv`, not to `python`.

1. **Microphone** - required. System Settings -> Privacy & Security ->
   Microphone -> turn on your terminal. macOS usually asks by itself the first
   time the daemon opens the microphone; if that dialog never appeared, add the
   app here by hand.
2. **Input Monitoring** - optional, only for the hotkeys. System Settings ->
   Privacy & Security -> Input Monitoring -> turn on your terminal. Without it
   everything works except the key; there is a way to have the key with no
   permission at all, see [The key](#the-key).

After changing either one, quit the terminal app completely and open it again -
macOS applies these on launch.

### Step 4 - start him and say his name

```bash
bash ~/.claude/jarvis/jarvisd.sh
```

The first start is slow: `uv` builds the environments for the daemon and its
workers (a couple of minutes), then the log shows

```
Jarvis daemon up. Engine: vosk (vosk-model-small-en-us-0.15) ...
asr worker ready
```

and a short click says he is listening. Say "Jarvis". A chime, then ask
something: "what is two times two". **The first question takes a while** - the
2.3 GB transcriber is downloaded at that moment and nothing else. From the
second question on, an answer takes a few seconds.

`Ctrl+C` stops him. To run him in the background instead, from any Claude Code
session type `/jarvis-daemon`.

### Check that everything is in place

Each of these takes seconds and tells you which part is off, if any.

```bash
cd ~/.claude/jarvis
uv run lang.py                          # the language, the wake word and the voices it picked
bash say.sh "Hello, I am up."           # you hear the local voice
uv run jarvis_daemon.py --selfcheck     # wake engine, routing examples, rooms, keys
uv run mic_check.py                     # talk; numbers above zero mean the mic reaches this terminal
```

`--selfcheck` should print `selfcheck ok, engine: vosk ...` and `routing: 8/8 ok`.
A line like `room 'chief' ... no window found` is normal - rooms are companion
sessions you have not started (see [Rooms and actions](#rooms-and-actions)).

### Make him yours

Everything personal lives in `jarvis.env`, one shell assignment per line -
your name, the keys, timings, which model answers, the language.
`jarvis.env.example` documents every knob with its default; set only what you
change, then restart the daemon. The code itself ships unchanged.

```sh
JARVIS_OWNER=Ada           # empty: he says "the owner of this computer"
JARVIS_MODEL=sonnet        # the Claude model for voice answers; haiku is fastest
```

The key is worth setting next - see [The key](#the-key).

### If something is off

| What you see | What it means | What to do |
|---|---|---|
| `uv is missing` | the installer stopped before doing anything | `brew install uv`, run `bash install.sh` again |
| `warning: the claude CLI is not on PATH` | he will listen but cannot answer | install Claude Code, run `claude` once to log in |
| `Vosk model not found: .../models/...` | the wake model is not there | `bash install.sh models` |
| the daemon is up, you talk, nothing happens | the microphone does not reach the terminal | `uv run mic_check.py`; grant Microphone to the terminal app, restart it |
| `grant Input Monitoring to your terminal` in the log | the key listener could not start | grant it, or use `jarvis-key.sh` from Shortcuts - the rest works without it |
| he answers with a network voice or a robotic system voice | the local voice model is missing | `bash install.sh models`; the log names the missing file |
| the first answer takes a minute or more | the 2.3 GB transcriber is downloading | wait it out once |
| every start is slow | `uv` rebuilding environments | normal only on the first start; check disk space if it repeats |
| `piper: Error processing file ... espeak-ng-data` | the path to uv's cache is longer than espeak-ng can handle (about 240 characters) | keep the repository and your home folder at ordinary depths |

The daemon writes what it hears and does to `listener.log`; `bash why.sh` shows
the last sixty lines.

### Russian

Set the language **before** the models are downloaded, because the language
decides which models those are:

```bash
cd ~/.claude/jarvis
cp jarvis.env.example jarvis.env
echo 'JARVIS_LANG=ru' >> jarvis.env
bash install.sh
```

For Russian the installer fetches the Russian wake recognizer (46 MB), the
vosk-tts voice with its stress dictionary (135 MB) and creates a small Python
environment `venv-vosk` for it - vosk-tts does not fit the one-file header the
other scripts use. The transcriber is the same multilingual model.

Already installed in English and want to switch? Add `JARVIS_LANG=ru` to
`jarvis.env`, run `bash install.sh models`, restart the daemon. Both sets of
models can sit side by side.

### Updating

```bash
cd ~/.claude/jarvis && git pull && bash install.sh
```

`jarvis.env`, your voice print, your rooms and actions in `rooms.d/` and
`actions.d/` are all ignored by git, so a pull never touches them. The installer
only adds what is new.

### Uninstall

```bash
bash ~/.claude/jarvis/keymap.sh off                          # if you set up a key remap
launchctl unload ~/Library/LaunchAgents/com.jarvis.keymap.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.jarvis.keymap.plist
rm -f ~/.claude/skills/voice-answer ~/.claude/skills/spotify
rm -f ~/.claude/commands/assist.md ~/.claude/commands/assist-off.md ~/.claude/commands/jarvis-daemon.md
rm -rf ~/.claude/jarvis ~/.claude/tts-cache
```

Then remove the terminal app from Microphone and Input Monitoring in System
Settings if you no longer want it there. The Python environments live in
`~/.cache/uv` and go away with `uv cache clean`.

## Your agent's ears and mouth

Jarvis does not think. He hears, he speaks, and he hands the words to a Claude
Code session - so what he *can do* is exactly what that session can do.

Run one exchange through `/assist` to see what that means. You type `/assist`
in the session where you are working on a repository with a Jira MCP server
configured. You say "Jarvis, what is left in the sprint". The listener
transcribes it and drops the line into the session as an event. The agent reads
it like any typed request: it calls the Jira MCP, counts the tickets, and speaks
three sentences through the `voice-answer` skill. Nothing in this repository
knows what Jira is. Swap the session for one in another repository with other
tools, and he answers other questions.

The same holds for rules. A session that is told to never push to `main` will
not push to `main` because you asked out loud. Your CLAUDE.md, your hooks, your
permission settings - all of it stays in force; the microphone is just another
way to type.

The standalone daemon (`jarvisd.sh`) is the light version of this: his own
`claude -p` session, started in an empty folder so that nothing heavy is loaded
on every question. It answers in about two seconds and has no tools unless you
give it some - `JARVIS_MCP=1` in `jarvis.env` starts it with your MCP servers,
at the cost of about two seconds per answer. Anything that needs real tools he
passes to a [room](#rooms-and-actions).

So there are two ways to run him, and one microphone between them:

- **`bash jarvisd.sh`** - the standalone daemon. Fast answers from his own
  session; heavy work is handed to a room.
- **`/assist`** in a Claude Code session - that session takes the microphone
  and becomes Jarvis, with all its tools. `/assist-off` releases it,
  `/jarvis-daemon` starts the daemon again.

Whoever takes the microphone says who had it before. Any session can still
speak through the `voice-answer` skill without holding it, and your spoken
reply always lands with the holder, which forwards it by name to the session
that asked.

## In a working day

Things that turned out to matter more than any feature, in the order people
usually discover them.

**"Tell me out loud when you are done."** Hand an agent a long task - a
refactor, a test run, a review - and add that sentence. You go to another
window, another task, another room; twenty minutes later a voice says "the
review is done, three remarks, one of them about the migration". No tab
switching, no glancing at a terminal every two minutes. This works from any
session, whether or not it holds the microphone: the `voice-answer` skill
speaks, and the daemon or listener stays out of its way.

**Alerts about what matters, spoken.** Any session that watches something - a
chat, a pipeline, a queue - can be told "say it out loud when someone mentions
me" or "tell me when the deploy finishes". Everything else stays silent text in
that session. The skill's own rules keep it honest: background noise is never
spoken unless you asked for exactly that kind of noise, and a spoken report
never ends in a question when you are not there to answer.

**Ask from across the room.** "Jarvis, is the build green", "what is the chief
doing", "anything new from Petrov" - status questions that used to mean sitting
down and typing. He is listening at 2 % of a core, so leaving him on all day
costs nothing.

**Music without touching the keyboard.** "Jarvis, turn it down", "next track",
"what's playing", "put on something calm for the evening" - the `music` action
and the `spotify` skill drive the Spotify app on the Mac. Pause, volume and
skipping work out of the box, over AppleScript, with nothing to set up. Playing
something *by name* - a track, an album, one of your own playlists - needs a
free Spotify developer app and two keys in the Keychain, plus a one-time
browser login for your playlists. Ten minutes, all of it in
[`skills/spotify/SKILL.md`](skills/spotify/SKILL.md). He never announces the
music he just started: you asked for music, not for a voice over it.

**Not at the desk at all?** `speak.sh --telegram` sends the same sentence as a
voice message to your phone, and `tg_listen.py` takes questions back the same
way. During a video call the skill switches to Telegram on its own, so the room
never hears him.

## What it costs your Mac

Measured on a MacBook with an Apple M5 and 16 GB, with the English models.
Memory is the process's resident size; the transcriber lives in unified memory
that Activity Monitor attributes to the GPU, so its number is the peak of a
one-shot run.

| Piece | When it runs | Memory | CPU / time |
|---|---|---|---|
| listener with the Vosk wake model | always | 120 MB | about 2 % of one core, averaged over an hour |
| the badge | always | 40 MB | under 1 % |
| transcriber (parakeet-mlx) | loaded once, kept warm | about 1.2 GB | 0.5-0.6 s per phrase; 12 s to load cold |
| voice, English (piper) | per phrase | 300 MB peak, then gone | 0.3 s to first sound, about 1 s for a sentence |
| voice, Russian (vosk-tts) | per phrase | 400 MB peak; the worker exits after 3 quiet minutes | 0.9 s to first sound |
| speaker check (CAM++) | per phrase, only with a profile | 160 MB | 1.6 s cold; a few tens of ms warm |
| `claude -p` | per question | whatever your CLI uses | typically 1-3 s to the first sentence |

So an idle day is 160 MB and a couple of percent of one core. The heavy part is
the transcriber, and it is paid for in disk and unified memory, not in CPU: a
question costs about half a second of work on the GPU and the rest
is Claude thinking. Nothing here runs a GPU hot or spins a fan.

## The voice lock - he answers only you

A microphone hears everyone in the room. The voice lock is how he tells your
voice from a colleague's, a visitor's, or his own coming back through the
speakers - and it is **off until you record a profile**. Without one he answers
whoever speaks, and nothing else changes.

### How it decides

It compares voices, not words, so the phrase does not matter.

1. The recorded phrase goes through `campplus.onnx`, a small speaker model,
   which folds it into one vector of 512 numbers - the voice print of whoever
   just spoke.
2. That print is compared with two crowds: every take you recorded at
   enrolment, and a cohort of voices that are not you - his own synthesised
   speech and two macOS system voices, which he gathers from disk himself.
3. Two numbers come out, "how much this looks like you" and "how much it looks
   like somebody else", and you have to win by a margin. The margin is
   measured at enrolment, not guessed.

Why two numbers and not one threshold, measured on a real profile: his own
synthesised voice, coming back through the speakers, scored 0.65 against the
owner's takes - higher than the owner's own tired take at 0.57. No single line
separates those. But the same synthesised clip scores 0.95 against other clips
of itself, so "looks like you minus looks like the cohort" lands at -0.31 for
it and at +0.10 or better for every take of the owner's. The tired voice gets
through; the speakers do not.

Everything **fails open**. No model, no profile, a broken file, a phrase under
one second - he lets it through. A lock that silences him because a file went
missing would be worse than no lock.

Three cases skip the check on purpose, because there is better evidence than a
voice:

- a take you started or ended with the key - that is your own hand on the
  keyboard;
- a take with music still playing in it - the print is then you and a song at
  once, and matches nobody;
- the follow-up window after his answer still checks, but refuses silently -
  nobody asked for that take.

### Recording your profile

Stop the daemon first (`Ctrl+C`, or `/assist-off`) - the script warns if he is
still listening, because he would hear the enrolment as questions. Then:

```bash
uv run ~/.claude/jarvis/enroll_voice.py
```

It records six takes of six seconds each. Before each one it prints how to
speak and what to say - the phrases come from your locale, so they are real
commands in your language, not test sentences:

```
[normal] Your ordinary voice, sitting at the Mac as usual.
    phrase: "Jarvis, what is on the list for today, have a look please"
    Enter, then talk:
    recorded, level 812
    keep it? [Y/n]
[close] Closer to the microphone, a little quieter than usual.
...
[far] Step two or three metres away and speak up.
[noise] Put music or a fan on and talk over it.
[tired] Quiet, tired, a little hoarse - the way you sound late at night.
[fast] Fast and careless, swallowing the ends of words.
```

Several takes rather than one average, because a microphone hears a tired
voice at arm's length differently from a fresh one leaning in; a single
average sits between the two and matches neither. Each take is kept whole, and
a phrase is yours if it matches any one of them.

At the end it works out where the line goes, prints the numbers, and says
either `Room between you and the cohort: ... - that is enough` or
`!! The gap is small` - the latter means one take sounded forced, re-record
that one with `--add <name>`. For scale, `--report` on a real profile after a
few weeks of use, thirteen takes against fifty-six other voices:

```
takes: 13 - normal, close, far, noise, tired, fast, fresh, aircon, ...
cohort voices: 56
recognition floor: 0.45, margin line: -0.14
worst own take: 0.65, its smallest gap: +0.11, cohort's best gap: -0.31 (jarvis-tts)
```

The profile is `voiceprint.json`. It is biometric data and is in `.gitignore`
along with the recordings; it never leaves the machine.

Restart the daemon. From now on a stranger hears the refusal line once, and for
the next sixty seconds further strangers are dropped without a word, so a
conversation next to your desk does not get a running commentary.

### Living with it

```bash
uv run enroll_voice.py --check          # say something, see the score and the verdict
uv run enroll_voice.py --report         # what the profile holds
uv run enroll_voice.py --forgive        # replay what he refused, add the ones that were you
uv run enroll_voice.py --add whisper    # one more condition, any name you like
uv run enroll_voice.py --rebuild        # redo the maths, keep the takes
```

`--forgive` is the one you will use. Every refused phrase is saved in
`rejected/`; it plays them back one at a time and asks "was that you?". A yes
adds the take to the profile and moves the line.

Changed the microphone, or the room? Either re-record, or switch the check off
until you do:

```sh
JARVIS_SPK=off                          # in jarvis.env: never check who is talking
JARVIS_STRANGER_LINE="Not for you."     # what he says to someone who is not you
```

Voice messages from Telegram go through the same check, so a stranger with
your phone gets the same refusal.

### Living without it

Do nothing - that is the default. Two ways to make the choice explicit:

- `JARVIS_NO_SPEAKER=1 bash install.sh` skips the 28 MB model download, and
  the check has nothing to run on;
- `JARVIS_SPK=off` in `jarvis.env` keeps the model and the profile but never
  consults them.

Without the lock the key is still your protection: a stranger can ask him
questions, but handing work to another agent needs the key by default, and he
says so out loud if someone tries by voice alone.

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
JARVIS_DONE_KEYS="<alt_r>" # "answer now", only while he is listening
JARVIS_OFF_KEYS="<esc>"    # shut up and wait
```

The defaults are the right Option key and Escape. The daemon does not swallow
key presses, so a "done" key that prints a character - space was tried - also
types that character into whatever window is in front. Right Option prints
nothing and no application binds it alone; Escape only acts while he is busy, so
pressing it in an editor never touches him.

**3. No key at all.** If you would rather not grant Input Monitoring, bind
`jarvis-key.sh` to a keyboard shortcut in Shortcuts.app (Run Shell Script). It
signals the running daemon and needs no permission whatsoever:

```bash
jarvis-key.sh              # one press
jarvis-key.sh double       # two presses
```

The same thing by hand: `kill -USR1 <pid>` and `kill -USR2 <pid>`.

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

![Where a heard phrase goes, in order: an order to a room, an action pattern, a mention word, otherwise Jarvis answers himself](docs/img/routing.svg)

*The routes, in the order they are tried. First match wins; an order always
outranks an automatic route, so "tell the chief what's playing" goes to the
chief, not to Spotify.*

Out of the box `config/rooms.toml` declares one room, `chief`, a working agent
started in `~/projects` - change `work_dir` to your own folder, or override it
without editing the file with `JARVIS_ROOM_CHIEF_DIR=~/code` in `jarvis.env`.
Raise it with `bash room.sh chief`; until then "tell the chief" gets "No window
for the chief". A second room, `chat`, ships switched off.

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
`uv run memory.py "a question you would ask"`.

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

### When he says a word wrong

**Russian.** The vosk voice reads by a stress dictionary of two million words,
and a personal override table sits on top of it and always wins. To fix a
word, write it with the stressed vowel in capitals:

```bash
~/.claude/jarvis/venv-vosk/bin/python ~/.claude/jarvis/vosk_dict.py творОг
~/.claude/jarvis/venv-vosk/bin/python ~/.claude/jarvis/vosk_dict.py list   # what you have fixed so far
```

It takes effect on the next phrase, and it survives updates - the table lives
in `models/`, which git never touches.

The easier way is to tell the agent. Any session with the `voice-answer` skill
knows this command, so this is enough, typed or said out loud:

```
Jarvis just said "творог" with the stress on the first syllable. It is творОг - fix it.
```

The agent runs the command above and says the word again so you can hear it.

**English.** piper reads by rule and has no override table, so a name it
mangles is best written the way it sounds in the text that is spoken:
"Kubernetes" is fine, "kube-ctl" reads better than "kubectl". The
`voice-answer` skill already tells agents to say numbers and ticket ids as
words for the same reason. Text read off another session's screen goes through
the `spoken_swaps` table in the locale file first, which is where symbols a
voice reads badly - `->`, `..`, `#` - are replaced.

## What else is in the box

Optional pieces, each independent - ignore what you do not need.

| Piece | What it does | Needs |
|---|---|---|
| `skills/voice-answer` | lets any Claude session speak a short answer aloud | nothing extra; `ffmpeg` for Telegram voice messages |
| `skills/spotify` | voice control for Spotify | two Keychain entries, see `skills/spotify/SKILL.md` |
| `overlay.sh` | floating badge showing listening / thinking / talking | nothing |
| `status.sh` | menu bar indicator | nothing |
| `tg_listen.py` | ask him from Telegram, by text or voice message | a bot token and your chat id in the Keychain as `jarvis-telegram-token` and `jarvis-telegram-chat` |
| `room.sh` | raise a companion session he can hand work to | a Terminal.app window |
| `enroll_voice.py` | record a voice print so he only answers you | `models/campplus.onnx` |
| `memory.py` | the vector-store hook, and a way to test it | a `recall` command of yours |
| `mic_check.py`, `probe_key.py`, `why.sh` | diagnostics when something is off | nothing |

Secrets go into the macOS Keychain, never into a file here:

```bash
security add-generic-password -U -s jarvis-telegram-token -a "$USER" -w '<bot token>'
security add-generic-password -U -s jarvis-telegram-chat  -a "$USER" -w '<your chat id>'
```

## Privacy

Everything stays on the machine, and the repository is built to keep it that way.
`.gitignore` excludes the voice print, the enrolment recordings, the logs (they
hold transcripts of everything said near the microphone), the Telegram chat id
and inbox, and the Spotify tokens. Secrets are read from the macOS Keychain, never
from a file in the repo.

The voice lock is off unless you enable it, and it fails open by design: a
missing model or a broken profile lets everyone through rather than silencing him.

## Disclaimer

This is a personal project, shared as is, with no warranty of any kind. Jarvis
listens to a microphone and hands what he hears to a Claude Code session that
can run commands, edit files and reach whatever services that session is
connected to. **You are the one who decides what that session may do**, through
its permission settings, its rules and the rooms and actions you configure -
and you are responsible for the consequences of every command it runs, every
message it sends and every recording it keeps.

Recording other people's voices is regulated in many places. Check what the law
where you live requires before enrolling anyone but yourself, and tell the
people around your desk that a microphone is on.

The author accepts no liability for any loss, damage, data leak or legal
consequence arising from installing, running or modifying this software. If
that is not acceptable to you, do not use it.

## Feedback

If he earned a place on your desk, a star on the repository is how the next
person finds him. If he did not, an issue is even more useful - say which Mac
and which macOS, what you said, and paste the last lines of `bash why.sh`; the
log holds the reason for every take that went wrong.

Pull requests are welcome for what the README promises and the code does not
yet do: another language, another voice backend, a room you built that others
would want.

## License

MIT.
