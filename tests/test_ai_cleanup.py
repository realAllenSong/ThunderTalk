"""No network/model loads: provider contracts, policy and paste safety."""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from thundertalk.core import ai_cleanup as ai
from thundertalk.core import llm_providers as lp
from thundertalk.core import text_output as output


@pytest.fixture
def offline(monkeypatch, isolated_home):
    monkeypatch.setattr(lp.shutil, "which", lambda name, path: None)
    monkeypatch.setattr(lp, "_http", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))


def test_empty_detection(offline):
    assert lp.detect() == []


def test_cli_detection_priority_models_login(offline, monkeypatch):
    monkeypatch.setattr(lp.shutil, "which", lambda name, path: "/bin/" + name)
    def run(args, **kwargs):
        assert Path(kwargs["cwd"]).is_dir()
        assert kwargs["timeout"] == 4
        name = Path(args[0]).name
        if "--help" in args:
            return "--tools --strict-mcp-config --mode --sandbox --trust --deny --approval-mode", ""
        if args[-1] == "models":
            return "Available models\ncheap-model - Cheap\nother-model - Other", ""
        if name == "claude":
            return '{"loggedIn":false}', ""
        return "Logged in using subscription", ""
    monkeypatch.setattr(lp, "_run", run)
    providers = lp.detect(model_overrides={"codex": ["my-model"]})
    assert [p.id for p in providers] == ["codex", "claude", "gemini", "grok", "cursor"]
    assert providers[0].models == ["my-model"]
    assert not providers[1].is_ready()
    assert providers[-1].models == ["cheap-model", "other-model"]
    assert providers[-1].models_source == "cli"
    assert not providers[2].is_ready()


def test_gui_path(offline, monkeypatch, isolated_home):
    folder = isolated_home / ".nvm/versions/node/v22/bin"
    folder.mkdir(parents=True)
    monkeypatch.setenv("PATH", "/usr/bin")
    path = lp.search_path()
    assert str(folder) in path and "/opt/homebrew/bin" in path
    assert str(isolated_home / ".local/bin") in path and "/usr/local/bin" in path


def test_timeout_detection_is_not_ready(offline, monkeypatch):
    monkeypatch.setattr(lp.shutil, "which", lambda name, path: name if name == "codex" else None)
    monkeypatch.setattr(lp, "_run", lambda *a, **k: (_ for _ in ()).throw(lp.ProviderError("timeout")))
    p, = lp.detect()
    assert p.status == "unavailable" and not p.is_ready()


def test_server_detection_and_auth(offline, monkeypatch):
    requests = []
    def http(url, **kwargs):
        requests.append((url, kwargs))
        if "11434" in url:
            return {"models": [{"name": "already-installed"}]}
        return {"data": [{"id": "server-model"}]}
    monkeypatch.setattr(lp, "_http", http)
    providers = lp.detect(custom_base_url="https://example.invalid/v1/", api_key="test-key",
                          cherry_api_key="cherry-key")
    assert [p.id for p in providers] == ["ollama", "lmstudio", "cherry", "custom"]
    assert providers[0].local and providers[1].local and not providers[2].local
    assert requests[-1][1]["api_key"] == "test-key"
    assert requests[-1][0] == "https://example.invalid/v1/models"
    assert all(p.is_ready() for p in providers)


def test_no_server_models(offline, monkeypatch):
    monkeypatch.setattr(lp, "_http", lambda *a, **k: {"models": [], "data": []})
    assert all(not p.is_ready() and p.status == "no_models" for p in lp.detect())


@pytest.mark.parametrize("ident", ["ollama", "lmstudio", "custom"])
def test_http_completion_payload(monkeypatch, ident):
    calls = []
    def http(url, **kwargs):
        calls.append((url, kwargs))
        return {"message": {"content": " clean "}, "choices": [{"message": {"content": " clean "}}]}
    monkeypatch.setattr(lp, "_http", http)
    p = lp.Provider(ident, "Fake", ["m"], ready=True, base_url="http://localhost", api_key="key")
    assert p.complete("system", "text", "m", 3) == "clean"
    body = calls[0][1]["data"]
    assert body["messages"] == [{"role": "system", "content": "system"}, {"role": "user", "content": "text"}]
    assert not body["stream"]
    if ident == "ollama":
        assert body["keep_alive"] == 0


@pytest.mark.parametrize("ident", ["codex", "claude", "cursor", "gemini", "grok"])
def test_cli_arguments_and_empty_workdir(monkeypatch, ident):
    calls = []
    def run(args, **kwargs):
        work = Path(kwargs["cwd"])
        assert not (work / "AGENTS.md").exists()
        assert not (work / ".git").exists()
        calls.append((args, kwargs))
        if ident == "codex":
            Path(args[args.index("-o") + 1]).write_text("Clean text")
        return "Clean text", ""
    monkeypatch.setattr(lp, "_run", run)
    p = lp.Provider(ident, "Fake", ["m"], ready=True, executable="/fake/cli",
                    help_text="--ephemeral --ignore-user-config --ignore-rules")
    assert p.complete("system", "text", "m", 7) == "Clean text"
    args, opts = calls[0]
    assert opts["timeout"] == 7
    if ident == "codex":
        assert args[-1] == "-" and args[args.index("-s") + 1] == "read-only"
        assert "--ignore-user-config" in args
    elif ident == "claude":
        assert args[args.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in args
    elif ident == "cursor":
        assert args[args.index("--mode") + 1] == "ask"
        assert args[args.index("--sandbox") + 1] == "enabled"
        assert "--trust" in args
    elif ident == "gemini":
        assert "plan" in args and "none" in args
    else:
        assert "MCPTool" in args


def test_process_timeout_kills_group(monkeypatch, tmp_path):
    class Proc:
        pid = 5678
        returncode = None
        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("fake", timeout)
            self.returncode = -9
            return "", ""
        def poll(self):
            return self.returncode
    monkeypatch.setattr(lp.subprocess, "Popen", lambda *a, **k: Proc())
    times = iter([0, 0, 2])
    monkeypatch.setattr(lp.time, "monotonic", lambda: next(times))
    kills = []
    monkeypatch.setattr(lp.os, "killpg", lambda *args: kills.append(args))
    with pytest.raises(lp.ProviderError, match="timed out"):
        lp._run(["fake"], cwd=tmp_path, timeout=1, input_text="text")
    assert kills[0][0] == 5678


def test_cancellation_and_bad_result(monkeypatch):
    event = threading.Event()
    p = lp.Provider("custom", "Fake", ["m"], ready=True)
    event.set()
    with pytest.raises(lp.CompletionCancelled):
        p.complete("s", "u", "m", 1, event)
    event.clear()
    monkeypatch.setattr(lp, "_http", lambda *a, **k: {"choices": [{"message": {"content": ""}}]})
    with pytest.raises(lp.ProviderError, match="empty"):
        p.complete("s", "u", "m", 1)


@pytest.mark.parametrize("app,style", [("ChatGPT", "prompt"), ("Terminal", "prompt"),
    ("Mail", "polished"), ("Cursor", "punctuation"), ("Visual Studio Code", "punctuation"),
    ("微信", "casual"), ("Slack", "casual"), ("Pages", "light")])
def test_style_routing(app, style):
    assert ai.style_for_app(app) == style
    assert ai.style_for_app(app, {app.upper(): "off"}) == "off"
    assert ai.style_for_app(app, {app: "auto"}) == style


@pytest.mark.parametrize("text,command", [("换行。", "newline"), ("New line!", "newline"),
    ("新段落", "paragraph"), ("new paragraph", "paragraph"), ("删掉上一句", "undo"),
    ("delete that.", "undo"), ("tab key", "tab")])
def test_commands(text, command):
    assert ai.parse_command(text) == command


@pytest.mark.parametrize("text", ["please add a new line here", "不要删掉上一句", "换行以后继续",
    "we should delete that file", "I said new paragraph yesterday", "换行，新段落", "new line\nnew paragraph"])
def test_command_false_positives(text):
    assert ai.parse_command(text) is None
    assert not ai.is_edit_instruction(text)


def test_cleanup_and_edit_contract():
    class Fake:
        def complete(self, system, user, model, timeout, cancel=None):
            assert "ONLY" in system and model == "fake" and timeout == 4
            self.prompt = system
            self.user = user
            return "clean"
    p = Fake()
    assert ai.cleanup(p, "what is two plus two", "fake", "prompt", 4) == "clean"
    assert "never answer" in p.prompt and p.user == "what is two plus two"
    assert ai.cleanup(p, "raw", "fake", "off", 4) == "raw"
    assert ai.edit_selection(p, "selection", "make this shorter", "fake", 4) == "clean"
    assert '"selection": "selection"' in p.user
    assert ai.is_edit_instruction("改得正式一点。")
    assert not ai.is_edit_instruction("他说改得正式一点就好了")


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setattr(output, "_SYSTEM", "Darwin")
    monkeypatch.setattr(output.activity, "available", True)
    monkeypatch.setattr(output.activity, "generation", 1)
    monkeypatch.setattr(output, "_frontmost_pid", lambda: 42)
    monkeypatch.setattr(output, "_get_frontmost_app", lambda: "Mail")
    monkeypatch.setattr(output, "_clipboard_write_verified", lambda text: True)
    monkeypatch.setattr(output.time, "sleep", lambda n: None)
    calls = []
    monkeypatch.setattr(output, "_send_cmd_key", lambda key: calls.append(key))
    monkeypatch.setattr(output, "_send_cmd_v_darwin", lambda: calls.append("paste"))
    ticket = output.PasteTicket("Mail", 42, 1)
    ticket.ready.set()
    return ticket, calls


def test_replace_and_undo_cleaned_dictation(guarded):
    ticket, calls = guarded
    assert output.apply_if_unchanged(ticket, "clean")
    assert calls == [6, "paste"]
    assert output.apply_if_unchanged(ticket, None)
    assert calls == [6, "paste", 6]
    assert not output.apply_if_unchanged(ticket, "late")


@pytest.mark.parametrize("change", ["typing", "focus", "pid", "unavailable", "cancel", "not_ready"])
def test_replace_only_if_unchanged(guarded, monkeypatch, change):
    ticket, calls = guarded
    if change == "typing":
        output.activity.generation += 1
    elif change == "focus":
        monkeypatch.setattr(output, "_get_frontmost_app", lambda: "Slack")
    elif change == "pid":
        monkeypatch.setattr(output, "_frontmost_pid", lambda: 43)
    elif change == "unavailable":
        output.activity.available = False
    elif change == "cancel":
        ticket.valid = False
    else:
        ticket.ready.clear()
        monkeypatch.setattr(ticket.ready, "wait", lambda timeout: False)
    assert not output.apply_if_unchanged(ticket, "clean")
    assert not calls


def test_selection_replacement_no_undo(guarded):
    ticket, calls = guarded
    ticket.selection = "original selection"
    assert output.apply_if_unchanged(ticket, "shorter")
    assert calls == ["paste"]


def test_final_guard_after_clipboard(guarded, monkeypatch):
    ticket, calls = guarded
    def write(text):
        output.activity.generation += 1
        return True
    monkeypatch.setattr(output, "_clipboard_write_verified", write)
    assert not output.apply_if_unchanged(ticket, "clean")
    assert not calls


@pytest.mark.parametrize("raises", [False, True])
def test_selection_clipboard_restore(monkeypatch, raises):
    monkeypatch.setattr(output, "_SYSTEM", "Darwin")
    monkeypatch.setattr(output, "_has_selection", lambda: True)
    monkeypatch.setattr(output, "_save_clipboard", lambda: [[("rich/text", b"old")]])
    restored = []
    monkeypatch.setattr(output, "_restore_clipboard", lambda saved: restored.append(saved))
    monkeypatch.setattr(output.pyperclip, "copy", lambda text: None)
    monkeypatch.setattr(output.time, "sleep", lambda n: None)
    monkeypatch.setattr(output, "_send_cmd_key", lambda key: None)
    def paste():
        if raises:
            raise RuntimeError("clipboard failure")
        return "selected"
    monkeypatch.setattr(output.pyperclip, "paste", paste)
    if raises:
        with pytest.raises(RuntimeError):
            output.read_selection()
    else:
        assert output.read_selection() == "selected"
    assert restored == [[[('rich/text', b'old')]]]


def test_copy_without_selection_never_runs(monkeypatch):
    monkeypatch.setattr(output, "_has_selection", lambda: False)
    monkeypatch.setattr(output, "_save_clipboard", lambda: pytest.fail("clipboard touched"))
    assert output.read_selection() == ""


def test_cleanup_settings_fake_providers(qapp, isolated_home):
    from thundertalk.core.i18n import set_language
    from thundertalk.core.settings import Settings
    from thundertalk.ui.cleanup_settings import CleanupSettings
    widget = CleanupSettings(Settings())
    assert not widget.chosen_provider()
    p = lp.Provider("fake", "Fake", ["cheap", "large"], ready=True, status="ready")
    widget._detected([p])
    assert widget.chosen_provider() is p and widget.toggle.isEnabled()
    widget.model_combo.setCurrentText("my-model")
    assert widget.chosen_model(p) == "my-model"
    widget.app_combo.setEditText("Mail")
    widget.style_combo.setCurrentIndex(widget.style_combo.findData("off"))
    widget._save_override()
    assert widget.settings.get("cleanup_app_overrides") == {"Mail": "off"}
    set_language("zh")
    assert widget.refresh_button.text() == "刷新"
    assert widget.chosen_model(p) == "my-model"
    widget._detected([])
    assert not widget.toggle.isEnabled()
    set_language("en")
    widget.close()


def test_activity_hotkey_does_not_hide_normal_typing(monkeypatch):
    tracker = output.InputActivity()
    tracker.set_hotkey("cmd_l+space")
    class Event:
        flags = 0
        def CGEvent(self):
            return None
        def type(self):
            return 10
        def keyCode(self):
            return 49
        def modifierFlags(self):
            return self.flags
    event = Event()
    tracker._event(event)
    assert tracker.generation == 1  # ordinary space invalidates
    event.flags = 1 << 20
    tracker._event(event)
    assert tracker.generation == 1  # Cmd+Space hotkey is ignored
    tracker._focus_changed(None)
    assert tracker.generation == 2


def test_ticket_invalidation_includes_cleaned_successor(guarded):
    ticket, _ = guarded
    assert output.apply_if_unchanged(ticket, "clean")
    ticket.invalidate()
    assert not output.apply_if_unchanged(ticket, None)


def test_cancel_before_paste(guarded):
    ticket, calls = guarded
    event = threading.Event()
    event.set()
    assert not output.apply_if_unchanged(ticket, "clean", cancel=event)
    assert not calls


def test_paste_failure_restores_clipboard(monkeypatch):
    monkeypatch.setattr(output, "_SYSTEM", "Darwin")
    monkeypatch.setattr(output, "_previous_app", "")
    monkeypatch.setattr(output, "_save_clipboard", lambda: "old clipboard")
    restored = []
    monkeypatch.setattr(output, "_restore_clipboard", restored.append)
    monkeypatch.setattr(output, "_clipboard_write_verified", lambda text: True)
    monkeypatch.setattr(output.time, "sleep", lambda n: None)
    monkeypatch.setattr(output, "_send_cmd_v_darwin", lambda: (_ for _ in ()).throw(RuntimeError("paste error")))
    with pytest.raises(RuntimeError):
        output._do_paste("raw", keep_clipboard=True)
    assert restored == ["old clipboard"]


def test_model_query_failure_keeps_curated_fallback(offline, monkeypatch):
    monkeypatch.setattr(lp.shutil, "which", lambda name, path: name if name == "cursor-agent" else None)
    def run(args, **kwargs):
        if args[-1] == "models":
            raise lp.ProviderError("timed out")
        if args[-1] == "--help":
            return "--mode --sandbox --trust", ""
        return "Logged in", ""
    monkeypatch.setattr(lp, "_run", run)
    p, = lp.detect()
    assert p.is_ready() and p.models == ["auto"] and p.models_source == "curated"


@pytest.mark.parametrize("selection", ["", "long selected text"])
def test_worker_uses_provider_and_guards_result(qapp, monkeypatch, guarded, selection):
    from thundertalk.app import LlmRewriteWorker
    ticket, calls = guarded
    ticket.selection = selection
    class Fake:
        def complete(self, system, user, model, timeout, cancel=None):
            assert model == "fake" and timeout == 1
            return "clean"
    worker = LlmRewriteWorker(Fake(), "make this shorter" if selection else "raw", "fake",
                              ticket, "light", 1, False, selection)
    worker.run()
    assert calls == (["paste"] if selection else [6, "paste"])


def test_worker_failure_preserves_raw(qapp, guarded):
    from thundertalk.app import LlmRewriteWorker
    ticket, calls = guarded
    class Fake:
        def complete(self, *args, **kwargs):
            raise lp.ProviderError("timeout")
    worker = LlmRewriteWorker(Fake(), "raw", "fake", ticket, "light", 1, False)
    worker.run()
    assert not calls and ticket.valid


@pytest.mark.parametrize("original,result", [
    ("um send the draft tomorrow", "明天发送草稿。"),
    ("这个 API 有点慢 we should wait", "这个接口有点慢，我们应该等待。"),
    ("明天开会", "Meet tomorrow."),
])
def test_cleanup_rejects_unrequested_translation(original, result):
    class Fake:
        def complete(self, *args, **kwargs):
            return result
    assert ai.cleanup(Fake(), original, "fake") == original


def test_selection_translation_is_allowed():
    class Fake:
        def complete(self, *args, **kwargs):
            return "Meet tomorrow."
    assert ai.edit_selection(Fake(), "明天开会", "翻译成英文", "fake") == "Meet tomorrow."


def test_mixed_input_with_surviving_acronym_is_not_translation():
    assert not ai.preserves_languages(
        "这个 API 的 latency 有点高 we need to reduce overhead 不要改 public interface",
        "这个 API 的延迟有点高，我们需要降低开销，不要改 public interface。")
    assert ai.preserves_languages(
        "呃这个 API 的 latency 有点高 we need to reduce overhead",
        "这个 API 的 latency 有点高，we need to reduce overhead。")


@pytest.mark.parametrize("original,result", [("嗯 send the mail", "Send the mail."), ("um 明天开会", "明天开会。")])
def test_language_guard_allows_bilingual_fillers(original, result):
    assert ai.preserves_languages(original, result)


def test_unavailable_selected_provider_stays_explicit(qapp, isolated_home):
    from thundertalk.core.settings import Settings
    from thundertalk.ui.cleanup_settings import CleanupSettings
    settings = Settings()
    settings.set("cleanup_provider", "missing-cli")
    settings.set("cleanup_app_overrides", {"Mail": "off"})
    widget = CleanupSettings(settings)
    widget._detected([lp.Provider("fake", "Fake", ["m"], ready=True)])
    assert widget.chosen_provider() is None
    assert widget.provider_combo.currentData() == "missing-cli"
    widget.app_combo.setEditText("mail")
    widget.style_combo.setCurrentIndex(widget.style_combo.findData("polished"))
    widget._save_override()
    assert settings.get("cleanup_app_overrides") == {"mail": "polished"}
    widget.close()


def test_cli_disappearing_after_detection_raises_provider_error(monkeypatch):
    monkeypatch.setattr(lp, "_run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("gone")))
    provider = lp.Provider("cursor", "Fake", ["m"], ready=True, executable="fake")
    with pytest.raises(lp.ProviderError, match="CLI request failed"):
        provider.complete("s", "u", "m", 1)


@pytest.mark.parametrize("has_provider,enabled", [(False, False), (True, True)])
def test_legacy_local_enable_does_not_opt_in_to_cloud(isolated_home, has_provider, enabled):
    import json
    from thundertalk.core.settings import Settings
    stored = {"llm_rewrite_enabled": True, "llm_rewrite_model": "old-local-model"}
    if has_provider:
        stored["cleanup_provider"] = ""
    (isolated_home / ".thundertalk/settings.json").write_text(json.dumps(stored))
    assert Settings().get("llm_rewrite_enabled") is enabled


def test_api_settings_file_is_private(isolated_home):
    import stat
    from thundertalk.core.settings import Settings
    Settings().set("cleanup_api_key", "synthetic-key")
    assert stat.S_IMODE((isolated_home / ".thundertalk/settings.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("supports_reference", [True, False])
def test_worker_cleanup_reference_api_compatibility(qapp, monkeypatch, supports_reference):
    from thundertalk.app import LlmRewriteWorker
    seen = []

    def modern(provider, text, model, style, timeout, cancel, *, reference_text=None):
        seen.append(reference_text)
        return text

    def legacy(provider, text, model, style, timeout, cancel):
        seen.append("legacy")
        return text

    monkeypatch.setattr(ai, "cleanup", modern if supports_reference else legacy)
    worker = LlmRewriteWorker(object(), "raw", "fake", object(), "light", 1, False,
                              reference_text="GPT 的 Astra、Luna")
    worker.run()
    assert seen == (["GPT 的 Astra、Luna"] if supports_reference else ["legacy"])


def test_worker_does_not_retry_internal_typeerror(qapp, monkeypatch):
    from thundertalk.app import LlmRewriteWorker
    seen = []

    def broken(provider, text, model, style, timeout, cancel, *, reference_text=None):
        seen.append(reference_text)
        raise TypeError("provider bug")

    monkeypatch.setattr(ai, "cleanup", broken)
    worker = LlmRewriteWorker(object(), "raw", "fake", object(), "light", 1, False,
                              reference_text="clean preview")
    worker.run()
    assert seen == ["clean preview"]
