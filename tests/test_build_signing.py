"""Build signing commands are inspected without accessing a real keychain."""
from types import SimpleNamespace

import pytest

import build_macos as build


@pytest.fixture
def signing(monkeypatch):
    commands = []
    for key in ("SIGN_IDENTITY", "SIGN_REQUIREMENT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(build.subprocess, "run", lambda command, **kwargs:
                        commands.append(command) or SimpleNamespace(stdout="", returncode=0))
    return commands


def test_local_identity_uses_certificate_requirement_only_on_outer_bundle(signing, monkeypatch):
    digest = "A" * 40
    monkeypatch.setattr(build, "signing_identities", lambda: {build.LOCAL_IDENTITY: digest})
    assert build.sign_app("Test.app") == build.LOCAL_IDENTITY
    nested, outer, verify, display = signing
    assert "--deep" in nested and "--requirements" not in nested
    assert "--deep" not in outer
    assert outer[outer.index("--requirements") + 1] == (
        f'=designated => identifier "com.thundertalk.app" and certificate leaf = H"{digest}"')
    assert verify == ["codesign", "--verify", "--deep", "--strict", "Test.app"]
    assert display == ["codesign", "-dr", "-", "Test.app"]


def test_absent_identity_falls_back_to_ad_hoc_and_still_verifies(signing, monkeypatch):
    monkeypatch.setattr(build, "signing_identities", lambda: {})
    assert build.sign_app("Test.app") == "-"
    assert signing[0] == ["codesign", "--force", "--deep", "--sign", "-", "Test.app"]
    assert "--verify" in signing[1]


def test_explicit_missing_identity_fails_instead_of_silently_changing_signer(signing, monkeypatch):
    monkeypatch.setattr(build, "signing_identities", lambda: {})
    monkeypatch.setenv("SIGN_IDENTITY", "Missing")
    with pytest.raises(RuntimeError, match="unavailable"):
        build.sign_app()
    assert signing == []


def test_developer_identity_and_renewal_requirement_override(signing, monkeypatch):
    monkeypatch.setattr(build, "signing_identities", lambda: {"Developer ID Application: Example": "B" * 40})
    monkeypatch.setenv("SIGN_IDENTITY", "Developer ID Application: Example")
    monkeypatch.setenv("SIGN_REQUIREMENT", 'designated => identifier "com.thundertalk.app" and anchor apple generic')
    build.sign_app()
    assert signing[1][signing[1].index("--requirements") + 1].endswith("anchor apple generic")
