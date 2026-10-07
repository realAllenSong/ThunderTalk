#!/bin/bash
# Run this entire script through lock.py gpu. Models and HOME stay isolated.
# Usage: package-check.sh [turbo] [xl]  (default: turbo xl); "base" runs the runtime/audio/asr checks.
set -euo pipefail
ROOT="${MUSIC_MODELS_ROOT:-$HOME/Library/Caches/ThunderTalk-bench/music-models}"
PACKAGE_HOME="$PWD/artifacts/music-spike/package-home"
APP="$PWD/dist/ThunderTalk.app/Contents/MacOS/ThunderTalk"
mkdir -p "$PACKAGE_HOME"
export HOME="$PACKAGE_HOME"
export HF_HOME="$PACKAGE_HOME/hf-cache"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
for step in "${@:-base turbo xl}"; do
  for s in $step; do
    case "$s" in
      base)
        "$APP" --selftest runtime
        "$APP" --selftest audio
        say -o "$PACKAGE_HOME/dictation-probe.aiff" 'Morning lanterns light the river. We walk toward a quiet garden.'
        "$APP" --selftest asr --model qwen3-asr-06b-int8 --model-dir "$ROOT/lyric-asr" \
          --file "$PACKAGE_HOME/dictation-probe.aiff" ;;
      turbo|xl)
        /usr/bin/time -l "$APP" --selftest music --model "$s" --model-dir "$ROOT" ;;
      *) echo "unknown step $s" >&2; exit 2 ;;
    esac
  done
done
