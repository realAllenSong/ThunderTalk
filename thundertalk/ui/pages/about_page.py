"""About page — version, tagline, links, and the in-app auto-updater.

Matches 闪电说 style: centered logo, version badge, tagline,
and footer links. The Check-for-Updates button drives the full
update flow (check → download → install + relaunch) without
sending the user to a browser.
"""

from __future__ import annotations

import pathlib
import webbrowser
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import thundertalk
from thundertalk.core.i18n import t
from thundertalk.core.updater import (
    UpdateInfo,
    check_for_update,
    download_update,
    install_update,
    installed_app_path,
)
from thundertalk.ui import theme


class _CheckWorker(QThread):
    """Background GitHub Releases poll. Never raises — updater
    swallows network errors and returns None."""

    done = Signal(object)  # Optional[UpdateInfo]

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self._current = current_version

    def run(self) -> None:
        info = check_for_update(self._current)
        self.done.emit(info)


class _DownloadWorker(QThread):
    """Background streaming download with progress callback."""

    progress = Signal(int, int)   # downloaded, total
    finished_ok = Signal(str)     # zip path as string
    failed = Signal(str)          # error message

    def __init__(self, info: UpdateInfo) -> None:
        super().__init__()
        self._info = info

    def run(self) -> None:
        try:
            path = download_update(
                self._info,
                progress_cb=lambda d, t: self.progress.emit(d, t),
            )
            self.finished_ok.emit(str(path))
        except Exception as e:
            self.failed.emit(str(e))


class _LogoWidget(QLabel):
    """Large app icon for the about page."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(80, 80)
        import os
        from PySide6.QtGui import QPixmap
        from thundertalk import asset_path
        icon_file = asset_path("icon.png")
        if os.path.isfile(icon_file):
            pm = QPixmap(icon_file).scaled(
                80, 80,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.setPixmap(pm)


class AboutPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        ly = QVBoxLayout(self)
        ly.setContentsMargins(32, 28, 32, 32)

        # ── Segment tab bar (to match settings page) ──
        from PySide6.QtWidgets import QTabBar
        tab_container = QHBoxLayout()
        tab_container.addStretch()
        tabs = QTabBar()
        tabs.setExpanding(False)
        tabs.setDrawBase(False)
        tabs.setStyleSheet(theme.segment_tab_qss())
        for name in ("Hotkey", "Microphone", "System", "Hotwords", "About"):
            tabs.addTab(name)
        tabs.setCurrentIndex(4)
        # These tabs are display-only since About is a separate page
        tabs.setEnabled(False)
        tabs.setStyleSheet(
            tabs.styleSheet()
            + f"QTabBar::tab:disabled {{ color: {theme.TEXT_SECONDARY}; }}"
            + f"QTabBar::tab:selected {{ color: {theme.TEXT_PRIMARY};"
            f" background: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER_SUBTLE}; }}"
        )
        tab_container.addWidget(tabs)
        tab_container.addStretch()
        # Don't show fake tabs — keep the page clean
        # ly.addLayout(tab_container)

        ly.addStretch()

        # ── Logo ──
        logo = _LogoWidget()
        ly.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)
        ly.addSpacing(16)

        # ── Title ──
        title = QLabel("ThunderTalk")
        title.setFont(theme.font_heading(24))
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(title)
        ly.addSpacing(10)

        # ── Version pill, alone on its row ──
        # Earlier the Check-for-Updates button sat next to the
        # version pill, but once it cycles to "Download Update" /
        # "Quit & Install" the layout got cramped — width pulses as
        # the label changes ("Downloading… 49%"), pushing the
        # version pill around. Stacking the action below the version
        # keeps the horizontal axis stable.
        ver_row = QHBoxLayout()
        ver_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_row.setSpacing(4)

        version = QLabel(f"v{thundertalk.__version__}")
        version.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px;"
            f" background: transparent; border: 1px solid {theme.BORDER_DEFAULT};"
            " border-radius: 12px; padding: 5px 16px;"
        )
        ver_row.addWidget(version)
        ly.addLayout(ver_row)
        ly.addSpacing(8)

        # ── Action button on its own row directly below the version ──
        action_row = QHBoxLayout()
        action_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._action_btn = QPushButton(t("about.check_updates"))
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn_base_qss = (
            f"QPushButton {{ color: {theme.TEXT_SECONDARY}; font-size: 12px;"
            f" background: transparent; border: 1px solid {theme.BORDER_DEFAULT};"
            " border-radius: 12px; padding: 5px 16px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_STRONG}; }}"
            f"QPushButton:disabled {{ color: {theme.TEXT_MUTED};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; }}"
        )
        self._action_btn_accent_qss = (
            f"QPushButton {{ color: #ffffff; font-size: 12px;"
            f" background: {theme.ACCENT_BLUE};"
            f" border: 1px solid {theme.ACCENT_BLUE};"
            " border-radius: 12px; padding: 5px 16px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_BLUE_HOVER};"
            f" border: 1px solid {theme.ACCENT_BLUE_HOVER}; }}"
        )
        self._action_btn.setStyleSheet(self._action_btn_base_qss)
        self._action_btn.clicked.connect(self._on_action)
        action_row.addWidget(self._action_btn)
        ly.addLayout(action_row)

        # Status row + progress bar — empty / hidden in idle state.
        ly.addSpacing(10)

        self._status_lbl = QLabel("")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px;"
        )
        # WordWrap + a 560 px cap was clipping "You're on the latest
        # version." in half — Qt's QVBoxLayout under stretches doesn't
        # always grant a wrapped QLabel its full heightForWidth, and
        # the second line bled outside the visible area. Status
        # messages are short single sentences; rendering them as a
        # single line that elides if absurdly long is the calmer fit.
        self._status_lbl.setWordWrap(False)
        self._status_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )
        ly.addWidget(self._status_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        self._progress = QProgressBar()
        self._progress.setFixedSize(280, 6)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar {{ background: {theme.BG_ELEVATED};"
            " border: none; border-radius: 3px; }"
            f"QProgressBar::chunk {{ background: {theme.ACCENT_BLUE};"
            " border-radius: 3px; }}"
        )
        self._progress.hide()
        ly.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)

        # Optional release-notes link, shown only when an update is
        # available. Clicking opens the GitHub release page in the
        # default browser without affecting the update flow.
        self._notes_btn = QPushButton(t("about.update.notes_link"))
        self._notes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._notes_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.ACCENT_BLUE}; background: transparent;"
            " border: none; font-size: 11px; padding: 2px 6px;"
            " text-decoration: underline; }"
        )
        self._notes_btn.hide()
        ly.addWidget(self._notes_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Internal state
        self._mode = "idle"  # idle / checking / available / downloading / ready / installing
        self._update_info: Optional[UpdateInfo] = None
        self._zip_path: Optional[pathlib.Path] = None
        self._check_worker: Optional[_CheckWorker] = None
        self._download_worker: Optional[_DownloadWorker] = None

        ly.addSpacing(14)

        # ── Tagline ──
        tagline = QLabel(t("about.tagline"))
        tagline.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 14px; font-style: italic;")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(tagline)

        ly.addStretch()

        # ── Footer links (inline like 闪电说) ──
        footer_links = QHBoxLayout()
        footer_links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer_links.setSpacing(0)

        def _link(text: str, url: str) -> QPushButton:
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ color: {theme.TEXT_MUTED}; background: transparent;"
                " border: none; font-size: 12px; padding: 4px 12px; }}"
                f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}"
            )
            btn.clicked.connect(lambda: webbrowser.open(url))
            return btn

        def _divider() -> QLabel:
            d = QLabel("|")
            d.setStyleSheet(f"color: {theme.BORDER_DEFAULT}; font-size: 12px; padding: 0 4px;")
            return d

        footer_links.addWidget(_link(t("about.website"), "https://github.com/realAllenSong/ThunderTalk"))
        footer_links.addWidget(_divider())
        footer_links.addWidget(_link(t("about.report_issue"), "https://github.com/realAllenSong/ThunderTalk/issues"))
        footer_links.addWidget(_divider())
        footer_links.addWidget(_link(t("about.license"), "https://github.com/realAllenSong/ThunderTalk/blob/main/LICENSE"))
        ly.addLayout(footer_links)

        ly.addSpacing(8)

        # ── Copyright ──
        copyright = QLabel(t("about.copyright"))
        copyright.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        copyright.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(copyright)

    # ── Public API ──────────────────────────────────────────────

    def trigger_background_check(self) -> None:
        """Run a silent update check on app launch. If an update is
        found, transitions the page into the 'available' state so
        the next time the user lands on About they see the prompt;
        otherwise stays idle without bothering the user with a
        'you're up to date' message they didn't ask for."""
        if installed_app_path() is None:
            return  # running from source, no point checking
        if self._mode != "idle":
            return
        self._start_check(silent_when_uptodate=True)

    # ── State machine ───────────────────────────────────────────

    def _on_action(self) -> None:
        """The single button cycles through the update states based
        on _mode. One button keeps the UI calm — never two competing
        prompts on screen at once."""
        if self._mode == "idle":
            if installed_app_path() is None:
                self._set_status(t("about.update.dev_mode"), muted=True)
                return
            self._start_check(silent_when_uptodate=False)
        elif self._mode == "available":
            self._start_download()
        elif self._mode == "ready":
            self._start_install()

    def _start_check(self, silent_when_uptodate: bool) -> None:
        self._mode = "checking"
        self._action_btn.setEnabled(False)
        self._action_btn.setText(t("about.check_updates"))
        self._action_btn.setStyleSheet(self._action_btn_base_qss)
        self._set_status(t("about.update.checking"), muted=True)
        self._notes_btn.hide()
        self._progress.hide()

        self._silent_when_uptodate = silent_when_uptodate
        self._check_worker = _CheckWorker(thundertalk.__version__)
        self._check_worker.done.connect(self._on_check_done)
        self._check_worker.finished.connect(self._check_worker.deleteLater)
        self._check_worker.start()

    def _on_check_done(self, info: Optional[UpdateInfo]) -> None:
        self._action_btn.setEnabled(True)
        if info is None:
            # Either you're on the latest, or the network failed.
            # Showing "couldn't reach GitHub" only on user-initiated
            # checks; background checks just silently stay idle.
            self._mode = "idle"
            if not self._silent_when_uptodate:
                self._set_status(t("about.update.up_to_date"), muted=True)
            else:
                self._set_status("")
            return
        self._update_info = info
        self._mode = "available"
        self._set_status(
            t("about.update.available").format(version=info.version),
            muted=False,
        )
        self._action_btn.setText(t("about.update.download"))
        self._action_btn.setStyleSheet(self._action_btn_accent_qss)
        if info.release_url:
            try:
                self._notes_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self._notes_btn.clicked.connect(
                lambda _=False, url=info.release_url: webbrowser.open(url)
            )
            self._notes_btn.show()

    def _start_download(self) -> None:
        info = self._update_info
        if info is None:
            return
        self._mode = "downloading"
        self._action_btn.setEnabled(False)
        self._action_btn.setText(t("about.update.downloading").format(pct=0))
        self._action_btn.setStyleSheet(self._action_btn_base_qss)
        self._set_status("")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.show()

        self._download_worker = _DownloadWorker(info)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished_ok.connect(self._on_download_done)
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.finished.connect(
            self._download_worker.deleteLater
        )
        self._download_worker.start()

    def _on_download_progress(self, downloaded: int, total: int) -> None:
        if total <= 0:
            self._progress.setRange(0, 0)  # indeterminate
            return
        pct = int((downloaded / total) * 100)
        self._progress.setValue(pct)
        self._action_btn.setText(t("about.update.downloading").format(pct=pct))

    def _on_download_done(self, zip_path_str: str) -> None:
        self._zip_path = pathlib.Path(zip_path_str)
        self._mode = "ready"
        self._progress.hide()
        self._set_status("")
        self._action_btn.setEnabled(True)
        self._action_btn.setText(t("about.update.install_restart"))
        self._action_btn.setStyleSheet(self._action_btn_accent_qss)

    def _on_download_failed(self, msg: str) -> None:
        print(f"[Updater] download failed: {msg}")
        self._mode = "available"
        self._progress.hide()
        self._action_btn.setEnabled(True)
        self._action_btn.setText(t("about.update.download"))
        self._action_btn.setStyleSheet(self._action_btn_accent_qss)
        self._set_status(t("about.update.download_failed"), muted=False)

    def _start_install(self) -> None:
        if self._zip_path is None:
            return
        app_path = installed_app_path()
        if app_path is None:
            self._set_status(t("about.update.dev_mode"), muted=True)
            return
        self._mode = "installing"
        self._action_btn.setEnabled(False)
        self._action_btn.setText(t("about.update.installing"))
        self._action_btn.setStyleSheet(self._action_btn_base_qss)
        self._set_status(t("about.update.installing"), muted=True)
        try:
            install_update(self._zip_path, app_path)
        except Exception as e:
            print(f"[Updater] install failed: {e}")
            self._set_status(str(e), muted=False)
            self._mode = "ready"
            self._action_btn.setEnabled(True)
            self._action_btn.setText(t("about.update.install_restart"))
            self._action_btn.setStyleSheet(self._action_btn_accent_qss)
            return
        # Helper script is now waiting on our PID. Quit so it can swap.
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    # ── helpers ──

    def _set_status(self, text: str, muted: bool = True) -> None:
        self._status_lbl.setText(text)
        color = theme.TEXT_MUTED if muted else theme.ACCENT_BLUE
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")
