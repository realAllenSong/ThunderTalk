"""Stretch scoring: the cache rewinds give the same numbers as scoring each text from scratch."""
import hashlib

import pytest

mx = pytest.importorskip("mlx.core")
pytest.importorskip("mlx_qwen3_asr")

from thundertalk.core import hotword_score as hs  # noqa: E402

VOCAB = 97


def _logits(prefix: tuple[int, ...]):
    """A language model with no mercy for a wrong cache: the next-token scores depend on every token so far."""
    seed = int.from_bytes(hashlib.sha256(repr(prefix).encode()).digest()[:4], "big")
    return mx.random.normal((VOCAB,), key=mx.random.key(seed))


class _Cache:
    def __init__(self):
        self.ids: list[int] = []
        self.offset = 0


class _Model:
    def __init__(self):
        self.fed = 0                                  # tokens pushed through the decoder after the prompt

    def audio_tower(self, mel, lens):
        return mx.zeros((1, 5, 4)), lens

    def create_cache(self, max_seq_len=None):
        return _Cache()

    def _feed(self, ids, cache):
        rows = []
        for t in ids:
            del cache.ids[cache.offset:]              # what a preallocated cache does: write at the offset
            cache.ids.append(int(t))
            cache.offset += 1
            rows.append(_logits(tuple(cache.ids)))
        return mx.stack(rows)[None]

    def prefill(self, input_ids, audio_features, position_ids, cache):
        return self._feed(input_ids[0].tolist(), cache)[:, -1:, :]

    def step_many(self, input_ids, position_ids, cache):
        assert position_ids[0, 0, 0].item() == cache.offset
        self.fed += input_ids.shape[1]
        return self._feed(input_ids[0].tolist(), cache)


class _Tokenizer:
    """One token per character; the prompt is the audio placeholders and the context."""

    def encode(self, text):
        return [ord(c) % VOCAB for c in text]

    def build_prompt_tokens(self, n_audio_tokens, language=None, context=""):
        return [1] * n_audio_tokens + self.encode(context) + ([2] if language else [])


@pytest.fixture(autouse=True)
def _no_audio(monkeypatch):
    import mlx_qwen3_asr.audio as audio
    monkeypatch.setattr(audio, "compute_features", lambda samples: (mx.zeros((1, 8, 10)), mx.array([10])))


def _logp(head: list[int], ids: list[int], lo: int, hi: int) -> float:
    total = 0.0
    for i in range(lo, hi):
        row = _logits(tuple(head + ids[:i]))
        total += float((row - mx.logsumexp(row))[ids[i]])
    return total


def _expected(tok, head, prefix, plain, s) -> float:
    base, alt = tok.encode(prefix + plain), tok.encode(prefix + hs._put(plain, s))
    c = hs._common_prefix(base, alt)
    tail = hs._common_suffix(base[c:], alt[c:])
    return (_logp(head, alt, c, min(len(alt), len(alt) - tail + hs.TAIL))
            - _logp(head, base, c, min(len(base), len(base) - tail + hs.TAIL)))


@pytest.mark.parametrize("language,prefix", [(None, "language Chinese<asr_text>"), ("Chinese", "")])
def test_gains_match_scoring_each_text_from_scratch(language, prefix):
    plain = "我用罗塞塔和 play right 写了一个测试，然后交给 cloud code。"
    found = [hs.Stretch(2, 5, "Rosetta", 0.55),
             hs.Stretch(plain.index("play"), plain.index("play") + 10, "Playwright", 0.8),
             hs.Stretch(plain.index("cloud"), plain.index("cloud") + 10, "Claude Code", 0.7),
             hs.Stretch(0, 1, "Wo", 0.5)]                      # differs from the first token on
    tok, model = _Tokenizer(), _Model()
    scored = hs.score(model, tok, [0.0] * 16000, plain, found, prompt="Rosetta Playwright",
                      language=language, detected="Chinese")
    head = tok.build_prompt_tokens(5, language=language, context="Rosetta Playwright")
    assert len(scored) == 4
    for s in scored:
        assert s.gain == pytest.approx(_expected(tok, head, prefix, plain, s), abs=1e-3), s.word
    # One pass over the transcript, then only each alternative's own tokens and the tail.
    assert model.fed < len(tok.encode(prefix + plain)) + sum(len(s.word) + hs.TAIL + 2 for s in scored)


def test_a_stretch_that_changes_nothing_scores_zero():
    plain = "open Rosetta now"
    scored = hs.score(_Model(), _Tokenizer(), [0.0] * 16000, plain, [hs.Stretch(5, 12, "Rosetta", 1.0)],
                      prompt="Rosetta", language="English", detected="English")
    assert scored[0].gain == 0.0


def test_only_the_closest_stretches_are_scored():
    plain = "a " * 40
    found = [hs.Stretch(2 * i, 2 * i + 1, "B", i / 100) for i in range(30)]
    scored = hs.score(_Model(), _Tokenizer(), [0.0] * 16000, plain, found, prompt="B", language="English", detected=None)
    assert len(scored) == hs.MAX_ALTERNATIVES
    assert min(s.alike for s in scored) == pytest.approx(0.18)


def test_apply_takes_preferred_stretches_and_the_better_of_two_that_overlap():
    plain = "我用罗塞塔和 play right 写测试"
    a = hs.Stretch(2, 5, "Rosetta", 0.6, gain=1.5)
    b = hs.Stretch(1, 5, "Rosette", 0.6, gain=0.4)             # overlaps a, lower gain
    c = hs.Stretch(plain.index("play"), plain.index("play") + 10, "Playwright", 0.8, gain=-0.3)
    assert hs.apply(plain, [a, b, c], 0.0) == "我用 Rosetta 和 play right 写测试"
    assert hs.apply(plain, [a, b, c], -1.0) == "我用 Rosetta 和 Playwright 写测试"
    assert hs.apply(plain, [a, b, c], 2.0) == plain
    a.bar = 2.0                                               # read off the screen: needs more than the user's own
    assert hs.apply(plain, [a, b, c], 0.0) == "我 Rosette 和 play right 写测试"
    b.bar = 2.0
    assert hs.apply(plain, [a, b, c], 0.0) == plain


def test_put_spaces_a_latin_hotword_from_chinese_text_only():
    assert hs._put("用罗塞塔来做", hs.Stretch(1, 4, "Rosetta", 1)) == "用 Rosetta 来做"
    assert hs._put("use rose etta now", hs.Stretch(4, 13, "Rosetta", 1)) == "use Rosetta now"
    assert hs._put("罗塞塔。", hs.Stretch(0, 3, "Rosetta", 1)) == "Rosetta。"
    assert hs._put("用罗塞塔来做", hs.Stretch(1, 4, "罗赛塔", 1)) == "用罗赛塔来做"


def test_the_encoder_answers_from_memory_for_the_take_it_has_just_seen():
    from thundertalk.core.asr import remember_encoder_output

    class Tower:
        calls = 0

        def __call__(self, mel, lens):
            Tower.calls += 1
            return mel * 2, lens

    class Model:
        pass

    model = Model()
    model.audio_tower = Tower()
    remember_encoder_output(model)
    a = mx.array([[1.0, 2.0, 3.0]])
    first, _ = model.audio_tower(a, mx.array([3]))
    again, _ = model.audio_tower(mx.array([[1.0, 2.0, 3.0]]), mx.array([3]))       # same take, new array
    assert Tower.calls == 1 and mx.array_equal(first, again).item()
    other, _ = model.audio_tower(mx.array([[1.0, 2.0, 4.0]]), mx.array([3]))       # another take
    assert Tower.calls == 2 and other[0, 2].item() == 8.0
    model.audio_tower(mx.array([[1.0, 2.0, 4.0]]).astype(mx.float16), mx.array([3]))  # same numbers, other type
    assert Tower.calls == 3
