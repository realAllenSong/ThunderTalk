# ruff: noqa  # Pinned upstream source; local integration is linted separately.
"""Offline checkpoint paths. Downloads belong to ThunderTalk, never upstream."""
import os
from pathlib import Path

DEFAULT_VAE_VARIANT = "official"

def get_checkpoints_dir():
    path = os.environ.get("ACESTEP_CHECKPOINTS_DIR")
    return Path(path) if path else Path(os.environ.get("ACESTEP_PROJECT_ROOT", os.getcwd())) / "checkpoints"

def resolve_vae_path(checkpoint_dir, vae_variant=None):
    if vae_variant not in (None, "", "official"):
        raise ValueError("Only the official VAE is supported")
    return Path(checkpoint_dir) / "vae"
