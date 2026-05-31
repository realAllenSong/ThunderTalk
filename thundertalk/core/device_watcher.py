"""Detect audio device hot-plug/unplug and BT profile transitions.

The recording UX broke for users in two reproducible ways before this:

1. Login-launch race: ``launch_at_startup=True`` puts ThunderTalk's
   first ``Pa_Initialize`` ahead of the OS finishing Bluetooth pairing.
   The saved input device is missing from the cached list; every
   subsequent recording silently uses the system default mic.

2. A2DP/HFP transition: a Bluetooth speaker like the Bose SoundLink
   exposes a microphone *only* in HFP profile. While in A2DP it appears
   in PortAudio with ``max_input_channels == 0`` and is filtered out.

Both are fixed by re-running ``Pa_Terminate + Pa_Initialize`` whenever
the OS reports a device topology change. macOS's CoreAudio exposes two
signals we monitor on the system object:

  - ``kAudioHardwarePropertyDevices``      — device list added/removed
  - ``kAudioHardwarePropertyDefaultInputDevice`` — default input changed

The second property is critical for the common case where the user
switches their default mic in System Settings without adding or removing
a device (e.g. switching between internal mic and already-paired AirPods).
Without monitoring it, PortAudio's cached default from the last
``Pa_Initialize`` stays stale and "auto" mode records from the wrong mic
until the app is restarted.

Non-macOS builds (or installations where the CoreAudio binding fails
to load) fall back to a 5-second QTimer that polls the cached
PortAudio list, with a heavier ``Pa_Terminate + Pa_Initialize`` every
30 seconds.

Threading
---------
- CoreAudio invokes the property listener on its own internal thread
  while holding HAL mutexes. We MUST NOT call back into sounddevice
  synchronously from there (deadlock). Instead the callback spawns a
  short-lived daemon thread which blocks on the existing
  ``AudioExecutor`` (single-threaded, watchdogged).
- ``devices_changed`` is a Qt signal. Emission is thread-safe; Qt's
  default ``Qt.AutoConnection`` queues delivery to the receiver's
  thread (typically the GUI main thread).
- ``_request_emit`` is an internal signal used to safely start the
  debounce timer from any thread (QTimer.start() must run on the
  GUI thread; Signal emission crosses thread boundaries transparently).

Debounce
--------
Bluetooth profile switches (A2DP→HFP) fire several rapid-fire
``kAudioHardwarePropertyDevices`` events within ~200ms. Without
debouncing, the dropdown would flash "no mic → has mic". We coalesce
these into a single UI update emitted 500ms after the last event.
The underlying PortAudio reinit still runs immediately on each event
(coalesced by the in-flight lock), so devices are usable as soon as
the OS settles — only the dropdown update is delayed.

Recording-aware
---------------
A device-change event during a live recording would tear down the
user's input stream as a side effect of ``Pa_Terminate``. When the
caller passes an ``AudioRecorder`` to ``start()``, we skip refresh
while ``is_recording`` is true. Fix-on-miss in ``audio.py`` plus the
next device-change event will reconcile state once recording stops.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import platform
import threading
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from thundertalk.core.audio import (
    AudioRecorder,
    _QUERY_TIMEOUT_S,
    _REINIT_TIMEOUT_S,
    _full_reinit,
    _query_input_device_names,
)
from thundertalk.core.audio_executor import AudioCallTimeout, get_executor


_POLL_INTERVAL_MS = 5_000
_HEAVY_REFRESH_EVERY_N_TICKS = 6  # 6 ticks * 5s = full re-enumeration every 30s
_DEBOUNCE_MS = 500                 # wait this long after last event before updating UI


class DeviceWatcher(QObject):
    """Emits ``devices_changed(list[str])`` whenever the set of input
    device names changes. Use ``get_watcher()`` to access the singleton."""

    devices_changed = Signal(list)

    # Internal signal: safely starts the debounce timer from any thread.
    # QTimer.start() must be called from the GUI thread; emitting this
    # signal from a non-GUI thread queues the call via AutoConnection.
    _request_emit = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._known_names: list[str] = []
        self._recorder: Optional[AudioRecorder] = None
        self._started = False

        # macOS listener state — held as instance attributes because
        # CoreAudio retains the function pointer; we must keep the
        # CFUNCTYPE wrapper alive for the listener's whole lifetime.
        self._listener_proc = None
        self._listener_addresses: list = []   # AudioObjectPropertyAddress structs
        self._coreaudio = None
        self._listener_registered = False

        # Cross-platform polling fallback.
        self._poll_timer: Optional[QTimer] = None
        self._poll_tick = 0

        # Coalesce bursts of CoreAudio events into one refresh + at most
        # one pending follow-up. Without this, a BT profile switch (which
        # fires multiple kAudioHardwarePropertyDevices events back to
        # back) would queue N serial Pa_Terminate cycles on the executor.
        self._refresh_lock = threading.Lock()
        self._refresh_in_flight = False
        self._refresh_pending = False

        # Debounced emission: accumulate the latest device list here,
        # then emit devices_changed 500ms after the last update.
        # _pending_emit_names is written from handoff threads and read on
        # the GUI thread; access is protected by _pending_emit_lock.
        self._pending_emit_names: list[str] = []
        self._pending_emit_lock = threading.Lock()

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._on_debounce_timeout)
        self._request_emit.connect(self._on_request_emit)

    def start(self, recorder: Optional[AudioRecorder] = None) -> None:
        """Begin watching. ``recorder`` is consulted before every refresh
        so we don't tear down a live mic stream as a side effect; pass
        the application's singleton ``AudioRecorder`` to opt in."""
        if self._started:
            return
        self._started = True
        self._recorder = recorder

        # Seed the known set so the first real change emits exactly once.
        try:
            self._known_names = list(AudioRecorder.list_devices())
        except Exception:
            self._known_names = []

        if platform.system() == "Darwin" and self._try_register_coreaudio_listener():
            print(
                "[DeviceWatcher] CoreAudio listeners registered "
                "(kAudioHardwarePropertyDevices + kAudioHardwarePropertyDefaultInputDevice)"
            )
            return

        print(
            "[DeviceWatcher] CoreAudio listener unavailable; "
            "polling every 5s with full refresh every 30s"
        )
        self._start_polling()

    def stop(self) -> None:
        if self._listener_registered:
            self._unregister_coreaudio_listener()
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self._started = False

    # ── macOS: CoreAudio property listener ────────────────────────────

    def _try_register_coreaudio_listener(self) -> bool:
        try:
            path = ctypes.util.find_library("CoreAudio")
            if not path:
                return False
            lib = ctypes.cdll.LoadLibrary(path)

            class _Address(ctypes.Structure):
                _fields_ = [
                    ("mSelector", ctypes.c_uint32),
                    ("mScope", ctypes.c_uint32),
                    ("mElement", ctypes.c_uint32),
                ]

            listener_proc_t = ctypes.CFUNCTYPE(
                ctypes.c_int32,    # OSStatus
                ctypes.c_uint32,   # AudioObjectID
                ctypes.c_uint32,   # UInt32 nAddresses
                ctypes.c_void_p,   # const AudioObjectPropertyAddress* (opaque)
                ctypes.c_void_p,   # void* clientData
            )

            lib.AudioObjectAddPropertyListener.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(_Address),
                listener_proc_t,
                ctypes.c_void_p,
            ]
            lib.AudioObjectAddPropertyListener.restype = ctypes.c_int32
            lib.AudioObjectRemovePropertyListener.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(_Address),
                listener_proc_t,
                ctypes.c_void_p,
            ]
            lib.AudioObjectRemovePropertyListener.restype = ctypes.c_int32

            def _fourcc(text: str) -> int:
                v = 0
                for ch in text.encode("latin-1"):
                    v = (v << 8) | ch
                return v

            k_audio_object_system_object = 1
            k_scope_global = _fourcc("glob")
            k_element_master = 0

            # The two properties we care about:
            #   kAudioHardwarePropertyDevices             = 'dev#'
            #   kAudioHardwarePropertyDefaultInputDevice  = 'dIn '
            properties = [_fourcc("dev#"), _fourcc("dIn ")]
            addresses = [
                _Address(sel, k_scope_global, k_element_master)
                for sel in properties
            ]

            # Strong ref: CoreAudio retains only the raw function pointer.
            self._listener_proc = listener_proc_t(self._native_callback)
            self._coreaudio = lib

            any_ok = False
            for addr in addresses:
                status = lib.AudioObjectAddPropertyListener(
                    k_audio_object_system_object,
                    ctypes.byref(addr),
                    self._listener_proc,
                    None,
                )
                if status == 0:
                    self._listener_addresses.append(addr)
                    any_ok = True
                else:
                    print(
                        f"[DeviceWatcher] AudioObjectAddPropertyListener "
                        f"returned status={status} for selector={addr.mSelector:#010x}"
                    )

            if not any_ok:
                self._listener_proc = None
                self._listener_addresses = []
                self._coreaudio = None
                return False

            self._listener_registered = True
            return True
        except Exception as e:
            print(f"[DeviceWatcher] CoreAudio listener setup failed: {e}")
            return False

    def _unregister_coreaudio_listener(self) -> None:
        if not self._listener_registered or self._coreaudio is None:
            return
        try:
            for addr in self._listener_addresses:
                self._coreaudio.AudioObjectRemovePropertyListener(
                    1,  # kAudioObjectSystemObject
                    ctypes.byref(addr),
                    self._listener_proc,
                    None,
                )
        except Exception as e:
            print(f"[DeviceWatcher] CoreAudio listener unregister failed: {e}")
        finally:
            self._listener_registered = False

    def _native_callback(self, in_object_id, n_addresses, addresses_ptr, client_data):
        """Fires on a CoreAudio internal thread for any registered property.

        Hand off to a short-lived daemon thread; CoreAudio holds HAL
        mutexes during this call and would deadlock if we touched
        PortAudio synchronously.
        """
        threading.Thread(
            target=self._handle_change,
            name="device-watcher-handoff",
            daemon=True,
        ).start()
        return 0  # noErr

    # ── Cross-platform: refresh logic ────────────────────────────────

    def _handle_change(self) -> None:
        # Coalesce: if a refresh is already running, mark a pending one
        # and let the current loop pick it up before exiting.
        with self._refresh_lock:
            if self._refresh_in_flight:
                self._refresh_pending = True
                return
            self._refresh_in_flight = True

        try:
            while True:
                with self._refresh_lock:
                    self._refresh_pending = False
                self._do_refresh()
                with self._refresh_lock:
                    if not self._refresh_pending:
                        self._refresh_in_flight = False
                        return

        except Exception as e:
            print(f"[DeviceWatcher] refresh loop crashed: {e}")
            with self._refresh_lock:
                self._refresh_in_flight = False
                self._refresh_pending = False

    def _do_refresh(self) -> None:
        # Pa_Terminate during a live recording would kill the user's
        # input stream. The fix-on-miss path in audio.start() catches
        # any topology change once recording ends; we simply skip here.
        if self._recorder is not None and self._recorder.is_recording:
            print(
                "[DeviceWatcher] device change during active recording "
                "— deferring refresh until idle"
            )
            return

        ex = get_executor()
        try:
            ex.call(_full_reinit, timeout=_REINIT_TIMEOUT_S)
            names = ex.call(_query_input_device_names, timeout=_QUERY_TIMEOUT_S)
        except AudioCallTimeout:
            print(
                "[DeviceWatcher] refresh timed out — CoreAudio HAL wedged; "
                "skipping this notification"
            )
            return
        self._queue_emit(names)

    # ── Debounced emission ────────────────────────────────────────────

    def _queue_emit(self, names: list[str]) -> None:
        """Accumulate latest device list for debounced emission.

        Safe to call from any thread. Suppresses scheduling if ``names``
        matches what's already pending (prevents polling fallback from
        resetting the debounce timer every 5s while devices are stable).
        """
        with self._pending_emit_lock:
            if names == self._pending_emit_names:
                return
            self._pending_emit_names = list(names)
        # Qt AutoConnection: if called from a non-GUI thread, delivery is
        # queued to the GUI thread, so _on_request_emit always runs there.
        self._request_emit.emit()

    @Slot()
    def _on_request_emit(self) -> None:
        """Start (or restart) the debounce timer. Always runs on GUI thread."""
        self._debounce_timer.start()

    @Slot()
    def _on_debounce_timeout(self) -> None:
        """500ms after the last device change: emit if list actually changed."""
        with self._pending_emit_lock:
            names = list(self._pending_emit_names)
        if names == self._known_names:
            return
        added = sorted(set(names) - set(self._known_names))
        removed = sorted(set(self._known_names) - set(names))
        msg = []
        if added:
            msg.append(f"+{added}")
        if removed:
            msg.append(f"-{removed}")
        print(f"[DeviceWatcher] input devices changed: {' '.join(msg) or '(reorder)'}")
        self._known_names = list(names)
        self.devices_changed.emit(list(names))

    # ── Polling fallback ─────────────────────────────────────────────

    def _start_polling(self) -> None:
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_tick_handler)
        self._poll_timer.start()

    def _poll_tick_handler(self) -> None:
        self._poll_tick += 1
        if self._recorder is not None and self._recorder.is_recording:
            return
        if self._poll_tick % _HEAVY_REFRESH_EVERY_N_TICKS == 0:
            try:
                names = AudioRecorder.refresh_devices()
            except Exception:
                names = []
        else:
            try:
                names = AudioRecorder.list_devices()
            except Exception:
                names = []
        self._queue_emit(names)


_default: Optional[DeviceWatcher] = None


def get_watcher() -> DeviceWatcher:
    """Module-level singleton. Construct once in ``main()``; call
    ``start(recorder)`` after the AudioRecorder exists."""
    global _default
    if _default is None:
        _default = DeviceWatcher()
    return _default
