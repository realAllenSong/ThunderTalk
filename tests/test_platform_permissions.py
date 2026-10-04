import sys

import pytest

from thundertalk.core import platform_utils as pu


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_objc_mic_status_is_a_known_value() -> None:
    """The ctypes AVFoundation call must work without pyobjc's AVFoundation
    binding (which is not a dependency) instead of silently reporting
    'authorized' for everyone."""
    status = pu._mic_status_via_objc()
    assert status in ("not_determined", "restricted", "denied", "authorized")


def test_check_microphone_returns_known_value() -> None:
    assert pu.check_microphone() in ("not_determined", "restricted", "denied", "authorized")


def test_status_table_matches_avfoundation_enum() -> None:
    assert pu._MIC_STATUS == {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}


@pytest.mark.parametrize("code,expected", [(0, "not_determined"), (1, "restricted"),
                                           (2, "denied"), (3, "authorized"), (99, "unknown")])
def test_native_microphone_status_mapping(monkeypatch, code, expected):
    from types import SimpleNamespace
    monkeypatch.setattr(pu, "_SYSTEM", "Darwin")
    monkeypatch.setitem(sys.modules, "AVFoundation", SimpleNamespace(
        AVMediaTypeAudio="audio", AVCaptureDevice=SimpleNamespace(
            authorizationStatusForMediaType_=lambda _: code)))
    assert pu.check_microphone() == expected


def test_failed_native_check_does_not_claim_permission(monkeypatch):
    monkeypatch.setattr(pu, "_SYSTEM", "Darwin")
    monkeypatch.setitem(sys.modules, "AVFoundation", None)
    monkeypatch.setattr(pu, "_mic_status_via_objc", lambda: None)
    assert pu.check_microphone() == "unknown"


@pytest.mark.parametrize("service", ["Microphone", "Accessibility"])
@pytest.mark.parametrize("returncode", [0, 1])
def test_reset_is_app_scoped_and_requests_only_on_success(monkeypatch, service, returncode):
    import subprocess
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(pu, "_SYSTEM", "Darwin")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=returncode))
    kind = service.lower()
    monkeypatch.setattr(pu, "request_" + kind, lambda: calls.append("request"))
    monkeypatch.setattr(pu, "open_" + kind + "_settings", lambda: calls.append("settings"))
    assert getattr(pu, "reset_" + kind)() is (returncode == 0)
    assert calls == [["tccutil", "reset", service, "com.thundertalk.app"]] + (
        ["request", "settings"] if returncode == 0 else [])


def test_reset_failure_is_recoverable(monkeypatch):
    import subprocess
    monkeypatch.setattr(pu, "_SYSTEM", "Darwin")
    def fail(*a, **kw):
        raise subprocess.TimeoutExpired("tccutil", 5)
    monkeypatch.setattr(subprocess, "run", fail)
    assert not pu.reset_microphone()
