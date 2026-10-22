"""Media input lifetime, transcript import and batch transcription for CLI v3."""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from thundertalk.cli import CliError


def media_options(parser):
    parser.add_argument("--engine", default="auto", help="auto | best | apple | model id")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--speakers", action="store_true")
    parser.add_argument("--start", type=float, default=0, help="start offset in seconds (needs ffmpeg)")
    parser.add_argument("--end", type=float, help="end offset in seconds (needs ffmpeg)")
    parser.add_argument("--max-seconds", type=float, help="limit decoded audio (download still fetches full audio)")


def validate_range(a):
    start, end, limit = a.start, a.end, a.max_seconds
    if (not math.isfinite(start) or start < 0 or
            end is not None and (not math.isfinite(end) or end <= start) or
            limit is not None and (not math.isfinite(limit) or limit <= 0)):
        raise CliError("bad_args", "Require finite --start >= 0, --end > start, --max-seconds > 0")


@contextmanager
def source(value: str, out, a=None):
    """Fetch and optionally trim into private storage, deleted even on SIGINT."""
    from thundertalk.core import audio_io, links
    fetched = None
    try:
        if a is not None:
            validate_range(a)
        path = Path(value).expanduser()
        if path.is_file():
            path = path.resolve()
            title, url, duration = path.stem, "", 0.0
        elif links.is_url(value):
            urls = links.find_urls(value)
            if len(urls) != 1:
                raise CliError("bad_args", "Pass each URL as a separate argument")
            out.stage("download")
            def progress(pct, done, total):
                out.emit(type="progress", label="download", value=pct / 100 if pct >= 0 else None,
                         bytes_done=done, bytes_total=total)
            fetched = links.fetch_audio(urls[0], progress=progress)
            path = Path(fetched.path)
            title, url, duration = fetched.title, fetched.url, fetched.duration
        else:
            raise CliError("not_found", f"Input file not found: {path}")
        with tempfile.TemporaryDirectory(prefix="thundertalk-cli-") as work:
            if a is not None and (a.start or a.end is not None or a.max_seconds is not None):
                ffmpeg = audio_io.find_ffmpeg()
                if not ffmpeg:
                    raise CliError("ffmpeg_missing", "Trimming needs ffmpeg (brew install ffmpeg)")
                trimmed = Path(work) / "clip.wav"
                cmd = [ffmpeg, "-nostdin", "-v", "error", "-ss", str(a.start), "-i", str(path)]
                lengths = [x for x in (a.end - a.start if a.end is not None else None, a.max_seconds) if x is not None]
                if lengths:
                    cmd += ["-t", str(min(lengths))]
                cmd += ["-vn", "-ac", "1", "-ar", "16000", str(trimmed)]
                out.stage("trim")
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                try:
                    _, err = proc.communicate()
                    if proc.returncode:
                        raise CliError("decode", err[-500:])
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                path = trimmed
            yield path, title, url, duration
    finally:
        if fetched:
            shutil.rmtree(fetched.workdir, ignore_errors=True)


def load_transcript(value: str):
    """Read CLI/core JSON, plain text, Markdown, SRT or VTT without a model."""
    from thundertalk.core.transcribe import Segment, Transcript
    path = Path(value).expanduser()
    if not path.is_file():
        raise CliError("not_found", f"Transcript not found: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".json":
            data = __import__("json").loads(text)
            segments = [Segment(float(s["start"]), float(s["end"]), s["text"], s.get("speaker", ""), s.get("words", []))
                        for s in data["segments"]]
            tr = Transcript(segments, float(data["duration"]), data.get("engine", "import"),
                            has_speakers=any(s.speaker for s in segments),
                            title=data.get("title", path.stem), source_url=data.get("source_url", ""),
                            model_id=data.get("model_id", data.get("model", "")),
                            speaker_names=data.get("speaker_names", {}), notes=data.get("notes", ""))
        elif path.suffix.lower() in (".srt", ".vtt"):
            stamp = r"(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})"
            pattern = re.compile(stamp + r"\s+-->\s+" + stamp + r"[^\n]*\n(.+?)(?=\n\s*\n|\Z)", re.S)
            def secs(values):
                h, m, s, ms = values
                return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
            segments = [Segment(secs(m.groups()[:4]), secs(m.groups()[4:8]), m.group(9).strip())
                        for m in pattern.finditer(text)]
            if not segments:
                raise ValueError("No subtitle cues")
            tr = Transcript(segments, max(s.end for s in segments), "import", title=path.stem)
        else:
            tr = Transcript([Segment(0, 0, text.strip())], 0, "import", title=path.stem)
        if (not tr.segments or not tr.to_text().strip() or not math.isfinite(tr.duration) or tr.duration < 0 or
                any(not math.isfinite(s.start) or not math.isfinite(s.end) or s.start < 0 or s.end < s.start
                    for s in tr.segments)):
            raise ValueError("Empty or invalid transcript")
        return tr
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CliError("bad_transcript", str(exc)) from exc


class ItemOut:
    def __init__(self, out, item, value):
        self.out, self.item, self.value = out, item, value

    def emit(self, **event):
        self.out.emit(**event, item=self.item, input=self.value)

    def stage(self, label):
        self.emit(type="stage", label=label)

    def progress(self, value):
        self.emit(type="progress", value=round(value, 3))


def cmd_transcribe(a, out):
    from thundertalk.cli import FORMATS, _transcribe_path, _write, error_result, export_transcript
    from thundertalk.core import links
    validate_range(a)
    if not a.out and not a.out_dir or a.out and a.out_dir or len(a.input) > 1 and not a.out_dir:
        raise CliError("bad_args", "Use --out for one input or --out-dir for a batch")
    if a.formats and not a.out_dir:
        raise CliError("bad_args", "--formats needs --out-dir")
    results = []
    for i, value in enumerate(a.input):
        events = ItemOut(out, i, value) if a.out_dir else out
        try:
            with source(value, events, a) as (path, title, url, duration):
                dest = Path(a.out).expanduser().resolve() if a.out else None
                fmt = a.format or (dest.suffix.lstrip(".").lower() if dest and dest.suffix.lstrip(".").lower() in FORMATS else "txt")
                formats = a.formats or [fmt]
                if dest and path.resolve() == dest:
                    raise CliError("bad_args", "Output must differ from input")
                tr, result = _transcribe_path(a, events, path)
                tr.title, tr.source_url = title, url
                tr.expected_duration = duration
                if url and tr.is_partial and not (a.start or a.end is not None or a.max_seconds is not None):
                    raise CliError("login", "The site returned a preview; sign in for the complete media")
                for speaker, name in a.speaker_name:
                    tr.rename_speaker(speaker, name)
                if a.timestamps and fmt == "txt":
                    text = tr.to_text(timestamps=True) + "\n"
                else:
                    text = export_transcript(tr, fmt)[0]
                if a.out_dir:
                    folder = Path(a.out_dir).expanduser().resolve()
                    folder.mkdir(parents=True, exist_ok=True)
                    # Reserve one stem for every format, including files left by earlier runs.
                    stem, n = links.safe_name(title), 2
                    base = stem
                    while any((folder / f"{stem}.{f}").exists() for f in formats):
                        stem, n = f"{base} ({n})", n + 1
                    paths = []
                    for f in formats:
                        p = folder / f"{stem}.{f}"
                        _write(p, tr.to_text(timestamps=True) + "\n" if a.timestamps and f == "txt" else export_transcript(tr, f)[0])
                        paths.append(str(p))
                    dest = Path(paths[0])
                else:
                    _write(dest, text)
                    paths = [str(dest)]
                if a.save_history:
                    from thundertalk.core.transcript_history import TranscriptHistory
                    result["history_id"] = TranscriptHistory().save(tr, value)
                result.update(output=str(dest), outputs=paths, format=fmt, title=title, source_url=url,
                              start=a.start, ok=True)
                events.progress(1.0)
        except Exception as exc:
            if not a.out_dir:
                raise
            result = error_result(exc)
        if a.out_dir:
            events.emit(type="result", **result)
            results.append(dict(result, item=i, input=value))
        else:
            return result
    failed = sum(not r["ok"] for r in results)
    return dict(ok=not failed, code="batch_failed" if failed else "ok", items=results,
                succeeded=len(results) - failed, failed=failed)


def speaker_pair(value):
    key, sep, name = value.partition("=")
    if not sep or not key.strip() or not name.strip():
        raise __import__("argparse").ArgumentTypeError("Use SPEAKER=NAME")
    return key, name
