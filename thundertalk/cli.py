"""Headless command line for other apps: ``ThunderTalk --cli <command> …``.

    ThunderTalk --cli transcribe talk.mp4 --out talk.srt [--format srt] [--engine auto|apple|<model-id>]
                                          [--language auto|zh|en|…]
    ThunderTalk --cli speak --text "你好" --out hi.m4a [--engine auto|apple|kokoro|voxcpm2|indextts]
                            [--voice <id>] [--language auto|zh|en] [--speed 1.0]
    ThunderTalk --cli voices [--engine …]
    ThunderTalk --cli status

Never opens a window and takes no lock, so it runs next to the app. Reads
the app's settings and downloaded models; writes only ``--out``.

stdout carries JSON lines and nothing else (engine logs and native libraries
go to stderr):

    {"type": "stage", "label": "load_model"}
    {"type": "progress", "value": 0.42}
    {"type": "result", "ok": true, "output": "/abs/path.srt", …}
    {"type": "result", "ok": false, "error": "…", "code": "no_model"}

Exit status 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

FORMATS = ("txt", "md", "srt", "vtt", "json")
TTS_ENGINES = ("auto", "best", "apple", "kokoro", "voxcpm2", "indextts")
# `--engine best` for files, most accurate first. Dictation (`auto`) favours
# latency; a recording or a call favours accuracy. Measured in docs/plans.
ASR_QUALITY = (
    "qwen3-asr-17b-mlx", "qwen3-asr-06b-mlx", "qwen3-asr-06b-int8", "funasr-nano-int8",
    "sensevoice-small-int8", "parakeet-tdt-06b-v3-int8", "parakeet-tdt-06b-v2-int8",
)
DIARIZE_MODEL = "moss-transcribe-diarize-mlx"
# Read-back error 0–3.4 % for IndexTTS and VoxCPM2 on zh/en/mixed; Kokoro is
# as good on one language but 8–16 % on mixed text (2026-09-30-tts-engines.md).
TTS_QUALITY = ("indextts", "voxcpm2", "kokoro")
_LANG = {"zh": "chinese", "en": "english", "ja": "japanese", "ko": "korean"}


class CliError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Out:
    """JSON lines on the real stdout; everything else printed goes to stderr."""

    def __init__(self) -> None:
        sys.stdout.flush()
        self._fd = os.dup(1)
        os.dup2(2, 1)                       # native libraries writing to fd 1 land on stderr
        self._f = os.fdopen(self._fd, "w", encoding="utf-8", buffering=1)
        self._saved = sys.stdout
        sys.stdout = sys.stderr
        self._last = -1.0

    def emit(self, **event) -> None:
        self._f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._f.flush()

    def stage(self, label: str) -> None:
        self.emit(type="stage", label=label)

    def progress(self, value: float) -> None:
        value = round(min(max(value, 0.0), 1.0), 3)
        if value != self._last:
            self._last = value
            self.emit(type="progress", value=value)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):                      # noqa: D401 - argparse API
        raise CliError("bad_args", message)


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="ThunderTalk --cli", description="ThunderTalk without a window.")
    sub = ap.add_subparsers(dest="command", parser_class=_Parser)
    from thundertalk.cli_media import media_options, speaker_pair
    tr = sub.add_parser("transcribe", help="files / URLs → transcripts (one or a batch)")
    tr.add_argument("input", nargs="+")
    tr.add_argument("--out")
    tr.add_argument("--out-dir")
    tr.add_argument("--format", choices=FORMATS)
    tr.add_argument("--formats", choices=FORMATS, nargs="+")
    tr.add_argument("--timestamps", action="store_true")
    tr.add_argument("--speaker-name", type=speaker_pair, action="append", default=[])
    tr.add_argument("--save-history", action="store_true")
    media_options(tr)
    sp = sub.add_parser("speak", help="text → m4a / wav / mp3")
    src = sp.add_mutually_exclusive_group(required=True)
    src.add_argument("--text")
    src.add_argument("--text-file")
    sp.add_argument("--out", required=True)
    sp.add_argument("--engine", choices=TTS_ENGINES, default="auto")
    sp.add_argument("--voice", default="")
    sp.add_argument("--language", default="auto", choices=("auto", "zh", "en"))
    sp.add_argument("--speed", type=float, default=1.0)
    vo = sub.add_parser("voices", help="list voices")
    vo.add_argument("--engine", choices=TTS_ENGINES, default="auto", help="auto = every engine")
    sub.add_parser("status", help="engines, downloaded models and permissions as JSON")
    return ap


# ── helpers ─────────────────────────────────────────────────────────────

def _settings():
    from thundertalk.core.settings import Settings
    return Settings()                   # read only: never saved from here


def _asr_models() -> list:
    from thundertalk.core.models import BUILTIN_MODELS
    return [m for m in BUILTIN_MODELS if m.backend != "seamless-torch"]


def _downloaded_asr() -> list[str]:
    from thundertalk.core.models import is_downloaded, is_variant_compatible
    return [m.id for m in _asr_models() if is_variant_compatible(m) and is_downloaded(m.id)]


def resolve_asr(engine: str, settings) -> str:
    """Model id for ``--engine``: ``auto`` is the app's dictation model when it
    is downloaded, otherwise Apple's built-in recognizer."""
    from thundertalk.core.models import APPLE_SPEECH_ID, is_downloaded
    ids = {m.id for m in _asr_models()}
    if engine == "apple":
        engine = APPLE_SPEECH_ID
    if engine == "best":
        for model_id in ASR_QUALITY:
            if model_id in ids and is_downloaded(model_id):
                return model_id
        engine = "auto"
    if engine != "auto":
        if engine not in ids:
            raise CliError("bad_args", f"Unknown engine '{engine}'. Use auto, apple or one of: {', '.join(sorted(ids))}")
        if not is_downloaded(engine):
            if engine == APPLE_SPEECH_ID:
                raise CliError("locale_unsupported", "No dictation language is installed for on-device recognition "
                               "(System Settings ▸ Keyboard ▸ Dictation ▸ Languages).")
            raise CliError("no_model", f"Model '{engine}' is not downloaded. Download it on ThunderTalk's Models page.")
        return engine
    active = settings.get("active_model_id") or ""
    if active in ids and is_downloaded(active):
        return active
    if is_downloaded(APPLE_SPEECH_ID):
        return APPLE_SPEECH_ID
    raise CliError("no_model", "No speech model is downloaded and Apple recognition is unavailable.")


def _apple_error(exc) -> CliError:
    hint = {"permission_denied": " Allow ThunderTalk in System Settings ▸ Privacy & Security ▸ Speech Recognition."}
    return CliError(exc.code, str(exc) + hint.get(exc.code, ""))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _one_line(text: str) -> str:
    a, _, b = text.partition("\n")
    if not b:
        return a
    latin = a[-1:].isascii() and a[-1:].isalnum() and b[:1].isascii() and b[:1].isalnum()
    return a + (" " if latin or a[-1:] in ",.;:!?" else "") + b


def export_transcript(tr, fmt: str) -> tuple[str, int]:
    """Text in ``fmt`` and the number of timed entries in it."""
    from thundertalk.core import transcribe as T
    if fmt == "txt":
        return tr.to_text() + "\n", len(tr.segments)
    if fmt == "md":
        # For language models: timestamps, and speakers when labelled.
        return tr.to_markdown(), len(tr.segments)
    cues = T.timed_cues(tr)
    if fmt == "srt":
        return T.cues_to_srt(cues), len(cues)
    if fmt == "vtt":
        return T.cues_to_vtt(cues), len(cues)
    words = [w for s in tr.segments for w in s.words]
    data = {
        "duration": round(tr.duration, 3), "engine": tr.engine, "model": tr.model_id,
        "text": tr.to_text(),
        "segments": [{"start": round(c.start, 3), "end": round(c.end, 3), "text": _one_line(c.text),
                      **({"speaker": c.speaker} if c.speaker else {})} for c in cues],
    }
    if words:
        data["words"] = [{"start": a, "end": b, "text": w} for a, b, w in words]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n", len(cues)


# ── commands ────────────────────────────────────────────────────────────

def _transcribe_path(a, out: _Out, src: Path):
    from thundertalk.core import apple_speech, audio_io, transcribe
    from thundertalk.core.asr import AsrEngine
    from thundertalk.core.models import BUILTIN_MODELS, get_model_path
    settings = _settings()
    speakers = bool(getattr(a, "speakers", False))
    if speakers:
        from thundertalk.core.models import is_downloaded
        if not is_downloaded(DIARIZE_MODEL):
            raise CliError("no_model", "Speaker labels need MOSS-Transcribe-Diarize; download it on the Models page.")
        model_id = DIARIZE_MODEL
    else:
        model_id = resolve_asr(a.engine, settings)
    info = next(m for m in BUILTIN_MODELS if m.id == model_id)
    out.stage("load_model")
    out.emit(type="stage", label="engine", engine=model_id)
    t0 = time.monotonic()
    engine = None
    try:
        if info.backend != "mlx-moss":
            engine = AsrEngine()
            engine.set_language(a.language)
            if settings.hotwords:
                engine.set_hotwords(settings.hotwords)
            engine.load_model(get_model_path(model_id), info.family, info.backend,
                              memory_mode=settings.memory_mode)
        out.stage("transcribe")

        def progress(pct: int, msg: str) -> None:
            if msg in ("decode", "load_moss", "diarize"):
                out.stage(msg)
            if pct >= 0:
                out.progress(pct / 100 * 0.98)

        tr = transcribe.transcribe_file(str(src), engine, speakers=speakers, progress=progress,
                                        model_id="" if engine is not None else model_id)
        tr.model_id = model_id
    except apple_speech.AppleSpeechError as exc:
        raise _apple_error(exc) from exc
    except audio_io.AudioDecodeError as exc:
        raise CliError("decode", str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) in ("no_speech", "no_model", "memory", "model_busy"):
            raise CliError(str(exc), {"no_speech": "No speech was found in this file."}.get(str(exc), str(exc))) from exc
        raise
    finally:
        if engine is not None:
            engine.unload()
    return tr, {"engine": model_id, "duration": round(tr.duration, 2),
                "seconds": round(time.monotonic() - t0, 2), "entries": len(tr.segments)}


def cmd_transcribe(a, out):
    from thundertalk.cli_media import cmd_transcribe as run
    return run(a, out)


def _text(a) -> str:
    if a.text is not None:
        return a.text
    p = Path(a.text_file).expanduser()
    if not p.is_file():
        raise CliError("not_found", f"Text file not found: {p}")
    return p.read_text(encoding="utf-8")


def _mixed(text: str) -> bool:
    """Chinese with English words in it (or the reverse)."""
    import re
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]{2,}", text))
    return han >= 4 and latin >= 2


def _best_voice(language: str, mixed: bool, settings) -> str | None:
    """The best ready engine's voice for ``language``; the Speak page's voice
    first when it belongs to that engine and speaks the language."""
    from thundertalk.core import speech
    saved = settings.get("studio_voice") or ""
    for bid in TTS_QUALITY:
        if bid == "kokoro" and mixed:
            continue
        b = speech.backend(bid)
        if not b.is_ready():
            continue
        voices = [v for v in b.voices() if v.language in (language, "multi")]
        if not voices:
            continue
        chosen = next((v for v in voices if v.id == saved), voices[0])
        return chosen.id
    return None


def resolve_voice(engine: str, voice: str, language: str, settings, mixed: bool = False) -> str:
    """A voice id for ``speak``. ``auto`` uses the Speak page's voice when its
    engine is downloaded and it speaks the text's language, else the best
    system voice for that language."""
    from thundertalk.core import speech
    from thundertalk.core.tts_backends import apple
    if voice:
        try:
            bid = speech.backend_id_for(voice)
        except ValueError as exc:
            raise CliError("bad_args", str(exc)) from exc
        if engine not in ("auto", bid):
            raise CliError("bad_args", f"Voice {voice} belongs to {bid}, not {engine}")
        if voice not in {v.id for v in speech.backend(bid).voices()}:
            raise CliError("bad_args", f"Unknown voice: {voice} (see: ThunderTalk --cli voices --engine {bid})")
        if not speech.backend(bid).is_ready():
            raise CliError("tts_missing", f"{bid} is not downloaded. Download it on ThunderTalk's Speak page.")
        return voice
    if engine == "best":
        best = _best_voice(language, mixed, settings)
        if best is not None:
            return best
        engine = "apple"
    if engine == "auto":
        saved = settings.get("studio_voice") or ""
        try:
            b = speech.backend(speech.backend_id_for(saved)) if saved and not saved.startswith("my:") else None
        except ValueError:
            b = None
        v = next((v for v in b.voices() if v.id == saved), None) if b is not None else None
        if v is not None and b.is_ready() and v.language in (language, "multi"):
            return saved
        engine = "apple"
    b = speech.backend(engine)
    if not b.is_ready():
        code = "unavailable" if engine == "apple" else "tts_missing"
        raise CliError(code, f"{engine} is not available." + (
            "" if engine == "apple" else " Download it on ThunderTalk's Speak page."))
    if engine == "apple":
        vid = apple.best_voice(language)
    else:
        vid = next((v.id for v in b.voices() if v.language in (language, "multi")), None)
    if vid is None:
        raise CliError("bad_args", f"{engine} has no {language} voice")
    return vid


def cmd_speak(a, out: _Out) -> dict:
    from thundertalk.core import audio_io, speech, tts
    text = _text(a).strip()
    if not text:
        raise CliError("bad_args", "The text is empty.")
    if not 0.5 <= a.speed <= 2.0:
        raise CliError("bad_args", "--speed must be between 0.5 and 2.0")
    dest = Path(a.out).expanduser().resolve()
    if dest.suffix.lower() not in (".m4a", ".wav", ".mp3"):
        raise CliError("bad_args", "--out must end in .m4a, .wav or .mp3")
    if dest.suffix.lower() == ".mp3" and not audio_io.find_ffmpeg():
        raise CliError("ffmpeg_missing", "MP3 needs ffmpeg (brew install ffmpeg); use .m4a or .wav instead.")
    lang = _LANG.get(a.language) or tts.detect_language(text)
    voice = resolve_voice(a.engine, a.voice, lang, _settings(), mixed=_mixed(text))
    out.emit(type="stage", label="engine", engine=speech.backend_id_for(voice), voice=voice)
    out.stage("load_model")
    eng = speech.get_engine()
    t0 = time.monotonic()

    def progress(i: int, n: int, _seg: str) -> None:
        if i == 0:
            out.stage("speak")
        out.progress(0.95 * i / max(1, n))

    try:
        r = eng.synthesize(text, voice, language=lang, speed=a.speed, progress=progress)
    except tts.TtsModelMissing as exc:
        raise CliError("tts_missing", str(exc)) from exc
    finally:
        eng.unload()
    out.stage("write")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        audio_io.export_audio(str(dest), r.audio, r.sample_rate)
    except audio_io.AudioDecodeError as exc:
        raise CliError("ffmpeg_missing" if str(exc) == "ffmpeg_missing" else "failed", str(exc)) from exc
    out.progress(1.0)
    return {"output": str(dest), "engine": speech.backend_id_for(voice), "voice": voice, "language": lang,
            "duration": round(r.duration, 2), "seconds": round(time.monotonic() - t0, 2),
            "sample_rate": r.sample_rate, "warnings": r.warnings}


def _voice_row(b, v) -> dict:
    row = {"id": v.id, "engine": b.info.id, "name": v.name, "language": v.language, "gender": v.gender,
           "ready": b.is_ready(), "description": v.blurb_en}
    if v.quality:
        row["quality"] = v.quality
    return row


def cmd_voices(a, out: _Out) -> dict:
    from thundertalk.core import speech
    bs = speech.backends() if a.engine in ("auto", "best") else [speech.backend(a.engine)]
    return {"voices": [_voice_row(b, v) for b in bs for v in b.voices()]}


def cmd_status(_a, out: _Out) -> dict:
    import thundertalk
    from thundertalk.core import apple_speech, audio_io, speech
    from thundertalk.core.models import is_variant_compatible
    settings = _settings()
    apple = {"available": apple_speech.available()}
    if apple["available"]:
        apple.update(on_device_locales=apple_speech.on_device_locales(),
                     supported_locales=len(apple_speech.supported_locales()))
    downloaded = _downloaded_asr()
    try:
        from thundertalk.core.platform_utils import check_microphone
        mic = check_microphone()
    except Exception:                                          # noqa: BLE001
        mic = "unknown"
    return {
        "version": thundertalk.__version__,
        "asr": {"downloaded": downloaded, "dictation_model": settings.get("active_model_id") or "",
                "auto": resolve_asr("auto", settings) if downloaded else "",
                "models": [{"id": m.id, "name": f"{m.name} {m.variant}", "downloaded": m.id in downloaded}
                           for m in _asr_models() if is_variant_compatible(m)]},
        "tts": [{"engine": b.info.id, "name": b.info.name, "ready": b.is_ready(), "voices": len(b.voices()),
                 "clone": b.info.supports_clone} for b in speech.backends()],
        "apple_speech": apple,
        "permissions": {"speech_recognition": apple_speech.authorization_status(), "microphone": mic},
        "ffmpeg": audio_io.find_ffmpeg() or "",
    }


COMMANDS = {"transcribe": cmd_transcribe, "speak": cmd_speak, "voices": cmd_voices, "status": cmd_status}


def error_result(exc):
    from thundertalk.core import audio_io, links, transcribe
    if isinstance(exc, (links.LinkCancelled, transcribe.TranscribeCancelled)):
        code = "cancelled"
    elif isinstance(exc, audio_io.AudioDecodeError):
        code = "decode"
    elif hasattr(exc, "code"):
        code = exc.code
    elif isinstance(exc, FileNotFoundError):
        code = "not_found"
    elif isinstance(exc, PermissionError):
        code = "permission_denied"
    elif isinstance(exc, RuntimeError) and str(exc) in ("no_speech", "no_model", "memory", "model_busy"):
        code = str(exc)
    elif isinstance(exc, OSError) and exc.errno == 28:
        code = "disk_full"
    else:
        code = "failed"
    return dict(ok=False, code=code, error=str(exc) or code)


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["-h"], ["--help"], []):
        _parser().print_help()
        return 0 if argv else 1
    out = _Out()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")          # use what is downloaded; never fetch
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from thundertalk.core import apple_speech
    apple_speech.PROMPT = False
    try:
        a = _parser().parse_args(argv)
        if a.command not in COMMANDS:
            raise CliError("bad_args", "Missing command: transcribe, speak, voices or status")
        result = COMMANDS[a.command](a, out)
        result.setdefault("ok", True)
        out.emit(type="result", **result)
        return 0 if result["ok"] else 1
    except KeyboardInterrupt:
        out.emit(type="result", ok=False, error="Interrupted", code="cancelled")
    except Exception as exc:                                   # noqa: BLE001
        result = error_result(exc)
        if result["code"] == "failed":
            import traceback
            traceback.print_exc()
        out.emit(type="result", **result)
    return 1
