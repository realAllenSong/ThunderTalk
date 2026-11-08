"""IndexTTS downloads every file its port reads, and repairs partial snapshots."""
from pathlib import Path

from thundertalk.core import models, speech
from thundertalk.core.tts_backends import indextts


def _snapshot(root: Path, files) -> Path:
    snap = root / "snap"
    snap.mkdir(parents=True, exist_ok=True)
    for f in files:
        (snap / f).write_bytes(b"x")
    return snap


def test_required_files_cover_what_the_port_loads():
    needed = set(indextts._NEEDED)
    assert {"config.yaml", "wav2vec2bert_stats.pt", "feat1.pt", "feat2.pt",
            "multilingual_zh_ja_yue_char_del.tiktoken"} <= needed
    main, w2v = indextts.IndexTTSBackend.info.downloads
    assert main.required == indextts._NEEDED and w2v.required == indextts._W2V_NEEDED
    assert {"*.yaml", "*.pt", "*.tiktoken"} <= set(main.allow)


def test_partial_snapshot_is_not_complete_and_is_refetched(monkeypatch, tmp_path):
    # What the old generic filter left behind: json + safetensors only.
    snap = _snapshot(tmp_path, ["config.json", "gpt.safetensors", "s2mel.safetensors",
                                "bigvgan.safetensors", "codec.safetensors", "model_manifest.json"])
    monkeypatch.setattr(models, "hf_snapshot_dir", lambda repo: snap)
    main = indextts.IndexTTSBackend.info.downloads[0]
    assert not speech.download_ready(main)
    fetched = []
    monkeypatch.setattr(models, "_hf_snapshot",
                        lambda repo, local, allow, ignore, cb, cancel: fetched.append(allow))
    models.download_repo(main.source, lambda r: models.hf_snapshot_has(r, main.required), None, None,
                         allow=(*models._MLX_ALLOW, *main.allow))
    assert fetched and {"*.yaml", "*.pt", "*.tiktoken", "*.json"} <= set(fetched[0])
    _snapshot(tmp_path, indextts._NEEDED)
    assert speech.download_ready(main)


def test_other_hf_backends_keep_the_generic_filter(monkeypatch):
    fetched = []
    monkeypatch.setattr(models, "_hf_cache_has", lambda repo: False)
    monkeypatch.setattr(models, "_hf_snapshot",
                        lambda repo, local, allow, ignore, cb, cancel: fetched.append(allow))
    models.download_repo("mlx-community/VoxCPM2-8bit")
    assert fetched == [models._MLX_ALLOW]
