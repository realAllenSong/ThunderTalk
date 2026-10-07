"""Widgets for Studio ▸ Compose: the lyrics editor with section tags, the
length / BPM / seed fields and one row per finished take."""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMenu, QPlainTextEdit, QVBoxLayout, QWidget

from thundertalk.core import music_lyrics
from thundertalk.core.i18n import t
from thundertalk.ui import theme
from thundertalk.ui.studio.parts import RoundButton, _ElidedLabel

SECTIONS = ("intro", "verse", "pre-chorus", "chorus", "bridge", "outro")
SECTION_RE = re.compile(r"^\s*\[([^\]\n]+)\]\s*$")
_TAG_QRE = QRegularExpression(r"^\s*\[[^\]\n]+\]\s*$")


def fmt_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_mmss(text: str) -> Optional[int]:
    """"2:50" → 170, "95" → 95, "" / garbage → None."""
    s = text.strip()
    m = re.fullmatch(r"(\d{1,3})(?::(\d{1,2}))?", s)
    if not m:
        return None
    if m.group(2) is None:
        return int(m.group(1))
    if int(m.group(2)) >= 60:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def section_tags(lyrics: str) -> list[str]:
    """The section tags in order, lower-cased: ["verse", "chorus", …]."""
    return [m.group(1).strip().lower() for line in lyrics.splitlines() if (m := SECTION_RE.match(line))]


def unknown_sections(lyrics: str) -> list[str]:
    """Tags the model wasn't trained on (shown as a hint; they still go through)."""
    known = set(SECTIONS)
    return [tag for tag in section_tags(lyrics) if re.sub(r"\s*\d+$", "", tag) not in known]


# ── lyrics editor ────────────────────────────────────────────────────────

class SectionHighlighter(QSyntaxHighlighter):
    """Section-tag lines ("[chorus]") in bold accent; unknown tags in amber."""

    def __init__(self, doc) -> None:
        super().__init__(doc)
        self._tag = QTextCharFormat()
        self._tag.setForeground(theme.qcolor(theme.ACCENT_BLUE))
        self._tag.setFontWeight(700)
        self._odd = QTextCharFormat()
        self._odd.setForeground(theme.qcolor(theme.WARNING))
        self._odd.setFontWeight(700)

    def highlightBlock(self, text: str) -> None:
        for word in music_lyrics.latin_words(text):
            fmt = QTextCharFormat()
            fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DotLine)
            fmt.setUnderlineColor(theme.qcolor(theme.ACCENT_BLUE))
            for match in re.finditer(re.escape(word), text):
                self.setFormat(match.start(), len(word), fmt)
        if not _TAG_QRE.match(text).hasMatch():
            return
        fmt = self._tag if not unknown_sections(text) else self._odd
        self.setFormat(0, len(text), fmt)


class LyricsEdit(QPlainTextEdit):
    """Plain-text lyrics with highlighted section tags and an insert menu."""

    pronounced = Signal(str, str, str)  # line, word, accepted sung form

    def __init__(self) -> None:
        super().__init__()
        self.setTabChangesFocus(True)
        self.setMinimumHeight(180)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.BG_CARD}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: {theme.RADIUS_CONTROL}px;"
            f" padding: 10px 12px; font-size: 14px; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {theme.INK}; }}")
        self._hl = SectionHighlighter(self.document())

    def contextMenuEvent(self, event) -> None:
        menu = self.createStandardContextMenu()
        cursor = self.cursorForPosition(event.pos())
        line = cursor.block().text()
        pos = cursor.positionInBlock()
        for match in re.finditer(r"[A-Za-z]+", line):
            if match.start() <= pos <= match.end() and match.group() in music_lyrics.latin_words(line):
                word = match.group()
                sung = music_lyrics.suggest_sung(word, "zh")
                menu.addSeparator()
                menu.addAction(f"{word} → {sung}", lambda: self.pronounced.emit(line, word, sung))
                break
        menu.exec(event.globalPos())

    def insert_section(self, tag: str) -> None:
        """Put "[tag]" on its own line at the cursor, with a blank line before
        it when the previous line has text."""
        cur = self.textCursor()
        cur.beginEditBlock()
        line = cur.block().text()
        if line.strip():
            cur.movePosition(cur.MoveOperation.EndOfBlock)
            cur.insertText("\n")
        prev = cur.block().previous()
        if prev.isValid() and prev.text().strip():
            cur.insertText("\n")
        cur.insertText(f"[{tag}]\n")
        cur.endEditBlock()
        self.setTextCursor(cur)

    def section_menu(self, parent: Optional[QWidget] = None) -> QMenu:
        menu = QMenu(parent or self)
        for tag in SECTIONS:
            menu.addAction(f"[{tag}]", lambda tag=tag: self.insert_section(tag))
        return menu


# ── settings fields ──────────────────────────────────────────────────────

class LengthField(QLineEdit):
    """Song length as m:ss (or plain seconds), clamped to [lo, hi] on edit."""

    changed = Signal(int)

    def __init__(self, seconds: int, lo: int, hi: int) -> None:
        super().__init__()
        self._lo, self._hi = lo, hi
        self._value = self._clamp(seconds)
        self.setStyleSheet(theme.INPUT_QSS)
        self.setFixedWidth(84)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,3}(:\d{0,2})?"), self))
        self.setText(fmt_mmss(self._value))
        self.editingFinished.connect(self._commit)

    def _clamp(self, s: int) -> int:
        return max(self._lo, min(self._hi, int(s)))

    def value(self) -> int:
        return self._value

    def set_value(self, seconds: int) -> None:
        self._value = self._clamp(seconds)
        self.setText(fmt_mmss(self._value))

    def set_bounds(self, lo: int, hi: int) -> None:
        self._lo, self._hi = lo, hi
        old = self._value
        self.set_value(old)
        if self._value != old:
            self.changed.emit(self._value)

    def bounds(self) -> tuple[int, int]:
        return self._lo, self._hi

    def _commit(self) -> None:
        parsed = parse_mmss(self.text())
        old = self._value
        self.set_value(old if parsed is None else parsed)
        if self._value != old:
            self.changed.emit(self._value)


class OptionalIntField(QLineEdit):
    """A number in [lo, hi] or empty for "automatic" (BPM: Auto, seed: Random);
    the placeholder names the automatic choice."""

    def __init__(self, lo: int, hi: int, placeholder: str, width: int = 84) -> None:
        super().__init__()
        self._lo, self._hi = lo, hi
        self.setStyleSheet(theme.INPUT_QSS)
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPlaceholderText(placeholder)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,%d}" % len(str(hi))), self))
        self.editingFinished.connect(self._commit)

    def value(self) -> Optional[int]:
        s = self.text().strip()
        return max(self._lo, min(self._hi, int(s))) if s.isdigit() else None

    def set_value(self, v: Optional[int]) -> None:
        self.setText("" if v is None else str(max(self._lo, min(self._hi, int(v)))))

    def _commit(self) -> None:
        self.set_value(self.value())


# ── takes ────────────────────────────────────────────────────────────────

class TakeRow(QWidget):
    """One finished take: ▶, "Take 2 · seed 1234 · 2:50", a lyric-check badge,
    Export WAV… and a ⋯ menu (Repaint a range…, Delete)."""

    action = Signal(str)                  # "play" | "export" | "repaint" | "delete"

    def __init__(self, index: int, seed: int, seconds: float, path: str) -> None:
        super().__init__()
        self.index, self.seed, self.seconds, self.path = index, seed, seconds, path
        self.setStyleSheet("background: transparent;")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 6, 0, 6)
        col.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        self._play = RoundButton("play", 30)
        self._play.clicked.connect(lambda: self.action.emit("play"))
        row.addWidget(self._play)
        self._label = _ElidedLabel()
        self._label.setFont(theme.font(13, bold=True))
        self._label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        row.addWidget(self._label, 1)
        self._badge = theme.badge("", "muted")
        self._badge.setVisible(False)
        row.addWidget(self._badge)
        self._export = theme.make_button("", "secondary", 28, font_px=12)
        self._export.clicked.connect(lambda: self.action.emit("export"))
        row.addWidget(self._export)
        self._more = theme.make_button("⋯", "ghost", 28, font_px=14)
        self._more.clicked.connect(self._show_menu)
        row.addWidget(self._more)
        col.addLayout(row)
        self.check_button = theme.make_button(t("studio.compose.check.details"), "ghost", 26, font_px=11)
        self.check_button.clicked.connect(lambda: self.action.emit("check"))
        self.check_button.hide()
        col.addWidget(self.check_button)
        self._repaint_ok = False
        self.retranslate()

    def label_text(self) -> str:
        return self._label.full()

    def badge_text(self) -> str:
        return "" if self._badge.isHidden() else self._badge.text()

    def set_playing(self, on: bool) -> None:
        self._play.set_icon("pause" if on else "play")

    def set_check(self, clear: int, total: int) -> None:
        """Lyric-check result; total 0 hides the badge (instrumental)."""
        if total <= 0:
            self._badge.setVisible(False)
            return
        bad = total - clear
        if bad:
            self._badge.setText(t("studio.compose.check.flagged").format(n=bad))
            self._badge.set_colors(*theme.PASTELS["amber"])
        else:
            self._badge.setText(t("studio.compose.check.clear").format(clear=clear, total=total))
            self._badge.set_colors(*theme.PASTELS["green"])
        self._badge.setVisible(True)

    def set_repaint_enabled(self, on: bool) -> None:
        self._repaint_ok = on

    def menu(self) -> QMenu:
        m = QMenu(self)
        rp = m.addAction(t("studio.compose.take.repaint"), lambda: self.action.emit("repaint"))
        rp.setEnabled(self._repaint_ok)
        m.addAction(t("studio.compose.take.delete"), lambda: self.action.emit("delete"))
        return m

    def _show_menu(self) -> None:
        self.menu().exec(self._more.mapToGlobal(self._more.rect().bottomLeft()))

    def retranslate(self) -> None:
        self._label.set_full(t("studio.compose.take.line").format(
            i=self.index, seed=self.seed, dur=fmt_mmss(round(self.seconds))))
        self._export.setText(t("studio.compose.take.export"))
        self._play.setToolTip(t("studio.compose.take.play"))
