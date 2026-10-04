"""Long final decoding, model budgets, overlap joins and durable recovery."""

import json
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from thundertalk.core import i18n, models
from thundertalk.core.asr import AsrEngine, AsrResult
from thundertalk.core.dictation import decode_chunks, has_audio_energy, join_chunks, recover_final
from thundertalk.core.history import HistoryStore

SR = 16000


def marked_audio(seconds=179):
    # Each second encodes one unique spoken word for a deterministic fake.
    return np.repeat(.1 + np.arange(seconds, dtype=np.float32) * .0001, SR)


def words(x):
    ids = np.unique(np.rint((x[x > 0] - .1) / .0001).astype(int))
    return ' '.join(f'word{i:03}' for i in ids)


class LimitedEngine:
    current_model = 'fake'
    active_backend = 'onnx'
    max_clip_seconds = 20
    uses_gpu = False

    def __init__(self, limit=8, empty=False, raises=False, truncated=False):
        self.limit, self.empty, self.raises, self.truncated = limit, empty, raises, truncated
        self.calls = []

    def recognize(self, samples, sr=SR):
        self.calls.append(len(samples) / sr)
        if self.raises:
            raise RuntimeError('fake decode failure')
        text = '' if self.empty or len(samples) / sr > self.limit else words(samples)
        return AsrResult(text, len(samples) / sr, 1, self.current_model,
                         self.active_backend, truncated=self.truncated)


def test_long_final_decode_obeys_model_limit_and_keeps_every_word():
    fake = LimitedEngine(limit=20)
    eng = AsrEngine()
    eng._model_family, eng._active_backend = 'Fun-ASR-Nano', 'onnx'
    eng._recognizer = object()
    eng._recognize_any = fake.recognize
    x = marked_audio()
    result = eng.recognize(x)
    assert result.text == words(x)
    assert len(fake.calls) > 1 and max(fake.calls) <= 20
    assert result.duration_secs == 179 and not result.truncated
    assert result.recovery_source == 'chunks'


def test_unknown_model_empty_final_is_redecoded_without_preview():
    x = marked_audio()
    eng = LimitedEngine(limit=8)
    result = recover_final(eng, x)
    assert eng.calls[0] == 179
    assert max(eng.calls[1:]) <= 8
    assert result.text == words(x) and not result.truncated


@pytest.mark.parametrize('left,right,expected', [
    ('please bring the report', 'the report tomorrow', 'please bring the report tomorrow'),
    ('Hello world.', 'world, again.', 'Hello world. again.'),
    ('今天我们讨论模型', '讨论模型的速度。', '今天我们讨论模型的速度。'),
    ('one alpha', 'alphabet soup', 'one alpha alphabet soup'),
])
def test_overlapping_joins(left, right, expected):
    assert join_chunks(left, right) == expected


def test_real_repetition_at_silent_join_is_preserved():
    assert join_chunks('yes yes', 'yes yes again', overlap=False) == 'yes yes yes yes again'


def test_pause_cut_is_bounded_and_no_samples_are_omitted():
    x = marked_audio(60)
    x[19*SR:20*SR] = 0
    seen = []

    def decode(clip, sr):
        seen.append(clip.copy())
        return AsrResult(words(clip), len(clip)/sr, 1, 'fake')

    result = decode_chunks(decode, x, SR, 20)
    assert max(map(len, seen)) <= 20*SR
    assert result.text == words(x)
    assert result.duration_secs == 60


@pytest.mark.parametrize('raises', [False, True])
def test_failed_final_falls_back_to_preview(raises):
    result = recover_final(LimitedEngine(empty=True, raises=raises), marked_audio(), 'saved preview')
    assert result.text == 'saved preview' and result.recovery_source == 'preview'
    assert not result.truncated


def test_reported_truncation_is_redecoded_even_when_text_is_nonempty():
    eng = LimitedEngine()
    initial = AsrResult('partial', 179, 1, 'fake', truncated=True)
    result = recover_final(eng, marked_audio(), initial=initial)
    assert result.text == words(marked_audio()) and not result.truncated


def test_all_empty_speech_has_failed_source_but_silence_does_not():
    assert recover_final(LimitedEngine(empty=True), marked_audio()).recovery_source == 'failed'
    silent = np.zeros(SR, np.float32)
    assert not has_audio_energy(silent)
    result = recover_final(LimitedEngine(empty=True), silent)
    assert result.text == '' and result.recovery_source == ''
    assert recover_final(LimitedEngine(empty=True), silent, 'preview').text == 'preview'


@pytest.mark.parametrize('mid', ['funasr-nano-int8', 'qwen3-asr-06b-int8', 'qwen3-asr-06b-mlx'])
def test_hotword_prompt_budget_is_bounded_and_preserves_short_words(mid):
    info = next(m for m in models.BUILTIN_MODELS if m.id == mid)
    eng = AsrEngine()
    eng._model_family, eng._active_backend = info.family, info.backend
    eng.set_hotwords(['很长的词' * 100, 'ThunderTalk', '开放时间'] + [f'word{i}' for i in range(200)])
    prompt = eng._prompt_hotwords()
    assert len(prompt.encode()) <= info.max_hotword_bytes
    assert prompt.startswith('ThunderTalk,开放时间')
    assert all(m.max_clip_seconds > 0 for m in models.BUILTIN_MODELS)
    assert info.max_clip_seconds <= 30


@pytest.mark.parametrize('source,text', [('preview', 'saved preview'), ('failed', ''), ('partial', 'partial'), ('redecoded', 'recovered')])
def test_recovery_history_has_durable_recording_even_when_capture_off(qapp, isolated_home, source, text):
    from thundertalk import app
    take = dict(keep=False, samples=marked_audio(2), model='fake', language='auto',
                preview='saved preview', preview_stats={}, hotwords=[])
    history = HistoryStore()
    thread = app._remember_dictation(history, take, '', text, 2, 1, 'fake', source, False)
    thread.join(2)
    again = HistoryStore()
    entry = again._entries[0]
    assert entry.text == text and entry.recognition_source == source
    path = Path(entry.recording_path)
    assert path.is_file() and path.parent.name == 'recovery'
    sidecar = json.loads(path.with_suffix('.json').read_text())
    assert sidecar['recognition_source'] == source and sidecar['merged_pasted_text'] == text
    assert sidecar['final_asr_text'] == ''


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_recovered_history_label_and_recording_link(qapp, isolated_home, monkeypatch, lang):
    from thundertalk.ui.pages.home_page import _HistoryRow
    monkeypatch.setattr(i18n, 'LANG', lang)
    history = HistoryStore()
    history.add('preview', 179, 1, 'fake', recognition_source='preview', recording_path='/local/audio.wav')
    row = _HistoryRow(history._entries[0])
    assert i18n.t('home.preview_recovered') in [x.text() for x in row.findChildren(QLabel)]
    assert i18n.t('home.open_recording') in [x.text() for x in row.findChildren(QPushButton)]
    row.close()


def test_asr_worker_recovers_before_emitting_text(qapp):
    from thundertalk.app import AsrWorker
    got = []
    worker = AsrWorker(LimitedEngine(empty=True), marked_audio(10), preview_text='do not lose this')
    worker.done.connect(lambda text, *_: got.append(text))
    worker.run()
    assert got == ['do not lose this'] and worker.result.recovery_source == 'preview'


def test_legacy_history_recovery_fields_default(isolated_home):
    from thundertalk.core import history
    record = dict(v=1, kind="entry", id="old", text="legacy", timestamp=1,
                  duration_secs=2, inference_ms=3, model="fake")
    history._JSONL_PATH.write_text(json.dumps(record) + "\n")
    entry = HistoryStore()._entries[0]
    assert entry.recognition_source == "final" and entry.recording_path == ""


def test_recovery_audio_survives_normal_rotation(isolated_home):
    from thundertalk.core.recordings import save_recording
    root = isolated_home / ".thundertalk" / "recordings"
    args = dict(model="fake", language="auto", final_text="", preview_text="",
                pasted_text="", loop_detected=False)
    protected = save_recording(marked_audio(1), directory=root / "recovery", **args)
    for _ in range(22):
        save_recording(marked_audio(1), directory=root, **args)
    assert protected.is_file() and protected.with_suffix(".wav").is_file()
    assert len(list(root.glob("*.json"))) == 20


def test_native_token_saturation_flags_possible_partial_result():
    from types import SimpleNamespace
    eng = AsrEngine()
    eng._model_family, eng._active_backend = "Fun-ASR-Nano", "onnx"
    eng._itn_enabled = False
    stream = SimpleNamespace(result=SimpleNamespace(text="partial", tokens=["a"]*150),
                             accept_waveform=lambda *args: None)
    eng._recognizer = SimpleNamespace(create_stream=lambda: stream, decode_stream=lambda *args: None)
    result = eng.recognize(marked_audio(20))
    assert result.text == "partial" and result.truncated
