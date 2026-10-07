"""Offscreen Studio ▸ Compose captures (English and Chinese) with a fake worker.

Run with the shared interpreter and PYTHONPATH=$PWD. No music model is loaded;
takes are short silent WAVs written into a temporary home under the output dir.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from thundertalk.core import audio_io, i18n, music  # noqa: E402
from thundertalk.core.music_check import TakeCheck  # noqa: E402
from thundertalk.core.music_lyrics import check_line, lyric_lines  # noqa: E402
from thundertalk.ui import theme  # noqa: E402
from thundertalk.ui.pages.studio_page import StudioPage  # noqa: E402
from thundertalk.ui.studio.compose_tab import ComposeTab  # noqa: E402

LYRICS = ("[verse]\nWalking home beneath the city lights\nEvery window hums a different tune\n"
          "[chorus]\nWe keep the rhythm, we keep it slow\nLa la la, and on we go")


class FakeWorker(QObject):
    event = Signal(object)
    take = Signal(int, str, object)
    done = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    finished = Signal()
    last = None

    def __init__(self, request, ram_need_mb):
        super().__init__()
        self.request = request
        FakeWorker.last = self

    def start(self):
        pass

    def cancel(self):
        pass

    def wait(self, _ms):
        return True


def settle(n=20):
    for _ in range(n):
        QApplication.processEvents()


def capture(page, path, size):
    page.resize(*size)
    settle()
    page.grab().save(str(path))
    tab = page.compose_tab
    full = QPixmap(tab.size())
    full.fill(QColor(theme.BG_BASE))
    tab.render(full)
    full.save(str(path.with_name(path.stem + "-tab.png")))
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="artifacts/compose-layout")
    parser.add_argument("--memory-gb", type=int, default=36)
    args = parser.parse_args()
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    theme.force_light(app)
    shots = []
    for lang in ("en", "zh"):
        with tempfile.TemporaryDirectory(dir=out) as home, \
                patch.object(Path, "home", return_value=Path(home)), \
                patch("thundertalk.ui.studio.speak_tab.AUTO_PRELOAD", False), \
                patch.object(music, "missing", lambda m, planner: []):
            i18n.set_language(lang)
            page = StudioPage(SimpleNamespace(microphone="auto"), compose=True,
                              make_compose=lambda s: ComposeTab(s, memory_gb=args.memory_gb,
                                                                make_worker=FakeWorker))
            tab = page.compose_tab
            page.show()
            page.show_tab("compose")
            tab._style.setPlainText("warm indie pop, acoustic guitar, brushed drums, female vocal")
            tab._lyrics.setPlainText(LYRICS)
            shots.append(capture(page, out / f"{lang}-setup-1200x900.png", (1200, 900)))
            tab._generate()
            w = FakeWorker.last
            for i, seed in enumerate((4120, 4121)):
                wav = Path(home) / f"take-{i + 1}.wav"
                audio_io.write_wav(str(wav), np.zeros((48000, 2), np.float32), 48000)
                w.take.emit(i, str(wav), {"seed": seed, "params": {"duration": tab._length.value()}})
            w.event.emit(("phase", "compose", 2))
            w.event.emit(("step", 5, 8))
            shots.append(capture(page, out / f"{lang}-running-1200x900.png", (1200, 900)))
            w.take.emit(2, str(Path(home) / "take-2.wav"), {"seed": 4122,
                                                            "params": {"duration": tab._length.value()}})
            w.done.emit([(i, "", {}) for i in range(3)])
            w.finished.emit()
            shots.append(capture(page, out / f"{lang}-done-1200x900.png", (1200, 900)))
            shots.append(capture(page, out / f"{lang}-done-1600x1000.png", (1600, 1000)))
            lines = lyric_lines(LYRICS)
            result = TakeCheck(lines, [check_line(i, "different words" if i == 1 else ln.text, [ln.text], "en")
                                       for i, ln in enumerate(lines)], [(0, 1)], False, [(0, 1)] * len(lines))
            tab._checked(tab._rows[0], result)
            tab._rows[0].check_button.click()
            tab._lang.setCurrentIndex(tab._lang.findData("zh"))
            tab._lyrics.setPlainText("[verse]\n我们唱欧拉SONG\n一行公式藏着星光")
            tab._sung.toggle.setChecked(True)
            shots.append(capture(page, out / f"{lang}-lyric-check-1200x900.png", (1200, 900)))
            tab._mode.set_current("bgm")
            tab._on_mode("bgm")
            shots.append(capture(page, out / f"{lang}-bgm-1200x900.png", (1200, 900)))
            page.shutdown()
            page.close()
    i18n.set_language("en")
    print(json.dumps({"captures": shots}, indent=2))


if __name__ == "__main__":
    main()
