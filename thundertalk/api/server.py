"""ThunderTalk ASR HTTP API server.

Exposes the active ASR engine as an OpenAI-compatible transcription endpoint.

Usage:
    python -m thundertalk.api.server [--port 8765] [--model <model-dir>]

Endpoints:
    POST /v1/audio/transcriptions   multipart/form-data: file=<audio>
    GET  /health                    {"status":"ok","model":"..."}
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError:
    sys.exit("Install API deps: uv pip install fastapi uvicorn python-multipart")

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is required")

app = FastAPI(title="ThunderTalk ASR API", version="1.0.0")

_engine = None
_model_name: str = "unloaded"


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


def _load_engine(model: str) -> None:
    global _engine, _model_name
    from thundertalk.core.asr import AsrEngine
    info, path = _resolve_model(model)
    engine = AsrEngine()
    engine.load_model(path, info.family, info.backend)
    _engine, _model_name = engine, info.id


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "model": _model_name})


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
    timestamp_granularities: Optional[str] = Form(None),
) -> JSONResponse:
    if _engine is None:
        raise HTTPException(503, "No model loaded — start the server with --model <path>")

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(500, "ffmpeg not found on PATH")

    audio_bytes = await file.read()
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        cmd = [ffmpeg, "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", "-f", "f32le", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            raise HTTPException(422, f"ffmpeg: {r.stderr.decode(errors='replace')[-400:]}")
        samples = np.frombuffer(r.stdout, dtype=np.float32)
    finally:
        os.unlink(tmp_path)

    if len(samples) == 0:
        raise HTTPException(422, "No audio data extracted from file")

    if language:
        _engine.set_language(language)
    result = _engine.recognize(samples, 16000)
    text = result.text.strip()

    if response_format == "text":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(text)

    return JSONResponse({"text": text})


def main() -> None:
    parser = argparse.ArgumentParser(description="ThunderTalk ASR API server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--model", default="", help="Model id from the Models page (e.g. qwen3-asr-06b-int8) or its folder")
    args = parser.parse_args()

    if args.model:
        print(f"Loading model: {args.model}")
        _load_engine(args.model)
    else:
        print("Warning: no --model specified; /v1/audio/transcriptions will return 503 until a model is loaded")

    print(f"ThunderTalk ASR API listening on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
