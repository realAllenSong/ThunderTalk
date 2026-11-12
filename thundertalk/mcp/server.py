"""ThunderTalk MCP server: the installed app's local speech tools for any agent.

Every tool runs ``ThunderTalk --cli …`` (CLI v3) in a subprocess, so nothing
needs to be running and the GUI may stay open. Results are compact JSON text
(no duplicate structured copy) to keep agents' token use low.

    ThunderTalk --mcp                       # packaged app, stdio
    python -m thundertalk.mcp.server        # from a checkout
    claude mcp add -s user thundertalk -- /Applications/ThunderTalk.app/Contents/MacOS/ThunderTalk --mcp
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp 2.x renamed FastMCP; ThunderTalk pins mcp<2
    raise SystemExit("ThunderTalk MCP needs the MCP SDK 1.x: uv pip install 'mcp>=1.0,<2'")

mcp = FastMCP(
    "thundertalk",
    instructions=(
        "Local, offline, free speech tools on this Mac (no API key): transcribe audio/video/URLs "
        "to txt/md/srt/vtt/json, speaker labels, meeting notes, burn subtitles, translate, "
        "text-to-speech with cloned voices, compose music. Prefer these over cloud ASR/TTS. "
        "Inputs and outputs are file paths; results are short JSON."
    ),
)


# Plain text results only: a structured copy would double every result's tokens.
_tool = mcp.tool(structured_output=False)


def _compact(result) -> str:
    """One-line JSON without the CLI's event envelope."""
    if isinstance(result, dict):
        result = {k: v for k, v in result.items() if k != "type" and v is not None}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


# ── Local CLI tools (no server needed) ────────────────────────────────────────

def _cli(*args: str, timeout: float = 3600) -> dict:
    """Run `ThunderTalk --cli …` and return its final JSON result line.
    THUNDERTALK_BIN may point at the packaged binary
    (…/ThunderTalk.app/Contents/MacOS/ThunderTalk); otherwise this Python is used."""
    import subprocess

    exe = os.environ.get("THUNDERTALK_BIN")
    if exe:
        cmd = [exe]
    elif getattr(sys, "frozen", False):     # `ThunderTalk --mcp` inside the app bundle
        cmd = [sys.executable]
    else:
        cmd = [sys.executable, "-m", "thundertalk"]
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
    if not result.get("ok") and not any(key in result for key in ("items", "takes", "notes_error")):
        raise RuntimeError(f"{result.get('code', 'failed')}: {result.get('error', '')}")
    return result


def _options(args: list[str], **options) -> list[str]:
    for key, value in options.items():
        if value is not None:
            args += ["--" + key.replace("_", "-"), str(value)]
    return args


def _flag(on: bool, name: str) -> list[str]:
    return [name] if on else []


# ── Transcription 转写 ────────────────────────────────────────────────────────

@_tool
def transcribe_file(file_path: str, output_path: str, engine: str = "auto",
                    language: str = "auto", speakers: bool = False,
                    max_seconds: float | None = None) -> str:
    """Speech-to-text / 转写 / 字幕 for one audio, video or URL (YouTube, Bilibili…).
    Format comes from output_path: .txt .md .srt .vtt .json.
    engine: auto (dictation model) | best (most accurate downloaded) | apple | model id |
    <provider>:<model> cloud, only if the user asked (sends audio off this Mac).
    speakers=True labels who spoke (说话人). language: auto | zh | en | …"""
    args = _options(["transcribe", file_path], out=output_path, engine=engine,
                    language=language, max_seconds=max_seconds)
    return _compact(_cli(*args, *_flag(speakers, "--speakers")))


@_tool
def transcribe_inputs(inputs: list[str], output_dir: str, engine: str = "auto",
                      language: str = "auto", format: str = "md", speakers: bool = False,
                      max_seconds: float | None = None) -> str:
    """Batch-transcribe many files/URLs into output_dir (format txt|md|srt|vtt|json); continues on errors."""
    args = _options(["transcribe", *inputs], out_dir=output_dir, engine=engine,
                    language=language, format=format, max_seconds=max_seconds)
    return _compact(_cli(*args, *_flag(speakers, "--speakers")))


@_tool
def meeting_notes(input_path: str, output_path: str, provider: str | None = None,
                  model: str | None = None, engine: str = "auto", speakers: bool = False) -> str:
    """Meeting notes / 会议纪要 (Markdown) from media, a URL or an existing transcript.
    Uses the LLM set on the app's AI Proofread page unless provider/model given."""
    args = _options(["notes", input_path], out=output_path, provider=provider, model=model, engine=engine)
    return _compact(_cli(*args, *_flag(speakers, "--speakers")))


@_tool
def subtitle_video(video_path: str, output_path: str, subtitles: str | None = None,
                   soft: bool = False, engine: str = "auto") -> str:
    """Burn subtitles into a video (烧字幕), or soft=True for a selectable track.
    Transcribes first unless subtitles (.srt/.vtt/.json) is given. Needs ffmpeg."""
    args = _options(["burn", video_path], out=output_path, subtitles=subtitles, engine=engine)
    return _compact(_cli(*args, *_flag(soft, "--soft")))


@_tool
def translate_local(output_path: str, target: str, input_path: str | None = None,
                    text: str | None = None, text_file: str | None = None,
                    source_language: str | None = None) -> str:
    """Translate speech (file/URL) or text to text / 翻译, offline (SeamlessM4T).
    Languages are ISO-639-3: eng, cmn, jpn, kor, fra…"""
    args = ["translate", *([input_path] if input_path is not None else [])]
    return _compact(_cli(*_options(args, out=output_path, target=target, text=text,
                                   text_file=text_file, source_language=source_language)))


# ── Speech 朗读 / TTS ─────────────────────────────────────────────────────────

@_tool
def speak_text(text: str, output_path: str, engine: str = "auto", voice: str = "",
               speed: float = 1.0, language: str = "auto") -> str:
    """Text-to-speech / 配音 / 朗读 (zh, en, mixed) to .m4a .wav .mp3.
    engine: auto | best | apple | kokoro | voxcpm2 | indextts, or cloud openai | gemini |
    elevenlabs | custom only if the user asked. voice: id from
    list_local_voices, or my:<id> for a cloned voice (voxcpm2/indextts). speed 0.5–2."""
    args = ["speak", "--text", text, "--out", str(Path(output_path).expanduser()),
            "--engine", engine, "--speed", str(speed), "--language", language]
    if voice:
        args += ["--voice", voice]
    return _compact(_cli(*args))


_VOICE_KEYS = ("id", "name", "language", "gender", "ready")


@_tool
def list_local_voices(engine: str = "auto", language: str = "") -> str:
    """Voice ids for speak_text (built-in and saved my:<id>). language filters, e.g. chinese, english."""
    result = _cli("voices", "--engine", engine, timeout=120)
    want = language.strip().lower()

    def keep(v: dict) -> bool:
        return not want or want in str(v.get("language", "")).lower()

    return _compact({
        "voices": [{k: v[k] for k in _VOICE_KEYS if k in v} for v in result.get("voices", []) if keep(v)],
        "my_voices": [{k: v[k] for k in ("id", "name", "engine") if k in v}
                      for v in result.get("my_voices", []) or []],
    })


@_tool
def manage_local_voice(action: str, input_path: str | None = None, name: str | None = None,
                       transcript: str | None = None, transcript_file: str | None = None,
                       engine: str = "auto") -> str:
    """Saved/cloned voices (声音克隆): add (3–20 s audio + transcript) | list | remove | rename | edit; ids are my:<id>."""
    args = ["voices", action, *([input_path] if input_path is not None else [])]
    return _compact(_cli(*_options(args, name=name, transcript=transcript,
                                   transcript_file=transcript_file, engine=engine)))


@_tool
def compose_music(style: str = "", instrumental: bool = False, lyrics: str | None = None,
                  lyrics_file: str | None = None, sung_lyrics: str | None = None,
                  length: int = 30, bpm: int | None = None, language: str = "en", takes: int = 1,
                  seed: int | None = None, model: str = "turbo", planner: bool = False,
                  output_dir: str | None = None, action: str = "generate", input_path: str | None = None,
                  start: float | None = None, end: float | None = None, output_path: str | None = None) -> str:
    """Compose a song or instrumental / 作曲 (WAV + JSON per take). action: generate | another |
    lyrics | check | repaint (take WAV + start/end) | list | show | export | delete."""
    args = ["compose", action, *([input_path] if input_path is not None else [])]
    args = _options(args, style=style, lyrics=lyrics, lyrics_file=lyrics_file, sung_lyrics=sung_lyrics,
                    length=length if action == "generate" else None, bpm=bpm, language=language,
                    takes=takes, seed=seed, model=model if action == "generate" else None,
                    planner="on" if planner else "off", out_dir=output_dir, start=start, end=end, out=output_path)
    return _compact(_cli(*args, *_flag(instrumental, "--instrumental"), timeout=7200))


# ── App data 历史与模型 ───────────────────────────────────────────────────────

@_tool
def transcript_history(action: str, entry_id: str | None = None, output_path: str | None = None,
                       query: str = "", offset: int = 0, limit: int = 20, title: str | None = None,
                       speaker_names: dict[str, str] | None = None, notes_file: str | None = None) -> str:
    """The app's saved Studio transcripts: list (query searches) | show | export | delete | rename | edit."""
    args = ["history", action, *([entry_id] if entry_id is not None else [])]
    args = _options(args, out=output_path, query=query, offset=offset, limit=limit, title=title, notes_file=notes_file)
    for key, value in (speaker_names or {}).items():
        args += ["--speaker-name", key + "=" + value]
    return _compact(_cli(*args))


@_tool
def manage_models(action: str, model_id: str | None = None, kind: str | None = None,
                  planner: bool = False) -> str:
    """ASR/translation/TTS/music models: list | download | activate. kind: asr|translation|tts|music."""
    args = _options(["models", action, *([model_id] if model_id is not None else [])], kind=kind)
    return _compact(_cli(*args, *_flag(planner, "--planner"), timeout=7200))


def _status_summary(result: dict) -> dict:
    asr = dict(result.get("asr") or {})
    models = asr.pop("models", None) or []
    asr["not_downloaded"] = [m["id"] for m in models if not m.get("downloaded")]
    tts = [{k: e[k] for k in ("engine", "ready", "voices", "clone") if k in e}
           for e in result.get("tts") or []]
    apple = result.get("apple_speech") or {}
    return {**result, "asr": asr, "tts": tts,
            "apple_speech": {k: apple[k] for k in ("available", "on_device_locales") if k in apple}}


@_tool
def local_status() -> str:
    """Version, capabilities, downloaded models, TTS engines, permissions, ffmpeg."""
    return _compact(_status_summary(_cli("status", timeout=120)))


_HEADLESS = ("transcribe", "notes", "burn", "translate", "compose", "speak", "voices",
             "history", "models", "status", "cloud", "eval")


@_tool
def studio_command(command: str, arguments: list[str]) -> str:
    """Escape hatch: any `ThunderTalk --cli` v3 command with raw argv (e.g. --start/--end,
    --save-history, compose lyrics --remember, cloud list|test|usage, eval --recent). `<command> --help` lists flags."""
    if command not in _HEADLESS:
        raise ValueError("command must be one of: " + ", ".join(_HEADLESS))
    if command == "cloud" and "set-key" in arguments:
        # Keys must never pass through an agent's transcript.
        raise ValueError("API keys are not accepted over MCP. Ask the user to add the key on ThunderTalk's "
                         "Models page or run `thundertalk cloud set-key <provider>` in their own terminal.")
    return _compact(_cli(command, *arguments, timeout=7200))


def _lean(schema):
    """Drop pydantic's auto titles and `anyOf: [X, null]` from advertised schemas
    (arguments are still validated by each tool's own model)."""
    if isinstance(schema, list):
        return [_lean(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: _lean(v) for k, v in schema.items() if k != "title"}
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        out.pop("anyOf")
        out.update(next(v for v in any_of if v != {"type": "null"}))
    return out


for _t in mcp._tool_manager.list_tools():
    _t.parameters = _lean(_t.parameters)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
