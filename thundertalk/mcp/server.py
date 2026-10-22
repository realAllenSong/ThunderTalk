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


# ── Local CLI tools (no server needed) ────────────────────────────────────────

def _cli(*args: str, timeout: float = 3600) -> dict:
    """Run `ThunderTalk --cli …` and return its final JSON result line.
    THUNDERTALK_BIN may point at the packaged binary
    (…/ThunderTalk.app/Contents/MacOS/ThunderTalk); otherwise this Python is used."""
    import subprocess
    import sys

    exe = os.environ.get("THUNDERTALK_BIN")
    cmd = [exe] if exe else [sys.executable, "-m", "thundertalk"]
    proc = subprocess.run([*cmd, "--cli", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)
    result: dict = {}
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "result":
            result = msg
    if not result:
        raise RuntimeError(f"ThunderTalk CLI exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    if not result.get("ok"):
        raise RuntimeError(f"{result.get('code', 'failed')}: {result.get('error', '')}")
    return result


@mcp.tool()
def transcribe_file(file_path: str, output_path: str, engine: str = "auto",
                    language: str = "auto", speakers: bool = False,
                    max_seconds: float | None = None) -> dict:
    """Transcribe an audio/video file locally to txt, srt, vtt or json (chosen by
    output_path's extension). Subtitle cues are at most ~7 s and two lines.

    Args:
        file_path: Audio or video file.
        output_path: Where to write the transcript (.txt / .srt / .vtt / .json).
        engine: "auto" (the dictation model, else Apple), "apple", or a model id.
        language: "auto", "zh", "en", …

    Returns:
        The CLI result: output, format, engine, duration, seconds, entries.
    """
    args = _options(["transcribe", file_path], out=output_path, engine=engine, language=language, max_seconds=max_seconds)
    return _cli(*args, *(["--speakers"] if speakers else []))


@mcp.tool()
def speak_text(text: str, output_path: str, engine: str = "auto", voice: str = "",
               speed: float = 1.0, language: str = "auto") -> dict:
    """Synthesize speech locally with ThunderTalk's Speak engines (including the
    built-in Apple system voices) to .m4a, .wav or .mp3.

    Args:
        text: Text to read (Chinese, English or mixed).
        output_path: Output audio file (.m4a / .wav / .mp3).
        engine: "auto", "apple", "kokoro", "voxcpm2" or "indextts".
        voice: A voice id from list_local_voices (empty = engine default).
        speed: 0.5 – 2.0.
    """
    args = ["speak", "--text", text, "--out", str(Path(output_path).expanduser()),
            "--engine", engine, "--speed", str(speed), "--language", language]
    if voice:
        args += ["--voice", voice]
    return _cli(*args)


@mcp.tool()
def list_local_voices(engine: str = "auto") -> list[dict]:
    """List the voices ThunderTalk's local Speak engines can use right now."""
    return _cli("voices", "--engine", engine, timeout=120).get("voices", [])





def _options(args: list[str], **options) -> list[str]:
    for key, value in options.items():
        if value is not None:
            args += ["--" + key.replace("_", "-"), str(value)]
    return args


@mcp.tool()
def transcribe_inputs(inputs: list[str], output_dir: str, engine: str = "auto",
                      language: str = "auto", format: str = "md", speakers: bool = False,
                      max_seconds: float | None = None) -> dict:
    """Transcribe a batch of files/URLs, continuing on errors. Returns per-item results."""
    args = _options(["transcribe", *inputs], out_dir=output_dir, engine=engine,
                    language=language, format=format, max_seconds=max_seconds)
    return _cli(*args, *(["--speakers"] if speakers else []))


@mcp.tool()
def meeting_notes(input_path: str, output_path: str, provider: str | None = None,
                  model: str | None = None, engine: str = "auto", speakers: bool = False) -> dict:
    """Media/URL or an existing txt/json/md transcript → Markdown meeting notes.
    Defaults to the provider/model configured on AI Proofread. No provider login is attempted.
    """
    args = _options(["notes", input_path], out=output_path, provider=provider, model=model, engine=engine)
    return _cli(*args, *(["--speakers"] if speakers else []))


@mcp.tool()
def subtitle_video(video_path: str, output_path: str, subtitles: str | None = None,
                   soft: bool = False, engine: str = "auto") -> dict:
    """Burn subtitles into video, or add a selectable track. Transcribe first if subtitles omitted."""
    args = _options(["burn", video_path], out=output_path, subtitles=subtitles, engine=engine)
    return _cli(*args, *(["--soft"] if soft else []))


@mcp.tool()
def translate_local(output_path: str, target: str, input_path: str | None = None,
                    text: str | None = None, text_file: str | None = None,
                    source_language: str | None = None) -> dict:
    """SeamlessM4T speech (file/URL) or text translation. Languages are ISO-639-3 (eng, cmn)."""
    args = ["translate", *([input_path] if input_path is not None else [])]
    return _cli(*_options(args, out=output_path, target=target, text=text,
                          text_file=text_file, source_language=source_language))


@mcp.tool()
def compose_music(style: str = "", instrumental: bool = False, lyrics: str | None = None,
                  lyrics_file: str | None = None, sung_lyrics: str | None = None,
                  length: int = 30, bpm: int | None = None, language: str = "en", takes: int = 1,
                  seed: int | None = None, model: str = "turbo", planner: bool = False,
                  output_dir: str | None = None, action: str = "generate", input_path: str | None = None,
                  start: float | None = None, end: float | None = None, output_path: str | None = None) -> dict:
    """Compose song/instrumental takes, with progress and lyric checks in the CLI.
    Also supports check/repaint/list/show/export/delete; repaint needs a take WAV and start/end.
    Each take has WAV + JSON metadata. The memory guard and dictation priority apply.
    """
    args = ["compose", action, *([input_path] if input_path is not None else [])]
    args = _options(args, style=style, lyrics=lyrics, lyrics_file=lyrics_file, sung_lyrics=sung_lyrics,
                    length=length if action == "generate" else None, bpm=bpm, language=language,
                    takes=takes, seed=seed, model=model if action == "generate" else None,
                    planner="on" if planner else "off", out_dir=output_dir, start=start, end=end, out=output_path)
    return _cli(*args, *(["--instrumental"] if instrumental else []), timeout=7200)


@mcp.tool()
def manage_local_voice(action: str, input_path: str | None = None, name: str | None = None,
                       transcript: str | None = None, transcript_file: str | None = None,
                       engine: str = "auto") -> dict:
    """Saved voices: add/list/remove/rename/edit. Add takes audio; other actions use my:<id>."""
    args = ["voices", action, *([input_path] if input_path is not None else [])]
    return _cli(*_options(args, name=name, transcript=transcript, transcript_file=transcript_file, engine=engine))


@mcp.tool()
def transcript_history(action: str, entry_id: str | None = None, output_path: str | None = None,
                       query: str = "", offset: int = 0, limit: int = 50, title: str | None = None,
                       speaker_names: dict[str, str] | None = None, notes_file: str | None = None) -> dict:
    """Studio history: list/search/show/export/delete/rename/edit (speaker names and notes)."""
    args = ["history", action, *([entry_id] if entry_id is not None else [])]
    args = _options(args, out=output_path, query=query, offset=offset, limit=limit, title=title, notes_file=notes_file)
    for key, value in (speaker_names or {}).items():
        args += ["--speaker-name", key + "=" + value]
    return _cli(*args)


@mcp.tool()
def manage_models(action: str, model_id: str | None = None, kind: str | None = None,
                  planner: bool = False) -> dict:
    """List/download/activate ASR, translation, TTS or music models. Downloads include runtime components."""
    args = _options(["models", action, *([model_id] if model_id is not None else [])], kind=kind)
    return _cli(*args, *(["--planner"] if planner else []), timeout=7200)


@mcp.tool()
def local_status() -> dict:
    """CLI version, capabilities, model readiness, engines, permissions and ffmpeg (no server needed)."""
    return _cli("status", timeout=120)


@mcp.tool()
def studio_command(command: str, arguments: list[str]) -> dict:
    """Full CLI v3 escape hatch for every flag, e.g. compose lyrics --remember WORD=SUNG.
    Arguments are passed directly as an argv array, without a shell. The command must be headless.
    """
    if command not in ("transcribe", "notes", "burn", "translate", "compose", "speak", "voices", "history", "models", "status"):
        raise ValueError("Unknown headless command")
    return _cli(command, *arguments, timeout=7200)


if __name__ == "__main__":
    mcp.run()
