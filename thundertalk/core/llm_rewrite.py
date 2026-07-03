"""ASR post-processing via a local LLM.

The core task is *intent reconstruction*, not grammar checking: the LLM uses
world knowledge to fix mis-recognized proper nouns (brand names, model names,
technical terms) while also cleaning up punctuation, capitalisation, and
common filler words.

Architecture: model loading is cached; per-utterance inference is called from
LlmRewriteWorker (a background QThread). _INFERENCE_LOCK ensures only one
mlx_lm.generate() runs at a time — the MLX Metal backend is not thread-safe
for concurrent generation on the same model object.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

_ENGINE_CACHE: dict[str, "_LlmRewriteEngine"] = {}
_CACHE_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()  # serialise MLX generate() calls

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
# Design goals:
#  1. Fix mis-recognised proper nouns using world knowledge (highest priority)
#  2. Remove meaningless spoken fillers (呃, 那个…那个, isolated 嗯)
#  3. Add punctuation and fix capitalisation
#  4. NEVER translate — Chinese stays Chinese, English stays English
#  5. Preserve deliberate code-switching
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """You are an ASR (speech-to-text) post-processor for a Chinese/English bilingual tech professional.

YOUR TASKS:
1. Fix mis-recognised proper nouns using world knowledge (brand names, AI model names, tech terms).
2. Remove spoken filler words: 呃, 嗯 (hesitation), 然后呢 (filler), exact verbal repetitions like "let me, let me" → "let me".
3. Add missing punctuation and fix capitalisation.

LANGUAGE PRESERVATION — ABSOLUTE RULE:
- Chinese text (汉字) MUST stay as Chinese. English text MUST stay as English.
- English technical terms embedded in Chinese sentences must remain English.

EXAMPLES (principles only — use these to understand the pattern, not to memorise names):

Input:  呃，我们需要优化这个system的latency，然后呢，减少overhead
Output: 我们需要优化这个system的latency，减少overhead。

Input:  我在用open a i的API做这个project
Output: 我在用OpenAI的API做这个project。

Input:  let me, let me think about this. 这个方案有问题。
Output: Let me think about this. 这个方案有问题。

Do NOT add, rephrase, or summarise. If uncertain about a proper noun, copy it unchanged.
Return ONLY the corrected text."""


class _LlmRewriteEngine:
    def __init__(self, model, tokenizer) -> None:
        self._model = model
        self._tokenizer = tokenizer

    @classmethod
    def from_pretrained(cls, model_id: str) -> "_LlmRewriteEngine":
        import mlx_lm
        print(f"[LlmRewrite] Loading model {model_id}…")
        t0 = time.monotonic()
        model, tokenizer = mlx_lm.load(model_id)
        print(f"[LlmRewrite] Model loaded in {time.monotonic() - t0:.1f}s")
        return cls(model, tokenizer)

    def rewrite(self, text: str) -> str:
        import re
        import mlx_lm

        messages = [
            {"role": "system", "content": "/no_think\n" + _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        prompt = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False,
        )
        input_ids = self._tokenizer.encode(prompt)
        # Allow up to input length + 100 tokens for corrections and clean-up.
        max_new = min(len(input_ids) + 100, 1024)

        with _INFERENCE_LOCK:
            response = mlx_lm.generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=max_new,
                verbose=False,
            )

        corrected = response.strip()
        # Strip <think>...</think> blocks (Qwen3 thinking-mode fallback)
        corrected = re.sub(r"<think>.*?</think>", "", corrected, flags=re.DOTALL).strip()
        # Strip common model preamble artefacts
        for prefix in ("Output:", "Corrected:", "Result:", "Answer:"):
            if corrected.lower().startswith(prefix.lower()):
                corrected = corrected[len(prefix):].strip()
        corrected = corrected.strip('"').strip("'")
        return corrected if corrected else text


def load_engine(model_id: str) -> _LlmRewriteEngine:
    with _CACHE_LOCK:
        if model_id not in _ENGINE_CACHE:
            _ENGINE_CACHE[model_id] = _LlmRewriteEngine.from_pretrained(model_id)
        return _ENGINE_CACHE[model_id]


def rewrite(text: str, model_id: str) -> Optional[str]:
    """Post-process *text* using *model_id*. Returns None if no change."""
    if not text or not text.strip():
        return None
    try:
        engine = load_engine(model_id)
        corrected = engine.rewrite(text)
        if corrected == text:
            return None
        print(f"[LlmRewrite] {repr(text[:60])} → {repr(corrected[:60])}")
        return corrected
    except Exception as e:
        print(f"[LlmRewrite] Error: {e}")
        return None
