from thundertalk.core import state as st
from thundertalk.core.state import AppState, model_display_name


def test_model_lifecycle_emits_and_tracks(qapp) -> None:
    s = AppState("cmd_r")
    seen = []
    s.model_changed.connect(lambda: seen.append(s.model_status))

    assert s.model_status == st.MODEL_NONE
    s.set_model_loading("qwen3-asr-06b-mlx")
    s.set_model_ready("qwen3-asr-06b-mlx")
    s.set_model_error("qwen3-asr-06b-mlx", "boom")
    s.set_model_none()

    assert seen == [st.MODEL_LOADING, st.MODEL_READY, st.MODEL_ERROR, st.MODEL_NONE]
    assert s.model_error == "" and s.model_id == ""


def test_recording_signal_only_fires_on_change(qapp) -> None:
    s = AppState()
    seen = []
    s.recording_changed.connect(seen.append)
    s.set_recording(st.REC_RECORDING)
    s.set_recording(st.REC_RECORDING)          # duplicate → no signal
    s.set_recording(st.REC_TRANSCRIBING)
    s.set_recording(st.REC_IDLE)
    assert seen == [st.REC_RECORDING, st.REC_TRANSCRIBING, st.REC_IDLE]


def test_hotkey_and_level_signals(qapp) -> None:
    s = AppState("cmd_r")
    keys, levels = [], []
    s.hotkey_changed.connect(keys.append)
    s.level_changed.connect(levels.append)
    s.set_hotkey("cmd_r")                      # unchanged
    s.set_hotkey("alt_l+space")
    s.push_level(0.25)
    assert keys == ["alt_l+space"] and levels == [0.25]


def test_permissions_ok_requires_both(qapp, monkeypatch) -> None:
    from thundertalk.core import platform_utils as pu
    s = AppState()
    changed = []
    s.permissions_changed.connect(lambda: changed.append(1))

    monkeypatch.setattr(pu, "check_microphone", lambda: "denied")
    monkeypatch.setattr(pu, "check_accessibility", lambda: True)
    s.refresh_permissions()
    assert not s.permissions_ok and len(changed) == 1

    s.refresh_permissions()                    # same state → no new signal
    assert len(changed) == 1

    monkeypatch.setattr(pu, "check_microphone", lambda: "authorized")
    s.refresh_permissions()
    assert s.permissions_ok and len(changed) == 2


def test_model_display_name_falls_back_to_id() -> None:
    assert "Qwen3-ASR-0.6B" in model_display_name("qwen3-asr-06b-mlx")
    assert model_display_name("mystery") == "mystery"
    assert model_display_name("") == ""
