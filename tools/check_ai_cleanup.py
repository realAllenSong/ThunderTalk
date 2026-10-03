"""Explicit live provider check using synthetic text, never called by tests.

Run through the machine-wide GPU lock, even for cloud providers:
  python3 ~/Library/Caches/ThunderTalk-bench/lock.py gpu -- env PYTHONPATH=$PWD \
    /Users/songallen/Desktop/ThunderTalk/.venv/bin/python tools/check_ai_cleanup.py
"""
import json
import time
import sys
from pathlib import Path

from thundertalk.core.ai_cleanup import cleanup
from thundertalk.core.llm_providers import detect

SAMPLES = [
    "呃那个我们明天下午三点开会然后呢讨论一下新的方案不要改时间",
    "um so i i think we should send the draft tomorrow uh but do not publish it yet",
    "那个这个 API 的 latency 有点高 we need to reduce overhead 然后不要改 public interface",
    "we need three things first buy milk second send the invoice third call the team",
    "嗯第一检查日志第二修复错误第三运行测试然后下一段请问这个方案有什么风险",
]
start = time.monotonic()
providers = detect()
report = {"detection_seconds": round(time.monotonic() - start, 3), "providers": [
    {"id": p.id, "ready": p.is_ready(), "status": p.status, "models": p.models,
     "models_source": p.models_source} for p in providers], "checks": []}
print(json.dumps(report, ensure_ascii=False), flush=True)
if len(sys.argv) > 1 and Path(".ai-cleanup-checks.json").exists():
    report = json.loads(Path(".ai-cleanup-checks.json").read_text())
for ident, model in (("codex", "gpt-6.1-sol"), ("cursor", "gpt-5.4-mini-none")):
    if len(sys.argv) > 1 and sys.argv[1] != ident:
        continue
    provider = next((p for p in providers if p.id == ident and p.is_ready()), None)
    for sentence in SAMPLES:
        started = time.monotonic()
        item = {"provider": ident, "model": model, "input": sentence}
        try:
            if provider is None:
                raise RuntimeError("Provider not ready")
            item["output"] = cleanup(provider, sentence, model, timeout=60)
        except Exception as exc:
            item["error"] = str(exc)
        item["seconds"] = round(time.monotonic() - started, 3)
        report["checks"].append(item)
        Path(".ai-cleanup-checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(item, ensure_ascii=False), flush=True)
