"""Hotwords may only change the words they are about (thundertalk.core.hotword_merge)."""
import pytest

from thundertalk.core.hotword_merge import merge

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


def test_no_hotwords_means_one_decode(monkeypatch):
    import numpy as np
    eng, calls = _engine(monkeypatch, "发布。", "发布啊！")
    eng.set_hotwords([])
    assert eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000).text == "发布。" and calls == [""]


def test_hotwords_recited_over_silence_are_not_taken(monkeypatch):
    import numpy as np
    eng, calls = _engine(monkeypatch, "", "PlayWright CLI")
    assert eng._recognize_mlx(np.zeros(16000, dtype=np.float32), 16000).text == "" and calls == [""]
