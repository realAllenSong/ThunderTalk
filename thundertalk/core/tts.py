"""Text-to-speech engine (Qwen3-TTS through mlx-audio), in-process.

Why this exists instead of the old `mlx-tts serve` sidecar:
  * the packaged app never contained that server, so TTS could not work for
    anyone who didn't have it pip-installed next to the app;
  * the server sent the whole text as one autoregressive pass with
    hard-coded sampling settings, which is what makes the voice wander.

What makes the output stable (each guard is covered by a test or by the
measurements in docs/plans/2026-09-28-tts-studio.md):
  * text is split into sentence-sized pieces and each piece is generated
    separately, so an error can't snowball across a paragraph;
  * generation length is capped from the *expected* duration of the piece —
    the model sometimes fails to emit its stop token and would otherwise run
    to 4096 tokens (5+ minutes of babble for one sentence);
  * a piece whose duration is implausible for its text is regenerated with a
    cooler temperature and a new seed, and the closest attempt wins;
  * pieces are level-matched, trimmed and joined with punctuation-aware
    pauses; the final mix is peak-normalised;
  * speed is a real pitch-preserving time-stretch (the model itself ignores
    its `speed` argument).
"""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

import numpy as np

from thundertalk.core.gpu_lock import GPU_LOCK

SR = 24000
FRAMES_PER_SEC = 12.5          # Qwen3-TTS 12 Hz codec: tokens per second of audio

# 8-bit: same pitch stability as bf16 in our measurements, 1.4–2.3× faster, 3.1 GB instead of 4.2 GB.
CUSTOM_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
BASE_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"


# Download sizes (GB) shown before the user commits to a multi-GB fetch.
REPO_SIZE_GB = {
    "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16": 4.2,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16": 4.2,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit": 3.1,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit": 3.1,
}


def repo_size_gb(repo: str) -> float:
    return REPO_SIZE_GB.get(repo, 4.2)


# ── voices ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PresetVoice:
    id: str
    name: str
    gender: str           # "f" | "m"
    native: str           # language the voice is native in
    blurb_en: str
    blurb_zh: str


PRESET_VOICES: list[PresetVoice] = [
    PresetVoice("vivian", "Vivian", "f", "chinese", "Bright, clear young woman", "明亮清晰的年轻女声"),
    PresetVoice("serena", "Serena", "f", "chinese", "Warm and gentle", "温暖柔和的女声"),
    PresetVoice("uncle_fu", "Uncle Fu", "m", "chinese", "Low, mellow, seasoned man", "低沉醇厚的成熟男声"),
    PresetVoice("dylan", "Dylan", "m", "chinese", "Youthful, Beijing accent", "年轻的北京口音男声"),
    PresetVoice("eric", "Eric", "m", "chinese", "Lively, Sichuan accent", "活泼的四川口音男声"),
    PresetVoice("ryan", "Ryan", "m", "english", "Dynamic, rhythmic man", "富有节奏感的男声"),
    PresetVoice("aiden", "Aiden", "m", "english", "Sunny American man", "阳光的美式男声"),
    PresetVoice("ono_anna", "Ono Anna", "f", "japanese", "Playful Japanese woman", "活泼的日语女声"),
    PresetVoice("sohee", "Sohee", "f", "korean", "Warm Korean woman", "温暖的韩语女声"),
]
PRESET_IDS = {v.id for v in PRESET_VOICES}

LANGUAGES = ["auto", "chinese", "english", "japanese", "korean", "french",
             "german", "spanish", "italian", "portuguese", "russian"]


@dataclass
class ClonePrompt:
    """A reference recording of the voice to imitate, plus what is said in it."""
    audio: np.ndarray            # mono float32 at SR
    text: str
    name: str = ""


VoiceRef = Union[str, ClonePrompt]     # preset id, or a clone prompt


@dataclass
class TtsParams:
    """Sampling and guard settings. The defaults come from measurements on the
    1.7B CustomVoice model (docs/plans/2026-09-28-tts-studio.md): T0.5 with a
    modest top-k/top-p keeps the pitch contour inside the range of natural read
    speech, and ~2–3-sentence pieces beat both whole-passage generation (drops
    or repeats text in about 1 run in 3) and sentence-by-sentence generation
    (every sentence restarts its own register, so the voice wanders)."""
    temperature: float = 0.5
    top_k: int = 30
    top_p: float = 0.85
    repetition_penalty: float = 1.05
    max_attempts: int = 3
    ratio_min: float = 0.72        # accepted audio/expected duration band
    ratio_max: float = 1.9
    chunk_scale: float = 2.5       # multiplier on the per-piece length targets
    verify_max_err: float = 0.12   # ASR read-back error above which a piece is redone (a skipped clause is ~15–20 %)


# A verifier reads a generated piece back and returns its error rate against
# the source text (0 = perfect), or None if it cannot judge.
Verifier = Callable[[np.ndarray, str, str], Optional[float]]


# ── language & text handling ─────────────────────────────────────────────

_HAN = re.compile(r"[㐀-䶿一-鿿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_CYR = re.compile(r"[Ѐ-ӿ]")
_WORD = re.compile(r"[A-Za-z0-9À-ɏ']+")

_STOP = {
    "german": {"der", "die", "das", "und", "ist", "nicht", "ich", "ein", "eine", "mit", "für", "auf", "zu", "den"},
    "french": {"le", "la", "les", "et", "est", "une", "des", "pour", "que", "dans", "pas", "vous", "je", "un"},
    "spanish": {"el", "la", "los", "las", "y", "es", "una", "para", "que", "por", "con", "no", "un", "del"},
    "italian": {"il", "lo", "la", "che", "di", "è", "per", "una", "con", "non", "sono", "un", "del", "gli"},
    "portuguese": {"o", "a", "os", "as", "e", "é", "uma", "para", "que", "não", "com", "um", "do", "da"},
}


def detect_language(text: str) -> str:
    """Script-based detection with a small stop-word vote for Latin scripts.
    Always returns an explicit language: the model's own 'auto' switches off
    its language conditioning and reads worse."""
    s = text.strip()
    if not s:
        return "english"
    n = max(1, len(re.sub(r"\s", "", s)))
    kana, han, hangul, cyr = len(_KANA.findall(s)), len(_HAN.findall(s)), len(_HANGUL.findall(s)), len(_CYR.findall(s))
    if kana / n > 0.08:
        return "japanese"
    if hangul / n > 0.15:
        return "korean"
    if han / n > 0.15:
        return "chinese"
    if cyr / n > 0.3:
        return "russian"
    words = [w.lower() for w in _WORD.findall(s)]
    if words:
        best, score = "english", 0
        for lang, stop in _STOP.items():
            sc = sum(1 for w in words if w in stop)
            if sc > score and sc / len(words) > 0.12:
                best, score = lang, sc
        return best
    return "english"


def expected_seconds(text: str, lang: str) -> float:
    """Rough speaking time of ``text`` — used to cap and sanity-check output."""
    words = len(_WORD.findall(text))
    han = len(_HAN.findall(text))
    kana = len(_KANA.findall(text))
    hangul = len(_HANGUL.findall(text))
    if lang == "chinese":
        base = han / 4.3 + words / 2.6
    elif lang == "japanese":
        base = (han + kana) / 6.5 + words / 2.6
    elif lang == "korean":
        base = hangul / 5.6 + words / 2.6
    else:
        base = words / 2.7 + han / 4.3
    pauses = 0.18 * len(re.findall(r"[,，、;；:：]", text)) + 0.30 * len(re.findall(r"[.!?。！？…]", text))
    return max(0.7, base + pauses)


_ABBREV = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc", "e.g", "i.e", "st", "no", "fig", "inc", "ltd", "a.m", "p.m", "u.s", "u.k"}
_CLOSERS = "\"'”’)]）】」』»"


def _is_terminator(text: str, i: int) -> bool:
    ch = text[i]
    if ch in "。！？!?…；;":
        return True
    if ch == ".":
        nxt = text[i + 1] if i + 1 < len(text) else " "
        prev = text[i - 1] if i > 0 else " "
        if prev.isdigit() and nxt.isdigit():
            return False                           # 3.14
        if nxt not in " \n\t" and nxt not in _CLOSERS:
            return False                           # a.b, file.txt
        j = i - 1
        while j >= 0 and (text[j].isalpha() or text[j] == "."):
            j -= 1
        word = text[j + 1:i].lower()
        if word in _ABBREV or (len(word) == 1 and word.isalpha()):
            return False                           # Dr. / e.g. / initials
        return True
    return False


def split_sentences(text: str) -> list[tuple[str, float]]:
    """Split into (sentence, pause_after_seconds). Blank lines are longer pauses."""
    out: list[tuple[str, float]] = []
    for para in re.split(r"\n\s*\n", text.replace("\r", "")):
        para = re.sub(r"[ \t]*\n[ \t]*", " ", para.strip())
        if not para:
            continue
        buf: list[str] = []
        i = 0
        while i < len(para):
            buf.append(para[i])
            if _is_terminator(para, i):
                while i + 1 < len(para) and para[i + 1] in _CLOSERS + "。！？!?….":
                    i += 1
                    buf.append(para[i])
                s = "".join(buf).strip()
                if s:
                    out.append((s, 0.34))
                buf = []
            i += 1
        tail = "".join(buf).strip()
        if tail:
            out.append((tail, 0.34))
        if out:
            out[-1] = (out[-1][0], 0.7)            # paragraph break
    return out


def _split_long(s: str, max_chars: int) -> list[str]:
    if len(s) <= max_chars:
        return [s]
    # prefer clause punctuation near the middle, then spaces
    mid = len(s) // 2
    best = None
    for m in re.finditer(r"[,，、;；:：]\s*|\s+", s):
        cut = m.end()
        weight = 0 if m.group(0).strip() else 1          # punctuation beats bare spaces
        score = (weight, abs(cut - mid))
        if 8 < cut < len(s) - 8 and (best is None or score < best[0]):
            best = (score, cut)
    if best is None:
        cut = mid
    else:
        cut = best[1]
    return _split_long(s[:cut].strip(), max_chars) + _split_long(s[cut:].strip(), max_chars)


def plan_segments(text: str, lang: str, scale: float = 1.0) -> list[tuple[str, float]]:
    """Pieces for generation: merge fragments that are too short (choppy
    delivery), split ones that are too long (drift). ``scale`` stretches the
    length targets — larger pieces keep the voice consistent across sentences,
    smaller ones limit how far one bad sample can wander."""
    cjk = lang in ("chinese", "japanese", "korean")
    target = int({"chinese": 46, "japanese": 52, "korean": 70}.get(lang, 120) * scale)
    hard = int({"chinese": 80, "japanese": 90, "korean": 120}.get(lang, 200) * scale)
    sents = split_sentences(text)
    merged: list[tuple[str, float]] = []
    for s, pause in sents:
        if merged and len(merged[-1][0]) + len(s) <= target and merged[-1][1] < 0.5:
            prev, _ = merged[-1]
            joiner = "" if cjk else " "
            merged[-1] = (prev + joiner + s, pause)
        else:
            merged.append((s, pause))
    out: list[tuple[str, float]] = []
    for s, pause in merged:
        parts = _split_long(s, hard)
        for k, part in enumerate(parts):
            out.append((part, pause if k == len(parts) - 1 else 0.16))
    return out


# ── signal helpers ───────────────────────────────────────────────────────

def _frame_rms(x: np.ndarray, frame: int) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.array([float(np.sqrt(np.mean(x ** 2) + 1e-12))])
    return np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)


def trim_silence(x: np.ndarray, sr: int = SR, thr_db: float = -44.0,
                 keep_head: float = 0.05, keep_tail: float = 0.12) -> np.ndarray:
    if len(x) == 0:
        return x
    frame = int(0.01 * sr)
    r = _frame_rms(x, frame)
    peak = float(np.max(r))
    if peak <= 1e-5:
        return x
    active = np.where(r > peak * (10 ** (thr_db / 20.0)))[0]
    if len(active) == 0:
        return x
    a = max(0, int(active[0] * frame - keep_head * sr))
    b = min(len(x), int((active[-1] + 1) * frame + keep_tail * sr))
    return x[a:b]


def active_rms(x: np.ndarray, sr: int = SR) -> float:
    frame = int(0.02 * sr)
    r = _frame_rms(x, frame)
    if len(r) == 0:
        return 1e-6
    thr = float(np.max(r)) * 0.1
    act = r[r > thr]
    return float(np.sqrt(np.mean(act ** 2))) if len(act) else float(np.max(r))


def fade(x: np.ndarray, ms: float = 8.0, sr: int = SR) -> np.ndarray:
    n = min(int(ms / 1000 * sr), len(x) // 2)
    if n < 2:
        return x
    x = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


def time_stretch(x: np.ndarray, rate: float, sr: int = SR) -> np.ndarray:
    """Pitch-preserving speed change (WSOLA). rate > 1 is faster.

    The model's own ``speed`` argument is ignored by mlx-audio's Qwen3-TTS, so
    the UI's speed control has to be implemented on the waveform."""
    if abs(rate - 1.0) < 0.02 or len(x) < 2048:
        return x
    rate = float(min(max(rate, 0.5), 2.0))
    N = int(0.032 * sr)                 # window
    Hs = N // 2                         # synthesis hop
    Ha = Hs * rate                      # nominal analysis hop
    tol = int(0.008 * sr)               # search tolerance
    win = np.hanning(N).astype(np.float32)
    n_out = int(len(x) / rate) + N
    y = np.zeros(n_out + N, dtype=np.float32)
    norm = np.zeros(n_out + N, dtype=np.float32)
    pos_a = 0.0
    prev_tail = None
    k = 0
    xp = np.concatenate([x, np.zeros(N + 2 * tol + 8, dtype=np.float32)])
    while True:
        nominal = int(round(pos_a))
        if nominal >= len(x):
            break
        if prev_tail is None:
            start = nominal
        else:
            lo, hi = max(0, nominal - tol), nominal + tol
            seg = xp[lo: hi + N]
            if len(seg) < N + 1:
                start = nominal
            else:
                corr = np.correlate(seg[: len(seg)], prev_tail, mode="valid")
                start = lo + int(np.argmax(corr[: hi - lo + 1]))
        frame = xp[start: start + N]
        if len(frame) < N:
            break
        o = k * Hs
        if o + N > len(y):
            break
        y[o: o + N] += frame * win
        norm[o: o + N] += win
        prev_tail = xp[start + Hs: start + Hs + N] if start + Hs + N <= len(xp) else None
        if prev_tail is not None and len(prev_tail) < N:
            prev_tail = None
        pos_a += Ha
        k += 1
    norm[norm < 1e-3] = 1.0
    out = y / norm
    return out[: int(len(x) / rate)].astype(np.float32)


def assemble(pieces: list[np.ndarray], pauses: list[float], sr: int = SR) -> np.ndarray:
    """Join pieces with pauses; overlap-add over a 10 ms crossfade at joins."""
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    xf = int(0.010 * sr)
    out = pieces[0].astype(np.float32).copy()
    for piece, pause in zip(pieces[1:], pauses):
        gap = np.zeros(int(pause * sr), dtype=np.float32)
        out = np.concatenate([out, gap])
        if len(out) >= xf and len(piece) >= xf:
            ramp = np.linspace(0.0, 1.0, xf, dtype=np.float32)
            out[-xf:] = out[-xf:] * (1 - ramp) + piece[:xf] * ramp
            out = np.concatenate([out, piece[xf:]])
        else:
            out = np.concatenate([out, piece])
    return out


# ── engine ───────────────────────────────────────────────────────────────

def repo_ready(repo: str) -> bool:
    """True when the repo is fully in the HF cache: the language model *and*
    the speech tokenizer (a cancelled download can leave the first without the
    second, which would only fail later, at load time)."""
    from thundertalk.core.models import _hf_cache_has, hf_snapshot_dir
    if not _hf_cache_has(repo):
        return False
    snap = hf_snapshot_dir(repo)
    return bool(snap and (snap / "speech_tokenizer" / "model.safetensors").exists())


class TtsModelMissing(RuntimeError):
    def __init__(self, repo: str) -> None:
        super().__init__(f"TTS model not downloaded: {repo}")
        self.repo = repo


class TtsCancelled(Exception):
    pass


@dataclass
class SegmentReport:
    text: str
    expected_s: float
    audio_s: float
    attempts: int
    ok: bool
    error_rate: Optional[float] = None      # ASR read-back error of the chosen take


@dataclass
class SynthResult:
    audio: np.ndarray
    sample_rate: int
    language: str
    segments: list[SegmentReport] = field(default_factory=list)
    seconds_taken: float = 0.0

    @property
    def duration(self) -> float:
        return len(self.audio) / self.sample_rate

    @property
    def warnings(self) -> list[str]:
        return [f"“{s.text[:40]}…” needed {s.attempts} attempts and may sound off"
                for s in self.segments if not s.ok]


ProgressCB = Callable[[int, int, str], None]


class TtsEngine:
    """Holds at most one Qwen3-TTS model in memory (they are ~4.5 GB each)."""

    def __init__(self) -> None:
        self._model = None
        self._repo: Optional[str] = None

    @staticmethod
    def repo_for(voice: VoiceRef) -> str:
        return BASE_REPO if isinstance(voice, ClonePrompt) else CUSTOM_REPO

    def is_available(self, voice: VoiceRef) -> bool:
        return repo_ready(self.repo_for(voice))

    def unload(self) -> None:
        with GPU_LOCK:
            self._model = None
            self._repo = None
            try:
                import mlx.core as mx
                mx.clear_cache()
            except Exception:
                pass

    def _ensure(self, repo: str):
        if self._model is not None and self._repo == repo:
            return self._model
        if not repo_ready(repo):
            raise TtsModelMissing(repo)
        self.unload()
        with GPU_LOCK:
            from mlx_audio.tts.utils import load_model
            self._model = load_model(repo)
            self._repo = repo
        return self._model

    # -- one piece ------------------------------------------------------
    def _generate_piece(self, model, text: str, voice: VoiceRef, lang: str, style: Optional[str],
                        params: TtsParams, temperature: float, max_tokens: int, seed: int) -> np.ndarray:
        import mlx.core as mx
        kw = dict(temperature=temperature, top_k=params.top_k, top_p=params.top_p,
                  repetition_penalty=params.repetition_penalty, max_tokens=max_tokens)
        chunks: list[np.ndarray] = []
        with GPU_LOCK:
            mx.random.seed(seed)
            if isinstance(voice, ClonePrompt):
                gen = model.generate(text, ref_audio=mx.array(voice.audio), ref_text=voice.text,
                                     lang_code=lang, stream=False, **kw)
            else:
                gen = model.generate_custom_voice(text=text, speaker=voice, language=lang,
                                                  instruct=style or None, **kw)
            for r in gen:
                chunks.append(np.array(r.audio).astype(np.float32).reshape(-1))
            try:
                mx.clear_cache()
            except Exception:
                pass
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def _render_piece(self, model, seg: str, voice: VoiceRef, lang: str, style: Optional[str],
                      params: TtsParams, seed: int, verifier: Optional[Verifier], state: dict,
                      cancel: Optional[threading.Event]) -> tuple[np.ndarray, "SegmentReport"]:
        """Generate one piece, keeping the best of up to ``max_attempts`` takes.
        A take is rejected if its length is implausible for the text or if the
        read-back verifier says the words don't match. ``state['off']`` is set
        when the verifier proves unable to read this language."""
        exp = expected_seconds(seg, lang)
        cap = int(exp * FRAMES_PER_SEC * 2.0) + 20
        best, best_pen, best_ratio, best_err = None, 1e9, 0.0, None
        attempts_used, confident_misses = 0, 0
        for attempt in range(params.max_attempts):
            if cancel is not None and cancel.is_set():
                raise TtsCancelled()
            attempts_used = attempt + 1
            # New seed each time, same temperature: cooling a take that failed only
            # makes the model repeat its favourite failure.
            audio = self._generate_piece(model, seg, voice, lang, style, params, params.temperature, cap,
                                         seed + attempt * 7919)
            audio = trim_silence(audio)
            dur = len(audio) / SR
            ratio = dur / exp
            dur_ok = params.ratio_min <= ratio <= params.ratio_max and dur > 0.25
            err: Optional[float] = None
            if dur_ok and verifier is not None and not state["off"]:
                try:
                    err = verifier(audio, seg, lang)
                except Exception:
                    err = None
            misread = err is not None and err > params.verify_max_err
            pen = (0.0 if dur_ok else 1.0) + 2.0 * (err or 0.0) + 0.1 * abs(math.log(max(ratio, 1e-3)))
            if pen < best_pen:
                best, best_pen, best_ratio, best_err = audio, pen, ratio, err
            if dur_ok and not misread:
                break
            # A reader that disagrees almost completely with a piece of normal length,
            # twice in a row, is more likely unable to read this language than a sign
            # of a bad take: stop trusting it.
            if dur_ok and err is not None and err > 0.6:
                confident_misses += 1
                if confident_misses >= 2:
                    state["off"] = True
                    break
        ok = (params.ratio_min <= best_ratio <= params.ratio_max
              and (state["off"] or best_err is None or best_err <= params.verify_max_err))
        audio = best if best is not None else np.zeros(int(0.2 * SR), np.float32)
        return audio, SegmentReport(seg, exp, len(audio) / SR, attempts_used, ok, best_err)

    # -- public ---------------------------------------------------------
    def synthesize(
        self,
        text: str,
        voice: VoiceRef,
        language: Optional[str] = None,
        style: Optional[str] = None,
        speed: float = 1.0,
        params: Optional[TtsParams] = None,
        seed: Optional[int] = None,
        progress: Optional[ProgressCB] = None,
        cancel: Optional[threading.Event] = None,
        verifier: Optional[Verifier] = None,
    ) -> SynthResult:
        text = text.strip()
        if not text:
            raise ValueError("Nothing to say — the text is empty.")
        if isinstance(voice, str) and voice not in PRESET_IDS:
            raise ValueError(f"Unknown voice: {voice}")
        params = params or TtsParams()
        lang = language if language and language != "auto" else detect_language(text)
        model = self._ensure(self.repo_for(voice))
        base_seed = seed if seed is not None else int(time.time()) & 0x7FFFFFFF

        pieces_text = plan_segments(text, lang, params.chunk_scale)
        t0 = time.monotonic()
        reports: list[SegmentReport] = []
        pieces: list[np.ndarray] = []
        pauses: list[float] = []
        rms_list: list[float] = []
        verifier_off = False

        for idx, (seg, pause) in enumerate(pieces_text):
            if cancel is not None and cancel.is_set():
                raise TtsCancelled()
            if progress:
                progress(idx, len(pieces_text), seg)
            state = {"off": verifier_off}
            audio, rep = self._render_piece(model, seg, voice, lang, style, params, base_seed + idx * 1009,
                                            verifier, state, cancel)
            if not rep.ok and params.max_attempts > 1:
                # Same context failed repeatedly (typically the model ends the piece early or
                # skips a sentence). A different context is a better bet than more luck in
                # the same one: redo it sentence by sentence.
                subs = plan_segments(seg, lang, 0.4)
                if len(subs) > 1:
                    parts, gaps, reps = [], [], []
                    for j, (sub, sp) in enumerate(subs):
                        if cancel is not None and cancel.is_set():
                            raise TtsCancelled()
                        a2, r2 = self._render_piece(model, sub, voice, lang, style, params,
                                                    base_seed + idx * 1009 + 100003 * (j + 1),
                                                    verifier, state, cancel)
                        parts.append(a2)
                        gaps.append(sp)
                        reps.append(r2)
                    if any(r.ok for r in reps):            # keep it only if it is better than what failed
                        audio = assemble(parts, gaps[:-1])
                        rep = SegmentReport(seg, rep.expected_s, len(audio) / SR,
                                            rep.attempts + sum(r.attempts for r in reps),
                                            all(r.ok for r in reps), None)
            verifier_off = state["off"]
            audio = fade(audio)
            reports.append(rep)
            pieces.append(audio)
            pauses.append(pause)
            rms_list.append(active_rms(audio))
            if progress:
                progress(idx + 1, len(pieces_text), seg)

        # Level-match every piece to the median loudness (avoids per-sentence jumps).
        target = float(np.median(rms_list)) if rms_list else 1.0
        leveled = []
        for a, r in zip(pieces, rms_list):
            g = min(max(target / max(r, 1e-6), 0.5), 2.0)
            leveled.append(a * g)
        mix = assemble(leveled, pauses[:-1] if pauses else [])
        if abs(speed - 1.0) >= 0.02:
            mix = time_stretch(mix, speed)
        peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
        if peak > 1e-6:
            mix = mix * (0.891 / peak)                      # −1 dBFS
        return SynthResult(mix.astype(np.float32), SR, lang, reports, time.monotonic() - t0)


_ENGINE: Optional[TtsEngine] = None


def get_engine() -> TtsEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = TtsEngine()
    return _ENGINE
