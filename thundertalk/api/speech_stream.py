"""Streaming speech synthesis for ``POST /v1/audio/speech``.

Text is cut into sentence pieces (``tts.plan_sentences``, the Speak page's own
splitter) and each piece is sent as soon as it is synthesized, so a voice
agent hears the first words while the rest is still being made. The first
sentence is cut once more at its first clause when it is long, because the
time to the first byte is the time to synthesize the first piece: Kokoro
speaks a 5-word clause in about 0.3 s on an M3 Max but needs about 0.5 s for a
13-word sentence.

This path speaks each piece once: no read-back check, retakes or loudness
matching (those belong to Studio and multiply the time to first audio).

Engines: Kokoro (sherpa-onnx, CPU, 24 kHz) is the default. The other local
engines (VoxCPM2, IndexTTS, LongCat) work through the same backend interface
when downloaded, but take seconds per sentence on the GPU.
"""

from __future__ import annotations

import re
import struct
import threading
from typing import Callable, Iterator, Optional

import numpy as np

LOCAL_ENGINES = ("kokoro", "voxcpm2", "indextts", "longcat")
DEFAULT_ENGINE = "kokoro"
KOKORO_THREADS = 8            # measured: 4 → 8 threads cut a 13-word sentence 0.77 → 0.50 s
# OpenAI's model and voice names, so OpenAI clients work unchanged: they map
# to the server's default engine and that engine's default voice.
OPENAI_MODELS = ("tts-1", "tts-1-hd", "gpt-4o-mini-tts")
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse")
FORMATS = ("pcm", "wav")
MIN_SPEED, MAX_SPEED = 0.5, 2.0
MAX_INPUT_CHARS = 4096        # OpenAI's limit

# First piece: cut a long first sentence at a clause so the first byte comes sooner.
FIRST_MAX_WORDS = 7           # English: longer first sentences are cut
FIRST_MIN_WORDS = 3
FIRST_MAX_HAN = 12            # Chinese
FIRST_MIN_HAN = 4
CLAUSE_PAUSE = 0.16           # the pipeline's pause inside a split sentence
_CLAUSE_END = re.compile(r"[,，、;；:：—–]\s*$")

_WORD = re.compile(r"[A-Za-z0-9À-ɏ']+")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_CLAUSE = re.compile(r"[,，、;；:：—]+\s*")
# A cut before these words keeps a phrase together when there is no comma.
_BREAK_BEFORE = {"and", "but", "so", "because", "which", "that", "when", "while", "if", "or", "to", "with",
                 "for", "in", "on", "at", "from", "about", "after", "before", "since", "until", "then"}


class SpeechError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# ── text → pieces ────────────────────────────────────────────────────────

def _size(text: str, cjk: bool) -> int:
    return len(_HAN.findall(text)) if cjk else len(_WORD.findall(text))


def split_first(sentence: str, lang: str) -> list[str]:
    """``sentence`` as one or two pieces: a long first sentence is cut at its
    first clause mark (or, in English with no comma, before a connective)."""
    cjk = lang in ("chinese", "japanese", "korean")
    lo, hi = (FIRST_MIN_HAN, FIRST_MAX_HAN) if cjk else (FIRST_MIN_WORDS, FIRST_MAX_WORDS)
    if _size(sentence, cjk) <= hi:
        return [sentence]
    for m in _CLAUSE.finditer(sentence):
        head, rest = sentence[:m.end()].strip(), sentence[m.end():].strip()
        if _size(head, cjk) > hi + (4 if not cjk else 6):
            break
        if _size(head, cjk) >= lo and _size(rest, cjk) >= lo:
            return [head, rest]
    if cjk:
        return [sentence]             # no clause mark: never cut Chinese mid-phrase
    words = list(_WORD.finditer(sentence))
    for k in range(FIRST_MIN_WORDS + 1, min(len(words) - FIRST_MIN_WORDS + 1, FIRST_MAX_WORDS)):
        if words[k].group(0).lower() in _BREAK_BEFORE:
            cut = words[k].start()
            return [sentence[:cut].strip(), sentence[cut:].strip()]
    return [sentence]


def plan_pieces(text: str, lang: str) -> list[tuple[str, float]]:
    """(piece, pause after it in seconds); the last piece's pause is 0."""
    from thundertalk.core import tts
    pieces = tts.plan_sentences(text, lang)
    if not pieces:
        return []
    first, pause = pieces[0]
    parts = split_first(first, lang)
    # A cut at a clause mark gets the comma's pause; a cut before a connective
    # ("… calendar | and there is …") has no punctuation and gets none.
    out = [(p, CLAUSE_PAUSE if _CLAUSE_END.search(p) else 0.0) for p in parts[:-1]] + [(parts[-1], float(pause))] + [
        (s, float(p)) for s, p in pieces[1:]]
    out[-1] = (out[-1][0], 0.0)
    return out


# ── audio encoding ───────────────────────────────────────────────────────

def pcm16(x: np.ndarray) -> bytes:
    return (np.clip(np.asarray(x, np.float32), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def wav_header(sample_rate: int) -> bytes:
    """A WAV header for a stream of unknown length (sizes 0xFFFFFFFF, as
    ffmpeg writes when it cannot seek back); readers play to the end."""
    unknown = 0xFFFFFFFF
    return (b"RIFF" + struct.pack("<I", unknown) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
            + b"data" + struct.pack("<I", unknown))


# ── engines ──────────────────────────────────────────────────────────────

class TtsHost:
    """The server's speech engines: which are ready, voices, synthesis."""

    def __init__(self, default_engine: str = DEFAULT_ENGINE, backend_factory: Optional[Callable] = None) -> None:
        if default_engine not in LOCAL_ENGINES:
            raise SpeechError(400, f"Unknown TTS engine '{default_engine}'. Use one of: {', '.join(LOCAL_ENGINES)}")
        self.default_engine = default_engine
        self._factory = backend_factory or self._make
        self._backends: dict = {}
        self._mu = threading.Lock()
        self._run = threading.Lock()          # one synthesis at a time (backends are single-caller)

    @staticmethod
    def _make(bid: str):
        if bid == "kokoro":
            from thundertalk.core.tts_backends.kokoro import KokoroBackend
            return KokoroBackend(num_threads=KOKORO_THREADS)
        from thundertalk.core import speech
        return speech.backend(bid)

    def backend(self, bid: str):
        with self._mu:
            if bid not in self._backends:
                self._backends[bid] = self._factory(bid)
            return self._backends[bid]

    def ready_engines(self) -> list[str]:
        out = []
        for bid in LOCAL_ENGINES:
            try:
                if self.backend(bid).is_ready():
                    out.append(bid)
            except Exception:                 # noqa: BLE001 - an engine whose extras are missing
                pass
        return out

    def voices(self) -> dict:
        engines, voices = [], []
        for bid in self.ready_engines():
            b = self.backend(bid)
            engines.append({"id": bid, "name": b.info.name, "sample_rate": b.sample_rate,
                            "default": bid == self.default_engine, "gpu": b.info.needs_gpu})
            voices += [{"id": v.id, "name": v.name, "engine": bid, "language": v.language,
                        "gender": v.gender, "description": v.blurb_en} for v in b.voices()]
        return {"engines": engines, "voices": voices}

    def resolve(self, model: Optional[str], voice: Optional[str], text: str):
        """(backend id, voice id, language) for a request, or SpeechError."""
        from thundertalk.core import tts
        model = (model or "").strip().lower()
        voice = (voice or "").strip()
        lang = tts.detect_language(text)
        if not model or model in OPENAI_MODELS:
            bid = voice.split(":", 1)[0] if ":" in voice else self.default_engine
        elif model in LOCAL_ENGINES:
            bid = model
        else:
            raise SpeechError(400, f"Unknown model '{model}'. Use one of: {', '.join(LOCAL_ENGINES)}")
        if bid not in LOCAL_ENGINES:
            raise SpeechError(400, f"Unknown voice '{voice}'. GET /v1/audio/voices lists them.")
        b = self.backend(bid)
        if not b.is_ready():
            raise SpeechError(503, f"{b.info.name} is not downloaded. Download it in ThunderTalk "
                                   f"(Models page, or `thundertalk models download {bid}`).")
        ids = [v.id for v in b.voices()]
        if not voice or voice.lower() in OPENAI_VOICES:
            voice = self.default_voice(b, lang)
        elif ":" not in voice and voice.isdigit() and f"{bid}:{voice}" in ids:
            voice = f"{bid}:{voice}"
        if voice not in ids and not (bid == "kokoro" and self._kokoro_sid(voice)):
            raise SpeechError(400, f"Unknown voice '{voice}' for {bid}. GET /v1/audio/voices lists them.")
        return bid, voice, lang

    @staticmethod
    def _kokoro_sid(voice: str) -> bool:
        from thundertalk.core.tts_backends.kokoro import all_voice_ids
        return voice in all_voice_ids()       # all 103 speakers, not only the curated list

    @staticmethod
    def default_voice(b, lang: str) -> str:
        voices = b.voices()
        if not voices:
            raise SpeechError(400, f"{b.info.name} has no built-in voices")
        want = "chinese" if lang == "chinese" else "english"
        return next((v.id for v in voices if v.language == want), voices[0].id)

    # ── synthesis (worker threads) ───────────────────────────────────────

    def load(self, bid: str) -> None:
        b = self.backend(bid)
        if b.info.needs_gpu:
            from thundertalk.core import speech
            speech.get_engine().load_backend(bid)
        else:
            b.load()

    def warm_up(self, bid: str) -> None:
        b = self.backend(bid)
        self.load(bid)
        voice = self.default_voice(b, "english")
        self._generate(b, "Hello there.", voice, "english", 1.0)

    def _generate(self, b, text: str, voice: str, lang: str, speed: float) -> np.ndarray:
        ctx: dict = {}
        if b.info.needs_gpu:
            from thundertalk.core.gpu_lock import GPU_LOCK
            from thundertalk.core.mlx_runtime import mlx_context
            with GPU_LOCK, mlx_context():
                a = b.generate(text, voice, lang, speed=speed, context=ctx)
                a = np.asarray(a, dtype=np.float32).reshape(-1)
        else:
            a = np.asarray(b.generate(text, voice, lang, speed=speed, context=ctx), dtype=np.float32).reshape(-1)
        if not ctx.get("native_speed") and abs(speed - 1.0) > 0.02:
            from thundertalk.core.tts import time_stretch
            a = time_stretch(a, speed, b.sample_rate)
        return a

    def stream(self, bid: str, voice: str, lang: str, text: str, speed: float = 1.0,
               cancel: Optional[threading.Event] = None) -> Iterator[bytes]:
        """PCM16 chunks, one per piece (pauses as silence). Runs on a worker
        thread; stops between pieces when ``cancel`` is set."""
        from thundertalk.core.tts import trim_silence
        b = self.backend(bid)
        sr = b.sample_rate
        pieces = plan_pieces(text, lang)
        with self._run:
            self.load(bid)
            after_join = False
            for k, (piece, pause) in enumerate(pieces):
                if cancel is not None and cancel.is_set():
                    return
                joined = pause == 0 and k < len(pieces) - 1      # mid-sentence cut: keep the join tight
                audio = trim_silence(self._generate(b, piece, voice, lang, speed), sr,
                                     keep_head=0.01 if after_join else 0.05, keep_tail=0.03 if joined else 0.12)
                after_join = joined
                if pause > 0:
                    audio = np.concatenate([audio, np.zeros(int(pause * sr), np.float32)])
                yield pcm16(audio)
