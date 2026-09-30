"""Pluggable speech-generation backends for Studio ▸ Speak.

The shared pipeline in ``thundertalk.core.tts`` (piece planning, duration and
read-back checks, level matching, joining, speed) is backend-agnostic; a
backend only has to turn one short piece of text into audio.
"""

from thundertalk.core.tts_backends.base import (  # noqa: F401
    BackendInfo,
    BackendVoice,
    Download,
    TtsBackend,
)
