# Translation Feature (SeamlessM4T v2) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add optional speech-to-text translation to ThunderTalk. User speaks in any language, the app outputs transcribed text directly in a user-selected target language (e.g. speak Chinese → get Spanish text pasted).

**Architecture:** Introduce a `TranslationEngine` alongside the existing `AsrEngine`. Both implement a common minimal interface (`recognize(samples) → text`). `Pipeline.on_toggle` routes samples to the translation engine when a non-Off target language is set in Settings; otherwise to the ASR engine (unchanged behavior). SeamlessM4T v2 Large (2.3B) runs via PyTorch + `transformers` with MPS backend on Apple Silicon, CPU elsewhere. Model is downloaded on demand through the existing Models page flow and stored under `~/.thundertalk/models/seamless-m4t-v2-large/`.

**Tech Stack (verified by spike 2026-04-24):**
- `torch>=2.4` (MPS on Apple Silicon, CPU fallback) — optional dep
- `transformers>=4.40,<6.0` — optional dep (v5+ renamed `audios=` → `audio=`; pin to v4.x range)
- `sentencepiece>=0.2` — SeamlessM4T tokenizer
- `tiktoken>=0.7` — tokenizer backend used by transformers 5.x (also kept for forward-compat)
- `protobuf>=4.25` — protobuf-based tokenizer fallback
- `soundfile>=0.12` — audio file IO (already implicit via existing audio path)
- `AutoProcessor` + `SeamlessM4Tv2Model` from transformers

**Model choice rationale:**
- v2 Large (2.3B) is the only v2 variant Meta released. Medium/Small are v1 (older quality). Worth the size given it's a one-time download.
- No MLX port exists (Apr 2026). `transformers` + `torch` with MPS backend is the simplest cross-platform path; CoreML/ONNX/CTranslate2 conversion can come later as an optimization.
- **Disk size: 8.6GB** (HF cache stores fp32 weights split into 4.7GB + 3.9GB safetensors). At runtime, model is loaded as fp16 on MPS (~4.3GB GPU memory).

**Spike results (M2 Pro 16GB, MPS, fp16):**
- Cold load (incl. download): ~96s
- Warm load: ~10s
- Inference RTF: **0.13** (5s audio → ~0.7s translation) — much better than initial estimate
- Verified outputs: English / Spanish / Japanese / French / German all correct on Chinese input
- ⚠ **Korean output is broken** — model produces malformed text. Exclude from v1 language list.

**Language coverage:** SeamlessM4T v2 supports 96 source speech languages and 35 target text languages. We ship a curated list of 12 major targets in UI v1 to keep the dropdown scannable.

---

## Task 0: Create a feature branch

**Step 1:** Create branch and worktree (optional).

```bash
git checkout -b feat/translation-seamless
```

**Step 2:** Commit.

Nothing to commit yet — just branching.

---

## Task 1: Add optional `translation` dependency group

**Files:**
- Modify: `pyproject.toml:17-20`

**Step 1:** Add optional dep group.

Edit `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
mlx = [
    "mlx-qwen3-asr>=0.3",
]
translation = [
    "torch>=2.4",
    "transformers>=4.40,<6.0",
    "sentencepiece>=0.2",
    "tiktoken>=0.7",
    "protobuf>=4.25",
    "soundfile>=0.12",
]
```

**Step 2:** Install.

```bash
uv sync --extra translation --extra mlx
```

Expected: `torch`, `transformers`, `sentencepiece` installed into `.venv`.

**Step 3:** Verify torch imports with MPS.

```bash
.venv/bin/python -c "import torch; print('MPS:', torch.backends.mps.is_available())"
```

Expected on Apple Silicon: `MPS: True`

**Step 4:** Commit.

```bash
git add pyproject.toml uv.lock
git commit -m "add optional translation dep group (torch, transformers, sentencepiece)"
```

---

## Task 2: Register SeamlessM4T model in the model registry

**Files:**
- Modify: `thundertalk/core/models.py` (append to `BUILTIN_MODELS`)

**Step 1:** Add `ModelInfo` entry. The new `backend` value is `"seamless-torch"`.

Append at the end of `BUILTIN_MODELS` (before the closing bracket):

```python
    # ── SeamlessM4T v2 (translation) ────────────────────────────────────
    ModelInfo(
        id="seamless-m4t-v2-large",
        family="SeamlessM4T-v2",
        name="SeamlessM4T v2 Large",
        variant="PyTorch fp16",
        backend="seamless-torch",
        size_mb=8600,
        language_count=96,
        accuracy_stars=5,
        download_url="hf://facebook/seamless-m4t-v2-large",
        hotword_support=False,
        platform="all",
        notes="Direct speech→translated-text. Required for Translation feature.",
    ),
```

**Step 2:** Sanity-check by listing models.

```bash
.venv/bin/python -c "from thundertalk.core.models import BUILTIN_MODELS; [print(m.id, m.backend, m.size_mb) for m in BUILTIN_MODELS]"
```

Expected: `seamless-m4t-v2-large seamless-torch 4700` appears.

**Step 3:** Commit.

```bash
git add thundertalk/core/models.py
git commit -m "register SeamlessM4T v2 Large in model registry"
```

---

## Task 3: Build the `TranslationEngine` class (TDD)

**Files:**
- Create: `thundertalk/core/translate.py`
- Create: `tests/test_translate.py`

**Step 1:** Write the failing test (minimal smoke test — no model download in CI; use a mock).

`tests/test_translate.py`:

```python
"""Smoke tests for TranslationEngine — no model download required."""

import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from thundertalk.core.translate import TranslationEngine, TranslationResult


def test_engine_not_loaded_by_default():
    engine = TranslationEngine()
    assert not engine.is_loaded
    assert engine.current_model is None


def test_empty_audio_raises():
    engine = TranslationEngine()
    with pytest.raises(RuntimeError):
        engine.translate(np.zeros(0, dtype=np.float32), tgt_lang="eng")


def test_too_quiet_returns_empty_text():
    engine = TranslationEngine()
    # Bypass is_loaded check by monkey-patching
    engine._model = MagicMock()
    engine._processor = MagicMock()
    engine._model_id = "seamless-m4t-v2-large"
    # Near-silent audio (RMS ~0)
    samples = np.full(16000, 0.0001, dtype=np.float32)
    result = engine.translate(samples, tgt_lang="eng")
    assert result.text == ""
```

**Step 2:** Run test — expect ImportError (module not yet created).

```bash
.venv/bin/python -m pytest tests/test_translate.py -v
```

Expected: `ModuleNotFoundError: No module named 'thundertalk.core.translate'`

**Step 3:** Create `thundertalk/core/translate.py`:

```python
"""Speech translation engine — SeamlessM4T v2 via transformers + torch.

Separate from AsrEngine because:
  1. Different runtime (torch, not sherpa-onnx or mlx-qwen3-asr)
  2. Different I/O (takes target language code, not just audio)
  3. Different model format (HF Hub repo, not ONNX dir)

Loading is lazy and heavy (~5GB model). load_model() blocks the main
thread; callers should run it on a ModelLoadWorker thread.
"""

from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

_IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"


@dataclass
class TranslationResult:
    text: str
    duration_secs: float
    inference_ms: int
    model: str
    tgt_lang: str


class TranslationEngine:
    """Wraps SeamlessM4T v2 for speech-to-text translation (S2TT).

    Usage:
      engine = TranslationEngine()
      engine.load_model("~/.thundertalk/models/seamless-m4t-v2-large")
      result = engine.translate(samples, tgt_lang="spa")  # ISO-639-3
    """

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._model_id: Optional[str] = None
        self._device: str = "cpu"
        self._dtype = None  # torch.float16 on GPU, torch.float32 on CPU

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    @property
    def current_model(self) -> Optional[str]:
        return self._model_id

    def unload(self) -> None:
        self._model = None
        self._processor = None
        self._model_id = None

    def load_model(self, model_dir_or_repo: str) -> None:
        """Load SeamlessM4T v2 from a local dir OR hf:// repo ID.

        Imports torch/transformers lazily so the base app doesn't pay the
        startup cost when translation is off.
        """
        import torch
        from transformers import AutoProcessor, SeamlessM4Tv2Model

        self.unload()

        if model_dir_or_repo.startswith("hf://"):
            pretrained = model_dir_or_repo[5:]
        else:
            pretrained = model_dir_or_repo

        # Device / dtype selection
        if _IS_APPLE_SILICON and torch.backends.mps.is_available():
            self._device = "mps"
            self._dtype = torch.float16
        elif torch.cuda.is_available():
            self._device = "cuda"
            self._dtype = torch.float16
        else:
            self._device = "cpu"
            self._dtype = torch.float32

        print(f"[Translate] Loading {pretrained} on {self._device} "
              f"({self._dtype})...")
        t0 = time.perf_counter()

        self._processor = AutoProcessor.from_pretrained(pretrained)
        self._model = SeamlessM4Tv2Model.from_pretrained(
            pretrained,
            torch_dtype=self._dtype,
        ).to(self._device)
        self._model.eval()

        self._model_id = os.path.basename(pretrained)
        elapsed = time.perf_counter() - t0
        print(f"[Translate] Loaded in {elapsed:.1f}s")

    def translate(
        self,
        samples: np.ndarray,
        tgt_lang: str,
        sample_rate: int = 16_000,
    ) -> TranslationResult:
        """Speech-to-text translation.

        Args:
          samples: mono float32 audio in [-1, 1]
          tgt_lang: ISO-639-3 code (e.g. "eng", "spa", "jpn", "cmn")
          sample_rate: input sample rate (will be resampled to 16k internally)
        """
        if not self.is_loaded:
            raise RuntimeError("No translation model loaded")
        if len(samples) == 0:
            raise RuntimeError("Empty audio")

        duration = len(samples) / sample_rate
        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms < 0.003:
            print(f"[Translate] Audio too quiet (rms={rms:.5f}), skipping")
            return TranslationResult(
                text="", duration_secs=duration, inference_ms=0,
                model=self._model_id or "unknown", tgt_lang=tgt_lang,
            )

        import torch

        t0 = time.perf_counter()
        # Processor expects sr=16000 raw audio.
        # Note: transformers 5.x renamed `audios=` to `audio=`. Pin
        # transformers<6.0 to keep this signature stable.
        inputs = self._processor(
            audios=samples,
            sampling_rate=sample_rate,
            return_tensors="pt",
        ).to(self._device, self._dtype)

        with torch.no_grad():
            # generate_speech=False gives us TEXT output (S2TT).
            output_tokens = self._model.generate(
                **inputs,
                tgt_lang=tgt_lang,
                generate_speech=False,
            )
        # output_tokens is a tuple (text_ids, …) when generate_speech=False
        text_ids = output_tokens[0].tolist()[0] if isinstance(output_tokens, tuple) else output_tokens[0].tolist()
        text = self._processor.decode(text_ids, skip_special_tokens=True)

        inference_ms = int((time.perf_counter() - t0) * 1000)
        print(f"[Translate] {duration:.1f}s → {tgt_lang}  "
              f"{inference_ms}ms  rtf={inference_ms/1000/duration:.2f}")

        return TranslationResult(
            text=text,
            duration_secs=duration,
            inference_ms=inference_ms,
            model=self._model_id or "unknown",
            tgt_lang=tgt_lang,
        )
```

**Step 4:** Run tests.

```bash
.venv/bin/python -m pytest tests/test_translate.py -v
```

Expected: 3 tests pass.

**Step 5:** Commit.

```bash
git add thundertalk/core/translate.py tests/test_translate.py
git commit -m "add TranslationEngine for SeamlessM4T v2 speech-to-text translation"
```

---

## Task 4: Add `translation_target` setting

**Files:**
- Modify: `thundertalk/core/settings.py:10-23`

**Step 1:** Add key to `DEFAULTS`.

```python
DEFAULTS: dict[str, Any] = {
    "hotkey": "cmd_r",
    "press_mode": "toggle",
    "microphone": "auto",
    "mute_speakers": True,
    "language": "en",
    "launch_at_startup": False,
    "silent_launch": True,
    "transcription_language": "auto",
    "save_to_clipboard": True,
    "hotwords": [],
    "active_model_id": "",
    "translation_target": "off",   # NEW: ISO-639-3 code or "off"
    "log_enabled": True,
}
```

**Step 2:** Add property.

Below the existing `@property` block:

```python
    @property
    def translation_target(self) -> str:
        return self._data.get("translation_target", "off")
```

**Step 3:** Verify persistence.

```bash
.venv/bin/python -c "
from thundertalk.core.settings import Settings
s = Settings()
s.set('translation_target', 'spa')
print('stored:', s.translation_target)
"
```

Expected: `stored: spa`

**Step 4:** Reset to default so the rest of the plan doesn't see it set.

```bash
.venv/bin/python -c "from thundertalk.core.settings import Settings; s = Settings(); s.set('translation_target', 'off')"
```

**Step 5:** Commit.

```bash
git add thundertalk/core/settings.py
git commit -m "add translation_target setting (off | ISO-639-3)"
```

---

## Task 5: Add the Translation section to Settings page

**Files:**
- Modify: `thundertalk/ui/pages/settings_page.py`
- Modify: `thundertalk/core/i18n.py` (add i18n keys)

**Step 1:** Decide the curated target language list (use ISO-639-3):

```python
TRANSLATION_TARGETS = [
    ("off", "Off (pure transcription)"),
    ("eng", "English"),
    ("cmn", "中文 (Chinese)"),
    ("jpn", "日本語 (Japanese)"),
    ("spa", "Español (Spanish)"),
    ("fra", "Français (French)"),
    ("deu", "Deutsch (German)"),
    ("por", "Português (Portuguese)"),
    ("rus", "Русский (Russian)"),
    ("ita", "Italiano (Italian)"),
    ("arb", "العربية (Arabic)"),
    ("hin", "हिन्दी (Hindi)"),
    # Korean (kor) excluded from v1: spike showed model produces malformed
    # output for kor target. Add back after upstream fix.
]
```

**Step 2:** Add i18n keys in `thundertalk/core/i18n.py` (follow existing pattern):

```python
"settings.translation.title": {"en": "Translation", "zh": "翻译"},
"settings.translation.desc": {
    "en": "Speak in any language, get text directly in your target language. Requires SeamlessM4T v2 model.",
    "zh": "用任何语言说话，直接得到目标语言文字。需要下载 SeamlessM4T v2 模型。",
},
"settings.translation.target": {"en": "Translate to", "zh": "翻译为"},
"settings.translation.model_missing": {
    "en": "SeamlessM4T v2 model not downloaded.",
    "zh": "SeamlessM4T v2 模型未下载。",
},
"settings.translation.download_cta": {"en": "Download now", "zh": "立即下载"},
```

**Step 3:** Add a "Translation" tab/section in the Settings page UI. Follow the existing card/section pattern in `settings_page.py`.

The section contains:
- Header: `settings.translation.title`
- Sub-caption: `settings.translation.desc`
- A `QComboBox` populated from `TRANSLATION_TARGETS` bound to `settings.translation_target`
- Below the combo: if model not downloaded, show a small warning row with a "Download now" button that opens the Models page scrolled to SeamlessM4T v2

**Step 4:** Wire a signal when combo changes:

```python
self.translation_target_changed = Signal(str)
# inside combo.currentIndexChanged:
code = TRANSLATION_TARGETS[idx][0]
self._settings.set("translation_target", code)
self.translation_target_changed.emit(code)
```

**Step 5:** Run the app locally and verify the UI renders.

```bash
.venv/bin/python run.py
```

Manually:
- Open Settings → Translation section visible
- Combo lists 13 options, default "Off"
- Selecting "Español" persists across app restart

**Step 6:** Commit.

```bash
git add thundertalk/ui/pages/settings_page.py thundertalk/core/i18n.py
git commit -m "add Translation section to Settings page with target language combo"
```

---

## Task 6: Wire translation routing into `Pipeline.on_toggle`

**Files:**
- Modify: `thundertalk/app.py` (Pipeline class + on_toggle)

**Step 1:** Extend `Pipeline.__init__` to create a lazy translator handle:

```python
class Pipeline(QObject):
    toggle_signal = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.recorder = AudioRecorder()
        self.asr = AsrEngine()
        self.translator = None   # lazily created TranslationEngine
        self._recording = False
        self._worker: AsrWorker | None = None
        self._load_worker: ModelLoadWorker | None = None
```

**Step 2:** Add a helper method for lazy translator setup:

```python
    def get_translator(self):
        """Return the TranslationEngine, creating it lazily on first access."""
        if self.translator is None:
            from thundertalk.core.translate import TranslationEngine
            self.translator = TranslationEngine()
        return self.translator
```

**Step 3:** Create an `AsrWorker`-equivalent `TranslationWorker` (or generalize `AsrWorker`). For minimal change, add a new QThread subclass next to `AsrWorker`:

```python
class TranslationWorker(QThread):
    done = Signal(object)
    error = Signal(str)

    def __init__(self, translator, samples, tgt_lang):
        super().__init__()
        self._translator = translator
        self._samples = samples
        self._tgt_lang = tgt_lang

    def run(self):
        try:
            result = self._translator.translate(self._samples, self._tgt_lang)
            self.done.emit(result)
        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))
```

**Step 4:** Modify `_finalize_stop` inside `on_toggle`:

Replace the existing `AsrWorker` kick-off block with logic that chooses engine based on setting:

```python
            tgt = settings.get("translation_target")
            if tgt and tgt != "off":
                translator = pipe.get_translator()
                if not translator.is_loaded:
                    print("[Toggle] Translation target set but model not loaded")
                    overlay.show_error("Translation model not loaded")
                    return
                print(f"[Toggle] Starting translation → {tgt}")
                worker = TranslationWorker(translator, samples, tgt)
                worker.done.connect(_on_asr_done)  # reuse same done handler
                worker.error.connect(_on_asr_error)
                worker.finished.connect(_clear_asr_worker)
                pipe._worker = worker
                worker.start()
                return

            # else: existing ASR path unchanged
            print(f"[Toggle] Starting ASR on {len(samples)} samples")
            worker = AsrWorker(pipe.asr, samples)
            worker.done.connect(_on_asr_done)
            worker.error.connect(_on_asr_error)
            worker.finished.connect(_clear_asr_worker)
            pipe._worker = worker
            worker.start()
```

**Step 5:** Check `_on_asr_done` — it accepts an `AsrResult`. `TranslationResult` has `.text` and `.duration_secs` too, so the duck-typed handler should work. Verify by reading `_on_asr_done`.

If `_on_asr_done` accesses fields that `TranslationResult` doesn't have (e.g. `.backend`, `.rtf`), patch `TranslationResult` to include them as default values, OR generalize the handler.

**Step 6:** Smoke-test (without model actually loaded):

Set target language to "spa" in settings, then press hotkey. Expected: `overlay.show_error("Translation model not loaded")`.

**Step 7:** Commit.

```bash
git add thundertalk/app.py
git commit -m "route audio to TranslationEngine when translation_target is set"
```

---

## Task 7: Hook SeamlessM4T download into Models page

**Files:**
- Modify: `thundertalk/ui/pages/models_page.py`

**Step 1:** The existing download flow is driven by `ModelInfo.download_url` in `models.py`. Because we registered the model in Task 2 with `"hf://facebook/seamless-m4t-v2-large"`, the existing `hf://` handler in the models download worker should already fetch it via `huggingface_hub.snapshot_download`.

Verify by searching:

```bash
grep -n "hf://" thundertalk/ui/pages/models_page.py thundertalk/core/
```

If the download path resolves `hf://` prefixes via `snapshot_download`, no code change needed.

**Step 2:** If the Models page groups models by `family`, ensure the new `SeamlessM4T-v2` family renders with a sensible card title. The existing grouping logic should handle it automatically.

**Step 3:** Launch the app and verify the card appears under Models with a Download button.

```bash
.venv/bin/python run.py
```

**Step 4:** Click Download (or manually trigger via API):

```bash
.venv/bin/python -c "
from huggingface_hub import snapshot_download
p = snapshot_download('facebook/seamless-m4t-v2-large',
    cache_dir='~/.thundertalk/models/seamless-m4t-v2-large')
print('Downloaded to:', p)
"
```

Expected: ~4.7GB downloaded, path printed.

**Step 5:** Wire up the Settings "Download now" CTA from Task 5 to switch to the Models page and highlight the SeamlessM4T row. Implementation: emit a `request_model_download(id)` signal from the Settings Translation section; the Main Window catches it and calls `main_window.show_models_page(highlight_id)`.

**Step 6:** Commit.

```bash
git add thundertalk/ui/pages/models_page.py thundertalk/ui/main_window.py
git commit -m "expose SeamlessM4T v2 in Models page; wire Settings CTA"
```

---

## Task 8: Wire translator load when target is non-off AND model is present

**Files:**
- Modify: `thundertalk/app.py` (after model discovery / on settings change)

**Step 1:** After app startup, if `translation_target != "off"` and the SeamlessM4T model is downloaded, load the translator on a background thread. Use the existing `ModelLoadWorker` pattern.

```python
def _maybe_load_translator() -> None:
    tgt = settings.get("translation_target")
    if not tgt or tgt == "off":
        return
    model_path = Path.home() / ".thundertalk" / "models" / "seamless-m4t-v2-large"
    if not model_path.exists():
        return
    translator = pipe.get_translator()
    if translator.is_loaded:
        return

    def _load():
        try:
            translator.load_model(str(model_path))
        except Exception:
            traceback.print_exc()

    threading.Thread(target=_load, daemon=True).start()

# Call once at startup and when translation_target changes:
_maybe_load_translator()
window.settings_page.translation_target_changed.connect(
    lambda _code: _maybe_load_translator()
)
```

**Step 2:** Verify in a manual run: set target to "spa", confirm log shows `[Translate] Loaded in X.Xs`.

**Step 3:** Commit.

```bash
git add thundertalk/app.py
git commit -m "lazy-load translator on startup and when target language changes"
```

---

## Task 9: E2E smoke test (manual)

**Step 1:** Rebuild the app.

```bash
.venv/bin/python build_macos.py
rm -rf /Applications/ThunderTalk.app
cp -R dist/ThunderTalk.app /Applications/
```

**Step 2:** Launch from /Applications. In Models page, download SeamlessM4T v2 Large (one-time, ~4.7GB).

**Step 3:** Settings → Translation → set target to "English".

**Step 4:** Press Right ⌘, speak a Chinese sentence ("今天天气很好"), press Right ⌘ again.

Expected: English text pasted into the foreground app. Log shows `[Translate] 2.1s → eng  850ms`.

**Step 5:** Set target to "Off", press hotkey, speak. Expected: original Chinese transcription (existing ASR behavior).

**Step 6:** Commit final doc update / release notes if any.

```bash
git commit --allow-empty -m "E2E translation smoke test passed"
```

---

## Task 10: Merge to main

**Step 1:** Push branch.

```bash
git push -u origin feat/translation-seamless
```

**Step 2:** Open PR.

```bash
gh pr create --title "Add speech-to-text translation via SeamlessM4T v2" --body "$(cat <<'EOF'
## Summary
- Adds `TranslationEngine` using SeamlessM4T v2 (HF transformers + torch MPS/CPU)
- Settings: new "Translation" section with target-language combo (13 curated languages)
- Pipeline routes audio through translator when target is non-off
- Model downloadable from Models page (4.7GB one-time)

## Test plan
- [ ] Download SeamlessM4T v2 from Models page
- [ ] Set target to English; speak Chinese; verify English output
- [ ] Set target to Off; verify existing ASR behavior unchanged
- [ ] Verify app startup time not regressed when translation off
EOF
)"
```

---

## Deferred / v2 considerations

- **Size optimization**: Port to ONNX or CTranslate2 to skip bundling ~500MB of torch.
- **MLX port**: Wait for mlx-community port or write one.
- **Streaming**: SeamlessStreaming variant for real-time.
- **T2TT mode**: Use SeamlessM4T v2 to translate existing Qwen3-ASR transcripts (dual-model flow) — useful when user wants Qwen3-ASR's better accuracy for source language + SeamlessM4T for translation.
- **Language detection display**: Show detected source language in the overlay.
