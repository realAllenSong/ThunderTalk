"""Smoke tests for TranslationEngine — no model download required.

These verify the engine's contract without loading the ~9GB SeamlessM4T
model. The actual translation quality is validated by the spike in
docs/plans/2026-04-24-translation-seamlessm4t.md and by the E2E manual
test in Task 9.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from thundertalk.core.translate import TranslationEngine, TranslationResult


def test_engine_not_loaded_by_default() -> None:
    engine = TranslationEngine()
    assert not engine.is_loaded
    assert engine.current_model is None


def test_translate_without_model_raises() -> None:
    engine = TranslationEngine()
    samples = np.zeros(16000, dtype=np.float32)
    with pytest.raises(RuntimeError, match="No translation model loaded"):
        engine.translate(samples, tgt_lang="eng")


def test_empty_audio_raises() -> None:
    engine = TranslationEngine()
    # Bypass is_loaded by stamping mocks directly
    engine._model = MagicMock()
    engine._processor = MagicMock()
    engine._model_id = "seamless-m4t-v2-large"
    engine._device = "cpu"
    with pytest.raises(RuntimeError, match="Empty audio"):
        engine.translate(np.zeros(0, dtype=np.float32), tgt_lang="eng")


def test_too_quiet_returns_empty_text() -> None:
    """RMS-below-threshold audio should short-circuit to empty result."""
    engine = TranslationEngine()
    engine._model = MagicMock()
    engine._processor = MagicMock()
    engine._model_id = "seamless-m4t-v2-large"
    engine._device = "cpu"
    # Constant DC bias of 0.0001 → rms ≈ 0.0001, well below 0.003 threshold
    samples = np.full(16000, 0.0001, dtype=np.float32)
    result = engine.translate(samples, tgt_lang="eng")
    assert result.text == ""
    assert result.tgt_lang == "eng"
    assert result.duration_secs == pytest.approx(1.0)
    # Critical: the model should NOT have been called for too-quiet audio
    engine._model.generate.assert_not_called()


def test_unload_clears_state() -> None:
    engine = TranslationEngine()
    engine._model = MagicMock()
    engine._processor = MagicMock()
    engine._model_id = "test"
    assert engine.is_loaded
    engine.unload()
    assert not engine.is_loaded
    assert engine.current_model is None


def test_translation_result_has_required_fields() -> None:
    """Pipeline._on_asr_done duck-types result by accessing .text and
    .duration_secs. Verify both are present on TranslationResult."""
    result = TranslationResult(
        text="hello",
        duration_secs=1.5,
        inference_ms=200,
        model="seamless-m4t-v2-large",
        tgt_lang="eng",
    )
    assert result.text == "hello"
    assert result.duration_secs == 1.5
