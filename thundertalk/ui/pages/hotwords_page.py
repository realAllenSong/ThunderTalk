"""Hotwords page — manage custom vocabulary for ASR recognition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.i18n import t
from thundertalk.ui import theme

if TYPE_CHECKING:
    from thundertalk.core.settings import Settings


class _WordChip(QFrame):
    """Removable pill chip for a single hotword."""

    remove_clicked = Signal(str)

    def __init__(self, word: str) -> None:
        super().__init__()
        self.word = word
        self.setStyleSheet(
            f"QFrame {{ background: transparent;"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 14px; }}"
            f"QFrame:hover {{ border: 1px solid {theme.BORDER_STRONG}; }}"
        )
        self.setFixedHeight(28)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 6, 0)
        row.setSpacing(6)

        label = QLabel(word)
        label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px; border: none;")
        row.addWidget(label)

        close_btn = QLabel("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        close_btn.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px; border: none;"
            f" background: transparent; border-radius: 9px;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.mousePressEvent = lambda ev: self.remove_clicked.emit(self.word)
        row.addWidget(close_btn)

    def enterEvent(self, ev) -> None:
        self.setStyleSheet(
            f"QFrame {{ background: transparent;"
            f" border: 1px solid {theme.BORDER_STRONG}; border-radius: 14px; }}"
        )

    def leaveEvent(self, ev) -> None:
        self.setStyleSheet(
            f"QFrame {{ background: transparent;"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 14px; }}"
        )


class _FlowLayout(QVBoxLayout):
    """Simple flow-like layout using rows of QHBoxLayouts."""
    pass


class HotwordsPage(QWidget):
    hotwords_changed = Signal(list)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(32, 32, 32, 20)
        self._layout.setSpacing(16)
        scroll.setWidget(container)

        self._heading = QLabel(t("hotwords.title"))
        self._heading.setFont(theme.font_heading(20))
        self._heading.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        self._layout.addWidget(self._heading)

        self._subtitle = QLabel(t("hotwords.desc"))
        self._subtitle.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
        self._subtitle.setWordWrap(True)
        self._layout.addWidget(self._subtitle)

        # --- Add word card ---
        add_card = theme.make_card()
        ac = QVBoxLayout(add_card)
        ac.setContentsMargins(20, 18, 20, 18)
        ac.setSpacing(12)

        sec = QLabel("Add Word")
        sec.setFont(theme.font(14, bold=True))
        sec.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        ac.addWidget(sec)

        ac.addWidget(theme.separator())

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText(t("hotwords.placeholder"))
        self._input.setStyleSheet(theme.INPUT_QSS)
        add_row.addWidget(self._input)

        self._add_btn = theme.accent_button(t("hotwords.add"), height=36)
        self._add_btn.setFixedWidth(72)
        self._add_btn.clicked.connect(self._add_word)
        self._input.returnPressed.connect(self._add_word)
        add_row.addWidget(self._add_btn)
        ac.addLayout(add_row)

        hint = QLabel("Press Enter or click Add. Words are saved automatically.")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px; border: none;")
        ac.addWidget(hint)
        self._layout.addWidget(add_card)

        # --- Words card ---
        words_card = theme.make_card()
        wc = QVBoxLayout(words_card)
        wc.setContentsMargins(20, 18, 20, 18)
        wc.setSpacing(12)

        header_row = QHBoxLayout()
        sec2 = QLabel("Custom Vocabulary")
        sec2.setFont(theme.font(14, bold=True))
        sec2.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        header_row.addWidget(sec2)

        header_row.addStretch()

        self._count_label = QLabel()
        self._count_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px;"
            f" background: transparent; border: 1px solid {theme.BORDER_SUBTLE};"
            " border-radius: 8px; padding: 2px 10px;"
        )
        header_row.addWidget(self._count_label)
        wc.addLayout(header_row)

        wc.addWidget(theme.separator())

        # Flow container for word chips
        self._chips_container = QWidget()
        self._chips_container.setStyleSheet("background: transparent;")
        self._chips_layout = QVBoxLayout(self._chips_container)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(8)
        wc.addWidget(self._chips_container)

        self._empty_label = QLabel("No hotwords added yet. Add words above to get started.")
        self._empty_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px; border: none; padding: 20px 0;"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wc.addWidget(self._empty_label)

        self._layout.addWidget(words_card)

        self._layout.addStretch()

        self._rebuild_chips()

    def _rebuild_chips(self) -> None:
        # Clear existing chips
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        words = self._settings.hotwords
        self._count_label.setText(f"{len(words)} words")
        self._empty_label.setVisible(len(words) == 0)
        self._chips_container.setVisible(len(words) > 0)

        if not words:
            return

        # Build rows of chips (flow layout simulation)
        current_row = QHBoxLayout()
        current_row.setSpacing(6)
        current_row.setContentsMargins(0, 0, 0, 0)
        row_width = 0
        max_width = 500

        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")

        for word in words:
            chip = _WordChip(word)
            chip.remove_clicked.connect(self._remove_word)
            estimated_width = len(word) * 9 + 50
            if row_width + estimated_width > max_width and row_width > 0:
                current_row.addStretch()
                row_widget.setLayout(current_row)
                self._chips_layout.addWidget(row_widget)
                current_row = QHBoxLayout()
                current_row.setSpacing(6)
                current_row.setContentsMargins(0, 0, 0, 0)
                row_widget = QWidget()
                row_widget.setStyleSheet("background: transparent;")
                row_width = 0
            current_row.addWidget(chip)
            row_width += estimated_width

        current_row.addStretch()
        row_widget.setLayout(current_row)
        self._chips_layout.addWidget(row_widget)

    def _add_word(self) -> None:
        word = self._input.text().strip()
        if not word:
            return
        words = self._settings.hotwords
        if word not in words:
            words.append(word)
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
        self._heading.setText(t("hotwords.title"))
        self._subtitle.setText(t("hotwords.desc"))
        self._input.setPlaceholderText(t("hotwords.placeholder"))
        self._add_btn.setText(t("hotwords.add"))
