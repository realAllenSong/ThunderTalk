"""Headless command line for other apps: ``ThunderTalk --cli <command> …``.

CLI v3: transcribe, notes, burn, translate, compose, speak, voices, history,
models and status. See docs/cli-v3.md for the complete command reference.
Never opens a window or takes the GUI instance lock. Reads app settings and
models; inference writes explicit output paths. Management commands and
--save-history also persist their requested changes. Shared priority leases
let a v3 app's dictation preempt CLI music jobs.

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
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

FORMATS = ("txt", "md", "srt", "vtt", "json")
# Cloud engines (openai, gemini, elevenlabs, custom) are used only when named.
TTS_ENGINES = ("auto", "best", "apple", "kokoro", "voxcpm2", "indextts", "longcat", "openai", "gemini", "elevenlabs", "custom")
CLONE_ENGINES = ("voxcpm2", "indextts", "longcat")
CLOUD_TTS = ("openai", "gemini", "elevenlabs", "custom")
# `--engine best` for files, most accurate first. Dictation (`auto`) favours
# latency; a recording or a call favours accuracy. Measured in docs/plans.
ASR_QUALITY = (
    "qwen3-asr-06b-tuned-mlx", "qwen3-asr-17b-mlx", "qwen3-asr-06b-mlx", "qwen3-asr-06b-int8", "funasr-nano-int8",
    "sensevoice-small-int8", "parakeet-tdt-06b-v3-int8", "parakeet-tdt-06b-v2-int8",
)
DIARIZE_MODEL = "moss-transcribe-diarize-mlx"
# Read-back error 0–3.4 % for IndexTTS and VoxCPM2 on zh/en/mixed; Kokoro is
# as good on one language but 8–16 % on mixed text (2026-09-30-tts-engines.md).
# Among the two that measure alike the user's ear decides: VoxCPM2 sounds best
# to them (2026-11-21). LongCat read back as cleanly in a first comparison but
# has far fewer hours behind it.
TTS_QUALITY = ("voxcpm2", "indextts", "longcat", "kokoro")
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
    tr.add_argument("--notes", action="store_true", help="generate meeting notes for each transcript")
    tr.add_argument("--provider")
    tr.add_argument("--model")
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
    sp.add_argument("--scene", default="auto",
                    help="for my:<id> voices: explain | chat | story | mixed | opener, the recording of that "
                         "way of speaking; auto = the mixed one for Chinese-English text, else the main one")
    sp.add_argument("--polish", action="store_true",
                    help="voice-over quality: 4 takes per sentence, each read back, the one in step with its "
                         "neighbours kept, pauses set by meaning (about 4x slower)")
    sp.add_argument("--no-check", action="store_true",
                    help="skip reading each piece back with a local recognizer (faster, unchecked)")
    vo = sub.add_parser("voices", help="list voices")
    vo.add_argument("--engine", choices=TTS_ENGINES, default="auto", help="auto = every engine")
    from thundertalk import cli_library, cli_studio
    cli_library.add_parsers(sub, vo)
    cli_studio.add_parsers(sub)
    from thundertalk.cli_music import add_parser
    add_parser(sub)
    sub.add_parser("status", help="engines, downloaded models and permissions as JSON")
    sub.add_parser("jobs", help="the running app's job queue (agents and scripts share it)")
    from thundertalk.cli_eval import add_parser as add_eval
    add_eval(sub)
    from thundertalk.cli_batch import add_parser as add_batch
    add_batch(sub)
    from thundertalk.cli_cloud import add_parser as add_cloud
    add_cloud(sub)
    se = sub.add_parser("serve", help="local speech API: streaming recognition (WebSocket) + speech (HTTP)")
    se.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default); there is no authentication")
    se.add_argument("--port", type=int, default=8765)
    se.add_argument("--asr", default="", help="recognition model id; default: the most accurate downloaded")
    se.add_argument("--tts", default="kokoro", choices=("kokoro", "voxcpm2", "indextts", "longcat"),
                    help="default speech engine (warmed up at start)")
    se.add_argument("--no-warm-up", action="store_true", help="load models on first use")
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
    is downloaded, otherwise Apple's built-in recognizer. A cloud model
    (``openai:gpt-transcribe``) is used only when named: ``auto``/``best``
    never send audio off this Mac, even when dictation uses a cloud model."""
    from thundertalk.core import cloud
    from thundertalk.core.models import APPLE_SPEECH_ID, is_downloaded
    pid, model = cloud.split_id(engine)
    if pid:
        if not cloud.PROVIDERS[pid].asr_models:
            raise CliError("cloud_unsupported", f"{cloud.PROVIDERS[pid].name} has no speech recognition")
        if not cloud.is_configured(pid):
            raise CliError("cloud_no_key", f"No API key for {cloud.PROVIDERS[pid].name}. "
                           f"Run: thundertalk cloud set-key {pid} (reads the key from stdin)")
        return f"{pid}:{model}"
    if engine in cloud.PROVIDERS:
        raise CliError("bad_args", f"Name the model too, e.g. --engine {engine}:{cloud.config(engine, settings)['asr_model']}")
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
            raise CliError("bad_args", f"Unknown engine '{engine}'. Use auto, best, apple, <provider>:<model> "
                           f"(cloud) or one of: {', '.join(sorted(ids))}")
        if not is_downloaded(engine):
            if engine == APPLE_SPEECH_ID:
                raise CliError("locale_unsupported", "No dictation language is installed for on-device recognition "
                               "(System Settings ▸ Keyboard ▸ Dictation ▸ Languages).")
            raise CliError("no_model", f"Model '{engine}' is not downloaded. Download it on ThunderTalk's Models page.")
        return engine
    active = settings.get("active_model_id") or ""
    if active in ids and is_downloaded(active):   # never a cloud id: those are not in ids
        return active
    if cloud.is_cloud_id(active):
        # Dictation goes to the cloud, but auto stays on this Mac: the best local model.
        for model_id in ASR_QUALITY:
            if model_id in ids and is_downloaded(model_id):
                return model_id
    if is_downloaded(APPLE_SPEECH_ID):
        return APPLE_SPEECH_ID
    raise CliError("no_model", "No speech model is downloaded and Apple recognition is unavailable.")


def _apple_error(exc) -> CliError:
    hint = {"permission_denied": " Allow ThunderTalk in System Settings ▸ Privacy & Security ▸ Speech Recognition."}
    return CliError(exc.code, str(exc) + hint.get(exc.code, ""))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f".{path.name}.",
                                         suffix=".part", dir=path.parent, delete=False) as f:
            tmp = Path(f.name)
            f.write(text)
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


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
        "text": tr.to_text(), "title": tr.title, "source_url": tr.source_url,
        "speaker_names": tr.speaker_names, "notes": tr.notes,
        "segments": [{"start": round(c.start, 3), "end": round(c.end, 3), "text": _one_line(c.text),
                      **({"speaker": c.speaker} if c.speaker else {})} for c in cues],
    }
    if words:
        data["words"] = [{"start": a, "end": b, "text": w} for a, b, w in words]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n", len(cues)


# ── commands ────────────────────────────────────────────────────────────

# The job being run on this thread: set by the app's service (and by `batch`).
#   cancel     threading.Event, set when the caller has gone away
#   asr        callable(model_id, language) -> an already loaded engine to use, or None
JOB = __import__("threading").local()
# Seconds a heavy job waits for memory or for another heavy job before giving up.
WAIT_S = float(os.environ.get("THUNDERTALK_WAIT", "900"))
COOLDOWN_S = 1.5        # after a dictation ends, before a job starts or resumes


def _cancel():
    return getattr(JOB, "cancel", None)


@contextmanager
def model_slot(out, *, heavy=True, info=None):
    """Share alternate-model admission with Studio, yield to dictation, and wait
    (up to ``THUNDERTALK_WAIT`` seconds) for memory and for another heavy job to
    finish, instead of failing at once: an agent's batch should queue, not break."""
    from contextlib import ExitStack
    from thundertalk.core.priority import DICTATION, alternate_job
    cancel = _cancel()
    DICTATION.wait_clear(cancel, on_wait=lambda: out.stage("yield"), cooldown=COOLDOWN_S)
    if not heavy:
        yield
        return
    from thundertalk.core.transcribe import _has_model_headroom
    deadline, told = time.monotonic() + WAIT_S, set()
    while True:
        stack = ExitStack()
        try:
            stack.enter_context(alternate_job(cancel))
        except RuntimeError as exc:
            if str(exc) == "cancelled":
                raise CliError("cancelled", "Cancelled") from exc
            if str(exc) != "model_busy":
                raise
            reason = "model_busy"
        else:
            if info is None or _has_model_headroom(info):
                break
            stack.close()
            reason = "memory"
        if time.monotonic() >= deadline:
            raise CliError(reason, {"memory": "Not enough free memory to load this model safely",
                                    "model_busy": "Another ThunderTalk job is still using a model"}[reason])
        if cancel is not None and cancel.is_set():
            raise CliError("cancelled", "Cancelled")
        if reason not in told:
            told.add(reason)
            out.emit(type="stage", label="waiting", reason=reason)
        time.sleep(2.0)
    with stack:
        yield


def _transcribe_path(a, out: _Out, src: Path):
    from thundertalk.core import apple_speech, audio_io, transcribe
    from thundertalk.core.asr import AsrEngine
    from thundertalk.core.models import find, get_model_path
    settings = _settings()
    speakers = bool(getattr(a, "speakers", False))
    if speakers:
        from thundertalk.core.models import is_downloaded
        if not is_downloaded(DIARIZE_MODEL):
            raise CliError("no_model", "Speaker labels need MOSS-Transcribe-Diarize; download it on the Models page.")
        model_id = DIARIZE_MODEL
    else:
        model_id = resolve_asr(a.engine, settings)
    info = find(model_id)
    from thundertalk.core.priority import DICTATION
    DICTATION.wait_clear(_cancel(), on_wait=lambda: out.stage("yield"), cooldown=COOLDOWN_S)
    out.stage("load_model")
    out.emit(type="stage", label="engine", engine=model_id)
    t0 = time.monotonic()
    engine = None
    # An engine already loaded with this model (the app's dictation engine, or one a batch
    # keeps): no second copy in memory, no load. It is not ours to unload.
    source = getattr(JOB, "asr", None)
    borrowed = source(model_id, a.language) if source is not None and info.backend != "mlx-moss" else None
    with model_slot(out, heavy=borrowed is None and not info.built_in and info.backend != "cloud", info=info):
        try:
            if borrowed is not None:
                engine = borrowed
                out.emit(type="stage", label="engine_shared", engine=model_id)
            elif info.backend != "mlx-moss":
                engine = AsrEngine()
                engine.set_language(a.language)
                if settings.hotwords:
                    engine.set_hotwords(settings.hotwords)
                engine.set_builtin_terms(settings.get("builtin_terms") is not False)
                engine.set_auto_languages(settings.get("auto_languages") or ["zh", "en"])
                engine.load_model(get_model_path(model_id), info.family, info.backend,
                                  memory_mode=settings.memory_mode)
            out.stage("transcribe")

            def progress(pct: int, msg: str) -> None:
                if msg in ("decode", "load_moss", "diarize"):
                    out.stage(msg)
                if pct >= 0:
                    out.progress(pct / 100 * 0.98)

            tr = transcribe.transcribe_file(str(src), engine, speakers=speakers, progress=progress,
                                            cancel=_cancel(), model_id="" if engine is not None else model_id)
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
            if engine is not None and engine is not borrowed:
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
    if engine in CLOUD_TTS or (voice and voice.split(":", 1)[0] in CLOUD_TTS):
        return _cloud_voice(engine, voice, settings)
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
            # The Speak page's voice, unless it is a cloud voice: auto never sends text off this Mac.
            local = saved and not saved.startswith("my:") and saved.split(":", 1)[0] not in CLOUD_TTS
            b = speech.backend(speech.backend_id_for(saved)) if local else None
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


def _cloud_voice(engine: str, voice: str, settings) -> str:
    """``--engine openai [--voice openai:marin]``: any voice name the provider accepts."""
    from thundertalk.core import cloud
    pid = voice.split(":", 1)[0] if voice else engine
    if engine not in ("auto", pid):
        raise CliError("bad_args", f"Voice {voice} belongs to {pid}, not {engine}")
    if not cloud.is_configured(pid, settings):
        raise CliError("cloud_no_key", f"No API key for {cloud.PROVIDERS[pid].name}. "
                       f"Run: thundertalk cloud set-key {pid} (reads the key from stdin)")
    if voice:
        if not cloud.split_id(voice)[0]:
            raise CliError("bad_args", f"Use --voice {pid}:<voice name>")
        return voice
    p = cloud.PROVIDERS[pid]
    name = cloud.config(pid, settings)["voice"] or (p.voices[0] if p.voices else "")
    if not name:
        rows = cloud.voices(pid, settings=settings)
        if not rows:
            raise CliError("bad_args", f"Choose a voice: thundertalk voices --engine {pid}")
        return rows[0]["id"]
    return f"{pid}:{name}"


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
    clone_backend = None
    voice_id = a.voice
    if a.voice.startswith("my:"):
        from thundertalk.core import voices
        from thundertalk.core.voices import VoiceLibrary
        lib = VoiceLibrary()
        saved = lib.get(a.voice[3:])
        if saved is None:
            raise CliError("not_found", f"Saved voice not found: {a.voice}")
        if a.engine == "best":
            clone_backend = next((bid for bid in TTS_QUALITY if bid in CLONE_ENGINES and speech.backend(bid).is_ready()), speech.DEFAULT_CLONE_BACKEND)
        elif a.engine == "auto":
            preferred = _settings().get("studio_engine")
            clone_backend = preferred if preferred in CLONE_ENGINES else speech.DEFAULT_CLONE_BACKEND
        else:
            clone_backend = a.engine
        if clone_backend not in CLONE_ENGINES:
            raise CliError("bad_args", "Cloned voices need voxcpm2, indextts or longcat")
        if not speech.backend(clone_backend).is_ready():
            raise CliError("tts_missing", f"Download {clone_backend} first")
        if not saved.ref_text.strip():
            raise CliError("transcript_required", "Add the reference transcript with voices edit --transcript")
        if a.scene not in ("auto", *voices.SCENES):
            raise CliError("bad_args", f"--scene must be auto or one of: {', '.join(voices.SCENES)}")
        voice = lib.prompt(saved.id, a.scene, text)
        bid = clone_backend
    else:
        voice = resolve_voice(a.engine, a.voice, lang, _settings(), mixed=_mixed(text))
        voice_id, bid = voice, speech.backend_id_for(voice)
    out.emit(type="stage", label="engine", engine=bid, voice=voice_id)
    out.stage("load_model")
    eng = speech.get_engine()
    t0 = time.monotonic()

    def progress(i: int, n: int, _seg: str) -> None:
        from thundertalk.core.priority import DICTATION
        DICTATION.wait_clear(on_wait=lambda: out.stage("yield"))
        if i == 0:
            out.stage("speak")
        out.progress(0.95 * i / max(1, n))

    polish = bool(getattr(a, "polish", False))
    checks = not getattr(a, "no_check", False) and bid not in ("apple", *CLOUD_TTS)
    keep = getattr(JOB, "keep_models", False)        # in the app: the Speak page shares this engine
    with model_slot(out, heavy=not keep and bid not in ("apple", "kokoro", *CLOUD_TTS)):
        asr, own_asr = _readback_engine(out) if checks else (None, False)
        try:
            from thundertalk.core import tts_verify
            params = tts.TtsParams(takes=speech.POLISH_TAKES) if polish else None
            r = eng.synthesize(text, voice, language=lang, speed=a.speed, progress=progress, clone_backend=clone_backend,
                               params=params, verifier=tts_verify.make_verifier(asr), cancel=_cancel())
        except tts.TtsModelMissing as exc:
            raise CliError("tts_missing", str(exc)) from exc
        except tts.TtsCancelled as exc:
            raise CliError("cancelled", "Cancelled") from exc
        finally:
            if not keep:
                eng.unload()
            if asr is not None and own_asr:
                asr.unload()
    out.stage("write")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        audio_io.export_audio(str(dest), r.audio, r.sample_rate)
    except audio_io.AudioDecodeError as exc:
        raise CliError("ffmpeg_missing" if str(exc) == "ffmpeg_missing" else "failed", str(exc)) from exc
    out.progress(1.0)
    return {"output": str(dest), "engine": bid, "voice": voice_id, "language": lang,
            "duration": round(r.duration, 2), "seconds": round(time.monotonic() - t0, 2),
            "sample_rate": r.sample_rate, "warnings": r.warnings, "polish": polish,
            "read_back": asr is not None}


def _readback_engine(out):
    """(recognizer, ours) to read each generated piece back: in the app its own
    dictation engine (``ours`` False: not to be unloaded), else the most accurate
    downloaded local model, without hotwords or number formatting. (None, False)
    when there is none (the speech is then made unchecked, as before)."""
    shared = getattr(JOB, "readback", None)
    if shared is not None:
        engine = shared()
        if engine is not None:
            return engine, False
    from thundertalk.core.asr import AsrEngine
    from thundertalk.core.models import find, get_model_path, is_downloaded
    for mid in ASR_QUALITY:
        info = find(mid)
        if info is None or not is_downloaded(mid) or info.backend in ("cloud", "mlx-moss"):
            continue
        out.stage("load_checker")
        try:
            engine = AsrEngine()
            engine.set_builtin_terms(False)
            engine.load_model(get_model_path(mid), info.family, info.backend)
            engine._itn_enabled = False
            return engine, True
        except Exception:
            return None, False
    return None, False


def _voice_row(b, v) -> dict:
    row = {"id": v.id, "engine": b.info.id, "name": v.name, "language": v.language, "gender": v.gender,
           "ready": b.is_ready(), "description": v.blurb_en}
    if v.quality:
        row["quality"] = v.quality
    return row


def cmd_voices(a, out: _Out) -> dict:
    from thundertalk.cli_library import cmd_voices as run
    return run(a, out)


def cmd_serve(a, out: _Out) -> dict:
    """Run the speech API server (thundertalk/api/server.py) until interrupted."""
    try:
        from thundertalk.api import server
    except SystemExit as exc:                                  # FastAPI / uvicorn / websockets missing
        raise CliError("missing_dependency", str(exc)) from None
    out.emit(type="stage", label="serve", url=f"http://{a.host}:{a.port}")
    try:
        server.serve(a.host, a.port, asr=a.asr or None, tts=a.tts or None, warm=not a.no_warm_up)
    except SystemExit as exc:
        raise CliError("bad_args", str(exc)) from None
    return {"stopped": True}


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
    from thundertalk import service
    return {
        "version": thundertalk.__version__, "cli_version": 3,
        "capabilities": ["transcribe", "batch", "notes", "burn", "translate", "speak", "voices", "compose", "history", "models",
                         "cloud", "jobs", "service", "serve"],
        "service": service.available() is not None,
        "asr": {"downloaded": downloaded, "dictation_model": settings.get("active_model_id") or "",
                "auto": resolve_asr("auto", settings) if downloaded else "",
                "models": [{"id": m.id, "name": f"{m.name} {m.variant}", "downloaded": m.id in downloaded}
                           for m in _asr_models() if is_variant_compatible(m)]},
        "tts": [{"engine": b.info.id, "name": b.info.name, "ready": b.is_ready(), "voices": len(b.voices()),
                 "clone": b.info.supports_clone} for b in speech.backends()],
        "apple_speech": apple,
        "permissions": {"speech_recognition": apple_speech.authorization_status(), "microphone": mic},
        "ffmpeg": audio_io.find_ffmpeg() or "",
        "cloud": [{"provider": r["provider"], "ready": r["ready"], "key": r["key"]} for r in _cloud_summary(settings)],
    }


def _cloud_summary(settings) -> list[dict]:
    from thundertalk.core import cloud
    return cloud.summary(settings)


def cmd_jobs(_a, out) -> dict:
    """Is the app taking jobs, and what is queued. Without the app, jobs run in their own process."""
    from thundertalk import service
    info = service.available()
    if info is None:
        return {"service": False, "jobs": []}
    import json as _json
    import socket as _socket
    with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(str(service.socket_path()))
        s.sendall(b'{"op": "jobs"}\n')
        jobs = _json.loads(s.makefile("rb").readline() or b"{}").get("jobs", [])
    return {"service": True, "version": info.get("version"), "jobs": jobs}


COMMANDS = {"transcribe": cmd_transcribe, "speak": cmd_speak, "voices": cmd_voices, "status": cmd_status,
            "jobs": cmd_jobs, "serve": cmd_serve}


def error_result(exc):
    from thundertalk.core import audio_io, burn, links, models, transcribe, tts
    from thundertalk.core.llm_providers import CompletionCancelled
    if isinstance(exc, burn.BurnError):
        code = "ffmpeg_missing" if exc.code == "no_ffmpeg" else "burn_" + exc.code
    elif isinstance(exc, (burn.BurnCancelled, models.DownloadCancelled, tts.TtsCancelled, CompletionCancelled)):
        code = "cancelled"
    elif isinstance(exc, (links.LinkCancelled, transcribe.TranscribeCancelled)):
        code = "cancelled"
    elif isinstance(exc, audio_io.AudioDecodeError):
        code = "decode"
    elif hasattr(exc, "code"):
        code = exc.code
        if code.startswith(("music:memory:", "music:memory_low:")):
            return dict(ok=False, code=code.rsplit(":", 1)[0], error=str(exc), free_percent=code.rsplit(":", 1)[1])
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


@contextmanager
def network_mode(download=False):
    """Only the explicit setup command may fetch weights, even after HF was imported."""
    keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    saved = {key: os.environ.get(key) for key in keys}
    hf = sys.modules.get("huggingface_hub.constants")
    old_hf = getattr(hf, "HF_HUB_OFFLINE", None)
    try:
        for key in keys:
            os.environ[key] = "0" if download else "1"
        if hf is not None:
            hf.HF_HUB_OFFLINE = not download
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if hf is not None:
            hf.HF_HUB_OFFLINE = old_hf


# Arguments that name files: made absolute before a job goes to the app, which
# does not share the caller's working directory.
PATH_ARGS = ("input", "inputs", "out", "out_dir", "text_file", "subtitles", "notes_file", "lyrics_file",
             "transcript_file", "sung_lyrics")


def _absolute(ns: argparse.Namespace) -> dict:
    from thundertalk.core import links
    args = dict(vars(ns))

    def fix(v):
        if not isinstance(v, str) or not v or links.is_url(v) or v.startswith("my:"):
            return v
        return str(Path(v).expanduser().resolve())

    for key in PATH_ARGS:
        if key in args:
            v = args[key]
            args[key] = [fix(x) for x in v] if isinstance(v, list) else fix(v)
    return args


def _handler(command: str):
    if command not in (*COMMANDS, "notes", "burn", "translate", "history", "models", "compose", "cloud", "eval",
                       "batch"):
        raise CliError("bad_args", "Missing command: transcribe, speak, voices or status")
    handler = COMMANDS.get(command)
    if handler is None:
        from thundertalk import cli_batch, cli_cloud, cli_eval, cli_library, cli_music, cli_studio
        module = (cli_music if command == "compose" else cli_cloud if command == "cloud"
                  else cli_eval if command == "eval" else cli_batch if command == "batch"
                  else cli_studio if command in ("notes", "burn", "translate") else cli_library)
        handler = getattr(module, "cmd_" + command)
    return handler


def execute(a: argparse.Namespace, out, *, in_app: bool = False) -> int:
    """Run one parsed command, emitting its events and final ``result`` on ``out``.
    ``in_app``: inside the running app's service, where the process-wide
    offline switch for model hubs is left alone (the app manages its downloads)."""
    from contextlib import nullcontext
    from thundertalk.core import apple_speech
    apple_speech.PROMPT = False
    try:
        handler = _handler(a.command)
        guard = nullcontext() if in_app else network_mode(a.command == "models" and a.action == "download")
        with guard:
            result = handler(a, out)
        result.setdefault("ok", True)
        out.emit(type="result", **result)
        return 0 if result["ok"] else 1
    except KeyboardInterrupt:
        out.emit(type="result", ok=False, error="Interrupted", code="cancelled")
        return 1
    except Exception as exc:                                   # noqa: BLE001
        result = error_result(exc)
        if result["code"] == "failed":
            import traceback
            traceback.print_exc()
        out.emit(type="result", **result)
    return 1


def app_service() -> bool:
    """The running app takes jobs: same version (so the same options), and not THUNDERTALK_LOCAL=1."""
    import thundertalk
    from thundertalk import service
    if os.environ.get("THUNDERTALK_LOCAL") == "1":
        return False
    info = service.available()
    return info is not None and info.get("version") == thundertalk.__version__


def _forward(a: argparse.Namespace, out) -> Optional[int]:
    """Hand a heavy job to the running app (one queue, shared models). None when
    there is no app to hand it to, or the caller asked to run it here."""
    from thundertalk import service
    if a.command not in service.SERVICE_COMMANDS or not app_service():
        return None
    result = service.run(a.command, _absolute(a), lambda e: out.emit(**e),
                         client=os.environ.get("THUNDERTALK_CLIENT", "cli"))
    if result is None:
        return None
    return 0 if result.get("ok") else 1


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["-h"], ["--help"], []):
        _parser().print_help()
        return 0 if argv else 1
    out = _Out()
    try:
        a = _parser().parse_args(argv)
    except CliError as exc:
        out.emit(type="result", **error_result(exc))
        return 1
    forwarded = _forward(a, out)
    if forwarded is not None:
        return forwarded
    return execute(a, out)
