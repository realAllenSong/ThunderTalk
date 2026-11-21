"""Recommend ThunderTalk: a ready text for the place the user will post it.

The user picks the place; the text follows it (some places do not allow
links). It is a plain description they can edit in the window. What they end
up with is put on the clipboard for them to paste, and for X their browser can
open the composer with it. Nothing is posted by the app.
"""

from __future__ import annotations

import webbrowser
from typing import Optional

from PySide6.QtCore import QLocale, QPointF, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import i18n, promo
from thundertalk.core.i18n import t
from thundertalk.ui import theme
from thundertalk.ui.styled_dialog import paint_panel


def _chip_qss() -> str:
    return (
        f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY};"
        f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 5px;"
        " padding: 0 12px; font-size: 12px; }"
        f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; border: 1px solid {theme.BORDER_STRONG}; }}"
        f"QPushButton:checked {{ background: {theme.BG_CARD}; color: {theme.TEXT_PRIMARY};"
        f" border: 1.5px solid {theme.INK}; font-weight: 600; }}")


def _switch_qss() -> str:
    return (
        f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED}; border: none;"
        " border-bottom: 1px solid transparent; border-radius: 0; padding: 2px 1px; font-size: 12px; }"
        f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}"
        f"QPushButton:checked {{ color: {theme.TEXT_PRIMARY}; border-bottom: 1px solid {theme.INK}; font-weight: 600; }}")


class RecommendDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None, language: str = "") -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(584)
        self._lang = language or promo.share_language(i18n.LANG, QLocale.system().uiLanguages())
        self._dest = promo.DESTINATIONS[self._lang][0].id

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 16)
        panel = QWidget()
        panel.setStyleSheet("background: transparent;")
        outer.addWidget(panel)
        ly = QVBoxLayout(panel)
        ly.setContentsMargins(30, 26, 30, 24)
        ly.setSpacing(12)

        title = QLabel(t("promo.dialog.title"))
        title.setFont(theme.font_serif(20))
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        ly.addWidget(title)
        sub = QLabel(t("promo.dialog.sub"))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px; background: transparent;")
        ly.addWidget(sub)
        ly.addSpacing(4)

        self._chips = QHBoxLayout()
        self._chips.setSpacing(6)
        ly.addLayout(self._chips)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._text = QPlainTextEdit()                # editable: the ready text is a starting point
        self._text.setFixedHeight(92)
        self._text.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 6px; padding: 8px 10px; font-size: 13px; }}")
        ly.addWidget(self._text)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        ly.addWidget(self._note)
        ly.addSpacing(6)
        self._tag = theme.badge("", "green")

        foot = QHBoxLayout()
        foot.setSpacing(12)
        self._lang_btns: dict[str, QPushButton] = {}
        lang_group = QButtonGroup(self)
        lang_group.setExclusive(True)
        for code, name in (("en", "English"), ("zh", "中文")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_switch_qss())
            b.setChecked(code == self._lang)
            b.clicked.connect(lambda _=False, c=code: self.set_language(c))
            lang_group.addButton(b)
            self._lang_btns[code] = b
            foot.addWidget(b)
        foot.addStretch()
        close = theme.make_button(t("promo.dialog.close"), "secondary", 36)
        close.clicked.connect(self.reject)
        close.setShortcut("Esc")
        foot.addWidget(close)
        self._copy = theme.make_button("", "primary", 36)
        self._copy.setDefault(True)
        self._copy.clicked.connect(self._on_copy)
        foot.addWidget(self._copy)
        ly.addLayout(foot)

        self._copied = QTimer(self)
        self._copied.setSingleShot(True)
        self._copied.setInterval(1600)
        self._copied.timeout.connect(self._label_copy)
        self._build_chips()

    # ── choices ──
    def _build_chips(self) -> None:
        for b in self._group.buttons():
            self._group.removeButton(b)
            b.setParent(None)                    # gone now, not at the next turn of the event loop
            b.deleteLater()
        while self._chips.count():
            self._chips.takeAt(0)                # buttons are deleted above; the tag is added back below
        for d in promo.DESTINATIONS[self._lang]:
            b = QPushButton(d.label)
            b.setCheckable(True)
            b.setFixedHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_chip_qss())
            b.setChecked(d.id == self._dest)
            b.clicked.connect(lambda _=False, i=d.id: self.select(i))
            self._group.addButton(b)
            self._chips.addWidget(b)
        self._chips.addStretch()
        self._chips.addWidget(self._tag)
        self._refresh()

    def set_language(self, code: str) -> None:
        if code not in promo.DESTINATIONS or code == self._lang:
            return
        self._lang = code
        self._dest = promo.destination(code, self._dest).id      # same place if both languages offer it
        self._lang_btns[code].setChecked(True)
        self._build_chips()

    def select(self, dest_id: str) -> None:
        if promo.destination(self._lang, dest_id).id == self._dest:
            return                               # the same place again: keep what the user typed
        self._dest = promo.destination(self._lang, dest_id).id
        for b, d in zip(self._group.buttons(), promo.DESTINATIONS[self._lang]):
            b.setChecked(d.id == self._dest)
        self._refresh()

    def _refresh(self) -> None:
        d = promo.destination(self._lang, self._dest)
        self._text.setPlainText(promo.share_text(self._lang, d.id))
        fg, bg, bd = theme.PASTELS["green" if d.link else "amber"]
        self._tag.set_colors(fg, bg, bd)
        self._tag.setText(t("promo.dialog.link_yes" if d.link else "promo.dialog.link_none"))
        self._note.setText("" if d.link else t("promo.dialog.link_no"))
        self._note.setVisible(not d.link)
        self._label_copy()

    def _label_copy(self) -> None:
        d = promo.destination(self._lang, self._dest)
        self._copy.setText(t("promo.dialog.copy_open").format(place=d.label) if d.compose else t("promo.dialog.copy"))

    # ── action ──
    @property
    def text(self) -> str:
        return self._text.toPlainText()

    def _on_copy(self) -> None:
        d = promo.destination(self._lang, self._dest)
        QApplication.clipboard().setText(self.text)
        url = promo.compose_url(self._lang, d.id, self.text)
        if url:
            webbrowser.open(url)
        self._copy.setText(t("promo.dialog.copied"))
        self._copied.start()

    def paintEvent(self, _ev) -> None:
        paint_panel(self)

    @classmethod
    def open_over(cls, parent: Optional[QWidget]) -> None:
        dlg = cls(parent)
        if parent is not None:
            geo = parent.window().frameGeometry()
            dlg.adjustSize()
            dlg.move(QPointF(geo.center().x() - dlg.width() // 2, geo.center().y() - dlg.height() // 2).toPoint())
        dlg.exec()
