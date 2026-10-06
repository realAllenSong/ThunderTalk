# ruff: noqa  # Pinned upstream source; local integration is linted separately.
"""ThunderTalk downloads models separately; generation is strictly offline."""
class InitServiceDownloadsMixin:
    def _ensure_models_present(self, *, checkpoint_path, config_path, prefer_source, vae_variant=None):
        for name in (config_path, "vae", "Qwen3-Embedding-0.6B"):
            folder = checkpoint_path / name
            if not folder.is_dir() or not any(folder.glob("*.safetensors")):
                return f"Missing model data: {folder}", False
        return None
