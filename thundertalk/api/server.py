"""ThunderTalk speech HTTP API server: local recognition and synthesis.

Usage:
    python -m thundertalk.api.server [--port 8765] [--host 127.0.0.1] [--asr <model id>] [--tts kokoro]
    thundertalk serve …                 (the same, from the CLI)

Endpoints:
    POST /v1/audio/transcriptions   multipart/form-data: file=<audio>  → {"text": …}
    WS   /v1/realtime/transcribe    ?language=en|zh|auto&silence_ms=350&model=<id>
                                    PCM16 16 kHz mono in; ready / partial / final JSON out
    POST /v1/audio/speech           {"model":"kokoro","input":…,"voice":"kokoro:0",
                                     "response_format":"pcm"|"wav","speed":1.0} → streamed audio
    GET  /v1/audio/voices           voices of the speech engines that are downloaded
    GET  /health                    {"status":"ok","model":"…"}

The default recognition model is the most accurate one downloaded (the CLI's
``--engine best``); it is loaded and warmed up at start. Speech engines load
on first use (``--tts`` is also warmed up at start). Nothing is downloaded.
There is no authentication: keep the default host (127.0.0.1) unless the
network is trusted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    sys.exit("Install API deps: uv pip install fastapi uvicorn python-multipart websockets")

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is required")

from thundertalk.api import realtime
from thundertalk.api.speech_stream import FORMATS, MAX_INPUT_CHARS, MAX_SPEED, MIN_SPEED, SpeechError, TtsHost, wav_header

app = FastAPI(title="ThunderTalk Speech API", version="2.0.0")

_LOCAL_NAMES = {"127.0.0.1", "localhost", "::1"}


def _hostname(value: str) -> str:
    """'http://localhost:8765' / 'localhost:8765' / '[::1]:8765' → 'localhost' / '::1'."""
    from urllib.parse import urlsplit
    value = value.strip()
    if "://" not in value:
        value = "//" + value
    try:
        return (urlsplit(value).hostname or "").lower()
    except ValueError:
        return ""


class LocalOnly:
    """No authentication, so keep web pages out. A browser lets any site send
    requests to 127.0.0.1 and open WebSockets to it (no CORS for WebSockets),
    and a DNS-rebinding page can even read the answers. Native clients send no
    ``Origin``; a browser always does, so a request with a non-local ``Origin``
    is refused. When serving on a local address, the ``Host`` header must name
    a local address too (``app.state.local_host_only``, set by ``serve``)."""

    def __init__(self, app) -> None:                    # Starlette passes the wrapped app as `app`
        self.inner = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            origin = headers.get("origin")
            bad = origin is not None and _hostname(origin) not in _LOCAL_NAMES   # "null" (file://, sandbox) too
            if getattr(app.state, "local_host_only", False) and _hostname(headers.get("host", "")) not in _LOCAL_NAMES:
                bad = True
            if bad:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": b"Forbidden: requests from web pages are not accepted"})
                return
        await self.inner(scope, receive, send)


app.add_middleware(LocalOnly)

def _find_ffmpeg() -> Optional[str]:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.isfile(c):
            return c
    return None


def _resolve_model(model: str):
    """A model id from the Models page, or the path of a downloaded model folder
    (named after its id). Returns (info, directory)."""
    from thundertalk.core.models import BUILTIN_MODELS, get_model_path
    by_id = {m.id: m for m in BUILTIN_MODELS}
    if model in by_id:
        path = get_model_path(model)
        if not path:
            raise SystemExit(f"Model not downloaded: {model}. Download it on the Models page first.")
        return by_id[model], path
    folder = Path(model).expanduser()
    info = by_id.get(folder.name)
    if info is None or not folder.is_dir():
        raise SystemExit(f"Unknown model '{model}'. Use a model id: {', '.join(sorted(by_id))}")
    return info, str(folder)


# ── recognition engines ──────────────────────────────────────────────────

class ModelError(Exception):
    pass


# Backends a server thread can drive: Apple Speech needs the main run loop,
# a cloud engine would send audio off this Mac.
_SERVABLE = ("mlx", "mlx-moss", "onnx", "onnx-cuda", "onnx-coreml", "onnx-directml")


class AsrHost:
    """Loaded recognition engines (one per model id), all used from the
    scheduler's worker thread."""

    def __init__(self, scheduler: realtime.DecodeScheduler) -> None:
        self.scheduler = scheduler
        self.default_id: Optional[str] = None
        self._engines: dict = {}
        self._paths: dict = {}
        self._mu = threading.Lock()
        self._loading: dict = {}

    def resolve(self, model: Optional[str]) -> str:
        """Model id for a request: ``model`` (id or folder), else the default."""
        if not model:
            if self.default_id is None:
                self.default_id = best_model()
            return self.default_id
        try:
            info, path = _resolve_model(model)
        except SystemExit as exc:
            raise ModelError(str(exc)) from None
        if not info.backend.startswith(_SERVABLE):
            raise ModelError(f"{info.id} cannot be served (backend {info.backend}); use a local MLX/ONNX model")
        self._paths[info.id] = path
        return info.id

    def loaded(self, model_id: str) -> bool:
        with self._mu:
            return model_id in self._engines

    def _load(self, model_id: str):
        """Worker thread: load and warm up ``model_id`` (same set-up as the CLI)."""
        if self.loaded(model_id):
            return self._engines[model_id]
        from thundertalk.cli import _settings
        from thundertalk.core.asr import AsrEngine
        from thundertalk.core.models import find, get_model_path
        from thundertalk.core.transcribe import _has_model_headroom
        info = find(model_id)
        path = self._paths.get(model_id) or get_model_path(model_id)
        if info is None or not path:
            raise ModelError(f"Model not downloaded: {model_id}")
        if not _has_model_headroom(info):
            raise ModelError(f"Not enough free memory to load {model_id} safely (needs ≥ 40% free)")
        settings = _settings()
        engine = AsrEngine()
        engine.set_language("auto")
        if settings.hotwords:
            engine.set_hotwords(settings.hotwords)
        engine.set_builtin_terms(settings.get("builtin_terms") is not False)
        engine.set_auto_languages(settings.get("auto_languages") or ["zh", "en"])
        engine.load_model(path, info.family, info.backend, memory_mode=settings.memory_mode)
        # First decode pays kernel compilation; do it now, not on the first segment.
        warm = (np.random.default_rng(0).standard_normal(16000) * 0.01).astype(np.float32)
        try:
            engine.recognize(warm, 16000, preview=True)
        except Exception as exc:                       # noqa: BLE001
            print(f"[API] warm-up decode failed (ignored): {exc}")
        with self._mu:
            self._engines[model_id] = engine
        return engine

    async def ensure(self, model_id: str) -> None:
        if self.loaded(model_id):
            return
        with self._mu:
            fut = self._loading.get(model_id)
            if fut is None or fut.done():
                fut = self.scheduler.submit(realtime.FINAL, self._load, model_id)
                self._loading[model_id] = fut
        await asyncio.wrap_future(fut)

    def recognizer(self, model_id: str) -> realtime.Recognize:
        engine = self._engines[model_id]

        def recognize(samples: np.ndarray, language: str, preview: bool) -> str:
            # Worker thread only, so the language switch and the decode are never interleaved.
            if engine._language_code != (language or "auto"):
                engine.set_language(language or "auto")
            return engine.recognize(samples, 16000, preview=preview).text.strip()
        return recognize


def best_model() -> str:
    """The CLI's ``--engine best``: most accurate downloaded local model."""
    from thundertalk import cli
    from thundertalk.core.models import find
    try:
        model_id = cli.resolve_asr("best", cli._settings())
    except Exception as exc:                           # noqa: BLE001 - CliError: nothing downloaded
        raise ModelError(str(exc)) from None
    info = find(model_id)
    if info is None or not info.backend.startswith(_SERVABLE):
        raise ModelError("No local MLX/ONNX speech model is downloaded (Apple Speech cannot be served). "
                         "Download one on ThunderTalk's Models page, e.g. qwen3-asr-06b-mlx.")
    return model_id


_scheduler = realtime.DecodeScheduler()
_asr = AsrHost(_scheduler)
_tts = TtsHost()
_tts_pool = None                                       # one synthesis thread (created on first use)


def configure(asr: Optional[str] = None, tts: Optional[str] = None) -> None:
    """Pick the default models (no loading)."""
    global _tts
    if asr:
        _asr.default_id = _asr.resolve(asr)
    if tts:
        _tts = TtsHost(tts)


# ── HTTP: health and file transcription ─────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    model = _asr.default_id if _asr.default_id and _asr.loaded(_asr.default_id) else "unloaded"
    return JSONResponse({"status": "ok", "model": model})


def _decode_file(audio_bytes: bytes, suffix: str) -> np.ndarray:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(500, "ffmpeg not found on PATH")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        cmd = [ffmpeg, "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", "-f", "f32le", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            raise HTTPException(422, f"ffmpeg: {r.stderr.decode(errors='replace')[-400:]}")
        return np.frombuffer(r.stdout, dtype=np.float32)
    finally:
        os.unlink(tmp_path)


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
    timestamp_granularities: Optional[str] = Form(None),
):
    try:
        model_id = _asr.resolve(None)
        await _asr.ensure(model_id)
    except ModelError as exc:
        raise HTTPException(503, f"No model loaded: {exc}") from None

    audio_bytes = await file.read()
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    samples = await asyncio.to_thread(_decode_file, audio_bytes, suffix)
    if len(samples) == 0:
        raise HTTPException(422, "No audio data extracted from file")

    recognize = _asr.recognizer(model_id)
    text = await asyncio.wrap_future(_scheduler.submit(realtime.FINAL, recognize, samples, language or "auto", False))

    if response_format == "text":
        return PlainTextResponse(text)
    return JSONResponse({"text": text})


# ── WebSocket: streaming recognition ────────────────────────────────────

async def _pump(ws: WebSocket, outbox: asyncio.Queue) -> None:
    while True:
        msg = await outbox.get()
        if msg is None:
            return
        await ws.send_text(json.dumps(msg, ensure_ascii=False))


@app.websocket("/v1/realtime/transcribe")
async def realtime_transcribe(ws: WebSocket, language: str = "auto",
                              silence_ms: int = realtime.DEFAULT_SILENCE_MS, model: Optional[str] = None):
    await ws.accept()
    language = (language or "auto").lower()
    if language not in realtime.LANGUAGES:
        await ws.send_json({"type": "error", "message": f"Unsupported language '{language}'. "
                                                        f"Use one of: {', '.join(realtime.LANGUAGES)}"})
        await ws.close(code=1008)
        return
    silence_ms = max(realtime.MIN_SILENCE_MS, min(realtime.MAX_SILENCE_MS, int(silence_ms)))
    try:
        model_id = _asr.resolve(model)
        await _asr.ensure(model_id)
    except Exception as exc:                           # noqa: BLE001 - ModelError, load failures
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close(code=1011)
        return

    loop = asyncio.get_running_loop()
    outbox: asyncio.Queue = asyncio.Queue()
    outbox.put_nowait({"type": "ready", "model": model_id, "sample_rate": realtime.SAMPLE_RATE})
    session = realtime.RealtimeSession(
        _asr.recognizer(model_id), scheduler=_scheduler,
        post=lambda fn: loop.call_soon_threadsafe(fn), send=outbox.put_nowait,
        language=language, silence_ms=silence_ms)
    sender = asyncio.create_task(_pump(ws, outbox))
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                session.feed_pcm16(msg["bytes"])
            elif msg.get("text") is not None:
                try:
                    kind = json.loads(msg["text"]).get("type")
                except (ValueError, AttributeError):
                    kind = None
                if kind == "commit":
                    session.commit()
                elif kind == "reset":
                    session.reset()
                else:
                    outbox.put_nowait({"type": "error", "message": "Expected binary PCM16 frames or "
                                                                   '{"type":"commit"} / {"type":"reset"}'})
            if sender.done():
                break                                  # the socket is gone
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
        outbox.put_nowait(None)
        sender.cancel()


# ── HTTP: speech synthesis ──────────────────────────────────────────────

class SpeechRequest(BaseModel):
    input: str
    model: Optional[str] = None
    voice: Optional[str] = None
    response_format: str = "wav"
    speed: float = 1.0


@app.get("/v1/audio/voices")
async def voices() -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(_tts.voices))


def _synthesis_pool():
    global _tts_pool
    if _tts_pool is None:
        from concurrent.futures import ThreadPoolExecutor
        _tts_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tt-tts")
    return _tts_pool


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest):
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(400, "input is empty")
    if len(text) > MAX_INPUT_CHARS:
        raise HTTPException(400, f"input is longer than {MAX_INPUT_CHARS} characters")
    fmt = (req.response_format or "wav").lower()
    if fmt not in FORMATS:
        raise HTTPException(400, f"response_format must be one of: {', '.join(FORMATS)}")
    speed = max(MIN_SPEED, min(MAX_SPEED, float(req.speed or 1.0)))
    try:
        bid, voice, lang = _tts.resolve(req.model, req.voice, text)
    except SpeechError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    sr = _tts.backend(bid).sample_rate

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel = threading.Event()
    done = object()

    def produce() -> None:
        put = lambda item: loop.call_soon_threadsafe(queue.put_nowait, item)   # noqa: E731
        gen = _tts.stream(bid, voice, lang, text, speed, cancel)
        try:
            for chunk in gen:
                put(chunk)
        except BaseException as exc:                   # noqa: BLE001 - delivered to the response
            put(exc)
        finally:
            gen.close()
            put(done)

    loop.run_in_executor(_synthesis_pool(), produce)
    first = await queue.get()                          # errors before any audio → an HTTP error
    if isinstance(first, BaseException):
        cancel.set()
        status = first.status if isinstance(first, SpeechError) else 500
        raise HTTPException(status, f"synthesis failed: {first}")

    async def body():
        try:
            if fmt == "wav":
                yield wav_header(sr)
            item = first
            while item is not done:
                if isinstance(item, BaseException):
                    print(f"[API] synthesis failed mid-stream: {item}")
                    return
                yield item
                item = await queue.get()
        finally:
            cancel.set()

    media = "audio/wav" if fmt == "wav" else "audio/pcm"
    return StreamingResponse(body(), media_type=media,
                             headers={"X-Sample-Rate": str(sr), "X-Voice": voice, "Cache-Control": "no-store"})


# ── start-up ────────────────────────────────────────────────────────────

def warm_up(tts: Optional[str]) -> None:
    """Load the default recognition model (and ``tts``) before serving."""
    try:
        model_id = _asr.resolve(None)
        print(f"Loading recognition model: {model_id}", flush=True)
        _scheduler.submit(realtime.FINAL, _asr._load, model_id).result()
    except Exception as exc:                           # noqa: BLE001
        print(f"Warning: recognition model not loaded ({exc}); it is retried on the first request", flush=True)
    if tts:
        try:
            print(f"Loading speech engine: {tts}", flush=True)
            _tts.warm_up(tts)
        except Exception as exc:                       # noqa: BLE001
            print(f"Warning: speech engine {tts} not loaded ({exc})", flush=True)


def serve(host: str = "127.0.0.1", port: int = 8765, asr: Optional[str] = None, tts: Optional[str] = "kokoro",
          warm: bool = True) -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")      # serving never downloads weights
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        configure(asr=asr, tts=tts)
    except (ModelError, SpeechError) as exc:
        raise SystemExit(str(exc)) from None
    if warm:
        warm_up(tts)
    app.state.local_host_only = host in _LOCAL_NAMES      # DNS rebinding: Host must be local too
    if host not in _LOCAL_NAMES:
        print(f"Warning: listening on {host} with no authentication", flush=True)
    print(f"ThunderTalk speech API listening on http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning", ws_max_size=4 * 1024 * 1024)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="ThunderTalk speech API server (ASR + TTS)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--asr", "--model", dest="asr", default="",
                        help="Recognition model id (e.g. qwen3-asr-06b-mlx) or its folder; default: best downloaded")
    parser.add_argument("--tts", default="kokoro", help="Default speech engine: kokoro (default), voxcpm2, indextts, longcat")
    parser.add_argument("--no-warm-up", action="store_true", help="Load models on first use instead of at start")
    args = parser.parse_args(argv)
    serve(args.host, args.port, asr=args.asr or None, tts=args.tts or None, warm=not args.no_warm_up)


if __name__ == "__main__":
    main()
