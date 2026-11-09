"""Decode the eval v2 corpus through the app's real dictation path (recover_final).

  python -m tools.asr_eval.run funasr|qwen      (sherpa version = the interpreter's)

Resumable: results append to results/<sherpa>-<model>.jsonl keyed by (phase, id).
Phases: main (every clip, no hotwords), repeat (40 clips twice more: determinism),
hot-<batch> (that batch's hotwords loaded: its targets + 10 control clips).
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import resource
import sys
import time
from pathlib import Path
from unittest.mock import patch

from tools.asr_eval.corpus import ROOT

MODELS = {"funasr": ("funasr-nano-int8", "Fun-ASR-Nano"), "qwen": ("qwen3-asr-06b-int8", "Qwen3-ASR")}


def main() -> None:
    from thundertalk.core import asr, audio_io
    from thundertalk.core.dictation import recover_final

    model = sys.argv[1]
    mid, family = MODELS[model]
    ver = importlib.metadata.version("sherpa-onnx")
    out = ROOT / ("smoke" if os.environ.get("EVAL_LIMIT") else "results") / f"{ver}-{model}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            done.add((r["phase"], r["id"]))
    clips = {r["id"]: r for r in json.loads((ROOT / "manifest.json").read_text())}
    hot = json.loads((ROOT / "hotwords.json").read_text())
    controls = [i for i, r in clips.items() if r["group"] == "clean" and r["dataset"] == "ascend"]

    plan: list[tuple[str, list[str], list[str]]] = [("main", [], list(clips))]
    clean = [i for i, r in clips.items() if r["group"] == "clean"]
    plan.append(("repeat", [], [c for c in clean[::12]][:40] * 2))
    for b in hot:
        ctrl = controls[(b["batch"] * 11) % len(controls):][:10]
        plan.append((f"hot-{b['batch']}", b["terms"], b["targets"] + ctrl))

    with out.open("a") as f:
        for phase, terms, ids in plan:
            todo = [i for i in ids if (phase, i) not in done] if phase != "repeat" else (
                ids if not any(p == "repeat" for p, _ in done) else [])
            if os.environ.get("EVAL_LIMIT"):
                todo = todo[: int(os.environ["EVAL_LIMIT"])]
            if not todo:
                continue
            engine = asr.AsrEngine()
            engine.set_hotwords(terms)
            t0 = time.perf_counter()
            with patch.object(asr, "_detect_threads", return_value=4):
                engine.load_model(str(Path.home() / ".thundertalk/models" / mid), family, "onnx")
            print(f"[eval] {ver} {model} {phase} load {time.perf_counter() - t0:.1f}s, {len(todo)} clips", flush=True)
            for k, cid in enumerate(todo):
                c = clips[cid]
                x = audio_io.decode_audio(str(ROOT / c["path"]), 16000)
                t1 = time.perf_counter()
                r = recover_final(engine, x)
                wall = time.perf_counter() - t1
                f.write(json.dumps(dict(phase=phase, id=cid, run=k // len(set(ids)) if phase == "repeat" else 0,
                                        version=ver, model=model, terms=terms, text=r.text,
                                        duration=len(x) / 16000, wall_s=wall, truncated=r.truncated,
                                        recovery_source=r.recovery_source,
                                        rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 30,
                                        load=os.getloadavg()[0]), ensure_ascii=False) + "\n")
                f.flush()
                if k % 100 == 0:
                    print(f"[eval] {phase} {k}/{len(todo)}", flush=True)
            engine.unload()
    print(f"[eval] FINISHED {ver} {model}", flush=True)


if __name__ == "__main__":
    main()
