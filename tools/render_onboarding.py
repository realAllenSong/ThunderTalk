"""Capture the real first-run widgets offscreen, with OS permissions faked.

Run with a throwaway HOME and PYTHONPATH=$PWD; use an uncommitted output directory.
No audio capture, permission prompts, downloads or model loading are performed.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from thundertalk.core import i18n, platform_utils
from thundertalk.core.history import HistoryStore
from thundertalk.core.models import HardwareInfo
from thundertalk.core.settings import Settings
from thundertalk.core.state import AppState
from thundertalk.ui import theme
from thundertalk.ui.main_window import MainWindow
from thundertalk.ui.pages import models_page
from thundertalk.ui.studio import speak_tab


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if not Path.home().name.startswith("fresh-home-"):
        raise SystemExit("Use a throwaway HOME named fresh-home-*; this tool resets its synthetic history.")
    args.output.mkdir(parents=True, exist_ok=True)
    platform_utils.check_microphone = lambda: "not_determined"
    platform_utils.check_accessibility = lambda: False
    models_page.detect_hardware = lambda: HardwareInfo("Apple M3 Max", 36, "Apple M3 Max", "apple-silicon")
    speak_tab.AUTO_PRELOAD = False
    if not hasattr(MainWindow, "accept_onboarding_dictation"):
        MainWindow._setup_macos_titlebar = lambda self: None
    app = QApplication([])
    theme.force_light(app)
    began = time.perf_counter()
    timings = []
    for language in ("en", "zh"):
        i18n.LANG = language
        for width, height in ((1120, 780), (1200, 800)):
            settings, state = Settings(), AppState()
            history = HistoryStore()
            history.clear()
            window = MainWindow(settings, history, state)
            window.resize(width, height)
            window.show()
            window.show_onboarding()
            app.processEvents()
            window.models_page.wait_background()
            app.processEvents()
            timings.append(time.perf_counter() - began)
            overlay = window._onboarding
            for step, name in enumerate(("welcome", "permissions", "model", "try")):
                if step == 3:
                    state.set_model_ready(overlay._rec.id)
                    platform_utils.check_microphone = lambda: "authorized"
                    platform_utils.check_accessibility = lambda: True
                    state.refresh_permissions()
                overlay._go(step, animate=False)
                app.processEvents()
                window.grab().save(str(args.output / f"{language}-{width}x{height}-{name}.png"))
                text = [label.text() for label in overlay.findChildren(QLabel) + overlay.findChildren(QPushButton)
                        if label.isVisible() and label.text()]
                (args.output / f"{language}-{width}x{height}-{name}.txt").write_text("\n".join(text))
            if hasattr(overlay, "accept_dictation"):
                overlay.accept_dictation("Hello, ThunderTalk. 你好，语音输入。")
                app.processEvents()
                window.grab().save(str(args.output / f"{language}-{width}x{height}-success.png"))
            history.add("Hello, ThunderTalk. 你好，语音输入。", 3.6, 637, overlay._rec.id)
            window.home_page.refresh()
            overlay._finish(True)
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            window.grab().save(str(args.output / f"{language}-{width}x{height}-main.png"))
            window.hide()
            window.deleteLater()
            app.processEvents()
            platform_utils.check_microphone = lambda: "not_determined"
            platform_utils.check_accessibility = lambda: False
    print(json.dumps({"first_window_seconds_after_imports": timings[0],
                      "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6,
                      "torch_imported": "torch" in sys.modules, "captures": len(list(args.output.glob("*.png")))}))


if __name__ == "__main__":
    main()
