#!/bin/bash
# Phase 6 real checks, one GPU-lock acquisition per model step so other agents can interleave.
# Logs: artifacts/music-spike/phase6/<step>.log, summary in artifacts/music-spike/phase6/chain.log.
set -u
cd "$(dirname "$0")/../.."
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
VPY="$BENCH/venv-music/bin/python"
OUT=artifacts/music-spike/phase6
SAMPLES="$HOME/Music/ThunderTalk-dev-samples"
mkdir -p "$OUT" "$SAMPLES"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

step() {
  local name=$1; shift
  echo "$(date +%T) start $name" >> "$OUT/chain.log"
  python3 "$BENCH/lock.py" gpu -- "$@" > "$OUT/$name.log" 2>&1
  local rc=$?
  echo "$(date +%T) $name exit $rc" >> "$OUT/chain.log"
  return $rc
}

bench() {
  local model=$1 kind=$2
  local flags="--dictation"
  [ "$kind" = vocal-170 ] && flags="--song --dictation"
  step "bench-$model-$kind" /usr/bin/time -l env PYTHONPATH="$PWD" "$VPY" tools/bench_music.py \
    --model "$model" $flags --output "$OUT/bench-$model-$kind"
  [ -f "$OUT/bench-$model-$kind/take-1.wav" ] && cp "$OUT/bench-$model-$kind/take-1.wav" "$SAMPLES/$model-$kind.wav"
}

step package-turbo bash tools/music-spike/package-check.sh base turbo
step repaint env PYTHONPATH="$PWD" "$VPY" tools/music-spike/check_repaint.py --output "$OUT/repaint"
[ -f "$OUT/repaint/take-1.wav" ] && cp "$OUT/repaint/take-1.wav" "$SAMPLES/turbo-repaint-3s-6s.wav"
bench turbo instrumental-60
bench turbo vocal-170
HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 step download-xl env -u HF_HUB_OFFLINE -u TRANSFORMERS_OFFLINE \
  PYTHONPATH="$PWD" "$VPY" tools/music-spike/download.py --xl
if grep -q "Download complete" "$OUT/download-xl.log"; then
  step package-xl bash tools/music-spike/package-check.sh xl
  bench xl instrumental-60
  bench xl vocal-170
fi
echo "$(date +%T) chain done" >> "$OUT/chain.log"
