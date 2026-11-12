"""Pick the best LoRA checkpoint of a run on the dev split, then gate it on test.

    uv run python -m tools.finetune.select_checkpoint RUN_DIR [--base qwen3-asr-06b-mlx]

Each checkpoint is merged into a full model (merge_lora.py) and scored with
``thundertalk eval --suite standard --split dev`` (108 clips the test suite
never sees). The best one is then compared with the base model on the test
split: it passes only if no category gets worse by more than ``--tolerance``
(absolute token error rate) and it is not slower. Merged folders of the losers
are deleted. Prints a JSON summary.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / "Library" / "Caches" / "ThunderTalk-bench" / "finetune"
PY = ROOT / "venv-qwen" / "bin" / "python"
LOCK = Path.home() / "Library" / "Caches" / "ThunderTalk-bench" / "lock.py"


def run_eval(engine: str, split: str, preview: bool = False) -> dict:
    cmd = [sys.executable, str(LOCK), "gpu", "--", "nice", "-n", "10", sys.executable, "-m", "thundertalk",
           "--cli", "eval", "--suite", "standard", "--split", split, "--engines", engine]
    if preview:
        cmd.append("--preview")
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip().splitlines()
    result = json.loads(out[-1]) if out else {"ok": False}
    if not result.get("ok"):
        raise SystemExit(f"eval failed for {engine}: {result}")
    return result["engines"][0]


def brief(e: dict) -> dict:
    cats = e["by_category"]
    n = sum(v["clips"] for v in cats.values())
    return {"ter": e["token_error_rate_tidy"],
            "rtf": round(sum(v["rtf"] * v["clips"] for v in cats.values()) / n, 3),
            **{k: v["ter_tidy"] for k, v in cats.items()},
            **({"preview": f"{e['preview']['kept_up']}/{e['preview']['long_clips']}"} if "preview" in e else {})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--base", default="qwen3-asr-06b-mlx")
    ap.add_argument("--tolerance", type=float, default=0.01)
    a = ap.parse_args()
    run = Path(a.run).expanduser()
    ckpts = sorted((p for p in run.glob("checkpoint-*") if (p / "adapter_model.safetensors").exists()),
                   key=lambda p: int(p.name.split("-")[1]))
    if not ckpts:
        raise SystemExit("no LoRA checkpoints in " + str(run))
    dev = {"base": brief(run_eval(a.base, "dev"))}
    print("dev base", dev["base"], file=sys.stderr)
    for ck in ckpts:
        merged = run / ("merged-" + ck.name.split("-")[1])
        if not (merged / "model.safetensors").exists():
            subprocess.run([str(PY), str(Path(__file__).with_name("merge_lora.py")), str(ck), str(merged)],
                           check=True, capture_output=True)
        dev[ck.name] = brief(run_eval(f"qwen-mlx:{merged}", "dev"))
        print("dev", ck.name, dev[ck.name], file=sys.stderr)
    best = min((k for k in dev if k != "base"), key=lambda k: dev[k]["ter"])
    for ck in ckpts:
        if ck.name != best:
            shutil.rmtree(run / ("merged-" + ck.name.split("-")[1]), ignore_errors=True)
    best_dir = run / ("merged-" + best.split("-")[1])
    test = {"base": brief(run_eval(a.base, "test", preview=True)),
            "tuned": brief(run_eval(f"qwen-mlx:{best_dir}", "test", preview=True))}
    worse = {k: (test["base"][k], test["tuned"][k]) for k in test["base"]
             if k.startswith(("short", "long")) and test["tuned"][k] > test["base"][k] + a.tolerance}
    slower = test["tuned"]["rtf"] > test["base"]["rtf"] * 1.1
    verdict = {"best": best, "model": str(best_dir), "dev": dev, "test": test, "regressions": worse,
               "slower": slower, "passes": not worse and not slower and test["tuned"]["ter"] < test["base"]["ter"]}
    (run / "selection.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1))
    print(json.dumps(verdict, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
