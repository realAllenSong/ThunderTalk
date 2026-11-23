"""`thundertalk batch JOBS`: many jobs in one call, for agents and scripts.

JOBS is a file with one job per line, either a JSON array of command-line
arguments (``["transcribe", "a.m4a", "--out", "a.md"]``), a JSON object with an
``argv`` array, or a plain command line (``speak --text-file s.txt --out s.wav``).
Blank lines and lines starting with ``#`` are skipped.

* Jobs run one after another. With the app running each goes to its queue
  (models stay loaded between jobs and dictation stays first); without it they
  run here, and a speech recognizer or voice engine is loaded once for all of
  them rather than once per job.
* A job whose ``--out`` file already exists is skipped (``--redo`` runs it again),
  so a batch that stopped half-way picks up where it left off.
* One failed job does not stop the others (``--stop-on-error`` does). The
  result lists every job; ``ok`` is true when none failed.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path


def add_parser(sub) -> None:
    p = sub.add_parser("batch", help="many transcribe / speak / translate / notes / burn jobs from one file")
    p.add_argument("jobs", help="file with one job per line (JSON array, {\"argv\": [...]} or a command line)")
    p.add_argument("--redo", action="store_true", help="run jobs whose --out file already exists")
    p.add_argument("--stop-on-error", action="store_true", help="stop at the first failed job")


def read_jobs(path: Path) -> list[list[str]]:
    from thundertalk.cli import CliError
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CliError("not_found", f"Jobs file not found: {path}") from exc
    jobs = []
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if line.startswith("["):
                argv = json.loads(line)
            elif line.startswith("{"):
                argv = json.loads(line).get("argv")
            else:
                argv = shlex.split(line)
        except ValueError as exc:
            raise CliError("bad_args", f"Line {n}: {exc}") from exc
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise CliError("bad_args", f"Line {n}: expected a list of arguments")
        if argv[0] in ("batch", "--cli", "serve"):           # serve never returns
            raise CliError("bad_args", f"Line {n}: a batch cannot contain {argv[0]}")
        jobs.append(argv)
    if not jobs:
        raise CliError("bad_args", "The jobs file has no jobs")
    return jobs


class _ItemOut:
    def __init__(self, out, item: int) -> None:
        self.out, self.item = out, item
        self.result: dict = {}

    def emit(self, **event) -> None:
        if event.get("type") == "result":
            self.result = {k: v for k, v in event.items() if k != "type"}
        self.out.emit(**event, item=self.item)

    def stage(self, label: str) -> None:
        self.emit(type="stage", label=label)

    def progress(self, value: float) -> None:
        self.emit(type="progress", value=round(min(max(value, 0.0), 1.0), 3))


class _LocalModels:
    """Without the app: recognizers loaded once for the whole batch, by model and language."""

    def __init__(self) -> None:
        self.engines: dict[tuple[str, str], object] = {}

    def __call__(self, model_id: str, language: str):
        from thundertalk.cli import _settings
        from thundertalk.core.asr import AsrEngine
        from thundertalk.core.models import find, get_model_path
        from thundertalk.core.transcribe import _has_model_headroom
        key = (model_id, language or "auto")
        if key not in self.engines:
            info = find(model_id)
            if info is None or not _has_model_headroom(info):
                return None                     # the job loads (and waits) on its own, as without a batch
            settings = _settings()
            eng = AsrEngine()
            eng.set_language(language or "auto")
            if settings.hotwords:
                eng.set_hotwords(settings.hotwords)
            eng.set_builtin_terms(settings.get("builtin_terms") is not False)
            eng.set_auto_languages(settings.get("auto_languages") or ["zh", "en"])
            eng.load_model(get_model_path(model_id), info.family, info.backend, memory_mode=settings.memory_mode)
            self.engines[key] = eng
        return self.engines[key]

    def close(self) -> None:
        for eng in self.engines.values():
            eng.unload()
        self.engines.clear()


def cmd_batch(a, out) -> dict:
    from thundertalk import cli, service
    jobs = read_jobs(Path(a.jobs).expanduser())
    parser = cli._parser()
    parsed = []
    for i, argv in enumerate(jobs):
        try:
            parsed.append(parser.parse_args(argv))
        except cli.CliError as exc:
            raise cli.CliError("bad_args", f"Job {i + 1} ({' '.join(argv[:2])}): {exc}") from exc
    remote = cli.app_service()
    out.emit(type="stage", label="batch", jobs=len(parsed), via="app" if remote else "here")
    local = _LocalModels() if not remote else None
    if local is not None:
        cli.JOB.asr = local
        cli.JOB.keep_models = True               # the voice engine too stays loaded between jobs
    items, failed, skipped = [], 0, 0
    try:
        for i, ns in enumerate(parsed):
            dest = getattr(ns, "out", None)
            if dest and not a.redo and Path(dest).expanduser().exists():
                items.append({"item": i, "ok": True, "skipped": True, "output": str(Path(dest).expanduser())})
                skipped += 1
                out.emit(type="stage", label="skipped", item=i)
                continue
            iout = _ItemOut(out, i)
            if remote and ns.command in service.SERVICE_COMMANDS:
                result = service.run(ns.command, cli._absolute(ns), lambda e: iout.emit(**e), client="batch")
                if result is None:                # the app went away mid-batch: carry on here
                    cli.execute(ns, iout)
            else:
                cli.execute(ns, iout)
            r = iout.result or {"ok": False, "code": "failed", "error": "no result"}
            items.append({"item": i, **r})
            if not r.get("ok"):
                failed += 1
                if a.stop_on_error:
                    break
            out.progress((i + 1) / len(parsed))
    finally:
        if local is not None:
            local.close()
            cli.JOB.asr = None
            cli.JOB.keep_models = False
            from thundertalk.core import speech
            speech.get_engine().unload()
    return {"ok": failed == 0, "items": items, "succeeded": len(items) - failed - skipped, "failed": failed,
            "skipped": skipped, "not_run": len(parsed) - len(items)}
