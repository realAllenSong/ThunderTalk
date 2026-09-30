"""Update install: extraction happens off the UI thread (prepare_update),
and the helper swaps the bundle by rename before relaunching."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from thundertalk.core import updater

pytestmark = pytest.mark.skipif(shutil.which("ditto") is None, reason="macOS ditto only")


def _fake_zip(tmp_path, executable=True):
    app = tmp_path / "src" / "ThunderTalk.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    binary = app / "ThunderTalk"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755 if executable else 0o644)
    z = tmp_path / "ThunderTalk-v9.9.9-macOS.zip"
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(tmp_path / "src" / "ThunderTalk.app"), str(z)],
                   check=True)
    return z


def test_prepare_extracts_checks_and_cleans(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setattr(updater, "_CACHE_DIR", cache)
    stale = cache / "thundertalk-update-old"
    stale.mkdir(parents=True)
    z = _fake_zip(tmp_path)

    new_app = updater.prepare_update(z)

    assert new_app.name == "ThunderTalk.app" and new_app.parent.parent == cache
    assert os.access(new_app / "Contents" / "MacOS" / "ThunderTalk", os.X_OK)
    assert not z.exists() and not stale.exists()


def test_prepare_refuses_a_bundle_without_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_CACHE_DIR", tmp_path / "cache")
    with pytest.raises(RuntimeError, match="not \\+x"):
        updater.prepare_update(_fake_zip(tmp_path, executable=False))
    assert not list((tmp_path / "cache").glob("thundertalk-update-*"))


def test_install_writes_a_rename_first_helper_and_returns_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_CACHE_DIR", tmp_path / "cache")
    new_app = updater.prepare_update(_fake_zip(tmp_path))
    spawned = []
    monkeypatch.setattr(updater, "subprocess", type("S", (), {
        "Popen": staticmethod(lambda args, **kw: spawned.append(args)),
        "DEVNULL": subprocess.DEVNULL}))

    updater.install_update(new_app, tmp_path / "Applications" / "ThunderTalk.app")

    helper = new_app.parent / "install.sh"
    script = helper.read_text()
    assert spawned == [["/bin/bash", str(helper)]]
    assert subprocess.run(["bash", "-n", str(helper)]).returncode == 0
    # Rename first, ditto only as fallback, relaunch before the slow checks.
    assert script.index('mv "$NEW" "$OLD"') < script.index('ditto "$NEW" "$OLD"')
    assert script.index('open "$OLD"\n# Housekeeping') < script.index("codesign --verify")
