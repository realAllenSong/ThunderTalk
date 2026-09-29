"""Vector icon set — stroke icons painted with QPainter on a 24x24 grid.

No SVG / QtSvg dependency (keeps the PyInstaller bundle lean). Each icon is
a small function drawing into a unit square; ``paint_icon`` scales it to any
rect and colour, ``icon_pixmap`` renders it to a crisp HiDPI pixmap.

Style: 1.75 px stroke on the 24 grid, round caps and joins (Lucide-like).
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap


def _poly(p: QPainter, pts: list[tuple[float, float]], closed: bool = False) -> None:
    path = QPainterPath(QPointF(*pts[0]))
    for x, y in pts[1:]:
        path.lineTo(x, y)
    if closed:
        path.closeSubpath()
    p.drawPath(path)


def _line(p: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _circle(p: QPainter, cx: float, cy: float, r: float) -> None:
    p.drawEllipse(QPointF(cx, cy), r, r)


def _rrect(p: QPainter, x: float, y: float, w: float, h: float, r: float) -> None:
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _arc(p: QPainter, cx: float, cy: float, r: float, start: float, span: float) -> None:
    """Arc around (cx, cy); angles in degrees, 0 = 3 o'clock, CCW positive."""
    path = QPainterPath()
    path.arcMoveTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), start)
    path.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), start, span)
    p.drawPath(path)


def _dot(p: QPainter, cx: float, cy: float, r: float = 1.0) -> None:
    # Filled dot in the current pen colour.
    color = p.pen().color()
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.restore()


# ── Icons ────────────────────────────────────────────────────────────────

def _home(p: QPainter) -> None:
    _poly(p, [(3, 11), (12, 3.5), (21, 11)])
    _poly(p, [(5.5, 9.5), (5.5, 20), (18.5, 20), (18.5, 9.5)])
    _poly(p, [(10, 20), (10, 14.5), (14, 14.5), (14, 20)])


def _cpu(p: QPainter) -> None:
    _rrect(p, 6, 6, 12, 12, 2.2)
    _rrect(p, 9.5, 9.5, 5, 5, 1)
    for x in (9.5, 14.5):
        _line(p, x, 2.8, x, 6)
        _line(p, x, 18, x, 21.2)
    for y in (9.5, 14.5):
        _line(p, 2.8, y, 6, y)
        _line(p, 18, y, 21.2, y)


def _star(p: QPainter) -> None:
    pts = []
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        r = 9.2 if i % 2 == 0 else 4.1
        pts.append((12 + r * math.cos(ang), 12.6 + r * math.sin(ang)))
    _poly(p, pts, closed=True)


def _sliders(p: QPainter) -> None:
    _line(p, 3.5, 6.5, 8, 6.5)
    _line(p, 12, 6.5, 20.5, 6.5)
    _circle(p, 10, 6.5, 2)
    _line(p, 3.5, 12, 13, 12)
    _line(p, 17, 12, 20.5, 12)
    _circle(p, 15, 12, 2)
    _line(p, 3.5, 17.5, 6, 17.5)
    _line(p, 10, 17.5, 20.5, 17.5)
    _circle(p, 8, 17.5, 2)


def _flask(p: QPainter) -> None:
    _line(p, 9, 3, 15, 3)
    _poly(p, [(10, 3), (10, 9.2), (4.6, 18.4), (6.1, 21), (17.9, 21),
              (19.4, 18.4), (14, 9.2), (14, 3)])
    _line(p, 7.4, 15, 16.6, 15)


def _info(p: QPainter) -> None:
    _circle(p, 12, 12, 9)
    _line(p, 12, 11, 12, 16.2)
    _dot(p, 12, 7.8, 1.1)


def _mic(p: QPainter) -> None:
    _rrect(p, 9, 2.8, 6, 11.4, 3)
    _arc(p, 12, 11, 7, 180, 180)
    _line(p, 12, 18, 12, 21)
    _line(p, 8.5, 21, 15.5, 21)


def _keyboard(p: QPainter) -> None:
    _rrect(p, 2.5, 5.5, 19, 13, 2.6)
    for x in (6.5, 10.2, 13.9, 17.5):
        _line(p, x, 9.6, x + 0.05, 9.6)
    for x in (6.5, 17.5):
        _line(p, x, 13.6, x + 0.05, 13.6)
    _line(p, 9.5, 14.2, 14.5, 14.2)


def _shield_check(p: QPainter) -> None:
    path = QPainterPath(QPointF(12, 2.8))
    path.lineTo(19.6, 5.8)
    path.lineTo(19.6, 11.4)
    path.cubicTo(19.6, 16, 16.4, 19.6, 12, 21.2)
    path.cubicTo(7.6, 19.6, 4.4, 16, 4.4, 11.4)
    path.lineTo(4.4, 5.8)
    path.closeSubpath()
    p.drawPath(path)
    _poly(p, [(8.6, 12), (11, 14.4), (15.6, 9.6)])


def _download(p: QPainter) -> None:
    _line(p, 12, 3.5, 12, 15)
    _poly(p, [(7.4, 10.6), (12, 15.2), (16.6, 10.6)])
    _poly(p, [(4, 15.5), (4, 19.5), (20, 19.5), (20, 15.5)])


def _check(p: QPainter) -> None:
    _poly(p, [(4.8, 12.6), (9.8, 17.6), (19.2, 7)])


def _copy(p: QPainter) -> None:
    _rrect(p, 9, 9, 11.5, 11.5, 2.4)
    path = QPainterPath(QPointF(15, 9))
    path.lineTo(15, 6.4)
    path.arcTo(QRectF(11.4, 3.8, 3.6, 3.6), 0, 90)  # top-right corner
    path.lineTo(6.6, 3.8)
    path.arcTo(QRectF(3.8, 3.8, 5.2, 5.2), 90, 90)
    path.lineTo(3.8, 12.6)
    path.arcTo(QRectF(3.8, 9.6, 5.2, 5.2), 180, 90)
    path.lineTo(9, 15)
    p.drawPath(path)


def _trash(p: QPainter) -> None:
    _line(p, 4, 6.5, 20, 6.5)
    _poly(p, [(9, 6.5), (9, 4), (15, 4), (15, 6.5)])
    _poly(p, [(6, 6.5), (6.9, 19.2), (7.6, 20.5), (16.4, 20.5), (17.1, 19.2), (18, 6.5)])
    _line(p, 10, 10.5, 10, 16.5)
    _line(p, 14, 10.5, 14, 16.5)


def _search(p: QPainter) -> None:
    _circle(p, 10.6, 10.6, 6.6)
    _line(p, 15.6, 15.6, 20.5, 20.5)


def _x(p: QPainter) -> None:
    _line(p, 6, 6, 18, 18)
    _line(p, 18, 6, 6, 18)


def _arrow_right(p: QPainter) -> None:
    _line(p, 4.5, 12, 19.5, 12)
    _poly(p, [(13.5, 6), (19.5, 12), (13.5, 18)])


def _sparkles(p: QPainter) -> None:
    def spark(cx, cy, r):
        path = QPainterPath(QPointF(cx, cy - r))
        path.cubicTo(cx + r * 0.15, cy - r * 0.15, cx + r * 0.15, cy - r * 0.15, cx + r, cy)
        path.cubicTo(cx + r * 0.15, cy + r * 0.15, cx + r * 0.15, cy + r * 0.15, cx, cy + r)
        path.cubicTo(cx - r * 0.15, cy + r * 0.15, cx - r * 0.15, cy + r * 0.15, cx - r, cy)
        path.cubicTo(cx - r * 0.15, cy - r * 0.15, cx - r * 0.15, cy - r * 0.15, cx, cy - r)
        p.drawPath(path)
    spark(10, 13, 7)
    spark(18.4, 5.6, 3)


def _bolt(p: QPainter) -> None:
    _poly(p, [(13.4, 2.6), (5, 13.4), (11.2, 13.4), (10.4, 21.4),
              (19, 10.2), (12.8, 10.2)], closed=True)


def _globe(p: QPainter) -> None:
    _circle(p, 12, 12, 9)
    _line(p, 3, 12, 21, 12)
    path = QPainterPath()
    path.addEllipse(QRectF(8, 3, 8, 18))
    p.drawPath(path)


def _clock(p: QPainter) -> None:
    _circle(p, 12, 12, 9)
    _poly(p, [(12, 7), (12, 12), (15.4, 14)])


def _text(p: QPainter) -> None:
    _line(p, 4, 6.5, 20, 6.5)
    _line(p, 4, 12, 15, 12)
    _line(p, 4, 17.5, 11, 17.5)


def _alert(p: QPainter) -> None:
    _poly(p, [(12, 3.6), (21.4, 19.8), (2.6, 19.8)], closed=True)
    _line(p, 12, 9.8, 12, 14.4)
    _dot(p, 12, 17.2, 1.05)


def _play(p: QPainter) -> None:
    _poly(p, [(7, 4.6), (19, 12), (7, 19.4)], closed=True)


def _refresh(p: QPainter) -> None:
    _arc(p, 12, 12, 8, 30, 290)
    _poly(p, [(18.4, 3.6), (19.4, 8.6), (14.4, 9.4)])


def _folder(p: QPainter) -> None:
    _poly(p, [(3, 7), (3, 18.6), (4.4, 20), (19.6, 20), (21, 18.6), (21, 9.4),
              (19.6, 8), (11.4, 8), (9.6, 5.4), (4.4, 5.4), (3, 6.8)])


def _external(p: QPainter) -> None:
    _poly(p, [(14, 4), (20, 4), (20, 10)])
    _line(p, 20, 4, 11, 13)
    _poly(p, [(18, 14), (18, 19), (17, 20), (5, 20), (4, 19), (4, 7), (5, 6), (10, 6)])


def _waveform(p: QPainter) -> None:
    for x, h in ((4, 3), (8, 7), (12, 11), (16, 7), (20, 3)):
        _line(p, x, 12 - h / 2 * 1.0, x, 12 + h / 2 * 1.0)


def _plus(p: QPainter) -> None:
    _line(p, 12, 5, 12, 19)
    _line(p, 5, 12, 19, 12)


def _lock(p: QPainter) -> None:
    _rrect(p, 5, 10.5, 14, 10, 2.4)
    path = QPainterPath(QPointF(8, 10.5))
    path.lineTo(8, 7.5)
    path.arcTo(QRectF(8, 3.2, 8, 8.6), 180, -180)
    path.lineTo(16, 10.5)
    p.drawPath(path)


def _cursor_text(p: QPainter) -> None:
    _line(p, 12, 4, 12, 20)
    _line(p, 8.6, 4, 15.4, 4)
    _line(p, 8.6, 20, 15.4, 20)


def _chevron_right(p: QPainter) -> None:
    _poly(p, [(9.2, 5.5), (15.7, 12), (9.2, 18.5)])


def _chevron_down(p: QPainter) -> None:
    _poly(p, [(5.5, 9.2), (12, 15.7), (18.5, 9.2)])


ICONS: dict[str, Callable[[QPainter], None]] = {
    "home": _home, "cpu": _cpu, "star": _star, "sliders": _sliders,
    "flask": _flask, "info": _info, "mic": _mic, "keyboard": _keyboard,
    "shield": _shield_check, "download": _download, "check": _check,
    "copy": _copy, "trash": _trash, "search": _search, "x": _x,
    "arrow-right": _arrow_right, "sparkles": _sparkles, "bolt": _bolt,
    "globe": _globe, "clock": _clock, "text": _text, "alert": _alert,
    "play": _play, "refresh": _refresh, "folder": _folder,
    "external": _external, "waveform": _waveform, "plus": _plus,
    "lock": _lock, "cursor": _cursor_text,
    "chevron-right": _chevron_right, "chevron-down": _chevron_down,
}


def paint_icon(
    p: QPainter,
    name: str,
    rect: QRectF,
    color: QColor | str,
    stroke: float = 1.75,
) -> None:
    """Draw icon ``name`` scaled into ``rect`` (any aspect — uses the min side)."""
    fn = ICONS.get(name)
    if fn is None:
        return
    c = QColor(color)
    side = min(rect.width(), rect.height())
    scale = side / 24.0
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    p.scale(scale, scale)
    pen = QPen(c, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    fn(p)
    p.restore()


def icon_pixmap(name: str, size: int, color: QColor | str,
                stroke: float = 1.75, dpr: float = 2.0) -> QPixmap:
    """Render an icon to a transparent HiDPI pixmap of logical ``size``."""
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    paint_icon(p, name, QRectF(0, 0, size, size), color, stroke)
    p.end()
    return px
