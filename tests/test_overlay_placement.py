"""Dictation bar: top or bottom, on the display being typed into (#14)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect

from thundertalk.core.active_display import front_window_center


def _win(pid, x, y, w, h, layer=0, alpha=1.0):
    return {"kCGWindowOwnerPID": pid, "kCGWindowLayer": layer, "kCGWindowAlpha": alpha,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


def test_the_front_normal_window_of_the_app_is_where_it_is_used():
    windows = [
        _win(7, 0, 0, 3000, 24, layer=24),          # the menu bar
        _win(42, 2600, 300, 200, 30, layer=101),    # a tooltip of the app, on a higher layer
        _win(42, 1950, 80, 60, 20),                 # a tiny helper window
        _win(42, 0, 0, 800, 600, alpha=0.0),        # an invisible one
        _win(42, 1920, 100, 1200, 800),             # the editor, on the second display
        _win(42, 100, 100, 800, 600),               # another window of it, behind, on the first
        _win(99, 0, 0, 1920, 1080),
    ]
    assert front_window_center(windows, 42) == (2520, 500)
    assert front_window_center(windows, 99) == (960, 540)
    assert front_window_center(windows, 5) is None


def test_bar_sits_at_the_top_or_leaves_room_for_the_live_text_at_the_bottom(qapp=None):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    from thundertalk.ui import overlay as ov
    bar = ov.VoiceOverlay()
    g = QRect(1920, 25, 2560, 1380)                 # the usable area of a second display
    top = bar.bar_origin(g)
    assert (top.x(), top.y()) == (1920 + (2560 - ov._W) // 2, 25 + 52)
    bar.set_position("bottom")
    bottom = bar.bar_origin(g)
    tallest = ov._H + ov._PV_LINES * ov._PV_LINE_H + ov._PV_PAD_B
    assert bottom.x() == top.x()
    assert bottom.y() + tallest == 25 + 1380 - 52   # with three lines of live text it ends 52 px above the edge
    bar.set_position("sideways")
    assert bar.bar_origin(g) == top


def test_the_bar_goes_to_the_display_the_finder_names_and_survives_a_finder_that_fails():
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    from thundertalk.ui import overlay as ov
    bar = ov.VoiceOverlay()
    screen = QGuiApplication.primaryScreen()
    asked = []
    bar.set_screen_finder(lambda: asked.append(1) or screen)
    bar.show_recording()
    assert asked == [1] and bar.pos() == bar.bar_origin(screen.availableGeometry())
    bar.hide_overlay(); bar.hide()

    def broken():
        raise RuntimeError("window server")

    bar.set_screen_finder(broken)
    bar.show_recording()
    assert bar.isVisible()
    bar.hide()


def _mouse(bar, kind, global_point):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    local = QPointF(global_point - bar.pos())
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    ev = QMouseEvent(kind, local, QPointF(global_point), Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseButtonPress: bar.mousePressEvent, QEvent.Type.MouseMove: bar.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: bar.mouseReleaseEvent}[kind](ev)


def test_dragging_the_bar_moves_it_and_it_comes_back_where_it_was_dropped():
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    from thundertalk.ui import overlay as ov
    bar = ov.VoiceOverlay()
    g = QGuiApplication.primaryScreen().availableGeometry()
    moved = []
    bar.on_moved(lambda fx, fy: moved.append((fx, fy)))
    bar.show_recording()
    start = bar.pos()
    grab = start + QPoint(60, 30)
    _mouse(bar, QEvent.Type.MouseButtonPress, grab)
    _mouse(bar, QEvent.Type.MouseMove, grab + QPoint(2, 2))            # a wobble: not a drag
    assert bar.pos() == start
    _mouse(bar, QEvent.Type.MouseMove, grab + QPoint(-120, 180))
    assert bar.pos() == start + QPoint(-120, 180)
    _mouse(bar, QEvent.Type.MouseButtonRelease, grab + QPoint(-120, 180))
    dropped = bar.pos()
    assert len(moved) == 1 and bar._position == "custom"
    bar.hide_overlay(); bar.hide()
    bar.show_recording()                                               # the next dictation
    assert bar.pos() == dropped
    # A new bar told the saved fractions (the next launch) goes to the same place.
    again = ov.VoiceOverlay(); again.set_position("custom", list(moved[0]))
    assert (again.bar_origin(g) - dropped).manhattanLength() <= 2
    bar.hide()


def test_a_click_without_a_drag_changes_nothing_and_the_bar_cannot_leave_the_display():
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    from thundertalk.ui import overlay as ov
    bar = ov.VoiceOverlay()
    g = QGuiApplication.primaryScreen().availableGeometry()
    moved = []
    bar.on_moved(lambda *a: moved.append(a))
    bar.show_recording()
    grab = bar.pos() + QPoint(60, 30)
    _mouse(bar, QEvent.Type.MouseButtonPress, grab)
    _mouse(bar, QEvent.Type.MouseButtonRelease, grab)
    assert moved == [] and bar._position == "top"
    _mouse(bar, QEvent.Type.MouseButtonPress, grab)
    _mouse(bar, QEvent.Type.MouseMove, grab + QPoint(-50000, 50000))   # flung off the corner
    _mouse(bar, QEvent.Type.MouseButtonRelease, grab + QPoint(-50000, 50000))
    tallest = ov._H + ov._PV_LINES * ov._PV_LINE_H + ov._PV_PAD_B
    assert bar.x() == g.x() and bar.y() + tallest <= g.y() + g.height()
    assert moved[-1][0] == 0.0 and moved[-1][1] == 1.0
    bar.hide()


def test_a_broken_saved_position_falls_back_to_something_usable():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    from thundertalk.ui import overlay as ov
    bar = ov.VoiceOverlay()
    for bad in (None, [], ["x", 1], [5, -3]):
        bar.set_position("custom", bad)
    assert bar._custom == (1.0, 0.0)                                   # the last one, clamped
