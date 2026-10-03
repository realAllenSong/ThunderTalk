"""Compatibility facade for the former experimental rewrite entry point.

Uses only an existing ready provider; never imports MLX or downloads a model.
New callers should use ai_cleanup.cleanup and llm_providers.detect directly.
"""
from thundertalk.core.ai_cleanup import cleanup
from thundertalk.core.llm_providers import detect


def rewrite(text: str, model_id: str = "") -> str | None:
    try:
        provider = next((p for p in detect() if p.is_ready()), None)
        if provider is None or not text.strip():
            return None
        result = cleanup(provider, text, model_id or provider.models[0])
        return result if result != text else None
    except Exception:
        return None
