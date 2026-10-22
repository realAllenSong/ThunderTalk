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
