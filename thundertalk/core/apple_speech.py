"""Apple's built-in speech recognition (Speech framework), on device only.

Nothing to download: macOS ships the recognizer, and the dictation assets for
a language are installed with it (System Settings ▸ Keyboard ▸ Dictation).
``requiresOnDeviceRecognition`` is always set, so audio never leaves the Mac;
a locale whose assets are not installed is reported, not sent to Apple.

Measured behaviour that shapes this module (macOS 15.6):

  * after every pause the recognizer starts a new utterance and the *final*
    result holds only the last one (a 4.7-minute file came back empty), so
    every result that carries ``speechRecognitionMetadata`` is collected;
  * even so, one request over a long file silently skips whole sentences
    after pauses, so audio is recognised in 8–12 s pieces cut at pauses;
  * there is no language auto-detection. In ``auto`` the candidate locales are
    tried in turn and the more confident result wins (right locale 0.57–0.87,
    wrong one 0.0–0.36 on our clips). Never side by side: the on-device
    service serves one request at a time and a concurrent one comes back
    empty;
  * results are delivered on the recognizer's operation queue (the main queue
    unless set), so each recognizer gets its own queue and callers can block;
  * asking for permission from a process without
    ``NSSpeechRecognitionUsageDescription`` in its Info.plist aborts it
    (SIGABRT), so the request is only made from the app bundle.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

USAGE_KEY = "NSSpeechRecognitionUsageDescription"
_STATUS = {0: "not_determined", 1: "denied", 2: "restricted", 3: "authorized"}
# Preferred locale per language code, in order; the first one installed for
# on-device recognition is used.
_PREFERRED = {
    "zh": ("zh-CN", "zh-TW", "zh-HK"),
    "en": ("en-US", "en-GB", "en-AU", "en-CA", "en-IN"),
    "yue": ("zh-HK", "yue-CN"),
    "ja": ("ja-JP",), "ko": ("ko-KR",), "fr": ("fr-FR", "fr-CA"), "de": ("de-DE",),
    "es": ("es-ES", "es-MX", "es-US"), "it": ("it-IT",), "pt": ("pt-BR", "pt-PT"),
    "ru": ("ru-RU",), "nl": ("nl-NL",), "tr": ("tr-TR",), "ar": ("ar-SA",), "hi": ("hi-IN",),
}
AUTO_LANGUAGES = ("zh", "en")
# One request over a whole file skips stretches of speech after pauses (a
# 23 s message lost two sentences); 8–12 s pieces cut at pauses keep them.
CHUNK_TARGET_S = 8.0
CHUNK_MAX_S = 12.0
_NO_SPEECH_CODES = {1110, 203}            # "No speech detected" / nothing recognised
CONFIDENT = 0.5                           # a locale this sure of a piece is not second-guessed
SWITCH_MARGIN = 0.2
PROBE_S = 4.0                             # auto language: decided on this much of a longer first piece
PROBE_MAX_S = 6.0
# The on-device service runs one request at a time: of two concurrent ones,
# one came back empty in 3 of 4 tries. All requests in the process queue here.
_REQUEST_LOCK = threading.Lock()


class AppleSpeechError(RuntimeError):
    """``code`` is stable ("unavailable", "permission_denied", "locale_unsupported",
    "failed"); the message is for people."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class Word:
    start: float
    end: float
    text: str
    confidence: float = 0.0


@dataclass
class AppleResult:
    text: str
    words: list[Word] = field(default_factory=list)
    locale: str = ""
    confidence: float = 0.0               # duration-weighted mean over words
    seconds: float = 0.0


# ── availability and permission ──────────────────────────────────────────

def _speech():
    if sys.platform != "darwin":
        raise AppleSpeechError("unavailable", "Apple speech recognition needs macOS")
    try:
        import Speech  # noqa: F401
        return Speech
    except ImportError as exc:
        raise AppleSpeechError("unavailable", f"Speech framework binding missing: {exc}") from exc


def available() -> bool:
    try:
        _speech()
        return True
    except AppleSpeechError:
        return False


def authorization_status() -> str:
    try:
        return _STATUS.get(int(_speech().SFSpeechRecognizer.authorizationStatus()), "unknown")
    except AppleSpeechError:
        return "unavailable"


def _responsible_for_self() -> bool:
    """TCC checks the usage string of the *responsible* process: started from
    a terminal that is the terminal app, whose Info.plist lacks it, and the
    request aborts this process even inside the bundle."""
    import ctypes
    try:
        fn = ctypes.CDLL(None).responsibility_get_pid_responsible_for_pid
    except (OSError, AttributeError):
        return False
    fn.restype, fn.argtypes = ctypes.c_int, [ctypes.c_int]
    return fn(os.getpid()) == os.getpid()


def can_request_permission() -> bool:
    """True inside the app bundle launched as an app (Finder, Dock, login
    item), whose Info.plist explains the request. Recognition still works
    while the status is "not determined", so not asking costs nothing."""
    try:
        from Foundation import NSBundle
        has_key = NSBundle.mainBundle().objectForInfoDictionaryKey_(USAGE_KEY) is not None
    except Exception:
        return False
    return has_key and _responsible_for_self()


PROMPT = True                             # the CLI turns this off: nobody may be there to answer


def request_permission(timeout: float = 120.0) -> str:
    """Show the system prompt once (macOS remembers the answer) and return the
    new status. Without the Info.plist key the current status is returned."""
    status = authorization_status()
    if status != "not_determined" or not PROMPT or not can_request_permission():
        return status
    done = threading.Event()
    _speech().SFSpeechRecognizer.requestAuthorization_(lambda _s: done.set())
    done.wait(timeout)
    return authorization_status()


def open_privacy_settings() -> None:
    import subprocess
    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition"],
                   check=False)


def open_dictation_settings() -> None:
    import subprocess
    subprocess.run(["open", "x-apple.systempreferences:com.apple.Keyboard-Settings.extension?Dictation"],
                   check=False)


# ── locales ──────────────────────────────────────────────────────────────

_LOCALE_CACHE: dict[str, list[str]] = {}
_LOCALE_LOCK = threading.Lock()


def _recognizer_for(locale: str):
    from Foundation import NSLocale
    return _speech().SFSpeechRecognizer.alloc().initWithLocale_(NSLocale.localeWithLocaleIdentifier_(locale))


def supported_locales() -> list[str]:
    """Every locale the recognizer knows (on device or not)."""
    with _LOCALE_LOCK:
        if "all" not in _LOCALE_CACHE:
            _LOCALE_CACHE["all"] = sorted(str(loc.localeIdentifier())
                                          for loc in _speech().SFSpeechRecognizer.supportedLocales())
        return list(_LOCALE_CACHE["all"])


def on_device_locales() -> list[str]:
    """Locales whose dictation assets are installed (checked once per process)."""
    with _LOCALE_LOCK:
        cached = _LOCALE_CACHE.get("device")
    if cached is not None:
        return list(cached)
    out = []
    for loc in supported_locales():
        r = _recognizer_for(loc)
        if r is not None and r.supportsOnDeviceRecognition():
            out.append(loc)
    with _LOCALE_LOCK:
        _LOCALE_CACHE["device"] = out
    return list(out)


def language_of(locale: str) -> str:
    """Language code for a locale, in the Models page's vocabulary."""
    if locale in ("zh-HK", "yue-CN"):
        return "yue"
    if locale == "zh-TW":
        return "cmn_Hant"
    return locale.split("-")[0].split("_")[0]


def on_device_languages() -> list[str]:
    seen: list[str] = []
    for loc in on_device_locales():
        code = language_of(loc)
        if code not in seen:
            seen.append(code)
    order = {"zh": 0, "en": 1, "cmn_Hant": 2, "yue": 3, "ja": 4}
    return sorted(seen, key=lambda c: (order.get(c, 9), c))


def resolve_locale(language: str) -> str:
    """An installed on-device locale for ``language`` ("zh", "en", "zh-TW", …)."""
    installed = on_device_locales()
    lang = (language or "").replace("_", "-")
    if lang in installed:
        return lang
    base = lang.split("-")[0].lower()
    for loc in _PREFERRED.get(base, ()):
        if loc in installed:
            return loc
    for loc in installed:
        if loc.split("-")[0].lower() == base:
            return loc
    raise AppleSpeechError(
        "locale_unsupported",
        f"No on-device Apple speech model for '{language}'. Installed: {', '.join(installed) or 'none'}. "
        "Add the language under System Settings ▸ Keyboard ▸ Dictation ▸ Languages.")


def auto_locales() -> list[str]:
    out = []
    for lang in AUTO_LANGUAGES:
        try:
            out.append(resolve_locale(lang))
        except AppleSpeechError:
            pass
    return out or on_device_locales()[:1]


# ── recognition ──────────────────────────────────────────────────────────

def _join(parts: list[str], cjk: bool) -> str:
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out:
            if out[-1] not in "。！？；，、,.!?;:：":
                out += "，" if cjk else ("." if p[:1].isupper() else ",")
            if not cjk:
                out += " "
        out += p
    return out


class AppleRecognizer:
    """One recognizer per locale, each with its own result queue. Calls block
    until the task finishes; any thread may call."""

    def __init__(self) -> None:
        self._by_locale: dict = {}
        self._lock = threading.Lock()
        self._last_locale = ""

    def _get(self, locale: str):
        with self._lock:
            entry = self._by_locale.get(locale)
            if entry is None:
                from Foundation import NSOperationQueue
                r = _recognizer_for(locale)
                if r is None:
                    raise AppleSpeechError("locale_unsupported", f"Unknown locale: {locale}")
                q = NSOperationQueue.alloc().init()
                q.setMaxConcurrentOperationCount_(1)
                r.setQueue_(q)
                entry = self._by_locale[locale] = (r, q)
            return entry[0]

    def recognize_file(self, path: str, locale: str, *, contextual: Sequence[str] = (),
                       duration: float = 0.0, dictation: bool = False) -> AppleResult:
        Speech = _speech()
        from Foundation import NSURL
        status = authorization_status()
        if status in ("denied", "restricted"):
            raise AppleSpeechError("permission_denied", "Speech Recognition access is turned off for ThunderTalk.")
        rec = self._get(locale)
        if not rec.supportsOnDeviceRecognition():
            raise AppleSpeechError("locale_unsupported", f"{locale} is not installed for on-device recognition.")
        req = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
        req.setRequiresOnDeviceRecognition_(True)
        req.setShouldReportPartialResults_(False)
        req.setAddsPunctuation_(True)
        req.setTaskHint_(1 if dictation else 0)       # SFSpeechRecognitionTaskHintDictation / unspecified
        if contextual:
            req.setContextualStrings_(list(contextual)[:100])
        done = threading.Event()
        utterances: dict[float, tuple[str, list[Word]]] = {}
        failure: list = []

        def handler(result, error) -> None:
            try:
                if error is not None:
                    failure.append((str(error.domain()), int(error.code()), str(error.localizedDescription())))
                    done.set()
                    return
                if result is None:
                    return
                if result.speechRecognitionMetadata() is not None or result.isFinal():
                    best = result.bestTranscription()
                    words = [Word(float(s.timestamp()), float(s.timestamp() + s.duration()),
                                  str(s.substring()), float(s.confidence())) for s in best.segments()]
                    words = [w for w in words if w.text.strip()]
                    if words:                  # keyed by start: a repeated final replaces its utterance
                        utterances[round(words[0].start, 2)] = (str(best.formattedString()), words)
                if result.isFinal():
                    done.set()
            except Exception as exc:          # never let an exception escape into the ObjC queue
                failure.append(("python", -1, str(exc)))
                done.set()

        with _REQUEST_LOCK:
            t0 = time.monotonic()
            task = rec.recognitionTaskWithRequest_resultHandler_(req, handler)
            if not done.wait(60.0 + 2.0 * duration):
                task.cancel()
                raise AppleSpeechError("failed", "Apple speech recognition timed out")
            seconds = time.monotonic() - t0
        if failure and not utterances:
            domain, code, msg = failure[0]
            if code in _NO_SPEECH_CODES or "no speech" in msg.lower():
                return AppleResult("", [], locale, 0.0, seconds)
            if authorization_status() in ("denied", "restricted"):
                raise AppleSpeechError("permission_denied", msg)
            raise AppleSpeechError("failed", f"{msg} ({domain} {code})")
        ordered = [utterances[k] for k in sorted(utterances)]
        words = [w for _text, ws in ordered for w in ws]
        span = sum(max(w.end - w.start, 0.01) for w in words) or 1.0
        conf = sum(w.confidence * max(w.end - w.start, 0.01) for w in words) / span
        cjk = locale.startswith(("zh", "ja", "ko", "yue"))
        return AppleResult(_join([t for t, _w in ordered], cjk), words, locale, conf, seconds)

    def _recognize_chunk(self, samples: np.ndarray, sample_rate: int, locale: str,
                         contextual: Sequence[str], dictation: bool) -> AppleResult:
        from thundertalk.core import audio_io
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="tt-apple-")
        os.close(fd)
        try:
            audio_io.write_wav(tmp, samples, sample_rate)
            return self.recognize_file(tmp, locale, contextual=contextual,
                                       duration=len(samples) / sample_rate, dictation=dictation)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def recognize(self, samples: np.ndarray, sample_rate: int, locales: Sequence[str], *,
                  contextual: Sequence[str] = (), dictation: bool = False) -> AppleResult:
        """Recognise ``samples`` in pieces of at most ``CHUNK_MAX_S`` cut at
        pauses. With several ``locales`` (auto) the language is chosen on the
        first piece: the last winner is tried first and kept when it is
        confident, otherwise the others are tried and one replaces it only when
        clearly more confident; the choice holds for the rest."""
        from thundertalk.core.transcribe import _cjk_heavy, _has_speech, segment_speech
        if not locales:
            raise AppleSpeechError("locale_unsupported", "No on-device Apple speech language is installed.")
        dur = len(samples) / sample_rate
        spans = (segment_speech(samples, sample_rate, target=CHUNK_TARGET_S, max_len=CHUNK_MAX_S, min_gap=0.25)
                 if dur > CHUNK_MAX_S else [(0.0, dur)])
        pad = int(0.15 * sample_rate)

        def piece(a: float, b: float) -> np.ndarray:
            return samples[max(0, int(a * sample_rate) - pad): min(len(samples), int(b * sample_rate) + pad)]

        def once(x: np.ndarray, loc: str) -> AppleResult:
            r = self._recognize_chunk(x, sample_rate, loc, contextual, dictation)
            if not r.text.strip() and len(x) > sample_rate and _has_speech(x, sample_rate):
                again = self._recognize_chunk(x, sample_rate, loc, contextual, dictation)
                again.seconds += r.seconds
                r = again
            return r

        order = sorted(locales, key=lambda loc: loc != self._last_locale)
        locale, results = order[0], []
        for i, (a, b) in enumerate(spans):
            x = piece(a, b)
            if i == 0 and len(order) > 1:
                # Decoding in the wrong language takes 4–7× longer than in the
                # right one, so the language is picked on the first seconds
                # and a second try is avoided when the first is plausible:
                # zh-CN writes English speech in Latin letters, so Chinese
                # characters out of it mean the audio is Chinese.
                probe = x if len(x) <= PROBE_MAX_S * sample_rate else x[:int(PROBE_S * sample_rate)]

                def cjk_out(t: AppleResult) -> bool:
                    return t.locale.startswith(("zh", "ja", "ko", "yue")) and _cjk_heavy(t.text)

                tries = [once(probe, order[0])]
                if not (tries[0].text.strip() and (tries[0].confidence >= CONFIDENT or cjk_out(tries[0]))):
                    tries += [once(probe, loc) for loc in order[1:]]
                # A wrong locale still returns words (Chinese through en-US),
                # at a confidence ≥ 0.2 below the right one's; switch only on
                # that kind of margin, on Chinese characters, or when the
                # first heard nothing at all (noisy English through zh-CN).
                r = tries[0]
                spoken = [t for t in tries[1:] if t.text.strip()]
                other = next((t for t in spoken if cjk_out(t)), None) or max(
                    spoken, key=lambda t: t.confidence, default=None)
                if other is not None and (not r.text.strip() or cjk_out(other)
                                          or other.confidence >= max(r.confidence + SWITCH_MARGIN, SWITCH_MARGIN)):
                    r = other
                spent = sum(t.seconds for t in tries)
                locale = r.locale
                if probe is not x:
                    r = once(x, locale)
                    spent += r.seconds
                r.seconds = spent
                if r.text.strip():
                    self._last_locale = locale
            else:
                r = once(x, locale)
            offset = max(0.0, a - pad / sample_rate)
            for w in r.words:
                w.start += offset
                w.end += offset
            results.append(r)
        words = [w for r in results for w in r.words]
        span = sum(max(w.end - w.start, 0.01) for w in words) or 1.0
        conf = sum(w.confidence * max(w.end - w.start, 0.01) for w in words) / span
        cjk = locale.startswith(("zh", "ja", "ko", "yue"))
        return AppleResult(_join([r.text for r in results], cjk), words, locale, conf,
                           sum(r.seconds for r in results))
