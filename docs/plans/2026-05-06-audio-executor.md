# AudioExecutor — Serialize all PortAudio calls onto a dedicated worker thread

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the entire class of CoreAudio HAL deadlocks (Pa_StopStream, Pa_Terminate, Pa_OpenStream, …) by running every PortAudio call on a dedicated daemon thread. The Qt main thread waits with a watchdog timeout instead of blocking on a kernel mutex.

**Architecture:** A module-level `AudioExecutor` owns a single `audio-executor` daemon thread plus a FIFO queue. Public `executor.call(fn, timeout=2.0)` enqueues a job, blocks the caller on a `threading.Event`, and either returns the result, re-raises the function's exception, or raises `AudioCallTimeout` if the watchdog fires. After a timeout the worker thread is **abandoned** (the wedged PA call is unrecoverable at the OS level — but the GUI is unblocked, which is the only invariant we care about).

All PortAudio entry points called from the GUI thread today (`sd._terminate/_initialize`, `sd.query_devices`, `sd.InputStream(...)`, `stream.start/stop/close`) get routed through the executor. The v1.1.12 inline daemon-thread-per-stop watchdog is replaced by the executor pattern.

**Tech Stack:** Python 3.12 `threading` + `queue.Queue`, sounddevice (PortAudio cffi), PySide6.

**Out of scope:** any change to the audio callback (`_audio_cb`) — that runs on PortAudio's own I/O thread, not ours, and isn't a deadlock vector. The translator/ASR worker QThreads are also untouched.

---

## Task 0: Verify clean baseline

**Step 1: Confirm no stale audio process is still around**

Run: `ps aux | grep -i ThunderTalk | grep -v 'Code Helper' | grep -v grep`
Expected: only `/Applications/ThunderTalk.app/...` if you've relaunched it, or empty.
Already done in this session: `kill -9 70158` returned cleanly.

**Step 2: Confirm working tree is clean**

Run: `git status --porcelain`
Expected: empty (last commit is v1.1.12 release).

---

## Task 1: Create `AudioExecutor` module

**Files:**
- Create: `thundertalk/core/audio_executor.py`
- Create: `tests/test_audio_executor.py`

**Step 1: Write the failing tests**

```python
# tests/test_audio_executor.py
"""Tests for AudioExecutor — the deadlock-bounded PortAudio dispatcher."""

from __future__ import annotations

import threading
import time

import pytest

from thundertalk.core.audio_executor import AudioCallTimeout, AudioExecutor


def test_call_runs_on_worker_thread() -> None:
    ex = AudioExecutor()
    main_tid = threading.get_ident()
    worker_tid = ex.call(threading.get_ident)
    assert worker_tid != main_tid


def test_call_returns_result() -> None:
    ex = AudioExecutor()
    assert ex.call(lambda: 21 * 2) == 42


def test_call_propagates_exception() -> None:
    ex = AudioExecutor()

    def boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        ex.call(boom)


def test_timeout_unblocks_caller() -> None:
    ex = AudioExecutor()
    t0 = time.perf_counter()
    with pytest.raises(AudioCallTimeout):
        ex.call(lambda: threading.Event().wait(), timeout=0.3)
    elapsed = time.perf_counter() - t0
    # Watchdog must release within ~timeout + small slack; never block longer.
    assert 0.25 <= elapsed <= 0.6, f"watchdog took {elapsed:.3f}s"


def test_subsequent_calls_after_normal_completion() -> None:
    ex = AudioExecutor()
    assert ex.call(lambda: "first") == "first"
    assert ex.call(lambda: "second") == "second"


def test_serializes_calls_in_order() -> None:
    """All PA work happens on one thread; calls must observe FIFO ordering."""
    ex = AudioExecutor()
    log: list[int] = []
    for i in range(10):
        ex.call(lambda i=i: log.append(i))
    assert log == list(range(10))
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_audio_executor.py -v`
Expected: ImportError / ModuleNotFoundError on `thundertalk.core.audio_executor`.

**Step 3: Implement the executor**

```python
# thundertalk/core/audio_executor.py
"""Dedicated worker thread for ALL PortAudio interactions.

Background
----------
macOS's CoreAudio HAL holds internal recursive mutexes during device
state changes (Bluetooth A2DP↔HFP profile switches, hot-unplug, sample-
rate negotiation). PortAudio's `Pa_StopStream`, `Pa_Terminate`,
`Pa_OpenStream`, and friends all route through that HAL and can block
on those mutexes for hours when the device gets into a bad state.

When such a call originates from the Qt main thread, the entire GUI
freezes. v1.1.12 patched `stream.stop()` specifically with an inline
watchdog; v1.1.13 generalises that pattern: ALL PortAudio calls run on
a single dedicated daemon thread, and callers wait with a bounded
timeout instead of a blocking system call.

If a job hangs, the watchdog releases the caller and the worker stays
stuck inside the wedged PA call. Subsequent jobs queue up behind it and
will also time out — that is fine: at this point the OS-level audio
state is unrecoverable from inside the process, and the only correct
behaviour is to keep the GUI responsive so the user can quit cleanly.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional


class AudioCallTimeout(Exception):
    """Raised when an enqueued PortAudio call did not finish in time."""


class _Job:
    __slots__ = ("fn", "result", "exc", "done")

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.fn: Callable[[], Any] = fn
        self.result: Any = None
        self.exc: Optional[BaseException] = None
        self.done = threading.Event()


class AudioExecutor:
    """Single-threaded executor that owns every PortAudio interaction.

    Submit work via ``call(fn, timeout=2.0)``. The function runs on the
    worker thread; the caller blocks at most ``timeout`` seconds. On
    timeout, ``AudioCallTimeout`` is raised and the job is abandoned.
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[_Job]]" = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop, name="audio-executor", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                job.result = job.fn()
            except BaseException as exc:
                job.exc = exc
            finally:
                job.done.set()

    def call(self, fn: Callable[[], Any], timeout: float = 2.0) -> Any:
        job = _Job(fn)
        self._q.put(job)
        if not job.done.wait(timeout=timeout):
            raise AudioCallTimeout(
                f"audio call did not complete within {timeout:.1f}s "
                "(CoreAudio HAL likely wedged); job abandoned"
            )
        if job.exc is not None:
            raise job.exc
        return job.result

    def shutdown(self) -> None:
        """Best-effort: ask the worker to exit. Ignored if it's stuck."""
        self._q.put(None)


_default: Optional[AudioExecutor] = None


def get_executor() -> AudioExecutor:
    """Module-level singleton. First call lazily spins up the worker."""
    global _default
    if _default is None:
        _default = AudioExecutor()
    return _default
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_audio_executor.py -v`
Expected: 5 passed.

**Step 5: Verify the existing test suite still runs**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 regressions.

**No commit yet** — the project ships one squash commit per release (`v{version}: …`). All five tasks land in a single commit at Task 6.

---

## Task 2: Refactor `audio.py` to use the executor

**Files:**
- Modify: `thundertalk/core/audio.py` (entire file)

Goals:
1. Replace per-call `_refresh_devices()` with a one-time refresh on first use plus an explicit `refresh_devices()` method for hot-plug rescans (called via executor).
2. Move `sd.InputStream(...)` construction, `stream.start()`, `stream.stop()`, `stream.close()`, and `sd.query_devices()` onto the executor.
3. Remove the v1.1.12 inline watchdog (`_close_stream_with_watchdog`, `_STREAM_CLOSE_TIMEOUT_S`) — superseded by the executor pattern.

**Step 1: Replace `audio.py` with the executor-backed version**

```python
# thundertalk/core/audio.py
"""Microphone recording — only holds the device while actively recording.

All PortAudio interactions are routed through ``AudioExecutor`` so a
hung CoreAudio HAL can never freeze the GUI thread.

Anti-pop measures:
  - Discards first 5 chunks (~50 ms) to skip device-activation transients.
  - Applies short linear fade-in/fade-out (10 ms) on returned samples.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np
import sounddevice as sd

from thundertalk.core.audio_executor import AudioCallTimeout, get_executor

SAMPLE_RATE = 16_000
CHANNELS = 1

_FADE_SAMPLES = int(SAMPLE_RATE * 0.010)  # 10ms fade
_SKIP_CHUNKS = 5                           # discard first N callback chunks (~50ms)

# Watchdog budgets (seconds). Generous enough that healthy CoreAudio
# completes in well under each, tight enough that the GUI never feels
# hung. All numbers are wall-clock on the GUI thread.
_OPEN_TIMEOUT_S = 3.0
_CLOSE_TIMEOUT_S = 2.0
_QUERY_TIMEOUT_S = 2.0
_REINIT_TIMEOUT_S = 4.0


def _query_input_device_names() -> list[str]:
    """MUST run on the executor thread."""
    return [
        d["name"]
        for d in sd.query_devices()
        if d["max_input_channels"] > 0
    ]


def _resolve_device_idx(name: Optional[str]) -> Optional[int]:
    """MUST run on the executor thread. Returns device index or None for default."""
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d["max_input_channels"] > 0:
            return i
    print(f"[Audio] Device '{name}' not found, falling back to system default")
    return None


def _full_reinit() -> None:
    """MUST run on the executor thread. Catches hot-plugged hardware."""
    sd._terminate()
    sd._initialize()


class AudioRecorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stream: Optional[sd.InputStream] = None
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._skip_counter = 0
        self._current_rms: float = 0.0
        self._exec = get_executor()

    @staticmethod
    def list_devices() -> list[str]:
        """Return current input device names. Uses PortAudio's cached list
        from the most recent ``Pa_Initialize``; call ``refresh_devices()``
        first if you need to pick up freshly-plugged hardware."""
        try:
            return get_executor().call(
                _query_input_device_names, timeout=_QUERY_TIMEOUT_S
            )
        except AudioCallTimeout:
            print("[Audio] list_devices timed out — CoreAudio HAL wedged")
            return []

    @staticmethod
    def refresh_devices() -> list[str]:
        """Force PortAudio to re-scan hardware (Pa_Terminate + Pa_Initialize),
        then return the new input device list. Heavy operation — only call
        in response to explicit user intent (e.g. a Refresh button)."""
        ex = get_executor()
        try:
            ex.call(_full_reinit, timeout=_REINIT_TIMEOUT_S)
        except AudioCallTimeout:
            print("[Audio] refresh_devices: re-init timed out — CoreAudio wedged")
            return []
        try:
            return ex.call(_query_input_device_names, timeout=_QUERY_TIMEOUT_S)
        except AudioCallTimeout:
            return []

    def start(self, device: Optional[str] = None) -> None:
        with self._lock:
            self.stop()
            self._chunks.clear()
            self._skip_counter = 0
            self._recording = True

            def _open() -> sd.InputStream:
                dev_idx = _resolve_device_idx(device)
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="float32",
                    device=dev_idx,
                    callback=self._audio_cb,
                )
                stream.start()
                return stream

            try:
                self._stream = self._exec.call(_open, timeout=_OPEN_TIMEOUT_S)
            except AudioCallTimeout:
                # CoreAudio is wedged; we can't open a stream. Reflect that
                # in our state so a subsequent stop() is a no-op and the UI
                # can show an error overlay.
                print("[Audio] start: open stream timed out — CoreAudio wedged")
                self._stream = None
                self._recording = False
                return

            try:
                actual = self._exec.call(
                    lambda: sd.query_devices(self._stream.device),
                    timeout=_QUERY_TIMEOUT_S,
                )
                print(
                    f"[Audio] Recording on: {actual['name']}  "
                    f"sr={actual['default_samplerate']}  "
                    f"channels={actual['max_input_channels']}"
                )
            except AudioCallTimeout:
                pass  # Logging is best-effort; not worth failing the start.

    def stop(self) -> Optional[np.ndarray]:
        with self._lock:
            self._recording = False
            stream = self._stream
            self._stream = None

            if stream is not None:
                def _close() -> None:
                    stream.stop()
                    stream.close()

                try:
                    self._exec.call(_close, timeout=_CLOSE_TIMEOUT_S)
                except AudioCallTimeout:
                    print(
                        f"[Audio] stop: close timed out after "
                        f"{_CLOSE_TIMEOUT_S:.1f}s — abandoning stream "
                        "to avoid GUI deadlock."
                    )

            if not self._chunks:
                return None
            samples = np.concatenate(self._chunks)
            self._chunks.clear()

            peak = float(np.max(np.abs(samples)))
            rms = float(np.sqrt(np.mean(samples ** 2)))
            print(
                f"[Audio] Recorded {len(samples)} samples "
                f"({len(samples)/SAMPLE_RATE:.1f}s)  "
                f"peak={peak:.4f}  rms={rms:.4f}"
            )

            if len(samples) < _FADE_SAMPLES * 2:
                return samples

            fade_in = np.linspace(0.0, 1.0, _FADE_SAMPLES, dtype=np.float32)
            samples[:_FADE_SAMPLES] *= fade_in

            fade_out = np.linspace(1.0, 0.0, _FADE_SAMPLES, dtype=np.float32)
            samples[-_FADE_SAMPLES:] *= fade_out

            return samples

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_rms(self) -> float:
        return self._current_rms

    def _audio_cb(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if self._recording:
            if self._skip_counter < _SKIP_CHUNKS:
                self._skip_counter += 1
                return
            chunk = indata[:, 0].copy()
            self._chunks.append(chunk)
            self._current_rms = float(np.sqrt(np.mean(chunk ** 2)))
```

**Step 2: Quick syntax check**

Run: `.venv/bin/python -c "import ast; ast.parse(open('thundertalk/core/audio.py').read()); print('audio.py: ok')"`
Expected: `audio.py: ok`

**Step 3: Smoke test the recorder against real hardware**

Run:
```bash
.venv/bin/python - <<'PY'
"""Sanity check: list devices, record 0.5s, stop. All PA calls go via executor."""
import time
from thundertalk.core.audio import AudioRecorder

print("devices:", AudioRecorder.list_devices()[:3])

r = AudioRecorder()
r.start()                       # default mic
time.sleep(0.5)
samples = r.stop()
print(f"captured: {None if samples is None else len(samples)} samples")
PY
```
Expected: a non-empty device list and a non-None sample count (~ several thousand).
If the local default mic is muted you may get None — that's fine; the point is no exception, no hang.

---

## Task 3: Update `settings_page.py` — drop direct `sd._terminate/_initialize`

**Files:**
- Modify: `thundertalk/ui/pages/settings_page.py:488-510` (`_refresh_mic_list` + `showEvent`)
- Verify: line 29 `import sounddevice as sd` is no longer needed in this file (remove if so)

**Step 1: Replace `_refresh_mic_list` to delegate to AudioRecorder**

Locate the current method:

```python
def _refresh_mic_list(self) -> None:
    """Re-scan audio devices and rebuild the mic dropdown."""
    self._mic_combo.blockSignals(True)
    while self._mic_combo.count() > 1:
        self._mic_combo.removeItem(1)
    try:
        sd._terminate()
        sd._initialize()
        for d in sd.query_devices():
            if d["max_input_channels"] > 0:
                self._mic_combo.addItem(d["name"])
    except Exception:
        pass
    current = self._settings.microphone
    if current != "auto":
        idx = self._mic_combo.findText(current)
        if idx >= 0:
            self._mic_combo.setCurrentIndex(idx)
    self._mic_combo.blockSignals(False)
```

Replace with:

```python
def _refresh_mic_list(self, *, force_rescan: bool = False) -> None:
    """Rebuild the mic dropdown from AudioRecorder's executor-backed
    device list. ``force_rescan=True`` triggers a Pa_Terminate+Initialize
    cycle on the audio worker thread (with watchdog) — only used when
    the user explicitly asks for a refresh."""
    from thundertalk.core.audio import AudioRecorder

    self._mic_combo.blockSignals(True)
    while self._mic_combo.count() > 1:
        self._mic_combo.removeItem(1)
    names = (
        AudioRecorder.refresh_devices()
        if force_rescan
        else AudioRecorder.list_devices()
    )
    for name in names:
        self._mic_combo.addItem(name)
    current = self._settings.microphone
    if current != "auto":
        idx = self._mic_combo.findText(current)
        if idx >= 0:
            self._mic_combo.setCurrentIndex(idx)
    self._mic_combo.blockSignals(False)
```

The `showEvent` keeps calling `self._refresh_mic_list()` (no `force_rescan`) — that's now the cheap, non-deadlocking path.

**Step 2: Drop the now-unused `sd` import**

If `sounddevice as sd` is only used by the old `_refresh_mic_list`, remove `import sounddevice as sd` from `settings_page.py:29`. Verify with:

Run: `grep -n "\bsd\." /Users/songallen/Desktop/ThunderTalk/thundertalk/ui/pages/settings_page.py`
Expected: no remaining usages. If any survive, leave the import.

**Step 3: Syntax check**

Run: `.venv/bin/python -c "import ast; ast.parse(open('thundertalk/ui/pages/settings_page.py').read()); print('settings_page.py: ok')"`
Expected: `settings_page.py: ok`

---

## Task 4: Bump version and rebuild

**Files:**
- Modify: `thundertalk/__init__.py` (`__version__ = "1.1.13"`)
- Modify: `pyproject.toml` (`version = "1.1.13"`)
- Refresh: `uv.lock` via `uv lock`

**Step 1: Bump both version files**

```bash
sed -i '' 's/__version__ = "1.1.12"/__version__ = "1.1.13"/' thundertalk/__init__.py
sed -i '' 's/version = "1.1.12"/version = "1.1.13"/' pyproject.toml
```

(Or use Edit tool. Either way, exactly one substitution per file.)

**Step 2: Refresh uv.lock**

Run: `uv lock`
Expected: `Updated thundertalk v1.1.12 -> v1.1.13`

**Step 3: Build the bundle**

Run: `.venv/bin/python build_macos.py 2>&1 | tail -10`
Expected: ends with `✅ Build + sign complete: dist/ThunderTalk.app`. (Several minutes — run in background.)

**Step 4: Zip the bundle**

Run:
```bash
cd dist && rm -f ThunderTalk-v1.1.13-macOS.zip \
  && ditto -c -k --keepParent ThunderTalk.app ThunderTalk-v1.1.13-macOS.zip \
  && ls -lh ThunderTalk-v1.1.13-macOS.zip
```
Expected: ~280 MB zip alongside the .app.

---

## Task 5: Commit, tag, push, release

**Step 1: Confirm pending changes match expectations**

Run: `git status --porcelain`
Expected, in some order:
```
 M pyproject.toml
 M thundertalk/__init__.py
 M thundertalk/core/audio.py
 M thundertalk/ui/pages/settings_page.py
 M uv.lock
?? thundertalk/core/audio_executor.py
?? tests/test_audio_executor.py
?? docs/plans/2026-05-06-audio-executor.md
```

**Step 2: Single release commit (project convention — no Co-Authored-By)**

Title: `v1.1.13: serialize all PortAudio calls onto a dedicated worker thread`

Body should explain:
- The v1.1.12 stream-stop watchdog left other PA entry points exposed (`Pa_Terminate` from Settings, `Pa_OpenStream` from start, etc.)
- Sample of PID 70158 captured 1607/1607 samples on `Pa_Terminate → AudioOutputUnitStop → CoreAudio HAL std::recursive_mutex::lock → __psynch_mutexwait`, triggered by Settings page's `showEvent → _refresh_mic_list → sd._terminate()`
- Generalised fix: `AudioExecutor` daemon thread now owns every PortAudio call; GUI waits with bounded watchdog (2–4 s depending on op)
- Settings page no longer calls `sd._terminate/_initialize` directly; uses the cached device list from `AudioRecorder.list_devices()`

**Step 3: Tag and push**

```bash
git tag v1.1.13
git push origin main
git push origin v1.1.13
```

**Step 4: Create GitHub release**

```bash
gh release create v1.1.13 dist/ThunderTalk-v1.1.13-macOS.zip \
  --title "ThunderTalk v1.1.13" \
  --notes "<release-notes>"
```

Release notes (markdown) should mirror the commit body but in user-facing language: "Settings page or hotkey could freeze the entire app when a Bluetooth/USB mic was hot-plugged or switched profile mid-session. v1.1.13 routes every PortAudio call through a dedicated worker thread, so a wedged audio device leaves the GUI fully responsive."

**Step 5: Verify the release is live and the asset is attached**

Run: `gh release view v1.1.13 --json tagName,name,assets,url`
Expected: a single `ThunderTalk-v1.1.13-macOS.zip` asset listed and `state=uploaded`.

---

## Verification matrix

Smoke checks before declaring done:

| Check | Command | Expected |
|---|---|---|
| Executor unit tests | `.venv/bin/python -m pytest tests/test_audio_executor.py -v` | 5 passed |
| Existing tests | `.venv/bin/python -m pytest -q` | no new failures |
| audio.py syntax | `.venv/bin/python -c "import thundertalk.core.audio"` | clean import |
| settings_page syntax | `.venv/bin/python -c "import thundertalk.ui.pages.settings_page"` | clean import |
| Live recorder smoke | (see Task 2 step 3) | sample count > 0 or graceful None |
| Release asset | `gh release view v1.1.13 --json assets` | one .zip uploaded |
| Local auto-update | Launch app, wait for updater popup | offers v1.1.13 |

---

## What this plan deliberately does NOT do

- **Does not remove `Pa_Initialize` from app startup.** The first PA call on the executor still does `Pa_Initialize` — but on the worker, with a watchdog. That's strictly better than the status quo and not worth a bigger change.
- **Does not migrate ASR / SeamlessM4T workers** — they don't touch PortAudio.
- **Does not add a "Refresh devices" button to Settings** — the plumbing (`refresh_devices()`) is in place; UI exposure can land in a later release without rework.
- **Does not try to "recover" from a wedged HAL.** Once the OS-level mutex is held by a stuck thread, only process restart fixes it. The executor's invariant is "GUI never freezes", not "audio always works".
