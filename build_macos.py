"""Build ThunderTalk.app for macOS using PyInstaller."""

import subprocess
import sys

CMD = [
    sys.executable, "-m", "PyInstaller",
    "--name", "ThunderTalk",
    "--windowed",
    "--onedir",
    "--icon", "assets/icon.icns",
    "--add-data", "assets:assets",
    "--noconfirm",
    "--clean",
    "--osx-bundle-identifier", "com.thundertalk.app",
    "thundertalk/__main__.py",
]

print("Running:", " ".join(CMD))
subprocess.run(CMD, check=True)
print("\n✅ Build complete: dist/ThunderTalk.app")
