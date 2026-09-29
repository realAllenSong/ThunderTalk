"""Design system v3 — "Paper & Ink".

A quiet, editorial look: warm paper canvas, off-black ink, hairline rules,
flat surfaces. No gradients, no glass, no glow, no decorative motion.

  * INK (near-black) is the primary action colour: solid buttons, on-toggles.
  * ORANGE is scarce — the live/recording dot, the active-nav marker, focus,
    links. Never a large fill.
  * Meaning comes from muted pastel tags (green / blue / yellow / red /
    orange), each a pale fill with dark text of the same hue.
  * Headings are set in a serif (Charter → Georgia; CJK falls through to
    Songti); everything else is the system UI font; keys and figures that
    should read as data use a monospace.

Colour is a token: every widget imports these names instead of hard-coding
values, so restyling the app is an edit to this file. Legacy names from the
previous design (BG_*, TEXT_*, ACCENT_*, pill_button, accent_button, ...) keep
working so pages need no rewrite to follow a palette change.

Import-safe before a QApplication exists, except the font helpers.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QRectF,
    QSize,
    Property,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ── Colour tokens ────────────────────────────────────────────────────────

BG_DEEPEST    = "#F3F1EC"
BG_BASE       = "#FBFBFA"   # canvas
BG_SIDEBAR    = "#F3F1EC"   # warm bone
BG_SURFACE    = "#FBFBFA"
BG_CARD       = "#FFFFFF"   # cards, inputs, popups
BG_CARD_HOVER = "#FAF9F6"
BG_ELEVATED   = "#F1EFEA"   # subtle wells, off-state tracks
BG_INPUT      = "#FFFFFF"
INK           = "#111111"   # primary action fill
INK_HOVER     = "#333333"

# Hairlines and interaction fills — ink at low alpha over paper.
BORDER_SUBTLE  = "rgba(31,30,27,0.07)"
BORDER_DEFAULT = "rgba(31,30,27,0.12)"
BORDER_STRONG  = "rgba(31,30,27,0.26)"
HOVER_FILL     = "rgba(31,30,27,0.05)"
PRESS_FILL     = "rgba(31,30,27,0.09)"
_BORDER_SUBTLE_C  = QColor(31, 30, 27, 18)
_BORDER_DEFAULT_C = QColor(31, 30, 27, 31)
_BORDER_STRONG_C  = QColor(31, 30, 27, 66)
_HOVER_FILL_C     = QColor(31, 30, 27, 13)
_PRESS_FILL_C     = QColor(31, 30, 27, 23)

# Text — never pure black.
TEXT_PRIMARY   = "#1F1E1B"
TEXT_SECONDARY = "#55534E"
TEXT_MUTED     = "#787774"
TEXT_SUBTLE    = "#A9A69D"

# The single warm accent (the bolt). Small doses only.
ACCENT_ORANGE        = "#D9480F"
ACCENT_ORANGE_HOVER  = "#B93C0B"   # darker on hover/press (this is a light theme)
ACCENT_ORANGE_WARM   = "#D9480F"
ACCENT_ORANGE_DIM    = "rgba(217,72,15,0.10)"
ACCENT_ORANGE_TOP    = "#E8590C"
ACCENT_ORANGE_DEEP   = "#9A3412"
ACCENT_ON            = "#FFFFFF"

GLOW_ACCENT   = "rgba(217,72,15,0.10)"
GLOW_ACCENT_S = "rgba(217,72,15,0.05)"

# State colours — for dots and inline text (dark enough for paper).
SUCCESS     = "#2E7D4F"
SUCCESS_DIM = "#EDF3EC"
WARNING     = "#B7791F"
WARNING_DIM = "#FBF3DB"
ERROR       = "#B42318"
ERROR_DIM   = "#FDEBEC"

# Secondary hues
ACCENT_BLUE         = "#1F6C9F"
ACCENT_BLUE_HOVER   = "#185782"
ACCENT_BLUE_DIM     = "#E1F3FE"
ACCENT_PURPLE       = "#6B4FA0"
ACCENT_CYAN         = "#1B7F8C"

# Muted pastel tags: (text, fill, hairline)
PASTELS = {
    "neutral": ("#55534E", "#EFEDE7", "#E2DFD6"),
    "muted":   ("#787774", "#F3F1EC", "#E6E3DC"),
    "green":   ("#346538", "#EDF3EC", "#D3E4D1"),
    "blue":    ("#1F6C9F", "#E1F3FE", "#C4E3F6"),
    "amber":   ("#956400", "#FBF3DB", "#F1E2B0"),
    "red":     ("#9F2F2D", "#FDEBEC", "#F4CDCF"),
    "orange":  ("#9A3D0C", "#FCEADD", "#F5D0B6"),
    "purple":  ("#5B3F94", "#EFE9F8", "#DCD0EF"),
}

# Retained alpha strings (older call sites)
ACCENT_BLUE_A10 = "rgba(31,108,159,0.10)"
ACCENT_BLUE_A20 = "rgba(31,108,159,0.20)"
ACCENT_BLUE_A30 = "rgba(31,108,159,0.30)"
SUCCESS_A20 = "rgba(46,125,79,0.20)"
SUCCESS_A40 = "rgba(46,125,79,0.40)"
ERROR_A40   = "rgba(180,35,24,0.40)"

# Shape — crisp, never pill-shaped for containers or buttons.
RADIUS_CARD = 10
RADIUS_CONTROL = 6

# Page rhythm — generous whitespace, constrained measure.
PAGE_MARGIN_X = 44
PAGE_MARGIN_TOP = 44
PAGE_MAX_WIDTH = 860


def qcolor(css: str, alpha: float | None = None) -> QColor:
    """QColor from '#rrggbb' or 'rgba(r,g,b,a)' strings used as QSS tokens."""
    if css.startswith("rgba("):
        r, g, b, a = [x.strip() for x in css[5:-1].split(",")]
        af = float(a)
        c = QColor(int(r), int(g), int(b))
        c.setAlphaF(af if af <= 1.0 else af / 255.0)
    else:
        c = QColor(css)
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def force_light(app) -> None:
    """The palette is light-only. Without this, a Mac in dark mode gives every
    unstyled widget (text edits, native dialogs) light-on-dark colours that
    vanish against the paper."""
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except Exception:
        pass


# ── Fonts ────────────────────────────────────────────────────────────────

_SERIF = ["Charter", "Georgia", "Songti SC", "Times New Roman"]
_MONO = ["SF Mono", "Menlo", "Courier New"]


def _ui_family() -> str:
    fam = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    if fam in ("", "Sans Serif"):
        fam = "Helvetica Neue"
    return fam


def font(size: int = 13, bold: bool = False) -> QFont:
    f = QFont(_ui_family(), size)
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


def font_serif(size: int = 20, bold: bool = True) -> QFont:
    """Editorial serif for headings and figures. Latin in Charter; CJK glyphs
    fall through to Songti."""
    f = QFont()
    f.setFamilies(_SERIF)
    f.setPointSize(size)
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 99)
    return f


def font_heading(size: int = 17) -> QFont:
    return font_serif(size, True)


def font_display(size: int = 34) -> QFont:
    """Large titles and figures."""
    return font_serif(size, True)


def font_mono(size: int = 12, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(_MONO)
    f.setPointSize(size)
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


# ── Global application stylesheet ───────────────────────────────────────

APP_QSS = f"""
QMainWindow {{ background: {BG_BASE}; }}

QStackedWidget {{ background: transparent; }}
QStackedWidget > QWidget {{ background: transparent; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget {{ background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px; }}
QScrollBar::handle:vertical {{
    background: rgba(31,30,27,0.16); min-height: 36px; border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(31,30,27,0.30); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{ height: 0; }}

QToolTip {{
    background: {INK}; color: #FFFFFF;
    border: none; padding: 6px 9px;
    border-radius: 4px; font-size: 12px;
}}

QMenu {{
    background: {BG_CARD}; color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_DEFAULT}; border-radius: 8px; padding: 5px;
}}
QMenu::item {{ padding: 6px 16px; border-radius: 4px; }}
QMenu::item:selected {{ background: {HOVER_FILL}; }}
QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
QMenu::separator {{ height: 1px; background: {BORDER_SUBTLE}; margin: 4px 8px; }}
"""


# ── Buttons ─────────────────────────────────────────────────────────────

def button_qss(kind: str = "secondary", height: int = 36, font_px: int = 13,
               pad: int | None = None) -> str:
    """QSS for a rectangular button (4-6px radius — never a pill).

    kind: primary (solid ink) | secondary (outlined) | ghost | danger."""
    r = 5 if height <= 32 else 6
    pad = pad if pad is not None else max(12, height // 2)
    base = f"border-radius: {r}px; padding: 0 {pad}px; font-size: {font_px}px;"
    if kind == "primary":
        return (
            f"QPushButton {{ background: {INK}; color: {ACCENT_ON};"
            f" border: 1px solid {INK}; font-weight: 600; {base} }}"
            f"QPushButton:hover {{ background: {INK_HOVER}; border: 1px solid {INK_HOVER}; }}"
            f"QPushButton:pressed {{ background: #000000; border: 1px solid #000000; }}"
            f"QPushButton:disabled {{ background: {BG_ELEVATED}; color: {TEXT_SUBTLE};"
            f" border: 1px solid {BORDER_SUBTLE}; }}"
        )
    if kind == "danger":
        return (
            f"QPushButton {{ background: {ERROR}; color: #FFFFFF;"
            f" border: 1px solid {ERROR}; font-weight: 600; {base} }}"
            f"QPushButton:hover {{ background: #921C12; border: 1px solid #921C12; }}"
            f"QPushButton:pressed {{ background: #7A170F; }}"
            f"QPushButton:disabled {{ background: {BG_ELEVATED}; color: {TEXT_SUBTLE};"
            f" border: 1px solid {BORDER_SUBTLE}; }}"
        )
    if kind == "ghost":
        return (
            f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY};"
            f" border: 1px solid transparent; {base} }}"
            f"QPushButton:hover {{ background: {HOVER_FILL}; color: {TEXT_PRIMARY}; }}"
            f"QPushButton:pressed {{ background: {PRESS_FILL}; }}"
            f"QPushButton:disabled {{ color: {TEXT_SUBTLE}; }}"
        )
    return (  # secondary
        f"QPushButton {{ background: {BG_CARD}; color: {TEXT_PRIMARY};"
        f" border: 1px solid {BORDER_DEFAULT}; {base} }}"
        f"QPushButton:hover {{ background: {BG_CARD_HOVER}; border: 1px solid {BORDER_STRONG}; }}"
        f"QPushButton:pressed {{ background: {BG_ELEVATED}; }}"
        f"QPushButton:disabled {{ color: {TEXT_SUBTLE}; background: {BG_SURFACE};"
        f" border: 1px solid {BORDER_SUBTLE}; }}"
    )


def make_button(text: str, kind: str = "secondary", height: int = 36,
                width: int = 0, font_px: int = 13) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    if width:
        btn.setFixedWidth(width)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(button_qss(kind, height, font_px))
    return btn


def restyle_button(btn: QPushButton, kind: str, height: int | None = None,
                   font_px: int = 13) -> None:
    btn.setStyleSheet(button_qss(kind, height or btn.height() or 36, font_px))


def pill_button(
    text: str,
    bg: str = "transparent",
    fg: str = TEXT_SECONDARY,
    bg_hover: str = HOVER_FILL,
    fg_hover: str = TEXT_PRIMARY,
    border: str = BORDER_DEFAULT,
    width: int = 0,
    height: int = 34,
) -> QPushButton:
    """Legacy helper (name kept) — a quiet outlined button."""
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    if width:
        btn.setFixedWidth(width)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
        f" border-radius: 5px; padding: 0 14px; font-size: 12px; }}"
        f"QPushButton:hover {{ background: {bg_hover}; color: {fg_hover};"
        f" border: 1px solid {BORDER_STRONG}; }}"
        f"QPushButton:pressed {{ background: {PRESS_FILL}; }}"
    )
    return btn


def accent_button(text: str, height: int = 40) -> QPushButton:
    """Legacy helper (name kept) — the solid-ink primary button."""
    return make_button(text, "primary", height)


# ── Card (painted, flat) ────────────────────────────────────────────────

class Card(QFrame):
    """Flat white surface with a 1px hairline. Painted (not QSS) so child
    QLabels never inherit its border.

    ``accent`` turns it into a pale, semantic banner (tinted fill + tinted
    hairline) — meant for warnings/errors only, not decoration.
    ``hoverable`` darkens the hairline while the pointer is over it."""

    def __init__(self, radius: int = RADIUS_CARD, hoverable: bool = False,
                 accent: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._radius = radius
        self._accent = QColor(accent) if accent else None
        self._hoverable = hoverable
        self._hover = False
        if hoverable:
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_accent(self, color: str | None) -> None:
        self._accent = QColor(color) if color else None
        self.update()

    def enterEvent(self, e) -> None:
        if self._hoverable:
            self._hover = True
            self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        if self._hoverable:
            self._hover = False
            self.update()
        super().leaveEvent(e)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.fillPath(path, QColor(BG_CARD))
        if self._accent is not None:
            tint = QColor(self._accent)
            tint.setAlpha(22)
            p.fillPath(path, tint)
            edge = QColor(self._accent)
            edge.setAlpha(90)
            p.setPen(QPen(edge, 1))
        else:
            p.setPen(QPen(_BORDER_STRONG_C if self._hover else _BORDER_DEFAULT_C, 1))
        p.drawPath(path)
        p.end()


def make_card() -> Card:
    return Card()


# ── Page transitions ────────────────────────────────────────────────────

def fade_in(widget: QWidget, duration: int = 0) -> None:
    """Kept for callers; pages now switch instantly. Decorative fades were
    dropped on purpose — motion here should carry information or be absent."""
    return None


# ── Toggle switch ───────────────────────────────────────────────────────

class ToggleSwitch(QWidget):
    """Flat switch: ink when on, warm grey when off. A short slide of the knob
    is the only motion. Space / Return flips it when focused."""

    toggled_signal = Signal(bool)

    _W, _H = 38, 22

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._W, self._H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._checked = checked
        self._t = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos_t")
        self._anim.setDuration(110)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool) -> None:
        """Programmatic set — no signal, no animation."""
        self._checked = bool(val)
        self._t = 1.0 if self._checked else 0.0
        self.update()

    def _get_t(self) -> float:
        return self._t

    def _set_t(self, v: float) -> None:
        self._t = v
        self.update()

    pos_t = Property(float, _get_t, _set_t)

    def _flip(self) -> None:
        self._checked = not self._checked
        self._anim.stop()
        self._anim.setStartValue(float(self._t))
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.start()
        self.toggled_signal.emit(self._checked)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._flip()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._flip()
        else:
            super().keyPressEvent(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self._W, self._H
        track = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(lerp_color(QColor("#D5D2C9"), QColor(INK), self._t))
        p.drawRoundedRect(track, h / 2, h / 2)
        d = h - 6
        x = 3 + (w - 6 - d) * self._t
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 3, d, d))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(ACCENT_ORANGE), 1.5))
            p.drawRoundedRect(QRectF(-1, -1, w + 2, h + 2), (h + 2) / 2, (h + 2) / 2)
        p.end()


# ── Small helpers ───────────────────────────────────────────────────────

def section_heading(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setFont(font_serif(15))
    lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; padding-top: 4px;")
    return lbl


def setting_row(label: str, description: str = "") -> tuple[QHBoxLayout, QLabel]:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(16)
    left = QVBoxLayout()
    left.setSpacing(3)
    left.setContentsMargins(0, 0, 0, 0)
    name = QLabel(label)
    name.setFont(font(13, bold=True))
    name.setStyleSheet(f"color: {TEXT_PRIMARY}; border: none; background: transparent;")
    left.addWidget(name)
    if description:
        desc = QLabel(description)
        desc.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 12px; border: none; background: transparent;"
        )
        desc.setWordWrap(True)
        left.addWidget(desc)
    row.addLayout(left, stretch=1)
    return row, name


def separator() -> QFrame:
    s = QFrame()
    s.setFixedHeight(1)
    s.setStyleSheet(f"background: {BORDER_SUBTLE}; border: none;")
    return s


def badge_qss(color: str = TEXT_SECONDARY, bg: str = "#EFEDE7",
              border: str = BORDER_SUBTLE, size_px: int = 10) -> str:
    return (
        f"color: {color}; background: {bg}; border: 1px solid {border};"
        f" border-radius: {size_px + 3}px; padding: 2px 9px;"
        f" font-size: {size_px}px; font-weight: 600; letter-spacing: 0.5px;"
    )


class Chip(QLabel):
    """Small self-painted tag: pale fill, dark same-hue text. (QSS radius on a
    QLabel clamps inconsistently across layouts, so the pill is drawn by hand.)"""

    def __init__(self, text: str, fg: str = TEXT_SECONDARY, bg: str = "#EFEDE7",
                 border: str = BORDER_SUBTLE, size_px: int = 10, upper: bool = False) -> None:
        self._upper = upper
        super().__init__(text.upper() if upper else text)
        self._fg, self._bg, self._bd = qcolor(fg), qcolor(bg), qcolor(border)
        f = QFont(_ui_family())
        f.setPixelSize(size_px)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5 if upper else 0.2)
        self.setFont(f)
        self.setStyleSheet("background: transparent; border: none;")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_colors(self, fg: str, bg: str, border: str) -> None:
        self._fg, self._bg, self._bd = qcolor(fg), qcolor(bg), qcolor(border)
        self.update()

    def setText(self, text: str) -> None:
        super().setText(text.upper() if getattr(self, "_upper", False) else text)
        self.updateGeometry()

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + 20, fm.height() + 7)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(self._bg)
        p.setPen(QPen(self._bd, 1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(self._fg)
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


def badge(text: str, kind: str = "neutral", upper: bool = False) -> Chip:
    """Muted pastel tag. kind: neutral | muted | green | blue | amber | red | orange | purple."""
    fg, bg, bd = PASTELS.get(kind, PASTELS["neutral"])
    return Chip(text, fg, bg, bd, upper=upper)


# ── Combo box / line edit / tabs ────────────────────────────────────────

COMBO_QSS = (
    f"QComboBox {{ background: {BG_CARD}; color: {TEXT_PRIMARY};"
    f" border: 1px solid {BORDER_DEFAULT};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 7px 12px; font-size: 13px; }}"
    f"QComboBox:hover {{ border: 1px solid {BORDER_STRONG}; }}"
    f"QComboBox:focus {{ border: 1px solid {INK}; }}"
    f"QComboBox::drop-down {{ border: none; width: 24px; }}"
    f"QComboBox QAbstractItemView {{ background: {BG_CARD}; color: {TEXT_PRIMARY};"
    f" border: 1px solid {BORDER_DEFAULT}; border-radius: 8px;"
    f" padding: 4px; outline: 0; }}"
    f"QComboBox QAbstractItemView::item {{ background: {BG_CARD}; color: {TEXT_PRIMARY};"
    f" padding: 6px 10px; min-height: 22px; border: none; border-radius: 4px; }}"
    f"QComboBox QAbstractItemView::item:selected {{"
    f" background: {INK}; color: #FFFFFF; }}"
    f"QComboBox QAbstractItemView::item:hover {{"
    f" background: {HOVER_FILL}; color: {TEXT_PRIMARY}; }}"
)


def style_combo(combo) -> None:
    """Apply COMBO_QSS and force the popup window background."""
    combo.setStyleSheet(COMBO_QSS)
    view = combo.view()
    if view is None:
        return
    view.setStyleSheet(
        f"QListView, QAbstractItemView {{"
        f" background: {BG_CARD}; border: none; outline: 0; }}"
    )
    win = view.window()
    if win is not None and win is not view:
        win.setStyleSheet(
            f"background: {BG_CARD}; border: 1px solid {BORDER_DEFAULT};"
            " border-radius: 8px;"
        )


INPUT_QSS = (
    f"QLineEdit {{ background: {BG_CARD}; color: {TEXT_PRIMARY};"
    f" border: 1px solid {BORDER_DEFAULT};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 8px 12px; font-size: 13px;"
    f" selection-background-color: #F5D0B6; selection-color: {TEXT_PRIMARY}; }}"
    f"QLineEdit:hover {{ border: 1px solid {BORDER_STRONG}; }}"
    f"QLineEdit:focus {{ border: 1px solid {INK}; }}"
)


def segment_tab_qss() -> str:
    return (
        f"QTabBar {{ background: transparent; }}"
        f"QTabBar::tab {{ background: transparent; color: {TEXT_SECONDARY};"
        f" padding: 8px 20px; border: 1px solid transparent;"
        f" border-radius: 5px; margin: 0 2px; font-size: 13px; }}"
        f"QTabBar::tab:selected {{ background: {BG_CARD}; color: {TEXT_PRIMARY};"
        f" border: 1px solid {BORDER_DEFAULT}; font-weight: bold; }}"
        f"QTabBar::tab:hover {{ color: {TEXT_PRIMARY}; background: {HOVER_FILL}; }}"
    )


def draw_boltPath(p: QPainter, rect: QRectF, color: str = "#ffffff") -> None:
    """Filled lightning bolt centred in ``rect`` (legacy helper)."""
    path = QPainterPath()
    cx, cy = rect.center().x(), rect.center().y()
    w, h = min(rect.width(), 20), min(rect.height(), 24)
    path.moveTo(cx + w * 0.15, cy - h * 0.45)
    path.lineTo(cx - w * 0.35, cy + h * 0.05)
    path.lineTo(cx + w * 0.15, cy + h * 0.05)
    path.lineTo(cx - w * 0.15, cy + h * 0.45)
    path.lineTo(cx + w * 0.35, cy - h * 0.15)
    path.lineTo(cx - w * 0.15, cy - h * 0.15)
    path.closeSubpath()
    old_pen = p.pen()
    old_brush = p.brush()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    p.setPen(old_pen)
    p.setBrush(old_brush)
