"""Reusable UI components for the "Paper & Ink" design system (theme.py).

Flat, self-painted, and quiet. Motion is limited to things that carry
information: the live level meter, a working spinner, an indeterminate
progress sweep, and the toggle's short slide. Nothing pulses, glows or fades
for decoration.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Optional

from PySide6.QtCore import (
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from thundertalk.ui import theme
from thundertalk.ui.icons import paint_icon
from thundertalk.ui.keys import display_key, split_combo


# ── Canvas ──────────────────────────────────────────────────────────────

def paint_canvas(p: QPainter, rect: QRectF) -> None:
    """The window backdrop: plain paper. (This used to paint a two-glow aurora.)"""
    p.fillRect(rect, QColor(theme.BG_BASE))


paint_aurora = paint_canvas   # legacy name


def column_scroll(margin_x: int = theme.PAGE_MARGIN_X, top: int = theme.PAGE_MARGIN_TOP,
                  bottom: int = 44, spacing: int = 16,
                  max_width: int = theme.PAGE_MAX_WIDTH) -> tuple[QScrollArea, QVBoxLayout]:
    """A scroll area whose content is a single centred column of bounded width
    (long lines are hard to read; wide windows just get more margin).

    Returns (scroll_area, column_layout) — add widgets to the layout."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    holder = QWidget()
    holder.setStyleSheet("background: transparent;")
    hl = QHBoxLayout(holder)
    hl.setContentsMargins(margin_x, 0, margin_x, 0)
    hl.setSpacing(0)
    column = QWidget()
    column.setStyleSheet("background: transparent;")
    column.setMaximumWidth(max_width)
    column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    lay = QVBoxLayout(column)
    lay.setContentsMargins(0, top, 0, bottom)
    lay.setSpacing(spacing)
    hl.addStretch(1)
    hl.addWidget(column, 1000)
    hl.addStretch(1)
    scroll.setWidget(holder)
    return scroll, lay


class Rule(QWidget):
    """A 1px hairline."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(1)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme._BORDER_SUBTLE_C)
        p.end()


# ── Page header ─────────────────────────────────────────────────────────

class PageHeader(QWidget):
    """Serif title, muted subtitle, hairline underneath. Optional tag beside
    the title and optional widget pinned right."""

    def __init__(self, title: str, subtitle: str = "",
                 right: Optional[QWidget] = None, badge: Optional[QWidget] = None) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel(title)
        self._title.setFont(theme.font_display(30))
        self._title.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        self._title.setContentsMargins(0, 0, 0, 5)
        row.addWidget(self._title)
        if badge is not None:
            row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        if right is not None:
            row.addWidget(right, alignment=Qt.AlignmentFlag.AlignBottom)
        col.addLayout(row)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 14px;")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        col.addWidget(self._subtitle)
        col.addSpacing(10)
        col.addWidget(Rule())

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))


# ── Toast ───────────────────────────────────────────────────────────────

_TOAST_KINDS = {
    "info":    ("info", "#D9D6CC"),
    "success": ("check", "#9AD7B0"),
    "warn":    ("alert", "#F1D48A"),
    "error":   ("alert", "#F4A9A4"),
    "copy":    ("check", "#9AD7B0"),
}


class Toast(QWidget):
    """A small solid-ink note at the bottom-centre of the window. One at a
    time; it appears and disappears without animation."""

    _H = 40

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text = ""
        self._kind = "info"
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()
        parent.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.parent() and ev.type() == ev.Type.Resize and self.isVisible():
            self.move(self._anchor())
        return False

    def _anchor(self) -> QPoint:
        par = self.parentWidget()
        return QPoint((par.width() - self.width()) // 2,
                      par.height() - self.height() - 28)

    def show_message(self, text: str, kind: str = "info", ms: int = 2600) -> None:
        self._text = text
        self._kind = kind if kind in _TOAST_KINDS else "info"
        fm = QFontMetrics(theme.font(13))
        text_w = min(fm.horizontalAdvance(text), 520)
        self.resize(14 + 16 + 10 + text_w + 18, self._H)
        self.move(self._anchor())
        self.show()
        self.raise_()
        self.update()
        self._hide_timer.start(ms)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.fillPath(path, QColor(theme.INK))
        icon, color = _TOAST_KINDS[self._kind]
        paint_icon(p, icon, QRectF(14, (self.height() - 16) / 2, 16, 16), color, 2.2)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(theme.font(13))
        fm = p.fontMetrics()
        p.drawText(QRectF(40, 0, self.width() - 40 - 14, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self._text, Qt.TextElideMode.ElideRight, self.width() - 56))
        p.end()


# ── Key caps ────────────────────────────────────────────────────────────

class KeyCaps(QWidget):
    """A hotkey as physical keys — monospace, hairline, faint bottom edge."""

    def __init__(self, combo: str = "", height: int = 30, accent: bool = False) -> None:
        super().__init__()
        self._height = height
        self._accent = accent
        self._combo = combo
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._relayout()

    def set_combo(self, combo: str) -> None:
        self._combo = combo
        self._relayout()

    def _labels(self) -> list[str]:
        return [display_key(p) for p in split_combo(self._combo)] or ["—"]

    def _font(self):
        return theme.font_mono(max(9, int(self._height * 0.36)), bold=True)

    def _cap_widths(self) -> list[int]:
        fm = QFontMetrics(self._font())
        return [max(int(self._height * 1.05), fm.horizontalAdvance(t) + int(self._height * 0.62))
                for t in self._labels()]

    def _relayout(self) -> None:
        widths = self._cap_widths()
        gap = 5
        self.setFixedSize(sum(widths) + gap * (len(widths) - 1) + 2, self._height + 3)
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font())
        x = 1.0
        h = float(self._height)
        for label, w in zip(self._labels(), self._cap_widths()):
            body = QRectF(x, 0.5, w, h)
            # bottom edge: the key's thickness
            edge = QPainterPath()
            edge.addRoundedRect(QRectF(x, 2.0, w, h), 4, 4)
            p.fillPath(edge, QColor(31, 30, 27, 46))
            face = QPainterPath()
            face.addRoundedRect(body, 4, 4)
            p.fillPath(face, QColor("#FFFFFF" if self._accent else "#F7F6F3"))
            p.setPen(QPen(theme._BORDER_STRONG_C if self._accent else theme._BORDER_DEFAULT_C, 1))
            p.drawPath(face)
            p.setPen(QColor(theme.TEXT_PRIMARY))
            p.drawText(body, Qt.AlignmentFlag.AlignCenter, label)
            x += w + 5
        p.end()


# ── Status dot / spinner ────────────────────────────────────────────────

class StatusDot(QWidget):
    """A flat coloured dot. (``pulse`` is accepted for old call sites and
    ignored — nothing breathes.)"""

    def __init__(self, color: str = theme.TEXT_MUTED, size: int = 8, pulse: bool = False) -> None:
        super().__init__()
        self._color = QColor(color)
        self._size = size
        self.setFixedSize(size + 4, size + 4)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_state(self, color: str, pulse: bool = False) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        r = self._size / 2
        p.drawEllipse(QRectF(self.width() / 2 - r, self.height() / 2 - r, self._size, self._size))
        p.end()


class Spinner(QWidget):
    """A thin rotating arc — shown only while something is genuinely working."""

    def __init__(self, size: int = 16, color: str = theme.INK, stroke: float = 2.0) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._color = QColor(color)
        self._stroke = stroke
        self._angle = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._timer.start(24)

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._timer.stop()

    def _tick(self) -> None:
        self._angle = (self._angle - 9) % 360
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self._stroke
        r = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        track = QColor(self._color)
        track.setAlpha(36)
        p.setPen(QPen(track, self._stroke))
        p.drawEllipse(r)
        p.setPen(QPen(self._color, self._stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(r, self._angle * 16, 90 * 16)
        p.end()


# ── Level meter ─────────────────────────────────────────────────────────

class Waveform(QWidget):
    """Live input level as thin vertical bars, newest on the right. Idle shows
    a flat dotted baseline — it does not animate until there is real audio."""

    def __init__(self, bars: int = 44, height: int = 40, color: str = theme.INK) -> None:
        super().__init__()
        self._n = bars
        self._levels: deque[float] = deque([0.0] * bars, maxlen=bars)
        self._color = QColor(color)
        self._idle = True
        self.setMinimumHeight(height)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_idle(self, idle: bool) -> None:
        if idle != self._idle:
            self._idle = idle
            if idle:
                self._levels = deque([0.0] * self._n, maxlen=self._n)
            self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def push(self, level: float) -> None:
        self._levels.append(max(0.0, min(1.0, level)))
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = self._n
        step = w / n
        bw = max(2.0, step * 0.42)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            v = 0.0 if self._idle else self._levels[i]
            bh = max(2.0, v * (h - 4))
            c = QColor(self._color)
            c.setAlpha(60 if v < 0.02 else int(110 + 145 * v))
            p.setBrush(c)
            x = i * step + (step - bw) / 2
            p.drawRoundedRect(QRectF(x, (h - bh) / 2, bw, bh), bw / 2, bw / 2)
        p.end()


# ── Number label (no count-up: figures simply change) ───────────────────

class CountUpLabel(QLabel):
    """Label for a figure. Name kept for callers; it no longer animates."""

    def __init__(self, text: str = "0") -> None:
        super().__init__(text)
        self._fmt: Callable[[float], str] = lambda v: f"{int(round(v)):,}"

    def animate_to(self, target: float, fmt: Optional[Callable[[float], str]] = None) -> None:
        if fmt is not None:
            self._fmt = fmt
        self.setText(self._fmt(target))


# ── Segmented control ───────────────────────────────────────────────────

class SegmentedControl(QWidget):
    """Outlined segmented switch; the selected segment is solid ink."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str = "") -> None:
        super().__init__()
        self._options = list(options)
        self._current = current or (options[0][0] if options else "")
        self._hover = -1
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def _text_font(self):
        return theme.font(12, bold=True)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._text_font())
        return QSize(sum(fm.horizontalAdvance(lbl) + 36 for _, lbl in self._options) + 6, 34)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def set_options(self, options: list[tuple[str, str]]) -> None:
        self._options = list(options)
        self.updateGeometry()
        self.update()

    def current(self) -> str:
        return self._current

    def set_current(self, key: str, animate: bool = False) -> None:
        if key != self._current:
            self._current = key
            self.update()

    def _seg_rects(self) -> list[QRectF]:
        fm = QFontMetrics(self._text_font())
        widths = [fm.horizontalAdvance(lbl) + 36 for _, lbl in self._options]
        total = sum(widths) or 1
        scale = (self.width() - 6) / total
        rects, x = [], 3.0
        for w in widths:
            rects.append(QRectF(x, 3, w * scale, self.height() - 6))
            x += w * scale
        return rects

    def mouseMoveEvent(self, ev) -> None:
        idx = -1
        for i, r in enumerate(self._seg_rects()):
            if r.contains(ev.position()):
                idx = i
        if idx != self._hover:
            self._hover = idx
            self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = -1
        self.update()

    def mousePressEvent(self, ev) -> None:
        for (key, _), r in zip(self._options, self._seg_rects()):
            if r.contains(ev.position()) and key != self._current:
                self._current = key
                self.changed.emit(key)
                self.update()
                return

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(theme.BG_CARD))
        p.setPen(QPen(theme._BORDER_DEFAULT_C, 1))
        p.drawRoundedRect(outer, 6, 6)
        p.setFont(self._text_font())
        for i, ((key, label), r) in enumerate(zip(self._options, self._seg_rects())):
            if key == self._current:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.INK))
                p.drawRoundedRect(r, 4, 4)
                p.setPen(QColor("#FFFFFF"))
            else:
                if i == self._hover:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(theme._HOVER_FILL_C)
                    p.drawRoundedRect(r, 4, 4)
                p.setPen(QColor(theme.TEXT_PRIMARY if i == self._hover else theme.TEXT_SECONDARY))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
        p.end()


# ── Search field ────────────────────────────────────────────────────────

class SearchField(QLineEdit):
    """Plain search input with a magnifier and a clear (×) button."""

    def __init__(self, placeholder: str = "") -> None:
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setFixedHeight(36)
        self.setTextMargins(22, 0, 20, 0)
        self.setStyleSheet(
            f"QLineEdit {{ background: {theme.BG_CARD}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 6px;"
            f" padding: 0 12px; font-size: 13px;"
            f" selection-background-color: #F5D0B6; selection-color: {theme.TEXT_PRIMARY}; }}"
            f"QLineEdit:hover {{ border: 1px solid {theme.BORDER_STRONG}; }}"
            f"QLineEdit:focus {{ border: 1px solid {theme.INK}; }}"
        )
        self._clear_hover = False
        self.setMouseTracking(True)

    def _clear_rect(self) -> QRectF:
        return QRectF(self.width() - 30, (self.height() - 16) / 2, 16, 16)

    def mouseMoveEvent(self, ev) -> None:
        over = bool(self.text()) and self._clear_rect().contains(ev.position())
        if over != self._clear_hover:
            self._clear_hover = over
            self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.IBeamCursor)
            self.update()
        super().mouseMoveEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if self.text() and self._clear_rect().contains(ev.position()):
            self.clear()
            return
        super().mousePressEvent(ev)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        paint_icon(p, "search", QRectF(12, (self.height() - 14) / 2, 14, 14),
                   theme.TEXT_PRIMARY if self.hasFocus() else theme.TEXT_MUTED, 2.2)
        if self.text():
            r = self._clear_rect()
            if self._clear_hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme._PRESS_FILL_C)
                p.drawEllipse(r)
            paint_icon(p, "x", r.adjusted(3.5, 3.5, -3.5, -3.5), theme.TEXT_SECONDARY, 2.4)
        p.end()


# ── Icon button ─────────────────────────────────────────────────────────

class IconButton(QPushButton):
    """Flat button drawing a vector icon; shades on hover."""

    def __init__(self, icon: str, size: int = 28, tooltip: str = "",
                 color: str = theme.TEXT_MUTED, hover_color: str = theme.TEXT_PRIMARY,
                 danger: bool = False) -> None:
        super().__init__()
        self._icon = icon
        self._color = color
        self._hover_color = theme.ERROR if danger else hover_color
        self._danger = danger
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFlat(True)
        if tooltip:
            self.setToolTip(tooltip)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def set_icon(self, icon: str, color: Optional[str] = None) -> None:
        self._icon = icon
        if color:
            self._color = color
        self.update()

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(180, 35, 24, 22) if self._danger else theme._HOVER_FILL_C)
            p.drawRoundedRect(QRectF(self.rect()), 5, 5)
        inset = self.width() * 0.27
        paint_icon(p, self._icon,
                   QRectF(self.rect()).adjusted(inset, inset, -inset, -inset),
                   self._hover_color if self._hover else self._color, 2.0)
        p.end()


# ── Thin progress bar ───────────────────────────────────────────────────

class ThinProgress(QWidget):
    """Flat progress bar: ink fill on a warm track. Indeterminate mode sweeps
    a segment — it is the "still working" signal, nothing more."""

    def __init__(self, height: int = 4, color: str = theme.INK) -> None:
        super().__init__()
        self.setFixedHeight(height)
        self._value = 0.0
        self._indeterminate = False
        self._phase = 0.0
        self._color = QColor(color)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_value(self, pct: float) -> None:
        self._indeterminate = False
        self._timer.stop()
        self._value = float(max(0.0, min(100.0, float(pct))))
        self.update()

    def set_indeterminate(self, on: bool = True) -> None:
        self._indeterminate = on
        if on and self.isVisible():
            self._timer.start(20)
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if self._indeterminate:
            self._timer.start(20)

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        rad = min(2.0, r.height() / 2)
        track = QPainterPath()
        track.addRoundedRect(r, rad, rad)
        p.fillPath(track, QColor(theme.BG_ELEVATED))
        p.save()
        p.setClipPath(track)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        if self._indeterminate:
            w = r.width() * 0.3
            x = -w + (r.width() + w) * self._phase
            p.drawRect(QRectF(x, 0, w, r.height()))
        else:
            p.drawRect(QRectF(0, 0, r.width() * self._value / 100.0, r.height()))
        p.restore()
        p.end()


# ── Form card (rows that survive a live language switch) ────────────────

class FormCard(theme.Card):
    """Card with an optional serif title and label/description/control rows.

    Rows remember their i18n keys, so ``retranslate()`` updates every string —
    the old settings page only re-labelled a handful of widgets on a language
    switch and left the rest stale until restart."""

    def __init__(self, title_key: str | None = None, badge: Optional[QWidget] = None) -> None:
        super().__init__(radius=theme.RADIUS_CARD)
        from thundertalk.core.i18n import t as _t
        self._t = _t
        self._ly = QVBoxLayout(self)
        self._ly.setContentsMargins(24, 20, 24, 22)
        self._ly.setSpacing(16)
        self._title_key = title_key
        self._title: Optional[QLabel] = None
        self._rows: list[tuple[QLabel, Optional[QLabel], str, str]] = []
        if title_key:
            head = QHBoxLayout()
            head.setSpacing(10)
            self._title = QLabel(_t(title_key))
            self._title.setFont(theme.font_serif(16))
            self._title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
            head.addWidget(self._title)
            if badge is not None:
                head.addWidget(badge)
            head.addStretch()
            self._ly.addLayout(head)

    def add_row(self, key_name: str, key_desc: str = "", control: Optional[QWidget] = None,
                sep: bool = False) -> QHBoxLayout:
        if sep:
            self._ly.addWidget(theme.separator())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(3)
        left.setContentsMargins(0, 0, 0, 0)
        name = QLabel(self._t(key_name))
        name.setFont(theme.font(13, bold=True))
        name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        left.addWidget(name)
        desc: Optional[QLabel] = None
        if key_desc:
            desc = QLabel(self._t(key_desc))
            desc.setWordWrap(True)
            desc.setMinimumWidth(0)
            desc.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
            left.addWidget(desc)
        row.addLayout(left, stretch=1)
        if control is not None:
            row.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._ly.addLayout(row)
        self._rows.append((name, desc, key_name, key_desc))
        return row

    def add_widget(self, w: QWidget) -> None:
        self._ly.addWidget(w)

    def add_layout(self, lay) -> None:
        self._ly.addLayout(lay)

    def retranslate(self) -> None:
        if self._title is not None and self._title_key:
            self._title.setText(self._t(self._title_key))
        for name, desc, kn, kd in self._rows:
            name.setText(self._t(kn))
            if desc is not None and kd:
                desc.setText(self._t(kd))


class SectionLabel(QWidget):
    """Small-caps caption with a hairline running to the right edge."""

    def __init__(self, icon: str, key: str) -> None:
        super().__init__()
        from thundertalk.core.i18n import t as _t
        self._t, self._key = _t, key        # `icon` accepted for old call sites; unused
        self.setFixedHeight(28)

    def retranslate(self) -> None:
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        f = theme.font(11, bold=True)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.3)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_MUTED))
        text = self._t(self._key).upper()
        tw = QFontMetrics(f).horizontalAdvance(text)
        p.drawText(QRectF(0, 0, tw + 4, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        y = self.height() // 2
        p.setPen(QPen(theme._BORDER_SUBTLE_C, 1))
        p.drawLine(tw + 16, y, self.width(), y)
        p.end()


# ── Flow layout ─────────────────────────────────────────────────────────

class FlowLayout(QLayout):
    """Left-to-right layout that wraps to new rows (height-for-width aware)."""

    def __init__(self, parent: Optional[QWidget] = None, hspacing: int = 8, vspacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = eff.x(), eff.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            nx = x + hint.width() + self._h
            if nx - self._h > eff.right() + 1 and line_h > 0:
                x = eff.x()
                y += line_h + self._v
                nx = x + hint.width() + self._h
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


# ── Brand mark ──────────────────────────────────────────────────────────

class BrandMark(QWidget):
    """Flat brand tile: a paper-coloured bolt on solid ink."""

    def __init__(self, size: int = 40) -> None:
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, self._size, self._size)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.INK))
        p.drawRoundedRect(r, self._size * 0.22, self._size * 0.22)
        inset = self._size * 0.24
        paint_icon(p, "bolt", r.adjusted(inset, inset, -inset, -inset), "#FBFBFA", 2.2)
        p.end()


class GlowLogo(BrandMark):
    """Old name (it used to breathe a halo). Now just the flat mark."""

    def __init__(self, size: int = 40) -> None:
        super().__init__(min(size, 64))
