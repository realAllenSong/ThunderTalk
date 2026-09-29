"""Hotwords — custom vocabulary the recognizer should favour."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import state as st
from thundertalk.core.i18n import t
from thundertalk.ui import theme
from thundertalk.ui.icons import paint_icon
from thundertalk.ui.widgets import FlowLayout, PageHeader, column_scroll

if TYPE_CHECKING:
    from thundertalk.core.settings import Settings
    from thundertalk.core.state import AppState

_SPLIT = re.compile(r"[,\n;，、；]+")


class _WordChip(QWidget):
    """Removable pill for one hotword (self-painted: clean hover, no QSS)."""

    remove_clicked = Signal(str)

    def __init__(self, word: str) -> None:
        super().__init__()
        self.word = word
        self._hover = False
        self._x_hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._font = theme.font(13)
        fm = QFontMetrics(self._font)
        self.setFixedSize(fm.horizontalAdvance(word) + 16 + 32, 32)
        self.setToolTip(t("hotwords.remove"))

    def _x_rect(self) -> QRectF:
        return QRectF(self.width() - 26, (self.height() - 18) / 2, 18, 18)

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = self._x_hover = False
        self.update()
        super().leaveEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        over = self._x_rect().contains(ev.position())
        if over != self._x_hover:
            self._x_hover = over
            self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, ev) -> None:
        if self._x_rect().contains(ev.position()):
            self.remove_clicked.emit(self.word)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor("#F3F1EC"))
        p.setPen(QPen(theme._BORDER_STRONG_C if self._hover else theme._BORDER_DEFAULT_C, 1))
        p.drawRoundedRect(r, 5, 5)
        p.setFont(self._font)
        p.setPen(QColor(theme.TEXT_PRIMARY))
        p.drawText(QRectF(14, 0, self.width() - 38, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.word)
        xr = self._x_rect()
        if self._x_hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme._PRESS_FILL_C)
            p.drawEllipse(xr)
        paint_icon(p, "x", xr.adjusted(4.5, 4.5, -4.5, -4.5),
                   theme.TEXT_PRIMARY if self._x_hover else theme.TEXT_MUTED, 2.4)
        p.end()


class _InfoStrip(theme.Card):
    """Amber note (used when the active model ignores hotwords)."""

    def __init__(self) -> None:
        super().__init__(radius=14, accent=theme.WARNING)
        ly = QHBoxLayout(self)
        ly.setContentsMargins(50, 12, 18, 12)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px; background: transparent;")
        ly.addWidget(self._label)
        self.hide()

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        paint_icon(p, "info", QRectF(18, (self.height() - 20) / 2, 20, 20), theme.WARNING, 2.0)
        p.end()


class HotwordsPage(QWidget):
    hotwords_changed = Signal(list)
    word_added = Signal(str)

    def __init__(self, settings: "Settings", state: Optional["AppState"] = None) -> None:
        super().__init__()
        self._settings = settings
        self._state = state

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll, self._layout = column_scroll(spacing=16)
        root.addWidget(scroll)

        self._header = PageHeader(t("hotwords.title"), t("hotwords.desc"))
        self._layout.addWidget(self._header)

        self._info = _InfoStrip()
        self._layout.addWidget(self._info)

        # Add-word card
        add_card = theme.Card()
        ac = QVBoxLayout(add_card)
        ac.setContentsMargins(24, 20, 24, 20)
        ac.setSpacing(12)
        self._sec_add = QLabel(t("hotwords.add_word"))
        self._sec_add.setFont(theme.font_serif(17))
        self._sec_add.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        ac.addWidget(self._sec_add)

        add_row = QHBoxLayout()
        add_row.setSpacing(10)
        self._input = QLineEdit()
        self._input.setPlaceholderText(t("hotwords.placeholder"))
        self._input.setFixedHeight(40)
        self._input.setStyleSheet(theme.INPUT_QSS)
        self._input.returnPressed.connect(self._add_from_input)
        add_row.addWidget(self._input)
        self._add_btn = theme.make_button(t("hotwords.add"), "primary", 40)
        self._add_btn.setFixedWidth(90)
        self._add_btn.clicked.connect(self._add_from_input)
        add_row.addWidget(self._add_btn)
        ac.addLayout(add_row)

        self._add_hint = QLabel(t("hotwords.add_hint2"))
        self._add_hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        ac.addWidget(self._add_hint)
        self._layout.addWidget(add_card)

        # Vocabulary card
        words_card = theme.Card()
        wc = QVBoxLayout(words_card)
        wc.setContentsMargins(24, 20, 24, 22)
        wc.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(10)
        self._sec_vocab = QLabel(t("hotwords.custom_vocab"))
        self._sec_vocab.setFont(theme.font_serif(17))
        self._sec_vocab.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        head.addWidget(self._sec_vocab)
        head.addStretch()
        self._count = theme.badge("", "muted")
        head.addWidget(self._count)
        wc.addLayout(head)

        self._chips_host = QWidget()
        self._chips_host.setStyleSheet("background: transparent;")
        self._flow = FlowLayout(self._chips_host, 8, 8)
        wc.addWidget(self._chips_host)

        self._empty = QLabel(t("hotwords.empty"))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 13px; background: transparent; padding: 22px 0;")
        wc.addWidget(self._empty)
        self._layout.addWidget(words_card)
        self._layout.addStretch()

        if self._state is not None:
            self._state.model_changed.connect(self._update_info)
        self._rebuild_chips()
        self._update_info()

    # ── model capability note ──
    def _update_info(self) -> None:
        s = self._state
        if s is None or s.model_status != st.MODEL_READY:
            self._info.hide()
            return
        from thundertalk.core.models import BUILTIN_MODELS
        info = next((m for m in BUILTIN_MODELS if m.id == s.model_id), None)
        if info is not None and not info.hotword_support:
            self._info.set_text(t("hotwords.unsupported").format(name=info.name))
            self._info.show()
        else:
            self._info.hide()

    # ── words ──
    def _rebuild_chips(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        words = self._settings.hotwords
        self._count.setText(t("hotwords.count").format(n=len(words)))
        self._empty.setVisible(not words)
        self._chips_host.setVisible(bool(words))
        for word in words:
            chip = _WordChip(word)
            chip.remove_clicked.connect(self._remove_word)
            self._flow.addWidget(chip)
        self._chips_host.updateGeometry()

    def _add_from_input(self) -> None:
        raw = self._input.text()
        added = [w for w in (x.strip() for x in _SPLIT.split(raw)) if w]
        if not added:
            return
        changed = False
        words = self._settings.hotwords
        for w in added:
            if w not in words:
                words.append(w)
                changed = True
                self.word_added.emit(w)
        if changed:
            self._settings.set("hotwords", words)
            self._rebuild_chips()
            self.hotwords_changed.emit(words)
        self._input.clear()

    def add_hotword_external(self, word: str) -> None:
        word = word.strip()
        if not word:
            return
        words = self._settings.hotwords
        if word not in words:
            words.append(word)
            self._settings.set("hotwords", words)
            self._rebuild_chips()
            self.hotwords_changed.emit(words)

    def _remove_word(self, word: str) -> None:
        words = self._settings.hotwords
        if word in words:
            words.remove(word)
            self._settings.set("hotwords", words)
            self._rebuild_chips()
            self.hotwords_changed.emit(words)

    def retranslate(self) -> None:
        self._header.set_title(t("hotwords.title"))
        self._header.set_subtitle(t("hotwords.desc"))
        self._input.setPlaceholderText(t("hotwords.placeholder"))
        self._add_btn.setText(t("hotwords.add"))
        self._sec_add.setText(t("hotwords.add_word"))
        self._add_hint.setText(t("hotwords.add_hint2"))
        self._sec_vocab.setText(t("hotwords.custom_vocab"))
        self._empty.setText(t("hotwords.empty"))
        self._rebuild_chips()
        self._update_info()
