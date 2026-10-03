"""Optional runtime: pinned integrity, atomicity, resume, and frozen UI wiring."""
from __future__ import annotations

import hashlib
import sys
import threading
import tomllib
import zipfile
from pathlib import Path

import pytest

from thundertalk.core import models, runtime


def test_manifest_matches_lock():
    packages = tomllib.loads(Path("uv.lock").read_text())["package"]
    for wheel in runtime._MANIFEST["wheels"]:
        p = next(p for p in packages if p["name"] == wheel["name"])
        assert p["version"] == wheel["version"]
        assert any(w["url"] == wheel["url"] and w["hash"] == "sha256:" + wheel["sha256"]
                   and w["size"] == wheel["size"] for w in p["wheels"])


@pytest.fixture
def component(tmp_path, monkeypatch):
    wheel = tmp_path / "component.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("fakepkg/__init__.py", "VALUE = 42\n")
        z.writestr("fakepkg-1.dist-info/METADATA", "Name: fakepkg\nVersion: 1\n")
    item = dict(name="fakepkg", version="1", url="https://example.invalid/component.whl",
                sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), size=wheel.stat().st_size)
    monkeypatch.setattr(runtime, "_MANIFEST", dict(id=runtime.RUNTIME_ID, wheels=[item]))
    monkeypatch.setattr(runtime, "root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(runtime, "activate", lambda: None)
    calls = []

    def fetch(url, dest, cb, cancel, lo, hi, **kw):
        calls.append(kw)
        dest.write_bytes(wheel.read_bytes())
        if cb:
            cb(hi, "bytes transferred")
    monkeypatch.setattr(models, "_curl_download", fetch)
    return item, calls


def test_install_is_atomic_and_idempotent(component):
    _, calls = component
    seen = []
    runtime.install(lambda pct, msg: seen.append((pct, runtime.installed())))
    assert runtime.installed() and not runtime.needed()
    assert calls == [dict(resume=True, total_bytes=component[0]["size"])]
    assert all(not installed for pct, installed in seen if pct < 100)
    assert seen[-1] == (100, True)
    runtime.install()
    assert len(calls) == 1
    assert not list(runtime.root().glob(".install-*"))


def test_corrupt_download_never_publishes(component):
    item, _ = component
    item["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum|校验"):
        runtime.install()
    assert not runtime.installed()
    assert not list(runtime.root().glob(".install-*"))
    assert not list(runtime.root().rglob("*.whl"))


def test_cancel_during_extraction_leaves_no_install(component, monkeypatch):
    stop = threading.Event()
    original = runtime._unpack

    def unpack(wheel, dest, cancel):
        stop.set()
        original(wheel, dest, cancel)
    monkeypatch.setattr(runtime, "_unpack", unpack)
    with pytest.raises(models.DownloadCancelled):
        runtime.install(cancel=stop)
    assert not runtime.installed()
    assert list(runtime.root().rglob("*.whl"))  # verified download kept for retry
    assert not list(runtime.root().glob(".install-*"))


@pytest.mark.parametrize("path", ["../escape", "/absolute", "x\\escape", "pkg.data/unknown/x"])
def test_reject_unsafe_or_unsupported_wheel(tmp_path, path):
    wheel = tmp_path / "bad.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr(path, "bad")
    with pytest.raises(ValueError):
        runtime._unpack(wheel, tmp_path / "out", None)


def test_source_environment_never_downloads(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(runtime, "installed", lambda: False)
    runtime.install()
    runtime.require()
    assert not runtime.needed()


def test_restart_required_after_transformers_initialized(component, monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", object())
    runtime.install()
    assert runtime.restart_needed()
    with pytest.raises(RuntimeError, match="Restart|重启"):
        runtime.require()


def test_translation_download_installs_runtime_even_with_cached_model(monkeypatch):
    info = next(i for i in models.BUILTIN_MODELS if i.backend == "seamless-torch")
    seen = []
    monkeypatch.setattr(runtime, "install", lambda *args: seen.append(args))
    monkeypatch.setattr(models, "is_downloaded", lambda _: True)
    models.download_model(info)
    assert seen == [(None, None)]


def test_index_download_installs_runtime_first(monkeypatch):
    from thundertalk.core import speech
    seen = []
    monkeypatch.setattr(runtime, "install", lambda *args: seen.append("runtime"))
    monkeypatch.setattr(models, "download_repo", lambda *args: seen.append("model"))
    speech.download_backend(speech.backend("indextts").info)
    assert seen == ["runtime", "model", "model"]


def test_translation_card_and_model_explain_component(qtbot, monkeypatch):
    from thundertalk.core.settings import Settings
    from thundertalk.ui.pages.models_page import TranslationModeCard, _blurb
    monkeypatch.setattr(runtime, "status", lambda: "Component: 90 MB")
    card = TranslationModeCard(Settings())
    qtbot.addWidget(card)
    assert "90 MB" in card._subtitle_lbl.text() and card._subtitle_lbl.wordWrap()
    card.retranslate()
    assert "90 MB" in card._subtitle_lbl.text()
    info = next(i for i in models.BUILTIN_MODELS if i.backend == "seamless-torch")
    assert "90 MB" in _blurb(info)
    monkeypatch.setattr(runtime, "status", lambda: "")
    card.set_translator_status("ready")
    assert "90 MB" not in card._subtitle_lbl.text()


def test_combined_index_progress_never_finishes_before_models(monkeypatch):
    from thundertalk.core import speech
    monkeypatch.setattr(runtime, "needed", lambda: True)
    def install(progress, cancel):
        progress(0, "component")
        progress(100, "component")
    def model(repo, ready, progress, cancel):
        progress(0, "model")
        progress(100, "model")
    monkeypatch.setattr(runtime, "install", install)
    monkeypatch.setattr(models, "download_repo", model)
    events = []
    speech.download_backend(speech.backend("indextts").info, lambda pct, msg: events.append((pct, msg)))
    assert [p for p, _ in events] == sorted(p for p, _ in events)
    assert events[1][0] < 100 and events[-1][0] == 100


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_index_card_shows_component_and_blocks_until_restart(qtbot, isolated_home, monkeypatch, lang):
    from thundertalk.core import i18n, speech
    from thundertalk.ui.studio.speak_tab import SpeakTab
    monkeypatch.setattr(i18n, "LANG", lang)
    monkeypatch.setattr(runtime, "needed", lambda: True)
    monkeypatch.setattr(runtime, "restart_needed", lambda: False)
    for b in speech.backends():
        monkeypatch.setattr(b, "is_ready", lambda: False)
    tab = SpeakTab()
    qtbot.addWidget(tab)
    tab._engine_id = "indextts"
    tab._voice_id = speech.backend("indextts").voices()[0].id
    tab._refresh()
    assert "PyTorch" in tab._engine_body.text() and "90 MB" in tab._engine_body.text()
    assert not tab._go.isEnabled()
    monkeypatch.setattr(runtime, "needed", lambda: False)
    monkeypatch.setattr(runtime, "restart_needed", lambda: True)
    monkeypatch.setattr(speech.backend("indextts"), "is_ready", lambda: True)
    tab._refresh()
    assert i18n.t("runtime.restart") in tab._engine_body.text()
    assert tab._dl_btn.isHidden() and not tab._engine_ready()


def test_concurrent_installers_publish_once(component, monkeypatch):
    import time
    fetch = models._curl_download
    def slow_fetch(*args, **kwargs):
        time.sleep(0.15)
        fetch(*args, **kwargs)
    monkeypatch.setattr(models, "_curl_download", slow_fetch)
    errors = []
    def run():
        try:
            runtime.install()
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert not errors and not any(t.is_alive() for t in threads)
    assert len(component[1]) == 1 and runtime.installed()


def test_waiting_for_install_lock_is_cancellable(component):
    import fcntl
    runtime.root().mkdir(parents=True)
    stop = threading.Event()
    with (runtime.root() / ".install.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        timer = threading.Timer(0.15, stop.set)
        timer.start()
        try:
            with pytest.raises(models.DownloadCancelled):
                runtime.install(cancel=stop)
        finally:
            timer.cancel()
    assert not runtime.installed() and not component[1]


def test_wheel_data_relocation(tmp_path):
    wheel = tmp_path / "data.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("pkg-1.data/data/share/man/man1/example.1", "manual")
        z.writestr("pkg-1.data/purelib/pkg/__init__.py", "VALUE = 1")
    target = tmp_path / "installed"
    runtime._unpack(wheel, target, None)
    assert (target / "share/man/man1/example.1").read_text() == "manual"
    assert (target / "pkg/__init__.py").read_text() == "VALUE = 1"


def test_cached_translation_component_does_not_prompt_for_model_gigabytes(monkeypatch):
    from types import SimpleNamespace
    from thundertalk.ui.pages import models_page as page
    monkeypatch.setattr(page, "is_downloaded", lambda _: True)
    seen = []
    fake = SimpleNamespace(_on_download=seen.append)
    page.ModelsPage._on_download_requested(fake, "seamless-m4t-v2-large")
    assert seen == ["seamless-m4t-v2-large"]
