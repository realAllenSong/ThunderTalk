"""Design system — colors, fonts, shared QSS, and reusable painted widgets.

Inspired by macOS-native dark UI conventions with warm tonal depth.
Reference: ShandianShuo UI (闪电说).
"""

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
# Dark palette inspired by Linear / Raycast / macOS-native — strong layer
# differentiation through background tints rather than visible borders.

# Translucent surfaces designed to sit on top of NSVisualEffectView
# (real macOS blur). The window itself is "transparent" — Qt paints
# nothing — and the cards use rgba so the blur shows through.
#
# Qt's QSS parser accepts rgba() with alpha as 0-1.0 float (the CSS
# form). The integer-alpha form fails silently with "Could not parse
# stylesheet" warnings, so always use the float form here.
BG_DEEPEST    = "transparent"
BG_BASE       = "transparent"                # window — NSVisualEffectView paints
BG_SIDEBAR    = "transparent"                # sidebar — same
BG_SURFACE    = "transparent"                # main content area
BG_CARD       = "rgba(20, 20, 22, 0.55)"     # smoked-glass card
BG_CARD_HOVER = "rgba(28, 28, 30, 0.65)"     # slight darken on hover
BG_ELEVATED   = "rgba(34, 34, 38, 0.78)"     # painted interactive surfaces
                                              # (ToggleSwitch track, HotkeyCapture)
BG_INPUT      = "rgba(0, 0, 0, 0.35)"        # gentle wash for inputs

# Borders — translucent white to act as the glass-rim highlight that
# macOS HUD / Control Center cards have. On a frosted bg, an opaque
# dark border looks dirty; a faint white edge reads as glass.
BORDER_SUBTLE  = "rgba(255, 255, 255, 0.07)"  # grouping only
BORDER_DEFAULT = "rgba(255, 255, 255, 0.13)"  # default card edge
BORDER_STRONG  = "rgba(255, 255, 255, 0.25)"  # focus / active

# Text hierarchy — bumped to pure white for primary so contrast stays
# readable on a translucent backdrop. Body / muted lift in tandem.
TEXT_PRIMARY   = "#ffffff"   # high-contrast headings, primary content
TEXT_SECONDARY = "#d4d4d8"   # body text
TEXT_MUTED     = "#9c9ca5"   # captions, metadata
TEXT_SUBTLE    = "#6c6c75"   # disabled, hints

# Brand accent — orange. Used SPARINGLY: active state, primary CTA only.
ACCENT_ORANGE        = "#f97316"
ACCENT_ORANGE_HOVER  = "#fb923c"
ACCENT_ORANGE_WARM   = "#fb923c"   # alias kept for back-compat
ACCENT_ORANGE_DIM    = "rgba(249, 115, 22, 0.12)"   # active backdrops

# Status colors — used sparingly (badges, error overlays)
SUCCESS     = "#10b981"   # toned down from #34d399
SUCCESS_DIM = "#0d3328"
WARNING     = "#f59e0b"
WARNING_DIM = "#78350f"
ERROR       = "#ef4444"   # toned down from #f87171
ERROR_DIM   = "#3b1111"

# Secondary accents — kept for badges / family color cues but minimized
ACCENT_BLUE         = "#5b8def"
ACCENT_BLUE_HOVER   = "#4a7de0"
ACCENT_BLUE_DIM     = "#1e3a5f"
ACCENT_PURPLE       = "#a78bfa"
ACCENT_CYAN         = "#22d3ee"

# Pre-computed rgba() values for Qt stylesheet alpha colors
ACCENT_BLUE_A10 = "rgba(91, 141, 239, 25)"
ACCENT_BLUE_A20 = "rgba(91, 141, 239, 50)"
ACCENT_BLUE_A30 = "rgba(91, 141, 239, 76)"
SUCCESS_A20 = "rgba(52, 211, 153, 50)"
SUCCESS_A40 = "rgba(52, 211, 153, 100)"
ERROR_A40 = "rgba(248, 113, 113, 100)"

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
/* The QMainWindow's central QWidget would otherwise paint macOS
   native gray on top of the NSVisualEffectView. Force transparent. */
QMainWindow > QWidget {{ background: transparent; }}

/* Pages inside the QStackedWidget — direct child QWidgets that Qt
   otherwise paints with the macOS native window tint. Setting
   transparent here lets the window-level NSVisualEffectView blur
   show through every page's empty area. */
QStackedWidget {{ background: {BG_BASE}; }}
QStackedWidget > QWidget {{ background: {BG_BASE}; }}

/* QScrollArea's viewport is a separate QWidget — styling QScrollArea
   alone leaves the viewport painting its native gray. The ">QWidget"
   here matches the viewport (the only direct QWidget child of a
   QScrollArea). */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget {{ background: {BG_BASE}; }}
QScrollBar:vertical {{
    background: transparent; width: 6px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_DEFAULT}; min-height: 30px; border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{ background: {BORDER_STRONG}; }}
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
)


def auto_shadow():
    """No-op for the flat pure-black design; kept so existing call sites
    don't need to be hunted down. Returning None means setGraphicsEffect(None)
    which clears any prior effect."""
    return None


def make_card() -> QFrame:
    f = QFrame()
    f.setStyleSheet(CARD_QSS)
    return f


# ── Painted Sidebar Icons ───────────────────────────────────────────────
# Cleaner, bolder vector icons drawn with QPainterPath

def _draw_icon_home(p: QPainter, r: QRect) -> None:
    """House icon — clean outlined shape."""
    cx, cy = r.center().x(), r.center().y()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
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
    """Chip/model icon — outlined rounded rect with inner details."""
    cx, cy = r.center().x(), r.center().y()
    p.drawRoundedRect(QRectF(cx - 6, cy - 6, 12, 12), 2, 2)
    p.drawPoint(int(cx - 2), int(cy - 2))
    p.drawPoint(int(cx + 2), int(cy - 2))
    p.drawPoint(int(cx - 2), int(cy + 2))
    p.drawPoint(int(cx + 2), int(cy + 2))


def _draw_icon_settings(p: QPainter, r: QRect) -> None:
    """Gear icon — cleaner stroke design."""
    cx, cy = r.center().x(), r.center().y()
    p.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))
    import math
    for angle in range(0, 360, 45):
        rad = math.radians(angle)
        x1, y1 = cx + 5 * math.cos(rad), cy + 5 * math.sin(rad)
        x2, y2 = cx + 7.5 * math.cos(rad), cy + 7.5 * math.sin(rad)
        p.drawLine(int(x1), int(y1), int(x2), int(y2))


def _draw_icon_hotwords(p: QPainter, r: QRect) -> None:
    """Star/sparkle icon for hotwords."""
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
    """Info icon — elegant circle with 'i'."""
    cx, cy = r.center().x(), r.center().y()
    p.drawEllipse(QRectF(cx - 7, cy - 7, 14, 14))
    f = QFont("Helvetica Neue", 10)
    p.setFont(f)
    p.drawText(QRectF(cx - 7, cy - 7, 14, 14), Qt.AlignmentFlag.AlignCenter, "i")


def draw_boltPath(p: QPainter, rect: QRectF, color: str = "#ffffff") -> None:
    """Draws a clean, dynamic lightning bolt symbol centered in the rect."""
    path = QPainterPath()
    # A classic bolt: wide at top, jutting left, narrowing to a point.
    cx, cy = rect.center().x(), rect.center().y()
    w, h = min(rect.width(), 20), min(rect.height(), 24)
    # Start top right
    path.moveTo(cx + w*0.15, cy - h*0.45)
    # angle down left
    path.lineTo(cx - w*0.35, cy + h*0.05)
    # horizontal right
    path.lineTo(cx + w*0.15, cy + h*0.05)
    # jut down
    path.lineTo(cx - w*0.15, cy + h*0.45)
    # angle up right
    path.lineTo(cx + w*0.35, cy - h*0.15)
    # horizontal left
    path.lineTo(cx - w*0.15, cy - h*0.15)
    path.closeSubpath()

    old_pen = p.pen()
    old_brush = p.brush()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    p.setPen(old_pen)
    p.setBrush(old_brush)


ICON_PAINTERS = [_draw_icon_home, _draw_icon_models, _draw_icon_hotwords, _draw_icon_settings, _draw_icon_about]


# ── Custom Toggle Switch (QPainter-based) ───────────────────────────────
# Matches 闪电说 style: dark gray track + white knob when ON
#                       darker track + gray knob when OFF

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
            # ON: darker fill track + white knob (like 闪电说)
            p.setBrush(QColor("#48484e"))
            p.setPen(Qt.PenStyle.NoPen)
        else:
            # OFF: translucent dark track + faint white rim. Painted
            # widgets need explicit QColor(r,g,b,a) — Qt's QColor does
            # NOT parse the CSS rgba() strings used by our QSS tokens.
            p.setBrush(QColor(34, 34, 38, 200))
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawRoundedRect(track, 12, 12)

        # Knob
        knob = QRectF(self._knob_x, 2, 20, 20)
        p.setPen(Qt.PenStyle.NoPen)
        if self._checked:
            p.setBrush(QColor("#f0f0f2"))   # White knob when ON
        else:
            # Mid-gray knob — must read as a control, not vanish into
            # the translucent track behind it.
            p.setBrush(QColor(140, 140, 145))
        p.drawEllipse(knob)

        p.end()


# ── Section heading helper ──────────────────────────────────────────────

def section_heading(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setFont(font(14, bold=True))
    lbl.setStyleSheet(
        f"color: {TEXT_PRIMARY}; padding-top: 4px;"
    )
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
    bg_hover: str = "rgba(255,255,255,0.05)",
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
        f"QPushButton:hover {{ background: {bg_hover}; color: {fg_hover}; }}"
    )
    return btn


def accent_button(text: str, height: int = 40) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: {ACCENT_BLUE};"
        f" color: #fff; border: none;"
        f" border-radius: {height // 2}px; padding: 0 24px; font-size: 13px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: {ACCENT_BLUE_HOVER}; }}"
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
    f"QComboBox {{ background: {BG_INPUT}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_SUBTLE};"
    f" border-radius: 10px; padding: 10px 16px; font-size: 13px; }}"
    f"QComboBox:hover {{ border: 1px solid {BORDER_DEFAULT}; }}"
    f"QComboBox::drop-down {{ border: none; width: 28px; }}"
    f"QComboBox QAbstractItemView {{ background: {BG_CARD}; color: {TEXT_PRIMARY};"
    f" selection-background-color: {BORDER_DEFAULT}; border: 1px solid {BORDER_DEFAULT};"
    f" border-radius: 8px; padding: 4px; }}"
)

# ── Line edit style ─────────────────────────────────────────────────────

INPUT_QSS = (
    f"QLineEdit {{ background: {BG_INPUT}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_SUBTLE};"
    f" border-radius: 10px; padding: 10px 16px; font-size: 13px; }}"
    f"QLineEdit:focus {{ border: 1px solid {ACCENT_BLUE}; }}"
)


# ── Segment tab bar (pill-style) ────────────────────────────────────────

def segment_tab_qss() -> str:
    """Returns QSS for a segment-control style tab bar (rounded pill tabs)."""
    return (
        f"QTabBar {{ background: transparent; }}"
        f"QTabBar::tab {{ background: transparent; color: {TEXT_SECONDARY};"
        f" padding: 8px 24px; border: 1px solid transparent;"
        f" border-radius: 8px; margin: 0 2px; font-size: 13px; }}"
        f"QTabBar::tab:selected {{ background: {BG_ELEVATED}; color: {TEXT_PRIMARY};"
        f" border: 1px solid {BORDER_SUBTLE}; font-weight: bold; }}"
        f"QTabBar::tab:hover {{ color: {TEXT_PRIMARY}; }}"
    )
