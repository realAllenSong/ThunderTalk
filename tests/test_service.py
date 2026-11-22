"""The local job service: one queue for agents and scripts, dictation first, callers cancel by hanging up."""

from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from thundertalk import cli, service
from thundertalk.core import priority


@pytest.fixture
def sock_dir():
    d = Path(tempfile.mkdtemp(prefix="tt", dir="/tmp"))      # AF_UNIX paths must stay short
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def svc(sock_dir, monkeypatch):
    runs, gate = [], {}

    def runner(job, out):
        runs.append((job.command, job.args))
        out.stage("working")
        ev = gate.get(job.args.get("name"))
        if ev is not None:
            while not ev.wait(0.05):
                if job.cancel.is_set():
                    return {"ok": False, "code": "cancelled"}
        out.progress(1.0)
        return {"ok": True, "echo": job.args.get("name")}

    path = sock_dir / "s.sock"
    monkeypatch.setattr(service, "socket_path", lambda: path)
    s = service.JobService(runner, path, cooldown_s=0.0)
    s.start()
    s.runs, s.gate = runs, gate
    yield s
    s.stop()


def _request(path: Path, req: dict, read_until_result=True):
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(str(path))
    f = conn.makefile("rwb", buffering=0)
    f.write((json.dumps(req) + "\n").encode())
    events = []
    for raw in f:
        events.append(json.loads(raw))
        if not read_until_result or events[-1].get("type") == "result":
            break
    return conn, events


def test_ping_reports_the_protocol_and_only_this_user_can_connect(svc):
    info = service.available()
    assert info and info["v"] == service.PROTOCOL and info["ok"]
    assert oct(svc.path.stat().st_mode)[-3:] == "600" and oct(svc.path.parent.stat().st_mode)[-3:] == "700"


def test_a_job_reports_its_place_then_its_own_events_then_one_result(svc):
    conn, events = _request(svc.path, {"op": "run", "command": "transcribe", "args": {"name": "a"}})
    conn.close()
    kinds = [e["type"] for e in events]
    assert kinds[0] == "queued" and events[0]["position"] == 1
    assert ("stage", "working") in [(e["type"], e.get("label")) for e in events]
    assert kinds.count("result") == 1 and events[-1] == {"type": "result", "ok": True, "echo": "a"}


def test_light_or_unknown_commands_are_refused(svc):
    _, events = _request(svc.path, {"op": "run", "command": "models", "args": {}})
    assert events[-1]["ok"] is False and events[-1]["code"] == "bad_args"


def test_jobs_run_one_at_a_time_in_the_order_they_came(svc):
    svc.gate["first"] = threading.Event()
    results = {}

    def call(name):
        _, ev = _request(svc.path, {"op": "run", "command": "speak", "args": {"name": name}})
        results[name] = ev

    t1 = threading.Thread(target=call, args=("first",)); t1.start()
    time.sleep(0.3)
    t2 = threading.Thread(target=call, args=("second",)); t2.start()
    time.sleep(0.3)
    assert [r[1]["name"] for r in svc.runs] == ["first"]                 # the second waits its turn
    assert [j["state"] for j in svc.snapshot()] == ["running", "queued"]
    svc.gate["first"].set()
    t1.join(5); t2.join(5)
    assert [r[1]["name"] for r in svc.runs] == ["first", "second"]
    assert results["second"][0]["position"] == 2


def test_a_caller_that_hangs_up_has_its_job_dropped_or_stopped(svc):
    svc.gate["long"] = threading.Event()
    svc.gate["queued"] = threading.Event()
    running, _ = _request(svc.path, {"op": "run", "command": "speak", "args": {"name": "long"}}, False)
    waiting, _ = _request(svc.path, {"op": "run", "command": "speak", "args": {"name": "queued"}}, False)
    time.sleep(0.3)
    waiting.close()                                                         # gone before it started
    time.sleep(0.6)
    assert [j["command"] for j in svc.snapshot()] == ["speak"] and len(svc.snapshot()) == 1
    running.close()                                                         # gone while running
    deadline = time.time() + 3
    while svc.snapshot() and time.time() < deadline:
        time.sleep(0.05)
    assert svc.snapshot() == [] and [r[1]["name"] for r in svc.runs] == ["long"]


def test_no_job_starts_while_the_user_dictates_or_just_after(svc, monkeypatch):
    p = priority.DictationPriority(stale_s=60.0)
    monkeypatch.setattr(priority, "DICTATION", p)
    svc.cooldown_s = 0.5
    p.begin()
    done = []
    t = threading.Thread(target=lambda: done.append(_request(svc.path, {"op": "run", "command": "speak",
                                                                          "args": {"name": "x"}})[1]))
    t.start()
    time.sleep(0.4)
    assert svc.runs == []                                                   # dictating
    t0 = time.monotonic()
    p.end()
    t.join(5)
    assert svc.runs and time.monotonic() - t0 >= 0.45                       # and the moment after
    labels = [e.get("label") for e in done[0]]
    assert "yield" in labels and "start" in labels


def test_a_second_service_will_not_take_over_a_live_socket(svc):
    with pytest.raises(RuntimeError):
        service.JobService(lambda j, o: {}, svc.path).start()


def test_a_socket_left_by_a_crash_is_replaced(sock_dir):
    path = sock_dir / "s.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path)); stale.close()                                   # file there, nobody listening
    s = service.JobService(lambda j, o: {"ok": True}, path)
    s.start()
    try:
        assert service._ping(path, 0.5)["ok"]
    finally:
        s.stop()


# ── the command line hands heavy jobs to the app ──
def test_heavy_commands_go_to_the_app_with_absolute_paths(svc, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "talk.m4a").touch()
    events = []
    monkeypatch.setattr(cli, "_Out", lambda: SimpleNamespace(emit=lambda **e: events.append(e)))
    rc = cli.main(["transcribe", "talk.m4a", "--out", "talk.md"])
    assert rc == 0 and events[-1]["type"] == "result" and events[-1]["ok"]
    command, args = svc.runs[-1]
    assert command == "transcribe"
    assert args["input"] == [str(tmp_path / "talk.m4a")] and args["out"] == str(tmp_path / "talk.md")


def test_urls_and_saved_voices_are_not_turned_into_paths():
    ns = cli._parser().parse_args(["transcribe", "https://youtu.be/x", "relative.wav", "--out-dir", "o"])
    args = cli._absolute(ns)
    assert args["input"][0] == "https://youtu.be/x" and Path(args["input"][1]).is_absolute()
    ns = cli._parser().parse_args(["speak", "--text", "hi", "--voice", "my:me", "--out", "a.wav"])
    assert cli._absolute(ns)["voice"] == "my:me"


def test_light_commands_and_thundertalk_local_stay_in_this_process(svc, monkeypatch):
    out = SimpleNamespace(emit=lambda **e: None)
    assert cli._forward(cli._parser().parse_args(["status"]), out) is None
    monkeypatch.setenv("THUNDERTALK_LOCAL", "1")
    assert cli._forward(cli._parser().parse_args(["speak", "--text", "hi", "--out", "a.wav"]), out) is None


def test_without_the_app_the_job_runs_here(monkeypatch, sock_dir):
    monkeypatch.setattr(service, "socket_path", lambda: sock_dir / "none.sock")
    assert service.available() is None
    assert cli._forward(cli._parser().parse_args(["speak", "--text", "hi", "--out", "a.wav"]),
                        SimpleNamespace(emit=lambda **e: None)) is None


# ── waiting instead of failing ──
def test_a_heavy_job_waits_for_memory_then_goes_on(monkeypatch):
    from thundertalk.core import transcribe
    answers = iter([False, False, True])
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda info: next(answers))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    events = []
    out = SimpleNamespace(emit=lambda **e: events.append(e), stage=lambda l: events.append({"label": l}))
    with cli.model_slot(out, heavy=True, info=SimpleNamespace(size_mb=1)):
        pass
    assert [e for e in events if e.get("label") == "waiting"] == [{"type": "stage", "label": "waiting",
                                                                    "reason": "memory"}]


def test_and_gives_up_with_a_clear_error_after_the_wait(monkeypatch):
    from thundertalk.core import transcribe
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda info: False)
    monkeypatch.setattr(cli, "WAIT_S", 0.0)
    out = SimpleNamespace(emit=lambda **e: None, stage=lambda l: None)
    with pytest.raises(cli.CliError) as e:
        with cli.model_slot(out, heavy=True, info=SimpleNamespace(size_mb=1)):
            pass
    assert e.value.code == "memory"


def test_dictation_cooldown_counts_from_the_end_of_the_last_dictation():
    p = priority.DictationPriority(stale_s=60.0)
    assert p.since_end() == float("inf")
    p.begin(); p.end()
    assert p.since_end() < 0.2
    t0 = time.monotonic()
    p.wait_clear(cooldown=0.3)
    assert time.monotonic() - t0 >= 0.25


def test_an_app_of_another_version_is_not_handed_jobs(svc, monkeypatch):
    assert cli.app_service() is True
    monkeypatch.setattr(service, "available", lambda timeout=0.3: {"ok": True, "v": 1, "version": "0.0.0-other"})
    assert cli.app_service() is False
    assert cli._forward(cli._parser().parse_args(["speak", "--text", "hi", "--out", "a.wav"]),
                        SimpleNamespace(emit=lambda **e: None)) is None
