"""`thundertalk batch`: many jobs from one file; skip what is done, go on past failures, load models once."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from thundertalk import cli, cli_batch


def test_jobs_can_be_json_arrays_objects_or_command_lines(tmp_path):
    f = tmp_path / "jobs.txt"
    f.write_text('\n'.join([
        '# a comment', '',
        json.dumps(["transcribe", "a.m4a", "--out", "a.md"]),
        json.dumps({"argv": ["speak", "--text", "hi", "--out", "hi.wav"]}),
        "speak --text 'two words' --out b.wav",
    ]), encoding="utf-8")
    assert cli_batch.read_jobs(f) == [["transcribe", "a.m4a", "--out", "a.md"], ["speak", "--text", "hi", "--out", "hi.wav"],
                                      ["speak", "--text", "two words", "--out", "b.wav"]]


@pytest.mark.parametrize("line", ['["batch", "x"]', '{"argv": 5}', "[1, 2]"])
def test_bad_lines_are_refused_before_anything_runs(tmp_path, line):
    f = tmp_path / "jobs.jsonl"
    f.write_text(line, encoding="utf-8")
    with pytest.raises(cli.CliError):
        cli_batch.read_jobs(f)


@pytest.fixture
def run(monkeypatch, tmp_path):
    events = []
    def fake_out():
        o = SimpleNamespace(emit=lambda **e: events.append(e))
        o.stage = lambda label: o.emit(type="stage", label=label)
        o.progress = lambda value: o.emit(type="progress", value=value)
        return o
    monkeypatch.setattr(cli, "_Out", fake_out)
    def invoke(*args):
        events.clear()
        rc = cli.main(list(args))
        return rc, list(events)
    return invoke


def test_a_batch_skips_finished_jobs_goes_on_past_failures_and_reports_each(run, monkeypatch, tmp_path):
    done = tmp_path / "done.wav"; done.write_bytes(b"x")
    ran = []

    def speak(a, out):
        ran.append(a.text)
        if a.text == "bad":
            raise cli.CliError("tts_missing", "no engine")
        out.stage("speak")
        return {"output": a.out}

    monkeypatch.setitem(cli.COMMANDS, "speak", speak)
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text("\n".join(json.dumps(j) for j in [
        ["speak", "--text", "one", "--out", str(tmp_path / "1.wav")],
        ["speak", "--text", "bad", "--out", str(tmp_path / "2.wav")],
        ["speak", "--text", "skipped", "--out", str(done)],
        ["speak", "--text", "three", "--out", str(tmp_path / "3.wav")],
    ]), encoding="utf-8")
    rc, events = run("batch", str(jobs))
    result = events[-1]
    assert rc == 1 and ran == ["one", "bad", "three"]
    assert (result["succeeded"], result["failed"], result["skipped"]) == (2, 1, 1)
    assert [i.get("code") for i in result["items"]] == [None, "tts_missing", None, None]
    assert any(e.get("item") == 3 and e.get("label") == "speak" for e in events)      # events say which job
    rc, events = run("batch", str(jobs), "--redo", "--stop-on-error")
    assert events[-1]["not_run"] == 2 and ran[-2:] == ["one", "bad"]


def test_a_batch_loads_each_recognizer_once_for_all_its_files(run, monkeypatch, tmp_path):
    from thundertalk.core import asr, models, transcribe
    loads = []

    class Fake:
        is_loaded = True
        def set_language(self, l): self.lang = l
        def set_hotwords(self, h): pass
        def set_builtin_terms(self, on): pass
        def set_auto_languages(self, l): pass
        def load_model(self, *a, **k): loads.append(a[0])
        def unload(self): loads.append("unload")

    monkeypatch.setattr(asr, "AsrEngine", Fake)
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda info: True)
    monkeypatch.setattr(cli, "resolve_asr", lambda engine, settings: "qwen3-asr-06b-int8")
    monkeypatch.setattr(models, "get_model_path", lambda mid: f"/models/{mid}")
    seen = []

    def fake_transcribe(path, engine, **kw):
        seen.append(engine)
        return transcribe.Transcript(segments=[transcribe.Segment(0.0, 1.0, "hello")], duration=1.0, engine="fake")

    monkeypatch.setattr(transcribe, "transcribe_file", fake_transcribe)
    jobs = tmp_path / "jobs.jsonl"
    lines = []
    for i in range(3):
        src = tmp_path / f"{i}.wav"; src.write_bytes(b"RIFF")
        lines.append(json.dumps(["transcribe", str(src), "--out", str(tmp_path / f"{i}.txt")]))
    jobs.write_text("\n".join(lines), encoding="utf-8")
    rc, events = run("batch", str(jobs))
    assert rc == 0, events[-1]
    assert loads == ["/models/qwen3-asr-06b-int8", "unload"]                  # one load for three files
    assert len(seen) == 3 and seen[0] is seen[1] is seen[2]
