"""Translation Review popup — pill-styled floating window matching the
VoiceOverlay aesthetic. Appears in loading state when the original is
pasted; updates when SeamlessM4T T2TT finishes. User picks Replace
(Cmd+Z + paste translation) or Keep Original. Stays open until the
user decides; no auto-dismiss.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.i18n import t
from thundertalk.ui import theme

_W = 420
# Min/max height; the window auto-resizes to its content between these.
_H_MIN = 132
_H_MAX = 640
# Inner content width (must match contentsMargins(18, _, 18, _) on the
# main layout). Word-wrap QLabels need their wrap width pinned so
# heightForWidth() resolves to the correct multi-line height.
_CONTENT_W = _W - 36

# Inline language picker — matches Settings TRANSLATION_TARGETS minus "off".
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
    """Compact floating popup. Background is custom-painted to match the
    VoiceOverlay's pill aesthetic (dark translucent, rounded, subtle
    border — no harsh card frame)."""

    replace_clicked = Signal(str)
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
        self.setFixedWidth(_W)
        # Height grows with content via _resize_to_fit() called after each
        # text update; bounded by _H_MIN..._H_MAX.
        self.resize(_W, _H_MIN)

        self._translated_text = ""
        self._original_text = ""
        self._tgt_lang = "eng"
        self._is_loading = False
        # Window-drag state
        self._drag_pos = None

        ly = QVBoxLayout(self)
        ly.setContentsMargins(18, 12, 18, 12)
        ly.setSpacing(6)

        # ── Top row: status + language combo ──
        top = QHBoxLayout()
        top.setSpacing(8)

        self._status_label = QLabel("→")
        self._status_label.setStyleSheet(
            f"color: {theme.ACCENT_ORANGE}; font-size: 11px;"
            " font-weight: 600; background: transparent; border: none;"
            " letter-spacing: 0.3px;"
        )
        top.addWidget(self._status_label)
        top.addStretch()

        self._lang_combo = QComboBox()
        self._lang_combo.setFixedHeight(22)
        self._lang_combo.setFixedWidth(118)
        self._lang_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lang_combo.setStyleSheet(
            f"QComboBox {{ background: rgba(255,255,255,0.06);"
            f" color: {theme.TEXT_PRIMARY};"
            " border: 1px solid rgba(255,255,255,0.10);"
            " border-radius: 11px; padding: 1px 10px; font-size: 11px; }}"
            "QComboBox:hover { background: rgba(255,255,255,0.10); }"
            "QComboBox::drop-down { border: none; width: 14px; }"
            "QComboBox QAbstractItemView { background: #1a1a1d;"
            f" color: {theme.TEXT_PRIMARY}; border: 1px solid {theme.BORDER_DEFAULT};"
            " border-radius: 8px; padding: 4px;"
            f" selection-background-color: {theme.ACCENT_ORANGE};"
            " selection-color: #ffffff; }"
        )
        for code, display in _REVIEW_LANGS:
            self._lang_combo.addItem(display, code)
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self._lang_combo)
        ly.addLayout(top)

        # ── Original text (small, muted) ──
        self._orig_text = QLabel("")
        self._orig_text.setWordWrap(True)
        # Pin wrap width so heightForWidth() returns correct multi-line height.
        self._orig_text.setMaximumWidth(_CONTENT_W)
        self._orig_text.setMinimumWidth(_CONTENT_W)
        self._orig_text.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px;"
            " background: transparent; border: none; padding-top: 2px;"
        )
        ly.addWidget(self._orig_text)

        # Hairline separator between original and translated (1px, very faint)
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255, 255, 255, 0.05);")
        ly.addSpacing(2)
        ly.addWidget(sep)
        ly.addSpacing(2)

        # ── Translated text (primary) ──
        self._trans_text = QLabel("")
        self._trans_text.setWordWrap(True)
        self._trans_text.setMaximumWidth(_CONTENT_W)
        self._trans_text.setMinimumWidth(_CONTENT_W)
        ly.addWidget(self._trans_text)
        self._set_trans_loading_style()

        ly.addStretch()

        # ── Buttons row ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        self._keep_btn = QPushButton(t("review.keep"))
        self._keep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keep_btn.setFixedHeight(26)
        self._keep_btn.setStyleSheet(
            "QPushButton { background: transparent;"
            f" color: {theme.TEXT_MUTED};"
            " border: 1px solid rgba(255,255,255,0.10);"
            " border-radius: 13px; font-size: 11px; padding: 2px 14px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.06);"
            f" color: {theme.TEXT_SECONDARY}; }}"
        )
        self._keep_btn.clicked.connect(self._on_keep)
        btn_row.addWidget(self._keep_btn)

        self._replace_btn = QPushButton(t("review.replace"))
        self._replace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._replace_btn.setFixedHeight(26)
        self._replace_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.ACCENT_ORANGE}; color: #ffffff;"
            " border: none; border-radius: 13px; font-size: 11px;"
            " font-weight: 600; padding: 2px 16px; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_ORANGE_WARM}; }}"
            "QPushButton:disabled { background: rgba(255,255,255,0.06);"
            f" color: {theme.TEXT_MUTED}; }}"
        )
        self._replace_btn.clicked.connect(self._on_replace)
        btn_row.addWidget(self._replace_btn)

        ly.addLayout(btn_row)

    # ── public API ──────────────────────────────────────────────────────

    def show_review_loading(self, original: str, tgt_lang: str) -> None:
        """Show popup immediately with original; translation is pending."""
        self._original_text = original
        self._tgt_lang = tgt_lang
        self._translated_text = ""
        self._is_loading = True

        self._set_combo_lang(tgt_lang)
        lang_display = self._lang_display(tgt_lang)
        # Loading state — neutral muted text (no jittery icon)
        self._status_label.setText(t("review.translating"))
        self._status_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px;"
            " font-weight: 500; background: transparent; border: none;"
            " letter-spacing: 0.3px;"
        )
        self._orig_text.setText(original)
        self._trans_text.setText("")
        self._set_trans_loading_style()
        self._replace_btn.setEnabled(False)
        self._resize_to_fit()

        # Position near the cursor every show — gives the popup natural
        # spatial context (your eyes are near where you typed). User can
        # still drag during this popup session, but next show resets.
        self._position_near_cursor()
        self.show()
        self.raise_()

    def update_translation(self, translated: str, tgt_lang: str) -> None:
        """Fill in the translation. Stale results (lang switched mid-flight) skipped."""
        if tgt_lang != self._tgt_lang:
            return
        self._translated_text = translated
        self._is_loading = False
        # Ready state — accent orange title
        self._status_label.setText(t("review.translation_label"))
        self._status_label.setStyleSheet(
            f"color: {theme.ACCENT_ORANGE}; font-size: 11px;"
            " font-weight: 600; background: transparent; border: none;"
            " letter-spacing: 0.3px;"
        )
        self._trans_text.setText(translated)
        self._set_trans_ready_style()
        self._replace_btn.setEnabled(True)
        self._resize_to_fit()

    # Back-compat shim for any older call sites that pass both texts at once.
    def show_review(self, original: str, translated: str, tgt_lang: str) -> None:
        self.show_review_loading(original, tgt_lang)
        self.update_translation(translated, tgt_lang)

    def hide_review(self) -> None:
        self.hide()

    # ── painting (matches VoiceOverlay's pill style) ───────────────────

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        radius = 18

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)

        # Dark glass background
        p.fillPath(path, QColor(18, 18, 18, 240))
        # Hairline edge
        from PySide6.QtGui import QPen
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawPath(path)
        p.end()

    # ── internals ──────────────────────────────────────────────────────

    def _set_trans_loading_style(self) -> None:
        self._trans_text.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 13px;"
            " font-style: italic; background: transparent; border: none;"
        )

    def _set_trans_ready_style(self) -> None:
        self._trans_text.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: 13px;"
            " font-weight: 500; background: transparent; border: none;"
        )

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
            y = geo.y() + geo.height() - self.height() - 80
            self.move(x, y)

    def _resize_to_fit(self) -> None:
        """Auto-size the popup to fit its full content, bounded by
        [_H_MIN, _H_MAX]. We measure the two word-wrapped QLabels with
        heightForWidth(_CONTENT_W) directly because QVBoxLayout.sizeHint()
        with WordWrap labels does not always return the wrapped height
        (the labels report their unwrapped sizeHint instead, which under-
        estimates total height by hundreds of pixels for long text)."""
        # Chrome height = top row (22) + spacings/separator + buttons row (26)
        # + main contentsMargins (12 top + 12 bottom) + layout spacing (6)*5.
        # Empirically ~94px; a small safety pad keeps a clean bottom edge.
        CHROME = 22 + 1 + 26 + 24 + 6 * 4 + 8
        orig_h = (
            self._orig_text.heightForWidth(_CONTENT_W)
            if self._orig_text.text() else 0
        )
        trans_h = (
            self._trans_text.heightForWidth(_CONTENT_W)
            if self._trans_text.text() else 0
        )
        # Loading state: reserve at least one line of trans text so the
        # popup doesn't visibly jump when the translation arrives.
        if self._is_loading and trans_h == 0:
            trans_h = self._trans_text.fontMetrics().height()
        new_h = max(_H_MIN, min(CHROME + orig_h + trans_h, _H_MAX))
        if new_h != self.height():
            self.resize(_W, new_h)

    def _position_near_cursor(self) -> None:
        """Place the popup ABOVE the current cursor, horizontally centered
        on cursor X. Falls back to below-cursor or screen-center if there's
        no room above. Clamps to the visible screen geometry."""
        cursor = QCursor.pos()
        # Resolve which screen the cursor is on (may differ from self.screen()
        # right after creation). QGuiApplication.screenAt is the right call.
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.screenAt(cursor) or self.screen()
        if screen is None:
            self._center()
            return
        geo = screen.availableGeometry()

        gap = 24  # px between cursor and popup edge
        # Center horizontally on cursor X, clamp into screen
        x = cursor.x() - _W // 2
        x = max(geo.x() + 12, min(x, geo.x() + geo.width() - _W - 12))

        # Try above cursor first
        h = self.height()
        y_above = cursor.y() - h - gap
        y_below = cursor.y() + gap

        if y_above >= geo.y() + 12:
            y = y_above
        elif y_below + h <= geo.y() + geo.height() - 12:
            y = y_below
        else:
            # No good space — fallback to center vertically
            y = geo.y() + (geo.height() - h) // 2

        self.move(x, y)

    def _on_lang_changed(self, idx: int) -> None:
        new_code = self._lang_combo.itemData(idx)
        if not new_code or new_code == self._tgt_lang:
            return
        self._tgt_lang = new_code
        self._is_loading = True
        self._translated_text = ""
        self._status_label.setText(t("review.translating"))
        self._status_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px;"
            " font-weight: 500; background: transparent; border: none;"
            " letter-spacing: 0.3px;"
        )
        self._trans_text.setText("")
        self._set_trans_loading_style()
        self._replace_btn.setEnabled(False)
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

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self._on_keep()
        elif ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_replace()
        else:
            super().keyPressEvent(ev)

    # ── window dragging ────────────────────────────────────────────────
    # Clicks on QPushButton / QComboBox children are consumed by those
    # widgets and never reach these handlers, so dragging only fires
    # when the user grabs the empty/background area.

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if (
            ev.buttons() & Qt.MouseButton.LeftButton
            and self._drag_pos is not None
        ):
            self.move(ev.globalPosition().toPoint() - self._drag_pos)
            ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag_pos is not None:
            self._drag_pos = None
            self.unsetCursor()
            ev.accept()
