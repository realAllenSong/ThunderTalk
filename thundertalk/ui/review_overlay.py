"""Translation Review popup — appears immediately after the original is
pasted (with a Translating… loading state) and updates with the
translation when SeamlessM4T finishes. The user picks Replace (Cmd+Z
the original, paste translation) or Keep Original (dismiss).

Inline language combo lets the user switch target language without
visiting Settings; changes persist.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
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
_W, _H = 380, 138

# Inline language picker — same set as Settings TRANSLATION_TARGETS minus "off".
# Keep tuple order: ISO-639-3 code, display label.
_REVIEW_LANGS: list[tuple[str, str]] = [
    ("eng", "English"),
    ("cmn", "中文"),
    ("jpn", "日本語"),
    ("spa", "Español"),
    ("fra", "Français"),
    ("deu", "Deutsch"),
    ("por", "Português"),
    ("rus", "Русский"),
    ("ita", "Italiano"),
    ("arb", "العربية"),
    ("hin", "हिन्दी"),
]


class ReviewOverlay(QWidget):
    """Compact floating popup for translation review."""

    replace_clicked = Signal(str)             # translated text
    keep_clicked = Signal()
    lang_change_requested = Signal(str, str)  # original, new_tgt_lang

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
        self._original_text = ""
        self._tgt_lang = "eng"
        self._is_loading = False

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)

        # ── Card frame ──
        self._card = QFrame(self)
        self._card.setGeometry(0, 0, _W, _H)
        self._card.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_DEFAULT};"
            f" border-radius: 12px; }}"
        )

        ly = QVBoxLayout(self._card)
        ly.setContentsMargins(14, 10, 14, 10)
        ly.setSpacing(6)

        # ── Top row: status + language combo ──
        top = QHBoxLayout()
        top.setSpacing(8)

        self._status_label = QLabel("→")
        self._status_label.setStyleSheet(
            f"color: {theme.ACCENT_ORANGE}; font-size: 11px;"
            " font-weight: 600; border: none; letter-spacing: 0.3px;"
        )
        top.addWidget(self._status_label)

        top.addStretch()

        self._lang_combo = QComboBox()
        self._lang_combo.setFixedHeight(22)
        self._lang_combo.setFixedWidth(120)
        self._lang_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lang_combo.setStyleSheet(
            f"QComboBox {{ background: {theme.BG_ELEVATED};"
            f" color: {theme.TEXT_PRIMARY}; border: 1px solid {theme.BORDER_SUBTLE};"
            f" border-radius: 6px; padding: 1px 6px; font-size: 11px; }}"
            f"QComboBox:hover {{ border: 1px solid {theme.BORDER_DEFAULT}; }}"
            f"QComboBox::drop-down {{ border: none; width: 16px; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.BG_ELEVATED};"
            f" color: {theme.TEXT_PRIMARY}; border: 1px solid {theme.BORDER_DEFAULT};"
            " border-radius: 6px; padding: 4px; selection-background-color: "
            f"{theme.ACCENT_ORANGE}; selection-color: #ffffff; }}"
        )
        for code, display in _REVIEW_LANGS:
            self._lang_combo.addItem(display, code)
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self._lang_combo)
        ly.addLayout(top)

        # ── Original text (small, muted) ──
        self._orig_text = QLabel("")
        self._orig_text.setWordWrap(True)
        self._orig_text.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; border: none;"
        )
        self._orig_text.setMaximumHeight(28)
        ly.addWidget(self._orig_text)

        # ── Translated text (primary) ──
        self._trans_text = QLabel("")
        self._trans_text.setWordWrap(True)
        self._trans_text.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: 13px;"
            " font-weight: 500; border: none;"
        )
        self._trans_text.setMaximumHeight(38)
        ly.addWidget(self._trans_text)

        ly.addStretch()

        # ── Buttons row ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch()

        self._keep_btn = QPushButton(t("review.keep"))
        self._keep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keep_btn.setFixedHeight(26)
        self._keep_btn.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" color: {theme.TEXT_MUTED}; border: 1px solid {theme.BORDER_SUBTLE};"
            " border-radius: 6px; font-size: 11px; padding: 2px 12px; }}"
            f"QPushButton:hover {{ background: {theme.BG_ELEVATED};"
            f" color: {theme.TEXT_SECONDARY}; }}"
        )
        self._keep_btn.clicked.connect(self._on_keep)
        btn_row.addWidget(self._keep_btn)

        self._replace_btn = QPushButton(t("review.replace"))
        self._replace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._replace_btn.setFixedHeight(26)
        self._replace_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.ACCENT_ORANGE}; color: #ffffff;"
            " border: none; border-radius: 6px; font-size: 11px;"
            " font-weight: 600; padding: 2px 14px; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_ORANGE_WARM}; }}"
            f"QPushButton:disabled {{ background: {theme.BG_ELEVATED};"
            f" color: {theme.TEXT_MUTED}; }}"
        )
        self._replace_btn.clicked.connect(self._on_replace)
        btn_row.addWidget(self._replace_btn)

        ly.addLayout(btn_row)

        # ── Countdown progress bar (2px line at bottom) ──
        self._progress = QProgressBar(self._card)
        self._progress.setRange(0, REVIEW_TIMEOUT_MS)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(2)
        self._progress.setGeometry(14, _H - 5, _W - 28, 2)
        self._progress.setStyleSheet(
            f"QProgressBar {{ background: rgba(255,255,255,0.04); border: none;"
            " border-radius: 1px; }}"
            f"QProgressBar::chunk {{ background: {theme.ACCENT_ORANGE};"
            " border-radius: 1px; }}"
        )

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._update_progress)

    # ── public API ──────────────────────────────────────────────────────

    def show_review_loading(self, original: str, tgt_lang: str) -> None:
        """Show popup immediately with original; translation slot is loading."""
        self._original_text = original
        self._tgt_lang = tgt_lang
        self._translated_text = ""
        self._is_loading = True

        self._set_combo_lang(tgt_lang)
        lang_display = self._lang_display(tgt_lang)
        self._status_label.setText(f"↻ {t('review.translating')} → {lang_display}")
        self._orig_text.setText(self._truncate(original, 120))
        self._trans_text.setText("…")
        self._trans_text.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 13px;"
            " font-style: italic; border: none;"
        )
        self._replace_btn.setEnabled(False)

        self._center()
        self.show()
        self.raise_()
        self._progress.setValue(REVIEW_TIMEOUT_MS)
        self._timeout.start(REVIEW_TIMEOUT_MS)
        self._tick_timer.start(40)

    def update_translation(self, translated: str, tgt_lang: str) -> None:
        """Fill in the translation. Stale results (lang changed mid-flight) skipped."""
        if tgt_lang != self._tgt_lang:
            return
        self._translated_text = translated
        self._is_loading = False
        lang_display = self._lang_display(tgt_lang)
        self._status_label.setText(f"→ {lang_display}")
        self._trans_text.setText(self._truncate(translated, 120))
        self._trans_text.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: 13px;"
            " font-weight: 500; border: none;"
        )
        self._replace_btn.setEnabled(True)

    # Backwards-compat: keep show_review() for any older call sites; it
    # treats both texts as already-known.
    def show_review(self, original: str, translated: str, tgt_lang: str) -> None:
        self.show_review_loading(original, tgt_lang)
        self.update_translation(translated, tgt_lang)

    def hide_review(self) -> None:
        self._timeout.stop()
        self._tick_timer.stop()
        self.hide()

    # ── internals ──────────────────────────────────────────────────────

    @staticmethod
    def _lang_display(code: str) -> str:
        for c, d in _REVIEW_LANGS:
            if c == code:
                return d
        return code

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit].rstrip() + "…"

    def _set_combo_lang(self, code: str) -> None:
        # Block signals to avoid firing lang_change_requested while restoring
        self._lang_combo.blockSignals(True)
        for i in range(self._lang_combo.count()):
            if self._lang_combo.itemData(i) == code:
                self._lang_combo.setCurrentIndex(i)
                break
        self._lang_combo.blockSignals(False)

    def _center(self) -> None:
        s = self.screen()
        if s:
            geo = s.availableGeometry()
            x = geo.x() + (geo.width() - _W) // 2
            y = geo.y() + geo.height() - _H - 80
            self.move(x, y)

    def _update_progress(self) -> None:
        remaining = self._timeout.remainingTime()
        self._progress.setValue(max(0, remaining))

    def _on_lang_changed(self, idx: int) -> None:
        new_code = self._lang_combo.itemData(idx)
        if not new_code or new_code == self._tgt_lang:
            return
        self._tgt_lang = new_code
        # Reset to loading state for the new language
        self._is_loading = True
        self._translated_text = ""
        lang_display = self._lang_display(new_code)
        self._status_label.setText(f"↻ {t('review.translating')} → {lang_display}")
        self._trans_text.setText("…")
        self._trans_text.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 13px;"
            " font-style: italic; border: none;"
        )
        self._replace_btn.setEnabled(False)
        # Reset countdown
        self._timeout.start(REVIEW_TIMEOUT_MS)
        self._progress.setValue(REVIEW_TIMEOUT_MS)
        self.lang_change_requested.emit(self._original_text, new_code)

    def _on_replace(self) -> None:
        if self._is_loading or not self._translated_text:
            return
        text = self._translated_text
        self.hide_review()
        self.replace_clicked.emit(text)

    def _on_keep(self) -> None:
        self.hide_review()
        self.keep_clicked.emit()

    def _on_timeout(self) -> None:
        self.hide_review()
        self.keep_clicked.emit()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self._on_keep()
        elif ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_replace()
        else:
            super().keyPressEvent(ev)
