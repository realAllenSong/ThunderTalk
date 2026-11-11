"""Cloud engines: request building, response parsing, error mapping, key
handling, and an end-to-end run against a local OpenAI-compatible server.
No real provider is called and no real key is used."""

from __future__ import annotations

import base64
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from thundertalk.core import cloud

FAKE_PORT = 18765
SECRET = "sk-test-SECRET-0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolated_keys(monkeypatch):
    monkeypatch.setenv("THUNDERTALK_KEYCHAIN_SERVICE", "ThunderTalk Cloud TEST")
    for pid, p in cloud.PROVIDERS.items():
        monkeypatch.delenv(f"THUNDERTALK_{pid.upper()}_API_KEY", raising=False)
        for name in p.env:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cloud, "_keychain_available", lambda: False)
    cloud.forget_cached_keys()
    cloud._VOICE_CACHE.clear()
    yield
    cloud.forget_cached_keys()


def _tone(seconds=1.0, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _wav(seconds=0.5, sr=24000) -> bytes:
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, _tone(seconds, sr), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class _Capture:
    def __init__(self, response, status=200, ctype="application/json"):
        self.calls = []
        self.response, self.status, self.ctype = response, status, ctype

    def __call__(self, req, timeout=0):
        self.calls.append(req)
        if self.status != 200:
            import urllib.error
            body = io.BytesIO(json.dumps({"error": {"message": f"bad key {SECRET}"}}).encode())
            raise urllib.error.HTTPError(req.full_url, self.status, "err", {}, body)
        payload = self.response if isinstance(self.response, bytes) else json.dumps(self.response).encode()

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R(payload)


def _use(monkeypatch, capture):
    monkeypatch.setattr(cloud.urllib.request, "urlopen", capture)


def _fields(req) -> dict:
    body = req.data
    ctype = req.headers.get("Content-type") or req.get_header("Content-type")
    boundary = ctype.split("boundary=")[1].encode()
    out: dict = {}
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        out.setdefault(name, []).append(value[:-2] if value.endswith(b"\r\n") else value)
    return out


# ── ids, catalog, config ─────────────────────────────────────────────────

def test_ids():
    assert cloud.split_id("openai:gpt-transcribe") == ("openai", "gpt-transcribe")
    assert cloud.split_id("kokoro:3") == ("", "")
    assert cloud.split_id("openai:") == ("", "")
    assert not cloud.is_cloud_id("qwen3-asr-06b-int8")


def test_models_find_and_downloaded(monkeypatch):
    from thundertalk.core import models
    info = models.find("groq:whisper-large-v3-turbo")
    assert info.backend == "cloud" and info.size_mb == 0 and "Groq" in info.notes
    assert not models.is_downloaded("groq:whisper-large-v3-turbo")
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    assert models.is_downloaded("groq:whisper-large-v3-turbo")
    assert models.get_model_path("groq:whisper-large-v3-turbo") == "groq:whisper-large-v3-turbo"


def test_custom_needs_base_url_not_key():
    assert not cloud.is_configured("custom")
    cloud.save_config("custom", base_url="http://127.0.0.1:18765/v1/")
    assert cloud.is_configured("custom")
    assert cloud.config("custom")["base_url"] == "http://127.0.0.1:18765/v1"
    with pytest.raises(cloud.CloudError) as e:
        cloud.save_config("custom", base_url="ftp://x")
    assert e.value.code == "cloud_bad_request"


def test_settings_keep_cloud_dictation_model(tmp_path, monkeypatch):
    from thundertalk.core import settings as settings_mod
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"active_model_id": "openai:gpt-transcribe", "studio_engine": "gemini",
                                "studio_voice": "gemini:Kore"}))
    monkeypatch.setattr(settings_mod, "_PATH", path)
    s = settings_mod.Settings()
    assert s.get("active_model_id") == "openai:gpt-transcribe"
    assert s.get("studio_engine") == "gemini" and s.get("studio_voice") == "gemini:Kore"


# ── speech to text ───────────────────────────────────────────────────────

def test_openai_gpt_transcribe_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    cap = _Capture({"text": " hello ", "languages": [{"code": "en"}]})
    _use(monkeypatch, cap)
    r = cloud.transcribe("openai:gpt-transcribe", _tone(), language="en", hotwords=["ThunderTalk", "Qwen<3>"])
    assert r.text == "hello" and r.language == "en"
    req = cap.calls[0]
    assert req.full_url == "https://api.openai.com/v1/audio/transcriptions"
    assert req.get_header("Authorization") == f"Bearer {SECRET}"
    f = _fields(req)
    assert f["model"] == [b"gpt-transcribe"] and f["languages[]"] == [b"en"]
    assert f["keywords[]"] == [b"ThunderTalk", b"Qwen 3"]          # no < > per the docs
    assert "language" not in f and "prompt" not in f
    assert f["file"][0][:4] == b"fLaC"


def test_whisper_style_uses_language_and_prompt(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    cap = _Capture({"text": "你好"})
    _use(monkeypatch, cap)
    cloud.transcribe("groq:whisper-large-v3-turbo", _tone(), language="zh", hotwords=["闪电说"])
    f = _fields(cap.calls[0])
    assert cap.calls[0].full_url.startswith("https://api.groq.com/openai/v1/")
    assert f["language"] == [b"zh"] and f["prompt"] == ["闪电说".encode()]


def test_auto_language_sends_no_hint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    cap = _Capture({"text": "x"})
    _use(monkeypatch, cap)
    cloud.transcribe("openai:whisper-1", _tone(), language="auto")
    f = _fields(cap.calls[0])
    assert "language" not in f and "languages[]" not in f


def test_elevenlabs_scribe(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)
    cap = _Capture({"text": "hi there", "language_code": "eng", "words": [
        {"text": "hi", "type": "word", "start": 0.1, "end": 0.3},
        {"text": " ", "type": "spacing", "start": 0.3, "end": 0.35},
        {"text": "there", "type": "word", "start": 0.35, "end": 0.7}]})
    _use(monkeypatch, cap)
    r = cloud.transcribe("elevenlabs:scribe_v2", _tone(), language="en")
    assert r.text == "hi there" and r.words == [(0.1, 0.3, "hi"), (0.35, 0.7, "there")]
    req = cap.calls[0]
    assert req.full_url == "https://api.elevenlabs.io/v1/speech-to-text"
    assert req.get_header("Xi-api-key") == SECRET
    assert _fields(req)["model_id"] == [b"scribe_v2"] and _fields(req)["language_code"] == [b"en"]


def test_gemini_interactions_request_and_parse(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    cap = _Capture({"steps": [{"type": "thought"}, {"type": "model_output", "content": [
        {"type": "text", "text": "Good "}, {"type": "text", "text": "morning."}]}]})
    _use(monkeypatch, cap)
    r = cloud.transcribe("gemini:gemini-3.8-flash", _tone(), language="en", hotwords=["ThunderTalk"])
    assert r.text == "Good morning."
    req = cap.calls[0]
    assert req.full_url == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert req.get_header("X-goog-api-key") == SECRET
    assert req.get_header("Api-revision") == cloud.GEMINI_API_REVISION
    body = json.loads(req.data)
    assert body["model"] == "gemini-3.8-flash"
    text, audio = body["input"]
    assert text["type"] == "text" and "English" in text["text"] and "ThunderTalk" in text["text"]
    assert audio["type"] == "audio" and audio["mime_type"] == "audio/flac"
    assert base64.b64decode(audio["data"])[:4] == b"fLaC"


def test_gemini_generatecontent_shape_also_parses():
    data = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    assert cloud._gemini_text(data, "Gemini") == "ok"


@pytest.mark.parametrize("status,code", [(401, "cloud_auth"), (403, "cloud_auth"), (429, "cloud_quota"),
                                         (413, "cloud_too_large"), (400, "cloud_bad_request"),
                                         (503, "cloud_network")])
def test_http_errors_map_and_never_leak_the_key(monkeypatch, status, code):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setattr(cloud.time, "sleep", lambda s: None)
    _use(monkeypatch, _Capture({}, status=status))
    with pytest.raises(cloud.CloudError) as e:
        cloud.transcribe("openai:gpt-transcribe", _tone())
    assert e.value.code == code
    assert SECRET not in str(e.value) and SECRET[3:] not in str(e.value)


def test_no_key_fails_before_any_request(monkeypatch):
    cap = _Capture({"text": "x"})
    _use(monkeypatch, cap)
    with pytest.raises(cloud.CloudError) as e:
        cloud.transcribe("openai:gpt-transcribe", _tone())
    assert e.value.code == "cloud_no_key" and not cap.calls


def test_timeout_maps(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)

    def boom(req, timeout=0):
        raise TimeoutError("timed out")
    monkeypatch.setattr(cloud.urllib.request, "urlopen", boom)
    with pytest.raises(cloud.CloudError) as e:
        cloud.transcribe("openai:gpt-transcribe", _tone())
    assert e.value.code == "cloud_timeout"


def test_bad_json_maps(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _use(monkeypatch, _Capture(b"<html>oops</html>"))
    with pytest.raises(cloud.CloudError) as e:
        cloud.transcribe("openai:gpt-transcribe", _tone())
    assert e.value.code == "cloud_bad_response"


# ── engine integration ───────────────────────────────────────────────────

def test_asr_engine_cloud_backend(monkeypatch):
    from thundertalk.core.asr import AsrEngine
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    cap = _Capture({"text": "hello world"})
    _use(monkeypatch, cap)
    eng = AsrEngine()
    eng.load_model("openai:gpt-transcribe", "Cloud-OpenAI", "cloud")
    assert eng.is_loaded and eng.is_cloud and not eng.uses_gpu
    assert eng.current_model == "openai:gpt-transcribe" and eng.max_clip_seconds == 600.0
    assert eng.recognize(_tone(2.0), 16000, preview=True).text == "" and not cap.calls   # previews are free
    assert eng.recognize(_tone(2.0), 16000).text == "hello world" and len(cap.calls) == 1
    from thundertalk.core.transcribe import active_model_id
    assert active_model_id(eng) == "openai:gpt-transcribe"
    eng.unload()
    assert not eng.is_loaded


def test_asr_engine_refuses_without_key():
    from thundertalk.core.asr import AsrEngine
    with pytest.raises(cloud.CloudError) as e:
        AsrEngine().load_model("openai:gpt-transcribe", "Cloud-OpenAI", "cloud")
    assert e.value.code == "cloud_no_key"


def test_long_audio_is_split_under_the_upload_limit(monkeypatch):
    from thundertalk.core.asr import AsrEngine
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    cap = _Capture({"text": "part"})
    _use(monkeypatch, cap)
    eng = AsrEngine()
    eng.load_model("openai:gpt-transcribe", "Cloud-OpenAI", "cloud")
    x = np.tile(np.concatenate([_tone(3.0), np.zeros(8000, np.float32)]), 300)   # ~17.5 min
    r = eng.recognize(x, 16000)
    assert len(cap.calls) >= 2 and r.text
    assert all(len(c.data) < 25 * 1024 * 1024 for c in cap.calls)


def test_live_preview_off_for_cloud_dictation():
    from thundertalk.core.live_preview import preview_wanted
    assert preview_wanted(dict(live_preview=True, active_model_id="qwen3-asr-06b-int8"))
    assert not preview_wanted(dict(live_preview=True, active_model_id="openai:gpt-transcribe"))


def test_cli_auto_and_best_never_pick_cloud(monkeypatch):
    from thundertalk import cli
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setattr("thundertalk.core.models.is_downloaded",
                        lambda mid: mid == "apple-speech" or cloud.is_cloud_id(mid))
    s = {"active_model_id": "openai:gpt-transcribe"}
    assert cli.resolve_asr("auto", s) == "apple-speech"
    assert cli.resolve_asr("best", s) == "apple-speech"
    assert cli.resolve_asr("openai:gpt-transcribe", s) == "openai:gpt-transcribe"
    with pytest.raises(cli.CliError) as e:
        cli.resolve_asr("gemini:gemini-3.8-flash", s)
    assert e.value.code == "cloud_no_key"


# ── text to speech ───────────────────────────────────────────────────────

def test_openai_speech(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    cap = _Capture(_wav(0.5, 24000))
    _use(monkeypatch, cap)
    x = cloud.synthesize("openai", "Hello", "marin")
    assert abs(len(x) / cloud.TTS_RATE - 0.5) < 0.01
    body = json.loads(cap.calls[0].data)
    assert body == {"model": "gpt-4o-mini-tts", "input": "Hello", "voice": "marin", "response_format": "wav"}


def test_gemini_speech(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    data = base64.b64encode(_wav(0.4, 24000)).decode()
    cap = _Capture({"steps": [{"type": "model_output", "content": [
        {"type": "audio", "data": data, "mime_type": "audio/wav"}]}]})
    _use(monkeypatch, cap)
    x = cloud.synthesize("gemini", "你好", "Kore")
    assert abs(len(x) / cloud.TTS_RATE - 0.4) < 0.01
    body = json.loads(cap.calls[0].data)
    assert body["model"] == "gemini-3.8-flash-tts"
    assert body["generation_config"]["speech_config"] == [{"voice": "Kore"}]
    assert body["input"][0]["type"] == "user_input"


def test_gemini_raw_l16_audio():
    pcm = (np.zeros(2400, dtype="<i2")).tobytes()
    assert len(cloud._decode_audio(pcm, "audio/l16;rate=24000")) == 2400


def test_elevenlabs_speech_pcm(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)
    cap = _Capture((np.zeros(4800, dtype="<i2")).tobytes())
    _use(monkeypatch, cap)
    x = cloud.synthesize("elevenlabs", "hi", "abc123")
    assert len(x) == 4800
    assert cap.calls[0].full_url == "https://api.elevenlabs.io/v1/text-to-speech/abc123?output_format=pcm_24000"
    assert json.loads(cap.calls[0].data) == {"text": "hi", "model_id": "eleven_multilingual_v2"}


def test_speech_engine_lists_only_configured_cloud(monkeypatch):
    from thundertalk.core import speech
    ids = [b.info.id for b in speech.backends()]
    assert "openai" not in ids
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    ids = [b.info.id for b in speech.backends()]
    assert "openai" in ids
    b = speech.backend("openai")
    assert not b.info.needs_gpu and b.stochastic is False and b.open_voices
    assert speech.backend_id_for("openai:cedar") == "openai"


def test_speech_engine_synthesizes_with_cloud(monkeypatch):
    from thundertalk.core import speech
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    cap = _Capture(_wav(0.6, 24000))
    _use(monkeypatch, cap)
    r = speech.SpeechEngine().synthesize("Hello there.", "openai:any-new-voice", language="english")
    assert r.sample_rate == 24000 and r.duration > 0.3
    assert json.loads(cap.calls[0].data)["voice"] == "any-new-voice"


# ── keys ─────────────────────────────────────────────────────────────────

def test_env_key_and_summary_never_show_it(monkeypatch):
    monkeypatch.setenv("THUNDERTALK_GROQ_API_KEY", SECRET)
    assert cloud.get_key("groq") == SECRET and cloud.key_source("groq") == "env"
    assert SECRET not in json.dumps(cloud.summary())


def test_bad_key_shape_rejected():
    with pytest.raises(cloud.CloudError):
        cloud.set_key("openai", "has space in it")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain")
def test_real_keychain_roundtrip(monkeypatch):
    monkeypatch.setattr(cloud, "_keychain_available", lambda: True)
    service = "ThunderTalk Cloud TEST"
    try:
        cloud.set_key("groq", SECRET)
        cloud.forget_cached_keys()
        assert cloud.get_key("groq") == SECRET and cloud.key_source("groq") == "keychain"
        import subprocess
        ps = subprocess.run(["ps", "-axww", "-o", "command"], capture_output=True, text=True).stdout
        assert SECRET not in ps
    finally:
        assert cloud.remove_key("groq")
    cloud.forget_cached_keys()
    assert cloud.get_key("groq") == ""
    assert service == cloud._service()


# ── end to end: a local OpenAI-compatible server ─────────────────────────

class _Server(BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.hits.append(("GET", self.path, dict(self.headers)))
        self._send(200, b'{"data": []}', "application/json")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.hits.append(("POST", self.path, dict(self.headers)))
        if self.path == "/v1/audio/transcriptions":
            assert b'name="model"' in body and b"RIFF" in body          # custom endpoints get WAV
            self._send(200, json.dumps({"text": "local server words"}).encode(), "application/json")
        elif self.path == "/v1/audio/speech":
            req = json.loads(body)
            assert req["response_format"] == "wav"
            self._send(200, _wav(0.5, 22050), "audio/wav")
        else:
            self._send(404, b"{}", "application/json")

    def _send(self, code, payload, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def local_server():
    _Server.hits = []
    srv = ThreadingHTTPServer(("127.0.0.1", FAKE_PORT), _Server)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield _Server.hits
    srv.shutdown()
    srv.server_close()


def test_end_to_end_cli_against_local_server(local_server, tmp_path, monkeypatch):
    import soundfile as sf
    from thundertalk import cli
    from thundertalk.core import settings as settings_mod
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(settings_mod, "_PATH", tmp_path / ".thundertalk/settings.json")
    monkeypatch.setenv("THUNDERTALK_CUSTOM_API_KEY", SECRET)

    class Events:
        def __init__(self):
            self.events = []

        def emit(self, **event):
            self.events.append(event)

        def stage(self, label):
            self.emit(type="stage", label=label)

        def progress(self, value):
            self.emit(type="progress", value=value)

    def run(*argv):
        sink = Events()
        monkeypatch.setattr(cli, "_Out", lambda: sink)
        code = cli.main(list(argv))
        return code, sink.events[-1]

    code, res = run("cloud", "set", "custom", "--base-url", f"http://127.0.0.1:{FAKE_PORT}/v1",
                    "--asr-model", "whisper-large", "--tts-model", "kokoro", "--voice", "af_sky")
    assert code == 0 and res["ready"] and res["asr_model"] == "whisper-large"
    code, res = run("cloud", "test", "custom")
    assert code == 0 and res["ok"]

    audio = tmp_path / "talk.wav"
    sf.write(audio, np.concatenate([_tone(2.0), np.zeros(16000, np.float32), _tone(2.0)]), 16000)
    out = tmp_path / "talk.srt"
    code, res = run("transcribe", str(audio), "--out", str(out), "--engine", "custom:whisper-large")
    assert code == 0, res
    assert res["engine"] == "custom:whisper-large" and "local server words" in out.read_text()

    speech_out = tmp_path / "hi.wav"
    code, res = run("speak", "--text", "Hello from the cloud.", "--out", str(speech_out), "--engine", "custom")
    assert code == 0, res
    assert res["engine"] == "custom" and res["voice"] == "custom:af_sky" and speech_out.stat().st_size > 1000

    posts = [h for h in local_server if h[0] == "POST"]
    assert {p[1] for p in posts} == {"/v1/audio/transcriptions", "/v1/audio/speech"}
    assert all(h[2].get("Authorization") == f"Bearer {SECRET}" for h in local_server)
    assert SECRET not in json.dumps(res)


# ── Models page card ─────────────────────────────────────────────────────

def test_cloud_card_use_for_dictation(qapp, monkeypatch, tmp_path):
    from thundertalk.core import settings as settings_mod
    monkeypatch.setattr(settings_mod, "_PATH", tmp_path / "settings.json")
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    s = settings_mod.Settings()
    s.set("cloud_dictation_ok", True)
    from thundertalk.ui.pages.cloud_card import CloudCard
    card = CloudCard(s)
    got = []
    card.use_for_dictation.connect(got.append)
    row = card.rows["groq"]
    assert row._use.isEnabled() and not card.rows["openai"]._use.isEnabled()
    row._use.click()
    assert got == ["groq:whisper-large-v3-turbo"]
    card.refresh("groq:whisper-large-v3-turbo")
    assert not row._use.isEnabled()


def test_cloud_card_never_shows_or_stores_key_in_settings(qapp, monkeypatch, tmp_path):
    from thundertalk.core import settings as settings_mod
    monkeypatch.setattr(settings_mod, "_PATH", tmp_path / "settings.json")
    saved = {}
    monkeypatch.setattr(cloud, "set_key", lambda pid, key: saved.setdefault(pid, key))
    from thundertalk.ui.pages.cloud_card import CloudCard
    card = CloudCard(settings_mod.Settings())
    row = card.rows["openai"]
    row._key.setText(SECRET)
    row._save.click()
    assert saved == {"openai": SECRET} and row._key.text() == ""
    assert SECRET not in (tmp_path / "settings.json").read_text() if (tmp_path / "settings.json").exists() else True


def test_models_page_hosts_cloud_card(qapp, monkeypatch, tmp_path):
    from thundertalk.core import settings as settings_mod
    monkeypatch.setattr(settings_mod, "_PATH", tmp_path / "settings.json")
    from thundertalk.ui.pages.models_page import ModelsPage
    page = ModelsPage(settings_mod.Settings())
    try:
        assert page._cloud_card is not None and set(page._cloud_card.rows) == set(cloud.PROVIDERS)
        page.set_active_model("openai:gpt-transcribe")
        assert page.activate_model("openai:gpt-transcribe") is False      # no key: refuse
        monkeypatch.setenv("OPENAI_API_KEY", SECRET)
        loads = []
        page.load_model_signal.connect(lambda *a: loads.append(a))
        assert page.activate_model("openai:gpt-transcribe") is True
        assert loads == [("openai:gpt-transcribe", "openai:gpt-transcribe", "Cloud-OpenAI", "cloud")]
    finally:
        page.wait_background()


def test_dictation_does_not_retry_cloud_errors():
    from thundertalk.core.dictation import recover_final

    class Engine:
        current_model, active_backend, max_clip_seconds = "openai:gpt-transcribe", "cloud", 600
        calls = 0

        def recognize(self, samples, sr=16000, **kw):
            Engine.calls += 1
            raise cloud.CloudError("cloud_quota", "OpenAI: rate limit or quota reached.")

    t = np.arange(16000 * 2) / 16000
    voice = (0.3 * np.sin(2 * np.pi * 180 * t) * (1 + np.sin(2 * np.pi * 3 * t))).astype(np.float32)
    with pytest.raises(cloud.CloudError):
        recover_final(Engine(), voice)
    assert Engine.calls == 1
