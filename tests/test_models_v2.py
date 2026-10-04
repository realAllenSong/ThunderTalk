"""Catalog facts, CPU construction and Studio cloning, with fake runtimes."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from PySide6.QtWidgets import QLabel

from thundertalk.core import i18n, models, speech, tts
from thundertalk.core.asr import AsrEngine
from thundertalk.core.tts_backends.zipvoice import ZipVoiceBackend
from thundertalk.ui.model_facts import facts_text, language_tags


def test_catalog_metadata():
    unpublished = {"fireredasr2-ctc-int8", "fireredasr2-aed-int8"}
    assert len({m.id for m in models.BUILTIN_MODELS}) == len(models.BUILTIN_MODELS)
    for m in models.BUILTIN_MODELS:
        assert m.params or m.id in unpublished
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
    assert ZipVoiceBackend.info.size_mb == 163


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


@pytest.mark.parametrize(
    "family,factory,files",
    [
        ("FireRedASR2-CTC", "from_fire_red_asr_ctc", ["model.int8.onnx", "tokens.txt"]),
        (
            "FireRedASR2-AED",
            "from_fire_red_asr",
            ["encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"],
        ),
        (
            "Fun-ASR-Nano",
            "from_funasr_nano",
            ["encoder_adaptor.int8.onnx", "embedding.int8.onnx", "llm.int8.onnx"],
        ),
    ],
)
def test_cpu_recognizer_construction(monkeypatch, tmp_path, family, factory, files):
    import sherpa_onnx

    for f in files:
        (tmp_path / f).touch()
    (tmp_path / "Qwen3-0.6B").mkdir()
    fake = MagicMock()
    monkeypatch.setattr(sherpa_onnx.OfflineRecognizer, factory, fake)
    eng = AsrEngine()
    eng.set_hotwords(["开放时间", "ThunderTalk"])
    eng.load_model(str(tmp_path), family, "onnx", "low")
    kw = fake.call_args.kwargs
    assert kw["provider"] == "cpu" and 1 <= kw["num_threads"] <= 4
    assert eng.is_loaded and not eng.uses_gpu
    if family == "Fun-ASR-Nano":
        assert kw["hotwords"] == "开放时间,ThunderTalk"
        assert kw["language"] == "" and kw["max_new_tokens"] == 256
        assert Path(kw["tokenizer"]).name == "Qwen3-0.6B"
    eng.unload()
    assert not eng.is_loaded


def test_zipvoice_config_and_exact_reference(monkeypatch, tmp_path):
    import sherpa_onnx as so

    for f in (
        "encoder.int8.onnx",
        "decoder.int8.onnx",
        "tokens.txt",
        "lexicon.txt",
        "vocoder.onnx",
    ):
        (tmp_path / f).touch()
    (tmp_path / "espeak-ng-data").mkdir()
    b = ZipVoiceBackend(tmp_path, tmp_path / "vocoder.onnx")
    assert b.is_ready()
    fake = MagicMock()
    fake.generate.return_value = SimpleNamespace(
        samples=np.ones(100, np.float32), sample_rate=24000
    )
    ctor = MagicMock(return_value=fake)
    zip_config = MagicMock(wraps=so.OfflineTtsZipvoiceModelConfig)
    monkeypatch.setattr(so, "OfflineTts", ctor)
    monkeypatch.setattr(so, "OfflineTtsZipvoiceModelConfig", zip_config)
    ref = np.arange(20, dtype=np.float32) / 100
    ctx = {}
    b.generate(
        "你好",
        tts.ClonePrompt(ref, "exact words", "fake"),
        "chinese",
        speed=1.15,
        context=ctx,
    )
    kw = fake.generate.call_args.kwargs
    assert kw["prompt_text"] == "exact words" and kw["sample_rate"] == 24000
    np.testing.assert_array_equal(kw["prompt_samples"], ref)
    assert kw["speed"] == 1.15 and kw["num_steps"] == 4 and ctx["native_speed"]
    assert zip_config.call_args.kwargs["decoder"].endswith("decoder.int8.onnx")
    with pytest.raises(ValueError, match="exact transcript"):
        b.generate("hello", tts.ClonePrompt(ref, "", "fake"), "english")
    with pytest.raises(ValueError, match="Unknown ZipVoice"):
        b.reference("zipvoice:missing")
    b.unload()
    assert b._tts is None


def test_zipvoice_picker_and_my_voices(qapp, isolated_home):
    from thundertalk.ui.studio.speak_tab import SpeakTab

    tab = SpeakTab()
    tab._on_engine("zipvoice")
    assert tab._clone_backend() == "zipvoice"
    assert tab._chips and all(vid.startswith("zipvoice:") for vid in tab._chips)
    assert tab._add_chip is not None
    assert [tab._lang.itemData(i) for i in range(tab._lang.count())] == [
        "auto",
        "chinese",
        "english",
    ]
    assert "123M" in tab._engine_tag.text() and "CPU" in tab._engine_tag.text()
    tab.shutdown()
    tab.close()


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
                "fireredasr2-ctc-int8",
                "--model-dir",
                str(tmp_path),
                "--file",
                "public.wav",
            ]
        )
        == 0
    )
    eng.load_model.assert_called_once_with(str(tmp_path), "FireRedASR2-CTC", "onnx")
    eng.unload.assert_called_once()


def test_sherpa_native_timestamps_survive_dictation():
    eng = AsrEngine()
    stream = MagicMock()
    stream.result = SimpleNamespace(text="Hello", tokens=["Hello"], timestamps=[0.2])
    eng._recognizer = MagicMock()
    eng._recognizer.create_stream.return_value = stream
    eng._model_family = "FireRedASR2-CTC"
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
    with transcribe.selected_engine(None, "fireredasr2-ctc-int8") as eng:
        assert eng is fake
        assert policy._entries[fake].active == 1
    fake.unload.assert_called_once()
    assert policy._entries[fake].active == 0


def test_model_row_live_language_change(qapp, isolated_home, monkeypatch):
    from thundertalk.ui.pages.models_page import VariantRow

    model = next(m for m in models.BUILTIN_MODELS if m.id == "fireredasr2-ctc-int8")
    row = VariantRow(model, None, True, True)
    monkeypatch.setattr(i18n, "LANG", "zh")
    row.retranslate()
    assert row._status_badge.text() == "测试模型"
    assert "参数量未公布" in row._facts.text()
    assert "原生时间戳" in [lb.text() for lb in row._tags.findChildren(QLabel)]
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
