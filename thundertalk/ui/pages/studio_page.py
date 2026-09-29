"""Studio — turn recordings into text, and text into natural speech.

Replaces the old experimental Lab: no external server, no ffmpeg, no scipy —
everything it needs ships inside the app or is a one-click model download."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from thundertalk.core.i18n import t
from thundertalk.ui.studio.speak_tab import SpeakTab
from thundertalk.ui.studio.transcribe_tab import TranscribeTab
from thundertalk.ui.widgets import PageHeader, SegmentedControl, column_scroll


class StudioPage(QWidget):
    navigate_requested = Signal(str)
    toast_requested = Signal(str, str)

    def __init__(self, settings=None) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll, layout = column_scroll(spacing=20)
        root.addWidget(scroll)

        self._header = PageHeader(t("studio.title"), t("studio.subtitle"))
        layout.addWidget(self._header)

        self._tabs = SegmentedControl(self._tab_options(), "transcribe")
        self._tabs.changed.connect(self._on_tab)
        layout.addWidget(self._tabs)

        self._stack = QStackedWidget()
        self._transcribe = TranscribeTab()
        self._speak = SpeakTab(settings)
        self._stack.addWidget(self._transcribe)
        self._stack.addWidget(self._speak)
        layout.addWidget(self._stack)
        layout.addStretch()

        for tab in (self._transcribe, self._speak):
            tab.navigate.connect(self.navigate_requested)
            tab.toast.connect(self.toast_requested)

    # ── API ───────────────────────────────────────────────────────────
    def set_engine(self, engine) -> None:
        self._transcribe.set_engine(engine)
        self._speak.set_engine(engine)

    def show_tab(self, key: str) -> None:
        self._tabs.set_current(key)
        self._on_tab(key)

    @property
    def transcribe_tab(self) -> TranscribeTab:
        return self._transcribe

    @property
    def speak_tab(self) -> SpeakTab:
        return self._speak

    def retranslate(self) -> None:
        self._header.set_title(t("studio.title"))
        self._header.set_subtitle(t("studio.subtitle"))
        self._tabs.set_options(self._tab_options())
        self._transcribe.retranslate()
        self._speak.retranslate()

    # ── internals ─────────────────────────────────────────────────────
    @staticmethod
    def _tab_options() -> list[tuple[str, str]]:
        return [("transcribe", t("studio.tab.transcribe")), ("speak", t("studio.tab.speak"))]

    def _on_tab(self, key: str) -> None:
        self._stack.setCurrentIndex(0 if key == "transcribe" else 1)
        if key != "speak":
            self._speak.stop_playback()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._transcribe.refresh()
        self._speak.refresh()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._speak.stop_playback()

    def shutdown(self) -> None:
        """Called when the app quits."""
        self._transcribe.shutdown()
        self._speak.shutdown()

    # Drop a recording anywhere on the page to transcribe it.
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls() and any(u.isLocalFile() for u in ev.mimeData().urls()):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        for u in ev.mimeData().urls():
            if u.isLocalFile():
                self.show_tab("transcribe")
                self._transcribe.load_file(u.toLocalFile())
                break
