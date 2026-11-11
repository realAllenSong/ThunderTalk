from __future__ import annotations

import pytest

from thundertalk.mcp import server


def test_url_is_not_converted_to_a_filesystem_path(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_cli", lambda *args, **kw: calls.append(args) or {})
    url = "https://www.bilibili.com/list/watchlater?bvid=BV1X6JX6sEYJ"
    server.transcribe_file(url, "out.md")
    assert calls[0][1] == url


@pytest.mark.parametrize("function,kwargs,command", [
    ("transcribe_inputs", dict(inputs=["a.wav", "https://youtu.be/abc"], output_dir="out"), "transcribe"),
    ("meeting_notes", dict(input_path="a.txt", output_path="notes.md", provider="fake", model="small"), "notes"),
    ("subtitle_video", dict(video_path="v.mp4", output_path="s.mp4", subtitles="s.srt", soft=True), "burn"),
    ("translate_local", dict(output_path="t.txt", target="cmn", text="hello", source_language="eng"), "translate"),
    ("compose_music", dict(style="jazz", instrumental=True, length=10, planner=False, takes=2, seed=1), "compose"),
    ("manage_local_voice", dict(action="add", input_path="public.wav", name="Public", transcript="hello"), "voices"),
    ("transcript_history", dict(action="export", entry_id="abc", output_path="h.md"), "history"),
    ("manage_models", dict(action="download", model_id="acestep-turbo", planner=True), "models"),
    ("local_status", {}, "status"),
])
def test_new_mcp_tools_delegate_to_cli(monkeypatch, function, kwargs, command):
    calls = []
    monkeypatch.setattr(server, "_cli", lambda *args, **kw: calls.append(args) or {"ok": True})
    getattr(server, function)(**kwargs)
    assert len(calls) == 1 and calls[0][0] == command
    assert all(isinstance(arg, str) for arg in calls[0])


@pytest.mark.parametrize("summary", [
    {"type": "result", "ok": False, "code": "batch_failed", "items": [{"ok": True, "output": "a.md"}]},
    {"type": "result", "ok": False, "code": "music:failed", "takes": [{"output": "take.wav"}]},
])
def test_mcp_preserves_partial_success_summaries(monkeypatch, summary):
    import json
    from types import SimpleNamespace
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=json.dumps(summary), stderr=""))
    assert server._cli("transcribe", "a.wav") == summary


def test_results_are_compact_json_without_envelope(monkeypatch):
    import json
    monkeypatch.setattr(server, "_cli", lambda *a, **kw: {"type": "result", "ok": True, "output": "a.srt", "x": None})
    text = server.transcribe_file("a.wav", "a.srt")
    assert "\n" not in text and ": " not in text
    assert json.loads(text) == {"ok": True, "output": "a.srt"}


def test_voices_are_trimmed_and_filtered(monkeypatch):
    import json
    voices = [
        {"id": "kokoro:zf", "name": "Z", "language": "chinese", "gender": "f", "ready": True, "description": "long"},
        {"id": "kokoro:af", "name": "A", "language": "english", "gender": "f", "ready": True, "description": "long"},
    ]
    monkeypatch.setattr(server, "_cli", lambda *a, **kw: {"ok": True, "voices": voices, "my_voices": []})
    out = json.loads(server.list_local_voices(language="Chinese"))
    assert out["voices"] == [{"id": "kokoro:zf", "name": "Z", "language": "chinese", "gender": "f", "ready": True}]


def test_status_summary_drops_model_catalog(monkeypatch):
    import json
    status = {"type": "result", "ok": True, "version": "1", "asr": {"downloaded": ["a"], "auto": "a",
              "models": [{"id": "a", "downloaded": True}, {"id": "b", "downloaded": False}]},
              "tts": [{"engine": "kokoro", "name": "Kokoro", "ready": True, "voices": 3}],
              "apple_speech": {"available": True, "supported_locales": 63}}
    monkeypatch.setattr(server, "_cli", lambda *a, **kw: status)
    out = json.loads(server.local_status())
    assert out["asr"] == {"downloaded": ["a"], "auto": "a", "not_downloaded": ["b"]}
    assert out["tts"] == [{"engine": "kokoro", "ready": True, "voices": 3}]
    assert out["apple_speech"] == {"available": True}


def test_frozen_app_runs_itself(monkeypatch):
    import sys
    import subprocess
    from types import SimpleNamespace
    seen = []
    monkeypatch.delenv("THUNDERTALK_BIN", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.append(cmd) or SimpleNamespace(
        returncode=0, stdout='{"type":"result","ok":true}', stderr=""))
    server._cli("status")
    assert seen[0] == [sys.executable, "--cli", "status"]


def test_no_legacy_http_tools():
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert not names & {"transcribe_audio", "synthesize_speech", "get_asr_status", "get_tts_voices"}
    assert len(names) == 13
