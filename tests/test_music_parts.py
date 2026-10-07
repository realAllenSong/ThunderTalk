"""Compose widgets: lyrics editor, length / optional-number fields, take rows."""
from __future__ import annotations

import pytest

from thundertalk.core.i18n import set_language
from thundertalk.ui.studio import music_parts as mp


@pytest.fixture(autouse=True)
def _english():
    set_language("en")
    yield
    set_language("en")


@pytest.mark.parametrize("text,want", [
    ("2:50", 170), ("0:10", 10), ("95", 95), ("10:00", 600), (" 3:05 ", 185),
    ("", None), ("1:75", None), ("a", None), ("1:2:3", None),
])
def test_parse_mmss(text, want):
    assert mp.parse_mmss(text) == want


def test_fmt_mmss_round_trips():
    for s in (10, 59, 60, 170, 600):
        assert mp.parse_mmss(mp.fmt_mmss(s)) == s


def test_section_tags_and_unknown_ones():
    lyrics = "[Intro]\n\n[verse 2]\nla la\n[chorus]\nhey\n[drop]\n[ bridge ]\nnot [a tag] here"
    assert mp.section_tags(lyrics) == ["intro", "verse 2", "chorus", "drop", "bridge"]
    assert mp.unknown_sections(lyrics) == ["drop"]


def test_insert_section_puts_the_tag_on_its_own_line(qapp):
    ed = mp.LyricsEdit()
    ed.insert_section("verse")
    assert ed.toPlainText() == "[verse]\n"
    ed.insertPlainText("first line")
    ed.insert_section("chorus")
    assert ed.toPlainText() == "[verse]\nfirst line\n\n[chorus]\n"
    assert [a.text() for a in ed.section_menu().actions()] == [f"[{s}]" for s in mp.SECTIONS]


def test_length_field_clamps_and_reports_changes(qapp):
    f = mp.LengthField(170, 10, 600)
    seen = []
    f.changed.connect(seen.append)
    assert f.text() == "2:50" and f.value() == 170
    f.setText("12:00")
    f.editingFinished.emit()
    assert f.value() == 600 and f.text() == "10:00" and seen == [600]
    f.setText("3")
    f.editingFinished.emit()
    assert f.value() == 10
    f.setText("")
    f.editingFinished.emit()
    assert f.value() == 10 and seen == [600, 10]
    f.set_value(300)
    f.set_bounds(10, 240)
    assert f.value() == 240 and seen[-1] == 240 and f.bounds() == (10, 240)


def test_optional_int_field_empty_means_auto(qapp):
    bpm = mp.OptionalIntField(30, 300, "Auto")
    assert bpm.value() is None and bpm.placeholderText() == "Auto"
    bpm.setText("12")
    bpm.editingFinished.emit()
    assert bpm.value() == 30 and bpm.text() == "30"
    bpm.set_value(999)
    assert bpm.value() == 300
    bpm.set_value(None)
    assert bpm.text() == "" and bpm.value() is None


def test_take_row_label_badge_and_actions(qapp):
    row = mp.TakeRow(2, 1234, 170.4, "/tmp/take-2.wav")
    assert row.label_text() == "Take 2 · seed 1234 · 2:50"
    assert row.badge_text() == ""
    row.set_check(12, 14)
    assert row.badge_text() == "Check 2 lines"
    row.set_check(14, 14)
    assert row.badge_text() == "14 / 14 lines clear"
    row.set_check(0, 0)
    assert row.badge_text() == ""

    seen = []
    row.action.connect(seen.append)
    row._play.click()
    row._export.click()
    m = row.menu()
    repaint, delete = m.actions()
    assert not repaint.isEnabled()
    row.set_repaint_enabled(True)
    repaint, delete = row.menu().actions()
    assert repaint.isEnabled()
    repaint.trigger()
    delete.trigger()
    assert seen == ["play", "export", "repaint", "delete"]


def test_take_row_retranslates(qapp):
    row = mp.TakeRow(1, 7, 65, "/tmp/t.wav")
    set_language("zh")
    row.retranslate()
    assert row.label_text() == "第 1 版 · 种子 7 · 1:05"
    assert row._export.text() == "导出 WAV…"
