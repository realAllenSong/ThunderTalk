"""Offscreen 1200×800 model facts captures; no models or audio devices loaded."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget  # noqa: E402

from thundertalk.core import i18n  # noqa: E402
from thundertalk.core.settings import Settings  # noqa: E402
from thundertalk.ui import theme  # noqa: E402
from thundertalk.ui.pages.models_page import ModelsPage  # noqa: E402
from thundertalk.ui.studio import speak_tab  # noqa: E402


def settle():
    for _ in range(5):
        QApplication.processEvents()


def main():
    app = QApplication([])
    theme.force_light(app)
    speak_tab.AUTO_PRELOAD = False
    out = Path("artifacts/models-v2/screenshots")
    out.mkdir(parents=True, exist_ok=True)
    for lang in ("en", "zh"):
        i18n.set_language(lang)
        shell = QWidget()
        shell.setStyleSheet(f"background: {theme.BG_BASE};")
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(28, 20, 28, 20)
        page = ModelsPage(Settings())
        layout.addWidget(page)
        shell.resize(1200, 800)
        shell.show()
        page.wait_background()
        settle()
        shell.grab().save(str(out / f"models-{lang}-top.png"))
        scroll = page.findChild(QScrollArea)
        for key, family in (
            ("parakeet", "Parakeet-TDT-v3"),
            ("new-cpu", "FireRedASR2-CTC"),
        ):
            y = page._family_cards[family].y()
            scroll.verticalScrollBar().setValue(y)
            settle()
            shell.grab().save(str(out / f"models-{lang}-{key}.png"))
        shell.close()
        page.deleteLater()
        settle()
        for bid in ("voxcpm2", "indextts", "kokoro", "zipvoice"):
            tab = speak_tab.SpeakTab()
            tab._engine_pick.set_current(bid)
            tab._on_engine(bid)
            host = QWidget()
            host.setStyleSheet(f"background: {theme.BG_BASE};")
            ly = QVBoxLayout(host)
            ly.setContentsMargins(28, 20, 28, 20)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QScrollArea.NoFrame)
            sc.setWidget(tab)
            ly.addWidget(sc)
            host.resize(1200, 800)
            host.show()
            settle()
            host.grab().save(str(out / f"speak-{lang}-{bid}.png"))
            tab.shutdown()
            host.close()
    print(out.resolve())


if __name__ == "__main__":
    main()
