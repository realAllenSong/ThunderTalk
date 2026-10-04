from thundertalk.ui.overlay import VoiceOverlay
from thundertalk.core.proofread_diff import display_text
from thundertalk.core.i18n import t
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import pytest


def test_stale_hide_timer_cannot_hide_the_next_recording(qapp) -> None:
    """show_result() schedules a hide; starting a new recording must cancel it
    (a leftover singleShot used to hide the fresh recording overlay)."""
    ov = VoiceOverlay()
    ov.show_result("done")
    assert ov._hide_timer.isActive()
    ov.show_recording()
    assert not ov._hide_timer.isActive()
    assert ov._state == VoiceOverlay._RECORDING
    ov.hide_overlay()


def test_error_then_transcribing_cancels_pending_hide(qapp) -> None:
    ov = VoiceOverlay()
    ov.show_error("nope")
    assert ov._hide_timer.isActive()
    ov.show_transcribing()
    assert not ov._hide_timer.isActive()
    ov.hide_overlay()


def test_levels_scroll_and_are_bounded(qapp) -> None:
    ov = VoiceOverlay()
    ov.show_recording()
    for i in range(200):
        ov.set_audio_level(0.5 if i % 2 else 5.0)   # 5.0 is way over range
    assert all(0.0 <= v <= 1.0 for v in ov._levels)
    assert len(ov._levels) == ov._levels.maxlen
    ov.hide_overlay()


def test_cleanup_working_diff_and_readable_frames(qapp):
    ov = VoiceOverlay()
    ov.show_cleanup('这个 promp 要改一下')
    assert ov._state == ov._CLEANUP and ov._anim.isActive()
    working = ov.grab().toImage()
    ov.show_cleanup_diff('这个 promp 要改一下', '这个 prompt 要改一下')
    ov.advance_cleanup_animation(0.0)
    assert ov._state == ov._DIFF and not ov._hide_timer.isActive()
    frame = ov.grab().toImage()
    ov.advance_cleanup_animation(0.8)
    assert frame != ov.grab().toImage()
    ov.advance_cleanup_animation(1.65)
    assert ov._state == ov._DIFF and not ov._hide_timer.isActive()
    assert working != ov.grab().toImage()
    ov.advance_cleanup_animation(ov._diff_pages[0].duration)
    assert ov._state == ov._IDLE and not ov._anim.isActive()
    ov.show_recording()
    assert not ov._hide_timer.isActive() and ov._state == ov._RECORDING
    ov.advance_cleanup_animation(2)
    assert ov._state == ov._RECORDING
    ov.hide_overlay()


def test_cleanup_no_changes_tick_and_timer(qapp):
    ov = VoiceOverlay()
    ov.show_cleanup_diff('明天开会。', '明天开会。')
    assert ov._state == ov._RESULT and ov._hide_timer.interval() == 900
    assert ov._text == t('cleanup.no_changes')
    ov.show_cleanup('next')
    assert not ov._hide_timer.isActive()
    ov.hide_overlay()


@pytest.mark.parametrize('before,after', [
    ('明天再三楼开会，然后用新的模形测试，最后记路结果。',
     '明天在三楼开会，然后用新的模型测试，最后记录结果。'),
    ('Use lama index for retrieval. The next stage uses rag. Then run promt checks.',
     'Use LlamaIndex for retrieval. The next stage uses RAG. Then run prompt checks.'),
    ('我们用 lama index 加上 rag 做检索，然后在三楼用 promp 测试。',
     '我们用 LlamaIndex 加上 RAG 做检索，然后在三楼用 prompt 测试。'),
    ('x' * 1000, '更' * 1000),
    ('start ' + 'wrong ' * 200 + 'end', 'start ' + 'correct ' * 200 + 'end'),
    ('', '新增的内容'), ('删除的内容', ''),
    ('test中文𠀀 ok', 'test中文𠀁 OK'),
])
def test_pages_fit_actual_overlay_font(qapp, before, after):
    ov = VoiceOverlay()
    ov.show_cleanup_diff(before, after)
    assert ov._diff_pages
    for page in ov._diff_pages:
        assert ov._diff_layout(display_text(page.spans)).lineCount() <= 2
        assert 1 <= page.changes <= 3
    assert sum(p.duration for p in ov._diff_pages) <= 25
    ov.hide_overlay()


def test_pages_advance_and_expire_without_sleeps(qapp):
    ov = VoiceOverlay()
    before = '。'.join(f'第{i}段用错词继续说明' for i in range(40))
    after = before.replace('错词', '术语')
    ov.show_cleanup_diff(before, after)
    pages = list(ov._diff_pages)
    assert len(pages) > 1 and pages[-1].omitted > 0
    elapsed = 0
    for i, page in enumerate(pages):
        ov.advance_cleanup_animation(elapsed)
        assert ov._diff_page == i and ov._diff == page.spans
        transition = ov.grab().toImage()
        ov.advance_cleanup_animation(elapsed + 0.2)
        assert transition != ov.grab().toImage()
        ov.advance_cleanup_animation(elapsed + page.duration - 0.001)
        assert ov._state == ov._DIFF and ov._diff_page == i
        elapsed += page.duration
    ov.advance_cleanup_animation(elapsed + 0.001)
    assert ov._state == ov._IDLE and not ov.isVisible()


def test_hover_pauses_and_resumes_remaining_page_time(qapp, monkeypatch):
    now = [100.0]
    monkeypatch.setattr('thundertalk.ui.overlay.time.monotonic', lambda: now[0])
    ov = VoiceOverlay()
    ov.show_cleanup_diff('明天再三楼开会', '明天在三楼开会')
    duration = ov._diff_pages[0].duration
    now[0] += 1
    QApplication.sendEvent(ov, QEnterEvent(QPointF(20, 20), QPointF(20, 20), QPointF(20, 20)))
    assert not ov._anim.isActive()
    now[0] += 100
    ov.advance_cleanup_animation(now[0] - ov._diff_started)
    assert ov._state == ov._DIFF
    QApplication.sendEvent(ov, QEvent(QEvent.Type.Leave))
    assert ov._anim.isActive() and ov._paused_seconds == 100
    now[0] += duration - 1 - 0.01
    ov._tick()
    assert ov._state == ov._DIFF
    now[0] += 0.02
    ov._tick()
    assert ov._state == ov._IDLE


def test_click_dismisses_paused_diff(qapp):
    ov = VoiceOverlay()
    ov.show_cleanup_diff('old', 'new')
    QApplication.sendEvent(ov, QEnterEvent(QPointF(20, 20), QPointF(20, 20), QPointF(20, 20)))
    QTest.mouseClick(ov, Qt.MouseButton.LeftButton)
    assert not ov.isVisible() and ov._state == ov._IDLE
    assert not ov._anim.isActive() and not ov._hide_timer.isActive()
    assert not ov._diff_pages and ov._pause_started is None


def test_recording_replaces_paused_diff_and_stale_advancement(qapp, monkeypatch):
    now = [100.0]
    monkeypatch.setattr('thundertalk.ui.overlay.time.monotonic', lambda: now[0])
    ov = VoiceOverlay()
    ov.show_cleanup_diff('old', 'new')
    QApplication.sendEvent(ov, QEnterEvent(QPointF(), QPointF(), QPointF()))
    ov.show_recording()
    assert ov._state == ov._RECORDING and not ov._diff_pages
    assert ov._pause_started is None and not ov._hide_timer.isActive()
    now[0] += 100
    ov.advance_cleanup_animation(100)
    ov._tick()
    QApplication.sendEvent(ov, QEvent(QEvent.Type.Leave))
    assert ov._state == ov._RECORDING and ov.isVisible() and ov._anim.interval() == 250
    ov.hide_overlay()


def test_already_hovered_overlay_pauses_new_diff(qapp, monkeypatch):
    now = [100.0]
    monkeypatch.setattr('thundertalk.ui.overlay.time.monotonic', lambda: now[0])
    ov = VoiceOverlay()
    ov.show_cleanup('old')
    QApplication.sendEvent(ov, QEnterEvent(QPointF(), QPointF(), QPointF()))
    ov.show_cleanup_diff('old', 'new')
    assert ov._pause_started == 100 and not ov._anim.isActive()
    now[0] += 30
    ov.advance_cleanup_animation(30)
    assert ov._state == ov._DIFF and ov._diff_progress == 0
    QApplication.sendEvent(ov, QEvent(QEvent.Type.Leave))
    now[0] += 1
    ov._tick()
    assert ov._state == ov._DIFF and ov._diff_progress == 1
    ov.hide_overlay()


def test_hover_on_later_page_preserves_page_and_remaining_budget(qapp, monkeypatch):
    now = [100.0]
    monkeypatch.setattr('thundertalk.ui.overlay.time.monotonic', lambda: now[0])
    ov = VoiceOverlay()
    before = '。'.join(f'第{i}段用错词继续说明' for i in range(12))
    ov.show_cleanup_diff(before, before.replace('错词', '术语'))
    total = sum(p.duration for p in ov._diff_pages)
    now[0] += ov._diff_pages[0].duration + 1
    QApplication.sendEvent(ov, QEnterEvent(QPointF(), QPointF(), QPointF()))
    assert ov._diff_page == 1
    for pause in (10, 20):
        now[0] += pause
        ov.advance_cleanup_animation(now[0] - ov._diff_started)
        assert ov._diff_page == 1
        QApplication.sendEvent(ov, QEvent(QEvent.Type.Leave))
        QApplication.sendEvent(ov, QEnterEvent(QPointF(), QPointF(), QPointF()))
    QApplication.sendEvent(ov, QEvent(QEvent.Type.Leave))
    assert ov._paused_seconds == pytest.approx(30)
    now[0] = 100 + 30 + total + 0.01
    ov._tick()
    assert ov._state == ov._IDLE
