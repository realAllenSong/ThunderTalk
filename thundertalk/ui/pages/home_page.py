"""Home — one plain answer to "can I dictate right now?", a ledger of figures,
and a searchable transcript log.

Typography does the work: an eyebrow line with the live state, a serif
headline, one sentence, and (only when something needs fixing) one button.
Below, four figures set like a ledger, then the history as ruled rows — no
cards inside cards.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import i18n
from thundertalk.core import state as st
from thundertalk.core.i18n import t
from thundertalk.ui import theme
from thundertalk.ui.icons import paint_icon
from thundertalk.ui.keys import display_combo as hotkey_text
from thundertalk.ui.widgets import (
    IconButton,
    KeyCaps,
    SearchField,
    Spinner,
    Waveform,
    column_scroll,
)

if TYPE_CHECKING:
    from thundertalk.core.history import HistoryStore
    from thundertalk.core.state import AppState

_PAGE_SIZE = 60
_CLAMP_CHARS = 300
_TYPING_UNITS_PER_MIN = 40.0


# ── formatting ───────────────────────────────────────────────────────────

def _fmt_duration(secs: float) -> str:
    secs = max(0, int(round(secs)))
    if secs < 60:
        return t("home.sec_short").format(s=secs)
    mins = secs // 60
    if mins >= 60:
        return t("home.hr_min_short").format(h=mins // 60, m=mins % 60)
    return t("home.min_short").format(m=mins)


def _day_label(day: datetime.date) -> str:
    today = datetime.date.today()
    if day == today:
        return t("home.today")
    if day == today - datetime.timedelta(days=1):
        return t("home.yesterday")
    if i18n.LANG == "zh":
        return f"{day.year}年{day.month}月{day.day}日"
    return day.strftime("%b %d, %Y").replace(" 0", " ")


class _Hairline(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(1)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme._BORDER_DEFAULT_C)
        p.end()


# ── hero ─────────────────────────────────────────────────────────────────

class _Hero(QWidget):
    """Eyebrow (live state) → serif headline → sentence → keys / button."""

    action_clicked = Signal()

    def __init__(self, state: "AppState") -> None:
        super().__init__()
        self._state = state
        self.setStyleSheet("background: transparent;")

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 8, 0, 8)
        col.setSpacing(10)

        eyebrow = QHBoxLayout()
        eyebrow.setSpacing(6)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 9px; background: transparent;")
        eyebrow.addWidget(self._dot)
        self._eyebrow = QLabel("")
        ef = theme.font_mono(11, bold=True)
        ef.setLetterSpacing(ef.SpacingType.AbsoluteSpacing, 1.0)
        self._eyebrow.setFont(ef)
        self._eyebrow.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent;")
        eyebrow.addWidget(self._eyebrow)
        self._spinner = Spinner(14, theme.INK, 1.8)
        self._spinner.hide()
        eyebrow.addWidget(self._spinner)
        eyebrow.addStretch()
        col.addLayout(eyebrow)

        self._title = QLabel("")
        self._title.setFont(theme.font_display(40))
        self._title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        self._title.setContentsMargins(0, 0, 0, 8)     # room for descenders (margins, not QSS padding)
        self._title.setWordWrap(True)
        col.addWidget(self._title)

        self._sub = QLabel("")
        self._sub.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 15px; background: transparent;")
        self._sub.setWordWrap(True)
        self._sub.setMaximumWidth(680)
        col.addWidget(self._sub)
        col.addSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(16)
        self._keycaps = KeyCaps(state.hotkey, height=32)
        row.addWidget(self._keycaps)
        self._cta = theme.make_button("", "primary", 38, font_px=13)
        self._cta.clicked.connect(self.action_clicked)
        row.addWidget(self._cta)
        self._wave = Waveform(bars=36, height=32, color=theme.ACCENT_ORANGE)
        self._wave.setFixedWidth(200)
        self._wave.hide()
        row.addWidget(self._wave)
        row.addStretch()
        col.addLayout(row)

        state.model_changed.connect(self.refresh)
        state.recording_changed.connect(lambda _s: self.refresh())
        state.hotkey_changed.connect(lambda _k: self.refresh())
        state.level_changed.connect(self._on_level)
        self.refresh()

    def _on_level(self, rms: float) -> None:
        if self._state.recording == st.REC_RECORDING:
            self._wave.push(min(1.0, rms * 9.0))

    def refresh(self) -> None:
        s = self._state
        key = hotkey_text(s.hotkey)
        self._keycaps.set_combo(s.hotkey)
        show_keys, cta_text, dot, spin, live = True, "", theme.SUCCESS, False, False

        if s.recording == st.REC_RECORDING:
            eyebrow, title, sub = t("status.listening"), t("home.hero.rec.title"), t("home.hero.rec.sub").format(key=key)
            dot, live = theme.ACCENT_ORANGE, True
        elif s.recording == st.REC_TRANSCRIBING:
            eyebrow, title, sub = t("status.transcribing"), t("home.hero.trans.title"), t("home.hero.trans.sub")
            dot, spin, show_keys = theme.ACCENT_BLUE, True, False
        elif s.model_status == st.MODEL_READY:
            eyebrow, title, sub = t("status.ready"), t("home.hero.ready.title"), t("home.hero.ready.sub").format(key=key)
        elif s.model_status == st.MODEL_LOADING:
            eyebrow, title, sub = t("status.loading"), t("home.hero.loading.title"), t("home.hero.loading.sub")
            dot, spin, show_keys = theme.WARNING, True, False
        elif s.model_status == st.MODEL_ERROR:
            eyebrow, title, sub = t("status.error"), t("home.hero.error.title"), s.model_error or ""
            dot, show_keys = theme.ERROR, False
            cta_text = t("home.hero.error.cta")
        else:
            eyebrow, title, sub = t("status.setup"), t("home.hero.nomodel.title"), t("home.hero.nomodel.sub")
            dot, show_keys = theme.WARNING, False
            cta_text = t("home.hero.nomodel.cta")

        self._eyebrow.setText(eyebrow.upper().rstrip("…").rstrip("."))
        self._dot.setStyleSheet(f"color: {dot}; font-size: 9px; background: transparent;")
        self._title.setText(title)
        self._sub.setText(sub)
        self._keycaps.setVisible(show_keys)
        self._cta.setVisible(bool(cta_text))
        if cta_text:
            self._cta.setText(cta_text)
            self._cta.setFixedWidth(QFontMetrics(self._cta.font()).horizontalAdvance(cta_text) + 40)
        self._spinner.setVisible(spin)
        self._wave.setVisible(live)
        self._wave.set_idle(not live)


# ── ledger ───────────────────────────────────────────────────────────────

class _Stat(QWidget):
    """One figure in the ledger: small mono caption over a serif number.
    A hairline separates it from its left neighbour."""

    def __init__(self, label: str, tooltip: str = "", first: bool = False) -> None:
        super().__init__()
        self._first = first
        self.setMinimumHeight(96)
        if tooltip:
            self.setToolTip(tooltip)
        ly = QVBoxLayout(self)
        ly.setContentsMargins(0 if first else 22, 14, 8, 12)
        ly.setSpacing(6)
        self._label = QLabel(label.upper())
        lf = theme.font_mono(10, bold=True)
        lf.setLetterSpacing(lf.SpacingType.AbsoluteSpacing, 0.9)
        self._label.setFont(lf)
        self._label.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent;")
        ly.addWidget(self._label)
        self._value = QLabel("0")
        self._value.setFont(theme.font_serif(34, bold=False))
        self._value.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        ly.addWidget(self._value)
        ly.addStretch()

    def set_label(self, text: str) -> None:
        self._label.setText(text.upper())

    def set_value(self, target: float, fmt=None) -> None:
        self._value.setText(fmt(target) if fmt else f"{int(round(target)):,}")

    def paintEvent(self, ev) -> None:
        if not self._first:
            p = QPainter(self)
            p.setPen(QPen(theme._BORDER_SUBTLE_C, 1))
            p.drawLine(0, 12, 0, self.height() - 12)
            p.end()


# ── permissions banner ───────────────────────────────────────────────────

class _PermBanner(theme.Card):
    """Pale amber strip shown while a system permission blocks dictation."""

    fix_clicked = Signal(str)   # "mic" | "acc"

    def __init__(self) -> None:
        super().__init__(radius=8, accent=theme.WARNING)
        self._which = "mic"
        ly = QHBoxLayout(self)
        ly.setContentsMargins(46, 12, 14, 12)
        ly.setSpacing(12)
        self._text = QLabel("")
        self._text.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; background: transparent;")
        self._text.setWordWrap(True)
        ly.addWidget(self._text, stretch=1)
        self._btn = theme.make_button(t("home.perm.fix"), "primary", 30, font_px=12)
        self._btn.setFixedWidth(84)
        self._btn.clicked.connect(lambda: self.fix_clicked.emit(self._which))
        ly.addWidget(self._btn)

    def show_for(self, which: str, text: str) -> None:
        self._which = which
        self._text.setText(text)
        self._btn.setText(t("home.perm.fix"))

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        paint_icon(p, "alert", QRectF(16, (self.height() - 18) / 2, 18, 18), theme.WARNING, 2.2)
        p.end()


# ── history ──────────────────────────────────────────────────────────────

class _DayHeader(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text.upper())
        f = theme.font_mono(10, bold=True)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.0)
        self.setFont(f)
        self.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent;")
        self.setContentsMargins(0, 22, 0, 8)     # margins, not QSS padding (padding shifts serif/mono text)


class _HistoryRow(QWidget):
    """A transcript as a ruled row: time on the left, text in the middle,
    quiet actions on the right, hairline underneath."""

    copy_requested = Signal(str)
    delete_requested = Signal(str)   # entry id

    def __init__(self, entry) -> None:
        super().__init__()
        self._entry = entry
        self._expanded = False
        self._hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 14, 4, 15)
        row.setSpacing(18)

        meta = QVBoxLayout()
        meta.setSpacing(3)
        meta.setContentsMargins(0, 1, 0, 0)
        ts = datetime.datetime.fromtimestamp(entry.timestamp)
        time_lbl = QLabel(ts.strftime("%H:%M"))
        time_lbl.setFont(theme.font_mono(12, bold=True))
        time_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent;")
        meta.addWidget(time_lbl)
        dur = entry.duration_secs
        dur_lbl = QLabel(f"{int(dur // 60)}m {int(dur % 60)}s" if dur >= 60 else f"{dur:.1f}s")
        dur_lbl.setFont(theme.font_mono(11))
        dur_lbl.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent;")
        meta.addWidget(dur_lbl)
        meta.addStretch()
        meta_w = QWidget()
        meta_w.setFixedWidth(56)
        meta_w.setStyleSheet("background: transparent;")
        meta_w.setLayout(meta)
        row.addWidget(meta_w, alignment=Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(6)
        body.setContentsMargins(0, 0, 0, 0)
        self._text = QLabel("")
        self._text.setWordWrap(True)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 15px; background: transparent;")
        self._text.setCursor(Qt.CursorShape.IBeamCursor)
        body.addWidget(self._text)

        self._more = QPushButton("")
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.setFlat(True)
        self._more.setStyleSheet(
            f"QPushButton {{ color: {theme.ACCENT_ORANGE}; background: transparent;"
            " border: none; font-size: 12px; font-weight: 600; text-align: left; padding: 0; }"
            f"QPushButton:hover {{ color: {theme.ACCENT_ORANGE_HOVER}; }}"
        )
        self._more.clicked.connect(self._toggle)
        body.addWidget(self._more, alignment=Qt.AlignmentFlag.AlignLeft)

        model = (entry.model or "").strip()
        if model and model != "unknown":
            m_lbl = QLabel(model)
            m_lbl.setFont(theme.font_mono(11))
            m_lbl.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent;")
            body.addWidget(m_lbl)

        if entry.translation:
            tr_row = QHBoxLayout()
            tr_row.setContentsMargins(0, 4, 0, 0)
            tr_row.setSpacing(10)
            arrow = QLabel(f"→ {entry.translation_lang or ''}".strip())
            arrow.setFont(theme.font_mono(11, bold=True))
            arrow.setStyleSheet(f"color: {theme.ACCENT_ORANGE}; background: transparent;")
            tr_row.addWidget(arrow, alignment=Qt.AlignmentFlag.AlignTop)
            tr = QLabel(entry.translation)
            tr.setWordWrap(True)
            tr.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            tr.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 14px; background: transparent;")
            tr_row.addWidget(tr, stretch=1)
            tr_copy = IconButton("copy", 26, t("home.copy_translation"))
            tr_copy.clicked.connect(lambda: self.copy_requested.emit(entry.translation))
            tr_row.addWidget(tr_copy, alignment=Qt.AlignmentFlag.AlignTop)
            body.addLayout(tr_row)
        row.addLayout(body, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(0)
        self._copy_btn = IconButton("copy", 30, t("home.copy"))
        self._copy_btn.clicked.connect(lambda: self.copy_requested.emit(entry.text))
        actions.addWidget(self._copy_btn)
        self._del_btn = IconButton("trash", 30, t("home.delete"), danger=True)
        self._del_btn.clicked.connect(lambda: self.delete_requested.emit(entry.id))
        actions.addWidget(self._del_btn)
        act_w = QWidget()
        act_w.setStyleSheet("background: transparent;")
        act_w.setLayout(actions)
        row.addWidget(act_w, alignment=Qt.AlignmentFlag.AlignTop)

        self._apply_text()

    def _apply_text(self) -> None:
        full = self._entry.text
        long = len(full) > _CLAMP_CHARS
        if long and not self._expanded:
            self._text.setText(full[:_CLAMP_CHARS].rstrip() + "…")
        else:
            self._text.setText(full)
        self._more.setVisible(long)
        self._more.setText(t("home.show_less") if self._expanded else t("home.show_more"))

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._apply_text()

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
        if self._hover:
            p.fillRect(self.rect().adjusted(-8, 0, 8, -1), QColor(31, 30, 27, 8))
        p.setPen(QPen(theme._BORDER_SUBTLE_C, 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


class _EmptyState(QWidget):
    """Plain typographic empty state."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(190)
        self._title = t("home.empty.title")
        self._sub = t("home.empty.sub")

    def set_texts(self, title: str, sub: str) -> None:
        self._title, self._sub = title, sub
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setFont(theme.font_serif(20))
        p.setPen(QColor(theme.TEXT_PRIMARY))
        p.drawText(QRectF(0, 56, self.width(), 32), Qt.AlignmentFlag.AlignCenter, self._title)
        p.setFont(theme.font(13))
        p.setPen(QColor(theme.TEXT_MUTED))
        p.drawText(QRectF(0, 92, self.width(), 24), Qt.AlignmentFlag.AlignCenter, self._sub)
        p.end()


# ── page ─────────────────────────────────────────────────────────────────

class HomePage(QWidget):
    navigate_requested = Signal(str)
    copied = Signal()
    deleted = Signal()

    def __init__(self, history: "HistoryStore", state: "AppState") -> None:
        super().__init__()
        self._history = history
        self._state = state
        self._query = ""
        self._limit = _PAGE_SIZE

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, self._root = column_scroll(spacing=14)
        outer.addWidget(scroll)

        self._hero = _Hero(state)
        self._hero.action_clicked.connect(lambda: self.navigate_requested.emit("models"))
        self._root.addWidget(self._hero)

        self._perm = _PermBanner()
        self._perm.fix_clicked.connect(self._fix_permission)
        self._perm.hide()
        self._root.addWidget(self._perm)

        # Ledger of figures, ruled above and below.
        self._root.addSpacing(10)
        self._root.addWidget(_Hairline())
        ledger = QHBoxLayout()
        ledger.setSpacing(0)
        self._stat_words = _Stat(t("home.words"), first=True)
        self._stat_time = _Stat(t("home.speaking_time"))
        self._stat_saved = _Stat(t("home.time_saved"), t("home.time_saved.tip"))
        self._stat_sessions = _Stat(t("home.sessions"))
        for c in (self._stat_words, self._stat_time, self._stat_saved, self._stat_sessions):
            ledger.addWidget(c, stretch=1)
        self._root.addLayout(ledger)
        self._root.addWidget(_Hairline())

        # History header
        self._root.addSpacing(18)
        head = QHBoxLayout()
        head.setSpacing(10)
        self._recent_title = QLabel(t("home.recent"))
        self._recent_title.setFont(theme.font_serif(22))
        self._recent_title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        self._recent_title.setContentsMargins(0, 0, 0, 4)
        head.addWidget(self._recent_title)
        head.addStretch()
        self._search = SearchField(t("home.search"))
        self._search.setFixedWidth(240)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(140)
        self._search_timer.timeout.connect(self._apply_search)
        self._search.textChanged.connect(lambda _t: self._search_timer.start())
        head.addWidget(self._search)
        self._clear_btn = theme.make_button(t("home.clear"), "ghost", 32, font_px=12)
        self._clear_btn.clicked.connect(self._on_clear)
        head.addWidget(self._clear_btn)
        self._root.addLayout(head)

        self._list = QVBoxLayout()
        self._list.setSpacing(0)
        self._root.addLayout(self._list)
        self._root.addStretch()

        # Permission polling only while this page is on screen.
        self._perm_timer = QTimer(self)
        self._perm_timer.setInterval(2500)
        self._perm_timer.timeout.connect(self._state.refresh_permissions)
        self._state.permissions_changed.connect(self._update_perm_banner)

        self.refresh()

    # ── lifecycle ──
    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._state.refresh_permissions()
        self._update_perm_banner()
        self._perm_timer.start()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._perm_timer.stop()

    # ── permissions ──
    def _update_perm_banner(self) -> None:
        s = self._state
        if s.mic_status in ("denied", "restricted"):
            self._perm.show_for("mic", t("home.perm.mic"))
            self._perm.show()
        elif s.mic_status == "not_determined":
            self._perm.show_for("mic_prompt", t("home.perm.mic_prompt"))
            self._perm.show()
        elif not s.accessibility_ok:
            self._perm.show_for("acc", t("home.perm.acc"))
            self._perm.show()
        else:
            self._perm.hide()

    def _fix_permission(self, which: str) -> None:
        from thundertalk.core import platform_utils as pu
        if which == "mic_prompt":
            pu.request_microphone()
        elif which == "mic":
            pu.open_microphone_settings()
        else:
            pu.request_accessibility()
            pu.open_accessibility_settings()

    # ── data ──
    def refresh(self) -> None:
        h = self._history
        units = h.total_units
        speak = h.total_duration_secs
        typing_min = units / _TYPING_UNITS_PER_MIN
        saved_secs = max(0.0, typing_min * 60 - speak)

        self._stat_words.set_value(units)
        self._stat_time.set_value(speak, _fmt_duration)
        self._stat_saved.set_value(saved_secs, _fmt_duration)
        self._stat_sessions.set_value(h.session_count)
        self._rebuild_list()

    def _matches(self, entry) -> bool:
        q = self._query
        if not q:
            return True
        return q in entry.text.lower() or q in (entry.translation or "").lower()

    def _rebuild_list(self) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        entries = [e for e in self._history.entries if self._matches(e)]
        self._clear_btn.setVisible(self._history.session_count > 0)
        self._search.setVisible(self._history.session_count > 0)

        if self._history.session_count == 0:
            self._list.addWidget(_EmptyState())
            return
        if not entries:
            empty = _EmptyState()
            empty.set_texts(t("home.no_results").format(q=self._search.text().strip()), "")
            self._list.addWidget(empty)
            return

        current_day: Optional[datetime.date] = None
        for entry in entries[: self._limit]:
            day = datetime.datetime.fromtimestamp(entry.timestamp).date()
            if day != current_day:
                current_day = day
                self._list.addWidget(_DayHeader(_day_label(day)))
            row = _HistoryRow(entry)
            row.copy_requested.connect(self._copy)
            row.delete_requested.connect(self._delete)
            self._list.addWidget(row)

        remaining = len(entries) - self._limit
        if remaining > 0:
            self._list.addSpacing(14)
            more = theme.make_button(f"{t('home.show_more')} ({remaining})", "secondary", 34, font_px=12)
            more.clicked.connect(self._show_more)
            self._list.addWidget(more, alignment=Qt.AlignmentFlag.AlignHCenter)

    def _show_more(self) -> None:
        self._limit += _PAGE_SIZE
        self._rebuild_list()

    def _apply_search(self) -> None:
        self._query = self._search.text().strip().lower()
        self._limit = _PAGE_SIZE
        self._rebuild_list()

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.copied.emit()

    def _delete(self, entry_id: str) -> None:
        from thundertalk.ui.styled_dialog import StyledDialog
        if not StyledDialog.confirm(
            self.window(),
            title=t("home.delete.confirm_title"),
            body=t("home.delete.confirm_body"),
            accept_label=t("home.delete"),
            cancel_label=t("home.clear.confirm_cancel"),
            destructive=True,
        ):
            return
        if self._history.remove(entry_id):
            self.refresh()
            self.deleted.emit()

    def _on_clear(self) -> None:
        from thundertalk.ui.styled_dialog import StyledDialog
        confirmed = StyledDialog.confirm(
            self.window(),
            title=t("home.clear.confirm_title"),
            body=t("home.clear.confirm_body"),
            accept_label=t("home.clear.confirm_yes"),
            cancel_label=t("home.clear.confirm_cancel"),
            destructive=True,
        )
        if not confirmed:
            return
        self._history.clear()
        self.refresh()

    def retranslate(self) -> None:
        self._stat_words.set_label(t("home.words"))
        self._stat_time.set_label(t("home.speaking_time"))
        self._stat_saved.set_label(t("home.time_saved"))
        self._stat_saved.setToolTip(t("home.time_saved.tip"))
        self._stat_sessions.set_label(t("home.sessions"))
        self._recent_title.setText(t("home.recent"))
        self._clear_btn.setText(t("home.clear"))
        self._search.setPlaceholderText(t("home.search"))
        self._hero.refresh()
        self._update_perm_banner()
        self.refresh()
