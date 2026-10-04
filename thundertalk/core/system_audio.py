"""Serialized, recoverable speaker ducking for recording sessions.

macOS snapshots native Float32 controls, never converts percentages to scalars.
Only the default output (and aggregate members) is silenced. The worker polls
routes every 100 ms while recording. Detectable user changes win: preserve the
new volume/mute, releasing only an unchanged mute that we applied ourselves.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import itertools
import json
import os
import platform
import queue
import subprocess
import threading
import time
import traceback
from concurrent.futures import Future
from pathlib import Path
from typing import Callable

_SYSTEM = platform.system()
_lock = threading.Lock()  # protects request generations, never held over OS calls
_generation = itertools.count(1)
_executor_lock = threading.Lock()
_system_audio_executor = None


def _fourcc(value: str) -> int:
    return int.from_bytes(value.encode("ascii"), "big")


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                ("element", ctypes.c_uint32)]


class _AudioBuffer(ctypes.Structure):
    _fields_ = [("channels", ctypes.c_uint32), ("size", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


class _AudioBufferList(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("buffers", _AudioBuffer * 1)]


class _DarwinAudio:
    """Small CoreAudio/AppleScript boundary, replaceable with a fake in tests."""

    def __init__(self):
        self.ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
        self.cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        self.ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address),
            ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        self.ca.AudioObjectSetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address),
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        self.ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address),
            ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.ca.AudioObjectHasProperty.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address)]
        self.ca.AudioObjectHasProperty.restype = ctypes.c_bool
        self.ca.AudioObjectIsPropertySettable.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address),
                                                        ctypes.POINTER(ctypes.c_ubyte)]
        self.cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
                                              ctypes.c_uint32]
        self.cf.CFStringGetCString.restype = ctypes.c_bool
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]

    def _address(self, selector, element=0, scope="outp"):
        return _Address(_fourcc(selector), _fourcc(scope), element)

    def _read(self, device, selector, element=0, kind=ctypes.c_uint32, scope="outp"):
        address = self._address(selector, element, scope)
        if not self.ca.AudioObjectHasProperty(device, ctypes.byref(address)):
            return None
        value = kind()
        size = ctypes.c_uint32(ctypes.sizeof(value))
        status = self.ca.AudioObjectGetPropertyData(device, ctypes.byref(address), 0, None,
                                                    ctypes.byref(size), ctypes.byref(value))
        return value.value if status == 0 else None

    def _data(self, device, selector, scope="glob"):
        address = self._address(selector, scope=scope)
        size = ctypes.c_uint32()
        if self.ca.AudioObjectGetPropertyDataSize(device, ctypes.byref(address), 0, None,
                                                  ctypes.byref(size)) or not size.value:
            return None
        data = ctypes.create_string_buffer(size.value)
        status = self.ca.AudioObjectGetPropertyData(device, ctypes.byref(address), 0, None,
                                                    ctypes.byref(size), data)
        return data if status == 0 else None

    def _ids(self, device, selector):
        data = self._data(device, selector)
        return list((ctypes.c_uint32 * (len(data) // 4)).from_buffer(data)) if data else []

    def uid(self, device):
        value = self._read(device, "uid ", kind=ctypes.c_void_p, scope="glob")
        if not value:
            return None
        try:
            buf = ctypes.create_string_buffer(4096)
            return buf.value.decode("utf-8") if self.cf.CFStringGetCString(
                value, buf, len(buf), 0x08000100) else None
        finally:
            self.cf.CFRelease(value)

    def devices(self):
        return {uid: device for device in self._ids(1, "dev#")
                if (uid := self.uid(device)) is not None}

    def default(self):
        device = self._read(1, "dOut", scope="glob")
        return self.uid(device) if device else None

    def route(self):
        devices = self.devices()
        uid = self.default()
        if uid not in devices:
            return []
        result = [uid]
        seen = {devices[uid]}
        pending = [devices[uid]]
        while pending:
            for child in self._ids(pending.pop(), "agrp"):
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
                    child_uid = self.uid(child)
                    if child_uid:
                        result.append(child_uid)
        return result

    def channels(self, uid):
        device = self.devices().get(uid)
        if device is None:
            return 0
        data = self._data(device, "slay", "outp")
        if not data:
            return 0
        count = ctypes.c_uint32.from_buffer(data).value
        offset = _AudioBufferList.buffers.offset
        if offset + count * ctypes.sizeof(_AudioBuffer) > len(data):
            return 0
        return sum(_AudioBuffer.from_buffer(data, offset + i * ctypes.sizeof(_AudioBuffer)).channels
                   for i in range(count))

    def controls(self, uid):
        device = self.devices().get(uid)
        if device is None:
            return None
        result = {}
        for selector in ("volm", "mute"):
            for element in range(self.channels(uid) + 1):
                value = self._read(device, selector, element,
                                   ctypes.c_float if selector == "volm" else ctypes.c_uint32)
                if value is not None:
                    result[f"{selector}:{element}"] = bool(value) if selector == "mute" else value
        # Virtual main is a last resort; writing it can disturb channel balance.
        if not any(k.startswith("volm:") for k in result):
            value = self._read(device, "vmvc", kind=ctypes.c_float)
            if value is not None:
                result["vmvc:0"] = value
        return result

    def writable(self, uid, key):
        device = self.devices().get(uid)
        if device is None:
            return False
        selector, element = key.split(":")
        address = self._address(selector, int(element))
        value = ctypes.c_ubyte()
        return not self.ca.AudioObjectIsPropertySettable(device, ctypes.byref(address),
                                                         ctypes.byref(value)) and bool(value.value)

    def write(self, uid, key, value):
        device = self.devices().get(uid)
        if device is None or not self.writable(uid, key):
            return False
        selector, element = key.split(":")
        address = self._address(selector, int(element))
        data = ctypes.c_uint32(bool(value)) if selector == "mute" else ctypes.c_float(value)
        status = self.ca.AudioObjectSetPropertyData(device, ctypes.byref(address), 0, None,
                                                    ctypes.sizeof(data), ctypes.byref(data))
        return status == 0

    def _script(self, source):
        try:
            result = subprocess.run(["/usr/bin/osascript", "-e", source], capture_output=True,
                                    text=True, timeout=2)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def apple_read(self):
        result = self._script("set s to get volume settings\n"
                              "return {output volume of s, output muted of s}")
        try:
            volume, muted = result.split(", ")
            return [int(volume), muted == "true"]
        except (AttributeError, ValueError):
            return None

    def apple_mute(self, muted):
        return self._script(f"set volume output muted {'true' if muted else 'false'}") is not None

    def apple_volume(self, volume):
        return self._script(f"set volume output volume {int(volume)}") is not None


class _DuckingController:
    def __init__(self, backend, path: Path):
        self.backend = backend
        self.path = path
        self.lock = threading.RLock()
        self.sessions = set()
        self.saved = {}
        self._journal_lock = None
        self._loaded = False
        self._next_retry = 0.0

    def _load(self):
        if self._loaded:
            return
        # Prevent another app instance from recovering our live recording.
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path.with_suffix(".lock"), "a+b")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                data = json.loads(self.path.read_text())
                if data["version"] != 1 or not isinstance(data["devices"], dict):
                    raise ValueError("invalid audio recovery journal")
                self.saved = data["devices"]
        except BaseException:
            handle.close()
            raise
        self._journal_lock = handle
        self._loaded = True

    def _persist(self):
        if not self.saved:
            self.path.unlink(missing_ok=True)
            return
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "devices": self.saved}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.path)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _changed(self, saved, current, apple):
        if current is None:
            return False  # disconnected, retain journal by UID
        expected = saved["expected"]
        restoring = saved.get("restoring", False) or saved.get("applying", False)
        changed = any(k in current and current[k] != v and
                      not (restoring and current[k] == saved["original"][k])
                      for k, v in expected.items())
        if saved["apple_owned"] and apple is not None:
            changed |= apple != saved["apple_expected"] and not (
                restoring and apple == saved["apple_original"])
        return changed

    def _try_persist(self):
        # Restoration must still run if the disk filled up after we muted.
        # The previous durable journal already contains the original state.
        try:
            self._persist()
        except OSError as exc:
            print(f"[Audio] Could not update recovery journal: {exc}")

    def _observe(self, uid, saved, *, strict=True):
        current = self.backend.controls(uid)
        apple = self.backend.apple_read() if saved["apple_owned"] and self.backend.default() == uid else None
        if not saved["user_changed"] and self._changed(saved, current, apple):
            saved["user_changed"] = True
            if strict:
                self._persist()
            else:
                self._try_persist()
            print("[Audio] User changed output controls; preserving their change")
        return current, apple

    def _silenced(self, uid, controls):
        if controls is None:
            return False
        if controls.get("mute:0") is True:
            return True
        if any(controls.get(k) == 0 for k in ("volm:0", "vmvc:0")):
            return True
        channels = self.backend.channels(uid)
        return channels > 0 and all(controls.get(f"mute:{i}") is True or
                                   controls.get(f"volm:{i}") == 0
                                   for i in range(1, channels + 1))

    def _apply(self, uid, saved, targets):
        # Write-ahead expected values make a crash between writes recoverable.
        saved["expected"].update(targets)
        # A writable master can also change read-only channel mirrors.
        for prefix in ("mute", "volm"):
            master = f"{prefix}:0"
            if master in targets:
                saved["expected"].update({k: targets[master] for k in saved["original"]
                                          if k.startswith(f"{prefix}:")})
        saved["applying"] = True
        self._persist()
        for key, value in targets.items():
            self.backend.write(uid, key, value)
        current = self.backend.controls(uid)
        if current is not None:
            # A volume change while we write only mute is external, not ours.
            saved["user_changed"] |= self._changed(saved, current, None)
            saved["expected"] = current.copy()
            saved["applying"] = False
            self._persist()
        return self._silenced(uid, current)

    def _duck(self, uid):
        if uid in self.saved:
            return  # never snapshot a ducked device again
        original = self.backend.controls(uid)
        if original is None:
            return
        apple = self.backend.apple_read() if self.backend.default() == uid else None
        saved = {"original": original, "expected": original.copy(), "apple_original": apple,
                 "apple_expected": apple, "apple_owned": False, "user_changed": False}
        self.saved[uid] = saved
        self._persist()  # fail closed: never mute without a durable snapshot
        if self._silenced(uid, original):
            return
        writable = {k for k in original if self.backend.writable(uid, k)}
        mutes = {k: True for k in writable if k.startswith("mute:")}
        if mutes and self._apply(uid, saved, mutes):
            return
        volumes = {k: 0.0 for k in writable if k.startswith(("volm:", "vmvc:"))}
        if volumes and self._apply(uid, saved, volumes):
            return
        # AppleScript is only a fallback for the same current output, never a
        # second percentage-volume authority on top of native scalars.
        if apple is not None and self.backend.default() == uid:
            if self._apple_fallback(uid, saved, apple):
                return
        print("[Audio] Output cannot be silenced with its available controls; skipping")

    def _apple_fallback(self, uid, saved, apple):
        # AppleScript has no device argument. Snapshot other outputs read-only
        # before its global write, in case the route switches inside that call.
        candidates = {uid}
        added = set()
        originals = {}
        for other in self.backend.devices():
            if other == uid or not self.backend.channels(other):
                continue
            original = self.backend.controls(other)
            if original is None or "mute:0" not in original:
                # There is no way to read an AppleScript-only non-default
                # output's prior mute flag without switching the user's route.
                print("[Audio] AppleScript fallback skipped: alternate output mute state is unreadable")
                return False
            originals[other] = original
        for other, original in originals.items():
            candidates.add(other)
            if other not in self.saved:
                self.saved[other] = {"original": original, "expected": original.copy(),
                                     "apple_original": None, "apple_expected": None,
                                     "apple_owned": False, "user_changed": False}
                added.add(other)
        saved["apple_owned"] = True
        saved["apple_expected"] = [apple[0], True]
        for other in candidates:
            if other not in self.saved:
                continue
            state = self.saved[other]
            state["applying"] = True
            state["apple_candidate"] = other != uid
            state["expected"].update({k: True for k in state["original"] if k.startswith("mute:")})
        self._persist()
        self.backend.apple_mute(True)
        actual = self.backend.apple_read()
        default = self.backend.default()
        if default == uid and actual is not None:
            saved["apple_expected"] = actual
        elif default in added and actual is not None:
            # Native mute gives us the new route's pre-call mute flag. Keep an
            # AppleScript mute-only restore as well, for read-only native flags.
            redirected = self.saved[default]
            redirected["apple_owned"] = True
            redirected["apple_mute_only"] = True
            redirected["apple_original"] = [actual[0], originals[default]["mute:0"]]
            redirected["apple_expected"] = actual
        for other in candidates:
            if other not in self.saved:
                continue
            state = self.saved[other]
            current = self.backend.controls(other)
            if current is not None:
                state["user_changed"] |= self._changed(state, current, None)
                state["expected"] = current.copy()
                state["applying"] = False
                if other in added and current == state["original"] and not state["apple_owned"]:
                    del self.saved[other]  # no mutation, no ownership of this output
        self._persist()
        return actual is not None and actual[1]

    def begin(self, generation):
        with self.lock:
            self._load()
            if generation in self.sessions:
                return
            if not self.sessions:
                self._restore()  # recover a previous crash before a fresh snapshot
            self.sessions.add(generation)
            try:
                self.poll()
            except BaseException:
                self.sessions.discard(generation)
                if not self.sessions:
                    self._restore()
                raise

    def poll(self):
        with self.lock:
            if not self.sessions:
                if self.saved and time.monotonic() >= self._next_retry:
                    self._next_retry = time.monotonic() + 1
                    self._restore()  # recover a disconnected device when it returns
                return
            for uid, saved in list(self.saved.items()):
                self._observe(uid, saved)
            for uid in self.backend.route():
                self._duck(uid)

    def end(self, generation):
        with self.lock:
            if generation not in self.sessions:
                return
            self.sessions.remove(generation)
            if not self.sessions:
                self._restore()

    def _restore(self):
        for uid, saved in list(self.saved.items()):
            try:
                self._restore_device(uid, saved)
            except Exception as exc:
                # One broken/disconnected driver must not strand other outputs.
                print(f"[Audio] Output restoration failed; will retry: {exc}")
        self._try_persist()

    def _restore_write(self, uid, key, value):
        try:
            current = self.backend.controls(uid)
            if current is not None and current.get(key) == value:
                return True  # master restore already restored a channel mirror
            return self.backend.write(uid, key, value)
        except Exception as exc:
            print(f"[Audio] Could not restore output control {key}: {exc}")
            return False

    def _restore_device(self, uid, saved):
        # A crash can occur inside AppleScript, before we learn which route
        # received the mute. Native flags saved ahead of the call identify it.
        if saved.get("apple_candidate") and not saved["apple_owned"]:
            current = self.backend.controls(uid)
            if (current is not None and self.backend.default() == uid and
                    current.get("mute:0") != saved["original"].get("mute:0") and
                    current.get("mute:0") == saved["expected"].get("mute:0")):
                apple = self.backend.apple_read()
                if apple is not None:
                    saved["apple_owned"] = True
                    saved["apple_mute_only"] = True
                    saved["apple_original"] = [apple[0], saved["original"]["mute:0"]]
                    saved["apple_expected"] = [apple[0], True]
        current, apple = self._observe(uid, saved, strict=False)
        if current is None:
            return
        saved["restoring"] = True
        self._try_persist()
        original, expected = saved["original"], saved["expected"]
        ok = True
        targets = {}
        apple_mute_target = None
        # Fallback must target the original device; changing routes is not
        # permission to set the new output's percentage volume.
        if saved["apple_owned"]:
            if self.backend.default() != uid:
                ok = False  # defer fallback until this UID is current again
            elif apple is None:
                ok = False
            else:
                before, applied = saved["apple_original"], saved["apple_expected"]
                if not saved["user_changed"] and not saved.get("apple_mute_only") and apple[0] != before[0]:
                    ok &= self.backend.apple_volume(before[0])
                if apple[1] == applied[1] and applied[1] != before[1]:
                    apple_mute_target = before[1]
                    ok &= self.backend.apple_mute(before[1])
                # Native controls (if present) have final authority below.
        if not saved["user_changed"]:
            for key, value in sorted(original.items()):
                if key.startswith(("volm:", "vmvc:")) and expected.get(key) != value:
                    targets[key] = value
                    ok &= self._restore_write(uid, key, value)
        for key, value in original.items():
            if key.startswith("mute:") and expected.get(key) != value:
                if current.get(key) == expected[key]:
                    targets[key] = value
                    ok &= self._restore_write(uid, key, value)
        check = self.backend.controls(uid)
        if check is None:
            ok = False
        else:
            ok &= all(check.get(k) == v for k, v in targets.items())
            if not saved["user_changed"]:
                ok &= all(check.get(k) == v for k, v in original.items())
        if saved["apple_owned"] and self.backend.default() == uid:
            check_apple = self.backend.apple_read()
            ok &= check_apple is not None and (
                saved["user_changed"] or check_apple == saved["apple_original"])
            if apple_mute_target is not None:
                ok &= check_apple is not None and check_apple[1] == apple_mute_target
        if ok:
            del self.saved[uid]
        else:
            print("[Audio] Output restoration incomplete; retaining recovery journal")
        self._try_persist()

    def recover(self):
        with self.lock:
            self._load()
            if not self.sessions:
                self._restore()

    def close(self):
        with self.lock:
            self.sessions.clear()
            try:
                self.recover()
            finally:
                if self._journal_lock:
                    self._journal_lock.close()
                    self._journal_lock = None
                    self._loaded = False


class _SystemAudioExecutor:
    """One FIFO worker for requests, route monitoring and shutdown restoration."""
    def __init__(self, controller=None):
        self.controller = controller
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="system-audio-executor", daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            try:
                job = self._q.get(timeout=0.1)
            except queue.Empty:
                if self.controller:
                    try:
                        self.controller.poll()
                    except Exception:
                        traceback.print_exc()
                continue
            if job is None:
                return
            fn, future = job
            deliver_result = future.set_running_or_notify_cancel()
            # Cancellation of a result must never discard a queued restore.
            try:
                result = fn()
            except BaseException as exc:
                if deliver_result:
                    future.set_exception(exc)
                traceback.print_exc()
            else:
                if deliver_result:
                    future.set_result(result)

    def submit(self, fn: Callable):
        future = Future()
        self._q.put((fn, future))
        return future

    def shutdown(self):
        self._q.put(None)
        self._thread.join(timeout=5)


def _get_system_audio_executor():
    global _system_audio_executor
    with _executor_lock:
        if _system_audio_executor is None:
            controller = _DuckingController(_DarwinAudio(), Path.home() / ".thundertalk" /
                                           "audio-ducking.json") if _SYSTEM == "Darwin" else None
            _system_audio_executor = _SystemAudioExecutor(controller)
        return _system_audio_executor


def _portable_mute(muted):
    if _SYSTEM == "Linux":
        try:
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", str(int(muted))],
                           check=False, timeout=3)
        except FileNotFoundError:
            subprocess.run(["amixer", "set", "Master", "mute" if muted else "unmute"],
                           check=False, timeout=3)
    elif _SYSTEM == "Windows":
        subprocess.run(["nircmd", "mutesysvolume", str(int(muted))], check=False, timeout=3)


class AudioDuckingSession:
    """A recording owns one token; stale/duplicate releases cannot unmute another."""
    def __init__(self, executor, generation):
        self._executor = executor
        self.generation = generation
        self.ready = executor.submit(lambda: executor.controller.begin(generation)
                                     if executor.controller else _portable_mute(True))

    def refresh(self):
        return self._executor.submit(lambda: self._executor.controller.poll()
                                     if self._executor.controller else None)

    def restore(self):
        return self._executor.submit(lambda: self._executor.controller.end(self.generation)
                                     if self._executor.controller else _portable_mute(False))


def mute_system_audio() -> AudioDuckingSession:
    """Return immediately. `ready` completes after the mute attempt, before capture."""
    with _lock:
        generation = next(_generation)
    return AudioDuckingSession(_get_system_audio_executor(), generation)


def unmute_system_audio(session: AudioDuckingSession) -> Future:
    return session.restore()


def recover_system_audio() -> Future:
    executor = _get_system_audio_executor()
    return executor.submit(lambda: executor.controller.recover() if executor.controller else None)


def shutdown_system_audio() -> None:
    """Wait for restoration on quit; retain journal if a wedged driver times out."""
    executor = _system_audio_executor
    if executor is None or not executor._thread.is_alive():
        return
    try:
        executor.submit(lambda: executor.controller.close() if executor.controller else
                        _portable_mute(False)).result(timeout=5)
    except Exception as exc:
        print(f"[Audio] Shutdown restoration could not finish: {exc}")
    executor.shutdown()


def stop_recording_and_restore(recorder, session):
    """Recording errors/cancellation must release the captured session token."""
    try:
        return recorder.stop() if recorder is not None else None
    finally:
        if session is not None:
            session.restore()
