"""First-run setup flow: welcome → permissions → model → try it.

A full-window overlay (child of the main window's central widget). Every
step reflects the real system state — permissions are polled live, the model
step drives the real downloader — so nothing here is a mock-up.

Emits ``finished(completed)``; the caller persists ``onboarding_done``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import state as st
from thundertalk.core.i18n import t
from thundertalk.core.models import BUILTIN_MODELS, is_downloaded
from thundertalk.ui import theme
from thundertalk.ui.icons import paint_icon
from thundertalk.ui.keys import display_combo
from thundertalk.ui.widgets import BrandMark, KeyCaps, ThinProgress, Waveform, paint_canvas

if TYPE_CHECKING:
    from thundertalk.core.settings import Settings
    from thundertalk.core.state import AppState
    from thundertalk.ui.pages.models_page import ModelsPage

_STEPS = 4


def _fmt_size(mb: int) -> str:
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


def recommended_model(hardware=None):
    """Qwen3-ASR-0.6B ONNX int8 on every Mac: as accurate as the MLX build,
    faster, CPU-only and half the download (879 MB vs 1.9 GB)."""
    return next(m for m in BUILTIN_MODELS if m.id == "qwen3-asr-06b-int8")


def _download_failure_key(message: str) -> str:
    message = message.lower()
    if any(part in message for part in ("no space left", "disk full", "errno 28", "curl: (23)")):
        return "onb.model.disk_full"
    return "onb.model.failed"


# ── small pieces ─────────────────────────────────────────────────────────

class _StepDots(QWidget):
    """Four short rules (done = ink, current = ink, ahead = hairline) and a
    mono "02 / 04" counter."""

    def __init__(self) -> None:
        super().__init__()
        self._cur = 0
        self.setFixedHeight(16)
        self.setFixedWidth(_STEPS * 30 + 78)

    def set_current(self, i: int) -> None:
        self._cur = i
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        x = 0
        for i in range(_STEPS):
            c = QColor(theme.INK) if i <= self._cur else QColor(31, 30, 27, 40)
            p.fillRect(QRectF(x, 6, 24, 2.5), c)
            x += 30
        p.setFont(theme.font_mono(11, bold=True))
        p.setPen(QColor(theme.TEXT_MUTED))
        p.drawText(QRectF(x + 6, 0, 70, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"{self._cur + 1:02d} / {_STEPS:02d}")
        p.end()


class _Sheet(QWidget):
    """The content column. Deliberately unpainted — the page is the paper."""

    def paintEvent(self, ev) -> None:
        return


class _Feature(QWidget):
    """A numbered point with a hairline underneath: 01  Title / sentence."""

    def __init__(self, index: str, title: str, sub: str) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 14, 0, 14)
        row.setSpacing(18)
        num = QLabel(index)
        num.setFont(theme.font_mono(12, bold=True))
        num.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; background: transparent;")
        num.setFixedWidth(28)
        row.addWidget(num, alignment=Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        self._a = QLabel(title)
        self._a.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 700; background: transparent;")
        self._b = QLabel(sub)
        self._b.setWordWrap(True)
        self._b.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px; background: transparent;")
        col.addWidget(self._a)
        col.addWidget(self._b)
        row.addLayout(col, stretch=1)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setPen(QPen(theme._BORDER_SUBTLE_C, 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


class _PermRow(theme.Card):
    action = Signal()
    reset = Signal()

    def __init__(self, icon: str, title: str, why: str) -> None:
        super().__init__(radius=8)
        self._icon = icon
        self.setFixedHeight(76)
        ly = QHBoxLayout(self)
        ly.setContentsMargins(60, 12, 16, 12)
        ly.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(3)
        self._title = QLabel(title)
        self._title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 700; background: transparent;")
        self._why = QLabel(why)
        self._why.setWordWrap(True)
        self._why.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        col.addWidget(self._title)
        col.addWidget(self._why)
        ly.addLayout(col, stretch=1)

        self._granted = theme.badge(t("onb.perm.granted"), "green", upper=True)
        ly.addWidget(self._granted)
        self._btn = theme.make_button("", "primary", 32, font_px=12)
        self._btn.setMinimumWidth(120)
        self._btn.clicked.connect(self.action)
        ly.addWidget(self._btn)
        self._reset_btn = theme.make_button(t("onb.perm.reset"), "secondary", 32, font_px=12)
        self._reset_btn.clicked.connect(self.reset)
        ly.addWidget(self._reset_btn)
        self._reset_btn.hide()
        self._is_granted = False
        self.set_state(False, t("onb.perm.allow"))

    def set_state(self, granted: bool, button_text: str) -> None:
        self._is_granted = granted
        self._granted.setVisible(granted)
        self._btn.setVisible(not granted)
        self._btn.setText(button_text)
        self.update()

    def retranslate(self, title: str, why: str) -> None:
        self._granted.setText(t("onb.perm.granted"))
        self._reset_btn.setText(t("onb.perm.reset"))
        self._title.setText(title)
        self._why.setText(why)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(theme.SUCCESS if self._is_granted else theme.TEXT_PRIMARY)
        paint_icon(p, self._icon, QRectF(20, (self.height() - 24) / 2, 24, 24), col, 1.9)
        p.end()


def _title(text: str, size: int = 28) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(theme.font_display(size))
    lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
    lbl.setContentsMargins(0, 2, 0, 10)
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
    lbl.setWordWrap(True)
    return lbl


def _sub(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 15px; background: transparent;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
    lbl.setWordWrap(True)
    return lbl


# ── overlay ──────────────────────────────────────────────────────────────

class OnboardingOverlay(QWidget):
    finished = Signal(bool)            # completed?
    navigate_requested = Signal(str)

    def __init__(self, parent: QWidget, settings: "Settings", state: "AppState",
                 models_page: Optional["ModelsPage"] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._state = state
        self._models = models_page
        self._step = 0
        self._hardware = getattr(models_page, "hardware", None)
        self._rec = recommended_model(self._hardware)
        self._failure_key = "onb.model.failed"
        self._trial_succeeded = False
        self._dl_phase = "idle"          # idle | downloading | cancelling | failed
        self._acc_prompted = False
        self._closing = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 56, 40, 40)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self._sheet = _Sheet()
        self._sheet.setFixedSize(620, 600)
        row.addWidget(self._sheet)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        sl = QVBoxLayout(self._sheet)
        sl.setContentsMargins(0, 8, 0, 8)
        sl.setSpacing(0)

        top = QHBoxLayout()
        self._dots = _StepDots()
        top.addWidget(self._dots)
        top.addStretch()
        sl.addLayout(top)
        sl.addSpacing(26)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        self._stack.addWidget(self._build_welcome())
        self._stack.addWidget(self._build_permissions())
        self._stack.addWidget(self._build_model())
        self._stack.addWidget(self._build_try())
        sl.addWidget(self._stack, stretch=1)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        self._back = theme.make_button(t("onb.back"), "ghost", 40)
        self._back.clicked.connect(self._on_back)
        foot.addWidget(self._back)
        self._skip = theme.make_button(t("onb.skip"), "ghost", 40)
        self._skip.clicked.connect(lambda: self._finish(False))
        foot.addWidget(self._skip)
        foot.addStretch()
        self._next = theme.make_button(t("onb.start"), "primary", 42, font_px=14)
        self._next.setMinimumWidth(160)
        self._next.clicked.connect(self._on_next)
        foot.addWidget(self._next)
        sl.addSpacing(10)
        sl.addLayout(foot)

        # Live wiring
        self._perm_timer = QTimer(self)
        self._perm_timer.setInterval(900)
        self._perm_timer.timeout.connect(self._state.refresh_permissions)
        self._state.permissions_changed.connect(self._refresh_permissions)
        self._state.model_changed.connect(self._refresh_model)
        self._state.recording_changed.connect(self._refresh_try_status)
        self._state.level_changed.connect(self._on_level)
        if self._models is not None:
            self._models.download_progress.connect(self._on_dl_progress)
            self._models.model_download_completed.connect(self._on_dl_done)
            self._models.download_failed.connect(self._on_dl_failed)
            self._models.download_cancelled.connect(self._on_dl_cancelled)
            self._models.hardware_detected.connect(self._on_hardware)

        self._refresh_recommendation()
        self._refresh_model()
        self._go(0, animate=False)

    # ── painting ──
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        paint_canvas(p, QRectF(self.rect()))
        p.end()

    def mousePressEvent(self, ev) -> None:      # swallow clicks on the backdrop
        ev.accept()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(ev)

    # ── steps ──
    def _build_welcome(self) -> QWidget:
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(6)
        ly.addWidget(BrandMark(44), alignment=Qt.AlignmentFlag.AlignLeft)
        ly.addSpacing(14)
        self._w_title = _title(t("onb.welcome.title"), 42)
        ly.addWidget(self._w_title)
        self._w_sub = _sub(t("onb.welcome.sub"))
        ly.addWidget(self._w_sub)
        ly.addSpacing(24)
        self._feats = [
            _Feature("01", t("onb.feat.private.title"), t("onb.feat.private.sub")),
            _Feature("02", t("onb.feat.fast.title"), t("onb.feat.fast.sub")),
            _Feature("03", t("onb.feat.anywhere.title"), t("onb.feat.anywhere.sub")),
        ]
        for f in self._feats:
            ly.addWidget(f)
        self._w_download = _sub("")
        ly.addWidget(self._w_download)
        ly.addStretch()
        return w

    def _build_permissions(self) -> QWidget:
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.setContentsMargins(0, 8, 0, 0)
        ly.setSpacing(10)
        self._p_title = _title(t("onb.perm.title"), 32)
        ly.addWidget(self._p_title)
        self._p_sub = _sub(t("onb.perm.sub"))
        ly.addWidget(self._p_sub)
        ly.addSpacing(18)
        self._mic_row = _PermRow("mic", t("onb.perm.mic.title"), t("onb.perm.mic.why"))
        self._mic_row.action.connect(self._on_mic_action)
        self._mic_row.reset.connect(self._on_mic_reset)
        ly.addWidget(self._mic_row)
        self._acc_row = _PermRow("keyboard", t("onb.perm.acc.title"), t("onb.perm.acc.why"))
        self._acc_row.action.connect(self._on_acc_action)
        self._acc_row.reset.connect(self._on_acc_reset)
        ly.addWidget(self._acc_row)
        ly.addSpacing(6)
        self._perm_hint = QLabel(t("onb.perm.hint"))
        self._perm_hint.setWordWrap(True)
        self._perm_hint.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._perm_hint.setStyleSheet(f"color: {theme.WARNING}; font-size: 12px; background: transparent;")
        self._perm_hint.hide()
        ly.addWidget(self._perm_hint)
        self._p_download = QLabel("")
        self._p_download.setWordWrap(True)
        self._p_download.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        ly.addWidget(self._p_download)
        self._p_cancel = theme.make_button(t("models.cancel"), "ghost", 30, font_px=12)
        self._p_cancel.clicked.connect(self._on_model_cancel)
        ly.addWidget(self._p_cancel, alignment=Qt.AlignmentFlag.AlignLeft)
        ly.addStretch()
        return w

    def _build_model(self) -> QWidget:
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.setContentsMargins(0, 8, 0, 0)
        ly.setSpacing(10)
        self._m_title = _title(t("onb.model.title"), 32)
        ly.addWidget(self._m_title)
        self._m_sub = _sub(t("onb.model.sub"))
        ly.addWidget(self._m_sub)
        ly.addSpacing(14)

        self._m_card = theme.Card(radius=10)
        self._m_card.setMinimumHeight(190)
        cl = QVBoxLayout(self._m_card)
        cl.setContentsMargins(24, 22, 24, 22)
        cl.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        name = self._m_name = QLabel(self._rec.name)
        name.setFont(theme.font_serif(22))
        name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        head.addWidget(name)
        self._m_variant = theme.badge(self._rec.variant, "blue")
        head.addWidget(self._m_variant)
        self._m_size = theme.badge(_fmt_size(self._rec.size_mb), "muted")
        head.addWidget(self._m_size)
        head.addStretch()
        cl.addLayout(head)
        from thundertalk.ui.pages.models_page import _blurb
        blurb = self._m_blurb = QLabel(f"{_blurb(self._rec)}  ·  {self._rec.language_count} {t('models.languages')}")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px; background: transparent;")
        cl.addWidget(blurb)
        cl.addStretch()

        self._m_status = QLabel("")
        self._m_status.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
        self._m_status.setWordWrap(True)
        cl.addWidget(self._m_status)
        self._m_progress = ThinProgress(4)
        cl.addWidget(self._m_progress)
        btns = QHBoxLayout()
        btns.setSpacing(10)
        self._m_action = theme.make_button("", "primary", 42, font_px=14)
        self._m_action.setMinimumWidth(200)
        self._m_action.clicked.connect(self._on_model_action)
        btns.addWidget(self._m_action)
        self._m_cancel = theme.make_button(t("models.cancel"), "ghost", 42)
        self._m_cancel.clicked.connect(self._on_model_cancel)
        btns.addWidget(self._m_cancel)
        btns.addStretch()
        self._m_other = theme.make_button(t("onb.model.other"), "ghost", 34, font_px=12)
        self._m_other.clicked.connect(self._on_other_model)
        btns.addWidget(self._m_other)
        cl.addLayout(btns)
        ly.addWidget(self._m_card)
        ly.addStretch()
        return w

    def _build_try(self) -> QWidget:
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.setContentsMargins(0, 8, 0, 0)
        ly.setSpacing(10)
        self._t_title = _title(t("onb.try.title"), 32)
        ly.addWidget(self._t_title)
        self._t_sub = _sub("")
        ly.addWidget(self._t_sub)
        ly.addSpacing(6)
        self._t_keys = KeyCaps(self._state.hotkey, height=38)
        ly.addWidget(self._t_keys, alignment=Qt.AlignmentFlag.AlignLeft)
        ly.addSpacing(4)

        self._t_edit = QTextEdit()
        self._t_edit.setPlaceholderText(t("onb.try.placeholder"))
        self._t_edit.setMinimumHeight(120)
        self._t_edit.setStyleSheet(
            f"QTextEdit {{ background: {theme.BG_CARD}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 8px; padding: 12px 14px;"
            f" font-size: 16px; selection-background-color: #F5D0B6; selection-color: {theme.TEXT_PRIMARY}; }}"
            f"QTextEdit:focus {{ border: 1px solid {theme.INK}; }}"
        )
        ly.addWidget(self._t_edit)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._t_wave = Waveform(bars=36, height=30, color=theme.ACCENT_ORANGE)
        self._t_wave.setFixedWidth(160)
        row.addWidget(self._t_wave)
        self._t_status = QLabel("")
        self._t_status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        row.addWidget(self._t_status, stretch=1)
        ly.addLayout(row)

        self._t_tip = QLabel(t("onb.try.tip"))
        self._t_tip.setWordWrap(True)
        self._t_tip.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._t_tip.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        ly.addWidget(self._t_tip)
        ly.addStretch()
        return w

    # ── navigation ──
    def _go(self, step: int, animate: bool = True) -> None:
        self._step = max(0, min(_STEPS - 1, step))
        self._stack.setCurrentIndex(self._step)
        self._dots.set_current(self._step)
        if animate:
            theme.fade_in(self._stack.currentWidget(), 220)
        self._back.setVisible(self._step > 0)
        self._skip.setVisible(self._step < _STEPS - 1)
        self._perm_timer.stop()
        if self._step in (1, 2, 3):
            self._state.refresh_permissions()
            self._refresh_permissions()
            self._perm_timer.start()
        if self._step == 2:
            self._enter_model()
        elif self._step == 3:
            self._t_sub.setText(t("onb.try.hold" if self._settings.get("press_mode") == "hold" else "onb.try.sub").format(key=display_combo(self._state.hotkey)))
            if self._state.hotkey == "cmd_r":
                self._t_sub.setText(self._t_sub.text() + " " + t("onb.try.right_cmd"))
            self._t_keys.set_combo(self._state.hotkey)
            self._refresh_try_status()
            self._t_edit.setFocus()
        self._update_next()

    def _on_back(self) -> None:
        self._go(self._step - 1)

    def _on_next(self) -> None:
        if self._step == _STEPS - 1:
            self._finish(True)
        elif self._step == 0:
            self._on_model_action()
            self._go(1)
        elif self._step == 2 and not self._state.permissions_ok:
            self._go(1)
        else:
            self._go(self._step + 1)

    def _update_next(self) -> None:
        s = self._step
        if s == 0:
            theme.restyle_button(self._next, "primary", 42, 14)
            self._next.setText(t("onb.start_download").format(size=_fmt_size(self._rec.size_mb))
                               if self._dl_phase == "idle" and not is_downloaded(self._rec.id) else t("onb.start"))
            self._next.setEnabled(True)
        elif s == 1:
            ok = self._state.permissions_ok
            theme.restyle_button(self._next, "primary" if ok else "secondary", 42, 14)
            self._next.setText(t("onb.next") if ok else t("onb.continue_anyway"))
            self._next.setEnabled(True)
        elif s == 2:
            ready = self._state.model_status == st.MODEL_READY
            theme.restyle_button(self._next, "primary", 42, 14)
            self._next.setText(t("onb.next" if self._state.permissions_ok else "onb.perm.review"))
            self._next.setEnabled(ready)
        else:
            theme.restyle_button(self._next, "primary", 42, 14)
            self._next.setText(t("onb.finish" if self._trial_succeeded else "onb.try.later"))
            self._next.setEnabled(self._state.recording == st.REC_IDLE)

    def _finish(self, completed: bool) -> None:
        if self._closing:
            return
        self._closing = True
        self._perm_timer.stop()
        if self._models is not None and self._dl_phase in ("downloading", "cancelling"):
            self._models.cancel_download(self._rec.id)
        self._settings.set("onboarding_done", True)
        self.finished.emit(completed)

    # ── permissions ──
    def _refresh_permissions(self) -> None:
        s = self._state
        mic = s.mic_status
        if mic == "authorized":
            self._mic_row.set_state(True, "")
        elif mic == "not_determined":
            self._mic_row.set_state(False, t("onb.perm.allow"))
        else:
            self._mic_row.set_state(False, t("onb.perm.open"))
        if s.accessibility_ok:
            self._acc_row.set_state(True, "")
        else:
            self._acc_row.set_state(False, t("onb.perm.allow"))
        self._mic_row._reset_btn.setVisible(mic == "denied")
        self._acc_row._reset_btn.setVisible(not s.accessibility_ok and self._acc_prompted)
        self._perm_hint.setVisible(not s.permissions_ok)
        if self._step in (1, 2, 3):
            self._update_next()
        if self._step == 3:
            self._refresh_try_status()

    def _on_mic_action(self) -> None:
        from thundertalk.core import platform_utils as pu
        if self._state.mic_status == "not_determined":
            pu.request_microphone()
        else:
            pu.open_microphone_settings()
        self._perm_hint.show()

    def _on_acc_action(self) -> None:
        from thundertalk.core import platform_utils as pu
        self._acc_prompted = True
        pu.request_accessibility()
        pu.open_accessibility_settings()
        self._refresh_permissions()
        self._perm_hint.show()

    def _on_mic_reset(self) -> None:
        from thundertalk.core import platform_utils as pu
        self._reset_result(pu.reset_microphone())

    def _on_acc_reset(self) -> None:
        from thundertalk.core import platform_utils as pu
        self._reset_result(pu.reset_accessibility())

    def _reset_result(self, ok: bool) -> None:
        self._perm_hint.setText(t("onb.perm.hint" if ok else "onb.perm.reset_failed"))
        self._state.refresh_permissions()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._step in (1, 2, 3) and not self._closing:
            self._state.refresh_permissions()
            self._perm_timer.start()

    def hideEvent(self, event) -> None:
        self._perm_timer.stop()
        super().hideEvent(event)

    # ── model ──
    def _on_hardware(self, hardware) -> None:
        if self._step != 0 or self._dl_phase != "idle":
            return  # Never switch the model after consent or during a transfer.
        self._hardware = hardware
        self._rec = recommended_model(hardware)
        self._refresh_recommendation()
        self._update_next()

    def _refresh_recommendation(self) -> None:
        from thundertalk.ui.pages.models_page import _blurb
        rec = self._rec
        reason = t("onb.model.gpu" if rec.backend == "mlx" else "onb.model.cpu")
        minutes = max(1, round(rec.size_mb / 600))
        detail = t("onb.model.estimate").format(size=_fmt_size(rec.size_mb), minutes=minutes, upper=minutes + 1)
        self._w_download.setText(t("onb.welcome.download").format(name=rec.name, detail=detail))
        self._m_sub.setText(reason + " " + detail)
        self._m_name.setText(rec.name)
        self._m_variant.setText(rec.variant)
        self._m_size.setText(_fmt_size(rec.size_mb))
        self._m_blurb.setText(f"{_blurb(rec)} · {rec.language_count} {t('models.languages')}")

    def _enter_model(self) -> None:
        self._refresh_model()

    def _refresh_model(self) -> None:
        if self._closing:
            return
        s = self._state
        rec = self._rec
        self._m_cancel.setVisible(self._dl_phase in ("downloading", "cancelling"))
        self._m_cancel.setEnabled(self._dl_phase != "cancelling")
        self._m_other.setVisible(self._dl_phase not in ("downloading", "cancelling") and s.model_status != st.MODEL_READY)
        if s.model_status == st.MODEL_READY:
            self._m_status.setText("✓ " + t("onb.model.ready") + (f" — {s.model_name}" if s.model_name else ""))
            self._m_status.setStyleSheet(f"color: {theme.SUCCESS}; font-size: 13px; font-weight: 600; background: transparent;")
            self._m_progress.hide()
            self._m_action.hide()
        elif s.model_status == st.MODEL_LOADING:
            self._m_status.setText(t("onb.model.loading"))
            self._m_status.setStyleSheet(f"color: {theme.ACCENT_ORANGE_HOVER}; font-size: 12px; background: transparent;")
            self._m_progress.show()
            self._m_progress.set_indeterminate(True)
            self._m_action.hide()
        elif self._dl_phase in ("downloading", "cancelling"):
            self._m_progress.show()
            self._m_action.hide()
        elif s.model_status == st.MODEL_ERROR or self._dl_phase == "failed":
            self._m_status.setText(t("onb.model.load_failed" if s.model_status == st.MODEL_ERROR else self._failure_key))
            self._m_status.setStyleSheet(f"color: {theme.ERROR}; font-size: 12px; background: transparent;")
            self._m_progress.hide()
            self._m_action.show()
            self._m_action.setText(t("onb.model.retry"))
        else:
            self._m_status.setText("")
            self._m_progress.hide()
            self._m_action.show()
            if is_downloaded(rec.id):
                self._m_action.setText(t("onb.model.use"))
            else:
                self._m_action.setText(t("onb.model.download").format(size=_fmt_size(rec.size_mb)))
        self._p_download.setText(self._m_status.text())
        self._p_cancel.setVisible(self._dl_phase in ("downloading", "cancelling"))
        self._p_cancel.setEnabled(self._dl_phase != "cancelling")
        self._update_next()

    def _on_model_action(self) -> None:
        if self._models is None:
            return
        rec = self._rec
        if self._dl_phase in ("downloading", "cancelling"):
            return
        if rec.backend == "onnx" and self._hardware and 0 < self._hardware.memory_gb < 16:
            self._settings.set("memory_mode", "low")
        if is_downloaded(rec.id):
            self._models.activate_model(rec.id)
            return
        self._dl_phase = "downloading"
        self._m_status.setText(t("models.connecting"))
        self._m_status.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
        self._m_progress.show()
        self._m_progress.set_indeterminate(True)
        self._models.start_download(rec.id)
        self._refresh_model()

    def _on_model_cancel(self) -> None:
        if self._models is not None:
            self._models.cancel_download(self._rec.id)
        self._dl_phase = "cancelling"
        self._m_status.setText(t("models.cancelling"))
        self._refresh_model()

    def _on_other_model(self) -> None:
        self._finish(False)
        self.navigate_requested.emit("models")

    def _on_dl_progress(self, model_id: str, pct: int, msg: str) -> None:
        if model_id != self._rec.id or self._dl_phase != "downloading":
            return
        if msg == "Done":
            msg = t("onb.model.download_complete")
        elif msg.startswith(("Extracting", "Connecting", "Starting")):
            msg = t("models.extracting" if msg.startswith("Extracting") else "models.connecting")
        if pct < 0:
            self._m_progress.set_indeterminate(True)
            self._m_status.setText(msg)
        else:
            self._m_progress.set_indeterminate(False)
            self._m_progress.set_value(pct)
            self._m_status.setText(f"{msg} · {pct}%")
        self._p_download.setText(self._m_status.text())

    def _on_dl_done(self, model_id: str) -> None:
        if model_id != self._rec.id:
            return
        self._dl_phase = "idle"
        self._refresh_model()

    def _on_dl_cancelled(self, model_id: str) -> None:
        if model_id == self._rec.id:
            self._dl_phase = "idle"
            self._refresh_model()

    def _on_dl_failed(self, model_id: str, _msg: str) -> None:
        if model_id != self._rec.id:
            return
        self._failure_key = _download_failure_key(_msg)
        self._dl_phase = "failed"
        self._refresh_model()

    # ── try it ──
    def _refresh_try_status(self, *_a) -> None:
        r = self._state.recording
        if r == st.REC_RECORDING:
            self._t_status.setText(t("status.listening"))
            self._t_status.setStyleSheet(f"color: {theme.ACCENT_ORANGE_HOVER}; font-size: 13px; font-weight: 600; background: transparent;")
            self._t_wave.set_idle(False)
        elif r == st.REC_TRANSCRIBING:
            self._t_status.setText(t("status.transcribing"))
            self._t_status.setStyleSheet(f"color: {theme.ACCENT_BLUE}; font-size: 13px; font-weight: 600; background: transparent;")
            self._t_wave.set_idle(True)
        else:
            if not self._state.permissions_ok:
                self._t_status.setText(t("onb.perm.review_hint"))
            elif self._trial_succeeded:
                self._t_status.setText("✓ " + t("onb.try.success"))
            else:
                self._t_status.setText(t("onb.try.ready" if self._state.permissions_ok else "onb.perm.review_hint"))
            self._t_wave.set_idle(True)
        if self._step == 3:
            self._update_next()

    def _on_level(self, rms: float) -> None:
        if self._step == 3 and self._state.recording == st.REC_RECORDING:
            self._t_wave.push(min(1.0, rms * 9.0))

    def accept_dictation(self, text: str) -> bool:
        """Show only a real recognition result as success, without OS paste races."""
        if self._step != 3 or self._closing or not text.strip():
            return False
        self._trial_succeeded = True
        self._t_edit.setPlainText(text)
        self._t_status.setText("✓ " + t("onb.try.success"))
        self._t_status.setStyleSheet(f"color: {theme.SUCCESS}; font-size: 13px; font-weight: 600; background: transparent;")
        self._update_next()
        return True

    def retranslate(self) -> None:
        self._w_title.setText(t("onb.welcome.title"))
        self._w_sub.setText(t("onb.welcome.sub"))
        self._p_title.setText(t("onb.perm.title"))
        self._p_sub.setText(t("onb.perm.sub"))
        self._perm_hint.setText(t("onb.perm.hint"))
        self._mic_row.retranslate(t("onb.perm.mic.title"), t("onb.perm.mic.why"))
        self._acc_row.retranslate(t("onb.perm.acc.title"), t("onb.perm.acc.why"))
        self._m_title.setText(t("onb.model.title"))
        self._refresh_recommendation()
        self._t_title.setText(t("onb.try.title"))
        for feature, key in zip(self._feats, ("private", "fast", "anywhere")):
            feature._a.setText(t(f"onb.feat.{key}.title"))
            feature._b.setText(t(f"onb.feat.{key}.sub"))
        self._m_cancel.setText(t("models.cancel"))
        self._p_cancel.setText(t("models.cancel"))
        self._m_other.setText(t("onb.model.other"))
        self._t_edit.setPlaceholderText(t("onb.try.placeholder"))
        self._t_tip.setText(t("onb.try.tip"))
        self._back.setText(t("onb.back"))
        self._skip.setText(t("onb.skip"))
        self._go(self._step, animate=False)
