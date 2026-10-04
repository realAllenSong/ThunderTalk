"""Short macOS ducking check. Read-only unless --run is explicitly supplied.

Print all output controls before/after each cycle, guarded by try/finally and
an independent readback. No playback, models, route changes, percentage writes
or non-default-device writes. Preserve a recovery journal inside the worktree.
Run with PYTHONPATH=$PWD and the project's shared Python interpreter.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import signal
import time
from pathlib import Path

from thundertalk.core.system_audio import _DarwinAudio, _DuckingController


def snapshot(backend):
    devices = backend.devices()
    output = {}
    for uid, device in devices.items():
        channels = backend.channels(uid)
        if channels:
            output[uid] = {"id": device, "channels": channels, "controls": backend.controls(uid),
                           "virtual_volume": backend._read(device, "vmvc", kind=ctypes.c_float)}
    return {"default": backend.default(), "apple": backend.apple_read(), "devices": output}


def printable(state):
    # Do not put hardware UIDs (which may contain serials/names) in logs.
    return {"default_id": state["devices"].get(state["default"], {}).get("id"),
            "apple": state["apple"],
            "devices": {str(v["id"]): {k: val for k, val in v.items() if k != "id"}
                        for v in state["devices"].values()}}


class MuteOnlyAudio(_DarwinAudio):
    def __init__(self, allowed_uid):
        super().__init__()
        self.allowed_uid = allowed_uid
        self.original = self.controls(allowed_uid)

    def write(self, uid, key, value):
        assert uid == self.allowed_uid and (key.startswith("mute:") or
                                           value == self.original.get(key)), "unexpected hardware mutation"
        return super().write(uid, key, value)

    def apple_mute(self, muted):
        raise AssertionError("hardware check must not use global AppleScript writes")

    def apple_volume(self, volume):
        raise AssertionError("hardware check must not write percentages")


def restore_guard(backend, before, allowed_uid):
    # Only repair differences on the output this check legitimately touched.
    # Virtual outputs are read-only throughout, including cleanup.
    original = before["devices"][allowed_uid]["controls"]
    current = backend.controls(allowed_uid)
    assert current is not None, "output disconnected; recovery journal retained"
    for key, value in original.items():
        if current.get(key) != value:
            assert key.startswith("mute:"), "unexpected volume drift (no volume writes allowed)"
            assert backend.write(allowed_uid, key, value), "guard restore failed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="run 10 short native mute/restore cycles")
    args = parser.parse_args()
    backend = _DarwinAudio()
    initial = snapshot(backend)
    print("INITIAL", json.dumps(printable(initial), sort_keys=True), flush=True)
    if not args.run:
        return
    uid = initial["default"]
    assert uid in initial["devices"] and backend.route() == [uid], "requires a single native output"
    assert initial["devices"][uid]["controls"].get("mute:0") is False, "requires unmuted output"
    assert backend.writable(uid, "mute:0"), "requires native master mute"
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("interrupted hardware check")
    signal.signal(signal.SIGTERM, interrupted)
    path = Path(__file__).resolve().parent.parent / "experiments" / "audio-hardware-recovery.json"
    assert not path.exists(), "resolve an existing hardware recovery journal before testing"
    controller = _DuckingController(MuteOnlyAudio(uid), path)
    started = time.perf_counter()
    try:
        for generation in range(1, 11):
            before = snapshot(backend)
            print(f"CYCLE {generation} BEFORE", json.dumps(printable(before), sort_keys=True), flush=True)
            assert before == initial, "state changed before this cycle; aborting"
            try:
                controller.begin(generation)
                during = backend.controls(uid)
                assert during["mute:0"] is True
                assert all(during[k] == v for k, v in before["devices"][uid]["controls"].items()
                           if not k.startswith("mute:"))
                print(f"CYCLE {generation} DURING", json.dumps(during, sort_keys=True), flush=True)
            finally:
                controller.end(generation)
                restore_guard(backend, before, uid)
                after = snapshot(backend)
                print(f"CYCLE {generation} AFTER", json.dumps(printable(after), sort_keys=True), flush=True)
                assert after == before, "all-output exact readback failed"
    finally:
        try:
            controller.close()
        finally:
            restore_guard(backend, initial, uid)
            final = snapshot(backend)
            print("FINAL", json.dumps(printable(final), sort_keys=True), flush=True)
            assert final == initial, "all-output final readback failed"
    print(f"PASS: 10/10 exact all-output restores in {time.perf_counter() - started:.3f}s", flush=True)


if __name__ == "__main__":
    main()
