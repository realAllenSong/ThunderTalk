"""Build and sign ThunderTalk.app.

Signing identity, in order: SIGN_IDENTITY (use '-' to force ad-hoc), a
"Developer ID Application" certificate in the keychain, ThunderTalk Local
Signing (tools/make_signing_identity.sh), else ad-hoc.

Developer ID builds sign every nested binary with the hardened runtime and a
secure timestamp, and keep Apple's team-based designated requirement so
macOS permissions survive certificate renewals. NOTARIZE=1 submits the app
with the notarytool keychain profile NOTARY_PROFILE (default
"ThunderTalk-notary") and staples the ticket.
The self-signed local identity pins its leaf certificate instead
(SIGN_REQUIREMENT overrides that requirement).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import re
from pathlib import Path
import subprocess
import sys
import time

LOCAL_IDENTITY = "ThunderTalk Local Signing"
APP_ID = "com.thundertalk.app"
APP_PATH = "dist/ThunderTalk.app"
ENTITLEMENTS = "entitlements.plist"
NOTARY_PROFILE = os.environ.get("NOTARY_PROFILE", "ThunderTalk-notary")
_MACHO = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def signing_identities() -> dict[str, str]:
    keychain = Path.home() / "Library/Keychains/ThunderTalkLocalSigning.keychain-db"
    password_file = Path.home() / ".thundertalk/signing/keychain-password"
    if keychain.is_file() and password_file.is_file():
        # Unlock only our dedicated keychain, never the user's login keychain.
        subprocess.run(["security", "unlock-keychain", "-p", password_file.read_text().strip(),
                        str(keychain)], check=True, capture_output=True)
    result = subprocess.run(["security", "find-identity", "-p", "codesigning"],
                            capture_output=True, text=True, check=True)
    return {name: digest for digest, name in re.findall(r'\b([A-Fa-f0-9]{40}) "([^"]+)"', result.stdout)}


def _choose_identity(identities: dict[str, str]) -> str:
    if os.environ.get("SIGN_IDENTITY"):
        return os.environ["SIGN_IDENTITY"]
    developer = sorted(n for n in identities if n.startswith("Developer ID Application:"))
    if developer:
        return developer[0]
    return LOCAL_IDENTITY if LOCAL_IDENTITY in identities else "-"


def _nested_code(app_path: str) -> list[Path]:
    """Every Mach-O file and bundle inside the app, deepest first."""
    contents = Path(app_path) / "Contents"
    main_exe = contents / "MacOS" / "ThunderTalk"
    files = []
    for p in contents.rglob("*"):
        if p.is_symlink() or not p.is_file() or p == main_exe:
            continue
        try:
            with open(p, "rb") as f:
                if f.read(4) in _MACHO:
                    files.append(p)
        except OSError:
            pass
    bundles = [p for p in contents.rglob("*") if not p.is_symlink() and p.is_dir()
               and p.suffix in (".framework", ".app", ".bundle", ".appex", ".xpc")]
    return sorted(files, key=lambda p: -len(p.parts)) + sorted(bundles, key=lambda p: -len(p.parts))


def sign_app(app_path: str = APP_PATH) -> str:
    identities = signing_identities()
    identity = _choose_identity(identities)
    print(f"Signing {app_path} (identity: {identity})")
    if identity == "-":
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", app_path], check=True)
        print("Ad-hoc fallback: permission grants may need resetting after updates.")
    else:
        digest = identities.get(identity)
        if digest is None:
            digest = next((h for h in identities.values() if h.lower() == identity.lower()), None)
        if digest is None:
            raise RuntimeError(f"Signing identity is unavailable: {identity}")
        apple = identity.startswith("Developer ID Application:")
        if apple:
            # Inside-out: each nested binary, then bundles, then the app.
            # Notarization requires the hardened runtime and a secure
            # timestamp on every Mach-O file, which --deep can miss.
            base = ["codesign", "--force", "--sign", identity, "--options", "runtime", "--timestamp"]
            nested = _nested_code(app_path)
            files = [p for p in nested if p.is_file()]
            with ThreadPoolExecutor(8) as pool:
                for r in pool.map(lambda p: subprocess.run([*base, str(p)], capture_output=True, text=True), files):
                    if r.returncode:
                        raise RuntimeError(r.stderr)
            for bundle in (p for p in nested if p.is_dir()):
                subprocess.run([*base, str(bundle)], check=True, capture_output=True)
            # Apple's default designated requirement is team based, so macOS
            # permissions survive a renewed Developer ID certificate.
            subprocess.run([*base, "--entitlements", ENTITLEMENTS, "--identifier", APP_ID, app_path],
                           check=True)
        else:
            requirement = os.environ.get("SIGN_REQUIREMENT") or (
                f'designated => identifier "{APP_ID}" and certificate leaf = H"{digest}"')
            # Nested code retains its own identifier/requirement. Only the outer
            # app is pinned to APP_ID and our stable leaf certificate.
            common = ["codesign", "--force", "--sign", identity, "--options", "runtime",
                      "--entitlements", ENTITLEMENTS]
            subprocess.run([*common, "--deep", app_path], check=True)
            subprocess.run([*common, "--identifier", APP_ID, "--requirements", "=" + requirement, app_path],
                           check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", app_path], check=True)
    subprocess.run(["codesign", "-dr", "-", app_path], check=True)
    return identity


def notarize(app_path: str = APP_PATH) -> None:
    zip_path = "dist/ThunderTalk-notarize.zip"
    subprocess.run(["ditto", "-c", "-k", "--keepParent", app_path, zip_path], check=True)
    try:
        subprocess.run(["xcrun", "notarytool", "submit", zip_path, "--keychain-profile", NOTARY_PROFILE,
                        "--wait"], check=True)
    finally:
        os.remove(zip_path)
    subprocess.run(["xcrun", "stapler", "staple", app_path], check=True)
    # Gatekeeper can lag a few seconds behind a freshly stapled ticket.
    for attempt in range(6):
        if subprocess.run(["spctl", "--assess", "--type", "execute", "-vv", app_path]).returncode == 0:
            return
        time.sleep(10)
    raise RuntimeError("Gatekeeper did not accept the notarized app")


def make_dmg(app_path: str = APP_PATH, version: str = "") -> str:
    """The download for the website: a disk image holding the notarized app and
    a shortcut to Applications, so people drag the app into place instead of
    running it from Downloads (where the microphone and Accessibility grants
    would belong to a copy they later move). Signed, notarized and stapled.
    The zip stays for the in-app updater and Homebrew."""
    import shutil
    import tempfile
    if not version:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import thundertalk
        version = thundertalk.__version__
    identity = _choose_identity(signing_identities())
    if not identity.startswith("Developer ID Application:"):
        raise RuntimeError("The website download must be signed with a Developer ID Application identity")
    out = f"dist/ThunderTalk-v{version}-macOS.dmg"
    staging = Path(tempfile.mkdtemp(prefix="thundertalk-dmg-"))
    try:
        subprocess.run(["ditto", app_path, str(staging / "ThunderTalk.app")], check=True)
        (staging / "Applications").symlink_to("/Applications")
        if os.path.exists(out):
            os.remove(out)
        subprocess.run(["hdiutil", "create", "-volname", f"ThunderTalk {version}", "-srcfolder", str(staging),
                        "-fs", "HFS+", "-format", "UDZO", "-imagekey", "zlib-level=9", out], check=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    subprocess.run(["codesign", "--force", "--sign", identity, "--timestamp", out], check=True)
    subprocess.run(["xcrun", "notarytool", "submit", out, "--keychain-profile", NOTARY_PROFILE, "--wait"], check=True)
    subprocess.run(["xcrun", "stapler", "staple", out], check=True)
    for attempt in range(6):
        if subprocess.run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature",
                           "-vv", out]).returncode == 0:
            return out
        time.sleep(10)
    raise RuntimeError("Gatekeeper did not accept the disk image")


def main() -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", "ThunderTalk.spec", "--noconfirm", "--clean"], check=True)
    identity = sign_app()
    if os.environ.get("NOTARIZE") == "1":
        if not identity.startswith("Developer ID Application:"):
            raise RuntimeError("Notarization needs a Developer ID Application identity")
        notarize()
    print(f"Build complete: {APP_PATH}")


if __name__ == "__main__":
    main()
