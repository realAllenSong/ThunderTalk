"""Apple unified-memory footprint sampling for music selftests and benchmarks."""
from __future__ import annotations

import ctypes
import sys
import threading


def physical_footprint(pid: int | None) -> int:
    if not pid or sys.platform != 'darwin':
        return 0
    buf = ctypes.create_string_buffer(1024)
    lib = ctypes.CDLL('/usr/lib/libproc.dylib')
    if lib.proc_pid_rusage(pid, 2, ctypes.byref(buf)):
        return 0
    return ctypes.c_uint64.from_buffer(buf, 16 + 7 * 8).value


class PeakFootprint:
    def __init__(self, process):
        self.process = process
        self.peak = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop.wait(0.5):
            self.peak = max(self.peak, physical_footprint(self.process.pid))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(2)
