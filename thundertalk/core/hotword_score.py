"""Ask the decoder about one stretch instead of decoding the whole take again.

``hotword_merge`` finds stretches of the plain transcript that sound like a
hotword. Deciding whether the hotword was really said used to cost a second
full decode with the hotwords as prompt. Here the same question is put
directly: with the hotwords in the prompt and the plain transcript forced as
the text so far, does the decoder give the hotword spelling a higher
probability at that stretch than what it wrote?

One prefill (prompt + audio) and one pass over the plain transcript fill the
KV cache. Each alternative then rewinds the cache to the point where it starts
to differ and scores only its own tokens plus a short tail, a few tokens
instead of the whole transcript. Because the text before every stretch is the
plain decode's own, a hotword cannot change anything but its stretch.
"""
from __future__ import annotations

from dataclasses import dataclass

TAIL = 3                 # shared tokens after the stretch that are scored too (the choice must fit what follows)
MAX_ALTERNATIVES = 12


@dataclass
class Stretch:
    start: int           # characters of the plain transcript that would be replaced
    end: int
    word: str            # the hotword, in the user's spelling
    alike: float         # how close the stretch sounds (hotword_merge)
    gain: float = 0.0    # log-probability of the hotword spelling minus the plain one, filled by ``score``
    bar: float = 0.0     # extra gain this stretch needs (names read off the screen may be an earlier mishearing)


def score(model, tokenizer, samples, plain: str, stretches: list[Stretch], *, prompt: str,
          language: str | None, detected: str | None, dtype=None) -> list[Stretch]:
    """Fill ``gain`` for each stretch (at most MAX_ALTERNATIVES, the closest-sounding first).

    ``language`` is the forced language (None = auto); ``detected`` is what the
    plain decode reported, needed to rebuild the "language X<asr_text>" prefix
    the decoder wrote itself in auto mode.
    """
    import mlx.core as mx
    from mlx_qwen3_asr.audio import compute_features
    from mlx_qwen3_asr.tokenizer import canonicalize_language

    stretches = sorted(stretches, key=lambda s: -s.alike)[:MAX_ALTERNATIVES]
    if not stretches or not plain.strip():
        return []
    dtype = dtype or mx.float16
    mel, feature_lens = compute_features(samples)
    audio_features, _ = model.audio_tower(mel.astype(dtype), feature_lens)
    forced = canonicalize_language(language)
    head = tokenizer.build_prompt_tokens(n_audio_tokens=audio_features.shape[1], language=forced, context=prompt)
    prefix = "" if forced else f"language {canonicalize_language(detected) or detected or 'Chinese'}<asr_text>"
    base = tokenizer.encode(prefix + plain)
    alts = [tokenizer.encode(prefix + _put(plain, s)) for s in stretches]
    n0 = len(head)
    longest = max([len(base)] + [len(a) for a in alts])
    cache = model.create_cache(max_seq_len=n0 + longest + 4)

    def positions(start: int, count: int):
        p = mx.arange(start, start + count)[None, :]
        return mx.stack([p, p, p], axis=1)

    def logprobs(logits):
        x = logits.astype(mx.float32)
        return x - mx.logsumexp(x, axis=-1, keepdims=True)

    first = logprobs(model.prefill(input_ids=mx.array([head]), audio_features=audio_features,
                                   position_ids=positions(0, n0), cache=cache))[0, 0]
    # Row i of ``after`` is the distribution over the token that follows base[:i + 1].
    after = logprobs(model.step_many(input_ids=mx.array([base]), position_ids=positions(n0, len(base)), cache=cache))[0]
    mx.eval(first, after)

    def base_logp(i: int) -> float:
        return float(first[base[i]] if i == 0 else after[i - 1, base[i]])

    # Rewinding to a later point first keeps the cache valid for the earlier ones:
    # an alternative only overwrites positions from where it differs.
    order = sorted(range(len(alts)), key=lambda k: -_common_prefix(base, alts[k]))
    for k in order:
        alt = alts[k]
        c = _common_prefix(base, alt)
        tail = _common_suffix(base[c:], alt[c:])
        if c == len(base) and c == len(alt):
            stretches[k].gain = 0.0
            continue
        base_end = min(len(base), len(base) - tail + TAIL)      # the stretch's own tokens and TAIL shared ones
        alt_end = min(len(alt), len(alt) - tail + TAIL)
        plain_lp = sum(base_logp(i) for i in range(c, base_end))
        lp = float((first if c == 0 else after[c - 1])[alt[c]])   # alt[c] follows the shared text base[:c]
        if alt_end - c > 1:
            cache.offset = n0 + c                                 # the cache holds prompt + base[:c] again
            fed = alt[c:alt_end - 1]
            rows = logprobs(model.step_many(input_ids=mx.array([fed]), position_ids=positions(n0 + c, len(fed)), cache=cache))[0]
            mx.eval(rows)
            lp += sum(float(rows[j, alt[c + 1 + j]]) for j in range(len(fed)))
        stretches[k].gain = lp - plain_lp
    return stretches


def apply(plain: str, stretches: list[Stretch], min_gain: float) -> str:
    """``plain`` with the stretches the decoder preferred as hotwords put in (overlaps: the better one)."""
    taken, out = [], plain
    for s in sorted(stretches, key=lambda s: -s.gain):
        if s.gain >= min_gain + s.bar and all(s.end <= t.start or s.start >= t.end for t in taken):
            taken.append(s)
    for s in sorted(taken, key=lambda s: -s.start):
        out = _put(out, s)
    return out


def _put(plain: str, s: Stretch) -> str:
    """``plain`` with the stretch replaced by its hotword, spaced the way the decoder
    writes mixed text ("用 Rosetta 来做"): scoring "用Rosetta来做" would count the
    missing spaces against the hotword."""
    left = " " if s.start and _cjk(plain[s.start - 1]) and _latin(s.word[:1]) else ""
    right = " " if s.end < len(plain) and _cjk(plain[s.end]) and _latin(s.word[-1:]) else ""
    return plain[:s.start] + left + s.word + right + plain[s.end:]


def _cjk(ch: str) -> bool:
    return "\u3400" <= ch <= "\u9fff"


def _latin(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _common_prefix(a: list[int], b: list[int]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _common_suffix(a: list[int], b: list[int]) -> int:
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        n += 1
    return n
