"""Design system — colors, fonts, shared QSS, and reusable painted widgets."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt, Signal, QSize, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

# ── Color Tokens ─────────────────────────────────────────────────────────

BG_DEEPEST = "#0d0d0d"
BG_BASE = "#111111"
BG_SIDEBAR = "#0f0f0f"
BG_CARD = "#181818"
BG_CARD_HOVER = "#1e1e1e"
BG_ELEVATED = "#222222"
BG_INPUT = "#141414"

BORDER_SUBTLE = "#1f1f1f"
BORDER_DEFAULT = "#2a2a2a"
BORDER_STRONG = "#3a3a3a"

TEXT_PRIMARY = "#f0f0f0"
TEXT_SECONDARY = "#9a9a9a"
TEXT_MUTED = "#5a5a5a"

ACCENT_BLUE = "#3b82f6"
ACCENT_BLUE_HOVER = "#2563eb"
ACCENT_BLUE_DIM = "#1e3a5f"

SUCCESS = "#22c55e"
SUCCESS_DIM = "#14532d"
WARNING = "#f59e0b"
WARNING_DIM = "#78350f"
ERROR = "#ef4444"
ERROR_DIM = "#7f1d1d"

ACCENT_ORANGE = "#f97316"
ACCENT_PURPLE = "#a855f7"
ACCENT_CYAN = "#06b6d4"

# Pre-computed rgba() values for Qt stylesheet alpha colors
ACCENT_BLUE_A10 = "rgba(59, 130, 246, 25)"
ACCENT_BLUE_A20 = "rgba(59, 130, 246, 50)"
ACCENT_BLUE_A30 = "rgba(59, 130, 246, 76)"
SUCCESS_A20 = "rgba(34, 197, 94, 50)"
SUCCESS_A40 = "rgba(34, 197, 94, 100)"
ERROR_A40 = "rgba(239, 68, 68, 100)"

# ── Font Helpers ─────────────────────────────────────────────────────────

FONT_FAMILY = "SF Pro Display, SF Pro Text, Helvetica Neue, Segoe UI, sans-serif"
FONT_MONO = "SF Mono, JetBrains Mono, Menlo, Consolas, monospace"


def font(size: int = 13, bold: bool = False) -> QFont:
    f = QFont("Helvetica Neue", size)
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


def font_heading(size: int = 17) -> QFont:
    f = QFont("Helvetica Neue", size)
    f.setWeight(QFont.Weight.Bold)
    return f


# ── Global App Stylesheet ───────────────────────────────────────────────

APP_QSS = f"""
QMainWindow {{ background: {BG_BASE}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 6px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #333; min-height: 30px; border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{ background: #4a4a4a; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{ height: 0; }}

QToolTip {{
    background: {BG_ELEVATED}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_DEFAULT}; padding: 6px 10px;
    border-radius: 6px; font-size: 12px;
}}
"""

# ── Reusable Card Frame ─────────────────────────────────────────────────

CARD_QSS = (
    f"QFrame {{ background: {BG_CARD}; border: 1px solid {BORDER_SUBTLE};"
    " border-radius: 14px; }"
)

CARD_HOVER_QSS = (
    f"QFrame {{ background: {BG_CARD_HOVER}; border: 1px solid {BORDER_DEFAULT};"
    " border-radius: 14px; }}"
)


def make_card() -> QFrame:
    f = QFrame()
    f.setStyleSheet(CARD_QSS)
    return f


# ── Painted Sidebar Icons ───────────────────────────────────────────────

def _draw_icon_home(p: QPainter, r: QRect) -> None:
    p.setPen(QPen(QColor(TEXT_SECONDARY), 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = r.center().x(), r.center().y()
    s = 7
    pts = [
        (cx, cy - s), (cx + s, cy - 1), (cx + s, cy + s - 1),
        (cx - s, cy + s - 1), (cx - s, cy - 1),
    ]
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    poly = QPolygonF([QPointF(x, y) for x, y in pts])
    p.drawPolygon(poly)


def _draw_icon_models(p: QPainter, r: QRect) -> None:
    p.setPen(QPen(QColor(TEXT_SECONDARY), 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = r.center().x(), r.center().y()
    p.drawRoundedRect(QRectF(cx - 6, cy - 6, 12, 12), 2.5, 2.5)
    p.drawLine(cx - 3, cy - 2, cx + 3, cy - 2)
    p.drawLine(cx - 3, cy + 1, cx + 2, cy + 1)


def _draw_icon_settings(p: QPainter, r: QRect) -> None:
    p.setPen(QPen(QColor(TEXT_SECONDARY), 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = r.center().x(), r.center().y()
    p.drawEllipse(QRectF(cx - 4, cy - 4, 8, 8))
    for angle in range(0, 360, 45):
        import math
        rad = math.radians(angle)
        x1, y1 = cx + 6 * math.cos(rad), cy + 6 * math.sin(rad)
        x2, y2 = cx + 8 * math.cos(rad), cy + 8 * math.sin(rad)
        p.drawLine(int(x1), int(y1), int(x2), int(y2))


def _draw_icon_about(p: QPainter, r: QRect) -> None:
    p.setPen(QPen(QColor(TEXT_SECONDARY), 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = r.center().x(), r.center().y()
    p.drawEllipse(QRectF(cx - 7, cy - 7, 14, 14))
    f = QFont("Helvetica Neue", 9)
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(QRectF(cx - 7, cy - 7, 14, 14), Qt.AlignmentFlag.AlignCenter, "i")


ICON_PAINTERS = [_draw_icon_home, _draw_icon_models, _draw_icon_settings, _draw_icon_about]


def make_nav_icon(index: int, size: int = 22, active: bool = False) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if active:
        pen_color = QColor(ACCENT_BLUE)
    else:
        pen_color = QColor(TEXT_SECONDARY)
    p.setPen(QPen(pen_color, 1.6))
    ICON_PAINTERS[index](p, QRect(0, 0, size, size))
    p.end()
    return px


# ── Custom Toggle Switch (QPainter-based) ───────────────────────────────

class ToggleSwitch(QWidget):
    toggled_signal = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._knob_x = 22.0 if checked else 2.0

        self._anim = QPropertyAnimation(self, b"knob_x")
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool) -> None:
        self._checked = val
        self._knob_x = 22.0 if val else 2.0
        self.update()

    def _get_knob_x(self) -> float:
        return self._knob_x

    def _set_knob_x(self, val: float) -> None:
        self._knob_x = val
        self.update()

    knob_x = Property(float, _get_knob_x, _set_knob_x)

    def mousePressEvent(self, ev) -> None:
        self._checked = not self._checked
        self._anim.stop()
        self._anim.setStartValue(self._knob_x)
        self._anim.setEndValue(22.0 if self._checked else 2.0)
        self._anim.start()
        self.toggled_signal.emit(self._checked)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QRectF(0, 0, 44, 24)
        if self._checked:
            p.setBrush(QColor(ACCENT_BLUE))
        else:
            p.setBrush(QColor(BG_ELEVATED))
        p.setPen(QPen(QColor(BORDER_DEFAULT), 1))
        p.drawRoundedRect(track, 12, 12)

        knob = QRectF(self._knob_x, 2, 20, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(knob)

        p.end()


# ── Section heading helper ──────────────────────────────────────────────

def section_heading(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setFont(font(13, bold=True))
    lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; padding-top: 4px;")
    return lbl


# ── Setting row helper ──────────────────────────────────────────────────

def setting_row(label: str, description: str = "") -> tuple[QHBoxLayout, QLabel]:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    left = QVBoxLayout()
    left.setSpacing(1)
    left.setContentsMargins(0, 0, 0, 0)
    name = QLabel(label)
    name.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 13px; border: none;")
    left.addWidget(name)
    if description:
        desc = QLabel(description)
        desc.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; border: none;")
        desc.setWordWrap(True)
        left.addWidget(desc)
    row.addLayout(left, stretch=1)
    return row, name


# ── Pill Button ─────────────────────────────────────────────────────────

def pill_button(
    text: str,
    bg: str = BG_CARD,
    fg: str = TEXT_SECONDARY,
    bg_hover: str = BG_ELEVATED,
    fg_hover: str = TEXT_PRIMARY,
    border: str = BORDER_DEFAULT,
    width: int = 0,
    height: int = 32,
) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    if width:
        btn.setFixedWidth(width)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
        f" border-radius: {height // 2}px; padding: 0 16px; font-size: 12px; }}"
        f"QPushButton:hover {{ background: {bg_hover}; color: {fg_hover}; }}"
    )
    return btn


def accent_button(text: str, height: int = 40) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: {ACCENT_BLUE}; color: #fff; border: none;"
        f" border-radius: {height // 2}px; padding: 0 24px; font-size: 14px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: {ACCENT_BLUE_HOVER}; }}"
    )
    return btn


def danger_button(text: str, height: int = 40) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: {ERROR}; color: #fff; border: none;"
        f" border-radius: {height // 2}px; padding: 0 24px; font-size: 14px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: #dc2626; }}"
    )
    return btn


# ── Separator ───────────────────────────────────────────────────────────

def separator() -> QFrame:
    s = QFrame()
    s.setFixedHeight(1)
    s.setStyleSheet(f"background: {BORDER_SUBTLE};")
    return s


# ── Combo box style ─────────────────────────────────────────────────────

COMBO_QSS = (
    f"QComboBox {{ background: {BG_INPUT}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_DEFAULT};"
    f" border-radius: 10px; padding: 8px 14px; font-size: 13px; }}"
    f"QComboBox:hover {{ border: 1px solid {BORDER_STRONG}; }}"
    f"QComboBox::drop-down {{ border: none; width: 24px; }}"
    f"QComboBox QAbstractItemView {{ background: {BG_CARD}; color: {TEXT_PRIMARY};"
    f" selection-background-color: {BORDER_DEFAULT}; border: 1px solid {BORDER_DEFAULT};"
    f" border-radius: 8px; padding: 4px; }}"
)

# ── Line edit style ─────────────────────────────────────────────────────

INPUT_QSS = (
    f"QLineEdit {{ background: {BG_INPUT}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_DEFAULT};"
    f" border-radius: 10px; padding: 8px 14px; font-size: 13px; }}"
    f"QLineEdit:focus {{ border: 1px solid {ACCENT_BLUE}; }}"
)
