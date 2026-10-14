"""`ThunderTalk --cli …` end to end, as a subprocess: stdout is JSON lines
only, the last is the result, exit code 0/1, and no window is opened.

Real speech clips are the human test recordings shipped with the downloaded
models; tests skip when they (or the Apple engines) are not on this Mac.
Set THUNDERTALK_BIN to run the same tests against the packaged binary."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = Path.home() / ".thundertalk" / "models"
ZH_CLIP = MODELS / "funasr-nano-int8" / "test_wavs" / "far_3.wav"
EN_CLIP = MODELS / "funasr-nano-int8" / "test_wavs" / "noise_en.wav"


def _cli(*args: str, timeout: float = 300) -> tuple[int, list[dict], str]:
    exe = os.environ.get("THUNDERTALK_BIN")
    cmd = [exe] if exe else [sys.executable, str(ROOT / "run.py")]
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "PYTHONPATH": str(ROOT)}
    p = subprocess.run([*cmd, "--cli", *args], capture_output=True, text=True, timeout=timeout,
                       env=env, cwd=str(ROOT), check=False)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    msgs = [json.loads(ln) for ln in lines]                 # every stdout line must be JSON
    assert msgs and msgs[-1]["type"] == "result", p.stderr[-2000:]
    assert all(m["type"] in ("stage", "progress", "result") for m in msgs)
    return p.returncode, msgs, p.stderr


def _apple_asr(lang: str) -> None:
    from thundertalk.core import apple_speech
    if not apple_speech.available() or apple_speech.authorization_status() in ("denied", "restricted"):
        pytest.skip("Apple speech recognition unavailable")
    try:
        apple_speech.resolve_locale(lang)
    except apple_speech.AppleSpeechError:
        pytest.skip(f"no on-device {lang} locale")


_SRT_TIME = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)")


def _srt_cues(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        m = _SRT_TIME.match(lines[1])
        assert m, block
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        cues.append((h1 * 3600 + m1 * 60 + s1 + ms1 / 1000, h2 * 3600 + m2 * 60 + s2 + ms2 / 1000,
                     "\n".join(lines[2:])))
    return cues


def _check_srt(path: Path) -> list[tuple[float, float, str]]:
    cues = _srt_cues(path.read_text(encoding="utf-8"))
    assert cues
    prev = 0.0
    for a, b, text in cues:
        assert a >= prev and b > a and b - a <= 7.05
        assert text.strip() and text.count("\n") <= 1
        prev = b
    return cues


@pytest.mark.parametrize("clip,lang,expect", [(ZH_CLIP, "zh", "露营"), (EN_CLIP, "en", "")])
def test_transcribe_real_clip_to_srt_with_apple(tmp_path, clip, lang, expect):
    if not clip.exists():
        pytest.skip(f"missing {clip}")
    _apple_asr(lang)
    out = tmp_path / "out.srt"
    code, msgs, _ = _cli("transcribe", str(clip), "--out", str(out), "--engine", "apple")
    res = msgs[-1]
    assert code == 0 and res["ok"], res
    assert res["engine"] == "apple-speech" and res["entries"] >= 1
    assert any(m["type"] == "progress" for m in msgs)
    cues = _check_srt(out)
    assert expect in "".join(c[2] for c in cues)


@pytest.mark.skipif(not os.environ.get("THUNDERTALK_CLI_MODELS"),
                    reason="loads the dictation model; set THUNDERTALK_CLI_MODELS=1")
@pytest.mark.parametrize("clip", [ZH_CLIP, EN_CLIP])
def test_transcribe_real_clip_with_dictation_model(tmp_path, clip):
    if not clip.exists():
        pytest.skip(f"missing {clip}")
    out = tmp_path / "out.srt"
    code, msgs, _ = _cli("transcribe", str(clip), "--out", str(out), timeout=900)
    assert code == 0 and msgs[-1]["ok"], msgs[-1]
    _check_srt(out)


def test_transcribe_json_has_cues_and_words(tmp_path):
    if not ZH_CLIP.exists():
        pytest.skip("missing clip")
    _apple_asr("zh")
    out = tmp_path / "out.json"
    code, msgs, _ = _cli("transcribe", str(ZH_CLIP), "--out", str(out), "--engine", "apple")
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["segments"] and all(s["text"] and " " not in s["text"] for s in data["segments"])
    starts = [s["start"] for s in data["segments"]]
    assert starts == sorted(starts)


@pytest.mark.parametrize("ext", ["m4a", "wav"])
def test_speak_with_apple_voice(tmp_path, ext):
    from thundertalk.core.tts_backends import apple
    if not apple.available() or not apple.system_voices():
        pytest.skip("no system voices")
    out = tmp_path / f"hi.{ext}"
    code, msgs, _ = _cli("speak", "--text", "你好，这是命令行测试。Hello from the command line.",
                         "--out", str(out), "--engine", "apple")
    res = msgs[-1]
    assert code == 0 and res["ok"], res
    assert res["engine"] == "apple" and res["duration"] > 0
    from thundertalk.core import audio_io
    assert audio_io.audio_duration(str(out)) == pytest.approx(res["duration"], abs=0.2)


def test_voices_and_status():
    code, msgs, _ = _cli("voices", "--engine", "apple")
    assert code == 0 and isinstance(msgs[-1]["voices"], list)
    code, msgs, _ = _cli("status")
    res = msgs[-1]
    assert code == 0 and res["ok"]
    assert "apple_speech" in res and "dictation_model" in res["asr"]
    assert "speech_recognition" in res["permissions"]


def test_cli_never_imports_qt(tmp_path):
    from thundertalk.core.tts_backends import apple
    if not apple.available() or not apple.system_voices():
        pytest.skip("no system voices")
    code = ("import sys\nfrom thundertalk import cli\n"
            "try:\n    cli.main(sys.argv[1:])\nexcept SystemExit:\n    pass\n"
            "import os\nos.write(2, ('QT=' + ','.join(m for m in sys.modules if m.startswith('PySide6'))).encode())\n")
    for args in (["status"], ["voices", "--engine", "apple"],
                 ["speak", "--text", "hello", "--out", str(tmp_path / "a.wav"), "--engine", "apple"]):
        p = subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)}, cwd=str(ROOT), check=False)
        assert "QT=" in p.stderr and p.stderr.rsplit("QT=", 1)[1].strip() == "", (args, p.stderr[-500:])


@pytest.mark.parametrize("args,code", [
    (("transcribe", "/no/such/file.wav", "--out", "/tmp/x.srt"), "not_found"),
    (("speak", "--text", "hi", "--out", "/tmp/x.ogg"), "bad_args"),
    (("bogus",), "bad_args"),
    (("transcribe",), "bad_args"),
])
def test_errors_are_json_with_exit_1(args, code):
    rc, msgs, _ = _cli(*args)
    assert rc == 1
    assert msgs[-1]["ok"] is False and msgs[-1]["code"] == code and msgs[-1]["error"]
