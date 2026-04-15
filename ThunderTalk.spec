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

custom_datas = [('assets', 'assets')]
custom_datas += collect_data_files("mlx")
custom_datas += collect_data_files("mlx_qwen3_asr")
custom_datas += collect_data_files("huggingface_hub")
custom_datas += collect_data_files("sherpa_onnx")

custom_binaries = []
custom_binaries += collect_dynamic_libs("mlx")
custom_binaries += collect_dynamic_libs("sherpa_onnx")
custom_binaries += collect_dynamic_libs("sounddevice")

a = Analysis(
    ['thundertalk/__main__.py'],
    pathex=[],
    binaries=custom_binaries,
    datas=custom_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['transformers', 'torch', 'tensorflow', 'keras', 'scipy', 'matplotlib', 'pandas'],
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
    codesign_identity='Apple Development: realoulasong@gmail.com (WQ9QHZC988)',
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
app = BUNDLE(
    coll,
    name='ThunderTalk.app',
    icon='assets/icon.icns',
    bundle_identifier='com.thundertalk.app',
    info_plist={
        'NSMicrophoneUsageDescription': 'ThunderTalk needs microphone access for voice-to-text transcription.',
        'NSAppleEventsUsageDescription': 'ThunderTalk needs accessibility access to paste transcribed text.',
    },
)
