"""ThunderTalk as a local service: one queue for every agent and script.

While the app runs it listens on a Unix socket that only this user can open.
`thundertalk <command>` (and so the MCP server, which runs the command line)
hands heavy jobs to it instead of running them in a process of its own:

* **one copy of each model.** A transcription with the dictation model uses the
  app's own loaded engine; nothing is loaded twice and nothing waits for a load.
* **one job at a time,** in the order they arrived, so memory stays bounded no
  matter how many agents call at once; a caller sees its place in the queue.
* **dictation first.** The queue does not start a job while the user is
  dictating or within a moment after (they often go on), and jobs that run long
  pause between their units of work.

When the app is not running, the command line runs the job itself, as before.

Protocol, version 1: the client sends one JSON line and reads JSON lines back.
``{"op": "ping"}`` → ``{"ok": true, "v": 1, "version": "…", "queued": n}``;
``{"op": "run", "command": "…", "args": {…}}`` → ``{"type": "queued", …}``,
then the command's own ``stage`` / ``progress`` events, then its ``result``;
``{"op": "jobs"}`` → the queue. Closing the connection cancels that job.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PROTOCOL = 1
# Heavy jobs: these load models. Everything else (status, voices, models,
# history, cloud keys) is quick and stays in the caller's own process.
SERVICE_COMMANDS = ("transcribe", "speak", "translate", "notes", "burn", "compose")


def socket_path() -> Path:
    from thundertalk.core.priority import _shared_path
    return _shared_path("service.sock")


# ── jobs ──────────────────────────────────────────────────────────────────

@dataclass
class Job:
    id: int
    command: str
    args: dict
    client: str = ""
    events: "queue.Queue[Optional[dict]]" = field(default_factory=queue.Queue)
    cancel: threading.Event = field(default_factory=threading.Event)
    state: str = "queued"           # queued | running | done | cancelled
    created: float = field(default_factory=time.time)


class JobOut:
    """The command's output object (``emit`` / ``stage`` / ``progress``), sent to its caller."""

    def __init__(self, job: Job) -> None:
        self._job = job
        self._last = -1.0

    def emit(self, **event) -> None:
        self._job.events.put(event)

    def stage(self, label: str) -> None:
        self.emit(type="stage", label=label)

    def progress(self, value: float) -> None:
        value = round(min(max(value, 0.0), 1.0), 3)
        if value != self._last:
            self._last = value
            self.emit(type="progress", value=value)


Runner = Callable[[Job, JobOut], Optional[dict]]


class JobService:
    """The queue and its one worker; ``runner`` does a job and returns its result dict."""

    def __init__(self, runner: Runner, path: Optional[Path] = None, *, cooldown_s: float = 1.5) -> None:
        self.runner = runner
        self.path = Path(path) if path else socket_path()
        self.cooldown_s = cooldown_s
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._waiting: list[Job] = []
        self._running: Optional[Job] = None
        self._wake = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._threads: list[threading.Thread] = []

    # ── life ──
    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists():
            if _ping(self.path, 0.3) is not None:
                raise RuntimeError("another ThunderTalk service is already running")
            self.path.unlink()                    # left by a crash
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(self.path))
        os.chmod(self.path, 0o600)
        s.listen(16)
        s.settimeout(0.5)
        self._sock = s
        for target in (self._accept_loop, self._work_loop):
            t = threading.Thread(target=target, daemon=True, name=f"tt-service-{target.__name__}")
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        with self._wake:
            for job in self._waiting:
                job.cancel.set()
                job.events.put(None)
            self._waiting.clear()
            if self._running is not None:
                self._running.cancel.set()
            self._wake.notify_all()
        if self._sock is not None:
            self._sock.close()
        try:
            self.path.unlink()
        except OSError:
            pass
        for t in self._threads:
            t.join(timeout=2)

    # ── queue ──
    def submit(self, command: str, args: dict, client: str = "") -> tuple[Job, int]:
        job = Job(next(self._ids), command, args, client)
        with self._wake:
            self._waiting.append(job)
            position = len(self._waiting) + (1 if self._running is not None else 0)
            self._wake.notify_all()
        return job, position

    def withdraw(self, job: Job) -> None:
        """The caller went away: drop the job if it has not started, else ask it to stop."""
        job.cancel.set()
        with self._wake:
            if job in self._waiting:
                self._waiting.remove(job)
                job.state = "cancelled"
                job.events.put(None)

    def snapshot(self) -> list[dict]:
        with self._lock:
            jobs = ([self._running] if self._running else []) + list(self._waiting)
            return [{"id": j.id, "command": j.command, "client": j.client, "state": j.state,
                     "seconds": round(time.time() - j.created, 1)} for j in jobs]

    def _next(self) -> Optional[Job]:
        with self._wake:
            while not self._waiting and not self._stop.is_set():
                self._wake.wait(0.5)
            if self._stop.is_set():
                return None
            job = self._waiting.pop(0)
            job.state = "running"
            self._running = job
            return job

    def _work_loop(self) -> None:
        from thundertalk.core import priority
        while not self._stop.is_set():
            job = self._next()
            if job is None:
                return
            try:
                # Never start while the user dictates, nor right after: they often go on.
                waited = priority.DICTATION.wait_clear(job.cancel, on_wait=lambda j=job: j.events.put(
                    {"type": "stage", "label": "yield"}), cooldown=self.cooldown_s)
                if job.cancel.is_set():
                    result = {"ok": False, "code": "cancelled", "error": "Cancelled"}
                else:
                    job.events.put({"type": "stage", "label": "start", "waited_s": round(waited, 2)})
                    result = self.runner(job, JobOut(job))      # None: the runner sent its own result
            except Exception as exc:                           # noqa: BLE001 - reported to the caller
                result = {"ok": False, "code": "failed", "error": str(exc)}
            if result is not None:
                result.setdefault("ok", True)
                job.events.put({"type": "result", **result})
            job.events.put(None)
            with self._wake:
                job.state = "cancelled" if job.cancel.is_set() else "done"
                self._running = None

    # ── connections ──
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True, name="tt-service-conn").start()

    def _serve(self, conn: socket.socket) -> None:
        import thundertalk
        with conn:
            f = conn.makefile("rwb", buffering=0)
            try:
                line = f.readline()
                req = json.loads(line or b"{}")
            except ValueError:
                return
            send = lambda obj: f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())  # noqa: E731
            op = req.get("op")
            try:
                if op == "ping":
                    send({"ok": True, "v": PROTOCOL, "version": thundertalk.__version__,
                          "queued": len(self.snapshot())})
                    return
                if op == "jobs":
                    send({"ok": True, "jobs": self.snapshot()})
                    return
                if op != "run" or req.get("command") not in SERVICE_COMMANDS:
                    send({"type": "result", "ok": False, "code": "bad_args", "error": f"Unknown request: {op}"})
                    return
                job, position = self.submit(req["command"], req.get("args") or {}, str(req.get("client") or ""))
                send({"type": "queued", "job": job.id, "position": position})
                watcher = threading.Thread(target=self._watch_hangup, args=(conn, job), daemon=True)
                watcher.start()
                while True:
                    event = job.events.get()
                    if event is None:
                        return
                    send(event)
            except (BrokenPipeError, ConnectionResetError, OSError):
                if "job" in locals():
                    self.withdraw(job)

    def _watch_hangup(self, conn: socket.socket, job: Job) -> None:
        """A caller that hangs up (Ctrl-C, a killed agent) gets its job cancelled."""
        while job.state in ("queued", "running"):
            try:
                if conn.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b"":
                    self.withdraw(job)
                    return
            except BlockingIOError:
                pass
            except OSError:
                self.withdraw(job)
                return
            time.sleep(0.2)


# ── client ────────────────────────────────────────────────────────────────

def _ping(path: Path, timeout: float) -> Optional[dict]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(b'{"op": "ping"}\n')
            data = s.makefile("rb").readline()
        return json.loads(data or b"{}") or None
    except (OSError, ValueError):
        return None


def available(timeout: float = 0.3) -> Optional[dict]:
    """The running app's service, if it speaks our protocol."""
    info = _ping(socket_path(), timeout)
    return info if info and info.get("v") == PROTOCOL else None


def run(command: str, args: dict, emit: Callable[[dict], None], client: str = "cli") -> Optional[dict]:
    """Hand a job to the app and relay its events; the final result, or None
    when the service went away before answering (the caller then runs it itself)."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(socket_path()))
    except OSError:
        return None
    with s:
        f = s.makefile("rwb", buffering=0)
        f.write((json.dumps({"op": "run", "command": command, "args": args, "client": client},
                            ensure_ascii=False) + "\n").encode())
        result = None
        for raw in f:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if event.get("type") == "result":
                result = event
            emit(event)
        return result
