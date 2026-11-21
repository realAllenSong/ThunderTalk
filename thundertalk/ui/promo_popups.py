"""The two one-time popups: what changed after an update, and (once per
milestone of time saved) an invitation to recommend the app.

They open over the app's own window, only while it is the active window, and
at most one per launch: a dictation in another app is never interrupted. The
permanent way to recommend is on the About page.
"""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QPointF, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import promo
from thundertalk.core.i18n import t
from thundertalk.ui import theme
from thundertalk.ui.styled_dialog import paint_panel

if TYPE_CHECKING:
    from thundertalk.core.history import HistoryStore
    from thundertalk.core.settings import Settings


class _Popup(QDialog):
    """Frameless panel in the app's dialog style; subclasses fill ``self.body``."""

    def __init__(self, parent: Optional[QWidget], width: int) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 16)
        panel = QWidget()
        panel.setStyleSheet("background: transparent;")
        outer.addWidget(panel)
        self.body = QVBoxLayout(panel)
        self.body.setContentsMargins(30, 26, 30, 24)
        self.body.setSpacing(12)

    def _title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(theme.font_serif(20))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        self.body.addWidget(lbl)
        return lbl

    def paintEvent(self, _ev) -> None:
        paint_panel(self)

    def open_over(self, parent: Optional[QWidget]) -> int:
        if parent is not None:
            geo = parent.window().frameGeometry()
            self.adjustSize()
            self.move(QPointF(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2).toPoint())
        return self.exec()


class WhatsNewDialog(_Popup):
    """After an update: up to three things worth knowing, the full notes one click away."""

    def __init__(self, parent: Optional[QWidget], version: str, keys: tuple[str, ...]) -> None:
        super().__init__(parent, 560)
        self.version = version
        self._title(t("whatsnew.title"))        # no version: a note may date from a release the user skipped
        self.body.addSpacing(2)
        for key in keys:
            # One label per note: two stacked labels shared the dialog's spare height unevenly.
            item = QLabel(
                f"<div style='color:{theme.TEXT_PRIMARY}; font-size:14px; font-weight:700;'>{t(key)}</div>"
                f"<div style='color:{theme.TEXT_SECONDARY}; font-size:13px; margin-top:3px;'>{t(key + '.detail')}</div>")
            item.setTextFormat(Qt.TextFormat.RichText)
            item.setWordWrap(True)
            item.setStyleSheet("background: transparent;")
            self.body.addWidget(item)
        self.body.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        notes = theme.make_button(t("whatsnew.notes"), "secondary", 36)
        notes.clicked.connect(lambda: webbrowser.open(promo.RELEASE_URL.format(version=version)))
        row.addWidget(notes)
        ok = theme.make_button(t("whatsnew.ok"), "primary", 36)
        ok.setDefault(True)
        ok.setShortcut("Esc")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        self.body.addLayout(row)


class MilestoneDialog(_Popup):
    """"ThunderTalk has saved you about N hours of typing." → Recommend / Star / Not now."""

    RECOMMEND, STAR, LATER, OFF = "recommend", "star", "later", "off"

    def __init__(self, parent: Optional[QWidget], hours: int) -> None:
        super().__init__(parent, 560)
        self.choice = self.LATER
        self._title(t("promo.milestone.title").format(n=f"{hours:,}"))
        sub = QLabel(t("promo.milestone.sub"))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 14px; background: transparent;")
        self.body.addWidget(sub)
        self.body.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        off = QPushButton(t("promo.off"))
        off.setCursor(Qt.CursorShape.PointingHandCursor)
        off.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_MUTED}; background: transparent; border: none;"
            " font-size: 12px; padding: 0; }"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}")
        off.clicked.connect(lambda: self._choose(self.OFF))
        row.addWidget(off)
        row.addStretch()
        later = theme.make_button(t("promo.later"), "ghost", 36)
        later.setShortcut("Esc")
        later.clicked.connect(lambda: self._choose(self.LATER))
        row.addWidget(later)
        star = theme.make_button(t("promo.star"), "secondary", 36)
        star.clicked.connect(lambda: self._choose(self.STAR))
        row.addWidget(star)
        rec = theme.make_button(t("promo.recommend"), "primary", 36)
        rec.setDefault(True)
        rec.clicked.connect(lambda: self._choose(self.RECOMMEND))
        row.addWidget(rec)
        self.body.addLayout(row)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class PromoPopups(QObject):
    """Decides when one of the two popups opens over ``window``. Call ``check()``
    whenever the window may have become the place the user is looking at."""

    def __init__(self, window: QWidget, settings: "Settings", history: "HistoryStore") -> None:
        super().__init__(window)
        self._window, self._settings, self._history = window, settings, history
        self._shown = False
        self._busy = False
        # The version that ran before this launch: the app overwrites the setting a moment later.
        self._last_run = settings.get("last_run_version") or ""
        if not self._last_run:
            # A new install: nothing to catch up on, now or after the first relaunch.
            import thundertalk
            settings.set("whats_new_seen", thundertalk.__version__)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(900)           # let the window settle; never the first thing on screen
        self._timer.timeout.connect(self._show)

    def check(self) -> None:
        if not self._shown and not self._busy:
            self._timer.start()

    def pending(self) -> Optional[tuple]:
        import thundertalk
        s, h = self._settings, self._history
        return promo.next_popup(
            current=thundertalk.__version__, seen=s.get("whats_new_seen") or "", last_run=self._last_run,
            saved_secs=promo.saved_seconds(h.total_units, h.total_duration_secs),
            acknowledged_hours=s.get("promo_milestone_hours") or 0, off=s.get("promo_off") is True,
            setup_done=bool(s.get("onboarding_done")), shown_this_launch=self._shown)

    def _in_the_way(self) -> bool:
        w = self._window
        return (not w.isVisible() or not w.isActiveWindow() or QApplication.activeModalWidget() is not None
                or getattr(w, "_onboarding", None) is not None)

    def _show(self) -> None:
        if self._busy or self._in_the_way():
            return                             # the next activation asks again
        what = self.pending()
        if what is None:
            return
        self._shown = self._busy = True
        try:
            if what[0] == "whatsnew":
                _, version, keys = what
                WhatsNewDialog(self._window, version, keys).open_over(self._window)
                self._settings.set("whats_new_seen", version)
            else:
                _, milestone, hours = what
                dlg = MilestoneDialog(self._window, hours)
                dlg.open_over(self._window)
                if dlg.choice == MilestoneDialog.OFF:
                    self._settings.set("promo_off", True)
                self._settings.set("promo_milestone_hours", milestone)
                if dlg.choice == MilestoneDialog.STAR:
                    webbrowser.open(promo.REPO_URL)
                elif dlg.choice == MilestoneDialog.RECOMMEND:
                    from thundertalk.ui.recommend_dialog import RecommendDialog
                    RecommendDialog.open_over(self._window)
        finally:
            self._busy = False
