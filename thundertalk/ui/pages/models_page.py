"""Models page — pick, download, and activate speech models.

Every row is a small state machine (idle → downloading → loading → active)
so a click can never be ambiguous: downloads show real byte progress and can
be cancelled, activation shows a spinner, and a row that is mid-download
can't be started twice.
"""

from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.i18n import t
from thundertalk.core.models import (
    BUILTIN_MODELS,
    DownloadCancelled,
    ModelInfo,
    detect_hardware,
    download_model,
    get_families,
    get_model_path,
    get_recommended_id,
    is_downloaded,
    is_variant_compatible,
)
from thundertalk.ui import theme
from thundertalk.ui.icons import paint_icon
from thundertalk.ui.widgets import PageHeader, SegmentedControl, Spinner, StatusDot, ThinProgress, column_scroll

_FAMILY_COLORS = {
    "SenseVoice": theme.ACCENT_CYAN,
    "Qwen3-ASR": theme.ACCENT_BLUE,
    "Qwen3-ASR-1.7B": theme.ACCENT_BLUE,
    "Parakeet-TDT-v3": theme.SUCCESS,
    "Parakeet-TDT-v2": theme.SUCCESS,
    "MOSS-Transcribe-Diarize": theme.ACCENT_PURPLE,
    "SeamlessM4T-v2": theme.ACCENT_ORANGE,
}

_BEST_FAMILY = "Qwen3-ASR"
_BIG_DOWNLOAD_MB = 2000


def _fmt_size(mb: int) -> str:
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


def _blurb(info: ModelInfo) -> str:
    key = f"model.blurb.{info.id}"
    text = t(key)
    text = info.notes if text == key else text
    if info.backend == "seamless-torch":
        from thundertalk.core.runtime import status
        text += "  " + status()
    return text


# ── workers ──────────────────────────────────────────────────────────────

class DownloadWorker(QThread):
    progress = Signal(int, str)      # percent (-1 = unknown), message
    done = Signal(str)               # model_id
    error = Signal(str)
    cancelled = Signal()

    def __init__(self, info: ModelInfo) -> None:
        super().__init__()
        self._info = info
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            download_model(
                self._info,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                cancel=self._cancel,
            )
            self.done.emit(self._info.id)
        except DownloadCancelled:
            self.cancelled.emit()
        except Exception as e:
            self.error.emit(str(e))


class _HardwareWorker(QThread):
    """`system_profiler` takes ~1 s — keep it off the UI thread."""

    detected = Signal(object)

    def run(self) -> None:
        try:
            self.detected.emit(detect_hardware())
        except Exception:
            self.detected.emit(None)


# ── translation card ─────────────────────────────────────────────────────

# ISO-639-3 codes + display labels for the inline target picker.
TRANSLATION_TARGETS_NEW: list[tuple[str, str]] = [
    ("eng", "English"),
    ("cmn", "中文 (Chinese)"),
    ("jpn", "日本語 (Japanese)"),
    ("spa", "Español (Spanish)"),
    ("fra", "Français (French)"),
    ("deu", "Deutsch (German)"),
    ("por", "Português (Portuguese)"),
    ("rus", "Русский (Russian)"),
    ("ita", "Italiano (Italian)"),
    ("arb", "العربية (Arabic)"),
    ("hin", "हिन्दी (Hindi)"),
]


class TranslationModeCard(theme.Card):
    """Off / Direct / Review switch, target-language picker, and the status
    of the translation engine (missing → loading → ready / error).

    Settings semantics:
      - Mode = Off    → translation_target = "off"  (mode value preserved)
      - Mode = Direct → translation_mode = "direct", target = current lang
      - Mode = Review → translation_mode = "review", target = current lang
    """

    mode_changed = Signal(str)        # "off" | "direct" | "review"
    target_changed = Signal(str)      # ISO-639-3
    download_translator_clicked = Signal()

    @staticmethod
    def _modes() -> list[tuple[str, str]]:
        return [
            ("off", t("models.mode_off")),
            ("direct", t("models.mode_direct")),
            ("review", t("models.mode_review")),
        ]

    def __init__(self, settings) -> None:
        super().__init__(radius=theme.RADIUS_CARD)
        self._settings = settings

        ly = QVBoxLayout(self)
        ly.setContentsMargins(24, 20, 24, 22)
        ly.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._title_lbl = QLabel(t("models.translation"))
        self._title_lbl.setFont(theme.font_serif(18))
        self._title_lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        head.addWidget(self._title_lbl)
        head.addStretch()
        ly.addLayout(head)

        from thundertalk.core.runtime import status
        self._subtitle_lbl = QLabel(t("models.translation_subtitle") + "  " + status())
        self._subtitle_lbl.setWordWrap(True)
        self._subtitle_lbl.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        ly.addWidget(self._subtitle_lbl)
        ly.addSpacing(4)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        self._segment = SegmentedControl(self._modes(), "off")
        self._segment.changed.connect(self._on_mode_clicked)
        controls.addWidget(self._segment)
        controls.addStretch()

        self._target_combo = QComboBox()
        self._target_combo.setFixedHeight(34)
        self._target_combo.setMinimumWidth(180)
        theme.style_combo(self._target_combo)
        for code, display in TRANSLATION_TARGETS_NEW:
            self._target_combo.addItem(display, code)
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        controls.addWidget(self._target_combo)
        ly.addLayout(controls)

        self._warning = QLabel(t("models.review_needs_asr"))
        self._warning.setStyleSheet(
            f"color: {theme.WARNING}; font-size: 12px; background: transparent; padding-top: 2px;")
        self._warning.setWordWrap(True)
        self._warning.setMinimumWidth(0)
        self._warning.hide()
        ly.addWidget(self._warning)

        # Translator engine status
        self._status_row = QWidget()
        self._status_row.setStyleSheet("background: transparent;")
        sr = QHBoxLayout(self._status_row)
        sr.setContentsMargins(0, 4, 0, 0)
        sr.setSpacing(8)
        self._dot = StatusDot(theme.TEXT_MUTED, 8)
        sr.addWidget(self._dot)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
        self._status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sr.addWidget(self._status_label, stretch=1)
        self._action_btn = theme.make_button("", "secondary", 30, font_px=12)
        self._action_btn.clicked.connect(self.download_translator_clicked)
        self._action_btn.hide()
        sr.addWidget(self._action_btn)
        self._status_row.hide()
        ly.addWidget(self._status_row)

        self._restore_state()

    # ── public API ──
    def refresh_warning(self) -> None:
        mode = self._settings.translation_mode
        target = self._settings.translation_target
        active_id = self._settings.active_model_id
        is_review = (target != "off") and (mode == "review")
        is_asr_active = bool(active_id) and not active_id.startswith("seamless")
        self._warning.setVisible(is_review and not is_asr_active)

    def set_translator_status(self, state: str, message: str = "") -> None:
        """state ∈ {"hidden", "missing", "loading", "ready", "error"}"""
        from thundertalk.core.runtime import status
        self._subtitle_lbl.setText(t("models.translation_subtitle") + "  " + status())
        if state == "hidden":
            self._status_row.hide()
            return
        palette = {
            "missing": (theme.WARNING, False, t("models.translator.missing")),
            "loading": (theme.WARNING, True, t("models.translator.loading")),
            "ready":   (theme.SUCCESS, False, t("models.translator.ready")),
            "error":   (theme.ERROR, False, message or t("models.translator.error")),
        }
        color, pulse, default_msg = palette.get(state, (theme.TEXT_MUTED, False, ""))
        self._dot.set_state(color, pulse)
        self._status_label.setText(message or default_msg)
        if state == "missing":
            self._action_btn.setText(t("models.btn.download"))
            self._action_btn.setFixedWidth(96)
            self._action_btn.show()
        else:
            self._action_btn.hide()
        self._status_row.show()

    def retranslate(self) -> None:
        self._title_lbl.setText(t("models.translation"))
        from thundertalk.core.runtime import status
        self._subtitle_lbl.setText(t("models.translation_subtitle") + "  " + status())
        self._segment.set_options(self._modes())
        self._warning.setText(t("models.review_needs_asr"))

    # ── internals ──
    def _restore_state(self) -> None:
        target = self._settings.translation_target
        mode = self._settings.translation_mode
        if not target or target == "off":
            effective = "off"
        else:
            effective = mode if mode in ("direct", "review") else "direct"
        self._segment.set_current(effective)

        restore_code = target if target and target != "off" else "eng"
        self._target_combo.blockSignals(True)
        for i in range(self._target_combo.count()):
            if self._target_combo.itemData(i) == restore_code:
                self._target_combo.setCurrentIndex(i)
                break
        self._target_combo.blockSignals(False)
        self.refresh_warning()

    def _on_mode_clicked(self, mode: str) -> None:
        if mode == "off":
            self._settings.set("translation_target", "off")
        else:
            current = self._target_combo.currentData() or "eng"
            self._settings.set("translation_target", current)
            self._settings.set("translation_mode", mode)
        self.mode_changed.emit(mode)
        self.target_changed.emit(self._settings.translation_target)
        self.refresh_warning()

    def _on_target_changed(self, idx: int) -> None:
        code = self._target_combo.itemData(idx)
        if not code:
            return
        if self._settings.translation_target != "off":
            self._settings.set("translation_target", code)
            self.target_changed.emit(code)


# ── variant row ──────────────────────────────────────────────────────────

class VariantRow(QWidget):
    """One downloadable/activatable build of a model."""

    activate_clicked = Signal(str, str, str, str)  # model_id, path, family, backend
    download_clicked = Signal(str)
    cancel_clicked = Signal(str)

    def __init__(self, info: ModelInfo, active_id: Optional[str],
                 is_recommended: bool, compatible: bool) -> None:
        super().__init__()
        self.info = info
        self._compatible = compatible
        self._loading = False
        self._downloading = False
        self._cancelling = False
        self._hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMinimumHeight(66)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 12, 0, 12)
        row.setSpacing(14)

        # Left: name + badges, blurb underneath
        left = QVBoxLayout()
        left.setSpacing(3)
        left.setContentsMargins(0, 0, 0, 0)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        vlabel = QLabel(info.variant)
        vlabel.setFont(theme.font(14, bold=True))
        vlabel.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY if compatible else theme.TEXT_MUTED}; background: transparent;")
        name_row.addWidget(vlabel)
        if is_recommended and compatible:
            name_row.addWidget(theme.badge(t("models.recommended"), "green", upper=True))
        name_row.addWidget(theme.badge(_fmt_size(info.size_mb), "muted"))
        name_row.addStretch()
        left.addLayout(name_row)

        self._blurb = QLabel(_blurb(info))
        self._blurb.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        self._blurb.setWordWrap(True)
        self._blurb.setMinimumWidth(0)
        left.addWidget(self._blurb)
        row.addLayout(left, stretch=1)

        # Right: progress cluster (downloading) …
        self._prog_box = QWidget()
        self._prog_box.setStyleSheet("background: transparent;")
        pb = QVBoxLayout(self._prog_box)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(4)
        self._prog_msg = QLabel("")
        self._prog_msg.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px; background: transparent;")
        self._prog_msg.setAlignment(Qt.AlignmentFlag.AlignRight)
        pb.addWidget(self._prog_msg)
        self._progress = ThinProgress(6)
        self._progress.setFixedWidth(170)
        pb.addWidget(self._progress)
        self._prog_box.hide()
        row.addWidget(self._prog_box)

        # … spinner for loading …
        self._spinner = Spinner(16, theme.INK)
        self._spinner.hide()
        row.addWidget(self._spinner)

        # … and the action button.
        self._btn = QPushButton()
        self._btn.setFixedHeight(32)
        self._btn.setMinimumWidth(108)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self._btn)

        self._update_button(active_id)
        self._btn.clicked.connect(self._on_click)

    # ── painting ──
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
            p.fillRect(self.rect().adjusted(-8, 0, 8, 0), QColor(31, 30, 27, 8))
        p.setPen(theme._BORDER_SUBTLE_C)
        p.drawLine(0, 0, self.width(), 0)          # hairline above each row
        p.end()

    # ── state ──
    def _style(self, kind: str, text: str, enabled: bool, font_px: int = 12) -> None:
        self._btn.setText(text)
        self._btn.setEnabled(enabled)
        self._btn.setStyleSheet(theme.button_qss(kind, 32, font_px))

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        self._spinner.setVisible(loading)
        if loading:
            self._style("secondary", t("models.btn.loading"), False)

    def set_downloading(self, on: bool) -> None:
        self._downloading = on
        self._cancelling = False
        self._prog_box.setVisible(on)
        if on:
            self._progress.set_indeterminate(True)
            self._prog_msg.setText(t("models.connecting"))
            self._style("ghost", t("models.cancel"), True)
        else:
            self._progress.set_indeterminate(False)

    def set_progress(self, val: int, msg: str) -> None:
        if not self._downloading or self._cancelling:
            return
        if val < 0:
            self._progress.set_indeterminate(True)
            self._prog_msg.setText(msg)
        else:
            self._progress.set_indeterminate(False)
            self._progress.set_value(val)
            if msg.startswith("Extracting"):
                self._prog_msg.setText(t("models.extracting"))
            elif msg.startswith(("Connecting", "Starting")):
                self._prog_msg.setText(t("models.connecting"))
            else:
                self._prog_msg.setText(f"{msg} · {val}%")

    def set_cancelling(self) -> None:
        self._cancelling = True
        self._prog_msg.setText(t("models.cancelling"))
        self._style("ghost", t("models.cancelling"), False)

    def _update_button(self, active_id: Optional[str],
                       translator_active: Optional[str] = None,
                       mode: str = "off") -> None:
        if self._loading or self._downloading:
            return
        from thundertalk.core.runtime import needed, restart_needed
        if self.info.backend == "seamless-torch":
            self._blurb.setText(_blurb(self.info))
            if restart_needed():
                self._style("secondary", t("runtime.restart_short"), False)
                return
        downloaded = is_downloaded(self.info.id) and not (self.info.backend == "seamless-torch" and needed())
        is_seamless = self.info.backend == "seamless-torch"
        is_asr_active = (active_id == self.info.id) and not is_seamless
        is_translator_active = (
            translator_active == self.info.id and is_seamless
            and mode in ("direct", "review")
        )

        if self._compatible:
            if mode == "direct" and not is_seamless and downloaded:
                self._style("secondary", t("models.btn.direct_uses_seamless"), False, 11)
                return
            if mode == "off" and is_seamless and downloaded:
                self._style("secondary", t("models.btn.direct_review_only"), False, 11)
                return

        if not self._compatible:
            key = ("models.btn.needs_apple_silicon" if self.info.platform == "apple-silicon"
                   else "models.btn.needs_nvidia")
            self._style("secondary", t(key), False, 11)
        elif is_translator_active:
            self._style("secondary", t("models.btn.translator"), False)
            fg, bg, bd = theme.PASTELS["orange"]
            self._btn.setStyleSheet(
                f"QPushButton {{ background: {bg}; color: {fg};"
                f" border: 1px solid {bd}; border-radius: 5px; padding: 0 14px;"
                " font-size: 12px; font-weight: 600; }")
        elif is_asr_active:
            self._style("secondary", t("models.btn.active"), False)
            fg, bg, bd = theme.PASTELS["green"]
            self._btn.setStyleSheet(
                f"QPushButton {{ background: {bg}; color: {fg};"
                f" border: 1px solid {bd}; border-radius: 5px; padding: 0 14px;"
                " font-size: 12px; font-weight: 600; }")
        elif downloaded:
            self._style("primary", t("models.btn.activate"), True)
        elif self.info.download_url:
            self._style("secondary", t("models.btn.download"), True)
        else:
            self._style("secondary", t("models.btn.coming_soon"), False, 11)

    def _on_click(self) -> None:
        if self._downloading:
            self.cancel_clicked.emit(self.info.id)
            return
        from thundertalk.core.runtime import needed
        if is_downloaded(self.info.id) and not (self.info.backend == "seamless-torch" and needed()):
            path = get_model_path(self.info.id)
            if path:
                self.activate_clicked.emit(
                    self.info.id, path, self.info.family, self.info.backend)
        elif self.info.download_url:
            self.download_clicked.emit(self.info.id)

    def download_done(self, active_id: Optional[str], translator_active: Optional[str] = None,
                      mode: str = "off") -> None:
        self.set_downloading(False)
        self._update_button(active_id, translator_active, mode)

    def refresh(self, active_id: Optional[str], translator_active: Optional[str] = None,
                mode: str = "off") -> None:
        self._update_button(active_id, translator_active, mode)


# ── family card ──────────────────────────────────────────────────────────

class FamilyCard(theme.Card):
    """Card for one model family containing its variant rows."""

    activate_clicked = Signal(str, str, str, str)
    download_clicked = Signal(str)
    cancel_clicked = Signal(str)

    def __init__(self, family: str, variants: list[ModelInfo], active_id: Optional[str]) -> None:
        super().__init__(radius=theme.RADIUS_CARD)
        self._family = family
        self._rows: dict[str, VariantRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 12)
        layout.setSpacing(10)

        first = variants[0]
        top = QHBoxLayout()
        top.setSpacing(10)
        name = QLabel(first.name)
        name.setFont(theme.font_serif(20))
        name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        top.addWidget(name)
        top.addWidget(theme.Chip(family, theme.TEXT_SECONDARY, "transparent", theme.BORDER_DEFAULT))
        if family == _BEST_FAMILY and any(is_variant_compatible(v) for v in variants):
            top.addWidget(theme.badge(t("models.best_for_you"), "orange", upper=True))
        top.addStretch()
        layout.addLayout(top)

        # Meta line: stars + chips
        meta = QHBoxLayout()
        meta.setSpacing(10)
        stars = QLabel()
        filled, empty = "★" * first.accuracy_stars, "★" * (5 - first.accuracy_stars)
        stars.setText(
            f"<span style='color:{theme.TEXT_PRIMARY}'>{filled}</span>"
            f"<span style='color:#D5D2C9'>{empty}</span>")
        stars.setStyleSheet("font-size: 13px; background: transparent;")
        meta.addWidget(stars)
        parts = [f"{first.language_count} {t('models.languages')}"]
        if any(v.hotword_support for v in variants):
            parts.append(t("models.hotwords_supported"))
        n = len(variants)
        parts.append(t("models.format_one") if n == 1 else t("models.formats").format(n=n))
        meta_lbl = QLabel("  ·  ".join(parts))
        meta_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        meta.addWidget(meta_lbl)
        meta.addStretch()
        layout.addLayout(meta)

        rec_id = get_recommended_id(family)
        for v in variants:
            row = VariantRow(v, active_id, is_recommended=(v.id == rec_id),
                             compatible=is_variant_compatible(v))
            row.activate_clicked.connect(self.activate_clicked)
            row.download_clicked.connect(self.download_clicked)
            row.cancel_clicked.connect(self.cancel_clicked)
            layout.addWidget(row)
            self._rows[v.id] = row

    def add_option_row(self, row_layout) -> None:
        """Append a per-family option row (e.g. a feature toggle)."""
        layout = self.layout()
        layout.addSpacing(2)
        layout.addWidget(theme.separator())
        layout.addLayout(row_layout)

    def get_row(self, model_id: str) -> Optional[VariantRow]:
        return self._rows.get(model_id)

    def refresh(self, active_id: Optional[str], translator_active: Optional[str] = None,
                mode: str = "off") -> None:
        for row in self._rows.values():
            row.refresh(active_id, translator_active, mode)


# ── hardware strip ───────────────────────────────────────────────────────

class _HardwareCard(theme.Card):
    def __init__(self) -> None:
        super().__init__(radius=14)
        self.setFixedHeight(66)
        ly = QHBoxLayout(self)
        ly.setContentsMargins(66, 10, 18, 10)
        ly.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        self._main = QLabel(t("models.detecting_hw"))
        self._main.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 600; background: transparent;")
        col.addWidget(self._main)
        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        col.addWidget(self._sub)
        ly.addLayout(col, stretch=1)
        self._chip_box = QHBoxLayout()
        ly.addLayout(self._chip_box)
        self._chip: Optional[QLabel] = None

    def set_info(self, hw, mlx: bool) -> None:
        if hw is None:
            self._main.setText("—")
            return
        self._main.setText(hw.cpu if hw.cpu != "Unknown" else hw.platform_tag.title())
        bits = []
        if hw.memory_gb:
            bits.append(t("models.hw.ram").format(gb=f"{hw.memory_gb:.0f}"))
        if hw.gpu and hw.gpu != "Unknown" and hw.gpu != hw.cpu:
            bits.append(hw.gpu)
        self._sub.setText("  ·  ".join(bits))
        if self._chip is not None:
            self._chip.deleteLater()
        if mlx:
            self._chip = theme.badge(t("models.hw.mlx_ready"), "green", upper=True)
        elif hw.platform_tag == "nvidia":
            self._chip = theme.badge(t("models.hw.nvidia"), "green")
        else:
            self._chip = theme.badge(t("models.hw.cpu_mode"), "neutral")
        self._chip_box.addWidget(self._chip)
        self.update()

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        disc = QRectF(18, (self.height() - 30) / 2, 30, 30)
        paint_icon(p, "cpu", disc, theme.TEXT_SECONDARY, 1.8)
        p.end()


class _Banner(theme.Card):
    """Dismissible inline message (errors from load/download)."""

    def __init__(self) -> None:
        super().__init__(radius=12, accent=theme.ERROR)
        ly = QHBoxLayout(self)
        ly.setContentsMargins(46, 10, 10, 10)
        ly.setSpacing(10)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px; background: transparent;")
        ly.addWidget(self._label, stretch=1)
        self._close = theme.make_button(t("models.dismiss"), "ghost", 28, font_px=12)
        self._close.clicked.connect(self.hide)
        ly.addWidget(self._close)
        self.hide()

    def show_message(self, text: str) -> None:
        self._label.setText(text)
        self._close.setText(t("models.dismiss"))
        self.show()

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        paint_icon(p, "alert", QRectF(16, (self.height() - 20) / 2, 20, 20), theme.ERROR, 2.0)
        p.end()


# ── page ─────────────────────────────────────────────────────────────────

class ModelsPage(QWidget):
    load_model_signal = Signal(str, str, str, str)  # model_id, path, family, backend
    translation_mode_changed = Signal(str)           # off | direct | review
    speaker_labels_toggled = Signal(bool)            # MOSS: keep S01:/S02: in dictation
    translation_target_changed = Signal(str)         # ISO-639-3 code or "off"
    download_translator_requested = Signal()
    model_download_completed = Signal(str)           # model_id
    download_failed = Signal(str, str)               # model_id, message
    download_started = Signal(str)                   # model_id
    download_progress = Signal(str, int, str)        # model_id, percent (-1 unknown), message

    def __init__(self, settings=None) -> None:
        super().__init__()
        self._settings = settings
        self._active_model: Optional[str] = None
        self._translator_active: Optional[str] = None
        self._current_mode: str = self._compute_current_mode(settings)
        self._family_cards: dict[str, FamilyCard] = {}
        self._workers: dict[str, DownloadWorker] = {}
        self._mode_card: Optional[TranslationModeCard] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll, self._layout = column_scroll(spacing=16)
        root.addWidget(scroll)

        self._header = PageHeader(t("models.title"), t("models.subtitle"))
        self._layout.addWidget(self._header)

        self._hw_card = _HardwareCard()
        self._layout.addWidget(self._hw_card)

        self._banner = _Banner()
        self._layout.addWidget(self._banner)

        if self._settings is not None:
            self._mode_card = TranslationModeCard(self._settings)
            self._mode_card.mode_changed.connect(self.translation_mode_changed)
            self._mode_card.target_changed.connect(self.translation_target_changed)
            self._mode_card.download_translator_clicked.connect(self.download_translator_requested)
            self._mode_card.mode_changed.connect(self._on_mode_changed)
            self._layout.addWidget(self._mode_card)

        for family, variants in get_families().items():
            card = FamilyCard(family, variants, self._active_model)
            card.activate_clicked.connect(self._on_activate)
            card.download_clicked.connect(self._on_download_requested)
            card.cancel_clicked.connect(self._on_cancel)
            if family == "MOSS-Transcribe-Diarize" and self._settings is not None:
                row, _ = theme.setting_row(t("models.moss.labels"), t("models.moss.labels_desc"))
                tg = theme.ToggleSwitch(bool(self._settings.get("moss_speaker_labels")))
                tg.toggled_signal.connect(self._on_speaker_labels_toggled)
                row.addWidget(tg)
                card.add_option_row(row)
            self._layout.addWidget(card)
            self._family_cards[family] = card

        self._layout.addStretch()

        self._hw_worker = _HardwareWorker()
        self._hw_worker.detected.connect(self._on_hw_detected)
        self._hw_worker.start()

    def wait_background(self, timeout_ms: int = 5000) -> None:
        """Block until the hardware probe thread has finished (tests / shutdown)."""
        self._hw_worker.wait(timeout_ms)

    # ── hardware ──
    def _on_hw_detected(self, hw) -> None:
        from thundertalk.core.asr import _IS_APPLE_SILICON
        self._hw_card.set_info(hw, bool(_IS_APPLE_SILICON))

    # ── activation ──
    def _on_activate(self, model_id: str, path: str, family: str, backend: str) -> None:
        self._banner.hide()
        self.load_model_signal.emit(model_id, path, family, backend)

    def _on_speaker_labels_toggled(self, enabled: bool) -> None:
        if self._settings is not None:
            self._settings.set("moss_speaker_labels", enabled)
        self.speaker_labels_toggled.emit(bool(enabled))

    @staticmethod
    def _compute_current_mode(settings) -> str:
        if settings is None:
            return "off"
        target = settings.translation_target
        mode = settings.translation_mode
        if not target or target == "off":
            return "off"
        return mode if mode in ("direct", "review") else "direct"

    def _on_mode_changed(self, mode: str) -> None:
        self._current_mode = mode
        self._refresh_all_rows()

    def _refresh_all_rows(self) -> None:
        for card in self._family_cards.values():
            card.refresh(self._active_model, self._translator_active, self._current_mode)

    def set_active_model(self, model_id: Optional[str]) -> None:
        self._active_model = model_id
        self._refresh_all_rows()
        if self._mode_card is not None:
            self._mode_card.refresh_warning()

    def set_translator_active(self, model_id: Optional[str]) -> None:
        self._translator_active = model_id
        self._refresh_all_rows()

    def set_loading(self, model_id: str, loading: bool) -> None:
        row = self._find_row(model_id)
        if row:
            row.set_loading(loading)
            if not loading:
                row.refresh(self._active_model, self._translator_active, self._current_mode)

    def set_translator_status(self, state: str, message: str = "") -> None:
        if self._mode_card is not None:
            self._mode_card.set_translator_status(state, message)

    def show_load_error(self, msg: str) -> None:
        self._banner.show_message(msg)

    # ── downloads ──
    def is_downloading(self, model_id: str) -> bool:
        return model_id in self._workers

    def _on_download_requested(self, model_id: str) -> None:
        """Row clicked Download: confirm very large downloads first."""
        info = next((m for m in BUILTIN_MODELS if m.id == model_id), None)
        if info is None:
            return
        if info.size_mb >= _BIG_DOWNLOAD_MB and not is_downloaded(model_id):
            from thundertalk.ui.styled_dialog import StyledDialog
            ok = StyledDialog.confirm(
                self.window(),
                title=t("models.big_download_title"),
                body=t("models.big_download_body").format(
                    name=info.name, size=_fmt_size(info.size_mb)),
                accept_label=t("models.big_download_go"),
                cancel_label=t("models.cancel"),
            )
            if not ok:
                return
        self._on_download(model_id)

    def _on_download(self, model_id: str) -> None:
        """Start (or ignore a duplicate request for) a download."""
        if model_id in self._workers:
            return
        info = next((m for m in BUILTIN_MODELS if m.id == model_id), None)
        if info is None:
            return
        worker = DownloadWorker(info)
        self._workers[model_id] = worker

        row = self._find_row(model_id)
        if row:
            row.set_downloading(True)
            worker.progress.connect(row.set_progress)
        worker.progress.connect(
            lambda p, msg, mid=model_id: self.download_progress.emit(mid, p, msg))
        worker.done.connect(self._download_done)
        worker.error.connect(lambda msg, mid=model_id: self._download_error(mid, msg))
        worker.cancelled.connect(lambda mid=model_id: self._download_cancelled(mid))
        # Only drop our reference once the OS thread has fully exited.
        worker.finished.connect(lambda mid=model_id: self._workers.pop(mid, None))
        worker.start()
        self.download_started.emit(model_id)

    # Public API used by the onboarding flow and app-level automation.
    def start_download(self, model_id: str) -> None:
        self._on_download(model_id)

    def cancel_download(self, model_id: str) -> None:
        self._on_cancel(model_id)

    def activate_model(self, model_id: str) -> bool:
        """Load an already-downloaded model. Returns False if it isn't local."""
        info = next((m for m in BUILTIN_MODELS if m.id == model_id), None)
        if info is None or not is_downloaded(model_id):
            return False
        path = get_model_path(model_id)
        if not path:
            return False
        self._on_activate(info.id, path, info.family, info.backend)
        return True

    def _on_cancel(self, model_id: str) -> None:
        worker = self._workers.get(model_id)
        row = self._find_row(model_id)
        if worker is not None:
            worker.cancel()
        if row is not None:
            row.set_cancelling()

    def _find_row(self, model_id: str) -> Optional[VariantRow]:
        for card in self._family_cards.values():
            row = card.get_row(model_id)
            if row:
                return row
        return None

    def _download_done(self, model_id: str) -> None:
        row = self._find_row(model_id)
        if row:
            row.download_done(self._active_model, self._translator_active, self._current_mode)
        from thundertalk.core.runtime import restart_needed
        if model_id == "seamless-m4t-v2-large" and restart_needed():
            self._banner.show_message(t("runtime.restart"))
            if self._mode_card:
                self._mode_card.retranslate()
            return
        self.model_download_completed.emit(model_id)

    def _download_cancelled(self, model_id: str) -> None:
        row = self._find_row(model_id)
        if row:
            row.download_done(self._active_model, self._translator_active, self._current_mode)

    def _download_error(self, model_id: str, msg: str) -> None:
        row = self._find_row(model_id)
        if row:
            row.download_done(self._active_model, self._translator_active, self._current_mode)
        self._banner.show_message(t("models.download_failed").format(err=msg[:240]))
        self.download_failed.emit(model_id, msg)

    def cancel_all_downloads(self) -> None:
        for w in list(self._workers.values()):
            w.cancel()

    def retranslate(self) -> None:
        self._header.set_title(t("models.title"))
        self._header.set_subtitle(t("models.subtitle"))
        if self._mode_card is not None:
            self._mode_card.retranslate()
