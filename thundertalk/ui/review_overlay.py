"""Translation Review popup — appears after ASR + T2TT to let the user
choose whether to replace the just-pasted original with the translation.

Replace → undo the original paste (Cmd+Z) and paste the translated text.
Keep Original → dismiss; original stays.
Auto-dismiss after REVIEW_TIMEOUT_MS to keep the Cmd+Z trick reliable.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.i18n import t
from thundertalk.ui import theme

REVIEW_TIMEOUT_MS = 8000
_W, _H = 460, 240

# Display name lookup matching the SettingsPage TRANSLATION_TARGETS list.
_LANG_DISPLAY: dict[str, str] = {
    "eng": "English",
    "cmn": "中文",
    "jpn": "日本語",
    "spa": "Español",
    "fra": "Français",
    "deu": "Deutsch",
    "por": "Português",
    "rus": "Русский",
    "ita": "Italiano",
    "arb": "العربية",
    "hin": "हिन्दी",
}


class ReviewOverlay(QWidget):
    """Floating window asking the user to confirm or reject the translation."""

    replace_clicked = Signal(str)  # emits translated text on confirm
    keep_clicked = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFixedSize(_W, _H)

        self._translated_text = ""
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)

        # ── Card frame ──
        self._card = QFrame(self)
        self._card.setGeometry(0, 0, _W, _H)
        self._card.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_DEFAULT};"
            f" border-radius: 14px; }}"
        )

        ly = QVBoxLayout(self._card)
        ly.setContentsMargins(20, 16, 20, 16)
        ly.setSpacing(8)

        # Title
        self._title = QLabel("")
        self._title.setStyleSheet(
            f"color: {theme.ACCENT_ORANGE}; font-size: 12px;"
            " font-weight: bold; border: none;"
        )
        ly.addWidget(self._title)

        # Original section
        self._orig_label = QLabel(t("review.original").upper())
        self._orig_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px;"
            " font-weight: bold; border: none;"
        )
        ly.addWidget(self._orig_label)

        self._orig_text = QLabel("")
        self._orig_text.setWordWrap(True)
        self._orig_text.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 13px; border: none;"
        )
        self._orig_text.setMaximumHeight(48)
        ly.addWidget(self._orig_text)

        # Translated section
        self._trans_label = QLabel(t("review.translated").upper())
        self._trans_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px;"
            " font-weight: bold; border: none;"
            " padding-top: 4px;"
        )
        ly.addWidget(self._trans_label)

        self._trans_text = QLabel("")
        self._trans_text.setWordWrap(True)
        self._trans_text.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: 14px;"
            " font-weight: 500; border: none;"
        )
        self._trans_text.setMaximumHeight(48)
        ly.addWidget(self._trans_text)

        ly.addStretch()

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._keep_btn = QPushButton(t("review.keep"))
        self._keep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keep_btn.setFixedHeight(32)
        self._keep_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.BG_ELEVATED};"
            f" color: {theme.TEXT_SECONDARY}; border: 1px solid {theme.BORDER_SUBTLE};"
            " border-radius: 8px; font-size: 12px; padding: 6px 16px; }}"
            f"QPushButton:hover {{ background: {theme.BG_CARD_HOVER};"
            f" color: {theme.TEXT_PRIMARY}; }}"
        )
        self._keep_btn.clicked.connect(self._on_keep)
        btn_row.addWidget(self._keep_btn)

        btn_row.addStretch()

        self._replace_btn = QPushButton(t("review.replace"))
        self._replace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._replace_btn.setFixedHeight(32)
        self._replace_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.ACCENT_ORANGE}; color: #ffffff;"
            " border: none; border-radius: 8px; font-size: 12px;"
            " font-weight: bold; padding: 6px 18px; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_ORANGE_WARM}; }}"
        )
        self._replace_btn.clicked.connect(self._on_replace)
        btn_row.addWidget(self._replace_btn)

        ly.addLayout(btn_row)

        # Countdown progress bar (1px line at bottom)
        self._progress = QProgressBar(self._card)
        self._progress.setRange(0, REVIEW_TIMEOUT_MS)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(2)
        self._progress.setGeometry(20, _H - 6, _W - 40, 2)
        self._progress.setStyleSheet(
            f"QProgressBar {{ background: {theme.BG_ELEVATED}; border: none; }}"
            f"QProgressBar::chunk {{ background: {theme.ACCENT_ORANGE}; }}"
        )

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._update_progress)

    # ── public API ──────────────────────────────────────────────────────

    def show_review(self, original: str, translated: str, tgt_lang: str) -> None:
        """Display the popup with the given texts and target language code."""
        self._translated_text = translated
        lang_display = _LANG_DISPLAY.get(tgt_lang, tgt_lang)
        self._title.setText(t("review.title").format(lang=lang_display))
        self._orig_text.setText(self._truncate(original, 120))
        self._trans_text.setText(self._truncate(translated, 120))

        self._center()
        self.show()
        self.raise_()
        self._progress.setValue(REVIEW_TIMEOUT_MS)
        self._timeout.start(REVIEW_TIMEOUT_MS)
        self._tick_timer.start(40)

    def hide_review(self) -> None:
        self._timeout.stop()
        self._tick_timer.stop()
        self.hide()

    # ── internals ──────────────────────────────────────────────────────

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit].rstrip() + "…"

    def _center(self) -> None:
        s = self.screen()
        if s:
            geo = s.availableGeometry()
            x = geo.x() + (geo.width() - _W) // 2
            # Position lower than VoiceOverlay so they don't overlap
            y = geo.y() + geo.height() - _H - 80
            self.move(x, y)

    def _update_progress(self) -> None:
        remaining = self._timeout.remainingTime()
        self._progress.setValue(max(0, remaining))

    def _on_replace(self) -> None:
        text = self._translated_text
        self.hide_review()
        self.replace_clicked.emit(text)

    def _on_keep(self) -> None:
        self.hide_review()
        self.keep_clicked.emit()

    def _on_timeout(self) -> None:
        # Timeout = same outcome as Keep Original (don't replace)
        self.hide_review()
        self.keep_clicked.emit()

    def keyPressEvent(self, ev) -> None:
        # Allow ESC to dismiss (== Keep Original)
        if ev.key() == Qt.Key.Key_Escape:
            self._on_keep()
        elif ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_replace()
        else:
            super().keyPressEvent(ev)
