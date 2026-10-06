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
