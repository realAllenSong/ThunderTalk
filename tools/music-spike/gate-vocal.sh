#!/bin/bash
# Phase 0 vocal check. The 1 GB MLX cache cap fixed the VAE memory, but every 30 s vocal take read
# back empty and the takes differ wildly between VAE variants with the same seed. Steps:
# ASR control on the existing takes, then MLX VAE vs PyTorch VAE (latents saved, so diffusion
# determinism can be compared too), then the transformers 4.57.6 fallback, then ASR on the new takes.
set -u
cd "$(dirname "$0")/../.."
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
G=tools/music-spike/gate.sh
S=docs/verification/music-gate-summary.log
D=artifacts/music-spike
asr() {
  python3 "$BENCH/lock.py" gpu -- env PYTHONPATH="$PWD" TT_BENCH="$BENCH" HF_HUB_OFFLINE=1 \
    /Users/songallen/Desktop/ThunderTalk/.venv/bin/python tools/music-spike/asr_check.py "${@:2}" \
    > "docs/verification/music-asr-$1.log" 2>&1
  echo "asr-$1 exit $?" >> "$S"
}
asr existing $D/turbo-5.6.2-mps-vocal-vae192*-mxcache1/gate.wav $D/turbo-5.6.2-mps/gate.wav
COMMON=(--vocal --planner --duration 30 --vae-chunk 192 --mx-cache-gb 1)
$G vocal-mlxvae -- "${COMMON[@]}" --transformers 5.6.2 --tag latents
$G vocal-torchvae -- "${COMMON[@]}" --transformers 5.6.2 --torch-vae
$G vocal-4.57.6 -- "${COMMON[@]}" --transformers 4.57.6 --tag latents
asr new $D/turbo-5.6.2-mps-vocal-vae192-mxcache1-latents/gate.wav \
  $D/turbo-5.6.2-mps-vocal-vae192-mxcache1-torchvae/gate.wav \
  $D/turbo-4.57.6-mps-vocal-vae192-mxcache1-latents/gate.wav
echo "vocal chain done" >> "$S"
