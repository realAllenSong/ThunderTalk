"""Sung-lyrics editor and advisory lyric-check details for Compose."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from thundertalk.core import music_lyrics
from thundertalk.core.i18n import t
from thundertalk.ui import theme


def label(text):
    w = QLabel(text)
    w.setWordWrap(True)
    w.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
    return w


class SungLyrics(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.overrides = {}
        self._lyrics, self._lang = "", "en"
        self._fields = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = theme.make_button("", "ghost", 28, font_px=12)
        self.toggle.setCheckable(True)
        layout.addWidget(self.toggle)
        self.host = QWidget()
        self.rows = QVBoxLayout(self.host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.host)
        self.host.hide()
        self.toggle.toggled.connect(self._expand)
        self.retranslate()

    def retranslate(self):
        self.toggle.setText(t("studio.compose.sung.title"))
        self._rebuild()

    def set_lyrics(self, lyrics, lang):
        self._lyrics, self._lang = lyrics, lang
        if self.toggle.isChecked():
            self._rebuild()

    def _expand(self, on):
        if on:
            self._rebuild()
        self.host.setVisible(on)

    def _rebuild(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._fields = {}
        self.rows.addWidget(label(t("studio.compose.sung.advice")))
        for shown, sung in music_lyrics.sung_pairs(self._lyrics, self._lang, self.overrides):
            host = QWidget()
            col = QVBoxLayout(host)
            col.setContentsMargins(0, 4, 0, 4)
            row = QHBoxLayout()
            row.addWidget(label(shown.text), 1)
            edit = QLineEdit(sung)
            edit.setStyleSheet(theme.INPUT_QSS)
            edit.editingFinished.connect(lambda e=edit, text=shown.text: self._edited(text, e.text()))
            row.addWidget(edit, 1)
            self._fields[shown.text] = edit
            reset = theme.make_button(t("studio.compose.sung.reset"), "ghost", 26, font_px=11)
            reset.clicked.connect(lambda _=False, text=shown.text: self._reset(text))
            row.addWidget(reset)
            col.addLayout(row)
            if self._lang in ("zh", "yue"):
                chips = QHBoxLayout()
                for word in music_lyrics.latin_words(shown.text):
                    suggestion = music_lyrics.suggest_sung(word, self._lang)
                    chip = theme.make_button(f"{word} → {suggestion}", "secondary", 26, font_px=11)
                    chip.clicked.connect(lambda _=False, text=shown.text, w=word, s=suggestion:
                                         self.accept_word(text, w, s))
                    chips.addWidget(chip)
                chips.addStretch()
                col.addLayout(chips)
            self.rows.addWidget(host)

    def _edited(self, text, sung):
        sung = sung.strip()
        if not sung or '\n' in sung:
            return
        self.overrides[text] = sung
        # A single Latin word has an unambiguous replacement inside its line.
        words = music_lyrics.latin_words(text)
        if self._lang in ("zh", "yue") and len(words) == 1:
            word = words[0]
            before, after = text.split(word, 1)
            if sung.startswith(before) and sung.endswith(after):
                replacement = sung[len(before):len(sung) - len(after) if after else None]
                if replacement:
                    try:
                        music_lyrics.remember(word, replacement)
                    except OSError:
                        pass
        self.changed.emit()

    def accept_word(self, text, word, sung):
        current = self.overrides.get(text, music_lyrics.sung_line(text, self._lang))
        old = music_lyrics.suggest_sung(word, self._lang)
        try:
            music_lyrics.remember(word, sung)
        except OSError:
            pass
        self.overrides[text] = current.replace(word, sung).replace(old, sung)
        self._rebuild()
        self.changed.emit()

    def _reset(self, text):
        self.overrides.pop(text, None)
        for word in music_lyrics.latin_words(text):
            try:
                music_lyrics.remember(word, "")
            except OSError:
                pass
        self._rebuild()
        self.changed.emit()

    def edit_line(self, text):
        self.toggle.setChecked(True)
        if text in self._fields:
            self._fields[text].setFocus()
            self._fields[text].selectAll()


class LyricCheckView(QWidget):
    action = Signal(int, str)

    def __init__(self, result, repaint_ok):
        super().__init__()
        self.result = result
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 8, 0, 8)
        col.addWidget(label(t("studio.compose.check.advice")))
        if not result.timed:
            col.addWidget(label(t("studio.compose.check.approximate")))
        self.line_labels = []
        for i, (line, check) in enumerate(zip(result.lines, result.checks)):
            text = label(line.text)
            if check.status != music_lyrics.CLEAR:
                text.setStyleSheet(f"color: {theme.WARNING}; font-size: 12px; background: transparent;")
            self.line_labels.append(text)
            col.addWidget(text)
            col.addWidget(label(t("studio.compose.check.heard").format(
                text=check.heard or t("studio.compose.check.not_heard"))))
            row = QHBoxLayout()
            for action in ("play_line", "edit_sung", "repaint_line"):
                btn = theme.make_button(t(f"studio.compose.check.{action}"), "ghost", 26, font_px=11)
                timed = i < len(result.line_ranges) and result.line_ranges[i] is not None
                btn.setEnabled(action == "edit_sung" or (timed and (action != "repaint_line" or repaint_ok)))
                btn.clicked.connect(lambda _=False, n=i, a=action: self.action.emit(n, a))
                row.addWidget(btn)
            row.addStretch()
            col.addLayout(row)
        new = theme.make_button(t("studio.compose.check.new_take"), "secondary", 28, font_px=12)
        new.clicked.connect(lambda: self.action.emit(-1, "new_take"))
        col.addWidget(new)
