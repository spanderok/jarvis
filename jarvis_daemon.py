# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["vosk", "openwakeword>=0.6", "onnxruntime", "sounddevice",
#                 "numpy<2", "pvporcupine", "pynput", "webrtcvad",
#                 "kaldi-native-fbank"]
# ///
"""Jarvis voice daemon: wake word -> record -> transcribe -> Claude -> speak.

One exchange, end to end:
  1. Wake engine listens to the mic (16 kHz mono).
     - vosk (default): offline Russian ASR, 87 MB model, triggers on the real
       Russian word "Джарвис". Free, no registration, no network.
     - oww: openWakeWord "hey jarvis", English pronunciation only.
     - porcupine: Picovoice, needs an access key; supports a custom .ppn.
  2. Chime, then record until SILENCE_SEC of quiet - or until the hotkey is
     pressed, which ends the recording immediately.
  3. parakeet-mlx transcribes locally; a leading wake word is stripped.
     If only the wake word was said, Jarvis says "Слушаю" and waits.
  4. `claude -p` answers inside one resumable session, the reply is spoken.
  5. Follow-up window: for FOLLOWUP_SEC after the reply Jarvis keeps listening
     without a wake word, so the conversation can continue naturally.

Run in a terminal (mic permission required on first start):
  bash ~/.claude/jarvis/jarvisd.sh

Keys (need macOS Input Monitoring permission for the terminal you start from):
  M5 / F13 / End once  - "switch to me": ends your sentence while he listens,
                         interrupts him while he thinks or talks, wakes him when
                         idle. Afterwards he always listens for what you say next
  M5 / F13 / End twice - drop it entirely, back to waiting for "Джарвис"
  Space                - same as one press, but never wakes him from idle, so
                         normal typing is unaffected
  Escape               - same as two presses: shut up and wait. He keeps
                         reacting to "Джарвис". Only acts while he is busy, so
                         Escape in an editor never touches him
  Signal equivalents that need no permission:
    kill -USR1 <pid>   one press
    kill -USR2 <pid>   two presses

Env:
  JARVIS_WAKE_ENGINE     "vosk" (default) | "oww" | "porcupine"
  JARVIS_VOSK_MODEL      Vosk model dir (default: models/vosk-model-small-ru-0.22)
  JARVIS_TAP_KEYS        tap keys, default "<f18>" (M5 is remapped to it by keymap.sh):
                         one press = answer now, two = interrupt / wake
  JARVIS_DONE_KEYS       "answer now" only, and only while listening, default <alt_r>;
                         default "<space>" so typing stays unaffected
  JARVIS_OFF_KEYS        "shut up and wait" keys, default "<esc>"
  JARVIS_DOUBLE_TAP      double-tap window in seconds, default 0.4
  JARVIS_DEBOUNCE        ignore repeated events from one press, default 0.18 s
  JARVIS_SILENCE         seconds of silence that end a command, default 2.6
  JARVIS_FOLLOWUP        seconds to keep listening after a reply, default 1.5
  JARVIS_PICOVOICE_KEY   Picovoice AccessKey (fallback: Keychain jarvis-picovoice-key)
  JARVIS_PPN             custom .ppn keyword file for porcupine
  JARVIS_PPN_MODEL       porcupine_params_ru.pv for Russian keywords
  JARVIS_WAKE_THRESHOLD  oww score 0..1 (default 0.32) / porcupine sensitivity (0.7)
  JARVIS_CWD             working dir for claude -p (default: home)
  JARVIS_MODEL           model for voice answers, default "sonnet"; "haiku" is
                         fastest, empty string keeps your CLI default
  JARVIS_TARGET=orch     send every question to the orchestrator window instead
                         of answering in Jarvis' own session (default: own)
  JARVIS_ROOMS           extra rooms: one .toml file or a directory of them,
                         merged over config/rooms.toml (see plugins.py)
  JARVIS_ACTIONS         the same for actions
  JARVIS_ROOM_<ID>_SESSION, JARVIS_ROOM_<ID>_DIR
                         override one room's session name or folder without
                         editing its file, e.g. JARVIS_ROOM_CHIEF_SESSION
  JARVIS_ASLEEP=1        start quietly: no greeting, wake word ignored until a
                         key is pressed (kill -HUP toggles it later)
  JARVIS_ECHO_SETTLE     quiet pause after speaking before the mic is trusted,
                         default 0.4 s (the speakers feed back into the mic)
  JARVIS_MCP=1           start claude with MCP servers (Jira, Obsidian, Sentry).
                         Off by default: they add about 2 s to every answer
  JARVIS_DEBUG=1         print what Vosk hears, transcripts and timings
"""
import collections
import contextlib
import difflib
import json
import os
import pathlib
import queue
import random
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave

import numpy as np
import sounddevice as sd

import plugins

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pane_filter import looks_like_shell, speakable  # noqa: E402
from vocab import correct as vocab_correct  # noqa: E402

# Shortcuts does not run a login shell, so uv / uvx / claude / ffplay are all
# missing from PATH when the daemon is started from Siri. Put them back.
for _extra in ("/opt/homebrew/bin", "/usr/local/bin",
               os.path.expanduser("~/.local/bin")):
    if os.path.isdir(_extra) and _extra not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = f"{_extra}:{os.environ.get('PATH', '')}"

SAMPLE_RATE = 16000
# The trade is symmetric: a longer pause never cuts him off mid-thought, but every
# answer waits that long after he stops. 1.0 cut him on 19.08, 2.6 felt sluggish
# on 20.08 - 1.5 is what he settled on.
SILENCE_SEC = float(os.environ.get("JARVIS_SILENCE", "1.5"))
# How long to wait when the pause lands in the MIDDLE of a phrase rather than at
# its end. 1.5 s is right for "he stopped talking", and wrong for "he is thinking
# of the next word" - on 22.08 he was cut off twice inside a sentence, and both
# times said the same thing: I paused for a second.
#
# The two cases are told apart by what came before. A pause after a completed
# thought follows a long stretch of steady speech; a pause for thinking sits among
# other short pauses. So: if at least one earlier pause in this take ran past
# SILENCE_SEC * 0.6 and speech resumed after it, this take is "thinking out loud"
# and every later pause gets the longer wait.
SILENCE_MID = float(os.environ.get("JARVIS_SILENCE_MID", "2.6"))
# Live knobs: tuning.json is re-read while running, so the pause can be tried on
# without restarting a listener that lives in someone else's chat session.
TUNING_FILE = os.path.expanduser("~/.claude/jarvis/tuning.json")
_tuning = {"at": 0.0, "data": {}}


def tuned(name: str, default: float) -> float:
    """Value from tuning.json if it is there, otherwise the constant above."""
    now = time.monotonic()
    if now - _tuning["at"] > 2.0:
        _tuning["at"] = now
        try:
            with open(TUNING_FILE) as f:
                _tuning["data"] = json.load(f)
        except (OSError, ValueError):
            _tuning["data"] = {}
    try:
        return float(_tuning["data"].get(name, default))
    except (TypeError, ValueError):
        return default
WAIT_SPEECH_SEC = float(os.environ.get("JARVIS_WAIT_SPEECH", "5"))
# How long to keep listening after the agent has spoken, without a wake word.
# Was 6 s; the owner asked on 21.08 for the same 1.5 s as the end-of-phrase pause -
# six seconds of an open microphone after every answer read as "never stops".
FOLLOWUP_SEC = float(os.environ.get("JARVIS_FOLLOWUP", "1.5"))
# after an interrupt the user is still forming the sentence, so wait longer
INTERRUPT_WAIT_SEC = float(os.environ.get("JARVIS_INTERRUPT_WAIT", "10"))
# start up quietly: no greeting, no wake word, until a key activates him
START_ASLEEP = os.environ.get("JARVIS_ASLEEP") == "1"
SESSION_TTL_SEC = 600       # keep Claude context this long between questions
# 25 s cut him off mid-question on 20.08 ("record end: cap" after 22.4 s of real
# speech). A minute of 16 kHz mono is 1.9 MB and about 2.5 s of recognition -
# cheap enough to let him think out loud.
MAX_UTTERANCE_SEC = float(os.environ.get("JARVIS_MAX_UTTERANCE", "60"))
# A follow-up reply needs nothing like a minute. The longest real one in the log
# of 21.08 ran 5.7 s; every take that reached the 60 s cap was room noise that
# had latched the recording open and came back from vosk as an empty string.
# 15 s was still too long in practice: on 21.08 at 18:09 a follow-up held the
# microphone for 11.4 s of room noise, vosk got an empty string out of it, and
# the owner had to press the key to shut it up. The longest real reply in the log
# runs 5.7 s, so 8 s keeps a margin over it and bounds the annoyance.
MAX_FOLLOWUP_SEC = float(os.environ.get("JARVIS_MAX_FOLLOWUP", "8"))
MIN_UTTERANCE_SEC = 0.5
# speech is "louder than the room": both numbers are tuned by the record-end log.
# 300 sits between the loudest false alarm and the quietest real question in the
# log of 20.08: background music peaked at 124-204, the owner's own quiet phrases at
# 460-797. The old floor of 120 let the music through.
SPEECH_FACTOR = float(os.environ.get("JARVIS_SPEECH_FACTOR", "1.8"))
MIN_SPEECH_LEVEL = float(os.environ.get("JARVIS_MIN_LEVEL", "300"))
# One loud frame is not speech, but three were too many: a frame is 125 ms, so
# that asked for 375 ms of unbroken loudness and threw away two real questions on
# 20.08 (peak 2120 against threshold 1282, and 1467 against 1179 - two loud
# frames each). Two frames is 250 ms and both would have passed.
MIN_SPEECH_FRAMES = int(os.environ.get("JARVIS_MIN_SPEECH_FRAMES", "2"))
# How many loud frames in a row keep a phrase in progress alive. 1 means the old
# behaviour: any single loud frame counts.
#
# 4 was tried on 21.08 to stop keystrokes from holding a take open - the built-in
# microphone sits over the keyboard, and the owner types while the mic is live. It
# worked for that (a 16.9 s take became 5.9 s) and broke the thing that matters
# more: it cut him off mid-sentence when he spoke quietly. His quiet voice in the
# 18:21:48 take swung between 54 and 689 around a bar of 270, so four loud frames
# in a row never happened and the silence counter ran to the end.
#
# The measurement that settles it: over one second his typing and his quiet speech
# produce about the same number of loud frames - 3.9 against 3.0. Loudness alone
# cannot tell them apart, so no value here is right. Back to 1; separating them
# needs a real voice-activity detector, not a threshold.
KEEP_RUN_FRAMES = int(os.environ.get("JARVIS_KEEP_RUN", "1"))
# Voice activity detection. Loudness alone cannot tell his typing from his quiet
# speech - measured on 21.08, both give about three or four loud frames a second
# (3.9 for typing, 3.0 for quiet speech). WebRTC's detector looks at the spectrum
# instead, so a broadband keyboard click and a voiced vowel are different things
# to it. It costs 9 microseconds per 125 ms frame, which is 0.007% of a core
# while recording, and about 15 MB once imported.
#
# It decides only ONE thing: whether a phrase already under way is still going.
# Starting a phrase is still the tuned loudness logic above - that part works, and
# handing the start to the detector as well would invite it to open the take on
# any street noise.
VAD_ENABLED = os.environ.get("JARVIS_VAD", "1") == "1"
# 0 lets almost everything through, 3 is the strictest. 2 is the usual choice for
# a close microphone in a quiet room; picked as a starting point, tune by the log.
VAD_LEVEL = int(os.environ.get("JARVIS_VAD_LEVEL", "3"))
# The detector works on 10 ms pieces, our frame is 125 ms - so twelve verdicts per
# frame. How many of them have to say "speech" for the frame to count as speech.
VAD_MIN_SUB = int(os.environ.get("JARVIS_VAD_MIN_SUB", "10"))
VAD_SUB_LEN = 160          # 10 ms at 16 kHz
# Neither a per-frame threshold nor a run of them works, and the per-frame log of
# 21.08 19:15 shows why. the owner's own speech came out as
#     7 12 8 0 0 0 9 12 4 9 8 6 12
# voiced pieces per frame - the detector jitters on his voice, so "ten or more,
# twice in a row" never happened and the take closed on him after two seconds.
#
# So the decision is made over a window instead of per frame: average voiced
# pieces per frame across the last twelve frames, which is the same 1.5 s the
# silence countdown uses. The sequence above averages 6.2. During the pure typing
# of 19:11 the same average sits near 2.7 - keystrokes are sparse, speech is dense
# even when each single frame is unreliable. 5.0 is between the two; it is a
# starting point from two takes, to be moved by the log.
# 24 frames is three seconds, twice the silence countdown. Deliberately longer:
# his own speech has gaps in it - the 19:18 sequence has runs of two and three
# zeros between words - and a short window dips into those gaps. A long one rides
# over them while a sparse noise like typing stays low, because typing is sparse
# over any window length. On the quiet tail of 19:18, where he trailed off with
# "тише, тише" and the take cut him off, a 12-frame window averaged 4.2 against a
# threshold of 5.0 - it closed on him. Over 24 frames the same tail averages 5.6.
VAD_WIN_FRAMES = int(os.environ.get("JARVIS_VAD_WIN", "24"))
# 2.5 rather than 4.0 because the owner said on 21.08 they will not type while talking,
# so the balance moves all the way towards never cutting him off. Replayed on the
# 19:19 sequence, where he stepped back from the microphone and his voice dropped
# to zeros for six frames: nothing between 1.5 and 4.0 cut that phrase once the
# window was 24 frames, and the only thing the threshold changes is how long the
# take stays open after he really stops - 1.5 s at 4.0, 2.25 s at 2.5, 2.9 s at 1.5.
# 1.5, and the threshold is no longer the whole story - see SILENCE_MID below.
# It was 2.5 and cut him off twice on 22.08 while he paused to think mid-sentence;
# 2.0 did it again within the hour. Replaying the 12:59 take frame by frame, 2.5
# ends it at 7.38 s, and 2.0 or lower never ends it at all - but a threshold alone
# cannot tell "thinking for a second" from "finished", because both look like the
# same quiet window. Hence the second, longer pause below.
VAD_WIN_AVG = float(os.environ.get("JARVIS_VAD_WIN_AVG", "1.5"))
# ...and a single frame far above the threshold is speech too - a short "да" or
# "стоп" never gets 250 ms. The music that started all this peaked at 124-204,
# which does not even reach the 300 floor, so this cannot bring it back.
LOUD_ENOUGH_ALONE = float(os.environ.get("JARVIS_LOUD_ALONE", "1.6"))
# Starting a phrase and continuing one need different bars. With music in the room
# the floor climbed to ~700 and the start bar to ~1260, above the middle of
# the owner's voice - so most frames of a phrase they were still speaking counted as
# silence, 1.7 s of it piled up in about two seconds and the recording was cut
# mid-sentence. Once speech has started the bar drops to 45% of the start bar,
# but never below the room itself times 1.15, or a noisy room would keep the
# recording open until the 25-second cap.
CONTINUE_FACTOR = float(os.environ.get("JARVIS_CONTINUE_FACTOR", "0.45"))
CONTINUE_FLOOR = float(os.environ.get("JARVIS_CONTINUE_FLOOR", "1.15"))
# The continue bar needs an absolute floor of its own, the way the start bar has
# MIN_SPEECH_LEVEL. Without one it follows a quiet room all the way down: on
# 21.08 the music was paused for listening, the measured floor fell 272 -> 179
# and the continue bar 313 -> 206, so room noise swinging 200-500 held the take
# open for 26.6 s and vosk recognised nothing at all. Replaying that take frame
# by frame: at 206 it runs the full 26.6 s, at 270 it ends after 4.9 s. 270 and
# not 300 because his own quiet phrases measure 460-797 - 270 keeps the headroom
# and 300 buys no extra silence.
CONTINUE_MIN_LEVEL = float(os.environ.get("JARVIS_CONTINUE_MIN_LEVEL", "270"))
# ...but 270 belongs to a voice at arm's length. Speaking from across the room on
# 22.08 17:23 the owner came in at 100-250 with the room at 40, so every second frame
# of a phrase he was still saying counted as silence. So the bar also follows the
# phrase itself: a third of its own peak, and 270 stays the ceiling for a loud
# take. On that recording the peak was 547, which puts the bar at 164 - replaying
# the frames, the longest run below it is 0.5 s against the 1.5 s countdown.
FAR_KEEP_SHARE = float(os.environ.get("JARVIS_FAR_KEEP_SHARE", "0.30"))

# Whose voice is holding the take open. Loudness and the detector both answer
# "is this speech", and on 23.08 at 17:36 that was not the question: a follow-up
# take stayed open 34.1 s while the owner typed, the keystrokes reading as speech,
# and the phrase that came out matched his profile at 0.22 against the 0.45 it
# needed - so his own sentence was thrown away as a stranger's. The speaker
# model answers the question that matters, and it is cheap enough to ask during
# the take: 11-30 ms per call against a 125 ms frame, measured on this machine.
# So noise and other people now count as silence, and only his voice keeps a
# phrase alive.
LIVE_SPK = os.environ.get("JARVIS_LIVE_SPK", "1") == "1"
# The window holds VOICED frames only. A wall-clock window failed on 23.08 at
# 17:41: the owner stayed quiet a second and then said two words, so the 1.5 s
# window was mostly silence, his profile scored 0.40 against the 0.45 it needed,
# and his own phrase was cut as a stranger's at a peak of 1458. Silence carries
# no voice, so it can only dilute the print - the model gets speech and nothing
# else.
# 3.0 s of speech, not 1.5: on 23.08 at 17:45 a 1.5 s window recognised him in
# only 6 windows out of 14 while he was the one talking, and twice declared
# silence mid-phrase. The print needs more voice than that to be steady - the
# call costs 30 ms at 3 s against 16 ms at 1.5 s, which changes nothing.
LIVE_SPK_WIN_SEC = float(os.environ.get("JARVIS_LIVE_SPK_WIN", "3.0"))
# Below this much voiced audio there is nothing to judge and the take keeps its
# old behaviour: 1.0 s is where voiceprint.check itself stops refusing (MIN_SEC).
LIVE_SPK_MIN_SEC = float(os.environ.get("JARVIS_LIVE_SPK_MIN", "1.0"))
# Every 2nd voiced frame - four times a second while he talks. One call is
# 11-30 ms against a 125 ms frame, so this is about 12% of one core out of ten.
LIVE_SPK_EVERY = int(os.environ.get("JARVIS_LIVE_SPK_EVERY", "2"))
# Three verdicts in a row. Two was not enough: at 17:45 on 23.08 it cut his own
# phrase twice, so the count went up along with the window. At four checks a
# second this still ends a take within a second of him stopping.
LIVE_SPK_STRIKES = int(os.environ.get("JARVIS_LIVE_SPK_STRIKES", "3"))
CLAUDE_TIMEOUT_SEC = 300
DEBUG = os.environ.get("JARVIS_DEBUG") == "1"
JARVIS_DIR = os.path.expanduser("~/.claude/jarvis")
SESSION_DIR = os.environ.get("JARVIS_CWD", os.path.join(JARVIS_DIR, "session"))
SAY = os.path.join(JARVIS_DIR, "say.sh")
JARVIS_DIR_PATH = pathlib.Path(JARVIS_DIR)
STATE_FILE = JARVIS_DIR_PATH / "state"
# Signed peaks of the microphone, published for the overlay to draw. The overlay
# has no microphone of its own - there is one, and this process holds it - so the
# only way the badge can show the real signal is if we hand it over. One line,
# overwritten in place: a counter so the reader can tell new from old, then four
# peaks in -1..1, one per quarter of the 125 ms frame. Four per frame is 32 values
# a second, which is what it takes to see syllables rather than a blur.
LEVEL_FILE = pathlib.Path(JARVIS_DIR) / "level"
LEVEL_PARTS = 4
# int16 full scale is 32768, but his voice never goes there. 8000 was the first
# guess and left the line barely moving: frame peaks in the log of 21.08 sat at
# 500 to 2400 RMS, a raw sample peak runs three or four times that, so ordinary
# speech only reached a fifth of the slot. 2500 puts quiet speech at a third to
# two thirds of the reach and clips the loudest phrases at the edge - a visible
# line matters more than headroom nobody looks at.
# 1800 after listening to it on real speech: 2500 was closer but still short of
# what he wanted to see. Loud phrases now clip at the edge on purpose.
LEVEL_SCALE = float(os.environ.get("JARVIS_LEVEL_SCALE", "1800"))
ASR_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
CLAUDE_MODEL = os.environ.get("JARVIS_MODEL", "sonnet")  # voice wants speed
# What the voice session may use on its own. Kept short on purpose: every tool it
# is allowed to reach is a tool it may spend seconds on before saying a word.
VOICE_TOOLS = os.environ.get("JARVIS_TOOLS", "WebSearch WebFetch").split()
# MCP servers are started fresh for every question and cost ~2 s each time, so
# voice answers skip them by default; JARVIS_MCP=1 brings Jira/Obsidian/Sentry back
STRICT_MCP = os.environ.get("JARVIS_MCP") != "1"
# a chunk is spoken as soon as it ends in punctuation and is long enough; the
# rest are big so speech keeps its natural flow instead of sounding chopped.
# The first chunk used to be 6 characters, to start the answer while Claude was
# still writing. the owner listened to a whole day of speech on 21.08 and the cost
# showed: an answer opening with "Готово." spoke 0.3 s, stopped, and only then
# said the real sentence. Measured on 49 phrases of that day the voice runs at
# 17.3 characters per second, so 60 characters is about 3.5 s - one spoken
# sentence, which is the portion he asked for. Claude streams far faster than
# the voice speaks, so the first sound arrives only slightly later.
FIRST_CHUNK_CHARS = int(os.environ.get("JARVIS_FIRST_CHUNK", "60"))
NEXT_CHUNK_CHARS = int(os.environ.get("JARVIS_NEXT_CHUNK", "220"))
# the mic hears the speakers, so ignore audio around our own speech and drop a
# transcript that turns out to be Jarvis quoting himself
# 0.4 was enough for the assistant, whose own queue knows when the sound ends.
# The agent listener only sees the lock disappear, and afplay is already draining
# by then - 0.9 covers that tail. Picked after the listener recorded a whole
# spoken answer on 20.08 and sent it back as a question.
ECHO_SETTLE_SEC = float(os.environ.get("JARVIS_ECHO_SETTLE", "0.9"))
ECHO_MEMORY_SEC = 25.0
ECHO_SIMILARITY = 0.55
CHIME = "/System/Library/Sounds/Pop.aiff"
CHIME_VOL = os.environ.get("JARVIS_CHIME_VOL", "0.6")
# The chime plays into the same room the microphone listens to, so the recording
# starts with our own click. Two loud frames mean "speech has started", the
# generous wait-for-speech grace collapses to zero, and 1.5 s later the take ends
# before the owner has said a word - which is exactly what "резко обрубился" was.
CHIME_DEAF_SEC = float(os.environ.get("JARVIS_CHIME_DEAF", "0.45"))
# How fast the bar follows a room that went quiet: 0.15 per 125 ms frame means it
# covers most of the drop in about a second. Picked to be faster than the pause
# that ends a take (1.5 s) and slower than one stray quiet frame.
FLOOR_FALL = float(os.environ.get("JARVIS_FLOOR_FALL", "0.15"))
# There used to be a combo hotkey here, Cmd+Shift+J. Dropped on 26.08: pynput's
# GlobalHotKeys keeps the pressed keys in a set and only removes a key when its
# release event canonicalises to the same object. On macOS a letter released
# with Cmd down often arrives without a character, so "j" stayed in that set for
# good and from then on bare Cmd+Shift woke Jarvis - four times during a call.
# Tap keys (the Keychron Q10 M5 key sends End, F13 covers a firmware remap):
#   one press   = "I'm done talking, answer now" - the dialogue keeps going
#   two presses = interrupt what Jarvis is doing, or wake him when he is idle
# F18 only. End used to be here because the Keychron M5 key sends End - but then
# every End pressed while editing woke Jarvis. keymap.sh remaps M5 instead, and it
# is applied at startup below, so M5 keeps working and End stays a plain key.
# The target moved from F13 to F18 on 21.08: F13 is Print Screen on a Mac, and
# pressing M5 inside Google Sheets wiped the cell being edited. F16..F20 are the
# only function keys neither macOS nor browsers claim.
DEFAULT_TAP_KEYS = "<f18>"
# Done keys: one press means "answer now", never anything else. Space used to be
# here and it was a bad neighbour - the daemon does not suppress the key, so every
# "I'm done" also typed a space into whatever window was in front. The right Option
# key does nothing on its own in macOS, is reachable by the thumb without looking,
# and no application binds it alone.
DEFAULT_DONE_KEYS = "<alt_r>"
# Off key: shuts him up and stops him listening for the wake word. Only acts
# mid-exchange, so pressing Escape in an editor never touches him.
DEFAULT_OFF_KEYS = "<esc>"
DOUBLE_TAP_SEC = float(os.environ.get("JARVIS_DOUBLE_TAP", "0.4"))
# One physical press of the Keychron M5 arrives as two key events, so anything
# closer together than this is the same press, not a double tap.
DEBOUNCE_SEC = float(os.environ.get("JARVIS_DEBOUNCE", "0.18"))
# said while a long question is still being transcribed: one word, no theatre
ACKS = ["Принял.", "Секунду."]
WAKE_VARIANTS = ("джарвис", "джарвес", "жарвис", "джервис", "jarvis", "javis")
# The spotter listens for one word, so it is handed one word and nothing else -
# see VoskEngine. JARVIS_WAKE_GRAMMAR=0 gives it the whole dictionary back.
# Off by default. Under music it is the only thing that hears the name at all
# (11 calls of about 20 on 24.08 against 0 for the full dictionary), but in a
# quiet room it fires on anything that sounds close - measured 19.08 on four
# synthesised files, and again 24.08 as two false wakes in three minutes with
# Spotify on. See patterns/vosk-wake-word-russian.md in the vault.
WAKE_GRAMMAR = os.environ.get(
    "JARVIS_WAKE_GRAMMAR", "0").lower() in ("1", "true", "yes", "on")
# Only the words the model has a pronunciation for belong here. The mangled
# variants above are deliberately left out: vosk drops a word it cannot
# pronounce and prints a warning for it every time a recognizer is built, and
# reset() builds one after every phrase. With a single word in the grammar the
# mangled forms are unnecessary anyway - "джавис" now decodes to the only thing
# the recognizer is allowed to print.
WAKE_GRAMMAR_WORDS = [w.strip() for w in os.environ.get(
    "JARVIS_WAKE_GRAMMAR_WORDS", "джарвис").split(",") if w.strip()]
# The exact substring test above misses whatever the small vosk model mangles the
# name into, and the owner has to call twice. So each word of the phrase is also
# compared to the name by similarity. The threshold cannot go below 0.80: at 0.77
# sits "сервис", which he says all day long, and "дарвин". Above it are the real
# mangles - "чарвис" 0.83, "жавис" 0.91, "джавис" 0.92, "джарвиса" 0.93.
WAKE_SIMILARITY = float(os.environ.get("JARVIS_WAKE_SIMILARITY", "0.80"))
# Anything this close but under the threshold is written to the log with its score,
# so a miss can be answered with a number instead of a guess.
WAKE_NEAR_MISS = float(os.environ.get("JARVIS_WAKE_NEAR", "0.60"))
STOP_WORDS = {"стоп", "отбой", "ничего", "отмена", "забудь", "спасибо"}
# say one of these and the rest of the sentence goes to the orchestrator window
# The orchestrator answers to three names. Bare ones ("шеф", "агент",
# "оркестратор") only count at the start of a sentence, see split_forward.
RESET_WORDS = {"новый разговор", "начнём заново", "забудь всё", "с чистого листа"}

# Where a phrase can go, and what it can set off, is declared in TOML and not
# here: config/rooms.toml, config/actions.toml, plus whatever you drop into
# rooms.d/ and actions.d/. plugins.py documents the format, and
# `jarvis_daemon.py --selfcheck` runs the routing examples written next to each
# room and action.
#
# A broken config stops the daemon rather than starting it with half the routes.
# A question that silently stops reaching the agent it was meant for is worse
# than one that was never asked: nobody notices for days.
try:
    CFG = plugins.load()
except plugins.ConfigError as _cfg_error:
    print(f"jarvis: {_cfg_error}", file=sys.stderr)
    raise SystemExit(2)

OWNER = os.environ.get("JARVIS_OWNER", "").strip()
_OWNER_INTRO = (f"служишь не Старку, а хозяину этого компьютера, его зовут {OWNER}. "
                if OWNER else "служишь не Старку, а хозяину этого компьютера. ")

SYSTEM_PROMPT = (
    "Ты Джарвис - тот самый ИИ-дворецкий из «Железного человека», только "
    + _OWNER_INTRO +
    "Обращайся на «ты», по имени или вообще без обращения; "
    "слово «сэр» не используй никогда. "
    "Манера как в фильмах: спокойная уверенность, сухая ирония, безукоризненная "
    "вежливость без подхалимства. Шутишь коротко и к месту - можешь мягко "
    "поддеть, отметить очевидное с невозмутимым лицом, но юмор никогда не "
    "заменяет ответ и не растягивает его. Без восторгов, без извинений без "
    "причины, без канцелярита. Сам предлагаешь следующий шаг. "
    "Вопрос пришёл голосом и распознан автоматически, огрехи бывают - "
    "догадывайся по смыслу, по мелочам не переспрашивай. "
    "Отвечай по-русски, одним-тремя предложениями, живой речью: никакого "
    "markdown, списков, кода, ссылок и идентификаторов. Числа и номера тикетов "
    "произноси словами. "
    # What he is told about his rooms and actions lives next to them, so that
    # renaming a room or deleting an action rewrites this prompt with it.
    + CFG.hints()
)


# When the rocket agent has no window (started inside another Claude session, or
# in a WebStorm pane), the daemon cannot type into it - but the chef can reach it
# by cross-session message, so the question goes there with instructions.
RELAY_ASK = ('(ответ пойдёт в голос: три-четыре предложения, без разметки, '
             'таблиц, ссылок и идентификаторов) Спроси через SendMessage сессию '
             '«{peer}» и перескажи её ответ. Про то, что отправил вопрос, не '
             'отчитывайся - Джарвис озвучивает каждую твою реплику, и лишняя '
             'строка звучит как ложный ответ. Напиши один раз, по делу: {q}')
# An agent's answer is read off its screen and spoken as it is, so every question
# asked by voice carries this mark. Without it the chef answered "какие сессии
# активны" with an ASCII table and Jarvis read its borders out loud.
VOICE_ASK = ("(ответ пойдёт в голос: три-четыре предложения, без разметки, "
             "таблиц, ссылок и идентификаторов) {q}")


# In listen-only mode stdout is an event stream for the Monitor tool: every line
# there becomes a notification in the agent's session. Diagnostics belong on
# stderr, or "wake!" and "asr worker ready" arrive as if the owner had said them.
LOG_STREAM = sys.stdout


# PortAudio can stop calling the callback without an error - the device changed,
# another app grabbed it exclusively - and then a blocking get() waits forever.
# On 20.08 the listener sat like that for eight minutes: alive, silent, no states
# written, no wake word heard. Both modes now notice and reopen the stream.
MIC_SILENT_SEC = float(os.environ.get("JARVIS_MIC_SILENT", "5"))


class Mic:
    """The input stream, kept alive.

    Reopening is done here and not in the loops, so both the assistant and the
    listener keep their plain `while True` - a stalled microphone is a plumbing
    problem, not something the conversation logic should know about.
    """

    def __init__(self, engine, audio_q):
        self.engine = engine
        self.q = audio_q
        self.stream = None

    def _cb(self, indata, _frames, _time, status):
        if status:
            log(f"mic status: {status}")
        self.q.put(indata[:, 0].copy())

    def open(self):
        self.close()
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                     dtype="int16",
                                     blocksize=self.engine.frame_len,
                                     callback=self._cb)
        self.stream.start()

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                log(f"mic close failed: {e}")
            self.stream = None

    @contextlib.contextmanager
    def open_ctx(self):
        self.open()
        global MIC
        MIC = self
        try:
            yield self
        finally:
            MIC = None
            self.close()


MIC: "Mic | None" = None

# Locking the screen must take the microphone away at once: a locked Mac is a Mac
# the owner walked away from, and anything said near it is not addressed to Jarvis.
# The key exists in the session dictionary only while the screen is locked.
_LOCK_CACHE = {"at": 0.0, "value": False}


def screen_locked() -> bool:
    """Is the screen locked right now? Asked at most once per second."""
    now = time.monotonic()
    if now - _LOCK_CACHE["at"] < 1.0:
        return _LOCK_CACHE["value"]
    try:
        from Quartz import CGSessionCopyCurrentDictionary
        info = CGSessionCopyCurrentDictionary() or {}
        locked = bool(info.get("CGSSessionScreenIsLocked"))
    except Exception:
        locked = False        # cannot tell - better to keep listening than to go deaf
    _LOCK_CACHE.update(at=now, value=locked)
    return locked


def locked_pause(trigger, audio_q, engine) -> bool:
    """True while the screen is locked; closes the mic and reopens it after unlock."""
    if screen_locked():
        if MIC is not None and MIC.stream is not None:
            MIC.close()
            log("экран заблокирован - микрофон отпущен, не слушаю")
        if trigger.state != trigger.ASLEEP:
            trigger.set_state(trigger.ASLEEP)
        time.sleep(0.5)
        return True
    if MIC is not None and MIC.stream is None:
        MIC.open()
        flush(audio_q)
        engine.reset()
        log("экран разблокирован - слушаю снова")
    return False


def next_frame(audio_q):
    """One frame, waiting through a stream that had to be reopened."""
    while True:
        try:
            return audio_q.get(timeout=MIC_SILENT_SEC)
        except queue.Empty:
            log(f"микрофон молчит {MIC_SILENT_SEC:.0f}s, перезапускаю поток")
            if MIC is None:
                continue
            try:
                MIC.open()
            except Exception as e:
                log(f"поток не открылся: {e}")
                time.sleep(1.0)


def apply_keymap() -> None:
    """Make the Keychron M5 key send F13, if that keyboard is here.

    hidutil mappings do not survive a reboot, so this runs on every start of
    either mode - otherwise M5 falls back to End, which Jarvis no longer listens
    to, and the key would silently stop working.
    """
    script = os.path.join(JARVIS_DIR, "keymap.sh")
    try:
        r = subprocess.run(["bash", script], capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            log(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"keymap failed: {e}")


# The listener writes its diagnostics into the pipe of whichever chat session
# started it, and no other session can read that. A copy on disk is what makes
# "посмотри по логам" answerable from anywhere.
LOG_FILE = os.path.expanduser("~/.claude/jarvis/listener.log")
LOG_FILE_MAX = 2_000_000    # bytes; older lines are dropped by a plain rewrite


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, file=LOG_STREAM, flush=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_FILE_MAX:
            with open(LOG_FILE) as f:
                tail = f.readlines()[-2000:]
            with open(LOG_FILE, "w") as f:
                f.writelines(tail)
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%d.%m %H:%M:%S')} {msg}\n")
    except OSError:
        pass


# every child runs in its own process group, so an interrupt kills the whole
# chain (say.sh -> uvx/edge-tts -> afplay), not just the shell wrapper
_procs: set[subprocess.Popen] = set()
_procs_lock = threading.Lock()


TURN = {"t0": None, "seen": set()}


def turn_start() -> None:
    """A voice turn begins the moment the wake word fires."""
    TURN["t0"] = time.monotonic()
    TURN["seen"] = set()
    TURN["answer_queued"] = False


def turn_mark(stage: str) -> None:
    """One line per stage, so the whole chain is readable in daemon.log."""
    t0 = TURN.get("t0")
    if t0 is None or stage in TURN["seen"]:
        return
    TURN["seen"].add(stage)
    log(f"этап {stage}: {time.monotonic() - t0:.2f} с от пробуждения")


def trace(msg: str) -> None:
    """Diagnostic trail with milliseconds, file only.

    "он резко обрубился" is unanswerable from the event stream alone: it shows
    what was heard, never why the recorder stopped. This writes the whole envelope
    of every take - thresholds, frame levels, the exact reason - so the next
    report can be answered by reading one file instead of guessing.
    """
    try:
        stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%d.%m')} {stamp} | {msg}\n")
    except OSError:
        pass


def log_quiet(msg: str) -> None:
    """Into the file only: useful for diagnosing later, noise in the event stream."""
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%d.%m %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def spawn(cmd: list[str], **kw) -> subprocess.Popen:
    p = subprocess.Popen(cmd, start_new_session=True, **kw)
    with _procs_lock:
        _procs.add(p)
    return p


def reap(p: subprocess.Popen) -> None:
    with _procs_lock:
        _procs.discard(p)


def kill_children() -> None:
    """Stop whatever Jarvis is saying or thinking right now."""
    with _procs_lock:
        procs = list(_procs)
        _procs.clear()
    for p in procs:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    # anything still making noise from an earlier run
    subprocess.run(["pkill", "-x", "afplay"], capture_output=True)


def kill_proc(p: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    reap(p)


def speak(text: str, wait: bool = True, trigger=None) -> None:
    p = spawn(["bash", SAY, text])
    if not wait:
        return
    while p.poll() is None:
        if trigger is not None and trigger.abort.is_set():
            kill_proc(p)  # stop mid-sentence, do not just stop waiting
            return
        time.sleep(0.05)
    reap(p)


def chime() -> None:
    subprocess.Popen(["afplay", "-v", CHIME_VOL, CHIME])


# What Jarvis says to a stranger. He says it once and then holds his tongue for a
# while: a television in the room would otherwise have him repeating it forever.
STRANGER_LINE = os.environ.get(
    "JARVIS_STRANGER_LINE",
    "Извините, мне разрешено разговаривать только с хозяином этого компьютера.")
STRANGER_QUIET_SEC = float(os.environ.get("JARVIS_STRANGER_QUIET", "60"))
_refused_at = 0.0


def warm_voiceprint() -> None:
    try:
        import voiceprint
        voiceprint.warmup()
    except Exception as e:
        log(f"голосовой профиль не поднялся: {e}")


# A wrong refusal is the expensive kind of mistake: he spoke, nothing happened,
# and the recording that would have taught the profile is gone. So the last few
# are kept on disk, and "enroll_voice.py --forgive" turns the ones that were
# really him into takes.
REJECTED_DIR = os.path.join(JARVIS_DIR, "rejected")
REJECTED_KEEP = 10


def keep_rejected(audio, score: float) -> None:
    try:
        os.makedirs(REJECTED_DIR, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{score:.2f}.wav"
        with wave.open(os.path.join(REJECTED_DIR, name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(audio.tobytes())
        old = sorted(os.listdir(REJECTED_DIR))[:-REJECTED_KEEP]
        for stale in old:
            os.unlink(os.path.join(REJECTED_DIR, stale))
    except Exception as e:
        trace(f"отброшенную фразу сохранить не смог: {e}")


def voice_is_his(audio, speaker=None, aloud: bool = True) -> bool:
    """Whose voice was that? Anything unclear counts as his.

    The phrase is already recorded by now - the wake word alone is too short to
    judge a voice on, so the take happens first and is thrown away afterwards.

    aloud=False drops the take without a word. It is for windows nobody opened -
    the follow-up one opens by itself, and a fan or a keyboard in the room would
    otherwise have him refusing a stranger who was never there.
    """
    global _refused_at
    judged = audio
    if (_voiced_audio is not None and _voiced_at
            and time.monotonic() - _voiced_at < 5.0):
        judged = _voiced_audio
    try:
        import voiceprint
        ok, score, why = voiceprint.check(judged)
    except Exception as e:
        log(f"проверка голоса пропущена: {e}")
        return True
    if ok:
        if why not in ("off", "no profile") and not why.startswith("too short"):
            trace(f"голос: {why}")
        return True
    log(f"чужой голос ({score:.2f}), фраза отброшена")
    trace(f"голос: {why}")
    keep_rejected(audio, score)
    if not aloud:
        trace("отказ вслух пропущен: эту запись никто не начинал")
        return False
    if time.monotonic() - _refused_at > STRANGER_QUIET_SEC:
        _refused_at = time.monotonic()
        # through the speaker where there is one: it remembers what was said, so
        # the microphone hearing the refusal does not take it for a new question
        if speaker is not None:
            speaker.say(STRANGER_LINE)
            speaker.wait()
            speaker.settle()
        else:
            speak(STRANGER_LINE, trigger=None)
    else:
        trace("отказ вслух пропущен: недавно уже отвечал чужому")
    return False


def heard_wake(text: str) -> bool:
    """Did the phrase contain his name - exactly, or mangled beyond a substring?

    Two passes on purpose. The substring test is what has always worked and costs
    nothing; the similarity pass is what catches "джавис" and "чарвис", which the
    small model produces often enough that he ends up calling twice.
    """
    if not text:
        return False
    low = text.lower()
    if any(v in low for v in WAKE_VARIANTS[:4]):
        return True
    best, word = 0.0, ""
    for w in re.findall(r"[а-яёa-z]+", low):
        if len(w) < 5:                      # "жар", "чар" match too much of anything
            continue
        for v in WAKE_VARIANTS[:4]:
            r = difflib.SequenceMatcher(None, w, v).ratio()
            if r > best:
                best, word = r, w
    if best >= WAKE_SIMILARITY:
        log(f"пробуждение по похожести: {word!r} на {best:.2f}")
        return True
    if best >= WAKE_NEAR_MISS:
        log_quiet(f"почти услышал имя: {word!r} похожесть {best:.2f}, "
                  f"порог {WAKE_SIMILARITY}")
    return False


_level_seq = 0


def reset_level() -> None:
    """Flat line on disk, so the overlay's read never has to fail.

    A missing file made the badge take the exception path 32 times a second,
    which cost more than the drawing did.
    """
    try:
        LEVEL_FILE.write_text("0 " + " ".join(["0.000"] * LEVEL_PARTS))
    except OSError:
        pass


reset_level()   # once at import: the file must exist before the badge looks


def publish_level(frame: np.ndarray) -> None:
    """Hand the overlay four signed peaks of this frame. Never raises."""
    global _level_seq
    try:
        _level_seq += 1
        step = max(1, len(frame) // LEVEL_PARTS)
        out = []
        for i in range(LEVEL_PARTS):
            chunk = frame[i * step:(i + 1) * step]
            if not len(chunk):
                out.append(0.0)
                continue
            lo, hi = int(chunk.min()), int(chunk.max())
            # signed peak keeps the envelope; the sign alone makes the trace
            # wobble around the axis instead of climbing on one side
            peak = hi if hi >= -lo else lo
            out.append(max(-1.0, min(1.0, peak / LEVEL_SCALE)))
        LEVEL_FILE.write_text(
            str(_level_seq) + " " + " ".join(f"{v:.3f}" for v in out))
    except (OSError, ValueError):
        pass


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))


_vad = None


def vad_speech(frame: np.ndarray) -> int | None:
    """How many of the twelve 10 ms pieces of this frame hold a voice, 0..12.

    Returns None if the detector is unavailable. A count rather than a yes/no on
    purpose: the threshold has to be picked from the real distribution, and on
    21.08 guessing it at three out of twelve called 232 frames of 238 speech,
    silence included.

    Built lazily and never rebuilt: a failed import must degrade to the old
    loudness rule, not take the microphone down with it.
    """
    global _vad
    if not VAD_ENABLED:
        return None
    if _vad is None:
        # Straight to the C extension, not the webrtcvad.py wrapper around it: that
        # wrapper does `import pkg_resources` just to read its own version string,
        # and setuptools 84 dropped pkg_resources - which is why the detector
        # silently stayed off through two test runs on 21.08. Talking to
        # _webrtcvad directly also saves the 15 MB the setuptools import cost.
        try:
            import _webrtcvad
            handle = _webrtcvad.create()
            _webrtcvad.init(handle)
            _webrtcvad.set_mode(handle, VAD_LEVEL)
            _vad = (_webrtcvad, handle)
        except Exception as e:            # noqa: BLE001 - any failure means "no detector"
            log(f"детектор речи не поднялся, остаюсь на громкости: {e}")
            _vad = False
    if _vad is False:
        return None
    lib, handle = _vad
    data = np.ascontiguousarray(frame, dtype=np.int16)
    hits = 0
    for start in range(0, len(data) - VAD_SUB_LEN + 1, VAD_SUB_LEN):
        try:
            if lib.process(handle, SAMPLE_RATE,
                           data[start:start + VAD_SUB_LEN].tobytes(), VAD_SUB_LEN):
                hits += 1
        except Exception:                 # noqa: BLE001 - bad frame length, treat as no voice
            return None
    return hits


def strip_wake(text: str) -> str:
    """Drop a leading wake word so only the command is left."""
    words = text.split()
    while words:
        bare = re.sub(r"[^а-яёa-z]", "", words[0].lower())
        if bare and any(bare.startswith(v[:5]) for v in WAKE_VARIANTS):
            words.pop(0)
            continue
        if bare in {"эй", "hey", "hi"} and len(words) > 1:
            words.pop(0)
            continue
        break
    return " ".join(words).lstrip(" ,.!?-").strip()


def split_forward(command: str) -> tuple[str, str, str, str]:
    """Explicit addressing: ("ask"|"tell"|"", room id, task, the wording used).

    The wording comes back so the caller can tell an order ("передай шефу ...")
    from a bare name ("шеф ..."): only an order outranks the automatic routes.
    The words themselves are in config/rooms.toml, one list per room.
    """
    return CFG.address(command)


# the owner speaks Russian. On music and room noise parakeet hallucinates fluent
# English ("I think that's a good thing." from 1.6 s of silence in the log of
# 20.08), so a transcript without a single Russian letter is noise, not a command.
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^а-яёa-z ]", "", text.lower()).split())



def route_auto(norm: str) -> str:
    """Automatic destination for this phrase: an action id, a room id, or "".

    Actions win over rooms, and both lose to explicit addressing, which the
    caller resolves first. Order inside each group is the order of the config.
    """
    return CFG.route(norm)


MEDIA_ON = os.environ.get("JARVIS_MEDIA", "1") == "1"
# When we pause the music the room goes quiet in an instant, and the noise floor
# measured a moment earlier - with the music still playing - is suddenly far above
# his voice. Every frame then counts as silence and the take dies at once. For a
# few seconds after pausing, the fixed floor is used instead of the measured one.
MEDIA_QUIET_SEC = 6.0
_media_paused_at = 0.0


def media_follow(state: str) -> None:
    """Music steps aside while Jarvis has the floor, and comes back when he thinks.

    Listening and speaking both mean a microphone or a speaker is busy, so the
    track is paused; thinking and idle give it back. Only what was really playing
    is touched, so a Spotify that was already paused stays paused.
    """
    if not MEDIA_ON:
        return

    def work() -> None:
        try:
            import media
            if state in ("listening", "speaking"):
                paused = media.pause()
                if paused:
                    global _media_paused_at
                    _media_paused_at = time.monotonic()
                    log(f"музыка на паузе: {', '.join(paused)}")
            else:
                back = media.resume()
                if back:
                    log(f"музыка снова играет: {', '.join(back)}")
        except Exception as e:
            log(f"media control failed: {e}")

    threading.Thread(target=work, daemon=True).start()


# --- hotkey / signals -------------------------------------------------------

class Trigger:
    """Shared state for the hotkeys and signal handlers.

    tap once  -> stop recording and answer now (the dialogue continues)
    tap twice -> interrupt the current answer, or wake Jarvis if he is idle
    """

    IDLE, LISTENING, THINKING, SPEAKING = "idle", "listening", "thinking", "speaking"
    ASLEEP = "asleep"  # running, but the wake word is ignored until a key wakes him

    def __init__(self):
        self.start = threading.Event()
        self.stop = threading.Event()
        self.abort = threading.Event()
        self.cancel = threading.Event()  # abort AND do not listen afterwards
        self.asleep = START_ASLEEP
        self.state = self.IDLE
        self._listeners: list = []  # keep listeners alive
        self.set_state(self.IDLE)

    def set_state(self, state: str) -> None:
        """Publish the state for the menu bar indicator to read."""
        self.state = state
        try:
            STATE_FILE.write_text(state)
        except OSError:
            pass
        media_follow(state)

    @property
    def recording(self) -> bool:
        return self.state == self.LISTENING

    def wake_up(self) -> None:
        if self.asleep:
            log("key: waking up, wake word is live again")
            self.asleep = False

    def toggle_sleep(self) -> None:
        self.asleep = not self.asleep
        log("asleep" if self.asleep else "awake")
        if self.asleep:
            self.cancel.set()
            self.abort.set()
            self.stop.set()

    def tap(self, wake_from_idle: bool = True) -> None:
        """One press always means "switch to me"."""
        self.wake_up()
        if self.state == self.LISTENING:
            log("key: done talking, answering now")
            self.stop.set()
        elif self.state in (self.THINKING, self.SPEAKING):
            log(f"key: interrupt {self.state}, listening to me instead")
            self.abort.set()
            self.stop.set()
        elif wake_from_idle:
            log("key: wake")
            self.start.set()

    def woke_by_key(self) -> bool:
        """Did this exchange start with a key press rather than the wake word?"""
        return bool(getattr(self, "_by_key", False))

    def mark_wake_source(self, by_key: bool) -> None:
        self._by_key = by_key

    def escape(self) -> None:
        """Same as a double tap: shut up and wait, still listening for "Джарвис".

        Does nothing while idle on purpose - Escape is pressed constantly in
        editors, and reacting to every one of those would be unusable.
        """
        if self.state == self.IDLE:
            if DEBUG:
                log("esc ignored (idle)")
            return
        log(f"esc: quiet (was {self.state})")
        self.cancel.set()
        self.abort.set()
        self.stop.set()

    def double_tap(self) -> None:
        """Two presses mean "drop it entirely" - no listening afterwards."""
        self.wake_up()
        if self.state != self.IDLE:
            log(f"key x2: cancel (was {self.state})")
            self.cancel.set()
            self.abort.set()
            self.stop.set()
        else:
            log("key x2: wake")
            self.start.set()

    def install(self) -> None:
        signal.signal(signal.SIGUSR1, lambda *_: self.tap())  # switch to me
        signal.signal(signal.SIGUSR2, lambda *_: self.double_tap())
        signal.signal(signal.SIGHUP, lambda *_: self.toggle_sleep())
        def keyset(env: str, default: str) -> set[str]:
            raw = os.environ.get(env, default)
            return {k.strip() for k in raw.split(",")
                    if k.strip() and k.strip().lower() != "off"}

        taps = keyset("JARVIS_TAP_KEYS", DEFAULT_TAP_KEYS)
        dones = keyset("JARVIS_DONE_KEYS", DEFAULT_DONE_KEYS) - taps
        offs = keyset("JARVIS_OFF_KEYS", DEFAULT_OFF_KEYS) - taps - dones
        if not taps and not dones and not offs:
            return
        try:
            from pynput import keyboard
            self._listeners.append(
                self._watch_taps(keyboard, taps, dones, offs))
            log(f"keys active: tap {', '.join(sorted(taps))} | "
                f"done {', '.join(sorted(dones))} | off {', '.join(sorted(offs))}")
        except Exception as e:
            log(f"hotkeys unavailable ({e.__class__.__name__}: {e}); "
                f"grant Input Monitoring to your terminal, or use kill -USR1/-USR2")

    def _watch_taps(self, keyboard, taps: set[str], dones: set[str],
                    offs: set[str] = frozenset()):
        """Single press acts at once; a second press within the window means x2.

        Acting on the first press keeps "answer now" instant - waiting out the
        double-tap window would add DOUBLE_TAP_SEC of delay to every answer.
        Events closer than DEBOUNCE_SEC are one physical press repeated by the
        keyboard or the OS, so they are dropped.
        """
        last: dict[str, float] = {}

        def name_of(key) -> str:
            if isinstance(key, keyboard.KeyCode):
                return key.char or f"<{key.vk}>"
            return f"<{key.name}>"

        def on_press(key):
            name = name_of(key)
            if name not in taps and name not in dones and name not in offs:
                return
            now = time.monotonic()
            since = now - last.get(name, 0.0)
            if since <= DEBOUNCE_SEC:
                if DEBUG:
                    log(f"key: duplicate event {since * 1000:.0f} ms, dropped")
                return
            last[name] = now
            if name in offs:
                self.escape()
            elif name in dones:  # Space must never start a recording by itself
                self.tap(wake_from_idle=False)
            elif since <= DOUBLE_TAP_SEC:
                self.double_tap()
            else:
                self.tap()

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        return listener


# --- wake engines -----------------------------------------------------------

class VoskEngine:
    """Offline Russian ASR used as a wake word spotter. Native "Джарвис"."""
    frame_len = 2000  # 125 ms

    def __init__(self):
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        path = os.environ.get(
            "JARVIS_VOSK_MODEL",
            os.path.join(JARVIS_DIR, "models", "vosk-model-small-ru-0.22"),
        )
        if not os.path.isdir(path):
            sys.exit(f"Vosk model not found: {path}\n"
                     "Download: https://alphacephei.com/vosk/models/"
                     "vosk-model-small-ru-0.22.zip")
        self._KaldiRecognizer = KaldiRecognizer
        self.model = Model(path)
        # A wake spotter only ever needs to hear one word. With the whole Russian
        # dictionary open, "Джарвис" competes with every other word in it, and
        # song lyrics win that competition often enough that he has to call
        # twice. A grammar leaves the recognizer nothing else it can print: the
        # name, or "[unk]" for everything else.
        self.grammar = (json.dumps(WAKE_GRAMMAR_WORDS + ["[unk]"],
                                   ensure_ascii=False)
                        if WAKE_GRAMMAR and WAKE_GRAMMAR_WORDS else None)
        self.rec = self._new_rec()
        self.name = (f"vosk ({os.path.basename(path)}) - слово «Джарвис»"
                     + (", словарь только из имени" if self.grammar else ""))

    def _new_rec(self):
        """Fails open: a model without a dynamic graph gets the old recognizer."""
        if self.grammar:
            try:
                return self._KaldiRecognizer(self.model, SAMPLE_RATE,
                                             self.grammar)
            except Exception as e:
                log("грамматику слова-будильника модель не приняла, "
                    f"слушаю полным словарём: {e}")
                self.grammar = None
        return self._KaldiRecognizer(self.model, SAMPLE_RATE)

    def detect(self, frame: np.ndarray) -> bool:
        final = self.rec.AcceptWaveform(frame.tobytes())
        if final:
            text = json.loads(self.rec.Result()).get("text", "")
            if text:
                # every finished phrase goes to the log file: this is the only way
                # to answer "I called him three times and he did not hear me"
                log_quiet(f"расслышал: {text!r}")
                if DEBUG:
                    log(f"vosk: {text!r}")
        else:
            text = json.loads(self.rec.PartialResult()).get("partial", "")
        return heard_wake(text)

    def reset(self) -> None:
        self.rec = self._new_rec()


class OwwEngine:
    """openWakeWord "hey jarvis" - free, but trained on English pronunciation."""
    name = "oww (hey jarvis)"
    frame_len = 1280

    def __init__(self, threshold: float | None):
        import openwakeword
        from openwakeword.model import Model
        openwakeword.utils.download_models()
        self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.threshold = threshold if threshold is not None else 0.32
        self.key = None

    def detect(self, frame: np.ndarray) -> bool:
        scores = self.model.predict(frame)
        if self.key is None:
            self.key = next(k for k in scores if "jarvis" in k)
        score = scores[self.key]
        if DEBUG and score > 0.2:
            log(f"wake score {score:.2f}")
        return score >= self.threshold

    def reset(self) -> None:
        self.model.reset()


class PorcupineEngine:
    """Picovoice Porcupine - built-in "jarvis" keyword or a custom .ppn."""

    def __init__(self, access_key: str, threshold: float | None):
        import pvporcupine
        kwargs = {"access_key": access_key,
                  "sensitivities": [threshold if threshold is not None else 0.7]}
        ppn = os.environ.get("JARVIS_PPN")
        if ppn:
            kwargs["keyword_paths"] = [ppn]
            model = os.environ.get("JARVIS_PPN_MODEL")
            if model:
                kwargs["model_path"] = model
            self.name = f"porcupine ({os.path.basename(ppn)})"
        else:
            kwargs["keywords"] = ["jarvis"]
            self.name = "porcupine (jarvis)"
        self.handle = pvporcupine.create(**kwargs)
        self.frame_len = self.handle.frame_length

    def detect(self, frame: np.ndarray) -> bool:
        return self.handle.process(frame) >= 0

    def reset(self) -> None:
        pass


def picovoice_key() -> str:
    key = os.environ.get("JARVIS_PICOVOICE_KEY", "")
    if key:
        return key
    r = subprocess.run(
        ["security", "find-generic-password", "-s", "jarvis-picovoice-key", "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def make_engine():
    want = os.environ.get("JARVIS_WAKE_ENGINE", "vosk")
    raw = os.environ.get("JARVIS_WAKE_THRESHOLD")
    threshold = float(raw) if raw else None
    if want == "oww":
        return OwwEngine(threshold)
    if want == "porcupine":
        key = picovoice_key()
        if key:
            return PorcupineEngine(key, threshold)
        log("no Picovoice key, falling back to vosk")
    return VoskEngine()


# --- audio capture ----------------------------------------------------------

def flush(audio_q: "queue.Queue[np.ndarray]") -> None:
    while not audio_q.empty():
        audio_q.get_nowait()


_voiced_audio = None     # речевая часть последней записи, для сверки голоса
_voiced_at = 0.0         # когда её положили - старую не берём
# How the last take ended, and whether the music was still playing inside it.
# Both are reasons not to judge the voice on that audio - see capture().
_last_end_reason = ""
_music_in_take = False


def live_speaker(window: list) -> bool | None:
    """Is the voice in these last frames his? None when there is no answer.

    Fails open the same way voice_is_his does: no model, no profile, too little
    audio, a broken call - all None, and the take keeps its old behaviour.
    """
    if not LIVE_SPK or not window:
        return None
    try:
        import voiceprint
        audio = np.concatenate(window)
        want = int(LIVE_SPK_WIN_SEC * SAMPLE_RATE)
        if len(audio) > want:
            audio = audio[-want:]
        if len(audio) / SAMPLE_RATE < LIVE_SPK_MIN_SEC:
            return None
        ok, score, why = voiceprint.check(audio)
    except Exception as e:
        trace(f"живая сверка голоса пропущена: {e}")
        return None
    if why in ("off", "no profile") or why.startswith("too short"):
        return None
    return ok


def record(audio_q, noise_floor: float, trigger: Trigger,
           keep_head: bool, wait_sec: float,
           max_sec: float = MAX_UTTERANCE_SEC) -> np.ndarray | None:
    """Record one utterance.

    keep_head=True starts recording immediately (the command may already be in
    flight right after the wake word); False waits for speech to start.
    The recording ends on SILENCE_SEC of quiet, on the hotkey, or at the cap.
    Returns None if the user never spoke.
    """
    if _media_paused_at and time.monotonic() - _media_paused_at < MEDIA_QUIET_SEC:
        # the music has just been silenced: the old floor belongs to a room that
        # no longer exists, so trust the fixed minimum until it is measured again
        noise_floor = MIN_SPEECH_LEVEL / SPEECH_FACTOR
    silence_limit = max(noise_floor * SPEECH_FACTOR, MIN_SPEECH_LEVEL)
    keep_limit = max(silence_limit * CONTINUE_FACTOR,
                     noise_floor * CONTINUE_FLOOR, CONTINUE_MIN_LEVEL)
    floor_est = noise_floor         # the room as it is right now, not as it was
    levels: list[int] = []          # one number per 125 ms frame, for the trace
    trace(f"запись НАЧАЛАСЬ: keep_head={keep_head} ждать речь {wait_sec:.1f}с "
          f"пауза {tuned('silence_sec', SILENCE_SEC):.1f}с "
          f"планка старта {silence_limit:.0f} планка продолжения {keep_limit:.0f} "
          f"шум {noise_floor:.0f}")
    frames: list[np.ndarray] = []
    trigger.stop.clear()
    trigger.set_state(trigger.LISTENING)
    try:
        silent_for = 0.0
        grace = wait_sec
        heard_speech = False
        loud_frames = 0
        run = 0                 # громких кадров подряд, см. KEEP_RUN_FRAMES
        resumed = False         # была ли уже пауза, после которой он продолжил
        pause_peak = 0.0        # самая длинная пауза внутри этой записи
        vad_frames = 0          # кадров, где детектор набрал VAD_MIN_SUB и больше
        vad_used = False        # он вообще отвечал, или мы весь раз на громкости
        vad_hist = [0] * 13     # сколько кадров дали 0, 1, ... 12 голосовых подкадров
        vad_seq: list[int] = []  # вердикт по каждому кадру - по нему подбирается порог
        vad_win: collections.deque = collections.deque(maxlen=VAD_WIN_FRAMES)
        # frames are 80-125 ms depending on the wake engine, so the window is
        # trimmed by samples inside live_speaker, not by a frame count here
        spk_win: collections.deque = collections.deque(maxlen=32)
        voiced: list[np.ndarray] = []   # только кадры, где что-то звучало
        voiced_seen = 0                 # их счётчик, по нему шаг сверки
        spk_strikes = 0         # подряд окон, где голос не его
        spk_checks = 0          # сколько раз вообще спросили
        spk_his = 0             # из них ответов «его голос»
        spk_muted = False       # последнее слово сверки: сейчас говорит не он
        peak = 0.0
        reason = "cap"
        t0 = time.monotonic()
        # The short cap bounds the *wait* for a voice, not the voice itself: on
        # 21.08 the 8 s follow-up cap fired at "я сейчас буду шуметь" while the owner
        # was still mid-sentence. Once a phrase is really under way, the long cap
        # takes over and the phrase is allowed to finish.
        while time.monotonic() - t0 < (MAX_UTTERANCE_SEC if heard_speech else max_sec):
            frame = next_frame(audio_q)
            publish_level(frame)
            if keep_head or heard_speech:
                frames.append(frame)
            level = rms(frame)
            levels.append(int(level))
            if time.monotonic() - t0 < CHIME_DEAF_SEC:
                # our own chime is still sounding: keep the audio, judge nothing
                continue
            peak = max(peak, level)
            # The room can go quiet after the take has started - that is exactly
            # what happens when the music is paused for listening. A bar measured
            # against the old, louder room then sits above his voice: on 21.08 the
            # start bar was 929 while his peak was 652, and the take died empty.
            # So the bar follows the room down, never up: speech only raises it
            # when the next take measures the floor again.
            if not heard_speech and level < floor_est:
                floor_est = (1 - FLOOR_FALL) * floor_est + FLOOR_FALL * level
                silence_limit = max(floor_est * SPEECH_FACTOR, MIN_SPEECH_LEVEL)
                keep_limit = max(silence_limit * CONTINUE_FACTOR,
                                 floor_est * CONTINUE_FLOOR, CONTINUE_MIN_LEVEL)
            # a phrase in progress is held by the lower bar
            if LISTEN_ONLY and os.path.exists(SPEAK_LOCK):
                trace("во время записи появился замок озвучки - говорю сам")
                # The agent started speaking while we were recording. Anything
                # after this point is its own voice - but what the owner had already
                # said is his, and throwing it away looked like "he cut me off".
                if heard_speech:
                    reason = "agent started speaking"
                    break
                return None
            keep_now = keep_limit
            if heard_speech and peak > 0:
                keep_now = max(floor_est * CONTINUE_FLOOR,
                               min(keep_limit, peak * FAR_KEEP_SHARE))
            loud = level > (keep_now if heard_speech else silence_limit)
            if loud:
                loud_frames += 1
                run += 1
                if not heard_speech and not keep_head:
                    frames.append(frame)
                if (loud_frames >= MIN_SPEECH_FRAMES
                        or level > silence_limit * LOUD_ENOUGH_ALONE):
                    if not heard_speech:
                        # the leading silence is not part of the phrase; keeping it
                        # in the window would drag the average under the threshold
                        # for the first seconds of speech and close the take
                        vad_win.clear()
                    heard_speech = True
            else:
                run = 0
            # Only sounding frames reach the speaker model. Silence between
            # words would dilute the print and read as someone else.
            if loud:
                spk_win.append(frame)
                voiced.append(frame)
                voiced_seen += 1
            if (LIVE_SPK and heard_speech and loud
                    and voiced_seen % LIVE_SPK_EVERY == 0):
                his = live_speaker(list(spk_win))
                if his is not None:
                    spk_checks += 1
                    if his:
                        spk_his += 1
                        spk_strikes = 0
                        spk_muted = False
                    else:
                        spk_strikes += 1
                        if spk_strikes >= LIVE_SPK_STRIKES and not spk_muted:
                            spk_muted = True
                            trace(f"живая сверка: голос не его "
                                  f"({spk_strikes} окна подряд) - считаю тишиной")
            hits = vad_speech(frame)
            voice = None
            if hits is not None:
                vad_used = True
                vad_hist[min(hits, 12)] += 1
                vad_seq.append(hits)
                vad_win.append(hits)
                # speech is dense over a window even when single frames jitter
                voice = sum(vad_win) / len(vad_win) >= VAD_WIN_AVG
                if voice:
                    vad_frames += 1
            # What keeps a phrase in progress alive. Once the detector is there it
            # answers this, because that is the one question loudness gets wrong:
            # a keystroke is loud and is not speech, a quiet vowel is speech and is
            # not loud. Starting a phrase stays on loudness, and without a detector
            # the whole thing falls back to the old rule.
            if heard_speech and voice is not None:
                # Two witnesses, and either one keeps the take open. The detector
                # alone used to decide, and on a distant voice it is simply deaf:
                # at 17:23 on 22.08 it returned zero voiced pieces for forty
                # frames in a row while the levels sat at 100-250 and the owner was
                # still talking. Loudness is wrong about keystrokes, the detector
                # is wrong about a quiet voice across the room - so a phrase ends
                # only when both of them say it ended.
                keeps_alive = voice or (loud and run >= KEEP_RUN_FRAMES)
            else:
                keeps_alive = loud and (run >= KEEP_RUN_FRAMES or not heard_speech)
            # Someone else, or the room: this is silence no matter how loud
            # it is, and no matter what the speech detector thinks of it.
            if spk_muted:
                keeps_alive = False
            if keeps_alive:
                # he started again after a pause long enough to be a thought -
                # from here on this take is treated as thinking out loud
                if heard_speech and silent_for >= tuned("silence_sec", SILENCE_SEC) * 0.6:
                    resumed = True
                pause_peak = max(pause_peak, silent_for)
                silent_for = 0.0
                grace = 0.0
            else:
                silent_for += len(frame) / SAMPLE_RATE
                limit = SILENCE_MID if resumed else tuned("silence_sec", SILENCE_SEC)
                # The long "he is thinking out loud" wait exists because a pause
                # might be a pause. Once the speaker model says the voice is not
                # his any more, there is nothing to wait for - on 23.08 at 17:45
                # that wait held a take 17.3 s while he had already stopped.
                if spk_muted:
                    limit = tuned("silence_sec", SILENCE_SEC)
                if silent_for >= limit + grace:
                    reason = "silence"
                    break
            if trigger.stop.is_set():
                reason = "key"
                break
        # these numbers are the only way to tell "he said nothing" from
        # "his voice was quieter than the threshold"
        log(f"record end: {reason}, peak {peak:.0f}, start {silence_limit:.0f}, "
            f"keep {keep_limit:.0f}, noise floor {noise_floor:.0f}, "
            f"loud frames {loud_frames}, speech {'yes' if heard_speech else 'NO'}"
            + (f", думал вслух (пауза до {pause_peak:.1f}с, ждал {SILENCE_MID}с)"
               if resumed else "")
            + (f", vad frames {vad_frames}/{len(levels)}" if vad_used
               else ", vad OFF")
            + (f", его голос {spk_his}/{spk_checks} окон" if spk_checks
               else (", живая сверка не спрашивалась" if LIVE_SPK else "")))
        if vad_used:
            trace("детектор, сколько кадров дали столько голосовых подкадров из 12: "
                  + " ".join(f"{n}:{c}" for n, c in enumerate(vad_hist) if c))
            trace("детектор по кадрам (голосовых подкадров из 12): "
                  + " ".join(str(v) for v in vad_seq[-240:]))
        trace(f"планки в конце: старт {silence_limit:.0f} продолжение {keep_limit:.0f} "
              f"(по пику фразы {peak * FAR_KEEP_SHARE:.0f}) "
              f"шум по ходу записи {floor_est:.0f}")
        trace(f"запись КОНЧИЛАСЬ: причина={reason} длилась {time.monotonic() - t0:.1f}с "
              f"тишины в конце {silent_for:.1f}с речь={'да' if heard_speech else 'НЕТ'} "
              f"громких кадров {loud_frames} пик {peak:.0f}")
        trace("уровни по кадрам (125 мс каждый): " + " ".join(str(v) for v in levels[-240:]))
        global _last_end_reason, _music_in_take
        _last_end_reason = reason
        # The music is silenced in a background thread, so the first part of a
        # take can still have it in. _media_paused_at is the moment it actually
        # went quiet: later than t0 means part of this take has music in it.
        _music_in_take = bool(_media_paused_at and _media_paused_at > t0)
        if not heard_speech or not frames:
            trace("взято НИЧЕГО: речи не было или кадры пусты")
            return None
        audio = np.concatenate(frames)
        # The final check gets the same diet as the live one: speech without the
        # pauses. On 23.08 at 17:41 a whole-take print scored 0.40 on two words
        # after a second of quiet; the voiced part alone is what he sounds like.
        global _voiced_audio, _voiced_at
        if voiced and sum(len(f) for f in voiced) / SAMPLE_RATE >= LIVE_SPK_MIN_SEC:
            _voiced_audio = np.concatenate(voiced)
            _voiced_at = time.monotonic()
        else:
            _voiced_audio = None
        if len(audio) / SAMPLE_RATE - silent_for < MIN_UTTERANCE_SEC:
            log("too short, ignoring")
            trace(f"взято НИЧЕГО: слишком коротко, {len(audio) / SAMPLE_RATE:.1f}с звука")
            return None
        return audio
    finally:
        trigger.stop.clear()


# --- speech to text / claude ------------------------------------------------

class Asr:
    """Long-lived parakeet process: keeps the model loaded between utterances.

    Reloading it per question cost 1.5-2.5 s; warm it answers in 0.13-0.14 s.
    Model load takes about 9 s and happens once, while Jarvis is still asleep.
    """

    WORKER = os.path.join(JARVIS_DIR, "asr_worker.py")

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        threading.Thread(target=self.start, daemon=True).start()

    def start(self) -> None:
        try:
            proc = subprocess.Popen(
                ["uv", "run", "--quiet", self.WORKER],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                start_new_session=True)
        except OSError as e:
            log(f"asr worker failed to start: {e}")
            return
        line = proc.stdout.readline().strip() if proc.stdout else ""
        if line != "!READY":
            log(f"asr worker did not come up: {line!r}")
            return
        with self.lock:
            self.proc = proc
        log("asr worker ready")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def transcribe(self, wav_path: str) -> str | None:
        """Returns the transcript, or None if the worker is unusable."""
        with self.lock:
            if not self.alive():
                return None
            try:
                self.proc.stdin.write(wav_path + "\n")
                self.proc.stdin.flush()
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        return None
                    line = line.rstrip("\n")
                    if line.startswith("TEXT "):
                        return line[5:].strip()
                    if line.startswith("!ERR"):
                        log(f"asr worker: {line}")
                        return ""
            except (BrokenPipeError, OSError) as e:
                log(f"asr worker broke: {e}")
                self.proc = None
                return None


ASR = Asr()


def transcribe(audio: np.ndarray, trigger=None) -> str | None:
    """Local speech-to-text. Returns None if the user interrupted."""
    with tempfile.TemporaryDirectory() as td:
        wav_path = os.path.join(td, "cmd.wav")
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(audio.tobytes())
        heard = ASR.transcribe(wav_path)
        if heard is not None:
            fixed = vocab_correct(heard)
            if DEBUG and fixed != heard:
                log(f"vocab: {heard!r} -> {fixed!r}")
            return fixed
        log("asr worker unavailable, falling back to a one-shot run")
        p = spawn(["uvx", "--from", "parakeet-mlx", "parakeet-mlx",
                   "--model", ASR_MODEL, "--output-format", "txt",
                   "--output-dir", td, wav_path],
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 180
        while p.poll() is None:
            if trigger is not None and trigger.abort.is_set():
                kill_proc(p)
                return None
            if time.monotonic() > deadline:
                kill_proc(p)
                log("ASR timed out")
                return ""
            time.sleep(0.05)
        _, err = p.communicate()
        reap(p)
        txt_path = os.path.join(td, "cmd.txt")
        if p.returncode != 0 or not os.path.exists(txt_path):
            log(f"ASR failed: {err.decode(errors='replace')[:300]}")
            return ""
        with open(txt_path) as f:
            heard = " ".join(f.read().split())
    # reuse the FluidVoice dictionary: parakeet-mlx has no vocabulary boosting
    fixed = vocab_correct(heard)
    if DEBUG and fixed != heard:
        log(f"vocab: {heard!r} -> {fixed!r}")
    return fixed


# The room a question goes to when nothing else claimed it, and the one
# `target()` falls back on. It is the first room in the config - not a special
# kind of room, just the first row.
MAIN_ROOM = CFG.room(CFG.default_room) or (CFG.rooms[0] if CFG.rooms else None)
# Kept as plain names because they are default arguments further down: the
# window is found by the session name, and by the folder only as a fallback for
# a window someone raised by hand without a name.
ORCH_NAME = MAIN_ROOM.env_session() if MAIN_ROOM else ""
ORCH_DIR = MAIN_ROOM.env_dir() if MAIN_ROOM else ""
SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")
ORCH_SETTLE_SEC = 1.5
# Lines the TUI draws around the conversation: tool calls, their sub-results and
# status. Speaking those out loud is what made Jarvis read log noise and URLs.
def osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def session_pids(name: str) -> list[int]:
    """Pids of live Claude sessions whose display name matches.

    Claude Code registers every session in ~/.claude/sessions/<pid>.json with the
    name given by `claude -n <name>`, so a spoken name resolves without knowing
    where the session was started.
    """
    want = name.strip().lower()
    out = []
    try:
        files = os.listdir(SESSIONS_DIR)
    except OSError:
        return out
    for fn in files:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, fn)) as f:
                info = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        got = str(info.get("name") or "").lower()
        pid = info.get("pid")
        if not pid or not got or (got != want and want not in got):
            continue
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        out.append(int(pid))
    return out


def session_alive(name: str) -> bool:
    """Is a session with this name running at all - window or not?"""
    return bool(session_pids(name))


def find_window_by_name(name: str) -> str:
    """Terminal window id hosting the session with this display name.

    Empty when the session runs somewhere AppleScript cannot see - a WebStorm
    terminal, for instance. Then it can only be reached through the orchestrator.
    """
    ttys = {}
    for pid in session_pids(name):
        tty = subprocess.run(["ps", "-p", str(pid), "-o", "tty="],
                             capture_output=True, text=True).stdout.strip()
        if tty and tty != "??":
            ttys["/dev/" + tty] = pid
    if not ttys:
        return ""
    ids = osa('tell application "Terminal" to get id of every window')
    for wid in [w.strip() for w in ids.split(",") if w.strip()]:
        if osa(f'tell application "Terminal" to get tty of tab 1 of window id {wid}') in ttys:
            return wid
    return ""


def orch_find_window(target_dir: str = ORCH_DIR) -> str:
    """Terminal window id whose shell runs claude in the given project dir."""
    ids = osa('tell application "Terminal" to get id of every window')
    for wid in [w.strip() for w in ids.split(",") if w.strip()]:
        tty = osa(f'tell application "Terminal" to get tty of tab 1 of window id {wid}')
        if not tty:
            continue
        ps = subprocess.run(["ps", "-t", tty.replace("/dev/", ""), "-o", "pid=,command="],
                            capture_output=True, text=True).stdout
        for line in ps.strip().split("\n"):
            if "claude" not in line:
                continue
            pid = line.split()[0]
            lsof = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                                  capture_output=True, text=True).stdout
            if any(l[1:] == target_dir for l in lsof.split("\n") if l.startswith("n")):
                return wid
    return ""


def press_enter(wid: str) -> None:
    """Submit what was just typed into a Claude Code window.

    The newline `do script` sends of its own lands in the input box but does not
    always send the message - the TUI treats a long line as a paste and waits for
    a real Enter (checked on Claude Code 2.1.236: the question sat unsent in the
    box). An empty `do script` is that Enter. Harmless when the text already
    went through: Enter on an empty prompt does nothing.
    """
    time.sleep(0.3)
    osa(f'tell application "Terminal" to do script "" in window id {wid}')


def orch_pane(wid: str) -> str:
    return osa(f'tell application "Terminal" to get history of tab 1 of window id {wid}')


def orch_chunks(reply: str, limit: int = NEXT_CHUNK_CHARS) -> list[str]:
    """Split a finished reply into speakable pieces."""
    chunks, buf = [], reply
    while buf:
        chunk, buf = split_speakable(buf, limit)
        if not chunk:
            break
        chunks.append(chunk)
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [reply]


def type_into(wid: str, text: str) -> None:
    """Type one line into a Claude Code window and send it."""
    # the leading space is a shield: Terminal sometimes swallows the first
    # character of `do script`, and losing it out of a question is worse
    safe = " " + text.replace("\\", "\\\\").replace('"', '\\"')
    osa(f'tell application "Terminal" to do script "{safe}" in window id {wid}')
    press_enter(wid)


def anchor_re(question: str, chars: int = 40) -> "re.Pattern[str]":
    """Match the tail of the question on screen, however it got wrapped.

    The TUI breaks a long question over several indented lines, so searching for
    the string as it was typed never finds it. Matching character by character
    with any whitespace allowed in between does.
    """
    tail = re.sub(r"\s+", "", question)[-chars:]
    return re.compile(r"\s*".join(re.escape(c) for c in tail))


def answer_from_pane(pane: str, before: str, question: str) -> str:
    """What the agent said after our question, cleaned up for the speaker.

    The question echo is the anchor, not the old snapshot: Claude Code redraws
    and trims its scrollback, so `pane` often does not start with what was there
    when we typed. Without an anchor we return nothing and keep waiting - taking
    the whole pane instead is what made the watcher read the login banner and
    announce "это оболочка, а не ответ" (лог 20.08, 09:12:17).
    """
    last = None
    for last in anchor_re(question).finditer(pane):
        pass
    if last is not None:
        return speakable(pane[last.end():])
    if pane.startswith(before):
        return speakable(pane[len(before):])
    return ""


def orch_ask(text: str, wid: str, trigger) -> tuple[str | None, str]:
    """Type the text into the window, wait for the answer to settle, read it."""
    before = orch_pane(wid)
    type_into(wid, text)
    last, stable_since = "", 0.0
    deadline = time.monotonic() + CLAUDE_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if trigger.abort.is_set():
            return None, wid
        time.sleep(0.4)
        now = orch_pane(wid)
        if now == last:
            if stable_since and time.monotonic() - stable_since >= ORCH_SETTLE_SEC:
                break
        else:
            last, stable_since = now, time.monotonic()
    return answer_from_pane(last, before, text), wid


def text_delta(raw: bytes) -> str:
    """Pull the text out of one stream-json line, ignoring everything else."""
    try:
        ev = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    if ev.get("type") != "stream_event":
        return ""
    inner = ev.get("event", {})
    if inner.get("type") != "content_block_delta":
        return ""
    delta = inner.get("delta", {})
    return delta.get("text", "") if delta.get("type") == "text_delta" else ""


def split_speakable(buf: str, min_chars: int) -> tuple[str, str]:
    """Cut off a speakable chunk ending on punctuation. Returns (chunk, rest)."""
    if len(buf) < min_chars:
        return "", buf
    cut = -1
    for i in range(len(buf) - 1, -1, -1):
        if buf[i] in ".!?…\n" and i + 1 >= min_chars:
            cut = i
            break
    if cut < 0:
        return "", buf
    return buf[:cut + 1].strip(), buf[cut + 1:]


class Speaker:
    """Talks to the long-lived tts_worker process.

    The worker keeps edge-tts warm, so the pause between sentences is about
    0.6 s instead of the 1.7-2.4 s a fresh process needed. Also remembers what
    was said, so the microphone hearing the speakers can be recognised.
    """

    EDGE_WORKER = os.path.join(JARVIS_DIR, "tts_worker.py")
    VOSK_WORKER = os.path.join(JARVIS_DIR, "vosk_worker.py")
    VOSK_PY = os.path.join(JARVIS_DIR, "venv-vosk", "bin", "python")

    def __init__(self, trigger: Trigger):
        self.trigger = trigger
        self.lock = threading.Lock()
        self.pending = 0
        self.finished_at = 0.0
        self.spoken: list[tuple[float, str]] = []
        self.idle = threading.Event()
        self.idle.set()
        self.proc: subprocess.Popen | None = None
        self._start_worker()

    # --- worker plumbing ----------------------------------------------------

    def _worker_command(self) -> list[str]:
        """Local voice by default; JARVIS_BACKEND=edge brings back the network one."""
        backend = os.environ.get("JARVIS_BACKEND", "vosk")
        if backend == "vosk" and os.access(self.VOSK_PY, os.X_OK):
            return [self.VOSK_PY, self.VOSK_WORKER]
        return ["uv", "run", "--quiet", self.EDGE_WORKER]

    def _start_worker(self) -> None:
        try:
            self.proc = subprocess.Popen(
                self._worker_command(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                start_new_session=True)
        except OSError as e:
            log(f"tts worker failed to start: {e}")
            self.proc = None
            return
        threading.Thread(target=self._read_status, daemon=True).start()

    def _read_status(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if line == "!SPEAKING":
                if TURN.get("answer_queued"):
                    turn_mark("первый звук ответа")
                else:
                    turn_mark("первый звук подтверждения")
            elif line == "!DONE":
                self._done_one()
            elif line.startswith("!ERR"):
                log(f"tts worker: {line}")
        log("tts worker exited")

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _send(self, line: str) -> bool:
        if not self._alive():
            self._start_worker()
        if not self._alive() or self.proc.stdin is None:
            return False
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    # --- public -------------------------------------------------------------

    def say(self, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        now = time.monotonic()
        with self.lock:
            self.pending += 1
            self.idle.clear()
            self.spoken.append((now, text))
            self.spoken = [(t, x) for t, x in self.spoken
                           if now - t < ECHO_MEMORY_SEC]
        if not self._send(text):
            log("tts worker unavailable, falling back to the system voice")
            self._done_one()
            spawn(["say", "-v", "Yuri", text],
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait(self) -> None:
        """Block until everything queued has been said, or an interrupt lands."""
        while not self.idle.is_set():
            if self.trigger.abort.is_set():
                return
            time.sleep(0.05)

    def settle(self) -> None:
        """Let the speakers go quiet before the mic is trusted again."""
        left = ECHO_SETTLE_SEC - (time.monotonic() - self.finished_at)
        if left > 0:
            time.sleep(left)

    def clear(self) -> None:
        self._send("!STOP")
        subprocess.run(["pkill", "-f", "ffplay -nodisp"], capture_output=True)
        # the local voice plays with afplay, one process per sentence
        subprocess.run(["pkill", "-x", "afplay"], capture_output=True)
        try:
            pathlib.Path(SPEAK_STOP).write_text(str(time.time()))
        except OSError:
            pass
        with self.lock:
            self.pending = 0
            self.finished_at = time.monotonic()
            self.idle.set()

    def is_echo(self, text: str) -> bool:
        """Did we just say this ourselves?"""
        probe = normalize(text)
        if len(probe) < 4:
            return False
        now = time.monotonic()
        with self.lock:
            recent = [x for t, x in self.spoken if now - t < ECHO_MEMORY_SEC]
        for said in recent:
            said_n = normalize(said)
            if not said_n:
                continue
            ratio = difflib.SequenceMatcher(None, probe, said_n).ratio()
            if ratio >= ECHO_SIMILARITY or (len(probe) > 6 and probe in said_n):
                log(f"echo ignored (similarity {ratio:.2f}): {text!r}")
                return True
            # a short tail of our own sentence: every word of it was just said
            words = probe.split()
            if 1 < len(words) <= 6 and all(w in said_n.split() for w in words):
                log(f"echo ignored (tail of our own line): {text!r}")
                return True
        return False

    def _done_one(self) -> None:
        with self.lock:
            self.pending = max(0, self.pending - 1)
            if self.pending == 0:
                self.finished_at = time.monotonic()
                self.idle.set()


class Orchestrator:
    """Types into the interactive orchestrator window - one Claude, one context.

    Everything said by voice appears in that window as if typed, so the whole
    dialogue stays visible there. The reply is read back off the pane, which is
    why answers cannot be streamed sentence by sentence like a private session.
    """

    def __init__(self, trigger: Trigger, speaker: Speaker,
                 work_dir: str = ORCH_DIR, label: str = "оркестратор",
                 peer_name: str = ""):
        self.trigger = trigger
        self.speaker = speaker
        self.dir = work_dir
        self.label = label
        self.name = peer_name
        self.wid = ""
        self.busy = threading.Lock()

    def _find(self) -> str:
        if self.name:
            wid = find_window_by_name(self.name)
            if wid:
                return wid
        return orch_find_window(self.dir) if self.dir else ""

    def available(self) -> bool:
        if not self.wid:
            self.wid = self._find()
        return bool(self.wid)

    def send_only(self, text: str) -> bool:
        """Hand a task over without waiting for the answer."""
        if not self.available():
            return False
        type_into(self.wid, text)
        return True

    def exchange(self, text: str, source: str = "voice") -> str | None:
        with self.busy:
            if not self.available():
                log(f"{self.label}: window not found "
                    f"(имя {self.name!r}, папка {self.dir!r})")
                return ""
            self.trigger.set_state(self.trigger.THINKING)
            t0 = time.monotonic()
            reply, wid = orch_ask(text, self.wid, self.trigger)
            self.wid = wid or self.wid
            if reply is None:
                return None
            if looks_like_shell(reply):
                # the window id went stale and we typed at a shell prompt
                log("typed into a plain shell, finding the window again")
                self.wid = self._find()
                if not self.wid:
                    return ""
                reply, wid = orch_ask(text, self.wid, self.trigger)
                self.wid = wid or self.wid
                if reply is None:
                    return None
            log(f"{self.label} ({time.monotonic() - t0:.1f}s): {reply[:200]!r}")
            if reply:
                self.trigger.set_state(self.trigger.SPEAKING)
                for chunk in orch_chunks(reply):
                    self.speaker.say(chunk)
            return reply


    def send_and_watch(self, text: str, watch: "AgentWatch") -> bool:
        """Hand the question over and let the watcher speak the answer later.

        Waiting for the answer inline blocked Jarvis for as long as the agent
        worked, and an interrupt threw the answer away: on 20.08 the rocket agent
        finished a digest into an empty room because the owner had asked the chef
        something else in the meantime.
        """
        with self.busy:
            if not self.available():
                log(f"{self.label}: window not found "
                    f"(имя {self.name!r}, папка {self.dir!r})")
                return False
            before = orch_pane(self.wid)
            type_into(self.wid, text)
            watch.add(self, self.wid, text, before)
            return True


class AgentWatch:
    """Watches every window a question was sent to, in parallel.

    One question per agent at a time is not a limit anybody set: several agents
    can be busy at once, and whoever finishes first gets spoken, named out loud
    so it is clear whose answer it is.
    """

    POLL_SEC = 1.0
    # the screen also stops changing right after the question is typed, before
    # the agent starts writing - so an answer is never accepted earlier than this
    MIN_WAIT_SEC = 4.0
    # An agent can answer in instalments. Relaying through the chef gives
    # "отправил задание, жду сводку" first and the numbers half a minute later,
    # when the peer replies - and in between the screen is genuinely idle, so
    # "stable for a moment" does not mean "done". The subscription therefore
    # stays on, speaking every new piece, until this much quiet has passed.
    QUIET_AFTER_ANSWER_SEC = 90.0
    # ignore a trickle: a redrawn footer or one changed character is not an answer
    MIN_NEW_CHARS = 15

    def __init__(self, speaker: "Speaker", trigger: Trigger):
        self.speaker = speaker
        self.trigger = trigger
        self.items: list[dict] = []
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def add(self, agent: "Orchestrator", wid: str, question: str, before: str) -> None:
        now = time.monotonic()
        with self.lock:
            self.items.append({"agent": agent, "wid": wid, "q": question,
                               "before": before, "last": before,
                               "since": now, "t0": now, "moved": False,
                               "spoken": "", "spoke_at": 0.0})
        log(f"{agent.label}: вопрос отправлен, слушаю окно {wid}")

    def waiting_for(self) -> list[str]:
        with self.lock:
            return [it["agent"].label for it in self.items]

    def _drop(self, it: dict) -> None:
        with self.lock:
            if it in self.items:
                self.items.remove(it)

    def _loop(self) -> None:
        while True:
            time.sleep(self.POLL_SEC)
            with self.lock:
                items = list(self.items)
            for it in items:
                try:
                    self._tick(it)
                except Exception as e:  # a closed window must not kill the thread
                    log(f"watch error ({it['agent'].label}): {e}")
                    self._drop(it)

    def _tick(self, it: dict) -> None:
        pane = orch_pane(it["wid"])
        waited = time.monotonic() - it["t0"]
        if not pane:  # the window was closed
            log(f"{it['agent'].label}: окно закрылось, ответа не будет")
            self._drop(it)
            return
        if pane != it["last"]:
            it["last"], it["since"], it["moved"] = pane, time.monotonic(), True
            return
        if it["spoken"]:
            # already said something: wait a while for a follow-up, then let go
            if time.monotonic() - it["spoke_at"] > self.QUIET_AFTER_ANSWER_SEC:
                log(f"{it['agent'].label}: тишина после ответа, снимаю с прослушки")
                self._drop(it)
                return
        elif waited > CLAUDE_TIMEOUT_SEC:
            log(f"{it['agent'].label}: молчит {waited:.0f}s, перестаю ждать")
            self._drop(it)
            self.speaker.say(f"{it['agent'].label} так и не ответил.")
            return
        if not it["moved"] or waited < self.MIN_WAIT_SEC:
            return
        if time.monotonic() - it["since"] < ORCH_SETTLE_SEC:
            return
        answer = answer_from_pane(pane, it["before"], it["q"])
        if not answer:
            return  # the screen just paused, he is still working
        if looks_like_shell(answer):
            log(f"{it['agent'].label}: это оболочка, а не ответ")
            self._drop(it)
            it["agent"].wid = ""
            return
        # only the part that was not spoken yet: an answer grows in instalments
        fresh = (answer[len(it["spoken"]):] if answer.startswith(it["spoken"])
                 else answer)
        if len(fresh.strip()) < self.MIN_NEW_CHARS:
            return
        it["spoken"], it["spoke_at"] = answer, time.monotonic()
        log(f"{it['agent'].label} ответил через {waited:.0f}s: {fresh[:150]!r}")
        self.trigger.set_state(self.trigger.SPEAKING)
        self.speaker.say(f"{it['agent'].label} отвечает.")
        for chunk in orch_chunks(fresh.strip()):
            self.speaker.say(chunk)


class LiveSession:
    """One long-lived `claude` process for the whole conversation.

    Restarting claude per question cost about 2 s of startup and lost context.
    Here the process stays up: questions go in as JSON lines, text comes back as
    deltas, and sentences are handed to the speaker as soon as they are complete.
    Both the microphone and the keyboard feed into this one session.
    """

    def __init__(self, trigger: Trigger, speaker: Speaker):
        self.trigger = trigger
        self.speaker = speaker
        self.proc: subprocess.Popen | None = None
        self.events: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.busy = threading.Lock()
        self.sid = str(uuid.uuid4())
        self.start()

    # --- process ------------------------------------------------------------

    def start(self) -> None:
        cmd = ["claude", "-p",
               "--input-format", "stream-json",
               "--output-format", "stream-json",
               "--include-partial-messages", "--verbose",
               "--session-id", self.sid,
               "--setting-sources", "project",
               # headless mode cannot ask for permission, so a tool that is not
               # listed here is simply refused - that is why the voice Jarvis had
               # no web search at all
               "--allowedTools", *VOICE_TOOLS,
               "--append-system-prompt", SYSTEM_PROMPT]
        if CLAUDE_MODEL:
            cmd += ["--model", CLAUDE_MODEL]
        if STRICT_MCP:
            cmd += ["--strict-mcp-config"]
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                start_new_session=True,
                cwd=SESSION_DIR)
        except OSError as e:
            log(f"claude session failed to start: {e}")
            self.proc = None
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def restart(self) -> None:
        """Forget everything: new session id, fresh process."""
        if self.proc is not None:
            kill_proc(self.proc)
        self.sid = str(uuid.uuid4())
        self._drain()
        self.start()

    def _read(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            kind = ev.get("type")
            if kind == "stream_event":
                inner = ev.get("event", {})
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        self.events.put(("delta", delta.get("text", "")))
            elif kind == "result" or "duration_api_ms" in ev:
                self.events.put(("end", ""))
        log("claude session exited")
        self.events.put(("dead", ""))

    def _drain(self) -> None:
        while not self.events.empty():
            try:
                self.events.get_nowait()
            except queue.Empty:
                break

    # --- one exchange -------------------------------------------------------

    def exchange(self, text: str, source: str = "voice") -> str | None:
        """Ask, speak the answer as it arrives. None means interrupted."""
        with self.busy:
            if not self._alive():
                log("claude session is down, starting a new one")
                self.sid = str(uuid.uuid4())
                self.start()
                if not self._alive():
                    self.speaker.say("Сессия Клода не поднялась, посмотри лог.")
                    return ""
            self._drain()
            msg = {"type": "user",
                   "message": {"role": "user",
                               "content": [{"type": "text", "text": text}]}}
            self.trigger.set_state(self.trigger.THINKING)
            t0 = time.monotonic()
            try:
                self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                log(f"claude session write failed: {e}")
                return ""

            parts, buf, spoken = [], "", False
            deadline = time.monotonic() + CLAUDE_TIMEOUT_SEC
            while True:
                if self.trigger.abort.is_set():
                    # the process keeps its context; we just stop listening to it
                    return None
                try:
                    kind, payload = self.events.get(timeout=0.1)
                except queue.Empty:
                    if time.monotonic() > deadline:
                        log("claude timed out")
                        return ""
                    continue
                if kind == "dead":
                    return ""
                if kind == "end":
                    break
                parts.append(payload)
                buf += payload
                limit = FIRST_CHUNK_CHARS if not spoken else NEXT_CHUNK_CHARS
                chunk, buf = split_speakable(buf, limit)
                if chunk:
                    if not spoken:
                        TURN["answer_queued"] = True
                        turn_mark("клод ответил первым куском")
                        log(f"first chunk after {time.monotonic() - t0:.1f}s "
                            f"({source}): {chunk!r}")
                        spoken = True
                    self.trigger.set_state(self.trigger.SPEAKING)
                    self.speaker.say(chunk)
            if buf.strip():
                self.trigger.set_state(self.trigger.SPEAKING)
                self.speaker.say(buf.strip())
            reply = "".join(parts).strip()
            log(f"claude ({time.monotonic() - t0:.1f}s): {reply[:200]!r}")
            return reply


def capture(audio_q, noise_floor, trigger, keep_head: bool,
            wait_sec: float, speaker=None,
            max_sec: float = MAX_UTTERANCE_SEC,
            verify: bool = True,
            refuse_aloud: bool = True) -> str | None:
    """Record one utterance and return its transcript ("" if unintelligible).

    verify=False is for takes the key started - that is the owner's own hand on the
    keyboard, and his voice does not have to prove it again.

    refuse_aloud=False still checks the voice and still drops a stranger, only
    without saying so - for the follow-up window, which nobody asked for.
    """
    audio = record(audio_q, noise_floor, trigger, keep_head, wait_sec, max_sec)
    if audio is None:
        return None
    turn_mark("запись закончена")
    # Two takes are not worth judging a voice on, and both fail open.
    #
    # Ended by the key: that is his own hand on the "done talking" button, the
    # same evidence that makes a key-started take skip the check.
    #
    # Music still playing inside it: the print is then made of him and a song at
    # once and matches nobody. On 24.08 at 14:27 such a take scored 0.23 on him
    # and 0.23 on strangers - a coin toss, and it refused him to his face.
    if verify:
        if _last_end_reason == "key":
            trace("сверка голоса пропущена: запись оборвана клавишей, это его рука")
            verify = False
        elif _music_in_take:
            trace("сверка голоса пропущена: в записи ещё играла музыка")
            verify = False
    if verify and not voice_is_his(audio, speaker, aloud=refuse_aloud):
        return None
    secs = len(audio) / SAMPLE_RATE
    trigger.set_state(trigger.THINKING)
    if secs > 2.5 and speaker is not None:
        speaker.say(random.choice(ACKS))
    t0 = time.monotonic()
    text = transcribe(audio, trigger)
    if text is None:
        return None
    turn_mark("фраза распознана")
    log(f"heard ({secs:.1f}s audio, {time.monotonic() - t0:.1f}s ASR): {text!r}")
    if speaker is not None and speaker.is_echo(text):
        return None
    if text and not CYRILLIC.search(text):
        log(f"not Russian, treating as noise: {text!r}")
        return None
    return text


def hear_after_wake_only(audio_q, noise_floor: float, trigger: Trigger, engine,
                         speaker=None) -> str | None:
    """He said just "Джарвис": prompt, then record the command itself.

    Shared by both modes on purpose - the assistant and the listener must behave
    the same, so a fix here lands in both. The only difference is the prompt:
    Jarvis says "Слушаю", the listener only clicks, because in listen-only mode
    the answering voice belongs to the agent.
    """
    if speaker is not None:
        speaker.say("Слушаю.")
        speaker.wait()
        speaker.settle()
    else:
        chime()
    flush(audio_q)
    text = capture(audio_q, noise_floor, trigger, speaker=speaker,
                   keep_head=False, wait_sec=WAIT_SPEECH_SEC)
    engine.reset()
    return text


# --- main loop ---------------------------------------------------------------

# --- listen-only mode: the ears of an agent session -------------------------
# `/assist` inside any Claude session arms this through the Monitor tool: the
# session keeps its context and tools, and every heard phrase arrives there as an
# event. Nothing here talks to Claude or to a speaker - the agent answers itself
# and speaks with the voice-answer skill.
SPEAK_LOCK = os.path.expanduser("~/.claude/tts-cache/.speak.lock")
# During a call the wake word must not work at all. Anyone on the other side can
# say "Джарвис" and start a recording in the owner's room - and whatever they say next
# goes to an agent with his tools. The existing call_guard.sh answers a different
# question, "may I speak out loud", and lets a call through on headphones; this one
# is about hearing, and headphones do not help - the voice comes out of them into
# the same room microphone.
#
# The check costs a subprocess, so its answer is cached: a call does not start and
# end between two frames.
# Handing a task to the orchestrator - the "chief" - is the heaviest thing the
# voice can do: that session runs on Opus with full permissions, so a sentence
# from this room ends up as an agent that may edit files and push code. The wake
# word cannot authorise that, because anyone in the room can say it and because
# recognition mishears. A key press can: it is the owner's own hand on their own
# keyboard, M5 or right Option.
#
# So forwarding is allowed only when THIS exchange started with a key. Asked by
# voice, Jarvis answers himself and says which key to press.
#
# Only for the standalone daemon. In --listen mode the agent session decides for
# itself what to do with the phrase, and gating it here would be theatre.
FORWARD_NEEDS_KEY = os.environ.get("JARVIS_FORWARD_NEEDS_KEY", "1") == "1"
CALL_MUTE = os.environ.get("JARVIS_CALL_MUTE", "1") == "1"
CALL_CHECK_EVERY = float(os.environ.get("JARVIS_CALL_CHECK_EVERY", "5"))
_call_state = {"at": 0.0, "in_call": False}


def in_call() -> bool:
    """Is a call running right now? Cached for CALL_CHECK_EVERY seconds."""
    if not CALL_MUTE:
        return False
    now = time.monotonic()
    if now - _call_state["at"] < CALL_CHECK_EVERY:
        return _call_state["in_call"]
    _call_state["at"] = now
    busy = False
    try:
        urls = subprocess.run(
            ["osascript", "-e",
             'tell application "Google Chrome" to get URL of tabs of windows'],
            capture_output=True, text=True, timeout=3).stdout
        busy = bool(re.search(r"meet\.google\.com/[a-z]{3}-", urls))
        if not busy:
            busy = subprocess.run(["/usr/bin/pgrep", "-x", "zoom.us"],
                                  capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        busy = _call_state["in_call"]      # не смогли проверить - оставляем как было
    if busy != _call_state["in_call"]:
        log("созвон начался - слово «Джарвис» не слушаю" if busy
            else "созвон кончился - слово «Джарвис» снова работает")
    _call_state["in_call"] = busy
    return busy


LISTENER_PID = os.path.join(JARVIS_DIR, "listener.pid")
LISTENER_OWNER = os.path.join(JARVIS_DIR, "listener.owner")
# The badge used to ask `pgrep -f jarvis_daemon.py` whether Jarvis is alive, and
# that matches any command line merely mentioning the file - including the check
# itself and the /assist-off kill loop. The badge then flashed a stale "слушаю"
# for one tick. Pid files answer the same question without guessing.
DAEMON_PID = os.path.join(JARVIS_DIR, "daemon.pid")
# The badge and the menu bar icon read the state file, so listen-only mode keeps
# it honest as well: "думаю" from the moment a phrase is handed to the agent,
# "говорю" while the voice-answer skill holds its lock, "жду" the rest of the
# time. Without this the badge only ever showed "жду" and "слушаю".
#
# 40 s, а не 300: снимать «думаю» умеет либо начало озвучки, либо answered.sh,
# и агент про второе забывает - 22.08 плашка врала трижды за день, каждый раз
# когда ответ был текстом без голоса. Пять минут делали из этого пять минут лжи.
# Сорок секунд - примерно вдвое дольше типичного ответа, так что честное «думаю»
# они не обрежут, а забытое погаснет быстро. При долгой работе агент всё равно
# обязан сказать голосом, что взялся, - там плашка не главный сигнал.
THINK_MAX_SEC = float(os.environ.get("JARVIS_THINK_MAX", "40"))


SPEAK_STOP = os.path.join(JARVIS_DIR, "speak.stop")
# The agent touches this when an answer is finished. Without it "думаю" could only
# be cleared by the start of speech, so a text-only answer left the badge stuck
# until the five-minute timeout.
ANSWER_DONE = os.path.join(JARVIS_DIR, "answer.done")


def answered_at() -> float:
    try:
        return os.path.getmtime(ANSWER_DONE)
    except OSError:
        return 0.0


LISTEN_ONLY = False   # true in --listen mode: the agent speaks, we must not record it


def stop_playback() -> None:
    """Escape and the double tap shut the agent up, exactly as they shut Jarvis up.

    Killing the player is not enough any more: the local voice says a long answer
    chunk by chunk, one afplay per sentence, so killing one only skipped to the
    next sentence and Jarvis kept talking into his own microphone. The stop file
    is the second half - every speaker checks it between chunks and gives up.
    """
    try:
        pathlib.Path(SPEAK_STOP).write_text(str(time.time()))
    except OSError:
        pass
    # -x, not -f: a full-line match also hits any shell whose command mentions
    # the word "afplay", and that shell may be the one asking for the stop
    subprocess.run(["pkill", "-x", "afplay"], capture_output=True)
    subprocess.run(["pkill", "-x", "ffplay"], capture_output=True)
    try:
        os.rmdir(SPEAK_LOCK)
    except OSError:
        pass


def owner_session() -> str:
    """Name of the Claude session that armed this listener.

    Walks up the parents: Monitor started us from that session, so its `claude`
    process is one of our ancestors, and the registry knows its display name.
    Needed because the microphone is single and the owner has to see which agent
    holds it - the badge shows this name.
    """
    pid = os.getpid()
    for _ in range(12):
        out = subprocess.run(["ps", "-p", str(pid), "-o", "ppid="],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            return ""
        pid = int(out)
        if pid <= 1:
            return ""
        path = os.path.join(SESSIONS_DIR, f"{pid}.json")
        try:
            with open(path) as f:
                return str(json.load(f).get("name") or "")
        except (OSError, json.JSONDecodeError):
            continue
    return ""


def listener_busy() -> int:
    """Pid of a listener already holding the microphone, or 0.

    The pid file alone was not enough: `take-mic.sh` and a crashed listener can
    leave it missing while a live listener keeps recording, and then a second one
    started and the two fought over the input. So a live process counts too.
    """
    try:
        pid = int(pathlib.Path(LISTENER_PID).read_text().strip())
        if pid != os.getpid():
            try:
                os.kill(pid, 0)
                return pid
            except PermissionError:
                return pid
            except ProcessLookupError:
                pass
    except (OSError, ValueError):
        pass
    # own pid and the whole parent chain are not "somebody else"
    mine, cur = {os.getpid()}, os.getpid()
    for _ in range(12):
        out = subprocess.run(["ps", "-p", str(cur), "-o", "ppid="],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            break
        cur = int(out)
        if cur <= 1:
            break
        mine.add(cur)
    found = subprocess.run(["pgrep", "-f", "jarvis_daemon.py --listen"],
                           capture_output=True, text=True).stdout.split()
    for other in found:
        if int(other) not in mine:
            return int(other)
    return 0


def listen_only_main() -> None:
    """Print every heard phrase as one line, one event per phrase."""
    global LOG_STREAM, LISTEN_ONLY
    LISTEN_ONLY = True
    LOG_STREAM = sys.stderr
    busy = listener_busy()
    if busy:
        print(f"BUSY: слушает уже другой процесс, pid {busy}", flush=True)
        return
    apply_keymap()
    warm_voiceprint()
    engine = make_engine()
    trigger = Trigger()
    trigger.install()
    trigger.asleep = False
    owner = owner_session()
    try:
        pathlib.Path(LISTENER_PID).write_text(str(os.getpid()))
        pathlib.Path(LISTENER_OWNER).write_text(owner)
    except OSError:
        pass
    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    noise_floor = 100.0
    print(f"LISTENING: слушаю «Джарвис»"
          f"{f' для сессии «{owner}»' if owner else ''} ({engine.name})", flush=True)
    try:
        with Mic(engine, audio_q).open_ctx():
            pending = 0.0   # когда фраза ушла агенту и он над ней работает
            pending_wall = 0.0  # то же время по стенным часам, для файла-сигнала
            listen_now = 0.0  # >0: слушаем без слова-будильника столько секунд
            spoke_until = 0.0  # до этого момента слышно эхо своего же ответа
            while True:
                if locked_pause(trigger, audio_q, engine):
                    pending = pending_wall = listen_now = 0.0
                    continue
                # asleep, interrupts and the follow-up window behave exactly as in
                # the assistant loop - same keys, same waits, same constants
                if trigger.asleep:
                    if trigger.state != trigger.ASLEEP:
                        trigger.set_state(trigger.ASLEEP)
                        log("asleep - press M5 to activate")
                    pending = listen_now = 0.0
                    next_frame(audio_q)
                    if trigger.start.is_set():
                        trigger.start.clear()
                        trigger.asleep = False
                    continue
                if trigger.abort.is_set() or trigger.cancel.is_set():
                    cancelled = trigger.cancel.is_set()
                    stop_playback()
                    trigger.abort.clear()
                    trigger.cancel.clear()
                    trigger.stop.clear()
                    pending = 0.0
                    listen_now = 0.0 if cancelled else INTERRUPT_WAIT_SEC
                    log("interrupted: " + ("cancelled" if cancelled
                                           else "listening for the new one"))
                    flush(audio_q)
                    engine.reset()
                    continue
                # the agent is speaking through the voice-answer skill: its lock
                # is our cue to stop listening, or we would record his own voice
                if os.path.exists(SPEAK_LOCK):
                    if trigger.state != trigger.SPEAKING:
                        trigger.set_state(trigger.SPEAKING)
                        trace("агент заговорил - замок озвучки на месте, слушать нельзя")
                    pending = 0.0
                    listen_now = FOLLOWUP_SEC  # он договорит - и мы слушаем дальше
                    spoke_until = time.monotonic() + ECHO_SETTLE_SEC
                    next_frame(audio_q)
                    continue
                if spoke_until:
                    if time.monotonic() < spoke_until:
                        # the lock is gone but the speakers are still draining:
                        # without this the follow-up window recorded the tail of
                        # his own answer and it came back as a question
                        next_frame(audio_q)
                        continue
                    spoke_until = 0.0
                    flush(audio_q)      # whatever the room recorded while he spoke
                    engine.reset()
                    trace("агент договорил, эхо осело - дальше слушаю без слова-будильника")
                if listen_now:
                    wait_sec, listen_now = listen_now, 0.0
                    trace(f"дослушивание: жду ответ {wait_sec:.1f}с, слово-будильник не нужно")
                    flush(audio_q)
                    chime()
                    text = capture(audio_q, noise_floor, trigger,
                                   keep_head=False, wait_sec=wait_sec,
                                   max_sec=MAX_FOLLOWUP_SEC,
                                   refuse_aloud=False)
                    engine.reset()
                    command = strip_wake(text or "")
                    trace(f"после дослушивания распознано: {text!r}")
                    if command and normalize(command) not in STOP_WORDS:
                        print(f"HEARD: {command}", flush=True)
                        pending = time.monotonic()
                        pending_wall = time.time()
                    continue
                if pending and answered_at() > pending_wall:
                    pending = 0.0  # агент сказал, что ответил - «думаю» больше не про него
                if pending and time.monotonic() - pending > THINK_MAX_SEC:
                    pending = 0.0  # молчит слишком долго, снимаем «думаю»
                want = trigger.THINKING if pending else trigger.IDLE
                if trigger.state != want:
                    trigger.set_state(want)
                frame = next_frame(audio_q)
                level = rms(frame)
                if level < noise_floor * 2.0:
                    noise_floor = 0.95 * noise_floor + 0.05 * level
                hot = trigger.start.is_set()
                if not hot and not engine.detect(frame):
                    continue
                # the key still works during a call - it is the owner's own hand;
                # the wake word does not, anyone in the meeting could say it
                if not hot and in_call():
                    engine.reset()
                    continue
                trigger.start.clear()
                trigger.mark_wake_source(hot)
                log("hotkey!" if hot else "wake!")
                trace("ПРОБУЖДЕНИЕ: " + ("клавиша" if hot else "услышал слово «Джарвис»"))
                turn_start()
                chime()
                text = capture(audio_q, noise_floor, trigger,
                               keep_head=not hot, wait_sec=WAIT_SPEECH_SEC,
                               verify=not hot)
                engine.reset()
                flush(audio_q)
                if not text:
                    continue
                command = strip_wake(text)
                if not command:
                    # only the wake word - dolisten for the question itself
                    text = hear_after_wake_only(audio_q, noise_floor, trigger,
                                                engine)
                    command = strip_wake(text or "")
                if not command or normalize(command) in STOP_WORDS:
                    continue
                print(f"HEARD: {command}", flush=True)
                pending = time.monotonic()
                pending_wall = time.time()
    finally:
        try:
            if pathlib.Path(LISTENER_PID).read_text().strip() == str(os.getpid()):
                pathlib.Path(LISTENER_PID).unlink()
                pathlib.Path(LISTENER_OWNER).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def main() -> None:
    try:
        pathlib.Path(DAEMON_PID).write_text(str(os.getpid()))
    except OSError:
        pass
    apply_keymap()
    warm_voiceprint()
    engine = make_engine()
    trigger = Trigger()
    trigger.install()
    speaker = Speaker(trigger)
    # One typist per room. Nothing here knows what any of them is for - that is
    # in config/rooms.toml, and adding a room adds an entry to this dict.
    rooms = {r.id: Orchestrator(trigger, speaker, r.env_dir(), r.label,
                                r.env_session())
             for r in CFG.rooms}
    orch = rooms.get(MAIN_ROOM.id) if MAIN_ROOM else None

    def run_action(action, phrase: str) -> str | None:
        """Run one action. Returns text for Jarvis to retell, or None when done.

        None means the action has already had its say - either it spoke its own
        output, or it was told to stay silent. Only speak = "retell" comes back
        with something, because only that needs his voice on top.
        """
        out = plugins.run_action(action, phrase, JARVIS_DIR_PATH, log)
        if out is None or (action.speak == "retell" and not out):
            speaker.say(action.fail_say or "Не получилось.")
            return None
        if action.speak == "retell":
            return plugins.fill(action.prompt, q=phrase, facts=out)
        if action.speak == "stdout":
            speaker.say(plugins.format_output(action, out, action.argv(phrase),
                                              JARVIS_DIR_PATH) or action.ok_say)
        return None

    def room_send(rid: str, question: str) -> bool:
        """Ask room `rid`, going through its relay and its fallback if needed.

        Returns False only when none of the three could be reached; whatever
        Jarvis says about it is the room's own line from the config.
        """
        room, dest = CFG.room(rid), rooms.get(rid)
        if room is None or dest is None:
            return False
        if dest.send_and_watch(VOICE_ASK.format(q=question), watch):
            speaker.say(plugins.fill(room.ack_ask or "Спросил {label}.",
                                     label=room.label))
            return True
        # the session is up but has no window of its own - started inside
        # another Claude session, or in an IDE pane - so a room that can reach
        # it by cross-session message is asked to relay
        relay = rooms.get(room.relay_via)
        if (relay is not None and room.env_session()
                and session_alive(room.env_session())
                and relay.send_and_watch(
                    RELAY_ASK.format(peer=room.env_session(), q=question), watch)):
            speaker.say(plugins.fill(
                room.ack_relay or "{label} не в окне терминала, спрошу через {relay}.",
                label=room.label, relay=CFG.room(room.relay_via).label))
            return True
        spare = rooms.get(room.fallback)
        if spare is not None and spare.send_and_watch(
                VOICE_ASK.format(q=question), watch):
            speaker.say(plugins.fill(
                room.ack_fallback or "{label} не нашёл, спросил {fallback}.",
                label=room.label, fallback=CFG.room(room.fallback).label))
            return True
        speaker.say(plugins.fill(room.ack_missing or "Окно {label} не найдено.",
                                 label=room.label,
                                 fallback=(CFG.room(room.fallback).label
                                           if CFG.room(room.fallback) else "")))
        return False
    watch = AgentWatch(speaker, trigger)
    # Jarvis answers himself; the orchestrator is reached by asking for it
    own_only = os.environ.get("JARVIS_TARGET", "own") != "orch"
    private: LiveSession | None = None
    last_target = [""]

    def target():
        """Prefer the orchestrator window; fall back to a private session.

        Checked per question: the window usually opens a moment after the daemon.
        """
        nonlocal private
        if not own_only and orch is not None and orch.available():
            if last_target[0] != "orch":
                last_target[0] = "orch"
                log(f"talking to the orchestrator window {orch.wid} - "
                    "the dialogue stays visible there")
            return orch
        if private is None:
            private = LiveSession(trigger, speaker)
        if last_target[0] != "own":
            last_target[0] = "own"
            log("talking to a private session (no orchestrator window)")
        return private
    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    def typing_loop() -> None:
        """Anything typed in this terminal goes to the same conversation."""
        while True:
            try:
                line = sys.stdin.readline()
            except (ValueError, OSError):
                return
            if not line:
                return
            text = line.strip()
            if not text:
                continue
            log(f"typed: {text!r}")
            # typed questions follow the same routes as spoken ones, otherwise
            # a question typed in this window would land in the wrong session.
            # No key gate here on purpose: typing into the daemon's own window is
            # already the owner's hands on their own keyboard - the same proof a key is.
            kind, room_id, task, phrase = split_forward(text)
            auto = "" if (kind and " " in phrase) else route_auto(normalize(text))
            action = CFG.action(auto)
            dest = rooms.get(room_id)
            if action is not None:
                retell = run_action(action, text)
                reply = (target().exchange(retell, source="typed")
                         if retell is not None else "")
            elif auto in rooms and rooms[auto].available():
                reply = rooms[auto].exchange(VOICE_ASK.format(q=text), source="typed")
            elif kind == "tell" and dest is not None and dest.available():
                dest.send_only(task)
                reply = plugins.fill(CFG.room(room_id).ack_tell or "Передал {label}.",
                                     label=CFG.room(room_id).label)
            elif kind and dest is not None and dest.available():
                reply = dest.exchange(task, source="typed")
            else:
                reply = target().exchange(text, source="typed")
            if reply:
                print(f"\n{reply}\n", flush=True)
            speaker.wait()
            speaker.settle()

    threading.Thread(target=typing_loop, daemon=True).start()

    noise_floor = 100.0
    log(f"Jarvis daemon up. Engine: {engine.name}, model: {CLAUDE_MODEL or 'default'}."
        " Type here to talk to the same session. Ctrl+C to stop.")
    if not trigger.asleep:
        chime()  # a click, not a greeting: he is up and listening

    def interrupted(where: str) -> bool:
        """User cut Jarvis off. Returns True if he wants to say something now."""
        listen = not trigger.cancel.is_set()
        log(f"interrupted while {where}: "
            f"{'listening for the new one' if listen else 'cancelled'}")
        speaker.clear()
        kill_children()
        trigger.abort.clear()
        trigger.cancel.clear()
        trigger.stop.clear()
        flush(audio_q)
        engine.reset()
        return listen

    with Mic(engine, audio_q).open_ctx():
        follow_up = False
        follow_up_wait = FOLLOWUP_SEC
        while True:
            if locked_pause(trigger, audio_q, engine):
                follow_up = False
                continue
            trigger.abort.clear()
            trigger.cancel.clear()
            if trigger.asleep:
                if trigger.state != trigger.ASLEEP:
                    trigger.set_state(trigger.ASLEEP)
                    speaker.clear()
                    log("asleep - press M5 to activate")
                follow_up = False
                next_frame(audio_q)  # keep the stream flowing, throw the audio away
                if trigger.start.is_set():
                    trigger.start.clear()
                    trigger.asleep = False
                continue
            trigger.set_state(trigger.IDLE)
            if follow_up:
                # listening for a continuation, no wake word needed
                speaker.settle()
                flush(audio_q)
                chime()
                text = capture(audio_q, noise_floor, trigger, speaker=speaker,
                               keep_head=False, wait_sec=follow_up_wait,
                               refuse_aloud=False)
                follow_up = False
                follow_up_wait = FOLLOWUP_SEC
                if trigger.abort.is_set():
                    follow_up = interrupted("recording")
                    follow_up_wait = INTERRUPT_WAIT_SEC
                    continue
                if text is None:
                    log("no follow-up, back to wake word")
                    engine.reset()
                    flush(audio_q)
                    continue
            else:
                frame = next_frame(audio_q)
                level = rms(frame)
                # adapt to background noise only, never to speech bursts
                if level < noise_floor * 2.0:
                    noise_floor = 0.95 * noise_floor + 0.05 * level
                hot = trigger.start.is_set()
                if not hot and not engine.detect(frame):
                    continue
                # the key still works during a call - it is the owner's own hand;
                # the wake word does not, anyone in the meeting could say it
                if not hot and in_call():
                    engine.reset()
                    continue
                trigger.start.clear()
                trigger.mark_wake_source(hot)
                log("hotkey!" if hot else "wake!")
                turn_start()
                chime()
                text = capture(audio_q, noise_floor, trigger, speaker=speaker,
                               keep_head=not hot, wait_sec=WAIT_SPEECH_SEC,
                               verify=not hot)
                engine.reset()
                if trigger.abort.is_set():
                    follow_up = interrupted("recording")
                    follow_up_wait = INTERRUPT_WAIT_SEC
                    continue
                if text is None:
                    log("no speech, ignoring")
                    flush(audio_q)
                    continue

            command = strip_wake(text)
            if not command:
                text = hear_after_wake_only(audio_q, noise_floor, trigger, engine,
                                            speaker)
                if text is None:
                    log("no command after 'Слушаю', ignoring")
                    flush(audio_q)
                    continue
                command = strip_wake(text)

            norm = normalize(command)
            if not command:
                speaker.say("Не расслышал, повтори.")
                speaker.wait()
                speaker.settle()
                flush(audio_q)
                continue
            if norm in STOP_WORDS:
                speaker.say("Отбой.")
                speaker.wait()
                speaker.settle()
                flush(audio_q)
                continue
            if norm in RESET_WORDS:
                if target() is private and private is not None:
                    private.restart()
                else:
                    speaker.say("Это окно оркестратора, начни заново командой слэш клеар.")
                speaker.say("Забыл. Слушаю с чистого листа.")
                speaker.wait()
                speaker.settle()
                flush(audio_q)
                follow_up = True
                continue

            log(f"command: {command!r}")
            kind, room_id, task, phrase = split_forward(command)
            # only the daemon gates this: in --listen mode the agent session
            # decides for itself, and it already knows how it was woken
            auto_peek = "" if (kind and " " in phrase) else route_auto(norm)
            # Everything that leaves this process for another agent goes through
            # the same gate, an automatic route included: a room can be a session
            # that posts messages other people then read.
            reaches_agents = bool(kind) or auto_peek in rooms
            if (reaches_agents and FORWARD_NEEDS_KEY and not LISTEN_ONLY
                    and not trigger.woke_by_key()):
                log("передача другому агенту отклонена - разбудили словом, "
                    f"не клавишей: {command!r}")
                speaker.say("Другим агентам передаю только с клавиши. "
                            "Нажми эм пять и повтори.")
                trigger.set_state(trigger.IDLE)
                continue
            # an order ("передай шефу ...") always wins; a bare name ("шеф ...")
            # gives way to the automatic routes below
            explicit = bool(kind) and " " in phrase
            auto = "" if explicit else route_auto(norm)
            action = CFG.action(auto)
            if action is not None:
                log(f"action {action.id}: {command!r}")
                retell = run_action(action, command)
                if retell is None:
                    speaker.wait()
                    speaker.settle()
                    flush(audio_q)
                    engine.reset()
                    follow_up = FOLLOWUP_SEC > 0
                    continue
                reply = target().exchange(retell)
            elif auto in rooms:
                log(f"routed to room {auto!r}: {command!r}")
                room_send(auto, command)
                speaker.wait()
                speaker.settle()
                flush(audio_q)
                engine.reset()
                follow_up = FOLLOWUP_SEC > 0
                continue
            elif kind:
                room, dest = CFG.room(room_id), rooms.get(room_id)
                if dest is None:
                    speaker.say("Такой комнаты нет.")
                elif kind == "tell":
                    log(f"handed to room {room_id!r}: {task!r}")
                    speaker.say(plugins.fill(
                        (room.ack_tell or "Передал {label}.") if dest.send_only(task)
                        else (room.ack_missing or "Окно {label} не найдено."),
                        label=room.label))
                else:
                    log(f"asked room {room_id!r}: {task!r}")
                    room_send(room_id, task)
                speaker.wait()
                speaker.settle()
                flush(audio_q)
                engine.reset()
                follow_up = FOLLOWUP_SEC > 0
                continue
            else:
                reply = target().exchange(command)
            if reply is None:  # interrupted while thinking or speaking
                speaker.clear()
                follow_up = interrupted("the answer")
                follow_up_wait = INTERRUPT_WAIT_SEC
                continue
            if not reply:
                speaker.say("Клод не ответил, посмотри лог демона.")
            speaker.wait()      # never open the mic while he is still talking
            speaker.settle()    # speakers still ringing, do not record that
            flush(audio_q)
            if trigger.abort.is_set():  # interrupted while speaking
                speaker.clear()
                follow_up = interrupted("speaking")
                follow_up_wait = INTERRUPT_WAIT_SEC
                continue

            engine.reset()
            follow_up = FOLLOWUP_SEC > 0 and bool(reply)


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        eng = make_engine()
        silence = np.zeros(eng.frame_len, dtype=np.int16)
        print(f"selfcheck ok, engine: {eng.name}, detect(silence)={eng.detect(silence)}")
        print("strip_wake:", repr(strip_wake("Джарвис, сколько будет дважды два?")))
        # The routing examples live next to the room or action they belong to,
        # in config/rooms.toml and config/actions.toml. Add a room, add its
        # examples, and this check covers it without being edited.
        cases = [(ph, want) for src in (*CFG.rooms, *CFG.actions)
                 for ph, want in src.examples]
        bad = [(q, want, route_auto(normalize(q))) for q, want in cases
               if route_auto(normalize(q)) != want]
        print(f"routing: {len(cases) - len(bad)}/{len(cases)} ok"
              + ("" if not bad else f", промахи: {bad}"))
        # an order must outrank the automatic routes
        for probe in (f"{w} проверка связи"
                      for r in CFG.rooms for w in (r.tell[:1] or r.bare[:1])):
            k, rid, task, phrase = split_forward(probe)
            print(f"forward: {probe!r} -> kind={k!r} room={rid!r} "
                  f"explicit={' ' in phrase} task={task!r}")
        for room in CFG.rooms:
            name, folder = room.env_session(), room.env_dir()
            wid = (find_window_by_name(name) if name else "") or (
                orch_find_window(folder) if folder else "")
            print(f"комната «{room.id}» (сессия {name or '-'}, папка {folder or '-'}): "
                  f"{wid or 'окно не найдено'}")
            for pid in (session_pids(name) if name else []):
                print(f"  сессия «{name}»: pid {pid}")
        for action in CFG.actions:
            print(f"действие «{action.id}»: {action.speak}, {action.run or '-'}")
        print(f"silence={SILENCE_SEC}s followup={FOLLOWUP_SEC}s "
              f"double_tap={DOUBLE_TAP_SEC}s "
              f"tap={os.environ.get('JARVIS_TAP_KEYS', DEFAULT_TAP_KEYS)} "
              f"done={os.environ.get('JARVIS_DONE_KEYS', DEFAULT_DONE_KEYS)} "
              f"off={os.environ.get('JARVIS_OFF_KEYS', DEFAULT_OFF_KEYS)}")
        sys.exit(0)
    # SIGTERM without a handler skips every finally block, so a killed listener
    # left the state file on "слушаю" and the badge kept showing it
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        listen_only_main() if "--listen" in sys.argv else main()
    except (KeyboardInterrupt, SystemExit):
        log("stopped")
    finally:
        kill_children()
        for worker in ("tts_worker.py", "vosk_worker.py"):
            subprocess.run(["pkill", "-f", worker], capture_output=True)
        subprocess.run(["pkill", "-f", "asr_worker.py"], capture_output=True)
        # Только своё: выход по BUSY (микрофон уже занят другой сессией) не должен
        # ни удалять её pid-файл, ни гасить её индикацию.
        def owned(path: str) -> bool:
            try:
                return pathlib.Path(path).read_text().strip() == str(os.getpid())
            except (OSError, ValueError):
                return False

        mine_listener, mine_daemon = owned(LISTENER_PID), owned(DAEMON_PID)
        if mine_listener:
            pathlib.Path(LISTENER_PID).unlink(missing_ok=True)
            pathlib.Path(LISTENER_OWNER).unlink(missing_ok=True)
        if mine_daemon:
            pathlib.Path(DAEMON_PID).unlink(missing_ok=True)
        if mine_listener or mine_daemon:
            try:
                STATE_FILE.write_text("off")
            except OSError:
                pass
