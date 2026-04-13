"""Silence / restore system speakers during voice recording.

On macOS we combine:
- CoreAudio per-device snapshots (VirtualMasterVolume + mute) for every duckable device
- AppleScript `get/set volume settings` for the **system output volume** (menu-bar slider),
  which many apps (Chrome, Bluetooth) still follow even when device-level APIs disagree

macOS  — CoreAudio (ctypes) + NSAppleScript aggregate volume
Linux  — pactl / amixer
Windows — nircmd
"""

from __future__ import annotations

import ctypes
import ctypes.util
import platform
import subprocess
import threading

_we_muted: bool = False
# macOS: device_id -> (volume_scalar_or_None, muted_or_None) captured before ducking
_darwin_duck_snapshots: dict[int, tuple[float | None, bool | None]] = {}
# macOS: (output volume 0..100, output muted) from AppleScript before ducking
_darwin_osascript_snapshot: tuple[int, bool] | None = None
_lock = threading.Lock()
_SYSTEM = platform.system()

# ---------------------------------------------------------------------------
# macOS — CoreAudio helpers
# ---------------------------------------------------------------------------

if _SYSTEM == "Darwin":
    from Foundation import NSAppleScript

    def _osascript_run(source: str) -> str | None:
        """Run AppleScript in-process; return string result or None on failure."""
        script = NSAppleScript.alloc().initWithSource_(source)
        result, error = script.executeAndReturnError_(None)
        if error:
            print(f"[Audio] AppleScript error: {error}")
            return None
        if result:
            return result.stringValue()
        return ""

    def _osascript_read_volume_settings() -> tuple[int, bool] | None:
        vol_s = _osascript_run("output volume of (get volume settings)")
        mut_s = _osascript_run("output muted of (get volume settings)")
        if vol_s is None or mut_s is None:
            return None
        try:
            vol = int(str(vol_s).strip())
        except (TypeError, ValueError):
            return None
        muted = str(mut_s).strip().lower() == "true"
        return (max(0, min(100, vol)), muted)

    def _osascript_apply_volume_settings(volume: int, muted: bool) -> bool:
        v = max(0, min(100, int(volume)))
        flag = "true" if muted else "false"
        src = f"set volume output volume {v}\nset volume output muted {flag}"
        script = NSAppleScript.alloc().initWithSource_(src)
        _result, error = script.executeAndReturnError_(None)
        if error:
            print(f"[Audio] AppleScript set volume failed: {error}")
            return False
        return True

    class _AudioObjectPropertyAddress(ctypes.Structure):
        _fields_ = [
            ("mSelector", ctypes.c_uint32),
            ("mScope", ctypes.c_uint32),
            ("mElement", ctypes.c_uint32),
        ]

    _coreaudio = None
    _coreaudio_path = ctypes.util.find_library("CoreAudio")
    if _coreaudio_path:
        _coreaudio = ctypes.cdll.LoadLibrary(_coreaudio_path)
        _coreaudio.AudioObjectGetPropertyData.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_AudioObjectPropertyAddress),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        _coreaudio.AudioObjectGetPropertyData.restype = ctypes.c_int32
        _coreaudio.AudioObjectSetPropertyData.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_AudioObjectPropertyAddress),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        _coreaudio.AudioObjectSetPropertyData.restype = ctypes.c_int32
        _coreaudio.AudioObjectHasProperty.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_AudioObjectPropertyAddress),
        ]
        _coreaudio.AudioObjectHasProperty.restype = ctypes.c_bool
        _coreaudio.AudioObjectGetPropertyDataSize.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_AudioObjectPropertyAddress),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        _coreaudio.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32

    def _fourcc(text: str) -> int:
        value = 0
        for ch in text.encode("latin-1"):
            value = (value << 8) | ch
        return value

    _K_AUDIO_OBJECT_SYSTEM_OBJECT = 1
    _K_AUDIO_OBJECT_PROPERTY_ELEMENT_MASTER = 0
    _K_AUDIO_HARDWARE_PROPERTY_DEFAULT_OUTPUT_DEVICE = _fourcc("dOut")
    _K_AUDIO_HARDWARE_PROPERTY_DEVICES = _fourcc("dev#")
    _K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL = _fourcc("glob")
    _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT = _fourcc("outp")
    _K_AUDIO_HARDWARE_SERVICE_DEVICE_PROPERTY_VIRTUAL_MASTER_VOLUME = _fourcc("vmvc")
    _K_AUDIO_DEVICE_PROPERTY_MUTE = _fourcc("mute")

    def _addr(selector: int, scope: int, element: int = _K_AUDIO_OBJECT_PROPERTY_ELEMENT_MASTER) -> _AudioObjectPropertyAddress:
        return _AudioObjectPropertyAddress(selector, scope, element)

    def _get_default_output_device_darwin() -> int | None:
        if _coreaudio is None:
            return None
        device_id = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(device_id))
        address = _addr(
            _K_AUDIO_HARDWARE_PROPERTY_DEFAULT_OUTPUT_DEVICE,
            _K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL,
        )
        status = _coreaudio.AudioObjectGetPropertyData(
            _K_AUDIO_OBJECT_SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(device_id),
        )
        if status != 0:
            print(f"[Audio] Failed to get default output device: status={status}")
            return None
        return int(device_id.value)

    def _has_property_darwin(device_id: int, selector: int, scope: int) -> bool:
        if _coreaudio is None:
            return False
        address = _addr(selector, scope)
        return bool(_coreaudio.AudioObjectHasProperty(device_id, ctypes.byref(address)))

    def _get_output_volume_darwin(device_id: int | None = None) -> float | None:
        if _coreaudio is None:
            return None
        target_id = device_id if device_id is not None else _get_default_output_device_darwin()
        if target_id is None:
            return None
        address = _addr(
            _K_AUDIO_HARDWARE_SERVICE_DEVICE_PROPERTY_VIRTUAL_MASTER_VOLUME,
            _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT,
        )
        volume = ctypes.c_float(0.0)
        size = ctypes.c_uint32(ctypes.sizeof(volume))
        status = _coreaudio.AudioObjectGetPropertyData(
            target_id,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(volume),
        )
        if status != 0:
            print(f"[Audio] Failed to get output volume: status={status} device={target_id}")
            return None
        return float(volume.value)

    def _set_output_volume_darwin(volume: float, device_id: int | None = None) -> bool:
        if _coreaudio is None:
            return False
        target_id = device_id if device_id is not None else _get_default_output_device_darwin()
        if target_id is None:
            return False
        address = _addr(
            _K_AUDIO_HARDWARE_SERVICE_DEVICE_PROPERTY_VIRTUAL_MASTER_VOLUME,
            _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT,
        )
        value = ctypes.c_float(max(0.0, min(1.0, float(volume))))
        status = _coreaudio.AudioObjectSetPropertyData(
            target_id,
            ctypes.byref(address),
            0,
            None,
            ctypes.c_uint32(ctypes.sizeof(value)),
            ctypes.byref(value),
        )
        if status != 0:
            print(f"[Audio] Failed to set output volume: status={status} device={target_id}")
            return False
        actual = _get_output_volume_darwin(target_id)
        return actual is not None and abs(actual - value.value) < 0.01

    def _get_output_muted_darwin(device_id: int | None = None) -> bool | None:
        if _coreaudio is None:
            return None
        target_id = device_id if device_id is not None else _get_default_output_device_darwin()
        if target_id is None:
            return None
        if not _has_property_darwin(
            target_id,
            _K_AUDIO_DEVICE_PROPERTY_MUTE,
            _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT,
        ):
            return None
        address = _addr(_K_AUDIO_DEVICE_PROPERTY_MUTE, _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT)
        value = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(value))
        status = _coreaudio.AudioObjectGetPropertyData(
            target_id,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(value),
        )
        if status != 0:
            print(f"[Audio] Failed to get output mute: status={status} device={target_id}")
            return None
        return bool(value.value)

    def _set_output_muted_darwin(muted: bool, device_id: int | None = None) -> bool:
        if _coreaudio is None:
            return False
        target_id = device_id if device_id is not None else _get_default_output_device_darwin()
        if target_id is None:
            return False
        if not _has_property_darwin(
            target_id,
            _K_AUDIO_DEVICE_PROPERTY_MUTE,
            _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT,
        ):
            return True
        address = _addr(_K_AUDIO_DEVICE_PROPERTY_MUTE, _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT)
        value = ctypes.c_uint32(1 if muted else 0)
        status = _coreaudio.AudioObjectSetPropertyData(
            target_id,
            ctypes.byref(address),
            0,
            None,
            ctypes.c_uint32(ctypes.sizeof(value)),
            ctypes.byref(value),
        )
        if status != 0:
            print(f"[Audio] Failed to set output mute: status={status} device={target_id}")
            return False
        actual = _get_output_muted_darwin(target_id)
        return actual is None or actual is muted

    def _list_audio_device_ids_darwin() -> list[int]:
        """Return all CoreAudio device IDs (includes inputs and outputs)."""
        if _coreaudio is None:
            return []
        address = _addr(
            _K_AUDIO_HARDWARE_PROPERTY_DEVICES,
            _K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL,
            _K_AUDIO_OBJECT_PROPERTY_ELEMENT_MASTER,
        )
        data_size = ctypes.c_uint32(0)
        status = _coreaudio.AudioObjectGetPropertyDataSize(
            _K_AUDIO_OBJECT_SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(data_size),
        )
        if status != 0:
            print(f"[Audio] AudioObjectGetPropertyDataSize(devices) failed: {status}")
            return []
        if data_size.value == 0:
            return []
        elem = ctypes.sizeof(ctypes.c_uint32)
        count = data_size.value // elem
        if count <= 0:
            return []
        buf = (ctypes.c_uint32 * count)()
        read_size = ctypes.c_uint32(data_size.value)
        status2 = _coreaudio.AudioObjectGetPropertyData(
            _K_AUDIO_OBJECT_SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(read_size),
            buf,
        )
        if status2 != 0:
            print(f"[Audio] AudioObjectGetPropertyData(devices) failed: {status2}")
            return []
        return [int(buf[i]) for i in range(count)]

    def _is_duckable_output_device(device_id: int) -> bool:
        """True if we can mute/duck this device via CoreAudio output controls."""
        return (
            _has_property_darwin(
                device_id,
                _K_AUDIO_HARDWARE_SERVICE_DEVICE_PROPERTY_VIRTUAL_MASTER_VOLUME,
                _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT,
            )
            or _has_property_darwin(
                device_id,
                _K_AUDIO_DEVICE_PROPERTY_MUTE,
                _K_AUDIO_DEVICE_PROPERTY_SCOPE_OUTPUT,
            )
        )


def mute_system_audio() -> None:
    """Mute system output. SYNCHRONOUS so mic doesn't pick up residual audio."""
    global _darwin_duck_snapshots, _darwin_osascript_snapshot, _we_muted
    print("[Audio] mute_system_audio() called")
    with _lock:
        if _SYSTEM == "Darwin":
            _darwin_osascript_snapshot = None
            _darwin_duck_snapshots.clear()
            device_ids = _list_audio_device_ids_darwin()
            if not device_ids:
                fallback = _get_default_output_device_darwin()
                if fallback is not None:
                    device_ids = [fallback]
                    print(f"[Audio]   device enumeration empty, fallback default={fallback}")
            for dev_id in device_ids:
                if not _is_duckable_output_device(dev_id):
                    continue
                vol = _get_output_volume_darwin(dev_id)
                mut = _get_output_muted_darwin(dev_id)
                _darwin_duck_snapshots[dev_id] = (vol, mut)
                _set_output_muted_darwin(True, dev_id)
                if vol is not None:
                    _set_output_volume_darwin(0.0, dev_id)

            _we_muted = len(_darwin_duck_snapshots) > 0
            o_snap = _osascript_read_volume_settings()
            if o_snap is not None:
                _darwin_osascript_snapshot = o_snap
                if _osascript_apply_volume_settings(0, True):
                    _we_muted = True
                    print(
                        "[Audio]   + aggregate output via AppleScript "
                        f"(saved vol={o_snap[0]} muted={o_snap[1]})"
                    )
            print(
                "[Audio]   silenced via CoreAudio "
                f"(devices={len(_darwin_duck_snapshots)} "
                f"ids={sorted(_darwin_duck_snapshots.keys())}) "
                f"result={'OK' if _we_muted else 'FAILED'}"
            )
            return

        elif _SYSTEM == "Linux":
            try:
                subprocess.run(
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"],
                    check=False, timeout=3,
                )
                _we_muted = True
            except FileNotFoundError:
                try:
                    subprocess.run(
                        ["amixer", "set", "Master", "mute"],
                        check=False, timeout=3,
                    )
                    _we_muted = True
                except Exception:
                    _we_muted = False

        elif _SYSTEM == "Windows":
            try:
                subprocess.run(
                    ["nircmd", "mutesysvolume", "1"],
                    check=False, timeout=3,
                )
                _we_muted = True
            except Exception:
                _we_muted = False


def unmute_system_audio() -> None:
    """Unmute system output. SYNCHRONOUS for reliability."""
    global _darwin_duck_snapshots, _darwin_osascript_snapshot, _we_muted
    print(f"[Audio] unmute_system_audio() called  "
          f"(_we_muted={_we_muted}, devices={len(_darwin_duck_snapshots)})")
    with _lock:
        if not _we_muted:
            print("[Audio]   skipped (we didn't mute)")
            return
        snapshots = dict(_darwin_duck_snapshots)
        _darwin_duck_snapshots.clear()
        o_snap = _darwin_osascript_snapshot
        _darwin_osascript_snapshot = None
        _we_muted = False

    if _SYSTEM == "Darwin":
        ok_all = True
        for dev_id, (prev_vol, prev_mut) in sorted(snapshots.items()):
            vol_ok = True
            if prev_vol is not None:
                vol_ok = _set_output_volume_darwin(prev_vol, dev_id)
            muted_target = bool(prev_mut) if prev_mut is not None else False
            mut_ok = _set_output_muted_darwin(muted_target, dev_id)
            ok_all = ok_all and vol_ok and mut_ok
        print(
            "[Audio]   restored via CoreAudio "
            f"(count={len(snapshots)} ids={sorted(snapshots.keys())}) ok={ok_all}"
        )
        if o_snap is not None:
            o_ok = _osascript_apply_volume_settings(o_snap[0], o_snap[1])
            print(
                "[Audio]   restored aggregate via AppleScript "
                f"(vol={o_snap[0]} muted={o_snap[1]}) ok={o_ok}"
            )
    elif _SYSTEM == "Linux":
        try:
            subprocess.run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
                check=False, timeout=3,
            )
        except FileNotFoundError:
            subprocess.run(
                ["amixer", "set", "Master", "unmute"],
                check=False, timeout=3,
            )
    elif _SYSTEM == "Windows":
        try:
            subprocess.run(
                ["nircmd", "mutesysvolume", "0"],
                check=False, timeout=3,
            )
        except Exception:
            pass


def force_unmute() -> None:
    """Emergency unmute — ignores flags, just unmutes."""
    global _darwin_duck_snapshots, _darwin_osascript_snapshot, _we_muted
    with _lock:
        snapshots = dict(_darwin_duck_snapshots)
        _darwin_duck_snapshots.clear()
        o_snap = _darwin_osascript_snapshot
        _darwin_osascript_snapshot = None
        _we_muted = False
    if _SYSTEM == "Darwin":
        if snapshots:
            for dev_id, (prev_vol, prev_mut) in sorted(snapshots.items()):
                if prev_vol is not None:
                    _set_output_volume_darwin(prev_vol, dev_id)
                _set_output_muted_darwin(bool(prev_mut) if prev_mut is not None else False, dev_id)
        else:
            dev = _get_default_output_device_darwin()
            if dev:
                _set_output_muted_darwin(False, dev)
        if o_snap is not None:
            _osascript_apply_volume_settings(o_snap[0], o_snap[1])
    elif _SYSTEM == "Linux":
        try:
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
                           check=False, timeout=3)
        except Exception:
            pass
    elif _SYSTEM == "Windows":
        try:
            subprocess.run(["nircmd", "mutesysvolume", "0"],
                           check=False, timeout=3)
        except Exception:
            pass
