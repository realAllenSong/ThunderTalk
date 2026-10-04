"""Smoke tests for TranslationEngine — no model download required.

These verify the engine's contract without loading the ~9GB SeamlessM4T
model. The actual translation quality is validated by the spike in
docs/plans/2026-04-24-translation-seamlessm4t.md and by the E2E manual
test in Task 9.
"""

from __future__ import annotations

import sys
from contextlib import nullcontext
from unittest.mock import MagicMock, call

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


def test_translate_text_without_model_raises() -> None:
    engine = TranslationEngine()
    with pytest.raises(RuntimeError, match="No translation model loaded"):
        engine.translate_text("hello", src_lang="eng", tgt_lang="cmn")


def test_translate_text_empty_raises() -> None:
    engine = TranslationEngine()
    engine._model = MagicMock()
    engine._processor = MagicMock()
    engine._model_id = "seamless-m4t-v2-large"
    engine._device = "cpu"
    with pytest.raises(RuntimeError, match="Empty text"):
        engine.translate_text("", src_lang="eng", tgt_lang="cmn")
    with pytest.raises(RuntimeError, match="Empty text"):
        engine.translate_text("   \n\t  ", src_lang="eng", tgt_lang="cmn")


@pytest.fixture
def mock_engine(monkeypatch):
    # PyTorch is optional; exercise generation without importing it or weights.
    torch = MagicMock()
    torch.no_grad.side_effect = nullcontext
    monkeypatch.setitem(sys.modules, "torch", torch)
    engine = TranslationEngine()
    engine._model = MagicMock()
    engine._model.generate.return_value = [np.array([[3, 123, 3]])]
    engine._processor = MagicMock()
    engine._processor.return_value = {"input_features": "speech features"}
    engine._model_id = "seamless-m4t-v2-large"
    return engine


def test_mandarin_speech_uses_english_pivot(mock_engine):
    engine = mock_engine
    engine._processor.side_effect = [
        {"input_features": "speech features"},
        {"input_ids": "text tokens"},
    ]
    engine._processor.decode.side_effect = ["Hello world.", "你好世界。"]
    samples = np.full(16000, 0.1, dtype=np.float32)
    result = engine.translate(samples, tgt_lang="cmn")
    assert engine._model.generate.call_args_list == [
        call(input_features="speech features", tgt_lang="eng", generate_speech=False),
        call(input_ids="text tokens", tgt_lang="cmn", generate_speech=False),
    ]
    assert engine._processor.call_args_list == [
        call(audio=samples, sampling_rate=16000, return_tensors="pt"),
        call(text="Hello world.", src_lang="eng", return_tensors="pt"),
    ]
    assert result.text == "你好世界。"
    assert result.tgt_lang == "cmn"
    assert result.duration_secs == 1.0
    assert result.model == "seamless-m4t-v2-large"


@pytest.mark.parametrize("tgt_lang", ["spa", "eng"])
def test_other_speech_targets_generate_directly(mock_engine, tgt_lang):
    engine = mock_engine
    engine._processor.decode.return_value = "translated speech"
    result = engine.translate(np.full(16000, 0.1, dtype=np.float32), tgt_lang=tgt_lang)
    engine._model.generate.assert_called_once_with(
        input_features="speech features", tgt_lang=tgt_lang, generate_speech=False,
    )
    engine._processor.assert_called_once()
    assert result.text == "translated speech"
    assert result.tgt_lang == tgt_lang


def test_empty_mandarin_pivot_skips_text_translation(mock_engine):
    mock_engine._processor.decode.return_value = "   "
    result = mock_engine.translate(np.full(16000, 0.1, dtype=np.float32), tgt_lang="cmn")
    mock_engine._model.generate.assert_called_once()
    mock_engine._processor.assert_called_once()
    assert not result.text.strip()


def test_text_translation_keeps_requested_target(mock_engine):
    engine = mock_engine
    engine._processor.return_value = {"input_ids": "text tokens"}
    engine._processor.decode.return_value = "你好世界。"
    result = engine.translate_text(" Hello world. ", src_lang="eng", tgt_lang="cmn")
    engine._processor.assert_called_once_with(
        text="Hello world.", src_lang="eng", return_tensors="pt",
    )
    engine._model.generate.assert_called_once_with(
        input_ids="text tokens", tgt_lang="cmn", generate_speech=False,
    )
    assert result.text == "你好世界。"
    assert result.duration_secs == 0.0
    assert result.tgt_lang == "cmn"


def test_text_translation_reloads_on_worker_request_after_idle(mock_engine, monkeypatch):
    engine = mock_engine
    model, processor = engine._model, engine._processor
    processor.return_value = {"input_ids": "text tokens"}
    processor.decode.return_value = "Hello."
    engine.prepare("/downloaded/seamless")
    engine.release_idle()
    assert not engine.is_loaded and engine.can_translate
    loads = []
    def load(source):
        loads.append(source)
        engine._model, engine._processor = model, processor
    monkeypatch.setattr(engine, "load_model", load)
    assert engine.translate_text("你好", "cmn", "eng").text == "Hello."
    assert loads == ["/downloaded/seamless"]


def test_detect_src_lang_chinese() -> None:
    from thundertalk.core.translate import detect_src_lang
    assert detect_src_lang("你好世界") == "cmn"
    assert detect_src_lang("今天天气真好。") == "cmn"


def test_detect_src_lang_japanese() -> None:
    from thundertalk.core.translate import detect_src_lang
    # Hiragana / Katakana presence → jpn
    assert detect_src_lang("こんにちは") == "jpn"
    assert detect_src_lang("カタカナ") == "jpn"
    # Mixed kanji + hiragana
    assert detect_src_lang("今日はいい天気です") == "jpn"


def test_detect_src_lang_korean() -> None:
    from thundertalk.core.translate import detect_src_lang
    assert detect_src_lang("안녕하세요") == "kor"


def test_detect_src_lang_english_default() -> None:
    from thundertalk.core.translate import detect_src_lang
    assert detect_src_lang("Hello world") == "eng"
    assert detect_src_lang("") == "eng"  # empty falls back to eng


def test_detect_src_lang_mixed_chinese_english() -> None:
    """Mostly Chinese with some English words → still cmn."""
    from thundertalk.core.translate import detect_src_lang
    assert detect_src_lang("我用ThunderTalk做语音识别。") == "cmn"
