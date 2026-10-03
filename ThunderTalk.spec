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
# mlx_audio loads STT model modules dynamically by name (MODEL_REMAPPING →
# importlib), so static analysis misses them — collect the full tree.
hidden_imports += collect_submodules("mlx_audio")
hidden_imports += collect_submodules("mlx_lm")
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

# IndexTTS-2.5: vendored MLX port (third_party/mlx_indextts) and its extra deps.
import sys as _sys
_sys.path.insert(0, 'third_party')
hidden_imports += collect_submodules("mlx_indextts")
hidden_imports += collect_submodules("torchaudio")
hidden_imports += collect_submodules("omegaconf")
hidden_imports += collect_submodules("wetext")
hidden_imports += collect_submodules("einops")
hidden_imports += ["psutil", "kaldifst", "tiktoken", "tiktoken_ext"]
# scipy imports parts of itself dynamically (array_api_compat backends); once it
# is in the bundle, transformers also imports it, so collect the whole tree.
hidden_imports += collect_submodules("scipy")
# ...and name this one explicitly: it is listed by collect_submodules but still
# dropped from the archive (PyInstaller 6.19's scipy hook predates scipy 1.18's
# move of array_api_compat from scipy._lib to scipy._external).
hidden_imports += ["scipy._external.array_api_compat.numpy.fft", "scipy._external.array_api_compat.numpy.linalg"]

# yt-dlp (Studio ▸ transcribe from a link). Its own PyInstaller hook adds the
# networking extras; extractors are listed here too so none can go missing.
hidden_imports += collect_submodules("yt_dlp")
hidden_imports += ["certifi"]

custom_datas = [('assets', 'assets')]
# (assets/voices — built-in reference voices shared by IndexTTS and VoxCPM2 — ship with assets/)
custom_datas += collect_data_files("mlx_indextts")
custom_datas += collect_data_files("wetext")
custom_datas += collect_data_files("contractions")      # wetext → contractions_dict.json
custom_datas += collect_data_files("anyascii")
custom_datas += collect_data_files("textsearch")
custom_datas += collect_data_files("torchaudio")
custom_datas += collect_data_files("mlx")
custom_datas += collect_data_files("mlx_qwen3_asr")
custom_datas += collect_data_files("mlx_audio")
custom_datas += collect_data_files("mlx_lm")
custom_datas += collect_data_files("huggingface_hub")
custom_datas += collect_data_files("sherpa_onnx")
custom_datas += collect_data_files("torch")
custom_datas += collect_data_files("transformers")

custom_binaries = []
custom_binaries += collect_dynamic_libs("mlx")
custom_binaries += collect_dynamic_libs("sherpa_onnx")
custom_binaries += collect_dynamic_libs("sounddevice")
custom_binaries += collect_dynamic_libs("torch")
custom_binaries += collect_dynamic_libs("torchaudio")
custom_binaries += collect_dynamic_libs("kaldifst")

a = Analysis(
    ['thundertalk/__main__.py'],
    pathex=['third_party'],
    binaries=custom_binaries,
    datas=custom_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # scipy is needed by VoxCPM2 (reference resampling in mlx-audio) and IndexTTS.
    excludes=['tensorflow', 'keras', 'matplotlib', 'pandas'],
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
        # Bundled libraries set the real floor: Qt needs macOS 13, onnxruntime 14,
        # MLX 15. Declaring it lets older systems say so instead of crashing.
        'LSMinimumSystemVersion': '15.0',
    },
)
