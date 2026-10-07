"""Studio — turn recordings into text, text into natural speech, and ideas into music.

Replaces the old experimental Lab: no external server, no ffmpeg, no scipy —
everything it needs ships inside the app or is a one-click model download."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from thundertalk.core.i18n import t
from thundertalk.ui.studio.speak_tab import SpeakTab
from thundertalk.ui.studio.transcribe_tab import TranscribeTab
from thundertalk.ui.widgets import PageHeader, SegmentedControl, column_scroll


class _StudioStack(QWidget):
    """Hidden tabs must not contribute minimum heights or height-for-width.

    QStackedLayout measures every page, including the inactive Speak tab.
    A normal vertical layout excludes hidden pages from all three measures.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pages: list[QWidget] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def addWidget(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)
        widget.setVisible(not self._pages)
        self._pages.append(widget)

    def setCurrentIndex(self, index: int) -> None:
        for i, page in enumerate(self._pages):
            page.setVisible(i == index)


def _compose_supported() -> bool:
    from thundertalk.core import music
    from thundertalk.core.music_process import total_memory_mb
    return music.supported(total_memory_mb() / 1024)


class StudioPage(QWidget):
    navigate_requested = Signal(str)
    toast_requested = Signal(str, str)

    def __init__(self, settings=None, compose: Optional[bool] = None, make_compose=None) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll, layout = column_scroll(spacing=20)
        root.addWidget(scroll)

        self._header = PageHeader(t("studio.title"), t("studio.subtitle"))
        layout.addWidget(self._header)

        self._stack = _StudioStack()
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._transcribe = TranscribeTab()
        self._speak = SpeakTab(settings)
        self._compose = None
        if compose if compose is not None else _compose_supported():
            if make_compose is None:
                from thundertalk.ui.studio.compose_tab import ComposeTab
                make_compose = ComposeTab
            self._compose = make_compose(settings)
        self._keys = ["transcribe", "speak"] + (["compose"] if self._compose is not None else [])

        self._tabs = SegmentedControl(self._tab_options(), "transcribe")
        self._tabs.changed.connect(self._on_tab)
        layout.addWidget(self._tabs)

        for tab in self._all_tabs():
            self._stack.addWidget(tab)
        layout.addWidget(self._stack)
        layout.addStretch()

        for tab in self._all_tabs():
            tab.navigate.connect(self.navigate_requested)
            tab.toast.connect(self.toast_requested)

        # Speak and Transcribe have no busy signal; a cheap poll keeps Compose's
        # Generate in step with them while the page is on screen.
        self._busy_poll = QTimer(self)
        self._busy_poll.setInterval(500)
        self._busy_poll.timeout.connect(self._sync_blocked)
        self._compose_blocked = False
        if self._compose is not None:
            self._compose.busy_changed.connect(self._on_compose_busy)

    # ── API ───────────────────────────────────────────────────────────
    def set_engine(self, engine) -> None:
        self._transcribe.set_engine(engine)
        self._speak.set_engine(engine)

    def show_tab(self, key: str) -> None:
        if key not in self._keys:
            return
        self._tabs.set_current(key)
        self._on_tab(key)

    @property
    def transcribe_tab(self) -> TranscribeTab:
        return self._transcribe

    @property
    def speak_tab(self) -> SpeakTab:
        return self._speak

    @property
    def compose_tab(self):
        """The Compose tab, or None where music is not offered."""
        return self._compose

    def retranslate(self) -> None:
        self._header.set_title(t("studio.title"))
        self._header.set_subtitle(t("studio.subtitle"))
        self._tabs.set_options(self._tab_options())
        for tab in self._all_tabs():
            tab.retranslate()

    # ── internals ─────────────────────────────────────────────────────
    def _all_tabs(self) -> list:
        return [self._transcribe, self._speak] + ([self._compose] if self._compose is not None else [])

    def _tab_options(self) -> list[tuple[str, str]]:
        return [(k, t(f"studio.tab.{k}")) for k in self._keys]

    def _on_tab(self, key: str) -> None:
        self._stack.setCurrentIndex(self._keys.index(key) if key in self._keys else 0)
        self._stack.updateGeometry()
        if key != "speak":
            self._speak.stop_playback()
        if key != "compose" and self._compose is not None:
            self._compose.stop_playback()

    def _on_compose_busy(self, on: bool) -> None:
        self._speak.set_blocked(on)
        self._transcribe.set_blocked(on)

    def _sync_blocked(self) -> None:
        if self._compose is None:
            return
        other = self._speak.busy() or self._transcribe.busy()
        if other != self._compose_blocked:
            self._compose_blocked = other
            self._compose.set_blocked(other)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._transcribe._view.set_height_limit(min(420, max(240, int(self.height() * 0.36))))

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        for tab in self._all_tabs():
            tab.refresh()
        if self._compose is not None:
            self._sync_blocked()
            self._busy_poll.start()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._busy_poll.stop()
        self._speak.stop_playback()
        if self._compose is not None:
            self._compose.stop_playback()

    def shutdown(self) -> None:
        """Called when the app quits."""
        self._busy_poll.stop()
        for tab in self._all_tabs():
            tab.shutdown()

    # Drop recordings (or a link dragged from the browser) anywhere on the page to transcribe them.
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls() and any(u.isLocalFile() or u.scheme() in ("http", "https")
                                           for u in ev.mimeData().urls()):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        urls = ev.mimeData().urls()
        files = [u.toLocalFile() for u in urls if u.isLocalFile()]
        web = [u.toString() for u in urls if u.scheme() in ("http", "https")]
        if not files and not web:
            return
        self.show_tab("transcribe")
        if len(files) == 1 and not web:
            self._transcribe.load_file(files[0])
        elif files:
            self._transcribe.add_files(files)
        if web:
            self._transcribe.add_links(" ".join(web))
