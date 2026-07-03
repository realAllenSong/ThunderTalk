"""Design system — colors, fonts, shared QSS, and reusable painted widgets.

Raycast / Linear inspired: cool near-black surfaces, rgba borders,
orange accent with animated hover glow, page fade-in transitions.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QRect, QRectF, Qt, Signal, QSize,
    QPropertyAnimation, QEasingCurve, Property, QTimer,
)
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ── Color Tokens ─────────────────────────────────────────────────────────
# Raycast/Linear dark palette — cool near-black base, surface depth via
# background tints, refined rgba borders, orange accent glow.

BG_DEEPEST    = "#04040a"   # reserve for deepest sunken surfaces
BG_BASE       = "#08080e"   # window background
BG_SIDEBAR    = "#0c0c14"   # nav sidebar — slightly lifted from base
BG_SURFACE    = "#08080e"   # main content area
BG_CARD       = "#111118"   # cards — filled surface, visible against base
BG_CARD_HOVER = "#161622"   # card hover lift
BG_ELEVATED   = "#1c1c28"   # ToggleSwitch track, dropdowns, pills
BG_INPUT      = "#0e0e16"   # inputs

# Borders — rgba so they respect dark surfaces naturally.
# NOTE: These are QSS strings. For QPainter use the _PAINT constants below.
BORDER_SUBTLE   = "rgba(255,255,255,0.06)"
BORDER_DEFAULT  = "rgba(255,255,255,0.10)"
BORDER_STRONG   = "rgba(255,255,255,0.18)"

# QPainter equivalents (QColor-compatible)
_BORDER_SUBTLE_C  = QColor(255, 255, 255, 15)
_BORDER_DEFAULT_C = QColor(255, 255, 255, 26)
_BORDER_STRONG_C  = QColor(255, 255, 255, 46)

# Text hierarchy
TEXT_PRIMARY   = "#f0f0f5"   # slightly cool white — crisper on dark bg
TEXT_SECONDARY = "#b0b0b8"
TEXT_MUTED     = "#72727c"
TEXT_SUBTLE    = "#4e4e58"

# Brand accent — orange
ACCENT_ORANGE        = "#f97316"
ACCENT_ORANGE_HOVER  = "#fb923c"
ACCENT_ORANGE_WARM   = "#fb923c"
ACCENT_ORANGE_DIM    = "rgba(249,115,22,0.12)"

# Glow tokens
GLOW_ACCENT   = "rgba(249,115,22,0.20)"
GLOW_ACCENT_S = "rgba(249,115,22,0.08)"

# Status colors
SUCCESS     = "#10b981"
SUCCESS_DIM = "#0d3328"
WARNING     = "#f59e0b"
WARNING_DIM = "#78350f"
ERROR       = "#ef4444"
ERROR_DIM   = "#3b1111"

# Secondary accents
ACCENT_BLUE         = "#5b8def"
ACCENT_BLUE_HOVER   = "#4a7de0"
ACCENT_BLUE_DIM     = "#1e3a5f"
ACCENT_PURPLE       = "#a78bfa"
ACCENT_CYAN         = "#22d3ee"

# Pre-computed rgba() values for Qt stylesheet alpha colors
ACCENT_BLUE_A10 = "rgba(91,141,239,25)"
ACCENT_BLUE_A20 = "rgba(91,141,239,50)"
ACCENT_BLUE_A30 = "rgba(91,141,239,76)"
SUCCESS_A20 = "rgba(52,211,153,50)"
SUCCESS_A40 = "rgba(52,211,153,100)"
ERROR_A40   = "rgba(248,113,113,100)"

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

QStackedWidget {{ background: {BG_BASE}; }}
QStackedWidget > QWidget {{ background: {BG_BASE}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget {{ background: {BG_BASE}; }}
QScrollArea > QWidget > QWidget {{ background: {BG_BASE}; }}
QScrollBar:vertical {{
    background: transparent; width: 6px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.12); min-height: 30px; border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.22); }}
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
    f"QFrame {{ background: {BG_CARD}; border: 1px solid {BORDER_DEFAULT};"
    " border-radius: 12px; }"
    f"QFrame:hover {{ background: {BG_CARD_HOVER}; border: 1px solid {BORDER_STRONG}; }}"
)


def auto_shadow():
    """No-op kept so existing call sites don't need to change."""
    return None


def make_card() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.NoFrame)
    f.setStyleSheet(CARD_QSS)
    return f


# ── Glow Card ───────────────────────────────────────────────────────────

class GlowCard(QFrame):
    """Card with animated Raycast-style orange hover glow.

    Drop-in for QFrame + CARD_QSS where hover glow is desired.
    The QGraphicsDropShadowEffect animates blurRadius 0 → 24 on hover.
    """

    def __init__(self, glow_alpha: int = 55, radius: int = 12,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background: {BG_CARD}; border: 1px solid {BORDER_DEFAULT};"
            f" border-radius: {radius}px; }}"
            f"QFrame:hover {{ background: {BG_CARD_HOVER}; border: 1px solid {BORDER_STRONG}; }}"
        )
        self._glow = QGraphicsDropShadowEffect()
        self._glow.setBlurRadius(0)
        self._glow.setColor(QColor(249, 115, 22, glow_alpha))
        self._glow.setOffset(0, 0)
        self.setGraphicsEffect(self._glow)

        self._glow_anim = QPropertyAnimation(self._glow, b"blurRadius")
        self._glow_anim.setDuration(220)
        self._glow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def enterEvent(self, e) -> None:
        self._glow_anim.stop()
        self._glow_anim.setStartValue(int(self._glow.blurRadius()))
        self._glow_anim.setEndValue(24)
        self._glow_anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._glow_anim.stop()
        self._glow_anim.setStartValue(int(self._glow.blurRadius()))
        self._glow_anim.setEndValue(0)
        self._glow_anim.start()
        super().leaveEvent(e)


# ── Page Fade-in ────────────────────────────────────────────────────────

def fade_in(widget: QWidget, duration: int = 220) -> None:
    # Disable (not detach) child graphics effects for the animation window.
    # Detaching via setGraphicsEffect(None) causes Qt C++ to delete the effect
    # object immediately, leaving a dangling Python wrapper → crash on restore.
    # setEnabled(False) keeps ownership intact and prevents the nested-effect
    # flicker on macOS without touching C++ lifetime.
    child_effects = []
    for child in widget.findChildren(QWidget):
        eff = child.graphicsEffect()
        if eff is not None:
            child_effects.append(eff)
            eff.setEnabled(False)

    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    effect.setOpacity(0.0)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)

    def _done(w=widget, effects=child_effects):
        w.setGraphicsEffect(None)
        for eff in effects:
            eff.setEnabled(True)

    anim.finished.connect(_done)
    anim.start()


# ── Painted Sidebar Icons ───────────────────────────────────────────────

def _draw_icon_home(p: QPainter, r: QRect) -> None:
    cx, cy = r.center().x(), r.center().y()
    path = QPainterPath()
    path.moveTo(cx, cy - 6)
    path.lineTo(cx + 7, cy)
    path.lineTo(cx + 7, cy + 7)
    path.lineTo(cx + 2, cy + 7)
    path.lineTo(cx + 2, cy + 3)
    path.lineTo(cx - 2, cy + 3)
    path.lineTo(cx - 2, cy + 7)
    path.lineTo(cx - 7, cy + 7)
    path.lineTo(cx - 7, cy)
    path.closeSubpath()
    p.drawPath(path)


def _draw_icon_models(p: QPainter, r: QRect) -> None:
    cx, cy = r.center().x(), r.center().y()
    p.drawRoundedRect(QRectF(cx - 6, cy - 6, 12, 12), 2, 2)
    p.drawPoint(int(cx - 2), int(cy - 2))
    p.drawPoint(int(cx + 2), int(cy - 2))
    p.drawPoint(int(cx - 2), int(cy + 2))
    p.drawPoint(int(cx + 2), int(cy + 2))


def _draw_icon_settings(p: QPainter, r: QRect) -> None:
    cx, cy = r.center().x(), r.center().y()
    p.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))
    import math
    for angle in range(0, 360, 45):
        rad = math.radians(angle)
        x1, y1 = cx + 5 * math.cos(rad), cy + 5 * math.sin(rad)
        x2, y2 = cx + 7.5 * math.cos(rad), cy + 7.5 * math.sin(rad)
        p.drawLine(int(x1), int(y1), int(x2), int(y2))


def _draw_icon_hotwords(p: QPainter, r: QRect) -> None:
    cx, cy = r.center().x(), r.center().y()
    import math
    path = QPainterPath()
    outer, inner = 7.5, 3.5
    for i in range(10):
        angle = math.radians(-90 + i * 36)
        rad = outer if i % 2 == 0 else inner
        x = cx + rad * math.cos(angle)
        y = cy + rad * math.sin(angle)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)


def _draw_icon_about(p: QPainter, r: QRect) -> None:
    cx, cy = r.center().x(), r.center().y()
    p.drawEllipse(QRectF(cx - 7, cy - 7, 14, 14))
    f = QFont("Helvetica Neue", 10)
    p.setFont(f)
    p.drawText(QRectF(cx - 7, cy - 7, 14, 14), Qt.AlignmentFlag.AlignCenter, "i")


def _draw_icon_lab(p: QPainter, r: QRect) -> None:
    cx, cy = r.center().x(), r.center().y()
    # Erlenmeyer flask: narrow neck + wide base
    path = QPainterPath()
    path.moveTo(cx - 2.5, cy - 7)
    path.lineTo(cx + 2.5, cy - 7)
    path.lineTo(cx + 2.5, cy - 2)
    path.lineTo(cx + 7.5, cy + 6.5)
    path.lineTo(cx - 7.5, cy + 6.5)
    path.lineTo(cx - 2.5, cy - 2)
    path.closeSubpath()
    p.drawPath(path)
    # Liquid line inside the flask body
    p.drawLine(int(cx - 4), int(cy + 2), int(cx + 4), int(cy + 2))


def draw_boltPath(p: QPainter, rect: QRectF, color: str = "#ffffff") -> None:
    path = QPainterPath()
    cx, cy = rect.center().x(), rect.center().y()
    w, h = min(rect.width(), 20), min(rect.height(), 24)
    path.moveTo(cx + w*0.15, cy - h*0.45)
    path.lineTo(cx - w*0.35, cy + h*0.05)
    path.lineTo(cx + w*0.15, cy + h*0.05)
    path.lineTo(cx - w*0.15, cy + h*0.45)
    path.lineTo(cx + w*0.35, cy - h*0.15)
    path.lineTo(cx - w*0.15, cy - h*0.15)
    path.closeSubpath()
    old_pen = p.pen()
    old_brush = p.brush()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    p.setPen(old_pen)
    p.setBrush(old_brush)


ICON_PAINTERS = [_draw_icon_home, _draw_icon_models, _draw_icon_hotwords, _draw_icon_settings, _draw_icon_lab, _draw_icon_about]


# ── Custom Toggle Switch ────────────────────────────────────────────────

class ToggleSwitch(QWidget):
    toggled_signal = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._knob_x = 22.0 if checked else 2.0

        self._anim = QPropertyAnimation(self, b"knob_x")
        self._anim.setDuration(180)
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
            # ON: warm orange track — brand color, clearly active
            p.setBrush(QColor(249, 115, 22, 90))
            p.setPen(QPen(QColor(249, 115, 22, 50), 1))
        else:
            # OFF: dark elevated surface
            p.setBrush(QColor(BG_ELEVATED))
            p.setPen(QPen(_BORDER_SUBTLE_C, 1))
        p.drawRoundedRect(track, 12, 12)

        # Knob
        knob = QRectF(self._knob_x, 2, 20, 20)
        p.setPen(Qt.PenStyle.NoPen)
        if self._checked:
            p.setBrush(QColor("#f0f0f2"))       # bright white knob on orange
        else:
            p.setBrush(_BORDER_DEFAULT_C)       # muted gray knob off
        p.drawEllipse(knob)
        p.end()


# ── Section heading helper ──────────────────────────────────────────────

def section_heading(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setFont(font(14, bold=True))
    lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; padding-top: 4px;")
    return lbl


# ── Setting row helper ──────────────────────────────────────────────────

def setting_row(label: str, description: str = "") -> tuple[QHBoxLayout, QLabel]:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    left = QVBoxLayout()
    left.setSpacing(4)
    left.setContentsMargins(0, 0, 0, 0)
    name = QLabel(label)
    name.setFont(font(13, bold=True))
    name.setStyleSheet(f"color: {TEXT_PRIMARY}; border: none;")
    left.addWidget(name)
    if description:
        desc = QLabel(description)
        desc.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; border: none;")
        desc.setWordWrap(True)
        left.addWidget(desc)
    row.addLayout(left, stretch=1)
    return row, name


# ── Pill Button ─────────────────────────────────────────────────────────

def pill_button(
    text: str,
    bg: str = "transparent",
    fg: str = TEXT_SECONDARY,
    bg_hover: str = "rgba(255,255,255,0.06)",
    fg_hover: str = TEXT_PRIMARY,
    border: str = BORDER_DEFAULT,
    width: int = 0,
    height: int = 34,
) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    if width:
        btn.setFixedWidth(width)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
        f" border-radius: {height // 2}px; padding: 0 18px; font-size: 12px; }}"
        f"QPushButton:hover {{ background: {bg_hover}; color: {fg_hover};"
        f" border: 1px solid {BORDER_STRONG}; }}"
    )
    return btn


def accent_button(text: str, height: int = 40) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        f" stop:0 {ACCENT_BLUE}, stop:1 {ACCENT_BLUE_HOVER});"
        f" color: #fff; border: none;"
        f" border-radius: {height // 2}px; padding: 0 24px; font-size: 13px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        f" stop:0 #6b9ef0, stop:1 {ACCENT_BLUE}); }}"
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
    f"QComboBox {{ background: {BG_ELEVATED}; color: {TEXT_PRIMARY};"
    f" border: 1px solid {BORDER_DEFAULT};"
    f" border-radius: 10px; padding: 10px 16px; font-size: 13px; }}"
    f"QComboBox:hover {{ border: 1px solid {BORDER_STRONG}; }}"
    f"QComboBox::drop-down {{ border: none; width: 28px; }}"
    f"QComboBox QAbstractItemView {{ background: {BG_ELEVATED}; color: {TEXT_PRIMARY};"
    f" border: 1px solid {BORDER_DEFAULT}; border-radius: 8px;"
    f" padding: 4px; outline: 0; }}"
    f"QComboBox QAbstractItemView::item {{ background: {BG_ELEVATED}; color: {TEXT_PRIMARY};"
    f" padding: 6px 12px; min-height: 22px; border: none; }}"
    f"QComboBox QAbstractItemView::item:selected {{"
    f" background: {ACCENT_BLUE}; color: #ffffff; }}"
    f"QComboBox QAbstractItemView::item:hover {{"
    f" background: rgba(91,141,239,0.18); color: {TEXT_PRIMARY}; }}"
)


def style_combo(combo) -> None:
    """Apply COMBO_QSS and force the popup window background."""
    combo.setStyleSheet(COMBO_QSS)
    view = combo.view()
    if view is None:
        return
    view.setStyleSheet(
        f"QListView, QAbstractItemView {{"
        f" background: {BG_ELEVATED}; border: none; outline: 0; }}"
    )
    win = view.window()
    if win is not None and win is not view:
        win.setStyleSheet(
            f"background: {BG_ELEVATED}; border: 1px solid {BORDER_DEFAULT};"
            " border-radius: 8px;"
        )


# ── Line edit style ─────────────────────────────────────────────────────

INPUT_QSS = (
    f"QLineEdit {{ background: {BG_ELEVATED}; color: {TEXT_PRIMARY};"
    f" border: 1px solid {BORDER_DEFAULT};"
    f" border-radius: 10px; padding: 10px 16px; font-size: 13px; }}"
    f"QLineEdit:hover {{ border: 1px solid {BORDER_STRONG}; }}"
    f"QLineEdit:focus {{ border: 1px solid {ACCENT_ORANGE}; }}"
)


# ── Segment tab bar (pill-style) ────────────────────────────────────────

def segment_tab_qss() -> str:
    return (
        f"QTabBar {{ background: transparent; }}"
        f"QTabBar::tab {{ background: transparent; color: {TEXT_SECONDARY};"
        f" padding: 8px 24px; border: 1px solid transparent;"
        f" border-radius: 8px; margin: 0 2px; font-size: 13px; }}"
        f"QTabBar::tab:selected {{ background: {BG_ELEVATED}; color: {TEXT_PRIMARY};"
        f" border: 1px solid {BORDER_DEFAULT}; font-weight: bold; }}"
        f"QTabBar::tab:hover {{ color: {TEXT_PRIMARY};"
        f" background: rgba(255,255,255,0.04); }}"
    )
