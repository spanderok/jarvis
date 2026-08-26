#!/bin/bash
# Set up Jarvis on this Mac. Safe to re-run: it skips what is already in place.
#
#   bash install.sh              everything, for the language in jarvis.env
#   bash install.sh models       only download the models for that language
#   JARVIS_LANG=ru bash install.sh models    the models for another language
#   bash install.sh link         only link skills and commands into ~/.claude
#   bash install.sh keymap       only install the login item for keymap.sh
#
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$HOME/.claude/jarvis"
MODELS="$REPO/models"
STEP="${1:-all}"

# Which models to fetch is the locale's business. Read once, before anything
# needs it, so a typo in JARVIS_LANG stops the installer rather than half of it.
LANG_FILE=""
lang_field() { python3 "$REPO/lang.py" get "$1" 2>/dev/null; }

# CAM++ speaker embeddings, exported to ONNX. Language-independent - it compares
# voices, not words - so it lives here rather than in a locale.
SPEAKER_URL="${JARVIS_SPEAKER_URL:-https://huggingface.co/FunAudioLLM/CosyVoice-300M/resolve/main/campplus.onnx}"

say() { printf '\n== %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

# --------------------------------------------------------------------- checks
if [ "$(uname)" != "Darwin" ]; then
  echo "This assistant is macOS-only: it uses hidutil, osascript, afplay and" >&2
  echo "the macOS microphone permissions." >&2
  exit 1
fi
if ! have uv; then
  echo "uv is missing. Install it first:" >&2
  echo "  brew install uv        # or: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi
have claude || echo "warning: the claude CLI is not on PATH - Jarvis needs it to answer." >&2

# Load jarvis.env early: JARVIS_LANG lives there, and it decides what gets
# downloaded below.
if [ -f "$REPO/jarvis.env" ]; then
  set -a; . "$REPO/jarvis.env"; set +a
fi
JARVIS_LANG="${JARVIS_LANG:-en}"
export JARVIS_LANG
if ! LANG_FILE=$(python3 "$REPO/lang.py" get name 2>&1); then
  echo "$LANG_FILE" >&2
  echo "JARVIS_LANG=$JARVIS_LANG has no locale file. Available:" >&2
  python3 "$REPO/lang.py" list >&2
  exit 1
fi
echo "language: $JARVIS_LANG (wake word: $LANG_FILE)"

# ------------------------------------------------------- the ~/.claude/jarvis path
# Every script and skill refers to ~/.claude/jarvis. If the repo lives elsewhere,
# a symlink makes both paths the same directory.
if [ "$STEP" = all ] || [ "$STEP" = link ]; then
  say "path"
  if [ "$REPO" = "$HOME_DIR" ]; then
    echo "repo is already at ~/.claude/jarvis"
  elif [ -L "$HOME_DIR" ]; then
    echo "symlink exists: $HOME_DIR -> $(readlink "$HOME_DIR")"
  elif [ -e "$HOME_DIR" ]; then
    echo "warning: $HOME_DIR exists and is not a symlink - leaving it alone." >&2
    echo "         Move it aside, or clone this repo there instead." >&2
  else
    mkdir -p "$HOME/.claude"
    ln -s "$REPO" "$HOME_DIR"
    echo "linked $HOME_DIR -> $REPO"
  fi
fi

# ------------------------------------------------------------ skills / commands
if [ "$STEP" = all ] || [ "$STEP" = link ]; then
  say "skills and commands"
  mkdir -p "$HOME/.claude/skills" "$HOME/.claude/commands"
  for s in voice-answer spotify; do
    dst="$HOME/.claude/skills/$s"
    if [ -e "$dst" ] || [ -L "$dst" ]; then
      echo "skip $dst (exists)"
    else
      ln -s "$REPO/skills/$s" "$dst"; echo "linked $dst"
    fi
  done
  for c in assist.md assist-off.md jarvis-daemon.md; do
    dst="$HOME/.claude/commands/$c"
    if [ -e "$dst" ] || [ -L "$dst" ]; then
      echo "skip $dst (exists)"
    else
      ln -s "$REPO/commands/$c" "$dst"; echo "linked $dst"
    fi
  done
fi

# ------------------------------------------------------------------- settings
if [ "$STEP" = all ]; then
  say "settings"
  if [ -f "$REPO/jarvis.env" ]; then
    echo "jarvis.env is already there, not touching it"
  else
    cp "$REPO/jarvis.env.example" "$REPO/jarvis.env"
    echo "created jarvis.env from the example - open it and set your name and keys"
  fi
fi

# --------------------------------------------------------------------- models
if [ "$STEP" = all ] || [ "$STEP" = models ]; then
  say "models for $JARVIS_LANG (one time)"
  mkdir -p "$MODELS"

  WAKE_MODEL=$(lang_field wake_model)
  WAKE_URL=$(lang_field wake_model_url)
  TTS_BACKEND=$(lang_field tts_backend)
  TTS_VOICE=$(lang_field tts_voice)
  TTS_URL=$(lang_field tts_voice_url)

  # 1. Wake word spotting: a small offline recognizer for this language.
  if [ -d "$MODELS/$WAKE_MODEL" ]; then
    echo "wake model: $WAKE_MODEL already there"
  else
    echo "wake model: downloading $WAKE_MODEL (about 45 MB)"
    curl -fL# -o /tmp/vosk-asr.zip "$WAKE_URL"
    unzip -q /tmp/vosk-asr.zip -d "$MODELS" && rm -f /tmp/vosk-asr.zip
  fi

  # 2a. The English voice: piper, two files per voice, no build step.
  if [ "$TTS_BACKEND" = "piper" ]; then
    mkdir -p "$MODELS/piper"
    if [ -f "$MODELS/piper/$TTS_VOICE.onnx" ]; then
      echo "voice: $TTS_VOICE already there"
    else
      echo "voice: downloading $TTS_VOICE (63 MB)"
      curl -fL# -o "$MODELS/piper/$TTS_VOICE.onnx" "$TTS_URL$TTS_VOICE.onnx" \
        && curl -fL# -o "$MODELS/piper/$TTS_VOICE.onnx.json" "$TTS_URL$TTS_VOICE.onnx.json" \
        || echo "warning: voice download failed - he will fall back to say -v $(lang_field system_voice)" >&2
    fi
    # A second voice costs 63 MB and one line. Handy for telling two rooms
    # apart by ear, and for hearing what an accent does to a long answer.
    for extra in ${JARVIS_EXTRA_VOICES:-}; do
      [ -f "$MODELS/piper/$extra.onnx" ] && continue
      family="${extra%%-*}"; rest="${extra#*-}"; who="${rest%%-*}"; qual="${rest##*-}"
      base="https://huggingface.co/rhasspy/piper-voices/resolve/main/${family%%_*}/$family/$who/$qual/"
      echo "extra voice: downloading $extra"
      curl -fL# -o "$MODELS/piper/$extra.onnx" "$base$extra.onnx" \
        && curl -fL# -o "$MODELS/piper/$extra.onnx.json" "$base$extra.onnx.json" \
        || echo "warning: could not fetch $extra" >&2
    done
  fi

  # 2b. The Russian voice: vosk-tts, plus the stress dictionary it needs.
  if [ "$TTS_BACKEND" = "vosk" ]; then
  if [ -f "$MODELS/vosk-0.7/model.onnx" ]; then
    echo "voice model: already there"
  else
    echo "voice model: downloading (140 MB)"
    curl -fL# -o /tmp/vosk-tts.zip "$TTS_URL"
    unzip -q /tmp/vosk-tts.zip -d /tmp/vosk-tts && rm -f /tmp/vosk-tts.zip
    src=$(find /tmp/vosk-tts -maxdepth 2 -name model.onnx | head -1)
    if [ -n "$src" ]; then
      mkdir -p "$MODELS/vosk-0.7"
      cp -R "$(dirname "$src")"/* "$MODELS/vosk-0.7/"
      rm -rf /tmp/vosk-tts
    else
      echo "warning: model.onnx not found in the archive - unpack it into" >&2
      echo "         $MODELS/vosk-0.7 by hand." >&2
    fi
  fi
  # The 2M-word stress table is shipped as text; sqlite is what keeps the voice
  # at 359 MB of RAM instead of 966 MB.
  if [ -f "$MODELS/vosk-0.7/dict.sqlite" ]; then
    echo "stress dictionary: already built"
  elif [ -s "$MODELS/vosk-0.7/dictionary" ]; then
    echo "stress dictionary: building (a minute or two)"
    uv run --quiet "$REPO/vosk_dict.py" build "$MODELS/vosk-0.7/dictionary"
  else
    echo "warning: no dictionary file in $MODELS/vosk-0.7 - the voice will read" >&2
    echo "         unknown words with default stress." >&2
  fi
  fi

  # 3. Speaker verification, so he can tell your voice from anyone else's.
  #    Optional: without campplus.onnx voiceprint.py fails open, and he answers
  #    whoever speaks - which is the default anyway until a profile is recorded.
  #    JARVIS_NO_SPEAKER=1 skips the download.
  if [ -f "$MODELS/campplus.onnx" ]; then
    echo "speaker check: model present"
  elif [ -n "${JARVIS_NO_SPEAKER:-}" ]; then
    echo "speaker check: skipped, the voice lock stays off"
  else
    echo "speaker check: downloading CAM++ (28 MB)"
    curl -fL# -o "$MODELS/campplus.onnx.tmp" "$SPEAKER_URL" \
      && mv "$MODELS/campplus.onnx.tmp" "$MODELS/campplus.onnx" \
      || { rm -f "$MODELS/campplus.onnx.tmp"
           echo "warning: CAM++ download failed - the voice lock stays off." >&2; }
  fi
  if [ -f "$MODELS/campplus.onnx" ] && [ ! -f "$REPO/voiceprint.json" ]; then
    echo "  no voice profile yet - record one with: uv run enroll_voice.py"
    echo "  (until then he answers anyone, which is the default)"
  fi
fi

# --------------------------------------------------------------------- keymap
if [ "$STEP" = all ] || [ "$STEP" = keymap ]; then
  say "key remap at login (optional)"
  if [ -z "${JARVIS_KEYMAP_VENDOR:-}" ] && ! grep -qs '^JARVIS_KEYMAP_VENDOR=' "$REPO/jarvis.env" 2>/dev/null; then
    echo "skipped: JARVIS_KEYMAP_VENDOR is not set in jarvis.env."
    echo "  Run 'bash keymap.sh list' to find your keyboard, fill in jarvis.env,"
    echo "  then re-run 'bash install.sh keymap'."
  else
    plist="$HOME/Library/LaunchAgents/com.jarvis.keymap.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s|__HOME__|$HOME|g" "$REPO/launchd/com.jarvis.keymap.plist" > "$plist"
    launchctl unload "$plist" 2>/dev/null
    launchctl load "$plist" && echo "installed $plist"
  fi
fi

say "next"
cat <<'NEXT'
1. Give your terminal microphone access:
   System Settings -> Privacy & Security -> Microphone
2. For the hotkeys, also give it Input Monitoring:
   System Settings -> Privacy & Security -> Input Monitoring
   (or skip it and bind jarvis-key.sh to a shortcut in Shortcuts.app)
3. Start him:  bash ~/.claude/jarvis/jarvisd.sh
   Then say his name.
NEXT
echo "   (this build is set to $JARVIS_LANG - the wake word is \"$LANG_FILE\")"
