"""Offscreen Studio layout captures and a model-free 2,000-segment benchmark.

Run with the shared interpreter and PYTHONPATH=$PWD. All data stays in the
output directory; no speech models or user history are accessed.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import tempfile
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from thundertalk.core import i18n  # noqa: E402
from thundertalk.core.transcribe import Segment, Transcript  # noqa: E402
from thundertalk.ui import theme  # noqa: E402
from thundertalk.ui.pages.studio_page import StudioPage  # noqa: E402


def synthetic_transcript(count=2000):
    return Transcript(
        [Segment(i * 1.8, (i + 1) * 1.8,
                 f"Segment {i + 1}: We reviewed the release plan and agreed to test the next build. 我们确认了下一步。")
         for i in range(count)],
        count * 1.8, "Synthetic ASR", count * 0.09, title="Release discussion",
        notes="## Summary\nThe team reviewed the release plan.\n\n"
              "## Decisions\n- Test the next build before release.\n\n"
              "## Action items\n- Verify transcription and export. Owner and deadline not mentioned.",
    )


def settle(iterations=3):
    for _ in range(iterations):
        QApplication.processEvents()


def heights(tab):
    return {name: getattr(tab, name).height() for name in
            ("_setup_card", "_drop", "_result", "_view", "_notes_card", "_notes_view", "_history_card", "_history_list")
            if hasattr(tab, name)}


def benchmark_viewer(output, label, baseline_ref):
    from PySide6.QtWidgets import QScrollArea
    if baseline_ref:
        source = subprocess.run(["git", "show", f"{baseline_ref}:thundertalk/ui/studio/parts.py"],
                                capture_output=True, text=True, check=True).stdout
        module = types.ModuleType("baseline_parts")
        exec(compile(source, "baseline_parts.py", "exec"), module.__dict__)
        view = module.TranscriptView()
    else:
        from thundertalk.ui.studio.parts import TranscriptView
        view = TranscriptView()
    window = QScrollArea()
    window.resize(860, 440)
    window.setWidgetResizable(True)
    window.setWidget(view)
    start = time.perf_counter()
    view.set_transcript(synthetic_transcript())
    window.show()
    settle()
    load_ms = (time.perf_counter() - start) * 1000
    print(f"Viewer load: {load_ms:.3f} ms", flush=True)
    samples = {"plain": [], "time": []}
    for _ in range(10):
        for key in samples:
            start = time.perf_counter()
            view.set_timestamps(key == "time")
            settle()
            elapsed = (time.perf_counter() - start) * 1000
            samples[key].append(elapsed)
            print(f"{key}: {elapsed:.3f} ms", flush=True)
    result = {"load_ms": load_ms, "widget_count": len(view.findChildren(QWidget)),
              "toggle_ms": {key: {"median": statistics.median(values), "max": max(values), "samples": values}
                            for key, values in samples.items()}}
    (output / f"{label}-viewer-measurements.json").write_text(json.dumps(result, indent=2) + "\n")
    window.close()
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-dir", default="artifacts/studio-layout")
    parser.add_argument("--viewer-only", action="store_true")
    parser.add_argument("--baseline-ref", help="Load the viewer from this git revision in memory")
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    theme.force_light(app)
    if args.viewer_only:
        benchmark_viewer(output, args.label, args.baseline_ref)
        return
    result = {"label": args.label, "captures": []}
    with tempfile.TemporaryDirectory(dir=output) as home, \
            patch.object(Path, "home", return_value=Path(home)), \
            patch.object(i18n, "LANG", "en"), \
            patch("thundertalk.ui.studio.speak_tab.AUTO_PRELOAD", False):
        page = StudioPage(SimpleNamespace(microphone="auto"))
        tab = page.transcribe_tab
        page.set_engine(SimpleNamespace(is_loaded=True, current_model="qwen3-asr-06b-int8"))
        page.show()
        for state in ("setup", "completed"):
            if state == "completed":
                tab._on_done(synthetic_transcript(24))
            for width, height in ((1200, 800), (1600, 1000)):
                page.resize(width, height)
                settle(20)
                path = output / f"{args.label}-{state}-{width}x{height}.png"
                page.grab().save(str(path))
                result["captures"].append({"path": str(path), "heights": heights(tab)})
                if state == "completed":
                    path = output / f"{args.label}-completed-tab-{width}x{height}.png"
                    capture = QPixmap(tab.size())
                    capture.fill(QColor(theme.BG_BASE))
                    tab.render(capture)
                    capture.save(str(path))
                    result["captures"].append({"path": str(path), "heights": heights(tab)})

        start = time.perf_counter()
        tab._show_result(synthetic_transcript())
        settle()
        result["load_ms"] = (time.perf_counter() - start) * 1000
        samples = {"plain": [], "time": []}
        for _ in range(10):
            for key in samples:
                start = time.perf_counter()
                tab._time_toggle.set_current(key)
                tab._time_toggle.changed.emit(key)
                settle()
                samples[key].append((time.perf_counter() - start) * 1000)
        result["toggle_ms"] = {
            key: {"median": statistics.median(values), "max": max(values), "samples": values}
            for key, values in samples.items()
        }
        result["widget_count"] = len(tab._view.findChildren(QWidget))
        result["long_heights"] = heights(tab)
        page.shutdown()
        page.close()
    (output / f"{args.label}-measurements.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
