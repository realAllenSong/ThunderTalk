"""Headless notes, subtitle rendering and SeamlessM4T translation."""
from __future__ import annotations

from pathlib import Path

from thundertalk.cli import CliError, _settings, _text, _transcribe_path, _write, model_slot
from thundertalk.cli_media import load_transcript, media_options, source, source_metadata

_GUI = None


def add_parsers(sub):
    notes = sub.add_parser("notes", help="media / URL / txt / json / md → meeting notes")
    notes.add_argument("input")
    notes.add_argument("--out", required=True)
    notes.add_argument("--provider")
    notes.add_argument("--model")
    media_options(notes)
    burn = sub.add_parser("burn", help="video + subtitles (or ASR) → subtitled video")
    burn.add_argument("input")
    burn.add_argument("--subtitles", help="existing srt / vtt / json transcript; otherwise transcribe first")
    burn.add_argument("--out", required=True)
    burn.add_argument("--soft", action="store_true", help="selectable subtitle track")
    # Cropping only the transcript would desynchronise the original video.
    burn.add_argument("--engine", default="auto")
    burn.add_argument("--language", default="auto")
    burn.add_argument("--speakers", action="store_true")
    tr = sub.add_parser("translate", help="speech or text → translated text (SeamlessM4T)")
    tr.add_argument("input", nargs="?", help="media file or URL")
    tr.add_argument("--text")
    tr.add_argument("--text-file")
    tr.add_argument("--source-language", help="ISO-639-3, required for text")
    tr.add_argument("--target", required=True, help="ISO-639-3: eng, cmn, jpn, …")
    tr.add_argument("--out", required=True)
    media_options(tr)


def progress(out):
    def cb(pct, label):
        out.emit(type="progress", label=label, value=pct / 100 if pct >= 0 else None)
    return cb


def notes_provider(a):
    from thundertalk.core import llm_providers as lp
    settings = _settings()
    selected = a.provider or settings.get("cleanup_provider") or ""
    if not selected:
        raise CliError("no_provider", "Choose a ready provider on AI Proofread, or pass --provider and --model")
    providers = lp.detect(custom_base_url=settings.get("cleanup_base_url") or "",
                          api_key=settings.get("cleanup_api_key") or "",
                          cherry_api_key=settings.get("cleanup_cherry_key") or "",
                          verified=(settings.get("cleanup_checks") or {}).keys())
    provider = next((p for p in providers if p.id == selected and p.is_ready()), None)
    if provider is None:
        raise CliError("no_provider", f"Provider '{selected}' is not ready")
    saved = (settings.get("cleanup_models") or {}).get(selected)
    if selected == "claude" and saved in ("haiku", "sonnet", "opus"):
        saved = next((m for m in provider.models if m.startswith("claude-" + saved + "-")), saved)
    model = a.model or saved or lp.preferred_model(provider)
    if not model:
        raise CliError("no_provider", "No model is configured for the selected provider")
    return provider, model


def cmd_notes(a, out):
    from thundertalk.core import meeting_notes
    # Check configuration before doing an expensive transcription.
    provider, model = notes_provider(a)
    out.stage("read_transcript")
    if Path(a.input).suffix.lower() in (".txt", ".json", ".md", ".srt", ".vtt"):
        tr = load_transcript(a.input)
    else:
        with source(a.input, out, a) as (path, title, url, duration):
            tr, _ = _transcribe_path(a, out, path)
            source_metadata(tr, title, url, duration, a)
    out.stage("notes")
    try:
        text = meeting_notes.generate(tr, provider, model, progress=progress(out))
    except meeting_notes.NotesError as exc:
        raise CliError(str(exc), str(exc)) from exc
    dest = Path(a.out).expanduser().resolve()
    _write(dest, text.rstrip() + "\n")
    return dict(output=str(dest), format="md", title=tr.title, provider=provider.id, model=model)


def cmd_burn(a, out):
    from thundertalk.core import audio_io, burn
    if not audio_io.find_ffmpeg():
        raise CliError("ffmpeg_missing", "Subtitles into video needs ffmpeg (brew install ffmpeg)")
    src, dest = Path(a.input).expanduser().resolve(), Path(a.out).expanduser().resolve()
    if not src.is_file():
        raise CliError("not_found", f"Video not found: {src}")
    if src == dest:
        raise CliError("bad_args", "Output must differ from the source video")
    if dest.suffix.lower() not in (".mp4", ".mov", ".m4v"):
        raise CliError("bad_args", "--out must end in .mp4, .mov or .m4v")
    tr = load_transcript(a.subtitles) if a.subtitles else _transcribe_path(a, out, src)[0]
    if not a.soft:
        import os
        # QImage/CoreText needs fonts but no window, event loop or GUI app startup.
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtGui import QGuiApplication
        global _GUI
        _GUI = QGuiApplication.instance() or QGuiApplication(["thundertalk-cli-burn", "-platform", "offscreen"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Preserve an existing output if encoding fails or is interrupted.
    import tempfile
    with tempfile.TemporaryDirectory(prefix=".thundertalk-burn-", dir=dest.parent) as work:
        temp = Path(work) / dest.name
        result = burn.burn(str(src), tr, str(temp), soft=a.soft, progress=progress(out))
        temp.replace(dest)
    return dict(output=str(dest), soft=a.soft, seconds=round(result.seconds, 2), size=result.size, cues=result.cues)


def cmd_translate(a, out):
    from thundertalk.core import audio_io, models
    from thundertalk.core.model_metadata import SEAMLESS_LANGUAGES
    from thundertalk.core.translate_process import TranslationProcess
    if sum(x is not None for x in (a.input, a.text, a.text_file)) != 1:
        raise CliError("bad_args", "Supply one media input, --text or --text-file")
    if a.target not in SEAMLESS_LANGUAGES or a.source_language and a.source_language not in SEAMLESS_LANGUAGES:
        raise CliError("bad_args", "Use supported ISO-639-3 language codes (eng, cmn, …)")
    if not a.input and not a.source_language:
        raise CliError("bad_args", "Text translation requires --source-language")
    text = _text(a).strip() if not a.input else None
    if text == "":
        raise CliError("bad_args", "The text is empty")
    mid = "seamless-m4t-v2-large"
    if not models.is_downloaded(mid):
        raise CliError("no_model", f"Run: ThunderTalk --cli models download {mid}")
    with model_slot(out):
        engine = TranslationProcess()
        title = ""
        try:
            out.stage("load_model")
            engine.load_model(models.get_model_path(mid))
            out.stage("translate")
            if text is not None:
                result = engine.translate_text(text, a.source_language, a.target)
            else:
                with source(a.input, out, a) as (path, title, url, duration):
                    samples = audio_io.decode_audio(str(path), 16000)
                    from thundertalk.core.transcribe import Transcript
                    source_metadata(Transcript([], len(samples) / 16000, "translate"), title, url, duration, a)
                    # Bounded units avoid feeding hours of audio to a single model call.
                    from thundertalk.core.priority import DICTATION
                    parts = []
                    for start in range(0, len(samples), 30 * 16000):
                        DICTATION.wait_clear(on_wait=lambda: out.stage("yield"))
                        result = engine.translate(samples[start:start + 30 * 16000], a.target, 16000)
                        parts.append(result.text)
                        out.progress(min(1, (start + 30 * 16000) / len(samples)))
                    text = "\n".join(parts)
            translated = result.text if not a.input else text
        finally:
            engine.unload()
    if not translated.strip():
        raise CliError("no_speech", "No translatable speech was found")
    dest = Path(a.out).expanduser().resolve()
    _write(dest, translated.rstrip() + "\n")
    out.progress(1.0)
    return dict(output=str(dest), text=translated, title=title, model=mid, target=a.target)
