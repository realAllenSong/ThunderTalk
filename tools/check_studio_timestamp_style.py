"""Compare restored timestamp rows with 08a90d1 using offscreen Qt pixels."""

from __future__ import annotations

import json
import os
import subprocess
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from thundertalk.core.transcribe import Segment, Transcript  # noqa: E402
from thundertalk.ui import theme  # noqa: E402
from thundertalk.ui.studio.parts import _TimestampViewport  # noqa: E402


def baseline_module():
    source = subprocess.run(["git", "show", "08a90d1:thundertalk/ui/studio/parts.py"],
                            capture_output=True, text=True, check=True).stdout
    module = types.ModuleType("baseline_parts")
    exec(compile(source, "baseline_parts.py", "exec"), module.__dict__)
    return module


def sample_transcript(mode, long=False):
    texts = [
        "We reviewed the release plan and agreed to test the next build. 我们确认了下一步。",
        "The transcript should keep its clear time column, comfortable spacing and readable typography.",
        "We will check file transcription, the queue, exports and meeting notes before releasing the update.",
        "The next recording includes English and Chinese. 下一段录音包含英文和中文。",
        "Speaker labels can be renamed, and consecutive segments belong to the same speaker turn.",
        "A longer line wraps naturally within the text column while the timestamp and speaker chip stay at the top.",
    ]
    segments = [Segment(i * 7.5, (i + 1) * 7.5, text,
                        "S01" if mode == "single" else f"S0{i // 2 % 2 + 1}" if mode == "multi" else "")
                for i, text in enumerate(texts * (30 if long else 1))]
    return Transcript(segments, len(segments) * 7.5, "Synthetic", has_speakers=mode != "none",
                      speaker_names={"S01": "Host", "S02": "Reviewer"})


def settle():
    for _ in range(20):
        QApplication.processEvents()


def pixels(pixmap):
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.width(), 4).copy()


def capture(widget):
    image = QPixmap(widget.size())
    image.fill(QColor(theme.BG_CARD))
    widget.render(image)
    return image


def main():
    app = QApplication.instance() or QApplication([])
    theme.force_light(app)
    output = Path("artifacts/studio-layout")
    output.mkdir(parents=True, exist_ok=True)
    baseline = baseline_module()
    results = []
    cases = [(mode, long, width) for mode, long in
             (("none", False), ("single", False), ("multi", False), ("single", True), ("multi", True))
             for width in ((812, 560) if long else (812,))]
    for mode, long, width in cases:
        window = QWidget()
        window.setStyleSheet(f"background: {theme.BG_CARD};")
        layout = QHBoxLayout(window)
        tr = sample_transcript(mode, long)
        old = baseline.TranscriptView()
        old.set_transcript(tr)
        old_scroll = QScrollArea()
        old_scroll.setFrameShape(QFrame.Shape.NoFrame)
        old_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        old_scroll.setWidget(old)
        new = _TimestampViewport()
        new.set_transcript(tr)
        for title, view in (("Original · 08a90d1", old_scroll), ("Restored · virtualized rows", new)):
            column = QVBoxLayout()
            heading = QLabel(title)
            heading.setFont(theme.font(14, bold=True))
            column.addWidget(heading)
            view.setFixedWidth(width)
            view.setFixedHeight(360)
            column.addWidget(view)
            layout.addLayout(column)
        window.show()
        settle()
        old.resize(old_scroll.viewport().width(), old.heightForWidth(old_scroll.viewport().width()))
        settle()
        for fraction in ((0, 0.5, 1) if long else (0,)):
            position = round(old_scroll.verticalScrollBar().maximum() * fraction)
            old_scroll.verticalScrollBar().setValue(position)
            new.verticalScrollBar().setValue(position)
            settle()
            suffix = mode + (f"-long-{width}-scroll{fraction:g}" if long else "")
            path = output / f"timestamps-old-vs-new-{suffix}.png"
            window.grab().save(str(path))
            a = pixels(capture(old_scroll.viewport()))
            b = pixels(capture(new.viewport()))
            assert a.shape == b.shape
            different = np.any(a != b, axis=2)
            results.append({"mode": suffix, "path": str(path.resolve()),
                            "different_pixels": int(different.sum()),
                            "compared_pixels": int(different.size),
                            "old_content_height": old.height(),
                            "new_content_height": new.content_height})
        window.close()
        window.deleteLater()
    (output / "timestamp-style-comparison.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    assert all(r["different_pixels"] == 0 and r["old_content_height"] == r["new_content_height"]
               for r in results), "Timestamp rendering differs from 08a90d1"


if __name__ == "__main__":
    main()
