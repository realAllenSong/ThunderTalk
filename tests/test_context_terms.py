"""Names around the cursor become hotwords for the take (thundertalk.core.context_terms)."""
import time

import pytest

from thundertalk.core.context_terms import ContextCapture, extract_terms


@pytest.mark.parametrize("text, want", [
    ("我觉得 Rosetta 的 dashboard 还不够好，你用 Playwright CLI 跑一下。", ["Playwright CLI", "Rosetta"]),
    ("The reviewer asked about GRPO vs DAPO. We ran it on Qwen3 and posted to OpenReview. Then I went home.",
     ["OpenReview", "Qwen3", "DAPO", "GRPO"]),
    ("This is fine. Maybe use Claude Code or Hugging Face models with MLX on my MacBook Pro.",
     ["MacBook Pro", "MLX", "Hugging Face", "Claude Code"]),
    ("为什么要用 IBKR 买 QQQ 和 ETF？", ["ETF", "QQQ", "IBKR"]),
    # Ordinary words, sentence starts, hashes and shouting are not names.
    ("Please check the dashboard. Then deploy it. OK?", []),
    ("commit 13beaf6a2c fixes the thing", []),
    ("", []),
])
def test_extract_terms(text, want):
    assert extract_terms(text) == want


def test_nearest_to_the_cursor_first_and_bounded():
    text = " ".join(f"用 Name{i}X 做" for i in range(100))
    terms = extract_terms(text, limit=5)
    assert terms == ["Name99X", "Name98X", "Name97X", "Name96X", "Name95X"]
    assert extract_terms("用 Rosetta，再用 rosetta，还是 Rosetta") == ["Rosetta"]


def test_capture_runs_off_thread_and_a_new_take_forgets_the_old_one():
    texts = {1: "用 Rosetta 做", 2: ""}
    cap = ContextCapture(reader=lambda pid: texts[pid])
    cap.start(1)
    deadline = time.monotonic() + 2
    while not cap.terms and time.monotonic() < deadline:
        time.sleep(0.01)
    assert cap.terms == ["Rosetta"]
    cap.start(2)
    assert cap.terms == []
    cap.start(None)                      # switched off: the reader gets no app and finds nothing
    assert cap.terms == []


def test_engine_checks_context_names_after_the_users_hotwords(monkeypatch):
    mlx_qwen3_asr = pytest.importorskip("mlx_qwen3_asr")
    from types import SimpleNamespace

    import numpy as np

    from thundertalk.core.asr import AsrEngine

    calls = []

    def fake(audio, model=None, context="", language=None, max_new_tokens=0):
        calls.append(context)
        return SimpleNamespace(text="用 Rosetta 的 dashboard" if context else "用 罗塞塔 的 dashboard")

    monkeypatch.setattr(mlx_qwen3_asr, "transcribe", fake)
    eng = AsrEngine()
    eng._mlx_model = object()
    audio = np.zeros(16000, dtype=np.float32)
    assert eng._recognize_mlx(audio, 16000).text == "用 罗塞塔 的 dashboard" and calls == [""]
    eng.set_context_terms(["Rosetta", "MCP"])
    assert eng._recognize_mlx(audio, 16000).text == "用 Rosetta 的 dashboard"
    assert calls[1:] == ["", "Rosetta"]
    eng.set_context_terms([])
    assert eng._recognize_mlx(audio, 16000).text == "用 罗塞塔 的 dashboard"
