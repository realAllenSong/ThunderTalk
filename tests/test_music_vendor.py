"""Verify the Phase 0 copy stays offline without importing torch or models."""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "third_party/acestep"


def load_file(name):
    spec = importlib.util.spec_from_file_location("music_spike_test", ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_precheck_requires_data_and_never_downloads(tmp_path):
    module = load_file("core/generation/handler/init_service_downloads.py")
    check = module.InitServiceDownloadsMixin()._ensure_models_present
    args = dict(checkpoint_path=tmp_path, config_path="acestep-v15-turbo", prefer_source=None)
    assert check(**args)[1] is False
    for name in ["acestep-v15-turbo", "vae", "Qwen3-Embedding-0.6B"]:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "model.safetensors").touch()
    assert check(**args) is None


def test_offline_paths_reject_unapproved_vae(tmp_path, monkeypatch):
    module = load_file("model_downloader.py")
    monkeypatch.setenv("ACESTEP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("ACESTEP_CHECKPOINTS_DIR", raising=False)
    assert module.get_checkpoints_dir() == tmp_path / "checkpoints"
    assert module.resolve_vae_path(tmp_path, "official") == tmp_path / "vae"
    with pytest.raises(ValueError, match="official"):
        module.resolve_vae_path(tmp_path, "/untrusted/vae")


@pytest.mark.parametrize("variant", ["turbo.modeling_acestep_v15_turbo", "xl_sft.modeling_acestep_v15_xl_base"])
def test_reloaded_model_keeps_weights_and_rebuilt_buffers(tmp_path, monkeypatch, variant):
    # Under transformers 5 the vendored _init_weights re-randomised every loaded weight and left
    # the rotary and FSQ buffers at zero: the DiT made sound but never sang the lyrics.
    pytest.importorskip("vector_quantize_pytorch")
    pytest.importorskip("loguru")
    import importlib

    torch = pytest.importorskip("torch")
    monkeypatch.syspath_prepend(str(ROOT.parent))
    modeling = importlib.import_module(f"acestep.models.{variant}")
    config = modeling.AceStepConfig(
        hidden_size=32, intermediate_size=64, num_attention_heads=2, num_key_value_heads=1, head_dim=16,
        num_lyric_encoder_hidden_layers=1, num_attention_pooler_hidden_layers=1, text_hidden_dim=16,
        audio_acoustic_hidden_dim=8)
    for cls in [modeling.AceStepLyricEncoder, modeling.AceStepAudioTokenizer]:
        original = cls(config)
        with torch.no_grad():
            for param in original.parameters():
                param.add_(0.5)
        folder = tmp_path / cls.__name__
        original.save_pretrained(folder)
        model = cls.from_pretrained(folder)
        for name, param in original.named_parameters():
            torch.testing.assert_close(model.get_parameter(name), param, msg=name)
        for name, buffer in original.named_buffers():
            torch.testing.assert_close(model.get_buffer(name), buffer, msg=name)
    expected = 1.0 / (config.rope_theta ** (torch.arange(0, 16, 2, dtype=torch.float32) / 16))
    torch.testing.assert_close(modeling.AceStepLyricEncoder.from_pretrained(
        tmp_path / "AceStepLyricEncoder").rotary_emb.inv_freq, expected)
    assert model.quantizer.layers[0]._levels.tolist() == list(config.fsq_input_levels)


def test_no_remote_code_or_network_imports_in_inference_copy():
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "trust_remote_code":
                        assert isinstance(keyword.value, ast.Constant) and keyword.value.value is False, path
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {"requests", "urllib.request", "modelscope"}, path
    assert not (ROOT / "cli.py").exists()
    assert not (ROOT / "ui").exists()
    assert not (ROOT / "core/generation/handler/lora").exists()


def test_mlx_conversion_materializes_each_tensor_and_preserves_layout(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    import numpy as np

    class Value:
        def __init__(self, array):
            self.array = array

        def detach(self):
            return self

        def cpu(self):
            return self

        def float(self):
            return self

        def numpy(self):
            return self.array

    pending = []

    class Array:
        def __init__(self, array):
            # A second fp32 source must not stay live across tensor conversions.
            assert not pending
            self.array = array.copy()
            self.dtype = None
            pending.append(self)

        def astype(self, dtype):
            self.dtype = dtype
            return self

    core = ModuleType('mlx.core')
    core.bfloat16 = 'bf16'
    core.array = Array

    def evaluate(value):
        assert pending == [value]
        pending.clear()

    core.eval = evaluate
    package = ModuleType('mlx')
    package.core = core
    monkeypatch.setitem(sys.modules, 'mlx', package)
    monkeypatch.setitem(sys.modules, 'mlx.core', core)
    values = {'proj_in.1.weight': np.arange(24).reshape(2, 3, 4),
              'proj_out.1.weight': np.arange(24).reshape(2, 3, 4),
              'norm.weight': np.arange(3), 'rotary_emb.inv_freq': np.arange(2)}
    model = SimpleNamespace(decoder=SimpleNamespace(state_dict=lambda: {k: Value(v) for k, v in values.items()}))
    result = dict(load_file('models/mlx/dit_convert.py').convert_decoder_weights(model))
    assert not pending and all(v.dtype == 'bf16' for v in result.values())
    assert set(result) == {'proj_in.weight', 'proj_out.weight', 'norm.weight'}
    np.testing.assert_array_equal(result['proj_in.weight'].array, values['proj_in.1.weight'].swapaxes(1, 2))
    np.testing.assert_array_equal(result['proj_out.weight'].array, values['proj_out.1.weight'].transpose(1, 2, 0))
    np.testing.assert_array_equal(result['norm.weight'].array, values['norm.weight'])


@pytest.mark.parametrize('defer, expected', [(True, 'cpu'), (False, 'mps')])
def test_mlx_load_defers_only_temporary_decoder_placement(monkeypatch, tmp_path, defer, expected):
    import os
    import sys
    from types import ModuleType, SimpleNamespace
    from typing import Optional

    class Tensor:
        def __init__(self):
            self.moves = []
            self.config = SimpleNamespace()

        def to(self, target):
            self.moves.append(target)
            return self

        def eval(self):
            return self

        def transpose(self, *args):
            return self

    model, silence = Tensor(), Tensor()
    fake = ModuleType('acestep.models.turbo.modeling_acestep_v15_turbo')
    fake.AceStepConditionGenerationModel = SimpleNamespace(from_pretrained=lambda *a, **k: model)
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False), load=lambda *a, **k: silence)
    tree = ast.parse((ROOT / 'core/generation/handler/init_service_loader.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_load_main_model_from_checkpoint')
    scope = dict(torch=torch, os=os, Optional=Optional, logger=SimpleNamespace(info=lambda *a: None))
    exec(compile(ast.Module(body=[method], type_ignores=[]), '<loader>', 'exec'), scope)
    (tmp_path / 'silence_latent.pt').touch()
    host = SimpleNamespace(dtype='bf16', _defer_mps_decoder=defer, offload_to_cpu=False,
                           offload_dit_to_cpu=False, _sync_alignment_config=lambda: None,
                           _apply_cuda_bool_argsort_workaround=lambda: None, _apply_dit_quantization=lambda q: None)
    scope['_load_main_model_from_checkpoint'](host, model_checkpoint_path=str(tmp_path), device='mps',
                                             use_flash_attention=False, compile_model=False, quantization=None)
    assert model.moves == [expected, 'bf16']
    assert silence.moves == ['mps', 'bf16']
