"""Downloader + registry behaviour. No internet: a local HTTP server stands in
for GitHub, and the HuggingFace cache is faked on disk."""

from __future__ import annotations

import http.server
import shutil
import threading
import time
from pathlib import Path

import pytest

from thundertalk.core import models as m


# ── local HTTP server ────────────────────────────────────────────────────

class _SlowHandler(http.server.BaseHTTPRequestHandler):
    TOTAL = 24 * 1024 * 1024
    CHUNK = 256 * 1024

    def log_message(self, *a):  # keep pytest output clean
        pass

    def _headers(self):
        self.send_response(200)
        self.send_header("Content-Length", str(self.TOTAL))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

    def do_HEAD(self):
        self._headers()

    def do_GET(self):
        self._headers()
        sent = 0
        try:
            while sent < self.TOTAL:
                self.wfile.write(b"x" * self.CHUNK)
                sent += self.CHUNK
                time.sleep(0.02)          # ~12 MB/s → ~2 s for the whole body
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/blob.bin"
    srv.shutdown()


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")
def test_content_length(server) -> None:
    assert m._content_length(server) == _SlowHandler.TOTAL


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")
def test_curl_download_reports_monotonic_progress(server, tmp_path) -> None:
    events = []
    dest = tmp_path / "blob.bin"
    m._curl_download(server, dest, lambda p, msg: events.append((p, msg)), None, 5, 82)
    assert dest.stat().st_size == _SlowHandler.TOTAL
    pcts = [e[0] for e in events]
    assert len(pcts) >= 3 and pcts == sorted(pcts)
    assert 5 <= pcts[0] <= pcts[-1] <= 82
    assert "MB" in events[-1][1]


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")
def test_curl_download_cancel_is_prompt_and_cleans_partial_file(server, tmp_path) -> None:
    cancel = threading.Event()
    dest = tmp_path / "blob.bin"
    threading.Timer(0.6, cancel.set).start()
    t0 = time.time()
    with pytest.raises(m.DownloadCancelled):
        m._curl_download(server, dest, None, cancel, 5, 82)
    assert time.time() - t0 < 2.0            # well before the ~2 s full download
    assert not dest.exists()                 # partial file removed


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")
def test_curl_failure_raises_and_leaves_nothing(tmp_path) -> None:
    dest = tmp_path / "x.bin"
    with pytest.raises(RuntimeError):
        m._curl_download("http://127.0.0.1:9/nothing", dest, None, None, 5, 82)
    assert not dest.exists()


# ── HuggingFace cache detection ──────────────────────────────────────────

def _fake_hf_snapshot(root: Path, repo: str, *, config: bool, weights: bool) -> None:
    snap = root / ("models--" + repo.replace("/", "--")) / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    blob = root / "blob"
    blob.write_bytes(b"w")
    if config:
        (snap / "config.json").write_text("{}")
    if weights:
        (snap / "model.safetensors").symlink_to(blob)


def test_hf_cache_has_requires_config_and_weights(tmp_path, monkeypatch) -> None:
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    assert not m._hf_cache_has("Qwen/Qwen3-ASR-0.6B")             # nothing at all

    _fake_hf_snapshot(tmp_path, "Qwen/Qwen3-ASR-0.6B", config=True, weights=False)
    assert not m._hf_cache_has("Qwen/Qwen3-ASR-0.6B")             # config only

    (tmp_path / "models--Qwen--Qwen3-ASR-0.6B" / "snapshots" / "abc123" / "model.safetensors").symlink_to(
        tmp_path / "blob")
    assert m._hf_cache_has("Qwen/Qwen3-ASR-0.6B")


def test_broken_symlink_counts_as_incomplete(tmp_path, monkeypatch) -> None:
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    snap = tmp_path / "models--Qwen--Qwen3-ASR-0.6B" / "snapshots" / "rev"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")
    (snap / "model.safetensors").symlink_to(tmp_path / "does-not-exist")   # half-written blob
    assert not m._hf_cache_has("Qwen/Qwen3-ASR-0.6B")


def test_mlx_is_downloaded_reflects_cache_not_always_true(tmp_path, monkeypatch) -> None:
    """Regression: is_downloaded() returned True for every MLX model, so a
    fresh install showed 'Activate' and the multi-GB download happened
    invisibly inside load_model()."""
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    assert m.is_downloaded("qwen3-asr-06b-mlx") is False
    _fake_hf_snapshot(tmp_path, "Qwen/Qwen3-ASR-0.6B", config=True, weights=True)
    assert m.is_downloaded("qwen3-asr-06b-mlx") is True


def test_leftover_directory_is_not_a_finished_download(tmp_path, monkeypatch) -> None:
    """A cancelled/failed attempt leaves models/<id>/ behind; the next Download
    must not be short-circuited by the directory merely existing."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    info = next(x for x in m.BUILTIN_MODELS if x.id == "sensevoice-small-int8")
    (m.get_models_dir() / info.id).mkdir(parents=True)
    assert not m.is_downloaded(info.id)


# ── registry sanity ──────────────────────────────────────────────────────

def test_registry_ids_are_unique_and_sizes_positive() -> None:
    ids = [x.id for x in m.BUILTIN_MODELS]
    assert len(ids) == len(set(ids))
    assert all(x.size_mb > 0 for x in m.BUILTIN_MODELS)


def test_seamless_download_is_filtered_to_safetensors() -> None:
    """The HF repo also holds ~20 GB of fairseq .pt checkpoints the
    transformers loader never reads; without the filter the download was
    29.9 GB instead of ~9.3 GB."""
    info = next(x for x in m.BUILTIN_MODELS if x.id == "seamless-m4t-v2-large")
    assert "*.safetensors" in info.hf_allow
    assert not any(".pt" in g for g in info.hf_allow)


def test_sensevoice_uses_the_small_int8_archive() -> None:
    info = next(x for x in m.BUILTIN_MODELS if x.id == "sensevoice-small-int8")
    assert "-int8-" in info.download_url and info.size_mb < 300


class _RangeHandler(_SlowHandler):
    starts = []

    def do_GET(self):
        start = int(self.headers.get("Range", "bytes=0-").split("=")[1].rstrip("-"))
        self.starts.append(start)
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(self.TOTAL - start))
        if start:
            self.send_header("Content-Range", f"bytes {start}-{self.TOTAL - 1}/{self.TOTAL}")
        self.end_headers()
        try:
            for pos in range(start, self.TOTAL, self.CHUNK):
                self.wfile.write(b"x" * min(self.CHUNK, self.TOTAL - pos))
                time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError):
            pass


def test_component_download_retains_partial_and_resumes(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/wheel"
    dest = tmp_path / "wheel"
    stop = threading.Event()
    timer = threading.Timer(0.5, stop.set)
    timer.start()
    try:
        with pytest.raises(m.DownloadCancelled):
            m._curl_download(url, dest, None, stop, 0, 90, resume=True, total_bytes=_RangeHandler.TOTAL)
        partial = dest.stat().st_size
        assert 0 < partial < _RangeHandler.TOTAL
        events = []
        m._curl_download(url, dest, lambda p, msg: events.append(p), None, 0, 90,
                         resume=True, total_bytes=_RangeHandler.TOTAL)
        assert _RangeHandler.starts[-1] == partial
        assert dest.stat().st_size == _RangeHandler.TOTAL
        assert events == sorted(events)
    finally:
        timer.cancel()
        srv.shutdown()
