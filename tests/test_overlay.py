from thundertalk.ui.overlay import VoiceOverlay


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
