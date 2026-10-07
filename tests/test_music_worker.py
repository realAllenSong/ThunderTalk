"""core/music_worker._serve with fake acestep / torch / mlx modules (no models)."""
import json
import sys
import threading
import types

import numpy as np
import pytest
import soundfile as sf

from thundertalk.core import music, music_worker
from thundertalk.core.music_worker import MusicRequest


class Tensor:
    def __init__(self, array):
        self.array = np.asarray(array, dtype=np.float32)

    def __getitem__(self, i):
        return Tensor(self.array[i])

    def detach(self):
        return self

    def cpu(self):
        return self

    def float(self):
        return self

    def numpy(self):
        return self.array

    @property
    def shape(self):
        return self.array.shape


class Connection:
    def __init__(self):
        self.events = []
        self.closed = False

    def send(self, event):
        self.events.append(event)

    def close(self):
        self.closed = True


@pytest.fixture
def fake(monkeypatch, tmp_path):
    state = types.SimpleNamespace(init=None, lm_init=None, calls=[], cache_limit=None, cwd=None,
                                  timeout_at_import=None, fail_load=None, during_step=None,
                                  align_calls=[], align_result="ok")
    hooks = types.ModuleType("acestep._hooks")
    hooks.TIMEOUT_S = 1800
    hooks.on_step = lambda done, total: None
    hooks.on_tokens = lambda done, total: None
    pkg = types.ModuleType("acestep")
    pkg._hooks = hooks

    class Handler:
        def initialize_service(self, **kwargs):
            state.init = kwargs
            state.timeout_at_import = hooks.TIMEOUT_S
            if state.fail_load:
                return state.fail_load, False
            return "ok", True

        def _decode_generate_music_pred_latents(self, latents):
            return latents

        def get_lyric_timestamp(self, **kwargs):
            state.align_calls.append(kwargs)
            if state.align_result == "raise":
                raise RuntimeError("attention failed")
            if state.align_result == "fail":
                return {"success": False, "error": "no attentions", "sentence_timestamps": []}
            stamp = types.SimpleNamespace
            return {"success": True, "error": None, "sentence_timestamps": [
                stamp(text="[Verse]", start=0.0, end=0.4, confidence=0.1),
                stamp(text="hello", start=1.25, end=3.5, confidence=0.9)]}

    class LLM:
        def initialize(self, *args, **kwargs):
            state.lm_init = (args, kwargs)
            return "ok", True

    class Config:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Params(Config):
        pass

    def generate_music(handler, lm, params, config, save_dir=None):
        import os
        state.cwd = os.getcwd()
        state.calls.append((params, config, save_dir))
        if params.thinking:
            hooks.on_tokens(16, 160)
        for k in range(params.inference_steps):
            if state.during_step:
                state.during_step(k)
            hooks.on_step(k, params.inference_steps)
        handler._decode_generate_music_pred_latents(None)
        frames = int(params.duration * 48000)
        audio = np.stack([np.full(frames, 0.25), np.full(frames, -0.25)])
        return types.SimpleNamespace(
            success=True, error=None, status_message="",
            audios=[{"tensor": Tensor(audio), "sample_rate": 48000}],
            extra_outputs={"pred_latents": Tensor(np.ones((1, 25 * params.duration, 64))),
                           "encoder_hidden_states": "ehs", "encoder_attention_mask": "eam",
                           "context_latents": "ctx", "lyric_token_idss": "ids",
                           "lm_metadata": {"bpm": 90}, "time_costs": {"dit_total_time_cost": 1.5}})

    handler_mod = types.ModuleType("acestep.handler")
    handler_mod.AceStepHandler = Handler
    llm_mod = types.ModuleType("acestep.llm_inference")
    llm_mod.LLMHandler = LLM
    inference = types.ModuleType("acestep.inference")
    inference.GenerationParams = Params
    inference.GenerationConfig = Config
    inference.generate_music = generate_music
    torch = types.ModuleType("torch")
    torch.bfloat16 = "bf16"
    mlx = types.ModuleType("mlx")
    core = types.ModuleType("mlx.core")
    core.set_cache_limit = lambda n: setattr(state, "cache_limit", n)
    mlx.core = core
    for name, mod in {"acestep": pkg, "acestep._hooks": hooks, "acestep.handler": handler_mod,
                      "acestep.llm_inference": llm_mod, "acestep.inference": inference,
                      "torch": torch, "mlx": mlx, "mlx.core": core}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.chdir(tmp_path)
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "ACESTEP_PROJECT_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    state.hooks = hooks
    return state


def _request(tmp_path, **kw):
    job = tmp_path / "job"
    job.mkdir(exist_ok=True)
    base = dict(project_root=str(tmp_path / "models/ace-step"), model_folder="acestep-v15-turbo",
                job_dir=str(job), caption="calm folk", lyrics="[Verse]\nhello", vocal_language="en",
                duration=10, steps=3, seeds=[7, 8])
    base.update(kw)
    return MusicRequest(**base)


def _serve(request, go=None, cancel=None):
    conn = Connection()
    if go is None:
        go = threading.Event()
        go.set()
    music_worker._serve(conn, request, go, cancel or threading.Event())
    assert conn.closed
    return conn.events


def test_job_loads_bf16_and_writes_every_take(fake, tmp_path):
    request = _request(tmp_path, estimate_s=1000)
    events = _serve(request)
    assert events[0] == ("phase", "load", None)
    assert events[-1] == ("done",)
    assert fake.init["dtype"] == "bf16" and fake.init["device"] == "mps"
    assert fake.init["use_mlx_dit"] is True and fake.init["config_path"] == "acestep-v15-turbo"
    assert fake.init["project_root"] == request.project_root
    assert fake.cache_limit == 1 << 30
    assert fake.timeout_at_import == 3000
    assert fake.cwd == request.job_dir
    assert fake.lm_init is None
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["ACESTEP_PROJECT_ROOT"] == request.project_root
    assert [(c.batch_size, c.seeds, c.use_random_seed, save) for _, c, save in fake.calls] == \
        [(1, [7], False, None), (1, [8], False, None)]
    assert [e[1] for e in events if e[0] == "step"] == [0, 1, 2, 0, 1, 2]
    takes = [e for e in events if e[0] == "take"]
    assert [t[1] for t in takes] == [0, 1]
    for index, path, meta in (t[1:] for t in takes):
        info = sf.info(path)
        assert (info.samplerate, info.channels, info.subtype, info.duration) == (48000, 2, "PCM_16", 10)
        with sf.SoundFile(path) as f:
            assert f.comment == music_worker.COMMENT
        sidecar = json.loads((tmp_path / "job" / f"take-{index + 1}.json").read_text())
        assert sidecar == meta
        assert sidecar["seed"] == [7, 8][index]
        latents = np.load(tmp_path / "job" / sidecar["repaint_source_latents_file"])
        assert latents.shape == (250, 64)
        assert sidecar["lm_metadata"] == {"bpm": 90}


def test_render_phase_is_reported_before_decode(fake, tmp_path):
    events = _serve(_request(tmp_path, seeds=[1]))
    kinds = [e[:2] for e in events]
    assert kinds.index(("phase", "render")) > max(i for i, e in enumerate(events) if e[0] == "step")
    assert kinds.index(("phase", "write")) > kinds.index(("phase", "render"))


def test_planner_and_mps_dit(fake, tmp_path):
    events = _serve(_request(tmp_path, planner=True, dit_backend="mps", seeds=[1]))
    assert fake.init["use_mlx_dit"] is False
    args, kwargs = fake.lm_init
    assert args == (str(tmp_path / "models/ace-step/checkpoints"), "acestep-5Hz-lm-1.7B")
    assert kwargs == {"backend": "mlx", "device": "mps"}
    params = fake.calls[0][0]
    assert params.thinking and params.use_cot_metas and not params.use_cot_caption
    assert ("tokens", 16, 160) in events
    assert ("phase", "plan", 0) in events


def test_instrumental_ignores_lyrics(fake, tmp_path):
    _serve(_request(tmp_path, instrumental=True, seeds=[1]))
    params = fake.calls[0][0]
    assert params.lyrics == "[Instrumental]" and params.vocal_language == "unknown"


def test_sentence_timestamps_are_saved_with_the_shown_lyrics(fake, tmp_path):
    events = _serve(_request(tmp_path, seeds=[5], lyrics="[Verse]\nhallo", shown_lyrics="[Verse]\nhello"))
    meta = next(e[3] for e in events if e[0] == "take")
    assert meta["lyrics"] == "[Verse]\nhallo" and meta["shown_lyrics"] == "[Verse]\nhello"
    assert meta["lyric_timestamps"] == [
        {"text": "[Verse]", "start": 0.0, "end": 0.4, "confidence": 0.1},
        {"text": "hello", "start": 1.25, "end": 3.5, "confidence": 0.9}]
    call = fake.align_calls[0]
    assert (call["seed"], call["inference_steps"], call["vocal_language"]) == (5, 3, "en")
    assert call["total_duration_seconds"] == 10 and call["lyric_token_ids"] == "ids"


@pytest.mark.parametrize("outcome", ["fail", "raise"])
def test_failed_alignment_keeps_the_take_without_timestamps(fake, tmp_path, outcome):
    fake.align_result = outcome
    events = _serve(_request(tmp_path, seeds=[5]))
    assert events[-1] == ("done",)
    assert next(e[3] for e in events if e[0] == "take")["lyric_timestamps"] is None


def test_mlx_dit_without_torch_decoder_skips_alignment(fake, tmp_path, monkeypatch):
    handler = sys.modules["acestep.handler"].AceStepHandler
    monkeypatch.setattr(handler, "model", types.SimpleNamespace(decoder=None), raising=False)
    events = _serve(_request(tmp_path, seeds=[5]))
    assert fake.align_calls == []
    assert events[-1] == ("done",)
    assert next(e[3] for e in events if e[0] == "take")["lyric_timestamps"] is None


def test_instrumental_takes_skip_alignment(fake, tmp_path):
    events = _serve(_request(tmp_path, instrumental=True, seeds=[1]))
    assert fake.align_calls == []
    assert next(e[3] for e in events if e[0] == "take")["lyric_timestamps"] is None


def test_load_failure_is_reported(fake, tmp_path):
    fake.fail_load = "missing checkpoint"
    events = _serve(_request(tmp_path))
    assert events[-1] == ("error", "music:load_failed", "missing checkpoint")


def test_cancel_during_a_step_keeps_finished_takes(fake, tmp_path):
    cancel = threading.Event()
    go = threading.Event()
    go.set()

    def during(k):
        if len(fake.calls) == 2 and k == 1:
            cancel.set()

    fake.during_step = during
    events = _serve(_request(tmp_path), go=go, cancel=cancel)
    assert events[-1] == ("cancelled",)
    assert [e[1] for e in events if e[0] == "take"] == [0]
    assert (tmp_path / "job/take-1.wav").exists() and not (tmp_path / "job/take-2.wav").exists()


def test_gate_pauses_until_go_and_reports_once():
    sent = []
    go, cancel = threading.Event(), threading.Event()
    gate = music_worker.make_gate(sent.append, go, cancel)
    timer = threading.Timer(0.35, go.set)
    timer.start()
    gate()
    timer.join()
    assert sent == [("paused", "dictation")]
    gate()
    assert sent == [("paused", "dictation")]


def test_gate_raises_on_cancel_while_paused():
    go, cancel = threading.Event(), threading.Event()
    gate = music_worker.make_gate(lambda e: None, go, cancel)
    threading.Timer(0.2, cancel.set).start()
    with pytest.raises(music_worker.MusicCancelled):
        gate()


def test_cancel_escapes_upstream_exception_wrappers():
    assert not issubclass(music_worker.MusicCancelled, Exception)


def test_parent_gone_ends_quietly(fake, tmp_path):
    class Broken(Connection):
        def send(self, event):
            raise BrokenPipeError

    conn = Broken()
    go = threading.Event()
    go.set()
    music_worker._serve(conn, _request(tmp_path), go, threading.Event())
    assert conn.closed and not fake.calls


def test_take_seeds_and_timeout():
    assert music_worker.take_seeds(41, 3) == [41, 42, 43]
    assert music_worker.take_seeds(2**32 - 1, 2) == [2**32 - 1, 0]
    seeds = music_worker.take_seeds(None, 2)
    assert len(seeds) == 2 and all(0 <= s < 2**32 for s in seeds)
    assert music_worker.timeout_s(100) == 1800
    assert music_worker.timeout_s(900) == 2700


def test_worker_never_imports_upstream_cli_and_downloads_no_code():
    src = (music_worker.__file__)
    text = open(src, encoding="utf-8").read()
    assert "acestep.cli" not in text and "import cli" not in text
    for m in music.MUSIC_MODELS:
        for d in music.with_planner(m).downloads:
            assert not any(g.endswith(".py") for g in d.allow)


def test_repaint_params_and_source_metadata(fake, tmp_path):
    request = MusicRequest(project_root=str(tmp_path), model_folder='acestep-v15-turbo',
                           job_dir=str(tmp_path), caption='gentle folk', task_type='repaint',
                           src_audio='/original/take.wav', repainting_start=2.0, repainting_end=5.0,
                           artist_warnings=['Artist reference'])
    params = music_worker._params(request, 7)
    assert params.task_type == 'repaint' and params.src_audio == '/original/take.wav'
    assert params.repainting_start == 2.0 and params.repainting_end == 5.0
    assert params.repaint_mode == 'balanced' and not params.thinking
