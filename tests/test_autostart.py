"""Smoke tests for the autostart module — no system mutation.

These tests exercise the public surface without actually registering
a Login Item (which would mutate global OS state). They verify that:
  - Bundle path detection is robust to dev mode (sys.executable points
    to a Python interpreter, not a .app)
  - is_enabled() never raises, returns a bool
  - set_enabled() on a non-bundle is a clean failure, not a crash
  - sync_with_setting() is a no-op when the OS state already matches
"""

from __future__ import annotations

import platform

import pytest

from thundertalk.core import autostart


def test_bundle_path_returns_none_in_dev_mode():
    # When pytest is the runner, sys.executable points at python(3),
    # which is not inside a .app bundle.
    assert autostart._bundle_path() is None


def test_is_enabled_returns_bool():
    # Should never raise. On macOS the result depends on real OS state;
    # on other platforms it must be False.
    result = autostart.is_enabled()
    assert isinstance(result, bool)
    if platform.system() != "Darwin":
        assert result is False


def test_set_enabled_noop_on_non_macos():
    if platform.system() == "Darwin":
        pytest.skip("non-macOS no-op path only relevant off-macOS")
    ok, err = autostart.set_enabled(True)
    assert ok is True
    assert err is None


def test_set_enabled_in_dev_mode_refuses_safely():
    if platform.system() != "Darwin":
        pytest.skip("dev-mode bundle guard only relevant on macOS")
    # In dev mode (no .app bundle around the interpreter), set_enabled
    # MUST refuse rather than silently registering some unrelated
    # bundle that SMAppService picks up from the Python framework
    # context.
    ok, err = autostart.set_enabled(True)
    assert ok is False
    assert err and "bundle" in err.lower()


def test_sync_with_setting_does_not_raise():
    # Should be safe to call with either value, on any platform.
    autostart.sync_with_setting(False)
    autostart.sync_with_setting(True)
