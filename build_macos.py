"""Build ThunderTalk.app for macOS using PyInstaller."""

import subprocess
import sys

CMD = [
    sys.executable, "-m", "PyInstaller",
    "ThunderTalk.spec",
    "--noconfirm",
    "--clean"
]

SIGN_IDENTITY = "Apple Development: realoulasong@gmail.com (WQ9QHZC988)"

print("Running:", " ".join(CMD))
subprocess.run(CMD, check=True)

print("\n🔏 Signing dist/ThunderTalk.app ...")
subprocess.run([
    "codesign", "--force", "--deep", "--sign", SIGN_IDENTITY,
    "dist/ThunderTalk.app",
], check=True)
print("✅ Build + sign complete: dist/ThunderTalk.app")
