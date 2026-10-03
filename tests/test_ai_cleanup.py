"""No network/model loads: provider contracts, proofreading policy and paste safety."""
from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError

import pytest

from thundertalk.core import ai_cleanup as ai
from thundertalk.core import llm_providers as lp
from thundertalk.core import text_output as output


@pytest.fixture(autouse=True)
def fresh_caches():
    lp._HELP_CACHE.clear()
    lp._MODEL_CACHE.clear()


@pytest.fixture
def offline(monkeypatch, isolated_home):
    monkeypatch.setattr(lp.shutil, "which", lambda name, path: None)
    monkeypatch.setattr(lp, "_http", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(lp, "_app_installed", lambda names, binary: False)


def by_id(providers):
    return {p.id: p for p in providers}


# ── detection ───────────────────────────────────────────────────────────

def test_offline_detection_lists_every_provider(offline):
    providers = lp.detect()
    assert [p.id for p in providers] == ["codex", "claude", "cursor", "gemini", "grok",
                                         "ollama", "lmstudio", "cherry"]
    assert all(p.status == "not_installed" and not p.is_ready() for p in providers)
    found = by_id(providers)
    assert found["codex"].install_command and found["claude"].login_command == "claude auth login"
    assert found["ollama"].port == 11434 and found["lmstudio"].app_name == "LM Studio"


def test_installed_app_without_server_is_not_running(offline, monkeypatch):
    monkeypatch.setattr(lp, "_app_installed", lambda names, binary: names == ("Ollama",))
    found = by_id(lp.detect_servers())
    assert found["ollama"].status == "not_running"
    assert found["lmstudio"].status == "not_installed"


def _fake_cli(monkeypatch, claude_logged_in=False, calls=None):
    monkeypatch.setattr(lp.shutil, "which", lambda name, path: "/bin/" + name)

    def run(args, **kwargs):
        assert Path(kwargs["cwd"]).is_dir()
        if calls is not None:
            calls.append(args)
        name = Path(args[0]).name
        if "--help" in args:
            return "--tools --strict-mcp-config --mode --sandbox --trust --deny --approval-mode", ""
        if args[-1] == "models":
            return "Available models\n\nauto - Auto\ngemini-3.8-flash-low - Flash\nbig-model - Big", ""
        if name == "claude":
            return json.dumps({"loggedIn": claude_logged_in}), ""
        return "Logged in using subscription", ""
    monkeypatch.setattr(lp, "_run", run)


def test_cli_status_and_models(offline, monkeypatch, isolated_home):
    _fake_cli(monkeypatch)
    cache = isolated_home / ".codex/models_cache.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"models": [
        {"slug": "gpt-6.1-sol", "visibility": "list", "supported_reasoning_levels": [{"effort": "low"}]},
        {"slug": "hidden", "visibility": "hide"},
        {"slug": "gpt-6-luna", "visibility": "list", "supported_reasoning_levels": [{"effort": "medium"}]},
    ]}))
    found = by_id(lp.detect_clis())
    codex, claude, cursor, gemini = found["codex"], found["claude"], found["cursor"], found["gemini"]
    assert codex.is_ready() and codex.models == ["gpt-6.1-sol", "gpt-6-luna"]
    assert codex.models_source == "cli" and codex.efforts["gpt-6.1-sol"] == ["low"]
    assert lp.preferred_model(codex) == "gpt-6-luna"
    assert claude.status == "login" and not claude.is_ready()
    assert cursor.models == ["auto", "gemini-3.8-flash-low", "big-model"]
    assert lp.preferred_model(cursor) == "gemini-3.8-flash-low"
    assert gemini.status == "login"  # no cached credentials


def test_codex_without_model_cache_uses_curated(offline, monkeypatch):
    _fake_cli(monkeypatch)
    codex = by_id(lp.detect_clis())["codex"]
    assert codex.models_source == "curated" and "gpt-6.1-sol" in codex.models


def test_gemini_credentials_unverified_until_verified(offline, monkeypatch, isolated_home):
    _fake_cli(monkeypatch)
    creds = isolated_home / ".gemini/oauth_creds.json"
    creds.parent.mkdir()
    creds.write_text('{"refresh_token": "synthetic"}')
    gemini = by_id(lp.detect_clis())["gemini"]
    assert gemini.status == "unverified" and gemini.usable() and not gemini.is_ready()
    assert lp.preferred_model(gemini) == "gemini-2.5-flash"
    assert by_id(lp.detect_clis(verified={"gemini"}))["gemini"].is_ready()


def test_help_and_model_listing_are_cached(offline, monkeypatch):
    calls = []
    _fake_cli(monkeypatch, claude_logged_in=True, calls=calls)
    lp.detect_clis()
    first = len(calls)
    lp.detect_clis()
    assert not [c for c in calls[first:] if "--help" in c or c[-1] == "models"
                and Path(c[0]).name == "cursor-agent"]


def test_timeout_detection_is_unavailable(offline, monkeypatch):
    monkeypatch.setattr(lp.shutil, "which", lambda name, path: name if name == "codex" else None)
    monkeypatch.setattr(lp, "_run", lambda *a, **k: (_ for _ in ()).throw(lp.ProviderError("timeout")))
    codex = by_id(lp.detect_clis())["codex"]
    assert codex.status == "unavailable" and not codex.is_ready()


def test_cursor_model_query_failure_keeps_curated(offline, monkeypatch):
    monkeypatch.setattr(lp.shutil, "which", lambda name, path: name if name == "cursor-agent" else None)

    def run(args, **kwargs):
        if args[-1] == "models":
            raise lp.ProviderError("timed out")
        if args[-1] == "--help":
            return "--mode --sandbox --trust", ""
        return "Logged in", ""
    monkeypatch.setattr(lp, "_run", run)
    cursor = by_id(lp.detect_clis())["cursor"]
    assert cursor.is_ready() and cursor.models == ["auto"] and cursor.models_source == "curated"


def test_server_detection_models_and_auth(offline, monkeypatch):
    requests = []

    def http(url, **kwargs):
        requests.append((url, kwargs))
        if "11434" in url:
            return {"models": [{"name": "big:70b", "size": 9}, {"name": "nomic-embed-text", "size": 1},
                               {"name": "small:3b", "size": 2}]}
        if "23333" in url:
            raise HTTPError(url, 401, "unauthorized", {}, io.BytesIO())
        return {"data": [{"id": "server-model"}]}
    monkeypatch.setattr(lp, "_http", http)
    found = by_id(lp.detect_servers(custom_base_url="https://example.invalid/v1/", api_key="test-key"))
    assert list(found) == ["ollama", "lmstudio", "cherry", "custom"]
    assert found["ollama"].models == ["small:3b", "big:70b"]  # smallest first, no embeddings
    assert lp.preferred_model(found["ollama"]) == "small:3b"
    assert found["lmstudio"].is_ready() and found["lmstudio"].local and not found["cherry"].local
    assert found["cherry"].status == "needs_key"
    assert requests[-1] == ("https://example.invalid/v1/models", {"timeout": 0.5, "api_key": "test-key"})
    assert found["custom"].is_ready() and found["custom"].models == ["server-model"]


def test_no_server_models(offline, monkeypatch):
    monkeypatch.setattr(lp, "_http", lambda *a, **k: {"models": [], "data": []})
    assert all(p.status == "no_models" and not p.is_ready() for p in lp.detect_servers())


# ── completion ──────────────────────────────────────────────────────────

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
                    help_text="--ephemeral --ignore-user-config --ignore-rules",
                    efforts={"m": ["low", "medium"]})
    assert p.complete("system", "text", "m", 7) == "Clean text"
    args, opts = calls[0]
    assert opts["timeout"] == 7
    if ident == "codex":
        assert args[-1] == "-" and args[args.index("-s") + 1] == "read-only"
        assert "--ignore-user-config" in args and 'model_reasoning_effort="low"' in args
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


def test_unverified_provider_may_run_a_check(monkeypatch):
    monkeypatch.setattr(lp, "_run", lambda args, **k: ("OK", ""))
    p = lp.Provider("gemini", "Gemini", ["gemini-2.5-flash"], status="unverified", executable="/x")
    assert not p.is_ready() and lp.check_model(p, "gemini-2.5-flash", 5) >= 0
    p.status = "login"
    with pytest.raises(lp.ProviderError):
        lp.check_model(p, "gemini-2.5-flash", 5)


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
    monkeypatch.setattr(lp, "_http", lambda *a, **k: {"choices": [{"message": {"content": ""}}]})
    with pytest.raises(lp.ProviderError, match="empty"):
        p.complete("s", "u", "m", 1)


def test_cli_disappearing_after_detection_raises_provider_error(monkeypatch):
    monkeypatch.setattr(lp, "_run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("gone")))
    provider = lp.Provider("cursor", "Fake", ["m"], ready=True, executable="fake")
    with pytest.raises(lp.ProviderError, match="CLI request failed"):
        provider.complete("s", "u", "m", 1)


# ── proofreading policy ─────────────────────────────────────────────────

class Recorder:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system, user, model, timeout, cancel=None):
        self.calls.append((system, user, model, timeout, cancel))
        return self.reply(user) if callable(self.reply) else self.reply


def test_prompt_is_proofreading_only():
    for rule in ("Minimal edits", "Never translate", "Never reformat", "Never answer",
                 "return the transcript unchanged", "reference", "hotwords"):
        assert rule in ai.SYSTEM
    for removed in ("filler", "paragraph", "list"):
        assert f"Remove {removed}" not in ai.SYSTEM


def test_request_carries_reference_and_hotwords():
    p = Recorder("GPT的Astra、Luna、whatever、Terra")
    result = ai.cleanup(p, "GPT的阿修罗、露娜、whatever、terra", "m", timeout=4,
                        reference_text="GPT的Astra、Luna、whatever、Terra", hotwords=["GRPO", " ", 3])
    assert result == "GPT的Astra、Luna、whatever、Terra"
    system, user, model, timeout, _ = p.calls[0]
    assert system == ai.SYSTEM and (model, timeout) == ("m", 4)
    assert json.loads(user) == {"transcript": "GPT的阿修罗、露娜、whatever、terra",
                                "reference": "GPT的Astra、Luna、whatever、Terra", "hotwords": ["GRPO"]}


def test_reference_omitted_when_empty_or_identical():
    assert json.loads(ai.build_request("same", "same")) == {"transcript": "same"}
    assert json.loads(ai.build_request("same", None, [])) == {"transcript": "same"}


def test_positional_signature_is_backward_compatible():
    p = Recorder("clean text")
    event = threading.Event()
    assert ai.cleanup(p, "clean txt", "fake", "light", 4, event) == "clean text"
    assert p.calls[0][3:] == (4, event)
    assert ai.proofread is ai.cleanup


def test_empty_text_is_not_sent():
    p = Recorder("x")
    assert ai.cleanup(p, "  ", "m") == "  " and not p.calls


@pytest.mark.parametrize("original,fixed", [
    ("GPT的阿修罗、露娜、whatever、terra", "GPT的Astra、Luna、whatever、Terra"),
    ("我们用 lama index 加上 rag 做检索", "我们用 LlamaIndex 加上 RAG 做检索"),
    ("the model uses group relative policy optimisation, also called g r p o",
     "the model uses group relative policy optimisation, also called GRPO"),
    ("这个 promp 要改一下", "这个 prompt 要改一下"),
    ("明天下午三点开会。", "明天下午三点开会。"),
    ("我们用拉玛做检索", "我们用 Llama 做检索"),
])
def test_term_fixes_are_accepted(original, fixed):
    assert ai.acceptable(original, fixed)
    assert ai.cleanup(Recorder(fixed), original, "m") == fixed


@pytest.mark.parametrize("original,result", [
    ("send the draft tomorrow but do not publish it", "明天发送草稿，但不要发布。"),
    ("这个 API 的 latency 有点高 we should reduce overhead", "这个接口的延迟有点高，我们应该降低开销。"),
    ("我们明天下午三点开会讨论新的方案不要改时间", "We will meet at 3 pm tomorrow to discuss the plan."),
    ("first buy milk second send the invoice", "1. Buy milk\n2. Send the invoice"),
    ("what is two plus two", "Two plus two is four. Let me know if you need anything else at all!"),
    ("这个 promp 要改一下", ""),
])
def test_translation_answers_and_reformatting_are_rejected(original, result):
    assert not ai.acceptable(original, result)
    assert ai.cleanup(Recorder(result or " "), original, "m") == original


@pytest.mark.parametrize("reply", ["```\nfixed GRPO\n```", '"fixed GRPO"', '{"transcript": "fixed GRPO"}'])
def test_wrappers_are_removed(reply):
    assert ai.cleanup(Recorder(reply), "fixed g r p o", "m") == "fixed GRPO"


def test_removed_features_are_gone():
    for name in ("parse_command", "is_edit_instruction", "edit_selection", "style_for_app", "STYLES"):
        assert not hasattr(ai, name)
    for name in ("read_selection", "selection_ticket", "_has_selection"):
        assert not hasattr(output, name)


# ── settings migration ──────────────────────────────────────────────────

def test_old_cleanup_keys_migrate_harmlessly(isolated_home):
    from thundertalk.core.settings import Settings
    path = isolated_home / ".thundertalk/settings.json"
    path.write_text(json.dumps({
        "llm_rewrite_enabled": True, "cleanup_provider": "codex",
        "cleanup_models": {"codex": "gpt-6.1-sol"}, "cleanup_app_overrides": {"Mail": "polished"},
        "cleanup_model_overrides": {"codex": ["x"]}, "voice_commands_enabled": True}))
    settings = Settings()
    assert settings.get("llm_rewrite_enabled") is True
    assert settings.get("cleanup_models") == {"codex": "gpt-6.1-sol"}
    assert settings.get("voice_commands_enabled") is None
    settings.set("hotwords", ["GRPO"])
    stored = json.loads(path.read_text())
    assert not {"cleanup_app_overrides", "cleanup_model_overrides", "voice_commands_enabled"} & set(stored)
    assert stored["cleanup_checks"] == {} and stored["cleanup_extra_models"] == {}


@pytest.mark.parametrize("has_provider,enabled", [(False, False), (True, True)])
def test_legacy_local_enable_does_not_opt_in_to_cloud(isolated_home, has_provider, enabled):
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


# ── guarded replacement ─────────────────────────────────────────────────

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


def test_replace_exactly_once(guarded):
    ticket, calls = guarded
    assert output.apply_if_unchanged(ticket, "clean")
    assert calls == [6, "paste"]
    assert not output.apply_if_unchanged(ticket, "")
    assert output.apply_if_unchanged(ticket, "again")  # successor of the cleaned paste
    ticket.invalidate()
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


def test_final_guard_after_clipboard(guarded, monkeypatch):
    ticket, calls = guarded

    def write(text):
        output.activity.generation += 1
        return True
    monkeypatch.setattr(output, "_clipboard_write_verified", write)
    assert not output.apply_if_unchanged(ticket, "clean")
    assert not calls


def test_cancel_before_paste(guarded):
    ticket, calls = guarded
    event = threading.Event()
    event.set()
    assert not output.apply_if_unchanged(ticket, "clean", cancel=event)
    assert not calls


def test_activity_hotkey_does_not_hide_normal_typing():
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


# ── dictation worker ────────────────────────────────────────────────────

def test_worker_proofreads_with_context_and_guards_result(qapp, guarded):
    from thundertalk.app import LlmRewriteWorker
    ticket, calls = guarded
    p = Recorder("the method is called GRPO")
    worker = LlmRewriteWorker(p, "the method is called g r p o", "fake", ticket, 1, False,
                              hotwords=["GRPO"], reference_text="the method is called GRPO")
    worker.run()
    assert calls == [6, "paste"]
    request = json.loads(p.calls[0][1])
    assert request["hotwords"] == ["GRPO"] and request["reference"] == "the method is called GRPO"


def test_worker_unchanged_result_does_not_touch_paste(qapp, guarded):
    from thundertalk.app import LlmRewriteWorker
    ticket, calls = guarded
    LlmRewriteWorker(Recorder("明天开会。"), "明天开会。", "fake", ticket, 1, False).run()
    assert not calls and ticket.valid


def test_worker_failure_preserves_raw(qapp, guarded):
    from thundertalk.app import LlmRewriteWorker
    ticket, calls = guarded

    class Fake:
        def complete(self, *args, **kwargs):
            raise lp.ProviderError("timeout")
    LlmRewriteWorker(Fake(), "raw", "fake", ticket, 1, False).run()
    assert not calls and ticket.valid
