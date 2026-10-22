"""Cross-process coordination without models or GUI startup."""
from __future__ import annotations

import os
import subprocess
import sys


def test_extra_model_slot_is_shared_and_released(tmp_path):
    from thundertalk.core.priority import ModelSlot
    path = tmp_path / "slot.lock"
    slot = ModelSlot(path)
    assert slot.acquire(blocking=False)
    script = ("import sys\nfrom pathlib import Path\nfrom thundertalk.core.priority import ModelSlot\n"
              "slot = ModelSlot(Path(sys.argv[1]))\nprint(slot.acquire(blocking=False))\n")
    def available():
        return subprocess.check_output([sys.executable, "-c", script, str(path)], text=True).strip()
    try:
        assert available() == "False"
    finally:
        slot.release()
    assert available() == "True"


def test_dictation_priority_visible_to_other_process_and_expires(tmp_path):
    from thundertalk.core.priority import DictationPriority
    path = tmp_path / "dictation.lock"
    priority = DictationPriority(shared_path=path)
    script = ("import sys\nfrom pathlib import Path\nfrom thundertalk.core.priority import DictationPriority\n"
              "print(DictationPriority(shared_path=Path(sys.argv[1])).active)\n")
    def active():
        return subprocess.check_output([sys.executable, "-c", script, str(path)], text=True).strip()
    priority.begin()
    priority.begin()
    try:
        assert active() == "True"
        priority.end()
        assert active() == "True"
        # A stale/hung owner must not pause jobs forever.
        os.utime(path, (0, 0))
        assert active() == "False"
    finally:
        priority.end()
    assert active() == "False"
