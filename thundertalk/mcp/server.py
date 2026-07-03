"""ThunderTalk MCP server.

Exposes ASR transcription (via ASR API at :8765) and TTS synthesis
(via mlx-tts-server at :8000) as MCP tools.

Usage:
    # Register with Claude Code:
    claude mcp add thundertalk -- python -m thundertalk.mcp.server

    # Run directly (stdio transport):
    python -m thundertalk.mcp.server
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Optional

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise SystemExit("Install MCP SDK: uv pip install mcp")

_ASR_BASE = "http://127.0.0.1:8765"
_TTS_BASE = "http://localhost:8000"

mcp = FastMCP("thundertalk")


# ── ASR tool ──────────────────────────────────────────────────────────────────

@mcp.tool()
def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio or video file to text using the ThunderTalk ASR engine.

    Args:
        file_path: Absolute path to the audio/video file to transcribe.

    Returns:
        The transcribed text.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, "rb") as f:
        audio_bytes = f.read()

    boundary = "----ThunderTalkBoundary"
    name = path.name
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{_ASR_BASE}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read())
            return result.get("text", "")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach ASR API at {_ASR_BASE}. "
            "Start it with: python -m thundertalk.api.server --model <path>"
        ) from e


@mcp.tool()
def get_asr_status() -> dict:
    """Get the current status of the ThunderTalk ASR API server.

    Returns:
        A dict with 'status' ('ok' or 'offline') and 'model' name.
    """
    try:
        with urllib.request.urlopen(f"{_ASR_BASE}/health", timeout=3) as resp:
            return json.loads(resp.read())
    except Exception:
        return {"status": "offline", "model": ""}


# ── TTS tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def synthesize_speech(
    text: str,
    output_path: str,
    voice: str = "ryan",
    speed: float = 1.0,
    instruct: str = "",
) -> str:
    """Synthesize speech from text using mlx-tts-server and save to a WAV file.

    Args:
        text: The text to synthesize.
        output_path: Where to save the output WAV file (absolute or ~-relative path).
        voice: Voice name (ryan, serena, vivian, aiden, eric, dylan, sohee, uncle_fu, ono_anna).
        speed: Speech speed multiplier (0.25 – 4.0).
        instruct: Optional speaking style instruction (e.g. "speak warmly and slowly").

    Returns:
        The absolute path to the saved WAV file.
    """
    payload: dict = {
        "model": "tts-1",
        "input": text,
        "voice": voice,
        "response_format": "wav",
        "speed": speed,
    }
    if instruct.strip():
        payload["instruct"] = instruct.strip()

    req = urllib.request.Request(
        f"{_TTS_BASE}/v1/audio/speech",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            wav_bytes = resp.read()
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach mlx-tts-server at {_TTS_BASE}. "
            "Start it with: mlx-tts-server"
        ) from e

    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(wav_bytes)
    return str(out)


@mcp.tool()
def get_tts_voices() -> list[str]:
    """Get the list of available voices from the mlx-tts-server.

    Returns:
        A list of voice name strings, or a default list if the server is offline.
    """
    try:
        with urllib.request.urlopen(f"{_TTS_BASE}/v1/audio/speech/voices", timeout=3) as resp:
            data = json.loads(resp.read())
            return data.get("voices", [])
    except Exception:
        return ["ryan", "serena", "vivian", "aiden", "eric", "dylan", "sohee", "uncle_fu", "ono_anna"]


if __name__ == "__main__":
    mcp.run()
