"""Explicit live proofreading check using synthetic text, never called by tests.

Run through the machine-wide GPU lock, even for cloud providers:
  python3 ~/Library/Caches/ThunderTalk-bench/lock.py gpu -- env PYTHONPATH=$PWD \
    $PY tools/check_ai_cleanup.py [provider[:model] ...]
  (default: codex:gpt-6.1-sol and cursor with its default model)
Results are written to .ai-cleanup-checks.json; do not commit it.
"""
import json
import sys
import time
from pathlib import Path

from thundertalk.core.ai_cleanup import cleanup
from thundertalk.core.llm_providers import detect, preferred_model

# (transcript, reference_text or None, expected)
SAMPLES = [
    ("GPT的阿修罗、露娜、whatever、terra", "GPT的Astra、Luna、whatever、Terra", "GPT的Astra、Luna、whatever、Terra"),
    ("我们用 lama index 加上 rag 做检索", None, "我们用 LlamaIndex 加上 RAG 做检索"),
    ("the model uses group relative policy optimisation, also called g r p o", None,
     "the model uses group relative policy optimisation, also called GRPO"),
    ("这个 promp 要改一下", None, "这个 prompt 要改一下"),
    ("明天下午三点我们在三楼会议室开会，记得带上电脑。", None, "明天下午三点我们在三楼会议室开会，记得带上电脑。"),
]
OUT = Path(".ai-cleanup-checks.json")

start = time.monotonic()
providers = detect()
report = {"detection_seconds": round(time.monotonic() - start, 3), "providers": [
    {"id": p.id, "status": p.status, "models_source": p.models_source, "models": len(p.models),
     "default_model": preferred_model(p)} for p in providers], "checks": []}
print(json.dumps(report, ensure_ascii=False), flush=True)
targets = sys.argv[1:] or ["codex:gpt-6.1-sol", "cursor"]
for target in targets:
    ident, _, model = target.partition(":")
    provider = next((p for p in providers if p.id == ident and p.is_ready()), None)
    model = model or (preferred_model(provider) if provider else "")
    for text, reference, expected in SAMPLES:
        item = {"provider": ident, "model": model, "input": text, "reference_text": reference}
        started = time.monotonic()
        try:
            if provider is None:
                raise RuntimeError("Provider not ready")
            item["output"] = cleanup(provider, text, model, timeout=90, reference_text=reference)
            item["as_expected"] = item["output"] == expected
        except Exception as exc:
            item["error"] = str(exc)
        item["seconds"] = round(time.monotonic() - started, 2)
        report["checks"].append(item)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(item, ensure_ascii=False), flush=True)
