"""Models page — grouped by family, each with selectable format variants."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QThread, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
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
from thundertalk.ui import theme

_FAMILY_COLORS = {
    "SenseVoice": theme.ACCENT_CYAN,
    "Qwen3-ASR": theme.ACCENT_BLUE,
    "Qwen3-ASR-1.7B": theme.ACCENT_BLUE,
    "FunASR-Nano": theme.ACCENT_PURPLE,
}


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
            f"QFrame {{ background: {theme.BG_CARD};"
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
            badge = QLabel("Recommended")
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
            self._btn.setText("Loading…")
            self._btn.setStyleSheet(
                f"QPushButton {{ background: {theme.BG_ELEVATED}; color: {theme.TEXT_MUTED};"
                f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 15px; font-size: 11px; }}"
            )
            self._btn.setEnabled(False)
            self._progress.show()
            self._progress.setRange(0, 0)  # indeterminate
        else:
            self._progress.hide()
            self._progress.setRange(0, 100)

    def _update_button(self, active_id: Optional[str]) -> None:
        if self._loading:
            return
        downloaded = is_downloaded(self.info.id)
        is_active = active_id == self.info.id

        if not self._compatible:
            plat = "Apple Silicon" if self.info.platform == "apple-silicon" else "NVIDIA GPU"
            self._btn.setText(f"Needs {plat}")
            self._btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_MUTED};"
                f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 15px; font-size: 10px; }}"
            )
            self._btn.setEnabled(False)
        elif is_active:
            self._btn.setText("✓ Active")
            self._btn.setStyleSheet(
                f"QPushButton {{ background: {theme.BG_ELEVATED}; color: {theme.SUCCESS};"
                f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 15px; font-weight: 500; font-size: 11px; }}"
            )
            self._btn.setEnabled(False)
        elif downloaded:
            self._btn.setText("Activate")
            self._btn.setStyleSheet(
                f"QPushButton {{ background: {theme.ACCENT_BLUE}; color: #ffffff; border: none;"
                " border-radius: 15px; font-weight: 500; font-size: 11px; }}"
                f"QPushButton:hover {{ background: {theme.ACCENT_BLUE_HOVER}; }}"
            )
            self._btn.setEnabled(True)
        elif self.info.download_url:
            self._btn.setText("Download")
            self._btn.setStyleSheet(
                f"QPushButton {{ background: {theme.BG_CARD}; color: {theme.TEXT_SECONDARY};"
                f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 15px; font-size: 11px; }}"
                f"QPushButton:hover {{ background: {theme.BORDER_DEFAULT}; color: {theme.TEXT_PRIMARY}; }}"
            )
            self._btn.setEnabled(True)
        else:
            self._btn.setText("Coming Soon")
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

    def download_done(self, active_id: Optional[str]) -> None:
        self._progress.hide()
        self._update_button(active_id)

    def refresh(self, active_id: Optional[str]) -> None:
        self._update_button(active_id)


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
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 14px; }}"
        )
        self.setGraphicsEffect(theme.auto_shadow())
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
            f" background: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER_DEFAULT};"
            " border-radius: 8px; padding: 2px 10px;"
        )
        top.addWidget(pill)
        top.addStretch()
        layout.addLayout(top)

        # Metadata line
        stars = "★" * first.accuracy_stars + "☆" * (5 - first.accuracy_stars)
        meta_parts = [f"{first.language_count} languages"]
        if any(v.hotword_support for v in variants):
            meta_parts.append("Hotwords")
        meta_parts.append(f"{len(variants)} variants available")
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

    def refresh(self, active_id: Optional[str]) -> None:
        for row in self._rows.values():
            row.refresh(active_id)


# ---------------------------------------------------------------------------
# ModelsPage
# ---------------------------------------------------------------------------

class ModelsPage(QWidget):
    load_model_signal = Signal(str, str, str, str)  # model_id, path, family, backend

    def __init__(self) -> None:
        super().__init__()
        self._active_model: Optional[str] = None
        self._family_cards: dict[str, FamilyCard] = {}
        self._workers: dict[str, DownloadWorker] = {}

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

        heading = QLabel("Models")
        heading.setFont(theme.font_heading(20))
        heading.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        self._layout.addWidget(heading)

        # Hardware info card
        hw_card = QFrame()
        hw_card.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 12px; }}"
        )
        hw_card.setGraphicsEffect(theme.auto_shadow())
        hw_ly = QHBoxLayout(hw_card)
        hw_ly.setContentsMargins(20, 16, 20, 16)
        hw_icon = QLabel("HW")
        hw_icon.setFont(QFont("Helvetica Neue", 11, QFont.Weight.Bold))
        hw_icon.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; border: none;"
            f" background: {theme.BG_ELEVATED}; border-radius: 4px;"
            " padding: 4px 6px;"
        )
        hw_ly.addWidget(hw_icon)
        hw_ly.addSpacing(6)
        self._hw_label = QLabel("Detecting hardware...")
        self._hw_label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px; border: none;"
        )
        self._hw_label.setWordWrap(True)
        hw_ly.addWidget(self._hw_label, stretch=1)
        self._layout.addWidget(hw_card)

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
        from thundertalk.core.asr import _MLX_AVAILABLE
        plat = hw.platform_tag.replace("-", " ").title()
        mlx_tag = "  [MLX available]" if _MLX_AVAILABLE else ""
        self._hw_label.setText(
            f"CPU: {hw.cpu}   ·   RAM: {hw.memory_gb:.0f} GB   ·   GPU: {hw.gpu}\n"
            f"Platform: {plat}{mlx_tag}"
        )

    def _on_activate(self, model_id: str, path: str, family: str, backend: str) -> None:
        self._error_label.hide()
        self.load_model_signal.emit(model_id, path, family, backend)

    def set_active_model(self, model_id: Optional[str]) -> None:
        self._active_model = model_id
        for card in self._family_cards.values():
            card.refresh(self._active_model)

    def set_loading(self, model_id: str, loading: bool) -> None:
        """Show/hide loading indicator on a specific variant row."""
        row = self._find_row(model_id)
        if row:
            row.set_loading(loading)
            if not loading:
                row.refresh(self._active_model)

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
            row.download_done(self._active_model)
        self._workers.pop(model_id, None)

    def _download_error(self, model_id: str, msg: str) -> None:
        row = self._find_row(model_id)
        if row:
            row._progress.hide()
        self._error_label.setText(f"Download failed for {model_id}: {msg}")
        self._error_label.show()
        self._workers.pop(model_id, None)
