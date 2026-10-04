"""Catalog facts, CPU construction and saved preference recovery, with fake runtimes."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from PySide6.QtWidgets import QLabel

from thundertalk.core import i18n, models, speech
from thundertalk.core.asr import AsrEngine
from thundertalk.ui.model_facts import facts_text, language_tags


def test_catalog_metadata():
    assert len({m.id for m in models.BUILTIN_MODELS}) == len(models.BUILTIN_MODELS)
    for m in models.BUILTIN_MODELS:
        assert m.params
        assert m.languages and len(m.languages) == len(set(m.languages))
        if m.languages_complete:
            assert m.language_count == len(m.languages)
        assert m.runs_on_cpu == (not m.backend.startswith("mlx"))
    for m in models.BUILTIN_MODELS:
        if m.family.startswith("Parakeet"):
            assert not m.supports_chinese
    assert (
        next(
            m for m in models.BUILTIN_MODELS if m.family == "Parakeet-TDT-v3"
        ).language_count
        == 25
    )
    assert (
        next(m for m in models.BUILTIN_MODELS if m.family == "SenseVoice").params
        == "234M"
    )
    for b in speech.backends():
        assert b.info.params and b.info.language_codes


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_language_tags_and_overflow(qapp, isolated_home, monkeypatch, lang):
    monkeypatch.setattr(i18n, "LANG", lang)
    codes = next(
        m.languages for m in models.BUILTIN_MODELS if m.family == "Parakeet-TDT-v3"
    )
    host = language_tags(codes)
    labels = host.findChildren(QLabel)
    assert i18n.t("models.no_chinese") in [lb.text() for lb in labels]
    more = next(lb for lb in labels if lb.text() == "+21")
    assert "uk" in more.toolTip() and "English" in more.toolTip()
    assert "0.6B" in facts_text("0.6B", 521, True)
    assert i18n.t("models.params_unknown") in facts_text("", 521, True)
    host.close()


@pytest.mark.parametrize("memory_mode,budget", [("low", 256), ("high", 512)])
def test_funasr_recognizer_construction(monkeypatch, tmp_path, memory_mode, budget):
    import sherpa_onnx

    for f in ("encoder_adaptor.int8.onnx", "embedding.int8.onnx", "llm.int8.onnx"):
        (tmp_path / f).touch()
    (tmp_path / "Qwen3-0.6B").mkdir()
    fake = MagicMock()
    monkeypatch.setattr(sherpa_onnx.OfflineRecognizer, "from_funasr_nano", fake)
    eng = AsrEngine()
    eng.set_hotwords(["开放时间", "ThunderTalk"])
    eng.load_model(str(tmp_path), "Fun-ASR-Nano", "onnx", memory_mode)
    kw = fake.call_args.kwargs
    assert kw["provider"] == "cpu" and kw["num_threads"] >= 1
    if memory_mode == "low":
        assert kw["num_threads"] <= 4
    assert eng.is_loaded and not eng.uses_gpu
    assert kw["hotwords"] == "开放时间,ThunderTalk"
    assert kw["language"] == "" and kw["max_new_tokens"] == budget
    assert Path(kw["tokenizer"]).name == "Qwen3-0.6B"
    eng.unload()
    assert not eng.is_loaded


def test_selftest_model_routes_and_unloads(monkeypatch, tmp_path):
    from thundertalk import selftest
    from thundertalk.core import asr, audio_io, transcribe

    eng = MagicMock()
    eng.recognize.return_value = SimpleNamespace(text="你好", inference_ms=2)
    monkeypatch.setattr(asr, "AsrEngine", lambda: eng)
    monkeypatch.setattr(audio_io, "decode_audio", lambda *a: np.ones(16000, np.float32))
    monkeypatch.setattr(
        transcribe,
        "transcribe_file",
        lambda *a: SimpleNamespace(
            segments=[object()], realtime_factor=0.1, to_text=lambda: "你好"
        ),
    )
    assert (
        selftest.run(
            [
                "asr",
                "--model",
                "funasr-nano-int8",
                "--model-dir",
                str(tmp_path),
                "--file",
                "public.wav",
            ]
        )
        == 0
    )
    eng.load_model.assert_called_once_with(str(tmp_path), "Fun-ASR-Nano", "onnx")
    eng.unload.assert_called_once()


def test_sherpa_native_timestamps_survive_dictation():
    eng = AsrEngine()
    stream = MagicMock()
    stream.result = SimpleNamespace(text="Hello", tokens=["Hello"], timestamps=[0.2])
    eng._recognizer = MagicMock()
    eng._recognizer.create_stream.return_value = stream
    eng._model_family = "Parakeet-TDT-v3"
    eng._itn_enabled = False
    result = eng.recognize(np.ones(16000, np.float32))
    assert result.tokens == ["Hello"] and result.token_timestamps == [0.2]
    assert result.text == "Hello"


def test_alternate_cpu_engine_has_memory_lease(monkeypatch, tmp_path):
    from thundertalk.core import asr, memory_policy, transcribe

    policy = memory_policy.MemoryPolicy()
    monkeypatch.setattr(memory_policy, "POLICY", policy)
    monkeypatch.setattr(policy, "start", lambda: None)
    fake = MagicMock()
    monkeypatch.setattr(asr, "AsrEngine", lambda: fake)
    monkeypatch.setattr(models, "is_downloaded", lambda _: True)
    monkeypatch.setattr(models, "get_model_path", lambda _: str(tmp_path))
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda _: True)
    with transcribe.selected_engine(None, "funasr-nano-int8") as eng:
        assert eng is fake
        assert policy._entries[fake].active == 1
    fake.unload.assert_called_once()
    assert policy._entries[fake].active == 0


def test_model_row_live_language_change(qapp, isolated_home, monkeypatch):
    from thundertalk.ui.pages.models_page import VariantRow

    model = next(m for m in models.BUILTIN_MODELS if m.id == "funasr-nano-int8")
    row = VariantRow(model, None, True, True)
    monkeypatch.setattr(i18n, "LANG", "zh")
    row.retranslate()
    assert row._status_badge.text() == "测试模型"
    assert "0.8B" in row._facts.text()
    assert "热词" in [lb.text() for lb in row._tags.findChildren(QLabel)]
    row.close()


def test_funasr_silence_marker_and_estimated_times():
    eng = AsrEngine()
    stream = MagicMock()
    stream.result = SimpleNamespace(
        text="Hello /sil world.", tokens=["Hello", "world"], timestamps=[0.0, 1.0]
    )
    eng._recognizer = MagicMock()
    eng._recognizer.create_stream.return_value = stream
    eng._model_family = "Fun-ASR-Nano"
    eng._itn_enabled = False
    result = eng.recognize(np.ones(16000, np.float32))
    assert "/sil" not in result.text
    assert result.token_timestamps == []
    stream.result.text = "The file is /silver/data."
    assert eng.recognize(np.ones(16000, np.float32)).text == "The file is /silver/data."


@pytest.mark.parametrize("retired", ["fireredasr2-ctc-int8", "fireredasr2-aed-int8", "unknown-asr"])
def test_retired_asr_setting_falls_back(isolated_home, retired):
    import json
    from thundertalk.core import settings

    settings._PATH.write_text(json.dumps({"active_model_id": retired}))
    prefs = settings.Settings()
    assert prefs.active_model_id == models.get_recommended_id("Qwen3-ASR")
    assert prefs.active_model_id in {m.id for m in models.BUILTIN_MODELS}
    # Loading migrates in memory; normal subsequent saves persist it atomically.
    prefs.save()
    assert settings.Settings().active_model_id == prefs.active_model_id


@pytest.mark.parametrize("voice", ["zipvoice:warm-female-zh", "my:retained"])
def test_retired_speak_setting_falls_back(qapp, isolated_home, voice):
    import json
    from thundertalk.core import settings
    from thundertalk.ui.studio.speak_tab import SpeakTab

    settings._PATH.write_text(json.dumps({"studio_engine": "zipvoice", "studio_voice": voice}))
    prefs = settings.Settings()
    assert prefs.get("studio_engine") == speech.DEFAULT_CLONE_BACKEND
    assert prefs.get("studio_voice") == (voice if voice.startswith("my:") else "")
    tab = SpeakTab(prefs)
    assert tab._engine_id == speech.DEFAULT_CLONE_BACKEND
    assert tab._voice_id in tab._chips
    assert tab._needed_backend().info.id == speech.DEFAULT_CLONE_BACKEND
    assert {bid for bid in speech.BACKEND_ORDER} == {"voxcpm2", "indextts", "kokoro"}
    tab.shutdown()
    tab.close()


def test_retained_preferences_and_experimental_funasr(isolated_home):
    import json
    from thundertalk.core import settings

    values = {"active_model_id": "funasr-nano-int8", "studio_engine": "kokoro", "studio_voice": "kokoro:3"}
    settings._PATH.write_text(json.dumps(values))
    prefs = settings.Settings()
    assert all(prefs.get(k) == v for k, v in values.items())
    model = next(m for m in models.BUILTIN_MODELS if m.id == prefs.active_model_id)
    assert model.experimental and model.hotword_support
