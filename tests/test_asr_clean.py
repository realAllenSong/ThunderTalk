"""Tests for the special-token stripping in ASR output.

The Qwen3-ASR (and SenseVoice) inference wrappers occasionally let
training-time control tokens reach the user — `<asr_text>`, `<|zh|>`,
`<|im_start|>`, etc. `_strip_special_tokens` removes them while
preserving legitimate dictation that happens to contain `<` (math,
emoticons, code).
"""

from __future__ import annotations

import pytest

from thundertalk.core.asr import _strip_special_tokens


# --- Real model leaks we should strip ---------------------------------------

@pytest.mark.parametrize("raw, expected", [
    # Qwen3-ASR chat-template wrapper
    ("<asr_text>你好</asr_text>", "你好"),
    ("<asr_text>hello world</asr_text>", "hello world"),
    ("<asr_text>", ""),
    ("</asr_text>", ""),
    # leak only at the start (no closing tag)
    ("<asr_text>这样子可行吗", "这样子可行吗"),
    # Chat-template markers
    ("<|im_start|>你好<|im_end|>", "你好"),
    ("<|endoftext|>hello", "hello"),
    # SenseVoice language / event tags
    ("<|zh|>你好", "你好"),
    ("<|en|><|HAPPY|><|Speech|><|withitn|>hello there", "hello there"),
    ("<|zh|><|NEUTRAL|><|Speech|><|withitn|>这是测试", "这是测试"),
    # Multiple leaks
    ("<asr_text><|zh|>测试<|im_end|></asr_text>", "测试"),
    # Tag with attribute (rare but possible)
    ('<span class="x">text</span>', "text"),
])
def test_strips_real_model_leaks(raw, expected):
    assert _strip_special_tokens(raw) == expected


# --- False-positive guards: real dictation that contains `<` ----------------

@pytest.mark.parametrize("text", [
    "x < 5",                # math, no letter follows `<`
    "if a < b then c",      # logical comparison with surrounding spaces
    "<3",                   # heart emoticon, digit follows `<`
    "<--",                  # arrow, dash follows `<`
    "<= and >=",            # comparison operators
    "1<2<3",                # chained comparisons (digits, no `>` close)
    "He said \"<\" means less than",
    "5 < 10",
    "",                     # empty stays empty
    "你好世界",              # plain Chinese
    "Hello, world!",        # plain English
])
def test_preserves_legitimate_dictation(text):
    # Strip is idempotent on already-clean input (modulo .strip()).
    assert _strip_special_tokens(text) == text.strip()


def test_idempotent_on_clean_text():
    text = "已经干净的文本 plain text"
    once = _strip_special_tokens(text)
    twice = _strip_special_tokens(once)
    assert once == twice == text


def test_collapses_to_empty_when_only_tags():
    assert _strip_special_tokens("<asr_text></asr_text>") == ""
    assert _strip_special_tokens("<|im_start|><|im_end|>") == ""
    assert _strip_special_tokens("   <asr_text>  </asr_text>   ") == ""
