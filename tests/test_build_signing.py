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


def _fake_app(tmp_path):
    app = tmp_path / "Test.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/MacOS/ThunderTalk").write_bytes(b"\xcf\xfa\xed\xfe main")
    lib = app / "Contents/Frameworks/lib/libx.dylib"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"\xcf\xfa\xed\xfe lib")
    (app / "Contents/Frameworks/Qt.framework").mkdir()
    (app / "Contents/Resources").mkdir()
    (app / "Contents/Resources/data.txt").write_text("not code")
    return app


def test_developer_id_is_preferred_and_signs_inside_out_with_timestamps(signing, monkeypatch, tmp_path):
    app = _fake_app(tmp_path)
    dev = "Developer ID Application: Example (TEAM123456)"
    monkeypatch.setattr(build, "signing_identities", lambda: {dev: "B" * 40, build.LOCAL_IDENTITY: "A" * 40})
    assert build.sign_app(str(app)) == dev
    signs = [c for c in signing if c[:2] == ["codesign", "--force"]]
    targets = [c[-1] for c in signs]
    assert targets == [str(app / "Contents/Frameworks/lib/libx.dylib"),
                       str(app / "Contents/Frameworks/Qt.framework"), str(app)]
    assert all("--timestamp" in c and "runtime" in c and "--deep" not in c for c in signs)
    assert all("--requirements" not in c for c in signs)  # Apple's team-based requirement
    assert "--entitlements" in signs[-1] and "--entitlements" not in signs[0]
    assert ["codesign", "--verify", "--deep", "--strict", str(app)] in signing


def test_notarize_uses_keychain_profile_then_staples(signing, monkeypatch, tmp_path):
    monkeypatch.setattr(build.os, "remove", lambda path: None)
    build.notarize("Test.app")
    submit = next(c for c in signing if c[:3] == ["xcrun", "notarytool", "submit"])
    assert submit[submit.index("--keychain-profile") + 1] == build.NOTARY_PROFILE and "--wait" in submit
    assert ["xcrun", "stapler", "staple", "Test.app"] in signing
