"""Hotwords may only change the words they are about (thundertalk.core.hotword_merge)."""
import pytest

from thundertalk.core.hotword_merge import candidates, merge, respell

HOT = ["PlayWright CLI", "Rosetta"]


@pytest.mark.parametrize("plain, hinted, want", [
    # The hotword stretch is taken; an unrelated change in the same decode is not.
    ("Play Rice OI.", "Playwright CLI.", "Playwright CLI."),
    ("我不懂。", "我不行。", "我不懂。"),
    ("你自己 play race，然后 iterator，你可以看它能不能去做得更好。",
     "你自己 Playwright CLI，然后 Iterative，可以看它能不能去做得更好。",
     "你自己 Playwright CLI，然后 iterator，你可以看它能不能去做得更好。"),
    ("所以现在逻辑塔是什么样的逻辑？", "所以现在，Logitech是什么样的逻辑？", "所以现在逻辑塔是什么样的逻辑？"),
    ("罗塞塔的这个 dashboard 还不够好。", "Rosetta的这个 dashboard，还不够好！", "Rosetta的这个 dashboard 还不够好。"),
    # Casing counts as a difference, so a hotword's own spelling wins.
    ("open playwright cli now", "open PlayWright CLI now", "open PlayWright CLI now"),
    # A hotword written over words that sound nothing like it is not taken.
    ("像 USD 我不知道它有没有要求", "像 PlayWright CLI 我不知道它有没有要求", "像 USD 我不知道它有没有要求"),
    ("会有更多opportunity", "会有更多Rosetta", "会有更多opportunity"),
    # Nothing to take.
    ("发布。", "发布啊！", "发布。"),
    ("", "PlayWright CLI", ""),
    ("好的。", "好的，PlayWright CLI。", "好的。"),          # hotword recited after the speech ended
    ("same text", "same text", "same text"),
])
def test_merge(plain, hinted, want):
    assert merge(plain, hinted, HOT) == want


def test_no_hotwords_or_no_hint_keeps_the_plain_text():
    assert merge("Play Rice OI.", "Playwright CLI.", []) == "Play Rice OI."
    assert merge("Play Rice OI.", "", HOT) == "Play Rice OI."


def test_candidates_are_the_hotwords_the_text_sounds_like():
    words = ["PlayWright CLI", "Rosetta", "Codex", "闪电说"]
    assert candidates("你自己 play race，然后 iterator", words) == ["PlayWright CLI"]
    assert candidates("用闪电硕来写，再跑 code x", words) == ["闪电说"]          # "code x" spells Codex: respelled, no decode
    assert candidates("我们用 rosetter 和 code ex 吧", words) == ["Codex", "Rosetta"]
    # Short hotwords need a close match: "所" (suo) is not "Sol", "还可以" is "hai ke yi" and does reach "Haiku".
    assert candidates("所以说这个还可以", ["Sol", "Opus", "Luna"]) == []
    assert candidates("所以说这个还可以", ["Haiku"]) == ["Haiku"]
    assert candidates("像 USD 我不知道它有没有要求，对吧？", words) == []
    assert candidates("我不懂。", words) == []
    # Already written: nothing left for a second decode to do.
    assert candidates("用 Rosetta 和 codex 做", words) == []


def test_respell_uses_the_users_spelling_and_leaves_longer_words_alone():
    assert respell("open playwright cli now", ["PlayWright CLI"]) == "open PlayWright CLI now"
    assert respell("用 rosetta 做", ["Rosetta"]) == "用 Rosetta 做"
    assert respell("the solution", ["Sol"]) == "the solution"
    assert merge("a solution", "a Solution", ["Sol"]) == "a solution"


# ── through the engine: two decodes for a final, one for a preview ─────────

def _engine(monkeypatch, plain, hinted):
    mlx_qwen3_asr = pytest.importorskip("mlx_qwen3_asr")
    from types import SimpleNamespace

    from thundertalk.core.asr import AsrEngine

    calls = []

    def fake(audio, model=None, context="", language=None, max_new_tokens=0):
        calls.append(context)
        return SimpleNamespace(text=hinted if context else plain)

    monkeypatch.setattr(mlx_qwen3_asr, "transcribe", fake)
    eng = AsrEngine()
    eng._mlx_model = object()
    eng.set_hotwords(["PlayWright CLI"])
    return eng, calls


def test_final_decodes_plain_then_hinted_and_takes_only_the_hotword(monkeypatch):
    import numpy as np
    eng, calls = _engine(monkeypatch, "你自己 play race，然后我不懂。", "你自己 Playwright CLI，然后我不行。")
    r = eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000)
    assert calls == ["", "PlayWright CLI"]
    assert r.text == "你自己 Playwright CLI，然后我不懂。"


def test_preview_decodes_once_without_the_hotwords(monkeypatch):
    import numpy as np
    eng, calls = _engine(monkeypatch, "Play Rice OI.", "Playwright CLI.")
    r = eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000, preview=True)
    assert calls == [""] and r.text == "Play Rice OI."


def test_no_sound_alike_stretch_means_one_decode(monkeypatch):
    import numpy as np
    eng, calls = _engine(monkeypatch, "像 USD 我不知道它有没有要求。", "像 PlayWright CLI 我不知道它有没有要求。")
    assert eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000).text == "像 USD 我不知道它有没有要求。"
    assert calls == [""]


def test_only_the_likely_hotwords_are_prompted(monkeypatch):
    import numpy as np
    eng, calls = _engine(monkeypatch, "用 play race 测", "用 Playwright CLI 测")
    eng.set_hotwords(["Rosetta", "Codex", "PlayWright CLI", "Antigravity"])
    assert eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000).text == "用 Playwright CLI 测"
    assert calls == ["", "PlayWright CLI"]


def test_no_hotwords_means_one_decode(monkeypatch):
    import numpy as np
    eng, calls = _engine(monkeypatch, "发布。", "发布啊！")
    eng.set_hotwords([])
    assert eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000).text == "发布。" and calls == [""]


def test_hotwords_recited_over_silence_are_not_taken(monkeypatch):
    import numpy as np
    eng, calls = _engine(monkeypatch, "", "PlayWright CLI")
    assert eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000).text == "" and calls == [""]


# ── through the engine: stretches are scored, not decoded again ────────────

def _scoring_engine(monkeypatch, plain, gain):
    """An engine whose decoder "prefers" every sound-alike stretch by ``gain``."""
    from mlx_qwen3_asr import tokenizer

    from thundertalk.core import hotword_score

    eng, calls = _engine(monkeypatch, plain, "never decoded")
    eng.set_hotwords([])
    monkeypatch.setattr(tokenizer._TokenizerHolder, "get", classmethod(lambda cls, _id: object()))

    def fake_score(model, tok, samples, text, stretches, **kw):
        for s in stretches:
            s.gain = gain
        fake_score.prompt = kw["prompt"]
        return stretches

    monkeypatch.setattr(hotword_score, "score", fake_score)
    return eng, calls, fake_score


def test_a_preferred_stretch_becomes_the_users_hotword_without_a_second_decode(monkeypatch):
    import numpy as np
    eng, calls, scored = _scoring_engine(monkeypatch, "你自己 play race，然后我不懂。", gain=0.5)
    eng.set_hotwords(["Playwright CLI", "Rosetta"])
    r = eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000)
    assert r.text == "你自己 Playwright CLI，然后我不懂。"
    assert calls == [""] and scored.prompt == "Playwright CLI"          # one decode; only the likely hotword prompted


def test_a_stretch_the_decoder_does_not_prefer_stays(monkeypatch):
    import numpy as np
    from thundertalk.core.asr import AsrEngine
    eng, calls, _ = _scoring_engine(monkeypatch, "你自己 play race，然后我不懂。", gain=AsrEngine.HOTWORD_GAIN - 0.5)
    eng.set_hotwords(["Playwright CLI"])
    assert eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000).text == "你自己 play race，然后我不懂。"
    assert calls == [""]


def test_a_name_off_the_screen_needs_more_than_the_users_own_hotword(monkeypatch):
    import numpy as np
    audio = np.zeros(16000, dtype=np.float32)
    from thundertalk.core.asr import AsrEngine
    enough_for_a_hotword = AsrEngine.HOTWORD_GAIN + 0.5
    eng, _calls, _ = _scoring_engine(monkeypatch, "我用 Rosetta 来做。", gain=enough_for_a_hotword)
    eng.set_context_terms(["Rozetta"])                                   # an earlier mishearing, still in the text field
    assert eng._recognize_mlx(audio, 16000).text == "我用 Rosetta 来做。"
    eng, _calls, _ = _scoring_engine(monkeypatch, "我用 rose etta 来做。", gain=enough_for_a_hotword + AsrEngine.CONTEXT_GAIN)
    eng.set_context_terms(["Rosetta"])
    assert eng._recognize_mlx(audio, 16000).text == "我用 Rosetta 来做。"


def test_only_the_users_own_hotwords_respell(monkeypatch):
    import numpy as np
    audio = np.zeros(16000, dtype=np.float32)
    eng, calls, _ = _scoring_engine(monkeypatch, "交给 Codex 和 playwright cli。", gain=-9)
    eng.set_hotwords(["PlayWright CLI"])
    eng.set_context_terms(["CodeX"])                                     # wrongly cased on screen
    assert eng._recognize_mlx(audio, 16000).text == "交给 Codex 和 PlayWright CLI。"


def test_letters_are_compared_the_way_they_are_said():
    from thundertalk.core.hotword_merge import stretches
    found = lambda text, word: [text[s:e] for _a, _w, s, e in stretches(text, [word])]      # noqa: E731
    assert found("Coco Roa 配旁白", "Kokoro") == ["Coco Roa"]            # c = k
    assert found("Tira 写的文案", "Terra") == ["Tira"]
    assert found("交给 Fase Book", "Facebook") == ["Fase Book"]            # ce = se
    assert found("你自己 play race，然后 iterator", "Rosetta") == []        # not across Latin and Chinese
    assert found("the solution is clear", "Sol") == []
