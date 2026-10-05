#!/bin/sh
# Run this worktree's built app with disposable settings and model caches.
set -eu
onboarding_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
onboarding_app="$onboarding_root/dist/ThunderTalk.app/Contents/MacOS/ThunderTalk"
if [ ! -x "$onboarding_app" ]; then
    printf '%s\n' "Build ThunderTalk.app in this worktree first (python build_macos.py)." >&2
    exit 1
fi
onboarding_cache="$HOME/Library/Caches/ThunderTalk-bench"
mkdir -p "$onboarding_cache"
onboarding_home=$(mktemp -d "$onboarding_cache/fresh-home-onboarding-demo.XXXXXX")
printf 'Throwaway HOME: %s\n' "$onboarding_home"
printf 'Quit ThunderTalk from its menu bar to finish; this HOME is then deleted automatically.\n'
printf "To delete it manually after quitting: rm -rf -- '%s'\n" "$onboarding_home"
printf '%s\n' "This isolates settings and downloads. macOS permissions still belong to the signed app."
trap 'rm -rf -- "$onboarding_home"' EXIT HUP INT TERM
onboarding_lock="$onboarding_cache/lock.py"
if [ -f "$onboarding_lock" ]; then
    python3 "$onboarding_lock" gpu -- env HOME="$onboarding_home" \
        XDG_CACHE_HOME="$onboarding_home/.cache" HF_HOME="$onboarding_home/.cache/huggingface" \
        HF_HUB_CACHE="$onboarding_home/.cache/huggingface/hub" \
        HUGGINGFACE_HUB_CACHE="$onboarding_home/.cache/huggingface/hub" \
        TRANSFORMERS_CACHE="$onboarding_home/.cache/huggingface/hub" "$onboarding_app"
else
    HOME="$onboarding_home" XDG_CACHE_HOME="$onboarding_home/.cache" \
        HF_HOME="$onboarding_home/.cache/huggingface" \
        HF_HUB_CACHE="$onboarding_home/.cache/huggingface/hub" \
        HUGGINGFACE_HUB_CACHE="$onboarding_home/.cache/huggingface/hub" \
        TRANSFORMERS_CACHE="$onboarding_home/.cache/huggingface/hub" "$onboarding_app"
fi
