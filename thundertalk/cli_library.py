"""CLI management of Studio voices, transcript history and model downloads."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from thundertalk.cli import FORMATS, CliError, _settings, _write, export_transcript
from thundertalk.cli_media import speaker_pair
from thundertalk.cli_studio import progress


def add_parsers(sub, vo):
    # Keep v2 `voices --engine …` as an alias of `voices list`.
    vo.add_argument("action", nargs="?", default="list", choices=("add", "list", "remove", "rename", "edit"))
    vo.add_argument("input", nargs="?", help="audio path for add; my:<id> for other actions")
    vo.add_argument("--name")
    text = vo.add_mutually_exclusive_group()
    text.add_argument("--transcript")
    text.add_argument("--transcript-file")
    hist = sub.add_parser("history", help="Studio transcripts (not dictation history)")
    hist.add_argument("action", choices=("list", "show", "export", "delete", "rename", "edit"))
    hist.add_argument("id", nargs="?")
    hist.add_argument("--query", default="")
    hist.add_argument("--offset", type=int, default=0)
    hist.add_argument("--limit", type=int, default=50)
    hist.add_argument("--out")
    hist.add_argument("--format", choices=FORMATS)
    hist.add_argument("--title")
    hist.add_argument("--speaker-name", type=speaker_pair, action="append", default=[])
    hist.add_argument("--notes-file")
    mods = sub.add_parser("models", help="ASR, translation, TTS and music setup")
    mods.add_argument("action", choices=("list", "download", "activate"))
    mods.add_argument("id", nargs="?")
    mods.add_argument("--kind", choices=("asr", "translation", "tts", "music"))
    mods.add_argument("--planner", action="store_true", help="include optional music planner")


def cmd_voices(a, out):
    from thundertalk.cli import _voice_row
    from thundertalk.core import audio_io, speech, voices
    lib = voices.VoiceLibrary()
    if a.action == "list":
        bs = speech.backends() if a.engine in ("auto", "best") else [speech.backend(a.engine)]
        return dict(voices=[_voice_row(b, v) for b in bs for v in b.voices()],
                    my_voices=[dict(asdict(v), id="my:" + v.id) for v in lib.list()])
    if not a.input:
        raise CliError("bad_args", f"voices {a.action} needs an input")
    ref_text = a.transcript
    if a.transcript_file:
        path = Path(a.transcript_file).expanduser()
        if not path.is_file():
            raise CliError("not_found", f"Transcript not found: {path}")
        ref_text = path.read_text(encoding="utf-8")
    if a.action == "add":
        if not a.name or not a.name.strip():
            raise CliError("bad_args", "voices add requires --name")
        path = Path(a.input).expanduser()
        if not path.is_file():
            raise CliError("not_found", f"Reference audio not found: {path}")
        out.stage("prepare_reference")
        check = voices.prepare_reference(audio_io.decode_audio(str(path), 24000), 24000)
        if not check.ok:
            raise CliError("bad_reference", "Need at least 3 seconds of audible reference audio")
        saved = lib.add(a.name, check.audio, ref_text or "")
        return dict(asdict(saved), id="my:" + saved.id, warnings=check.warnings)
    vid = a.input.removeprefix("my:")
    if not lib.get(vid):
        raise CliError("not_found", f"Saved voice not found: {a.input}")
    if a.action == "remove":
        lib.delete(vid)
    elif a.action == "rename":
        if not a.name or not a.name.strip():
            raise CliError("bad_args", "voices rename requires --name")
        lib.rename(vid, a.name)
    elif a.action == "edit":
        if ref_text is None:
            raise CliError("bad_args", "voices edit requires --transcript or --transcript-file")
        lib.update_text(vid, ref_text)
    return dict(id="my:" + vid, action=a.action)


def cmd_history(a, out):
    from thundertalk.core.transcript_history import TranscriptHistory
    if a.offset < 0 or not 1 <= a.limit <= 1000:
        raise CliError("bad_args", "Require offset >= 0 and 1 <= limit <= 1000")
    if a.action != "list" and not a.id:
        raise CliError("bad_args", f"history {a.action} requires an ID")
    hist = TranscriptHistory()
    if a.action == "list":
        return dict(entries=hist.list(a.query, a.offset, a.limit), offset=a.offset, limit=a.limit)
    try:
        tr, meta = hist.load(a.id)
    except ValueError as exc:
        raise CliError("bad_args", str(exc)) from exc
    if a.action == "show":
        return dict(transcript=asdict(tr), metadata=meta)
    if a.action == "delete":
        hist.delete(a.id)
    elif a.action == "rename":
        if not a.title or not a.title.strip():
            raise CliError("bad_args", "history rename requires --title")
        hist.rename(a.id, a.title)
    elif a.action == "edit":
        if not a.speaker_name and not a.notes_file:
            raise CliError("bad_args", "history edit requires --speaker-name or --notes-file")
        for speaker, name in a.speaker_name:
            tr.rename_speaker(speaker, name)
        if a.notes_file:
            path = Path(a.notes_file).expanduser()
            if not path.is_file():
                raise CliError("not_found", str(path))
            tr.notes = path.read_text(encoding="utf-8")
        hist.save(tr, meta["source"])
    elif a.action == "export":
        if not a.out:
            raise CliError("bad_args", "history export requires --out")
        dest = Path(a.out).expanduser().resolve()
        fmt = a.format or dest.suffix.lstrip(".").lower()
        if fmt not in FORMATS:
            raise CliError("bad_args", "Choose txt, md, srt, vtt or json")
        _write(dest, export_transcript(tr, fmt)[0])
        return dict(id=a.id, output=str(dest), format=fmt)
    return dict(id=a.id, action=a.action)


def catalog():
    from thundertalk.core import models, music, speech
    entries = []
    for m in models.BUILTIN_MODELS:
        entries.append(dict(id=m.id, name=m.name, kind="translation" if m.backend == "seamless-torch" else "asr",
                            ready=models.is_downloaded(m.id), compatible=models.is_variant_compatible(m),
                            size_mb=m.size_mb))
    for b in speech.backends():
        entries.append(dict(id=b.info.id, name=b.info.name, kind="tts", ready=b.is_ready(), compatible=True,
                            size_mb=b.info.size_mb))
    for m in music.MUSIC_MODELS:
        entries.append(dict(id=m.info.id, name=m.info.name, kind="music", ready=not music.missing(m, False),
                            compatible=music.supported(__import__("thundertalk.core.music_process", fromlist=["total_memory_mb"]).total_memory_mb() / 1024),
                            size_mb=m.info.size_mb))
    return entries


def cmd_models(a, out):
    from thundertalk.core import models, music, speech
    entries = catalog()
    if a.action == "list":
        return dict(models=[m for m in entries if not a.kind or m["kind"] == a.kind])
    row = next((m for m in entries if m["id"] == a.id and (not a.kind or m["kind"] == a.kind)), None)
    if row is None:
        raise CliError("bad_args", "Unknown model ID; use models list")
    kind = row["kind"]
    if not row["compatible"]:
        raise CliError("unsupported", "This model is not supported on this machine")
    if a.action == "download":
        out.stage("download")
        if kind in ("asr", "translation"):
            info = next(m for m in models.BUILTIN_MODELS if m.id == a.id)
            if not info.built_in:
                models.download_model(info, progress_cb=progress(out))
        else:
            m = music.model(a.id) if kind == "music" else None
            info = music.with_planner(m) if m and a.planner else m.info if m else speech.backend(a.id).info
            speech.download_backend(info, progress=progress(out))
        out.progress(1.0)
        return dict(id=a.id, kind=kind, action="download")
    if not row["ready"]:
        raise CliError("no_model", f"Download {a.id} first")
    settings = _settings()
    key = {"asr": "active_model_id", "translation": "translation_target", "tts": "studio_engine", "music": "compose_model"}[kind]
    if kind == "translation":
        # Translation has one fixed model, its language/mode are independent.
        return dict(id=a.id, kind=kind, action="activate", active=True)
    settings.set(key, a.id)
    if kind == "tts":
        first = next(iter(speech.backend(a.id).voices()), None)
        if first:
            settings.set("studio_voice", first.id)
    return dict(id=a.id, kind=kind, action="activate", restart_required=True)
