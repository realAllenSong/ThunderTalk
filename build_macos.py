"""Build and sign ThunderTalk.app.

Defaults to ThunderTalk Local Signing if installed by tools/make_signing_identity.sh,
otherwise ad-hoc. SIGN_IDENTITY overrides this (use '-' to force ad-hoc).
Developer ID: set SIGN_IDENTITY, APPLE_ID, APPLE_APP_PASSWORD and TEAM_ID.
SIGN_REQUIREMENT can override the leaf-pinned requirement for certificate renewal.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
import subprocess
import sys

LOCAL_IDENTITY = "ThunderTalk Local Signing"
APP_ID = "com.thundertalk.app"
APP_PATH = "dist/ThunderTalk.app"
ENTITLEMENTS = "entitlements.plist"


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


def sign_app(app_path: str = APP_PATH) -> str:
    identities = signing_identities()
    identity = os.environ.get("SIGN_IDENTITY") or (LOCAL_IDENTITY if LOCAL_IDENTITY in identities else "-")
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
        requirement = os.environ.get("SIGN_REQUIREMENT") or (
            f'designated => identifier "{APP_ID}" and certificate leaf = H"{digest}"')
        # Nested code retains its own identifier/requirement. Only the outer
        # app is pinned to APP_ID and our stable leaf certificate.
        common = ["codesign", "--force", "--sign", identity, "--options", "runtime",
                  "--entitlements", ENTITLEMENTS]
        subprocess.run([*common, "--deep", app_path], check=True)
        subprocess.run([*common, "--identifier", APP_ID, "--requirements", "=" + requirement, app_path], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", app_path], check=True)
    subprocess.run(["codesign", "-dr", "-", app_path], check=True)
    return identity


def main() -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", "ThunderTalk.spec", "--noconfirm", "--clean"], check=True)
    identity = sign_app()
    apple_id = os.environ.get("APPLE_ID", "")
    password = os.environ.get("APPLE_APP_PASSWORD", "")
    team = os.environ.get("TEAM_ID", "")
    if identity != "-" and apple_id and password and team:
        zip_path = "dist/ThunderTalk-notarize.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", APP_PATH, zip_path], check=True)
        subprocess.run(["xcrun", "notarytool", "submit", zip_path, "--apple-id", apple_id,
                        "--password", password, "--team-id", team, "--wait"], check=True)
        subprocess.run(["xcrun", "stapler", "staple", APP_PATH], check=True)
        os.remove(zip_path)
    print(f"Build complete: {APP_PATH}")


if __name__ == "__main__":
    main()
