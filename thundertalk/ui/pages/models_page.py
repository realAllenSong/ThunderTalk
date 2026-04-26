"""Models page — grouped by family, each with selectable format variants."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QThread, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.models import (
    BUILTIN_MODELS,
    ModelInfo,
    detect_hardware,
    download_model,
    get_families,
    get_model_path,
    get_recommended_id,
    is_downloaded,
    is_variant_compatible,
)
from thundertalk.core.i18n import t
from thundertalk.ui import theme

_FAMILY_COLORS = {
    "SenseVoice": theme.ACCENT_CYAN,
    "Qwen3-ASR": theme.ACCENT_BLUE,
    "Qwen3-ASR-1.7B": theme.ACCENT_BLUE,
}


class _DeviceIcon(QWidget):
    """Outlined device glyph — MacBook (clamshell + base) or generic PC tower."""

    def __init__(self, kind: str = "mac") -> None:
        super().__init__()
        self._kind = kind
        self.setFixedSize(44, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(theme.TEXT_SECONDARY)
        p.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)

        if self._kind == "mac":
            # Screen
            p.drawRoundedRect(QRectF(6, 4, 32, 20), 2.2, 2.2)
            # Base lip
            p.drawLine(2, 26, 42, 26)
            # Notch/hinge
            p.drawLine(18, 26, 26, 26)
        else:
            # Desktop tower / generic
            p.drawRoundedRect(QRectF(10, 4, 24, 22), 3, 3)
            p.drawLine(14, 28, 30, 28)
        p.end()


class DownloadWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, info: ModelInfo) -> None:
        super().__init__()
        self._info = info

    def run(self) -> None:
        try:
            download_model(self._info, progress_cb=lambda p, m: self.progress.emit(p, m))
            self.finished.emit(self._info.id)
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Translation mode card — appears at the top of Models page.
# ---------------------------------------------------------------------------

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


class TranslationModeCard(QFrame):
    """Top-of-page card with a 3-segment Mode switch (Off / Direct / Review)
    and an inline target language picker. Replaces the old Settings ▸
    Translation tab.

    Settings semantics:
      - Mode = Off    → translation_target = "off"  (mode value preserved)
      - Mode = Direct → translation_mode = "direct", target = current lang
      - Mode = Review → translation_mode = "review", target = current lang
    """

    mode_changed = Signal(str)        # "off" | "direct" | "review"
    target_changed = Signal(str)      # ISO-639-3
    download_translator_clicked = Signal()  # user wants the Seamless model

    @staticmethod
    def _modes() -> list[tuple[str, str]]:
        # Re-evaluated each call so a language switch updates labels.
        return [
            ("off", t("models.mode_off")),
            ("direct", t("models.mode_direct")),
            ("review", t("models.mode_review")),
        ]

    def __init__(self, settings) -> None:
        super().__init__()
        self._settings = settings
        self.setStyleSheet(
            f"QFrame#translationModeCard {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 12px; }}"
        )
        self.setObjectName("translationModeCard")

        ly = QVBoxLayout(self)
        ly.setContentsMargins(20, 16, 20, 16)
        ly.setSpacing(10)

        # ── Heading: title on its own line, subtitle muted underneath.
        # Mirrors the FamilyCard structure (name → pill → meta line) so
        # this card sits in the same visual rhythm as the model cards
        # below instead of looking like a different design.
        self._title_lbl = QLabel(t("models.translation"))
        self._title_lbl.setFont(theme.font(15, bold=True))
        self._title_lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        ly.addWidget(self._title_lbl)

        self._subtitle_lbl = QLabel(t("models.translation_subtitle"))
        self._subtitle_lbl.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px; border: none;"
        )
        ly.addWidget(self._subtitle_lbl)

        ly.addSpacing(2)

        # ── Controls row: mode segment on the LEFT, target combo on the
        # RIGHT, on the same horizontal axis. They're conceptually one
        # control ("how to translate, into what language"); putting them
        # on opposite ends of opposite rows broke that grouping.
        controls_row = QHBoxLayout()
        controls_row.setSpacing(12)

        seg_outer = QFrame()
        seg_outer.setStyleSheet(
            "QFrame { background: transparent;"
            f" border: 1px solid {theme.BORDER_DEFAULT};"
            " border-radius: 11px; }"
        )
        seg_outer.setFixedHeight(34)
        seg_inner = QHBoxLayout(seg_outer)
        seg_inner.setContentsMargins(3, 3, 3, 3)
        seg_inner.setSpacing(2)

        self._buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for code, label in self._modes():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton {"
                " background: transparent;"
                f" color: {theme.TEXT_MUTED};"
                " border: none; border-radius: 8px;"
                " padding: 4px 22px; font-size: 12px; font-weight: 500;"
                " }"
                f"QPushButton:hover {{ color: {theme.TEXT_SECONDARY}; }}"
                f"QPushButton:checked {{ background: {theme.ACCENT_ORANGE};"
                f" color: #ffffff; font-weight: 600; }}"
            )
            btn.clicked.connect(lambda _, c=code: self._on_mode_clicked(c))
            self._buttons[code] = btn
            self._group.addButton(btn)
            seg_inner.addWidget(btn)

        controls_row.addWidget(seg_outer)
        controls_row.addStretch()

        self._target_combo = QComboBox()
        self._target_combo.setFixedHeight(34)
        self._target_combo.setMinimumWidth(170)
        theme.style_combo(self._target_combo)
        for code, display in TRANSLATION_TARGETS_NEW:
            self._target_combo.addItem(display, code)
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        controls_row.addWidget(self._target_combo)
        ly.addLayout(controls_row)

        # Warning shown when Review is picked without an active ASR.
        # MaximumWidth caps wrap to the card width — without it, the
        # WordWrap label reports its unwrapped sizeHint and forces the
        # whole card (and the window) to grow rightward.
        self._warning = QLabel(t("models.review_needs_asr"))
        self._warning.setStyleSheet(
            f"color: {theme.ACCENT_ORANGE}; font-size: 11px; border: none;"
            " padding-top: 2px;"
        )
        self._warning.setWordWrap(True)
        self._warning.setMinimumWidth(0)
        self._warning.setMaximumWidth(680)
        self._warning.hide()
        ly.addWidget(self._warning)

        # Translator (SeamlessM4T) status: a single line beneath the
        # segment control — the user previously had no way to see whether
        # the translation engine was loading / ready / missing on disk.
        # Visible only while a target is set (i.e. not Off).
        self._translator_status_row = QWidget()
        self._translator_status_row.setStyleSheet("background: transparent;")
        ts_ly = QHBoxLayout(self._translator_status_row)
        ts_ly.setContentsMargins(0, 2, 0, 0)
        ts_ly.setSpacing(8)

        self._translator_dot = QLabel("●")
        self._translator_dot.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px;"
            " background: transparent; border: none;"
        )
        ts_ly.addWidget(self._translator_dot)

        self._translator_status_label = QLabel("")
        self._translator_status_label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 11px;"
            " background: transparent; border: none;"
        )
        self._translator_status_label.setWordWrap(True)
        self._translator_status_label.setMinimumWidth(0)
        self._translator_status_label.setMaximumWidth(560)
        # WordWrap labels report their unwrapped 1-line sizeHint; in a
        # QHBoxLayout that bleeds upward into the parent card's
        # preferredSize and pushes the whole Models page (and the
        # window) wider when a longer status string lands. Telling the
        # layout to ignore our horizontal sizeHint pins the width to
        # whatever the row gives us, so the card never grows just
        # because the status text changes.
        from PySide6.QtWidgets import QSizePolicy
        self._translator_status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        ts_ly.addWidget(self._translator_status_label, stretch=1)

        self._translator_action_btn = QPushButton("")
        self._translator_action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._translator_action_btn.setFixedHeight(24)
        self._translator_action_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.ACCENT_ORANGE};"
            f" border: 1px solid {theme.ACCENT_ORANGE};"
            " border-radius: 12px; padding: 0 12px; font-size: 11px; }}"
            f"QPushButton:hover {{ color: #ffffff; background: {theme.ACCENT_ORANGE}; }}"
        )
        self._translator_action_btn.clicked.connect(
            self.download_translator_clicked
        )
        self._translator_action_btn.hide()
        ts_ly.addWidget(self._translator_action_btn)

        self._translator_status_row.hide()
        ly.addWidget(self._translator_status_row)

        self._restore_state()

    # ── public API ──────────────────────────────────────────────────────

    def refresh_warning(self) -> None:
        """Re-evaluate whether to show the 'Review needs ASR' warning.
        Called by ModelsPage when active model state changes."""
        mode = self._settings.translation_mode
        target = self._settings.translation_target
        active_id = self._settings.active_model_id
        is_review = (target != "off") and (mode == "review")
        is_asr_active = bool(active_id) and not active_id.startswith("seamless")
        self._warning.setVisible(is_review and not is_asr_active)

    def set_translator_status(self, state: str, message: str = "") -> None:
        """Update the inline translator-engine status row.

        state ∈ {"hidden", "missing", "loading", "ready", "error"}
        - hidden     → row not shown (Off mode)
        - missing    → orange dot + "Download required" + Download button
        - loading    → orange dot + "Loading translator…"
        - ready      → green dot  + "Translator ready"
        - error      → red dot    + custom message
        """
        if state == "hidden":
            self._translator_status_row.hide()
            return

        palette = {
            "missing": (theme.ACCENT_ORANGE, t("models.translator.missing")),
            "loading": (theme.ACCENT_ORANGE, t("models.translator.loading")),
            "ready":   (theme.SUCCESS,       t("models.translator.ready")),
            "error":   (theme.ERROR,
                        message or t("models.translator.error")),
        }
        color, default_msg = palette.get(state, (theme.TEXT_MUTED, ""))
        self._translator_dot.setStyleSheet(
            f"color: {color}; font-size: 10px;"
            " background: transparent; border: none;"
        )
        self._translator_status_label.setText(message or default_msg)
        # Download button is only relevant in the "missing" state.
        if state == "missing":
            self._translator_action_btn.setText(t("models.btn.download"))
            self._translator_action_btn.show()
        else:
            self._translator_action_btn.hide()
        self._translator_status_row.show()

    # ── internals ──────────────────────────────────────────────────────

    def _restore_state(self) -> None:
        """Sync UI to current settings."""
        target = self._settings.translation_target
        mode = self._settings.translation_mode

        # Determine effective mode for the segmented control
        if not target or target == "off":
            effective_mode = "off"
        else:
            effective_mode = mode if mode in ("direct", "review") else "direct"

        for code, btn in self._buttons.items():
            btn.setChecked(code == effective_mode)

        # Restore target language (use current target, or default to "eng" if off)
        restore_code = target if target and target != "off" else "eng"
        self._target_combo.blockSignals(True)
        for i in range(self._target_combo.count()):
            if self._target_combo.itemData(i) == restore_code:
                self._target_combo.setCurrentIndex(i)
                break
        self._target_combo.blockSignals(False)

        self.refresh_warning()

    def _on_mode_clicked(self, mode: str) -> None:
        """User clicked one of the segment buttons."""
        if mode == "off":
            # Off = no translation. Clear translation_target.
            self._settings.set("translation_target", "off")
        else:
            # Direct or Review: ensure target_lang is set (use combo selection)
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
        # Only meaningful when not in Off mode; persist anyway so it sticks
        # the next time user enables Direct/Review.
        if self._settings.translation_target != "off":
            self._settings.set("translation_target", code)
            self.target_changed.emit(code)


# ---------------------------------------------------------------------------
# VariantRow — one row inside a FamilyCard
# ---------------------------------------------------------------------------

class VariantRow(QFrame):
    """A single format variant row with its own Download / Activate button."""

    activate_clicked = Signal(str, str, str, str)  # model_id, path, family, backend
    download_clicked = Signal(str)

    def __init__(
        self,
        info: ModelInfo,
        active_id: Optional[str],
        is_recommended: bool,
        compatible: bool,
    ) -> None:
        super().__init__()
        self.info = info
        self._compatible = compatible
        self._loading = False

        self.setStyleSheet(
            f"QFrame {{ background: transparent;"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 10px; }}"
            f"QFrame:hover {{ border: 1px solid {theme.BORDER_DEFAULT}; }}"
        )
        self.setMinimumHeight(52)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)

        # Variant name
        vlabel = QLabel(info.variant)
        vlabel.setFont(theme.font(13, bold=True))
        vlabel.setStyleSheet(f"color: {theme.TEXT_PRIMARY if compatible else theme.TEXT_MUTED}; border: none;")
        row.addWidget(vlabel)

        # Recommended badge
        if is_recommended and compatible:
            badge = QLabel(t("models.recommended"))
            badge.setStyleSheet(
                f"color: {theme.SUCCESS}; font-size: 10px; font-weight: bold;"
                " background: transparent; border: none; padding: 2px 8px;"
            )
            row.addWidget(badge)

        # Size
        size = QLabel(f"{info.size_mb} MB")
        size.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px; border: none;")
        row.addWidget(size)

        row.addStretch()

        # Notes
        if info.notes:
            note = QLabel(info.notes)
            note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px; border: none;")
            note.setWordWrap(True)
            note.setMaximumWidth(220)
            row.addWidget(note)

        # Progress bar (hidden by default)
        self._progress = QProgressBar()
        self._progress.setFixedSize(80, 3)
        self._progress.setTextVisible(False)
        accent = _FAMILY_COLORS.get(info.family, theme.ACCENT_BLUE)
        self._progress.setStyleSheet(
            f"QProgressBar {{ background: {theme.BORDER_SUBTLE}; border: none; border-radius: 1px; }}"
            f"QProgressBar::chunk {{ background: {accent}; border-radius: 1px; }}"
        )
        self._progress.hide()
        row.addWidget(self._progress)

        # Action button
        self._btn = QPushButton()
        self._btn.setFixedSize(110, 30)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self._btn)

        self._update_button(active_id)
        self._btn.clicked.connect(self._on_click)

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        if loading:
            self._btn.setText(t("models.btn.loading"))
            self._btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};"
                f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 15px; font-size: 11px; }}"
            )
            self._btn.setEnabled(False)
            # Intentionally do NOT show the indeterminate progress bar
            # here. _progress is a fixed-size 80x3 QWidget and toggling
            # it visible adds 80+10px (spacing) to the row's
            # minimumSizeHint, which jumps the SeamlessM4T card from
            # ~558→648 px and forces a horizontal scrollbar on narrower
            # windows. The "Loading…" button text is enough indicator;
            # the progress bar stays reserved for actual download
            # progress (set_progress), where the percentage matters.
        else:
            self._progress.hide()
            self._progress.setRange(0, 100)

    def _update_button(
        self,
        active_id: Optional[str],
        translator_active: Optional[str] = None,
        mode: str = "off",
    ) -> None:
        if self._loading:
            return
        downloaded = is_downloaded(self.info.id)
        is_seamless = self.info.backend == "seamless-torch"
        is_asr_active = (active_id == self.info.id) and not is_seamless
        # Translator badge is only meaningful when translation is on.
        # In Off mode we keep the engine in RAM (so re-enabling is
        # instant) but suppress the visual badge — the user shouldn't
        # see "✓ Translator" on Facebook when translation is disabled.
        is_translator_active = (
            translator_active == self.info.id
            and is_seamless
            and mode in ("direct", "review")
        )

        # Mode gating — some rows aren't activatable in some modes:
        #   Direct mode is "audio → translated text in one pass"; only
        #     SeamlessM4T can do that, so every other row is disabled.
        #   Off mode doesn't use the translator engine; SeamlessM4T as
        #     a pure ASR isn't supported yet, so its row is disabled
        #     with a hint that it belongs to Direct/Review modes.
        if self._compatible and not self._loading:
            if mode == "direct" and not is_seamless and downloaded:
                self._btn.setText(t("models.btn.direct_uses_seamless"))
                self._btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};"
                    f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 15px;"
                    " font-size: 10px; }}"
                )
                self._btn.setEnabled(False)
                return
            if mode == "off" and is_seamless and downloaded:
                self._btn.setText(t("models.btn.direct_review_only"))
                self._btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};"
                    f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 15px;"
                    " font-size: 10px; }}"
                )
                self._btn.setEnabled(False)
                return

        if not self._compatible:
            plat_key = ("models.btn.needs_apple_silicon"
                        if self.info.platform == "apple-silicon"
                        else "models.btn.needs_nvidia")
            self._btn.setText(t(plat_key))
            self._btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};"
                f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 15px; font-size: 10px; }}"
            )
            self._btn.setEnabled(False)
        elif is_translator_active:
            # SeamlessM4T loaded into the TranslationEngine; visually distinct
            # from the ASR Active badge so both engines can co-exist clearly.
            self._btn.setText(t("models.btn.translator"))
            self._btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.ACCENT_ORANGE};"
                f" border: 1px solid {theme.ACCENT_ORANGE}; border-radius: 15px; font-weight: 500; font-size: 11px; }}"
            )
            self._btn.setEnabled(False)
        elif is_asr_active:
            self._btn.setText(t("models.btn.active"))
            self._btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.SUCCESS};"
                f" border: 1px solid {theme.SUCCESS}; border-radius: 15px; font-weight: 500; font-size: 11px; }}"
            )
            self._btn.setEnabled(False)
        elif downloaded:
            self._btn.setText(t("models.btn.activate"))
            self._btn.setStyleSheet(
                f"QPushButton {{ background: {theme.ACCENT_BLUE}; color: #ffffff; border: none;"
                " border-radius: 15px; font-weight: 500; font-size: 11px; }}"
                f"QPushButton:hover {{ background: {theme.ACCENT_BLUE_HOVER}; }}"
            )
            self._btn.setEnabled(True)
        elif self.info.download_url:
            self._btn.setText(t("models.btn.download"))
            self._btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY};"
                f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 15px; font-size: 11px; }}"
                f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; border: 1px solid {theme.BORDER_STRONG}; }}"
            )
            self._btn.setEnabled(True)
        else:
            self._btn.setText(t("models.btn.coming_soon"))
            self._btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};"
                f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 15px;"
                " font-size: 10px; font-style: italic; }}"
            )
            self._btn.setEnabled(False)

    def _on_click(self) -> None:
        if is_downloaded(self.info.id):
            path = get_model_path(self.info.id)
            if path:
                self.activate_clicked.emit(
                    self.info.id, path, self.info.family, self.info.backend,
                )
        elif self.info.download_url:
            self.download_clicked.emit(self.info.id)

    def set_progress(self, val: int, msg: str) -> None:
        self._progress.show()
        self._progress.setValue(val)

    def download_done(
        self,
        active_id: Optional[str],
        translator_active: Optional[str] = None,
        mode: str = "off",
    ) -> None:
        self._progress.hide()
        self._update_button(active_id, translator_active, mode)

    def refresh(
        self,
        active_id: Optional[str],
        translator_active: Optional[str] = None,
        mode: str = "off",
    ) -> None:
        self._update_button(active_id, translator_active, mode)


# ---------------------------------------------------------------------------
# FamilyCard — groups all variants of one model family
# ---------------------------------------------------------------------------

class FamilyCard(QFrame):
    """Card for one model family containing variant rows."""

    activate_clicked = Signal(str, str, str, str)
    download_clicked = Signal(str)

    def __init__(
        self,
        family: str,
        variants: list[ModelInfo],
        active_id: Optional[str],
    ) -> None:
        super().__init__()
        self._family = family
        self._accent = _FAMILY_COLORS.get(family, theme.ACCENT_BLUE)
        self._rows: dict[str, VariantRow] = {}

        self.setStyleSheet(
            f"QFrame#familyCard {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 14px; }}"
        )
        self.setObjectName("familyCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        # Header: name + family pill + metadata
        first = variants[0]
        top = QHBoxLayout()
        top.setSpacing(10)
        name = QLabel(first.name)
        name.setFont(theme.font(15, bold=True))
        name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        top.addWidget(name)

        pill = QLabel(family)
        pill.setStyleSheet(
            f"color: {self._accent}; font-size: 10px;"
            f" background: transparent; border: 1px solid {self._accent};"
            " border-radius: 8px; padding: 2px 10px;"
        )
        top.addWidget(pill)
        top.addStretch()
        layout.addLayout(top)

        # Metadata line
        stars = "★" * first.accuracy_stars + "☆" * (5 - first.accuracy_stars)
        meta_parts = [f"{first.language_count} {t('models.languages')}"]
        if any(v.hotword_support for v in variants):
            meta_parts.append(t("models.hotwords_supported"))
        meta_parts.append(t("models.variants_available").format(n=len(variants)))
        meta = QLabel(f"{stars}   {'  ·  '.join(meta_parts)}")
        meta.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; border: none;")
        layout.addWidget(meta)

        layout.addSpacing(4)

        # Variant rows
        rec_id = get_recommended_id(family)
        for v in variants:
            compatible = is_variant_compatible(v)
            row = VariantRow(v, active_id, is_recommended=(v.id == rec_id), compatible=compatible)
            row.activate_clicked.connect(self.activate_clicked)
            row.download_clicked.connect(self.download_clicked)
            layout.addWidget(row)
            self._rows[v.id] = row

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Left accent bar
        grad = QLinearGradient(1, 12, 1, 60)
        grad.setColorAt(0, QColor(self._accent))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 10, 3, 50), 1.5, 1.5)
        p.drawPath(path)
        p.end()

    def get_row(self, model_id: str) -> Optional[VariantRow]:
        return self._rows.get(model_id)

    def refresh(
        self,
        active_id: Optional[str],
        translator_active: Optional[str] = None,
        mode: str = "off",
    ) -> None:
        for row in self._rows.values():
            row.refresh(active_id, translator_active, mode)


# ---------------------------------------------------------------------------
# ModelsPage
# ---------------------------------------------------------------------------

class ModelsPage(QWidget):
    load_model_signal = Signal(str, str, str, str)  # model_id, path, family, backend
    translation_mode_changed = Signal(str)           # off | direct | review
    translation_target_changed = Signal(str)         # ISO-639-3 code or "off"
    download_translator_requested = Signal()         # user clicked "Download" on the translator status row
    model_download_completed = Signal(str)           # model_id — emitted after a successful download finishes

    def __init__(self, settings=None) -> None:
        super().__init__()
        self._settings = settings
        self._active_model: Optional[str] = None
        # Translator (SeamlessM4T) is loaded into a separate engine; tracked
        # independently of _active_model so both badges can co-exist.
        self._translator_active: Optional[str] = None
        # Translation mode drives which rows are activatable. Initial value
        # matches whatever the user had set last session.
        self._current_mode: str = self._compute_current_mode(settings)
        self._family_cards: dict[str, FamilyCard] = {}
        self._workers: dict[str, DownloadWorker] = {}
        self._mode_card: Optional[TranslationModeCard] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(32, 32, 32, 20)
        self._layout.setSpacing(16)
        scroll.setWidget(container)

        self._heading = QLabel(t("models.title"))
        self._heading.setFont(theme.font_heading(20))
        self._heading.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        self._layout.addWidget(self._heading)

        # Hardware info card
        hw_card = QFrame()
        hw_card.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 12px; }}"
        )
        hw_ly = QHBoxLayout(hw_card)
        hw_ly.setContentsMargins(20, 16, 20, 16)
        hw_ly.setSpacing(14)
        self._hw_icon = _DeviceIcon("mac")
        hw_ly.addWidget(self._hw_icon)
        self._hw_label = QLabel(t("models.detecting_hw"))
        self._hw_label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px; border: none;"
        )
        self._hw_label.setWordWrap(True)
        hw_ly.addWidget(self._hw_label, stretch=1)
        self._layout.addWidget(hw_card)

        # Translation mode + target picker (only if settings provided)
        if self._settings is not None:
            self._mode_card = TranslationModeCard(self._settings)
            self._mode_card.mode_changed.connect(self.translation_mode_changed)
            self._mode_card.target_changed.connect(self.translation_target_changed)
            self._mode_card.download_translator_clicked.connect(
                self.download_translator_requested
            )
            # Mode card drives which rows are activatable in this page.
            # Off → only ASR rows enabled, Facebook row reads "Direct /
            # Review only".  Direct → only the Facebook row enabled.
            # Review → ASR rows enabled, Facebook row shows Translator
            # badge once loaded.
            self._mode_card.mode_changed.connect(self._on_mode_changed)
            self._layout.addWidget(self._mode_card)

        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            f"color: {theme.ERROR}; background: {theme.ERROR_DIM};"
            f" border: 1px solid {theme.ERROR_A40};"
            " border-radius: 10px; padding: 12px 16px; font-size: 12px;"
        )
        self._error_label.hide()
        self._layout.addWidget(self._error_label)

        # Build family cards
        for family, variants in get_families().items():
            card = FamilyCard(family, variants, self._active_model)
            card.activate_clicked.connect(self._on_activate)
            card.download_clicked.connect(self._on_download)
            self._layout.addWidget(card)
            self._family_cards[family] = card

        self._layout.addStretch()

        self._detect_hw()

    def _detect_hw(self) -> None:
        hw = detect_hardware()
        from thundertalk.core.asr import _check_mlx, _IS_APPLE_SILICON
        plat = hw.platform_tag.replace("-", " ").title()
        mlx_tag = "  [MLX capable]" if _IS_APPLE_SILICON else ""
        import platform
        self._hw_icon.set_kind("mac" if platform.system() == "Darwin" else "pc")
        self._hw_label.setText(
            f"CPU: {hw.cpu}   ·   RAM: {hw.memory_gb:.0f} GB   ·   GPU: {hw.gpu}\n"
            f"Platform: {plat}{mlx_tag}"
        )

    def _on_activate(self, model_id: str, path: str, family: str, backend: str) -> None:
        self._error_label.hide()
        self.load_model_signal.emit(model_id, path, family, backend)

    @staticmethod
    def _compute_current_mode(settings) -> str:
        """Derive 'off' / 'direct' / 'review' from raw settings — mirrors
        TranslationModeCard._restore_state's logic so the page-level
        mode tracker matches the UI segment selection on first paint."""
        if settings is None:
            return "off"
        target = settings.translation_target
        mode = settings.translation_mode
        if not target or target == "off":
            return "off"
        return mode if mode in ("direct", "review") else "direct"

    def _on_mode_changed(self, mode: str) -> None:
        """User flipped the segment control. Re-render every row with
        the new mode so disabled-rows update immediately."""
        self._current_mode = mode
        self._refresh_all_rows()

    def _refresh_all_rows(self) -> None:
        for card in self._family_cards.values():
            card.refresh(
                self._active_model, self._translator_active, self._current_mode
            )

    def set_active_model(self, model_id: Optional[str]) -> None:
        self._active_model = model_id
        self._refresh_all_rows()
        if self._mode_card is not None:
            self._mode_card.refresh_warning()

    def set_translator_active(self, model_id: Optional[str]) -> None:
        """Mark the SeamlessM4T translator model as active in the UI.

        Independent of set_active_model: ASR and translator can both be
        loaded simultaneously; both badges co-exist.
        """
        self._translator_active = model_id
        self._refresh_all_rows()

    def set_loading(self, model_id: str, loading: bool) -> None:
        """Show/hide loading indicator on a specific variant row."""
        row = self._find_row(model_id)
        if row:
            row.set_loading(loading)
            if not loading:
                row.refresh(
                    self._active_model, self._translator_active, self._current_mode
                )

    def set_translator_status(self, state: str, message: str = "") -> None:
        """Forward to the inline status row inside TranslationModeCard."""
        if self._mode_card is not None:
            self._mode_card.set_translator_status(state, message)

    def show_load_error(self, msg: str) -> None:
        self._error_label.setText(msg)
        self._error_label.show()

    def _on_download(self, model_id: str) -> None:
        info = next(m for m in BUILTIN_MODELS if m.id == model_id)
        worker = DownloadWorker(info)
        self._workers[model_id] = worker

        row = self._find_row(model_id)
        if row:
            worker.progress.connect(row.set_progress)
        worker.finished.connect(lambda mid: self._download_done(mid))
        worker.error.connect(lambda msg: self._download_error(model_id, msg))
        worker.start()

    def _find_row(self, model_id: str) -> Optional[VariantRow]:
        for card in self._family_cards.values():
            row = card.get_row(model_id)
            if row:
                return row
        return None

    def _download_done(self, model_id: str) -> None:
        row = self._find_row(model_id)
        if row:
            row.download_done(
                self._active_model, self._translator_active, self._current_mode
            )
        self._workers.pop(model_id, None)
        self.model_download_completed.emit(model_id)

    def _download_error(self, model_id: str, msg: str) -> None:
        row = self._find_row(model_id)
        if row:
            row._progress.hide()
        self._error_label.setText(f"Download failed for {model_id}: {msg}")
        self._error_label.show()
        self._workers.pop(model_id, None)

    def retranslate(self) -> None:
        self._heading.setText(t("models.title"))
