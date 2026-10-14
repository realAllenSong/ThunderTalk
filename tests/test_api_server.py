"""The HTTP server resolves models from the catalog (no model is loaded)."""
import pytest

pytest.importorskip("fastapi")
from thundertalk.api import server  # noqa: E402
from thundertalk.core import models  # noqa: E402


def test_model_id_resolves_to_catalog_entry_and_folder(monkeypatch, tmp_path):
    folder = tmp_path / "qwen3-asr-06b-int8"
    folder.mkdir()
    monkeypatch.setattr(models, "get_model_path", lambda mid: str(folder))
    info, path = server._resolve_model("qwen3-asr-06b-int8")
    assert info.id == "qwen3-asr-06b-int8" and info.backend == "onnx" and path == str(folder)


def test_model_folder_path_is_accepted(tmp_path):
    folder = tmp_path / "funasr-nano-int8"
    folder.mkdir()
    info, path = server._resolve_model(str(folder))
    assert info.id == "funasr-nano-int8" and path == str(folder)


def test_unknown_or_missing_models_explain_what_to_use(monkeypatch):
    with pytest.raises(SystemExit, match="model id"):
        server._resolve_model("nope")
    monkeypatch.setattr(models, "get_model_path", lambda mid: None)
    with pytest.raises(SystemExit, match="not downloaded"):
        server._resolve_model("qwen3-asr-06b-int8")
