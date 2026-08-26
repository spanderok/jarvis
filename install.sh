#!/bin/bash
# Set up Jarvis on this Mac. Safe to re-run: it skips what is already in place.
#
#   bash install.sh              everything
#   bash install.sh models       only download the voice models
#   bash install.sh link         only link skills and commands into ~/.claude
#   bash install.sh keymap       only install the login item for keymap.sh
#
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$HOME/.claude/jarvis"
MODELS="$REPO/models"
STEP="${1:-all}"

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
  say "models (about 400 MB, one time)"
  mkdir -p "$MODELS"

  # 1. Wake word and command recognition, offline Russian ASR.
  if [ -d "$MODELS/vosk-model-small-ru-0.22" ]; then
    echo "wake model: already there"
  else
    echo "wake model: downloading (46 MB)"
    curl -fL# -o /tmp/vosk-asr.zip \
      https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip
    unzip -q /tmp/vosk-asr.zip -d "$MODELS" && rm -f /tmp/vosk-asr.zip
  fi

  # 2. The voice he speaks with, plus the stress dictionary it needs.
  if [ -f "$MODELS/vosk-0.7/model.onnx" ]; then
    echo "voice model: already there"
  else
    echo "voice model: downloading (140 MB)"
    curl -fL# -o /tmp/vosk-tts.zip \
      https://alphacephei.com/vosk/models/vosk-model-tts-ru-0.7-multi.zip
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

  # 3. Silero, the fallback voice. speak.py downloads it on first use, so this
  #    is only a head start.
  if [ -f "$MODELS/v4_ru.pt" ]; then
    echo "silero voice: already there"
  else
    echo "silero voice: downloading (38 MB)"
    curl -fL# -o "$MODELS/v4_ru.pt" https://models.silero.ai/models/tts/ru/v4_ru.pt \
      || echo "warning: silero download failed, speak.py will retry later" >&2
  fi

  # 4. Speaker verification is optional. Without campplus.onnx voiceprint.py
  #    fails open - Jarvis answers whoever speaks, which is the default anyway.
  if [ -f "$MODELS/campplus.onnx" ]; then
    echo "speaker check: model present"
  else
    echo "speaker check: no campplus.onnx - the voice lock stays off."
    echo "  To enable it, put a CAM++ speaker-embedding model exported to ONNX at"
    echo "  $MODELS/campplus.onnx, then run: uv run enroll_voice.py"
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
   Then say "Джарвис".
NEXT
