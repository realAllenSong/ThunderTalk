"""Headless Compose uses the same disposable worker and memory guard as Studio."""
from __future__ import annotations

import json
import shutil
import threading
from dataclasses import asdict
from pathlib import Path

from thundertalk.cli import CliError, _settings


def add_parser(sub):
    p = sub.add_parser("compose", help="song / instrumental, lyric checks, repaint and take files")
    p.add_argument("action", nargs="?", default="generate", choices=("generate", "check", "repaint", "list", "show", "export", "delete"))
    p.add_argument("input", nargs="?", help="take WAV (with its JSON sidecar)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--instrumental", action="store_true")
    mode.add_argument("--song", action="store_true")
    p.add_argument("--style", default="")
    lyrics = p.add_mutually_exclusive_group()
    lyrics.add_argument("--lyrics")
    lyrics.add_argument("--lyrics-file")
    p.add_argument("--sung-lyrics", help="JSON file mapping shown lyric lines to sung text")
    p.add_argument("--length", type=int, help="10–600 seconds, within the hardware tier")
    p.add_argument("--bpm", type=int)
    p.add_argument("--language", help="vocal language code, e.g. zh, en")
    p.add_argument("--takes", type=int, default=1)
    p.add_argument("--seed", type=int)
    p.add_argument("--model", choices=("turbo", "xl"))
    p.add_argument("--planner", choices=("on", "off"), default="off")
    p.add_argument("--dit-backend", choices=("auto", "mlx", "mps"), default="auto")
    p.add_argument("--out-dir", help="new job directory under this root (default ~/Music/ThunderTalk)")
    p.add_argument("--out", help="destination for compose export")
    p.add_argument("--start", type=float, help="repaint range, seconds")
    p.add_argument("--end", type=float, help="repaint range, seconds")


def read_meta(path):
    wav = Path(path).expanduser().resolve()
    if not wav.is_file():
        raise CliError("not_found", f"Take not found: {wav}")
    if wav.suffix.lower() != ".wav":
        raise CliError("bad_args", "Use a Compose take WAV with a JSON sidecar")
    try:
        meta = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or not isinstance(meta.get("duration"), (int, float)) or meta["duration"] <= 0:
            raise ValueError("Missing take duration")
        return wav, meta
    except (ValueError, TypeError) as exc:
        raise CliError("bad_take", str(exc)) from exc


def check_lyrics(path, meta, out):
    from thundertalk.core import music_check, models, transcribe
    from thundertalk.core.priority import DICTATION
    if meta.get("instrumental") or not (meta.get("lyrics") or meta.get("shown_lyrics")):
        return dict(status="instrumental")
    ordered = ["qwen3-asr-06b-int8", "qwen3-asr-06b-mlx", "qwen3-asr-17b-mlx"]
    chosen = next((m for m in ordered if models.is_downloaded(m)), None)
    if not chosen:
        return dict(status="unavailable", code="music:no_asr")
    out.stage("lyric_check")
    def between():
        DICTATION.wait_clear(on_wait=lambda: out.stage("yield"))
    try:
        with transcribe.selected_engine(None, chosen) as engine:
            engine.set_language(meta.get("vocal_language") or "auto")
            result = music_check.check_take(Path(path), lambda x: engine.recognize(x, 16000).text,
                                           meta=meta, between=between)
        if result is None:
            return dict(status="instrumental")
        clear, total = result.summary
        return dict(status="checked", clear=clear, total=total, timed=result.timed,
                    lines=[asdict(c) for c in result.checks], ranges=result.line_ranges)
    except Exception as exc:
        # The check is advisory. A finished song must remain usable if ASR fails.
        from thundertalk.cli import error_result
        return dict(status="unavailable", **error_result(exc))


def _job_dir(root, title):
    import time
    from thundertalk.core import music
    root = Path(root).expanduser().resolve() if root else music.takes_root()
    root.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("%Y-%m-%d-%H%M") + "-" + music.slug(title)
    for n in range(1, 100000):
        path = root / (stem if n == 1 else f"{stem}-{n}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise CliError("failed", "Could not allocate a job folder")


def cmd_compose(a, out):
    from thundertalk.core import music, music_lyrics, music_process, music_rules
    from thundertalk.core.model_metadata import ACESTEP_LANGUAGES
    from thundertalk.core.music_worker import MusicRequest, take_seeds
    if a.action == "list":
        root = Path(a.out_dir).expanduser() if a.out_dir else music.takes_root()
        return dict(takes=[str(p.resolve()) for p in sorted(root.glob("*/take-*.wav"))])
    wav = meta = None
    if a.action != "generate":
        if not a.input:
            raise CliError("bad_args", f"compose {a.action} needs a take WAV")
        wav, meta = read_meta(a.input)
        if a.action == "show":
            return dict(output=str(wav), metadata=meta)
        if a.action == "check":
            return dict(output=str(wav), lyric_check=check_lyrics(wav, meta, out))
        if a.action == "export":
            if not a.out:
                raise CliError("bad_args", "compose export needs --out")
            dest = Path(a.out).expanduser().resolve()
            if dest == wav or dest.suffix.lower() != ".wav":
                raise CliError("bad_args", "Export to a different .wav file")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(wav, dest)
            shutil.copyfile(wav.with_suffix(".json"), dest.with_suffix(".json"))
            return dict(output=str(dest))
        if a.action == "delete":
            # Only predictable sidecars, never paths taken from metadata.
            for path in (wav, wav.with_suffix(".json"), wav.with_suffix(".latents.npy")):
                path.unlink(missing_ok=True)
            return dict(output=str(wav), action="delete")
    total_mb = music_process.total_memory_mb()
    tier = music.tier(total_mb / 1024)
    if not music.supported(total_mb / 1024) or tier is None:
        raise CliError("unsupported", "Compose requires Apple Silicon and at least 16 GB memory")
    repaint = a.action == "repaint"
    folder = meta.get("model") if repaint else None
    selected = "acestep-" + a.model if a.model else (
        next((m.info.id for m in music.MUSIC_MODELS if m.folder == folder), "") if repaint else
        _settings().get("compose_model") or tier.recommended)
    if selected not in tier.models:
        raise CliError("unsupported", "This model needs more memory than this machine has")
    m = music.model(selected)
    planner = a.planner == "on" and not repaint
    if planner and not tier.planner:
        raise CliError("unsupported", "The planner requires at least 24 GB memory")
    seconds = a.length if a.length is not None else int(meta["duration"]) if repaint else 30
    if not music.MIN_SECONDS <= seconds <= tier.max_seconds:
        raise CliError("bad_args", f"--length must be between 10 and {tier.max_seconds}")
    if not 1 <= a.takes <= music.MAX_TAKES or total_mb < 24 * 1024 and a.takes != 1:
        raise CliError("bad_args", "Use 1–5 takes (one take below 24 GB)")
    if a.bpm is not None and not 30 <= a.bpm <= 300 or a.seed is not None and not 0 <= a.seed < 2**32:
        raise CliError("bad_args", "Require BPM 30–300 and seed 0–4294967295")
    language = a.language or (meta.get("vocal_language") if repaint else "en") or "en"
    if language not in (*ACESTEP_LANGUAGES, "unknown"):
        raise CliError("bad_args", "Unknown vocal language code")
    lyrics = a.lyrics or ""
    if a.lyrics_file:
        lyrics = Path(a.lyrics_file).expanduser().read_text(encoding="utf-8")
    elif repaint and a.lyrics is None:
        lyrics = meta.get("shown_lyrics") or meta.get("lyrics") or ""
    style = a.style or (meta.get("caption", "") if repaint else "")
    instrumental = a.instrumental or bool(meta and meta.get("instrumental"))
    if not style.strip() and not lyrics.strip() or not instrumental and not music_lyrics.lyric_lines(lyrics):
        raise CliError("bad_args", "Supply a style for an instrumental or lyrics for a song")
    if len(style) > music.STYLE_MAX_CHARS or len(lyrics) > music.LYRICS_MAX_CHARS:
        raise CliError("bad_args", "Style limit: 512 characters; lyrics limit: 4096")
    overrides = {}
    if a.sung_lyrics:
        try:
            overrides = json.loads(Path(a.sung_lyrics).expanduser().read_text(encoding="utf-8"))
            if not isinstance(overrides, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in overrides.items()):
                raise ValueError("Expected a string-to-string JSON object")
        except ValueError as exc:
            raise CliError("bad_args", f"Invalid --sung-lyrics: {exc}") from exc
    warnings = sorted({s.text for text in (style, lyrics) for s in music_rules.find_artist_mentions(text)})
    if warnings:
        out.emit(type="stage", label="artist_warning", warning=warnings)
    if music.missing(m, planner):
        raise CliError("no_model", f"Run: ThunderTalk --cli models download {selected}" + (" --planner" if planner else ""))
    backend = tier.dit_backend if a.dit_backend == "auto" else a.dit_backend
    if repaint:
        import math
        if (a.start is None or a.end is None or not math.isfinite(a.start) or not math.isfinite(a.end) or
                not 0 <= a.start < a.end <= meta["duration"]):
            raise CliError("bad_args", "Repaint requires 0 <= --start < --end <= take duration")
        filename = meta.get("repaint_source_latents_file") or ""
        if Path(filename).name != filename or not filename or not (wav.parent / filename).is_file():
            raise CliError("bad_take", "The repaint source latents are missing")
        if folder != m.folder:
            raise CliError("bad_args", "Repaint must use the original model")
    job = _job_dir(a.out_dir, style or lyrics or "song")
    req = MusicRequest(project_root=str(music.project_root()), model_folder=m.folder, job_dir=str(job),
                       caption=style, lyrics="" if instrumental else music_lyrics.sung_lyrics(lyrics, language, overrides),
                       shown_lyrics="" if instrumental else lyrics, instrumental=instrumental,
                       vocal_language="unknown" if instrumental else language, duration=seconds,
                       bpm=a.bpm if a.bpm is not None else meta.get("bpm") if repaint else None,
                       steps=m.steps, seeds=take_seeds(a.seed if not repaint else meta.get("seed"), a.takes),
                       planner=planner, dit_backend=backend, artist_warnings=warnings,
                       estimate_s=music.estimate_take_s(m, seconds, planner),
                       task_type="repaint" if repaint else "text2music", src_audio=str(wav) if repaint else None,
                       repainting_start=a.start or 0, repainting_end=a.end if a.end is not None else -1)
    takes = []
    def event(e):
        kind = e[0]
        if kind == "phase":
            out.emit(type="stage", label=e[1], take=e[2])
        elif kind in ("tokens", "step"):
            out.emit(type="progress", label="planning" if kind == "tokens" else "diffusion",
                     value=e[1] / max(1, e[2]), done=e[1], total=e[2])
        elif kind == "take":
            takes.append(dict(take=e[1], output=e[2], metadata=e[3]))
            out.emit(type="stage", label="take_written", take=e[1], output=e[2])
        else:
            out.emit(type="stage", label=kind, detail=list(e[1:]))
    failure = None
    try:
        result = music_process.MusicProcess().run(req, event, threading.Event(),
            ram_need_mb=music.ram_need_mb(m, planner, dit_backend=backend, repaint=repaint), total_mb=total_mb)
        if result == "cancelled":
            raise CliError("cancelled", "Compose was cancelled")
    except (Exception, KeyboardInterrupt) as exc:
        from thundertalk.cli import error_result
        failure = error_result(exc) if not isinstance(exc, KeyboardInterrupt) else dict(ok=False, code="cancelled", error="Interrupted")
    # The process has exited before ASR is allowed to load. Keep completed takes on any later error.
    for take in takes:
        take["lyric_check"] = check_lyrics(take["output"], take["metadata"], out)
        out.emit(type="result", ok=True, warning=warnings, **take)
    if failure:
        return dict(**failure, output=str(job), takes=takes, warning=warnings)
    out.progress(1.0)
    return dict(output=str(job), takes=takes, warning=warnings)
