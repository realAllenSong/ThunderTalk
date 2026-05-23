"""Build ThunderTalk.app for macOS using PyInstaller.

Usage (ad-hoc, no Developer ID):
    python build_macos.py

Usage (Developer ID + notarization):
    SIGN_IDENTITY="Developer ID Application: Allen Song (XXXXXXXXXX)" \
    APPLE_ID="zysong@seas.upenn.edu" \
    APPLE_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" \
    TEAM_ID="XXXXXXXXXX" \
    python build_macos.py
"""

import os
import subprocess
import sys

CMD = [
    sys.executable, "-m", "PyInstaller",
    "ThunderTalk.spec",
    "--noconfirm",
    "--clean"
]

# Set SIGN_IDENTITY env var to your "Developer ID Application: Name (TEAMID)"
# to enable proper signing + notarization. Defaults to ad-hoc ("-").
SIGN_IDENTITY = os.environ.get("SIGN_IDENTITY", "-")
APPLE_ID       = os.environ.get("APPLE_ID", "")
APP_PASSWORD   = os.environ.get("APPLE_APP_PASSWORD", "")
TEAM_ID        = os.environ.get("TEAM_ID", "")

APP_PATH = "dist/ThunderTalk.app"
ENTITLEMENTS = "entitlements.plist"

print("Running:", " ".join(CMD))
subprocess.run(CMD, check=True)

print(f"\n🔏 Signing {APP_PATH} (identity: {SIGN_IDENTITY}) ...")

if SIGN_IDENTITY == "-":
    # Ad-hoc sign — works locally, triggers Gatekeeper on other machines.
    subprocess.run([
        "codesign", "--force", "--deep", "--sign", SIGN_IDENTITY,
        APP_PATH,
    ], check=True)
    print("✅ Ad-hoc sign complete. Users will see Gatekeeper warning on first launch.")
else:
    # Developer ID sign — sign binaries inside-out then the .app bundle.
    # --options runtime enables Hardened Runtime (required for notarization).
    # Sign all nested binaries/dylibs first, then the main bundle.
    print("  Signing nested frameworks and dylibs...")
    subprocess.run([
        "codesign", "--force", "--sign", SIGN_IDENTITY,
        "--options", "runtime",
        "--entitlements", ENTITLEMENTS,
        "--deep",
        APP_PATH,
    ], check=True)

    # Verify the signature
    result = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", APP_PATH],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("❌ Signature verification failed:", result.stderr)
        sys.exit(1)
    print("✅ Signature verified.")

    # Notarize if credentials are provided
    if APPLE_ID and APP_PASSWORD and TEAM_ID:
        import zipfile, pathlib

        ZIP_PATH = "dist/ThunderTalk-notarize.zip"
        print(f"\n📦 Creating zip for notarization: {ZIP_PATH}")
        subprocess.run([
            "ditto", "-c", "-k", "--keepParent",
            APP_PATH, ZIP_PATH,
        ], check=True)

        print("🚀 Submitting to Apple Notary Service (this takes 1–5 min)...")
        subprocess.run([
            "xcrun", "notarytool", "submit", ZIP_PATH,
            "--apple-id", APPLE_ID,
            "--password", APP_PASSWORD,
            "--team-id", TEAM_ID,
            "--wait",
        ], check=True)

        print("📎 Stapling notarization ticket to app...")
        subprocess.run(["xcrun", "stapler", "staple", APP_PATH], check=True)

        os.remove(ZIP_PATH)
        print("✅ Notarization complete. App is Gatekeeper-clean.")
    else:
        print("ℹ️  APPLE_ID / APPLE_APP_PASSWORD / TEAM_ID not set — skipping notarization.")

print(f"\n✅ Build complete: {APP_PATH}")
