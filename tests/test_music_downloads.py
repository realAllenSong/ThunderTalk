"""hf-dir downloads, runtime components and disk checks used by the music models."""

from __future__ import annotations

import errno
import shutil
from collections import namedtuple

import pytest

from thundertalk.core import model_metadata, models, runtime, speech
from thundertalk.core.tts_backends.base import BackendInfo, Download

ACE = Download("hf-dir", "ACE-Step/Ace-Step1.5", dest="ace-step/checkpoints", size_mb=100,
               allow=("vae/*.json", "vae/*.safetensors"))


def _info(*downloads, components=()):
    return BackendInfo(id="m", name="m", blurb_en="", blurb_zh="", languages=(), supports_presets=False,
                       supports_clone=False, needs_gpu=True, downloads=downloads, components=components)


def test_hf_dir_downloads_allowed_files_into_models_folder_once(isolated_home, monkeypatch):
    calls = []

    def snapshot(repo, local_dir, allow, ignore, cb, cancel):
        calls.append((repo, local_dir, allow))
        (local_dir / "vae").mkdir(parents=True)
        (local_dir / "vae" / "config.json").write_text("{}")
        cb(100, "done")

    monkeypatch.setattr(models, "_hf_snapshot", snapshot)
    speech.download_backend(_info(ACE))
    target = isolated_home / ".thundertalk" / "models" / "ace-step" / "checkpoints"
    assert calls == [("ACE-Step/Ace-Step1.5", target, ["vae/*.json", "vae/*.safetensors"])]
    assert speech.download_ready(ACE) and (target / "vae" / "config.json").is_file()
    speech.download_backend(_info(ACE))
    assert len(calls) == 1


def test_interrupted_hf_dir_is_not_ready(isolated_home, monkeypatch):
    def snapshot(*args):
        raise models.DownloadCancelled()

    monkeypatch.setattr(models, "_hf_snapshot", snapshot)
    with pytest.raises(models.DownloadCancelled):
        speech.download_backend(_info(ACE))
    assert not speech.download_ready(ACE)


@pytest.mark.parametrize("allow", [(), ("*.py",), ("*",), ("dit/*.json", "dit/modeling.py")])
def test_hf_dir_refuses_code_or_whole_repo(isolated_home, monkeypatch, allow):
    monkeypatch.setattr(models, "_hf_snapshot", lambda *a: pytest.fail("downloaded"))
    with pytest.raises(ValueError):
        speech.download_backend(_info(Download("hf-dir", "org/r", dest="r", size_mb=1, allow=allow)))


def test_download_refused_without_disk_room(isolated_home, monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(0, 0, 119 * 1_000_000))
    monkeypatch.setattr(models, "_hf_snapshot", lambda *a: pytest.fail("downloaded"))
    with pytest.raises(OSError) as err:
        speech.download_backend(_info(ACE))
    assert err.value.errno == errno.ENOSPC


def test_components_install_before_files(isolated_home, monkeypatch):
    seen = []
    monkeypatch.setattr(runtime, "needed", lambda: True)
    monkeypatch.setattr(runtime, "install", lambda *a: seen.append("torch"))
    monkeypatch.setattr(models, "_hf_snapshot", lambda *a: seen.append("files"))
    speech.download_backend(_info(ACE, components=("torch",)))
    assert seen == ["torch", "files"]
    assert speech.backend("indextts").info.components == ("torch",)


def test_unknown_component_rejected(isolated_home):
    with pytest.raises(ValueError):
        speech.download_backend(_info(ACE, components=("nope",)))


def test_acestep_languages_match_vendored_constants():
    from pathlib import Path
    src = (Path(__file__).parents[1] / "third_party" / "acestep" / "constants.py").read_text()
    block = src.split("VALID_LANGUAGES = [", 1)[1].split("]", 1)[0]
    vendored = {c.strip().strip("'\"") for c in block.split(",") if c.strip()} - {"unknown"}
    assert set(model_metadata.ACESTEP_LANGUAGES) == vendored
    assert len(model_metadata.ACESTEP_LANGUAGES) == 50 == len(set(model_metadata.ACESTEP_LANGUAGES))
    assert model_metadata.ACESTEP_LANGUAGES[:2] == ("zh", "en")
