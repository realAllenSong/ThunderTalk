"""First-run transitions with real widgets and fake permissions/downloads."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from thundertalk.core import i18n, models, platform_utils as pu, state as st
from thundertalk.core.i18n import t
from thundertalk.core.settings import Settings
from thundertalk.core.state import AppState
from thundertalk.ui.onboarding import OnboardingOverlay, recommended_model


class FakeModels(QObject):
    download_progress = Signal(str, int, str)
    model_download_completed = Signal(str)
    download_failed = Signal(str, str)
    download_cancelled = Signal(str)
    hardware_detected = Signal(object)

    def __init__(self):
        super().__init__()
        self.hardware = models.HardwareInfo("Apple M3 Max", 36, "Apple", "apple-silicon")
        self.started, self.cancelled, self.activated = [], [], []

    def start_download(self, mid):
        self.started.append(mid)

    def cancel_download(self, mid):
        self.cancelled.append(mid)

    def activate_model(self, mid):
        self.activated.append(mid)
        return True


@pytest.fixture
def wizard(qapp, isolated_home, monkeypatch):
    permissions = SimpleNamespace(mic="not_determined", acc=False)
    monkeypatch.setattr(pu, "check_microphone", lambda: permissions.mic)
    monkeypatch.setattr(pu, "check_accessibility", lambda: permissions.acc)
    monkeypatch.setattr("thundertalk.ui.onboarding.is_downloaded", lambda mid: False)
    host = QWidget()
    host.resize(1120, 780)
    page, settings, state = FakeModels(), Settings(), AppState()
    overlay = OnboardingOverlay(host, settings, state, page)
    overlay.resize(host.size())
    host.show()
    overlay.show()
    yield overlay, page, permissions, state, settings
    overlay._perm_timer.stop()
    host.close()


@pytest.mark.parametrize("cpu,ram,platform,mlx,expected", [
    ("M1", 8, "apple-silicon", True, "onnx"),
    ("M2", 16, "apple-silicon", True, "mlx"),
    ("M3 Max", 36, "apple-silicon", True, "mlx"),
    ("Intel", 16, "all", False, "onnx"),
    ("M2", 16, "apple-silicon", False, "onnx"),
])
def test_recommendation_uses_memory_and_mlx(monkeypatch, cpu, ram, platform, mlx, expected):
    monkeypatch.setattr("thundertalk.core.asr._IS_APPLE_SILICON", mlx)
    assert recommended_model(models.HardwareInfo(cpu, ram, cpu, platform)).backend == expected
    assert recommended_model().backend == "onnx"


def test_welcome_consent_overlaps_permissions_and_back_does_not_duplicate(wizard):
    overlay, page, _, _, _ = wizard
    assert not page.started
    assert "1.9 GB" in overlay._next.text()
    overlay._next.click()
    assert overlay._step == 1 and page.started == [overlay._rec.id]
    overlay._on_dl_progress(overlay._rec.id, 40, "752 / 1881 MB")
    assert "40%" in overlay._p_download.text()
    original = overlay._rec.id
    page.hardware_detected.emit(models.HardwareInfo("M1", 8, "M1", "apple-silicon"))
    assert overlay._rec.id == original
    overlay._back.click()
    overlay._next.click()
    assert page.started == [original]


def test_cancel_blocks_retry_until_worker_exits_then_can_retry(wizard):
    overlay, page, _, _, _ = wizard
    overlay._next.click()
    overlay._on_model_cancel()
    assert overlay._dl_phase == "cancelling" and page.cancelled == [overlay._rec.id]
    overlay._on_model_action()
    assert len(page.started) == 1
    page.download_cancelled.emit(overlay._rec.id)
    overlay._on_model_action()
    assert len(page.started) == 2


@pytest.mark.parametrize("message,key", [
    ("No network", "onb.model.failed"), ("connection reset mid-way", "onb.model.failed"),
    ("[Errno 28] No space left on device", "onb.model.disk_full"),
    ("curl: (23) Failed writing received data", "onb.model.disk_full"),
])
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_download_failure_has_localized_recovery(wizard, monkeypatch, message, key, lang):
    overlay, page, _, _, _ = wizard
    monkeypatch.setattr(i18n, "LANG", lang)
    overlay._on_model_action()
    overlay._go(2, animate=False)
    page.download_failed.emit(overlay._rec.id, message)
    assert overlay._m_status.text() == t(key)
    assert overlay._m_action.text() == t("onb.model.retry")
    assert overlay._m_other.isVisible()


def test_load_failure_can_retry_local_files(wizard, monkeypatch):
    overlay, page, _, state, _ = wizard
    monkeypatch.setattr("thundertalk.ui.onboarding.is_downloaded", lambda mid: True)
    state.set_model_error(overlay._rec.id, "OOM")
    overlay._go(2, animate=False)
    assert overlay._m_status.text() == t("onb.model.load_failed")
    overlay._m_action.click()
    assert page.activated == [overlay._rec.id] and not page.started


def test_missing_or_revoked_permissions_return_to_permission_step(wizard):
    overlay, _, perm, state, _ = wizard
    overlay._go(2, animate=False)
    state.set_model_ready(overlay._rec.id)
    assert overlay._next.isEnabled() and overlay._next.text() == t("onb.perm.review")
    overlay._next.click()
    assert overlay._step == 1
    perm.mic, perm.acc = "authorized", True
    overlay._perm_timer.timeout.emit()
    overlay._next.click()
    overlay._next.click()
    assert overlay._step == 3
    perm.acc = False
    overlay._perm_timer.timeout.emit()
    assert overlay._t_status.text() == t("onb.perm.review_hint")


def test_typed_text_is_not_dictation_and_finish_waits_for_recording(wizard):
    overlay, _, perm, state, settings = wizard
    perm.mic, perm.acc = "authorized", True
    overlay._go(3, animate=False)
    overlay._t_edit.setPlainText("Typed by hand")
    assert not overlay._trial_succeeded and overlay._next.text() == t("onb.try.later")
    state.set_recording(st.REC_RECORDING)
    assert not overlay._next.isEnabled()
    state.set_recording(st.REC_TRANSCRIBING)
    assert overlay._t_status.text() == t("status.transcribing")
    state.set_recording(st.REC_IDLE)
    assert not overlay.accept_dictation("")
    assert overlay.accept_dictation("Recognized sentence")
    assert overlay._t_edit.toPlainText() == "Recognized sentence"
    assert overlay._t_status.text().endswith(t("onb.try.success"))
    assert overlay._next.text() == t("onb.finish")
    overlay._next.click()
    assert settings.get("onboarding_done")


def test_hold_mode_and_retranslation(wizard, monkeypatch):
    overlay, _, _, _, settings = wizard
    settings.set("press_mode", "hold")
    overlay._go(3, animate=False)
    assert "Hold" in overlay._t_sub.text()
    monkeypatch.setattr(i18n, "LANG", "zh")
    overlay.retranslate()
    assert "按住" in overlay._t_sub.text()
    assert overlay._feats[0]._a.text() == t("onb.feat.private.title")


def test_skip_cancels_and_saves_but_back_does_not(wizard):
    overlay, page, _, _, settings = wizard
    overlay._next.click()
    overlay._back.click()
    assert not page.cancelled and not settings.get("onboarding_done")
    overlay._skip.click()
    assert settings.get("onboarding_done") and page.cancelled == [overlay._rec.id]


def test_disk_preflight_prevents_network(isolated_home, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: SimpleNamespace(free=1))
    monkeypatch.setattr(models, "is_downloaded", lambda mid: False)
    monkeypatch.setattr(models, "_hf_snapshot", lambda *args: pytest.fail("Network must not start"))
    with pytest.raises(OSError, match="disk full"):
        models.download_model(recommended_model())


def test_hotkey_recovery_destination(qapp, isolated_home, monkeypatch, no_audio_hw):
    from thundertalk.core.history import HistoryStore
    from thundertalk.ui.main_window import MainWindow
    monkeypatch.setattr(pu, "check_microphone", lambda: "authorized")
    monkeypatch.setattr(pu, "check_accessibility", lambda: True)
    window = MainWindow(Settings(), HistoryStore(), AppState())
    window.show_model_setup()
    assert window._stack.currentWidget() is window.models_page
    window.show_onboarding()
    window.show_model_setup()
    assert window._onboarding._step == 2
    window.models_page.shutdown_downloads()
    window.hide()
    window.deleteLater()


def test_first_launch_does_not_probe_optional_ai(qapp, isolated_home, monkeypatch):
    from thundertalk.ui.pages.proofread_page import ProofreadPage
    settings = Settings()
    page = ProofreadPage(settings, detect_clis=lambda _: [], detect_servers=lambda **_: [])
    calls = []
    monkeypatch.setattr(page, "refresh", lambda: calls.append("probe"))
    page.refresh_for_startup()
    assert not calls
    settings.set("llm_rewrite_enabled", True)
    page.refresh_for_startup()
    assert calls == ["probe"]
    page.shutdown()


def test_cancel_during_hf_metadata_never_starts_transfer(isolated_home, monkeypatch):
    import threading
    import huggingface_hub
    cancel = threading.Event()
    calls = []

    def snapshot(**kwargs):
        calls.append(kwargs)
        cancel.set()
        return []

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    with pytest.raises(models.DownloadCancelled):
        models._hf_snapshot("test/repo", None, None, None, None, cancel)
    assert len(calls) == 1 and calls[0]["dry_run"]


def test_no_mlx_installation_uses_cpu_without_importing_runtime(monkeypatch):
    monkeypatch.setattr("thundertalk.core.asr._IS_APPLE_SILICON", True)
    monkeypatch.setattr("thundertalk.core.asr._MLX_AVAILABLE", False)
    assert recommended_model(models.HardwareInfo("M3 Max", 36, "M3 Max", "apple-silicon")).backend == "onnx"


def test_stale_download_finish_cannot_remove_a_retry(qapp, isolated_home):
    from thundertalk.ui.pages.models_page import ModelsPage
    page = ModelsPage(Settings())
    old, retry = object(), object()
    page._workers["test"] = retry
    page._forget_download("test", old)
    assert page._workers["test"] is retry
    page._forget_download("test", retry)
    assert "test" not in page._workers
    page.shutdown_downloads()


def test_gui_bytes_progress_survives_globally_disabled_hf_bars(isolated_home, monkeypatch):
    import huggingface_hub
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_HUB_DISABLE_PROGRESS_BARS", True)
    events = []

    def snapshot(**kwargs):
        if kwargs.get("dry_run"):
            return [SimpleNamespace(file_size=100_000_000, will_download=True)]
        with kwargs["tqdm_class"](unit="B", total=100_000_000,
                                  name="huggingface_hub.snapshot_download") as bar:
            bar.update(50_000_000)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    models._hf_snapshot("test/repo", None, None, None, lambda p, m: events.append((p, m)), None)
    assert events[-1] == (50, "50 / 100 MB")


def test_right_hotkey_cap_is_localized(monkeypatch):
    from thundertalk.ui.keys import display_combo
    monkeypatch.setattr(i18n, "LANG", "zh")
    assert display_combo("cmd_r") == "右侧 ⌘"
