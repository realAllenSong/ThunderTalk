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

from thundertalk.core.audio_diagnostics import audio_diagnostic

_SYSTEM = platform.system()
_lock = threading.Lock()  # protects request generations, never held over OS calls
_generation = itertools.count(1)
_executor_lock = threading.Lock()
_system_audio_executor = None
_SETTLE_TIMEOUT = 0.5
_SETTLE_INTERVAL = 0.02


class AudioSilenceError(RuntimeError):
    """A controllable output did not confirm silence; do not start capture."""


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
        self._transitions = {}
        self._transition_lock = threading.Lock()
        self._last_skip = {}

    def _diag(self, event, uid=None, **fields):
        try:
            device = self.backend.devices().get(uid) if uid is not None else None
        except Exception:
            device = None
        if event == "mute_skipped":
            signature = json.dumps([sorted(self.sessions), fields], sort_keys=True)
            if self._last_skip.get(uid) == signature:
                return
            self._last_skip[uid] = signature
        audio_diagnostic(event, owners=sorted(self.sessions), device_id=device, **fields)

    def transition(self, generation, phase):
        # No CoreAudio or journal I/O on the UI thread at microphone boundaries.
        with self._transition_lock:
            self._transitions[generation] = phase
        audio_diagnostic("microphone_transition", generation=generation, phase=phase)

    def _in_transition(self):
        with self._transition_lock:
            return bool(self.sessions.intersection(self._transitions)) or getattr(self, "_restoring_transition", False)

    def _settled(self, uid, targets=None, required=None, timeout=_SETTLE_TIMEOUT):
        deadline = time.monotonic() + timeout
        previous = None
        stable = 0
        while True:
            current = self.backend.controls(uid)
            compatible = current is not None and (required is None or set(required) <= set(current))
            matches = compatible and (targets is None or all(current.get(k) == v for k, v in targets.items()))
            stable = stable + 1 if matches and current == previous else 1 if matches else 0
            if stable >= 3:
                return current
            if time.monotonic() >= deadline:
                self._diag("settle_timeout", uid, current=current, targets=targets)
                return current if compatible else None
            previous = current
            time.sleep(_SETTLE_INTERVAL)

    def _load(self):
        if self._loaded:
            return
        self._diag("journal_load")
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
        except BaseException as exc:
            self._diag("journal_load_failed", error=type(exc).__name__)
            handle.close()
            raise
        self._journal_lock = handle
        self._loaded = True
        self._diag("journal_loaded", pending_devices=len(self.saved))

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
                      not (saved["apple_owned"] and k.startswith("mute:") and
                           current[k] == saved["original"][k]) and
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
            self._diag("journal_write_failed", error=type(exc).__name__)
            print(f"[Audio] Could not update recovery journal: {exc}")

    def _observe(self, uid, saved, *, strict=True):
        current = self.backend.controls(uid)
        if current is not None and not set(saved["original"]) <= set(current):
            if self._in_transition() and not saved.get("graph_reset"):
                saved["graph_reset"] = True
                self._try_persist()
            self._diag("controls_unstable", uid, current=current, original=saved["original"],
                       decision="defer_not_user_change")
            return None, None
        apple = self.backend.apple_read() if saved["apple_owned"] and self.backend.default() == uid else None
        observed = current
        if current is not None and (self._in_transition() or saved.get("graph_reset")):
            # PortAudio opening/closing can reset mute without a key press.
            # Volume differences on stable elements still count as user changes.
            observed = {k: saved["expected"].get(k, v)
                        if k.startswith("mute:") or saved.get("graph_reset") else v
                        for k, v in current.items()}
            if observed != current:
                self._diag("boundary_change", uid, current=current, expected=saved["expected"],
                           decision="microphone_transition_not_user_change")
        if not saved["user_changed"] and self._changed(saved, observed, apple):
            self._diag("user_change", uid, current=current, expected=saved["expected"],
                       original=saved["original"], apple=apple, decision="preserve")
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

    def _apple_write(self, uid, kind, value):
        started = time.perf_counter()
        accepted = (self.backend.apple_mute(value) if kind == "mute"
                    else self.backend.apple_volume(value))
        self._diag("apple_write", uid, element=kind, target=value, accepted=accepted,
                   readback=self.backend.apple_read(), ms=round((time.perf_counter()-started)*1000, 3))
        return accepted

    def _apple_settled(self, muted):
        deadline = time.monotonic() + _SETTLE_TIMEOUT
        while True:
            value = self.backend.apple_read()
            if value is not None and value[1] == muted:
                return value
            if time.monotonic() >= deadline:
                return value
            time.sleep(_SETTLE_INTERVAL)

    def _apply(self, uid, saved, targets):
        previous_expected = saved["expected"].copy()
        previous_intent = saved.get("intent", {}).copy()
        previous_native = saved.get("native_intent", {}).copy()
        planned = targets.copy()
        for prefix in ("mute", "volm"):
            master = f"{prefix}:0"
            if master in targets:
                planned.update({k: targets[master] for k in saved["original"]
                                if k.startswith(f"{prefix}:")})
        saved["expected"].update(planned)
        intent = saved.setdefault("intent", {})
        intent.update(planned)  # durable ownership survives lagging getters
        native_intent = saved.setdefault("native_intent", {})
        native_intent.update(planned)
        saved["applying"] = True
        self._persist()
        accepted = set()
        for key, value in sorted(targets.items()):
            started = time.perf_counter()
            ok = self.backend.write(uid, key, value)
            if ok:
                accepted.add(key)
            else:
                self._rejected_intent(saved, key, previous_expected, previous_intent, previous_native)
            self._diag("mute_write", uid, element=key, target=value, accepted=ok,
                       readback=self.backend.controls(uid), ms=round((time.perf_counter()-started)*1000, 3))
        confirmed = {k: v for k, v in planned.items() if k in accepted or
                     any(f"{k.split(':')[0]}:0" == master for master in accepted)}
        saved.setdefault("native_intent", {}).update(confirmed)
        # Mirrors of a rejected master are not owned by this operation.
        for key in set(planned) - set(confirmed):
            self._rejected_intent(saved, key, previous_expected, previous_intent, previous_native)
        current = self._settled(uid, confirmed, saved["original"])
        relaid = False
        if current is None:
            # Opening the microphone can re-lay out the output's controls
            # (per-channel gains become one master gain). Judge the write by
            # the controls that exist now rather than the snapshot's layout.
            current = self._settled(uid, confirmed)
            relaid = current is not None
            if relaid:
                self._diag("controls_relaid", uid, current=current, original=saved["original"])
        complete = current is not None and all(current.get(k) == v for k, v in confirmed.items())
        self._diag("mute_readback", uid, current=current, expected=saved["expected"],
                   confirmed=complete, silenced=self._silenced(uid, current))
        if current is not None:
            # Never replace the target with a getter that still reports the
            # pre-write value: late delivery is our write, not a user change.
            if not saved.get("graph_reset") and not relaid:
                if not saved["user_changed"] and self._changed(saved, current, None):
                    self._diag("user_change", uid, current=current, expected=saved["expected"],
                               decision="preserve_during_write")
                    saved["user_changed"] = True
            saved["applying"] = not complete
        self._persist()
        return complete and self._silenced(uid, current)

    @staticmethod
    def _rejected_intent(saved, key, expected, intent, native):
        # A failed reassertion does not revoke ownership of an earlier write.
        for field, previous in (("intent", intent), ("native_intent", native)):
            if key in previous:
                saved[field][key] = previous[key]
            else:
                saved[field].pop(key, None)
        saved["expected"][key] = expected[key]

    def _duck(self, uid):
        if uid in self.saved:
            self._diag("mute_skipped", uid, reason="snapshot_exists",
                       current=self.backend.controls(uid), expected=self.saved[uid]["expected"])
            return  # never snapshot a ducked device again
        original = self.backend.controls(uid)
        if original is None:
            self._diag("mute_skipped", uid, reason="disconnected")
            return
        apple = self.backend.apple_read() if self.backend.default() == uid else None
        saved = {"original": original, "expected": original.copy(), "apple_original": apple,
                 "apple_expected": apple, "apple_owned": False, "user_changed": False}
        self.saved[uid] = saved
        self._diag("snapshot", uid, original=original, apple=apple)
        self._persist()  # fail closed: never mute without a durable snapshot
        if self._silenced(uid, original):
            self._diag("mute_skipped", uid, reason="already_silent")
            return
        writable = {k for k in original if self.backend.writable(uid, k)}
        mutes = {k: True for k in writable if k.startswith("mute:")}
        if mutes and self._apply(uid, saved, mutes):
            return
        if mutes and saved.get("applying") and saved.get("intent"):
            # Accepted but unconfirmed writes may still arrive. Never stack a
            # zero-volume fallback on them or declare the microphone ready.
            raise AudioSilenceError("Output mute readback did not settle")
        volumes = {k: 0.0 for k in writable if k.startswith(("volm:", "vmvc:"))}
        if volumes and self._apply(uid, saved, volumes):
            return
        # AppleScript is only a fallback for the same current output, never a
        # second percentage-volume authority on top of native scalars.
        if apple is not None and self.backend.default() == uid:
            if self._apple_fallback(uid, saved, apple):
                return
        self._diag("mute_failed", uid, reason="controls_unavailable")
        print("[Audio] Output cannot be silenced with its available controls; skipping")
        if writable or saved.get("intent") or saved["apple_owned"]:
            raise AudioSilenceError("Output mute was not confirmed")

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
                self._diag("apple_skipped", other, reason="alternate_mute_unreadable")
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
            state.setdefault("intent", {}).update({k: True for k in state["original"] if k.startswith("mute:")})
        self._persist()
        accepted = self._apple_write(uid, "mute", True)
        actual = self._apple_settled(True)
        self._diag("apple_mute", uid, accepted=accepted, readback=actual)
        default = self.backend.default()
        if default in added and actual is not None:
            # Native mute gives us the new route's pre-call mute flag. Keep an
            # AppleScript mute-only restore as well, for read-only native flags.
            redirected = self.saved[default]
            redirected["apple_owned"] = True
            redirected["apple_mute_only"] = True
            redirected["apple_original"] = [actual[0], originals[default]["mute:0"]]
            redirected["apple_expected"] = [actual[0], True]
        for other in candidates:
            if other not in self.saved:
                continue
            state = self.saved[other]
            current = self.backend.controls(other)
            if current is not None:
                state["user_changed"] |= self._changed(state, current, None)
                state["applying"] = not accepted or actual is None or not actual[1]
                if other in added and current == state["original"] and not state["apple_owned"]:
                    del self.saved[other]  # no mutation, no ownership of this output
        self._persist()
        return accepted and actual is not None and actual[1]

    def begin(self, generation):
        started = time.perf_counter()
        with self.lock:
            self._diag("session_start", generation=generation)
            self._load()
            if generation in self.sessions:
                return
            if not self.sessions:
                self._restore()  # recover a previous crash before a fresh snapshot
                if any(uid in self.saved for uid in self.backend.route()):
                    self._diag("session_start_skipped", reason="pending_route_recovery")
                    raise AudioSilenceError("Output restoration is still pending")
            self.sessions.add(generation)
            try:
                self.poll()
                self._diag("session_ready", generation=generation,
                           ms=round((time.perf_counter()-started)*1000, 3))
            except BaseException as exc:
                self._diag("session_start_failed", generation=generation, error=type(exc).__name__)
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

    def synchronize(self, generation):
        with self.lock:
            if generation not in self.sessions:
                self._diag("capture_check_skipped", generation=generation, reason="stale_generation")
                with self._transition_lock:
                    self._transitions.pop(generation, None)
                return
            try:
                self.poll()
                for uid in self.backend.route():
                    saved = self.saved.get(uid)
                    if saved is None:
                        continue
                    current = self._settled(uid, required=saved["original"])
                    if saved["user_changed"] or (self._silenced(uid, current) and not saved.get("graph_reset")):
                        continue
                    targets = {k: v for k, v in saved.get("intent", {}).items()
                               if self.backend.writable(uid, k)}
                    if not targets and self._in_transition():
                        targets = {k: True for k in saved["original"]
                                   if k.startswith("mute:") and self.backend.writable(uid, k)}
                    if targets and not self._apply(uid, saved, targets):
                        raise AudioSilenceError("Microphone graph reset speaker mute")
                    if targets and saved.get("graph_reset"):
                        # Re-establish the original native gains after a HAL
                        # topology reset, while the owned mute keeps this quiet.
                        for key, value in saved["original"].items():
                            if key.startswith(("volm:", "vmvc:")):
                                if not self._restore_write(uid, key, value):
                                    raise AudioSilenceError("Output graph gains did not recover")
                        saved["expected"].update({k: v for k, v in saved["original"].items()
                                                 if k.startswith(("volm:", "vmvc:"))})
                        saved.pop("graph_reset", None)
                        self._persist()
                    if targets:
                        verified = self._settled(uid, saved["expected"], saved["original"])
                        if not self._silenced(uid, verified):
                            raise AudioSilenceError("Output silence was lost during graph setup")
                    if not targets and saved["apple_owned"]:
                        actual = self.backend.apple_read()
                        if self.backend.default() != uid or actual is None or not actual[1]:
                            accepted = self._apple_write(uid, "mute", True)
                            actual = self._apple_settled(True)
                            if not accepted or actual is None or not actual[1]:
                                raise AudioSilenceError("AppleScript mute was not confirmed")
                    if not targets and not saved["apple_owned"] and any(
                            self.backend.writable(uid, k) for k in saved["original"]):
                        raise AudioSilenceError("No confirmed mute ownership")
                self._diag("capture_ready", generation=generation)
            finally:
                with self._transition_lock:
                    self._transitions.pop(generation, None)

    def end(self, generation):
        with self.lock:
            self._diag("session_release", generation=generation)
            if generation not in self.sessions:
                self._diag("release_skipped", generation=generation, reason="stale_generation")
                with self._transition_lock:
                    self._transitions.pop(generation, None)
                return
            # Keep transition ownership through restore even after the last
            # session is removed from the active owner set.
            closing = self._in_transition()
            self.sessions.remove(generation)
            self._restoring_transition = closing
            try:
                if not self.sessions:
                    self._restore()
            finally:
                self._restoring_transition = False
                with self._transition_lock:
                    self._transitions.pop(generation, None)

    def _restore(self):
        for uid, saved in list(self.saved.items()):
            try:
                self._restore_device(uid, saved)
            except Exception as exc:
                self._diag("restore_failed", uid, error=type(exc).__name__)
                # One broken/disconnected driver must not strand other outputs.
                print(f"[Audio] Output restoration failed; will retry: {exc}")
        self._try_persist()

    def _restore_write(self, uid, key, value, *, force=True):
        try:
            current = self.backend.controls(uid)
            if (not force or not self.backend.writable(uid, key)) and current is not None and current.get(key) == value:
                self._diag("restore_write_skipped", uid, element=key, target=value, current=current)
                return True  # read-only mirror already restored by its master
            started = time.perf_counter()
            ok = self.backend.write(uid, key, value)
            self._diag("restore_write", uid, element=key, target=value, accepted=ok,
                       readback=self.backend.controls(uid), ms=round((time.perf_counter()-started)*1000, 3))
            return ok
        except Exception as exc:
            self._diag("restore_write_failed", uid, element=key, target=value, error=type(exc).__name__)
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
        self._settled(uid, required=saved["original"])
        current, apple = self._observe(uid, saved, strict=False)
        self._diag("restore_start", uid, current=current, expected=saved["expected"],
                   original=saved["original"], user_changed=saved["user_changed"])
        if current is None:
            self._diag("restore_deferred", uid, reason="disconnected")
            return
        saved["restoring"] = True
        self._try_persist()
        original, expected = saved["original"], saved["expected"]
        intent = saved.get("intent", {k: v for k, v in expected.items() if original.get(k) != v})
        native_intent = saved.get("native_intent", intent if not saved["apple_owned"] else {})
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
                    ok &= self._apple_write(uid, "volume", before[0])
                if applied[1] != before[1] and (apple[1] == applied[1] or saved.get("applying")):
                    apple_mute_target = before[1]
                    ok &= self._apple_write(uid, "mute", before[1])
                    self._apple_settled(before[1])
                # Native controls (if present) have final authority below.
        if not saved["user_changed"]:
            for key, value in sorted(original.items()):
                if key.startswith(("volm:", "vmvc:")) and key in intent:
                    targets[key] = value
                    ok &= self._restore_write(uid, key, value, force=key in native_intent)
        for key, value in original.items():
            if key.startswith("mute:") and key in intent:
                if current.get(key) == expected[key] or current.get(key) == value or saved.get("applying"):
                    # Always publish our restore, even when the getter already
                    # says unmuted: an earlier asynchronous mute may be pending.
                    targets[key] = value
                    ok &= self._restore_write(uid, key, value, force=key in native_intent)
        # A volume key republishes both gain and mute. Republish the exact raw
        # scalar after unmute too, without percentage rounding or raising gain.
        if any(k.startswith("mute:") and k in native_intent for k in targets):
            gains = current if saved["user_changed"] else original
            for key, value in sorted(gains.items()):
                if key.startswith(("volm:", "vmvc:")) and self.backend.writable(uid, key):
                    targets[key] = value
                    ok &= self._restore_write(uid, key, value)
        check = self._settled(uid, targets, original)
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
        self._diag("restore_readback", uid, current=check, targets=targets,
                   complete=bool(ok), user_changed=saved["user_changed"])
        if ok:
            del self.saved[uid]
        else:
            print("[Audio] Output restoration incomplete; retaining recovery journal")
        self._try_persist()

    def recover(self):
        with self.lock:
            self._diag("recovery_start")
            self._load()
            if not self.sessions:
                self._restore()

    def close(self):
        with self.lock:
            self._restoring_transition = self._in_transition()
            self.sessions.clear()
            try:
                self.recover()
            finally:
                self._restoring_transition = False
                with self._transition_lock:
                    self._transitions.clear()
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
                        self.controller._diag("route_poll_failed")
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
        self.requested_at = time.perf_counter()
        audio_diagnostic("session_requested", generation=generation)
        self.ready = executor.submit(lambda: executor.controller.begin(generation)
                                     if executor.controller else _portable_mute(True))
        self.ready.add_done_callback(self._ready_logged)

    def _ready_logged(self, future):
        error = "CancelledError" if future.cancelled() else (
            type(future.exception()).__name__ if future.exception() is not None else None)
        self.diagnostic("ready_delivered", error=error)

    def diagnostic(self, event, **fields):
        audio_diagnostic(event, generation=self.generation,
                         elapsed_ms=round((time.perf_counter()-self.requested_at)*1000, 3), **fields)

    def microphone_transition(self, phase):
        if self._executor.controller is not None:
            self._executor.controller.transition(self.generation, phase)

    def synchronize(self):
        self.diagnostic("capture_check_requested")
        return self._executor.submit(lambda: self._executor.controller.synchronize(self.generation)
                                     if self._executor.controller else None)

    def refresh(self):
        self.diagnostic("refresh_requested")
        return self._executor.submit(lambda: self._executor.controller.poll()
                                     if self._executor.controller else None)

    def restore(self):
        self.diagnostic("restore_requested")
        return self._executor.submit(lambda: self._executor.controller.end(self.generation)
                                     if self._executor.controller else _portable_mute(False))


def mute_system_audio() -> AudioDuckingSession:
    """Return immediately; `ready` confirms native silence before microphone open."""
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
        if session is not None and recorder is not None:
            session.microphone_transition("close")
        return recorder.stop() if recorder is not None else None
    finally:
        if session is not None:
            session.restore()
