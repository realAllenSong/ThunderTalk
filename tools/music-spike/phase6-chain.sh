#!/bin/bash
# Phase 6 real checks, one GPU-lock acquisition per model step so other agents can interleave.
# Before every model step we wait (holding no lock) until the app's own admission rule would pass,
# and a refused/failed step is retried. XL fetching and conversion both use the GPU lock.
# Logs: artifacts/music-spike/phase6/<step>.log, summary in artifacts/music-spike/phase6/chain.log.
# Usage: phase6-chain.sh [--build]   (--build first rebuilds dist/ThunderTalk.app via lock.py build)
set -u
cd "$(dirname "$0")/../.."
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
VPY="$BENCH/venv-music/bin/python"
APY=/Users/songallen/Desktop/ThunderTalk/.venv/bin/python
OUT=artifacts/music-spike/phase6
SAMPLES="$HOME/Music/ThunderTalk-dev-samples"
mkdir -p "$OUT" "$SAMPLES"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export HF_HUB_DOWNLOAD_TIMEOUT=60

note() { echo "$(date +%T) $*" >> "$OUT/chain.log"; }

# step NAME MODEL PLANNER(0|1) CMD...: wait for admission, then run under the GPU lock; 3 attempts.
step() {
  local name=$1 model=$2 planner=$3; shift 3
  local flag=""; [ "$planner" = 1 ] && flag="--planner"
  local rc=1
  for attempt in 1 2 3; do
    note "wait $name (attempt $attempt)"
    PYTHONPATH="$PWD" "$APY" tools/music-spike/wait_admit.py "$model" $flag >> "$OUT/$name.wait.log" 2>&1 \
      || { note "$name admission timeout"; return 1; }
    note "start $name"
    python3 "$BENCH/lock.py" gpu -- "$@" > "$OUT/$name.log" 2>&1
    rc=$?
    note "$name exit $rc"
    [ $rc = 0 ] && return 0
    grep -qE "music:memory|model_busy" "$OUT/$name.log" || return $rc
    sleep 60
  done
  return $rc
}

bench() {
  local model=$1 kind=$2
  local flags="--dictation"
  [ "$kind" = vocal-170 ] && flags="--song --dictation"
  step "bench-$model-$kind" "$model" 1 /usr/bin/time -l env PYTHONPATH="$PWD" "$VPY" tools/bench_music.py \
    --model "$model" $flags --output "$OUT/bench-$model-$kind"
  # Copy selected, verified takes to the sample folder after reviewing benchmark.json.
}

note "chain start $*"
if [ "${1:-}" = --build ]; then
  note "start build"
  python3 "$BENCH/lock.py" build -- env -u NOTARIZE "$APY" build_macos.py > artifacts/build-music.log 2>&1
  note "build exit $?"
fi

# Heavy downloads are serialized by the same machine-wide lock as model runs.
note "start fetch-xl"
python3 "$BENCH/lock.py" gpu -- env -u HF_HUB_OFFLINE -u TRANSFORMERS_OFFLINE PYTHONPATH="$PWD" \
  "$VPY" tools/music-spike/download.py --xl --fetch-only > "$OUT/fetch-xl.log" 2>&1
note "fetch-xl exit $?"

step package-turbo turbo 0 bash tools/music-spike/package-check.sh base turbo
step repaint turbo 0 env PYTHONPATH="$PWD" "$VPY" tools/music-spike/check_repaint.py --output "$OUT/repaint"
bench turbo instrumental-60
bench turbo vocal-170

if grep -q "Fetch complete" "$OUT/fetch-xl.log"; then
  step convert-xl turbo 0 env PYTHONPATH="$PWD" "$VPY" tools/music-spike/download.py --convert-only
  if grep -q "Download complete" "$OUT/convert-xl.log"; then
    step package-xl xl 0 bash tools/music-spike/package-check.sh xl
    bench xl instrumental-60
    bench xl vocal-170
  fi
fi
note "chain done"
