#!/bin/bash
# Chatterbox Turbo on the workstation GPU. ser5 points VOICE_TTS_URL here.
set -euo pipefail
ROOT="${VOICE_CHATTER_HOME:-$HOME/.local/share/voice-chatter}"
VENV="${VOICE_CHATTER_VENV:-$HOME/projects/lab-provisioning/.venv}"
mkdir -p "$ROOT/voices" "$ROOT/hf"
export HF_HOME="$ROOT/hf"
export CHATTER_VOICES_DIR="$ROOT/voices"
export CHATTER_DEVICE="${CHATTER_DEVICE:-cuda}"
export CHATTER_VARIANT="${CHATTER_VARIANT:-turbo}"
exec "$VENV/bin/python" -m voice_chatter
