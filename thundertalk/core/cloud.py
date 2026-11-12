"""Cloud speech engines: bring-your-own-key ASR and TTS over HTTPS.

Opt-in only. Nothing here touches the network unless the user (or a caller)
explicitly picks a cloud engine or presses "Test"; ``auto``/``best`` never pick
one. Audio or text sent to a provider leaves this Mac, so every surface labels
cloud engines with the provider's name.

Ids follow ``<provider>:<model>`` for ASR (``openai:gpt-transcribe``) and
``<provider>:<voice>`` for TTS voices (``gemini:Kore``). Model and voice names
are free text, so a provider renaming a model needs no app update.

Keys live in the macOS Keychain (generic password, service ``ThunderTalk
Cloud``, account = provider id). ``THUNDERTALK_<PROVIDER>_API_KEY`` or the
provider's usual variable (``OPENAI_API_KEY`` …) is accepted as a headless
fallback. Keys never reach settings, logs, results or exception text.

API shapes were checked against the providers' docs on 2026-11-11:
OpenAI /v1/audio/transcriptions + /v1/audio/speech, Gemini Interactions API
(v1beta/interactions, audio input and TTS), Groq OpenAI-compatible Whisper,
ElevenLabs /v1/speech-to-text (Scribe) and /v1/text-to-speech/{voice_id}.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

KEYCHAIN_SERVICE = "ThunderTalk Cloud"
GEMINI_API_REVISION = "2026-05-20"
TTS_RATE = 24000


class CloudError(RuntimeError):
    """``code`` is stable (cloud_no_key, cloud_auth, cloud_quota, cloud_network,
    cloud_timeout, cloud_too_large, cloud_bad_response, cloud_bad_request,
    cloud_unsupported); the message never contains a key."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str                      # "openai" (OpenAI-compatible) | "gemini" | "elevenlabs"
    base_url: str                  # default; "custom" has none
    asr_models: tuple[str, ...] = ()
    tts_models: tuple[str, ...] = ()
    voices: tuple[str, ...] = ()   # built-in voice names ("" = fetched or free text)
    env: tuple[str, ...] = ()      # extra env-var names accepted for the key
    key_required: bool = True
    clip_seconds: float = 600.0    # per-request audio (stays under upload limits as FLAC)
    upload: str = "flac"           # "flac" | "wav"
    hotwords: bool = True
    notes: str = ""


_OPENAI_VOICES = ("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "fable",
                  "nova", "onyx", "sage", "shimmer", "verse")
_GEMINI_VOICES = ("Kore", "Puck", "Zephyr", "Charon", "Fenrir", "Leda", "Orus", "Aoede",
                  "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
                  "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
                  "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
                  "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat")

PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("openai", "OpenAI", "openai", "https://api.openai.com/v1",
             asr_models=("gpt-transcribe", "whisper-1"),
             tts_models=("gpt-4o-mini-tts", "tts-1-hd", "tts-1"),
             voices=_OPENAI_VOICES, env=("OPENAI_API_KEY",),
             notes="whisper-1 and the TTS models are scheduled to shut down in early 2027"),
    Provider("gemini", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta",
             asr_models=("gemini-3.8-flash",),
             tts_models=("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"),
             voices=_GEMINI_VOICES, env=("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    Provider("groq", "Groq", "openai", "https://api.groq.com/openai/v1",
             asr_models=("whisper-large-v3-turbo", "whisper-large-v3"), env=("GROQ_API_KEY",)),
    Provider("elevenlabs", "ElevenLabs", "elevenlabs", "https://api.elevenlabs.io/v1",
             asr_models=("scribe_v2",), tts_models=("eleven_multilingual_v2", "eleven_flash_v2_5"),
             env=("ELEVENLABS_API_KEY",)),
    Provider("custom", "OpenAI-compatible", "openai", "",
             asr_models=("whisper-1",), tts_models=("tts-1",), key_required=False,
             clip_seconds=300.0, upload="wav"),
)}
ASR_PROVIDERS = tuple(p for p in PROVIDERS if PROVIDERS[p].asr_models)
# Keychain accounts that are not providers: an optional OpenAI admin key, used
# only to read the organization's spend (GET /v1/organization/costs).
ADMIN_ACCOUNTS = {"openai-admin": "openai"}
# Announced shutdown dates (shown next to the model; the app keeps working until then).
RETIRING = {"openai:whisper-1": "2027-02-26", "openai:gpt-4o-mini-tts": "2027-01-06",
            "openai:tts-1": "2027-01-06", "openai:tts-1-hd": "2027-01-06"}
TTS_PROVIDERS = tuple(p for p in PROVIDERS if PROVIDERS[p].tts_models)


# ── ids ──────────────────────────────────────────────────────────────────

def split_id(item_id: str) -> tuple[str, str]:
    """``"openai:gpt-transcribe"`` → ("openai", "gpt-transcribe"); ("", "") if not cloud."""
    pid, sep, rest = (item_id or "").partition(":")
    if sep and pid in PROVIDERS and rest.strip():
        return pid, rest.strip()
    return "", ""


def is_cloud_id(item_id: str) -> bool:
    return bool(split_id(item_id)[0])


def model_choices(pid: str, kind: str, settings=None) -> list[str]:
    """The models offered for ``kind`` ("asr" | "tts"): the provider's own list,
    or for an OpenAI-compatible server the ids its /models returned on Test."""
    p = PROVIDERS[pid]
    base = list(p.asr_models if kind == "asr" else p.tts_models)
    if pid != "custom":
        return base
    stored = ((settings or _settings()).get("cloud") or {}).get(pid) or {}
    found = [m for m in stored.get("models") or [] if isinstance(m, str)]
    if found:
        audio = [m for m in found if _looks_like(m, kind)]
        base = audio or found
    current = config(pid, settings)["asr_model" if kind == "asr" else "tts_model"]
    if current and current not in base:
        base.insert(0, current)
    return base


def _looks_like(model: str, kind: str) -> bool:
    m = model.lower()
    if kind == "asr":
        return any(k in m for k in ("whisper", "transcribe", "asr", "stt", "sensevoice", "paraformer", "parakeet"))
    return any(k in m for k in ("tts", "speech", "voice", "kokoro", "piper"))


# ── settings (non-secret) ────────────────────────────────────────────────

def _settings():
    from thundertalk.core.settings import Settings
    return Settings()


def config(pid: str, settings=None) -> dict:
    """{"base_url", "asr_model", "tts_model", "voice"} for ``pid`` (defaults filled in)."""
    p = PROVIDERS[pid]
    stored = ((settings or _settings()).get("cloud") or {}).get(pid) or {}
    return {
        "base_url": (stored.get("base_url") or p.base_url).strip().rstrip("/"),
        "asr_model": (stored.get("asr_model") or (p.asr_models[0] if p.asr_models else "")).strip(),
        "tts_model": (stored.get("tts_model") or (p.tts_models[0] if p.tts_models else "")).strip(),
        "voice": (stored.get("voice") or "").strip(),
    }


def save_config(pid: str, settings=None, **values) -> dict:
    if pid not in PROVIDERS:
        raise CloudError("cloud_unsupported", f"Unknown provider: {pid}")
    s = settings or _settings()
    allowed = {"base_url", "asr_model", "tts_model", "voice"}
    stored = dict(s.get("cloud") or {})
    entry = dict(stored.get(pid) or {})
    for k, v in values.items():
        if k not in allowed or v is None:
            continue
        v = str(v).strip()
        if k == "base_url" and v and not re.match(r"^https?://[^\s/]+", v):
            raise CloudError("cloud_bad_request", "The base URL must start with http:// or https://")
        if v:
            entry[k] = v
        else:
            entry.pop(k, None)
    stored[pid] = entry
    s.set("cloud", stored)
    return config(pid, s)


# ── keys ─────────────────────────────────────────────────────────────────

_KEY_RE = re.compile(r"^[A-Za-z0-9._~+/=:\-]{8,512}$")
_KEY_CACHE: dict[str, tuple[float, Optional[str]]] = {}
_KEY_LOCK = threading.Lock()
_KEY_TTL = (30.0, 300.0)       # seconds a miss / a hit is trusted (another process may change it)


def _keychain_available() -> bool:
    return sys.platform == "darwin" and os.path.exists("/usr/bin/security")


def _keychain(cmd: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    # `security -i` reads its commands from stdin, so a key never appears in argv
    # (visible to every process through ps).
    return subprocess.run(["/usr/bin/security", "-i"], input=cmd + "\n", capture_output=True,
                          text=True, timeout=timeout, check=False)


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _service() -> str:
    return os.environ.get("THUNDERTALK_KEYCHAIN_SERVICE") or KEYCHAIN_SERVICE   # tests use a throwaway one


def _env_key(pid: str) -> str:
    if pid in ADMIN_ACCOUNTS:
        names: tuple[str, ...] = ("THUNDERTALK_OPENAI_ADMIN_KEY", "OPENAI_ADMIN_KEY")
    else:
        names = (f"THUNDERTALK_{pid.upper()}_API_KEY", *PROVIDERS[pid].env)
    for name in names:
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    return ""


def get_key(pid: str) -> str:
    """The key for ``pid`` ("" if none): Keychain first, then the environment."""
    with _KEY_LOCK:
        hit = _KEY_CACHE.get(pid)
        if hit is not None and time.monotonic() - hit[0] < _KEY_TTL[1 if hit[1] else 0]:
            cached = hit[1]
        else:
            cached = None
            if _keychain_available():
                try:
                    r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", _service(),
                                        "-a", pid, "-w"], capture_output=True, text=True, timeout=10, check=False)
                    if r.returncode == 0 and r.stdout.strip():
                        cached = r.stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    cached = None
            _KEY_CACHE[pid] = (time.monotonic(), cached)
    return cached or _env_key(pid)


def key_source(pid: str) -> str:
    """"keychain" | "env" | "" (no key)."""
    get_key(pid)
    if (_KEY_CACHE.get(pid) or (0.0, None))[1]:
        return "keychain"
    return "env" if _env_key(pid) else ""


def _account_name(pid: str) -> str:
    if pid in ADMIN_ACCOUNTS:
        return PROVIDERS[ADMIN_ACCOUNTS[pid]].name + " admin"
    return PROVIDERS[pid].name


def set_key(pid: str, key: str) -> None:
    if pid not in PROVIDERS and pid not in ADMIN_ACCOUNTS:
        raise CloudError("cloud_unsupported", f"Unknown provider: {pid}")
    key = (key or "").strip()
    if not _KEY_RE.match(key):
        raise CloudError("cloud_bad_request", "That does not look like an API key (8–512 characters, no spaces).")
    if not _keychain_available():
        raise CloudError("cloud_unsupported", f"No Keychain on this system; set THUNDERTALK_{pid.upper()}_API_KEY instead.")
    r = _keychain(f"add-generic-password -U -s {_quote(_service())} -a {_quote(pid)} "
                  f"-l {_quote('ThunderTalk ' + _account_name(pid))} -w {_quote(key)}")
    if r.returncode != 0 or "error" in (r.stderr or "").lower():
        raise CloudError("cloud_unsupported", "Could not save the key to the Keychain.")
    with _KEY_LOCK:
        _KEY_CACHE[pid] = (time.monotonic(), key)


def remove_key(pid: str) -> bool:
    if pid not in PROVIDERS and pid not in ADMIN_ACCOUNTS:
        raise CloudError("cloud_unsupported", f"Unknown provider: {pid}")
    removed = False
    if _keychain_available():
        r = subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", _service(), "-a", pid],
                           capture_output=True, text=True, timeout=10, check=False)
        removed = r.returncode == 0
    with _KEY_LOCK:
        _KEY_CACHE.pop(pid, None)
    return removed


def forget_cached_keys() -> None:
    with _KEY_LOCK:
        _KEY_CACHE.clear()


def is_configured(pid: str, settings=None) -> bool:
    """Ready to call: a key (when the provider needs one) and a base URL."""
    if pid not in PROVIDERS:
        return False
    if not config(pid, settings)["base_url"]:
        return False
    return bool(get_key(pid)) or not PROVIDERS[pid].key_required


def configured(settings=None) -> list[str]:
    return [pid for pid in PROVIDERS if is_configured(pid, settings)]


# ── HTTP ─────────────────────────────────────────────────────────────────

def _scrub(text: str, key: str) -> str:
    text = text or ""
    if key:
        text = text.replace(key, "***")
    return re.sub(r"(sk-|xi-|gsk_|AIza)[A-Za-z0-9_\-]{8,}", r"\1***", text)


def _headers(pid: str, key: str) -> dict:
    kind = PROVIDERS[pid].kind
    h = {"User-Agent": "ThunderTalk"}
    if not key:
        return h
    if kind == "gemini":
        h["x-goog-api-key"] = key
    elif kind == "elevenlabs":
        h["xi-api-key"] = key
    else:
        h["Authorization"] = f"Bearer {key}"
    return h


def _request(pid: str, url: str, *, data: Optional[bytes] = None, content_type: str = "",
             timeout: float = 60.0, extra: Optional[dict] = None, method: str = "",
             account: str = "") -> bytes:
    key = get_key(account or pid)
    if PROVIDERS[pid].key_required and not key:
        raise CloudError("cloud_no_key", f"No API key for {PROVIDERS[pid].name}. Add one on the Models page "
                         f"or run: thundertalk cloud set-key {pid}")
    headers = _headers(pid, key)
    if content_type:
        headers["Content-Type"] = content_type
    headers.update(extra or {})
    last: Optional[CloudError] = None
    for attempt in range(2):                      # one retry for network errors and 5xx
        req = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if data else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:                     # noqa: BLE001
                body = ""
            detail = _scrub(_error_text(body), key)[:300]
            code = exc.code
            if code in (401, 403):
                raise CloudError("cloud_auth", f"{PROVIDERS[pid].name} rejected the API key ({code}). {detail}".strip()) from None
            if code == 429:
                raise CloudError("cloud_quota", f"{PROVIDERS[pid].name}: rate limit or quota reached. {detail}".strip()) from None
            if code == 413:
                raise CloudError("cloud_too_large", f"{PROVIDERS[pid].name}: the upload is too large.") from None
            if code >= 500 and attempt == 0:
                last = CloudError("cloud_network", f"{PROVIDERS[pid].name} server error ({code}). {detail}".strip())
                time.sleep(1.5)
                continue
            if code >= 500:
                raise CloudError("cloud_network", f"{PROVIDERS[pid].name} server error ({code}). {detail}".strip()) from None
            raise CloudError("cloud_bad_request", f"{PROVIDERS[pid].name} ({code}): {detail or 'request rejected'}") from None
        except TimeoutError:
            raise CloudError("cloud_timeout", f"{PROVIDERS[pid].name} did not answer in {int(timeout)} s.") from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise CloudError("cloud_timeout", f"{PROVIDERS[pid].name} did not answer in {int(timeout)} s.") from None
            last = CloudError("cloud_network", f"Cannot reach {PROVIDERS[pid].name}: {_scrub(str(exc.reason), key)[:160]}")
            if attempt == 0:
                time.sleep(1.0)
                continue
        except OSError as exc:
            last = CloudError("cloud_network", f"Cannot reach {PROVIDERS[pid].name}: {_scrub(str(exc), key)[:160]}")
            if attempt == 0:
                time.sleep(1.0)
                continue
    raise last or CloudError("cloud_network", f"Cannot reach {PROVIDERS[pid].name}.")


def _error_text(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:300]
    err = data.get("error", data.get("detail", data)) if isinstance(data, dict) else data
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or err)
    return str(err)


def _json(raw: bytes, pid: str) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CloudError("cloud_bad_response", f"{PROVIDERS[pid].name} returned something that is not JSON.") from None
    if not isinstance(data, dict):
        raise CloudError("cloud_bad_response", f"{PROVIDERS[pid].name} returned an unexpected answer.")
    return data


def _multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----ThunderTalk" + uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in fields:
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode())
        out.write(str(value).encode("utf-8") + b"\r\n")
    for name, filename, blob, ctype in files:
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                  f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
        out.write(blob + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


# ── speech to text ───────────────────────────────────────────────────────

@dataclass
class Transcription:
    text: str
    language: str = ""
    words: list[tuple[float, float, str]] = field(default_factory=list)


def _encode(samples: np.ndarray, sr: int, fmt: str) -> tuple[bytes, str, str]:
    import soundfile as sf
    buf = io.BytesIO()
    x = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    if fmt == "flac":
        sf.write(buf, x, sr, format="FLAC", subtype="PCM_16")
        return buf.getvalue(), "audio.flac", "audio/flac"
    sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), "audio.wav", "audio/wav"


def _lang(code: str) -> str:
    code = (code or "").strip().lower()
    if code in ("", "auto"):
        return ""
    return {"yue": "zh", "zh-cn": "zh", "zh-tw": "zh"}.get(code, code.split("-")[0].split("_")[0])


_LANG_NAMES = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French",
               "de": "German", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "ru": "Russian"}


def _clean_hotwords(words: list[str], limit: int = 40, max_len: int = 60, max_words: int = 0) -> list[str]:
    out, seen = [], set()
    for w in words or []:
        w = re.sub(r"[<>\r\n]", " ", str(w)).strip()
        if not w or w.lower() in seen or len(w) > max_len or (max_words and len(w.split()) > max_words):
            continue
        seen.add(w.lower())
        out.append(w)
        if len(out) >= limit:
            break
    return out


def _wants_simplified(language: str, settings=None) -> bool:
    """Mandarin should come back in Simplified characters: the dictation language
    says so, or it is auto and the app itself is in Chinese."""
    lang = (language or "auto").strip().lower()
    if lang in ("zh", "zh-cn", "zh-hans", "cmn"):
        return True
    if lang not in ("", "auto"):
        return False
    try:
        return str((settings or _settings()).get("language") or "").startswith("zh")
    except Exception:                             # noqa: BLE001
        return False


_KANA = re.compile(r"[\u3040-\u30ff]")


def to_simplified(text: str) -> str:
    """Traditional → Simplified Chinese with the system's ICU transform
    (Whisper sometimes answers Mandarin in Traditional). Japanese is left alone."""
    if not text or _KANA.search(text) or not re.search(r"[\u4e00-\u9fff]", text):
        return text
    try:
        from Foundation import NSString
        out = NSString.stringWithString_(text).stringByApplyingTransform_reverse_("Hant-Hans", False)
        return str(out) if out else text
    except Exception:                             # noqa: BLE001
        return text


_ZH_STYLE = "以下是普通话和英文混合的句子，使用简体中文和标点符号。"


def _whisper_prompt(words: list[str], simplified: bool, limit: int = 400) -> str:
    """A Whisper-style prompt (it reads it as preceding text, up to 224 tokens)."""
    head = _ZH_STYLE if simplified else ""
    out = head
    for w in words:
        piece = ("" if out == head else ", ") + w
        if len(out) + len(piece) > limit:
            break
        out += piece
    return out.strip()


def transcribe(model_id: str, samples: np.ndarray, sr: int = 16000, *, language: str = "auto",
               hotwords: Optional[list[str]] = None, terms: Optional[list[str]] = None,
               timeout: Optional[float] = None, settings=None, previous: str = "") -> Transcription:
    """One request: ``samples`` (mono float32) → text. The caller chunks long audio.
    ``hotwords`` are the user's; ``terms`` the built-in vocabulary, added where
    it costs nothing extra (ElevenLabs bills keyterms, so only the user's go there)."""
    r = _transcribe(model_id, samples, sr, language=language, hotwords=hotwords, terms=terms,
                    timeout=timeout, settings=settings, previous=(previous or "").strip()[-200:])
    if _wants_simplified(language, settings):
        r.text = to_simplified(r.text)
    from thundertalk.core import cloud_usage
    cloud_usage.record(model_id, seconds=len(samples) / sr if sr else 0.0)
    return r


def _transcribe(model_id: str, samples: np.ndarray, sr: int, *, language: str,
                hotwords: Optional[list[str]], terms: Optional[list[str]],
                timeout: Optional[float], settings, previous: str = "") -> Transcription:
    pid, model = split_id(model_id)
    if not pid:
        raise CloudError("cloud_unsupported", f"Not a cloud model: {model_id}")
    p = PROVIDERS[pid]
    cfg = config(pid, settings)
    if not cfg["base_url"]:
        raise CloudError("cloud_bad_request", "Set the base URL for the OpenAI-compatible endpoint first.")
    duration = len(samples) / sr if sr else 0.0
    timeout = timeout or min(300.0, 25.0 + duration * 0.6)
    blob, filename, ctype = _encode(samples, sr, p.upload)
    lang = _lang(language)
    simplified = _wants_simplified(language, settings)
    user = list(hotwords or []) if p.hotwords else []
    words = _clean_hotwords(user + list(terms or []), limit=100) if p.hotwords else []
    if p.kind == "openai":
        fields = [("model", model), ("response_format", "json")]
        if model.startswith("gpt-transcribe"):
            if lang:
                fields.append(("languages[]", lang))
            fields += [("keywords[]", w) for w in words]
            head = (_ZH_STYLE if simplified else "") + previous
            if head:
                fields.append(("prompt", head))
        else:
            if lang:
                fields.append(("language", lang))
            prompt = _whisper_prompt(words, simplified)
            if previous:
                # Whisper reads the prompt as the text before this audio.
                prompt = (prompt + " " + previous).strip()[-450:] if prompt else previous
            if prompt:
                fields.append(("prompt", prompt))
        body, ct = _multipart(fields, [("file", filename, blob, ctype)])
        data = _json(_request(pid, cfg["base_url"] + "/audio/transcriptions", data=body,
                              content_type=ct, timeout=timeout), pid)
        text = data.get("text")
        if not isinstance(text, str):
            raise CloudError("cloud_bad_response", f"{p.name} returned no transcript.")
        langs = data.get("languages") or []
        found = data.get("language") or (langs[0].get("code") if langs and isinstance(langs[0], dict) else "")
        return Transcription(text.strip(), str(found or ""))
    if p.kind == "elevenlabs":
        fields = [("model_id", model), ("tag_audio_events", "false")]
        if lang:
            fields.append(("language_code", lang))
        # Keyterms add 20% to the price, so only the user's own hotwords go here.
        fields += [("keyterms", w) for w in _clean_hotwords(user, limit=100, max_len=49, max_words=5)]
        body, ct = _multipart(fields, [("file", filename, blob, ctype)])
        data = _json(_request(pid, cfg["base_url"] + "/speech-to-text", data=body,
                              content_type=ct, timeout=timeout), pid)
        text = data.get("text")
        if not isinstance(text, str):
            raise CloudError("cloud_bad_response", f"{p.name} returned no transcript.")
        ws = [(float(w["start"]), float(w["end"]), str(w.get("text", "")).strip())
              for w in data.get("words") or [] if isinstance(w, dict) and w.get("type") == "word"
              and w.get("start") is not None and w.get("end") is not None]
        return Transcription(text.strip(), str(data.get("language_code") or ""), ws)
    # Gemini: a prompt plus the audio, through the Interactions API.
    prompt = ("Transcribe this audio verbatim in the language it is spoken in. Output only the "
              "transcript with natural punctuation: no timestamps, labels, notes or translation. "
              "If there is no speech, output nothing.")
    if lang:
        prompt += f" The speech is in {_LANG_NAMES.get(lang, lang)}."
    if simplified:
        prompt += " Write Mandarin in Simplified Chinese characters."
    if previous:
        prompt += f" The audio continues this transcript (do not repeat it): «{previous}»."
    if words:
        prompt += " Spell these terms exactly when they occur: " + ", ".join(words) + "."
    payload = {"model": model, "input": [
        {"type": "text", "text": prompt},
        {"type": "audio", "data": base64.b64encode(blob).decode("ascii"), "mime_type": ctype},
    ]}
    data = _json(_request(pid, cfg["base_url"] + "/interactions", data=json.dumps(payload).encode(),
                          content_type="application/json", timeout=timeout,
                          extra={"Api-Revision": GEMINI_API_REVISION}), pid)
    return Transcription(_gemini_text(data, p.name).strip())


def _gemini_outputs(data: dict) -> list[dict]:
    items: list[dict] = []
    for step in data.get("steps") or []:
        if isinstance(step, dict) and step.get("type") in ("model_output", None):
            items += [c for c in step.get("content") or [] if isinstance(c, dict)]
    for c in data.get("outputs") or []:                       # older Interactions shape
        if isinstance(c, dict):
            items.append(c)
    for cand in data.get("candidates") or []:                 # generateContent shape
        for part in ((cand or {}).get("content") or {}).get("parts") or []:
            if "text" in part:
                items.append({"type": "text", "text": part["text"]})
            inline = part.get("inlineData") or part.get("inline_data")
            if inline:
                items.append({"type": "audio", "data": inline.get("data"),
                              "mime_type": inline.get("mimeType") or inline.get("mime_type")})
    return items


def _gemini_text(data: dict, name: str) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    texts = [c.get("text") for c in _gemini_outputs(data) if c.get("type") == "text" and isinstance(c.get("text"), str)]
    if not texts:
        raise CloudError("cloud_bad_response", f"{name} returned no transcript.")
    return "".join(texts)


# ── text to speech ───────────────────────────────────────────────────────

def _decode_audio(blob: bytes, mime: str, rate: int = TTS_RATE) -> np.ndarray:
    """WAV/FLAC/MP3 (anything soundfile reads), or raw 16-bit PCM for audio/l16 and pcm_*."""
    from thundertalk.core import audio_io
    mime = (mime or "").lower()
    if "l16" in mime or mime.startswith("audio/pcm") or mime == "pcm":
        m = re.search(r"rate=(\d+)", mime)
        sr = int(m.group(1)) if m else rate
        x = np.frombuffer(blob[: len(blob) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    else:
        import soundfile as sf
        try:
            x, sr = sf.read(io.BytesIO(blob), dtype="float32", always_2d=False)
        except Exception as exc:                  # noqa: BLE001
            raise CloudError("cloud_bad_response", f"Could not read the returned audio ({type(exc).__name__}).") from None
        if x.ndim > 1:
            x = x.mean(axis=1)
    if len(x) == 0:
        raise CloudError("cloud_bad_response", "The provider returned empty audio.")
    return audio_io.resample(x, sr, TTS_RATE) if sr != TTS_RATE else x.astype(np.float32)


def synthesize(pid: str, text: str, voice: str, *, model: str = "", timeout: float = 90.0,
               settings=None) -> np.ndarray:
    """One request: ``text`` (a sentence or a few) → mono float32 at 24 kHz."""
    audio = _synthesize(pid, text, voice, model=model, timeout=timeout, settings=settings)
    from thundertalk.core import cloud_usage
    cloud_usage.record(f"{pid}:{model or config(pid, settings)['tts_model']}", kind="tts",
                       chars=len(text), seconds=len(audio) / TTS_RATE)
    return audio


def _synthesize(pid: str, text: str, voice: str, *, model: str, timeout: float, settings) -> np.ndarray:
    if pid not in PROVIDERS or not PROVIDERS[pid].tts_models:
        raise CloudError("cloud_unsupported", f"{pid} has no text-to-speech")
    p = PROVIDERS[pid]
    cfg = config(pid, settings)
    if not cfg["base_url"]:
        raise CloudError("cloud_bad_request", "Set the base URL for the OpenAI-compatible endpoint first.")
    model = model or cfg["tts_model"]
    voice = (voice or cfg["voice"] or (p.voices[0] if p.voices else "")).strip()
    if not voice:
        raise CloudError("cloud_bad_request", f"Choose a {p.name} voice first.")
    if p.kind == "openai":
        payload = {"model": model, "input": text, "voice": voice, "response_format": "wav"}
        blob = _request(pid, cfg["base_url"] + "/audio/speech", data=json.dumps(payload).encode(),
                        content_type="application/json", timeout=timeout)
        return _decode_audio(blob, "audio/wav")
    if p.kind == "elevenlabs":
        url = (cfg["base_url"] + "/text-to-speech/" + urllib.parse.quote(voice, safe="")
               + "?output_format=pcm_24000")
        blob = _request(pid, url, data=json.dumps({"text": text, "model_id": model}).encode(),
                        content_type="application/json", timeout=timeout)
        return _decode_audio(blob, "audio/l16;rate=24000")
    payload = {
        "model": model,
        "input": [{"type": "user_input", "content": [{"type": "text", "text": text}]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    data = _json(_request(pid, cfg["base_url"] + "/interactions", data=json.dumps(payload).encode(),
                          content_type="application/json", timeout=timeout,
                          extra={"Api-Revision": GEMINI_API_REVISION}), pid)
    audio = [c for c in _gemini_outputs(data) if c.get("type") == "audio" and c.get("data")]
    if not audio:
        raise CloudError("cloud_bad_response", f"{p.name} returned no audio.")
    try:
        blob = base64.b64decode(audio[-1]["data"])
    except (ValueError, TypeError):
        raise CloudError("cloud_bad_response", f"{p.name} returned unreadable audio.") from None
    return _decode_audio(blob, audio[-1].get("mime_type") or "audio/wav")


_VOICE_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _in_gui() -> bool:
    """True inside the app's Qt process: never block it on the network."""
    qt = sys.modules.get("PySide6.QtWidgets")
    return bool(qt is not None and qt.QApplication.instance() is not None)


def voices(pid: str, refresh: bool = False, settings=None, blocking: Optional[bool] = None) -> list[dict]:
    """[{"id": "<pid>:<voice>", "name", "language", "gender"}]. Built-in lists for
    OpenAI and Gemini; ElevenLabs asks the account (cached 10 min); custom offers
    the configured voice (any name is accepted). In the app (``blocking`` unset)
    ElevenLabs answers from the cache and refreshes it in the background."""
    p = PROVIDERS[pid]
    if p.voices:
        return [{"id": f"{pid}:{v}", "name": v, "language": "multi", "gender": ""} for v in p.voices]
    if pid == "custom":
        v = config(pid, settings)["voice"] or "alloy"
        return [{"id": f"custom:{v}", "name": v, "language": "multi", "gender": ""}]
    if pid == "elevenlabs":
        hit = _VOICE_CACHE.get(pid)
        if hit and not refresh and time.monotonic() - hit[0] < 600:
            return hit[1]
        if not is_configured(pid, settings):
            return []
        if blocking is False or (blocking is None and _in_gui()):
            _prefetch_voices(pid)
            stale = hit[1] if hit else []
            chosen = config(pid, settings)["voice"]
            if chosen and not any(r["id"] == f"elevenlabs:{chosen}" for r in stale):
                stale = [{"id": f"elevenlabs:{chosen}", "name": chosen, "language": "multi", "gender": ""}, *stale]
            return stale
        data = _json(_request(pid, config(pid, settings)["base_url"] + "/voices", timeout=15), pid)
        rows = []
        for v in data.get("voices") or []:
            if not isinstance(v, dict) or not v.get("voice_id"):
                continue
            labels = v.get("labels") or {}
            g = str(labels.get("gender", "")).lower()
            rows.append({"id": f"elevenlabs:{v['voice_id']}", "name": str(v.get("name") or v["voice_id"]),
                         "language": "multi", "gender": "f" if g.startswith("f") else "m" if g.startswith("m") else ""})
        _VOICE_CACHE[pid] = (time.monotonic(), rows)
        return rows
    return []


_PREFETCHING: set[str] = set()


def _prefetch_voices(pid: str) -> None:
    with _KEY_LOCK:
        if pid in _PREFETCHING:
            return
        _PREFETCHING.add(pid)

    def run() -> None:
        try:
            voices(pid, refresh=True, blocking=True)
        except CloudError as exc:
            print(f"[Cloud] {pid} voices: {exc}")
        finally:
            with _KEY_LOCK:
                _PREFETCHING.discard(pid)
    threading.Thread(target=run, name=f"cloud-voices-{pid}", daemon=True).start()


# ── checks ───────────────────────────────────────────────────────────────

def test(pid: str, settings=None) -> dict:
    """Cheapest authenticated call that proves the key and URL work."""
    p = PROVIDERS[pid]
    cfg = config(pid, settings)
    if not cfg["base_url"]:
        raise CloudError("cloud_bad_request", "Set the base URL first.")
    t0 = time.monotonic()
    if p.kind == "elevenlabs":
        _request(pid, cfg["base_url"] + "/voices", timeout=15)
        return {"provider": pid, "ok": True, "seconds": round(time.monotonic() - t0, 2)}
    raw = _request(pid, cfg["base_url"] + "/models", timeout=15)
    out = {"provider": pid, "ok": True, "seconds": round(time.monotonic() - t0, 2)}
    if pid == "custom":
        # Remember what the server offers so the model pickers can list it.
        try:
            ids = [str(m["id"]) for m in json.loads(raw.decode("utf-8")).get("data") or []
                   if isinstance(m, dict) and m.get("id")]
        except (ValueError, UnicodeDecodeError, AttributeError):
            ids = []
        s = settings or _settings()
        stored = dict(s.get("cloud") or {})
        entry = dict(stored.get(pid) or {})
        entry["models"] = ids[:200]
        stored[pid] = entry
        s.set("cloud", stored)
        out["models"] = len(ids)
    return out


def summary(settings=None) -> list[dict]:
    """Providers for status/list output. Never includes a key."""
    rows = []
    for pid, p in PROVIDERS.items():
        cfg = config(pid, settings)
        rows.append({"provider": pid, "name": p.name, "key": key_source(pid) or "none",
                     "ready": is_configured(pid, settings), "base_url": cfg["base_url"],
                     "asr_models": model_choices(pid, "asr", settings) if p.asr_models else [],
                     "tts_models": model_choices(pid, "tts", settings) if p.tts_models else [],
                     "asr_model": cfg["asr_model"], "tts_model": cfg["tts_model"],
                     "retiring": {m: d for m, d in RETIRING.items() if m.startswith(pid + ":")}})
    return rows


# ── model catalog entries for the ASR side ───────────────────────────────

def model_info(model_id: str, settings=None):
    """A ``models.ModelInfo`` for a cloud ASR id, or None."""
    pid, model = split_id(model_id)
    if not pid or not PROVIDERS[pid].asr_models:
        return None
    from thundertalk.core.models import ModelInfo
    p = PROVIDERS[pid]
    return ModelInfo(
        id=f"{pid}:{model}", family=f"Cloud-{p.name}", name=p.name, variant=model, backend="cloud",
        size_mb=0, language_count=0, languages_complete=False, accuracy_stars=0, download_url="",
        speed="cloud", hotword_support=p.hotwords, platform="all", max_clip_seconds=p.clip_seconds,
        max_hotword_bytes=1024, notes=f"Cloud · sends audio to {p.name}",
    )


def asr_models(settings=None, only_ready: bool = True) -> list:
    out = []
    for pid in ASR_PROVIDERS:
        if only_ready and not is_configured(pid, settings):
            continue
        cfg = config(pid, settings)
        names = model_choices(pid, "asr", settings)
        if cfg["asr_model"] and cfg["asr_model"] not in names:
            names.insert(0, cfg["asr_model"])
        out += [model_info(f"{pid}:{m}", settings) for m in names]
    return out
