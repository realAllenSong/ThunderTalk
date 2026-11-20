"""A launch loads the model that is on disk; the pinned revision arrives in the background."""
import os
import time

import pytest

from thundertalk.core import models

REPO = "Oulasong/ThunderTalk-ASR-0.6B"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setitem(models.HF_REVISIONS, REPO, "v2")
    return tmp_path / "models--Oulasong--ThunderTalk-ASR-0.6B"


def _snapshot(repo, commit: str, ref: str | None = None, weights: bytes = b"w", age: float = 0.0):
    """A snapshot the way huggingface_hub lays it out: files are links into blobs/."""
    snap = repo / "snapshots" / commit
    snap.mkdir(parents=True)
    (repo / "blobs").mkdir(exist_ok=True)
    for name, data in (("config.json", b"{}"), ("model.safetensors", weights)):
        blob = repo / "blobs" / f"{commit}-{name}" if name != "config.json" else repo / "blobs" / "config"
        blob.write_bytes(data)
        (snap / name).symlink_to(blob)
    if ref:
        (repo / "refs").mkdir(exist_ok=True)
        (repo / "refs" / ref).write_text(commit)
    if age:
        os.utime(snap, (time.time() - age, time.time() - age))
    return snap


def test_nothing_cached_means_nothing_local_and_no_update(cache):
    assert models.hf_local_snapshot(REPO) is None
    assert models.hf_pinned_snapshot(REPO) is None
    assert not models.hf_update_pending(REPO)          # not downloaded: the Models page downloads it, not the updater


def test_an_older_snapshot_loads_while_the_pinned_one_is_pending(cache):
    old = _snapshot(cache, "aaa", ref="main")
    assert models.hf_local_snapshot(REPO) == old
    assert models.hf_update_pending(REPO)


def test_the_pinned_revision_wins_over_a_newer_looking_snapshot(cache):
    pinned = _snapshot(cache, "bbb", ref="v2", age=3600)
    _snapshot(cache, "ccc", ref="main")
    assert models.hf_local_snapshot(REPO) == pinned
    assert not models.hf_update_pending(REPO)


def test_a_half_downloaded_pinned_revision_is_not_used(cache):
    old = _snapshot(cache, "aaa", ref="main")
    pinned = _snapshot(cache, "bbb", ref="v2")
    (cache / "blobs" / "bbb-model.safetensors").unlink()       # the link dangles: weights not there yet
    assert models.hf_pinned_snapshot(REPO) is None
    assert models.hf_local_snapshot(REPO) == old
    assert models.hf_update_pending(REPO)
    assert pinned.exists()


def test_a_repo_without_a_pin_loads_its_newest_snapshot_and_never_updates(cache, tmp_path):
    repo = tmp_path / "models--Qwen--Qwen3-ASR-0.6B"
    _snapshot(repo, "old", age=3600)
    new = _snapshot(repo, "new")
    assert models.hf_local_snapshot("Qwen/Qwen3-ASR-0.6B") == new
    assert not models.hf_update_pending("Qwen/Qwen3-ASR-0.6B")


def test_update_fetches_the_pinned_revision_and_removes_the_old_weights(cache, monkeypatch):
    _snapshot(cache, "aaa", ref="main", weights=b"old-weights")
    asked = {}

    def fake_download(repo_id, local_dir, allow, ignore, progress_cb, cancel, revision=None):
        asked.update(repo=repo_id, revision=revision, allow=allow)
        _snapshot(cache, "bbb", ref="v2", weights=b"new-weights")

    monkeypatch.setattr(models, "_hf_snapshot", fake_download)
    assert models.hf_update(REPO) is True
    assert asked == {"repo": REPO, "revision": "v2", "allow": models._MLX_ALLOW}
    assert [d.name for d in (cache / "snapshots").iterdir()] == ["bbb"]
    assert sorted(b.name for b in (cache / "blobs").iterdir()) == ["bbb-model.safetensors", "config"]   # shared config kept
    assert models.hf_local_snapshot(REPO).name == "bbb"
    assert (models.hf_local_snapshot(REPO) / "model.safetensors").read_bytes() == b"new-weights"
    assert not models.hf_update_pending(REPO)
    assert models.hf_update(REPO) is False             # nothing left to do


def test_a_failed_update_leaves_the_old_model_in_place(cache, monkeypatch):
    old = _snapshot(cache, "aaa", ref="main")

    def offline(*_a, **_k):
        raise OSError("no network")

    monkeypatch.setattr(models, "_hf_snapshot", offline)
    with pytest.raises(OSError):
        models.hf_update(REPO)
    assert models.hf_local_snapshot(REPO) == old
    assert (old / "model.safetensors").exists()


def test_first_download_asks_for_the_pinned_revision_and_falls_back_to_main(cache, monkeypatch):
    calls = []

    class RevisionNotFoundError(Exception):
        pass

    def fake_download(repo_id, local_dir, allow, ignore, progress_cb, cancel, revision=None):
        calls.append(revision)
        if revision:
            raise RevisionNotFoundError("404")          # the tag is uploaded together with the release
        _snapshot(cache, "aaa", ref="main")

    monkeypatch.setattr(models, "_hf_snapshot", fake_download)
    models.download_model(models.find(models.TUNED_QWEN_ID))
    assert calls == ["v2", None]
    assert models._hf_cache_has(REPO)


def test_the_engine_loads_the_cached_snapshot_without_asking_the_hub(cache, monkeypatch):
    from thundertalk.core import asr
    snap = _snapshot(cache, "bbb", ref="v2")
    seen = []
    model = type("Model", (), {"audio_tower": staticmethod(lambda mel, lens: (mel, lens))})()
    monkeypatch.setattr(asr, "load_qwen_mlx", lambda source: seen.append(source) or model)
    monkeypatch.setattr(asr, "evaluate_model", lambda model: None)
    eng = asr.AsrEngine()
    eng._load_mlx_qwen3("hf://" + REPO)
    assert seen == [str(snap)]
    assert eng._model_id == "ThunderTalk-ASR-0.6B"      # the catalog still recognises it by repository
