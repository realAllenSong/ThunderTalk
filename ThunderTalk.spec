# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

hidden_imports = []
hidden_imports += collect_submodules("pynput")
hidden_imports += collect_submodules("sherpa_onnx")
hidden_imports += collect_submodules("sounddevice")
hidden_imports += collect_submodules("rubicon")
# MLX & mlx_qwen3_asr: only import names, not full submodule trees.
# They are lazy-loaded at runtime only when user selects an MLX model.
hidden_imports += ["mlx", "mlx.core", "mlx.nn", "mlx._reprlib_fix"]
hidden_imports += ["mlx_qwen3_asr"]
# huggingface_hub: needed by mlx_qwen3_asr for model downloads
hidden_imports += ["huggingface_hub"]
# torch + transformers: needed by the SeamlessM4T translation engine
# (Direct / Review modes). User report: bundling without these gives
# "No module named 'torch'" when activating the Facebook model.
# These are heavy (~700 MB on macOS arm64) but the only realistic path
# for the in-app translator since pip-installing into a frozen runtime
# is impractical. Excluding tensorflow / keras / scipy / matplotlib /
# pandas keeps the size from getting truly absurd.
hidden_imports += collect_submodules("torch")
hidden_imports += collect_submodules("transformers")
hidden_imports += collect_submodules("safetensors")
hidden_imports += collect_submodules("tokenizers")
hidden_imports += collect_submodules("sentencepiece")

custom_datas = [('assets', 'assets')]
custom_datas += collect_data_files("mlx")
custom_datas += collect_data_files("mlx_qwen3_asr")
custom_datas += collect_data_files("huggingface_hub")
custom_datas += collect_data_files("sherpa_onnx")
custom_datas += collect_data_files("torch")
custom_datas += collect_data_files("transformers")

custom_binaries = []
custom_binaries += collect_dynamic_libs("mlx")
custom_binaries += collect_dynamic_libs("sherpa_onnx")
custom_binaries += collect_dynamic_libs("sounddevice")
custom_binaries += collect_dynamic_libs("torch")

a = Analysis(
    ['thundertalk/__main__.py'],
    pathex=[],
    binaries=custom_binaries,
    datas=custom_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tensorflow', 'keras', 'scipy', 'matplotlib', 'pandas'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ThunderTalk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ThunderTalk',
)
# Read app version from thundertalk/__init__.py so spec stays in sync.
import re as _re_for_spec
with open('thundertalk/__init__.py', 'r') as _vf:
    _APP_VERSION = _re_for_spec.search(
        r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', _vf.read(), _re_for_spec.M
    ).group(1)

app = BUNDLE(
    coll,
    name='ThunderTalk.app',
    icon='assets/icon.icns',
    bundle_identifier='com.thundertalk.app',
    version=_APP_VERSION,
    info_plist={
        'NSMicrophoneUsageDescription': 'ThunderTalk needs microphone access for voice-to-text transcription.',
        'NSAppleEventsUsageDescription': 'ThunderTalk needs accessibility access to paste transcribed text.',
        'CFBundleShortVersionString': _APP_VERSION,
        'CFBundleVersion': _APP_VERSION,
    },
)
