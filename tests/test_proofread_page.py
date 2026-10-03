"""AI Proofread page with fake providers and real Qt widgets/workers; no network."""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QPushButton

from thundertalk.core import llm_providers as lp
from thundertalk.core.i18n import set_language
from thundertalk.core.settings import Settings
from thundertalk.ui.pages.proofread_page import ProofreadPage


def wait_for(cond, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def cli(ident, status, models=("m1", "m2"), source="cli", **kw):
    spec = next(s for s in lp.CLIS if s.id == ident)
    return lp.Provider(ident, spec.name, list(models), ready=status == "ready", status=status,
                       executable="" if status == "not_installed" else "/bin/" + spec.binary,
                       models_source=source, login_command=spec.login,
                       install_command=kw.get("install", spec.install))


def server(ident, status, models=(), **kw):
    spec = next((s for s in lp.SERVERS if s[0] == ident), None)
    name, url, port, local, apps = spec[1:6] if spec else ("OpenAI-compatible API", kw["url"], 0, False, ("",))
    return lp.Provider(ident, name, list(models), ready=status == "ready", status=status, kind="server",
                       base_url=url, port=port, local=local, app_name=apps[0], models_source="server")


class Fakes:
    """Detection/check fakes the page calls from its worker threads."""

    def __init__(self, clis=(), servers=()):
        self.clis, self.servers = list(clis), list(servers)
        self.cli_calls, self.server_calls, self.checks = [], [], []
        self.check_result = 0.42

    def detect_clis(self, verified):
        self.cli_calls.append(set(verified))
        return [lp.Provider(**{**p.__dict__}) for p in self.clis]

    def detect_servers(self, **options):
        self.server_calls.append(options)
        return [lp.Provider(**{**p.__dict__}) for p in self.servers]

    def check(self, provider, model, timeout, cancel):
        self.checks.append((provider.id, model))
        if isinstance(self.check_result, Exception):
            raise self.check_result
        return self.check_result


@pytest.fixture
def make_page(qapp, isolated_home, monkeypatch):
    monkeypatch.setattr(lp, "_app_installed", lambda names, binary: True)
    pages = []

    def make(fakes, show=True, settings=None):
        page = ProofreadPage(settings or Settings(), detect_clis=fakes.detect_clis,
                             detect_servers=fakes.detect_servers, check_model=fakes.check)
        pages.append(page)
        page.resize(900, 1200)
        if show:
            page.show()
            assert wait_for(lambda: fakes.cli_calls and fakes.server_calls and page._cli_worker is None
                            and page._server_worker is None)
        return page
    yield make
    for page in pages:
        page.shutdown()
        page.close()
    set_language("en")


def everything():
    return Fakes(
        clis=[cli("codex", "ready", ["gpt-6.1-sol", "gpt-6-luna"]),
              cli("claude", "login", ["haiku", "sonnet"], source="curated"),
              cli("cursor", "not_installed", ["auto"], source="curated"),
              cli("gemini", "unverified", ["gemini-2.5-flash", "gemini-2.5-pro"], source="curated"),
              cli("grok", "not_installed", ["grok-4.6"], source="curated", install="")],
        servers=[server("ollama", "not_running"), server("lmstudio", "not_installed"),
                 server("cherry", "needs_key"),
                 server("custom", "no_models", url="http://127.0.0.1:8000/v1")])


def row_state(page, pid):
    row = page._rows[pid]
    return row.status.text(), row.button.text() if row.button.isVisibleTo(page) else ""


def test_status_maps_to_the_right_action(make_page):
    page = make_page(everything())
    assert row_state(page, "codex") == ("Ready", "")
    assert row_state(page, "claude") == ("Needs login", "How to log in")
    assert row_state(page, "cursor") == ("Not installed", "How to install")
    assert row_state(page, "gemini") == ("Not verified", "Verify")
    assert row_state(page, "grok") == ("Not installed", "")
    assert row_state(page, "ollama") == ("Not running", "Open Ollama")
    assert "ollama serve" in page._rows["ollama"].hint.text() and "11434" in page._rows["ollama"].hint.text()
    assert row_state(page, "lmstudio") == ("Not installed", "")
    assert "1234" in page._rows["lmstudio"].hint.text()
    assert row_state(page, "cherry") == ("Needs API key", "Enter key")
    assert row_state(page, "custom") == ("No models", "")
    assert [p.id for p in page.providers] == list(page._rows)
    assert page.settings.get("cleanup_provider") == "codex"  # first ready provider by default
    assert page.chosen_provider().id == "codex" and page._rows["codex"].dot.checked
    assert not page._rows["claude"].selectable and not page._rows["gemini"].selectable


def test_no_refresh_button(make_page):
    page = make_page(everything())
    labels = {b.text().casefold() for b in page.findChildren(QPushButton)}
    assert not labels & {"refresh", "刷新", "detect providers", "检测服务"}


def test_login_help_shows_copyable_command(make_page):
    page = make_page(everything())
    row = page._rows["claude"]
    assert not row.panel.isVisible()
    row.button.click()
    row = page._rows["claude"]
    assert row.panel.isVisible() and row.command.text() == "claude auth login"
    row.copy.click()
    assert QApplication.clipboard().text() == "claude auth login"
    assert row.copy.text() == "Copied"
    page._rows["cursor"].button.click()
    assert "cursor.com/install" in page._rows["cursor"].command.text()


def test_verify_updates_status_automatically(make_page):
    fakes = everything()
    page = make_page(fakes)
    page._rows["gemini"].button.click()
    assert page._rows["gemini"].status.text() == "Verifying…"
    assert wait_for(lambda: page._check_worker is None)
    assert fakes.checks == [("gemini", "gemini-2.5-flash")]
    assert row_state(page, "gemini") == ("Ready", "")
    assert page._rows["gemini"].selectable
    assert page.settings.get("cleanup_checks") == {"gemini": {"gemini-2.5-flash": 0.42}}
    page.detect_clis()
    assert wait_for(lambda: page._cli_worker is None)
    assert "gemini" in fakes.cli_calls[-1]  # remembered for later detections


def test_failed_verify_asks_to_log_in(make_page):
    fakes = everything()
    fakes.check_result = lp.ProviderError("CLI exited with status 1")
    page = make_page(fakes)
    page._rows["gemini"].button.click()
    assert wait_for(lambda: page._check_worker is None)
    row = page._rows["gemini"]
    assert row.status.text() == "Needs login" and row.button.text() == "Verify"
    assert row.panel.isVisible() and row.command.text() == "gemini"
    assert not page.settings.get("cleanup_checks")


def test_model_dropdown_comes_from_provider_listing(make_page):
    fakes = Fakes(clis=[cli("cursor", "ready", ["auto", "gemini-3.8-flash-low", "big-model"])])
    page = make_page(fakes)
    combo = page.model_combo
    assert not combo.isEditable()
    assert [combo.itemData(i) for i in range(combo.count() - 1)] == ["auto", "gemini-3.8-flash-low", "big-model"]
    assert combo.itemText(combo.count() - 1) == "Other…"
    assert combo.currentData() == "gemini-3.8-flash-low"  # fast, inexpensive default
    assert "Models listed by Cursor CLI" in page.model_status.text()
    combo.setCurrentIndex(combo.findData("big-model"))
    assert page.settings.get("cleanup_models") == {"cursor": "big-model"}
    assert page.chosen_model(page.chosen_provider()) == "big-model"
    assert not fakes.checks  # listed models need no test request


def test_server_models_fill_dropdown(make_page):
    page = make_page(Fakes(servers=[server("ollama", "ready", ["small:3b", "big:70b"])]))
    assert page.chosen_provider().id == "ollama"
    assert page.model_combo.currentData() == "small:3b"


def test_curated_model_is_validated(make_page):
    fakes = Fakes(clis=[cli("claude", "ready", ["haiku", "sonnet"], source="curated")])
    page = make_page(fakes)
    assert wait_for(lambda: fakes.checks and page._check_worker is None)
    assert fakes.checks == [("claude", "haiku")]
    assert page.model_status.text() == "haiku works · answered in 0.4 s"
    fakes.check_result = lp.ProviderError("bad model")
    page.model_combo.setCurrentIndex(page.model_combo.findData("sonnet"))
    assert wait_for(lambda: len(fakes.checks) == 2 and page._check_worker is None)
    assert page.model_status.text() == "sonnet didn't work. Choose another model."
    page.detect_clis()
    assert wait_for(lambda: page._cli_worker is None)
    assert len(fakes.checks) == 2  # a failed model is not retried on every detection


def test_other_model_must_pass_validation(make_page):
    fakes = Fakes(clis=[cli("codex", "ready", ["gpt-6.1-sol"])])
    page = make_page(fakes)
    page.model_combo.setCurrentIndex(page.model_combo.count() - 1)
    assert page._other_row.isVisible()
    fakes.check_result = lp.ProviderError("unknown model")
    page.other_edit.setText("typo-model")
    page.other_button.click()
    assert wait_for(lambda: page._check_worker is None)
    assert not page.settings.get("cleanup_extra_models")
    assert "typo-model didn't work" in page.model_status.text()
    fakes.check_result = 1.5
    page.other_edit.setText("gpt-6-astra")
    page.other_button.click()
    assert wait_for(lambda: page._check_worker is None)
    assert page.settings.get("cleanup_extra_models") == {"codex": ["gpt-6-astra"]}
    assert page.model_combo.currentData() == "gpt-6-astra" and not page._other_row.isVisible()
    assert page.chosen_model(page.chosen_provider()) == "gpt-6-astra"


def test_choose_provider_by_clicking_row(make_page):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    page = make_page(Fakes(clis=[cli("codex", "ready"), cli("claude", "ready", ["haiku"], source="cli"),
                                 cli("gemini", "login")]))
    changes = []
    page.selection_changed.connect(lambda: changes.append(1))
    QTest.mouseClick(page._rows["gemini"], Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
    assert page.settings.get("cleanup_provider") == "codex"  # not ready: not selectable
    QTest.mouseClick(page._rows["claude"], Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
    assert page.settings.get("cleanup_provider") == "claude" and changes
    assert page._rows["claude"].dot.checked and not page._rows["codex"].dot.checked
    assert page.model_combo.currentData() == "haiku"


def test_auto_redetect_while_visible(make_page, monkeypatch):
    monkeypatch.setattr(ProofreadPage, "CLI_INTERVAL_MS", 60)
    monkeypatch.setattr(ProofreadPage, "SERVER_INTERVAL_MS", 30)
    fakes = Fakes(servers=[server("lmstudio", "not_running")])
    page = make_page(fakes)
    assert page._cli_timer.isActive() and page._server_timer.isActive()
    assert page.summary.text().startswith("No provider is ready")
    fakes.servers = [server("lmstudio", "ready", ["qwen3-4b"])]
    assert wait_for(lambda: page._rows["lmstudio"].status.text() == "Ready")
    assert page.chosen_provider().id == "lmstudio" and not page.summary.isVisible()
    assert wait_for(lambda: len(fakes.cli_calls) >= 3)
    page.hide()
    assert not page._cli_timer.isActive() and not page._server_timer.isActive()
    calls = len(fakes.server_calls)
    wait_for(lambda: False, timeout=0.2)
    assert len(fakes.server_calls) <= calls + 1


def test_hidden_page_only_detects_on_request(make_page):
    fakes = Fakes(clis=[cli("codex", "ready")])
    page = make_page(fakes, show=False)
    assert not fakes.cli_calls and not page._cli_timer.isActive()
    page.refresh()
    assert wait_for(lambda: page.chosen_provider() is not None)


def test_custom_endpoint_models_are_fetched(make_page):
    fakes = Fakes()
    page = make_page(fakes)
    fakes.servers = [server("custom", "ready", ["llama-3.1-8b"], url="http://127.0.0.1:8000/v1")]
    page.url.setText("http://127.0.0.1:8000/v1")
    page.key.setText("synthetic-key")
    page.key.editingFinished.emit()
    assert wait_for(lambda: "custom" in page._rows and page._server_worker is None)
    assert fakes.server_calls[-1]["custom_base_url"] == "http://127.0.0.1:8000/v1"
    assert fakes.server_calls[-1]["api_key"] == "synthetic-key"
    assert page.settings.get("cleanup_base_url") == "http://127.0.0.1:8000/v1"
    assert page.model_combo.currentData() == "llama-3.1-8b"


def test_cherry_key_prompt(make_page, monkeypatch):
    fakes = everything()
    page = make_page(fakes)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (" cherry-key ", True))
    page._rows["cherry"].button.click()
    assert wait_for(lambda: fakes.server_calls[-1]["cherry_api_key"] == "cherry-key")


def test_open_app_button(make_page, monkeypatch):
    opened = []
    monkeypatch.setattr("thundertalk.ui.pages.proofread_page.subprocess.Popen", opened.append)
    page = make_page(everything())
    page._rows["ollama"].button.click()
    assert opened == [["open", "-a", "Ollama"]]


def test_toggle_and_chinese(make_page):
    page = make_page(everything())
    page.toggle._flip()
    assert page.settings.get("llm_rewrite_enabled") is True
    set_language("zh")
    assert page._header._title.text() == "AI 校对"
    assert row_state(page, "claude") == ("需要登录", "如何登录")
    assert row_state(page, "gemini") == ("未验证", "验证")
    assert "云端" in page.privacy.text()
    assert page.model_combo.itemText(page.model_combo.count() - 1) == "其他…"


def test_main_window_has_proofread_page(qapp, isolated_home, no_audio_hw, monkeypatch):
    from thundertalk.core.history import HistoryStore
    from thundertalk.core.state import AppState
    from thundertalk.ui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "_setup_macos_titlebar", lambda self: None)
    monkeypatch.setattr(ProofreadPage, "refresh", lambda self: None)
    settings = Settings()
    w = mw.MainWindow(settings, HistoryStore(), AppState(settings.hotkey))
    labels = [b._label for b in w._nav_buttons]
    assert labels.index("AI Proofread") == labels.index("Hotwords") + 1
    assert labels.index("Settings") == labels.index("AI Proofread") + 1
    assert not hasattr(w.settings_page, "cleanup_settings")
    w._nav_buttons[labels.index("AI Proofread")].click()
    assert w._stack.currentWidget() is w.proofread_page
    w.models_page.wait_background()
    w.studio_page.shutdown()
    w.proofread_page.shutdown()
    w.close()
